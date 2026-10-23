"""The deployer's project roles for each iam.auth_strategy (docs/PERMISSIONS.md §2.2, §5.3; tested live, §0).

The deployer is whoever runs ./agdemo up / deploy-theme / down. A project owner grants these roles once:
`./agdemo deployer-roles --member user:you@example.com` prints the commands, `--apply` runs them.

standard: 15 predefined roles, Project IAM Admin unconditional.
minimum:  15 roles in 16 bindings. aiplatform.expressUser instead of aiplatform.user, a project custom role instead of
          iap.admin, no logging.viewer, iam.roleAdmin (Terraform creates the UI's two custom roles), and Project IAM Admin twice, each limited by a condition to the roles
          the demo grants (IAM allows at most 10 roles per hasOnly() list). Pair it with iam.auth_strategy
          minimum: list B only covers the minimum service-account roles.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..config import CONFIG_DIR, DemoConfig

COMMON = ["roles/serviceusage.serviceUsageAdmin", "roles/iam.serviceAccountAdmin", "roles/iam.serviceAccountUser",
          "roles/storage.admin", "roles/artifactregistry.admin", "roles/cloudbuild.builds.editor", "roles/run.admin",
          "roles/networkservices.editor", "roles/networksecurity.editor", "roles/agentregistry.editor",
          "roles/modelarmor.admin"]
STANDARD = ["roles/resourcemanager.projectIamAdmin", *COMMON, "roles/aiplatform.user", "roles/iap.admin",
            "roles/logging.viewer"]
PRIVATE_NETWORKING = ["roles/compute.networkAdmin", "roles/dns.admin"]
GEMINI_ENTERPRISE = ["roles/discoveryengine.agentAdmin"]          # not tested live
# iam.auth_strategy minimum: Terraform creates, updates, undeletes and deletes the UI's two project custom roles
# (iam.roles.*). Found by the live test: without it the apply fails on iam.roles.get. A standard deployer used with
# iam.auth_strategy minimum needs it too.
ROLE_ADMIN = "roles/iam.roleAdmin"

DEPLOYER_IAP_PERMISSIONS = ["iap.webTypes.getIamPolicy", "iap.webTypes.setIamPolicy",
                            "iap.webServices.getIamPolicy", "iap.webServices.setIamPolicy",
                            "iap.webServiceVersions.getIamPolicy", "iap.webServiceVersions.setIamPolicy"]

# Every project role the demo's tooling grants or removes, in two lists of at most 10 (one change must fit
# in one list: `down` removes all of an Agent Identity principal's roles, or all of a service account's, at once).
AGENT_IDENTITY_ROLES = ["roles/aiplatform.user", "roles/agentregistry.viewer", "roles/logging.logWriter",
                        "roles/monitoring.metricWriter", "roles/cloudtrace.agent", "roles/browser",
                        "roles/serviceusage.serviceUsageConsumer", "roles/modelarmor.calloutUser",
                        "roles/modelarmor.user"]


def role_prefix(cfg: DemoConfig) -> str:
    """Custom role ids allow letters, digits, _ and . only (the same rule as infra/terraform/modules/base/iam.tf)."""
    return cfg.prefix.replace("-", "_")


def deployer_iap_role(cfg: DemoConfig) -> str:
    return f"projects/{cfg.project}/roles/{role_prefix(cfg)}DeployerIap"


def service_account_roles(cfg: DemoConfig) -> list[str]:
    p = f"projects/{cfg.project}/roles/{role_prefix(cfg)}"
    return ["roles/aiplatform.expressUser", "roles/agentregistry.viewer", "roles/logging.viewer",
            "roles/serviceusage.serviceUsageConsumer", f"{p}RegistryEgressAdmin", f"{p}GatewayPolicyEditor",
            "roles/compute.networkAdmin", "roles/dns.peer", "roles/modelarmor.calloutUser", "roles/modelarmor.user"]


def _has_only(roles: list[str]) -> str:
    assert len(roles) <= 10, "IAM allows at most 10 roles in a hasOnly() list"
    return ("api.getAttribute('iam.googleapis.com/modifiedGrantsByRole', []).hasOnly(["
            + ", ".join(f"'{r}'" for r in roles) + "])")


def conditions(cfg: DemoConfig) -> list[dict[str, str]]:
    return [
        {"title": f"{cfg.prefix}-agent-identity-roles",
         "description": "Roles deploy-theme grants to Agent Identity principals, and the Model Armor service-agent roles",
         "expression": _has_only(AGENT_IDENTITY_ROLES)},
        {"title": f"{cfg.prefix}-service-account-roles",
         "description": "Roles Terraform grants the demo's service accounts (iam.auth_strategy minimum) and service agents",
         "expression": _has_only(service_account_roles(cfg))},
    ]


@dataclass
class Grant:
    role: str
    condition_file: Path | None = None


def grants(cfg: DemoConfig, strategy: str, private_networking: bool, gemini_enterprise: bool,
           out_dir: Path | None = None) -> list[Grant]:
    """The deployer's bindings. minimum writes its two condition files to out_dir (config/generated)."""
    extra = (PRIVATE_NETWORKING if private_networking else []) + (GEMINI_ENTERPRISE if gemini_enterprise else [])
    if strategy == "standard":
        return [Grant(r) for r in STANDARD + extra]
    if strategy != "minimum":
        raise ValueError("auth strategy must be standard or minimum")
    out_dir = out_dir or CONFIG_DIR / "generated"
    out_dir.mkdir(parents=True, exist_ok=True)
    conds = []
    for i, cond in enumerate(conditions(cfg)):
        f = out_dir / f"deployer-condition-{'ab'[i]}.yaml"
        f.write_text(yaml.safe_dump(cond, sort_keys=False, width=1000))
        conds.append(Grant("roles/resourcemanager.projectIamAdmin", f))
    return [*conds, *(Grant(r) for r in COMMON), Grant("roles/aiplatform.expressUser"),
            Grant(deployer_iap_role(cfg)), Grant(ROLE_ADMIN), *(Grant(r) for r in extra)]


def ensure_deployer_iap_role(cfg: DemoConfig) -> str:
    """Create the deployer's custom IAP role, or bring it back: a deleted custom role keeps its id reserved for
    about 37 days, so a second `deployer-roles --apply` after a teardown must undelete it. Returns what it did."""
    from ..gcp import gcloud
    rid, proj = f"{role_prefix(cfg)}DeployerIap", f"--project={cfg.project}"
    perms = f"--permissions={','.join(DEPLOYER_IAP_PERMISSIONS)}"
    try:
        role = gcloud.run(["iam", "roles", "describe", rid, proj], json_out=True) or {}
    except gcloud.GcloudError:
        role = None
    if role is None:
        gcloud.run(["iam", "roles", "create", rid, proj, f"--title={cfg.prefix} deployer: IAP grants on registry "
                    "entries and the UI", perms, "--stage=GA", "--quiet"])
        return "created"
    did = "updated"
    if role.get("deleted"):
        gcloud.run(["iam", "roles", "undelete", rid, proj, "--quiet"])
        did = "undeleted"
    gcloud.run(["iam", "roles", "update", rid, proj, perms, "--stage=GA", "--quiet"])
    return did


def commands(cfg: DemoConfig, member: str, strategy: str, private_networking: bool, gemini_enterprise: bool,
             gcloud: str = "gcloud", out_dir: Path | None = None) -> list[list[str]]:
    """gcloud argument lists, in order: the custom IAP role first (minimum), then one binding per grant."""
    cmds: list[list[str]] = []
    if strategy == "minimum":
        cmds.append([gcloud, "iam", "roles", "create", f"{role_prefix(cfg)}DeployerIap", f"--project={cfg.project}",
                     f"--title={cfg.prefix} deployer: IAP grants on registry entries and the UI",
                     f"--permissions={','.join(DEPLOYER_IAP_PERMISSIONS)}", "--stage=GA", "--quiet"])
    for g in grants(cfg, strategy, private_networking, gemini_enterprise, out_dir):
        cond = [f"--condition-from-file={g.condition_file}"] if g.condition_file else ["--condition=None"]
        cmds.append([gcloud, "projects", "add-iam-policy-binding", cfg.project, f"--member={member}",
                     f"--role={g.role}", *cond, "--quiet"])
    return cmds
