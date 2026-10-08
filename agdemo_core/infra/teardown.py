"""./agdemo teardown <theme> | --all — deletes only resources carrying the configured prefix."""
from __future__ import annotations

from ..config import DemoConfig
from ..gcp import gateways as gw
from ..gcp import gcloud, iap, project, registry
from ..gcp import model_armor as ma
from ..gcp.engines import project_principal_set
from ..gcp.rest import GcpError, aiplatform
from ..state import load_state, save_state
from ..themes import load_theme
from . import common as c


def teardown_theme(cfg: DemoConfig, theme_id: str) -> None:
    r = c.client(cfg)
    st = load_state()
    ts = st.get("themes", {}).get(theme_id, {})
    o = ts.get("orchestrator", {})
    if o.get("engine"):
        c.log(f"  deleting engine {o['engine']}")
        try:
            op = r.delete(f"{aiplatform(cfg.region)}/{o['engine']}", params={"force": "true"})
            r.wait(op, aiplatform(cfg.region), timeout=1200)
        except GcpError as e:
            if not e.not_found:
                raise
        if o.get("principal"):
            project.project_remove_member(r, cfg.project, o["principal"])
    try:
        theme = load_theme(theme_id)
        cids = [x.id for x in theme.components]
    except Exception:  # noqa: BLE001
        cids = list(ts.get("components", {}))
    for cid in cids:
        name = c.service_name(cfg, theme_id, cid)
        assert name.startswith(cfg.prefix + "-")
        if registry.delete_service(r, cfg.registry_parent, name):
            c.log(f"  deleted registry service {name}")
        if gcloud.run(["run", "services", "delete", name, f"--region={cfg.region}", f"--project={cfg.project}",
                       "--quiet"], check=False, capture=True) is not None:
            c.log(f"  deleted Cloud Run {name} (if it existed)")
    st = load_state()
    st.get("themes", {}).pop(theme_id, None)
    save_state(st)


def teardown_shared(cfg: DemoConfig) -> None:
    r = c.client(cfg)
    st = load_state()
    # UI
    gcloud.run(["run", "services", "delete", c.ui_service_name(cfg), f"--region={cfg.region}",
                f"--project={cfg.project}", "--quiet"], check=False)
    # authz policies, then extensions, then gateways
    for p in ("egress", "ingress"):
        proj = gw.gateway_project(cfg, p)
        for name in (gw.ma_policy_name(cfg, p), gw.iap_policy_name(cfg, p)):
            if gw.delete_policy(r, proj, cfg.region, name):
                c.log(f"  deleted authz policy {name}")
    for p in ("egress", "ingress"):
        proj = gw.gateway_project(cfg, p)
        if gw.delete_extension(r, proj, cfg.region, gw.iap_extension_name(cfg, p)):
            c.log(f"  deleted extension {gw.iap_extension_name(cfg, p)}")
    for proj in sorted({cfg.project, cfg.gateway_project}):
        if gw.delete_extension(r, proj, cfg.region, gw.ma_extension_name(cfg)):
            c.log(f"  deleted extension {gw.ma_extension_name(cfg)}")
    if cfg.gateways.create:
        for p in ("egress", "ingress"):
            if gw.delete_gateway(cfg, p, r):
                c.log(f"  deleted {p} gateway")
    if ma.delete_template(r, cfg):
        c.log(f"  deleted Model Armor template {cfg.model_armor_template}")
    # platform endpoints we registered (prefix-owned only)
    member = project_principal_set(cfg)
    for s in registry.list_services(r, cfg.registry_parent):
        sid = s["name"].split("/")[-1]
        if sid.startswith(cfg.name("plat") + "-"):
            if s.get("registryResource"):
                try:
                    iap.remove_member(r, iap.from_registry_resource(s["registryResource"]), member)
                except GcpError:
                    pass
            registry.delete_service(r, cfg.registry_parent, sid)
            c.log(f"  deleted registry service {sid}")
    # service accounts
    for acc in (c.denied_sa_id(cfg), c.allowed_sa_id(cfg), c.ui_sa_id(cfg), c.run_sa_id(cfg)):
        email = c.sa(cfg, acc)
        project.project_remove_member(r, cfg.project, f"serviceAccount:{email}")
        if project.delete_sa(r, cfg.project, email):
            c.log(f"  deleted service account {email}")
    c.log("  kept: APIs, Artifact Registry repo and staging bucket contents are deleted below")
    gcloud.run(["artifacts", "repositories", "delete", c.repo_id(cfg), f"--location={cfg.region}",
                f"--project={cfg.project}", "--quiet"], check=False)
    gcloud.run(["storage", "rm", "-r", f"gs://{c.staging_bucket(cfg)}", f"--project={cfg.project}"], check=False)
    st["shared"] = {}
    save_state(st)


def run(cfg: DemoConfig, theme_id: str | None, all_: bool) -> None:
    if all_:
        for tid in list(load_state().get("themes", {})):
            c.log(f"[bold]theme {tid}[/]")
            teardown_theme(cfg, tid)
        c.log("[bold]shared[/]")
        teardown_shared(cfg)
    else:
        teardown_theme(cfg, theme_id)
    c.log("[green]teardown complete[/]")
