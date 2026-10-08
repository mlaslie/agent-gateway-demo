"""Shared naming + small utilities for infra steps. Every name derives from config (prefix/project/region)."""
from __future__ import annotations

import re
from typing import Callable

from rich.console import Console

from ..config import DemoConfig
from ..gcp.rest import Rest, rest

console = Console()

Log = Callable[[str], None]


def log(msg: str) -> None:
    console.print(msg)


def client(cfg: DemoConfig) -> Rest:
    return rest(cfg.project)


# ---------------------------------------------------------------- names
def ui_sa_id(cfg: DemoConfig) -> str:
    return cfg.name("ui")


def run_sa_id(cfg: DemoConfig) -> str:
    """Runtime identity of the demo Cloud Run targets (MCP servers / A2A agents)."""
    return cfg.name("targets")


def sa(cfg: DemoConfig, account_id: str) -> str:
    return f"{account_id}@{cfg.project}.iam.gserviceaccount.com"




def repo_id(cfg: DemoConfig) -> str:
    return cfg.prefix


def staging_bucket(cfg: DemoConfig) -> str:
    return f"{cfg.project}-{cfg.prefix}-staging"[:63]


def service_name(cfg: DemoConfig, theme_id: str, component_id: str) -> str:
    """Cloud Run service + registry service id: <prefix>-<theme>-<component>."""
    return cfg.name(theme_id, component_id)


def ui_service_name(cfg: DemoConfig) -> str:
    return cfg.name("ui")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-")


# ---------------------------------------------------------------- platform endpoints (default-deny allowlist)
def platform_endpoints(cfg: DemoConfig) -> list[tuple[str, str]]:
    """(slug, url) of the Google APIs an ADK agent on Agent Runtime calls. Matching is by exact host,
    so the regional, global, .mtls. and gRPC host:443 variants are registered separately."""
    r = cfg.region
    hosts = [
        f"{r}-aiplatform.googleapis.com", f"{r}-aiplatform.mtls.googleapis.com",
        f"aiplatform.{r}.rep.googleapis.com",
        "aiplatform.googleapis.com", "aiplatform.mtls.googleapis.com",
        "agentregistry.googleapis.com", "agentregistry.mtls.googleapis.com",
        "logging.googleapis.com", "logging.mtls.googleapis.com",
        "telemetry.googleapis.com", "telemetry.mtls.googleapis.com",
        "cloudtrace.googleapis.com", "cloudtrace.mtls.googleapis.com",
        "monitoring.googleapis.com", "monitoring.mtls.googleapis.com",
        "cloudresourcemanager.googleapis.com", "cloudresourcemanager.mtls.googleapis.com",
        "iamcredentials.googleapis.com", "iamcredentials.mtls.googleapis.com",
        "secretmanager.googleapis.com", "secretmanager.mtls.googleapis.com",
    ]
    out = [(slug(h.replace(".googleapis.com", "")), f"https://{h}") for h in hosts]
    # gRPC clients present host:443 (exact match) — ADK telemetry setup uses CRM/logging/trace over gRPC
    for h in ("cloudresourcemanager.mtls.googleapis.com", "logging.mtls.googleapis.com",
              "cloudtrace.mtls.googleapis.com", "telemetry.mtls.googleapis.com"):
        out.append((slug(h.replace(".googleapis.com", "")) + "-443", f"https://{h}:443"))
    return out


def platform_service_id(cfg: DemoConfig, s: str) -> str:
    return cfg.name("plat", s)[:63]
