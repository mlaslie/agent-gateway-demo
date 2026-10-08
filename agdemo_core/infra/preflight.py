"""./agdemo preflight — pass/fail checks with the exact fix for each failure."""
from __future__ import annotations

from dataclasses import dataclass

from ..config import DemoConfig
from ..gcp import gateways as gw
from ..gcp import gcloud, project
from ..gcp.rest import AGENTREGISTRY, CRM, GcpError, Rest, aiplatform
from . import common as c

PERMS = [
    "networkservices.agentGateways.create", "networkservices.authzExtensions.create",
    "networksecurity.authzPolicies.create", "iap.webServiceVersions.setIamPolicy",
    "agentregistry.services.create", "aiplatform.reasoningEngines.create", "aiplatform.reasoningEngines.update",
    "modelarmor.templates.create", "run.services.create", "cloudbuild.builds.create",
    "artifactregistry.repositories.create", "iam.serviceAccounts.create", "resourcemanager.projects.setIamPolicy",
    "serviceusage.services.enable",
]


@dataclass
class Check:
    name: str
    ok: bool | None          # None = warning
    detail: str
    fix: str = ""


def active_account() -> str:
    return gcloud.value(["config", "get-value", "account"])


def run(cfg: DemoConfig, r: Rest, fix: bool = False) -> list[Check]:
    out: list[Check] = []
    P, R = cfg.project, cfg.region

    # auth
    acct = active_account()
    out.append(Check("gcloud auth", bool(acct), acct or "no active account", "gcloud auth login"))
    try:
        r.credentials.refresh(__import__("google.auth.transport.requests", fromlist=["Request"]).Request())
        out.append(Check("Application Default Credentials", True, type(r.credentials).__name__))
    except Exception as e:  # noqa: BLE001
        out.append(Check("Application Default Credentials", False, str(e)[:120],
                         "gcloud auth application-default login"))
        return out

    # project + billing
    try:
        proj = r.get(f"{CRM}/projects/{P}")
        out.append(Check("project", True, f"{P} (#{proj['projectNumber']})"))
    except GcpError as e:
        out.append(Check("project", False, e.message[:120], "check environment.project_id"))
        return out
    try:
        b = r.get(f"https://cloudbilling.googleapis.com/v1/projects/{P}/billingInfo")
        out.append(Check("billing", bool(b.get("billingEnabled")), "enabled" if b.get("billingEnabled") else "disabled",
                         f"gcloud billing projects link {P} --billing-account=ACCOUNT"))
    except GcpError as e:
        v = gcloud.value(["billing", "projects", "describe", P, "--format=value(billingEnabled)"])
        if v:
            out.append(Check("billing", v.lower() == "true", f"billingEnabled={v} (gcloud)",
                             f"gcloud billing projects link {P} --billing-account=ACCOUNT"))
        else:
            out.append(Check("billing", None, f"could not read ({e.status})",
                             "needs billing.resourceAssociations.list"))

    # APIs
    have = project.enabled_services(r, P)
    missing = [s for s in project.REQUIRED_APIS if s not in have]
    if missing and fix:
        project.enable_services(r, P, missing)
        missing = []
    out.append(Check("APIs", not missing, "all enabled" if not missing else ", ".join(missing),
                     f"./agdemo preflight --fix  (or gcloud services enable {' '.join(missing)} --project={P})"))

    # caller permissions
    try:
        granted = project.test_permissions(r, P, PERMS)
        lacking = [p for p in PERMS if p not in granted]
        out.append(Check("caller permissions", not lacking, "ok" if not lacking else "missing: " + ", ".join(lacking),
                         "Project Owner is simplest; see docs/SETUP.md §2"))
    except GcpError as e:
        out.append(Check("caller permissions", None, e.message[:120]))

    # region support
    for label, url in [("Agent Gateway in region", f"https://networkservices.googleapis.com/v1/projects/{P}/locations/{R}/agentGateways"),
                       ("Agent Registry in region", f"{AGENTREGISTRY}/projects/{P}/locations/{R}/services"),
                       ("Agent Runtime in region", f"{aiplatform(R)}/projects/{P}/locations/{R}/reasoningEngines")]:
        try:
            r.get(url, params={"pageSize": 1})
            out.append(Check(label, True, R))
        except GcpError as e:
            out.append(Check(label, False, f"{e.status}: {e.message[:100]}", "pick another environment.region"))

    # gateway conflicts: other gateways in region, and engines bound to other gateways
    try:
        gws = gw.list_gateways(r, P, R)
        ours = {cfg.egress_gateway, cfg.ingress_gateway}
        others = [g["name"].split("/")[-1] for g in gws if g["name"].split("/")[-1] not in ours]
        mine = [g["name"].split("/")[-1] for g in gws if g["name"].split("/")[-1] in ours]
        out.append(Check("gateways in region", None if others else True,
                         (f"ours: {', '.join(mine) or 'none yet'}") + (f"; others: {', '.join(others)}" if others else ""),
                         "others exist: engines in this project+region must all use ONE egress/ingress gateway"))
    except GcpError as e:
        out.append(Check("gateways in region", False, e.message[:120]))
    try:
        engines = r.list_all(f"{aiplatform(R)}/projects/{P}/locations/{R}/reasoningEngines", "reasoningEngines")
        conflicts = []
        for e in engines:
            agc = (e.get("spec", {}).get("deploymentSpec", {}) or {}).get("agentGatewayConfig") or {}
            for k, want in (("agentToAnywhereConfig", cfg.egress_gateway_resource),
                            ("clientToAgentConfig", cfg.ingress_gateway_resource)):
                g = (agc.get(k) or {}).get("agentGateway")
                if g and not g.endswith(want.split("/locations/", 1)[1]):
                    conflicts.append(f"{e.get('displayName')} -> {g}")
        out.append(Check("Runtime gateway bindings", not conflicts,
                         f"{len(engines)} engines, no conflicting bindings" if not conflicts else "; ".join(conflicts),
                         "set gateways.create=false and reuse the bound gateways, or choose another region"))
    except GcpError as e:
        out.append(Check("Runtime gateway bindings", None, e.message[:120]))

    # org policy for IAM access policies (only needed for UAP / iapPolicyVersion V2)
    out.append(Check("IAP policy model", True, "iapPolicyVersion V1 (IAM allow policies, roles/iap.egressor)"))
    return out
