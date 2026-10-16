"""./agdemo status / reset — live GCP state for shared resources and themes."""
from __future__ import annotations

import time

from rich.table import Table

from ..config import DemoConfig
from ..gcp import engines
from ..gcp import gateways as gw
from ..policies import Ctx, get_handler
from ..policies import model_armor as ma_policy
from ..state import load_state
from ..themes import list_themes, load_theme
from . import common as c


def run(cfg: DemoConfig, theme_id: str | None = None) -> None:
    r = c.client(cfg)
    st = load_state()
    ctx = Ctx(config=cfg, state=st)
    t = Table(title=f"Shared resources: {cfg.project} / {cfg.region}")
    t.add_column("Resource")
    t.add_column("Status", overflow="fold")
    for p in ("egress", "ingress"):
        g = gw.get_gateway(cfg, p, r)
        t.add_row(f"{p} gateway", g["name"] if g else "[red]missing[/]")
        names = ([gw.iap_policy_name(cfg, p)] if p == "egress" else []) + [gw.ma_policy_name(cfg, p)]
        for name in names:
            pol = gw.get_policy(r, gw.gateway_project(cfg, p), cfg.region, name)
            t.add_row(f"  authz policy {name}", (pol or {}).get("policyProfile", "[dim]absent[/]"))
    ma = ma_policy.status(ctx)
    t.add_row("Model Armor checkbox", f"{ma['status']}: {ma['detail']}")
    t.add_row("platform endpoints", str(len(st.get("shared", {}).get("platform_registry_ids", {}))))
    t.add_row("UI", st.get("shared", {}).get("ui_url", "[dim]not deployed[/]"))
    c.console.print(t)

    themes = [theme_id] if theme_id else [x for x in list_themes() if x in st.get("themes", {})]
    for tid in themes:
        theme = load_theme(tid)
        ts = st.get("themes", {}).get(tid, {})
        tt = Table(title=f"Theme {tid}")
        tt.add_column("Item")
        tt.add_column("State", overflow="fold")
        o = ts.get("orchestrator", {})
        eng = engines.get_engine(r, cfg, o["engine"]) if o.get("engine") else None
        tt.add_row("orchestrator", o.get("engine", "[red]not deployed[/]"))
        if eng:
            tt.add_row("  identity", f"{eng.get('spec', {}).get('identityType')} {o.get('principal', '')}")
            tt.add_row("  agentGatewayConfig", str(engines.gateway_config(eng) or "{} (wide open)"))
        for oid, x in (ts.get("orchestrators") or {}).items():      # additional orchestrators (CONTRACTS §12)
            xe = engines.get_engine(r, cfg, x["engine"]) if x.get("engine") else None
            tt.add_row(f"orchestrator {oid}", x.get("engine", "[red]not deployed[/]"))
            if xe:
                tt.add_row("  identity", f"{xe.get('spec', {}).get('identityType')} {x.get('principal', '')}")
                tt.add_row("  agentGatewayConfig", str(engines.gateway_config(xe) or "{} (wide open)"))
        for cid, v in ts.get("components", {}).items():
            tt.add_row(cid, f"{v.get('url', '')}  registry={v.get('registry', '')}")
        for p in theme.policies:
            try:
                s = get_handler(p.type).status(ctx, theme, p)
                tt.add_row(f"policy {p.id}", f"{s['status']}: {s['detail']}")
            except Exception as e:  # noqa: BLE001
                tt.add_row(f"policy {p.id}", f"[red]{e}[/]")
        c.console.print(tt)


def theme_engines(state: dict, theme_id: str) -> list[str]:
    ts = (state.get("themes") or {}).get(theme_id, {})
    return [o["engine"] for o in [ts.get("orchestrator") or {}, *((ts.get("orchestrators") or {}).values())]
            if o.get("engine")]


def reset_theme(cfg: DemoConfig, theme_id: str, ctx: Ctx | None = None, log=c.log) -> tuple[list[str], list[str]]:
    """Remove every applied (or pending / unreadable) policy of a theme. Gateway detaches go in ONE engine PATCH
    per engine (gateway_attach.remove_many, as the UI's reset does: GCP rejects a second gateway change while
    one runs). Doesn't wait. Returns (the theme's engines, errors); one failing policy doesn't stop the rest."""
    theme = load_theme(theme_id)
    ctx = ctx or Ctx(config=cfg, state=load_state())
    errors: list[str] = []
    detach = []
    for p in theme.policies:
        h = get_handler(p.type)
        s = h.status(ctx, theme, p)
        if not (s["applied"] or s["status"] in ("pending", "error")):
            continue
        if p.type == "gateway_attach":
            detach.append(p)
            continue
        log(f"  {theme_id}: removing {p.id}")
        try:
            h.remove(ctx, theme, p)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{theme_id}/{p.id}: {type(e).__name__}: {e}"[:300])
    if detach:
        log(f"  {theme_id}: detaching gateways ({', '.join(p.id for p in detach)}), one PATCH per engine")
        try:
            get_handler("gateway_attach").remove_many(ctx, theme, detach)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{theme_id}/gateway detach: {type(e).__name__}: {e}"[:300])
    return theme_engines(ctx.state, theme_id), errors


def wait_engines(cfg: DemoConfig, r, engs: list[str], timeout: float = 1800, interval: float = 20,
                 log=c.log, sleep=time.sleep, clock=time.time) -> list[str]:
    """Block until no gateway PATCH runs on these engines. Returns errors (failed PATCHes, timeout)."""
    errors: list[str] = []
    pending = list(dict.fromkeys(engs))
    start = clock()
    while pending:
        still = []
        for e in pending:
            rec = engines.inflight(r, cfg, e)
            if rec is None:
                continue
            if rec.get("done"):
                errors.append(f"{e.split('/')[-1]}: gateway PATCH failed: {rec.get('error')}")
                continue
            still.append(e)
        pending = still
        if not pending:
            break
        if clock() - start > timeout:
            errors.append(f"still updating after {int(timeout)}s: {', '.join(x.split('/')[-1] for x in pending)}")
            break
        log(f"  ... waiting for {len(pending)} engine update(s) ({int(clock() - start)}s)")
        sleep(interval)
    return errors


def reset(cfg: DemoConfig, theme_id: str, wait: bool = False) -> None:
    engs, errors = reset_theme(cfg, theme_id)
    if wait:
        errors += wait_engines(cfg, c.client(cfg), engs)
    for e in errors:
        c.log(f"  [red]{e}[/]")
    c.log(f"[green]{theme_id} reset to wide open[/]" + ("" if wait else " (gateway detach may take minutes)")
          if not errors else f"[red]{theme_id}: reset finished with {len(errors)} error(s)[/]")
