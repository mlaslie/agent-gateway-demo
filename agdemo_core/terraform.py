"""Export a theme's current policy state as Terraform (backlog #12, docs/CONTRACTS.md §11).

render(cfg, state, theme, applied, model_armor, include_shared) -> {"main.tf", "variables.tf", "README.md"}

Mapping (verified against the hashicorp/google 8.6.0 schema; see docs/BACKLOG.md "#17 research"):
  a2a_allow / mcp_server_allow / mcp_tool_allow -> google_iap_agent_registry_{agent,mcp_server}_iam_member
      (roles/iap.egressor for the orchestrator's Agent Identity; mcp_tool_allow adds the exact condition the demo uses)
  gateway_attach (egress / ingress)              -> terraform_data + local-exec PATCH of the engine (no standalone
      resource binds an existing engine; google_vertex_ai_reasoning_engine only if Terraform owns the engine)
  Model Armor on                                  -> google_network_security_authz_policy CONTENT_AUTHZ per gateway
  include_shared                                  -> gateways, authz extensions, IAP REQUEST_AUTHZ policy, Model Armor
      template, Agent Registry services (components + platform endpoints) and project IAM as real resources.
Agent Runtime engines and Cloud Run services are created by `./agdemo deploy-theme` and only referenced.
Pure function: no GCP calls.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

from .config import DemoConfig, Environment
from .gcp import gateways as gw
from .gcp import iap
from .infra import common as c
from .themes import Policy, Theme

PROVIDER_VERSION = ">= 8.1.0"      # agent_gateway_config on google_vertex_ai_reasoning_engine (8.1.0); rest older
TF_VERSION = ">= 1.5"              # terraform_data (1.4) + import blocks (1.5) mentioned in the README
ENGINE_MASK = "spec.deploymentSpec.agentGatewayConfig"
GW_KEYS = {"egress": "agentToAnywhereConfig", "ingress": "clientToAgentConfig"}
EGRESS_TYPES = ("a2a_allow", "mcp_server_allow", "mcp_tool_allow")
# Project roles deploy-theme grants the orchestrator's Agent Identity (infra/deploy_theme.AGENT_ROLES).
AGENT_ROLES = ["roles/aiplatform.user", "roles/agentregistry.viewer", "roles/logging.logWriter",
               "roles/monitoring.metricWriter", "roles/cloudtrace.agent", "roles/browser",
               "roles/serviceusage.serviceUsageConsumer"]
UI_ROLES = ["roles/aiplatform.user", "roles/agentregistry.viewer", "roles/iap.admin",
            "roles/networkservices.viewer", "roles/networksecurity.editor",
            "roles/networkservices.serviceExtensionsAdmin", "roles/modelarmor.viewer",
            "roles/logging.viewer", "roles/serviceusage.serviceUsageConsumer"]
TARGET_ROLES = ["roles/aiplatform.user", "roles/logging.logWriter"]
MA_AGENT_ROLES = ["roles/modelarmor.calloutUser", "roles/modelarmor.user", "roles/serviceusage.serviceUsageConsumer"]

FILES = ("main.tf", "variables.tf", "README.md")


# ====================================================================== HCL writer
def q(s: str) -> str:
    """HCL quoted string (escapes quotes, backslashes, newlines and template sequences)."""
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return '"' + s.replace("${", "$${").replace("%{", "%%{") + '"'


def tq(s: str) -> str:
    """HCL quoted *template*: like q() but keeps ${...} interpolations."""
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return '"' + s.replace("%{", "%%{") + '"'


def tset(items: list[str]) -> str:
    return "toset([\n" + "".join(f"{q(i)},\n" for i in items) + "])"


def ident(s: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_]", "_", s)
    return out if out and not out[0].isdigit() else f"r_{out}"


def hval(v: Any, ind: int = 0) -> str:
    """Python value -> HCL expression (multi-line objects/lists, aligned the way `terraform fmt` does)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    if isinstance(v, str):
        return q(v)
    pad = "  " * (ind + 1)
    if isinstance(v, list):
        if not v:
            return "[]"
        if all(not isinstance(x, (dict, list)) for x in v):
            return "[" + ", ".join(hval(x) for x in v) + "]"
        return "[\n" + "".join(f"{pad}{hval(x, ind + 1)},\n" for x in v) + "  " * ind + "]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        items = [(k if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) else json.dumps(k), hval(x, ind + 1))
                 for k, x in v.items()]
        return "{\n" + "".join(_align(items, pad)) + "  " * ind + "}"
    raise TypeError(f"unsupported value {v!r}")


def _align(items: list[tuple[str, str]], pad: str) -> list[str]:
    """`key = value` lines; consecutive single-line items share one `=` column (terraform fmt)."""
    out: list[str] = []
    group: list[tuple[str, str]] = []

    def flush() -> None:
        w = max((len(k) for k, _ in group), default=0)
        for k, v in group:
            out.append(f"{pad}{k.ljust(w)} = {v}\n")
        group.clear()

    for k, v in items:
        if "\n" in v:          # a multi-line value ends the alignment run and is not padded itself
            flush()
            out.append(f"{pad}{k} = {v}\n")
        else:
            group.append((k, v))
    flush()
    return out


class Block:
    """An HCL block: header + items. Items: ("a", key, expr) attribute, ("c", text) comment, ("", ) blank,
    ("b", Block) nested block."""

    def __init__(self, *header: str):
        self.header = " ".join(header)
        self.items: list[tuple] = []

    def a(self, key: str, expr: str) -> "Block":
        self.items.append(("a", key, expr))
        return self

    def s(self, key: str, value: str) -> "Block":
        return self.a(key, q(value))

    def t(self, key: str, value: str) -> "Block":
        return self.a(key, tq(value))

    def c(self, text: str) -> "Block":
        for line in text.split("\n"):
            self.items.append(("c", line))
        return self

    def nl(self) -> "Block":
        self.items.append(("",))
        return self

    def b(self, *header: str) -> "Block":
        blk = Block(*header)
        self.items.append(("b", blk))
        return blk

    def render(self, ind: int = 0) -> str:
        pad = "  " * ind
        inner = "  " * (ind + 1)
        out = [f"{pad}{self.header} {{\n"]
        group: list[tuple[str, str]] = []

        def flush() -> None:
            out.extend(_align(group, inner))
            group.clear()

        for it in self.items:
            if it[0] == "a":
                val = it[2]
                if "\n" in val:     # re-indent multi-line expressions (objects, heredocs) to this depth
                    val = _reindent(val, ind + 1)
                group.append((it[1], val))
                continue
            flush()
            if it[0] == "c":
                out.append(f"{inner}# {it[1]}".rstrip() + "\n" if it[1] else f"{inner}#\n")
            elif it[0] == "":
                out.append("\n")
            else:
                out.append(it[1].render(ind + 1))
        flush()
        out.append(f"{pad}}}\n")
        return "".join(out)


def _reindent(val: str, ind: int) -> str:
    if val.lstrip().startswith("<<-"):
        return val          # heredocs are written already indented (see heredoc())
    lines = val.split("\n")
    return "\n".join([lines[0]] + ["  " * ind + ln if ln else ln for ln in lines[1:]])


def heredoc(text: str, ind: int, marker: str = "EOT", template: bool = False) -> str:
    """Indented heredoc for an attribute at nesting depth `ind` (the attribute's own indentation)."""
    pad = "  " * (ind + 1)
    body = text if template else text.replace("${", "$${").replace("%{", "%%{")
    lines = "".join(f"{pad}{ln}\n" if ln else "\n" for ln in body.rstrip("\n").split("\n"))
    return f"<<-{marker}\n{lines}{'  ' * ind}{marker}"


def comment_lines(text: str, width: int = 110) -> list[str]:
    """Wrap plain text into comment lines (without the leading '# ')."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}" if cur else w
    if cur:
        lines.append(cur)
    return lines


def top_comment(text: str) -> str:
    return "".join(f"# {ln}\n" if ln else "#\n" for ln in text.split("\n"))


# ====================================================================== state helpers
def _fallback_cfg() -> DemoConfig:
    return DemoConfig(environment=Environment(project_id="PROJECT_ID"))


def _number_from(*names: str | None) -> str | None:
    for n in names:
        m = re.match(r"projects/(\d+)/", n or "")
        if m:
            return m.group(1)
    return None


def _principal_set(principal: str) -> str | None:
    m = re.match(r"principal://([^/]+)/resources/aiplatform/projects/(\d+)/", principal or "")
    return f"principalSet://{m.group(1)}/attribute.platformContainer/aiplatform/projects/{m.group(2)}" if m else None


class Ctx:
    """Everything the renderer needs, resolved once (state values or clearly marked placeholders)."""

    def __init__(self, cfg: DemoConfig | None, state: dict[str, Any] | None, theme: Theme):
        self.cfg = cfg or _fallback_cfg()
        self.state = state or {}
        self.theme = theme
        self.shared = self.state.get("shared", {}) or {}
        self.tstate = (self.state.get("themes", {}) or {}).get(theme.id, {}) or {}
        self.comps = self.tstate.get("components", {}) or {}
        orch = self.tstate.get("orchestrator", {}) or {}
        cfg = self.cfg
        self.placeholders: list[str] = []
        self.include_shared = False
        self.engine = orch.get("engine") or self._ph(
            f"projects/PROJECT_NUMBER/locations/{cfg.region}/reasoningEngines/ENGINE_ID", "Agent Runtime engine")
        self.principal = orch.get("principal") or self._ph(
            "principal://agents.global.org-ORG_ID.system.id.goog/resources/aiplatform/projects/PROJECT_NUMBER/"
            f"locations/{cfg.region}/reasoningEngines/ENGINE_ID", "orchestrator Agent Identity principal")
        self.project_set = _principal_set(orch.get("principal", "")) or (
            "principalSet://agents.global.org-ORG_ID.system.id.goog/attribute.platformContainer/aiplatform/"
            "projects/PROJECT_NUMBER")
        regs = [v.get("registry") for v in self.comps.values()]
        # Additional orchestrators (CONTRACTS §12): engine + Agent Identity principal per id.
        extra = self.tstate.get("orchestrators", {}) or {}
        self.agent_engines: dict[str, str] = {}
        self.agent_principals: dict[str, str] = {}
        for o in theme.additional_orchestrators:
            rec = extra.get(o.id, {}) or {}
            tag = f"ENGINE_ID_{o.id.upper().replace('-', '_')}"
            self.agent_engines[o.id] = rec.get("engine") or self._ph(
                f"projects/PROJECT_NUMBER/locations/{cfg.region}/reasoningEngines/{tag}",
                f"Agent Runtime engine of {o.id}")
            self.agent_principals[o.id] = rec.get("principal") or self._ph(
                "principal://agents.global.org-ORG_ID.system.id.goog/resources/aiplatform/projects/PROJECT_NUMBER/"
                f"locations/{cfg.region}/reasoningEngines/{tag}", f"{o.id} Agent Identity principal")
        self.project_number = _number_from(orch.get("engine"), *regs) or "PROJECT_NUMBER"
        self.gateway_project_number = _number_from(*regs) or self.project_number

    def _ph(self, value: str, what: str) -> str:
        self.placeholders.append(what)
        return value

    def registry(self, cid: str) -> tuple[str, str]:
        """(kind, id) of a component's Agent Registry projection (agents|mcpServers, agentregistry-...)."""
        rr = self.comps.get(cid, {}).get("registry") or ""
        kind = "agents" if self.theme.component(cid).kind == "a2a_agent" else "mcpServers"
        if rr:
            parts = rr.split("/")
            return parts[-2], parts[-1]
        self.placeholders.append(f"Agent Registry id of {cid}")
        return kind, f"REGISTRY_ID_{cid.upper().replace('-', '_')}"

    def url(self, cid: str) -> str:
        u = self.comps.get(cid, {}).get("url")
        if u:
            return u
        return f"https://{c.service_name(self.cfg, self.theme.id, cid)}-{self.project_number}.{self.cfg.region}.run.app"

    def pfx(self, name: str) -> str:
        """A config-derived name as an HCL template using var.prefix when it follows the <prefix>-... pattern."""
        p = self.cfg.prefix
        return "${var.prefix}" + name[len(p):] if name == p or name.startswith(p + "-") or name.startswith(p + " ") \
            else name


# ====================================================================== render
def render(cfg: DemoConfig | None, state: dict[str, Any] | None, theme: Theme, applied: Iterable[str],
           model_armor: bool, include_shared: bool = False, now: datetime | None = None) -> dict[str, str]:
    """Terraform files for the theme's policy state. `applied` = ids of the policies currently in place."""
    x = Ctx(cfg, state, theme)
    x.include_shared = include_shared
    applied_set = set(applied)
    pols = [p for p in theme.policies if p.id in applied_set]
    ts = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    main = _main(x, pols, model_armor, include_shared, ts)
    return {"main.tf": main, "variables.tf": _variables(x), "README.md": _readme(x, pols, model_armor,
                                                                                    include_shared, ts)}


def state_line(theme: Theme, applied: Iterable[str], model_armor: bool) -> str:
    ids = [p.id for p in theme.policies if p.id in set(applied)]
    return f"{', '.join(ids) if ids else 'no policies (wide open)'} · Model Armor {'on' if model_armor else 'off'}"


def _src(p: Policy) -> str | None:
    """The orchestrator a policy applies to (params.source), None = the primary (CONTRACTS §12)."""
    return p.params.get("source") or None


def _gateway_policies(pols: list[Policy], source: str | None = None) -> dict[str, Policy]:
    """{path: gateway_attach policy} applied to orchestrator `source` (None = the primary)."""
    return {p.params.get("path", "egress"): p for p in pols if p.type == "gateway_attach" and _src(p) == source}


def _gateway_groups(x: "Ctx", pols: list[Policy]) -> list[tuple[str | None, dict[str, Policy]]]:
    """[(source, {path: policy})] for every engine that has a gateway attached, primary first."""
    out = []
    for src in [None, *(o.id for o in x.theme.additional_orchestrators)]:
        g = _gateway_policies(pols, src)
        if g:
            out.append((src, g))
    return out


def _member_ref(p: Policy) -> str:
    src = _src(p)
    return f"local.agent_principals[{q(src)}]" if src else "local.orchestrator_principal"


def _main(x: Ctx, pols: list[Policy], model_armor: bool, include_shared: bool, ts: str) -> str:
    cfg, theme = x.cfg, x.theme
    out: list[str] = []
    out.append(top_comment(
        f"Generated by agdemo (Agent Gateway & Agent Registry demo) at {ts}\n"
        f"Theme: {theme.id} ({theme.name})\n"
        f"Policy state: {state_line(theme, [p.id for p in pols], model_armor)}\n"
        f"Scope: {'policies + shared infrastructure' if include_shared else 'policies only'}\n"
        "\n"
        + ("Shared resources (Agent Gateways, authz extensions, Model Armor template, Agent Registry entries, IAM)\n"
           "are managed below as well. If they already exist (created by `./agdemo bootstrap` / `deploy-theme`),\n"
           "import them first (see README.md).\n" if include_shared else
           "Shared resources (Agent Gateways, authz extensions, Model Armor template, Agent Registry entries) are\n"
           "created by `./agdemo bootstrap` and `./agdemo deploy-theme`; they are referenced by name in `locals`.\n")
        + "The Agent Runtime engine and the Cloud Run services are created by `./agdemo deploy-theme` and are\n"
        "referenced, not managed.\n"
        "Nothing here has been applied by agdemo: run `terraform plan` and review it before applying."))
    if x.placeholders:
        out.append(top_comment("PLACEHOLDERS: the theme is not deployed, so these values are placeholders "
                               "(search for UPPER_CASE ids): " + ", ".join(dict.fromkeys(x.placeholders)) + "."))
    out.append("\n")

    tf = Block("terraform")
    tf.s("required_version", TF_VERSION)
    tf.nl()
    rp = tf.b("required_providers")
    rp.a("google", hval({"source": "hashicorp/google", "version": PROVIDER_VERSION}, 1))
    out.append(tf.render())
    out.append("\n")
    prov = Block('provider "google"')
    prov.a("project", "var.project_id").a("region", "var.region")
    out.append(prov.render())
    out.append("\n")

    gws = _gateway_policies(pols)
    groups = _gateway_groups(x, pols)
    egress_pols = [p for p in pols if p.type in EGRESS_TYPES]
    out.append(_locals(x, gws, egress_pols, model_armor, include_shared, groups))

    if groups:
        out.append("\n")
        out.append(_section("Gateway binding (gateway_attach)"))
        out.extend(_join(_gateway_binding(x, g, src) for src, g in groups))
    if egress_pols:
        out.append("\n")
        out.append(_section("Egress allow policies: roles/iap.egressor on Agent Registry entries"))
        out.extend(_join(_egress_member(x, p) for p in egress_pols))
    if model_armor:
        out.append("\n")
        out.append(_section("Model Armor (CONTENT_AUTHZ authz policy on each gateway)"))
        out.extend(_join(_ma_policy(x, path) for path in ("egress", "ingress")))
    if include_shared:
        out.append("\n")
        out.append(_shared(x))
    text = _fix_indent("".join(out))
    return re.sub(r"\n{3,}", "\n\n", text).rstrip("\n") + "\n"


def _join(blocks: Iterable[str]) -> list[str]:
    out: list[str] = []
    for i, b in enumerate(blocks):
        if i:
            out.append("\n")
        out.append(b)
    return out


def _section(title: str) -> str:
    return f"# {'-' * 4} {title} {'-' * max(4, 104 - len(title))}\n\n"


# ---------------------------------------------------------------------- locals
def _locals(x: Ctx, gws: dict[str, Policy], egress_pols: list[Policy], model_armor: bool, include_shared: bool,
            groups: list[tuple[str | None, dict[str, Policy]]] | None = None) -> str:
    cfg, theme = x.cfg, x.theme
    lo = Block("locals")
    lo.a("gateway_project", "coalesce(var.gateway_project_id, var.project_id)")
    lo.a("gateway_project_number", "coalesce(var.gateway_project_number, var.project_number)")
    lo.a("labels", hval({**cfg.environment.labels, "demo-prefix": "__PREFIX__"}, 1)
         .replace('"__PREFIX__"', "var.prefix"))
    lo.nl()
    lo.c("Created by `./agdemo deploy-theme` (referenced, not managed by this configuration).")
    lo.s("engine", x.engine)
    lo.s("orchestrator_principal", x.principal)
    if theme.additional_orchestrators:
        lo.c("Additional orchestrators: their own engines and Agent Identities, bound to the same gateways.")
        lo.a("agent_engines", "{\n" + "".join(f"{q(k)} = {q(v)}\n" for k, v in x.agent_engines.items()) + "}")
        lo.a("agent_principals", "{\n" + "".join(f"{q(k)} = {q(v)}\n" for k, v in x.agent_principals.items())
             + "}")
    lo.nl()
    if include_shared:
        lo.c("Agent Gateways (managed below).")
        lo.a("egress_gateway", "google_network_services_agent_gateway.egress.id")
        lo.a("ingress_gateway", "google_network_services_agent_gateway.ingress.id")
    else:
        lo.c("Shared resources created by `./agdemo bootstrap` (referenced by name).")
        lo.t("egress_gateway", f"projects/${{local.gateway_project}}/locations/${{var.region}}/agentGateways/"
                               f"{x.pfx(cfg.egress_gateway)}")
        lo.t("ingress_gateway", f"projects/${{var.project_id}}/locations/${{var.region}}/agentGateways/"
                                f"{x.pfx(cfg.ingress_gateway)}")
        if model_armor:
            ext = x.pfx(gw.ma_extension_name(cfg))
            lo.a("model_armor_extension", "{\n"
                 f'egress  = "projects/${{local.gateway_project}}/locations/${{var.region}}/authzExtensions/{ext}"\n'
                 f'ingress = "projects/${{var.project_id}}/locations/${{var.region}}/authzExtensions/{ext}"\n'
                 "}")
    lo.nl()
    if include_shared:
        lo.c("Agent Registry projection ids (agents/<id>, mcpServers/<id>) of the services registered below.")
        lo.a("registry_ids", "{\n" + "".join(
            f'{q(comp.id)} = basename(google_agent_registry_service.{ident(comp.id)}.registry_resource)\n'
            for comp in theme.components) + "}")
    else:
        lo.c("Agent Registry projection ids (agents/<id>, mcpServers/<id>) registered by `./agdemo deploy-theme`.")
        lo.a("registry_ids", "{\n" + "".join(f"{q(comp.id)} = {q(x.registry(comp.id)[1])}\n"
                                            for comp in theme.components) + "}")
    for src, g in groups if groups is not None else ([(None, gws)] if gws else []):
        lo.nl()
        lo.c(f"agentGatewayConfig for {'the engine' if src is None else src + chr(39) + 's engine'}: every direction "
             "that is currently attached (the PATCH replaces the whole field).")
        cfgs = "".join(f"{GW_KEYS[p]} = {{\nagentGateway = local.{p}_gateway\n}}\n"
                       for p in ("egress", "ingress") if p in g)
        lo.a(_body_local(src), "jsonencode({\nspec = {\ndeploymentSpec = {\nagentGatewayConfig = {\n"
             + cfgs + "}\n}\n}\n})")
    return _fix_indent(lo.render())


def _fix_indent(text: str) -> str:
    """Re-indent brace-nested lines (used for the hand-built multi-line expressions in locals)."""
    out, depth = [], 0
    in_heredoc: str | None = None
    for line in text.split("\n"):
        s = line.strip()
        if in_heredoc:
            out.append(line)
            if s == in_heredoc:
                in_heredoc = None
            continue
        if not s:
            out.append("")
            continue
        closes = s.startswith(("}", "]", ")"))
        d = depth - 1 if closes else depth
        out.append("  " * d + s)
        m = re.search(r"<<-?(\w+)$", s)
        if m:
            in_heredoc = m.group(1)
        if not s.startswith("#"):
            code = re.sub(r'"(?:[^"\\]|\\.)*"', '""', s)      # brackets inside strings don't count
            net = sum(code.count(ch) for ch in "{[(") - sum(code.count(ch) for ch in "}])")
            depth += (net > 0) - (net < 0)       # terraform fmt indents one level per line, however many open
    return _realign("\n".join(out))


def _realign(text: str) -> str:
    """Align `=` within runs of consecutive single-line `key = value` lines at the same indentation."""
    lines = text.split("\n")
    pat = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_-]*|\"[^\"]*\")\s*=\s(.*)$")
    i = 0
    while i < len(lines):
        m = pat.match(lines[i])
        if not m:
            i += 1
            continue
        multiline = lambda v: bool(re.search(r"[{\[(]$", v.rstrip()))  # noqa: E731
        heredoc_ = lambda v: bool(re.search(r"<<-?\w+$", v.rstrip()))  # noqa: E731
        if multiline(m.group(3)):      # multi-line {}/[] values are never padded and end a run
            lines[i] = f"{m.group(1)}{m.group(2)} = {m.group(3)}"
            i += 1
            continue
        run = [i]
        j = i + 1
        while j < len(lines) and not heredoc_(pat.match(lines[j - 1]).group(3)):   # a heredoc ends a run
            mj = pat.match(lines[j])
            if not mj or mj.group(1) != m.group(1) or multiline(mj.group(3)):
                break
            run.append(j)
            j += 1
        w = max(len(pat.match(lines[k]).group(2)) for k in run)
        for k in run:
            mk = pat.match(lines[k])
            lines[k] = f"{mk.group(1)}{mk.group(2).ljust(w)} = {mk.group(3)}"
        i = j
    return "\n".join(lines)


# ---------------------------------------------------------------------- gateway binding
def _body_local(src: str | None) -> str:
    return "gateway_binding_body" if src is None else f"gateway_binding_body_{ident(src)}"


def _gateway_binding(x: Ctx, gws: dict[str, Policy], src: str | None = None) -> str:
    """terraform_data PATCH binding for one engine: the primary's (`gateway_binding`) or an additional
    orchestrator's (`gateway_binding_<id>`), CONTRACTS §12."""
    name = "gateway_binding" if src is None else f"gateway_binding_{ident(src)}"
    eng = "local.engine" if src is None else f"local.agent_engines[{q(src)}]"
    body = f"local.{_body_local(src)}"
    lines: list[str] = []
    if src is not None:
        lines += [f"{x.theme.orchestrator_for(src).display_name} ({src}): its own engine and Agent Identity, "
                  "bound to the same shared gateway.", ""]
    for path in ("egress", "ingress"):
        if path in gws:
            p = gws[path]
            lines.append(f"{p.id}: " + p.text)
    head = Block(f'resource "terraform_data" "{name}"')
    hdr = [*lines, "",
           "No Terraform resource binds an existing Agent Runtime engine to an Agent Gateway, so this runs the same",
           "PATCH the demo makes (a long-running operation that redeploys the engine; curl returns once it is",
           "accepted, the engine is ready a few minutes later). Changing the set of gateways replaces this resource:",
           "the destroy step detaches every direction, then the create step attaches the desired ones.",
           "Needs curl and an authenticated gcloud on the machine that runs Terraform.",
           "",
           "Teams that manage the engine itself in Terraform set the binding on the engine instead",
           "(google >= 8.1.0; requires importing the existing engine with a matching spec, not verified by agdemo):",
           "",
           '  resource "google_vertex_ai_reasoning_engine" "orchestrator" {',
           "    # ... display_name, spec.package_spec / source_code_spec, identity_type ...",
           "    spec {",
           "      deployment_spec {",
           "        agent_gateway_config {"]
    for path, blk in (("egress", "agent_to_anywhere_config"), ("ingress", "client_to_agent_config")):
        if path in gws:
            hdr += [f"          {blk} {{", f"            agent_gateway = local.{path}_gateway", "          }"]
    hdr += ["        }", "      }", "    }", "  }"]
    pre = "".join(f"# {h}\n" if h else "#\n" for h in hdr)
    head.a("input", "{\nurl = \"https://${var.region}-aiplatform.googleapis.com/v1/${" + eng + "}?updateMask="
           + ENGINE_MASK + "\"\n}")
    head.a("triggers_replace", f"[{eng}, {body}]")
    head.nl()
    attach = head.b('provisioner "local-exec"')
    attach.c("Attach: PATCH agentGatewayConfig with the directions above.")
    attach.a("command", heredoc(
        'curl -sS --fail-with-body -X PATCH "$URL" \\\n'
        '  -H "Authorization: Bearer $(gcloud auth print-access-token)" \\\n'
        '  -H "Content-Type: application/json" \\\n'
        '  -d "$BODY"', 2))
    attach.a("environment", "{\nURL  = self.input.url\nBODY = " + body + "\n}")
    head.nl()
    detach = head.b('provisioner "local-exec"')
    detach.c("Detach on destroy: an empty agentGatewayConfig unbinds both directions.")
    detach.a("when", "destroy")
    detach.a("command", heredoc(
        'curl -sS --fail-with-body -X PATCH "$URL" \\\n'
        '  -H "Authorization: Bearer $(gcloud auth print-access-token)" \\\n'
        '  -H "Content-Type: application/json" \\\n'
        """  -d '{"spec":{"deploymentSpec":{"agentGatewayConfig":{}}}}'""", 2))
    detach.a("environment", "{\nURL = self.input.url\n}")
    return pre + _fix_indent(head.render())


# ---------------------------------------------------------------------- IAP egress grants
def _egress_member(x: Ctx, p: Policy, ref: bool = False) -> str:
    from .policies.egress_allow import condition

    target = p.params["target"]
    kind, _ = x.registry(target)
    if kind == "agents":
        rtype, key = "google_iap_agent_registry_agent_iam_member", "agent_id"
    else:
        rtype, key = "google_iap_agent_registry_mcp_server_iam_member", "mcp_server_id"
    b = Block(f'resource "{rtype}" "{ident(p.id)}"')
    b.a("project", "local.gateway_project_number")
    b.a("location", "var.region")
    b.a(key, f"local.registry_ids[{q(target)}]")
    b.s("role", iap.EGRESSOR)
    b.a("member", _member_ref(p))       # the Agent Identity of the orchestrator named by params.source
    cond = condition(x.theme, p)
    if cond:
        b.nl()
        cb = b.b("condition")
        cb.s("title", cond["title"])
        if cond.get("description"):
            cb.s("description", cond["description"])
        cb.s("expression", cond["expression"])
    what = {"a2a_allow": "A2A agent", "mcp_server_allow": "MCP server (all tools)",
            "mcp_tool_allow": "MCP server (tool-level condition)"}[p.type]
    head = [f"{p.id}: {p.text}", f"{p.type} -> {what} {target}"
            + (f" (member: {x.theme.orchestrator_for(_src(p)).display_name}'s Agent Identity)" if _src(p) else "")]
    if p.explain:
        head += comment_lines(p.explain)
    return "".join(f"# {h}\n" for h in head) + b.render()


# ---------------------------------------------------------------------- Model Armor
def _ma_policy(x: Ctx, path: str) -> str:
    cfg = x.cfg
    b = Block(f'resource "google_network_security_authz_policy" "model_armor_{path}"')
    b.a("project", "local.gateway_project" if path == "egress" else "var.project_id")
    b.a("location", "var.region")
    b.t("name", x.pfx(gw.ma_policy_name(cfg, path)))
    b.s("policy_profile", "CONTENT_AUTHZ")
    b.s("action", "CUSTOM")
    b.a("labels", "local.labels")
    b.nl()
    b.b("target").a("resources", f"[local.{path}_gateway]")
    b.nl()
    ext = (f"google_network_services_authz_extension.model_armor[{'local.gateway_project' if path == 'egress' else 'var.project_id'}].id"
           if x.include_shared else f"local.model_armor_extension.{path}")
    b.b("custom_provider").b("authz_extension").a("resources", f"[{ext}]")
    return f"# Model Armor on: screen {path} traffic ({'AGENT_TO_ANYWHERE' if path == 'egress' else 'CLIENT_TO_AGENT'})\n" \
           + b.render()


# ---------------------------------------------------------------------- shared infrastructure
def _shared(x: Ctx) -> str:
    cfg, theme = x.cfg, x.theme
    out: list[str] = [_section("Shared infrastructure (created by ./agdemo bootstrap)")]
    # gateways
    for path in ("egress", "ingress"):
        b = Block(f'resource "google_network_services_agent_gateway" "{path}"')
        b.a("project", "local.gateway_project" if path == "egress" else "var.project_id")
        b.a("location", "var.region")
        b.t("name", x.pfx(gw.gateway_name(cfg, path)))
        b.t("description", f"{x.pfx(cfg.prefix)} demo {path} gateway")
        if path == "egress":
            b.a("registries", '["//agentregistry.googleapis.com/projects/${local.gateway_project}/locations/'
                              '${var.region}"]')
        b.a("labels", "local.labels")
        b.nl()
        b.b("google_managed").s("governed_access_path",
                                "AGENT_TO_ANYWHERE" if path == "egress" else "CLIENT_TO_AGENT")
        out.append(f"# {path.capitalize()} Agent Gateway ({'Agent-to-Anywhere' if path == 'egress' else 'Client-to-Agent'})"
                   + ("; `protocols` is deprecated since google 7.37.0 and omitted." if path == "egress" else "")
                   + "\n" + b.render() + "\n")
    # IAP authz extension + REQUEST_AUTHZ policy (egress only)
    dry = cfg.gateways.iap_enforcement == "DRY_RUN"
    md: dict[str, str] = {"iapPolicyVersion": "V1"}
    if dry:
        md["iamEnforcementMode"] = "DRY_RUN"
    b = Block('resource "google_network_services_authz_extension" "egress_iap"')
    b.a("project", "local.gateway_project").a("location", "var.region")
    b.t("name", x.pfx(gw.iap_extension_name(cfg, "egress")))
    b.s("service", "iap.googleapis.com")
    b.a("fail_open", "true" if dry else "false")
    b.s("timeout", "1s")
    b.a("metadata", hval(md, 1))
    b.a("labels", "local.labels")
    out.append("# IAP request authorization: roles/iap.egressor on each Agent Registry entry decides every egress call.\n"
               f"# iap_enforcement = {cfg.gateways.iap_enforcement} (config/demo.yaml)\n" + b.render() + "\n")
    b = Block('resource "google_network_security_authz_policy" "egress_iap"')
    b.a("project", "local.gateway_project").a("location", "var.region")
    b.t("name", x.pfx(gw.iap_policy_name(cfg, "egress")))
    b.s("policy_profile", "REQUEST_AUTHZ")
    b.s("action", "CUSTOM")
    b.a("labels", "local.labels")
    b.nl()
    b.b("target").a("resources", "[local.egress_gateway]")
    b.nl()
    b.b("custom_provider").b("authz_extension").a("resources", "[google_network_services_authz_extension.egress_iap.id]")
    out.append(b.render() + "\n")
    # Model Armor template + extension
    from .gcp import model_armor as ma
    body = ma.template_body(cfg)
    b = Block('resource "google_model_armor_template" "shield"')
    b.a("project", "var.project_id").a("location", "var.region")
    b.t("template_id", x.pfx(cfg.model_armor_template))
    b.a("labels", "local.labels")
    b.nl()
    fc = b.b("filter_config")
    f = body["filterConfig"]
    if "piAndJailbreakFilterSettings" in f:
        s = f["piAndJailbreakFilterSettings"]
        fc.b("pi_and_jailbreak_filter_settings").s("filter_enforcement", s["filterEnforcement"]) \
            .s("confidence_level", s["confidenceLevel"])
    if "sdpSettings" in f:
        fc.b("sdp_settings").b("basic_config").s("filter_enforcement", f["sdpSettings"]["basicConfig"]["filterEnforcement"])
    if "maliciousUriFilterSettings" in f:
        fc.b("malicious_uri_filter_settings").s("filter_enforcement", "ENABLED")
    if "raiSettings" in f:
        rs = fc.b("rai_settings")
        for rf in f["raiSettings"]["raiFilters"]:
            rs.b("rai_filters").s("filter_type", rf["filterType"]).s("confidence_level", rf["confidenceLevel"])
    b.nl()
    tm = b.b("template_metadata")
    m = body["templateMetadata"]
    tm.a("log_sanitize_operations", "true").a("log_template_operations", "true")
    tm.a("custom_prompt_safety_error_code", str(m["customPromptSafetyErrorCode"]))
    tm.s("custom_prompt_safety_error_message", m["customPromptSafetyErrorMessage"])
    tm.a("custom_llm_response_safety_error_code", str(m["customLlmResponseSafetyErrorCode"]))
    tm.s("custom_llm_response_safety_error_message", m["customLlmResponseSafetyErrorMessage"])
    out.append(f"# Model Armor template (filters: {', '.join(cfg.model_armor.filters)})\n" + b.render() + "\n")
    b = Block('resource "google_network_services_authz_extension" "model_armor"')
    b.a("for_each", "toset(distinct([var.project_id, local.gateway_project]))")
    b.nl()
    b.a("project", "each.value").a("location", "var.region")
    b.t("name", x.pfx(gw.ma_extension_name(cfg)))
    b.t("service", "modelarmor.${var.region}.rep.googleapis.com")
    b.a("fail_open", "false")
    b.s("timeout", "5s")
    b.a("labels", "local.labels")
    b.a("metadata", "{\nmodel_armor_settings = jsonencode([{\nrequest_template_id  = google_model_armor_template.shield.id\n"
                    "response_template_id = google_model_armor_template.shield.id\n}])\n}")
    out.append("# Model Armor authz extension, one per gateway project. The CONTENT_AUTHZ policies that attach it\n"
               "# are the Model Armor checkbox (see above; present only when Model Armor is on).\n"
               + _fix_indent(b.render()) + "\n")

    # Agent Registry: theme components
    out.append(_section(f"Agent Registry entries for theme {theme.id} (registered by ./agdemo deploy-theme)"))
    from .infra.deploy_theme import mcp_toolspec
    for comp in theme.components:
        sid = c.service_name(cfg, theme.id, comp.id)
        url = x.url(comp.id)
        b = Block(f'resource "google_agent_registry_service" "{ident(comp.id)}"')
        b.a("project", "local.gateway_project").a("location", "var.region")
        b.t("service_id", x.pfx(sid))
        if comp.kind == "mcp_server":
            spec = theme.mcp_servers[comp.id]
            b.t("display_name", x.pfx(f"{cfg.prefix} {theme.id} {spec.display_name}"[:63]))
            b.s("description", spec.description)
            b.nl()
            ms = b.b("mcp_server_spec")
            ms.s("type", "TOOL_SPEC")
            ms.a("content", heredoc(json.dumps({"tools": mcp_toolspec(theme, comp.id)}, indent=2), 2, "JSON"))
            b.nl()
            b.b("interfaces").s("url", f"{url}/mcp").s("protocol_binding", "JSONRPC")
            note = f"# MCP server {comp.id}: tool spec with MCP annotations (readOnlyHint drives mcp.tool.isReadOnly)\n"
        else:
            spec = theme.a2a_agents[comp.id]
            b.t("display_name", x.pfx(f"{cfg.prefix} {theme.id} {spec.display_name}"[:63]))
            b.s("description", spec.description)
            b.nl()
            ag = b.b("agent_spec")
            ag.s("type", "A2A_AGENT_CARD")
            ag.a("content", heredoc(json.dumps(_agent_card(theme, comp.id, url), indent=2), 2, "JSON"))
            note = (f"# A2A agent {comp.id}: the agent card the Cloud Run service serves at "
                    "/.well-known/agent-card.json\n# (deploy-theme registers the served card; interfaces must "
                    "be empty for A2A cards)\n")
        out.append(note + f"# Cloud Run service {sid} is created by ./agdemo deploy-theme (referenced, not managed)\n"
                   + b.render() + "\n")

    # Platform endpoints
    out.append(_section("Platform endpoints: default-deny allowlist for every Agent Runtime agent in the project"))
    lo = Block("locals")
    lo.c("Google APIs an ADK agent on Agent Runtime calls (exact host match, so regional, .mtls. and :443 variants).")
    lo.a("platform_endpoints", "{\n" + "".join(f"{q(s)} = {q(u)}\n" for s, u in c.platform_endpoints(cfg)) + "}")
    lo.c("Every Agent Identity agent in the project.")
    lo.s("project_agents", x.project_set)
    out.append(_fix_indent(lo.render()) + "\n")
    b = Block('resource "google_agent_registry_service" "platform"')
    b.a("for_each", "local.platform_endpoints")
    b.nl()
    b.a("project", "local.gateway_project").a("location", "var.region")
    b.a("service_id", '"${var.prefix}-plat-${each.key}"')
    b.a("display_name", '"${var.prefix} ${trimprefix(each.value, "https://")}"')
    b.s("description", "agdemo platform endpoint (default-deny allowlist)")
    b.nl()
    b.b("endpoint_spec").s("type", "NO_SPEC")
    b.nl()
    b.b("interfaces").a("url", "each.value").s("protocol_binding", "JSONRPC")
    out.append(b.render() + "\n")
    b = Block('resource "google_iap_agent_registry_endpoint_iam_member" "platform"')
    b.a("for_each", "google_agent_registry_service.platform")
    b.nl()
    b.a("project", "local.gateway_project_number").a("location", "var.region")
    b.a("endpoint_id", "basename(each.value.registry_resource)")
    b.s("role", iap.EGRESSOR)
    b.a("member", "local.project_agents")
    out.append(b.render() + "\n")

    # IAM
    out.append(_section("Service accounts and project IAM"))
    for key, acct, desc in (("ui", c.ui_sa_id(cfg), "agdemo demo UI backend"),
                            ("targets", c.run_sa_id(cfg), "agdemo Cloud Run demo targets")):
        b = Block(f'resource "google_service_account" "{key}"')
        b.a("project", "var.project_id")
        b.t("account_id", x.pfx(acct))
        b.s("display_name", desc)
        out.append(b.render() + "\n")
    for key, roles, member, note in (
            ("ui", UI_ROLES, "google_service_account.ui.member", "UI backend: reads and applies policies"),
            ("targets", TARGET_ROLES, "google_service_account.targets.member", "Cloud Run demo targets call Gemini"),
            ("orchestrator", AGENT_ROLES, "local.orchestrator_principal",
             "Orchestrator Agent Identity (basic roles, granted by deploy-theme)"),
            *((f"agent_{ident(o.id)}", AGENT_ROLES, f"local.agent_principals[{q(o.id)}]",
               f"{o.display_name} Agent Identity (basic roles, granted by deploy-theme)")
              for o in theme.additional_orchestrators)):
        b = Block(f'resource "google_project_iam_member" "{key}"')
        b.a("for_each", tset(roles))
        b.nl()
        b.a("project", "var.project_id").a("role", "each.value").a("member", member)
        out.append(f"# {note}\n" + b.render() + "\n")
    presenters = [m for m in cfg.ui.admin_access if m.startswith(("user:", "group:", "serviceAccount:"))]
    if presenters:
        b = Block('resource "google_project_iam_member" "presenters"')
        b.a("for_each", tset(presenters))
        b.nl()
        b.a("project", "var.project_id").s("role", "roles/aiplatform.user").a("member", "each.value")
        out.append("# Presenters (ui.admin_access) call Agent Runtime directly\n" + b.render() + "\n")
    agents = ["serviceAccount:service-${var.project_number}@gcp-sa-dep.iam.gserviceaccount.com",
              "serviceAccount:service-${var.project_number}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"]
    agents += [f"serviceAccount:{a}" for a in x.shared.get("gateway_service_agents", [])
               if not a.startswith(f"service-{x.project_number}@gcp-sa-dep")]
    lo = Block("locals")
    lo.c("Service agents that call Model Armor for the gateways.")
    lo.a("model_armor_agents", "[\n" + "".join(f"{tq(a)},\n" for a in agents) + "]")
    lo.a("model_armor_roles", hval(MA_AGENT_ROLES, 1))
    out.append(_fix_indent(lo.render()) + "\n")
    b = Block('resource "google_project_iam_member" "model_armor_agents"')
    b.a("for_each", "{\nfor pair in setproduct(local.model_armor_agents, local.model_armor_roles) :\n"
                    '"${pair[0]} ${pair[1]}" => pair\n}')
    b.nl()
    b.a("project", "var.project_id").a("member", "each.value[0]").a("role", "each.value[1]")
    out.append(_fix_indent(b.render()))
    return "".join(out)


def _agent_card(theme: Theme, cid: str, url: str) -> dict[str, Any]:
    spec = theme.a2a_agents[cid]
    return {
        "name": spec.display_name, "description": spec.description, "version": "1.0.0",
        "supportedInterfaces": [{"protocolBinding": "JSONRPC", "url": url, "protocolVersion": "1.0"}],
        "capabilities": {}, "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"],
        "skills": [{"id": s.id, "name": s.name, "description": s.description, "tags": list(s.tags),
                    "examples": list(s.examples)} for s in spec.skills],
    }


# ---------------------------------------------------------------------- variables.tf
def _variables(x: Ctx) -> str:
    cfg = x.cfg
    out = [top_comment(f"Generated by agdemo. Defaults come from config/demo.yaml and config/state.json "
                       f"(theme {x.theme.id})."), "\n"]
    specs = [
        ("project_id", "Project for Agent Runtime, the ingress gateway, Cloud Run and Model Armor.", q(cfg.project)),
        ("project_number", "Number of project_id (service agents and principals use it).", q(x.project_number)),
        ("gateway_project_id", "Project for the egress gateway and Agent Registry (null = project_id).",
         q(cfg.gateway_project) if cfg.gateway_project != cfg.project else "null"),
        ("gateway_project_number", "Number of the gateway project; IAP IAM on Agent Registry entries uses the "
                                   "number (null = project_number).",
         q(x.gateway_project_number) if x.gateway_project_number != x.project_number else "null"),
        ("region", "Region for gateways, Agent Runtime, Agent Registry and Cloud Run.", q(cfg.region)),
        ("prefix", "Resource name prefix (resource_prefix in config/demo.yaml).", q(cfg.prefix)),
    ]
    blocks = []
    for name, desc, default in specs:
        b = Block(f'variable "{name}"')
        b.s("description", desc)
        b.a("type", "string")
        b.a("default", default)
        blocks.append(b.render())
    out.extend(_join(blocks))
    return _fix_indent("".join(out)).rstrip("\n") + "\n"


# ---------------------------------------------------------------------- README.md
def _readme(x: Ctx, pols: list[Policy], model_armor: bool, include_shared: bool, ts: str) -> str:
    cfg, theme = x.cfg, x.theme
    rows = []
    for p in pols:
        kind, _ = x.registry(p.params["target"]) if p.params.get("target") else ("", "")
        res = {"gateway_attach": f"terraform_data.gateway_binding{'_' + ident(_src(p)) if _src(p) else ''} "
                                 "(local-exec PATCH)",
               "a2a_allow": f"google_iap_agent_registry_agent_iam_member.{ident(p.id)}",
               "mcp_server_allow": f"google_iap_agent_registry_{'agent' if kind == 'agents' else 'mcp_server'}"
                                   f"_iam_member.{ident(p.id)}",
               "mcp_tool_allow": f"google_iap_agent_registry_mcp_server_iam_member.{ident(p.id)} (with condition)"}
        rows.append(f"| `{p.id}` | {p.text} | `{res[p.type]}` |")
    if model_armor:
        rows.append("| Model Armor | Prompts and responses are screened on both gateways | "
                    "`google_network_security_authz_policy.model_armor_{egress,ingress}` |")
    table = "\n".join(["| Policy | What it does | Terraform |", "|---|---|---|", *rows]) if rows else \
        "_No policies are applied: the theme is wide open (no gateway, no grants, Model Armor off)._"
    managed = ("Policies **and** the shared infrastructure: Agent Gateways, the IAP and Model Armor authz extensions, "
               "the IAP `REQUEST_AUTHZ` policy, the Model Armor template, the theme's Agent Registry services, the "
               "platform endpoint allowlist, and project IAM for the service accounts and the orchestrator identity."
               if include_shared else
               "Policies only. Shared resources created by `./agdemo bootstrap` and `./agdemo deploy-theme` "
               "(gateways, authz extensions, Model Armor template, Agent Registry entries) are referenced by name "
               "in `locals`.")
    extra_engines = "".join(f", {o.display_name}'s engine (`{x.agent_engines[o.id]}`)"
                            for o in theme.additional_orchestrators)
    ph = (f"\n> **Placeholders:** the theme isn't deployed, so these values are placeholders: "
          f"{', '.join(dict.fromkeys(x.placeholders))}. Replace the UPPER_CASE ids before planning.\n"
          if x.placeholders else "")
    imports = [
        "```sh",
        "terraform init",
        "terraform plan     # review: nothing here has been applied by agdemo",
        "terraform apply",
        "```",
    ]
    imp_lines = []
    if model_armor:
        for path in ("egress", "ingress"):
            proj = gw.gateway_project(cfg, path)
            imp_lines.append(f"terraform import google_network_security_authz_policy.model_armor_{path} "
                             f"projects/{proj}/locations/{cfg.region}/authzPolicies/{gw.ma_policy_name(cfg, path)}")
    if include_shared:
        for path in ("egress", "ingress"):
            imp_lines.append(f"terraform import google_network_services_agent_gateway.{path} "
                             f"{gw.gateway_resource(cfg, path)}")
        imp_lines.append(f"terraform import google_model_armor_template.shield "
                         f"projects/{cfg.project}/locations/{cfg.region}/templates/{cfg.model_armor_template}")
        imp_lines.append("# ... and likewise for the authz extensions/policy, registry services and service accounts")
    return f"""# Terraform export: {theme.name} (`{theme.id}`)

Generated by agdemo at {ts}. Policy state: **{state_line(theme, [p.id for p in pols], model_armor)}**.

## What's in here

- `main.tf`: the resources below, plus `locals` with the ids the demo created.
- `variables.tf`: `project_id`, `project_number`, `gateway_project_id`, `gateway_project_number`, `region`,
  `prefix` (defaults from `config/demo.yaml` and `config/state.json`).

**Managed:** {managed}

**Referenced, not managed:** the Agent Runtime engine (`{x.engine}`){extra_engines} and the Cloud Run services;
both are created by `./agdemo deploy-theme`.
{ph}
{table}

## How each policy maps to Terraform

- **Allow policies** (`a2a_allow`, `mcp_server_allow`, `mcp_tool_allow`) grant `roles/iap.egressor` on the
  destination's Agent Registry entry to the orchestrator's Agent Identity (the one named by the policy's
  `source`, else the primary). Tool-level policies carry the exact IAM
  condition the demo applies (tool names, or `mcp.tool.isReadOnly` for the annotation-based policy). IAP IAM on
  Agent Registry uses the **project number**. `_iam_member` resources only add a member to a binding, so applying
  over an environment where the demo already granted the same binding is a no-op.
- **Gateway attach** has no standalone Terraform resource for an engine Terraform doesn't own. A `terraform_data`
  resource runs the same `PATCH ...?updateMask={ENGINE_MASK}` the demo makes (needs `curl` and an authenticated
  `gcloud`), and detaches on destroy. If you manage the engine with `google_vertex_ai_reasoning_engine`, set
  `spec.deployment_spec.agent_gateway_config` there instead (commented example in `main.tf`; needs an import of the
  existing engine and isn't verified by agdemo).
- **Model Armor** is a `CONTENT_AUTHZ` `google_network_security_authz_policy` per gateway that points at the Model
  Armor authz extension.

## Use it

{chr(10).join(imports)}

Resources that already exist (for example because the demo applied them) must be imported first, or the create
fails with "already exists":

```sh
{chr(10).join(imp_lines) if imp_lines else "# nothing to import: IAM members and the gateway binding are idempotent"}
```

Requires Terraform {TF_VERSION} and the `hashicorp/google` provider {PROVIDER_VERSION} (no `google-beta`).
The resource types and fields were checked against the provider schema with `terraform validate`; agdemo has not
applied this configuration to GCP.
"""
