"""IAP IAM policies on Agent Registry resources (who may egress to what through Agent Gateway).

REST (verified with `gcloud iap web get-iam-policy --resource-type=agent-registry --log-http`):
  https://iap.googleapis.com/v1/projects/<NUMBER>/locations/<R>/iap_web/agentRegistry:getIamPolicy           (whole registry)
  https://iap.googleapis.com/v1/projects/<NUMBER>/locations/<R>/iap_web/agentRegistry/<kind>/<id>:getIamPolicy
  kind: agents | mcpServers | endpoints

Bindings use roles/iap.egressor (permission iap.webServiceVersions.egressViaIAP) for egress. MCP
tool-level conditions (verified codelab agw-cuj-arun-egress-emcp):
  api.getAttribute('iap.googleapis.com/mcp.toolName', '') in ['get_ticket', '']
  api.getAttribute('iap.googleapis.com/mcp.tool.isReadOnly', false) == true
"""
from __future__ import annotations

from typing import Any

from .rest import IAP, GcpError, Rest

EGRESSOR = "roles/iap.egressor"
ACCESSOR = "roles/iap.httpsResourceAccessor"


def resource_url(project_number: str, region: str, kind: str | None = None, rid: str | None = None) -> str:
    base = f"{IAP}/projects/{project_number}/locations/{region}/iap_web/agentRegistry"
    return f"{base}/{kind}/{rid}" if kind and rid else base


def from_registry_resource(rr: str) -> str:
    """'projects/N/locations/R/mcpServers/ID' -> IAP resource URL."""
    p = rr.split("/")
    return resource_url(p[1], p[3], p[4], p[5])


def get_policy(r: Rest, url: str) -> dict:
    return r.post(f"{url}:getIamPolicy", json={"options": {"requestedPolicyVersion": 3}})


def set_policy(r: Rest, url: str, policy: dict) -> dict:
    policy = {**policy, "version": 3}
    return r.post(f"{url}:setIamPolicy", json={"policy": policy})


def _same_cond(a: dict | None, b: dict | None) -> bool:
    if not a and not b:
        return True
    return bool(a and b and a.get("expression") == b.get("expression"))


def has_binding(policy: dict, member: str, role: str = EGRESSOR, condition: dict | None | bool = False) -> bool:
    """condition=False: any condition; None: unconditional only; dict: that exact expression."""
    for b in policy.get("bindings", []):
        if b.get("role") != role or member not in b.get("members", []):
            continue
        if condition is False or _same_cond(b.get("condition"), condition):
            return True
    return False


def member_bindings(policy: dict, member: str, role: str = EGRESSOR) -> list[dict]:
    return [b for b in policy.get("bindings", []) if b.get("role") == role and member in b.get("members", [])]


def add_binding(r: Rest, url: str, member: str, role: str = EGRESSOR, condition: dict | None = None,
                replace_member_bindings: bool = True, retries: int = 4) -> bool:
    """Grant `role` to `member` (optionally conditional). If replace_member_bindings, any other binding
    of that member+role is removed first (so 'all tools' and 'read-only tools' don't stack). Returns changed."""
    for attempt in range(retries):
        pol = get_policy(r, url)
        if has_binding(pol, member, role, condition) and \
                (not replace_member_bindings or len(member_bindings(pol, member, role)) == 1):
            return False
        bindings = []
        for b in pol.get("bindings", []):
            if replace_member_bindings and b.get("role") == role and member in b.get("members", []):
                rest_m = [m for m in b["members"] if m != member]
                if rest_m:
                    bindings.append({**b, "members": rest_m})
                continue
            bindings.append(b)
        nb: dict[str, Any] = {"role": role, "members": [member]}
        if condition:
            nb["condition"] = condition
        bindings.append(nb)
        try:
            set_policy(r, url, {"bindings": bindings, "etag": pol.get("etag")})
            return True
        except GcpError as e:
            if e.status in (409, 412) and attempt < retries - 1:
                continue
            raise
    return False


def remove_member(r: Rest, url: str, member: str, role: str = EGRESSOR, retries: int = 4) -> bool:
    for attempt in range(retries):
        try:
            pol = get_policy(r, url)
        except GcpError as e:
            if e.not_found:
                return False
            raise
        if not member_bindings(pol, member, role):
            return False
        bindings = []
        for b in pol.get("bindings", []):
            if b.get("role") == role and member in b.get("members", []):
                rest_m = [m for m in b["members"] if m != member]
                if rest_m:
                    bindings.append({**b, "members": rest_m})
                continue
            bindings.append(b)
        try:
            set_policy(r, url, {"bindings": bindings, "etag": pol.get("etag")})
            return True
        except GcpError as e:
            if e.status in (409, 412) and attempt < retries - 1:
                continue
            raise
    return False


def remove_binding(r: Rest, url: str, member: str, role: str = EGRESSOR, condition: dict | None = None,
                   retries: int = 4) -> bool:
    """Remove `member` only from the binding with exactly this condition (None = the unconditional one)."""
    for attempt in range(retries):
        try:
            pol = get_policy(r, url)
        except GcpError as e:
            if e.not_found:
                return False
            raise
        changed, bindings = False, []
        for b in pol.get("bindings", []):
            if b.get("role") == role and member in b.get("members", []) and _same_cond(b.get("condition"), condition):
                changed = True
                rest_m = [m for m in b["members"] if m != member]
                if rest_m:
                    bindings.append({**b, "members": rest_m})
                continue
            bindings.append(b)
        if not changed:
            return False
        try:
            set_policy(r, url, {"bindings": bindings, "etag": pol.get("etag")})
            return True
        except GcpError as e:
            if e.status in (409, 412) and attempt < retries - 1:
                continue
            raise
    return False


# ---------------------------------------------------------------- MCP tool conditions
def tool_name_condition(tools: list[str], title: str) -> dict:
    """Allow only these tools. '' keeps non-tool MCP traffic (initialize, tools/list, notifications) working."""
    names = ", ".join(f"'{t}'" for t in [*tools, ""])
    return {"title": title[:100],
            "description": "Agent Gateway MCP tool-level policy (agdemo)",
            "expression": f"api.getAttribute('iap.googleapis.com/mcp.toolName', '') in [{names}]"}


def read_only_condition(title: str, read_only_tools: list[str] | None = None) -> dict:
    """Read-only tools only. Non-tool MCP methods carry no toolName -> allowed via the '' branch."""
    expr = ("api.getAttribute('iap.googleapis.com/mcp.tool.isReadOnly', false) == true || "
            "api.getAttribute('iap.googleapis.com/mcp.toolName', '') == ''")
    return {"title": title[:100], "description": "Agent Gateway MCP read-only tools (agdemo)",
            "expression": expr}
