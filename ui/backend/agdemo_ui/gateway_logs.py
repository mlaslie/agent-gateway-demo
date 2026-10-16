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

from agdemo_core.themes import Theme, split_source

POLICY = {"denied": "agdemo-egress-iap-policy", "blocked": "agdemo-egress-ma-policy"}
PN_POLICY = {"denied": "agdemo-pn-egress-iap-policy"}
SIM_PSC_IP = "10.70.250.10"          # demo.example.yaml private_networking.psc_ip
SIM_PUBLIC_IP = "34.143.77.2"        # a public Google front-end IP shape, for the internet path


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
                  gateway: str | None = None, region: str | None = None, psc_ip: str = SIM_PSC_IP) -> None:
        """Synthesize one entry per governed egress edge result (allowed / denied / blocked). A Private
        Networking theme's entries name its VPC-enabled gateway and carry httpRequest.serverIp: the PSC
        endpoint IP for an internal target (private path), a public IP otherwise."""
        pn = theme.network_mode == "private_networking"
        gateway = gateway or (f"{prefix}-pn-egress" if pn else f"{prefix}-egress")
        region = region or theme.region or "us-east4"
        book = self.entries.setdefault(theme.id, deque(maxlen=self.maxlen))
        now = time.time()
        for edge, st in edges.items():
            if edge.startswith("ingress:") or not st.get("governed") or st.get("state") not in POLICY | {"allowed": 1}:
                continue
            book.append((now + random.uniform(3, 8), self._entry(theme, edge, st["state"], now, prefix, gateway,
                                                                 region, psc_ip)))

    @staticmethod
    def _entry(theme: Theme, edge: str, decision: str, ts: float, prefix: str, gateway: str,
               region: str = "us-east4", psc_ip: str = SIM_PSC_IP) -> dict[str, Any]:
        from agdemo_core.gcp.gateway_logs import is_private_ip
        comp, _, tool = split_source(edge)[1].partition(":")     # '<orchestrator>/' prefix: same destination
        pn = theme.network_mode == "private_networking"
        pol_names = {**POLICY, **{k: v.replace("agdemo-", f"{prefix}-", 1) for k, v in PN_POLICY.items()}} if pn \
            else POLICY
        host = f"{prefix}-{theme.id}-{comp}-000000000000.{region}.run.app"
        status = 200 if decision == "allowed" else 403
        url = f"https://{host}/mcp" if tool else f"https://{host}/"
        mcp = {"method": "tools/call", "parameter": tool} if tool else None
        pols = [{"name": f"projects/0/locations/{region}/authzPolicies/{pol_names['denied']}",
                 "result": "DENIED" if decision == "denied" else "ALLOWED"}]
        if decision == "blocked":
            pols.append({"name": f"projects/0/locations/{region}/authzPolicies/{POLICY['blocked']}", "result": "DENIED"})
        # Denied entries carry no serverIp (P0 §2.3); allowed ones do.
        sip = None
        if pn and decision == "allowed":
            sip = f"{psc_ip}:443" if theme.internal(comp) else f"{SIM_PUBLIC_IP}:443"
        insert_id = uuid.uuid4().hex[:14]
        raw = {
            "insertId": insert_id, "timestamp": _iso(ts), "severity": "INFO" if status == 200 else "WARNING",
            "logName": "projects/my-demo-project/logs/networkservices.googleapis.com%2Fgateway_requests",
            "resource": {"type": "networkservices.googleapis.com/Gateway",
                         "labels": {"gateway_name": gateway, "gateway_type": "SECURE_WEB_GATEWAY", "location": region}},
            "httpRequest": {"requestMethod": "POST", "requestUrl": url, "status": status, "protocol": "HTTP/1.1",
                            "userAgent": "python-httpx2/2.13.1", **({"serverIp": sip} if sip else {})},
            "jsonPayload": {
                "@type": "type.googleapis.com/google.cloud.loadbalancing.type.LoadBalancerLogEntry",
                "agentGatewayInfo": {"agentRegistryResource": f"projects/0/locations/{region}/"
                                     f"{'mcpServers' if tool else 'agents'}/agentregistry-{comp}",
                                     **({"mcpInfo": mcp} if mcp else {})},
                "authzPolicyInfo": {"policies": pols, "result": "ALLOWED" if decision == "allowed" else "DENIED"},
                "enforcedGatewaySecurityPolicy": {"hostname": host, "requestWasTlsIntercepted": True},
            },
        }
        denying = pol_names.get(decision)
        private = is_private_ip(sip)
        spec = theme.a2a_agents.get(comp) or theme.mcp_servers.get(comp)
        what = f"tools/call {tool}" if tool else f"POST / → {spec.display_name if spec else comp}"
        return {
            "id": insert_id, "timestamp": raw["timestamp"], "gateway": gateway, "decision": decision,
            "status": status, "method": "POST", "url": url, "host": host,
            "mcp_method": "tools/call" if tool else None, "mcp_tool": tool or None,
            "edge": edge, "component": comp,
            "policies": [{"name": p["name"].rsplit("/", 1)[-1],
                          "kind": "model_armor" if "-ma-" in p["name"] else "iap", "result": p["result"]} for p in pols],
            "decided_by": denying, "server_ip": sip, "private_path": private,
            "summary": f"{decision.upper()}{' by ' + denying if denying else ''} · {what} · {status}"
                       + (" · private path" if private else ""),
            "console_url": None, "simulated": True, "raw": raw,
        }

    def list(self, theme_id: str, since: str | None, denied_only: bool, limit: int) -> list[dict[str, Any]]:
        now, floor = time.time(), parse_ts(since) if since else 0.0
        out = [e for visible_at, e in self.entries.get(theme_id, ()) if visible_at <= now
               and parse_ts(e["timestamp"]) >= floor
               and (not denied_only or e["decision"] != "allowed")]
        return sorted(out, key=lambda e: e["timestamp"], reverse=True)[:limit]
