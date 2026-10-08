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


def run(cfg: DemoConfig, theme_id: str) -> None:
    if not cfg.gemini_enterprise.app_id:
        raise SystemExit("gemini_enterprise.app_id is not set in config/demo.yaml")
    if not shutil.which("agents-cli"):
        raise SystemExit("agents-cli not found on PATH (needed for Gemini Enterprise registration)")
    theme = load_theme(theme_id)
    engine = load_state().get("themes", {}).get(theme_id, {}).get("orchestrator", {}).get("engine")
    if not engine:
        raise SystemExit(f"theme {theme_id} is not deployed; run ./agdemo deploy-theme {theme_id} first")
    o = theme.orchestrator
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
