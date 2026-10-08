"""Agent Gateways, Service Extensions authz extensions and Network Security authz policies (REST).

Verified endpoints (gcloud --log-http, Oct 2026):
  agentGateways    https://networkservices.googleapis.com/v1/projects/P/locations/R/agentGateways
  authzExtensions  https://networkservices.googleapis.com/v1beta1/projects/P/locations/R/authzExtensions
  authzPolicies    https://networksecurity.googleapis.com/v1/projects/P/locations/R/authzPolicies
"""
from __future__ import annotations

from typing import Any, Callable

from ..config import DemoConfig
from .rest import NETWORKSECURITY, NETWORKSERVICES, NETWORKSERVICES_BETA, GcpError, Rest, rest


def _loc(project: str, region: str) -> str:
    return f"projects/{project}/locations/{region}"


# ------------------------------------------------------------------ names (all derived from config)
def iap_extension_name(cfg: DemoConfig, path: str) -> str:
    return cfg.name(path, "iap-authz")                       # agdemo-egress-iap-authz


def iap_policy_name(cfg: DemoConfig, path: str) -> str:
    return cfg.name(path, "iap-policy")                      # agdemo-egress-iap-policy


def ma_extension_name(cfg: DemoConfig) -> str:
    return cfg.name("ma-authz")                              # agdemo-ma-authz


def ma_policy_name(cfg: DemoConfig, path: str) -> str:
    return cfg.name(path, "ma-policy")                       # agdemo-egress-ma-policy


def gateway_project(cfg: DemoConfig, path: str) -> str:
    return cfg.gateway_project if path == "egress" else cfg.project


def gateway_name(cfg: DemoConfig, path: str) -> str:
    return cfg.egress_gateway if path == "egress" else cfg.ingress_gateway


def gateway_resource(cfg: DemoConfig, path: str) -> str:
    return cfg.egress_gateway_resource if path == "egress" else cfg.ingress_gateway_resource


# ------------------------------------------------------------------ gateways
def get_gateway(cfg: DemoConfig, path: str, r: Rest | None = None) -> dict | None:
    r = r or rest(cfg.project)
    return r.get(f"{NETWORKSERVICES}/{gateway_resource(cfg, path)}", ok404=True)


def list_gateways(r: Rest, project: str, region: str) -> list[dict]:
    return r.list_all(f"{NETWORKSERVICES}/{_loc(project, region)}/agentGateways", "agentGateways")


def gateway_body(cfg: DemoConfig, path: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "protocols": ["MCP"],
        "googleManaged": {"governedAccessPath": "AGENT_TO_ANYWHERE" if path == "egress" else "CLIENT_TO_AGENT"},
        "labels": cfg.labels,
        "description": f"{cfg.prefix} demo {path} gateway",
    }
    if path == "egress":
        body["registries"] = [f"//agentregistry.googleapis.com/{cfg.registry_parent}"]
    return body


def start_create_gateway(cfg: DemoConfig, path: str, r: Rest | None = None) -> dict | None:
    """Starts creation; returns the LRO (or None if it already exists)."""
    r = r or rest(cfg.project)
    if get_gateway(cfg, path, r):
        return None
    proj = gateway_project(cfg, path)
    try:
        return r.post(f"{NETWORKSERVICES}/{_loc(proj, cfg.region)}/agentGateways",
                      json=gateway_body(cfg, path), params={"agentGatewayId": gateway_name(cfg, path)})
    except GcpError as e:
        if e.conflict:
            return None
        raise


def delete_gateway(cfg: DemoConfig, path: str, r: Rest | None = None, wait: bool = True) -> bool:
    r = r or rest(cfg.project)
    try:
        op = r.delete(f"{NETWORKSERVICES}/{gateway_resource(cfg, path)}")
    except GcpError as e:
        if e.not_found:
            return False
        raise
    if wait:
        r.wait(op, NETWORKSERVICES, timeout=1800)
    return True


# ------------------------------------------------------------------ authz extensions
def ext_url(project: str, region: str, name: str = "") -> str:
    return f"{NETWORKSERVICES_BETA}/{_loc(project, region)}/authzExtensions" + (f"/{name}" if name else "")


def iap_extension_body(cfg: DemoConfig) -> dict[str, Any]:
    md: dict[str, Any] = {"iapPolicyVersion": "V1"}   # V1 = IAM allow policies (roles/iap.egressor + conditions)
    dry = cfg.gateways.iap_enforcement == "DRY_RUN"
    if dry:
        md["iamEnforcementMode"] = "DRY_RUN"
    return {"service": "iap.googleapis.com", "failOpen": dry, "timeout": "1s", "metadata": md,
            "labels": cfg.labels}


def ma_extension_body(cfg: DemoConfig, template: str) -> dict[str, Any]:
    import json as _json
    return {
        "service": f"modelarmor.{cfg.region}.rep.googleapis.com",
        "failOpen": False,
        "timeout": "5s",
        "metadata": {"model_armor_settings": _json.dumps(
            [{"request_template_id": template, "response_template_id": template}])},
        "labels": cfg.labels,
    }


def ensure_extension(r: Rest, project: str, region: str, name: str, body: dict[str, Any],
                     update_fields: tuple[str, ...] = ("metadata", "failOpen", "timeout")) -> str:
    """Create, or PATCH if it differs. Returns 'created' | 'updated' | 'ok'."""
    cur = r.get(ext_url(project, region, name), ok404=True)
    if cur is None:
        op = r.post(ext_url(project, region), json=body, params={"authzExtensionId": name})
        r.wait(op, NETWORKSERVICES_BETA, timeout=900, interval=5)
        return "created"
    defaults = {"failOpen": False}
    diff = [f for f in update_fields if cur.get(f, defaults.get(f)) != body.get(f) and not
            (f == "timeout" and str(cur.get(f, "")).rstrip("s").startswith(str(body.get(f, "")).rstrip("s")))]
    if diff:
        op = r.patch(ext_url(project, region, name), json={f: body[f] for f in diff},
                     params={"updateMask": ",".join(diff)})
        r.wait(op, NETWORKSERVICES_BETA, timeout=900, interval=5)
        return "updated"
    return "ok"


def delete_extension(r: Rest, project: str, region: str, name: str) -> bool:
    try:
        op = r.delete(ext_url(project, region, name))
    except GcpError as e:
        if e.not_found:
            return False
        raise
    r.wait(op, NETWORKSERVICES_BETA, timeout=900, interval=5)
    return True


# ------------------------------------------------------------------ authz policies
def pol_url(project: str, region: str, name: str = "") -> str:
    return f"{NETWORKSECURITY}/{_loc(project, region)}/authzPolicies" + (f"/{name}" if name else "")


def policy_body(cfg: DemoConfig, gateway: str, profile: str, extension: str) -> dict[str, Any]:
    return {
        "target": {"resources": [gateway]},
        "policyProfile": profile,                  # REQUEST_AUTHZ (IAP) | CONTENT_AUTHZ (Model Armor)
        "action": "CUSTOM",
        "customProvider": {"authzExtension": {"resources": [extension]}},
        "labels": cfg.labels,
    }


def get_policy(r: Rest, project: str, region: str, name: str) -> dict | None:
    return r.get(pol_url(project, region, name), ok404=True)


def ensure_policy(r: Rest, project: str, region: str, name: str, body: dict[str, Any],
                  wait: bool = True, on_tick: Callable[[float], None] | None = None) -> str:
    if get_policy(r, project, region, name) is not None:
        return "ok"
    try:
        op = r.post(pol_url(project, region), json=body, params={"authzPolicyId": name})
    except GcpError as e:
        if e.conflict:
            return "ok"
        raise
    if wait:
        r.wait(op, NETWORKSECURITY, timeout=900, interval=5, on_tick=on_tick)
    return "created"


def delete_policy(r: Rest, project: str, region: str, name: str, wait: bool = True) -> bool:
    try:
        op = r.delete(pol_url(project, region, name))
    except GcpError as e:
        if e.not_found:
            return False
        raise
    if wait:
        r.wait(op, NETWORKSECURITY, timeout=900, interval=5)
    return True


def list_policies(r: Rest, project: str, region: str) -> list[dict]:
    return r.list_all(pol_url(project, region), "authzPolicies")


def policies_targeting(r: Rest, project: str, region: str, gateway_res: str) -> list[dict]:
    """authz policies whose target is this gateway (target may use project id or number)."""
    gw_tail = gateway_res.split("/locations/", 1)[1]
    return [p for p in list_policies(r, project, region)
            if any(t.endswith(gw_tail) for t in p.get("target", {}).get("resources", []))]
