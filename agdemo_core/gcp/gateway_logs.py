"""Agent Gateway request logs from Cloud Logging (docs/CONTRACTS.md §9).

Every egress request the gateway handles is logged to
  projects/P/logs/networkservices.googleapis.com%2Fgateway_requests   (resource networkservices.googleapis.com/Gateway)
with the HTTP status, the authz policy decisions (IAP / Model Armor), the target host and, for MCP, the
JSON-RPC method and tool name. The ingress gateway writes no request logs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, urlparse

from ..config import DemoConfig
from ..themes import Theme
from .rest import Rest

LOG_ID = "networkservices.googleapis.com%2Fgateway_requests"
ENTRIES_URL = "https://logging.googleapis.com/v2/entries:list"


def theme_hosts(state: dict[str, Any], theme: Theme) -> dict[str, str]:
    """{hostname: component id} for the theme's deployed Cloud Run targets."""
    comps = state.get("themes", {}).get(theme.id, {}).get("components", {})
    return {urlparse(c["url"]).hostname: cid for cid, c in comps.items() if c.get("url")}


def build_filter(cfg: DemoConfig, hosts: list[str], since: str, denied_only: bool) -> str:
    parts = [f'logName="projects/{cfg.gateway_project}/logs/{LOG_ID}"',
             f'resource.labels.gateway_name="{cfg.egress_gateway}"',
             f'timestamp>="{since}"']
    if hosts:
        parts.append("jsonPayload.enforcedGatewaySecurityPolicy.hostname=("
                     + " OR ".join(f'"{h}"' for h in hosts) + ")")
    if denied_only:
        parts.append('(httpRequest.status>=400 OR jsonPayload.authzPolicyInfo.result="DENIED")')
    return " AND ".join(parts)


def query_url(cfg: DemoConfig, flt: str) -> str:
    return f"https://console.cloud.google.com/logs/query;query={quote(flt, safe='')}?project={cfg.gateway_project}"


def entry_url(cfg: DemoConfig, insert_id: str, ts: str) -> str:
    flt = f'insertId="{insert_id}"\ntimestamp="{ts}"'
    return (f"https://console.cloud.google.com/logs/query;query={quote(flt, safe='')}"
            f";cursorTimestamp={quote(ts, safe='')}?project={cfg.gateway_project}")


def policy_kind(name: str) -> str:
    n = name.lower()
    if "-ma-" in n or "model" in n and "armor" in n:
        return "model_armor"
    if "iap" in n:
        return "iap"
    return "other"


def parse_entry(cfg: DemoConfig, theme: Theme, hosts: dict[str, str], e: dict[str, Any]) -> dict[str, Any]:
    http = e.get("httpRequest") or {}
    jp = e.get("jsonPayload") or {}
    agw = jp.get("agentGatewayInfo") or {}
    mcp = agw.get("mcpInfo") or {}
    host = (jp.get("enforcedGatewaySecurityPolicy") or {}).get("hostname") or urlparse(
        http.get("requestUrl", "")).hostname
    pols = [{"name": p.get("name", "").rsplit("/", 1)[-1], "kind": policy_kind(p.get("name", "")),
             "result": p.get("result", "")} for p in (jp.get("authzPolicyInfo") or {}).get("policies", [])]
    denying = next((p for p in pols if p["result"] == "DENIED"), None)
    status = int(http.get("status") or 0)
    if denying:
        decision = "blocked" if denying["kind"] == "model_armor" else "denied"
    else:
        decision = "denied" if status == 403 else "allowed"
    comp = hosts.get(host or "")
    tool = mcp.get("parameter") if mcp.get("method") == "tools/call" else None
    edge = None
    if comp:
        kind = next((c.kind for c in theme.components if c.id == comp), None)
        edge = f"{comp}:{tool}" if (kind == "mcp_server" and tool) else comp
    what = " ".join(x for x in [mcp.get("method"), mcp.get("parameter")] if x) or \
        f"{http.get('requestMethod', '')} {urlparse(http.get('requestUrl', '')).path or '/'}"
    if not tool and comp:                       # A2A / non-tool calls: name the target so entries are distinct
        spec = theme.a2a_agents.get(comp) or theme.mcp_servers.get(comp)
        what += f" → {spec.display_name if spec else comp}"
    label = decision.upper() + (f" by {denying['name']}" if denying else "")
    ts = e.get("timestamp", "")
    return {
        "id": e.get("insertId", ""), "timestamp": ts,
        "gateway": (e.get("resource") or {}).get("labels", {}).get("gateway_name", ""),
        "decision": decision, "status": status, "method": http.get("requestMethod"),
        "url": http.get("requestUrl"), "host": host,
        "mcp_method": mcp.get("method"), "mcp_tool": mcp.get("parameter"),
        "edge": edge, "component": comp, "policies": pols,
        "decided_by": denying["name"] if denying else None,
        "summary": f"{label} · {what} · {status}",
        "console_url": entry_url(cfg, e.get("insertId", ""), ts),
        "simulated": False, "raw": e,
    }


def fetch(r: Rest, cfg: DemoConfig, state: dict[str, Any], theme: Theme, since: str | None = None,
          denied_only: bool = False, limit: int = 50) -> dict[str, Any]:
    since_dt = (datetime.fromisoformat(since.replace("Z", "+00:00")) if since
                else datetime.now(timezone.utc) - timedelta(minutes=15))
    since = since_dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    hosts = theme_hosts(state, theme)
    flt = build_filter(cfg, sorted(hosts), since, denied_only)
    body = {"resourceNames": [f"projects/{cfg.gateway_project}"], "filter": flt,
            "orderBy": "timestamp desc", "pageSize": max(1, min(limit, 500))}
    raw = r.post(ENTRIES_URL, json=body).get("entries", [])
    return {"source": "live", "filter": flt, "console_url": query_url(cfg, flt),
            "entries": [parse_entry(cfg, theme, hosts, e) for e in raw]}
