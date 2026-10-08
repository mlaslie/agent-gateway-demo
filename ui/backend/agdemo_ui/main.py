"""FastAPI app for the Agent Gateway demo UI (docs/CONTRACTS.md §7).

Run locally:  uv run uvicorn agdemo_ui.main:app --app-dir ui/backend --port 8080
"""
from __future__ import annotations

import asyncio
import os
import json
import logging
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request

from .gateway_logs import SimLogBook
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from agdemo_core import recordings, simulate
from agdemo_core.themes import Theme, list_themes, load_theme

from . import settings
from .engine import Engine, LiveUnavailable
from .graph import build_graph

sim_logs = SimLogBook()
log = logging.getLogger("agdemo_ui")
Mode = Literal["live", "demo", "live_with_fallback"]

app = FastAPI(title="Agent Gateway demo", version="0.1.0")
engine = Engine()


# ------------------------------------------------------------------ bodies
class ModeBody(BaseModel):
    mode: Mode = "demo"


class PolicyBody(BaseModel):
    action: Literal["apply", "remove"]
    mode: Mode = "demo"


class ModelArmorBody(BaseModel):
    enabled: bool
    mode: Mode = "demo"


class RunBody(BaseModel):
    mode: Mode = "demo"
    use_llm: bool = False
    scenario_id: str | None = None      # optional: disambiguates test ids shared by several scenarios


class RecordBody(BaseModel):
    use_llm: bool = False
    scenario_id: str | None = None


# ------------------------------------------------------------------ helpers
def _theme(theme_id: str) -> Theme:
    if theme_id not in list_themes():
        raise HTTPException(404, f"unknown theme {theme_id}")
    try:
        return load_theme(theme_id)
    except Exception as e:
        raise HTTPException(500, f"theme {theme_id} failed to load: {e}") from e


def _policy(theme: Theme, pid: str):
    try:
        return theme.policy(pid)
    except StopIteration:
        raise HTTPException(404, f"unknown policy {pid}") from None


def _test(theme: Theme, test_id: str, scenario_id: str | None):
    try:
        return simulate.find_test(theme, test_id, scenario_id)
    except KeyError:
        raise HTTPException(404, f"unknown test {test_id}") from None


def _require_admin(request: Request, mode: str) -> None:
    if mode != "demo" and not settings.can_admin(request.headers):
        raise HTTPException(403, "Live changes are limited to ui.admin_access")


def _enabled_themes() -> list[str]:
    avail = list_themes()
    cfg, _ = settings.get_config()
    if cfg is None:
        return avail
    enabled = [t for t in cfg.themes.enabled if t in avail]
    return enabled or avail


async def _paced(events, pace: float):
    """Strip `_delay` and sleep it (scaled by AGDEMO_PACE); format for sse-starlette."""
    async for e in events:
        d = float(e.pop("_delay", 0.0) or 0.0) * pace
        if d > 0:
            await asyncio.sleep(d)
        yield e


# ------------------------------------------------------------------ routes
@app.get("/api/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def get_config(request: Request) -> dict[str, Any]:
    cfg, cfg_err = settings.get_config()
    state = settings.get_state()
    live_ok, live_why = settings.live_available()
    themes = []
    for tid in _enabled_themes():
        try:
            t = load_theme(tid)
            themes.append({"id": t.id, "name": t.name, "description": t.description,
                           "deployed": settings.theme_deployed(state, tid)})
        except Exception as e:  # a broken theme pack shouldn't take the UI down
            log.warning("theme %s failed to load: %s", tid, e)
    ids = [t["id"] for t in themes]
    default_theme = cfg.themes.default if cfg and cfg.themes.default in ids else (ids[0] if ids else "")
    wanted = os.environ.get("AGDEMO_DEFAULT_MODE") or (cfg.default_mode if cfg else "demo")
    default_mode = wanted if live_ok else "demo"
    return {
        "themes": themes,
        "default_theme": default_theme,
        "default_mode": default_mode,
        "gemini_enterprise": {"app_url": cfg.gemini_enterprise.app_url if cfg else "",
                              "configured": bool(cfg and cfg.gemini_enterprise.app_id)},
        "environment": {"project_id": cfg.project if cfg else "", "region": cfg.region if cfg else "",
                        "prefix": cfg.prefix if cfg else ""},
        "live_available": live_ok,
        "live_unavailable_reason": "" if live_ok else live_why,
        "can_admin": settings.can_admin(request.headers),
    }


@app.get("/api/themes/{theme_id}")
def get_theme(theme_id: str) -> dict[str, Any]:
    theme = _theme(theme_id)
    cfg, _ = settings.get_config()
    return build_graph(theme, cfg, settings.get_state())


@app.get("/api/themes/{theme_id}/state")
async def get_state(theme_id: str, mode: Mode | None = None) -> dict[str, Any]:
    theme = _theme(theme_id)
    mode = mode or "demo"
    if mode == "demo":
        return engine.demo.state(theme)
    try:
        engine.live.check_available(theme)
    except LiveUnavailable as e:
        expected = simulate.evaluate(theme, set(), False)
        err = {"applied": False, "status": "error", "detail": f"Live mode unavailable: {e}", "changed_at": None}
        return {"policies": {p.id: dict(err) for p in theme.policies},
                "gateways": {g: {"attached": False, "status": "error"} for g in ("egress", "ingress")},
                "model_armor": {"enabled": False, "status": "error"},
                "edges": {k: {**v, "state": "unknown", "source": "live", "detail": str(e), "http_status": None}
                          for k, v in expected.items()},
                "expected": expected, "live_error": str(e)}
    return await engine.live.state(theme)


@app.post("/api/themes/{theme_id}/policies/{pid}")
async def set_policy(theme_id: str, pid: str, body: PolicyBody, request: Request) -> dict[str, Any]:
    theme = _theme(theme_id)
    policy = _policy(theme, pid)
    if body.mode == "demo":
        return engine.demo.set_policy(theme.id, policy.id, body.action == "apply")
    _require_admin(request, body.mode)
    try:
        return await engine.live.set_policy(theme, policy, body.action == "apply")
    except LiveUnavailable as e:
        raise HTTPException(409, f"Live mode unavailable: {e}") from e


@app.post("/api/model-armor")
async def set_model_armor(body: ModelArmorBody, request: Request) -> dict[str, Any]:
    if body.mode == "demo":
        engine.demo.model_armor = body.enabled
        return {"enabled": body.enabled, "status": "applied" if body.enabled else "removed"}
    _require_admin(request, body.mode)
    try:
        return await engine.live.set_model_armor(body.enabled)
    except LiveUnavailable as e:
        raise HTTPException(409, f"Live mode unavailable: {e}") from e


@app.post("/api/themes/{theme_id}/tests/{test_id}/run")
async def run_test(theme_id: str, test_id: str, body: RunBody, request: Request):
    theme = _theme(theme_id)
    scenario, test = _test(theme, test_id, body.scenario_id)
    pace = settings.pace()

    async def gen():
        try:
            async for e in _paced(engine.run_test(theme, scenario, test, body.mode, body.use_llm), pace):
                st = e.get("state") if e.get("type") == "edge" else None
                if st and st.get("source") != "live":       # no real gateway log for simulated/replayed calls
                    sim_logs.add_edges(theme, {e["edge"]: st}, _prefix())
                yield {"data": json.dumps(e)}
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.exception("test run failed")
            yield {"data": json.dumps({"type": "error", "text": f"{type(e).__name__}: {e}"})}

    return EventSourceResponse(gen())


@app.post("/api/themes/{theme_id}/probe")
async def probe(theme_id: str, body: ModeBody, request: Request) -> dict[str, Any]:
    theme = _theme(theme_id)
    if body.mode == "demo":
        edges = engine.demo.state(theme)["edges"]
        sim_logs.add_edges(theme, edges, _prefix())
        return {"edges": edges}
    try:
        await asyncio.wait_for(engine.live.probe(theme), settings.live_timeout())
        st = await engine.live.state(theme)
        return {"edges": st["edges"]}
    except Exception as e:  # noqa: BLE001
        reason = f"{type(e).__name__}: {e}" if not isinstance(e, asyncio.TimeoutError) else \
            f"probe timed out after {settings.live_timeout():.0f}s"
        if body.mode == "live_with_fallback":
            try:
                applied, ma = await engine.live.signature_now(theme)
            except Exception:  # noqa: BLE001
                applied, ma = set(), False
            edges = simulate.evaluate(theme, applied, ma)
            sim_logs.add_edges(theme, edges, _prefix())
            return {"edges": edges, "fallback": reason}
        if isinstance(e, LiveUnavailable):
            raise HTTPException(409, f"Live mode unavailable: {e}") from e
        raise HTTPException(502, f"probe failed: {reason}") from e


@app.post("/api/themes/{theme_id}/reset")
async def reset(theme_id: str, body: ModeBody, request: Request) -> dict[str, Any]:
    theme = _theme(theme_id)
    if body.mode == "demo":
        engine.demo.reset(theme.id)
        return {"policies": {p.id: engine.demo.policy_status(theme.id, p.id) for p in theme.policies}}
    _require_admin(request, body.mode)
    try:
        return await engine.live.reset(theme)
    except LiveUnavailable as e:
        raise HTTPException(409, f"Live mode unavailable: {e}") from e


def _prefix() -> str:
    cfg, _ = settings.get_config()
    return cfg.prefix if cfg else "agdemo"


@app.get("/api/themes/{theme_id}/gateway-logs")
async def gateway_logs(theme_id: str, mode: Mode = "demo", since: str | None = None,
                       denied_only: bool = False, limit: int = 50) -> dict[str, Any]:
    """Agent Gateway request log entries for the theme (CONTRACTS §9)."""
    theme = _theme(theme_id)
    limit = max(1, min(limit, 500))
    simulated = sim_logs.list(theme.id, since, denied_only, limit)
    if mode == "demo":
        return {"source": "simulated", "filter": "", "console_url": None, "entries": simulated}
    cfg, err = settings.get_config()
    if not cfg:
        raise HTTPException(409, f"Live mode unavailable: {err}")
    try:
        from agdemo_core.gcp import gateway_logs as gl
        from agdemo_core.gcp.rest import rest

        out = await asyncio.get_running_loop().run_in_executor(
            None, lambda: gl.fetch(rest(cfg.project), cfg, settings.get_state(), theme, since, denied_only, limit))
    except Exception as e:  # noqa: BLE001
        if mode == "live_with_fallback":
            return {"source": "simulated", "filter": "", "console_url": None, "entries": simulated,
                    "error": f"{type(e).__name__}: {e}"}
        raise HTTPException(502, f"Cloud Logging query failed: {type(e).__name__}: {e}") from e
    if mode == "live_with_fallback" and simulated:      # entries from runs that fell back to replay
        out["entries"] = sorted(out["entries"] + simulated, key=lambda x: x["timestamp"], reverse=True)[:limit]
    return out


@app.post("/api/themes/{theme_id}/sync")
async def sync(theme_id: str, body: ModeBody) -> dict[str, Any]:
    """Re-read policies and Model Armor from GCP, clear pending states GCP shows as done (CONTRACTS §7)."""
    theme = _theme(theme_id)
    if body.mode == "demo":
        st = engine.demo.state(theme)
        return {"policies": [{"id": p.id, "label": p.text, "applied": st["policies"][p.id]["applied"],
                              "status": st["policies"][p.id]["status"], "detail": "simulated"} for p in theme.policies],
                "model_armor": {"enabled": st["model_armor"]["enabled"], "status": st["model_armor"]["status"],
                                "detail": "simulated"}}
    try:
        return await engine.live.sync(theme)
    except LiveUnavailable as e:
        raise HTTPException(409, f"Live mode unavailable: {e}") from e


@app.post("/api/themes/{theme_id}/verify")
async def verify(theme_id: str, body: ModeBody) -> dict[str, Any]:
    """Is the theme back at step 1 (no policies, no gateways, Model Armor off, all connections direct)?"""
    theme = _theme(theme_id)
    if body.mode == "demo":
        return engine.demo.verify_start_state(theme)
    try:
        return await engine.live.verify_start_state(theme)
    except LiveUnavailable as e:
        raise HTTPException(409, f"Live mode unavailable: {e}") from e


@app.post("/api/themes/{theme_id}/tests/{test_id}/record")
async def record(theme_id: str, test_id: str, request: Request, body: RecordBody | None = None) -> dict[str, Any]:
    theme = _theme(theme_id)
    body = body or RecordBody()
    scenario, test = _test(theme, test_id, body.scenario_id)
    _require_admin(request, "live")
    try:
        engine.live.check_available(theme)
    except LiveUnavailable as e:
        raise HTTPException(409, f"Recording needs Live mode: {e}") from e
    rec = recordings.Recorder()
    try:
        async for e in engine.run_test(theme, scenario, test, "live", body.use_llm):
            e.pop("_delay", None)
            if e.get("type") == "error":
                raise HTTPException(502, f"live run failed, nothing recorded: {e.get('text')}")
            rec.add(e)
    except HTTPException:
        raise
    applied, ma = await engine.live.signature_now(theme)
    sig = simulate.test_signature(theme, scenario, test, applied, ma)
    path = recordings.save(theme.id, test.id, sig, rec.events)
    return {"saved": str(path), "signature": sig, "events": len(rec.events)}


@app.get("/api/themes/{theme_id}/policies/{pid}/explain")
async def explain(theme_id: str, pid: str) -> dict[str, Any]:
    theme = _theme(theme_id)
    policy = _policy(theme, pid)
    lines: list[str] = []
    try:
        from agdemo_core.policies import get_handler

        ctx = engine.live.ctx()
        h = get_handler(policy.type)
        lines = list(await asyncio.to_thread(h.describe, ctx, theme, policy))
    except Exception as e:  # noqa: BLE001 - no config/handlers: generic explanation
        log.info("explain fallback for %s: %s", pid, e)
    if not lines:
        lines = _generic_explain(theme, policy)
    if policy.explain and policy.explain not in lines:
        lines.insert(0, policy.explain)
    return {"lines": lines}


def _generic_explain(theme: Theme, policy) -> list[str]:
    t, p = policy.type, policy.params
    orch = theme.orchestrator.id
    if t == "gateway_attach":
        kind = "AGENT_TO_ANYWHERE (egress)" if p.get("path", "egress") == "egress" else "CLIENT_TO_AGENT (ingress)"
        return [f"PATCH reasoningEngines/<{orch}> spec.deploymentSpec.agentGatewayConfig -> {kind} gateway",
                "Default deny: only platform endpoints are allowlisted until a policy allows a destination."]
    if t == "a2a_allow":
        return [f"Grant roles/iap.egressor to the {orch} agent identity on the Agent Registry entry of {p.get('target')}"]
    if t == "mcp_server_allow":
        return [f"Grant roles/iap.egressor to the {orch} agent identity on the Agent Registry MCP server {p.get('target')}"]
    if t == "mcp_tool_allow":
        what = "read-only tools" if p.get("read_only") else ", ".join(p.get("tools") or [])
        return [f"Conditional roles/iap.egressor on {p.get('target')}: only {what}",
                "Write/destructive tools/call requests get 403 at the gateway."]
    return [policy.text]


# ------------------------------------------------------------------ static frontend (SPA)
@app.get("/{path:path}", include_in_schema=False)
def spa(path: str):
    if path.startswith("api/") or path == "api":
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    dist = settings.frontend_dist()
    index = dist / "index.html"
    if not index.exists():
        return JSONResponse({"detail": "frontend not built (ui/frontend/dist missing); API is at /api"},
                            status_code=404)
    if path:
        f = (dist / path).resolve()
        if f.is_file() and dist.resolve() in f.parents:
            return FileResponse(f)
    return FileResponse(index)
