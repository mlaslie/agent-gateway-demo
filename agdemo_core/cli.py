"""./agdemo — demo CLI (see docs/SETUP.md). Every value comes from config/demo.yaml via load_config()."""
from __future__ import annotations

import sys
from typing import Optional

import typer
from rich.table import Table

from .config import load_config
from .infra import common as c

app = typer.Typer(help="Agent Gateway & Agent Registry demo CLI", no_args_is_help=True,
                  pretty_exceptions_show_locals=False)
ui_app = typer.Typer(help="Demo UI: deploy to Cloud Run behind IAP, or run locally")
app.add_typer(ui_app, name="ui")


def _cfg():
    try:
        return load_config()
    except FileNotFoundError as e:
        c.console.print(f"[red]{e}[/]")
        raise typer.Exit(2)


def _me() -> str | None:
    from .infra.preflight import active_account
    a = active_account()
    if not a:
        return None
    return f"serviceAccount:{a}" if a.endswith("gserviceaccount.com") else f"user:{a}"


@app.command()
def preflight(fix: bool = typer.Option(False, "--fix", help="Enable missing APIs")):
    """Check auth, billing, roles, APIs, region support and gateway-binding conflicts."""
    from .infra import preflight as pf
    cfg = _cfg()
    checks = pf.run(cfg, c.client(cfg), fix=fix)
    t = Table(title=f"Preflight: {cfg.project} / {cfg.region} / prefix {cfg.prefix}")
    for col in ("Check", "Result", "Detail", "Fix"):
        t.add_column(col, overflow="fold")
    bad = 0
    for ch in checks:
        res = "[green]PASS[/]" if ch.ok else ("[yellow]WARN[/]" if ch.ok is None else "[red]FAIL[/]")
        bad += ch.ok is False
        t.add_row(ch.name, res, ch.detail, "" if ch.ok else ch.fix)
    c.console.print(t)
    raise typer.Exit(1 if bad else 0)


@app.command()
def bootstrap():
    """Create shared resources (idempotent): APIs, SAs, Artifact Registry, gateways, IAP + Model Armor authz,
    platform endpoint allowlist. Writes ids to config/state.json."""
    from .infra import bootstrap as b
    shared = b.run(_cfg(), me=_me())
    c.console.print_json(data=shared)


@app.command("deploy-theme")
def deploy_theme(theme: str,
                 skip_build: bool = typer.Option(False, help="Reuse the images already in Artifact Registry"),
                 only: Optional[str] = typer.Option(None, help="images|run|registry|engine (one step)"),
                 recreate_engine: bool = typer.Option(False, help="Delete and recreate the orchestrator engines "
                                                      "(primary and additional)"),
                 update_engine: bool = typer.Option(False, help="Push new orchestrator code/spec to the existing "
                                                    "engines (keeps identity, gateway binding and IAM)")):
    """Build + deploy a theme: Cloud Run targets, Agent Registry entries, orchestrators on Agent Runtime
    (Agent Identity, no gateway): the primary and every additional_orchestrators entry."""
    from .infra import deploy_theme as d
    d.run(_cfg(), theme, skip_build=skip_build, only=only, recreate_engine=recreate_engine,
          update_engine=update_engine)


@app.command("publish-ge")
def publish_ge(theme: str,
               agent: Optional[str] = typer.Option(None, "--agent", help="Additional orchestrator id to publish "
                                                   "instead of the primary (default: the primary)")):
    """Register a theme's orchestrator in the Gemini Enterprise app from config (GE Demo mode)."""
    from .infra import publish_ge as g
    g.run(_cfg(), theme, agent)


@app.command()
def status(theme: Optional[str] = typer.Argument(None)):
    """Show shared resources, per-theme resources, gateway bindings and applied policies (live GCP state)."""
    from .infra import status as s
    s.run(_cfg(), theme)


@app.command()
def reset(theme: str, wait: bool = typer.Option(False, help="Wait for gateway detach PATCHes to finish")):
    """Return a theme to wide open: remove every policy and detach the gateways."""
    from .infra import status as s
    s.reset(_cfg(), theme, wait=wait)


@app.command("export-terraform")
def export_terraform(theme: str,
                     out: Optional[str] = typer.Option(None, "--out", help="Output directory "
                                                       "(default: config/generated/terraform/<theme>)"),
                     include_shared: bool = typer.Option(False, "--include-shared",
                                                         help="Also emit the shared resources created by bootstrap "
                                                              "and deploy-theme (gateways, extensions, registry, IAM)")):
    """Write Terraform for a theme's current Live policy state (read-only: reads GCP, changes nothing)."""
    from pathlib import Path

    from . import terraform as tf
    from .config import CONFIG_DIR
    from .policies import Ctx, get_handler, model_armor_handler
    from .themes import load_theme

    cfg = _cfg()
    t = load_theme(theme)
    ctx = Ctx.load()
    applied = set()
    for p in t.policies:
        st = get_handler(p.type).status(ctx, t, p)
        if st["status"] == "error":
            c.console.print(f"[yellow]{p.id}: could not read status ({st['detail'][:160]}); treated as not applied[/]")
        elif st["applied"] and st["status"] != "pending_removal":
            applied.add(p.id)
    ma = model_armor_handler().status(ctx)
    if ma.get("status") == "error":
        c.console.print(f"[yellow]Model Armor: could not read status ({ma.get('detail', '')[:160]}); treated as off[/]")
    files = tf.render(cfg, ctx.state, t, applied, bool(ma.get("enabled")), include_shared)
    d = Path(out) if out else CONFIG_DIR / "generated" / "terraform" / theme
    d.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (d / name).write_text(text)
    c.console.print(f"Policy state: {tf.state_line(t, applied, bool(ma.get('enabled')))}")
    c.console.print(f"[green]wrote[/] {', '.join(files)} to {d}")
    c.console.print(f"Next: cd {d} && terraform init && terraform plan  (review before applying)")


@app.command()
def teardown(theme: Optional[str] = typer.Argument(None),
             all_: bool = typer.Option(False, "--all", help="Remove every theme AND the shared resources"),
             yes: bool = typer.Option(False, "--yes", "-y")):
    """Delete a theme's resources, or everything with --all (only resources carrying the prefix)."""
    from .infra import teardown as t
    cfg = _cfg()
    if not theme and not all_:
        c.console.print("[red]give a theme or --all[/]")
        raise typer.Exit(2)
    if not yes:
        typer.confirm(f"Delete {'ALL ' + cfg.prefix + ' resources' if all_ else 'theme ' + theme} "
                      f"in {cfg.project}/{cfg.region}?", abort=True)
    t.run(cfg, theme, all_)


@ui_app.command("deploy")
def ui_deploy(skip_build: bool = typer.Option(False)):
    """Build ui/ with Cloud Build and deploy it to Cloud Run behind IAP."""
    from .infra import ui as u
    u.deploy(_cfg(), skip_build=skip_build)


@ui_app.command("local")
def ui_local(mode: Optional[str] = typer.Option(None, help="live | demo | live_with_fallback"),
             port: int = 8080, reload: bool = False):
    """Run the UI backend locally (serves ui/frontend/dist if built)."""
    from .infra import ui as u
    u.local(mode=mode, port=port, reload=reload)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
