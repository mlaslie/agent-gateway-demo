"""Outbound calls to A2A agents and MCP servers. Each call opens its own connection and never raises.

Every call returns the tool result object from docs/CONTRACTS.md §6:

    {"edge": "...", "outcome": "ok|denied|blocked|error", "http_status": int|None,
     "detail": "...", "result": <payload or None>, "latency_ms": int}
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx
import httpx2
from mcp.client import Client as McpClient
from mcp.client.streamable_http import streamable_http_client

from . import spec

log = logging.getLogger(__name__)

TIMEOUT_S = 60.0
_BODY_SNIPPET = 400

# --------------------------------------------------------------------------------------
# Failure classification. Kept in one place so the Model Armor signal can be tuned once
# the real gateway response is observed (document it in docs/ARCHITECTURE.md).
# --------------------------------------------------------------------------------------
MODEL_ARMOR_MARKERS = ("model armor", "modelarmor", "model_armor", "model-armor", "sanitiz", "blocked by")


def classify_failure(http_status: int | None, headers: dict[str, str] | None = None,
                     body: str = "") -> str:
    """Map a failed call to an outcome: 'blocked' (Model Armor), 'denied' (403) or 'error'.

    Model Armor is detected heuristically from the response body or headers, and takes
    precedence over the status code (a Model Armor block may itself be a 403).
    """
    haystack = " ".join([body or "", *(f"{k}: {v}" for k, v in (headers or {}).items())]).lower()
    if any(m in haystack for m in MODEL_ARMOR_MARKERS):
        return "blocked"
    if http_status == 403:
        return "denied"
    return "error"


def _result(edge: str, outcome: str, started: float, *, http_status: int | None = None,
            detail: str = "", result: Any = None) -> dict[str, Any]:
    return {"edge": edge, "outcome": outcome, "http_status": http_status, "detail": detail[:1000],
            "result": result, "latency_ms": int((time.monotonic() - started) * 1000)}


def _root_cause(exc: BaseException) -> BaseException:
    """Unwrap (nested) exception groups raised by anyio task groups inside the MCP client."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return exc


# --------------------------------------------------------------------------------------
# Auth: optional Google ID token for private Cloud Run targets (AUTH_MODE=id_token).
# --------------------------------------------------------------------------------------
_TOKEN_TTL_S = 45 * 60
_token_cache: dict[str, tuple[str, float]] = {}


def _fetch_id_token(audience: str) -> str:
    import google.auth.transport.requests
    import google.oauth2.id_token

    return google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), audience)


async def auth_headers(url: str) -> dict[str, str]:
    """Authorization header for `url` (audience = scheme://host), or {} — best effort, never raises."""
    if spec.auth_mode() != "id_token":
        return {}
    parts = urlsplit(url)
    audience = f"{parts.scheme}://{parts.netloc}"
    cached = _token_cache.get(audience)
    if cached and cached[1] > time.time():
        return {"Authorization": f"Bearer {cached[0]}"}
    try:
        token = await asyncio.to_thread(_fetch_id_token, audience)
    except Exception as e:  # noqa: BLE001 - best effort; the call proceeds unauthenticated
        log.warning("ID token for %s unavailable: %s", audience, e)
        return {}
    _token_cache[audience] = (token, time.time() + _TOKEN_TTL_S)
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------------------
# A2A: plain JSON-RPC over httpx. `message/send` (A2A 0.3 wire format) is accepted by both
# a2a-sdk 0.3 servers and 1.x servers (ADK to_a2a enables the 0.3 compat adapter).
# --------------------------------------------------------------------------------------
def _a2a_text(result: dict[str, Any]) -> str:
    """Concatenate the text parts of a Task (artifacts, else status message) or a Message."""
    def texts(parts: list[dict[str, Any]] | None) -> list[str]:
        return [p["text"] for p in parts or [] if p.get("text")]

    if result.get("kind") == "message" or "parts" in result:
        return "\n".join(texts(result.get("parts")))
    out = [t for a in result.get("artifacts") or [] for t in texts(a.get("parts"))]
    if not out:
        out = texts(((result.get("status") or {}).get("message") or {}).get("parts"))
    return "\n".join(out)


async def call_a2a(agent_id: str, message: str) -> dict[str, Any]:
    started = time.monotonic()
    edge = agent_id
    comp = spec.component(agent_id)
    if not comp or comp.get("kind") != "a2a_agent":
        return _result(edge, "error", started, detail=f"unknown A2A agent '{agent_id}'")
    base = comp["url"].rstrip("/")
    try:
        headers = await auth_headers(base)
        async with httpx.AsyncClient(timeout=TIMEOUT_S, headers=headers) as http:
            # 1) Agent card (discovery). A gateway denial usually shows up here first.
            card_resp = await http.get(f"{base}/.well-known/agent-card.json")
            if card_resp.status_code != 200:
                return _http_failure(edge, started, card_resp, "agent card")
            # 2) message/send to the registered endpoint (the topology URL, which is what the
            #    gateway / registry knows; the card's advertised URL is informational here).
            rpc = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": "message/send",
                   "params": {"message": {"kind": "message", "role": "user", "messageId": str(uuid.uuid4()),
                                          "parts": [{"kind": "text", "text": message}]}}}
            resp = await http.post(f"{base}/", json=rpc)
            if resp.status_code != 200:
                return _http_failure(edge, started, resp, "message/send")
            body = resp.json()
            if "error" in body:
                err = json.dumps(body["error"])
                return _result(edge, classify_failure(200, dict(resp.headers), err), started,
                               http_status=200, detail=f"A2A error: {err}")
            result = body.get("result") or {}
            text = _a2a_text(result)
            state = str((result.get("status") or {}).get("state", "")).lower()
            if state.endswith(("failed", "rejected")):
                # The remote agent itself failed (e.g. its own model call was denied): not a policy result.
                return _result(edge, classify_failure(200, dict(resp.headers), text), started, http_status=200,
                               detail=f"remote agent task {state}: {text[:300]}")
            return _result(edge, "ok", started, http_status=200, detail="message/send completed", result=text)
    except Exception as e:  # noqa: BLE001 - tools must never raise
        e = _root_cause(e)
        return _result(edge, "error", started, detail=f"{type(e).__name__}: {e}")


def _http_failure(edge: str, started: float, resp: httpx.Response, step: str) -> dict[str, Any]:
    body = resp.text[:_BODY_SNIPPET]
    outcome = classify_failure(resp.status_code, dict(resp.headers), body)
    return _result(edge, outcome, started, http_status=resp.status_code,
                   detail=f"{step}: HTTP {resp.status_code} {body}".strip())


# --------------------------------------------------------------------------------------
# MCP: official SDK client over Streamable HTTP, one session per call. The SDK turns HTTP
# errors into generic JSON-RPC errors, so an httpx2 response hook records the first
# failing HTTP response (status, headers, body) to recover the real status code.
# --------------------------------------------------------------------------------------
class _HttpRecorder:
    def __init__(self) -> None:
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.body = ""

    async def __call__(self, response: httpx2.Response) -> None:
        # Only JSON-RPC POSTs: the optional GET SSE stream / DELETE may legitimately get 405.
        if response.status_code >= 400 and self.status is None and response.request.method == "POST":
            self.status = response.status_code
            self.headers = dict(response.headers)
            try:
                self.body = (await response.aread()).decode(errors="replace")[:_BODY_SNIPPET]
            except Exception:  # noqa: BLE001
                self.body = ""


def _mcp_payload(result: Any) -> Any:
    """Prefer structured content; else parse text content as JSON when possible."""
    if getattr(result, "structured_content", None) is not None:
        return result.structured_content
    texts = [c.text for c in result.content or [] if getattr(c, "text", None) is not None]
    joined = "\n".join(texts)
    try:
        return json.loads(joined)
    except (ValueError, TypeError):
        return joined


async def call_mcp(server_id: str, tool: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    started = time.monotonic()
    edge = f"{server_id}:{tool}"
    comp = spec.component(server_id)
    if not comp or comp.get("kind") != "mcp_server":
        return _result(edge, "error", started, detail=f"unknown MCP server '{server_id}'")
    recorder = _HttpRecorder()
    try:
        headers = await auth_headers(comp["url"])
        http = httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(TIMEOUT_S),
                                  event_hooks={"response": [recorder]})
        async with http:
            # mode="legacy": classic initialize handshake (widest compatibility with gateways/proxies).
            async with McpClient(streamable_http_client(comp["url"], http_client=http),
                                 mode="legacy", read_timeout_seconds=TIMEOUT_S) as client:
                res = await client.call_tool(tool, arguments or {})
        payload = _mcp_payload(res)
        if res.is_error:
            text = payload if isinstance(payload, str) else json.dumps(payload)
            return _result(edge, classify_failure(200, None, text), started, http_status=200,
                           detail=f"tool error: {text}", result=None)
        return _result(edge, "ok", started, http_status=200, detail="tools/call completed", result=payload)
    except BaseException as e:  # noqa: BLE001 - tools must never raise (incl. exception groups)
        if isinstance(e, (KeyboardInterrupt, SystemExit, asyncio.CancelledError)):
            raise
        cause = _root_cause(e)
        if recorder.status is not None:
            outcome = classify_failure(recorder.status, recorder.headers, recorder.body)
            return _result(edge, outcome, started, http_status=recorder.status,
                           detail=f"HTTP {recorder.status} {recorder.body}".strip())
        return _result(edge, "error", started, detail=f"{type(cause).__name__}: {cause}")
