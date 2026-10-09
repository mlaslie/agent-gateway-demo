"""Mode engine: Demo (in-memory simulation), Live (real GCP) and Live-with-fallback.

Every public coroutine here is called by the FastAPI routes in main.py. Test runs are async generators of
§7 SSE event dicts *with* a `_delay` (seconds to wait before sending), which main.py strips and sleeps.
"""
from __future__ import annotations

import asyncio
import json
import inspect
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from agdemo_core import recordings, simulate
from agdemo_core.policies.base import PENDING_SECONDS, Ctx
from agdemo_core.runtime_client import RuntimeCallError
from agdemo_core.themes import (INGRESS_EDGE, MODEL_ARMOR_TYPICAL_SECONDS, Policy, ScenarioTest, Theme, edge_ids,
                                load_theme, source_edge, split_source)

from . import settings

STATUS_TTL_S = 8.0
CONFIRM_INTERVAL_S = 30.0
MA_SETTLE_S = 15.0         # Model Armor: once both gateway policies are created/deleted, confirm after this
_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="agdemo-live")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ev(event: dict[str, Any], delay: float = 0.0) -> dict[str, Any]:
    return {**event, "_delay": delay}


class LiveUnavailable(Exception):
    pass


# =========================================================================== demo
class DemoStore:
    """Demo-mode policy state: per-theme applied set + global Model Armor flag (shared by all viewers)."""

    def __init__(self) -> None:
        self.applied: dict[str, set[str]] = {}
        self.changed: dict[tuple[str, str], str] = {}
        self.model_armor = False

    def applied_for(self, theme_id: str) -> set[str]:
        return self.applied.setdefault(theme_id, set())

    def policy_status(self, theme_id: str, pid: str) -> dict[str, Any]:
        on = pid in self.applied_for(theme_id)
        return {"applied": on, "status": "applied" if on else "removed",
                "detail": "Simulated (Demo mode)", "changed_at": self.changed.get((theme_id, pid))}

    def set_policy(self, theme_id: str, pid: str, apply: bool) -> dict[str, Any]:
        s = self.applied_for(theme_id)
        (s.add if apply else s.discard)(pid)
        self.changed[(theme_id, pid)] = now_iso()
        return self.policy_status(theme_id, pid)

    def reset(self, theme_id: str) -> None:
        self.applied[theme_id] = set()

    def verify_start_state(self, theme: Theme) -> dict[str, Any]:
        applied = self.applied_for(theme.id)
        checks = [{"id": p.id, "label": p.text, "ok": p.id not in applied,
                   "detail": "applied (simulated)" if p.id in applied else "not present"} for p in theme.policies]
        checks.append({"id": "model-armor", "label": "Model Armor is off", "ok": not self.model_armor,
                       "detail": "on" if self.model_armor else "off"})
        edges = simulate.evaluate(theme, applied, self.model_armor)
        bad = [e for e, st in edges.items() if not e.startswith("ingress:") and st["state"] != "direct"]
        checks.append({"id": "connections", "label": "Every connection goes direct (no gateway)",
                       "ok": not bad, "detail": "all direct (simulated)" if not bad else ", ".join(bad)})
        return {"ok": all(c["ok"] for c in checks), "checks": checks}

    def state(self, theme: Theme) -> dict[str, Any]:
        applied = self.applied_for(theme.id)
        edges = simulate.evaluate(theme, applied, self.model_armor)
        return {
            "policies": {p.id: self.policy_status(theme.id, p.id) for p in theme.policies},
            "gateways": {
                path: {"attached": simulate.gateway_applied(theme, applied, path),
                       "status": "applied" if simulate.gateway_applied(theme, applied, path) else "removed"}
                for path in ("egress", "ingress")},
            "model_armor": {"enabled": self.model_armor, "status": "applied" if self.model_armor else "removed",
                            "typical_seconds": MODEL_ARMOR_TYPICAL_SECONDS, "changed_at": None},
            "edges": edges,
            "expected": edges,
        }


def replay_or_simulate(theme: Theme, scenario: Any, test: ScenarioTest, applied: set[str],
                       model_armor: bool) -> list[dict[str, Any]]:
    """§8: replay a matching recording, else synthesize from simulate.evaluate."""
    rec = (recordings.load(theme.id, test.id, simulate.test_signature(theme, scenario, test, applied, model_armor))
           or recordings.load(theme.id, test.id, simulate.signature(applied, model_armor)))   # older recordings
    if rec is not None:
        return [ev(e, d) for d, e in recordings.replay(rec)]
    return [ev(e, d) for d, e in simulate.synth_events(theme, scenario, test, applied, model_armor)]


# =========================================================================== live
def policy_edges(theme: Theme, policy: Policy) -> list[str]:
    """Edges whose state a policy can change (used for pending + probe confirmation)."""
    all_e = edge_ids(theme)
    t, prm = policy.type, policy.params
    src = simulate.policy_source(policy)          # the orchestrator it applies to (CONTRACTS §12)
    if t == "gateway_attach":
        if prm.get("path", "egress") == "ingress":
            return list(simulate.INGRESS_EDGES)
        return simulate.orchestrator_egress_edges(theme, src)
    if t == "a2a_allow":
        return [source_edge(src, prm.get("target"))]
    if t in ("mcp_server_allow", "mcp_tool_allow"):
        return [e for e in all_e if e.startswith(f"{source_edge(src, prm.get('target'))}:")]
    return []


# A background policy change older than this is treated as stalled; GCP's actual state wins on sync.
STALE_OP_SECONDS = 900


def is_model_armor_block(text: str) -> bool:
    """A 403 whose body says Model Armor blocked it (ingress: "Model Armor: Prompt violates ...")."""
    low = text.lower()
    return "403" in low and ("model armor" in low or "agdemo-model-armor-block" in low)


class Op:
    def __init__(self, action: str):
        self.action = action               # apply | remove
        self.started = time.time()
        self.started_iso = now_iso()
        self.running = True
        self.done_at: float | None = None
        self.error: str | None = None
        self.confirmed = False
        self.note: str | None = None       # e.g. "not present": remove was a no-op


class LiveTheme:
    def __init__(self) -> None:
        self.status_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self.ops: dict[str, Op] = {}
        self.probe_results: dict[str, dict[str, Any]] = {}
        self.lock = asyncio.Lock()


class LiveEngine:
    def __init__(self) -> None:
        self.themes: dict[str, LiveTheme] = {}
        self.ma_cache: tuple[float, dict[str, Any]] | None = None
        self.ma_op: Op | None = None
        self.ma_target: bool | None = None
        self.tasks: set[asyncio.Task] = set()

    # ---------------------------------------------------------------- plumbing
    def lt(self, theme_id: str) -> LiveTheme:
        return self.themes.setdefault(theme_id, LiveTheme())

    def ctx(self) -> Ctx:
        cfg, err = settings.get_config()
        if cfg is None:
            raise LiveUnavailable(f"config not loaded: {err}")
        return Ctx(config=cfg, state=settings.get_state())

    def check_available(self, theme: Theme | None = None) -> None:
        ok, why = settings.live_available()
        if not ok:
            raise LiveUnavailable(why)
        if theme is not None and not settings.theme_deployed(settings.get_state(), theme.id):
            raise LiveUnavailable(f"theme {theme.id} is not deployed (run ./agdemo deploy-theme {theme.id})")

    def _spawn(self, coro) -> None:
        t = asyncio.get_running_loop().create_task(coro)
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    @staticmethod
    async def _thread(fn, *a):
        return await asyncio.get_running_loop().run_in_executor(_pool, fn, *a)

    # ---------------------------------------------------------------- status
    async def _handler_status(self, theme: Theme, policy: Policy, force: bool = False) -> dict[str, Any]:
        lt = self.lt(theme.id)
        hit = lt.status_cache.get(policy.id)
        if hit and not force and time.monotonic() - hit[0] < STATUS_TTL_S:
            return hit[1]
        try:
            from agdemo_core.policies import get_handler

            h = get_handler(policy.type)
            st = dict(await self._thread(h.status, self.ctx(), theme, policy))
        except Exception as e:
            st = {"applied": False, "status": "error", "detail": f"{type(e).__name__}: {e}", "changed_at": None}
        lt.status_cache[policy.id] = (time.monotonic(), st)
        return st

    def _effective(self, theme: Theme, policy: Policy, base: dict[str, Any]) -> dict[str, Any]:
        op = self.lt(theme.id).ops.get(policy.id)
        if op is None:
            return base
        apply = op.action == "apply"
        pend = "pending" if apply else "pending_removal"
        if op.running:
            return {"applied": apply, "status": pend, "changed_at": op.started_iso,
                    "detail": ("Applying" if apply else "Removing") + " in GCP..."}
        if op.error:
            return {"applied": base.get("applied", False), "status": "error", "changed_at": op.started_iso,
                    "detail": op.error}
        if op.note:
            return {**base, "detail": op.note, "changed_at": op.started_iso}
        age = time.time() - (op.done_at or op.started)
        if not op.confirmed and age < PENDING_SECONDS and base.get("status") in ("applied", "removed"):
            return {**base, "applied": apply, "status": pend, "changed_at": op.started_iso,
                    "detail": f"Waiting for enforcement ({int(age)}s of up to {PENDING_SECONDS}s); "
                              "confirmed by the next matching probe"}
        return {**base, "changed_at": base.get("changed_at") or op.started_iso}

    async def policy_statuses(self, theme: Theme) -> dict[str, dict[str, Any]]:
        bases = await asyncio.gather(*(self._handler_status(theme, p) for p in theme.policies))
        return {p.id: self._effective(theme, p, b) for p, b in zip(theme.policies, bases)}

    async def model_armor_status(self, force: bool = False) -> dict[str, Any]:
        if not force and self.ma_cache and time.monotonic() - self.ma_cache[0] < STATUS_TTL_S:
            base = self.ma_cache[1]
        else:
            try:
                from agdemo_core.policies import model_armor_handler

                base = dict(await self._thread(model_armor_handler().status, self.ctx()))
            except Exception as e:
                base = {"enabled": False, "status": "error", "detail": f"{type(e).__name__}: {e}"}
            self.ma_cache = (time.monotonic(), base)
        op = self.ma_op
        timing = {"typical_seconds": MODEL_ARMOR_TYPICAL_SECONDS,
                  "changed_at": op.started_iso if op else None}
        if op and op.running:
            return {"enabled": bool(self.ma_target), "status": "pending", "detail": "Updating gateways...", **timing}
        if op and op.error:
            return {**base, "status": "error", "detail": op.error}
        # Confirm as soon as GCP shows the target state on both gateways (after a short settle time).
        # Probes can't confirm Model Armor, so without this the UI would wait out PENDING_SECONDS.
        target = "applied" if self.ma_target else "removed"
        if op and op.done_at and not op.confirmed and base.get("status") == target \
                and time.time() - op.done_at >= MA_SETTLE_S:
            op.confirmed = True
        if op and op.done_at and time.time() - op.done_at < PENDING_SECONDS and base.get("status") != "error" \
                and not op.confirmed:
            return {**base, "enabled": bool(self.ma_target), "status": "pending",
                    "detail": "Model Armor change propagating", **timing}
        return {**base, **timing}

    @staticmethod
    def desired_applied(statuses: dict[str, dict[str, Any]]) -> set[str]:
        return {pid for pid, s in statuses.items() if s.get("applied") and s.get("status") != "pending_removal"}

    async def state(self, theme: Theme) -> dict[str, Any]:
        statuses, ma = await asyncio.gather(self.policy_statuses(theme), self.model_armor_status())
        applied = self.desired_applied(statuses)
        expected = simulate.evaluate(theme, applied, bool(ma.get("enabled")))
        pending_edges: set[str] = set()
        for p in theme.policies:
            if statuses[p.id]["status"] in ("pending", "pending_removal"):
                pending_edges.update(policy_edges(theme, p))
        lt = self.lt(theme.id)
        edges: dict[str, Any] = {}
        for e in edge_ids(theme):
            if e in pending_edges:
                edges[e] = {"state": "pending", "governed": expected[e]["governed"], "source": "live",
                            "detail": "Policy change propagating; expected: " + expected[e]["state"],
                            "http_status": None}
            elif e in lt.probe_results:
                edges[e] = lt.probe_results[e]
            else:
                edges[e] = {"state": "unknown", "governed": expected[e]["governed"], "source": "live",
                            "detail": "Not tested yet", "http_status": None}
        gws = {}
        for path in ("egress", "ingress"):
            gp = next((p for p in theme.policies
                       if p.type == "gateway_attach" and p.params.get("path", "egress") == path), None)
            if gp is None:
                gws[path] = {"attached": False, "status": "removed"}
            else:
                s = statuses[gp.id]
                gws[path] = {"attached": bool(s.get("applied")) and s.get("status") != "pending_removal",
                             "status": s.get("status")}
        return {"policies": statuses, "gateways": gws,
                "model_armor": {"enabled": bool(ma.get("enabled")), "status": ma.get("status", "error"),
                                **({"detail": ma["detail"]} if ma.get("detail") else {}),
                                "typical_seconds": ma.get("typical_seconds", MODEL_ARMOR_TYPICAL_SECONDS),
                                "changed_at": ma.get("changed_at")},
                "edges": edges, "expected": expected}

    # ---------------------------------------------------------------- mutations
    async def set_policy(self, theme: Theme, policy: Policy, apply: bool) -> dict[str, Any]:
        self.check_available(theme)
        lt = self.lt(theme.id)
        cur = lt.ops.get(policy.id)
        if cur and cur.running:
            return self._effective(theme, policy, {})
        op = Op("apply" if apply else "remove")
        lt.ops[policy.id] = op
        for e in policy_edges(theme, policy):
            lt.probe_results.pop(e, None)
        self._spawn(self._run_op(theme, policy, op))
        return self._effective(theme, policy, {})

    async def _run_op(self, theme: Theme, policy: Policy, op: Op) -> None:
        try:
            from agdemo_core.policies import get_handler

            h = get_handler(policy.type)
            if op.action == "remove":
                cur = dict(await self._thread(h.status, self.ctx(), theme, policy))
                if not cur.get("applied") and cur.get("status") == "removed":
                    # Nothing in GCP to remove: treat as already at the start state, not an error.
                    op.note, op.confirmed = "Policy not present: nothing to remove", True
                    return
            fn = h.apply if op.action == "apply" else h.remove
            await self._thread(fn, self.ctx(), theme, policy)
        except Exception as e:
            op.error = f"{op.action} failed: {type(e).__name__}: {e}"
        finally:
            op.running = False
            op.done_at = time.time()
            self.lt(theme.id).status_cache.pop(policy.id, None)
        if not op.error:
            await self._confirm_loop(theme, policy, op)

    async def _confirm_loop(self, theme: Theme, policy: Policy, op: Op) -> None:
        """Probe the policy's edges periodically until they match the expected state (or timeout)."""
        edges = policy_edges(theme, policy)
        while not op.confirmed and time.time() - (op.done_at or 0) < PENDING_SECONDS:
            if self.lt(theme.id).ops.get(policy.id) is not op:
                return
            await asyncio.sleep(CONFIRM_INTERVAL_S)
            try:
                await self.probe(theme, edges)
            except Exception:
                pass

    async def set_model_armor(self, enabled: bool) -> dict[str, Any]:
        self.check_available()
        if self.ma_op and self.ma_op.running:
            return {"enabled": bool(self.ma_target), "status": "pending"}
        self.ma_op, self.ma_target = Op("apply" if enabled else "remove"), enabled
        op = self.ma_op

        async def run():
            try:
                from agdemo_core.policies import model_armor_handler

                await self._thread(model_armor_handler().set, self.ctx(), enabled)
            except Exception as e:
                op.error = f"Model Armor update failed: {type(e).__name__}: {e}"
            finally:
                op.running = False
                op.done_at = time.time()
                self.ma_cache = None

        self._spawn(run())
        return {"enabled": enabled, "status": "pending"}

    async def reset(self, theme: Theme) -> dict[str, Any]:
        """Remove every policy. Gateway detaches are done in ONE engine PATCH (GCP rejects a second
        gateway change while one is running); policies that aren't present are reported as such."""
        self.check_available(theme)
        statuses = await self.policy_statuses(theme)
        out: dict[str, Any] = {}
        gw_policies: list[Policy] = []
        for p in theme.policies:
            s_ = statuses[p.id]
            if not (s_.get("applied") or s_.get("status") in ("pending", "error")):
                out[p.id] = {**s_, "detail": "Policy not present: nothing to remove"}
            elif p.type == "gateway_attach":
                gw_policies.append(p)
            else:
                out[p.id] = await self.set_policy(theme, p, False)
        if gw_policies:
            lt = self.lt(theme.id)
            ops = {p.id: Op("remove") for p in gw_policies}
            for p in gw_policies:
                lt.ops[p.id] = ops[p.id]
                for e in policy_edges(theme, p):
                    lt.probe_results.pop(e, None)
            self._spawn(self._run_gateway_reset(theme, gw_policies, ops))
            for p in gw_policies:
                out[p.id] = self._effective(theme, p, {})
        self.lt(theme.id).probe_results.clear()
        return {"policies": out}

    async def _run_gateway_reset(self, theme: Theme, policies: list[Policy], ops: dict[str, "Op"]) -> None:
        err = None
        try:
            from agdemo_core.policies import get_handler

            await self._thread(get_handler("gateway_attach").remove_many, self.ctx(), theme, policies)
        except Exception as e:
            err = f"remove failed: {type(e).__name__}: {e}"
        lt = self.lt(theme.id)
        for p in policies:
            op = ops[p.id]
            op.error, op.running, op.done_at = err, False, time.time()
            lt.status_cache.pop(p.id, None)
        if not err:
            await asyncio.gather(*(self._confirm_loop(theme, p, ops[p.id]) for p in policies))

    async def sync(self, theme: Theme) -> dict[str, Any]:
        """Re-read every policy and Model Armor straight from GCP (no caches) and clear pending states
        that GCP shows are already done. Returns what's in place."""
        self.check_available(theme)
        lt = self.lt(theme.id)
        bases = await asyncio.gather(*(self._handler_status(theme, p, force=True) for p in theme.policies))
        ma = await self.model_armor_status(force=True)
        now = time.time()
        for p, b in zip(theme.policies, bases):
            op = lt.ops.get(p.id)
            if not op or op.confirmed:
                continue
            reached = b.get("status") == ("applied" if op.action == "apply" else "removed")
            stale = op.running and now - op.started > STALE_OP_SECONDS
            if reached and (not op.running or stale):
                op.running, op.confirmed, op.done_at = False, True, op.done_at or now
        mop = self.ma_op
        if mop and not mop.confirmed and bool(ma.get("enabled")) == bool(self.ma_target):
            if not mop.running or now - mop.started > STALE_OP_SECONDS:
                mop.running, mop.confirmed, mop.done_at = False, True, mop.done_at or now
                self.ma_cache = None
                ma = await self.model_armor_status(force=True)
        statuses = await self.policy_statuses(theme)
        return {
            "policies": [{"id": p.id, "label": p.text, "applied": bool(statuses[p.id].get("applied")),
                          "status": statuses[p.id].get("status"), "detail": statuses[p.id].get("detail", "")}
                         for p in theme.policies],
            "model_armor": {"enabled": bool(ma.get("enabled")), "status": ma.get("status"),
                            "detail": ma.get("detail", "")},
        }

    async def verify_start_state(self, theme: Theme) -> dict[str, Any]:
        """Check (fresh from GCP, no caches) that the theme is back at step 1: no policies, no gateways,
        Model Armor off, and every connection reachable directly."""
        self.check_available(theme)
        lt = self.lt(theme.id)
        bases = await asyncio.gather(*(self._handler_status(theme, p, force=True) for p in theme.policies))
        ma = await self.model_armor_status(force=True)
        checks: list[dict[str, Any]] = []
        for p, b in zip(theme.policies, bases):
            op = lt.ops.get(p.id)
            busy = bool(op and op.running)
            ok = not b.get("applied") and b.get("status") == "removed" and not busy
            checks.append({"id": p.id, "label": p.text, "ok": ok,
                           "detail": "still being removed" if busy else (b.get("detail") or b.get("status", ""))})
        checks.append({"id": "model-armor", "label": "Model Armor is off", "ok": not ma.get("enabled")
                       and ma.get("status") != "pending", "detail": ma.get("detail") or ma.get("status", "")})
        policies_clear = all(c["ok"] for c in checks)
        edges_ok, bad = None, []
        if policies_clear:
            try:
                res = await self.probe(theme, [e for e in edge_ids(theme) if not e.startswith("ingress:")])
                bad = [e for e, st in res.items() if st.get("state") != "direct"]
                edges_ok = not bad
            except Exception as e:
                bad, edges_ok = [f"probe failed: {type(e).__name__}: {e}"], False
            checks.append({"id": "connections", "label": "Every connection goes direct (no gateway)",
                           "ok": edges_ok, "detail": "all direct" if edges_ok else ", ".join(bad)})
        return {"ok": policies_clear and bool(edges_ok), "checks": checks}

    # ---------------------------------------------------------------- probes / runs
    def _client(self, theme: Theme, source: str | None = None):
        """RuntimeClient for the primary orchestrator (source None) or an additional one (CONTRACTS §12)."""
        from agdemo_core.runtime_client import RuntimeClient

        ts = settings.get_state().get("themes", {}).get(theme.id, {})
        rec = ts.get("orchestrator", {}) if source is None else (ts.get("orchestrators") or {}).get(source, {})
        engine = (rec or {}).get("engine")
        if not engine:
            who = f"orchestrator {source}" if source else "orchestrator"
            raise LiveUnavailable(f"no {who} engine for theme {theme.id} in state.json "
                                  f"(run ./agdemo deploy-theme {theme.id})")
        return RuntimeClient(engine, timeout_s=settings.live_timeout())

    def undeployed_sources(self, theme: Theme) -> set[str]:
        """Additional orchestrators with no engine in state.json."""
        extra = settings.get_state().get("themes", {}).get(theme.id, {}).get("orchestrators") or {}
        return {o.id for o in theme.additional_orchestrators if not (extra.get(o.id) or {}).get("engine")}

    async def _probe_groups(self, theme: Theme, items: list[str | dict[str, Any]], malicious: bool
                            ) -> list[dict[str, Any]]:
        """Run probes (edge ids or §6 probe dicts, possibly prefixed '<orchestrator>/') on the right engines:
        grouped by orchestrator, base edge ids sent to each engine's __PROBE__ in parallel, results mapped
        back to the prefixed edge ids (CONTRACTS §12)."""
        groups: dict[str | None, list[dict[str, Any]]] = {}
        for it in items:
            d = dict(it) if isinstance(it, dict) else {"edge": it}
            src, base = split_source(d["edge"])
            groups.setdefault(src, []).append({**d, "edge": base})

        async def one(src: str | None, probes: list[dict[str, Any]]) -> list[dict[str, Any]]:
            res = await self._client(theme, src).run_probes(probes, malicious)
            return [{**r, "edge": source_edge(src, r["edge"])} if r.get("edge") else r for r in res]

        outs = await asyncio.gather(*(one(src, probes) for src, probes in groups.items()))
        return [r for o in outs for r in o]

    async def _ingress_call(self, theme: Theme, message: str) -> dict[str, Any]:
        try:
            from agdemo_core.gcp.ingress_client import invoke_via_ingress
        except Exception as e:
            raise LiveUnavailable(f"ingress client unavailable: {e}") from e
        ctx = self.ctx()
        if inspect.iscoroutinefunction(invoke_via_ingress):
            res = await invoke_via_ingress(ctx, theme, message)
        else:
            res = await self._thread(invoke_via_ingress, ctx, theme, message)
        return res if isinstance(res, dict) else {"outcome": "error", "detail": f"unexpected result {res!r}"}

    def _record_results(self, theme: Theme, results: dict[str, dict[str, Any]], expected: dict[str, Any]) -> None:
        lt = self.lt(theme.id)
        lt.probe_results.update(results)
        for pid, op in lt.ops.items():
            if op.running or op.confirmed or op.error:
                continue
            p = theme.policy(pid)
            inv = [e for e in policy_edges(theme, p) if e in results]
            if inv and all(results[e]["state"] == expected[e]["state"] for e in inv):
                op.confirmed = True
                lt.status_cache.pop(pid, None)
        if self.ma_op and not self.ma_op.running and not self.ma_op.confirmed:
            # Benign probes can't confirm Model Armor; it confirms by timeout or a malicious test.
            pass

    async def probe(self, theme: Theme, edges: list[str] | None = None, malicious: bool = False
                    ) -> dict[str, dict[str, Any]]:
        """Probe edges live (egress via probe protocol, ingress via the ingress gateway). Raises on failure."""
        self.check_available(theme)
        edges = edges or edge_ids(theme)
        statuses = await self.policy_statuses(theme)
        applied = self.desired_applied(statuses)
        ingress_on = simulate.gateway_applied(theme, applied, "ingress")
        results: dict[str, dict[str, Any]] = {}
        from agdemo_core.runtime_client import tool_result_to_edge_state, build_probe_message

        eg = [e for e in edges if not simulate.is_ingress_edge(e)]
        ing = [e for e in edges if simulate.is_ingress_edge(e)]
        # An additional orchestrator that isn't deployed yet: its edges stay untested instead of failing the probe.
        missing = self.undeployed_sources(theme)
        for e in [x for x in eg if split_source(x)[0] in missing]:
            eg.remove(e)
            results[e] = {"state": "unknown", "governed": False, "source": "live", "http_status": None,
                          "detail": f"{split_source(e)[0]} is not deployed (run ./agdemo deploy-theme {theme.id})"}

        async def do_egress():
            if not eg:
                return
            for r in await self._probe_groups(theme, eg, malicious):
                if r.get("edge"):
                    results[r["edge"]] = tool_result_to_edge_state(r, egress_attached(theme, applied, r["edge"]))

        async def do_ingress(e: str):
            try:
                r = await asyncio.wait_for(self._ingress_call(theme, build_probe_message([], malicious)),
                                           settings.live_timeout())
                results[e] = tool_result_to_edge_state(r, ingress_on)
            except LiveUnavailable as ex:   # ingress client missing: leave the edge untested
                results[e] = {"state": "unknown", "governed": ingress_on, "source": "live",
                              "detail": str(ex), "http_status": None}
            except Exception as ex:  # noqa: BLE001 - a failed ingress call shouldn't fail the whole probe
                results[e] = {"state": "error", "governed": ingress_on, "source": "live",
                              "detail": f"{type(ex).__name__}: {ex}", "http_status": None}

        await asyncio.gather(do_egress(), *(do_ingress(e) for e in ing))
        ma = await self.model_armor_status()
        self._record_results(theme, results, simulate.evaluate(theme, applied, bool(ma.get("enabled"))))
        return results

    async def involved_pending(self, theme: Theme, test_edges: list[str], malicious: bool) -> list[str]:
        statuses = await self.policy_statuses(theme)
        out = [pid for pid, s in statuses.items()
               if s["status"] in ("pending", "pending_removal")
               and set(policy_edges(theme, theme.policy(pid))) & set(test_edges)]
        if malicious and (await self.model_armor_status()).get("status") == "pending":
            out.append("model-armor")
        return out

    async def live_run(self, theme: Theme, scenario: Any, test: ScenarioTest, use_llm: bool
                       ) -> AsyncIterator[dict[str, Any]]:
        """Real test run. Yields events; raises on infrastructure failure (caller decides fallback)."""
        from agdemo_core.runtime_client import build_probe_message, tool_result_to_edge_state

        statuses = await self.policy_statuses(theme)
        applied = self.desired_applied(statuses)
        edges_wanted = simulate.test_edges(theme, scenario, test)
        results: dict[str, dict[str, Any]] = {}
        agent = theme.orchestrator.display_name

        if simulate.test_is_ingress(scenario, test):
            ingress_on = simulate.gateway_applied(theme, applied, "ingress")
            yield ev({"type": "status", "text": f"Calling {agent} through the ingress path..."})
            yield ev({"type": "message", "role": "user", "text": test.prompt})
            # Without Gemini, a probe envelope that still carries the prompt text, so Model Armor on the
            # ingress gateway screens exactly what the user typed (the agent answers without an LLM call).
            msg = test.prompt if use_llm else build_probe_message([], test.malicious, prompt=test.prompt)
            r = await self._ingress_call(theme, msg)
            e = INGRESS_EDGE
            st = tool_result_to_edge_state(r, ingress_on)
            if st["state"] == "error":
                raise RuntimeError(f"ingress call failed: {st['detail']}")
            results[e] = st
            yield ev({"type": "edge", "edge": e, "state": st})
            text = r.get("text") or (r.get("result") if isinstance(r.get("result"), str) else None)
            if not text or text.startswith("__PROBE_RESULT__") or not use_llm:
                text = simulate.synth_message(theme, scenario, test, results, samples=False)
            yield ev({"type": "message", "role": "agent", "text": text}, 0.2)
        else:
            async def egress_steps():
                # The test's agent (CONTRACTS §12: ScenarioTest.agent, default primary) runs the prompt.
                me = theme.orchestrator_for(test.agent)
                yield ev({"type": "status", "text": simulate.calling_text(theme, scenario, test)})
                yield ev({"type": "message", "role": "user", "text": test.prompt})
                if use_llm:
                    client = self._client(theme, test.agent)
                    prompt = test.prompt
                    if test.malicious and me.malicious_payload not in prompt:
                        prompt = f"{prompt}\n\n{me.malicious_payload}"
                    on = simulate.gateway_applied(theme, applied, "egress", test.agent)
                    async for e in client.chat(prompt, on):
                        if e["type"] == "edge":
                            e = {**e, "edge": source_edge(test.agent, e["edge"])}
                            results[e["edge"]] = e["state"]
                        yield ev(e)
                remaining = [e for e in edges_wanted if e not in results]
                if remaining:
                    yield ev({"type": "status", "text": f"Probing {len(remaining)} connection(s) (deterministic, no LLM)..."})
                    probe = await self._probe_groups(theme, probe_requests(test, remaining), test.malicious)
                    got = {r["edge"]: tool_result_to_edge_state(r, egress_attached(theme, applied, r["edge"]))
                           for r in probe if r.get("edge")}
                    errs = [f"{k}: {v['detail']}" for k, v in got.items() if v["state"] == "error"]
                    if errs:
                        raise RuntimeError("probe error on " + "; ".join(errs))
                    for i, e in enumerate(remaining):
                        if e in got:
                            results[e] = got[e]
                            yield ev({"type": "edge", "edge": e, "state": got[e]}, 0.0 if i == 0 else 0.25)
                    if not use_llm:
                        text = simulate.synth_message(theme, scenario, test, results, samples=False)
                        replies = reply_lines(theme, [r for r in probe if r.get("edge") in remaining])
                        yield ev({"type": "message", "role": "agent",
                                  "text": "\n\n".join([text, *replies])}, 0.2)

            try:
                async for x in egress_steps():
                    yield x
            except RuntimeCallError as ex:
                # The call into the agent itself was screened: with the ingress gateway attached and
                # Model Armor on, a malicious prompt is blocked before Helpdesk Agent ever sees it.
                if not is_model_armor_block(str(ex)):
                    raise
                detail = (f"Not attempted: Model Armor on the ingress gateway blocked the prompt before it "
                          f"reached {agent} (403)")
                yield ev({"type": "status", "text": "Blocked at the ingress gateway by Model Armor (403)"})
                for i, e in enumerate(x for x in edges_wanted if x not in results):
                    results[e] = {"state": "blocked", "governed": True, "source": "live", "http_status": 403,
                                  "detail": detail}
                    yield ev({"type": "edge", "edge": e, "state": results[e]}, 0.0 if i == 0 else 0.15)
                yield ev({"type": "message", "role": "agent",
                          "text": f"Model Armor on the ingress gateway blocked this prompt (403 \"Prompt violates "
                                  f"content security configurations\") before it reached {agent}, so no tools "
                                  "were called. To show the egress gateway's Model Armor screening tool calls, "
                                  "detach the ingress gateway (step 5) first."}, 0.2)
        ma = await self.model_armor_status()
        self._record_results(theme, results, simulate.evaluate(theme, applied, bool(ma.get("enabled")), test))
        yield ev({"type": "done", "edges": results})

    async def signature_now(self, theme: Theme) -> tuple[set[str], bool]:
        statuses, ma = await asyncio.gather(self.policy_statuses(theme), self.model_armor_status())
        return self.desired_applied(statuses), bool(ma.get("enabled"))


async def with_deadline(agen: AsyncIterator[dict[str, Any]], seconds: float) -> AsyncIterator[dict[str, Any]]:
    """Re-yield `agen` items; raise asyncio.TimeoutError if the whole run exceeds `seconds`."""
    q: asyncio.Queue = asyncio.Queue()

    async def pump():
        try:
            async for item in agen:
                await q.put(("item", item))
            await q.put(("end", None))
        except BaseException as e:  # noqa: BLE001 - forwarded to consumer
            await q.put(("exc", e))

    loop = asyncio.get_running_loop()
    task = loop.create_task(pump())
    deadline = loop.time() + seconds
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            kind, val = await asyncio.wait_for(q.get(), remaining)
            if kind == "end":
                return
            if kind == "exc":
                raise val
            yield val
    finally:
        task.cancel()


# =========================================================================== facade
class Engine:
    def __init__(self) -> None:
        self.demo = DemoStore()
        self.live = LiveEngine()

    @staticmethod
    def theme(theme_id: str) -> Theme:
        return load_theme(theme_id)

    async def run_test(self, theme: Theme, scenario: Any, test: ScenarioTest, mode: str,
                       use_llm: bool) -> AsyncIterator[dict[str, Any]]:
        if mode == "demo":
            for e in replay_or_simulate(theme, scenario, test, self.demo.applied_for(theme.id),
                                        self.demo.model_armor):
                yield e
            return

        fallback = mode == "live_with_fallback"

        async def do_fallback(reason: str):
            yield ev({"type": "fallback", "reason": reason})
            try:
                applied, ma = await self.live.signature_now(theme)
            except Exception:
                applied, ma = set(), False
            for e in replay_or_simulate(theme, scenario, test, applied, ma):
                yield e

        try:
            self.live.check_available(theme)
        except LiveUnavailable as e:
            if fallback:
                async for x in do_fallback(f"live unavailable: {e}"):
                    yield x
            else:
                yield ev({"type": "error", "text": f"Live mode unavailable: {e}"})
            return

        if fallback:
            pend = await self.live.involved_pending(theme, simulate.test_edges(theme, scenario, test), test.malicious)
            if pend:
                async for x in do_fallback("policy change pending (" + ", ".join(pend) + ")"):
                    yield x
                return
        try:
            async for x in with_deadline(self.live.live_run(theme, scenario, test, use_llm),
                                         settings.live_timeout()):
                yield x
        except asyncio.TimeoutError:
            reason = f"live call timed out after {settings.live_timeout():.0f}s"
            if fallback:
                async for x in do_fallback(reason):
                    yield x
            else:
                yield ev({"type": "error", "text": reason})
        except Exception as e:  # noqa: BLE001
            reason = f"live call failed: {type(e).__name__}: {e}"
            if fallback:
                async for x in do_fallback(reason):
                    yield x
            else:
                yield ev({"type": "error", "text": reason})

def egress_attached(theme: Theme, applied: set[str], edge: str) -> bool:
    """Is the egress gateway attached to the orchestrator that owns `edge` (prefixed or primary)?"""
    return simulate.gateway_applied(theme, applied, "egress", split_source(edge)[0])


def probe_requests(test: ScenarioTest, edges: list[str]) -> list[dict[str, Any]]:
    """Probe dicts for a test run (CONTRACTS §6). A single A2A probe sends the test's own prompt,
    so e.g. "What does jdoe earn?" really reaches HR Records Agent and its answer is shown."""
    by_edge = {p.edge: p for p in test.probes}
    out: list[dict[str, Any]] = []
    for e in edges:
        p, d = by_edge.get(e), {"edge": e}
        if p and p.message:
            d["message"] = p.message
        elif ":" not in split_source(e)[1] and len(edges) == 1:
            d["message"] = test.prompt
        if p and p.args:
            d["args"] = p.args
        out.append(d)
    return out


def reply_lines(theme: Theme, raw: list[dict[str, Any]], limit: int = 400) -> list[str]:
    """What each successful call actually returned, for the activity log."""
    lines = []
    for r in raw:
        res = r.get("result")
        if r.get("outcome") != "ok" or res in (None, "", [], {}):
            continue
        text = res if isinstance(res, str) else json.dumps(res, separators=(", ", ": "))
        text = text if len(text) <= limit else text[:limit] + "…"
        verb = "replied" if ":" not in r["edge"] else "returned"
        lines.append(f"{simulate.edge_label(theme, r['edge'])} {verb}: {text}")
    return lines
