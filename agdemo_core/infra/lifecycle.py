"""`./agdemo up` / `./agdemo down` (docs/TERRAFORM_PLAN.md §4.1, §4.3, phase T3).

up:   preflight -> tf init -> plan + plan-safety (no delete / replace) -> confirm -> apply (recordings bucket
      first) -> output-sync -> images once -> deploy-theme per enabled theme -> ui deploy -> [publish-ge] -> verify
down: confirm (type <project>/<prefix>; nobody presenting) -> reset --all -> Gemini Enterprise unregister ->
      delete engines, prefix Cloud Run services (themes, private MCP, UI), prefix registry entries, legacy
      resources, non-foundation prefix Model Armor templates -> check-clean --strict -> keep the shared
      project grants (terraform state rm) -> terraform destroy (the guard re-runs check-clean --strict) ->
      read-only re-check of the shared grants -> clear state.json
APIs stay enabled; the state and recordings buckets are never deleted (neither is in Terraform).

Write-command safety: both refuse an environment that isn't Terraform-managed. up refuses a live egress
gateway that state.json doesn't record as Terraform's (tf_environment) or that the Terraform state doesn't
hold: that is the bootstrap-created live demo, adopted only through the T2 runbook. down needs
tf_environment in state.json, a Terraform state whose `environment` output is this config, and the typed
confirmation (or --yes with an explicit, matching --prefix).

Shared grants (T1 note, decided in T3): `_member` grants aren't reference-counted, so a destroy can strip a
role another environment in the project still uses. down never removes the project-wide ones: the
presenters' aiplatform.user and the static service agents' Model Armor roles (shared_project_grants), plus
the egress gateway agent's roles when another gateway in the project uses the same agent. They're removed
from the state before the destroy (`terraform state rm`: the grant stays in IAM) and adopted again, as a
no-op additive create, by the next up. After the destroy a read-only re-check reports their roles.

The sequencing functions take an `ops` object (LiveUpOps / LiveDownOps, or a fake in tests).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from ..config import DemoConfig
from . import check_clean as cc
from . import common as c
from . import reset_all

MA_ROLES = ("roles/modelarmor.calloutUser", "roles/modelarmor.user", "roles/serviceusage.serviceUsageConsumer")
STATIC_AGENTS = ("gcp-sa-dep", "gcp-sa-aiplatform-re")      # infra/terraform/variables.tf model_armor_static_agents
IAM_ADDR = "module.base.google_project_iam_member"


# ------------------------------------------------------------------ environment identity (pure)
def env_of(cfg: DemoConfig) -> dict[str, str]:
    return {"project_id": cfg.project, "gateway_project_id": cfg.gateway_project, "region": cfg.region,
            "prefix": cfg.prefix}


def same_env(cfg: DemoConfig, env: dict | None) -> bool:
    if not env:
        return False
    want = env_of(cfg)
    return all((env.get(k) or (want["project_id"] if k == "gateway_project_id" else "")) == v
               for k, v in want.items())


def describe(env: dict | None) -> str:
    env = env or {}
    return f"{env.get('project_id')}/{env.get('region')} (prefix {env.get('prefix')})"


def up_refusal(cfg: DemoConfig, state: dict, gateway_live: bool, tf_managed: bool | None) -> str | None:
    """None = up may run. tf_managed: the egress gateway is in this Terraform state or imported by imports.tf
    (None = not known yet, before tf init)."""
    shared = state.get("shared") or {}
    env = shared.get("tf_environment")
    if env and not same_env(cfg, env):
        return (f"config/state.json belongs to Terraform environment {describe(env)}, but config/demo.yaml "
                f"describes {describe(env_of(cfg))}. Use that environment's config (AGDEMO_CONFIG) or a "
                "separate checkout; up refuses.")
    if gateway_live and not env:
        return (f"{cfg.egress_gateway_resource} exists but state.json has no tf_environment: this environment "
                "was created by bootstrap (the live demo?). Adopt it first with "
                "docs/TERRAFORM_MIGRATION_RUNBOOK.md; up refuses.")
    if not gateway_live and not env and shared.get("egress_gateway"):
        return (f"config/state.json describes a bootstrap environment ({shared['egress_gateway']}) whose gateway "
                "isn't live. Move config/state.json aside before up creates a new environment.")
    if gateway_live and tf_managed is False:
        return (f"{cfg.egress_gateway_resource} exists but isn't in this Terraform state (nor in imports.tf). "
                "Check the state prefix (./agdemo tf init) or run the T2 runbook; up refuses.")
    return None


def down_refusal_state(cfg: DemoConfig, state: dict) -> str | None:
    env = (state.get("shared") or {}).get("tf_environment")
    if not env:
        return ("This environment isn't Terraform-managed (no tf_environment in config/state.json): down only "
                "runs on environments created by up or adopted by the T2 runbook. The live demo before its "
                "migration is never a down target (teardown --all --legacy is the old path).")
    if not same_env(cfg, env):
        return (f"config/state.json belongs to {describe(env)}, but config/demo.yaml describes "
                f"{describe(env_of(cfg))}; down refuses.")
    return None


def down_refusal_tf(cfg: DemoConfig, tf_env: dict | None, addresses: set[str], migrating: bool) -> str | None:
    from ..tf_import import EGRESS_GW_ADDR
    if migrating:
        return "infra/terraform/imports.tf exists: finish the T2 migration (runbook step e) before down."
    if not same_env(cfg, tf_env):
        return (f"the Terraform state describes {describe(tf_env) if tf_env else 'nothing (no outputs)'}, not "
                f"{describe(env_of(cfg))}; down refuses.")
    if EGRESS_GW_ADDR not in addresses:
        return "the Terraform state has no egress gateway: nothing for down to destroy."
    return None


def confirm_down(cfg: DemoConfig, yes: bool, prefix_arg: str | None, ask: Callable[[str], str]) -> tuple[bool, str]:
    """--yes needs an explicit --prefix equal to the config's prefix; otherwise the operator types
    <project>/<prefix>. Returns (go, reason)."""
    token = f"{cfg.project}/{cfg.prefix}"
    if prefix_arg is not None and prefix_arg != cfg.prefix:
        return False, f"--prefix {prefix_arg} doesn't match this environment's prefix {cfg.prefix}; nothing changed"
    if yes:
        if prefix_arg is None:
            return False, f"--yes needs an explicit --prefix {cfg.prefix}; nothing changed"
        return True, ""
    ans = (ask(f"Type {token} to delete this environment") or "").strip()
    return (True, "") if ans == token else (False, "confirmation didn't match; nothing changed")


# ------------------------------------------------------------------ teardown plan (pure)
@dataclass
class TeardownPlan:
    ge_agents: list[dict] = field(default_factory=list)
    engines: list[dict] = field(default_factory=list)          # {"name", "display", "principal"}
    run_services: list[str] = field(default_factory=list)      # full Cloud Run v2 names
    registry: list[str] = field(default_factory=list)          # service ids
    legacy_policies: list[tuple[str, str]] = field(default_factory=list)     # (project, name)
    legacy_extensions: list[tuple[str, str]] = field(default_factory=list)
    legacy_sas: list[str] = field(default_factory=list)
    ma_templates: list[str] = field(default_factory=list)      # full names

    def lines(self) -> list[str]:
        out = [f"Gemini Enterprise registration {a.get('displayName', a.get('name'))}" for a in self.ge_agents]
        out += [f"engine {e['display']} ({cc.engine_id(e['name'])})" for e in self.engines]
        out += [f"Cloud Run {cc.leaf(n)}" for n in self.run_services]
        out += [f"registry entry {s}" for s in self.registry]
        out += [f"legacy authz policy {n}" for _, n in self.legacy_policies]
        out += [f"legacy authz extension {n}" for _, n in self.legacy_extensions]
        out += [f"legacy service account {s}" for s in self.legacy_sas]
        out += [f"Model Armor template {cc.leaf(n)}" for n in self.ma_templates]
        return out


def plan_teardown(cfg: DemoConfig, state: dict, snap: cc.Snapshot) -> TeardownPlan:
    p = TeardownPlan()
    st_eng = cc.state_engines(state)
    ours = [e for e in snap.engines if cc.engine_is_ours(cfg, state, e)]
    for e in ours:
        rec = st_eng.get(cc.engine_id(e["name"])) or {}
        eff = (e.get("spec") or {}).get("effectiveIdentity")
        principal = rec.get("principal") or (eff if not eff or eff.startswith("principal://") else f"principal://{eff}")
        p.engines.append({"name": e["name"], "display": e.get("displayName") or cc.leaf(e["name"]),
                          "principal": principal})
    p.ge_agents = cc.ge_matches(cfg, snap.ge_agents or [], {cc.engine_id(e["name"]) for e in ours} | set(st_eng))
    p.run_services = sorted(s["name"] for s in snap.run_services
                            if cc.prefix_named(cfg, cc.leaf(s.get("name", ""))) and cc.labelled(cfg, s))
    plat = cc.platform_ids(cfg)
    p.registry = sorted(cc.leaf(s["name"]) for s in snap.registry_services
                        if cc.prefix_named(cfg, cc.leaf(s.get("name", ""))) and cc.leaf(s["name"]) not in plat)
    leg = cc.legacy(cfg)
    for proj, pols in snap.authz_policies.items():
        p.legacy_policies += [(proj, cc.leaf(x["name"])) for x in pols if cc.leaf(x["name"]) in leg["authz_policies"]]
    for proj, exts in snap.authz_extensions.items():
        p.legacy_extensions += [(proj, cc.leaf(x["name"])) for x in exts
                                if cc.leaf(x["name"]) in leg["authz_extensions"]]
    p.legacy_sas = list(snap.legacy_sas)
    p.ma_templates = sorted(t["name"] for t in snap.ma_templates if cc.leaf(t["name"]) != cfg.model_armor_template
                            and cc.prefix_named(cfg, cc.leaf(t["name"])) and cc.labelled(cfg, t))
    return p


def shared_grant_addresses(addresses: list[str] | set[str], egress_agent_shared: bool) -> list[str]:
    """State addresses of the project-wide grants down keeps (see the module docstring)."""
    out = []
    for a in sorted(addresses):
        if a.startswith(f"{IAM_ADDR}.presenters["):
            out.append(a)
        elif a.startswith(f"{IAM_ADDR}.model_armor_agents["):
            key = a.split("[", 1)[1].rstrip("]").strip('"')
            if not key.startswith("gateway-") or egress_agent_shared:
                out.append(a)
    return out


# ------------------------------------------------------------------ up
def up(cfg: DemoConfig, ops, *, yes: bool = False, skip_preflight: bool = False, themes: list[str] | None = None,
       ui: bool = True, ge: bool = False, ask: Callable[[str], bool] | None = None, log=c.log) -> int:
    themes = list(themes if themes is not None else cfg.themes.enabled)
    msg = up_refusal(cfg, ops.state(), ops.gateway_live(), None)
    if msg:
        log(f"[red]{msg}[/]")
        return 2
    log(f"[bold]up: {cfg.project}/{cfg.region} prefix {cfg.prefix}[/] (themes: {', '.join(themes) or 'none'})")
    log("[bold]1/8 Preflight[/]")
    fails = ops.preflight()
    for f in fails:
        log(f"  [red]FAIL[/] {f}")
    if fails and not skip_preflight:
        log("[red]preflight failed; fix it (or --skip-preflight)[/]")
        return 1
    log("[bold]2/8 terraform init[/]")
    ops.tf_init()
    msg = up_refusal(cfg, ops.state(), ops.gateway_live(), ops.tf_managed())
    if msg:
        log(f"[red]{msg}[/]")
        return 2
    log("[bold]3/8 terraform plan + plan-safety[/]")
    planfile, v = ops.tf_plan()
    log(f"  plan: {v.summary()}")
    for addr, actions in v.blockers:
        log(f"  [red]BLOCKED {'/'.join(actions)}[/]: {addr}")
    if not v.ok:
        log("[red]plan-safety: REFUSED (deletes or replaces); nothing applied[/]")
        return 1
    if not yes and not (ask or (lambda q: False))(
            f"Apply ({v.summary()}) to {cfg.project}/{cfg.region}, then deploy {', '.join(themes) or 'no themes'}"
            + (" and the UI" if ui else "") + "?"):
        log("aborted; nothing applied")
        return 1
    log("[bold]4/8 terraform apply[/]")
    ops.tf_apply(planfile)
    log("[bold]5/8 output-sync[/]")
    ops.output_sync()
    log("[bold]6/8 Themes[/] (images once, then deploy-theme per theme; engines one at a time)")
    if themes:
        ops.build_images(themes)
    for t in themes:
        log(f"[bold]deploy-theme {t}[/]")
        ops.deploy_theme(t)
    log("[bold]7/8 UI[/]")
    if ui:
        ops.ui_deploy()
    else:
        log("  skipped (--no-ui)")
    if ge:
        for t in themes:
            ops.publish_ge(t)
    log("[bold]8/8 Verify[/]")
    checks = ops.verify(themes, ui)
    bad = [x for x in checks if not x[1]]
    for name, ok, detail in checks:
        log(f"  {'[green]ok[/]  ' if ok else '[red]FAIL[/]'} {name}: {detail}")
    log("[green]up complete[/]" if not bad else f"[red]up finished, {len(bad)} verify check(s) failed[/]")
    return 0 if not bad else 1


# ------------------------------------------------------------------ down
PRESENTING = ("Make sure NOBODY IS PRESENTING on this environment's UI or Policy Studio: down resets every "
              "theme, then deletes the engines, the UI and the Terraform foundation (about 20-30 min).")


def down(cfg: DemoConfig, ops, *, yes: bool = False, prefix_arg: str | None = None,
         ask: Callable[[str], str] | None = None, dry_run: bool = False, log=c.log) -> int:
    msg = down_refusal_state(cfg, ops.state())
    if msg:
        log(f"[red]{msg}[/]")
        return 2
    ops.tf_init()
    msg = down_refusal_tf(cfg, ops.tf_environment(), ops.tf_addresses(), ops.migrating())
    if msg:
        log(f"[red]{msg}[/]")
        return 2
    plan = ops.inventory()
    log(f"[bold]down: {cfg.project}/{cfg.region} prefix {cfg.prefix}[/]")
    log(f"[yellow]{PRESENTING}[/]")
    log("  CLI deletes (after reset --all):")
    for line in plan.lines() or ["(nothing code-deployed)"]:
        log(f"    {line}")
    log("  then terraform destroy (the foundation). Kept: APIs, the state bucket, the recordings bucket, "
        "the shared project grants.")
    if dry_run:
        reset_all.run(cfg, ops.reset_ops(), dry_run=True, log=log)
        log("[bold]dry run: nothing changed[/]")
        return 0
    go, why = confirm_down(cfg, yes, prefix_arg, ask or (lambda q: ""))
    if not go:
        log(f"[red]{why}[/]")
        return 1
    log("[bold]1/6 reset --all[/]")
    rep = reset_all.run(cfg, ops.reset_ops(), log=log)
    if not rep.ok:
        log("[red]reset --all failed; nothing deleted. Fix it and re-run down.[/]")
        return 1
    plan = ops.inventory()
    log("[bold]2/6 Gemini Enterprise[/]")
    ops.ge_unregister(plan.ge_agents)
    log("[bold]3/6 Engines, Cloud Run, registry entries, legacy[/]")
    ops.teardown(plan)
    log("[bold]4/6 check-clean --strict[/]")
    findings = ops.check_clean()
    if cc.failed(findings, strict=True):
        for x in findings:
            if x.level != cc.INFO:
                log(f"  [red]{x.level}[/] {x.category}: {x.resource} {x.detail}")
        log("[red]not clean; terraform destroy not started. Fix the items above and re-run down.[/]")
        return 1
    log("  clean")
    log("[bold]5/6 terraform destroy[/]")
    kept = ops.keep_shared_grants()
    for a in kept:
        log(f"  kept (state rm, stays in IAM): {a}")
    ops.destroy()
    log("[bold]6/6 Shared grants re-check[/]")
    for line in ops.recheck_shared():
        log(f"  {line}")
    ops.clear_shared_state()
    log("[green]down complete[/] (APIs, state and recordings buckets kept)")
    return 0


# ------------------------------------------------------------------ live operations
class _Base:
    def __init__(self, cfg: DemoConfig, r=None, local_state: str | None = None, log=c.log):
        self.cfg, self.r, self.local_state, self.log = cfg, r or c.client(cfg), local_state, log

    def state(self) -> dict:
        from ..state import load_state
        return load_state()

    def gateway_live(self) -> bool:
        from ..gcp import gateways as gw
        return bool(gw.get_gateway(self.cfg, "egress", self.r))

    def _tfvars(self):
        from .. import tf_foundation as tfm
        from ..gcp.rest import organization_id
        existing = tfm.discover_existing_platform(self.cfg, self.r)
        return organization_id(self.cfg.project) or "", existing


class LiveUpOps(_Base):
    def __init__(self, cfg, r=None, local_state=None, allow_replace=False, skip_build=False, log=c.log):
        super().__init__(cfg, r, local_state, log)
        self.allow_replace, self.skip_build = allow_replace, skip_build

    def preflight(self) -> list[str]:
        from . import preflight as pf
        return [f"{x.name}: {x.detail} (fix: {x.fix})" for x in pf.run(self.cfg, self.r) if x.ok is False]

    def tf_init(self) -> None:
        from .. import tf_foundation as tfm
        org, existing = self._tfvars()
        tfm.init(self.cfg, self.r, local_state=self.local_state, organization_id=org, existing_platform=existing)

    def tf_managed(self) -> bool:
        from .. import tf_foundation as tfm
        from .. import tf_import as tfi
        return tfi.imports_address(tfm.tf_dir(), tfi.EGRESS_GW_ADDR) or tfi.EGRESS_GW_ADDR in tfm.state_addresses()

    def tf_plan(self):
        from .. import tf_foundation as tfm
        return tfm.plan(allow_replace=self.allow_replace)

    def tf_apply(self, planfile) -> None:
        from .. import tf_foundation as tfm
        rb = tfm.recordings_bucket(self.cfg)
        if tfm.ensure_bucket(self.r, self.cfg.project, rb, self.cfg.region, self.cfg.labels, versioning=False):
            self.log(f"  created recordings bucket gs://{rb} (outside Terraform; survives down)")
        tfm.run_tf(["apply", "-input=false", str(planfile)])

    def output_sync(self) -> None:
        from .. import tf_foundation as tfm
        from ..state import load_state, save_state
        save_state(tfm.merge_outputs(load_state(), tfm.outputs()))

    def build_images(self, themes: list[str]) -> None:
        if self.skip_build:
            self.log("  images: reused (--skip-build)")
            return
        from ..themes import load_theme
        from . import deploy_theme as d
        d.build_images(self.cfg, {x.kind for t in themes for x in load_theme(t).components})

    def deploy_theme(self, theme: str) -> None:
        from . import deploy_theme as d
        d.run(self.cfg, theme, skip_build=True)

    def ui_deploy(self) -> None:
        from . import ui as u
        u.deploy(self.cfg, skip_build=self.skip_build)

    def publish_ge(self, theme: str) -> None:
        from . import publish_ge as g
        g.run(self.cfg, theme)

    def verify(self, themes: list[str], ui: bool) -> list[tuple[str, bool, str]]:
        return verify(self.cfg, self.r, self.state(), themes, ui)


def verify(cfg: DemoConfig, r, state: dict, themes: list[str], ui: bool) -> list[tuple[str, bool, str]]:
    """Read-only: the foundation exists and every theme is at the start state (engines unbound)."""
    from ..gcp import engines
    from ..gcp import gateways as gw
    from ..gcp import model_armor as ma
    out: list[tuple[str, bool, str]] = []
    for p in ("egress", "ingress"):
        g = gw.get_gateway(cfg, p, r)
        out.append((f"{p} gateway", bool(g), gw.gateway_name(cfg, p) if g else "missing"))
    pol = gw.get_policy(r, cfg.gateway_project, cfg.region, gw.iap_policy_name(cfg, "egress"))
    out.append(("IAP REQUEST_AUTHZ policy", bool(pol), (pol or {}).get("policyProfile", "missing")))
    out.append(("Model Armor template", bool(ma.get_template(r, cfg)), cfg.model_armor_template))
    sh = state.get("shared") or {}
    out.append(("platform endpoints", len(sh.get("platform_registry_ids") or {}) == len(c.platform_endpoints(cfg)),
                f"{len(sh.get('platform_registry_ids') or {})} in state.json"))
    for t in themes:
        ts = (state.get("themes") or {}).get(t, {})
        engs = [o for o in [ts.get("orchestrator") or {}, *((ts.get("orchestrators") or {}).values())]]
        if not engs or not all(o.get("engine") for o in engs):
            out.append((f"{t} engines", False, "not deployed"))
            continue
        for o in engs:
            eng = engines.get_engine(r, cfg, o["engine"])
            out.append((f"{t} engine {o.get('display_name', cc.leaf(o['engine']))}", bool(eng) and not
                        engines.gateway_config(eng), "missing" if not eng else
                        ("unbound (start state)" if not engines.gateway_config(eng) else "still bound")))
        comps = ts.get("components") or {}
        missing = [k for k, v in comps.items() if not (v.get("url") and v.get("registry"))]
        out.append((f"{t} components", bool(comps) and not missing,
                    f"{len(comps)} deployed" if not missing else f"incomplete: {', '.join(missing)}"))
    if ui:
        out.append(("UI", bool(sh.get("ui_url")), sh.get("ui_url") or "no ui_url in state.json"))
    return out


class LiveDownOps(_Base):
    def tf_init(self) -> None:
        from .. import tf_foundation as tfm
        org, existing = self._tfvars()
        tfm.init(self.cfg, self.r, local_state=self.local_state, organization_id=org, existing_platform=existing,
                 create_bucket=False)

    def tf_environment(self) -> dict | None:
        from .. import tf_foundation as tfm
        return tfm.environment_output()

    def tf_addresses(self) -> set[str]:
        from .. import tf_foundation as tfm
        return tfm.state_addresses()

    def migrating(self) -> bool:
        from .. import tf_foundation as tfm
        return tfm.migrating()

    def snapshot(self) -> cc.Snapshot:
        return cc.collect(self.cfg, self.r, self.state())

    def inventory(self) -> TeardownPlan:
        return plan_teardown(self.cfg, self.state(), self.snapshot())

    def reset_ops(self):
        return reset_all.LiveOps(self.cfg, self.r, log=self.log)

    def ge_unregister(self, agents: list[dict]) -> None:
        from . import publish_ge
        if not agents:
            self.log("  nothing registered")
            return
        publish_ge.unregister(self.cfg, self.r, agents, log=self.log)

    def teardown(self, plan: TeardownPlan) -> None:
        teardown(self.cfg, self.r, plan, log=self.log)

    def check_clean(self) -> list[cc.Finding]:
        return cc.run(self.cfg, self.r, self.state())[1]

    def egress_agent_shared(self) -> bool:
        """True when another agent gateway in the project uses our egress gateway's service-extensions agent
        (or when that can't be read: keeping a grant is the safe side)."""
        from ..gcp import gateways as gw
        from ..gcp.rest import NETWORKSERVICES
        try:
            mine = gw.get_gateway(self.cfg, "egress", self.r) or {}
            sa = (mine.get("agentGatewayCard") or {}).get("serviceExtensionsServiceAccount")
            if not sa:
                return False
            ours = {gw.gateway_resource(self.cfg, p) for p in ("egress", "ingress")}
            for proj in sorted({self.cfg.project, self.cfg.gateway_project}):
                for g in self.r.list_all(f"{NETWORKSERVICES}/projects/{proj}/locations/-/agentGateways",
                                         "agentGateways"):
                    if g.get("name") not in ours and \
                            (g.get("agentGatewayCard") or {}).get("serviceExtensionsServiceAccount") == sa:
                        return True
            return False
        except Exception:  # noqa: BLE001
            return True

    def keep_shared_grants(self) -> list[str]:
        from .. import tf_foundation as tfm
        addrs = shared_grant_addresses(tfm.state_addresses(), self.egress_agent_shared())
        tfm.state_rm(addrs)
        return addrs

    def destroy(self) -> None:
        from .. import tf_foundation as tfm
        tfm.destroy()

    def recheck_shared(self) -> list[str]:
        return recheck_shared(self.cfg, self.r)

    def clear_shared_state(self) -> None:
        from ..state import load_state, save_state
        st = load_state()
        st["shared"], st["themes"] = {}, {}
        st.pop("pending_ops", None)
        st.pop("code_updates", None)
        save_state(st)


def recheck_shared(cfg: DemoConfig, r) -> list[str]:
    """Read-only, after the destroy: do the project's static service agents still hold the Model Armor roles
    (other environments in the project rely on them)? Reports only."""
    from ..gcp import project
    from ..gcp.rest import project_number
    try:
        pol = project.get_project_policy(r, cfg.project)
        num = project_number(cfg.project)
    except Exception as e:  # noqa: BLE001
        return [f"[yellow]could not read the project IAM policy: {e}[/]"]
    has: dict[str, set[str]] = {}
    for b in pol.get("bindings", []):
        if not b.get("condition"):
            for m in b.get("members", []):
                has.setdefault(m, set()).add(b["role"])
    out = []
    for s in STATIC_AGENTS:
        m = f"serviceAccount:service-{num}@{s}.iam.gserviceaccount.com"
        missing = [x for x in MA_ROLES if x not in has.get(m, set())]
        out.append(f"{s}: Model Armor roles " + ("present" if not missing else
                   f"[yellow]missing {', '.join(missing)}[/] (needed only if another environment in the project "
                   "uses Model Armor; its next tf apply re-adds them)"))
    return out


def teardown(cfg: DemoConfig, r, plan: TeardownPlan, log=c.log) -> None:
    """Delete what plan_teardown found. Every delete treats 404 as done. Engines in parallel (force=true)."""
    from concurrent.futures import ThreadPoolExecutor

    from ..gcp import gateways as gw
    from ..gcp import project, registry
    from ..gcp.rest import RUN, GcpError, aiplatform, modelarmor
    from ..state import load_state, save_state

    def gone_ok(fn):
        try:
            fn()
        except GcpError as e:
            if not e.not_found:
                raise

    def engine(e: dict) -> None:
        log(f"  deleting engine {e['display']}")
        def rm():
            op = r.delete(f"{aiplatform(cfg.region)}/{e['name']}", params={"force": "true"})
            r.wait(op, aiplatform(cfg.region), timeout=1200)
        gone_ok(rm)
        if e.get("principal"):
            project.project_remove_member(r, cfg.project, e["principal"])

    def run_service(name: str) -> None:
        log(f"  deleting Cloud Run {cc.leaf(name)}")
        def rm():
            op = r.delete(f"{RUN}/{name}")
            r.wait(op, RUN, timeout=600, interval=5)
        gone_ok(rm)

    with ThreadPoolExecutor(max(1, min(8, len(plan.engines) + len(plan.run_services)))) as ex:
        list(ex.map(engine, plan.engines))
        list(ex.map(run_service, plan.run_services))
    for sid in plan.registry:
        if registry.delete_service(r, cfg.registry_parent, sid):
            log(f"  deleted registry entry {sid}")
    for proj, name in plan.legacy_policies:
        if gw.delete_policy(r, proj, cfg.region, name):
            log(f"  deleted legacy authz policy {name}")
    for proj, name in plan.legacy_extensions:
        if gw.delete_extension(r, proj, cfg.region, name):
            log(f"  deleted legacy authz extension {name}")
    for email in plan.legacy_sas:
        project.project_remove_member(r, cfg.project, f"serviceAccount:{email}")
        if project.delete_sa(r, cfg.project, email):
            log(f"  deleted legacy service account {email}")
    for name in plan.ma_templates:
        gone_ok(lambda name=name: r.delete(f"{modelarmor(cfg.region)}/{name}"))
        log(f"  deleted Model Armor template {cc.leaf(name)}")
    st = load_state()
    st["themes"] = {}
    st.get("shared", {}).pop("ui_url", None)
    st.pop("pending_ops", None)
    st.pop("code_updates", None)
    save_state(st)
    log("  cleared state.json themes and ui_url")


__all__ = ["up", "down", "up_refusal", "down_refusal_state", "down_refusal_tf", "confirm_down", "plan_teardown",
           "shared_grant_addresses", "TeardownPlan", "LiveUpOps", "LiveDownOps", "verify", "teardown"]
