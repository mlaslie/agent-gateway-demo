"""T4 "bypass" probe: call a theme's MCP servers directly from the demo UI backend, from outside every agent and
gateway (docs/TERRAFORM_PLAN.md §7c demo step 1). An `ingress: internal` Cloud Run service isn't reachable from
the internet, so Google's front end answers 404; a public one answers normally.

Used for scenario tests with `via: direct`. Every URL form of the service is tried (both run.app forms for an
internal component); the edge takes the first form's result, the detail lists all of them. Never raises.
"""
from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

TIMEOUT_S = 20.0
_ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _rpc(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method, "params": params}


async def call_once(client: httpx.AsyncClient, url: str, tool: str, args: dict[str, Any]) -> tuple[int | None, str]:
    """POST initialize + tools/call to <url>/mcp. Returns (HTTP status of the first failing step or 200, note)."""
    mcp = url.rstrip("/") + "/mcp"
    try:
        r = await client.post(mcp, json=_rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                                             "clientInfo": {"name": "agdemo-bypass", "version": "1"}}),
                              headers=_ACCEPT)
        if r.status_code != 200:
            return r.status_code, f"initialize: HTTP {r.status_code}"
        r = await client.post(mcp, json=_rpc("tools/call", {"name": tool, "arguments": args}), headers=_ACCEPT)
        return r.status_code, f"tools/call: HTTP {r.status_code}"
    except httpx.HTTPError as e:
        return None, f"{type(e).__name__}: {e}"


async def bypass(edge: str, urls: list[str], tool: str, args: dict[str, Any], internal: bool,
                 transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """The §2 EdgeState of a direct call (source live)."""
    started = time.monotonic()
    notes: list[str] = []
    first: int | None = None
    async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=transport, follow_redirects=False) as client:
        for i, u in enumerate(urls):
            status, note = await call_once(client, u, tool, args)
            notes.append(f"{u.split('//')[-1]}: {note}")
            if i == 0:
                first = status
    if first == 404:
        state = "unreachable"
    elif first is not None and 200 <= first < 300:
        state = "direct"
    elif first == 403:
        state = "denied"
    else:
        state = "error"
    lead = ("Direct call from the demo UI backend (no agent, no gateway)"
            + ("; Cloud Run ingress internal answers 404 from the internet" if internal and state == "unreachable"
               else ""))
    return {"edge": edge, "state": state, "governed": False, "source": "live", "http_status": first,
            "path": "internet", "detail": f"{lead}. " + "; ".join(notes),
            "latency_ms": int((time.monotonic() - started) * 1000)}
