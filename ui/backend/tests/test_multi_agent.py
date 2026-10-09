"""Multiple agents per theme (backlog #3, CONTRACTS §12): schema, edge ids, simulator, graph, probe grouping,
registry view, Terraform per source, and Live reset/verify across orchestrators."""
import time
import types
from datetime import datetime, timezone

import pytest

from agdemo_core import simulate
from agdemo_core import terraform as T
from agdemo_core.config import DemoConfig
from agdemo_core.gcp import registry_view as rv
from agdemo_core.themes import (Theme, edge_ids, load_theme, orchestrator_ids, source_edge, split_source)
from agdemo_ui import settings
from agdemo_ui.engine import policy_edges, probe_requests
from agdemo_ui.graph import build_graph
from test_api import FakeGcp, client, sse_events  # noqa: F401  (shared TestClient fixture)

PRIMARY = "projects/42/locations/us-east4/reasoningEngines/1"
HR_ENGINE = "projects/42/locations/us-east4/reasoningEngines/2"
P_PRIMARY = "principal://agents.global.org-1.system.id.goog/resources/aiplatform/projects/42/locations/us-east4/reasoningEngines/1"
P_HR = "principal://agents.global.org-1.system.id.goog/resources/aiplatform/projects/42/locations/us-east4/reasoningEngines/2"


@pytest.fixture(scope="module")
def theme():
    return load_theme("helpdesk")


def states(r):
    return {k: v["state"] for k, v in r.items()}


# ------------------------------------------------------------------ schema / edges
def test_helpers():
    assert split_source("hr-assistant/hr-records-agent") == ("hr-assistant", "hr-records-agent")
    assert split_source("hr-assistant/tickets-mcp:get_ticket") == ("hr-assistant", "tickets-mcp:get_ticket")
    assert split_source("kb-agent") == (None, "kb-agent")
    assert source_edge(None, "kb-agent") == "kb-agent"
    assert source_edge("hr-assistant", "kb-agent") == "hr-assistant/kb-agent"


def test_edge_ids_order(theme):
    ids = edge_ids(theme)
    assert orchestrator_ids(theme) == [None, "hr-assistant"]
    assert ids[0] == "kb-agent" and ids[-1] == "ingress:user"
    n = (len(ids) - 1) // 2
    assert ids[n:2 * n] == [f"hr-assistant/{e}" for e in ids[:n]]
    assert "hr-assistant/tickets-mcp:get_ticket" in ids


def _theme_data(**over):
    base = load_theme("helpdesk").model_dump(mode="json")
    base.update(over)
    return base


def test_validation_unknown_source():
    data = _theme_data()
    data["policies"].append({"id": "bad", "text": "x", "type": "a2a_allow",
                             "params": {"target": "kb-agent", "source": "nobody"}})
    with pytest.raises(ValueError, match="unknown source"):
        Theme.model_validate(data)


def test_validation_unknown_node_and_agent():
    data = _theme_data()
    data["scenarios"][-1]["nodes"].append("orchestrator:nobody")
    with pytest.raises(ValueError, match="unknown node"):
        Theme.model_validate(data)
    data = _theme_data()
    data["scenarios"][-1]["tests"][0]["agent"] = "nobody"
    with pytest.raises(ValueError, match="unknown agent"):
        Theme.model_validate(data)


def test_validation_ingress_is_primary_only():
    data = _theme_data()
    data["policies"].append({"id": "bad", "text": "x", "type": "gateway_attach",
                             "params": {"path": "ingress", "source": "hr-assistant"}})
    with pytest.raises(ValueError, match="primary-only"):
        Theme.model_validate(data)


def test_scenario7_loaded(theme):
    sc = next(s for s in theme.scenarios if s.id == "two-agents")
    assert sc.order == 7 and sc.nodes[1] == "orchestrator:hr-assistant"
    assert sc.preconditions == ["gw-egress", "gw-egress-hr"]
    t = next(t for t in sc.tests if t.id == "hr-salary")
    assert t.agent == "hr-assistant" and t.probes[0].edge == "hr-assistant/hr-records-agent"
    retail = load_theme("retail")
    assert [o.id for o in retail.additional_orchestrators] == ["pricing-analyst"]
    assert any(s.id == "two-agents" for s in retail.scenarios)


# ------------------------------------------------------------------ simulator
def test_each_orchestrator_has_its_own_gateway(theme):
    r = states(simulate.evaluate(theme, {"gw-egress"}, False))
    assert r["kb-agent"] == "denied" and r["hr-assistant/kb-agent"] == "direct"
    r = states(simulate.evaluate(theme, {"gw-egress-hr"}, False))
    assert r["kb-agent"] == "direct" and r["hr-assistant/kb-agent"] == "denied"


def test_allows_follow_identity(theme):
    applied = {"gw-egress", "gw-egress-hr", "allow-kb", "hr-assistant-allow-hr"}
    r = states(simulate.evaluate(theme, applied, False))
    assert r["kb-agent"] == "allowed"
    assert r["hr-records-agent"] == "denied"
    assert r["hr-assistant/hr-records-agent"] == "allowed"
    assert r["hr-assistant/kb-agent"] == "denied"
    # the primary's allow doesn't leak to the additional orchestrator and vice versa
    r = states(simulate.evaluate(theme, {"gw-egress", "gw-egress-hr", "allow-hr"}, False))
    assert r["hr-records-agent"] == "allowed" and r["hr-assistant/hr-records-agent"] == "denied"


def test_labels_and_signature(theme):
    assert simulate.edge_label(theme, "hr-assistant/hr-records-agent") == "HR Assistant → HR Records Agent"
    assert simulate.edge_label(theme, "hr-records-agent") == "HR Records Agent"
    assert simulate.edge_label(theme, "hr-assistant/tickets-mcp:get_ticket") == "HR Assistant → Tickets MCP · get_ticket"
    sc = next(s for s in theme.scenarios if s.id == "two-agents")
    hr = next(t for t in sc.tests if t.id == "hr-salary")
    every = {p.id for p in theme.policies}
    assert simulate.test_signature(theme, sc, hr, every, False) == "gw-egress-hr+hr-assistant-allow-hr+ma-any"
    hd = next(t for t in sc.tests if t.id == "helpdesk-salary")
    assert simulate.test_signature(theme, sc, hd, every, False) == "allow-hr+gw-egress+ma-any"


def test_synth_run_names_agent_and_reply(theme):
    sc = next(s for s in theme.scenarios if s.id == "two-agents")
    hr = next(t for t in sc.tests if t.id == "hr-salary")
    evs = [e for _, e in simulate.synth_events(theme, sc, hr, {"gw-egress-hr", "hr-assistant-allow-hr"}, False)]
    assert evs[0]["text"] == "Calling hr-assistant via Agent Runtime..."
    edge = next(e for e in evs if e["type"] == "edge")
    assert edge["edge"] == "hr-assistant/hr-records-agent" and edge["state"]["state"] == "allowed"
    msg = next(e for e in evs if e["type"] == "message" and e["role"] == "agent")["text"]
    assert "HR Assistant → HR Records Agent replied: Jordan Doe (jdoe) earns $182,000" in msg
    both = next(t for t in sc.tests if t.id == "both-agents")
    assert simulate.calling_text(theme, sc, both) == "Calling helpdesk-agent and hr-assistant via Agent Runtime..."
    # the test's own edges, unprefixed or prefixed, are what a test with no probes exercises
    t = types.SimpleNamespace(probes=[], agent="hr-assistant", malicious=False)
    assert simulate.test_edges(theme, sc, t)[0] == "hr-assistant/kb-agent"


# ------------------------------------------------------------------ backend helpers
def test_policy_edges(theme):
    assert policy_edges(theme, theme.policy("hr-assistant-allow-hr")) == ["hr-assistant/hr-records-agent"]
    gw = policy_edges(theme, theme.policy("gw-egress-hr"))
    assert gw and all(e.startswith("hr-assistant/") for e in gw)
    assert not any("/" in e for e in policy_edges(theme, theme.policy("gw-egress")))


def test_probe_requests_prefixed_single(theme):
    sc = next(s for s in theme.scenarios if s.id == "two-agents")
    hr = next(t for t in sc.tests if t.id == "hr-salary")
    assert probe_requests(hr, ["hr-assistant/hr-records-agent"]) == [
        {"edge": "hr-assistant/hr-records-agent", "message": "What does jdoe earn?"}]


def test_graph_nodes_and_edges(theme):
    g = build_graph(theme)
    node = next(n for n in g["nodes"] if n["id"] == "orchestrator:hr-assistant")
    assert node == {"id": "orchestrator:hr-assistant", "type": "orchestrator", "label": "HR Assistant",
                    "sublabel": "Agent Runtime · hr-assistant"}
    e = next(e for e in g["edges"] if e["id"] == "hr-assistant/hr-records-agent")
    assert e["source"] == "orchestrator:hr-assistant" and e["target"] == "hr-records-agent" and e["kind"] == "a2a"
    e = next(e for e in g["edges"] if e["id"] == "hr-assistant/tickets-mcp:get_ticket")
    assert e["tool"] == "get_ticket" and e["kind"] == "mcp"
    assert {e["id"] for e in g["edges"]} == set(edge_ids(theme))


# ------------------------------------------------------------------ registry view
def test_registry_demo_labels_additional_identity(theme):
    state = {"themes": {"helpdesk": {"orchestrator": {"principal": P_PRIMARY},
                                     "orchestrators": {"hr-assistant": {"principal": P_HR,
                                                                        "registry": "projects/42/locations/us-east4/agents/agentregistry-hra"}}}}}
    out = rv.simulate(theme, {"hr-assistant-allow-hr", "allow-kb"}, None, state)
    hr = next(a for a in out["agents"] if a["id"] == "hr-records-agent")
    assert hr["access"] == [{**hr["access"][0], "member": P_HR, "member_label": "HR Assistant (Agent Identity)",
                             "member_kind": "orchestrator", "policy_id": "hr-assistant-allow-hr"}]
    kb = next(a for a in out["agents"] if a["id"] == "kb-agent")
    assert [(a["member_label"], a["policy_id"]) for a in kb["access"]] == [("Helpdesk Agent (Agent Identity)", "allow-kb")]
    orch = next(a for a in out["agents"] if a["id"] == "orchestrator:hr-assistant")
    assert orch["kind"] == "orchestrator" and orch["registry_id"] == "agentregistry-hra"
    assert out["additional_orchestrators"] == [{"id": "hr-assistant", "display_name": "HR Assistant", "principal": P_HR}]


def test_registry_live_parse_access_matches_source_policy(theme):
    pol = {"bindings": [{"role": "roles/iap.egressor", "members": [P_PRIMARY, P_HR]}]}
    acc = rv.parse_access(pol, theme, "kb-agent", P_PRIMARY, principals={P_HR: "hr-assistant"})
    assert [(a["member_label"], a["policy_id"]) for a in acc] == [
        ("Helpdesk Agent (Agent Identity)", "allow-kb"), ("HR Assistant (Agent Identity)", "hr-assistant-allow-kb")]


# ------------------------------------------------------------------ terraform
def test_terraform_per_source(theme):
    cfg = DemoConfig.model_validate({"environment": {"project_id": "demo-proj", "region": "us-east4"}})
    state = {"themes": {"helpdesk": {
        "orchestrator": {"engine": PRIMARY, "principal": P_PRIMARY},
        "orchestrators": {"hr-assistant": {"engine": HR_ENGINE, "principal": P_HR}},
        "components": {c: {"registry": f"projects/42/locations/us-east4/{'agents' if 'agent' in c else 'mcpServers'}/agentregistry-{c}"}
                       for c in ("kb-agent", "hr-records-agent", "tickets-mcp", "directory-mcp")}}}}
    applied = {"gw-egress", "gw-egress-hr", "allow-kb", "hr-assistant-allow-hr"}
    main = T.render(cfg, state, theme, applied, False, False, now=datetime(2026, 10, 8, tzinfo=timezone.utc))["main.tf"]
    assert 'resource "terraform_data" "gateway_binding" {' in main
    assert 'resource "terraform_data" "gateway_binding_hr_assistant" {' in main
    assert '"hr-assistant" = "' + P_HR + '"' in main and '"hr-assistant" = "' + HR_ENGINE + '"' in main
    assert "triggers_replace = [local.agent_engines[\"hr-assistant\"], local.gateway_binding_body_hr_assistant]" in main
    hr_block = main.split('"hr_assistant_allow_hr" {', 1)[1].split("}", 1)[0]
    assert 'member   = local.agent_principals["hr-assistant"]' in hr_block
    kb_block = main.split('"allow_kb" {', 1)[1].split("}", 1)[0]
    assert "member   = local.orchestrator_principal" in kb_block
    # only the HR engine has a gateway: no primary binding
    main = T.render(cfg, state, theme, {"gw-egress-hr"}, False, False)["main.tf"]
    assert '"gateway_binding_hr_assistant"' in main and 'resource "terraform_data" "gateway_binding" {' not in main
    shared = T.render(cfg, state, theme, set(), False, True)["main.tf"]
    assert 'resource "google_project_iam_member" "agent_hr_assistant"' in shared


# ------------------------------------------------------------------ live: probe grouping, runs, reset, verify
class MultiFake(FakeGcp):
    """FakeGcp whose probe results depend on which engine (orchestrator) the client calls."""

    def __init__(self, theme):
        super().__init__(theme)
        self.calls: list[tuple[str, list[str]]] = []

    async def run_probes(self, client_self, edges, malicious=False):
        src = "hr-assistant" if client_self.engine == HR_ENGINE else None
        test = types.SimpleNamespace(malicious=malicious)
        exp = simulate.evaluate(self.theme, self.applied, self.ma, test)
        m = {"allowed": "ok", "direct": "ok", "denied": "denied", "blocked": "blocked"}
        base = [e["edge"] if isinstance(e, dict) else e for e in edges]
        assert not any("/" in e for e in base), base          # engines only ever see their own base edge ids
        self.calls.append((src, base))
        return [{"edge": e, "outcome": m[exp[source_edge(src, e)]["state"]], "http_status": 200, "detail": "fake",
                 "result": "ok", "latency_ms": 5} for e in base]


@pytest.fixture
def live_multi(monkeypatch, client):  # noqa: F811
    import agdemo_core.policies as pol
    from agdemo_core.runtime_client import RuntimeClient

    fake = MultiFake(load_theme("helpdesk"))
    cfg = DemoConfig.model_validate({"environment": {"project_id": "p"}})
    monkeypatch.setattr(settings, "get_config", lambda: (cfg, ""))
    monkeypatch.setattr(settings, "live_available", lambda: (True, ""))
    monkeypatch.setattr(settings, "get_state", lambda: {"shared": {"egress_gateway": "x"}, "themes": {
        "helpdesk": {"orchestrator": {"engine": PRIMARY},
                     "orchestrators": {"hr-assistant": {"engine": HR_ENGINE}}}}})
    monkeypatch.setattr(pol, "get_handler", fake.handler)
    monkeypatch.setattr(pol, "model_armor_handler", fake.ma_module)
    monkeypatch.setattr(RuntimeClient, "run_probes", lambda s, e, m=False: fake.run_probes(s, e, m))
    return fake


def test_live_probe_groups_by_orchestrator(client, live_multi):  # noqa: F811
    live_multi.applied |= {"gw-egress", "gw-egress-hr", "allow-kb", "hr-assistant-allow-hr"}
    evs = sse_events(client.post("/api/themes/helpdesk/tests/both-agents/run",
                                 json={"mode": "live", "scenario_id": "two-agents"}))
    assert evs[0] == {"type": "status", "text": "Calling helpdesk-agent and hr-assistant via Agent Runtime..."}
    done = evs[-1]
    assert done["type"] == "done", evs
    assert states(done["edges"]) == {"kb-agent": "allowed", "hr-records-agent": "denied",
                                     "hr-assistant/kb-agent": "denied", "hr-assistant/hr-records-agent": "allowed"}
    assert sorted(live_multi.calls, key=str) == sorted([(None, ["kb-agent", "hr-records-agent"]),
                                                       ("hr-assistant", ["kb-agent", "hr-records-agent"])], key=str)
    # a single-agent test only calls its own engine
    live_multi.calls.clear()
    evs = sse_events(client.post("/api/themes/helpdesk/tests/hr-salary/run",
                                 json={"mode": "live", "scenario_id": "two-agents"}))
    assert evs[0]["text"] == "Calling hr-assistant via Agent Runtime..."
    assert live_multi.calls == [("hr-assistant", ["hr-records-agent"])]
    assert evs[-1]["edges"]["hr-assistant/hr-records-agent"]["state"] == "allowed"


def test_live_reset_and_verify_cover_every_orchestrator(client, live_multi):  # noqa: F811
    live_multi.applied |= {"gw-egress", "gw-egress-hr", "allow-kb", "hr-assistant-allow-hr"}
    v = client.post("/api/themes/helpdesk/verify", json={"mode": "live"}).json()
    bad = {c["id"] for c in v["checks"] if not c["ok"]}
    assert not v["ok"] and {"gw-egress-hr", "hr-assistant-allow-hr"} <= bad
    r = client.post("/api/themes/helpdesk/reset", json={"mode": "live"}).json()
    assert r["policies"]["gw-egress-hr"]["status"] == "pending_removal"
    for _ in range(50):
        if not live_multi.applied:
            break
        time.sleep(0.05)
    assert not live_multi.applied
    live_multi.calls.clear()
    v = client.post("/api/themes/helpdesk/verify", json={"mode": "live"}).json()
    assert v["ok"], v
    assert {src for src, _ in live_multi.calls} == {None, "hr-assistant"}


def test_live_probe_skips_undeployed_orchestrator(client, live_multi, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "get_state", lambda: {"shared": {"egress_gateway": "x"}, "themes": {
        "helpdesk": {"orchestrator": {"engine": PRIMARY}}}})
    r = client.post("/api/themes/helpdesk/probe", json={"mode": "live"}).json()
    assert r["edges"]["kb-agent"]["state"] == "direct"
    assert r["edges"]["hr-assistant/kb-agent"]["state"] == "unknown"
    assert all(src is None for src, _ in live_multi.calls)
