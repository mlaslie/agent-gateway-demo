"""ingress_allow{caller: allowed|denied}: let that caller reach the orchestrator through the ingress
(CLIENT_TO_AGENT) gateway.

Mechanism: IAP IAM policy on the orchestrator's auto-registered Agent Registry entry (the Agent Runtime
engine is registered automatically as `agents/<id>`), evaluated by the REQUEST_AUTHZ IAP extension on the
ingress gateway. INGRESS_ROLE is the role verified in docs/ARCHITECTURE.md §Ingress.
"""
from __future__ import annotations

from ..gcp import iap, registry
from ..infra import common as c
from ..themes import Policy, Theme
from . import _live as L
from .base import Ctx, PolicyStatus

INGRESS_ROLE = iap.ACCESSOR      # roles/iap.httpsResourceAccessor (see ARCHITECTURE.md §Ingress)


def members(ctx: Ctx, policy: Policy) -> list[str]:
    caller = policy.params.get("caller", "allowed")
    return c.allowed_members(ctx.config) if caller == "allowed" else [c.denied_member(ctx.config)]


def agent_registry_resource(ctx: Ctx, theme: Theme) -> str:
    o = L.theme_state(ctx, theme).get("orchestrator", {})
    if o.get("registry"):
        return o["registry"]
    a = registry.find_runtime_agent(L.r(ctx), ctx.config.registry_parent if ctx.config.gateway_project ==
                                    ctx.config.project else f"projects/{ctx.config.project}/locations/{ctx.config.region}",
                                    L.engine(ctx, theme))
    if not a:
        raise L.NotDeployed("orchestrator is not (yet) auto-registered in Agent Registry")
    return a["name"]


def _url(ctx: Ctx, theme: Theme) -> str:
    rr = agent_registry_resource(ctx, theme)
    parts = rr.split("/")
    from ..gcp.rest import project_number
    num = parts[1] if parts[1].isdigit() else project_number(parts[1])
    return iap.resource_url(num, parts[3], parts[4], parts[5])


class IngressAllow:
    def apply(self, ctx: Ctx, theme: Theme, policy: Policy) -> None:
        url = _url(ctx, theme)
        for m in members(ctx, policy):
            iap.add_binding(L.r(ctx), url, m, INGRESS_ROLE, None, replace_member_bindings=False)

    def remove(self, ctx: Ctx, theme: Theme, policy: Policy) -> None:
        url = _url(ctx, theme)
        for m in members(ctx, policy):
            iap.remove_binding(L.r(ctx), url, m, INGRESS_ROLE, None)

    def status(self, ctx: Ctx, theme: Theme, policy: Policy) -> PolicyStatus:
        try:
            pol = iap.get_policy(L.r(ctx), _url(ctx, theme))
            ms = members(ctx, policy)
            have = [m for m in ms if iap.has_binding(pol, m, INGRESS_ROLE, None)]
            if have and len(have) < len(ms):
                return L.status(True, f"partially granted: {', '.join(have)}")
            return L.status(bool(have), f"{INGRESS_ROLE} on the orchestrator's registry entry for "
                                        f"{', '.join(ms)}: {'granted' if have else 'not granted'}")
        except Exception as ex:  # noqa: BLE001
            return L.error(ex)

    def describe(self, ctx: Ctx, theme: Theme, policy: Policy) -> list[str]:
        cfg = ctx.config
        try:
            rid = agent_registry_resource(ctx, theme).split("/")[-1]
        except Exception:
            rid = "ORCHESTRATOR_REGISTRY_AGENT_ID"
        lines = [f"# {policy.text}"]
        for m in members(ctx, policy):
            lines += ["gcloud iap web add-iam-policy-binding --resource-type=agent-registry \\",
                      f"  --agent={rid} --region={cfg.region} --project={cfg.project} \\",
                      f"  --member='{m}' --role={INGRESS_ROLE}"]
        lines.append("# Enforced by the REQUEST_AUTHZ IAP policy on the ingress gateway "
                     f"({cfg.ingress_gateway}); callers not granted get 403.")
        return lines


HANDLER = IngressAllow()
