"""Model Armor checkbox (global, all themes): attach/detach a CONTENT_AUTHZ authz policy that points at the
Model Armor authz extension (created by bootstrap) on the egress and the ingress gateway.

set(ctx, enabled) -> creates / deletes `<prefix>-egress-ma-policy` and `<prefix>-ingress-ma-policy`.
status(ctx)       -> {"enabled", "status": applied|removed|pending|error, "detail"}
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..gcp import gateways as gw
from ..gcp.rest import rest
from .base import Ctx

PATHS = ("egress", "ingress")


def _names(ctx: Ctx, path: str) -> tuple[str, str, str]:
    cfg = ctx.config
    proj = gw.gateway_project(cfg, path)
    ext = f"projects/{proj}/locations/{cfg.region}/authzExtensions/{gw.ma_extension_name(cfg)}"
    return proj, gw.ma_policy_name(cfg, path), ext


def set(ctx: Ctx, enabled: bool) -> dict[str, Any]:  # noqa: A001 (contract name)
    cfg = ctx.config
    r = rest(cfg.project)

    def one(path: str) -> str:
        proj, name, ext = _names(ctx, path)
        if enabled:
            return gw.ensure_policy(r, proj, cfg.region, name,
                                    gw.policy_body(cfg, gw.gateway_resource(cfg, path), "CONTENT_AUTHZ", ext))
        return "deleted" if gw.delete_policy(r, proj, cfg.region, name) else "absent"

    with ThreadPoolExecutor(2) as ex:
        res = dict(zip(PATHS, ex.map(one, PATHS)))
    return {"enabled": enabled, "status": "pending", "detail": str(res)}


def status(ctx: Ctx) -> dict[str, Any]:
    cfg = ctx.config
    r = rest(cfg.project)
    try:
        present = {}
        for path in PATHS:
            proj, name, _ = _names(ctx, path)
            present[path] = gw.get_policy(r, proj, cfg.region, name) is not None
        on = [p for p, v in present.items() if v]
        if len(on) == len(PATHS):
            return {"enabled": True, "status": "applied",
                    "detail": f"CONTENT_AUTHZ Model Armor policy on both gateways (template {cfg.model_armor_template})"}
        if not on:
            return {"enabled": False, "status": "removed", "detail": "No Model Armor authz policy attached"}
        return {"enabled": True, "status": "pending", "detail": f"Model Armor attached only on: {', '.join(on)}"}
    except Exception as e:  # noqa: BLE001
        return {"enabled": False, "status": "error", "detail": f"{type(e).__name__}: {e}"[:500]}


def describe(ctx: Ctx) -> list[str]:
    cfg = ctx.config
    lines = ["# Model Armor on Agent Gateway (CONTENT_AUTHZ)"]
    for path in PATHS:
        proj, name, ext = _names(ctx, path)
        lines += [f"cat > {name}.yaml <<EOF", f"name: {name}", "target:", "  resources:",
                  f"  - {gw.gateway_resource(cfg, path)}", "policyProfile: CONTENT_AUTHZ", "action: CUSTOM",
                  "customProvider:", "  authzExtension:", "    resources:", f"    - {ext}", "EOF",
                  f"gcloud network-security authz-policies import {name} --source={name}.yaml "
                  f"--location={cfg.region} --project={proj}"]
    lines.append("# Off: gcloud network-security authz-policies delete <name> --location=" + cfg.region)
    return lines
