"""`./agdemo tf import`: import blocks that adopt a bootstrap-created environment into Terraform
(docs/TERRAFORM_PLAN.md §5.1 step 3, phase T2).

Output: infra/terraform/imports.tf. It is **generated and git-ignored**, never committed: it holds the real
project id, and it is only needed until the first apply has the resources in state (delete it after; a
leftover file is harmless, since an import of an address already in state is a no-op).

Inputs: config/demo.yaml (names, through the same helpers bootstrap and the tfvars use), config/state.json
`shared` (cross-checked: a mismatch with the computed name is reported, never silently used) and read-only
live lists. A resource is imported only when it exists live; a missing one is left for Terraform to create
and reported. Legacy resources Terraform never adopts (plan §2.3) are reported when still present.

Addresses (module `base`):
  google_network_services_agent_gateway.gw["egress"|"ingress"]
  google_network_services_authz_extension.egress_iap, .model_armor[<project>]
  google_network_security_authz_policy.egress_iap
  google_model_armor_template.shield
  google_service_account.sa["ui"|"targets"]
  google_artifact_registry_repository.repo
  google_storage_bucket.staging
  google_agent_registry_service.platform[<url>]       (one for_each import block)
Not imported (additive, creating them is a no-op): google_project_service, *_iam_member.

Pure functions (build_imports, render) make no GCP calls; discover_live is read-only (GET/list).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import DemoConfig
from .gcp import gateways as gw
from .infra import common as c

IMPORTS_NAME = "imports.tf"
MODULE = "module.base"
PLATFORM_ADDR = f"{MODULE}.google_agent_registry_service.platform"
EGRESS_GW_ADDR = f'{MODULE}.google_network_services_agent_gateway.gw["egress"]'


@dataclass
class ImportSet:
    blocks: list[tuple[str, str]] = field(default_factory=list)      # (address, import id)
    platform: dict[str, str] = field(default_factory=dict)           # url -> registry service id (for_each)
    missing: list[str] = field(default_factory=list)                 # expected but not live: Terraform creates
    warnings: list[str] = field(default_factory=list)
    legacy_present: list[str] = field(default_factory=list)          # delete before importing (runbook step a)

    @property
    def count(self) -> int:
        return len(self.blocks) + len(self.platform)


def legacy_names(cfg: DemoConfig) -> dict[str, list[str]]:
    """Resources older bootstraps created that Terraform will not adopt (plan §2.3, Q4: delete them)."""
    return {
        "authz_policies": [gw.iap_policy_name(cfg, "ingress")],
        "authz_extensions": [gw.iap_extension_name(cfg, "ingress")],
        "service_accounts": [c.sa(cfg, cfg.name("denied-caller")), c.sa(cfg, cfg.name("allowed-caller"))],
    }


def _leaf(name: str) -> str:
    return name.rstrip("/").split("/")[-1]


def build_imports(cfg: DemoConfig, shared: dict[str, Any], live: dict[str, Any],
                  existing_platform: dict[str, str] | None = None) -> ImportSet:
    """live: {"gateways": {project: [names]}, "authz_extensions": {project: [names]},
    "authz_policies": {project: [names]}, "service_accounts": [emails], "repositories": [ids],
    "buckets": [names], "model_armor_templates": [ids], "registry_services": [service ids]}.
    existing_platform: url -> registry resource for URLs another service registered (not ours to import)."""
    out = ImportSet()
    region = cfg.region
    gproj = cfg.gateway_project

    def lv(kind: str, project: str | None = None) -> set[str]:
        v = live.get(kind) or ({} if project is not None else [])
        return set(v.get(project, []) if project is not None else v)

    def add(addr: str, rid: str, present: bool, state_key: str | None = None, state_value: str | None = None):
        if state_key and shared.get(state_key) and state_value is not None and shared[state_key] != state_value:
            out.warnings.append(f"state.json shared.{state_key} is {shared[state_key]!r}, config gives "
                                f"{state_value!r}; importing the config name")
        if present:
            out.blocks.append((addr, rid))
        else:
            out.missing.append(f"{addr} ({rid})")

    # gateways
    for p in ("egress", "ingress"):
        proj = gw.gateway_project(cfg, p)
        name = gw.gateway_name(cfg, p)
        rid = f"projects/{proj}/locations/{region}/agentGateways/{name}"
        add(f'{MODULE}.google_network_services_agent_gateway.gw["{p}"]', rid, name in lv("gateways", proj),
            f"{p}_gateway", rid)

    # IAP extension + REQUEST_AUTHZ policy (egress only)
    ext = gw.iap_extension_name(cfg, "egress")
    rid = f"projects/{gproj}/locations/{region}/authzExtensions/{ext}"
    add(f"{MODULE}.google_network_services_authz_extension.egress_iap", rid,
        ext in lv("authz_extensions", gproj), "egress_iap_extension", rid)
    pol = gw.iap_policy_name(cfg, "egress")
    rid = f"projects/{gproj}/locations/{region}/authzPolicies/{pol}"
    add(f"{MODULE}.google_network_security_authz_policy.egress_iap", rid,
        pol in lv("authz_policies", gproj), "egress_iap_policy", rid)

    # Model Armor template + extension in each gateway project
    tmpl = cfg.model_armor_template
    rid = f"projects/{cfg.project}/locations/{region}/templates/{tmpl}"
    add(f"{MODULE}.google_model_armor_template.shield", rid, tmpl in lv("model_armor_templates"),
        "model_armor_template", rid)
    ma_ext = gw.ma_extension_name(cfg)
    for proj in sorted({cfg.project, gproj}):
        rid = f"projects/{proj}/locations/{region}/authzExtensions/{ma_ext}"
        add(f'{MODULE}.google_network_services_authz_extension.model_armor["{proj}"]', rid,
            ma_ext in lv("authz_extensions", proj),
            "model_armor_extension" if proj == cfg.project else None, rid)

    # service accounts, repo, staging bucket
    for key, acc, skey in (("ui", c.ui_sa_id(cfg), "ui_sa"), ("targets", c.run_sa_id(cfg), "targets_sa")):
        email = c.sa(cfg, acc)
        add(f'{MODULE}.google_service_account.sa["{key}"]', f"projects/{cfg.project}/serviceAccounts/{email}",
            email in lv("service_accounts"), skey, email)
    repo = c.repo_id(cfg)
    add(f"{MODULE}.google_artifact_registry_repository.repo",
        f"projects/{cfg.project}/locations/{region}/repositories/{repo}", repo in lv("repositories"),
        "artifact_repo", f"{region}-docker.pkg.dev/{cfg.project}/{repo}")
    bucket = c.staging_bucket(cfg)
    add(f"{MODULE}.google_storage_bucket.staging", bucket, bucket in lv("buckets"),
        "staging_bucket", f"gs://{bucket}")

    # platform registry entries (one for_each import); URLs another service owns are granted, not imported
    have = lv("registry_services")
    for s, url in c.platform_endpoints(cfg):
        if url in (existing_platform or {}):
            continue
        sid = c.platform_service_id(cfg, s)
        if sid in have:
            out.platform[url] = f"projects/{gproj}/locations/{region}/services/{sid}"
        else:
            out.missing.append(f'{PLATFORM_ADDR}["{url}"] (services/{sid})')
    known = set(shared.get("platform_registry_ids") or {})
    if known and set(out.platform) - known:
        out.warnings.append(f"{len(set(out.platform) - known)} platform URL(s) are live but not in "
                            "state.json platform_registry_ids")

    # legacy (never imported; runbook step a deletes them)
    leg = legacy_names(cfg)
    out.legacy_present += [f"authzPolicies/{n}" for n in leg["authz_policies"]
                           if n in lv("authz_policies", gw.gateway_project(cfg, "ingress"))]
    out.legacy_present += [f"authzExtensions/{n}" for n in leg["authz_extensions"]
                           if n in lv("authz_extensions", gw.gateway_project(cfg, "ingress"))]
    out.legacy_present += [f"serviceAccounts/{e}" for e in leg["service_accounts"] if e in lv("service_accounts")]
    return out


def render(imports: ImportSet) -> str:
    """HCL for imports.tf (terraform fmt-clean)."""
    lines = [
        "# GENERATED by `./agdemo tf import` (docs/TERRAFORM_PLAN.md §5.1). Git-ignored; do not edit or commit.",
        "# Adopts the bootstrap-created environment into this state. Delete after the first successful apply.",
        "",
    ]
    for addr, rid in imports.blocks:
        lines += ["import {", f"  to = {addr}", f"  id = {json.dumps(rid)}", "}", ""]
    if imports.platform:
        lines += ["locals {", "  agdemo_platform_imports = {"]
        w = max(len(json.dumps(u)) for u in imports.platform)
        for url in sorted(imports.platform):
            k = json.dumps(url)
            lines.append(f"    {k}{' ' * (w - len(k))} = {json.dumps(imports.platform[url])}")
        lines += ["  }", "}", "",
                  "import {", "  for_each = local.agdemo_platform_imports", f"  to       = {PLATFORM_ADDR}[each.key]",
                  "  id       = each.value", "}", ""]
    return "\n".join(lines)


def write(imports: ImportSet, d: Path) -> Path:
    p = d / IMPORTS_NAME
    p.write_text(render(imports))
    return p


def imports_address(d: Path, address: str) -> bool:
    """True when imports.tf in d has an import block for `address` (the apply guard accepts that)."""
    p = d / IMPORTS_NAME
    return p.exists() and f"to = {address}\n" in p.read_text()


# ------------------------------------------------------------------ live lists (read-only)
def discover_live(cfg: DemoConfig, r) -> dict[str, Any]:
    from .gcp import registry
    from .gcp.rest import ARTIFACTREGISTRY, IAM, NETWORKSECURITY, NETWORKSERVICES, NETWORKSERVICES_BETA, modelarmor
    from .tf_foundation import STORAGE

    def names(url: str, key: str, params: dict | None = None) -> list[str]:
        out, tok = [], None
        while True:
            d = r.get(url, params={**(params or {}), **({"pageToken": tok} if tok else {})}, ok404=True) or {}
            out += [_leaf(x["name"]) for x in d.get(key, [])]
            tok = d.get("nextPageToken")
            if not tok:
                return out

    region = cfg.region
    projects = sorted({cfg.project, cfg.gateway_project})
    live: dict[str, Any] = {"gateways": {}, "authz_extensions": {}, "authz_policies": {}}
    for proj in projects:
        loc = f"projects/{proj}/locations/{region}"
        live["gateways"][proj] = names(f"{NETWORKSERVICES}/{loc}/agentGateways", "agentGateways")
        live["authz_extensions"][proj] = names(f"{NETWORKSERVICES_BETA}/{loc}/authzExtensions", "authzExtensions")
        live["authz_policies"][proj] = names(f"{NETWORKSECURITY}/{loc}/authzPolicies", "authzPolicies")
    d = r.get(f"{IAM}/projects/{cfg.project}/serviceAccounts", params={"pageSize": 100}) or {}
    sas = [a["email"] for a in d.get("accounts", [])]
    while d.get("nextPageToken"):
        d = r.get(f"{IAM}/projects/{cfg.project}/serviceAccounts",
                  params={"pageSize": 100, "pageToken": d["nextPageToken"]}) or {}
        sas += [a["email"] for a in d.get("accounts", [])]
    live["service_accounts"] = sas
    live["repositories"] = names(f"{ARTIFACTREGISTRY}/projects/{cfg.project}/locations/{region}/repositories",
                                 "repositories")
    b = c.staging_bucket(cfg)
    live["buckets"] = [b] if r.get(f"{STORAGE}/b/{b}", ok404=True) is not None else []
    live["model_armor_templates"] = names(f"{modelarmor(region)}/projects/{cfg.project}/locations/{region}/templates",
                                          "templates")
    live["registry_services"] = [_leaf(s["name"]) for s in registry.list_services(r, cfg.registry_parent)]
    return live
