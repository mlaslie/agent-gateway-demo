"""Terraform foundation (docs/TERRAFORM_PLAN.md, phase T1): `./agdemo tf init|vars|plan|apply|output-sync`.

- tfvars: generated from config/demo.yaml (load_config) with the same naming helpers bootstrap uses.
  Terraform never reads YAML.
- State: GCS bucket <project>-agdemo-tfstate, created here through the API (never by Terraform), versioning on;
  one prefix per environment: <resource_prefix>/<project>/<region>. `--local-state PATH` instead writes a
  git-ignored backend_override.tf (local backend) for scratch plans.
- Recordings: bucket <project>-<prefix>-recordings, created here before apply so `terraform destroy` can
  never delete it (plan §7b).
- Outputs: `state_shared` is merged into config/state.json `shared` under bootstrap's keys; other keys stay.

T2: `./agdemo tf import` (agdemo_core/tf_import.py) writes the git-ignored imports.tf that adopts a
bootstrap-created environment; while it exists, plans are checked in migration mode (tf_safety).
Pure helpers (tfvars, merge) make no GCP calls.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from .config import REPO_ROOT, DemoConfig
from .gcp import gateways as gw
from .gcp import model_armor as ma
from .gcp import project as proj
from .infra import common as c

TFVARS_NAME = "generated.auto.tfvars.json"
OVERRIDE_NAME = "backend_override.tf"
PLAN_NAME = "agdemo.tfplan"
STORAGE = "https://storage.googleapis.com/storage/v1"


class TfError(RuntimeError):
    pass


def tf_dir() -> Path:
    return Path(os.environ.get("AGDEMO_TF_DIR", REPO_ROOT / "infra" / "terraform"))


# ------------------------------------------------------------------ names
def state_bucket(cfg: DemoConfig) -> str:
    """One state bucket per project, shared by every environment (prefix keys them)."""
    return f"{cfg.project}-agdemo-tfstate"[:63]


def state_prefix(cfg: DemoConfig) -> str:
    return f"{cfg.prefix}/{cfg.project}/{cfg.region}"


def recordings_bucket(cfg: DemoConfig) -> str:
    return f"{cfg.project}-{cfg.prefix}-recordings"[:63]


# ------------------------------------------------------------------ tfvars (pure)
def _endpoints(cfg: DemoConfig) -> dict[str, dict[str, str]]:
    """url -> {service_id, display_name} of the platform endpoints of the view's region."""
    return {
        url: {"service_id": c.platform_service_id(cfg, s),
              "display_name": f"{cfg.prefix} {url.split('//')[1]}"[:63]}
        for s, url in c.platform_endpoints(cfg)
    }


def pn_tfvars(cfg: DemoConfig, existing_platform: dict[str, str] | None = None) -> dict[str, Any]:
    """The `private_networking` variable (T4). Disabled: {"enabled": false}, exactly as before T4, so the base
    plan doesn't change. existing_platform: the PN region's url -> registry_resource (as for the base)."""
    pn = cfg.private_networking
    if not pn.enabled:
        return {"enabled": False}
    v = cfg.for_region(pn.region)
    endpoints = _endpoints(v)
    return {
        "enabled": True,
        "region": pn.region,
        "subnet_range": pn.subnet_range,
        "psc_ip": pn.psc_ip,
        "dns_domains": list(pn.dns_domains),
        "vpc_egress": pn.vpc_egress,
        "manage_service_agent_roles": cfg.manage_pn_service_agent_roles,
        "names": c.pn_names(v),
        "platform_endpoints": endpoints,
        "platform_endpoints_existing": {u: rr for u, rr in (existing_platform or {}).items() if u in endpoints},
    }


def tfvars(cfg: DemoConfig, organization_id: str = "", existing_platform: dict[str, str] | None = None,
           recordings: str | None = None, existing_platform_pn: dict[str, str] | None = None) -> dict[str, Any]:
    """demo.yaml -> Terraform variables. existing_platform: url -> registry_resource for URLs another
    (non-prefix) service already registered (bootstrap's url_owner branch); they are granted, not created."""
    cfg = cfg.home()
    endpoints = _endpoints(cfg)
    return {
        "project_id": cfg.project,
        "gateway_project_id": cfg.gateway_project if cfg.gateway_project != cfg.project else "",
        "region": cfg.region,
        "prefix": cfg.prefix,
        "labels": cfg.labels,
        "organization_id": organization_id or "",
        "required_apis": list(proj.REQUIRED_APIS),
        "names": {
            "ui_sa": c.ui_sa_id(cfg),
            "targets_sa": c.run_sa_id(cfg),
            "artifact_repo": c.repo_id(cfg),
            "staging_bucket": c.staging_bucket(cfg),
            "egress_gateway": cfg.egress_gateway,
            "ingress_gateway": cfg.ingress_gateway,
            "egress_iap_extension": gw.iap_extension_name(cfg, "egress"),
            "egress_iap_policy": gw.iap_policy_name(cfg, "egress"),
            "model_armor_template": cfg.model_armor_template,
            "model_armor_ext": gw.ma_extension_name(cfg),
        },
        "iap_enforcement": cfg.gateways.iap_enforcement,
        "model_armor_filters": list(cfg.model_armor.filters),
        "model_armor_block_marker": ma.BLOCK_MESSAGE_MARKER,
        "shared_project_grants": cfg.terraform.shared_project_grants,
        "auth_strategy": cfg.iam.auth_strategy,
        "presenters": sorted({m for m in cfg.ui.admin_access if m.startswith(("user:", "group:", "serviceAccount:"))}),
        "platform_endpoints": endpoints,
        "platform_endpoints_existing": {u: rr for u, rr in (existing_platform or {}).items() if u in endpoints},
        "recordings_bucket": recordings if recordings is not None else recordings_bucket(cfg),
        "private_networking": pn_tfvars(cfg, existing_platform_pn),
    }


def discover_existing_platform_pn(cfg: DemoConfig, r) -> dict[str, str]:
    """The Private Networking region's existing platform URLs ({} when the mode is off)."""
    if not cfg.private_networking.enabled:
        return {}
    return discover_existing_platform(cfg.for_region(cfg.private_networking.region), r)


def write_tfvars(cfg: DemoConfig, d: Path | None = None, **kw) -> Path:
    d = d or tf_dir()
    p = d / TFVARS_NAME
    p.write_text(json.dumps(tfvars(cfg, **kw), indent=2, sort_keys=True) + "\n")
    return p


# ------------------------------------------------------------------ outputs -> state.json (pure)
def merge_outputs(state: dict[str, Any], outputs: dict[str, Any], force: bool = False) -> dict[str, Any]:
    """Merge `terraform output -json` into state["shared"] (bootstrap's keys); every other key is kept.
    Refuses (unless force) when state.json already describes a different environment."""
    if "state_shared" not in outputs:
        raise TfError("terraform outputs have no state_shared (nothing applied yet?)")
    new = dict(outputs["state_shared"]["value"])
    env = (outputs.get("environment") or {}).get("value")
    shared = state.setdefault("shared", {})
    state.setdefault("themes", {})
    old_gw = shared.get("egress_gateway")
    if not force and old_gw and old_gw != new.get("egress_gateway"):
        raise TfError(f"state.json describes another environment (egress_gateway {old_gw}, Terraform has "
                      f"{new.get('egress_gateway')}); pass --state for a separate file, or --force")
    if not new.get("recordings_bucket"):
        new.pop("recordings_bucket", None)
    shared.update(new)
    # T4: per-region sections (Private Networking). Replaced as a whole, so turning the mode off removes it.
    if "state_regions" in outputs:
        regions = (outputs["state_regions"] or {}).get("value") or {}
        if regions:
            shared["regions"] = regions
        else:
            shared.pop("regions", None)
    if env:
        shared["tf_environment"] = env
    return state


# ------------------------------------------------------------------ GCP (CLI-owned buckets, read-only discovery)
def ensure_bucket(r, project: str, name: str, location: str, labels: dict[str, str], versioning: bool) -> bool:
    """Create the bucket if missing (UBLA, labels). Returns True when created."""
    from .gcp.rest import GcpError
    if r.get(f"{STORAGE}/b/{name}", ok404=True) is not None:
        return False
    body: dict[str, Any] = {"name": name, "location": location.upper(), "labels": labels,
                            "iamConfiguration": {"uniformBucketLevelAccess": {"enabled": True}}}
    if versioning:
        body["versioning"] = {"enabled": True}
    try:
        r.post(f"{STORAGE}/b", params={"project": project}, json=body)
    except GcpError as e:
        if not e.conflict:
            raise
        return False
    return True


def discover_existing_platform(cfg: DemoConfig, r) -> dict[str, str]:
    """Read-only: platform URLs already registered by a service whose id isn't the one Terraform would use
    (in the view's region: pass cfg.for_region(...) for the Private Networking registry)."""
    from .gcp import registry
    ours = {url: c.platform_service_id(cfg, s) for s, url in c.platform_endpoints(cfg)}
    out: dict[str, str] = {}
    for svc in registry.list_services(r, cfg.registry_parent):
        sid = svc["name"].split("/")[-1]
        for i in svc.get("interfaces", []):
            url = i.get("url")
            if url in ours and sid != ours[url] and svc.get("registryResource"):
                out[url] = svc["registryResource"]
    return out


# ------------------------------------------------------------------ terraform binary
def terraform_bin() -> str:
    b = os.environ.get("AGDEMO_TERRAFORM") or shutil.which("terraform")
    if not b:
        raise TfError("terraform not found (install >= 1.7 or set AGDEMO_TERRAFORM)")
    return b


def run_tf(args: list[str], d: Path | None = None, capture: bool = False) -> str:
    cmd = [terraform_bin(), f"-chdir={d or tf_dir()}", *args]
    p = subprocess.run(cmd, text=True, capture_output=capture, env={**os.environ, "TF_IN_AUTOMATION": "1"})
    if p.returncode != 0:
        raise TfError(f"{' '.join(cmd[1:3])} failed (exit {p.returncode})"
                      + (f": {p.stderr.strip()[-2000:]}" if capture else ""))
    return p.stdout if capture else ""


def init(cfg: DemoConfig, r=None, local_state: str | None = None, log: Callable[[str], None] = c.log,
         organization_id: str = "", existing_platform: dict[str, str] | None = None,
         create_bucket: bool = True, existing_platform_pn: dict[str, str] | None = None) -> None:
    """create_bucket=False (down): refuse instead of creating a missing state bucket (an empty new state
    can't describe the environment being destroyed)."""
    d = tf_dir()
    write_tfvars(cfg, d, organization_id=organization_id, existing_platform=existing_platform,
                 existing_platform_pn=existing_platform_pn)
    log(f"  wrote {d / TFVARS_NAME}")
    override = d / OVERRIDE_NAME
    if local_state:
        override.write_text("# Written by ./agdemo tf init --local-state (git-ignored). Scratch state only.\n"
                            'terraform {\n  backend "local" {\n'
                            f"    path = {json.dumps(str(Path(local_state).resolve()))}\n  }}\n}}\n")
        log(f"  local backend: {local_state}")
        run_tf(["init", "-input=false", "-reconfigure"], d)
        return
    if override.exists():
        override.unlink()
    bucket = state_bucket(cfg)
    if not create_bucket:
        if r.get(f"{STORAGE}/b/{bucket}", ok404=True) is None:
            raise TfError(f"state bucket gs://{bucket} doesn't exist: this environment has no Terraform state")
    elif ensure_bucket(r, cfg.project, bucket, cfg.region, cfg.labels, versioning=True):
        log(f"  created state bucket gs://{bucket} (versioning on)")
    run_tf(["init", "-input=false", "-reconfigure", f"-backend-config=bucket={bucket}",
            f"-backend-config=prefix={state_prefix(cfg)}"], d)


def migrating(d: Path | None = None) -> bool:
    """True while infra/terraform/imports.tf exists (T2 adoption of a bootstrap-created environment)."""
    from .tf_import import IMPORTS_NAME
    return ((d or tf_dir()) / IMPORTS_NAME).exists()


def plan(out: Path | None = None, allow_replace: bool = False, json_out: Path | None = None,
         allow_template_swap: bool = False, allow_role_changes: bool = False):
    """terraform plan -out, then the plan-safety check (migration mode while imports.tf exists).
    Returns (plan file, Verdict). json_out: also save `terraform show -json`."""
    from . import tf_safety
    d = tf_dir()
    out = out or d / PLAN_NAME
    run_tf(["plan", "-input=false", f"-out={out}"], d)
    js = run_tf(["show", "-json", str(out)], d, capture=True)
    if json_out:
        Path(json_out).write_text(js)
    return out, tf_safety.check(json.loads(js), allow_replace=allow_replace, migration=migrating(d),
                                allow_template_swap=allow_template_swap, allow_role_changes=allow_role_changes)


def state_addresses() -> set[str]:
    try:
        return set(run_tf(["state", "list"], capture=True).split())
    except TfError:
        return set()


def outputs() -> dict[str, Any]:
    return json.loads(run_tf(["output", "-json"], capture=True) or "{}")


GUARD_ADDR = "terraform_data.teardown_guard"


def guard_environment(show: dict[str, Any]) -> dict[str, Any] | None:
    """The environment the destroy guard recorded (`input.environment` = <project>/<region>/<prefix>, the
    gateway project from its gateway ids), from `terraform show -json`. None without a well-formed guard."""
    for res in ((show.get("values") or {}).get("root_module") or {}).get("resources") or []:
        if res.get("address") != GUARD_ADDR:
            continue
        inp = (res.get("values") or {}).get("input") or {}
        parts = str(inp.get("environment") or "").split("/")
        if len(parts) != 3 or not all(parts):
            return None
        gws = [g.split("/") for g in inp.get("gateways") or [] if g]
        gw_project = gws[0][1] if gws and len(gws[0]) > 1 and gws[0][0] == "projects" else ""
        return {"project_id": parts[0], "region": parts[1], "prefix": parts[2],
                "gateway_project_id": "" if gw_project == parts[0] else gw_project}
    return None


def environment_output() -> dict[str, Any] | None:
    """The `environment` output of the current state (None when nothing is applied).

    Any `terraform destroy` removes the root outputs from state, even one the guard aborts before deleting
    anything (T5, §7i). Then the guard's recorded environment stands in, so `down` can still run (or re-run
    after a half-finished destroy). `./agdemo tf apply` (no resource changes) writes the outputs back."""
    try:
        env = (outputs().get("environment") or {}).get("value")
    except (TfError, ValueError):
        env = None
    if env:
        return env
    try:
        return guard_environment(json.loads(run_tf(["show", "-json"], capture=True) or "{}"))
    except (TfError, ValueError):
        return None


def state_rm(addresses: list[str]) -> None:
    """Forget resources without deleting them (down keeps shared project grants this way)."""
    if addresses:
        run_tf(["state", "rm", *addresses])


def destroy() -> None:
    """terraform destroy (the guard resource re-runs check-clean --strict first)."""
    run_tf(["destroy", "-input=false", "-auto-approve"])
