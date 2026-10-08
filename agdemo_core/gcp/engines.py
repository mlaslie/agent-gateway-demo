"""Agent Runtime (reasoningEngines) helpers: gateway binding PATCH, identity principal, LRO tracking.

GET   https://R-aiplatform.googleapis.com/v1/<engine>
PATCH https://R-aiplatform.googleapis.com/v1/<engine>?updateMask=spec.deploymentSpec.agentGatewayConfig
      body {"spec":{"deploymentSpec":{"agentGatewayConfig":{"agentToAnywhereConfig":{"agentGateway":EG},
                                                              "clientToAgentConfig":{"agentGateway":IG}}}}}
The PATCH replaces the whole agentGatewayConfig, so each direction is preserved by re-sending the other.
A PATCH redeploys the engine container (several minutes); it is a long-running operation.
"""
from __future__ import annotations

import threading
import time
from typing import Any

from ..config import DemoConfig
from .rest import GcpError, Rest, aiplatform, organization_id, project_number

MASK = "spec.deploymentSpec.agentGatewayConfig"
KEYS = {"egress": "agentToAnywhereConfig", "ingress": "clientToAgentConfig"}

# engine -> {"op": op name, "target": {path: gateway|None}, "started": ts}
_inflight: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def engine_url(cfg: DemoConfig, engine: str) -> str:
    return f"{aiplatform(cfg.region)}/{engine}"


def get_engine(r: Rest, cfg: DemoConfig, engine: str) -> dict | None:
    return r.get(engine_url(cfg, engine), ok404=True)


def gateway_config(eng: dict | None) -> dict:
    return ((eng or {}).get("spec", {}).get("deploymentSpec", {}) or {}).get("agentGatewayConfig") or {}


def bound_gateway(eng: dict | None, path: str) -> str | None:
    return (gateway_config(eng).get(KEYS[path]) or {}).get("agentGateway") or None


def principal(cfg: DemoConfig, eng: dict | None, engine: str) -> str:
    """Agent Identity principal: principal://<effectiveIdentity> (falls back to the documented format)."""
    eff = (eng or {}).get("spec", {}).get("effectiveIdentity")
    if eff:
        return eff if eff.startswith("principal://") else f"principal://{eff}"
    org = organization_id(cfg.project)
    num = project_number(cfg.project)
    eid = engine.rstrip("/").split("/")[-1]
    return (f"principal://agents.global.org-{org}.system.id.goog/resources/aiplatform/projects/{num}"
            f"/locations/{cfg.region}/reasoningEngines/{eid}")


def project_principal_set(cfg: DemoConfig) -> str:
    """Every Agent Runtime agent (Agent Identity) in the project."""
    org = organization_id(cfg.project)
    num = project_number(cfg.project)
    return f"principalSet://agents.global.org-{org}.system.id.goog/attribute.platformContainer/aiplatform/projects/{num}"


def _op_done(r: Rest, cfg: DemoConfig, op_name: str) -> tuple[bool, str | None]:
    try:
        op = r.get(f"{aiplatform(cfg.region)}/{op_name}")
    except GcpError as e:
        return (True, None) if e.not_found else (False, None)
    if not op.get("done"):
        return False, None
    err = op.get("error")
    return True, (err.get("message") if err else None)


def _running_update_op(r: Rest, cfg: DemoConfig, engine: str) -> str | None:
    try:
        ops = r.get(f"{engine_url(cfg, engine)}/operations", params={"pageSize": 20}).get("operations", [])
    except GcpError:
        return None
    for op in ops:
        if not op.get("done") and "UpdateReasoningEngine" in op.get("metadata", {}).get("@type", ""):
            return op["name"]
    return None


def _code_update_running(engine: str) -> bool:
    """True while `deploy-theme --update-engine` is updating this engine (marker in state.json)."""
    try:
        from ..state import load_state
        started = load_state().get("code_updates", {}).get(engine)
        return bool(started) and time.time() - started < 3600
    except Exception:  # noqa: BLE001
        return False


def _persisted(engine: str) -> dict | None:
    try:
        from ..state import load_state
        return load_state().get("pending_ops", {}).get(engine)
    except Exception:  # noqa: BLE001
        return None


def _persist(engine: str, rec: dict | None) -> None:
    try:
        from ..state import load_state, save_state
        st = load_state()
        ops = st.setdefault("pending_ops", {})
        if rec is None:
            ops.pop(engine, None)
        else:
            ops[engine] = {k: rec[k] for k in ("op", "path", "target", "started")}
        save_state(st)
    except Exception:  # noqa: BLE001
        pass


def inflight(r: Rest, cfg: DemoConfig, engine: str) -> dict | None:
    """Record of a running gateway PATCH (None when finished). Records an error on failure.
    Looks at this process first, then config/state.json `pending_ops`, then the engine's operations."""
    with _lock:
        rec = _inflight.get(engine)
    if rec is None:
        p = _persisted(engine)
        running = _running_update_op(r, cfg, engine)
        if running and not p and _code_update_running(engine):
            return None    # `deploy-theme --update-engine` is pushing code, not changing gateways
        if running:
            rec = dict(p) if p and p.get("op") == running else {"op": running, "path": None, "target": None,
                                                                  "started": time.time()}
            rec["done"] = False
            with _lock:
                _inflight[engine] = rec
        elif p:
            _persist(engine, None)
            return None
        else:
            return None
    if not rec or rec.get("done"):
        return rec if rec and rec.get("error") and time.time() - rec.get("done_at", 0) < 600 else None
    done, err = _op_done(r, cfg, rec["op"])
    if done:
        with _lock:
            rec.update(done=True, done_at=time.time(), error=err)
        return rec if err else None
    return rec


def set_gateways(r: Rest, cfg: DemoConfig, engine: str, path: str, gateway: str | None,
                 wait: bool = False, timeout: float = 1800) -> dict:
    """Bind (gateway=resource) or unbind (gateway=None) one direction, preserving the other."""
    eng = get_engine(r, cfg, engine)
    if eng is None:
        raise GcpError(404, f"engine {engine} not found")
    cur = gateway_config(eng)
    new: dict[str, Any] = {}
    for p, k in KEYS.items():
        gw = gateway if p == path else (cur.get(k) or {}).get("agentGateway")
        if gw:
            new[k] = {"agentGateway": gw}
    if {k: (v or {}).get("agentGateway") for k, v in cur.items()} == {k: v["agentGateway"] for k, v in new.items()}:
        return {"changed": False}
    body = {"spec": {"deploymentSpec": {"agentGatewayConfig": new}}}
    op = r.patch(engine_url(cfg, engine), json=body, params={"updateMask": MASK})
    rec = {"op": op.get("name", ""), "path": path, "target": gateway, "started": time.time(), "done": False}
    with _lock:
        _inflight[engine] = rec
    _persist(engine, rec)
    if wait:
        r.wait(op, aiplatform(cfg.region), timeout=timeout, interval=15)
    return {"changed": True, "operation": op.get("name")}
