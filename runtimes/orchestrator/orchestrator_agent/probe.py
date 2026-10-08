"""Deterministic probe mode (docs/CONTRACTS.md §6). No LLM call is made.

User message:  __PROBE__ {"probes": [{"edge": "kb-agent"}, {"edge": "tickets-mcp:delete_ticket"}], "malicious": false}
Agent reply:   __PROBE_RESULT__ {"results": [<tool result object>, ...]}
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.genai import types

from . import remote, spec

PROBE_PREFIX = "__PROBE__ "
RESULT_PREFIX = "__PROBE_RESULT__ "


def _with_payload_args(args: dict[str, Any], payload: str) -> dict[str, Any]:
    """Inject the malicious payload into the first string argument (or a `note` argument)."""
    args = dict(args)
    key = next((k for k, v in args.items() if isinstance(v, str)), None)
    if key:
        args[key] = f"{args[key]} {payload}"
    else:
        args["note"] = payload
    return args


async def run_probe(probe: dict[str, Any], malicious: bool) -> dict[str, Any]:
    """probe = {"edge", "message"?, "args"?}; message/args override the spec's probe defaults."""
    edge = probe["edge"]
    payload = spec.load().get("malicious_payload", "") if malicious else ""
    if ":" in edge:
        server_id, tool = edge.split(":", 1)
        comp = spec.component(server_id) or {}
        tdef = next((t for t in comp.get("tools", []) if t["name"] == tool), {})
        args = probe.get("args") or tdef.get("probe_args") or {}
        return await remote.call_mcp(server_id, tool, _with_payload_args(args, payload) if payload else args)
    comp = spec.component(edge) or {}
    message = probe.get("message") or comp.get("probe_message") or "ping"
    return await remote.call_a2a(edge, f"{message} {payload}".strip())


async def run_probes(request: dict[str, Any]) -> dict[str, Any]:
    probes = [p if isinstance(p, dict) else {"edge": p} for p in request.get("probes", [])]
    edges = [p["edge"] for p in probes]
    malicious = bool(request.get("malicious", False))
    results = await asyncio.gather(*(run_probe(p, malicious) for p in probes), return_exceptions=True)
    return {"results": [r if not isinstance(r, BaseException) else
                        {"edge": e, "outcome": "error", "http_status": None, "detail": repr(r),
                         "result": None, "latency_ms": 0}
                        for e, r in zip(edges, results)]}


def _probe_text(llm_request: LlmRequest) -> str | None:
    """The probe JSON if the latest content is a user message starting with __PROBE__."""
    if not llm_request.contents:
        return None
    last = llm_request.contents[-1]
    if last.role != "user":
        return None
    text = "".join(p.text or "" for p in last.parts or [])
    return text[len(PROBE_PREFIX):] if text.startswith(PROBE_PREFIX) else None


async def probe_callback(callback_context: CallbackContext, llm_request: LlmRequest) -> LlmResponse | None:
    """before_model_callback: answer probe messages directly; otherwise let the LLM run."""
    raw = _probe_text(llm_request)
    if raw is None:
        return None
    started = time.monotonic()
    try:
        out = await run_probes(json.loads(raw))
    except Exception as e:  # noqa: BLE001 - always answer with a parseable result
        out = {"results": [], "error": f"{type(e).__name__}: {e}"}
    out["latency_ms"] = int((time.monotonic() - started) * 1000)
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=RESULT_PREFIX + json.dumps(out))]))
