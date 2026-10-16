"""Plan-safety check for the Terraform foundation (docs/TERRAFORM_PLAN.md §6).

Reads `terraform show -json <planfile>` and refuses any plan that deletes or replaces a resource:
actions ["delete"], ["delete", "create"] or ["create", "delete"]. Exceptions:
  allow_replace=True        every replace is allowed (deletes still refused)
  allow_template_swap=True  create_before_destroy swaps of the connectivity template (T4) are allowed
  migration=True            adopting a live environment (T2, plan §5.1): additionally refuses every create
                            except the additive types (MIGRATION_CREATE_OK). A create of anything else means
                            an import is missing, and apply would try to recreate a live resource.

Usage: python -m agdemo_core.tf_safety plan.json [--allow-replace] [--allow-template-swap] [--migration]
Exit 0 = safe, 1 = blocked, 2 = unreadable input.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TEMPLATE_TYPE = "google_network_services_agent_connectivity_template"
REPLACE = (["delete", "create"], ["create", "delete"])
# Creating these over an existing live grant/API is a no-op (additive), so a migration plan may create them.
# terraform_data is Terraform-only (the T3 destroy guard, infra/terraform/guards.tf): no GCP resource.
MIGRATION_CREATE_OK = ("google_project_service", "google_project_iam_member", "google_storage_bucket_iam_member",
                       "google_iap_agent_registry_endpoint_iam_member", "terraform_data")


@dataclass
class Verdict:
    ok: bool
    blockers: list[tuple[str, list[str]]] = field(default_factory=list)   # (address, actions)
    imported: list[str] = field(default_factory=list)
    allowed_replaces: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)                 # create/update/replace/delete/read/no-op

    def summary(self) -> str:
        c = self.counts
        s = (f"{c.get('create', 0)} to add, {c.get('update', 0)} to change, {c.get('replace', 0)} to replace, "
             f"{c.get('delete', 0)} to destroy")
        if c.get("import"):
            s += f", {c['import']} to import"
        return s


def check(plan: dict[str, Any], allow_replace: bool = False, allow_template_swap: bool = False,
          migration: bool = False) -> Verdict:
    v = Verdict(ok=True, counts={k: 0 for k in ("create", "update", "replace", "delete", "read", "no-op", "import")})
    for rc in plan.get("resource_changes") or []:
        if rc.get("mode") == "data":
            continue
        ch = rc.get("change") or {}
        actions = list(ch.get("actions") or [])
        addr = rc.get("address", "?")
        if ch.get("importing"):
            v.counts["import"] += 1
            v.imported.append(addr)
        if actions in REPLACE:
            v.counts["replace"] += 1
            if allow_replace or (allow_template_swap and rc.get("type") == TEMPLATE_TYPE
                                 and actions == ["create", "delete"]):
                v.allowed_replaces.append(addr)
            else:
                v.blockers.append((addr, actions))
        elif "delete" in actions:
            v.counts["delete"] += 1
            v.blockers.append((addr, actions))
        elif actions:
            if migration and actions == ["create"] and rc.get("type") not in MIGRATION_CREATE_OK:
                v.blockers.append((addr, actions))
            key = actions[0] if actions[0] in v.counts else None
            if key:
                v.counts[key] += 1
    v.ok = not v.blockers
    return v


def check_file(path: str | Path, **kw) -> Verdict:
    return check(json.loads(Path(path).read_text()), **kw)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    flags = {a for a in args if a.startswith("--")}
    files = [a for a in args if not a.startswith("--")]
    if len(files) != 1:
        print(__doc__.strip().splitlines()[-2])
        return 2
    try:
        v = check_file(files[0], allow_replace="--allow-replace" in flags,
                       allow_template_swap="--allow-template-swap" in flags, migration="--migration" in flags)
    except (OSError, ValueError) as e:
        print(f"cannot read plan JSON: {e}")
        return 2
    print(f"plan: {v.summary()}")
    for a in v.allowed_replaces:
        print(f"  allowed replace: {a}")
    for addr, actions in v.blockers:
        print(f"  BLOCKED {'/'.join(actions)}: {addr}")
    print("plan-safety: OK" if v.ok else "plan-safety: REFUSED (deletes, replaces"
          + (" or non-additive creates" if "--migration" in flags else "") + "; see above)")
    return 0 if v.ok else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
