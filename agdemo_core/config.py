"""Demo environment configuration (config/demo.yaml) — shared by the CLI, infra steps and UI backend."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, PrivateAttr, model_validator

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


class GeminiEnterprise(BaseModel):
    """Optional: a Gemini Enterprise app the theme orchestrators are published to (GE Demo mode)."""
    app_id: str = ""                 # Discovery Engine engine id, e.g. my-ge-app_1234567890123
    location: str = "us"             # global | us | eu
    app_url: str = ""                # URL presenters open, e.g. https://vertexaisearch.cloud.google.com/us/home/cid/...


class ThemesCfg(BaseModel):
    enabled: list[str] = Field(default_factory=lambda: ["helpdesk"])
    default: str = "helpdesk"


class TerraformCfg(BaseModel):
    """Terraform foundation (preview, docs/TERRAFORM_PLAN.md)."""
    # Project-wide grants other environments in the same project also rely on: presenters' aiplatform.user
    # and the Model Armor roles of the project service agents. false for a second environment in a project
    # that already has one, so its `terraform destroy` can't strip the first one's grants.
    shared_project_grants: bool = True


class PrivateNetworking(BaseModel):
    """Private Networking mode (docs/TERRAFORM_PLAN.md §7c, phase T4): a VPC-enabled egress gateway in a second
    region, created by Terraform (`up`) with the connectivity template from the start. Themes declare
    `network_mode: private_networking` and `region: <this region>`."""
    enabled: bool = False
    region: str = "us-west1"                    # must differ from environment.region (one egress gateway per region)
    subnet_range: str = "10.70.0.0/28"          # attachment subnet (/28, Private Google Access)
    psc_ip: str = "10.70.250.10"                # PSC endpoint for Google APIs; run.app resolves to it in the VPC
    dns_domains: list[str] = Field(default_factory=lambda: ["run.app."])
    vpc_egress: Literal["PRIVATE_RANGES_ONLY"] = "PRIVATE_RANGES_ONLY"
    egress_name: str = ""                       # default <prefix>-pn-egress
    # gcp-sa-agentgateway roles/compute.networkAdmin + roles/dns.peer (project-wide). None = follow
    # terraform.shared_project_grants (false for a second environment that relies on the first one's grants).
    manage_service_agent_roles: bool | None = None
    themes: list[str] = Field(default_factory=lambda: ["privnet"])   # shown in the UI's Private Networking mode


class DemoConfig(BaseModel):
    environment: Environment
    gateways: Gateways = Gateways()
    models: Models = Models()
    model_armor: ModelArmor = ModelArmor()
    cloud_run: CloudRun = CloudRun()
    ui: UI = UI()
    themes: ThemesCfg = ThemesCfg()
    gemini_enterprise: GeminiEnterprise = GeminiEnterprise()
    terraform: TerraformCfg = TerraformCfg()
    private_networking: PrivateNetworking = PrivateNetworking()
    default_mode: Mode = "live_with_fallback"

    # Regional view (T4): None = the base (Internet mode) region. for_region() returns a copy whose `region`,
    # gateway and registry names are those of another region; `home_region` stays the base region (images,
    # the UI, the Terraform state and the recordings bucket live there).
    _view: str | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _pn_region(self) -> "DemoConfig":
        if self.private_networking.enabled and self.private_networking.region == self.environment.region:
            raise ValueError("private_networking.region must differ from environment.region (a project can have "
                             "only one egress gateway per region serving agents)")
        return self

    # ---- derived names (single place for resource naming) ----
    @property
    def project(self) -> str:
        return self.environment.project_id

    @property
    def gateway_project(self) -> str:
        return self.environment.gateway_project_id or self.environment.project_id

    @property
    def region(self) -> str:
        return self._view or self.environment.region

    @property
    def home_region(self) -> str:
        """The base region (Internet mode): Artifact Registry, the UI, Terraform state, recordings."""
        return self.environment.region

    @property
    def is_home(self) -> bool:
        return self.region == self.environment.region

    @property
    def is_private_networking(self) -> bool:
        """This view is the Private Networking region (VPC-enabled gateway, no ingress gateway, no Model Armor)."""
        return not self.is_home and self.region == self.private_networking.region

    def for_region(self, region: str) -> "DemoConfig":
        """A view of this config in `region`: the base region, or the enabled Private Networking region."""
        if region == self.environment.region:
            return self.home()
        if not (self.private_networking.enabled and region == self.private_networking.region):
            raise ValueError(f"region {region} is neither environment.region ({self.environment.region}) nor the "
                             "enabled private_networking.region")
        v = self.model_copy()
        v._view = region
        return v

    def home(self) -> "DemoConfig":
        if self._view is None:
            return self
        v = self.model_copy()
        v._view = None
        return v

    def region_views(self) -> list["DemoConfig"]:
        """[base view] + [Private Networking view when enabled]: what up/down/check-clean/reset --all cover."""
        out = [self.home()]
        if self.private_networking.enabled:
            out.append(self.for_region(self.private_networking.region))
        return out

    def enabled_themes(self) -> list[str]:
        """themes.enabled, plus the Private Networking themes when that mode is enabled (deploy order)."""
        out = list(self.themes.enabled)
        if self.private_networking.enabled:
            out += [t for t in self.private_networking.themes if t not in out]
        return out

    @property
    def manage_pn_service_agent_roles(self) -> bool:
        m = self.private_networking.manage_service_agent_roles
        return self.terraform.shared_project_grants if m is None else m

    @property
    def prefix(self) -> str:
        return self.environment.resource_prefix

    @property
    def egress_gateway(self) -> str:
        if self.is_private_networking:
            return self.private_networking.egress_name or f"{self.prefix}-pn-egress"
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
    def has_ingress_gateway(self) -> bool:
        return not self.is_private_networking

    @property
    def has_model_armor(self) -> bool:
        """Model Armor (template, extension, the checkbox) exists only in the base region."""
        return not self.is_private_networking

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
