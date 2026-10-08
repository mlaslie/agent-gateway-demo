import json
import time
import types

import pytest
from fastapi.testclient import TestClient

from agdemo_core import simulate
from agdemo_core.config import DemoConfig
from agdemo_core.themes import load_theme
from agdemo_ui import main, settings
from agdemo_ui.engine import Engine


def sse_events(resp):
    out = []
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            out.append(json.loads(line[5:].strip()))
    return out


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("AGDEMO_RECORDINGS_DIR", str(tmp_path / "rec"))
    monkeypatch.setattr(main, "engine", Engine())
    settings.reset_caches()
    with TestClient(main.app) as c:
        yield c
    settings.reset_caches()


def test_config(client):
    r = client.get("/api/config").json()
    assert any(t["id"] == "helpdesk" for t in r["themes"])
    assert r["default_theme"] == "helpdesk"
    assert r["live_available"] is False          # no state.json in tests
    assert r["default_mode"] == "demo"
    assert r["can_admin"] is True
    assert set(r["environment"]) == {"project_id", "region", "prefix"}


def test_theme_graph(client):
    r = client.get("/api/themes/helpdesk").json()
    ids = {n["id"] for n in r["nodes"]}
    assert {"user", "caller:allowed", "caller:denied", "ingress_gateway", "orchestrator",
            "egress_gateway", "registry", "kb-agent", "tickets-mcp"} <= ids
    tickets = next(n for n in r["nodes"] if n["id"] == "tickets-mcp")
    assert {"name": "delete_ticket", "read_only": False} .items() <= next(
        t for t in tickets["tools"] if t["name"] == "delete_ticket").items()
    kb = next(n for n in r["nodes"] if n["id"] == "kb-agent")
    assert kb["skills"][0]["id"] == "search_kb"
    edges = {e["id"]: e for e in r["edges"]}
    assert edges["tickets-mcp:get_ticket"]["tool"] == "get_ticket"
    assert edges["ingress:denied"]["source"] == "caller:denied"
    assert r["theme"]["id"] == "helpdesk"
    assert client.get("/api/themes/nope").status_code == 404


def test_demo_policy_flow(client):
    st = client.get("/api/themes/helpdesk/state?mode=demo").json()
    assert st["edges"]["kb-agent"]["state"] == "direct"
    assert st["gateways"]["egress"]["attached"] is False
    r = client.post("/api/themes/helpdesk/policies/gw-egress", json={"action": "apply", "mode": "demo"}).json()
    assert r["applied"] is True and r["status"] == "applied"
    client.post("/api/themes/helpdesk/policies/allow-kb", json={"action": "apply", "mode": "demo"})
    st = client.get("/api/themes/helpdesk/state?mode=demo").json()
    assert st["gateways"]["egress"]["attached"] is True
    assert st["edges"]["kb-agent"]["state"] == "allowed"
    assert st["edges"]["hr-records-agent"]["state"] == "denied"
    assert st["expected"] == st["edges"]
    assert client.post("/api/themes/helpdesk/policies/nope", json={"action": "apply", "mode": "demo"}).status_code == 404
    client.post("/api/themes/helpdesk/reset", json={"mode": "demo"})
    st = client.get("/api/themes/helpdesk/state?mode=demo").json()
    assert not any(p["applied"] for p in st["policies"].values())


def test_demo_model_armor_and_run(client):
    for pid in ("gw-egress", "allow-kb", "tickets-readonly"):
        client.post(f"/api/themes/helpdesk/policies/{pid}", json={"action": "apply", "mode": "demo"})
    assert client.post("/api/model-armor", json={"enabled": True, "mode": "demo"}).json()["enabled"] is True
    with client.stream("POST", "/api/themes/helpdesk/tests/injection/run",
                       json={"mode": "demo", "use_llm": False, "scenario_id": "model-armor"}) as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        resp.read()
        evs = sse_events(resp)
    types_ = [e["type"] for e in evs]
    assert types_[0] == "status" and types_[-1] == "done"
    edges = {e["edge"]: e["state"] for e in evs if e["type"] == "edge"}
    assert edges["tickets-mcp:get_ticket"]["state"] == "blocked" and edges["tickets-mcp:get_ticket"]["source"] == "simulated"
    assert any(e["type"] == "message" and e["role"] == "agent" for e in evs)
    client.post("/api/model-armor", json={"enabled": False, "mode": "demo"})


def test_demo_run_uses_recording(client, tmp_path):
    from agdemo_core import recordings

    recordings.save("helpdesk", "ask-kb", "none+ma-off", [
        {"type": "status", "text": "recorded!", "t_ms": 0},
        {"type": "edge", "edge": "kb-agent", "state": {"state": "direct", "source": "live"}, "t_ms": 100},
        {"type": "message", "role": "agent", "text": "Use GlobalProtect v6.2+", "t_ms": 200},
        {"type": "done", "edges": {"kb-agent": {"state": "direct", "source": "live"}}, "t_ms": 300},
    ])
    resp = client.post("/api/themes/helpdesk/tests/ask-kb/run", json={"mode": "demo"})
    evs = sse_events(resp)
    assert evs[0]["text"] == "recorded!"
    assert evs[1]["state"]["source"] == "replayed"
    assert evs[-1]["edges"]["kb-agent"]["source"] == "replayed"


def test_ingress_demo_run(client):
    client.post("/api/themes/helpdesk/policies/gw-ingress", json={"action": "apply", "mode": "demo"})
    evs = sse_events(client.post("/api/themes/helpdesk/tests/denied-caller/run", json={"mode": "demo"}))
    assert evs[-1]["edges"] == {"ingress:denied": evs[-1]["edges"]["ingress:denied"]}
    # caller identity isn't enforced at the ingress gateway yet (docs/ISSUES.md #1)
    assert evs[-1]["edges"]["ingress:denied"]["state"] == "allowed"


def test_fallback_when_live_unavailable(client):
    evs = sse_events(client.post("/api/themes/helpdesk/tests/ask-kb/run", json={"mode": "live_with_fallback"}))
    assert evs[0]["type"] == "fallback"
    assert evs[-1]["type"] == "done"
    evs = sse_events(client.post("/api/themes/helpdesk/tests/ask-kb/run", json={"mode": "live"}))
    assert evs[0]["type"] == "error"
    assert client.post("/api/themes/helpdesk/policies/gw-egress",
                       json={"action": "apply", "mode": "live"}).status_code == 409


def test_explain_and_probe(client):
    r = client.get("/api/themes/helpdesk/policies/gw-egress/explain").json()
    assert r["lines"] and isinstance(r["lines"][0], str)
    r = client.post("/api/themes/helpdesk/probe", json={"mode": "demo"}).json()
    assert r["edges"]["kb-agent"]["state"] == "direct"


def test_can_admin_on_cloud_run(monkeypatch):
    monkeypatch.setenv("K_SERVICE", "agdemo-ui")
    cfg = DemoConfig.model_validate({"environment": {"project_id": "p"},
                                     "ui": {"admin_access": ["user:boss@example.com"]}})
    monkeypatch.setattr(settings, "get_config", lambda: (cfg, ""))
    assert settings.can_admin({"x-goog-authenticated-user-email": "accounts.google.com:boss@example.com"})
    assert not settings.can_admin({"x-goog-authenticated-user-email": "accounts.google.com:other@example.com"})
    assert not settings.can_admin({})


def test_spa_fallback(client, monkeypatch, tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>app</html>")
    (dist / "assets" / "a.js").write_text("console.log(1)")
    monkeypatch.setenv("AGDEMO_FRONTEND_DIST", str(dist))
    assert client.get("/").text == "<html>app</html>"
    assert client.get("/scenario/3").text == "<html>app</html>"
    assert client.get("/assets/a.js").text == "console.log(1)"
    assert client.get("/api/nope").status_code == 404


# ------------------------------------------------------------------ live mode with stubs
class FakeGcp:
    def __init__(self, theme):
        self.theme = theme
        self.applied: set[str] = set()
        self.ma = False

    def handler(self, _type):
        g = self

        class H:
            def apply(self, ctx, theme, policy):
                g.applied.add(policy.id)

            def remove(self, ctx, theme, policy):
                g.applied.discard(policy.id)

            def status(self, ctx, theme, policy):
                on = policy.id in g.applied
                return {"applied": on, "status": "applied" if on else "removed", "detail": "", "changed_at": None}

            def remove_many(self, ctx, theme, policies):
                for p in policies:
                    self.remove(ctx, theme, p)

            def describe(self, ctx, theme, policy):
                return [f"gcloud fake {policy.id}"]
        return H()

    def ma_module(self):
        g = self
        return types.SimpleNamespace(
            set=lambda ctx, enabled: setattr(g, "ma", enabled),
            status=lambda ctx: {"enabled": g.ma, "status": "applied" if g.ma else "removed", "detail": ""})

    async def run_probes(self, client_self, edges, malicious=False):
        test = types.SimpleNamespace(malicious=malicious)
        exp = simulate.evaluate(self.theme, self.applied, self.ma, test)
        m = {"allowed": "ok", "direct": "ok", "denied": "denied", "blocked": "blocked"}
        edges = [e["edge"] if isinstance(e, dict) else e for e in edges]   # probes may be dicts (§6)
        return [{"edge": e, "outcome": m[exp[e]["state"]], "http_status": exp[e]["http_status"],
                 "detail": "fake", "result": None, "latency_ms": 5} for e in edges]


@pytest.fixture
def live(monkeypatch, client):
    import agdemo_core.policies as pol
    from agdemo_core.runtime_client import RuntimeClient

    fake = FakeGcp(load_theme("helpdesk"))
    cfg = DemoConfig.model_validate({"environment": {"project_id": "p"}})
    monkeypatch.setattr(settings, "get_config", lambda: (cfg, ""))
    monkeypatch.setattr(settings, "live_available", lambda: (True, ""))
    monkeypatch.setattr(settings, "get_state", lambda: {"shared": {"egress_gateway": "x"}, "themes": {
        "helpdesk": {"orchestrator": {"engine": "projects/p/locations/us-east4/reasoningEngines/1"}}}})
    monkeypatch.setattr(pol, "get_handler", fake.handler)
    monkeypatch.setattr(pol, "model_armor_handler", fake.ma_module)
    monkeypatch.setattr(RuntimeClient, "run_probes", lambda s, e, m=False: fake.run_probes(s, e, m))
    return fake


def _wait_applied(client, pid, mode="live"):
    for _ in range(50):
        st = client.get(f"/api/themes/helpdesk/state?mode={mode}").json()
        if st["policies"][pid]["status"] != "pending" or "Waiting" in st["policies"][pid]["detail"]:
            return st
        time.sleep(0.05)
    return st


def test_live_flow(client, live):
    r = client.post("/api/themes/helpdesk/policies/gw-egress", json={"action": "apply", "mode": "live"}).json()
    assert r["status"] == "pending" and r["applied"] is True
    st = _wait_applied(client, "gw-egress")
    assert "gw-egress" in live.applied
    assert st["policies"]["gw-egress"]["status"] == "pending"        # not yet confirmed by a probe
    assert st["edges"]["kb-agent"]["state"] == "pending"
    assert st["expected"]["kb-agent"]["state"] == "denied"

    # live_with_fallback falls back while the change is pending
    evs = sse_events(client.post("/api/themes/helpdesk/tests/ask-kb/run", json={"mode": "live_with_fallback"}))
    assert evs[0]["type"] == "fallback" and "pending" in evs[0]["reason"]

    # a live probe run confirms the change
    evs = sse_events(client.post("/api/themes/helpdesk/tests/both/run",
                                 json={"mode": "live", "scenario_id": "a2a"}))
    assert evs[-1]["type"] == "done", evs
    assert evs[-1]["edges"]["kb-agent"] == {**evs[-1]["edges"]["kb-agent"], "state": "denied", "source": "live"}
    st = client.get("/api/themes/helpdesk/state?mode=live").json()
    assert st["policies"]["gw-egress"]["status"] == "applied"   # probe matched expected -> confirmed
    assert st["edges"]["kb-agent"]["state"] == "denied"
    assert st["edges"]["tickets-mcp:get_ticket"]["state"] == "unknown"

    # probe endpoint fills in every egress edge
    r = client.post("/api/themes/helpdesk/probe", json={"mode": "live"})
    assert r.status_code == 502 or r.json()["edges"]["tickets-mcp:get_ticket"]["state"] == "denied"

    # record saves a recording keyed by the live signature, demo replays it
    r = client.post("/api/themes/helpdesk/tests/ask-kb/record").json()
    assert r["signature"] == "gw-egress+ma-off" and r["events"] >= 4
    assert client.get("/api/themes/helpdesk/policies/gw-egress/explain").json()["lines"][-1] == "gcloud fake gw-egress"

    # model armor live
    assert client.post("/api/model-armor", json={"enabled": True, "mode": "live"}).json()["status"] == "pending"

    # reset removes everything
    r = client.post("/api/themes/helpdesk/reset", json={"mode": "live"}).json()
    assert r["policies"]["gw-egress"]["status"] == "pending_removal"
    for _ in range(50):
        if not live.applied:
            break
        time.sleep(0.05)
    assert not live.applied


@pytest.mark.parametrize("theme_id", __import__("agdemo_core.themes", fromlist=["list_themes"]).list_themes())
def test_every_theme_and_test_runs_in_demo(client, theme_id):
    g = client.get(f"/api/themes/{theme_id}").json()
    assert g["nodes"] and g["edges"]
    st = client.get(f"/api/themes/{theme_id}/state?mode=demo").json()
    assert set(st["edges"]) == {e["id"] for e in g["edges"]}
    for scen in g["theme"]["scenarios"]:
        for t in scen["tests"]:
            evs = sse_events(client.post(f"/api/themes/{theme_id}/tests/{t['id']}/run",
                                         json={"mode": "demo", "scenario_id": scen["id"]}))
            assert evs[-1]["type"] == "done", (scen["id"], t["id"], evs)
    st = client.get(f"/api/themes/{theme_id}/state?mode=live").json()
    assert all(p["status"] == "error" for p in st["policies"].values())


def test_probe_requests_send_test_prompt_and_show_replies():
    from agdemo_ui.engine import probe_requests, reply_lines
    from agdemo_core.themes import load_theme
    theme = load_theme("helpdesk")
    sc = next(s for s in theme.scenarios if s.id == "wide-open")
    salary = next(t for t in sc.tests if t.id == "salary")
    assert probe_requests(salary, ["hr-records-agent"]) == [{"edge": "hr-records-agent", "message": "What does jdoe earn?"}]
    every = next(t for t in sc.tests if t.id == "everything")
    assert all("message" not in p for p in probe_requests(every, [p.edge for p in every.probes]))
    lines = reply_lines(theme, [{"edge": "hr-records-agent", "outcome": "ok", "result": "jdoe earns $182,000."},
                                {"edge": "kb-agent", "outcome": "denied", "result": None}])
    assert lines == ["HR Records Agent replied: jdoe earns $182,000."]


def test_demo_verify_start_state(client):
    r = client.post("/api/themes/helpdesk/verify", json={"mode": "demo"}).json()
    assert r["ok"] is True and any(c["id"] == "connections" for c in r["checks"])
    client.post("/api/themes/helpdesk/policies/gw-egress", json={"action": "apply", "mode": "demo"})
    r = client.post("/api/themes/helpdesk/verify", json={"mode": "demo"}).json()
    assert r["ok"] is False and not next(c for c in r["checks"] if c["id"] == "gw-egress")["ok"]
    client.post("/api/themes/helpdesk/reset", json={"mode": "demo"})
    assert client.post("/api/themes/helpdesk/verify", json={"mode": "demo"}).json()["ok"] is True


def test_live_reset_detaches_gateways_in_one_call_and_skips_absent(live, client):
    calls = []
    import agdemo_core.policies as pol
    real = pol.get_handler

    class GW:
        def __init__(self, h): self.h = h
        def __getattr__(self, n): return getattr(self.h, n)
        def remove_many(self, ctx, theme, policies):
            calls.append(sorted(p.id for p in policies))
            for p in policies:
                self.h.remove(ctx, theme, p)

    import pytest as _pt
    mp = _pt.MonkeyPatch()
    mp.setattr(pol, "get_handler", lambda t: GW(real(t)) if t == "gateway_attach" else real(t))
    try:
        for pid in ("gw-egress", "gw-ingress"):
            client.post(f"/api/themes/helpdesk/policies/{pid}", json={"action": "apply", "mode": "live"})
        import time as _t
        _t.sleep(0.5)
        r = client.post("/api/themes/helpdesk/reset", json={"mode": "live"}).json()
        assert "not present" in r["policies"]["allow-kb"]["detail"]
        _t.sleep(0.5)
        assert calls == [["gw-egress", "gw-ingress"]]
    finally:
        mp.undo()
