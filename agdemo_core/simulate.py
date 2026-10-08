"""Policy simulator: the expected edge states for a theme (docs/CONTRACTS.md §3) and synthesized
test runs used by Demo mode and the Live-with-fallback replay (§8).

Pure functions, no GCP access.
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from .themes import INGRESS_EDGE, Policy, ScenarioTest, Theme, edge_ids

EdgeState = dict[str, Any]

INGRESS_EDGES = (INGRESS_EDGE,)


# ---------------------------------------------------------------- helpers
def edge_state(state: str, *, governed: bool, source: str = "simulated", detail: str = "",
               http_status: int | None = None) -> EdgeState:
    return {"state": state, "governed": governed, "source": source, "detail": detail,
            "http_status": http_status}


def _applied_policies(theme: Theme, applied: Iterable[str]) -> list[Policy]:
    ids = set(applied)
    return [p for p in theme.policies if p.id in ids]


def gateway_applied(theme: Theme, applied: Iterable[str], path: str) -> bool:
    return any(p.type == "gateway_attach" and p.params.get("path", "egress") == path
               for p in _applied_policies(theme, applied))


def is_ingress_edge(edge: str) -> bool:
    return edge.startswith("ingress:")


def split_edge(edge: str) -> tuple[str, str | None]:
    """'tickets-mcp:get_ticket' -> ('tickets-mcp', 'get_ticket'); 'kb-agent' -> ('kb-agent', None)."""
    if ":" in edge:
        a, b = edge.split(":", 1)
        return a, b
    return edge, None


def egress_edge_ids(theme: Theme) -> list[str]:
    return [e for e in edge_ids(theme) if not is_ingress_edge(e)]


def _tool_read_only(theme: Theme, server: str, tool: str) -> bool:
    spec = theme.mcp_servers.get(server)
    if not spec:
        return False
    return next((t.read_only for t in spec.tools if t.name == tool), False)


def _egress_allowing_policy(theme: Theme, pols: list[Policy], edge: str) -> Policy | None:
    target, tool = split_edge(edge)
    for p in pols:
        if p.params.get("target") != target:
            continue
        if tool is None and p.type == "a2a_allow":
            return p
        if tool is not None and p.type == "mcp_server_allow":
            return p
        if tool is not None and p.type == "mcp_tool_allow":
            if p.params.get("read_only") and _tool_read_only(theme, target, tool):
                return p
            if tool in (p.params.get("tools") or []):
                return p
    return None


def edge_label(theme: Theme, edge: str) -> str:
    """Human label for an edge, e.g. 'Tickets MCP · get_ticket'."""
    return _label(theme, edge)


def _label(theme: Theme, edge: str) -> str:
    target, tool = split_edge(edge)
    if target == "ingress":
        return "User"
    spec = theme.a2a_agents.get(target) or theme.mcp_servers.get(target)
    name = spec.display_name if spec else target
    return f"{name} · {tool}" if tool else name


# ---------------------------------------------------------------- §3 evaluate
def evaluate(theme: Theme, applied: Iterable[str], model_armor: bool,
             test: ScenarioTest | None = None) -> dict[str, EdgeState]:
    """Expected state of every edge of `theme` (docs/CONTRACTS.md §3)."""
    applied = set(applied)
    pols = _applied_policies(theme, applied)
    malicious = bool(test and test.malicious)
    egress_on = gateway_applied(theme, applied, "egress")
    ingress_on = gateway_applied(theme, applied, "ingress")
    out: dict[str, EdgeState] = {}

    def armor(st: EdgeState, gw: str) -> EdgeState:
        if model_armor and malicious and st["state"] == "allowed":
            return edge_state("blocked", governed=True, http_status=403,
                              detail=f"Model Armor on the {gw} gateway blocked the request "
                                     "(prompt injection / sensitive data)")
        return st

    for e in edge_ids(theme):
        if is_ingress_edge(e):
            # Ingress gateways screen content (Model Armor); they never deny a request on other grounds.
            if not ingress_on:
                out[e] = edge_state("direct", governed=False, http_status=200,
                                    detail="User calls the agent directly; no ingress gateway")
            else:
                out[e] = armor(edge_state("allowed", governed=True, http_status=200,
                                          detail="Through the ingress gateway"
                                                 + (" (screened by Model Armor)" if model_armor else "")),
                               "ingress")
            continue

        if not egress_on:
            out[e] = edge_state("direct", governed=False, http_status=200,
                                detail="Direct call; no Agent Gateway attached")
        elif (p := _egress_allowing_policy(theme, pols, e)):
            st = edge_state("allowed", governed=True, http_status=200,
                            detail=f"Allowed by Agent Gateway ({p.id})")
            # Verified behaviour: egress Model Armor screens MCP tools/call traffic, not A2A messages.
            out[e] = armor(st, "egress") if ":" in e else st
        else:
            out[e] = edge_state("denied", governed=True, http_status=403,
                                detail="403 from gateway: iap.egressor missing for "
                                       f"{_label(theme, e)}")
    return out


# ---------------------------------------------------------------- §8 signature
def signature(applied: Iterable[str], model_armor: bool) -> str:
    """Sorted applied policy ids (or 'none') + 'ma-on'|'ma-off', joined with '+'.

    e.g. signature({"gw-egress","allow-kb"}, False) == "allow-kb+gw-egress+ma-off";
         signature(set(), True) == "none+ma-on"
    """
    ids = sorted(set(applied)) or ["none"]
    return "+".join([*ids, "ma-on" if model_armor else "ma-off"])


def test_signature(theme: Theme, scenario: Any, test: ScenarioTest, applied: Iterable[str],
                   model_armor: bool) -> str:
    """Recording key for a test: only the policies that can affect the test's own connections, and the
    Model Armor flag only for malicious tests (benign prompts behave the same either way).

    e.g. "Ask for a salary" (hr-records-agent) with every helpdesk policy + Model Armor applied
         -> "allow-hr+gw-egress+ma-any"; at step 1 -> "none+ma-any".
    """
    edges = test_edges(theme, scenario, test)
    egress = any(not is_ingress_edge(e) for e in edges)
    ingress = any(is_ingress_edge(e) for e in edges)
    keep = []
    for pid in set(applied):
        try:
            p = theme.policy(pid)
        except StopIteration:
            continue
        prm = p.params
        if p.type == "gateway_attach":
            hit = egress if prm.get("path", "egress") == "egress" else ingress
        elif p.type == "a2a_allow":
            hit = prm.get("target") in edges
        else:   # mcp_server_allow / mcp_tool_allow
            hit = any(e.startswith(f"{prm.get('target')}:") for e in edges)
        if hit:
            keep.append(pid)
    ids = sorted(keep) or ["none"]
    ma = ("ma-on" if model_armor else "ma-off") if test.malicious else "ma-any"
    return "+".join([*ids, ma])


# ---------------------------------------------------------------- tests
def find_test(theme: Theme, test_id: str, scenario_id: str | None = None) -> tuple[Any, ScenarioTest]:
    """(scenario, test) for a test id. Test ids may repeat across scenarios; the first match wins
    unless `scenario_id` is given."""
    for s in theme.scenarios:
        if scenario_id and s.id != scenario_id:
            continue
        for t in s.tests:
            if t.id == test_id:
                return s, t
    raise KeyError(test_id)


def test_is_ingress(scenario: Any, test: ScenarioTest) -> bool:
    return getattr(scenario, "flow", "egress") == "ingress" or any(is_ingress_edge(p.edge) for p in test.probes)


def test_edges(theme: Theme, scenario: Any, test: ScenarioTest) -> list[str]:
    """Edges a test exercises: its probes, else (ingress) the user edge, else every egress edge."""
    if test.probes:
        return [p.edge for p in test.probes]
    if test_is_ingress(scenario, test):
        return [INGRESS_EDGE]
    return egress_edge_ids(theme)


def _render(value: Any, args: dict[str, Any]) -> Any:
    if isinstance(value, str):
        try:
            return value.format(**args)
        except (KeyError, IndexError, ValueError):
            return value
    if isinstance(value, list):
        return [_render(v, args) for v in value]
    if isinstance(value, dict):
        return {k: _render(v, args) for k, v in value.items()}
    return value


def mock_result(theme: Theme, edge: str) -> Any:
    """The mock payload a successful call on `edge` returns (MCP tool response rendered with probe_args)."""
    target, tool = split_edge(edge)
    if tool and target in theme.mcp_servers:
        t = next((t for t in theme.mcp_servers[target].tools if t.name == tool), None)
        if t is not None:
            return _render(t.response, t.probe_args)
    if target in theme.a2a_agents:
        return f"{theme.a2a_agents[target].display_name} answered."
    return None


def _short(v: Any, n: int = 160) -> str:
    s = v if isinstance(v, str) else json.dumps(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def edge_sentence(theme: Theme, edge: str, st: EdgeState) -> str:
    name = _label(theme, edge)
    state = st.get("state")
    if state in ("direct", "allowed"):
        via = "through Agent Gateway" if state == "allowed" else "directly (no gateway)"
        res = mock_result(theme, edge)
        tail = f": {_short(res)}" if res is not None and split_edge(edge)[1] else "."
        return f"✓ {name} responded {via}{tail}"
    if state == "denied":
        return f"✕ {name}: access was blocked by Agent Gateway policy (403)."
    if state == "blocked":
        return f"⛨ {name}: Model Armor blocked the request (prompt injection / sensitive data)."
    if state == "pending":
        return f"… {name}: policy change still propagating."
    return f"! {name}: call failed ({st.get('detail') or state})."


def synth_message(theme: Theme, scenario: Any, test: ScenarioTest, edges: dict[str, EdgeState]) -> str:
    """A short agent reply summarizing the outcome of each exercised edge."""
    agent = theme.orchestrator.display_name
    if test_is_ingress(scenario, test):
        st = edges.get(INGRESS_EDGE, {}).get("state")
        if st in ("allowed", "direct"):
            via = "through the ingress gateway" if st == "allowed" else "directly (no gateway)"
            answer = test.sample_replies.get(INGRESS_EDGE)
            return (f"{agent} received the request {via} and answered: {answer}" if answer
                    else f"{agent} received the request {via} and answered: “{test.prompt}” → handled.")
        if st == "blocked":
            return (f"Model Armor on the ingress gateway blocked the request (403) before it reached {agent}: "
                    "prompt injection / sensitive data.")
        return f"The call failed: {edges.get(INGRESS_EDGE, {}).get('detail', st)}."
    lines = [edge_sentence(theme, e, edges[e]) for e in test_edges(theme, scenario, test) if e in edges]
    ok = sum(1 for e in edges.values() if e.get("state") in ("direct", "allowed"))
    head = (f"I tried {len(lines)} connection{'s' if len(lines) != 1 else ''}; {ok} worked."
            if len(lines) > 1 else "")
    # What the remote agents said (simulated): only for calls that went through.
    replies = [f"{_label(theme, e)} replied: {test.sample_replies[e]}" for e in test_edges(theme, scenario, test)
               if e in edges and edges[e].get("state") in ("direct", "allowed") and test.sample_replies.get(e)]
    return "\n".join([h for h in [head] if h] + lines + replies)


def synth_events(theme: Theme, scenario: Any, test: ScenarioTest, applied: Iterable[str],
                 model_armor: bool, source: str = "simulated") -> list[tuple[float, dict[str, Any]]]:
    """[(delay_seconds_before, sse_event)] for a simulated run of `test` (§7 event shapes)."""
    full = evaluate(theme, applied, model_armor, test)
    edges = test_edges(theme, scenario, test)
    result: dict[str, EdgeState] = {}
    ev: list[tuple[float, dict[str, Any]]] = []
    agent = theme.orchestrator.display_name
    if test_is_ingress(scenario, test):
        ev.append((0.0, {"type": "status",
                         "text": f"Calling {agent} through the ingress path..."}))
    else:
        ev.append((0.0, {"type": "status", "text": f"Calling {theme.orchestrator.id} via Agent Runtime..."}))
    ev.append((0.3, {"type": "message", "role": "user", "text": test.prompt}))
    first = True
    for e in edges:
        if e not in full:
            continue
        st = {**full[e], "source": source}
        result[e] = st
        ev.append((0.9 if first else 0.55, {"type": "edge", "edge": e, "state": st}))
        first = False
    ev.append((0.6, {"type": "message", "role": "agent", "text": synth_message(theme, scenario, test, result)}))
    ev.append((0.2, {"type": "done", "edges": result}))
    return ev
