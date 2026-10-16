"""Terraform T2 (docs/TERRAFORM_PLAN.md §5.1): `./agdemo tf import` generator, migration-mode plan-safety,
apply guard and the bootstrap deprecation. No GCP calls."""
import re
import shutil
import subprocess

from agdemo_core import tf_import as tfi
from agdemo_core import tf_safety
from agdemo_core.cli import bootstrap_refusal
from agdemo_core.config import REPO_ROOT, DemoConfig
from agdemo_core.infra import common as c

TF_DIR = REPO_ROOT / "infra" / "terraform"


def make_cfg(**env):
    e = {"project_id": "demo-proj", "region": "us-east1", "resource_prefix": "agdtest", **env}
    return DemoConfig.model_validate({"environment": e})


def live_for(cfg, *, legacy=False, drop=()):
    """A live environment exactly as bootstrap leaves it (optionally with legacy leftovers)."""
    p = cfg.project
    live = {
        "gateways": {p: ["agdtest-egress", "agdtest-ingress", "someone-elses-gw"]},
        "authz_extensions": {p: ["agdtest-egress-iap-authz", "agdtest-ma-authz"]},
        "authz_policies": {p: ["agdtest-egress-iap-policy"]},
        "service_accounts": [f"agdtest-ui@{p}.iam.gserviceaccount.com", f"agdtest-targets@{p}.iam.gserviceaccount.com",
                             f"policy-studio@{p}.iam.gserviceaccount.com"],
        "repositories": ["agdtest", "policy-studio"],
        "buckets": [f"{p}-agdtest-staging"],
        "model_armor_templates": ["agdtest-shield"],
        "registry_services": [c.platform_service_id(cfg, s) for s, _ in c.platform_endpoints(cfg)]
        + ["agdtest-retail-orders-mcp"],
    }
    if legacy:
        live["authz_extensions"][p].append("agdtest-ingress-iap-authz")
        live["authz_policies"][p].append("agdtest-ingress-iap-policy")
        live["service_accounts"] += [f"agdtest-denied-caller@{p}.iam.gserviceaccount.com",
                                     f"agdtest-allowed-caller@{p}.iam.gserviceaccount.com"]
    for kind, name in drop:
        v = live[kind]
        if isinstance(v, dict):
            v[p].remove(name)
        else:
            v.remove(name)
    return live


def test_imports_cover_every_bootstrap_resource():
    cfg = make_cfg()
    imp = tfi.build_imports(cfg, {}, live_for(cfg))
    blocks = dict(imp.blocks)
    loc = "projects/demo-proj/locations/us-east1"
    assert blocks == {
        'module.base.google_network_services_agent_gateway.gw["egress"]': f"{loc}/agentGateways/agdtest-egress",
        'module.base.google_network_services_agent_gateway.gw["ingress"]': f"{loc}/agentGateways/agdtest-ingress",
        "module.base.google_network_services_authz_extension.egress_iap": f"{loc}/authzExtensions/agdtest-egress-iap-authz",
        "module.base.google_network_security_authz_policy.egress_iap": f"{loc}/authzPolicies/agdtest-egress-iap-policy",
        "module.base.google_model_armor_template.shield": f"{loc}/templates/agdtest-shield",
        'module.base.google_network_services_authz_extension.model_armor["demo-proj"]':
            f"{loc}/authzExtensions/agdtest-ma-authz",
        'module.base.google_service_account.sa["ui"]':
            "projects/demo-proj/serviceAccounts/agdtest-ui@demo-proj.iam.gserviceaccount.com",
        'module.base.google_service_account.sa["targets"]':
            "projects/demo-proj/serviceAccounts/agdtest-targets@demo-proj.iam.gserviceaccount.com",
        "module.base.google_artifact_registry_repository.repo": f"{loc}/repositories/agdtest",
        "module.base.google_storage_bucket.staging": "demo-proj-agdtest-staging",
    }
    assert len(imp.platform) == len(c.platform_endpoints(cfg)) == 25
    url = "https://us-east1-aiplatform.googleapis.com"
    assert imp.platform[url] == f"{loc}/services/{c.platform_service_id(cfg, 'us-east1-aiplatform')}"
    assert imp.count == 35 and not imp.missing and not imp.warnings and not imp.legacy_present


def test_imports_never_include_legacy_and_report_it():
    cfg = make_cfg()
    imp = tfi.build_imports(cfg, {}, live_for(cfg, legacy=True))
    text = tfi.render(imp)
    assert "ingress-iap" not in text and "caller" not in text
    assert sorted(imp.legacy_present) == sorted([
        "authzPolicies/agdtest-ingress-iap-policy", "authzExtensions/agdtest-ingress-iap-authz",
        "serviceAccounts/agdtest-denied-caller@demo-proj.iam.gserviceaccount.com",
        "serviceAccounts/agdtest-allowed-caller@demo-proj.iam.gserviceaccount.com"])


def test_missing_resources_are_left_to_create_and_reported():
    cfg = make_cfg()
    sid = c.platform_service_id(cfg, "logging")
    imp = tfi.build_imports(cfg, {}, live_for(cfg, drop=[("authz_policies", "agdtest-egress-iap-policy"),
                                                          ("registry_services", sid)]))
    assert "module.base.google_network_security_authz_policy.egress_iap" not in dict(imp.blocks)
    assert "https://logging.googleapis.com" not in imp.platform
    assert any("egress_iap" in m for m in imp.missing) and any(sid in m for m in imp.missing)
    assert imp.count == 33


def test_urls_owned_by_another_service_are_not_imported():
    cfg = make_cfg()
    url = "https://logging.googleapis.com"
    imp = tfi.build_imports(cfg, {}, live_for(cfg), existing_platform={url: "projects/1/locations/x/endpoints/e"})
    assert url not in imp.platform and len(imp.platform) == 24


def test_state_json_mismatch_is_a_warning_not_used():
    cfg = make_cfg()
    shared = {"egress_gateway": "projects/demo-proj/locations/us-east1/agentGateways/other",
              "staging_bucket": "gs://demo-proj-agdtest-staging",
              "ui_sa": "agdtest-ui@demo-proj.iam.gserviceaccount.com",
              "platform_registry_ids": {"https://aiplatform.googleapis.com": "x"}}
    imp = tfi.build_imports(cfg, shared, live_for(cfg))
    assert len(imp.warnings) == 2
    assert "shared.egress_gateway" in imp.warnings[0]
    assert "not in state.json platform_registry_ids" in imp.warnings[1]
    assert dict(imp.blocks)['module.base.google_network_services_agent_gateway.gw["egress"]'].endswith("/agdtest-egress")


def test_cross_project_gateway():
    cfg = make_cfg(gateway_project_id="gw-proj")
    live = live_for(cfg)
    live["gateways"] = {"gw-proj": ["agdtest-egress"], "demo-proj": ["agdtest-ingress"]}
    live["authz_extensions"] = {"gw-proj": ["agdtest-egress-iap-authz", "agdtest-ma-authz"],
                                "demo-proj": ["agdtest-ma-authz"]}
    live["authz_policies"] = {"gw-proj": ["agdtest-egress-iap-policy"]}
    imp = tfi.build_imports(cfg, {}, live)
    b = dict(imp.blocks)
    assert b['module.base.google_network_services_agent_gateway.gw["egress"]'].startswith("projects/gw-proj/")
    assert b['module.base.google_network_services_agent_gateway.gw["ingress"]'].startswith("projects/demo-proj/")
    assert 'module.base.google_network_services_authz_extension.model_armor["gw-proj"]' in b
    assert 'module.base.google_network_services_authz_extension.model_armor["demo-proj"]' in b
    assert all(v.startswith("projects/gw-proj/") for v in imp.platform.values())
    assert not imp.missing


def test_render_hcl(tmp_path):
    cfg = make_cfg()
    imp = tfi.build_imports(cfg, {}, live_for(cfg))
    text = tfi.render(imp)
    assert text.count("import {") == 11                       # 10 blocks + one for_each block
    assert "for_each = local.agdemo_platform_imports" in text
    assert "to       = module.base.google_agent_registry_service.platform[each.key]" in text
    assert len(re.findall(r'^    "https://', text, re.M)) == 25
    p = tfi.write(imp, tmp_path)
    assert p.name == "imports.tf"
    assert tfi.imports_address(tmp_path, tfi.EGRESS_GW_ADDR)
    assert not tfi.imports_address(tmp_path, 'module.base.google_network_services_agent_gateway.gw["x"]')
    assert not tfi.imports_address(tmp_path / "nope", tfi.EGRESS_GW_ADDR)
    if shutil.which("terraform"):
        r = subprocess.run(["terraform", "fmt", "-check", str(p)], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr


def test_imports_file_is_git_ignored():
    r = subprocess.run(["git", "check-ignore", "-q", str(TF_DIR / tfi.IMPORTS_NAME)], cwd=REPO_ROOT)
    assert r.returncode == 0


def test_addresses_exist_in_module():
    """Every address the generator imports names a resource declared in modules/base."""
    src = "\n".join(p.read_text() for p in (TF_DIR / "modules" / "base").glob("*.tf"))
    cfg = make_cfg()
    imp = tfi.build_imports(cfg, {}, live_for(cfg))
    for addr in [a for a, _ in imp.blocks] + [tfi.PLATFORM_ADDR]:
        rtype, rname = addr.split(".")[2], addr.split(".")[3].split("[")[0]
        assert f'resource "{rtype}" "{rname}"' in src, addr


# ---------------------------------------------------------------------- migration-mode plan-safety
def rc(addr, actions, rtype="google_project_iam_member", importing=False):
    ch = {"actions": actions}
    if importing:
        ch["importing"] = {"id": "x"}
    return {"address": addr, "mode": "managed", "type": rtype, "change": ch}


MIGRATION_OK = {"resource_changes": [
    rc("gw", ["no-op"], "google_network_services_agent_gateway", importing=True),
    rc("repo", ["update"], "google_artifact_registry_repository", importing=True),
    rc("api", ["create"], "google_project_service"),
    rc("iam", ["create"], "google_project_iam_member"),
    rc("bkt", ["create"], "google_storage_bucket_iam_member"),
    rc("egr", ["create"], "google_iap_agent_registry_endpoint_iam_member"),
]}


def test_migration_allows_imports_and_additive_creates():
    v = tf_safety.check(MIGRATION_OK, migration=True)
    assert v.ok and v.counts["import"] == 2 and v.counts["create"] == 4 and v.imported == ["gw", "repo"]


def test_migration_refuses_creates_that_should_be_imports():
    plan = {"resource_changes": MIGRATION_OK["resource_changes"] + [
        rc("module.base.google_network_security_authz_policy.egress_iap", ["create"],
           "google_network_security_authz_policy")]}
    assert tf_safety.check(plan).ok                                    # fresh-environment mode allows it
    v = tf_safety.check(plan, migration=True)
    assert not v.ok and v.blockers == [("module.base.google_network_security_authz_policy.egress_iap", ["create"])]


def test_migration_cli_flag(tmp_path, capsys):
    import json
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"resource_changes": [rc("sa", ["create"], "google_service_account")]}))
    assert tf_safety.main([str(f)]) == 0
    assert tf_safety.main([str(f), "--migration"]) == 1
    assert "non-additive creates" in capsys.readouterr().out


# ---------------------------------------------------------------------- bootstrap deprecation
def test_bootstrap_refused_once_terraform_owns_the_environment():
    assert bootstrap_refusal({}) is None
    assert bootstrap_refusal({"shared": {"egress_gateway": "x"}}) is None          # bootstrap-created
    msg = bootstrap_refusal({"shared": {"tf_environment": {"project_id": "p", "region": "r", "prefix": "x"}}})
    assert msg and "--legacy" in msg and "tf apply" in msg

