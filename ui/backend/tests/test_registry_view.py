"""Agent Registry view (CONTRACTS §10): projection / IAP policy parsing and the Demo-mode endpoint."""
from agdemo_core.config import DemoConfig
from agdemo_core.gcp import iap
from agdemo_core.gcp import registry_view as rv
from agdemo_core.policies.egress_allow import condition
from agdemo_core.themes import load_theme
from test_api import client  # noqa: F401  (shared TestClient fixture)

PRINCIPAL = ("principal://agents.global.org-1.system.id.goog/resources/aiplatform/projects/42/locations/"
             "us-east4/reasoningEngines/77")
PROJECT_SET = "principalSet://agents.global.org-1.system.id.goog/attribute.platformContainer/aiplatform/projects/42"

AGENT = {
    "name": "projects/p/locations/us-east4/agents/agentregistry-kb",
    "displayName": "KB Agent", "description": "Answers questions.", "version": "1.0.0",
    "protocols": [{"type": "A2A_AGENT", "protocolVersion": "1.0",
                   "interfaces": [{"url": "https://kb.run.app", "protocolBinding": "JSONRPC"}]}],
    "skills": [{"id": "search_kb", "name": "Search knowledge base", "description": "Find articles.",
                "tags": ["it", "kb"], "examples": ["x"]}],
    "attributes": {rv.RUNTIME_REF: {"uri": "//agentregistry.googleapis.com/projects/42/locations/us-east4/"
                                           "services/agdemo-helpdesk-kb-agent"}},
    "card": {"type": "A2A_AGENT_CARD", "content": {
        "name": "KB Agent", "description": "Answers questions.", "version": "1.0.0",
        "supportedInterfaces": [{"protocolBinding": "JSONRPC", "url": "https://kb.run.app", "protocolVersion": "1.0"}],
        "skills": [{"id": "search_kb", "name": "Search knowledge base", "description": "Find articles.",
                    "tags": ["it", "kb"]}]}},
}
MCP = {
    "name": "projects/p/locations/us-east4/mcpServers/agentregistry-tickets",
    "displayName": "agdemo helpdesk Tickets MCP", "description": "IT support ticketing system",
    "interfaces": [{"url": "https://tickets.run.app/mcp", "protocolBinding": "JSONRPC"}],
    "tools": [{"name": "list_tickets", "description": "List.", "annotations": {"idempotentHint": True, "readOnlyHint": True}},
              {"name": "delete_ticket", "description": "Delete.", "annotations": {"destructiveHint": True}}],
}
ENDPOINT = {
    "name": "projects/p/locations/us-east4/endpoints/agentregistry-ep",
    "displayName": "agdemo agentregistry.googleapis.com",
    "interfaces": [{"url": "https://agentregistry.googleapis.com", "protocolBinding": "JSONRPC"}],
    "attributes": {rv.RUNTIME_REF: {"uri": "//agentregistry.googleapis.com/projects/42/locations/us-east4/"
                                           "services/agdemo-plat-agentregistry"}},
}


def test_parse_agent_card_summary():
    a = rv.parse_agent(AGENT)
    assert a["display_name"] == "KB Agent" and a["url"] == "https://kb.run.app"
    assert a["registry_id"] == "agentregistry-kb" and a["card"]["protocol_version"] == "1.0"
    assert a["skills"] == [{"id": "search_kb", "name": "Search knowledge base", "description": "Find articles.",
                            "tags": ["it", "kb"]}]


def test_parse_mcp_hints():
    m = rv.parse_mcp(MCP)
    assert m["url"] == "https://tickets.run.app/mcp"
    lt, dt = m["tools"]
    assert lt["read_only"] is True and lt["annotations"] == {"readOnlyHint": True, "idempotentHint": True}
    assert dt["read_only"] is False and dt["annotations"] == {"destructiveHint": True}


def test_platform_endpoint_filter():
    assert rv.is_platform_endpoint(ENDPOINT, "agdemo", set())
    assert rv.is_platform_endpoint({**ENDPOINT, "attributes": {}}, "agdemo", {"agentregistry-ep"})
    assert not rv.is_platform_endpoint({**ENDPOINT, "attributes": {}}, "agdemo", set())
    assert rv.parse_endpoint(ENDPOINT)["url"] == "https://agentregistry.googleapis.com"


def test_access_matches_theme_policies():
    theme = load_theme("helpdesk")
    hints = condition(theme, theme.policy("tickets-readonly-hints"))
    pol = {"version": 3, "etag": "x", "bindings": [
        {"role": iap.EGRESSOR, "members": [PRINCIPAL], "condition": hints},
        {"role": iap.EGRESSOR, "members": [PRINCIPAL]},
        {"role": iap.EGRESSOR, "members": ["user:someone@example.com"]},
        {"role": "roles/iap.httpsResourceAccessor", "members": [PRINCIPAL]},
    ]}
    acc = rv.parse_access(pol, theme, "tickets-mcp", PRINCIPAL)
    assert len(acc) == 3
    assert acc[0]["member_label"] == "Helpdesk Agent (Agent Identity)" and acc[0]["member_kind"] == "orchestrator"
    assert acc[0]["policy_id"] == "tickets-readonly-hints"
    assert acc[0]["condition"]["expression"] == hints["expression"]
    assert acc[1]["policy_id"] == "tickets-all" and acc[1]["condition"] is None
    assert acc[2]["member_label"] == "someone@example.com" and acc[2]["policy_id"] is None


def test_access_empty_and_project_set():
    theme = load_theme("helpdesk")
    assert rv.parse_access({"version": 1, "etag": "x"}, theme, "kb-agent", PRINCIPAL) == []
    acc = rv.parse_access({"bindings": [{"role": iap.EGRESSOR, "members": [PROJECT_SET]}]}, theme, None, PRINCIPAL)
    assert acc[0]["member_kind"] == "project_agents" and acc[0]["member_label"].startswith("All agents in project")


class FakeRest:
    """Answers registry list calls and IAP getIamPolicy; records every call (all must be reads)."""

    def __init__(self, policies, fail=()):
        self.policies, self.fail, self.calls = policies, set(fail), []

    def list_all(self, url, key, params=None):
        self.calls.append(("LIST", url))
        return {"agents": [AGENT], "mcpServers": [MCP], "endpoints": [ENDPOINT]}[key]

    def post(self, url, json=None, **kw):
        self.calls.append(("POST", url))
        assert url.endswith(":getIamPolicy")
        rid = url.split("/")[-1].split(":")[0]
        if rid in self.fail:
            raise RuntimeError("boom")
        return self.policies.get(rid, {"version": 1, "etag": "x"})


def test_fetch_live_shape_and_partial_failure():
    theme = load_theme("helpdesk")
    cfg = DemoConfig.model_validate({"environment": {"project_id": "p"}})
    state = {"shared": {"platform_registry_ids": {}}, "themes": {"helpdesk": {
        "orchestrator": {"principal": PRINCIPAL, "engine": "projects/42/locations/us-east4/reasoningEngines/77"},
        "components": {
            "kb-agent": {"registry": "projects/42/locations/us-east4/agents/agentregistry-kb", "url": "https://kb.run.app"},
            "tickets-mcp": {"registry": "projects/42/locations/us-east4/mcpServers/agentregistry-tickets"}}}}}
    r = FakeRest({"agentregistry-kb": {"bindings": [{"role": iap.EGRESSOR, "members": [PRINCIPAL]}]}},
                 fail={"agentregistry-tickets"})
    out = rv.fetch(r, cfg, state, theme)
    assert out["source"] == "live" and "agent-registry?project=p" in out["console_url"]
    kb = next(a for a in out["agents"] if a["id"] == "kb-agent")
    assert kb["access"][0]["policy_id"] == "allow-kb"
    hr = next(a for a in out["agents"] if a["id"] == "hr-records-agent")
    assert hr["error"] and hr["skills"]                      # not registered: theme definition + error
    tickets = next(m for m in out["mcp_servers"] if m["id"] == "tickets-mcp")
    assert tickets["access"] is None and "boom" in tickets["access_error"]
    assert out["endpoints"]["count"] == 1 and out["endpoints"]["items"][0]["access"] == []
    assert all(m in ("LIST", "POST") for m, _ in r.calls)
    assert all(u.endswith(":getIamPolicy") and "/projects/42/" in u for m, u in r.calls if m == "POST")


def test_demo_endpoint_shows_applied_bindings(client):
    r = client.get("/api/themes/helpdesk/registry", params={"mode": "demo"}).json()
    assert r["source"] == "simulated"
    assert {a["id"] for a in r["agents"]} == {"kb-agent", "hr-records-agent", "orchestrator", "orchestrator:hr-assistant"}
    assert all(m["access"] == [] for m in r["mcp_servers"])
    tickets = next(m for m in r["mcp_servers"] if m["id"] == "tickets-mcp")
    assert {t["name"]: t["read_only"] for t in tickets["tools"]}["delete_ticket"] is False
    assert r["endpoints"]["count"] > 0
    assert r["endpoints"]["items"][0]["access"][0]["member_kind"] == "project_agents"

    client.post("/api/themes/helpdesk/policies/tickets-readonly-hints", json={"action": "apply", "mode": "demo"})
    client.post("/api/themes/helpdesk/policies/allow-kb", json={"action": "apply", "mode": "demo"})
    r = client.get("/api/themes/helpdesk/registry", params={"mode": "demo"}).json()
    tickets = next(m for m in r["mcp_servers"] if m["id"] == "tickets-mcp")
    (b,) = tickets["access"]
    assert b["policy_id"] == "tickets-readonly-hints" and "mcp.tool.isReadOnly" in b["condition"]["expression"]
    assert b["member_label"] == "Helpdesk Agent (Agent Identity)"
    kb = next(a for a in r["agents"] if a["id"] == "kb-agent")
    assert kb["access"][0]["policy_id"] == "allow-kb" and kb["access"][0]["condition"] is None
    client.post("/api/themes/helpdesk/reset", json={"mode": "demo"})


def test_live_without_config_falls_back_or_409(client):
    r = client.get("/api/themes/helpdesk/registry", params={"mode": "live_with_fallback"}).json()
    assert r["source"] == "simulated" and r["error"].startswith("Live mode unavailable")
    assert client.get("/api/themes/helpdesk/registry", params={"mode": "live"}).status_code == 409
    assert client.get("/api/themes/nope/registry", params={"mode": "demo"}).status_code == 404
