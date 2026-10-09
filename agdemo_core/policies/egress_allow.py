"""a2a_allow / mcp_server_allow / mcp_tool_allow: roles/iap.egressor for the orchestrator's Agent Identity on
(the orchestrator named by params.source, else the primary; docs/CONTRACTS.md §12)
the target's Agent Registry resource (IAP IAM policy, `gcloud iap web ... --resource-type=agent-registry`).

mcp_tool_allow adds a *conditional* binding (verified attribute iap.googleapis.com/mcp.toolName):
  read_only: true  -> the theme's read-only tools by name   (or the isReadOnly annotation if
                      params.use_annotation: true -> iap.googleapis.com/mcp.tool.isReadOnly)
  tools: [a, b]    -> exactly these tools
Each policy owns exactly one binding (identified by its condition expression), so `tickets-readonly`
and `tickets-all` can be applied together and removed independently (IAM bindings are OR'ed).
"""
from __future__ import annotations

from ..gcp import iap
from ..themes import Policy, Theme
from . import _live as L
from .base import Ctx, PolicyStatus


def condition(theme: Theme, policy: Policy) -> dict | None:
    if policy.type != "mcp_tool_allow":
        return None
    target = policy.params["target"]
    title = f"agdemo {theme.id} {policy.id}"
    if policy.params.get("read_only"):
        if policy.params.get("use_annotation"):
            return iap.read_only_condition(title)
        tools = [t.name for t in theme.mcp_servers[target].tools if t.read_only]
        return iap.tool_name_condition(tools, title)
    return iap.tool_name_condition(list(policy.params.get("tools", [])), title)


def _url(ctx: Ctx, theme: Theme, policy: Policy) -> str:
    return iap.from_registry_resource(L.component_registry(ctx, theme, policy.params["target"]))


class EgressAllow:
    def apply(self, ctx: Ctx, theme: Theme, policy: Policy) -> None:
        iap.add_binding(L.r(ctx), _url(ctx, theme, policy), L.principal(ctx, theme, L.source(policy)), iap.EGRESSOR,
                        condition(theme, policy), replace_member_bindings=False)

    def remove(self, ctx: Ctx, theme: Theme, policy: Policy) -> None:
        iap.remove_binding(L.r(ctx), _url(ctx, theme, policy), L.principal(ctx, theme, L.source(policy)), iap.EGRESSOR,
                           condition(theme, policy))

    def status(self, ctx: Ctx, theme: Theme, policy: Policy) -> PolicyStatus:
        try:
            pol = iap.get_policy(L.r(ctx), _url(ctx, theme, policy))
            cond = condition(theme, policy)
            on = iap.has_binding(pol, L.principal(ctx, theme, L.source(policy)), iap.EGRESSOR, cond)
            what = f"conditional ({cond['expression']})" if cond else "unconditional"
            return L.status(on, f"roles/iap.egressor {what} on {policy.params['target']}: "
                                f"{'granted' if on else 'not granted'}")
        except Exception as ex:  # noqa: BLE001
            return L.error(ex)

    def describe(self, ctx: Ctx, theme: Theme, policy: Policy) -> list[str]:
        cfg = ctx.config
        target = policy.params["target"]
        try:
            rr = L.component_registry(ctx, theme, target)
            kind, rid = rr.split("/")[-2], rr.split("/")[-1]
        except Exception:
            kind, rid = ("agents" if theme.component(target).kind == "a2a_agent" else "mcpServers"), "REGISTRY_ID"
        try:
            member = L.principal(ctx, theme, L.source(policy))
        except Exception:
            member = "principal://agents.global.org-ORG_ID.system.id.goog/resources/aiplatform/projects/" \
                     "PROJECT_NUMBER/locations/REGION/reasoningEngines/ENGINE_ID"
        flag = {"agents": "--agent", "mcpServers": "--mcp-server", "endpoints": "--endpoint"}.get(kind, "--agent")
        cond = condition(theme, policy)
        src = L.source(policy)
        who = theme.orchestrator_for(src).display_name
        lines = [f"# {policy.text}", f"# Member: {who}'s Agent Identity" + (f" (orchestrator {src})" if src else ""),
                 "gcloud iap web add-iam-policy-binding --resource-type=agent-registry \\",
                 f"  {flag}={rid} --region={cfg.region} --project={cfg.gateway_project} \\",
                 f"  --member='{member}' \\",
                 "  --role=roles/iap.egressor" + (" \\" if cond else "")]
        if cond:
            lines.append(f'  --condition="^|^title={cond["title"]}|expression={cond["expression"]}"')
        lines.append("# Remove: gcloud iap web remove-iam-policy-binding with the same flags.")
        return lines


HANDLER = EgressAllow()
