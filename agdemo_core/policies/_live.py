"""Shared helpers for the Live policy handlers (state lookups, status records)."""
from __future__ import annotations

from typing import Any

from ..gcp.rest import Rest, rest
from ..state import load_state
from ..themes import Theme
from .base import Ctx, PolicyStatus, now_iso


class NotDeployed(RuntimeError):
    pass


def r(ctx: Ctx) -> Rest:
    return rest(ctx.config.project)


def theme_state(ctx: Ctx, theme: Theme) -> dict[str, Any]:
    st = (ctx.state or {}).get("themes", {}).get(theme.id) or load_state().get("themes", {}).get(theme.id) or {}
    return st


def engine(ctx: Ctx, theme: Theme) -> str:
    e = theme_state(ctx, theme).get("orchestrator", {}).get("engine")
    if not e:
        raise NotDeployed(f"theme {theme.id}: no orchestrator engine in state.json (run ./agdemo deploy-theme {theme.id})")
    return e


def principal(ctx: Ctx, theme: Theme) -> str:
    o = theme_state(ctx, theme).get("orchestrator", {})
    if o.get("principal"):
        return o["principal"]
    from ..gcp.engines import get_engine, principal as p
    e = engine(ctx, theme)
    return p(ctx.config, get_engine(r(ctx), ctx.config, e), e)


def component_registry(ctx: Ctx, theme: Theme, cid: str) -> str:
    rr = theme_state(ctx, theme).get("components", {}).get(cid, {}).get("registry")
    if not rr:
        raise NotDeployed(f"theme {theme.id}: component {cid} has no registry entry in state.json")
    return rr


def status(applied: bool, detail: str, st: str | None = None, changed_at: str | None = None) -> PolicyStatus:
    return {"applied": applied, "status": st or ("applied" if applied else "removed"),
            "detail": detail, "changed_at": changed_at}


def error(e: Exception) -> PolicyStatus:
    return {"applied": False, "status": "error", "detail": f"{type(e).__name__}: {e}"[:500], "changed_at": None}


__all__ = ["r", "engine", "principal", "component_registry", "status", "error", "now_iso", "NotDeployed"]
