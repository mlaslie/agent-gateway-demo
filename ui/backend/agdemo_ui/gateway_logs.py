"""Gateway log feed for the UI (docs/CONTRACTS.md §9).

Live: real entries from Cloud Logging (agdemo_core.gcp.gateway_logs).
Demo / replayed runs: entries synthesized from the run's governed edge results, in the same shape as the real
ones (simulated: true), and revealed after a short delay to mimic Cloud Logging ingestion.
"""
from __future__ import annotations

import random
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any

from agdemo_core.themes import Theme

POLICY = {"denied": "agdemo-egress-iap-policy", "blocked": "agdemo-egress-ma-policy"}


def parse_ts(iso: str) -> float:
    """Any RFC 3339 form (ms or µs precision, 'Z' or offset) -> epoch seconds."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


class SimLogBook:
    def __init__(self, maxlen: int = 500) -> None:
        self.entries: dict[str, deque[tuple[float, dict[str, Any]]]] = {}
        self.maxlen = maxlen

    def add_edges(self, theme: Theme, edges: dict[str, dict[str, Any]], prefix: str = "agdemo",
                  gateway: str = "agdemo-egress") -> None:
        """Synthesize one entry per governed egress edge result (allowed / denied / blocked)."""
        book = self.entries.setdefault(theme.id, deque(maxlen=self.maxlen))
        now = time.time()
        for edge, st in edges.items():
            if edge.startswith("ingress:") or not st.get("governed") or st.get("state") not in POLICY | {"allowed": 1}:
                continue
            book.append((now + random.uniform(3, 8), self._entry(theme, edge, st["state"], now, prefix, gateway)))

    @staticmethod
    def _entry(theme: Theme, edge: str, decision: str, ts: float, prefix: str, gateway: str) -> dict[str, Any]:
        comp, _, tool = edge.partition(":")
        host = f"{prefix}-{theme.id}-{comp}-000000000000.us-east4.run.app"
        status = 200 if decision == "allowed" else 403
        url = f"https://{host}/mcp" if tool else f"https://{host}/"
        mcp = {"method": "tools/call", "parameter": tool} if tool else None
        pols = [{"name": f"projects/0/locations/us-east4/authzPolicies/{POLICY['denied']}",
                 "result": "DENIED" if decision == "denied" else "ALLOWED"}]
        if decision == "blocked":
            pols.append({"name": f"projects/0/locations/us-east4/authzPolicies/{POLICY['blocked']}", "result": "DENIED"})
        insert_id = uuid.uuid4().hex[:14]
        raw = {
            "insertId": insert_id, "timestamp": _iso(ts), "severity": "INFO" if status == 200 else "WARNING",
            "logName": "projects/my-demo-project/logs/networkservices.googleapis.com%2Fgateway_requests",
            "resource": {"type": "networkservices.googleapis.com/Gateway",
                         "labels": {"gateway_name": gateway, "gateway_type": "SECURE_WEB_GATEWAY", "location": "us-east4"}},
            "httpRequest": {"requestMethod": "POST", "requestUrl": url, "status": status, "protocol": "HTTP/1.1",
                            "userAgent": "python-httpx2/2.13.1"},
            "jsonPayload": {
                "@type": "type.googleapis.com/google.cloud.loadbalancing.type.LoadBalancerLogEntry",
                "agentGatewayInfo": {"agentRegistryResource": f"projects/0/locations/us-east4/"
                                     f"{'mcpServers' if tool else 'agents'}/agentregistry-{comp}",
                                     **({"mcpInfo": mcp} if mcp else {})},
                "authzPolicyInfo": {"policies": pols, "result": "ALLOWED" if decision == "allowed" else "DENIED"},
                "enforcedGatewaySecurityPolicy": {"hostname": host, "requestWasTlsIntercepted": True},
            },
        }
        denying = POLICY.get(decision)
        spec = theme.a2a_agents.get(comp) or theme.mcp_servers.get(comp)
        what = f"tools/call {tool}" if tool else f"POST / → {spec.display_name if spec else comp}"
        return {
            "id": insert_id, "timestamp": raw["timestamp"], "gateway": gateway, "decision": decision,
            "status": status, "method": "POST", "url": url, "host": host,
            "mcp_method": "tools/call" if tool else None, "mcp_tool": tool or None,
            "edge": edge, "component": comp,
            "policies": [{"name": p["name"].rsplit("/", 1)[-1],
                          "kind": "model_armor" if "-ma-" in p["name"] else "iap", "result": p["result"]} for p in pols],
            "decided_by": denying,
            "summary": f"{decision.upper()}{' by ' + denying if denying else ''} · {what} · {status}",
            "console_url": None, "simulated": True, "raw": raw,
        }

    def list(self, theme_id: str, since: str | None, denied_only: bool, limit: int) -> list[dict[str, Any]]:
        now, floor = time.time(), parse_ts(since) if since else 0.0
        out = [e for visible_at, e in self.entries.get(theme_id, ()) if visible_at <= now
               and parse_ts(e["timestamp"]) >= floor
               and (not denied_only or e["decision"] != "allowed")]
        return sorted(out, key=lambda e: e["timestamp"], reverse=True)[:limit]
