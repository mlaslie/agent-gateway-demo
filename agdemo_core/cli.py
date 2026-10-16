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
tf_app = typer.Typer(help="Terraform foundation (preview, docs/TERRAFORM_PLAN.md T1). bootstrap stays the default.",
                     no_args_is_help=True)
app.add_typer(tf_app, name="tf")


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


# ---------------------------------------------------------------------- Terraform foundation (preview, T1)
def _tf_vars(cfg, r, log=True):
    from . import tf_foundation as tfm
    from .gcp.rest import organization_id
    org = organization_id(cfg.project) or ""
    existing = tfm.discover_existing_platform(cfg, r)
    for u in existing:
        c.console.print(f"  [yellow]{u} is already registered by a service we don't own; granted, not created[/]")
    p = tfm.write_tfvars(cfg, organization_id=org, existing_platform=existing)
    if log:
        c.console.print(f"  wrote {p}")
    return org, existing


def _tf_live_guard(cfg) -> bool:
    """True when the egress gateway exists in GCP but not in this Terraform state (an environment created
    by bootstrap: it must be imported first, phase T2)."""
    from . import tf_foundation as tfm
    from .gcp import gateways as gw
    addr = 'module.base.google_network_services_agent_gateway.gw["egress"]'
    return bool(gw.get_gateway(cfg, "egress", c.client(cfg))) and addr not in tfm.state_addresses()


def _tf_err(e: Exception):
    c.console.print(f"[red]{e}[/]")
    raise typer.Exit(1)


@tf_app.command("vars")
def tf_vars():
    """Write infra/terraform/generated.auto.tfvars.json from config/demo.yaml (read-only GCP lookups)."""
    cfg = _cfg()
    _tf_vars(cfg, c.client(cfg))


@tf_app.command("init")
def tf_init(local_state: Optional[str] = typer.Option(None, "--local-state",
                                                      help="Use a local state file (scratch plans); no bucket")):
    """Write tfvars, ensure the GCS state bucket (versioning on) and run terraform init for this environment."""
    from . import tf_foundation as tfm
    cfg = _cfg()
    r = c.client(cfg)
    try:
        org, existing = _tf_vars(cfg, r, log=False)
        tfm.init(cfg, r, local_state=local_state, organization_id=org, existing_platform=existing)
    except tfm.TfError as e:
        _tf_err(e)
    c.console.print(f"[green]initialized[/] state: "
                    f"{local_state or f'gs://{tfm.state_bucket(cfg)}/{tfm.state_prefix(cfg)}'}")


@tf_app.command("plan")
def tf_plan(out: Optional[str] = typer.Option(None, "--out", help="Plan file (default infra/terraform/agdemo.tfplan)"),
            allow_replace: bool = typer.Option(False, "--allow-replace")):
    """Refresh tfvars, terraform plan, then the plan-safety check (read-only)."""
    from pathlib import Path

    from . import tf_foundation as tfm
    cfg = _cfg()
    try:
        _tf_vars(cfg, c.client(cfg))
        if _tf_live_guard(cfg):
            c.console.print("[yellow]The egress gateway already exists but isn't in this state: this environment "
                            "was created by bootstrap. Apply is refused until it's imported (phase T2).[/]")
        _, v = tfm.plan(Path(out) if out else None, allow_replace=allow_replace)
    except tfm.TfError as e:
        _tf_err(e)
    c.console.print(f"plan: {v.summary()}")
    for addr, actions in v.blockers:
        c.console.print(f"  [red]BLOCKED {'/'.join(actions)}[/]: {addr}")
    c.console.print("[green]plan-safety: OK[/]" if v.ok else "[red]plan-safety: REFUSED[/]")
    raise typer.Exit(0 if v.ok else 1)


@tf_app.command("apply")
def tf_apply(allow_replace: bool = typer.Option(False, "--allow-replace", help="Allow replaces (never deletes)"),
             state: Optional[str] = typer.Option(None, "--state", help="state.json to merge outputs into"),
             yes: bool = typer.Option(False, "--yes", "-y")):
    """Plan, check (no delete/replace), apply, then merge outputs into state.json. Creates the recordings
    bucket first (outside Terraform, so `down` keeps it)."""
    from . import tf_foundation as tfm
    cfg = _cfg()
    r = c.client(cfg)
    try:
        _tf_vars(cfg, r)
        if _tf_live_guard(cfg):
            _tf_err(tfm.TfError(f"{cfg.egress_gateway_resource} exists but isn't in this Terraform state. "
                                "Import it first (phase T2); refusing to apply."))
        planfile, v = tfm.plan(allow_replace=allow_replace)
        c.console.print(f"plan: {v.summary()}")
        if not v.ok:
            for addr, actions in v.blockers:
                c.console.print(f"  [red]BLOCKED {'/'.join(actions)}[/]: {addr}")
            _tf_err(tfm.TfError("plan-safety: REFUSED (deletes or replaces)"))
        if not yes:
            typer.confirm(f"Apply to {cfg.project}/{cfg.region} (prefix {cfg.prefix})?", abort=True)
        rb = tfm.recordings_bucket(cfg)
        if tfm.ensure_bucket(r, cfg.project, rb, cfg.region, cfg.labels, versioning=False):
            c.console.print(f"  created recordings bucket gs://{rb} (outside Terraform; survives down)")
        tfm.run_tf(["apply", "-input=false", str(planfile)])
    except tfm.TfError as e:
        _tf_err(e)
    tf_output_sync(state=state, force=False)


@tf_app.command("output-sync")
def tf_output_sync(state: Optional[str] = typer.Option(None, "--state", help="state.json path "
                                                       "(default config/state.json)"),
                   force: bool = typer.Option(False, "--force", help="Merge even if state.json describes "
                                              "another environment")):
    """Merge terraform outputs into state.json `shared` (bootstrap's keys; other keys kept)."""
    from pathlib import Path

    from . import tf_foundation as tfm
    from .state import STATE_PATH, load_state, save_state
    p = Path(state) if state else STATE_PATH
    try:
        st = tfm.merge_outputs(load_state(p), tfm.outputs(), force=force)
    except tfm.TfError as e:
        _tf_err(e)
    save_state(st, p)
    c.console.print(f"[green]merged[/] {len(st['shared'])} shared keys into {p}")


@tf_app.command("check-plan")
def tf_check_plan(plan_json: str, allow_replace: bool = typer.Option(False, "--allow-replace"),
                  allow_template_swap: bool = typer.Option(False, "--allow-template-swap")):
    """Plan-safety check on `terraform show -json` output: exit 1 on any delete or replace."""
    from . import tf_safety
    args = [plan_json] + (["--allow-replace"] if allow_replace else []) + \
        (["--allow-template-swap"] if allow_template_swap else [])
    raise typer.Exit(tf_safety.main(args))


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
