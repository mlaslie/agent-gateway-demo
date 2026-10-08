import pytest

from agdemo_core import recordings, simulate
from agdemo_core.runtime_client import (build_probe_message, parse_event, parse_probe_result,
                                        tool_result_to_edge_state)
from agdemo_core.themes import ScenarioTest, edge_ids, load_theme


@pytest.fixture(scope="module")
def theme():
    return load_theme("helpdesk")


def states(r):
    return {k: v["state"] for k, v in r.items()}


def test_all_edges_present(theme):
    r = simulate.evaluate(theme, set(), False)
    assert set(r) == set(edge_ids(theme))
    for v in r.values():
        assert v["source"] == "simulated"
        assert set(v) >= {"state", "governed", "source", "detail", "http_status"}


def test_wide_open_is_direct(theme):
    r = simulate.evaluate(theme, set(), False)
    assert all(v["state"] == "direct" and v["governed"] is False for v in r.values())


def test_wide_open_ignores_allow_policies_and_model_armor(theme):
    mal = ScenarioTest(id="x", label="x", prompt="x", malicious=True)
    r = simulate.evaluate(theme, {"allow-kb"}, True, mal)
    assert all(v["state"] == "direct" for v in r.values())


def test_gateway_default_deny(theme):
    r = simulate.evaluate(theme, {"gw-egress"}, False)
    for e, v in r.items():
        if e.startswith("ingress:"):
            assert v["state"] == "direct"
        else:
            assert v["state"] == "denied" and v["http_status"] == 403 and v["governed"]


def test_a2a_allow(theme):
    r = states(simulate.evaluate(theme, {"gw-egress", "allow-kb"}, False))
    assert r["kb-agent"] == "allowed"
    assert r["hr-records-agent"] == "denied"


def test_mcp_tool_allow_read_only(theme):
    r = states(simulate.evaluate(theme, {"gw-egress", "tickets-readonly"}, False))
    assert r["tickets-mcp:list_tickets"] == "allowed"
    assert r["tickets-mcp:get_ticket"] == "allowed"
    assert r["tickets-mcp:close_ticket"] == "denied"
    assert r["tickets-mcp:delete_ticket"] == "denied"
    assert r["directory-mcp:lookup_user"] == "denied"


def test_mcp_tool_allow_named_tools(theme):
    theme2 = theme.model_copy(deep=True)
    theme2.policies[4].params = {"target": "tickets-mcp", "tools": ["close_ticket"]}
    r = states(simulate.evaluate(theme2, {"gw-egress", "tickets-readonly"}, False))
    assert r["tickets-mcp:close_ticket"] == "allowed"
    assert r["tickets-mcp:get_ticket"] == "denied"


def test_mcp_server_allow(theme):
    r = states(simulate.evaluate(theme, {"gw-egress", "tickets-all", "allow-directory"}, False))
    assert all(r[e] == "allowed" for e in r if e.startswith(("tickets-mcp:", "directory-mcp:")))
    assert r["kb-agent"] == "denied"


def test_model_armor_blocks_allowed_only_when_malicious(theme):
    applied = {"gw-egress", "allow-kb", "tickets-readonly"}
    benign = ScenarioTest(id="b", label="b", prompt="p")
    mal = ScenarioTest(id="m", label="m", prompt="p", malicious=True)
    assert states(simulate.evaluate(theme, applied, True, benign))["kb-agent"] == "allowed"
    assert states(simulate.evaluate(theme, applied, False, mal))["kb-agent"] == "allowed"
    r = states(simulate.evaluate(theme, applied, True, mal))
    assert r["kb-agent"] == "allowed"               # egress Model Armor screens MCP, not A2A
    assert r["tickets-mcp:get_ticket"] == "blocked"
    assert r["hr-records-agent"] == "denied"          # denied stays denied
    assert r["tickets-mcp:delete_ticket"] == "denied"


def test_ingress_rules(theme):
    """Ingress gateways screen content only: direct without a gateway, allowed through it, blocked by
    Model Armor for a malicious prompt, never denied."""
    r = states(simulate.evaluate(theme, set(), False))
    assert r["ingress:user"] == "direct"
    r = states(simulate.evaluate(theme, {"gw-ingress"}, False))
    assert r["ingress:user"] == "allowed" and r["kb-agent"] == "direct"
    mal = ScenarioTest(id="m", label="m", prompt="p", malicious=True)
    assert states(simulate.evaluate(theme, {"gw-ingress"}, True, mal))["ingress:user"] == "blocked"
    assert states(simulate.evaluate(theme, {"gw-ingress"}, False, mal))["ingress:user"] == "allowed"
    assert states(simulate.evaluate(theme, set(), True, mal))["ingress:user"] == "direct"


def test_signature():
    assert simulate.signature({"gw-egress", "allow-kb"}, False) == "allow-kb+gw-egress+ma-off"
    assert simulate.signature(set(), True) == "none+ma-on"


def test_synth_events_shape(theme):
    scen, test = simulate.find_test(theme, "both", "a2a")
    evs = simulate.synth_events(theme, scen, test, {"gw-egress", "allow-kb"}, False)
    types = [e["type"] for _, e in evs]
    assert types[0] == "status" and types[-1] == "done"
    edges = [e for _, e in evs if e["type"] == "edge"]
    assert [e["edge"] for e in edges] == ["kb-agent", "hr-records-agent"]
    assert evs[-1][1]["edges"]["hr-records-agent"]["state"] == "denied"
    msg = next(e for _, e in evs if e["type"] == "message" and e["role"] == "agent")["text"]
    assert "HR Records Agent" in msg and "403" in msg


def test_ingress_test_edges(theme):
    scen, test = simulate.find_test(theme, "injection", "ingress")
    assert simulate.test_is_ingress(scen, test) and test.malicious
    assert simulate.test_edges(theme, scen, test) == ["ingress:user"]


def test_recordings_roundtrip(tmp_path):
    rec = recordings.Recorder()
    rec.add({"type": "status", "text": "x"})
    rec.add({"type": "edge", "edge": "kb-agent", "state": {"state": "allowed", "source": "live"}})
    rec.events[1]["t_ms"] = 5000  # long gap gets capped
    p = recordings.save("helpdesk", "ask-kb", "allow-kb+gw-egress+ma-off", rec.events, themes_dir=tmp_path)
    assert p.exists()
    loaded = recordings.load("helpdesk", "ask-kb", "allow-kb+gw-egress+ma-off", themes_dir=tmp_path)
    out = list(recordings.replay(loaded))
    assert out[0][0] == 0.0
    assert out[1][0] == recordings.MAX_GAP_S
    assert out[1][1]["state"]["source"] == "replayed" and "t_ms" not in out[1][1]
    assert out[-1][1]["type"] == "done" and out[-1][1]["edges"]["kb-agent"]["source"] == "replayed"
    assert recordings.load("helpdesk", "ask-kb", "nope", themes_dir=tmp_path) is None


def test_runtime_parsing():
    ev = {"author": "helpdesk-agent", "content": {"role": "model", "parts": [
        {"function_call": {"name": "call_agent", "args": {"agent_id": "kb-agent"}, "id": "1"}}]}}
    assert parse_event(ev)[0]["kind"] == "function_call"
    ev = {"content": {"role": "user", "parts": [{"functionResponse": {"name": "call_agent", "response": {
        "result": {"edge": "kb-agent", "outcome": "denied", "http_status": 403}}}}]}}
    p = parse_event(ev)[0]
    assert p["kind"] == "function_response" and p["response"]["edge"] == "kb-agent"
    msg = build_probe_message(["kb-agent"], True)
    assert msg.startswith("__PROBE__ ") and '"malicious": true' in msg
    assert parse_probe_result('__PROBE_RESULT__ {"results":[{"edge":"kb-agent","outcome":"ok"}]}')[0]["edge"] == "kb-agent"
    assert parse_probe_result("hello") is None


def test_tool_result_mapping():
    assert tool_result_to_edge_state({"outcome": "ok"}, True)["state"] == "allowed"
    assert tool_result_to_edge_state({"outcome": "ok"}, False)["state"] == "direct"
    assert tool_result_to_edge_state({"outcome": "denied", "http_status": 403}, True)["state"] == "denied"
    assert tool_result_to_edge_state({"outcome": "blocked"}, True)["state"] == "blocked"
    assert tool_result_to_edge_state({"outcome": "error"}, True)["state"] == "error"


def test_runtime_client_stream(monkeypatch):
    import asyncio
    import json as _json

    import httpx

    from agdemo_core.runtime_client import RuntimeClient

    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["body"] = _json.loads(request.content)
        msg = seen["body"]["input"]["message"]
        if msg.startswith("__PROBE__"):
            ev = {"author": "a", "content": {"role": "model", "parts": [{"text": '__PROBE_RESULT__ {"results":'
                  '[{"edge":"kb-agent","outcome":"ok","http_status":200}]}'}]}}
            return httpx.Response(200, text=f"data: {_json.dumps(ev)}\n\n")
        evs = [
            {"content": {"role": "model", "parts": [{"function_call": {"name": "call_agent", "args": {"agent_id": "kb-agent"}}}]}},
            {"content": {"role": "user", "parts": [{"function_response": {"name": "call_agent", "response": {"edge": "kb-agent", "outcome": "denied", "http_status": 403}}}]}},
            {"content": {"role": "model", "parts": [{"text": "Access to KB Agent was blocked."}]}},
        ]
        return httpx.Response(200, text="\n".join(_json.dumps(e) for e in evs) + "\n")

    c = RuntimeClient("projects/p/locations/us-east4/reasoningEngines/9", transport=httpx.MockTransport(handler))
    res = asyncio.run(c.run_probes(["kb-agent"]))
    assert res[0]["outcome"] == "ok"
    assert seen["url"].startswith("https://us-east4-aiplatform.googleapis.com/v1/projects/p/locations/us-east4/reasoningEngines/9:streamQuery")
    assert seen["body"]["class_method"] == "async_stream_query"

    async def chat():
        return [e async for e in c.chat("hi", gateway_attached=True)]
    evs = asyncio.run(chat())
    edge = next(e for e in evs if e["type"] == "edge")
    assert edge["state"]["state"] == "denied"
    assert evs[-1] == {"type": "message", "role": "agent", "text": "Access to KB Agent was blocked."}


def test_recording_signature_uses_only_relevant_policies(theme):
    scen, salary = simulate.find_test(theme, "salary", "wide-open")
    everything = {p.id for p in theme.policies}
    assert simulate.test_signature(theme, scen, salary, everything, True) == "allow-hr+gw-egress+ma-any"
    assert simulate.test_signature(theme, scen, salary, set(), False) == "none+ma-any"
    scen, inj = simulate.find_test(theme, "injection", "ingress")
    assert simulate.test_signature(theme, scen, inj, everything, True) == "gw-ingress+ma-on"
    assert simulate.test_signature(theme, scen, inj, {"gw-ingress"}, False) == "gw-ingress+ma-off"


def test_hints_based_read_only_policy(theme):
    """tickets-readonly-hints: same edges as the name-based policy, different IAM condition."""
    from agdemo_core.policies.egress_allow import condition
    a = states(simulate.evaluate(theme, {"gw-egress", "tickets-readonly-hints"}, False))
    b = states(simulate.evaluate(theme, {"gw-egress", "tickets-readonly"}, False))
    assert {k: v for k, v in a.items() if k.startswith("tickets-mcp")} == \
           {k: v for k, v in b.items() if k.startswith("tickets-mcp")}
    assert a["tickets-mcp:get_ticket"] == "allowed" and a["tickets-mcp:delete_ticket"] == "denied"
    hints = condition(theme, theme.policy("tickets-readonly-hints"))["expression"]
    names = condition(theme, theme.policy("tickets-readonly"))["expression"]
    assert "mcp.tool.isReadOnly" in hints and "get_ticket" not in hints
    assert "mcp.toolName" in names and "get_ticket" in names
