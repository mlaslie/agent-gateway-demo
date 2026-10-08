"""./agdemo bootstrap — shared resources (idempotent). Writes ids to config/state.json `shared`."""
from __future__ import annotations

import time

from ..config import DemoConfig
from ..gcp import gateways as gw
from ..gcp import iap, project, registry
from ..gcp import model_armor as ma
from ..gcp.engines import project_principal_set
from ..gcp.rest import GcpError, Rest, project_number
from ..state import load_state, save_state
from . import common as c


def _save_shared(**kv) -> None:
    st = load_state()
    st.setdefault("shared", {}).update(kv)
    save_state(st)


def step_apis(cfg: DemoConfig, r: Rest) -> None:
    have = project.enabled_services(r, cfg.project)
    missing = [s for s in project.REQUIRED_APIS if s not in have]
    if cfg.gateway_project != cfg.project:
        gh = project.enabled_services(r, cfg.gateway_project)
        gmiss = [s for s in project.REQUIRED_APIS if s not in gh]
        if gmiss:
            c.log(f"  enabling {len(gmiss)} APIs in gateway project {cfg.gateway_project}")
            project.enable_services(r, cfg.gateway_project, gmiss)
    if missing:
        c.log(f"  enabling {', '.join(missing)}")
        project.enable_services(r, cfg.project, missing)
    else:
        c.log("  [green]all required APIs enabled[/]")


def step_service_accounts(cfg: DemoConfig, r: Rest, me: str | None) -> dict[str, str]:
    sas = {
        "denied_caller_sa": project.ensure_sa(r, cfg.project, c.denied_sa_id(cfg), "agdemo ingress denied caller"),
        "allowed_caller_sa": project.ensure_sa(r, cfg.project, c.allowed_sa_id(cfg), "agdemo ingress allowed caller"),
        "ui_sa": project.ensure_sa(r, cfg.project, c.ui_sa_id(cfg), "agdemo demo UI backend"),
        "targets_sa": project.ensure_sa(r, cfg.project, c.run_sa_id(cfg), "agdemo Cloud Run demo targets"),
    }
    time.sleep(2)
    # Both demo callers may call Agent Runtime when nothing governs ingress (scenario: direct).
    pairs = [(f"serviceAccount:{sas['denied_caller_sa']}", "roles/aiplatform.user"),
             (f"serviceAccount:{sas['allowed_caller_sa']}", "roles/aiplatform.user")]
    if cfg.ingress_demo.allowed_principal and cfg.ingress_demo.allowed_principal.startswith(
            ("user:", "group:", "serviceAccount:")):
        pairs.append((cfg.ingress_demo.allowed_principal, "roles/aiplatform.user"))
    # Cloud Run demo targets: the A2A agents call Gemini on Vertex AI.
    targets = f"serviceAccount:{sas['targets_sa']}"
    pairs += [(targets, "roles/aiplatform.user"), (targets, "roles/logging.logWriter")]
    # UI backend: reads/applies policies and calls the orchestrator.
    ui = f"serviceAccount:{sas['ui_sa']}"
    for role in ("roles/aiplatform.user", "roles/agentregistry.viewer", "roles/iap.admin",
                 "roles/networkservices.viewer", "roles/networksecurity.editor",
                 "roles/networkservices.serviceExtensionsAdmin", "roles/modelarmor.viewer",
                 "roles/logging.viewer", "roles/serviceusage.serviceUsageConsumer"):
        pairs.append((ui, role))
    added = project.project_add_bindings(r, cfg.project, pairs)
    for m, role in added:
        c.log(f"  granted {role} to {m}")
    # Who may impersonate the demo callers: the UI SA, and the operator running the CLI.
    impersonators = [ui] + ([me] if me else [])
    for caller in (sas["denied_caller_sa"], sas["allowed_caller_sa"]):
        for m in impersonators:
            if project.sa_add_binding(r, cfg.project, caller, m, "roles/iam.serviceAccountTokenCreator"):
                c.log(f"  {m} can impersonate {caller}")
    return sas


def step_artifact_registry(cfg: DemoConfig, r: Rest) -> str:
    return project.ensure_docker_repo(r, cfg.project, cfg.region, c.repo_id(cfg), cfg.labels)


def step_staging_bucket(cfg: DemoConfig, r: Rest) -> str:
    name = c.staging_bucket(cfg)
    url = f"https://storage.googleapis.com/storage/v1/b/{name}"
    if r.get(url, ok404=True) is None:
        try:
            r.post("https://storage.googleapis.com/storage/v1/b", params={"project": cfg.project},
                   json={"name": name, "location": cfg.region.upper(), "labels": cfg.labels,
                         "iamConfiguration": {"uniformBucketLevelAccess": {"enabled": True}}})
        except GcpError as e:
            if not e.conflict:
                raise
    return f"gs://{name}"


def step_gateways(cfg: DemoConfig, r: Rest, timeout: float = 2400) -> dict[str, dict]:
    if not cfg.gateways.create:
        out = {}
        for p in ("egress", "ingress"):
            g = gw.get_gateway(cfg, p, r)
            if not g:
                raise RuntimeError(f"gateways.create=false but {gw.gateway_resource(cfg, p)} does not exist")
            out[p] = g
        return out
    ops = {}
    for p in ("egress", "ingress"):
        op = gw.start_create_gateway(cfg, p, r)
        if op:
            c.log(f"  creating {p} gateway {gw.gateway_name(cfg, p)} (several minutes)")
            ops[p] = op
    start = time.time()
    out: dict[str, dict] = {}
    for p in ("egress", "ingress"):
        if p in ops:
            r.wait(ops[p], "https://networkservices.googleapis.com/v1", timeout=timeout, interval=15,
                   on_tick=lambda s, p=p: c.log(f"  ... {p} gateway still creating ({int(s)}s)")
                   if int(s) % 120 < 15 else None)
        # creation is done when the gateway is readable
        while True:
            g = gw.get_gateway(cfg, p, r)
            if g or time.time() - start > timeout:
                break
            time.sleep(15)
        if not g:
            raise TimeoutError(f"{p} gateway not ready after {int(timeout)}s")
        out[p] = g
        c.log(f"  [green]{p} gateway ready[/]: {g['name']}")
    return out


def step_iap_authz(cfg: DemoConfig, r: Rest) -> dict[str, str]:
    out = {}
    for p in ("egress", "ingress"):
        proj = gw.gateway_project(cfg, p)
        ext = gw.iap_extension_name(cfg, p)
        res = gw.ensure_extension(r, proj, cfg.region, ext, gw.iap_extension_body(cfg))
        ext_res = f"projects/{proj}/locations/{cfg.region}/authzExtensions/{ext}"
        pol = gw.iap_policy_name(cfg, p)
        res2 = gw.ensure_policy(r, proj, cfg.region, pol,
                                gw.policy_body(cfg, gw.gateway_resource(cfg, p), "REQUEST_AUTHZ", ext_res))
        c.log(f"  {p}: IAP extension {ext} ({res}), REQUEST_AUTHZ policy {pol} ({res2}), "
              f"enforcement={cfg.gateways.iap_enforcement}")
        out[f"{p}_iap_policy"] = f"projects/{proj}/locations/{cfg.region}/authzPolicies/{pol}"
        out[f"{p}_iap_extension"] = ext_res
    return out


def step_model_armor(cfg: DemoConfig, r: Rest, gws: dict[str, dict]) -> dict[str, str]:
    res = ma.ensure_template(r, cfg)
    tmpl = ma.template_name(cfg)
    c.log(f"  Model Armor template {cfg.model_armor_template} ({res})")
    ext = gw.ma_extension_name(cfg)
    out = {"model_armor_template": tmpl}
    # The extension lives in each gateway's project (egress may be cross-project).
    for proj in sorted({cfg.project, cfg.gateway_project}):
        res = gw.ensure_extension(r, proj, cfg.region, ext, gw.ma_extension_body(cfg, tmpl))
        c.log(f"  Model Armor authz extension {ext} in {proj} ({res}); policy NOT attached (UI checkbox)")
    out["model_armor_extension"] = f"projects/{cfg.project}/locations/{cfg.region}/authzExtensions/{ext}"
    # Service agents that call Model Armor for the gateways.
    num = project_number(cfg.project)
    agents = {f"serviceAccount:service-{num}@gcp-sa-dep.iam.gserviceaccount.com",
              f"serviceAccount:service-{num}@gcp-sa-aiplatform-re.iam.gserviceaccount.com"}
    for g in gws.values():
        sa = (g.get("agentGatewayCard") or {}).get("serviceExtensionsServiceAccount")
        if sa:
            agents.add(f"serviceAccount:{sa}")
    for m in sorted(agents):
        try:
            added = project.project_add_bindings(r, cfg.project, [
                (m, "roles/modelarmor.calloutUser"), (m, "roles/modelarmor.user"),
                (m, "roles/serviceusage.serviceUsageConsumer")])
            if added:
                c.log(f"  granted Model Armor roles to {m}")
        except GcpError as e:
            c.log(f"  [yellow]could not grant Model Armor roles to {m}: {e.message[:160]}[/]")
    return out


def step_platform_endpoints(cfg: DemoConfig, r: Rest) -> dict[str, str]:
    """Register the Google APIs the Runtime needs and grant every Agent Identity agent in the project
    roles/iap.egressor on each (only these entries — the rest of the registry stays default-deny)."""
    parent = cfg.registry_parent
    member = project_principal_set(cfg)
    ids: dict[str, str] = {}
    existing = {s["name"].split("/")[-1]: s for s in registry.list_services(r, parent)}
    url_owner = {i.get("url"): s for s in existing.values() for i in s.get("interfaces", [])}
    for s, url in c.platform_endpoints(cfg):
        sid = c.platform_service_id(cfg, s)
        svc = existing.get(sid)
        if svc is None and url in url_owner:
            other = url_owner[url]["name"].split("/")[-1]
            if not other.startswith(cfg.prefix):
                c.log(f"  [yellow]{url} already registered as {other} (not ours) — reusing it[/]")
            svc = url_owner[url]
        if svc is None:
            try:
                svc = registry.ensure_service(r, parent, sid, registry.endpoint_body(
                    f"{cfg.prefix} {url.split('//')[1]}", url, "agdemo platform endpoint (default-deny allowlist)"))
            except GcpError as e:
                c.log(f"  [red]register {url} failed: {e.message[:200]}[/]")
                continue
        rr = svc.get("registryResource")
        if not rr:
            svc = registry.get_service(r, parent, svc["name"].split("/")[-1]) or {}
            rr = svc.get("registryResource")
        if not rr:
            c.log(f"  [yellow]{url}: registryResource not ready yet; re-run bootstrap[/]")
            continue
        ids[url] = rr
        if iap.add_binding(r, iap.from_registry_resource(rr), member, iap.EGRESSOR, None,
                           replace_member_bindings=False):
            c.log(f"  egressor({member.split('/')[-1]}) on {url}")
    c.log(f"  {len(ids)} platform endpoints registered + allowed for Agent Runtime agents in this project")
    return ids


def run(cfg: DemoConfig, me: str | None = None, skip_gateways_wait: bool = False) -> dict:
    r = c.client(cfg)
    c.log("[bold]1/8 APIs[/]")
    step_apis(cfg, r)
    c.log("[bold]2/8 Service accounts[/]")
    sas = step_service_accounts(cfg, r, me)
    _save_shared(**sas, denied_caller_member=c.denied_member(cfg), allowed_caller_members=c.allowed_members(cfg))
    c.log("[bold]3/8 Artifact Registry + staging bucket[/]")
    repo = step_artifact_registry(cfg, r)
    bucket = step_staging_bucket(cfg, r)
    _save_shared(artifact_repo=repo, staging_bucket=bucket)
    c.log(f"  {repo}  {bucket}")
    c.log("[bold]4/8 Agent Gateways[/]")
    gws = step_gateways(cfg, r)
    _save_shared(egress_gateway=cfg.egress_gateway_resource, ingress_gateway=cfg.ingress_gateway_resource,
                 ingress_gateway_url=f"https://{cfg.region}-aiplatform.googleapis.com",
                 gateway_service_agents=sorted({(g.get('agentGatewayCard') or {}).get(
                     'serviceExtensionsServiceAccount', '') for g in gws.values()} - {""}))
    c.log("[bold]5/8 IAP authz extensions + policies[/]")
    _save_shared(**step_iap_authz(cfg, r))
    c.log("[bold]6/8 Model Armor template + extension[/]")
    _save_shared(**step_model_armor(cfg, r, gws))
    c.log("[bold]7/8 Platform endpoints (default-deny allowlist)[/]")
    ids = step_platform_endpoints(cfg, r)
    _save_shared(platform_registry_ids=ids)
    c.log("[bold]8/8 Done[/]")
    return load_state()["shared"]
