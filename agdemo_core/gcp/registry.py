"""Agent Registry (agentregistry.googleapis.com/v1) — manual registration via writable Service resources.

A Service is projected by the registry into a read-only Agent / McpServer / Endpoint resource; the
projection's id (e.g. `mcpServers/agentregistry-0000...`) is what IAP policies are set on. The Service's
`registryResource` field points at it (projects/<NUMBER>/locations/<R>/<kind>/<id>).

Body shapes (verified against existing services):
  endpoint: {"endpointSpec": {"type": "NO_SPEC"}, "interfaces": [{"url": U, "protocolBinding": "JSONRPC"}]}
  MCP:      {"mcpServerSpec": {"type": "TOOL_SPEC", "content": {"tools": [...tools/list...]}}, "interfaces": [...]}
  A2A:      {"agentSpec": {"type": "A2A_AGENT_CARD", "content": <agent card JSON>}}   (interfaces must be empty)
"""
from __future__ import annotations

import time
from typing import Any

from .rest import AGENTREGISTRY, GcpError, Rest


def services_url(parent: str, sid: str = "") -> str:
    return f"{AGENTREGISTRY}/{parent}/services" + (f"/{sid}" if sid else "")


def get_service(r: Rest, parent: str, sid: str) -> dict | None:
    return r.get(services_url(parent, sid), ok404=True)


def list_services(r: Rest, parent: str) -> list[dict]:
    return r.list_all(services_url(parent), "services")


def list_projection(r: Rest, parent: str, kind: str) -> list[dict]:
    """kind: agents | mcpServers | endpoints"""
    return r.list_all(f"{AGENTREGISTRY}/{parent}/{kind}", kind)


def endpoint_body(display: str, url: str, description: str = "") -> dict[str, Any]:
    return {"displayName": display[:63], "description": description,
            "endpointSpec": {"type": "NO_SPEC"},
            "interfaces": [{"url": url, "protocolBinding": "JSONRPC"}]}


def mcp_body(display: str, url: str, tools: list[dict], description: str = "") -> dict[str, Any]:
    return {"displayName": display[:63], "description": description,
            "mcpServerSpec": {"type": "TOOL_SPEC", "content": {"tools": tools}},
            "interfaces": [{"url": url, "protocolBinding": "JSONRPC"}]}


def a2a_body(display: str, card: dict, description: str = "") -> dict[str, Any]:
    return {"displayName": display[:63], "description": description or card.get("description", ""),
            "agentSpec": {"type": "A2A_AGENT_CARD", "content": card}}


def ensure_service(r: Rest, parent: str, sid: str, body: dict[str, Any], update: bool = True,
                   timeout: float = 300) -> dict:
    """Create the service (or update it in place). Returns the Service resource incl. registryResource."""
    cur = get_service(r, parent, sid)
    if cur is None:
        try:
            op = r.post(services_url(parent), json=body, params={"serviceId": sid})
            r.wait(op, AGENTREGISTRY, timeout=timeout, interval=3)
        except GcpError as e:
            if not e.conflict:
                raise
    elif update:
        mask = [k for k in body if cur.get(k) != body[k]]
        if mask:
            op = r.patch(services_url(parent, sid), json=body, params={"updateMask": ",".join(mask)})
            r.wait(op, AGENTREGISTRY, timeout=timeout, interval=3)
    # registryResource is filled asynchronously; poll briefly
    deadline = time.time() + 120
    while True:
        cur = get_service(r, parent, sid) or {}
        if cur.get("registryResource") or time.time() > deadline:
            return cur
        time.sleep(3)


def delete_service(r: Rest, parent: str, sid: str) -> bool:
    try:
        op = r.delete(services_url(parent, sid))
    except GcpError as e:
        if e.not_found:
            return False
        raise
    r.wait(op, AGENTREGISTRY, timeout=300, interval=3)
    return True


def split_registry_resource(rr: str) -> tuple[str, str]:
    """'projects/N/locations/R/mcpServers/ID' -> ('mcpServers', 'ID')"""
    parts = rr.split("/")
    return parts[-2], parts[-1]


def find_runtime_agent(r: Rest, parent: str, engine: str) -> dict | None:
    """The auto-registered Agent for an Agent Runtime engine (matches on engine id)."""
    eid = engine.rstrip("/").split("/")[-1]
    for a in list_projection(r, parent, "agents"):
        attrs = a.get("attributes", {})
        ref = attrs.get("agentregistry.googleapis.com/system/RuntimeReference", {}).get("uri", "")
        if ref.endswith(f"/reasoningEngines/{eid}") or f"reasoningEngines:{eid}" in a.get("agentId", ""):
            return a
    return None
