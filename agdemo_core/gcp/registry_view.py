"""Read-only view of a theme's Agent Registry entries and who may call them (docs/CONTRACTS.md §10).

Live: lists the registry projections (agents / mcpServers / endpoints) and reads the IAP IAM policy of every
entry the theme uses (roles/iap.egressor = may egress to it through Agent Gateway). Nothing is ever written.
Simulated: the same shape built from the theme files and the set of applied policies.

Projection shapes (agentregistry.googleapis.com/v1, verified):
  agent:      {name, displayName, description, version, protocols:[{type, protocolVersion, interfaces:[{url}]}],
               skills:[{id,name,description,tags}], card:{type, content:<A2A card>}, attributes:{...}}
  mcpServer:  {name, displayName, description, interfaces:[{url}], tools:[{name, description, annotations}]}
  endpoint:   {name, displayName, interfaces:[{url}], attributes:{.../RuntimeReference:{uri: .../services/<sid>}}}
IAP policy:   {version, etag, bindings:[{role, members:[...], condition:{title, expression}}]}
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from ..config import DemoConfig
from ..themes import Policy, Theme
from . import iap, registry
from .rest import Rest

RUNTIME_REF = "agentregistry.googleapis.com/system/RuntimeReference"
SIM_PRINCIPAL = ("principal://agents.global.org-ORG_ID.system.id.goog/resources/aiplatform/projects/"
                 "PROJECT_NUMBER/locations/{region}/reasoningEngines/ENGINE_ID")
SIM_PROJECT_SET = ("principalSet://agents.global.org-ORG_ID.system.id.goog/attribute.platformContainer/"
                   "aiplatform/projects/PROJECT_NUMBER")
EGRESS_POLICY_TYPES = {"a2a_allow", "mcp_server_allow", "mcp_tool_allow"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def console_url(cfg: DemoConfig | None) -> str | None:
    if not cfg:
        return None
    return f"https://console.cloud.google.com/agent-platform/agent-registry?project={cfg.gateway_project}"


def rid(name: str) -> str:
    """Last path segment (the agentregistry-0000... id) — the projection's name uses the project id,
    state.json the project number, so entries are matched on this."""
    return (name or "").rstrip("/").split("/")[-1]


# ---------------------------------------------------------------- members / policies
def member_label(member: str, theme: Theme, principal: str | None,
                 principals: dict[str, str] | None = None) -> tuple[str, str]:
    """(label, kind) for an IAM member. `principals` maps additional orchestrators' principals to their ids
    (CONTRACTS §12), labelled e.g. "HR Assistant (Agent Identity)"."""
    if principal and member == principal:
        return f"{theme.orchestrator.display_name} (Agent Identity)", "orchestrator"
    if principals and member in principals:
        return f"{theme.orchestrator_for(principals[member]).display_name} (Agent Identity)", "orchestrator"
    if member.startswith("principalSet://") and "attribute.platformContainer/aiplatform/projects/" in member:
        return "All agents in project (Agent Identity)", "project_agents"
    if member.startswith("principal://") and "/reasoningEngines/" in member:
        return f"Agent Runtime engine {member.rsplit('/', 1)[-1]} (Agent Identity)", "agent"
    for p, kind in (("user:", "user"), ("serviceAccount:", "service_account"), ("group:", "group"),
                    ("domain:", "domain")):
        if member.startswith(p):
            return member[len(p):], kind
    return member, "other"


def component_policies(theme: Theme, cid: str, source: str | None | bool = False) -> list[Policy]:
    """Egress allow policies targeting `cid`; with `source` (None = primary) only that orchestrator's."""
    return [p for p in theme.policies if p.type in EGRESS_POLICY_TYPES and p.params.get("target") == cid
            and (source is False or (p.params.get("source") or None) == source)]


def match_policy(theme: Theme, cid: str | None, condition: dict | None, source: str | None = None) -> Policy | None:
    """The theme policy that creates a binding with this condition on component `cid` for orchestrator `source`."""
    if not cid:
        return None
    from ..policies.egress_allow import condition as policy_condition

    expr = (condition or {}).get("expression")
    for p in component_policies(theme, cid, source):
        pc = policy_condition(theme, p)
        if (pc is None and not expr) or (pc and expr and pc["expression"] == expr):
            return p
    return None


def parse_access(policy: dict, theme: Theme, cid: str | None, principal: str | None,
                 role: str = iap.EGRESSOR, principals: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """IAP IAM policy -> one entry per (member, binding) holding `role`. `principals`: additional
    orchestrators' {principal: id}."""
    out = []
    for b in policy.get("bindings", []) or []:
        if b.get("role") != role:
            continue
        cond = b.get("condition") or None
        for m in b.get("members", []):
            label, kind = member_label(m, theme, principal, principals)
            src = (principals or {}).get(m)
            pol = match_policy(theme, cid, cond, src) if kind == "orchestrator" else None
            out.append({
                "member": m, "member_label": label, "member_kind": kind, "role": role,
                "condition": ({"title": cond.get("title", ""), "expression": cond.get("expression", "")}
                              if cond else None),
                "policy_id": pol.id if pol else None,
                "policy_text": pol.text if pol else None,
                "note": "platform allowlist (./agdemo bootstrap)" if kind == "project_agents" else None,
            })
    return out


# ---------------------------------------------------------------- projections
def first_url(interfaces: list[dict] | None) -> str | None:
    return next((i.get("url") for i in interfaces or [] if i.get("url")), None)


def _skills(raw: list[dict] | None) -> list[dict[str, Any]]:
    return [{"id": s.get("id", ""), "name": s.get("name") or s.get("id", ""),
             "description": s.get("description", ""), "tags": list(s.get("tags") or [])} for s in raw or []]


def parse_agent(a: dict) -> dict[str, Any]:
    card = ((a.get("card") or {}).get("content")) or {}
    protos = a.get("protocols") or []
    proto = next((p for p in protos if p.get("type") == "A2A_AGENT"), protos[0] if protos else {})
    url = first_url(proto.get("interfaces")) or first_url(card.get("supportedInterfaces")) or card.get("url")
    return {
        "display_name": a.get("displayName") or card.get("name") or rid(a.get("name", "")),
        "resource": a.get("name", ""), "registry_id": rid(a.get("name", "")),
        "description": a.get("description") or card.get("description", ""),
        "url": url, "protocol": proto.get("type"),
        "card": {"name": card.get("name") or a.get("displayName", ""),
                 "description": card.get("description") or a.get("description", ""),
                 "version": card.get("version") or a.get("version"),
                 "protocol_version": proto.get("protocolVersion"),
                 "skills": _skills(card.get("skills") or a.get("skills"))} if (card or a.get("skills")) else None,
        "skills": _skills(a.get("skills") or card.get("skills")),
        "interfaces": [i.get("url") for p in protos for i in p.get("interfaces", []) if i.get("url")],
        "runtime": ((a.get("attributes") or {}).get(RUNTIME_REF) or {}).get("uri"),
    }


def hints(annotations: dict | None) -> dict[str, Any]:
    a = annotations or {}
    ro = bool(a.get("readOnlyHint"))
    return {"read_only": ro, "annotations": {k: a[k] for k in ("readOnlyHint", "destructiveHint",
                                                              "idempotentHint", "openWorldHint") if k in a}}


def parse_mcp(m: dict) -> dict[str, Any]:
    return {
        "display_name": m.get("displayName") or rid(m.get("name", "")),
        "resource": m.get("name", ""), "registry_id": rid(m.get("name", "")),
        "description": m.get("description", ""), "url": first_url(m.get("interfaces")),
        "tools": [{"name": t.get("name", ""), "description": t.get("description", ""),
                   **hints(t.get("annotations"))} for t in m.get("tools", []) or []],
        "runtime": ((m.get("attributes") or {}).get(RUNTIME_REF) or {}).get("uri"),
    }


def parse_endpoint(e: dict) -> dict[str, Any]:
    return {"display_name": e.get("displayName") or rid(e.get("name", "")), "resource": e.get("name", ""),
            "registry_id": rid(e.get("name", "")), "description": e.get("description", ""),
            "url": first_url(e.get("interfaces")),
            "runtime": ((e.get("attributes") or {}).get(RUNTIME_REF) or {}).get("uri")}


def is_platform_endpoint(e: dict, prefix: str, ids: set[str]) -> bool:
    ref = ((e.get("attributes") or {}).get(RUNTIME_REF) or {}).get("uri", "")
    return rid(e.get("name", "")) in ids or f"/services/{prefix}-plat-" in ref


# ---------------------------------------------------------------- theme-derived items
def _theme_agent(theme: Theme, cid: str, url: str | None = None) -> dict[str, Any]:
    spec = theme.a2a_agents[cid]
    skills = [{"id": s.id, "name": s.name, "description": s.description, "tags": list(s.tags)} for s in spec.skills]
    return {"display_name": spec.display_name, "resource": "", "registry_id": "", "description": spec.description,
            "url": url, "protocol": "A2A_AGENT", "interfaces": [url] if url else [], "runtime": None,
            "card": {"name": spec.display_name, "description": spec.description, "version": "1.0.0",
                     "protocol_version": "1.0", "skills": skills}, "skills": skills}


def _theme_mcp(theme: Theme, cid: str, url: str | None = None) -> dict[str, Any]:
    spec = theme.mcp_servers[cid]
    tools = [{"name": t.name, "description": t.description, **hints(
        {"readOnlyHint": True, "idempotentHint": True} if t.read_only else {"destructiveHint": True})}
        for t in spec.tools]
    return {"display_name": spec.display_name, "resource": "", "registry_id": "", "description": spec.description,
            "url": url, "tools": tools, "runtime": None}


def _orch_item(theme: Theme, source: str | None = None) -> dict[str, Any]:
    o = theme.orchestrator_for(source)
    return {"display_name": o.display_name, "resource": "", "registry_id": "", "description": o.description,
            "url": None, "protocol": "CUSTOM", "interfaces": [], "runtime": None, "card": None, "skills": []}


def _wrap(cid: str, kind: str, item: dict[str, Any], label: str | None = None) -> dict[str, Any]:
    return {"id": cid, "kind": kind, **item, **({"label": label} if label else {}),
            "access": None, "access_error": None, "error": None}


# ---------------------------------------------------------------- simulated
def simulate(theme: Theme, applied: set[str], cfg: DemoConfig | None, state: dict[str, Any] | None,
             reason: str | None = None) -> dict[str, Any]:
    """Demo-mode view: the bindings the applied policies would create, entries from the theme files."""
    from ..policies.egress_allow import condition as policy_condition

    region = cfg.region if cfg else "us-east4"
    tstate = (state or {}).get("themes", {}).get(theme.id, {})
    comps = tstate.get("components", {})
    principal = tstate.get("orchestrator", {}).get("principal") or SIM_PRINCIPAL.format(region=region)
    extra = tstate.get("orchestrators") or {}
    srcs = {o.id: (extra.get(o.id) or {}).get("principal")
            or SIM_PRINCIPAL.format(region=region).replace("ENGINE_ID", f"ENGINE_ID_{o.id.upper().replace('-', '_')}")
            for o in theme.additional_orchestrators}
    principals = {v: k for k, v in srcs.items()}

    def access_for(cid: str) -> list[dict[str, Any]]:
        pol = {"bindings": []}
        for p in component_policies(theme, cid):
            if p.id not in applied:
                continue
            src = p.params.get("source") or None
            b: dict[str, Any] = {"role": iap.EGRESSOR, "members": [srcs[src] if src else principal]}
            cond = policy_condition(theme, p)
            if cond:
                b["condition"] = cond
            pol["bindings"].append(b)
        return parse_access(pol, theme, cid, principal, principals=principals)

    agents, mcps = [], []
    for c in theme.components:
        url = comps.get(c.id, {}).get("url")
        if c.kind == "a2a_agent":
            it = _wrap(c.id, "a2a_agent", _theme_agent(theme, c.id, url))
            agents.append(it)
        else:
            it = _wrap(c.id, "mcp_server", _theme_mcp(theme, c.id, url and f"{url}/mcp"))
            mcps.append(it)
        it["resource"] = comps.get(c.id, {}).get("registry", "")
        it["registry_id"] = rid(it["resource"])
        it["access"] = access_for(c.id)
    orch = _wrap("orchestrator", "orchestrator", _orch_item(theme), "auto-registered (Agent Runtime)")
    orch["resource"] = tstate.get("orchestrator", {}).get("registry", "")
    orch["registry_id"] = rid(orch["resource"])
    orch["access"] = []
    agents.append(orch)
    for o in theme.additional_orchestrators:          # CONTRACTS §12: each has its own auto-registered entry
        it = _wrap(f"orchestrator:{o.id}", "orchestrator", _orch_item(theme, o.id), "auto-registered (Agent Runtime)")
        it["resource"] = (extra.get(o.id) or {}).get("registry", "")
        it["registry_id"] = rid(it["resource"])
        it["access"] = []
        agents.append(it)

    ids = (state or {}).get("shared", {}).get("platform_registry_ids") or {}
    hosts = sorted(ids) or [f"https://{h}" for h in _default_platform_hosts(region)]
    ps = SIM_PROJECT_SET
    eps = []
    for h in hosts:
        e = _wrap(h, "endpoint", {"display_name": f"{cfg.prefix if cfg else 'agdemo'} {h.split('//')[-1]}",
                                  "resource": ids.get(h, ""), "registry_id": rid(ids.get(h, "")),
                                  "description": "platform endpoint (default-deny allowlist)", "url": h,
                                  "runtime": None})
        e["access"] = parse_access({"bindings": [{"role": iap.EGRESSOR, "members": [ps]}]}, theme, None, principal)
        eps.append(e)
    out = {"theme": theme.id, "source": "simulated", "fetched_at": now_iso(), "console_url": console_url(cfg),
           "orchestrator": {"display_name": theme.orchestrator.display_name, "principal": principal},
           "additional_orchestrators": [{"id": o.id, "display_name": o.display_name, "principal": srcs[o.id]}
                                        for o in theme.additional_orchestrators],
           "agents": agents, "mcp_servers": mcps, "endpoints": {"count": len(eps), "items": eps}}
    if reason:
        out["error"] = reason
    return out


def _default_platform_hosts(region: str) -> list[str]:
    return [f"{region}-aiplatform.googleapis.com", "aiplatform.googleapis.com", "agentregistry.googleapis.com",
            "logging.googleapis.com", "telemetry.googleapis.com", "cloudtrace.googleapis.com",
            "monitoring.googleapis.com", "cloudresourcemanager.googleapis.com", "iamcredentials.googleapis.com",
            "secretmanager.googleapis.com"]


# ---------------------------------------------------------------- live
def _err(e: Exception) -> str:
    return f"{type(e).__name__}: {getattr(e, 'message', None) or e}"[:400]


def fetch(r: Rest, cfg: DemoConfig, state: dict[str, Any], theme: Theme,
          project_number: Callable[[], str] | None = None, workers: int = 12) -> dict[str, Any]:
    """Live view (read-only: list/get registry resources + IAP getIamPolicy). Partial failures are reported
    per item (`error` / `access_error`) instead of failing the whole view."""
    parent = cfg.registry_parent
    tstate = state.get("themes", {}).get(theme.id, {})
    comps = tstate.get("components", {})
    orch_state = tstate.get("orchestrator", {})
    principal = orch_state.get("principal")
    extra = tstate.get("orchestrators") or {}
    principals = {v["principal"]: k for k, v in extra.items() if (v or {}).get("principal")}
    shared_ids = state.get("shared", {}).get("platform_registry_ids") or {}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        lists = {k: pool.submit(registry.list_projection, r, parent, k) for k in ("agents", "mcpServers", "endpoints")}
        listed: dict[str, list[dict]] = {}
        list_err: dict[str, str] = {}
        for k, f in lists.items():
            try:
                listed[k] = f.result()
            except Exception as e:  # noqa: BLE001
                listed[k], list_err[k] = [], _err(e)
        by_id = {k: {rid(x.get("name", "")): x for x in v} for k, v in listed.items()}

        num = None
        for c in comps.values():
            if c.get("registry", "").startswith("projects/"):
                num = c["registry"].split("/")[1]
                break
        if not num or not num.isdigit():
            num = project_number() if project_number else None

        def iap_url(kind: str, xid: str) -> str:
            return iap.resource_url(num or cfg.gateway_project, cfg.region, kind, xid)

        items: list[tuple[dict[str, Any], str, str, str | None]] = []   # (item, kind, id, component id)
        agents, mcps = [], []
        for c in theme.components:
            cs = comps.get(c.id, {})
            xid = rid(cs.get("registry", ""))
            kind = "agents" if c.kind == "a2a_agent" else "mcpServers"
            raw = by_id[kind].get(xid) if xid else None
            if c.kind == "a2a_agent":
                base = parse_agent(raw) if raw else _theme_agent(theme, c.id, cs.get("url"))
                it = _wrap(c.id, "a2a_agent", base)
                agents.append(it)
            else:
                base = parse_mcp(raw) if raw else _theme_mcp(theme, c.id, cs.get("url") and f"{cs['url']}/mcp")
                it = _wrap(c.id, "mcp_server", base)
                mcps.append(it)
            if not xid:
                it["error"] = "not registered (no registry entry in state.json; deploy the theme)"
                continue
            if raw is None:
                it["resource"], it["registry_id"] = cs.get("registry", ""), xid
                it["error"] = list_err.get(kind) or "registry entry not found (showing the theme definition)"
            items.append((it, kind, xid, c.id))

        # The primary orchestrator's auto-registered entry, then each additional orchestrator's (CONTRACTS §12).
        for src, ost in [(None, orch_state), *((o.id, extra.get(o.id) or {}) for o in theme.additional_orchestrators)]:
            orch_rr = ost.get("registry") or ""
            orch_raw = by_id["agents"].get(rid(orch_rr)) if orch_rr else None
            if orch_raw is None and ost.get("engine"):
                eid = rid(ost["engine"])
                orch_raw = next((a for a in listed["agents"] if ((a.get("attributes") or {}).get(RUNTIME_REF) or {})
                                 .get("uri", "").endswith(f"/reasoningEngines/{eid}")), None)
            orch = _wrap(f"orchestrator:{src}" if src else "orchestrator", "orchestrator",
                         parse_agent(orch_raw) if orch_raw else _orch_item(theme, src),
                         "auto-registered (Agent Runtime)")
            agents.append(orch)
            if orch_raw:
                items.append((orch, "agents", rid(orch_raw["name"]), None))
            else:
                orch["error"] = ("orchestrator not found in Agent Registry" if ost else
                                 ("theme not deployed" if src is None else f"{src} not deployed"))

        eps = []
        pids = {rid(v) for v in shared_ids.values()}
        for e in listed["endpoints"]:
            if is_platform_endpoint(e, cfg.prefix, pids):
                it = _wrap(first_url(e.get("interfaces")) or rid(e["name"]), "endpoint", parse_endpoint(e))
                eps.append(it)
                items.append((it, "endpoints", rid(e["name"]), None))
        if not listed["endpoints"] and list_err.get("endpoints"):
            for h, rr in sorted(shared_ids.items()):
                it = _wrap(h, "endpoint", {"display_name": h.split("//")[-1], "resource": rr, "registry_id": rid(rr),
                                           "description": "", "url": h, "runtime": None})
                it["error"] = list_err["endpoints"]
                eps.append(it)
                items.append((it, "endpoints", rid(rr), None))
        eps.sort(key=lambda x: x.get("url") or "")

        futs = {pool.submit(iap.get_policy, r, iap_url(kind, xid)): (it, cid) for it, kind, xid, cid in items}
        for f, (it, cid) in futs.items():
            try:
                it["access"] = parse_access(f.result() or {}, theme, cid, principal, principals=principals)
            except Exception as e:  # noqa: BLE001
                it["access_error"] = _err(e)

    out = {"theme": theme.id, "source": "live", "fetched_at": now_iso(), "console_url": console_url(cfg),
           "orchestrator": {"display_name": theme.orchestrator.display_name, "principal": principal},
           "additional_orchestrators": [{"id": o.id, "display_name": o.display_name,
                                         "principal": (extra.get(o.id) or {}).get("principal")}
                                        for o in theme.additional_orchestrators],
           "agents": agents, "mcp_servers": mcps, "endpoints": {"count": len(eps), "items": eps}}
    if list_err:
        out["error"] = "; ".join(f"list {k}: {v}" for k, v in list_err.items())
    return out


__all__ = ["fetch", "simulate", "parse_access", "parse_agent", "parse_mcp", "parse_endpoint", "member_label",
           "match_policy", "console_url"]
