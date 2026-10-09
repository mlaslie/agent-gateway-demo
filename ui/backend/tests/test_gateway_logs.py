"""Gateway log parsing (CONTRACTS §9) and the Demo-mode simulated feed."""
from agdemo_core.config import DemoConfig
from agdemo_core.gcp import gateway_logs as gl
from agdemo_core.themes import load_theme
from test_api import client  # noqa: F401  (shared TestClient fixture)

HOST = "agdemo-helpdesk-tickets-mcp-123.us-east4.run.app"
REAL = {
    "insertId": "18zn2brf65ij11", "timestamp": "2026-10-08T00:47:46.214128Z",
    "httpRequest": {"requestMethod": "POST", "requestUrl": f"https://{HOST}/mcp", "status": 403},
    "jsonPayload": {
        "agentGatewayInfo": {"mcpInfo": {"method": "tools/call", "parameter": "list_tickets"}},
        "authzPolicyInfo": {"policies": [{"name": "projects/1/locations/us-east4/authzPolicies/agdemo-egress-iap-policy",
                                          "result": "DENIED"}], "result": "DENIED"},
        "enforcedGatewaySecurityPolicy": {"hostname": HOST}},
    "resource": {"labels": {"gateway_name": "agdemo-egress"}},
}


def cfg():
    return DemoConfig.model_validate({"environment": {"project_id": "p"}})


def test_parse_real_deny_entry():
    e = gl.parse_entry(cfg(), load_theme("helpdesk"), {HOST: "tickets-mcp"}, REAL)
    assert e["decision"] == "denied" and e["status"] == 403
    assert e["edge"] == "tickets-mcp:list_tickets" and e["decided_by"] == "agdemo-egress-iap-policy"
    assert e["summary"] == "DENIED by agdemo-egress-iap-policy · tools/call list_tickets · 403"
    assert "insertId%3D%2218zn2brf65ij11%22" in e["console_url"]


def test_model_armor_denial_is_blocked():
    raw = {**REAL, "jsonPayload": {**REAL["jsonPayload"], "authzPolicyInfo": {"policies": [
        {"name": "x/agdemo-egress-iap-policy", "result": "ALLOWED"},
        {"name": "x/agdemo-egress-ma-policy", "result": "DENIED"}], "result": "DENIED"}}}
    e = gl.parse_entry(cfg(), load_theme("helpdesk"), {HOST: "tickets-mcp"}, raw)
    assert e["decision"] == "blocked" and e["decided_by"] == "agdemo-egress-ma-policy"


def test_filter_scopes_to_gateway_and_hosts():
    f = gl.build_filter(cfg(), ["a.run.app", "b.run.app"], "2026-10-08T00:00:00Z", True)
    assert 'resource.labels.gateway_name="agdemo-egress"' in f and '"a.run.app" OR "b.run.app"' in f
    assert "httpRequest.status>=400" in f


def test_demo_feed_after_probe(client, monkeypatch):
    import agdemo_ui.gateway_logs as m
    monkeypatch.setattr(m.random, "uniform", lambda a, b: 0)
    client.post("/api/themes/helpdesk/policies/gw-egress", json={"action": "apply", "mode": "demo"})
    client.post("/api/themes/helpdesk/policies/allow-kb", json={"action": "apply", "mode": "demo"})
    client.post("/api/themes/helpdesk/probe", json={"mode": "demo"})
    r = client.get("/api/themes/helpdesk/gateway-logs", params={"mode": "demo", "denied_only": True}).json()
    assert r["source"] == "simulated" and r["entries"]
    assert all(e["decision"] != "allowed" and e["simulated"] for e in r["entries"])
    assert any(e["edge"] == "hr-records-agent" and e["status"] == 403 for e in r["entries"])
    everything = client.get("/api/themes/helpdesk/gateway-logs", params={"mode": "demo"}).json()["entries"]
    assert any(e["edge"] == "kb-agent" and e["decision"] == "allowed" for e in everything)
    client.post("/api/themes/helpdesk/reset", json={"mode": "demo"})


# ------------------------------------------------------------------ CONTRACTS §12: which agent called
def _a2a(fp, status=403, host="agdemo-helpdesk-hr-records-agent-1.us-east4.run.app"):
    return {"insertId": f"x{fp}", "timestamp": "2026-10-08T23:51:46Z",
            "httpRequest": {"requestMethod": "POST", "requestUrl": f"https://{host}/", "status": status},
            "jsonPayload": {"mtls": {"clientCertSha256Fingerprint": fp},
                            "authzPolicyInfo": {"policies": [], "result": "ALLOWED" if status == 200 else "DENIED"},
                            "enforcedGatewaySecurityPolicy": {"hostname": host}},
            "resource": {"labels": {"gateway_name": "agdemo-egress"}}}


def _session(fp, eid):
    return {"httpRequest": {"requestUrl": f"https://us-east4-aiplatform.mtls.googleapis.com/v1beta1/projects/p/"
                                          f"locations/us-east4/reasoningEngines/{eid}/sessions/9:appendEvent"},
            "jsonPayload": {"mtls": {"clientCertSha256Fingerprint": fp}}}


def test_caller_attribution_by_client_cert():
    theme = load_theme("helpdesk")
    state = {"themes": {"helpdesk": {"orchestrator": {"engine": "projects/1/locations/us-east4/reasoningEngines/11"},
                                     "orchestrators": {"hr-assistant": {
                                         "engine": "projects/1/locations/us-east4/reasoningEngines/22"}}}}}
    engines = gl.theme_engines(state, theme)
    assert engines == {"11": None, "22": "hr-assistant"}
    assert 'httpRequest.requestUrl:"reasoningEngines/22/"' in gl.caller_filter(cfg(), sorted(engines), "t")
    callers = gl.caller_map([_session("FP1", "11"), _session("FP2", "22"), _session("FPX", "11"),
                             _session("FPX", "22")], engines)
    assert callers == {"FP1": None, "FP2": "hr-assistant"}            # FPX seen on both engines: ambiguous
    hosts = {"agdemo-helpdesk-hr-records-agent-1.us-east4.run.app": "hr-records-agent"}
    hr = gl.parse_entry(cfg(), theme, hosts, _a2a("FP2", 200), callers)
    assert hr["edge"] == "hr-assistant/hr-records-agent" and hr["caller"] == {"orchestrator": "hr-assistant",
                                                                            "display_name": "HR Assistant"}
    assert hr["summary"] == "ALLOWED · POST / → HR Records Agent (from HR Assistant) · 200"
    hd = gl.parse_entry(cfg(), theme, hosts, _a2a("FP1"), callers)
    assert hd["edge"] == "hr-records-agent" and hd["caller"]["orchestrator"] is None
    unknown = gl.parse_entry(cfg(), theme, hosts, _a2a("FP9"), callers)
    assert unknown["edge"] is None and unknown["component"] == "hr-records-agent" and unknown["caller"] is None
