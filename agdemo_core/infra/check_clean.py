"""`./agdemo check-clean [--strict]`: read-only check that nothing but the Terraform foundation is left
(docs/TERRAFORM_PLAN.md §4.3 step 4, phase T3). `down` runs it before `terraform destroy`, and the destroy
guard (`terraform_data.teardown_guard`, infra/terraform/guards.tf) runs it again inside the destroy.

Two parts:
- collect(cfg, r, state) -> Snapshot   read-only lists and GETs (IAP getIamPolicy is a POST, but read-only)
- classify(cfg, state, snap, derived)  pure: Snapshot -> findings

Levels:
  BLOCK  non-foundation things that `down` must remove first (engine bindings to this environment's gateways,
         theme engines, theme Cloud Run services, prefix registry entries, grants on prefix registry entries,
         CONTENT_AUTHZ / other authz policies on our gateways, non-foundation prefix extensions and templates).
  WARN   the UI service, Gemini Enterprise registrations of our engines, foundation drift (IAP dry run, IAP
         policy repointed), legacy resources, stale state.json themes, and checks that couldn't be read.
  INFO   reported, never fails (things in shared places that aren't ours).
Exit: default fails on BLOCK; --strict (down and the guard) fails on BLOCK or WARN.

"Ours" is decided by name and label: ids start with `<prefix>-` and, where the resource type has labels,
`demo-prefix=<prefix>` (deploy-theme, ui deploy, bootstrap and Terraform all set it). Engines in
state.json themes are ours too. A prefix must not be the start of another environment's prefix plus `-`
(e.g. `agd` next to `agd-x`): names can't tell them apart.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..config import DemoConfig
from ..gcp import gateways as gw
from ..gcp import iap
from . import common as c

BLOCK, WARN, INFO = "BLOCK", "WARN", "INFO"
GE_SUFFIX = "(Agent Gateway demo)"       # publish_ge's display-name suffix


@dataclass
class Finding:
    level: str
    category: str
    resource: str
    detail: str = ""
    fix: str = ""


@dataclass
class Snapshot:
    engines: list[dict] = field(default_factory=list)                 # reasoningEngines in the region
    run_services: list[dict] = field(default_factory=list)            # Cloud Run v2 services in the region
    registry_services: list[dict] = field(default_factory=list)       # Agent Registry services (registry_parent)
    iap_policies: dict[str, dict] = field(default_factory=dict)       # registryResource -> IAP IAM policy
    registry_root_policy: dict | None = None                          # region-wide agentRegistry IAP policy
    authz_policies: dict[str, list[dict]] = field(default_factory=dict)    # project -> policies
    authz_extensions: dict[str, list[dict]] = field(default_factory=dict)  # project -> extensions
    ma_templates: list[dict] = field(default_factory=list)
    ge_agents: list[dict] | None = None                               # None = no GE app configured
    legacy_sas: list[str] = field(default_factory=list)               # legacy SAs still present (emails)
    errors: dict[str, str] = field(default_factory=dict)              # check -> error


@dataclass
class Derived:
    """Values that need a lookup (project number / org): computed once by the caller, passed in for tests."""
    principal_set: str          # the platform egressor member (every Agent Identity agent in the project)


# ------------------------------------------------------------------ naming (pure)
def leaf(name: str) -> str:
    return (name or "").rstrip("/").split("/")[-1]


def tail(resource: str) -> str:
    """'projects/X/locations/R/kind/N' -> 'R/kind/N' (project id and number forms compare equal)."""
    return resource.split("/locations/", 1)[1] if "/locations/" in (resource or "") else resource or ""


def prefix_named(cfg: DemoConfig, rid: str) -> bool:
    return rid.startswith(cfg.prefix + "-")


def labelled(cfg: DemoConfig, obj: dict) -> bool:
    return (obj.get("labels") or {}).get("demo-prefix") == cfg.prefix


def platform_ids(cfg: DemoConfig) -> set[str]:
    return {c.platform_service_id(cfg, s) for s, _ in c.platform_endpoints(cfg)}


def legacy(cfg: DemoConfig) -> dict[str, list[str]]:
    from ..tf_import import legacy_names
    return legacy_names(cfg)


def foundation_extensions(cfg: DemoConfig) -> set[tuple[str, str]]:
    """(project, name) of the Terraform-managed authz extensions."""
    out = {(cfg.gateway_project, gw.iap_extension_name(cfg, "egress"))}
    out |= {(p, gw.ma_extension_name(cfg)) for p in {cfg.project, cfg.gateway_project}}
    return out


def gateway_paths(cfg: DemoConfig, resources: list[str]) -> list[str]:
    """Which of this environment's gateways ('egress'/'ingress') the resource names point at."""
    want = {p: tail(gw.gateway_resource(cfg, p)) for p in ("egress", "ingress")}
    return [p for p, t in want.items() if any(tail(x) == t for x in resources or [])]


def engine_id(name: str) -> str:
    return leaf(name)


def bound_paths(cfg: DemoConfig, eng: dict) -> list[str]:
    agc = ((eng.get("spec") or {}).get("deploymentSpec") or {}).get("agentGatewayConfig") or {}
    res = [(agc.get(k) or {}).get("agentGateway") for k in ("agentToAnywhereConfig", "clientToAgentConfig")]
    return gateway_paths(cfg, [x for x in res if x])


def state_engines(state: dict) -> dict[str, dict]:
    """engine id -> {"engine", "principal", "theme"} for every orchestrator in state.json themes."""
    out: dict[str, dict] = {}
    for tid, ts in (state.get("themes") or {}).items():
        for o in [ts.get("orchestrator") or {}, *((ts.get("orchestrators") or {}).values())]:
            if o.get("engine"):
                out[engine_id(o["engine"])] = {"engine": o["engine"], "principal": o.get("principal"), "theme": tid}
    return out


def engine_is_ours(cfg: DemoConfig, state: dict, eng: dict) -> bool:
    if engine_id(eng.get("name", "")) in state_engines(state):
        return True
    return prefix_named(cfg, eng.get("displayName", "")) and labelled(cfg, eng)


def our_principals(cfg: DemoConfig, state: dict, snap: Snapshot) -> set[str]:
    out = {v["principal"] for v in state_engines(state).values() if v.get("principal")}
    for e in snap.engines:
        if engine_is_ours(cfg, state, e):
            eff = (e.get("spec") or {}).get("effectiveIdentity")
            if eff:
                out.add(eff if eff.startswith("principal://") else f"principal://{eff}")
    return out


def theme_service_names(cfg: DemoConfig, state: dict) -> set[str]:
    """Cloud Run / registry ids deploy-theme uses: state.json components plus every theme folder's components."""
    out = {c.service_name(cfg, tid, cid) for tid, ts in (state.get("themes") or {}).items()
           for cid in (ts.get("components") or {})}
    try:
        from ..themes import list_themes, load_theme
        for tid in list_themes():
            try:
                out |= {c.service_name(cfg, tid, x.id) for x in load_theme(tid).components}
            except Exception:  # noqa: BLE001 (a broken theme folder must not break the check)
                pass
    except Exception:  # noqa: BLE001
        pass
    return out


def is_platform_egressor(b: dict, member: str, principal_set: str) -> bool:
    return b.get("role") == iap.EGRESSOR and member == principal_set and not b.get("condition")


def extra_bindings(policy: dict, keep) -> list[tuple[str, str, dict | None]]:
    """(role, member, condition) of every binding member `keep(binding, member)` rejects."""
    out = []
    for b in (policy or {}).get("bindings", []) or []:
        for m in b.get("members", []) or []:
            if not keep(b, m):
                out.append((b.get("role", ""), m, b.get("condition")))
    return out


def ge_engine(agent: dict) -> str:
    adk = agent.get("adkAgentDefinition") or agent.get("adk_agent_definition") or {}
    pre = adk.get("provisionedReasoningEngine") or adk.get("provisioned_reasoning_engine") or {}
    return pre.get("reasoningEngine") or pre.get("reasoning_engine") or ""


def ge_matches(cfg: DemoConfig, agents: list[dict], engine_ids: set[str]) -> list[dict]:
    """GE registrations whose ADK agent is one of these engines (same region)."""
    return [a for a in agents or [] if engine_id(ge_engine(a)) in engine_ids
            and f"/locations/{cfg.region}/" in ge_engine(a)]


def iap_drift(cfg: DemoConfig, ext: dict) -> list[str]:
    want = gw.iap_extension_body(cfg)
    out = []
    if bool(ext.get("failOpen", False)) != bool(want["failOpen"]):
        out.append(f"failOpen={ext.get('failOpen', False)} (demo.yaml: {want['failOpen']})")
    md = ext.get("metadata") or {}
    for k, v in want["metadata"].items():
        if md.get(k) != v:
            out.append(f"metadata.{k}={md.get(k)!r} (demo.yaml: {v!r})")
    extra = sorted(k for k in md if k not in want["metadata"])
    if extra:
        out.append(f"metadata also has {', '.join(extra)} (e.g. iamEnforcementMode=DRY_RUN from Policy Studio)")
    return out


def state_describes(cfg: DemoConfig, state: dict) -> bool:
    """False when config/state.json belongs to another environment (one checkout, two configs): its themes and
    engines then say nothing about this one."""
    sh = state.get("shared") or {}
    env = sh.get("tf_environment")
    if env and (env.get("project_id"), env.get("region"), env.get("prefix")) != (cfg.project, cfg.region, cfg.prefix):
        return False
    return not sh.get("egress_gateway") or tail(sh["egress_gateway"]) == tail(cfg.egress_gateway_resource)


# ------------------------------------------------------------------ classification (pure)
def classify(cfg: DemoConfig, state: dict, snap: Snapshot, derived: Derived) -> list[Finding]:
    f: list[Finding] = []
    add = lambda *a: f.append(Finding(*a))   # noqa: E731
    if not state_describes(cfg, state):
        add(WARN, "state", "config/state.json", "describes another environment; ignored for this check",
            "give each environment its own checkout (state.json is per checkout)")
        state = {"shared": {}, "themes": {}}
    ours_eng = {engine_id(e["name"]) for e in snap.engines if engine_is_ours(cfg, state, e)}

    # engines and their gateway bindings
    for e in snap.engines:
        disp = e.get("displayName") or leaf(e.get("name", ""))
        ours = engine_id(e.get("name", "")) in ours_eng
        for p in bound_paths(cfg, e):
            add(BLOCK, "binding", f"engine {disp} ({engine_id(e['name'])})", f"bound to the {p} gateway "
                f"{gw.gateway_name(cfg, p)}" + ("" if ours else " (not ours)"),
                "./agdemo reset --all" if ours else "unbind it by hand (not created by this environment)")
        if ours:
            add(BLOCK, "engine", f"engine {disp} ({engine_id(e['name'])})", "theme engine (Agent Runtime)",
                "./agdemo down deletes it (or ./agdemo teardown <theme>)")

    # Cloud Run
    ui = c.ui_service_name(cfg)
    known = theme_service_names(cfg, state)
    for s in snap.run_services:
        sid = leaf(s.get("name", ""))
        if not prefix_named(cfg, sid):
            continue
        if not labelled(cfg, s):
            add(WARN, "cloud_run", f"Cloud Run {sid}", f"named {cfg.prefix}-* but not labelled "
                f"demo-prefix={cfg.prefix}; check who owns it", "delete it by hand if it is ours")
        elif sid == ui:
            add(WARN, "ui", f"Cloud Run {sid}", "the demo UI", "./agdemo down deletes it")
        else:
            add(BLOCK, "cloud_run", f"Cloud Run {sid}", "theme service" if sid in known else
                "prefix service that isn't a theme component (private MCP / manual?)", "./agdemo down deletes it")

    # Agent Registry entries and their grants
    plat = platform_ids(cfg)
    for svc in snap.registry_services:
        sid = leaf(svc.get("name", ""))
        if not prefix_named(cfg, sid):
            continue
        rr = svc.get("registryResource") or ""
        if sid in plat:
            extra = extra_bindings(snap.iap_policies.get(rr, {}),
                                   lambda b, m: is_platform_egressor(b, m, derived.principal_set))
        else:
            add(BLOCK, "registry", f"registry {sid}", "theme entry" if sid in known else
                "not a theme entry (made by Policy Studio or by hand?)", "./agdemo down deletes it")
            extra = extra_bindings(snap.iap_policies.get(rr, {}), lambda b, m: False)
        for role, m, cond in extra:
            add(BLOCK, "grant", f"registry {sid}", f"{role} for {m}" + (f" if {cond.get('expression')}"
                if cond else ""), "./agdemo reset --all")
    mine = our_principals(cfg, state, snap)
    for role, m, cond in extra_bindings(snap.registry_root_policy or {}, lambda b, m: False):
        if m in mine:
            add(BLOCK, "grant", f"registry-wide ({cfg.region})", f"{role} for our agent {m}", "./agdemo reset --all")
        else:
            add(INFO, "grant", f"registry-wide ({cfg.region})", f"{role} for {m} (not ours; left alone)")

    # authz policies on our gateways
    iap_pol, ma_pols = gw.iap_policy_name(cfg, "egress"), {gw.ma_policy_name(cfg, p) for p in ("egress", "ingress")}
    leg = legacy(cfg)
    iap_ext_tail = tail(f"projects/{cfg.gateway_project}/locations/{cfg.region}/authzExtensions/"
                        f"{gw.iap_extension_name(cfg, 'egress')}")
    for proj, pols in snap.authz_policies.items():
        for p in pols:
            name = leaf(p.get("name", ""))
            paths = gateway_paths(cfg, (p.get("target") or {}).get("resources") or [])
            provider = ((p.get("customProvider") or {}).get("authzExtension") or {}).get("resources") or []
            if name == iap_pol and proj == cfg.gateway_project:
                if provider and not any(tail(x) == iap_ext_tail for x in provider):
                    add(WARN, "drift", f"authz policy {name}", f"points at {', '.join(map(leaf, provider))}, "
                        f"not {gw.iap_extension_name(cfg, 'egress')} (Policy Studio's own-extension copy)",
                        "./agdemo reset --all")
                continue
            if name in leg["authz_policies"]:
                add(WARN, "legacy", f"authz policy {name}", "legacy (never in Terraform)",
                    "./agdemo down deletes it (or runbook step a)")
                continue
            if not paths:
                if prefix_named(cfg, name) and labelled(cfg, p):
                    add(WARN, "authz_policy", f"authz policy {name} ({proj})", "ours, but targets no gateway of "
                        "this environment", "delete it by hand")
                continue
            if p.get("policyProfile") == "CONTENT_AUTHZ":
                add(BLOCK, "model_armor", f"authz policy {name}", f"CONTENT_AUTHZ on {'/'.join(paths)}"
                    + (" (the Model Armor checkbox)" if name in ma_pols else ""), "./agdemo reset --all")
            else:
                add(BLOCK, "authz_policy", f"authz policy {name}", f"{p.get('policyProfile', '?')} on "
                    f"{'/'.join(paths)}" + ("" if prefix_named(cfg, name) and labelled(cfg, p) else " (not ours)"),
                    "./agdemo reset --all" if prefix_named(cfg, name) and labelled(cfg, p) else "delete it by hand")

    # authz extensions
    found = foundation_extensions(cfg)
    for proj, exts in snap.authz_extensions.items():
        for x in exts:
            name = leaf(x.get("name", ""))
            if (proj, name) in found:
                if name == gw.iap_extension_name(cfg, "egress"):
                    for d in iap_drift(cfg, x):
                        add(WARN, "drift", f"authz extension {name}", d, "./agdemo reset --all")
                continue
            if name in leg["authz_extensions"]:
                add(WARN, "legacy", f"authz extension {name}", "legacy (never in Terraform)",
                    "./agdemo down deletes it (or runbook step a)")
            elif prefix_named(cfg, name) or labelled(cfg, x):
                add(BLOCK, "authz_extension", f"authz extension {name} ({proj})", "not part of the foundation "
                    "(e.g. a Policy Studio copy)", "./agdemo reset --all" if prefix_named(cfg, name) and
                    labelled(cfg, x) else "delete it by hand")

    # Model Armor templates
    for t in snap.ma_templates:
        name = leaf(t.get("name", ""))
        if name != cfg.model_armor_template and (prefix_named(cfg, name) or labelled(cfg, t)):
            add(BLOCK, "ma_template", f"Model Armor template {name}", "not part of the foundation",
                "./agdemo down deletes it" if prefix_named(cfg, name) and labelled(cfg, t) else "delete it by hand")

    # Gemini Enterprise
    existing = {engine_id(e.get("name", "")) for e in snap.engines}
    for a in snap.ge_agents or []:
        eid = engine_id(ge_engine(a))
        if not eid:
            continue
        if eid in ours_eng or eid in state_engines(state):
            add(WARN, "gemini_enterprise", f"GE agent {a.get('displayName', '?')}", f"registered for engine {eid}",
                "./agdemo down unregisters it")
        elif (a.get("displayName") or "").endswith(GE_SUFFIX) and f"/locations/{cfg.region}/" in ge_engine(a) \
                and eid not in existing:
            add(INFO, "gemini_enterprise", f"GE agent {a.get('displayName')}", f"its engine {eid} no longer exists")

    # service accounts Terraform never adopted
    for sa in snap.legacy_sas:
        add(WARN, "legacy", f"service account {sa}", "legacy (never in Terraform)", "./agdemo down deletes it")

    # state.json and unreadable checks
    stale = [t for t in (state.get("themes") or {})]
    if stale:
        add(WARN, "state", "config/state.json", f"still lists themes: {', '.join(sorted(stale))}",
            "./agdemo down clears it (or ./agdemo teardown <theme>)")
    for k, msg in snap.errors.items():
        add(WARN, "check_error", k, msg[:200], "fix access and re-run (strict mode can't pass blind)")
    order = {BLOCK: 0, WARN: 1, INFO: 2}
    return sorted(f, key=lambda x: (order[x.level], x.category, x.resource))


def failed(findings: list[Finding], strict: bool) -> bool:
    return any(x.level == BLOCK or (strict and x.level == WARN) for x in findings)


def counts(findings: list[Finding]) -> dict[str, int]:
    return {lv: sum(1 for x in findings if x.level == lv) for lv in (BLOCK, WARN, INFO)}


def expect_matches(cfg: DemoConfig, expect: str | None) -> str | None:
    """--expect PROJECT/REGION/PREFIX (the destroy guard passes the environment from Terraform state).
    Returns an error message when config/demo.yaml describes another environment."""
    if not expect:
        return None
    mine = f"{cfg.project}/{cfg.region}/{cfg.prefix}"
    if expect != mine:
        return (f"config/demo.yaml describes {mine}, but the environment being checked is {expect}. "
                "Point AGDEMO_CONFIG at that environment's demo.yaml and re-run.")
    return None


# ------------------------------------------------------------------ collection (read-only)
def collect(cfg: DemoConfig, r, state: dict) -> Snapshot:
    from ..gcp import registry
    from ..gcp.rest import IAM, RUN, aiplatform, modelarmor, project_number
    from ..infra import publish_ge

    snap = Snapshot()
    region = cfg.region

    def guard(key: str, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 (record and go on: --strict fails on errors)
            snap.errors[key] = f"{type(e).__name__}: {e}"
            return None

    snap.engines = guard("engines", lambda: r.list_all(
        f"{aiplatform(region)}/projects/{cfg.project}/locations/{region}/reasoningEngines", "reasoningEngines")) or []
    snap.run_services = guard("cloud_run", lambda: r.list_all(
        f"{RUN}/projects/{cfg.project}/locations/{region}/services", "services")) or []
    snap.registry_services = guard("registry", lambda: registry.list_services(r, cfg.registry_parent)) or []
    for svc in snap.registry_services:
        sid, rr = leaf(svc.get("name", "")), svc.get("registryResource")
        if prefix_named(cfg, sid) and rr:
            pol = guard(f"iap policy {sid}", lambda rr=rr: iap.get_policy(r, iap.from_registry_resource(rr)))
            if pol is not None:
                snap.iap_policies[rr] = pol
    snap.registry_root_policy = guard("iap policy (registry-wide)", lambda: iap.get_policy(
        r, iap.resource_url(project_number(cfg.gateway_project), region)))
    for proj in sorted({cfg.project, cfg.gateway_project}):
        snap.authz_policies[proj] = guard(f"authz policies {proj}", lambda proj=proj: gw.list_policies(
            r, proj, region)) or []
        snap.authz_extensions[proj] = guard(f"authz extensions {proj}", lambda proj=proj: r.list_all(
            gw.ext_url(proj, region), "authzExtensions")) or []
    snap.ma_templates = guard("model armor templates", lambda: r.list_all(
        f"{modelarmor(region)}/projects/{cfg.project}/locations/{region}/templates", "templates")) or []
    if cfg.gemini_enterprise.app_id:
        snap.ge_agents = guard("gemini enterprise agents", lambda: publish_ge.list_agents(cfg, r)) or []
    leg = set(legacy(cfg)["service_accounts"])
    sas = guard("service accounts", lambda: r.list_all(f"{IAM}/projects/{cfg.project}/serviceAccounts", "accounts",
                                                       params={"pageSize": 100})) or []
    snap.legacy_sas = sorted(a["email"] for a in sas if a.get("email") in leg)
    return snap


def derived(cfg: DemoConfig) -> Derived:
    from ..gcp.engines import project_principal_set
    return Derived(principal_set=project_principal_set(cfg))


def run(cfg: DemoConfig, r, state: dict) -> tuple[Snapshot, list[Finding]]:
    snap = collect(cfg, r, state)
    try:
        d = derived(cfg)
    except Exception as e:  # noqa: BLE001
        snap.errors["principal set"] = f"{type(e).__name__}: {e}"
        d = Derived(principal_set="")
    return snap, classify(cfg, state, snap, d)
