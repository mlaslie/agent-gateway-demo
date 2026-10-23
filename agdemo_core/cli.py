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
tf_app = typer.Typer(help="Terraform foundation steps (docs/TERRAFORM_PLAN.md); ./agdemo up / down run them.",
                     no_args_is_help=True)
app.add_typer(tf_app, name="tf")


def _cfg(auth_strategy: str | None = None):
    try:
        cfg = load_config()
    except FileNotFoundError as e:
        c.console.print(f"[red]{e}[/]")
        raise typer.Exit(2)
    if auth_strategy is None or auth_strategy == cfg.iam.auth_strategy:
        return cfg
    if auth_strategy not in ("standard", "minimum"):
        c.console.print("[red]--auth-strategy must be standard or minimum[/]")
        raise typer.Exit(2)
    c.console.print(f"[yellow]--auth-strategy {auth_strategy} overrides iam.auth_strategy "
                    f"({cfg.iam.auth_strategy}) for this run. Set it in config/demo.yaml so later runs keep it.[/]")
    return cfg.model_copy(update={"iam": cfg.iam.model_copy(update={"auth_strategy": auth_strategy})})


AUTH_HELP = ("Roles for the demo's service accounts: standard (Google predefined roles; the default) or minimum "
             "(least privilege). Overrides iam.auth_strategy in config/demo.yaml (docs/PERMISSIONS.md)")
ROLE_CHANGES_HELP = ("Allow removing project role grants and custom roles (needed once when switching "
                     "--auth-strategy on an existing environment); every other delete is still refused")


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


def bootstrap_refusal(state: dict) -> str | None:
    """The message bootstrap prints once `tf output-sync` has recorded that Terraform owns this environment
    (state.json shared.tf_environment); docs/TERRAFORM_PLAN.md §5.1 step 6. Offline check."""
    env = (state.get("shared") or {}).get("tf_environment")
    if env:
        return (f"Terraform manages this environment ({env.get('project_id')}/{env.get('region')}, prefix "
                f"{env.get('prefix')}): use ./agdemo up (or tf plan / tf apply). bootstrap is deprecated; "
                "./agdemo bootstrap --legacy runs the old path anyway.")
    return None


BOOTSTRAP_POINTER = ("bootstrap is replaced by ./agdemo up (Terraform foundation, then every theme and the UI; "
                     "docs/TERRAFORM_PLAN.md). An environment bootstrap created (the live demo) is adopted with "
                     "docs/TERRAFORM_MIGRATION_RUNBOOK.md. ./agdemo bootstrap --legacy runs the old path for one "
                     "more release.")


@app.command()
def bootstrap(legacy: bool = typer.Option(False, "--legacy", help="Run the deprecated bootstrap path")):
    """DEPRECATED: points at ./agdemo up. --legacy runs the old path (APIs, SAs, Artifact Registry, gateways,
    IAP + Model Armor authz, platform endpoint allowlist; writes ids to config/state.json)."""
    from .infra import bootstrap as b
    from .state import load_state
    if not legacy:
        msg = bootstrap_refusal(load_state())
        c.console.print(f"[yellow]{msg or BOOTSTRAP_POINTER}[/]")
        raise typer.Exit(2)
    c.console.print("[yellow]bootstrap --legacy: deprecated path (use ./agdemo up for new environments).[/]")
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
def reset(theme: Optional[str] = typer.Argument(None),
          all_: bool = typer.Option(False, "--all", help="Every theme, Model Armor off, and prefix-owned Policy "
                                    "Studio leftovers (grants, policies, extension copies); waits for the engines"),
          wait: bool = typer.Option(False, help="Wait for gateway detach PATCHes to finish (always on with --all)"),
          dry_run: bool = typer.Option(False, "--dry-run", help="--all: print what would change; change nothing"),
          yes: bool = typer.Option(False, "--yes", "-y", help="--all: don't ask")):
    """Return a theme to wide open (remove every policy, detach the gateways), or everything with --all
    (docs/TERRAFORM_PLAN.md §4.2). Keeps the foundation and every deployed engine and service."""
    from .infra import reset_all
    from .infra import status as s
    cfg = _cfg()
    if not all_:
        if not theme:
            c.console.print("[red]give a theme or --all[/]")
            raise typer.Exit(2)
        s.reset(cfg, theme, wait=wait)
        return
    if theme:
        c.console.print("[red]--all resets every theme; don't name one[/]")
        raise typer.Exit(2)
    if not yes and not dry_run:
        regions = ", ".join(v.region for v in cfg.region_views())
        typer.confirm(f"Reset EVERY theme in {cfg.project}/{regions} (prefix {cfg.prefix}), turn Model Armor "
                      "off and remove prefix-owned Policy Studio leftovers? Nobody should be presenting.", abort=True)
    rep = reset_all.run_regions(cfg, lambda v=None: reset_all.LiveOps(v or cfg), dry_run=dry_run)
    raise typer.Exit(0 if rep.ok else 1)


@app.command("check-clean")
def check_clean(strict: bool = typer.Option(False, "--strict", help="Also fail on warnings (UI, GE "
                                            "registrations, drift, legacy, unreadable checks): down and the "
                                            "destroy guard use it"),
                expect: Optional[str] = typer.Option(None, "--expect", help="PROJECT/REGION/PREFIX that "
                                                     "config/demo.yaml must describe (the destroy guard passes it)"),
                json_out: bool = typer.Option(False, "--json", help="Print the findings as JSON")):
    """Read-only: is anything left besides the Terraform foundation? Exit 1 with a list when something is
    (BLOCK; with --strict also WARN), 2 when config/demo.yaml isn't the expected environment."""
    import json
    from dataclasses import asdict

    from .infra import check_clean as cc
    from .state import load_state
    cfg = _cfg()
    msg = cc.expect_matches(cfg, expect)
    if msg:
        c.console.print(f"[red]check-clean: {msg}[/]")
        raise typer.Exit(2)
    _, findings = cc.run(cfg, c.client(cfg), load_state())
    bad = cc.failed(findings, strict)
    if json_out:
        print(json.dumps({"clean": not bad, "strict": strict, "findings": [asdict(x) for x in findings]}, indent=2))
        raise typer.Exit(1 if bad else 0)
    if findings:
        t = Table(title=f"check-clean: {cfg.project} / {', '.join(v.region for v in cfg.region_views())} / "
                  f"prefix {cfg.prefix}"
                  + (" (strict)" if strict else ""))
        for col in ("Level", "Category", "Resource", "Detail", "Fix"):
            t.add_column(col, overflow="fold")
        color = {cc.BLOCK: "red", cc.WARN: "yellow", cc.INFO: "dim"}
        for x in findings:
            t.add_row(f"[{color[x.level]}]{x.level}[/]", x.category, x.resource, x.detail, x.fix)
        c.console.print(t)
    n = cc.counts(findings)
    c.console.print(f"{n['BLOCK']} blocking, {n['WARN']} warnings, {n['INFO']} info: "
                    + ("[red]NOT CLEAN[/]" + (" (run ./agdemo down, or reset --all for live toggles)") if bad
                       else "[green]CLEAN[/]" + ("" if strict else " (default mode; --strict also fails on warnings)")))
    if bad and expect:
        c.console.print("[red]Destroy aborted by the teardown guard: run ./agdemo down (or reset --all) first. "
                        "Nothing was deleted.[/] Terraform has removed the root outputs from state; ./agdemo down "
                        "still works, and ./agdemo tf apply (no resource changes) restores them.")
    raise typer.Exit(1 if bad else 0)


@app.command()
def up(yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask before applying"),
       allow_replace: bool = typer.Option(False, "--allow-replace", help="Allow replaces (never deletes)"),
       allow_template_swap: bool = typer.Option(False, "--allow-template-swap",
                                                help="Allow the connectivity template swap (Private Networking)"),
       skip_build: bool = typer.Option(False, "--skip-build", help="Reuse the images in Artifact Registry"),
       skip_preflight: bool = typer.Option(False, "--skip-preflight", help="Go on when preflight fails"),
       theme: Optional[list[str]] = typer.Option(None, "--theme", help="Deploy only these themes "
                                                  "(repeatable; default themes.enabled)"),
       no_ui: bool = typer.Option(False, "--no-ui", help="Skip ui deploy"),
       ge: bool = typer.Option(False, "--ge", help="Also publish each theme's orchestrator to Gemini Enterprise"),
       local_state: Optional[str] = typer.Option(None, "--local-state", help="Local scratch Terraform state"),
       auth_strategy: Optional[str] = typer.Option(None, "--auth-strategy", help=AUTH_HELP),
       allow_role_changes: bool = typer.Option(False, "--allow-role-changes", help=ROLE_CHANGES_HELP)):
    """Build an environment: tf init, plan + plan-safety, apply, output-sync, deploy-theme for each theme,
    ui deploy, verify (docs/TERRAFORM_PLAN.md §4.1). Idempotent. Refuses the bootstrap-created live demo."""
    from . import tf_foundation as tfm
    from .infra import lifecycle as lc
    cfg = _cfg(auth_strategy)
    ops = lc.LiveUpOps(cfg, local_state=local_state, allow_replace=allow_replace, skip_build=skip_build,
                       allow_template_swap=allow_template_swap, allow_role_changes=allow_role_changes)
    try:
        rc = lc.up(cfg, ops, yes=yes, skip_preflight=skip_preflight, themes=theme or None, ui=not no_ui, ge=ge,
                   ask=lambda q: typer.confirm(q))
    except tfm.TfError as e:
        _tf_err(e)
    raise typer.Exit(rc)


@app.command()
def down(yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask (needs --prefix)"),
         prefix: Optional[str] = typer.Option(None, "--prefix",
                                              help="This environment's prefix (required with --yes)"),
         dry_run: bool = typer.Option(False, "--dry-run",
                                      help="Print the inventory and the reset plan; change nothing"),
         local_state: Optional[str] = typer.Option(None, "--local-state", help="Local scratch Terraform state")):
    """Delete an environment: reset --all, Gemini Enterprise unregister, CLI teardown (engines, Cloud Run,
    registry, UI), check-clean --strict, terraform destroy (docs/TERRAFORM_PLAN.md §4.3). Keeps APIs, the
    state and recordings buckets and the project-wide shared grants. Terraform-managed environments only."""
    from . import tf_foundation as tfm
    from .infra import lifecycle as lc
    cfg = _cfg()
    try:
        rc = lc.down(cfg, lc.LiveDownOps(cfg, local_state=local_state), yes=yes, prefix_arg=prefix,
                     dry_run=dry_run, ask=lambda q: typer.prompt(q, default="", show_default=False))
    except tfm.TfError as e:
        _tf_err(e)
    raise typer.Exit(rc)


@app.command("deployer-roles")
def deployer_roles(member: str = typer.Option(..., "--member",
                                              help="Who deploys: user:you@example.com, group:... or serviceAccount:..."),
                   auth_strategy: Optional[str] = typer.Option(None, "--auth-strategy", help=AUTH_HELP),
                   private_networking: Optional[bool] = typer.Option(None, "--private-networking/--no-private-networking",
                                                                     help="Add the 2 Private Networking roles "
                                                                          "(default: private_networking.enabled)"),
                   gemini_enterprise: Optional[bool] = typer.Option(None, "--gemini-enterprise/--no-gemini-enterprise",
                                                                    help="Add the Gemini Enterprise role "
                                                                         "(default: gemini_enterprise.app_id set)"),
                   apply: bool = typer.Option(False, "--apply", help="Run the commands (as a project owner) "
                                                                    "instead of printing them")):
    """The deployer's project roles for the auth strategy (docs/PERMISSIONS.md). Prints the gcloud commands a
    project owner runs once; --apply runs them. minimum also creates a custom IAP role and limits Project IAM
    Admin with two conditions (written to config/generated/)."""
    import shlex

    from .gcp import gcloud
    from .infra import deployer_roles as dr
    cfg = _cfg(auth_strategy)
    strategy = cfg.iam.auth_strategy
    pn = cfg.private_networking.enabled if private_networking is None else private_networking
    ge = bool(cfg.gemini_enterprise.app_id) if gemini_enterprise is None else gemini_enterprise
    cmds = dr.commands(cfg, member, strategy, pn, ge)
    if strategy == "minimum" and load_config().iam.auth_strategy != "minimum":
        c.console.print("[yellow]minimum deployer roles can only grant the minimum service-account roles: also set "
                        "iam.auth_strategy: minimum in config/demo.yaml (or pass --auth-strategy minimum to up).[/]")
    if not apply:
        c.console.print(f"# Deployer roles ({strategy}{', Private Networking' if pn else ''}"
                        f"{', Gemini Enterprise' if ge else ''}) for {member} on {cfg.project}. Run as a project owner:")
        for cmd in cmds:
            print(shlex.join(cmd))
            if cmd[1:4] == ["iam", "roles", "create"]:
                print(f"# If that role was deleted in the last ~37 days: gcloud iam roles undelete {cmd[4]} "
                      f"--project={cfg.project}  (--apply does this for you)")
        return
    if strategy == "minimum":
        c.console.print(f"  custom role {dr.deployer_iap_role(cfg)}: {dr.ensure_deployer_iap_role(cfg)}")
    for cmd in cmds:
        if cmd[1:4] == ["iam", "roles", "create"]:
            continue
        c.console.print(f"  {cmd[5].split('=', 1)[1]}{' (conditional)' if 'condition-from-file' in cmd[-2] else ''}")
        gcloud.run(cmd[1:])
    c.console.print(f"[green]granted {len(cmds) - (1 if strategy == 'minimum' else 0)} bindings to {member}[/]")


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
    from .themes import ThemeRegionError, load_theme, theme_config

    cfg = _cfg()
    t = load_theme(theme)
    try:
        view = theme_config(cfg, t)        # the theme's region, its gateway and registry (as the UI's export)
    except ThemeRegionError as e:
        c.console.print(f"[red]{e}[/]")
        raise typer.Exit(1)
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
    files = tf.render(view, ctx.state, t, applied, bool(ma.get("enabled")), include_shared)
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
             yes: bool = typer.Option(False, "--yes", "-y"),
             legacy: bool = typer.Option(False, "--legacy", help="--all on a Terraform-managed environment "
                                         "(deletes the foundation behind Terraform's back; use down instead)")):
    """Delete a theme's resources, or everything with --all (only resources carrying the prefix). For a
    Terraform-managed environment use ./agdemo down."""
    from .infra import teardown as t
    from .state import load_state
    cfg = _cfg()
    if not theme and not all_:
        c.console.print("[red]give a theme or --all[/]")
        raise typer.Exit(2)
    if all_ and not legacy and (load_state().get("shared") or {}).get("tf_environment"):
        c.console.print("[red]Terraform manages this environment's foundation: use ./agdemo down "
                        "(teardown --all --legacy deletes it outside Terraform).[/]")
        raise typer.Exit(2)
    if not yes:
        typer.confirm(f"Delete {'ALL ' + cfg.prefix + ' resources' if all_ else 'theme ' + theme} "
                      f"in {cfg.project}/{cfg.region}?", abort=True)
    t.run(cfg, theme, all_)


@ui_app.command("deploy")
def ui_deploy(skip_build: bool = typer.Option(False),
              auth_strategy: Optional[str] = typer.Option(None, "--auth-strategy", help=AUTH_HELP)):
    """Build ui/ with Cloud Build and deploy it to Cloud Run behind IAP."""
    from .infra import ui as u
    u.deploy(_cfg(auth_strategy), skip_build=skip_build)


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
    existing_pn = tfm.discover_existing_platform_pn(cfg, r)
    for u in [*existing, *existing_pn]:
        c.console.print(f"  [yellow]{u} is already registered by a service we don't own; granted, not created[/]")
    p = tfm.write_tfvars(cfg, organization_id=org, existing_platform=existing, existing_platform_pn=existing_pn)
    if log:
        c.console.print(f"  wrote {p}")
    return org, existing


def _tf_live_guard(cfg) -> bool:
    """True when the egress gateway exists in GCP but is neither in this Terraform state nor imported by
    imports.tf (an environment created by bootstrap: run `./agdemo tf import` first, phase T2)."""
    from . import tf_foundation as tfm
    from . import tf_import as tfi
    from .gcp import gateways as gw
    if tfi.imports_address(tfm.tf_dir(), tfi.EGRESS_GW_ADDR):
        return False
    return bool(gw.get_gateway(cfg, "egress", c.client(cfg))) and tfi.EGRESS_GW_ADDR not in tfm.state_addresses()


def _tf_err(e: Exception):
    c.console.print(f"[red]{e}[/]")
    raise typer.Exit(1)


@tf_app.command("vars")
def tf_vars(auth_strategy: Optional[str] = typer.Option(None, "--auth-strategy", help=AUTH_HELP)):
    """Write infra/terraform/generated.auto.tfvars.json from config/demo.yaml (read-only GCP lookups)."""
    cfg = _cfg(auth_strategy)
    _tf_vars(cfg, c.client(cfg))


@tf_app.command("init")
def tf_init(local_state: Optional[str] = typer.Option(None, "--local-state",
                                                      help="Use a local state file (scratch plans); no bucket"),
            auth_strategy: Optional[str] = typer.Option(None, "--auth-strategy", help=AUTH_HELP)):
    """Write tfvars, ensure the GCS state bucket (versioning on) and run terraform init for this environment."""
    from . import tf_foundation as tfm
    cfg = _cfg(auth_strategy)
    r = c.client(cfg)
    try:
        org, existing = _tf_vars(cfg, r, log=False)
        tfm.init(cfg, r, local_state=local_state, organization_id=org, existing_platform=existing,
                 existing_platform_pn=tfm.discover_existing_platform_pn(cfg, r))
    except tfm.TfError as e:
        _tf_err(e)
    c.console.print(f"[green]initialized[/] state: "
                    f"{local_state or f'gs://{tfm.state_bucket(cfg)}/{tfm.state_prefix(cfg)}'}")


@tf_app.command("plan")
def tf_plan(out: Optional[str] = typer.Option(None, "--out", help="Plan file (default infra/terraform/agdemo.tfplan)"),
            json_out: Optional[str] = typer.Option(None, "--json", help="Also save `terraform show -json` here"),
            allow_replace: bool = typer.Option(False, "--allow-replace"),
            allow_template_swap: bool = typer.Option(False, "--allow-template-swap",
                                                     help="Allow the connectivity template's create-before-destroy "
                                                          "swap (a Private Networking content change)"),
            auth_strategy: Optional[str] = typer.Option(None, "--auth-strategy", help=AUTH_HELP),
            allow_role_changes: bool = typer.Option(False, "--allow-role-changes", help=ROLE_CHANGES_HELP)):
    """Refresh tfvars, terraform plan, then the plan-safety check (read-only). While imports.tf exists the
    check runs in migration mode (only additive IAM/API creates allowed)."""
    from pathlib import Path

    from . import tf_foundation as tfm
    cfg = _cfg(auth_strategy)
    try:
        _tf_vars(cfg, c.client(cfg))
        if _tf_live_guard(cfg):
            c.console.print("[yellow]The egress gateway already exists but isn't in this state: this environment "
                            "was created by bootstrap. Run ./agdemo tf import first; apply is refused until then.[/]")
        _, v = tfm.plan(Path(out) if out else None, allow_replace=allow_replace,
                        json_out=Path(json_out) if json_out else None, allow_template_swap=allow_template_swap,
                        allow_role_changes=allow_role_changes)
    except tfm.TfError as e:
        _tf_err(e)
    c.console.print(f"plan: {v.summary()}" + (" (migration mode: imports.tf present)" if tfm.migrating() else ""))
    for addr, actions in v.blockers:
        c.console.print(f"  [red]BLOCKED {'/'.join(actions)}[/]: {addr}")
    c.console.print("[green]plan-safety: OK[/]" if v.ok else "[red]plan-safety: REFUSED[/]")
    raise typer.Exit(0 if v.ok else 1)


@tf_app.command("apply")
def tf_apply(allow_replace: bool = typer.Option(False, "--allow-replace", help="Allow replaces (never deletes)"),
             allow_template_swap: bool = typer.Option(False, "--allow-template-swap",
                                                      help="Allow the connectivity template swap"),
             state: Optional[str] = typer.Option(None, "--state", help="state.json to merge outputs into"),
             yes: bool = typer.Option(False, "--yes", "-y"),
             auth_strategy: Optional[str] = typer.Option(None, "--auth-strategy", help=AUTH_HELP),
             allow_role_changes: bool = typer.Option(False, "--allow-role-changes", help=ROLE_CHANGES_HELP)):
    """Plan, check (no delete/replace), apply, then merge outputs into state.json. Creates the recordings
    bucket first (outside Terraform, so `down` keeps it)."""
    from . import tf_foundation as tfm
    cfg = _cfg(auth_strategy)
    r = c.client(cfg)
    try:
        _tf_vars(cfg, r)
        if _tf_live_guard(cfg):
            _tf_err(tfm.TfError(f"{cfg.egress_gateway_resource} exists but isn't in this Terraform state. "
                                "Run ./agdemo tf import first (phase T2); refusing to apply."))
        planfile, v = tfm.plan(allow_replace=allow_replace, allow_template_swap=allow_template_swap,
                               allow_role_changes=allow_role_changes)
        c.console.print(f"plan: {v.summary()}")
        if not v.ok:
            for addr, actions in v.blockers:
                c.console.print(f"  [red]BLOCKED {'/'.join(actions)}[/]: {addr}")
            _tf_err(tfm.TfError("plan-safety: REFUSED (deletes or replaces"
                                + (", or creates that should be imports)" if tfm.migrating() else ")")))
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


@tf_app.command("recordings-bucket")
def tf_recordings_bucket():
    """Create the recordings bucket if missing (the same call `tf apply` makes first; outside Terraform, so
    `down` keeps it). Prints its gs:// URL. Used by the T2 runbook to copy recordings before the apply."""
    from . import tf_foundation as tfm
    cfg = _cfg()
    rb = tfm.recordings_bucket(cfg)
    created = tfm.ensure_bucket(c.client(cfg), cfg.project, rb, cfg.region, cfg.labels, versioning=False)
    c.console.print(f"gs://{rb} ({'created' if created else 'already exists'})")


@tf_app.command("import")
def tf_import(dry_run: bool = typer.Option(False, "--dry-run", help="Print the import blocks; write nothing")):
    """Write infra/terraform/imports.tf (generated, git-ignored) that adopts the bootstrap-created environment:
    state.json + read-only live lists (plan §5.1). Delete the file after the first successful apply."""
    from . import tf_foundation as tfm
    from . import tf_import as tfi
    from .state import load_state
    cfg = _cfg()
    r = c.client(cfg)
    existing = tfm.discover_existing_platform(cfg, r)
    imp = tfi.build_imports(cfg, load_state().get("shared", {}), tfi.discover_live(cfg, r), existing)
    for w in imp.warnings:
        c.console.print(f"  [yellow]{w}[/]")
    for m in imp.missing:
        c.console.print(f"  [yellow]not live, Terraform will create it[/]: {m}")
    for lg in imp.legacy_present:
        c.console.print(f"  [red]legacy resource still present (delete it first, runbook step a)[/]: {lg}")
    if dry_run:
        c.console.print(tfi.render(imp))
    else:
        p = tfi.write(imp, tfm.tf_dir())
        c.console.print(f"  wrote {p}")
    c.console.print(f"[green]{imp.count} imports[/] ({len(imp.blocks)} blocks + for_each over "
                    f"{len(imp.platform)} platform entries)")


@tf_app.command("check-plan")
def tf_check_plan(plan_json: str, allow_replace: bool = typer.Option(False, "--allow-replace"),
                  allow_template_swap: bool = typer.Option(False, "--allow-template-swap"),
                  migration: bool = typer.Option(False, "--migration", help="T2: refuse non-additive creates")):
    """Plan-safety check on `terraform show -json` output: exit 1 on any delete or replace (and, with
    --migration, on any create other than additive IAM/API)."""
    from . import tf_safety
    args = [plan_json] + (["--allow-replace"] if allow_replace else []) + \
        (["--allow-template-swap"] if allow_template_swap else []) + (["--migration"] if migration else [])
    raise typer.Exit(tf_safety.main(args))


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
