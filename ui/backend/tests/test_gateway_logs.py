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
