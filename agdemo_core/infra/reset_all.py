"""`./agdemo reset --all`: every theme back to the start state, Model Armor off, and the prefix-owned leftovers
Policy Studio (or a person) may have made removed (docs/TERRAFORM_PLAN.md §4.2, phase T3). Keeps all
foundation resources and all code-deployed resources (engines, Cloud Run, registry entries, UI).

Order:
  1. each theme in state.json: the theme reset (status.reset_theme; gateway detaches one PATCH per engine)
  2. Model Armor off (the checkbox policies <prefix>-{egress,ingress}-ma-policy)
  3. leftovers, from a fresh read-only snapshot (plan_leftovers, pure):
     a. other authz policies on our gateways: deleted when named <prefix>-* AND labelled demo-prefix=<prefix>
     b. the foundation IAP policy pointed back at the foundation IAP extension (Policy Studio's own-extension
        copy), labels re-sent (a customProvider PATCH without labels in the mask clears them)
     c. the foundation IAP extension's enforcement back to demo.yaml (Policy Studio's Enforce/Dry run toggle)
     d. non-foundation authz extensions named <prefix>-* AND labelled, once no remaining policy uses them
     e. IAP grants on prefix registry entries: theme / other prefix entries lose every binding; platform
        entries keep only the foundation egressor (the project's Agent Identity principalSet); the region-wide
        registry policy loses only our engines' principals
     f. our engines still bound to our gateways but not covered by a theme reset: unbound
  4. wait for every engine update (gateway PATCH) to finish
Legacy resources (plan §2.3) are never touched here (down deletes them); anything not ours is reported only.
Idempotent: a second run plans nothing. dry_run=True prints the plan and writes nothing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import DemoConfig
from ..gcp import gateways as gw
from ..gcp import iap
from . import check_clean as cc
from . import common as c


@dataclass
class Action:
    kind: str            # delete_policy | repoint_iap | restore_enforcement | delete_extension | prune_iam | unbind
    target: str          # resource name (for output)
    detail: str = ""
    args: dict[str, Any] = field(default_factory=dict)

    def line(self) -> str:
        return f"{self.kind.replace('_', ' ')}: {self.target}" + (f" ({self.detail})" if self.detail else "")


ORDER = ("delete_policy", "repoint_iap", "restore_enforcement", "delete_extension", "prune_iam", "unbind")


@dataclass
class Report:
    done: list[str] = field(default_factory=list)
    reported: list[str] = field(default_factory=list)       # left alone (not ours / legacy / in use)
    errors: list[str] = field(default_factory=list)
    engines: list[str] = field(default_factory=list)        # engines whose updates were waited for

    @property
    def ok(self) -> bool:
        return not self.errors


# ------------------------------------------------------------------ IAM pruning (pure)
def prune(policy: dict, keep: Callable[[dict, str], bool]) -> tuple[list[dict], list[str]]:
    """Bindings with only the members `keep(binding, member)` accepts (empty bindings dropped), plus what went."""
    out, gone = [], []
    for b in (policy or {}).get("bindings", []) or []:
        members = [m for m in b.get("members", []) or [] if keep(b, m)]
        gone += [f"{b.get('role')} {m}" for m in b.get("members", []) or [] if not keep(b, m)]
        if members:
            out.append({**b, "members": members})
    return out, gone


def keep_rule(kind: str, principal_set: str = "", ours: frozenset[str] = frozenset()) -> Callable[[dict, str], bool]:
    if kind == "platform":
        return lambda b, m: cc.is_platform_egressor(b, m, principal_set)
    if kind == "root":
        return lambda b, m: m not in ours
    return lambda b, m: False          # theme / other prefix entries: no foundation grant at all


# ------------------------------------------------------------------ planning (pure)
def plan_leftovers(cfg: DemoConfig, state: dict, snap: cc.Snapshot, derived: cc.Derived,
                   touched: set[str] = frozenset()) -> tuple[list[Action], list[str]]:
    """Actions for step 3 and the things only reported. touched: engine ids the theme resets already PATCH."""
    acts: list[Action] = []
    rep: list[str] = []
    leg = cc.legacy(cfg)
    iap_pol, iap_ext = gw.iap_policy_name(cfg, "egress"), gw.iap_extension_name(cfg, "egress")
    iap_ext_res = f"projects/{cfg.gateway_project}/locations/{cfg.region}/authzExtensions/{iap_ext}"
    ma_pols = {gw.ma_policy_name(cfg, p) for p in ("egress", "ingress")}
    ours = lambda o: cc.prefix_named(cfg, cc.leaf(o.get("name", ""))) and cc.labelled(cfg, o)   # noqa: E731

    # a/b. authz policies
    deleted: set[str] = set()
    remaining_refs: set[str] = set()      # extension tails still referenced after the actions
    for proj, pols in snap.authz_policies.items():
        for p in pols:
            name = cc.leaf(p.get("name", ""))
            provider = ((p.get("customProvider") or {}).get("authzExtension") or {}).get("resources") or []
            paths = cc.gateway_paths(cfg, (p.get("target") or {}).get("resources") or [])
            if name == iap_pol and proj == cfg.gateway_project:
                if provider and not any(cc.tail(x) == cc.tail(iap_ext_res) for x in provider):
                    acts.append(Action("repoint_iap", name, f"from {', '.join(map(cc.leaf, provider))} back to "
                                       f"{iap_ext}", {"project": proj, "name": name, "extension": iap_ext_res,
                                                       "labels": p.get("labels") or {}}))
                remaining_refs.add(cc.tail(iap_ext_res))
                continue
            if name in leg["authz_policies"]:
                rep.append(f"legacy authz policy {name}: left alone (./agdemo down deletes it)")
                remaining_refs |= {cc.tail(x) for x in provider}
                continue
            if name in ma_pols and paths:
                continue                                    # step 2 (Model Armor off) owns these
            if not paths:
                remaining_refs |= {cc.tail(x) for x in provider}
                continue
            if ours(p):
                acts.append(Action("delete_policy", name, f"{p.get('policyProfile', '?')} on {'/'.join(paths)}",
                                   {"project": proj, "name": name}))
                deleted.add(name)
            else:
                rep.append(f"authz policy {name} ({proj}) targets our {'/'.join(paths)} gateway but isn't "
                           f"ours (not named {cfg.prefix}-* with label demo-prefix={cfg.prefix}): delete it by hand")
                remaining_refs |= {cc.tail(x) for x in provider}

    # c/d. extensions
    found = cc.foundation_extensions(cfg)
    for proj, exts in snap.authz_extensions.items():
        for x in exts:
            name = cc.leaf(x.get("name", ""))
            if (proj, name) in found:
                if name == iap_ext and proj == cfg.gateway_project:
                    drift = cc.iap_drift(cfg, x)
                    if drift:
                        acts.append(Action("restore_enforcement", name, "; ".join(drift),
                                           {"project": proj, "name": name}))
                continue
            if name in leg["authz_extensions"]:
                rep.append(f"legacy authz extension {name}: left alone (./agdemo down deletes it)")
                continue
            if not (cc.prefix_named(cfg, name) or cc.labelled(cfg, x)):
                continue
            if not ours(x):
                rep.append(f"authz extension {name} ({proj}) is only half ours (name or label): delete it by hand")
            elif cc.tail(x.get("name", "")) in remaining_refs:
                rep.append(f"authz extension {name} ({proj}) is still used by a policy we don't delete: left alone")
            else:
                acts.append(Action("delete_extension", name, "not part of the foundation",
                                   {"project": proj, "name": name}))

    # e. IAP grants
    plat = cc.platform_ids(cfg)
    mine = frozenset(cc.our_principals(cfg, state, snap))
    for svc in snap.registry_services:
        sid, rr = cc.leaf(svc.get("name", "")), svc.get("registryResource") or ""
        if not cc.prefix_named(cfg, sid) or rr not in snap.iap_policies:
            continue
        kind = "platform" if sid in plat else "entry"
        if kind == "platform" and not derived.principal_set:
            rep.append(f"registry {sid}: platform grants not checked (the project principalSet is unknown)")
            continue
        _, gone = prune(snap.iap_policies[rr], keep_rule(kind, derived.principal_set))
        if gone:
            acts.append(Action("prune_iam", f"registry {sid}", ", ".join(gone),
                               {"url": iap.from_registry_resource(rr), "kind": kind}))
    if snap.registry_root_policy:
        _, gone = prune(snap.registry_root_policy, keep_rule("root", ours=mine))
        if gone:
            acts.append(Action("prune_iam", f"registry-wide ({cfg.region})", ", ".join(gone),
                               {"url": "root", "kind": "root", "ours": sorted(mine)}))
        others = [f"{b.get('role')} {m}" for b in snap.registry_root_policy.get("bindings", []) or []
                  for m in b.get("members", []) or [] if m not in mine]
        if others:
            rep.append(f"registry-wide grants that aren't ours, left alone: {', '.join(others)}")

    # f. engines still bound
    for e in snap.engines:
        paths = cc.bound_paths(cfg, e)
        if not paths:
            continue
        eid = cc.engine_id(e.get("name", ""))
        if eid in touched:
            continue
        if cc.engine_is_ours(cfg, state, e):
            acts.append(Action("unbind", f"engine {e.get('displayName', eid)}", "/".join(paths),
                               {"engine": e["name"], "paths": paths}))
        else:
            rep.append(f"engine {e.get('displayName', eid)} ({eid}) isn't ours but is bound to our "
                       f"{'/'.join(paths)} gateway: unbind it by hand")
    acts.sort(key=lambda a: ORDER.index(a.kind))
    return acts, rep


# ------------------------------------------------------------------ run (sequencing)
def run(cfg: DemoConfig, ops, dry_run: bool = False, log=c.log) -> Report:
    """ops: LiveOps (below) or a test fake with the same methods."""
    rep = Report()
    touched: list[str] = []
    themes = ops.themes()
    log(f"[bold]1/4 Theme resets[/] ({', '.join(themes) or 'no deployed themes'})")
    for tid in themes:
        if dry_run:
            from .status import theme_engines
            for line in ops.plan_theme(tid):
                log(f"  would remove {tid}/{line}")
            touched += theme_engines(ops.state(), tid)
            continue
        engs, errs = ops.reset_theme(tid)
        touched += engs
        rep.errors += errs
        rep.done.append(f"theme {tid} reset")
    log("[bold]2/4 Model Armor off[/]")
    if ops.model_armor_on():
        if dry_run:
            log("  would delete the Model Armor policies")
        else:
            try:
                ops.model_armor_off()
                rep.done.append("Model Armor off")
                log("  Model Armor policies deleted")
            except Exception as e:  # noqa: BLE001
                rep.errors.append(f"Model Armor off: {type(e).__name__}: {e}"[:300])
    else:
        log("  already off")
    log("[bold]3/4 Leftovers on prefix resources[/]")
    if dry_run:
        log("  (read now, before the theme resets: grants those resets remove are listed here too)")
    snap = ops.snapshot()
    for k, msg in snap.errors.items():
        rep.errors.append(f"could not read {k}: {msg}"[:300])
    actions, reported = plan_leftovers(cfg, ops.state(), snap, ops.derived(),
                                       {cc.engine_id(e) for e in touched})
    if not actions:
        log("  nothing to remove")
    for a in actions:
        if dry_run:
            log(f"  would {a.line()}")
            continue
        try:
            ops.execute(a)
            rep.done.append(a.line())
            log(f"  {a.line()}")
            if a.kind == "unbind":
                touched.append(a.args["engine"])
        except Exception as e:  # noqa: BLE001
            rep.errors.append(f"{a.line()}: {type(e).__name__}: {e}"[:300])
    for line in reported:
        log(f"  [yellow]reported: {line}[/]")
    rep.reported = reported
    log("[bold]4/4 Engine updates[/]")
    rep.engines = list(dict.fromkeys(touched))
    if dry_run:
        log("  (dry run: nothing changed)")
    elif rep.engines:
        rep.errors += ops.wait(rep.engines)
        log(f"  {len(rep.engines)} engine(s) idle")
    else:
        log("  none")
    for e in rep.errors:
        log(f"  [red]{e}[/]")
    if not dry_run:
        log("[green]reset --all complete[/]" if rep.ok else f"[red]reset --all: {len(rep.errors)} error(s)[/]")
    return rep


def run_regions(cfg: DemoConfig, make_ops, dry_run: bool = False, log=c.log) -> Report:
    """`reset --all` over every region of the environment (T4): the base, then the Private Networking region.
    make_ops() -> the base region's ops; make_ops(view) -> another region's. Reports are merged."""
    total = Report()
    for v in cfg.region_views():
        if not v.is_home:
            log(f"[bold]Region {v.region}[/] (Private Networking)")
        rep = run(v, make_ops() if v.is_home else make_ops(v), dry_run=dry_run, log=log)
        total.done += rep.done
        total.reported += rep.reported
        total.errors += rep.errors
        total.engines += rep.engines
    return total


# ------------------------------------------------------------------ live operations
class LiveOps:
    def __init__(self, cfg: DemoConfig, r=None, log=c.log):
        self.cfg, self.r, self.log = cfg, r or c.client(cfg), log
        self._derived: cc.Derived | None = None

    def state(self) -> dict:
        from ..state import load_state
        return load_state()

    def themes(self) -> list[str]:
        """The deployed themes that run in this ops' region (T4: a Private Networking theme in its own region)."""
        from ..themes import list_themes, themes_in_region
        have = set(list_themes())
        return themes_in_region(self.cfg, [t for t in sorted(self.state().get("themes") or {}) if t in have])

    def _ctx(self):
        from ..policies import Ctx
        return Ctx(config=self.cfg, state=self.state())

    def plan_theme(self, tid: str) -> list[str]:
        from ..policies import get_handler
        from ..themes import load_theme
        theme, ctx = load_theme(tid), self._ctx()
        out = []
        for p in theme.policies:
            s = get_handler(p.type).status(ctx, theme, p)
            if s["applied"] or s["status"] in ("pending", "error"):
                out.append(f"{p.id} ({s['status']})")
        return out

    def reset_theme(self, tid: str):
        from .status import reset_theme
        return reset_theme(self.cfg, tid, self._ctx(), log=self.log)

    def model_armor_on(self) -> bool:
        if not self.cfg.has_model_armor:          # Model Armor exists only in the base region
            return False
        from ..policies import model_armor_handler
        st = model_armor_handler().status(self._ctx())
        return bool(st.get("enabled")) or st.get("status") in ("pending", "error")

    def model_armor_off(self) -> None:
        from ..policies import model_armor_handler
        model_armor_handler().set(self._ctx(), False)

    def snapshot(self) -> cc.Snapshot:
        return cc.collect(self.cfg, self.r, self.state())

    def derived(self) -> cc.Derived:
        if self._derived is None:
            self._derived = cc.derived(self.cfg)
        return self._derived

    def wait(self, engs: list[str]) -> list[str]:
        from .status import wait_engines
        return wait_engines(self.cfg, self.r, engs, log=self.log)

    def execute(self, a: Action) -> None:
        cfg, r, g = self.cfg, self.r, a.args
        if a.kind == "delete_policy":
            gw.delete_policy(r, g["project"], cfg.region, g["name"])
        elif a.kind == "repoint_iap":
            from ..gcp.rest import NETWORKSECURITY
            body = {"customProvider": {"authzExtension": {"resources": [g["extension"]]}}, "labels": g["labels"]}
            op = r.patch(gw.pol_url(g["project"], cfg.region, g["name"]), json=body,
                         params={"updateMask": "customProvider.authzExtension.resources,labels"})
            r.wait(op, NETWORKSECURITY, timeout=900, interval=5)
        elif a.kind == "restore_enforcement":
            gw.ensure_extension(r, g["project"], cfg.region, g["name"], gw.iap_extension_body(cfg),
                                update_fields=("metadata", "failOpen"))
        elif a.kind == "delete_extension":
            gw.delete_extension(r, g["project"], cfg.region, g["name"])
        elif a.kind == "prune_iam":
            self._prune(g)
        elif a.kind == "unbind":
            from ..gcp import engines
            engines.set_gateway_paths(r, cfg, g["engine"], {p: None for p in g["paths"]})
        else:
            raise ValueError(a.kind)

    def _prune(self, g: dict, retries: int = 4) -> None:
        from ..gcp.rest import GcpError, project_number
        url = g["url"] if g["url"] != "root" else iap.resource_url(project_number(self.cfg.gateway_project),
                                                                     self.cfg.region)
        ps = self.derived().principal_set
        if g["kind"] == "platform" and not ps:
            raise RuntimeError("the project principalSet is unknown; refusing to prune a platform entry")
        keep = keep_rule(g["kind"], ps, frozenset(g.get("ours") or ()))
        for attempt in range(retries):
            pol = iap.get_policy(self.r, url)
            bindings, gone = prune(pol, keep)
            if not gone:
                return
            try:
                iap.set_policy(self.r, url, {**pol, "bindings": bindings})
                return
            except GcpError as e:
                if e.status in (409, 412) and attempt < retries - 1:
                    continue
                raise
