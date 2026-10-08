"""Local end-to-end test of the three runtimes with the helpdesk theme.

Starts 2 MCP servers + 2 A2A agents (+ fake 403 / Model Armor endpoints) on local ports, then
exercises the orchestrator's tools directly and the __PROBE__ path through an ADK InMemoryRunner.

A2A agents and the LLM test call Gemini on Vertex AI using ADC. Project / region / model come
from config/demo.yaml; override with GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION, MODEL.
Set RUNTIMES_SKIP_LLM=1 to skip the natural-language (LLM-driven) test.

Run:  uv run pytest runtimes/tests -v      (or runtimes/tests/run_local.sh)
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
RUNTIMES = ROOT / "runtimes"
sys.path[:0] = [str(RUNTIMES / "tools"), str(RUNTIMES / "orchestrator")]

from make_specs import component_spec, encode_spec, orchestrator_spec  # noqa: E402

from agdemo_core.themes import edge_ids, load_theme  # noqa: E402

THEME = load_theme("helpdesk")
_cfg = yaml.safe_load((ROOT / "config" / "demo.yaml").read_text())
PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT") or _cfg["environment"]["project_id"]
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION") or _cfg["environment"]["region"]
MODEL = os.environ.get("MODEL") or _cfg.get("models", {}).get("default", "gemini-2.5-flash")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _FakeGateway(BaseHTTPRequestHandler):
    """Answers every request like a gateway denial; /armor/* answers like a Model Armor block."""

    def _reply(self) -> None:
        if self.path.startswith("/armor"):
            body = b'{"error":"Request blocked by Model Armor: prompt injection detected"}'
        else:
            body = b'{"error":"Forbidden: caller lacks iap.egressor on this endpoint"}'
        self.send_response(403)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_DELETE = _reply

    def log_message(self, *args):  # quiet
        pass


def _wait_healthy(url: str, proc: subprocess.Popen, timeout: float = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{url} exited with {proc.returncode}")
        try:
            if httpx.get(f"{url}/healthz", timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise TimeoutError(url)


@pytest.fixture(scope="session")
def stack(tmp_path_factory):
    """Start every component; set ORCHESTRATOR_SPEC; yield {component_id: url}."""
    logs = tmp_path_factory.mktemp("logs")
    procs, urls = [], {}
    base_env = {**os.environ, "GOOGLE_CLOUD_PROJECT": PROJECT, "GOOGLE_CLOUD_LOCATION": LOCATION,
                "GOOGLE_GENAI_USE_VERTEXAI": "TRUE", "MODEL": MODEL}
    for comp in THEME.components:
        port = _free_port()
        url = f"http://127.0.0.1:{port}"
        main = RUNTIMES / ("mcp_server" if comp.kind == "mcp_server" else "a2a_agent") / "main.py"
        env = {**base_env, "PORT": str(port), "PUBLIC_URL": url,
               "COMPONENT_SPEC": encode_spec(component_spec(THEME, comp.id))}
        log = open(logs / f"{comp.id}.log", "w")
        procs.append(subprocess.Popen([sys.executable, str(main)], env=env, stdout=log, stderr=subprocess.STDOUT))
        urls[comp.id] = url

    fake = ThreadingHTTPServer(("127.0.0.1", 0), _FakeGateway)
    threading.Thread(target=fake.serve_forever, daemon=True).start()
    fake_url = f"http://127.0.0.1:{fake.server_address[1]}"

    try:
        for comp, proc in zip(THEME.components, procs):
            _wait_healthy(urls[comp.id], proc)

        spec = orchestrator_spec(THEME, urls, MODEL)
        # Extra components that point at the fake gateway, to check failure classification.
        spec["topology"] += [
            {"id": "denied-agent", "kind": "a2a_agent", "url": fake_url, "probe_message": "hi"},
            {"id": "denied-mcp", "kind": "mcp_server", "url": f"{fake_url}/mcp",
             "tools": [{"name": "get_ticket", "read_only": True, "probe_args": {"ticket_id": "INC-1"}}]},
            {"id": "armor-mcp", "kind": "mcp_server", "url": f"{fake_url}/armor/mcp",
             "tools": [{"name": "get_ticket", "read_only": True, "probe_args": {"ticket_id": "INC-1"}}]},
            {"id": "down-mcp", "kind": "mcp_server", "url": f"http://127.0.0.1:{_free_port()}/mcp", "tools": []},
        ]
        os.environ.update({"ORCHESTRATOR_SPEC": encode_spec(spec), "MODEL": MODEL, "AUTH_MODE": "none",
                           "GOOGLE_CLOUD_PROJECT": PROJECT, "GOOGLE_CLOUD_LOCATION": LOCATION,
                           "GOOGLE_GENAI_USE_VERTEXAI": "TRUE"})
        from orchestrator_agent import spec as spec_mod
        spec_mod.load.cache_clear()
        yield urls
    finally:
        for p in procs:
            p.terminate()
        for p in procs:
            p.wait(10)
        fake.shutdown()


# ------------------------------------------------------------------ unit
def test_classify_failure():
    from orchestrator_agent.remote import classify_failure

    assert classify_failure(403, {}, "Forbidden") == "denied"
    assert classify_failure(403, {}, "Request blocked by Model Armor") == "blocked"
    assert classify_failure(400, {"x-goog-model-armor": "match"}, "") == "blocked"
    assert classify_failure(502, {}, "bad gateway") == "error"


def test_mcp_placeholder_fill():
    sys.path.insert(0, str(RUNTIMES / "mcp_server"))
    from main import fill

    assert fill({"id": "{ticket_id}", "n": ["x {a}"], "k": 1}, {"ticket_id": "T1", "a": 2}) == \
        {"id": "T1", "n": ["x 2"], "k": 1}


# ------------------------------------------------------------------ tools, called directly
def test_list_capabilities(stack):
    from orchestrator_agent.tools import list_capabilities

    caps = list_capabilities()
    assert {"kb-agent", "hr-records-agent"} <= {a["id"] for a in caps["agents"]}
    tickets = next(s for s in caps["mcp_servers"] if s["id"] == "tickets-mcp")
    assert {t["name"] for t in tickets["tools"]} == {"list_tickets", "get_ticket", "close_ticket", "delete_ticket"}


def test_call_mcp_tool_ok(stack):
    from orchestrator_agent.tools import call_mcp_tool

    r = asyncio.run(call_mcp_tool("tickets-mcp", "get_ticket", {"ticket_id": "INC-7"}))
    assert r["outcome"] == "ok", r
    assert r["edge"] == "tickets-mcp:get_ticket" and r["http_status"] == 200
    assert r["result"]["id"] == "INC-7"


def test_call_agent_ok(stack):
    from orchestrator_agent.tools import call_agent

    r = asyncio.run(call_agent("kb-agent", "What is the password policy? One sentence."))
    assert r["outcome"] == "ok", r
    assert "14" in r["result"], r


def test_failures_classified(stack):
    from orchestrator_agent.tools import call_agent, call_mcp_tool

    async def run():
        return await asyncio.gather(
            call_agent("denied-agent", "hi"),
            call_mcp_tool("denied-mcp", "get_ticket", {"ticket_id": "INC-1"}),
            call_mcp_tool("armor-mcp", "get_ticket", {"ticket_id": "INC-1"}),
            call_mcp_tool("down-mcp", "get_ticket", {}),
            call_agent("no-such-agent", "hi"),
        )

    a2a_403, mcp_403, armor, down, unknown = asyncio.run(run())
    assert (a2a_403["outcome"], a2a_403["http_status"]) == ("denied", 403), a2a_403
    assert (mcp_403["outcome"], mcp_403["http_status"]) == ("denied", 403), mcp_403
    assert (armor["outcome"], armor["http_status"]) == ("blocked", 403), armor
    assert down["outcome"] == "error", down
    assert unknown["outcome"] == "error", unknown


# ------------------------------------------------------------------ probe protocol via ADK Runner
async def _ask(text: str):
    from google.adk.runners import InMemoryRunner
    from google.genai import types
    from orchestrator_agent.agent import root_agent

    runner = InMemoryRunner(agent=root_agent, app_name="orchestrator_test")
    session = await runner.session_service.create_session(app_name="orchestrator_test", user_id="u")
    events = []
    async for ev in runner.run_async(user_id="u", session_id=session.id,
                                     new_message=types.Content(role="user", parts=[types.Part(text=text)])):
        events.append(ev)
    return events


def _final_text(events) -> str:
    return "".join(p.text or "" for ev in events if ev.content for p in ev.content.parts or [] if p.text)


def test_probe_all_edges_ok(stack):
    egress = [e for e in edge_ids(THEME) if not e.startswith("ingress:")]
    probe = {"probes": [{"edge": e} for e in egress] + [{"edge": "denied-mcp:get_ticket"}], "malicious": False}
    events = asyncio.run(_ask("__PROBE__ " + json.dumps(probe)))
    text = _final_text(events)
    assert text.startswith("__PROBE_RESULT__ "), text
    results = {r["edge"]: r for r in json.loads(text[len("__PROBE_RESULT__ "):])["results"]}
    for e in egress:
        assert results[e]["outcome"] == "ok", results[e]
    assert results["denied-mcp:get_ticket"]["outcome"] == "denied"
    # No LLM call was made: no event carries usage metadata.
    assert not any(ev.usage_metadata for ev in events)


def test_probe_malicious_still_ok_without_gateway(stack):
    probe = {"probes": [{"edge": "tickets-mcp:get_ticket"}, {"edge": "kb-agent"}], "malicious": True}
    text = _final_text(asyncio.run(_ask("__PROBE__ " + json.dumps(probe))))
    results = json.loads(text[len("__PROBE_RESULT__ "):])["results"]
    assert [r["outcome"] for r in results] == ["ok", "ok"], results
    assert "Ignore all previous instructions" in results[0]["result"]["id"]   # payload reached the tool


@pytest.mark.skipif(os.environ.get("RUNTIMES_SKIP_LLM") == "1", reason="LLM test disabled")
def test_llm_uses_tools(stack):
    events = asyncio.run(_ask("Look up ticket INC-1042 and tell me its priority."))
    calls = [fc for ev in events for fc in ev.get_function_calls()]
    responses = [fr.response for ev in events for fr in ev.get_function_responses()]
    assert any(fc.name == "call_mcp_tool" and fc.args.get("tool") == "get_ticket" for fc in calls), calls
    assert any(r.get("edge") == "tickets-mcp:get_ticket" and r.get("outcome") == "ok" for r in responses), responses
    assert "P3" in _final_text(events)
