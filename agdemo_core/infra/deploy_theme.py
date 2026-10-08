"""./agdemo deploy-theme <t> — images (Cloud Build), Cloud Run targets, Agent Registry entries, and the
orchestrator on Agent Runtime with Agent Identity and NO gateway (wide-open start). Idempotent."""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..config import CONFIG_DIR, REPO_ROOT, DemoConfig
from ..gcp import gcloud, project, registry
from ..gcp.engines import get_engine, principal
from ..gcp.rest import GcpError, Rest, aiplatform, project_number
from ..state import load_state, save_state, update_state
from ..themes import Theme, load_theme
from . import common as c

RUNTIMES = REPO_ROOT / "runtimes"
IMAGES = {"mcp_server": "mcp-server", "a2a_agent": "a2a-agent"}

# Project roles for the orchestrator's Agent Identity (agent-gateway troubleshooting "basic roles").
AGENT_ROLES = ["roles/aiplatform.user", "roles/agentregistry.viewer", "roles/logging.logWriter",
               "roles/monitoring.metricWriter", "roles/cloudtrace.agent", "roles/browser",
               "roles/serviceusage.serviceUsageConsumer"]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def make_specs():
    return _load_module("agdemo_make_specs", RUNTIMES / "tools" / "make_specs.py")


# ---------------------------------------------------------------- images
def image_uri(cfg: DemoConfig, kind: str) -> str:
    return f"{cfg.region}-docker.pkg.dev/{cfg.project}/{c.repo_id(cfg)}/{IMAGES[kind]}:latest"


def build_images(cfg: DemoConfig, kinds: set[str]) -> None:
    def one(kind: str) -> None:
        src = RUNTIMES / kind
        c.log(f"  building {IMAGES[kind]} with Cloud Build ({src.relative_to(REPO_ROOT)})")
        gcloud.run(["builds", "submit", str(src), f"--tag={image_uri(cfg, kind)}", f"--project={cfg.project}",
                    f"--region={cfg.region}", "--quiet"], timeout=1800)
        c.log(f"  [green]built[/] {image_uri(cfg, kind)}")
    with ThreadPoolExecutor(len(kinds)) as ex:
        list(ex.map(one, sorted(kinds)))


# ---------------------------------------------------------------- Cloud Run
def run_url(cfg: DemoConfig, service: str) -> str:
    """Deterministic Cloud Run URL: https://<service>-<project number>.<region>.run.app"""
    return f"https://{service}-{project_number(cfg.project)}.{cfg.region}.run.app"


def deploy_service(cfg: DemoConfig, theme: Theme, cid: str, ms) -> str:
    comp = theme.component(cid)
    svc = c.service_name(cfg, theme.id, cid)
    url = run_url(cfg, svc)
    env = {"COMPONENT_SPEC": ms.encode_spec(ms.component_spec(theme, cid))}
    if comp.kind == "a2a_agent":
        env.update(MODEL=theme.a2a_agents[cid].model or cfg.models.default, PUBLIC_URL=url,
                   GOOGLE_CLOUD_PROJECT=cfg.project, GOOGLE_CLOUD_LOCATION=cfg.region,
                   GOOGLE_GENAI_USE_VERTEXAI="TRUE")
    env_file = CONFIG_DIR / "generated" / f"{svc}.env.yaml"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text("\n".join(f"{k}: {json.dumps(v)}" for k, v in env.items()) + "\n")
    labels = ",".join(f"{k}={v}" for k, v in {**cfg.labels, "demo-theme": theme.id, "demo-component": cid}.items())
    args = ["run", "deploy", svc, f"--image={image_uri(cfg, comp.kind)}", f"--region={cfg.region}",
            f"--project={cfg.project}", f"--env-vars-file={env_file}", f"--labels={labels}",
            f"--service-account={c.sa(cfg, c.run_sa_id(cfg))}", "--min-instances=0", "--max-instances=3",
            "--memory=1Gi", "--cpu=1", "--port=8080", "--quiet"]
    args.append("--allow-unauthenticated" if cfg.cloud_run.public_targets else "--no-allow-unauthenticated")
    c.log(f"  deploying Cloud Run {svc}")
    try:
        gcloud.run(args, timeout=900)
    except gcloud.GcloudError as e:
        if cfg.cloud_run.public_targets and "allUsers" in str(e):
            raise RuntimeError(f"{svc}: org policy blocks allUsers invoker; set cloud_run.public_targets=false") from e
        raise
    c.log(f"  [green]deployed[/] {svc} -> {url}")
    return url


# ---------------------------------------------------------------- registry
def mcp_toolspec(theme: Theme, cid: str) -> list[dict[str, Any]]:
    """tools/list-shaped spec. Annotations drive iap.googleapis.com/mcp.tool.isReadOnly."""
    out = []
    for t in theme.mcp_servers[cid].tools:
        props = {k: {"type": p.type, "description": p.description} for k, p in t.params.items()}
        req = [k for k, p in t.params.items() if p.required]
        out.append({"name": t.name, "description": t.description,
                    "inputSchema": {"type": "object", "properties": props, **({"required": req} if req else {})},
                    "annotations": {"readOnlyHint": t.read_only, "destructiveHint": not t.read_only,
                                    "idempotentHint": t.read_only, "openWorldHint": False}})
    return out


def fetch_card(url: str) -> dict | None:
    import httpx
    for path in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
        try:
            resp = httpx.get(url + path, timeout=30)
            if resp.status_code == 200:
                return resp.json()
        except Exception:  # noqa: BLE001
            pass
    return None


def register_component(cfg: DemoConfig, r: Rest, theme: Theme, cid: str, url: str) -> dict[str, Any]:
    comp = theme.component(cid)
    sid = c.service_name(cfg, theme.id, cid)
    parent = cfg.registry_parent
    info: dict[str, Any] = {}
    if comp.kind == "mcp_server":
        spec = theme.mcp_servers[cid]
        svc = registry.ensure_service(r, parent, sid, registry.mcp_body(
            f"{cfg.prefix} {theme.id} {spec.display_name}", f"{url}/mcp", mcp_toolspec(theme, cid), spec.description))
        info["registry_mode"] = "mcp_tool_spec"
    else:
        spec = theme.a2a_agents[cid]
        card = None
        for _ in range(10):
            card = fetch_card(url)
            if card:
                break
            time.sleep(6)
        svc = None
        if card:
            try:
                svc = registry.ensure_service(r, parent, sid, registry.a2a_body(
                    f"{cfg.prefix} {theme.id} {spec.display_name}", card, spec.description))
                info["registry_mode"] = "a2a_agent_card"
            except GcpError as e:
                c.log(f"  [yellow]{sid}: agent card rejected ({e.message[:160]}); registering with no-spec + URL[/]")
        if svc is None:
            body = {"displayName": f"{cfg.prefix} {theme.id} {spec.display_name}"[:63],
                    "description": spec.description, "agentSpec": {"type": "NO_SPEC"},
                    "interfaces": [{"url": url, "protocolBinding": "JSONRPC"}]}
            if registry.get_service(r, parent, sid):
                registry.delete_service(r, parent, sid)
            svc = registry.ensure_service(r, parent, sid, body)
            info["registry_mode"] = "agent_no_spec"
    info["registry"] = svc.get("registryResource", "")
    info["registry_service"] = svc.get("name", "")
    c.log(f"  registered {sid} -> {info['registry'] or '(projection pending)'} [{info['registry_mode']}]")
    return info


# ---------------------------------------------------------------- Agent Runtime
def engine_display_name(cfg: DemoConfig, theme: Theme) -> str:
    return cfg.name(theme.id, "orchestrator")


def find_engine(r: Rest, cfg: DemoConfig, display: str) -> dict | None:
    base = f"{aiplatform(cfg.region)}/projects/{cfg.project}/locations/{cfg.region}/reasoningEngines"
    for e in r.list_all(base, "reasoningEngines", params={"filter": f'display_name="{display}"'}):
        if e.get("displayName") == display:
            return e
    return None


def create_engine(cfg: DemoConfig, theme: Theme, urls: dict[str, str], ms) -> str:
    import vertexai
    from vertexai.agent_engines import AdkApp

    ds = _load_module("agdemo_deploy_spec", RUNTIMES / "orchestrator" / "deploy_spec.py")
    spec = ms.orchestrator_spec(theme, urls, cfg.models.default)
    auth_mode = "none" if cfg.cloud_run.public_targets else "id_token"
    env = ds.env_vars(ms.encode_spec(spec), spec["model"], auth_mode)
    vertexai.init(project=cfg.project, location=cfg.region, staging_bucket=f"gs://{c.staging_bucket(cfg)}")
    client = vertexai.Client(project=cfg.project, location=cfg.region)
    with ds.staged(env) as (root_agent, conf):
        conf.update(display_name=engine_display_name(cfg, theme),
                    description=f"{theme.orchestrator.display_name} ({cfg.prefix} demo, theme {theme.id})",
                    staging_bucket=f"gs://{c.staging_bucket(cfg)}", labels={**cfg.labels, "demo-theme": theme.id})
        c.log(f"  creating Agent Runtime engine {conf['display_name']} (identity_type={conf.get('identity_type')}, "
              "no gateway) — ~5-10 min")
        remote = client.agent_engines.create(agent=AdkApp(agent=root_agent), config=conf)
    name = remote.api_resource.name
    c.log(f"  [green]engine created[/] {name}")
    return name


def update_engine_code(cfg: DemoConfig, theme: Theme, urls: dict[str, str], ms, engine: str) -> None:
    """Redeploy the orchestrator package + spec into the existing engine. The engine keeps its name,
    Agent Identity principal, gateway binding and IAM, so applied policies stay valid."""
    import vertexai
    from vertexai.agent_engines import AdkApp

    ds = _load_module("agdemo_deploy_spec", RUNTIMES / "orchestrator" / "deploy_spec.py")
    spec = ms.orchestrator_spec(theme, urls, cfg.models.default)
    auth_mode = "none" if cfg.cloud_run.public_targets else "id_token"
    env = ds.env_vars(ms.encode_spec(spec), spec["model"], auth_mode)
    vertexai.init(project=cfg.project, location=cfg.region, staging_bucket=f"gs://{c.staging_bucket(cfg)}")
    client = vertexai.Client(project=cfg.project, location=cfg.region)
    with ds.staged(env) as (root_agent, conf):
        conf.pop("identity_type", None)          # fixed at create time; not updatable
        conf.update(staging_bucket=f"gs://{c.staging_bucket(cfg)}")
        c.log(f"  updating {engine} in place — ~5 min")
        # Marker so policy status doesn't mistake this code update for a gateway PATCH.
        update_state("code_updates", engine, value=time.time())
        try:
            client.agent_engines.update(name=engine, agent=AdkApp(agent=root_agent), config=conf)
        finally:
            update_state("code_updates", engine, value=None)
    c.log("  [green]engine updated[/]")


def grant_agent_roles(cfg: DemoConfig, r: Rest, member: str, theme: Theme) -> None:
    for role in AGENT_ROLES:
        try:
            project.project_add_bindings(r, cfg.project, [(member, role)])
        except GcpError as e:
            c.log(f"  [yellow]could not grant {role} to agent: {e.message[:120]}[/]")
    if not cfg.cloud_run.public_targets:
        for comp in theme.components:
            svc = c.service_name(cfg, theme.id, comp.id)
            gcloud.run(["run", "services", "add-iam-policy-binding", svc, f"--region={cfg.region}",
                        f"--project={cfg.project}", f"--member={member}", "--role=roles/run.invoker", "--quiet"])


# ---------------------------------------------------------------- main
def run(cfg: DemoConfig, theme_id: str, skip_build: bool = False, only: str | None = None,
        recreate_engine: bool = False, update_engine: bool = False) -> dict:
    theme = load_theme(theme_id)
    r = c.client(cfg)
    ms = make_specs()
    st = load_state()
    if not st.get("shared", {}).get("egress_gateway"):
        c.log("[yellow]shared resources missing — run ./agdemo bootstrap first[/]")
    tstate: dict[str, Any] = st.setdefault("themes", {}).setdefault(theme.id, {})
    comps: dict[str, Any] = tstate.setdefault("components", {})
    steps = {only} if only else {"images", "run", "registry", "engine"}

    if "images" in steps and not skip_build:
        c.log("[bold]Images[/]")
        build_images(cfg, {comp.kind for comp in theme.components})

    urls: dict[str, str] = {cid: v.get("url") for cid, v in comps.items() if v.get("url")}
    if "run" in steps:
        c.log("[bold]Cloud Run targets[/]")
        with ThreadPoolExecutor(4) as ex:
            res = dict(zip([x.id for x in theme.components],
                           ex.map(lambda comp: deploy_service(cfg, theme, comp.id, ms), theme.components)))
        for cid, url in res.items():
            comps.setdefault(cid, {}).update(url=url, service=c.service_name(cfg, theme.id, cid),
                                             kind=theme.component(cid).kind)
        urls.update(res)
        update_state("themes", theme.id, "components", value=comps)
    for comp in theme.components:
        urls.setdefault(comp.id, run_url(cfg, c.service_name(cfg, theme.id, comp.id)))

    if "registry" in steps:
        c.log("[bold]Agent Registry[/]")
        for comp in theme.components:
            comps.setdefault(comp.id, {}).update(register_component(cfg, r, theme, comp.id, urls[comp.id]))
        update_state("themes", theme.id, "components", value=comps)

    if "engine" in steps:
        c.log("[bold]Agent Runtime orchestrator[/]")
        orch = tstate.get("orchestrator", {})
        existing = get_engine(r, cfg, orch["engine"]) if orch.get("engine") else None
        if existing is None:
            existing = find_engine(r, cfg, engine_display_name(cfg, theme))
        if existing is not None and recreate_engine:
            c.log(f"  deleting {existing['name']}")
            op = r.delete(f"{aiplatform(cfg.region)}/{existing['name']}", params={"force": "true"})
            r.wait(op, aiplatform(cfg.region), timeout=900)
            existing = None
        if existing is not None and update_engine:
            update_engine_code(cfg, theme, urls, ms, existing["name"])
        engine = existing["name"] if existing else create_engine(cfg, theme, urls, ms)
        eng = get_engine(r, cfg, engine)
        member = principal(cfg, eng, engine)
        orch = {"engine": engine, "principal": member, "display_name": engine_display_name(cfg, theme),
                "identity_type": (eng or {}).get("spec", {}).get("identityType")}
        grant_agent_roles(cfg, r, member, theme)
        a = None
        for _ in range(20):
            a = registry.find_runtime_agent(r, f"projects/{cfg.project}/locations/{cfg.region}", engine)
            if a:
                break
            time.sleep(6)
        if a:
            orch["registry"] = a["name"]
        else:
            c.log("  [yellow]engine not yet auto-registered in Agent Registry (re-run later for ingress policy)[/]")
        update_state("themes", theme.id, "orchestrator", value=orch)
        c.log(f"  orchestrator {engine}\n  principal {member}")
    st = load_state()
    c.console.print_json(data=st["themes"][theme.id])
    return st["themes"][theme.id]
