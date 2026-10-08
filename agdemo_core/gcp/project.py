"""Project-level helpers: API enablement, service accounts, project / SA IAM bindings, Artifact Registry."""
from __future__ import annotations

from typing import Any

from .rest import ARTIFACTREGISTRY, CRM, IAM, SERVICEUSAGE, GcpError, Rest

REQUIRED_APIS = [
    "aiplatform.googleapis.com",
    "agentregistry.googleapis.com",
    "networkservices.googleapis.com",
    "networksecurity.googleapis.com",
    "compute.googleapis.com",
    "iap.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "modelarmor.googleapis.com",
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "cloudtrace.googleapis.com",
    "telemetry.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
    "dns.googleapis.com",
    "certificatemanager.googleapis.com",
]


# ---------------------------------------------------------------- APIs
def enabled_services(r: Rest, project: str) -> set[str]:
    items = r.list_all(f"{SERVICEUSAGE}/projects/{project}/services", "services",
                       params={"filter": "state:ENABLED", "pageSize": 200})
    return {s["config"]["name"] for s in items}


def enable_services(r: Rest, project: str, services: list[str]) -> None:
    for i in range(0, len(services), 20):
        op = r.post(f"{SERVICEUSAGE}/projects/{project}/services:batchEnable",
                    json={"serviceIds": services[i:i + 20]})
        r.wait(op, SERVICEUSAGE, timeout=900, interval=5)


# ---------------------------------------------------------------- service accounts
def sa_email(project: str, account_id: str) -> str:
    return f"{account_id}@{project}.iam.gserviceaccount.com"


def ensure_sa(r: Rest, project: str, account_id: str, display: str) -> str:
    email = sa_email(project, account_id)
    if r.get(f"{IAM}/projects/{project}/serviceAccounts/{email}", ok404=True) is None:
        try:
            r.post(f"{IAM}/projects/{project}/serviceAccounts",
                   json={"accountId": account_id, "serviceAccount": {"displayName": display}})
        except GcpError as e:
            if not e.conflict:
                raise
    return email


def delete_sa(r: Rest, project: str, email: str) -> bool:
    try:
        r.delete(f"{IAM}/projects/{project}/serviceAccounts/{email}")
        return True
    except GcpError as e:
        if e.not_found:
            return False
        raise


def sa_add_binding(r: Rest, project: str, email: str, member: str, role: str) -> bool:
    url = f"{IAM}/projects/{project}/serviceAccounts/{email}"
    for _ in range(5):
        pol = r.post(f"{url}:getIamPolicy")
        bindings = pol.get("bindings", [])
        for b in bindings:
            if b["role"] == role and member in b.get("members", []) and not b.get("condition"):
                return False
        tgt = next((b for b in bindings if b["role"] == role and not b.get("condition")), None)
        if tgt:
            tgt["members"].append(member)
        else:
            bindings.append({"role": role, "members": [member]})
        try:
            r.post(f"{url}:setIamPolicy", json={"policy": {"bindings": bindings, "etag": pol.get("etag")}})
            return True
        except GcpError as e:
            if e.status not in (409, 412, 400):
                raise
            import time
            time.sleep(3)
    return False


# ---------------------------------------------------------------- project IAM
def get_project_policy(r: Rest, project: str) -> dict:
    return r.post(f"{CRM}/projects/{project}:getIamPolicy", json={"options": {"requestedPolicyVersion": 3}})


def project_roles_of(r: Rest, project: str, member: str) -> set[str]:
    pol = get_project_policy(r, project)
    return {b["role"] for b in pol.get("bindings", []) if member in b.get("members", [])}


def project_add_bindings(r: Rest, project: str, pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Add (member, role) pairs (unconditional) in one read-modify-write. Returns the pairs added."""
    import time
    for attempt in range(6):
        pol = get_project_policy(r, project)
        bindings: list[dict[str, Any]] = pol.get("bindings", [])
        added = []
        for member, role in pairs:
            tgt = next((b for b in bindings if b["role"] == role and not b.get("condition")), None)
            if tgt and member in tgt["members"]:
                continue
            if tgt:
                tgt["members"].append(member)
            else:
                bindings.append({"role": role, "members": [member]})
            added.append((member, role))
        if not added:
            return []
        try:
            r.post(f"{CRM}/projects/{project}:setIamPolicy",
                   json={"policy": {"bindings": bindings, "etag": pol.get("etag"), "version": 3}})
            return added
        except GcpError as e:
            if e.status in (409, 412) or "concurrent" in e.message.lower():
                time.sleep(2 + attempt * 2)
                continue
            raise
    raise GcpError(409, "project IAM policy update kept conflicting")


def project_remove_member(r: Rest, project: str, member: str, roles: set[str] | None = None) -> list[str]:
    import time
    for attempt in range(6):
        pol = get_project_policy(r, project)
        removed = []
        for b in pol.get("bindings", []):
            if member in b.get("members", []) and (roles is None or b["role"] in roles):
                b["members"].remove(member)
                removed.append(b["role"])
        if not removed:
            return []
        pol["bindings"] = [b for b in pol["bindings"] if b.get("members")]
        try:
            r.post(f"{CRM}/projects/{project}:setIamPolicy",
                   json={"policy": {"bindings": pol["bindings"], "etag": pol.get("etag"), "version": 3}})
            return removed
        except GcpError as e:
            if e.status in (409, 412):
                time.sleep(2 + attempt * 2)
                continue
            raise
    return []


def test_permissions(r: Rest, project: str, perms: list[str]) -> set[str]:
    out: set[str] = set()
    for i in range(0, len(perms), 90):
        res = r.post(f"{CRM}/projects/{project}:testIamPermissions", json={"permissions": perms[i:i + 90]})
        out |= set(res.get("permissions", []))
    return out


# ---------------------------------------------------------------- Artifact Registry
def ensure_docker_repo(r: Rest, project: str, region: str, repo: str, labels: dict[str, str]) -> str:
    parent = f"projects/{project}/locations/{region}"
    if r.get(f"{ARTIFACTREGISTRY}/{parent}/repositories/{repo}", ok404=True) is None:
        op = r.post(f"{ARTIFACTREGISTRY}/{parent}/repositories",
                    json={"format": "DOCKER", "labels": labels, "description": "agdemo images"},
                    params={"repositoryId": repo})
        r.wait(op, ARTIFACTREGISTRY, timeout=300, interval=3)
    return f"{region}-docker.pkg.dev/{project}/{repo}"
