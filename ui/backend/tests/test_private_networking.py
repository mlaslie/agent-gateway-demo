"""Phase T4 (docs/TERRAFORM_PLAN.md §7c): Private Networking mode. Terraform module plumbing, region-aware
themes, deploy arguments (internal ingress, both run.app URLs, the token-sharing env var), the private-IP
classifier, check-clean / reset --all / down across regions, the privnet theme and its Demo-mode scenarios.
No GCP and no Terraform apply: fakes and file checks only."""

import json
import re
from pathlib import Path

import httpx
import pytest

from agdemo_core import simulate
from agdemo_core import tf_foundation as tfm
from agdemo_core.config import REPO_ROOT, DemoConfig
from agdemo_core.gcp import gateway_logs as gl
from agdemo_core.infra import check_clean as cc
from agdemo_core.infra import common as c
from agdemo_core.infra import deploy_theme as d
from agdemo_core.infra import lifecycle as lc
from agdemo_core.infra import reset_all as ra
from agdemo_core.runtime_client import tool_result_to_edge_state
from agdemo_core.themes import (
    ThemeRegionError,
    edge_ids,
    load_theme,
    theme_config,
    theme_region,
    themes_in_region,
)

TF = REPO_ROOT / "infra" / "terraform"
P, N = "demo-proj", "123456789012"


def cfg(pn=True, **kw):
    data = {"environment": {"project_id": P, "region": "us-east1", "resource_prefix": "agdtest"},
            "private_networking": {"enabled": pn, "region": "us-west1"}, **kw}
    return DemoConfig.model_validate(data)


# ---------------------------------------------------------------------- config views
def test_regional_view_names():
    base = cfg()
    v = base.for_region("us-west1")
    assert (v.region, v.home_region, v.is_private_networking) == ("us-west1", "us-east1", True)
    assert v.egress_gateway == "agdtest-pn-egress" and base.egress_gateway == "agdtest-egress"
    assert v.registry_parent == f"projects/{P}/locations/us-west1"
    assert not v.has_ingress_gateway and not v.has_model_armor and base.has_model_armor
    assert [x.region for x in base.region_views()] == ["us-east1", "us-west1"]
    assert [x.region for x in cfg(pn=False).region_views()] == ["us-east1"]
    assert v.home() .region == "us-east1" and base.for_region("us-east1") is base
    with pytest.raises(ValueError):
        cfg(pn=False).for_region("us-west1")
    with pytest.raises(ValueError):          # same region as the base: one egress gateway per region
        DemoConfig.model_validate({"environment": {"project_id": P, "region": "us-west1"},
                                   "private_networking": {"enabled": True, "region": "us-west1"}})
    assert c.staging_bucket(v) == f"{P}-agdtest-staging-us-west1" and c.staging_bucket(base) == f"{P}-agdtest-staging"
    n = c.pn_names(base)
    assert n["psc_forwarding_rule"] == "agdtestpnpsc" and re.fullmatch(r"[a-z][a-z0-9]{0,19}", n["psc_forwarding_rule"])
    assert n["egress_iap_policy"] == "agdtest-pn-egress-iap-policy" and n["network"] == "agdtest-pn-vpc"
    assert any("us-west1-aiplatform" in u for _, u in c.platform_endpoints(v))
    assert base.enabled_themes() == ["helpdesk", "privnet"] and cfg(pn=False).enabled_themes() == ["helpdesk"]


def test_service_agent_roles_follow_shared_grants():
    assert cfg().manage_pn_service_agent_roles is True
    assert cfg(terraform={"shared_project_grants": False}).manage_pn_service_agent_roles is False
    x = DemoConfig.model_validate({"environment": {"project_id": P, "region": "us-east1"},
                                   "terraform": {"shared_project_grants": False},
                                   "private_networking": {"enabled": True, "manage_service_agent_roles": True}})
    assert x.manage_pn_service_agent_roles is True


# ---------------------------------------------------------------------- Terraform plumbing
def test_tfvars_private_networking():
    off = tfm.tfvars(cfg(pn=False), organization_id="0")
    assert off["private_networking"] == {"enabled": False}          # unchanged from T3: same base plan
    on = tfm.tfvars(cfg(), organization_id="0")
    pn = on["private_networking"]
    assert pn["enabled"] and pn["region"] == "us-west1" and pn["vpc_egress"] == "PRIVATE_RANGES_ONLY"
    assert pn["names"] == c.pn_names(cfg()) and len(pn["platform_endpoints"]) == len(on["platform_endpoints"])
    assert all("us-east1-" not in u for u in pn["platform_endpoints"])
    assert on["region"] == "us-east1" and on["names"]["egress_gateway"] == "agdtest-egress"   # base untouched
    # the base tfvars don't change with the mode (only the private_networking key)
    assert {k: v for k, v in on.items() if k != "private_networking"} == \
        {k: v for k, v in off.items() if k != "private_networking"}
    # a view passed in still produces the base tfvars
    assert tfm.tfvars(cfg().for_region("us-west1"), organization_id="0")["region"] == "us-east1"


def test_tfvars_match_terraform_variable():
    """Every key the generator writes is an attribute of the root variable (and names match the module)."""
    text = (TF / "variables.tf").read_text()
    block = text[text.index('variable "private_networking"'):text.index('variable "agdemo_cli"')]
    pn = tfm.tfvars(cfg(), organization_id="0")["private_networking"]
    for k in pn:
        assert re.search(rf"\b{k}\s+=", block), k
    for k in pn["names"]:
        assert re.search(rf"\b{k}\s+=\s+string", block), k
    main = (TF / "main.tf").read_text()
    for k in pn["names"]:
        assert f"local.pn.names.{k}" in main, k


def test_merge_outputs_regions():
    out = {"state_shared": {"value": {"egress_gateway": "g"}},
           "state_regions": {"value": {"us-west1": {"egress_gateway": "pn", "mode": "private_networking"}}}}
    st = tfm.merge_outputs({"shared": {}}, out)
    assert st["shared"]["regions"]["us-west1"]["egress_gateway"] == "pn"
    st = tfm.merge_outputs(st, {"state_shared": {"value": {"egress_gateway": "g"}}, "state_regions": {"value": {}}})
    assert "regions" not in st["shared"]                # mode turned off: the section goes
    st = tfm.merge_outputs({"shared": {"regions": {"x": 1}}}, {"state_shared": {"value": {"egress_gateway": "g"}}})
    assert st["shared"]["regions"] == {"x": 1}          # older state without the output: kept


def _tf(name):
    return "\n".join(p.read_text() for p in (TF / "modules" / name).glob("*.tf"))


def test_private_egress_module_rules():
    t = _tf("private_egress")
    assert 'connection_preference = "ACCEPT_AUTOMATIC"' in t and "private_ip_google_access = true" in t
    assert 'target                = "all-apis"' in t and 'purpose      = "PRIVATE_SERVICE_CONNECT"' in t
    assert 'dns_name    = "run.app."' in t and '["run.app.", "*.run.app."]' in t
    assert '"roles/compute.networkAdmin", "roles/dns.peer"' in t and "gcp-sa-agentgateway" in t
    assert "create_before_destroy = true" in t and 'deployment_model = "CENTRALIZED"' in t
    assert re.search(r'"\$\{var.names.template_prefix\}-\$\{substr\(sha1\(jsonencode\(local.template_content\)\), 0, 6\)\}"', t)
    # template body by project ID, the gateway reference by project NUMBER, ordered after the grants
    assert 'attachment       = "projects/${var.project_id}/regions/${var.region}/networkAttachments/' in t
    out = (TF / "modules/private_egress/outputs.tf").read_text()
    ref = out[out.index('output "template_ref"'):out.index('output "state"')]
    assert "projects/${var.project_number}/locations/" in ref and "depends_on = [google_project_iam_member.agentgateway]" in ref
    # plan §8 Q7: the grants wait for the base gateway
    assert "var.service_agent_after" in (TF / "modules/private_egress/service_agent.tf").read_text()
    assert "service_agent_after        = module.base.egress_gateway_id" in (TF / "main.tf").read_text()


def test_regional_module_mirrors_base():
    reg, base = _tf("regional"), _tf("base")
    assert "agent_connectivity_template = var.connectivity_template" in reg
    assert 'startswith(var.connectivity_template, "projects/${var.gateway_project_number}/")' in reg
    for rule in ("ignore_changes = [protocols]", "ignore_changes = [fail_open, metadata]",
                 "ignore_changes = [custom_provider]", 'policy_profile = "REQUEST_AUTHZ"', 'role        = "roles/iap.egressor"',
                 'protocol_binding = "JSONRPC"', 'type = "NO_SPEC"', 'iapPolicyVersion = "V1"'):
        assert rule in reg and rule in base, rule
    assert "ingress" not in (TF / "modules/regional/gateway.tf").read_text().lower().replace("no ingress", "")
    assert "model_armor" not in reg


def test_guard_covers_every_region():
    g = (TF / "guards.tf").read_text()
    assert "depends_on = [module.base, module.private_egress, module.pn_region]" in g
    assert "module.pn_region[*].egress_gateway_id" in g


def test_no_real_identifiers_in_new_files():
    # Kept split so this file doesn't contain them either.
    bad = tuple("".join(x) for x in (("alto", "strat"), ("3138", "6077848"), ("1776", "63386949"),
                                     ("4855", "19459960"), ("gm", "ail"), ("laslie", "-demo")))
    files = [*(TF / "modules/private_egress").glob("*.tf"), *(TF / "modules/regional").glob("*.tf"),
             *(REPO_ROOT / "themes/privnet").rglob("*.yaml"), Path(__file__),
             REPO_ROOT / "agdemo_core/bypass.py", REPO_ROOT / "agdemo_core/network_view.py"]
    for f in files:
        txt = f.read_text()
        assert not any(b in txt for b in bad), f


# ---------------------------------------------------------------------- region-aware themes
def test_theme_regions():
    base, pn = cfg(), load_theme("privnet")
    hd = load_theme("helpdesk")
    assert theme_region(base, hd) == "us-east1" and theme_config(base, hd) is base      # unchanged
    v = theme_config(base, pn)
    assert v.region == "us-west1" and v.egress_gateway == "agdtest-pn-egress"
    with pytest.raises(ThemeRegionError):
        theme_config(cfg(pn=False), pn)
    other = DemoConfig.model_validate({"environment": {"project_id": P, "region": "us-east1"},
                                       "private_networking": {"enabled": True, "region": "us-west2"}})
    with pytest.raises(ThemeRegionError):
        theme_config(other, pn)
    assert themes_in_region(base, ["helpdesk", "privnet", "nope"]) == ["helpdesk", "nope"]
    assert themes_in_region(base.for_region("us-west1"), ["helpdesk", "privnet"]) == ["privnet"]
    assert themes_in_region(cfg(pn=False), ["helpdesk", "privnet"]) == ["helpdesk"]


def test_privnet_theme_loads():
    t = load_theme("privnet")
    assert t.network_mode == "private_networking" and t.region == "us-west1"
    assert not t.has_ingress and not t.has_model_armor and "ingress:user" not in edge_ids(t)
    assert t.internal("payroll-mcp") and not t.internal("benefits-mcp")
    assert {x.name: x.read_only for x in t.mcp_servers["payroll-mcp"].tools} == {"get_payslip": True,
                                                                                 "update_bank_details": False}
    assert {p.type for p in t.policies} == {"gateway_attach", "mcp_tool_allow", "mcp_server_allow"}
    assert [s.order for s in t.scenarios] == [1, 2, 3, 4, 5]


def test_internal_needs_private_networking():
    from agdemo_core.themes import Theme
    data = load_theme("helpdesk").model_dump()
    data["components"][2]["ingress"] = "internal"
    with pytest.raises(ValueError):
        Theme.model_validate(data)


# ---------------------------------------------------------------------- simulator: the §7c demo steps
def _run(t, sid, tid, applied):
    sc, te = simulate.find_test(t, tid, sid)
    ev = simulate.synth_events(t, sc, te, applied, False)
    return ev[-1][1]["edges"]


def test_demo_steps():
    t = load_theme("privnet")
    s1 = _run(t, "internal-only", "bypass", set())
    assert s1["payroll-mcp:get_payslip"]["state"] == "unreachable" and s1["payroll-mcp:get_payslip"]["http_status"] == 404
    assert s1["benefits-mcp:list_plans"]["state"] == "direct"
    assert _run(t, "internal-only", "bypass", {"gw-egress"})["payroll-mcp:get_payslip"]["state"] == "unreachable"
    s1b = _run(t, "internal-only", "agent-no-gateway", set())
    assert s1b["payroll-mcp:get_payslip"]["state"] == "unreachable" and s1b["benefits-mcp:list_plans"]["state"] == "direct"
    assert _run(t, "through-gateway", "payslip-no-grant", {"gw-egress"})["payroll-mcp:get_payslip"]["http_status"] == 403
    s3 = _run(t, "registered", "registered-no-grant", {"gw-egress"})
    assert {v["state"] for v in s3.values()} == {"denied"}
    ok = _run(t, "grant", "payslip", {"gw-egress", "payroll-readonly"})["payroll-mcp:get_payslip"]
    assert ok["state"] == "allowed" and ok["path"] == "private" and "PSC endpoint" in ok["detail"]
    assert _run(t, "grant", "bank-details", {"gw-egress", "payroll-readonly"})["payroll-mcp:update_bank_details"]["state"] == "denied"
    assert _run(t, "grant", "bank-details", {"gw-egress", "payroll-all"})["payroll-mcp:update_bank_details"]["state"] == "allowed"
    s5 = _run(t, "public-contrast", "side-by-side", {"gw-egress", "payroll-readonly", "allow-benefits"})
    # run.app is DNS-peered into the VPC: through the gateway both servers take the PSC endpoint (§7h, live)
    assert s5["benefits-mcp:list_plans"]["path"] == "private" and s5["payroll-mcp:get_payslip"]["path"] == "private"
    assert _run(t, "public-contrast", "side-by-side", {"gw-egress"})["benefits-mcp:list_plans"]["state"] == "denied"


def test_internet_themes_unchanged():
    t = load_theme("helpdesk")
    ev = simulate.evaluate(t, set(), False)
    assert all("path" not in v for v in ev.values()) and {v["state"] for v in ev.values()} == {"direct"}


def test_edge_state_mapping():
    r404 = {"edge": "payroll-mcp:get_payslip", "outcome": "error", "http_status": 404, "detail": "HTTP 404"}
    assert tool_result_to_edge_state(r404, False, internal=True)["state"] == "unreachable"
    assert tool_result_to_edge_state(r404, False)["state"] == "error"          # public: still an error
    ok = tool_result_to_edge_state({"outcome": "ok", "http_status": 200}, True, path="private")
    assert ok["state"] == "allowed" and ok["path"] == "private"
    assert "path" not in tool_result_to_edge_state({"outcome": "ok", "http_status": 200}, True)


# ---------------------------------------------------------------------- private-IP classifier
@pytest.mark.parametrize("ip,private", [("10.70.250.10:443", True), ("172.16.0.5", True), ("172.31.255.1:443", True),
                                        ("192.168.1.1:443", True), ("172.32.0.1:443", False), ("74.125.1.95:443", False),
                                        ("[2607:f8b0::1]:443", False), ("", False), (None, False), ("junk", False)])
def test_private_ip(ip, private):
    assert gl.is_private_ip(ip) is private


def test_parse_entry_private_path():
    t = load_theme("privnet")
    host = f"agdtest-privnet-payroll-mcp-{N}.us-west1.run.app"
    e = {"insertId": "x", "timestamp": "2026-10-16T00:00:00Z",
         "httpRequest": {"status": 200, "requestUrl": f"https://{host}/mcp", "serverIp": "10.70.250.10:443"},
         "jsonPayload": {"enforcedGatewaySecurityPolicy": {"hostname": host},
                         "agentGatewayInfo": {"mcpInfo": {"method": "tools/call", "parameter": "get_payslip"}}}}
    out = gl.parse_entry(cfg().for_region("us-west1"), t, {host: "payroll-mcp"}, e)
    assert out["private_path"] and out["server_ip"] == "10.70.250.10:443" and out["summary"].endswith("private path")
    assert out["edge"] == "payroll-mcp:get_payslip"
    st = {"themes": {"privnet": {"components": {"payroll-mcp": {"url": "https://a-1.us-west1.run.app",
                                                                "urls": ["https://a-1.us-west1.run.app", "https://a-h-uw.a.run.app"]}}}}}
    assert gl.theme_hosts(st, t) == {"a-1.us-west1.run.app": "payroll-mcp", "a-h-uw.a.run.app": "payroll-mcp"}
    assert 'gateway_name="agdtest-pn-egress"' in gl.build_filter(cfg().for_region("us-west1"), [], "t", False)


def test_sim_log_private_path():
    import sys
    sys.path.insert(0, str(REPO_ROOT / "ui" / "backend"))
    from agdemo_ui.gateway_logs import SimLogBook
    t = load_theme("privnet")
    e = SimLogBook._entry(t, "payroll-mcp:get_payslip", "allowed", 0, "agdtest", "agdtest-pn-egress", "us-west1")
    assert e["private_path"] and e["raw"]["httpRequest"]["serverIp"].startswith("10.")
    e = SimLogBook._entry(t, "benefits-mcp:list_plans", "allowed", 0, "agdtest", "agdtest-pn-egress", "us-west1")
    assert e["private_path"] and e["raw"]["httpRequest"]["serverIp"].startswith("10.")   # public run.app also via PSC
    e = SimLogBook._entry(t, "payroll-mcp:get_payslip", "denied", 0, "agdtest", "agdtest-pn-egress", "us-west1")
    assert e["decided_by"] == "agdtest-pn-egress-iap-policy" and "serverIp" not in e["raw"]["httpRequest"]


# ---------------------------------------------------------------------- deploy arguments
def test_deploy_args(tmp_path):
    t = load_theme("privnet")
    v = theme_config(cfg(), t)
    a = d.service_args(v, t, "payroll-mcp", tmp_path / "x.env")
    assert "--ingress=internal" in a and "--region=us-west1" in a
    assert any(x.startswith("--image=us-east1-docker.pkg.dev/") for x in a)          # images: base repo
    b = d.service_args(v, t, "benefits-mcp", tmp_path / "x.env")
    assert not any(x.startswith("--ingress") for x in b)
    hd = load_theme("helpdesk")
    h = d.service_args(cfg(), hd, "tickets-mcp", tmp_path / "x.env")
    assert not any(x.startswith("--ingress") for x in h) and "--region=us-east1" in h   # unchanged
    env = d.engine_env(v, t, {"MODEL": "m"})
    assert env["GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES"] == "false" and env["MODEL"] == "m"
    assert d.engine_env(cfg(), hd, {"MODEL": "m"}) == {"MODEL": "m"}


def test_register_both_urls(monkeypatch):
    from agdemo_core.gcp import registry
    seen = {}
    monkeypatch.setattr(registry, "ensure_service", lambda r, parent, sid, body: seen.update(parent=parent, body=body)
                        or {"registryResource": "rr", "name": "n"})
    t = load_theme("privnet")
    v = theme_config(cfg(), t)
    u1, u2 = f"https://agdtest-privnet-payroll-mcp-{N}.us-west1.run.app", "https://agdtest-privnet-payroll-mcp-h-uw.a.run.app"
    info = d.register_component(v, None, t, "payroll-mcp", u1, [u1, u2])
    assert seen["parent"] == f"projects/{P}/locations/us-west1"
    assert [i["url"] for i in seen["body"]["interfaces"]] == [f"{u1}/mcp", f"{u2}/mcp"]
    assert info["registry_urls"] == [f"{u1}/mcp", f"{u2}/mcp"]


def test_service_urls(monkeypatch):
    from agdemo_core.gcp import gcloud
    monkeypatch.setattr(d, "project_number", lambda p: N)
    monkeypatch.setattr(gcloud, "value", lambda args: "https://svc-h-uw.a.run.app")
    v = cfg().for_region("us-west1")
    assert d.service_urls(v, "svc") == [f"https://svc-{N}.us-west1.run.app", "https://svc-h-uw.a.run.app"]
    monkeypatch.setattr(gcloud, "value", lambda args: "")
    assert d.service_urls(v, "svc") == [f"https://svc-{N}.us-west1.run.app"]


def test_engine_urls_follow_engine_region():
    from agdemo_core.gcp import engines
    e = f"projects/{N}/locations/us-west1/reasoningEngines/1"
    assert engines.engine_url(cfg(), e).startswith("https://us-west1-aiplatform.googleapis.com")


def test_gateway_attach_uses_pn_gateway(monkeypatch):
    from agdemo_core.gcp import engines
    from agdemo_core.policies import Ctx, get_handler
    calls = []
    monkeypatch.setattr(engines, "set_gateways", lambda r, c_, e, p, g: calls.append((c_.region, e, p, g)))
    monkeypatch.setattr("agdemo_core.policies._live.rest", lambda p: None)
    t = load_theme("privnet")
    st = {"themes": {"privnet": {"orchestrator": {"engine": f"projects/{N}/locations/us-west1/reasoningEngines/1"}}}}
    get_handler("gateway_attach").apply(Ctx(config=cfg(), state=st), t, t.policy("gw-egress"))
    assert calls == [("us-west1", st["themes"]["privnet"]["orchestrator"]["engine"], "egress",
                      f"projects/{P}/locations/us-west1/agentGateways/agdtest-pn-egress")]


# ---------------------------------------------------------------------- bypass probe (Live, mocked HTTP)
def test_bypass_probe():
    import asyncio

    from agdemo_core import bypass

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404 if "payroll" in req.url.host else 200, json={"jsonrpc": "2.0", "result": {}})

    tr = httpx.MockTransport(handler)
    st = asyncio.run(bypass.bypass("payroll-mcp:get_payslip", ["https://payroll-1.run.app", "https://payroll-h.a.run.app"],
                                   "get_payslip", {}, True, transport=tr))
    assert st["state"] == "unreachable" and st["http_status"] == 404 and st["detail"].count("HTTP 404") == 2
    st = asyncio.run(bypass.bypass("benefits-mcp:list_plans", ["https://benefits-1.run.app"], "list_plans", {}, False,
                                   transport=tr))
    assert st["state"] == "direct" and not st["governed"]


# ---------------------------------------------------------------------- check-clean / reset --all / down across regions
def test_check_clean_runs_every_region(monkeypatch):
    seen = []

    def fake(v, r, state):
        seen.append(v.region)
        return cc.Snapshot(), [cc.Finding(cc.BLOCK, "engine", f"engine e-{v.region}")]

    monkeypatch.setattr(cc, "run_region", fake)
    _, f = cc.run(cfg(), None, {})
    assert seen == ["us-east1", "us-west1"]
    assert {x.resource for x in f} == {"engine e-us-east1", "[us-west1] engine e-us-west1"}
    seen.clear()
    cc.run(cfg(pn=False), None, {})
    assert seen == ["us-east1"]


def test_check_clean_pn_classify():
    v = cfg().for_region("us-west1")
    pn_gw = f"projects/{P}/locations/us-west1/agentGateways/agdtest-pn-egress"
    snap = cc.Snapshot(engines=[{"name": f"projects/{N}/locations/us-west1/reasoningEngines/9",
                                 "displayName": "agdtest-privnet-orchestrator", "labels": {"demo-prefix": "agdtest"},
                                 "spec": {"deploymentSpec": {"agentGatewayConfig": {
                                     "agentToAnywhereConfig": {"agentGateway": pn_gw}}}}}])
    state = {"shared": {"tf_environment": {"project_id": P, "region": "us-east1", "prefix": "agdtest"},
                        "egress_gateway": f"projects/{P}/locations/us-east1/agentGateways/agdtest-egress"},
             "themes": {"privnet": {}}}
    f = cc.classify(v, state, snap, cc.Derived(principal_set="ps"))
    assert ("binding", cc.BLOCK) in {(x.category, x.level) for x in f}
    assert not any(x.category == "state" for x in f)      # state.json belongs to this environment; stale-themes WARN only once
    assert cc.state_describes(v, state)


def test_collect_skips_project_wide_checks_in_other_regions(monkeypatch):
    class R:
        def __init__(self):
            self.urls = []

        def list_all(self, url, key, params=None):
            self.urls.append(url)
            return []

    r = R()
    v = cfg(gemini_enterprise={"app_id": "x"}).for_region("us-west1")
    import sys
    monkeypatch.setattr(cc.iap, "get_policy", lambda r_, u: {})
    monkeypatch.setattr(sys.modules["agdemo_core.gcp.rest"], "project_number", lambda p: N)
    snap = cc.collect(v, r, {})
    assert all("us-west1" in u for u in r.urls) and snap.ge_agents is None
    assert not any("serviceAccounts" in u for u in r.urls)


class FakeOps:
    def __init__(self, region, themes):
        self.region, self._themes, self.calls = region, themes, []

    def state(self):
        return {}

    def themes(self):
        return self._themes

    def reset_theme(self, tid):
        self.calls.append(("reset_theme", self.region, tid))
        return [], []

    def model_armor_on(self):
        return False

    def snapshot(self):
        return cc.Snapshot()

    def derived(self):
        return cc.Derived(principal_set="ps")

    def wait(self, engs):
        return []


def test_reset_all_every_region():
    made = []

    def make(v=None):
        o = FakeOps(v.region if v else "base", ["privnet"] if v else ["helpdesk"])
        made.append(o)
        return o

    rep = ra.run_regions(cfg(), make, log=lambda m: None)
    assert [o.region for o in made] == ["base", "us-west1"] and rep.ok
    assert [c_ for o in made for c_ in o.calls] == [("reset_theme", "base", "helpdesk"),
                                                    ("reset_theme", "us-west1", "privnet")]
    made.clear()
    ra.run_regions(cfg(pn=False), make, log=lambda m: None)
    assert [o.region for o in made] == ["base"]


def test_live_ops_theme_filter(monkeypatch):
    st = {"themes": {"helpdesk": {}, "privnet": {}}}
    monkeypatch.setattr(ra.LiveOps, "state", lambda self: st)
    assert ra.LiveOps(cfg(), r=object()).themes() == ["helpdesk"]
    assert ra.LiveOps(cfg().for_region("us-west1"), r=object()).themes() == ["privnet"]
    assert ra.LiveOps(cfg().for_region("us-west1"), r=object()).model_armor_on() is False


def test_teardown_plan_lines_and_shared_pn_grants():
    p = lc.TeardownPlan(run_services=["x/agdtest-a"], others=[lc.TeardownPlan(region="us-west1", run_services=["y/agdtest-b"])])
    assert p.lines() == ["Cloud Run agdtest-a", "[us-west1] Cloud Run agdtest-b"]
    addrs = [f'{lc.PN_AGENT_ADDR}["roles/dns.peer"]', 'module.base.google_project_iam_member.presenters["user:x"]']
    assert lc.shared_grant_addresses(addrs, False) == [addrs[1]]
    assert sorted(lc.shared_grant_addresses(addrs, False, pn_agent_shared=True)) == sorted(addrs)


def test_down_teardown_other_regions_first(monkeypatch):
    calls = []
    monkeypatch.setattr(lc, "teardown", lambda v, r, plan, log=None, clear_state=True:
                        calls.append((v.region, clear_state)))
    ops = lc.LiveDownOps(cfg(), r=object())
    ops.teardown(lc.TeardownPlan(others=[lc.TeardownPlan(region="us-west1")]))
    assert calls == [("us-west1", False), ("us-east1", True)]


# ---------------------------------------------------------------------- the UI API in Demo mode
@pytest.fixture
def client(monkeypatch, tmp_path):
    import sys
    sys.path.insert(0, str(REPO_ROOT / "ui" / "backend"))
    p = tmp_path / "demo.yaml"
    p.write_text(json.dumps({"environment": {"project_id": "demo-proj", "region": "us-east4", "resource_prefix": "agdemo"},
                             "themes": {"enabled": ["helpdesk"]}, "private_networking": {"enabled": True}}))
    monkeypatch.setenv("AGDEMO_CONFIG", str(p))
    monkeypatch.setenv("AGDEMO_STATE", str(tmp_path / "state.json"))
    monkeypatch.setenv("AGDEMO_PACE", "0")
    from agdemo_ui import settings
    settings.reset_caches()
    from agdemo_ui.main import app
    from fastapi.testclient import TestClient
    yield TestClient(app)
    settings.reset_caches()


def test_api_config_and_network(client):
    c_ = client.get("/api/config").json()
    th = {t["id"]: t for t in c_["themes"]}
    assert th["privnet"]["network_mode"] == "private_networking" and th["privnet"]["region"] == "us-west1"
    assert th["helpdesk"]["region"] == "us-east4" and th["privnet"]["model_armor"] is False
    assert c_["private_networking"] == {"enabled": True, "region": "us-west1"}
    n = client.get("/api/themes/privnet/network?mode=demo").json()
    assert n["source"] == "config" and n["pieces"]["psc_ip"] == "10.70.250.10"
    assert n["pieces"]["gateway"].endswith("/agentGateways/agdemo-pn-egress") and n["internal_components"] == ["payroll-mcp"]
    assert client.get("/api/themes/helpdesk/network").json()["pieces"] is None
    st = client.get("/api/themes/privnet/state?mode=demo").json()
    assert st["model_armor"]["available"] is False and "ingress:user" not in st["edges"]
    v = client.post("/api/themes/privnet/verify", json={"mode": "demo"}).json()
    assert v["ok"] and not any(x["id"] == "model-armor" for x in v["checks"])
    g = client.get("/api/themes/privnet").json()
    assert g["region"] == "us-west1" and not any(x["id"] == "user" for x in g["nodes"])


def test_network_view_from_state():
    from agdemo_core import network_view
    t = load_theme("privnet")
    st = {"shared": {"regions": {"us-west1": {"egress_gateway": "gw", "private_networking": {"psc_ip": "10.1.2.3"}}}}}
    n = network_view.view(cfg(), st, t, live=True)
    assert n["source"] == "state" and n["pieces"] == {"psc_ip": "10.1.2.3", "gateway": "gw"}
    assert network_view.view(None, None, t)["source"] == "placeholder"
