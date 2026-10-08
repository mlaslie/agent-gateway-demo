"""Client for the theme's orchestrator on Agent Runtime (Vertex AI reasoning engine).

REST: POST https://{location}-aiplatform.googleapis.com/v1/{engine}:streamQuery?alt=sse
      body {"class_method": "async_stream_query", "input": {"user_id": ..., "message": ...}}
The response is a stream of ADK Event dicts (snake_case, `exclude_none`), either SSE `data:` lines or
newline-delimited JSON. See vertexai/_genai/_agent_engines_utils.py `_wrap_async_stream_query_operation`.

Probe protocol (docs/CONTRACTS.md §6): message "__PROBE__ {json}" -> one text part "__PROBE_RESULT__ {json}".
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any, AsyncIterator

import httpx

PROBE_PREFIX = "__PROBE__ "
PROBE_RESULT_PREFIX = "__PROBE_RESULT__"
DEFAULT_TIMEOUT_S = float(os.environ.get("AGDEMO_LIVE_TIMEOUT", "45"))
_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


class RuntimeCallError(Exception):
    """Agent Runtime call failed (HTTP error, timeout, malformed stream)."""


# ------------------------------------------------------------------ auth
_creds = None


def _token() -> str:
    global _creds
    import google.auth
    import google.auth.transport.requests

    if _creds is None:
        _creds, _ = google.auth.default(scopes=_SCOPES)
    if not _creds.valid:
        _creds.refresh(google.auth.transport.requests.Request())
    return _creds.token


async def _auth_headers() -> dict[str, str]:
    tok = await asyncio.to_thread(_token)
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


# ------------------------------------------------------------------ mapping
def tool_result_to_edge_state(obj: dict[str, Any], gateway_attached: bool,
                              source: str = "live") -> dict[str, Any]:
    """Map a §6 tool return object to a §2 EdgeState."""
    outcome = (obj or {}).get("outcome", "error")
    status = obj.get("http_status") if isinstance(obj, dict) else None
    detail = str(obj.get("detail") or "") if isinstance(obj, dict) else ""
    if outcome == "ok":
        state = "allowed" if gateway_attached else "direct"
    elif outcome in ("denied", "blocked"):
        state = outcome
    else:
        state = "error"
    if not detail:
        detail = {"allowed": "Allowed by Agent Gateway", "direct": "Direct call (no gateway)",
                  "denied": "403 from gateway", "blocked": "Blocked by Model Armor"}.get(state, outcome)
    return {"state": state, "governed": gateway_attached or state in ("denied", "blocked"),
            "source": source, "detail": detail, "http_status": status,
            **({"latency_ms": obj["latency_ms"]} if isinstance(obj, dict) and "latency_ms" in obj else {})}


def _get(d: dict[str, Any], snake: str, camel: str) -> Any:
    return d.get(snake, d.get(camel))


def parse_event(ev: Any) -> list[dict[str, Any]]:
    """ADK event dict -> [{"kind": "function_call"|"function_response"|"text", ...}]."""
    out: list[dict[str, Any]] = []
    if not isinstance(ev, dict):
        return out
    content = ev.get("content") or {}
    for part in content.get("parts") or []:
        if not isinstance(part, dict):
            continue
        fc = _get(part, "function_call", "functionCall")
        fr = _get(part, "function_response", "functionResponse")
        if fc:
            out.append({"kind": "function_call", "name": fc.get("name"), "args": fc.get("args") or {},
                        "id": fc.get("id"), "author": ev.get("author")})
        elif fr:
            resp = fr.get("response") or {}
            # ADK wraps non-dict returns as {"result": ...}
            if isinstance(resp, dict) and "edge" not in resp and isinstance(resp.get("result"), dict) \
                    and "edge" in resp["result"]:
                resp = resp["result"]
            out.append({"kind": "function_response", "name": fr.get("name"), "response": resp,
                        "id": fr.get("id"), "author": ev.get("author")})
        elif part.get("text") and not part.get("thought"):
            out.append({"kind": "text", "text": part["text"], "author": ev.get("author"),
                        "role": content.get("role")})
    return out


def _parse_stream_line(line: str) -> Any:
    line = line.strip()
    if line.startswith("data:"):
        line = line[5:].strip()
    if not line or line.startswith(":") or line.startswith("event:") or line.startswith("id:"):
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def build_probe_message(probes: list[str | dict[str, Any]], malicious: bool = False) -> str:
    """`probes`: edge ids, or {"edge", "message"?, "args"?} dicts (docs/CONTRACTS.md §6)."""
    items = [p if isinstance(p, dict) else {"edge": p} for p in probes]
    return PROBE_PREFIX + json.dumps({"probes": items, "malicious": malicious})


def parse_probe_result(text: str) -> list[dict[str, Any]] | None:
    t = (text or "").strip()
    if not t.startswith(PROBE_RESULT_PREFIX):
        return None
    try:
        data = json.loads(t[len(PROBE_RESULT_PREFIX):].strip())
    except json.JSONDecodeError:
        return None
    return list(data.get("results") or [])


# ------------------------------------------------------------------ client
class RuntimeClient:
    def __init__(self, engine: str, timeout_s: float = DEFAULT_TIMEOUT_S, api_version: str = "v1",
                 transport: httpx.AsyncBaseTransport | None = None):
        if not engine or "/locations/" not in engine:
            raise ValueError(f"bad engine resource name: {engine!r}")
        self.engine = engine
        self.location = engine.split("/locations/")[1].split("/")[0]
        self.timeout_s = timeout_s
        self.transport = transport             # tests inject httpx.MockTransport
        self.url = (f"https://{self.location}-aiplatform.googleapis.com/{api_version}/"
                    f"{engine}:streamQuery?alt=sse")

    async def stream_events(self, message: str, user_id: str | None = None) -> AsyncIterator[dict[str, Any]]:
        """Raw ADK event dicts from :streamQuery. Raises RuntimeCallError on HTTP errors."""
        body = {"class_method": "async_stream_query",
                "input": {"user_id": user_id or f"agdemo-ui-{uuid.uuid4().hex[:8]}", "message": message}}
        headers = await _auth_headers() if self.transport is None else {}
        timeout = httpx.Timeout(self.timeout_s, connect=15)
        async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
            async with client.stream("POST", self.url, json=body, headers=headers) as r:
                if r.status_code >= 400:
                    txt = (await r.aread()).decode(errors="replace")[:500]
                    raise RuntimeCallError(f"Agent Runtime HTTP {r.status_code}: {txt}")
                buf = ""
                async for chunk in r.aiter_text():
                    buf += chunk
                    while "\n" in buf:
                        line, buf = buf.split("\n", 1)
                        ev = _parse_stream_line(line)
                        if isinstance(ev, dict):
                            if "error" in ev and "content" not in ev:
                                raise RuntimeCallError(f"Agent Runtime error: {ev['error']}")
                            yield ev
                ev = _parse_stream_line(buf)
                if isinstance(ev, dict):
                    yield ev

    async def run_probes(self, edges: list[str | dict[str, Any]], malicious: bool = False) -> list[dict[str, Any]]:
        """Deterministic probe run; returns the §6 tool return objects."""
        msg = build_probe_message(edges, malicious)

        async def _go() -> list[dict[str, Any]]:
            texts: list[str] = []
            async for ev in self.stream_events(msg):
                for p in parse_event(ev):
                    if p["kind"] == "text":
                        texts.append(p["text"])
                        res = parse_probe_result(p["text"])
                        if res is not None:
                            return res
            res = parse_probe_result("".join(texts))
            if res is None:
                raise RuntimeCallError("orchestrator did not return a __PROBE_RESULT__ "
                                    f"(got: {''.join(texts)[:200]!r})")
            return res

        try:
            return await asyncio.wait_for(_go(), timeout=self.timeout_s)
        except asyncio.TimeoutError as e:
            raise RuntimeCallError(f"probe timed out after {self.timeout_s:.0f}s") from e
        except httpx.HTTPError as e:
            raise RuntimeCallError(f"Agent Runtime request failed: {e}") from e

    async def chat(self, prompt: str, gateway_attached: bool) -> AsyncIterator[dict[str, Any]]:
        """Natural-language run. Yields §7 SSE-style events: message (tool/agent) and edge."""
        calls: dict[str, dict[str, Any]] = {}
        try:
            async for ev in self.stream_events(prompt):
                for p in parse_event(ev):
                    if p["kind"] == "function_call":
                        calls[p.get("id") or p["name"]] = p
                        args = p["args"]
                        tgt = args.get("agent_id") or args.get("server_id") or ""
                        tool = f".{args['tool']}" if args.get("tool") else ""
                        yield {"type": "message", "role": "tool",
                               "text": f"→ {p['name']}({tgt}{tool})"}
                    elif p["kind"] == "function_response":
                        resp = p["response"]
                        if isinstance(resp, dict) and resp.get("edge"):
                            yield {"type": "edge", "edge": resp["edge"],
                                   "state": tool_result_to_edge_state(resp, gateway_attached)}
                            yield {"type": "message", "role": "tool",
                                   "text": f"← {resp['edge']}: {resp.get('outcome')} "
                                           f"({resp.get('http_status')})"}
                    elif p["kind"] == "text" and p.get("role") != "user":
                        yield {"type": "message", "role": "agent", "text": p["text"]}
        except httpx.HTTPError as e:
            raise RuntimeCallError(f"Agent Runtime request failed: {e}") from e
