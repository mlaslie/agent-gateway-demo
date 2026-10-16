"""T4 "Under the hood" for the Private Networking mode: the pre-built network pieces and the connectivity
template behind a theme's VPC-enabled gateway (docs/TERRAFORM_PLAN.md §7c).

Source, in order: state.json shared.regions.<region> (written by `./agdemo tf output-sync` from the Terraform
`state_regions` output), else names computed from config/demo.yaml (what `up` would create, "planned"), else
placeholders (no config: Demo only). Pure: no GCP call.
"""
from __future__ import annotations

from typing import Any

from .config import DemoConfig
from .themes import Theme, theme_region

AGENTGATEWAY_ROLES = ["roles/compute.networkAdmin", "roles/dns.peer"]


def _pieces(region: str, n: dict[str, str], project: str, number: str, psc_ip: str, subnet_range: str,
            domains: list[str], vpc_egress: str, template_id: str, gateway: str) -> dict[str, Any]:
    return {
        "network": f"projects/{project}/global/networks/{n['network']}",
        "subnet": f"projects/{project}/regions/{region}/subnetworks/{n['subnet']}",
        "subnet_range": subnet_range,
        "attachment": f"projects/{project}/regions/{region}/networkAttachments/{n['attachment']}",
        "psc_address": f"projects/{project}/global/addresses/{n['psc_address']}",
        "psc_ip": psc_ip,
        "psc_forwarding_rule": f"projects/{project}/global/forwardingRules/{n['psc_forwarding_rule']}",
        "psc_target": "all-apis",
        "dns_zone": f"projects/{project}/managedZones/{n['dns_zone']}",
        "dns_name": "run.app.",
        "dns_records": ["*.run.app.", "run.app."],
        "template": f"projects/{project}/locations/{region}/agentConnectivityTemplates/{template_id}",
        "template_id": template_id,
        "template_ref": f"projects/{number}/locations/{region}/agentConnectivityTemplates/{template_id}",
        "deployment_model": "CENTRALIZED",
        "vpc_egress": vpc_egress,
        "dns_domains": domains,
        "service_agent": f"serviceAccount:service-{number}@gcp-sa-agentgateway.iam.gserviceaccount.com",
        "service_agent_roles": list(AGENTGATEWAY_ROLES),
        "gateway": gateway,
    }


def view(cfg: DemoConfig | None, state: dict[str, Any] | None, theme: Theme, live: bool = False) -> dict[str, Any]:
    region = theme_region(cfg, theme)
    if theme.network_mode != "private_networking":
        return {"theme": theme.id, "network_mode": theme.network_mode, "region": region, "source": "none",
                "pieces": None}
    sec = ((state or {}).get("shared") or {}).get("regions", {}).get(region) or {}
    pn = sec.get("private_networking")
    if live and pn:
        pieces = {**pn, "gateway": sec.get("egress_gateway", "")}
        source = "state"
    elif cfg is not None and cfg.private_networking.enabled and cfg.private_networking.region == region:
        from .infra import common as c
        v = cfg.for_region(region)
        n = c.pn_names(v)
        p = cfg.private_networking
        pieces = _pieces(region, n, v.gateway_project, "PROJECT_NUMBER", p.psc_ip, p.subnet_range,
                         list(p.dns_domains), p.vpc_egress, f"{n['template_prefix']}-<hash>",
                         v.egress_gateway_resource)
        if pn and not live:      # Demo mode on a deployed environment: show the real names anyway
            pieces = {**pn, "gateway": sec.get("egress_gateway", "")}
        source = "state" if pn and not live else "config"
    else:
        prefix = cfg.prefix if cfg else "agdemo"
        n = {"network": f"{prefix}-pn-vpc", "subnet": f"{prefix}-pn-att", "attachment": f"{prefix}-pn-att",
             "psc_address": f"{prefix}-pn-psc-ip", "psc_forwarding_rule": f"{prefix.replace('-', '')}pnpsc",
             "dns_zone": f"{prefix}-pn-run-app"}
        pieces = _pieces(region, n, "PROJECT_ID", "PROJECT_NUMBER", "10.70.250.10", "10.70.0.0/28", ["run.app."],
                         "PRIVATE_RANGES_ONLY", f"{prefix}-pn-tmpl-<hash>",
                         f"projects/PROJECT_ID/locations/{region}/agentGateways/{prefix}-pn-egress")
        source = "placeholder"
    internal = [c.id for c in theme.components if c.ingress == "internal"]
    public = [c.id for c in theme.components if c.ingress != "internal"]
    return {
        "theme": theme.id, "network_mode": theme.network_mode, "region": region, "source": source,
        "pieces": pieces, "internal_components": internal, "public_components": public,
        "notes": [
            "The gateway was created WITH the connectivity template (the verified path); bare -> template by "
            "PATCH fails (P0 T2).",
            "Inside the template the attachment and network use the project ID; the gateway references the "
            "template by project NUMBER.",
            "PRIVATE_RANGES_ONLY: only names that resolve to private IPs (run.app -> the PSC endpoint) go into "
            "the VPC; Google APIs leave directly.",
            "Proof of the private path: the gateway log's httpRequest.serverIp is the PSC endpoint IP. Cloud "
            "Run's own log shows 0.0.0.0.",
            "Not deployed, only explained: an agent with its own PSC interface reaches the VPC without the "
            "gateway (ungoverned).",
        ],
    }
