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
        for cid, v in ts.get("components", {}).items():
            tt.add_row(cid, f"{v.get('url', '')}  registry={v.get('registry', '')}")
        for p in theme.policies:
            try:
                s = get_handler(p.type).status(ctx, theme, p)
                tt.add_row(f"policy {p.id}", f"{s['status']}: {s['detail']}")
            except Exception as e:  # noqa: BLE001
                tt.add_row(f"policy {p.id}", f"[red]{e}[/]")
        c.console.print(tt)


def reset(cfg: DemoConfig, theme_id: str, wait: bool = False) -> None:
    theme = load_theme(theme_id)
    ctx = Ctx(config=cfg, state=load_state())
    for p in sorted(theme.policies, key=lambda p: p.type == "gateway_attach"):
        h = get_handler(p.type)
        s = h.status(ctx, theme, p)
        if s["applied"] or s["status"] in ("pending", "error"):
            c.log(f"  removing {p.id}")
            h.remove(ctx, theme, p)
    if wait:
        r = c.client(cfg)
        o = ctx.state.get("themes", {}).get(theme_id, {}).get("orchestrator", {})
        while o.get("engine") and engines.inflight(r, cfg, o["engine"]):
            c.log("  ... waiting for engine PATCH")
            time.sleep(20)
    c.log(f"[green]{theme_id} reset to wide open[/] (gateway detach may take minutes)")
