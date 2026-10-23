"""Phase T3 (docs/TERRAFORM_PLAN.md §4): check-clean classification, reset --all planning and sequencing,
up/down ordering, write-command safety, the down confirmation, shared grants and the destroy guard.
Everything runs on fakes: no GCP and no Terraform calls."""

import json
import re

import pytest
from typer.testing import CliRunner

from agdemo_core import cli
from agdemo_core import tf_safety
from agdemo_core.config import REPO_ROOT, DemoConfig
from agdemo_core.infra import check_clean as cc
from agdemo_core.infra import common as c
from agdemo_core.infra import lifecycle as lc
from agdemo_core.infra import reset_all as ra
from agdemo_core.infra import status as st

TF_DIR = REPO_ROOT / "infra" / "terraform"
P, N, R, X = "demo-proj", "123456789012", "us-east1", "agdtest"
LOC = f"projects/{P}/locations/{R}"
PSET = (
    f"principalSet://agents.global.org-000000000000.system.id.goog/attribute.platformContainer/aiplatform/projects/{N}"
)
LABELS = {"app": "agent-gateway-demo", "demo-prefix": X}
EG, IG = f"{LOC}/agentGateways/{X}-egress", f"{LOC}/agentGateways/{X}-ingress"
IAP_EXT = f"{LOC}/authzExtensions/{X}-egress-iap-authz"


def make_cfg(**kw):
    env = {"project_id": P, "region": R, "resource_prefix": X}
    return DemoConfig.model_validate({"environment": env, **kw})


CFG = make_cfg()
DER = cc.Derived(principal_set=PSET)


def engine(eid, display, egress=None, ingress=None, labels=LABELS):
    agc = {}
    if egress:
        agc["agentToAnywhereConfig"] = {"agentGateway": egress}
    if ingress:
        agc["clientToAgentConfig"] = {"agentGateway": ingress}
    return {
        "name": f"projects/{N}/locations/{R}/reasoningEngines/{eid}",
        "displayName": display,
        "labels": labels,
        "spec": {
            "effectiveIdentity": f"agents.global.org-0.system.id.goog/resources/aiplatform/e{eid}",
            "deploymentSpec": {"agentGatewayConfig": agc},
        },
    }


def state_with(theme="retail", eid="111"):
    return {
        "shared": {},
        "themes": {
            theme: {
                "orchestrator": {
                    "engine": f"projects/{N}/locations/{R}/reasoningEngines/{eid}",
                    "principal": f"principal://p{eid}",
                },
                "components": {
                    "pricing-agent": {"url": "https://x", "registry": f"projects/{N}/locations/{R}/agents/a1"}
                },
            }
        },
    }


def plat_sid():
    s, _ = c.platform_endpoints(CFG)[0]
    return c.platform_service_id(CFG, s)


def svc(sid, rr):
    return {"name": f"{LOC}/services/{sid}", "registryResource": rr}


def iap_ext(fail_open=False, md=None):
    return {"name": IAP_EXT, "labels": LABELS, "failOpen": fail_open, "metadata": md or {"iapPolicyVersion": "V1"}}


def foundation_snapshot():
    """What a clean environment looks like right after `up` (+ reset): only foundation resources."""
    rr = f"projects/{N}/locations/{R}/endpoints/plat1"
    return cc.Snapshot(
        registry_services=[svc(plat_sid(), rr)],
        iap_policies={rr: {"bindings": [{"role": "roles/iap.egressor", "members": [PSET]}]}},
        registry_root_policy={"bindings": []},
        authz_policies={
            P: [
                {
                    "name": f"{LOC}/authzPolicies/{X}-egress-iap-policy",
                    "policyProfile": "REQUEST_AUTHZ",
                    "target": {"resources": [EG]},
                    "labels": LABELS,
                    "customProvider": {"authzExtension": {"resources": [IAP_EXT]}},
                }
            ]
        },
        authz_extensions={P: [iap_ext(), {"name": f"{LOC}/authzExtensions/{X}-ma-authz", "labels": LABELS}]},
        ma_templates=[{"name": f"{LOC}/templates/{X}-shield", "labels": LABELS}],
        ge_agents=[],
    )


def cats(findings, level=None):
    return sorted((f.category, f.resource) for f in findings if level is None or f.level == level)


# ---------------------------------------------------------------------- check-clean classification
def test_foundation_only_is_clean_even_strict():
    f = cc.classify(CFG, {"shared": {}, "themes": {}}, foundation_snapshot(), DER)
    assert f == [] and not cc.failed(f, strict=True)


def test_engine_bindings_any_owner_block():
    snap = foundation_snapshot()
    # number-form gateway names (as the engine API returns them) still match
    snap.engines = [
        engine("111", f"{X}-retail-orchestrator", egress=f"projects/{N}/locations/{R}/agentGateways/{X}-egress"),
        engine("222", "someone-else", ingress=IG, labels={}),
        engine("333", "other-gw", egress=f"{LOC}/agentGateways/other"),
    ]
    f = cc.classify(CFG, state_with(), snap, DER)
    blocks = cats(f, cc.BLOCK)
    assert ("binding", "engine agdtest-retail-orchestrator (111)") in blocks
    assert ("binding", "engine someone-else (222)") in blocks
    assert ("engine", "engine agdtest-retail-orchestrator (111)") in blocks
    assert not any("333" in r for _, r in blocks)  # bound to another gateway: not ours to judge
    assert not any(r == "engine someone-else (222)" for k, r in blocks if k == "engine")


def test_prefix_engine_needs_label_unless_in_state():
    snap = foundation_snapshot()
    snap.engines = [engine("444", f"{X}-x", labels={}), engine("555", f"{X}-y")]
    blocks = cats(cc.classify(CFG, {"themes": {}}, snap, DER), cc.BLOCK)
    assert ("engine", f"engine {X}-y (555)") in blocks and not any("444" in r for _, r in blocks)


def test_cloud_run_ui_registry_and_grants():
    snap = foundation_snapshot()
    snap.run_services = [
        {"name": f"{LOC}/services/{X}-retail-pricing-agent", "labels": LABELS},
        {"name": f"{LOC}/services/{X}-ui", "labels": LABELS},
        {"name": f"{LOC}/services/{X}-unlabelled"},
        {"name": f"{LOC}/services/policy-studio", "labels": {}},
    ]
    theme_rr = f"projects/{N}/locations/{R}/agents/a1"
    plat_rr = f"projects/{N}/locations/{R}/endpoints/plat1"
    snap.registry_services += [
        svc(f"{X}-retail-pricing-agent", theme_rr),
        svc(f"{X}-ps-made", ""),
        svc("not-ours", "projects/x/locations/r/agents/z"),
    ]
    snap.iap_policies[theme_rr] = {"bindings": [{"role": "roles/iap.egressor", "members": ["principal://p111"]}]}
    snap.iap_policies[plat_rr]["bindings"].append({"role": "roles/iap.egressor", "members": ["user:a@example.com"]})
    f = cc.classify(CFG, state_with(), snap, DER)
    assert ("cloud_run", f"Cloud Run {X}-retail-pricing-agent") in cats(f, cc.BLOCK)
    assert ("ui", f"Cloud Run {X}-ui") in cats(f, cc.WARN)
    assert ("cloud_run", f"Cloud Run {X}-unlabelled") in cats(f, cc.WARN)
    assert not any("policy-studio" in r for _, r in cats(f))
    reg = [x for x in f if x.category == "registry"]
    assert {x.resource for x in reg} == {f"registry {X}-retail-pricing-agent", f"registry {X}-ps-made"}
    assert any("Policy Studio" in x.detail for x in reg if x.resource.endswith("ps-made"))
    grants = [x for x in f if x.category == "grant"]
    assert {(x.resource, x.detail) for x in grants} == {
        (f"registry {X}-retail-pricing-agent", "roles/iap.egressor for principal://p111"),
        (f"registry {plat_sid()}", "roles/iap.egressor for user:a@example.com"),
    }  # the principalSet is fine


def test_registry_wide_grants_ours_block_foreign_info():
    snap = foundation_snapshot()
    snap.registry_root_policy = {
        "bindings": [{"role": "roles/iap.egressor", "members": ["principal://p111", "user:o@example.com"]}]
    }
    f = cc.classify(CFG, state_with(), snap, DER)
    g = {(x.level, x.detail) for x in f if x.category == "grant"}
    assert (cc.BLOCK, "roles/iap.egressor for our agent principal://p111") in g
    assert any(lv == cc.INFO and "o@example.com" in d for lv, d in g)


def test_authz_policies_extensions_templates_drift_legacy():
    snap = foundation_snapshot()
    pols = snap.authz_policies[P]
    pols[0]["customProvider"]["authzExtension"]["resources"] = [f"{LOC}/authzExtensions/{X}-egress-copy"]
    pols += [
        {
            "name": f"{LOC}/authzPolicies/{X}-egress-ma-policy",
            "policyProfile": "CONTENT_AUTHZ",
            "target": {"resources": [EG]},
            "labels": LABELS,
        },
        {"name": f"{LOC}/authzPolicies/ps-content", "policyProfile": "CONTENT_AUTHZ", "target": {"resources": [IG]}},
        {
            "name": f"{LOC}/authzPolicies/{X}-extra",
            "policyProfile": "REQUEST_AUTHZ",
            "target": {"resources": [EG]},
            "labels": LABELS,
        },
        {
            "name": f"{LOC}/authzPolicies/{X}-ingress-iap-policy",
            "policyProfile": "REQUEST_AUTHZ",
            "target": {"resources": [IG]},
            "labels": LABELS,
        },
        {
            "name": f"{LOC}/authzPolicies/unrelated",
            "policyProfile": "REQUEST_AUTHZ",
            "target": {"resources": [f"{LOC}/agentGateways/z"]},
        },
    ]
    snap.authz_extensions[P][0] = iap_ext(True, {"iapPolicyVersion": "V1", "iamEnforcementMode": "DRY_RUN"})
    snap.authz_extensions[P] += [
        {"name": f"{LOC}/authzExtensions/{X}-egress-copy", "labels": LABELS},
        {"name": f"{LOC}/authzExtensions/{X}-ingress-iap-authz", "labels": LABELS},
    ]
    snap.ma_templates.append({"name": f"{LOC}/templates/{X}-ps-template", "labels": LABELS})
    snap.legacy_sas = [f"{X}-denied-caller@{P}.iam.gserviceaccount.com"]
    f = cc.classify(CFG, {"themes": {}}, snap, DER)
    b, w = cats(f, cc.BLOCK), cats(f, cc.WARN)
    assert ("model_armor", f"authz policy {X}-egress-ma-policy") in b
    assert ("model_armor", "authz policy ps-content") in b
    assert ("authz_policy", f"authz policy {X}-extra") in b
    assert ("authz_extension", f"authz extension {X}-egress-copy ({P})") in b
    assert ("ma_template", f"Model Armor template {X}-ps-template") in b
    assert ("drift", f"authz policy {X}-egress-iap-policy") in w
    assert ("drift", f"authz extension {X}-egress-iap-authz") in w
    assert ("legacy", f"authz policy {X}-ingress-iap-policy") in w
    assert ("legacy", f"authz extension {X}-ingress-iap-authz") in w
    assert ("legacy", f"service account {X}-denied-caller@{P}.iam.gserviceaccount.com") in w
    assert not any("unrelated" in r for _, r in cats(f))


def test_ge_state_and_errors_are_warnings():
    snap = foundation_snapshot()
    snap.engines = [engine("111", f"{X}-retail-orchestrator")]
    eng = f"projects/{N}/locations/{R}/reasoningEngines"
    snap.ge_agents = [
        {
            "name": "a/1",
            "displayName": "Store Ops Agent (Agent Gateway demo)",
            "adkAgentDefinition": {"provisionedReasoningEngine": {"reasoningEngine": f"{eng}/111"}},
        },
        {
            "name": "a/2",
            "displayName": "Old (Agent Gateway demo)",
            "adkAgentDefinition": {"provisionedReasoningEngine": {"reasoningEngine": f"{eng}/999"}},
        },
        {"name": "a/3", "displayName": "Someone's agent"},
    ]
    snap.errors = {"cloud_run": "GcpError: HTTP 403"}
    f = cc.classify(CFG, state_with(), snap, DER)
    assert ("gemini_enterprise", "GE agent Store Ops Agent (Agent Gateway demo)") in cats(f, cc.WARN)
    assert ("gemini_enterprise", "GE agent Old (Agent Gateway demo)") in cats(f, cc.INFO)
    assert ("state", "config/state.json") in cats(f, cc.WARN)
    assert ("check_error", "cloud_run") in cats(f, cc.WARN)


def test_failed_default_vs_strict_and_expect():
    warn = [cc.Finding(cc.WARN, "ui", "x")]
    info = [cc.Finding(cc.INFO, "grant", "x")]
    assert not cc.failed(warn, strict=False) and cc.failed(warn, strict=True)
    assert not cc.failed(info, strict=True)
    assert cc.failed([cc.Finding(cc.BLOCK, "engine", "x")], strict=False)
    assert cc.expect_matches(CFG, None) is None
    assert cc.expect_matches(CFG, f"{P}/{R}/{X}") is None
    assert "AGDEMO_CONFIG" in cc.expect_matches(CFG, f"{P}/us-east4/agdemo")


# ---------------------------------------------------------------------- reset --all: planning (pure)
def leftovers_snapshot():
    snap = foundation_snapshot()
    pols = snap.authz_policies[P]
    pols[0]["customProvider"]["authzExtension"]["resources"] = [f"{LOC}/authzExtensions/{X}-egress-copy"]
    pols += [
        {
            "name": f"{LOC}/authzPolicies/{X}-egress-ma-policy",
            "policyProfile": "CONTENT_AUTHZ",
            "target": {"resources": [EG]},
            "labels": LABELS,
        },
        {
            "name": f"{LOC}/authzPolicies/{X}-ps-ma",
            "policyProfile": "CONTENT_AUTHZ",
            "target": {"resources": [IG]},
            "labels": LABELS,
        },
        {
            "name": f"{LOC}/authzPolicies/{X}-unlabelled",
            "policyProfile": "CONTENT_AUTHZ",
            "target": {"resources": [IG]},
        },
        {
            "name": f"{LOC}/authzPolicies/{X}-ingress-iap-policy",
            "policyProfile": "REQUEST_AUTHZ",
            "target": {"resources": [IG]},
            "labels": LABELS,
            "customProvider": {"authzExtension": {"resources": [f"{LOC}/authzExtensions/{X}-ingress-iap-authz"]}},
        },
        {
            "name": f"{LOC}/authzPolicies/foreign",
            "policyProfile": "REQUEST_AUTHZ",
            "target": {"resources": [EG]},
            "customProvider": {"authzExtension": {"resources": [f"{LOC}/authzExtensions/{X}-held"]}},
        },
    ]
    snap.authz_extensions[P][0] = iap_ext(True, {"iapPolicyVersion": "V1", "iamEnforcementMode": "DRY_RUN"})
    snap.authz_extensions[P] += [
        {"name": f"{LOC}/authzExtensions/{X}-egress-copy", "labels": LABELS},
        {"name": f"{LOC}/authzExtensions/{X}-held", "labels": LABELS},
        {"name": f"{LOC}/authzExtensions/{X}-ingress-iap-authz", "labels": LABELS},
    ]
    theme_rr, plat_rr = f"projects/{N}/locations/{R}/agents/a1", f"projects/{N}/locations/{R}/endpoints/plat1"
    snap.registry_services.append(svc(f"{X}-retail-pricing-agent", theme_rr))
    snap.iap_policies[theme_rr] = {
        "bindings": [{"role": "roles/iap.egressor", "members": ["principal://p111"], "condition": {"expression": "x"}}]
    }
    snap.iap_policies[plat_rr]["bindings"] = [{"role": "roles/iap.egressor", "members": [PSET, "principal://p111"]}]
    snap.registry_root_policy = {
        "bindings": [{"role": "roles/iap.egressor", "members": ["principal://p111", "user:o@example.com"]}]
    }
    snap.engines = [
        engine("111", f"{X}-retail-orchestrator", egress=EG),
        engine("666", f"{X}-stray", ingress=IG),
        engine("777", "foreign", egress=EG, labels={}),
    ]
    return snap


def test_plan_leftovers_actions_and_reports():
    acts, rep = ra.plan_leftovers(CFG, state_with(), leftovers_snapshot(), DER, touched={"111"})
    got = [(a.kind, a.target) for a in acts]
    assert sorted(got) == sorted(
        [
            ("delete_policy", f"{X}-ps-ma"),
            ("repoint_iap", f"{X}-egress-iap-policy"),
            ("restore_enforcement", f"{X}-egress-iap-authz"),
            ("delete_extension", f"{X}-egress-copy"),
            ("prune_iam", f"registry {plat_sid()}"),
            ("prune_iam", f"registry {X}-retail-pricing-agent"),
            ("prune_iam", f"registry-wide ({R})"),
            ("unbind", f"engine {X}-stray"),
        ]
    )
    kinds = [a.kind for a in acts]
    assert kinds == sorted(kinds, key=ra.ORDER.index)  # policies before their extensions, etc.
    rp = next(a for a in acts if a.kind == "repoint_iap")
    assert rp.args["extension"] == IAP_EXT and rp.args["labels"] == LABELS  # labels re-sent with the PATCH
    root = next(a for a in acts if a.target.startswith("registry-wide"))
    assert "principal://p111" in root.args["ours"]  # state.json principals + engines' identities
    assert all("o@example.com" not in m for m in root.args["ours"])
    text = "\n".join(rep)
    assert f"{X}-unlabelled" in text and "foreign" in text and f"{X}-held" in text
    assert (
        f"legacy authz policy {X}-ingress-iap-policy" in text
        and f"legacy authz extension {X}-ingress-iap-authz" in text
    )
    assert "user:o@example.com" in text and "777" in text
    assert not any(a.target == f"{X}-egress-ma-policy" for a in acts)  # step 2 (Model Armor off) owns it
    assert not any("111" in a.target or a.args.get("engine", "").endswith("/111") for a in acts if a.kind == "unbind")


def test_plan_leftovers_clean_is_empty_idempotent():
    acts, rep = ra.plan_leftovers(CFG, {"themes": {}}, foundation_snapshot(), DER)
    assert acts == [] and rep == []


def test_plan_leftovers_never_prunes_platform_without_principal_set():
    acts, rep = ra.plan_leftovers(CFG, state_with(), leftovers_snapshot(), cc.Derived(principal_set=""))
    assert not any(a.target == f"registry {plat_sid()}" for a in acts)
    assert any("principalSet is unknown" in x for x in rep)


def test_prune_keeps_only_the_platform_egressor():
    pol = {
        "bindings": [
            {"role": "roles/iap.egressor", "members": [PSET, "user:a@example.com"]},
            {"role": "roles/iap.egressor", "members": [PSET], "condition": {"expression": "c"}},
            {"role": "roles/iap.admin", "members": ["user:b@example.com"]},
        ]
    }
    kept, gone = ra.prune(pol, ra.keep_rule("platform", PSET))
    assert kept == [{"role": "roles/iap.egressor", "members": [PSET]}]
    assert len(gone) == 3  # incl. the conditional principalSet binding
    assert ra.prune(pol, ra.keep_rule("entry"))[0] == []
    kept, _ = ra.prune(pol, ra.keep_rule("root", ours=frozenset({PSET})))
    assert all(PSET not in b["members"] for b in kept) and len(kept) == 2


# ---------------------------------------------------------------------- reset --all: sequencing
class FakeResetOps:
    def __init__(self, snaps, themes=("helpdesk", "retail"), ma_on=True, fail=None):
        self.calls, self.snaps, self._themes, self.ma_on, self.fail = (
            [],
            list(snaps),
            list(themes),
            ma_on,
            fail or set(),
        )

    def state(self):
        return state_with()

    def themes(self):
        return self._themes

    def plan_theme(self, tid):
        self.calls.append(("plan_theme", tid))
        return ["p1 (applied)"]

    def reset_theme(self, tid):
        self.calls.append(("reset_theme", tid))
        return ([f"projects/{N}/locations/{R}/reasoningEngines/111"] if tid == "retail" else []), (
            ["boom"] if tid in self.fail else []
        )

    def model_armor_on(self):
        return self.ma_on

    def model_armor_off(self):
        self.calls.append(("model_armor_off",))
        self.ma_on = False

    def snapshot(self):
        self.calls.append(("snapshot",))
        return self.snaps.pop(0) if len(self.snaps) > 1 else self.snaps[0]

    def derived(self):
        return DER

    def execute(self, a):
        self.calls.append(("execute", a.kind, a.target))

    def wait(self, engs):
        self.calls.append(("wait", tuple(sorted(e.split("/")[-1] for e in engs))))
        return []


def test_reset_all_order():
    ops = FakeResetOps([leftovers_snapshot()])
    rep = ra.run(CFG, ops, log=lambda m: None)
    names = [x[0] for x in ops.calls]
    assert names[:2] == ["reset_theme", "reset_theme"] and names[2] == "model_armor_off" and names[3] == "snapshot"
    ex = [x for x in ops.calls if x[0] == "execute"]
    assert [x[1] for x in ex] == sorted([x[1] for x in ex], key=ra.ORDER.index)
    assert ops.calls[-1] == ("wait", ("111", "666"))  # theme engines + the stray engine it unbound
    assert rep.ok and rep.reported


def test_reset_all_idempotent_and_dry_run():
    ops = FakeResetOps([foundation_snapshot()], ma_on=False)
    rep = ra.run(CFG, ops, log=lambda m: None)
    assert not [x for x in ops.calls if x[0] in ("execute", "model_armor_off")] and rep.ok
    assert ops.calls[-1] == ("wait", ("111",))
    dry = FakeResetOps([leftovers_snapshot()])
    out = []
    ra.run(CFG, dry, dry_run=True, log=out.append)
    assert not [x for x in dry.calls if x[0] in ("execute", "reset_theme", "model_armor_off", "wait")]
    assert any("would delete policy" in m for m in out) and any("would remove retail/p1" in m for m in out)


def test_reset_all_reports_errors_and_keeps_going():
    ops = FakeResetOps([foundation_snapshot()], fail={"helpdesk"})
    rep = ra.run(CFG, ops, log=lambda m: None)
    assert not rep.ok and "boom" in rep.errors
    assert ("reset_theme", "retail") in ops.calls and ("model_armor_off",) in ops.calls


def test_wait_engines_timeout_and_failures(monkeypatch):
    seq = {"a": [{"done": False}, None], "b": [{"done": True, "error": "bad"}], "c": [{"done": False}] * 50}
    monkeypatch.setattr(st.engines, "inflight", lambda r, cfg, e: seq[e].pop(0) if len(seq[e]) > 1 else seq[e][0])
    t = [0.0]

    def sleep(s):
        t[0] += s

    errs = st.wait_engines(
        CFG, None, ["a", "b", "c", "a"], timeout=60, interval=20, log=lambda m: None, sleep=sleep, clock=lambda: t[0]
    )
    assert any("b: gateway PATCH failed: bad" in e for e in errs)
    assert any("still updating" in e and "c" in e for e in errs) and not any(e.startswith("a") for e in errs)


def test_reset_theme_one_detach_per_theme(monkeypatch):
    from agdemo_core.themes import load_theme

    theme = load_theme("retail")
    calls = []

    class H:
        def __init__(self, kind):
            self.kind = kind

        def status(self, ctx, t, p):
            return {"applied": True, "status": "applied"}

        def remove(self, ctx, t, p):
            calls.append(("remove", p.id))

        def remove_many(self, ctx, t, ps):
            calls.append(("remove_many", tuple(p.id for p in ps)))

    monkeypatch.setattr(st, "get_handler", lambda kind: H(kind))
    engs, errs = st.reset_theme(CFG, "retail", ctx=type("Ctx", (), {"state": state_with()})(), log=lambda m: None)
    gws = [p.id for p in theme.policies if p.type == "gateway_attach"]
    assert calls[-1] == ("remove_many", tuple(gws)) and sum(1 for x in calls if x[0] == "remove_many") == 1
    assert {x[1] for x in calls if x[0] == "remove"} == {p.id for p in theme.policies if p.type != "gateway_attach"}
    assert engs == [f"projects/{N}/locations/{R}/reasoningEngines/111"] and errs == []


# ---------------------------------------------------------------------- write-command safety + confirm
TF_ENV = {"project_id": P, "gateway_project_id": P, "region": R, "prefix": X}


def test_up_refusal():
    assert lc.up_refusal(CFG, {}, gateway_live=False, tf_managed=None) is None  # fresh
    boot = {"shared": {"egress_gateway": "projects/p/locations/r/agentGateways/agdemo-egress"}}
    assert "bootstrap" in lc.up_refusal(CFG, boot, gateway_live=True, tf_managed=None)    # the live demo, early
    empty = {"shared": {}, "themes": {}}                                                  # load_state() w/o a file
    assert lc.up_refusal(CFG, empty, gateway_live=True, tf_managed=None) is None    # decide after tf init
    assert lc.up_refusal(CFG, empty, gateway_live=True, tf_managed=True) is None    # resume a failed first up
    assert "bootstrap" in lc.up_refusal(CFG, empty, gateway_live=True, tf_managed=False)
    assert lc.up_refusal(CFG, {"shared": {"tf_environment": TF_ENV}}, True, True) is None  # re-run
    assert "isn't in this Terraform state" in lc.up_refusal(CFG, {"shared": {"tf_environment": TF_ENV}}, True, False)
    other = {**TF_ENV, "region": "us-east4", "prefix": "agdemo"}
    assert "belongs to Terraform environment" in lc.up_refusal(CFG, {"shared": {"tf_environment": other}}, False, None)
    assert "aside" in lc.up_refusal(CFG, {"shared": {"egress_gateway": EG}}, False, None)


def test_down_refusals():
    assert "isn't Terraform-managed" in lc.down_refusal_state(CFG, {"shared": {"egress_gateway": EG}})
    assert lc.down_refusal_state(CFG, {"shared": {"tf_environment": TF_ENV}}) is None
    assert "describes" in lc.down_refusal_state(CFG, {"shared": {"tf_environment": {**TF_ENV, "prefix": "agdemo"}}})
    from agdemo_core.tf_import import EGRESS_GW_ADDR

    assert lc.down_refusal_tf(CFG, TF_ENV, {EGRESS_GW_ADDR}, migrating=False) is None
    assert "imports.tf" in lc.down_refusal_tf(CFG, TF_ENV, {EGRESS_GW_ADDR}, migrating=True)
    assert "nothing" in lc.down_refusal_tf(CFG, None, set(), migrating=False)
    assert "no egress gateway" in lc.down_refusal_tf(CFG, TF_ENV, set(), migrating=False)
    assert lc.same_env(CFG, {**TF_ENV, "gateway_project_id": ""})  # empty = the project itself


def _show(env: str | None, gw_project: str = P) -> dict:
    """`terraform show -json` with the destroy guard (env None = no guard) and no outputs."""
    res = [{"address": "module.base.x", "values": {}}]
    if env is not None:
        res.append({"address": "terraform_data.teardown_guard", "values": {"input": {
            "environment": env,
            "gateways": [f"projects/{gw_project}/locations/{R}/agentGateways/{X}-egress"]}}})
    return {"values": {"root_module": {"resources": res}}}


def test_guard_environment():
    from agdemo_core import tf_foundation as tfm

    env = tfm.guard_environment(_show(f"{P}/{R}/{X}"))
    assert env == {"project_id": P, "region": R, "prefix": X, "gateway_project_id": ""}
    assert lc.same_env(CFG, env)
    assert tfm.guard_environment(_show(f"{P}/{R}/{X}", "gw-proj"))["gateway_project_id"] == "gw-proj"
    assert not lc.same_env(CFG, tfm.guard_environment(_show(f"{P}/{R}/{X}", "gw-proj")))
    for bad in (None, "", f"{P}/{R}", f"{P}//{X}", f"{P}/{R}/{X}/extra"):
        assert tfm.guard_environment(_show(bad)) is None
    assert tfm.guard_environment({}) is None


def test_environment_output_falls_back_to_guard(monkeypatch):
    """T5 (§7i): a guard-aborted `terraform destroy` deletes nothing but removes the root outputs; down must
    still recognise its environment, and refuse another one."""
    from agdemo_core import tf_foundation as tfm
    from agdemo_core.tf_import import EGRESS_GW_ADDR

    calls = []
    show = {"v": _show(f"{P}/{R}/{X}")}
    outs = {"v": {}}

    def fake_run_tf(args, d=None, capture=False):
        calls.append(args[0])
        return json.dumps(outs["v"] if args[0] == "output" else show["v"])

    monkeypatch.setattr(tfm, "run_tf", fake_run_tf)
    env = tfm.environment_output()
    assert lc.down_refusal_tf(CFG, env, {EGRESS_GW_ADDR}, migrating=False) is None
    show["v"] = _show(f"{P}/{R}/agdemo")
    assert "describes" in lc.down_refusal_tf(CFG, tfm.environment_output(), {EGRESS_GW_ADDR}, migrating=False)
    show["v"] = _show(None)
    assert "nothing" in lc.down_refusal_tf(CFG, tfm.environment_output(), {EGRESS_GW_ADDR}, migrating=False)
    # Outputs present: they win, and the state isn't read.
    outs["v"] = {"environment": {"value": TF_ENV}}
    calls.clear()
    assert tfm.environment_output() == TF_ENV and calls == ["output"]


def test_confirm_down():
    asked = []

    def ask(q):
        asked.append(q)
        return f" {P}/{X} "

    assert lc.confirm_down(CFG, False, None, ask) == (True, "") and P in asked[0] and X in asked[0]
    assert not lc.confirm_down(CFG, False, None, lambda q: X)[0]  # prefix alone isn't enough
    assert not lc.confirm_down(CFG, False, None, lambda q: "")[0]
    ok, why = lc.confirm_down(CFG, True, None, ask)
    assert not ok and "--prefix" in why
    assert lc.confirm_down(CFG, True, X, lambda q: pytest.fail("must not ask")) == (True, "")
    assert not lc.confirm_down(CFG, True, "agdemo", ask)[0]
    assert not lc.confirm_down(CFG, False, "agdemo", ask)[0]


def test_shared_grant_addresses():
    a = [
        f'{lc.IAM_ADDR}.presenters["user:p@example.com"]',
        f'{lc.IAM_ADDR}.model_armor_agents["gcp-sa-dep roles/modelarmor.user"]',
        f'{lc.IAM_ADDR}.model_armor_agents["gateway-egress roles/modelarmor.user"]',
        f'{lc.IAM_ADDR}.sa["ui roles/iap.admin"]',
        'module.base.google_network_services_agent_gateway.gw["egress"]',
    ]
    assert lc.shared_grant_addresses(a, egress_agent_shared=False) == [a[1], a[0]]
    assert set(lc.shared_grant_addresses(a, egress_agent_shared=True)) == {a[0], a[1], a[2]}


def test_plan_teardown():
    snap = leftovers_snapshot()
    snap.run_services = [
        {"name": f"{LOC}/services/{X}-retail-pricing-agent", "labels": LABELS},
        {"name": f"{LOC}/services/{X}-ui", "labels": LABELS},
        {"name": f"{LOC}/services/{X}-unlabelled"},
    ]
    eng = f"projects/{N}/locations/{R}/reasoningEngines"
    snap.ge_agents = [
        {"name": "a/1", "adkAgentDefinition": {"provisionedReasoningEngine": {"reasoningEngine": f"{eng}/111"}}},
        {"name": "a/2", "adkAgentDefinition": {"provisionedReasoningEngine": {"reasoningEngine": f"{eng}/777"}}},
    ]
    snap.ma_templates.append({"name": f"{LOC}/templates/{X}-ps-template", "labels": LABELS})
    snap.legacy_sas = [f"{X}-allowed-caller@{P}.iam.gserviceaccount.com"]
    p = lc.plan_teardown(CFG, state_with(), snap)
    assert [cc.engine_id(e["name"]) for e in p.engines] == ["111", "666"]  # not the foreign 777
    assert p.engines[0]["principal"] == "principal://p111" and p.engines[1]["principal"].startswith("principal://")
    assert [a["name"] for a in p.ge_agents] == ["a/1"]
    assert [cc.leaf(n) for n in p.run_services] == [f"{X}-retail-pricing-agent", f"{X}-ui"]
    assert p.registry == [f"{X}-retail-pricing-agent"]  # platform entries stay (Terraform)
    assert p.legacy_policies == [(P, f"{X}-ingress-iap-policy")]
    assert p.legacy_extensions == [(P, f"{X}-ingress-iap-authz")]
    assert [cc.leaf(n) for n in p.ma_templates] == [f"{X}-ps-template"]
    assert p.lines()


# ---------------------------------------------------------------------- up / down ordering
class Verdict:
    def __init__(self, ok=True):
        self.ok, self.blockers = ok, ([] if ok else [("x", ["delete"])])

    def summary(self):
        return "1 to add"


class FakeUpOps:
    def __init__(self, live=False, managed=True, plan_ok=True, preflight=()):
        self.calls, self.live, self.managed, self.plan_ok, self.pf = [], live, managed, plan_ok, list(preflight)
        self._state = {"shared": {"tf_environment": TF_ENV}} if live else {}

    def state(self):
        return self._state

    def gateway_live(self):
        return self.live

    def tf_managed(self):
        return self.managed

    def __getattr__(self, name):
        def f(*a):
            self.calls.append((name, *a))
            if name == "preflight":
                return self.pf
            if name == "tf_plan":
                return "plan.bin", Verdict(self.plan_ok)
            if name == "verify":
                return [("x", True, "")]
            return None

        return f


def test_up_order():
    ops = FakeUpOps()
    assert lc.up(CFG, ops, yes=True, themes=["helpdesk", "retail"], log=lambda m: None) == 0
    assert [x[0] for x in ops.calls] == [
        "preflight",
        "tf_init",
        "tf_plan",
        "tf_apply",
        "output_sync",
        "build_images",
        "deploy_theme",
        "deploy_theme",
        "ui_deploy",
        "verify",
    ]
    assert ops.calls[3] == ("tf_apply", "plan.bin") and ops.calls[6:8] == [
        ("deploy_theme", "helpdesk"),
        ("deploy_theme", "retail"),
    ]


def test_up_stops_on_preflight_plan_safety_and_no():
    ops = FakeUpOps(preflight=["APIs: missing"])
    assert lc.up(CFG, ops, yes=True, themes=[], log=lambda m: None) == 1 and [x[0] for x in ops.calls] == ["preflight"]
    ops = FakeUpOps(plan_ok=False)
    assert lc.up(CFG, ops, yes=True, themes=[], log=lambda m: None) == 1
    assert "tf_apply" not in [x[0] for x in ops.calls]
    ops = FakeUpOps()
    assert lc.up(CFG, ops, yes=False, themes=[], ask=lambda q: False, log=lambda m: None) == 1
    assert "tf_apply" not in [x[0] for x in ops.calls]


def test_up_refuses_live_demo_before_anything():
    ops = FakeUpOps(live=True)
    ops._state = {"shared": {"egress_gateway": "projects/p/locations/r/agentGateways/agdemo-egress"}}   # bootstrap
    assert lc.up(CFG, ops, yes=True, log=lambda m: None) == 2 and ops.calls == []
    ops = FakeUpOps(live=True, managed=False)  # tf_environment but not in this state
    assert lc.up(CFG, ops, yes=True, log=lambda m: None) == 2
    assert [x[0] for x in ops.calls] == ["preflight", "tf_init"]


class FakeDownOps:
    def __init__(self, reset_ok=True, clean=True, tf_env=TF_ENV, managed=True):
        from agdemo_core.tf_import import EGRESS_GW_ADDR

        self.calls, self.reset_ok, self.clean = [], reset_ok, clean
        self._tf_env, self._addrs = tf_env, ({EGRESS_GW_ADDR} if managed else set())
        self._state = {"shared": {"tf_environment": TF_ENV}, "themes": {}}

    def state(self):
        return self._state

    def tf_init(self):
        self.calls.append(("tf_init",))

    def tf_environment(self):
        return self._tf_env

    def tf_addresses(self):
        return self._addrs

    def migrating(self):
        return False

    def inventory(self):
        self.calls.append(("inventory",))
        return lc.TeardownPlan(ge_agents=[{"name": "a/1"}])

    def reset_ops(self):
        outer = self

        class R(FakeResetOps):
            def reset_theme(self, tid):
                outer.calls.append(("reset_theme", tid))
                return [], ([] if outer.reset_ok else ["boom"])

            def execute(self, a):
                outer.calls.append(("reset_execute", a.kind))

        return R([foundation_snapshot()], themes=["retail"], ma_on=False)

    def ge_unregister(self, agents):
        self.calls.append(("ge_unregister", tuple(a["name"] for a in agents)))

    def teardown(self, plan):
        self.calls.append(("teardown",))

    def check_clean(self):
        self.calls.append(("check_clean",))
        return [] if self.clean else [cc.Finding(cc.WARN, "ui", "Cloud Run x-ui")]

    def keep_shared_grants(self):
        self.calls.append(("keep_shared_grants",))
        return ["a"]

    def destroy(self):
        self.calls.append(("destroy",))

    def recheck_shared(self):
        self.calls.append(("recheck_shared",))
        return ["ok"]

    def clear_shared_state(self):
        self.calls.append(("clear_shared_state",))


def test_down_order():
    ops = FakeDownOps()
    assert lc.down(CFG, ops, yes=True, prefix_arg=X, log=lambda m: None) == 0
    assert [x[0] for x in ops.calls] == [
        "tf_init",
        "inventory",
        "reset_theme",
        "inventory",
        "ge_unregister",
        "teardown",
        "check_clean",
        "keep_shared_grants",
        "destroy",
        "recheck_shared",
        "clear_shared_state",
    ]
    assert ("ge_unregister", ("a/1",)) in ops.calls


def test_down_stops_safely():
    ops = FakeDownOps()  # --yes without --prefix: nothing changes
    assert lc.down(CFG, ops, yes=True, log=lambda m: None) == 1
    assert [x[0] for x in ops.calls] == ["tf_init", "inventory"]
    ops = FakeDownOps()
    assert lc.down(CFG, ops, ask=lambda q: "nope", log=lambda m: None) == 1 and "reset_theme" not in str(ops.calls)
    ops = FakeDownOps(reset_ok=False)  # reset fails: nothing deleted
    assert lc.down(CFG, ops, yes=True, prefix_arg=X, log=lambda m: None) == 1
    assert not {"teardown", "ge_unregister", "destroy"} & {x[0] for x in ops.calls}
    ops = FakeDownOps(clean=False)  # strict check fails: no destroy
    assert lc.down(CFG, ops, yes=True, prefix_arg=X, log=lambda m: None) == 1
    assert "teardown" in [x[0] for x in ops.calls] and "destroy" not in [x[0] for x in ops.calls]
    ops = FakeDownOps(tf_env={**TF_ENV, "prefix": "agdemo"})  # Terraform state is another environment
    assert lc.down(CFG, ops, yes=True, prefix_arg=X, log=lambda m: None) == 2 and ops.calls == [("tf_init",)]
    ops = FakeDownOps()
    ops._state = {"shared": {"egress_gateway": EG}}  # bootstrap-created (the live demo)
    assert lc.down(CFG, ops, yes=True, prefix_arg=X, log=lambda m: None) == 2 and ops.calls == []


def test_down_dry_run_changes_nothing():
    ops = FakeDownOps()
    out = []
    assert lc.down(CFG, ops, dry_run=True, log=out.append) == 0
    assert [x[0] for x in ops.calls] == ["tf_init", "inventory"]
    assert any("NOBODY IS PRESENTING" in m for m in out)


# ---------------------------------------------------------------------- the destroy guard
def test_guard_resource():
    text = (TF_DIR / "guards.tf").read_text()
    body = text[text.index('resource "terraform_data" "teardown_guard"') :]
    assert "depends_on = [module.base, module.private_egress, module.pn_region]" in body  # T4: every region
    assert re.search(r"when\s+=\s+destroy", body)
    cmd = re.search(r'command\s+=\s+"([^"]+)"', body).group(1)
    assert cmd == "${self.input.agdemo} check-clean --strict --expect ${self.input.environment}"
    assert 'environment = "${var.project_id}/${var.region}/${var.prefix}"' in body
    assert "-target" in text  # the bypass is documented
    assert 'variable "agdemo_cli"' in (TF_DIR / "variables.tf").read_text()
    assert (TF_DIR / "../../agdemo").resolve() == REPO_ROOT / "agdemo"  # the default path, from infra/terraform


def test_guard_command_runs_check_clean(monkeypatch):
    """The exact command the guard runs: --expect mismatch exits 2 before any GCP call; a match runs the
    strict check and exits 1 on a finding (Terraform then aborts the destroy)."""
    monkeypatch.setattr(cli, "_cfg", lambda: CFG)
    called = []

    def fake_run(cfg, r, state):
        called.append(cfg.prefix)
        return None, [cc.Finding(cc.WARN, "ui", f"Cloud Run {X}-ui")]

    monkeypatch.setattr(cc, "run", fake_run)
    monkeypatch.setattr(c, "client", lambda cfg: None)
    runner = CliRunner()
    res = runner.invoke(cli.app, ["check-clean", "--strict", "--expect", f"{P}/us-east4/agdemo"])
    assert res.exit_code == 2 and called == []
    res = runner.invoke(cli.app, ["check-clean", "--strict", "--expect", f"{P}/{R}/{X}"])
    assert res.exit_code == 1 and called == [X] and "Destroy aborted" in res.output and "tf apply" in res.output
    res = runner.invoke(cli.app, ["check-clean"])  # default mode: a warning doesn't fail
    assert res.exit_code == 0


def test_migration_plan_allows_the_guard():
    plan = {
        "resource_changes": [
            {
                "address": "terraform_data.teardown_guard",
                "mode": "managed",
                "type": "terraform_data",
                "change": {"actions": ["create"]},
            }
        ]
    }
    assert tf_safety.check(plan, migration=True).ok


# ---------------------------------------------------------------------- CLI entry points
def test_bootstrap_points_at_up_and_teardown_all_refuses_terraform(monkeypatch):
    from agdemo_core import state as state_mod

    monkeypatch.setattr(cli, "_cfg", lambda: CFG)
    runner = CliRunner()
    monkeypatch.setattr(state_mod, "load_state", lambda *a: {"shared": {}})
    res = runner.invoke(cli.app, ["bootstrap"])
    assert res.exit_code == 2 and "./agdemo up" in res.output and "--legacy" in res.output
    monkeypatch.setattr(state_mod, "load_state", lambda *a: {"shared": {"tf_environment": TF_ENV}})
    res = runner.invoke(cli.app, ["teardown", "--all", "--yes"])
    assert res.exit_code == 2 and "down" in res.output
    res = runner.invoke(cli.app, ["reset", "retail", "--all"])
    assert res.exit_code == 2


def test_state_of_another_environment_is_ignored():
    other = {
        "shared": {"egress_gateway": f"projects/{P}/locations/us-east4/agentGateways/agdemo-egress"},
        "themes": {"retail": {"orchestrator": {"engine": f"projects/{N}/locations/{R}/reasoningEngines/111"}}},
    }
    snap = foundation_snapshot()
    snap.engines = [engine("111", "not-prefixed", labels={})]
    f = cc.classify(CFG, other, snap, DER)
    assert [(x.level, x.category) for x in f] == [(cc.WARN, "state")] and "another environment" in f[0].detail
