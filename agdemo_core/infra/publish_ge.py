"""`./agdemo publish-ge <theme>`: register a theme's orchestrator in a Gemini Enterprise app (GE Demo mode).

Gemini Enterprise invokes the Agent Runtime engine natively (ADK registration, `:streamQuery`), so the
agent's outbound calls take the same egress path as in the UI: through Agent Gateway once it is attached.
Registration is done with `agents-cli publish gemini-enterprise` and is idempotent.
"""
from __future__ import annotations

import shutil
import subprocess

from ..config import DemoConfig
from ..gcp.rest import project_number
from ..state import load_state
from ..themes import load_theme
from . import common as c


def app_resource(cfg: DemoConfig) -> str:
    ge = cfg.gemini_enterprise
    return (f"projects/{project_number(cfg.project)}/locations/{ge.location}"
            f"/collections/default_collection/engines/{ge.app_id}")


# ------------------------------------------------------------------ registrations (read / unregister, T3 down)
# Same Discovery Engine API agents-cli uses (publish/cmd_publish.py: _upsert_agent / _match_adk_agent):
#   {base}/v1alpha/<app>/assistants/default_assistant/agents   list (paged), DELETE {base}/v1alpha/<agent name>
# base = https://discoveryengine.googleapis.com (global) or https://<location>-discoveryengine.googleapis.com.
def ge_base(location: str) -> str:
    return "https://discoveryengine.googleapis.com" if location == "global" else \
        f"https://{location}-discoveryengine.googleapis.com"


def agents_url(cfg: DemoConfig) -> str:
    return f"{ge_base(cfg.gemini_enterprise.location)}/v1alpha/{app_resource(cfg)}/assistants/default_assistant/agents"


def list_agents(cfg: DemoConfig, r) -> list[dict]:
    """Every agent registered in the GE app (read-only)."""
    return r.list_all(agents_url(cfg), "agents")


def unregister(cfg: DemoConfig, r, agents: list[dict], log=c.log) -> list[str]:
    """Delete these GE registrations (from check_clean.ge_matches). 404 = already gone. Returns the names."""
    from ..gcp.rest import GcpError
    done = []
    for a in agents:
        try:
            r.delete(f"{ge_base(cfg.gemini_enterprise.location)}/v1alpha/{a['name']}")
            log(f"  unregistered {a.get('displayName', a['name'])} from Gemini Enterprise")
        except GcpError as e:
            if not e.not_found:
                raise
        done.append(a["name"])
    return done


def run(cfg: DemoConfig, theme_id: str, agent: str | None = None) -> None:
    """Publish the theme's primary orchestrator, or the additional orchestrator `agent` (CONTRACTS §12)."""
    if not cfg.gemini_enterprise.app_id:
        raise SystemExit("gemini_enterprise.app_id is not set in config/demo.yaml")
    if not shutil.which("agents-cli"):
        raise SystemExit("agents-cli not found on PATH (needed for Gemini Enterprise registration)")
    theme = load_theme(theme_id)
    if agent and agent not in {x.id for x in theme.additional_orchestrators}:
        if agent == theme.orchestrator.id:
            agent = None
        else:
            raise SystemExit(f"theme {theme_id} has no agent {agent!r} (additional orchestrators: "
                             f"{', '.join(x.id for x in theme.additional_orchestrators) or 'none'})")
    ts = load_state().get("themes", {}).get(theme_id, {})
    rec = ts.get("orchestrator", {}) if not agent else (ts.get("orchestrators") or {}).get(agent, {})
    engine = rec.get("engine")
    if not engine:
        raise SystemExit(f"theme {theme_id} is not deployed; run ./agdemo deploy-theme {theme_id} first")
    o = theme.orchestrator_for(agent)
    cmd = ["agents-cli", "publish", "gemini-enterprise",
           "--registration-type", "adk",
           "--agent-runtime-id", engine,
           "--gemini-enterprise-app-id", app_resource(cfg),
           "--project-id", cfg.project,
           "--display-name", f"{o.display_name} (Agent Gateway demo)",
           "--description", o.description or theme.description.strip(),
           "--tool-description", o.description or o.display_name]
    c.log(f"[bold]Publishing {o.display_name} to Gemini Enterprise[/] ({cfg.gemini_enterprise.app_id})")
    subprocess.run(cmd, check=True)
    if cfg.gemini_enterprise.app_url:
        c.log(f"  open: {cfg.gemini_enterprise.app_url}")
