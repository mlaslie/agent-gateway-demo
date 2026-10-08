"""gateway_attach{path: egress|ingress}: bind/unbind the theme's Agent Runtime engine to the shared gateway.

PATCH spec.deploymentSpec.agentGatewayConfig (each direction preserved independently). The PATCH is a
long-running operation that redeploys the engine; apply/remove return once it is accepted and status()
reports pending until the operation finishes.
"""
from __future__ import annotations

from ..gcp import engines
from ..gcp import gateways as gw
from ..themes import Policy, Theme
from . import _live as L
from .base import Ctx, PolicyStatus


def _path(policy: Policy) -> str:
    return policy.params.get("path", "egress")


class GatewayAttach:
    def apply(self, ctx: Ctx, theme: Theme, policy: Policy) -> None:
        p = _path(policy)
        engines.set_gateways(L.r(ctx), ctx.config, L.engine(ctx, theme), p, gw.gateway_resource(ctx.config, p))

    def remove(self, ctx: Ctx, theme: Theme, policy: Policy) -> None:
        engines.set_gateways(L.r(ctx), ctx.config, L.engine(ctx, theme), _path(policy), None)

    def remove_many(self, ctx: Ctx, theme: Theme, policies: list[Policy]) -> None:
        """Detach several directions in ONE engine PATCH (used by reset)."""
        engines.set_gateway_paths(L.r(ctx), ctx.config, L.engine(ctx, theme), {_path(p): None for p in policies})

    def status(self, ctx: Ctx, theme: Theme, policy: Policy) -> PolicyStatus:
        try:
            r, cfg, p = L.r(ctx), ctx.config, _path(policy)
            e = L.engine(ctx, theme)
            eng = engines.get_engine(r, cfg, e)
            if eng is None:
                return L.status(False, f"engine {e} not found", "error")
            bound = engines.bound_gateway(eng, p)
            want = gw.gateway_resource(cfg, p)
            rec = engines.inflight(r, cfg, e)
            if rec and rec.get("path") is None:
                return L.status(bool(bound), f"engine update in progress (operation {rec['op'].split('/')[-1]})",
                                "pending" if not bound else "pending_removal")
            paths = (rec or {}).get("paths") or ({rec["path"]: rec.get("target")} if rec and rec.get("path") else {})
            if rec and p in paths:
                if rec.get("error"):
                    return L.status(bool(bound), f"gateway PATCH failed: {rec['error']}", "error")
                target_on = paths[p] is not None
                return L.status(target_on, f"engine redeploying with {p} gateway "
                                f"{'attached' if target_on else 'detached'} (PATCH {rec['op'].split('/')[-1]})",
                                "pending" if target_on else "pending_removal")
            if bound and not bound.endswith(want.split("/locations/", 1)[1]):
                return L.status(True, f"bound to a different gateway: {bound}", "error")
            return L.status(bool(bound), f"{p} gateway {'attached: ' + bound if bound else 'not attached'}")
        except Exception as ex:  # noqa: BLE001
            return L.error(ex)

    def describe(self, ctx: Ctx, theme: Theme, policy: Policy) -> list[str]:
        cfg, p = ctx.config, _path(policy)
        key = engines.KEYS[p]
        try:
            e = L.engine(ctx, theme)
        except Exception:
            e = f"projects/{cfg.project}/locations/{cfg.region}/reasoningEngines/ENGINE_ID"
        return [
            f"# Attach the {p} Agent Gateway to {theme.orchestrator.display_name}",
            f"curl -X PATCH -H \"Authorization: Bearer $(gcloud auth print-access-token)\" \\",
            f"  -H 'Content-Type: application/json' \\",
            f"  'https://{cfg.region}-aiplatform.googleapis.com/v1/{e}?updateMask={engines.MASK}' \\",
            f"  -d '{{\"spec\":{{\"deploymentSpec\":{{\"agentGatewayConfig\":{{\"{key}\":"
            f"{{\"agentGateway\":\"{gw.gateway_resource(cfg, p)}\"}}}}}}}}}}'",
            "# Remove: PATCH the same field without this direction (the other direction is re-sent unchanged).",
            "# The engine redeploys (minutes). Once attached, the gateway is default-deny: "
            + ("only registered destinations with roles/iap.egressor are reachable." if p == "egress"
               else "only callers allowed by the ingress IAP policy reach query/streamQuery."),
        ]


HANDLER = GatewayAttach()
