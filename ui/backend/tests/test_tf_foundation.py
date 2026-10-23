"""Terraform foundation (docs/TERRAFORM_PLAN.md T1): tfvars generation, outputs -> state.json merge, plan-safety."""
import json
import re
import subprocess
from pathlib import Path

import pytest

from agdemo_core import tf_foundation as tfm
from agdemo_core import tf_safety
from agdemo_core.config import REPO_ROOT, DemoConfig
from agdemo_core.gcp import gateways as gw
from agdemo_core.gcp import model_armor as ma
from agdemo_core.gcp.project import REQUIRED_APIS
from agdemo_core.infra import common as c

TF_DIR = REPO_ROOT / "infra" / "terraform"


def make_cfg(**env):
    e = {"project_id": "demo-proj", "region": "us-east1", "resource_prefix": "agdtest", **env}
    return DemoConfig.model_validate({"environment": e,
                                      "ui": {"admin_access": ["user:p@example.com", "domain:example.com",
                                                              "group:g@example.com"]}})


# ---------------------------------------------------------------------- tfvars
def test_tfvars_names_match_bootstrap_helpers():
    cfg = make_cfg()
    v = tfm.tfvars(cfg, organization_id="123")
    assert v["project_id"] == "demo-proj" and v["region"] == "us-east1" and v["prefix"] == "agdtest"
    assert v["gateway_project_id"] == ""
    assert v["labels"] == {"app": "agent-gateway-demo", "demo-prefix": "agdtest"}
    assert v["organization_id"] == "123"
    assert v["required_apis"] == REQUIRED_APIS
    n = v["names"]
    assert n == {
        "ui_sa": "agdtest-ui", "targets_sa": "agdtest-targets", "artifact_repo": "agdtest",
        "staging_bucket": "demo-proj-agdtest-staging",
        "egress_gateway": "agdtest-egress", "ingress_gateway": "agdtest-ingress",
        "egress_iap_extension": "agdtest-egress-iap-authz", "egress_iap_policy": "agdtest-egress-iap-policy",
        "model_armor_template": "agdtest-shield", "model_armor_ext": "agdtest-ma-authz",
    }
    assert n["staging_bucket"] == c.staging_bucket(cfg)
    assert n["egress_iap_extension"] == gw.iap_extension_name(cfg, "egress")
    assert v["model_armor_block_marker"] == ma.BLOCK_MESSAGE_MARKER
    assert v["iap_enforcement"] == "ENFORCE"
    assert v["recordings_bucket"] == "demo-proj-agdtest-recordings"
    assert v["private_networking"] == {"enabled": False}


def test_tfvars_presenters_filtered_like_bootstrap():
    v = tfm.tfvars(make_cfg())
    assert v["presenters"] == ["group:g@example.com", "user:p@example.com"]   # domain: is skipped


def test_tfvars_platform_endpoints():
    cfg = make_cfg()
    v = tfm.tfvars(cfg)
    ep = v["platform_endpoints"]
    assert len(ep) == 25 == len(c.platform_endpoints(cfg))
    assert ep["https://us-east1-aiplatform.googleapis.com"] == {
        "service_id": "agdtest-plat-us-east1-aiplatform", "display_name": "agdtest us-east1-aiplatform.googleapis.com"}
    assert ep["https://logging.mtls.googleapis.com:443"]["service_id"] == "agdtest-plat-logging-mtls-443"
    for s, url in c.platform_endpoints(cfg):
        assert ep[url]["service_id"] == c.platform_service_id(cfg, s)
    assert v["platform_endpoints_existing"] == {}


def test_tfvars_existing_platform_only_known_urls():
    v = tfm.tfvars(make_cfg(), existing_platform={
        "https://logging.googleapis.com": "projects/1/locations/us-east1/endpoints/agentregistry-x",
        "https://not-a-platform.example.com": "projects/1/locations/us-east1/endpoints/y"})
    assert v["platform_endpoints_existing"] == {
        "https://logging.googleapis.com": "projects/1/locations/us-east1/endpoints/agentregistry-x"}


def test_tfvars_cross_project_and_dry_run():
    cfg = DemoConfig.model_validate({"environment": {"project_id": "a", "gateway_project_id": "b",
                                                     "region": "us-east1", "resource_prefix": "agdtest"},
                                     "gateways": {"iap_enforcement": "DRY_RUN", "egress_name": "eg"}})
    v = tfm.tfvars(cfg)
    assert v["gateway_project_id"] == "b"
    assert v["iap_enforcement"] == "DRY_RUN"
    assert v["names"]["egress_gateway"] == "eg"


def test_tfvars_shared_project_grants():
    assert tfm.tfvars(make_cfg())["shared_project_grants"] is True
    cfg = DemoConfig.model_validate({"environment": {"project_id": "p", "region": "us-east1",
                                                     "resource_prefix": "agdtest"},
                                     "terraform": {"shared_project_grants": False}})
    assert tfm.tfvars(cfg)["shared_project_grants"] is False


def test_write_tfvars(tmp_path):
    p = tfm.write_tfvars(make_cfg(), tmp_path, organization_id="9")
    assert p.name == "generated.auto.tfvars.json"
    assert json.loads(p.read_text())["organization_id"] == "9"


def test_tfvars_keys_match_root_variables():
    """Every generated variable is declared in infra/terraform/variables.tf (Terraform warns otherwise)."""
    text = (TF_DIR / "variables.tf").read_text()
    for k in tfm.tfvars(make_cfg()):
        assert f'variable "{k}"' in text, k


def test_backend_names():
    cfg = make_cfg()
    assert tfm.state_bucket(cfg) == "demo-proj-agdemo-tfstate"
    assert tfm.state_prefix(cfg) == "agdtest/demo-proj/us-east1"


# ---------------------------------------------------------------------- outputs -> state.json
BOOTSTRAP_KEYS = {"ui_sa", "targets_sa", "artifact_repo", "staging_bucket", "egress_gateway", "ingress_gateway",
                  "ingress_gateway_url", "gateway_service_agents", "egress_iap_extension", "egress_iap_policy",
                  "model_armor_template", "model_armor_extension", "platform_registry_ids"}


def tf_outputs(gw_name="agdtest-egress", recordings="demo-proj-agdtest-recordings"):
    loc = "projects/demo-proj/locations/us-east1"
    return {
        "state_shared": {"sensitive": False, "type": ["object", {}], "value": {
            "ui_sa": "agdtest-ui@demo-proj.iam.gserviceaccount.com",
            "targets_sa": "agdtest-targets@demo-proj.iam.gserviceaccount.com",
            "artifact_repo": "us-east1-docker.pkg.dev/demo-proj/agdtest",
            "staging_bucket": "gs://demo-proj-agdtest-staging",
            "egress_gateway": f"{loc}/agentGateways/{gw_name}",
            "ingress_gateway": f"{loc}/agentGateways/agdtest-ingress",
            "ingress_gateway_url": "https://us-east1-aiplatform.googleapis.com",
            "gateway_service_agents": ["service-1@gcp-sa-dep.iam.gserviceaccount.com"],
            "egress_iap_extension": f"{loc}/authzExtensions/agdtest-egress-iap-authz",
            "egress_iap_policy": f"{loc}/authzPolicies/agdtest-egress-iap-policy",
            "model_armor_template": f"{loc}/templates/agdtest-shield",
            "model_armor_extension": f"{loc}/authzExtensions/agdtest-ma-authz",
            "platform_registry_ids": {"https://logging.googleapis.com": "projects/1/locations/us-east1/endpoints/x"},
            "recordings_bucket": recordings,
        }},
        "environment": {"value": {"project_id": "demo-proj", "gateway_project_id": "demo-proj",
                                  "region": "us-east1", "prefix": "agdtest"}},
    }


def test_merge_outputs_preserves_other_keys():
    state = {"shared": {"ui_url": "https://ui.example", "egress_gateway":
                        "projects/demo-proj/locations/us-east1/agentGateways/agdtest-egress", "extra": 1},
             "themes": {"helpdesk": {"orchestrator": {"engine": "e"}}}, "code_updates": ["x"]}
    out = tfm.merge_outputs(state, tf_outputs())
    sh = out["shared"]
    assert BOOTSTRAP_KEYS <= set(sh)
    assert sh["ui_url"] == "https://ui.example" and sh["extra"] == 1
    assert out["themes"] == {"helpdesk": {"orchestrator": {"engine": "e"}}}
    assert out["code_updates"] == ["x"]
    assert sh["recordings_bucket"] == "demo-proj-agdtest-recordings"
    assert sh["tf_environment"]["region"] == "us-east1"


def test_merge_outputs_empty_state_and_no_recordings():
    out = tfm.merge_outputs({}, tf_outputs(recordings=""))
    assert BOOTSTRAP_KEYS <= set(out["shared"]) and "recordings_bucket" not in out["shared"]
    assert out["themes"] == {}


def test_merge_outputs_refuses_other_environment():
    state = {"shared": {"egress_gateway": "projects/live/locations/us-east4/agentGateways/agdemo-egress"}}
    with pytest.raises(tfm.TfError, match="another environment"):
        tfm.merge_outputs(state, tf_outputs())
    assert state["shared"] == {"egress_gateway": "projects/live/locations/us-east4/agentGateways/agdemo-egress"}
    forced = tfm.merge_outputs(state, tf_outputs(), force=True)
    assert forced["shared"]["egress_gateway"].endswith("/agdtest-egress")


def test_merge_outputs_needs_state_shared():
    with pytest.raises(tfm.TfError):
        tfm.merge_outputs({}, {})


def test_module_outputs_cover_bootstrap_keys():
    """modules/base/outputs.tf exposes exactly bootstrap's state.json keys (+ recordings_bucket)."""
    text = (TF_DIR / "modules" / "base" / "outputs.tf").read_text()
    for k in BOOTSTRAP_KEYS | {"recordings_bucket"}:
        assert re.search(rf"^\s+{k}\s+=", text, re.M), k


# ---------------------------------------------------------------------- plan-safety
def rc(addr, actions, typ="google_project_iam_member", mode="managed", importing=False):
    ch = {"actions": actions}
    if importing:
        ch["importing"] = {"id": "x"}
    return {"address": addr, "type": typ, "mode": mode, "change": ch}


SAFE = {"format_version": "1.2", "resource_changes": [
    rc("module.base.google_project_iam_member.sa[\"ui roles/iap.admin\"]", ["create"]),
    rc("module.base.google_network_services_agent_gateway.gw[\"egress\"]", ["update"],
       "google_network_services_agent_gateway"),
    rc("module.base.google_service_account.sa[\"ui\"]", ["no-op"], "google_service_account", importing=True),
    rc("data.google_project.this", ["read"], "google_project", mode="data"),
]}


def test_safety_ok():
    v = tf_safety.check(SAFE)
    assert v.ok and not v.blockers
    assert v.counts["create"] == 1 and v.counts["update"] == 1 and v.counts["import"] == 1
    assert "1 to add, 1 to change, 0 to replace, 0 to destroy, 1 to import" == v.summary()


@pytest.mark.parametrize("actions", [["delete"], ["delete", "create"], ["create", "delete"]])
def test_safety_blocks_delete_and_replace(actions):
    plan = {"resource_changes": SAFE["resource_changes"] + [
        rc('module.base.google_network_services_agent_gateway.gw["egress"]', actions,
           "google_network_services_agent_gateway")]}
    v = tf_safety.check(plan)
    assert not v.ok
    assert v.blockers == [('module.base.google_network_services_agent_gateway.gw["egress"]', actions)]


def test_safety_allow_replace_never_allows_delete():
    plan = {"resource_changes": [rc("a", ["delete", "create"]), rc("b", ["delete"])]}
    v = tf_safety.check(plan, allow_replace=True)
    assert v.allowed_replaces == ["a"] and v.blockers == [("b", ["delete"])]


def test_safety_template_swap():
    t = tf_safety.TEMPLATE_TYPE
    plan = {"resource_changes": [rc("module.pe.tmpl", ["create", "delete"], t)]}
    assert not tf_safety.check(plan).ok
    assert tf_safety.check(plan, allow_template_swap=True).ok
    # destroy-first replace of the template is still refused
    assert not tf_safety.check({"resource_changes": [rc("x", ["delete", "create"], t)]},
                               allow_template_swap=True).ok


def test_safety_cli(tmp_path, capsys):
    good, bad = tmp_path / "good.json", tmp_path / "bad.json"
    good.write_text(json.dumps(SAFE))
    bad.write_text(json.dumps({"resource_changes": [rc("x", ["delete"])]}))
    assert tf_safety.main([str(good)]) == 0
    assert tf_safety.main([str(bad)]) == 1
    assert "BLOCKED delete: x" in capsys.readouterr().out
    assert tf_safety.main([str(tmp_path / "missing.json")]) == 2


# ---------------------------------------------------------------------- hygiene of the committed Terraform
def test_no_real_identifiers_in_terraform():
    # Kept split so this file doesn't contain them either.
    banned = tuple("".join(x) for x in (("las", "lie"), ("alto", "strat"), ("3138", "6077848"),
                                         ("1776", "63386949"), ("4855", "19459960"), ("gm", "ail")))
    try:   # files git would commit (git-ignored local working files such as backend_override.tf are skipped)
        ls = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", str(TF_DIR)],
                            cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.split()
        files = [REPO_ROOT / f for f in ls]
    except (FileNotFoundError, subprocess.CalledProcessError):
        files = [p for p in TF_DIR.rglob("*") if ".terraform" not in p.parts and p.suffix in (".tf", ".hcl")]
    assert files
    for p in files:
        if p.is_file():
            text = p.read_text().lower()
            assert not any(b in text for b in banned), p


def test_terraform_fmt():
    try:
        r = subprocess.run(["terraform", "fmt", "-check", "-recursive", str(TF_DIR)], capture_output=True, text=True)
    except FileNotFoundError:
        pytest.skip("terraform not installed")
    assert r.returncode == 0, r.stdout + r.stderr


# ---------------------------------------------------------------------- iam.auth_strategy
def test_auth_strategy_default_standard_and_tfvars():
    assert make_cfg().iam.auth_strategy == "standard"
    assert tfm.tfvars(make_cfg(), organization_id="1")["auth_strategy"] == "standard"
    cfg = DemoConfig.model_validate({"environment": {"project_id": "p", "region": "us-east1",
                                                     "resource_prefix": "agdtest"},
                                     "iam": {"auth_strategy": "minimum"}})
    assert tfm.tfvars(cfg, organization_id="1")["auth_strategy"] == "minimum"
    with pytest.raises(Exception):
        DemoConfig.model_validate({"environment": {"project_id": "p", "region": "us-east1",
                                                   "resource_prefix": "x"}, "iam": {"auth_strategy": "owner"}})


def test_safety_allow_role_changes_only_iam():
    plan = {"resource_changes": [
        rc('module.base.google_project_iam_member.sa["ui roles/iap.admin"]', ["delete"]),
        rc('module.base.google_project_iam_custom_role.demo["registry_egress"]', ["delete"],
           "google_project_iam_custom_role"),
        rc('module.base.google_network_services_agent_gateway.gw["egress"]', ["delete"],
           "google_network_services_agent_gateway")]}
    assert len(tf_safety.check(plan).blockers) == 3
    v = tf_safety.check(plan, allow_role_changes=True)
    assert v.blockers == [('module.base.google_network_services_agent_gateway.gw["egress"]', ["delete"])]


def test_iam_tf_has_both_role_sets():
    t = (TF_DIR / "modules" / "base" / "iam.tf").read_text()
    for role in ("roles/aiplatform.expressUser", "RegistryEgressAdmin", "GatewayPolicyEditor",
                 "roles/iap.admin", "roles/networksecurity.editor", "roles/logging.logWriter"):
        assert role in t
    assert 'var.auth_strategy == "minimum"' in t


def test_deployer_roles(tmp_path):
    from agdemo_core.infra import deployer_roles as dr
    cfg = make_cfg(resource_prefix="ag-test")
    std = dr.grants(cfg, "standard", False, False)
    assert len(std) == 15 and all(g.condition_file is None for g in std)
    assert {"roles/aiplatform.user", "roles/iap.admin", "roles/logging.viewer"} <= {g.role for g in std}
    assert len(dr.grants(cfg, "standard", True, True)) == 18
    mn = dr.grants(cfg, "minimum", False, False, out_dir=tmp_path)
    roles = [g.role for g in mn]
    assert len(mn) == 15 and len(set(roles)) == 14 and roles.count("roles/resourcemanager.projectIamAdmin") == 2
    assert "roles/aiplatform.expressUser" in roles and "roles/iap.admin" not in roles
    assert "roles/logging.viewer" not in roles and "projects/demo-proj/roles/ag_testDeployerIap" in roles
    for g in mn[:2]:
        expr = __import__("yaml").safe_load(g.condition_file.read_text())["expression"]
        assert expr.count("'roles/") + expr.count("'projects/") <= 10       # IAM's hasOnly() limit
    assert "ag_testRegistryEgressAdmin" in (tmp_path / "deployer-condition-b.yaml").read_text()
    cmds = dr.commands(cfg, "user:a@example.com", "minimum", True, False, out_dir=tmp_path)
    assert cmds[0][1:4] == ["iam", "roles", "create"] and len(cmds) == 1 + 15 + 2
