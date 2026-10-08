"""REST helper: ADC-authorized session, error type, long-running operation polling."""
from __future__ import annotations

import threading
import time
from functools import lru_cache
from typing import Any, Callable

import google.auth
from google.auth.transport.requests import AuthorizedSession

SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]

NETWORKSERVICES = "https://networkservices.googleapis.com/v1"
NETWORKSERVICES_BETA = "https://networkservices.googleapis.com/v1beta1"   # authzExtensions
NETWORKSECURITY = "https://networksecurity.googleapis.com/v1"             # authzPolicies
AGENTREGISTRY = "https://agentregistry.googleapis.com/v1"
IAP = "https://iap.googleapis.com/v1"
CRM = "https://cloudresourcemanager.googleapis.com/v1"
SERVICEUSAGE = "https://serviceusage.googleapis.com/v1"
IAM = "https://iam.googleapis.com/v1"
RUN = "https://run.googleapis.com/v2"
ARTIFACTREGISTRY = "https://artifactregistry.googleapis.com/v1"


def aiplatform(region: str, version: str = "v1") -> str:
    return f"https://{region}-aiplatform.googleapis.com/{version}"


def modelarmor(region: str) -> str:
    return f"https://modelarmor.{region}.rep.googleapis.com/v1"


class GcpError(Exception):
    def __init__(self, status: int, message: str, url: str = "", body: Any = None):
        super().__init__(f"HTTP {status}: {message}" + (f" ({url})" if url else ""))
        self.status = status
        self.message = message
        self.url = url
        self.body = body

    @property
    def not_found(self) -> bool:
        return self.status == 404

    @property
    def conflict(self) -> bool:
        return self.status == 409


class Rest:
    """Small wrapper over AuthorizedSession. `credentials` may be impersonated credentials."""

    def __init__(self, credentials=None, quota_project: str | None = None):
        if credentials is None:
            credentials, _ = google.auth.default(scopes=SCOPES)
            if quota_project and hasattr(credentials, "with_quota_project") \
                    and not getattr(credentials, "quota_project_id", None):
                try:
                    credentials = credentials.with_quota_project(quota_project)
                except Exception:
                    pass
        self.credentials = credentials
        self._local = threading.local()

    @property
    def session(self) -> AuthorizedSession:
        s = getattr(self._local, "s", None)
        if s is None:
            s = self._local.s = AuthorizedSession(self.credentials)
        return s

    def request(self, method: str, url: str, *, json: Any = None, params: dict | None = None,
                ok404: bool = False, timeout: float = 60) -> Any:
        r = self.session.request(method, url, json=json, params=params, timeout=timeout)
        if r.status_code == 404 and ok404:
            return None
        if r.status_code >= 400:
            try:
                body = r.json()
                msg = body.get("error", {}).get("message") or r.text
            except Exception:
                body, msg = r.text, r.text
            raise GcpError(r.status_code, str(msg)[:2000], url, body)
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return r.text

    def get(self, url: str, **kw) -> Any:
        return self.request("GET", url, **kw)

    def post(self, url: str, json: Any = None, **kw) -> Any:
        return self.request("POST", url, json=json if json is not None else {}, **kw)

    def patch(self, url: str, json: Any, **kw) -> Any:
        return self.request("PATCH", url, json=json, **kw)

    def delete(self, url: str, **kw) -> Any:
        return self.request("DELETE", url, **kw)

    def list_all(self, url: str, key: str, params: dict | None = None) -> list[dict]:
        out: list[dict] = []
        p = dict(params or {})
        while True:
            r = self.get(url, params=p) or {}
            out += r.get(key, [])
            tok = r.get("nextPageToken")
            if not tok:
                return out
            p["pageToken"] = tok

    # ---- long-running operations
    def wait(self, op: dict, base: str, timeout: float = 1800, interval: float = 10,
             on_tick: Callable[[float], None] | None = None) -> dict:
        """Poll `{base}/{op.name}` until done. Returns the operation's `response` (or {})."""
        if not isinstance(op, dict) or "name" not in op or op.get("done") is True and "error" not in op:
            return (op or {}).get("response", op or {}) if isinstance(op, dict) else {}
        start = time.time()
        name = op["name"]
        while True:
            if op.get("done"):
                if "error" in op:
                    e = op["error"]
                    raise GcpError(e.get("code", 500), e.get("message", str(e)), name, e)
                return op.get("response", {})
            if time.time() - start > timeout:
                raise TimeoutError(f"operation {name} still running after {int(timeout)}s")
            if on_tick:
                on_tick(time.time() - start)
            time.sleep(interval)
            op = self.get(f"{base}/{name}")


@lru_cache(maxsize=4)
def rest(quota_project: str | None = None) -> Rest:
    """Process-wide default client (ADC)."""
    return Rest(quota_project=quota_project)


@lru_cache(maxsize=16)
def project_number(project_id: str) -> str:
    return str(rest(project_id).get(f"{CRM}/projects/{project_id}")["projectNumber"])


@lru_cache(maxsize=16)
def organization_id(project_id: str) -> str | None:
    r = rest(project_id).post(f"{CRM}/projects/{project_id}:getAncestry")
    for a in r.get("ancestor", []):
        if a.get("resourceId", {}).get("type") == "organization":
            return a["resourceId"]["id"]
    return None
