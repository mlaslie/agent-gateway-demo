"""Demo environment configuration (config/demo.yaml) — shared by the CLI, infra steps and UI backend."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "demo.yaml"

Mode = Literal["live", "demo", "live_with_fallback"]


class Environment(BaseModel):
    project_id: str
    gateway_project_id: str = ""
    region: str = "us-east4"
    resource_prefix: str = Field("agdemo", pattern=r"^[a-z][a-z0-9-]{0,9}$")
    labels: dict[str, str] = Field(default_factory=lambda: {"app": "agent-gateway-demo"})


class Gateways(BaseModel):
    create: bool = True
    egress_name: str = ""
    ingress_name: str = ""
    iap_enforcement: Literal["ENFORCE", "DRY_RUN"] = "ENFORCE"


class Models(BaseModel):
    default: str = "gemini-2.5-flash"


class ModelArmor(BaseModel):
    template_id: str = ""
    filters: list[str] = Field(default_factory=lambda: ["prompt_injection_jailbreak", "sensitive_data"])


class CloudRun(BaseModel):
    public_targets: bool = True


class UI(BaseModel):
    deploy: Literal["cloud_run", "local"] = "cloud_run"
    iap_access: list[str] = Field(default_factory=list)
    admin_access: list[str] = Field(default_factory=list)


class IngressDemo(BaseModel):
    allowed_principal: str = ""
    denied_principal: str = "auto"


class GeminiEnterprise(BaseModel):
    """Optional: a Gemini Enterprise app the theme orchestrators are published to (GE Demo mode)."""
    app_id: str = ""                 # Discovery Engine engine id, e.g. my-ge-app_1234567890123
    location: str = "us"             # global | us | eu
    app_url: str = ""                # URL presenters open, e.g. https://vertexaisearch.cloud.google.com/us/home/cid/...


class ThemesCfg(BaseModel):
    enabled: list[str] = Field(default_factory=lambda: ["helpdesk"])
    default: str = "helpdesk"


class DemoConfig(BaseModel):
    environment: Environment
    gateways: Gateways = Gateways()
    models: Models = Models()
    model_armor: ModelArmor = ModelArmor()
    cloud_run: CloudRun = CloudRun()
    ui: UI = UI()
    ingress_demo: IngressDemo = IngressDemo()
    themes: ThemesCfg = ThemesCfg()
    gemini_enterprise: GeminiEnterprise = GeminiEnterprise()
    default_mode: Mode = "live_with_fallback"

    # ---- derived names (single place for resource naming) ----
    @property
    def project(self) -> str:
        return self.environment.project_id

    @property
    def gateway_project(self) -> str:
        return self.environment.gateway_project_id or self.environment.project_id

    @property
    def region(self) -> str:
        return self.environment.region

    @property
    def prefix(self) -> str:
        return self.environment.resource_prefix

    @property
    def egress_gateway(self) -> str:
        return self.gateways.egress_name or f"{self.prefix}-egress"

    @property
    def ingress_gateway(self) -> str:
        return self.gateways.ingress_name or f"{self.prefix}-ingress"

    @property
    def egress_gateway_resource(self) -> str:
        return f"projects/{self.gateway_project}/locations/{self.region}/agentGateways/{self.egress_gateway}"

    @property
    def ingress_gateway_resource(self) -> str:
        return f"projects/{self.project}/locations/{self.region}/agentGateways/{self.ingress_gateway}"

    @property
    def model_armor_template(self) -> str:
        return self.model_armor.template_id or f"{self.prefix}-shield"

    @property
    def registry_parent(self) -> str:
        return f"projects/{self.gateway_project}/locations/{self.region}"

    def name(self, *parts: str) -> str:
        """Resource name: <prefix>-<part>-<part>... (lowercase, Cloud Run/registry safe, <= 49 chars)."""
        n = "-".join([self.prefix, *parts]).lower().replace("_", "-")
        return n[:49].rstrip("-")

    @property
    def labels(self) -> dict[str, str]:
        return {**self.environment.labels, "demo-prefix": self.prefix}


def config_path() -> Path:
    return Path(os.environ.get("AGDEMO_CONFIG", DEFAULT_CONFIG_PATH))


@lru_cache(maxsize=1)
def load_config(path: str | None = None) -> DemoConfig:
    p = Path(path) if path else config_path()
    if not p.exists():
        raise FileNotFoundError(
            f"{p} not found. Copy config/demo.example.yaml to config/demo.yaml and edit it."
        )
    return DemoConfig.model_validate(yaml.safe_load(p.read_text()))
