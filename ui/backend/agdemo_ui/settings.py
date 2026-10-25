"""Runtime settings for the UI backend: config/state loading (file or env), live availability, admin check.

Config sources, in order:
  AGDEMO_CONFIG_YAML  full demo.yaml contents (e.g. from a Secret Manager secret mounted as env)
  AGDEMO_CONFIG       path to demo.yaml (default config/demo.yaml)
State sources, in order:
  AGDEMO_STATE_JSON   full state.json contents
  AGDEMO_STATE        path to state.json (default config/state.json)
Other:
  AGDEMO_PACE         demo/replay pacing multiplier (1 = realistic, 0 = instant)
  AGDEMO_LIVE_TIMEOUT seconds before a live call counts as failed (default 45)
  AGDEMO_FRONTEND_DIST built frontend dir (default ui/frontend/dist)
  AGDEMO_ADMIN_ALL=1  treat every viewer as admin (local/dev only)
  AGDEMO_IAP_AUDIENCE expected audience of IAP's signed header on Cloud Run
                      (/projects/<NUMBER>/locations/<REGION>/services/<SERVICE>; `ui deploy` sets it,
                      else it's built from the metadata server)
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from agdemo_core.config import REPO_ROOT, DemoConfig, load_config
from agdemo_core.state import STATE_PATH, load_state

_cfg_cache: tuple[float, DemoConfig | None, str] | None = None
_live_cache: tuple[float, bool, str] | None = None


def _bootstrap_env() -> None:
    raw = os.environ.get("AGDEMO_CONFIG_YAML")
    if raw and not os.environ.get("_AGDEMO_CONFIG_FROM_ENV"):
        fd, path = tempfile.mkstemp(prefix="agdemo-", suffix=".yaml")
        with os.fdopen(fd, "w") as f:
            f.write(raw)
        os.environ["AGDEMO_CONFIG"] = path
        os.environ["_AGDEMO_CONFIG_FROM_ENV"] = path
    # Make state visible to code that calls agdemo_core.state.load_state() directly (handlers, clients).
    raw_state = os.environ.get("AGDEMO_STATE_JSON")
    if raw_state and not STATE_PATH.exists():
        try:
            json.loads(raw_state)
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(raw_state)
        except (ValueError, OSError):
            pass


_bootstrap_env()


def pace() -> float:
    try:
        return max(0.0, float(os.environ.get("AGDEMO_PACE", "1")))
    except ValueError:
        return 1.0


def live_timeout() -> float:
    try:
        return float(os.environ.get("AGDEMO_LIVE_TIMEOUT", "45"))
    except ValueError:
        return 45.0


def frontend_dist() -> Path:
    return Path(os.environ.get("AGDEMO_FRONTEND_DIST", REPO_ROOT / "ui" / "frontend" / "dist"))


def get_config() -> tuple[DemoConfig | None, str]:
    """(config, error). Cached for 30 s; never raises."""
    global _cfg_cache
    now = time.monotonic()
    if _cfg_cache and now - _cfg_cache[0] < 30:
        return _cfg_cache[1], _cfg_cache[2]
    try:
        load_config.cache_clear()
        cfg, err = load_config(), ""
    except Exception as e:  # missing / invalid config -> demo only
        cfg, err = None, f"{type(e).__name__}: {e}"
    _cfg_cache = (now, cfg, err)
    return cfg, err


def get_state() -> dict[str, Any]:
    raw = os.environ.get("AGDEMO_STATE_JSON")
    try:
        if raw:
            return json.loads(raw)
        return load_state(Path(os.environ.get("AGDEMO_STATE", STATE_PATH)))
    except Exception:
        return {"shared": {}, "themes": {}}


def theme_deployed(state: dict[str, Any], theme_id: str) -> bool:
    return bool(state.get("themes", {}).get(theme_id, {}).get("orchestrator", {}).get("engine"))


def handlers_importable() -> tuple[bool, str]:
    try:
        from agdemo_core.policies import _MODULES, get_handler, model_armor_handler

        for t in _MODULES:
            get_handler(t)
        model_armor_handler()
        return True, ""
    except Exception as e:
        return False, f"policy handlers unavailable: {type(e).__name__}: {e}"


def live_available() -> tuple[bool, str]:
    """Config loads + state has the shared gateways + handlers import. Cached 30 s."""
    global _live_cache
    now = time.monotonic()
    if _live_cache and now - _live_cache[0] < 30:
        return _live_cache[1], _live_cache[2]
    cfg, err = get_config()
    if cfg is None:
        res = (False, f"config: {err}")
    else:
        shared = get_state().get("shared", {})
        if not (shared.get("egress_gateway") or shared.get("ingress_gateway")):
            res = (False, "state.json has no shared gateways (run ./agdemo bootstrap)")
        else:
            res = handlers_importable()
    _live_cache = (now, res[0], res[1])
    return res


def reset_caches() -> None:
    global _cfg_cache, _live_cache
    _cfg_cache = None
    _live_cache = None


def _norm_member(m: str) -> str:
    m = m.strip().lower()
    for p in ("user:", "serviceaccount:"):
        if m.startswith(p):
            return m[len(p):]
    return m


IAP_ISSUER = "https://cloud.google.com/iap"
IAP_CERTS = "https://www.gstatic.com/iap/verify/public_key"
_METADATA = "http://metadata.google.internal/computeMetadata/v1"
_iap_audience: str | None = None
_iap_seen: dict[str, tuple[float, str]] = {}      # token -> (exp, email); IAP tokens live ~10 min


def iap_audience() -> str:
    """Audience of IAP's signed header for IAP directly on Cloud Run (docs: signed-headers-howto)."""
    global _iap_audience
    if _iap_audience is None:
        aud = os.environ.get("AGDEMO_IAP_AUDIENCE", "")
        if not aud:
            import requests
            h = {"Metadata-Flavor": "Google"}
            num = requests.get(f"{_METADATA}/project/numeric-project-id", headers=h, timeout=5).text.strip()
            region = requests.get(f"{_METADATA}/instance/region", headers=h, timeout=5).text.strip().split("/")[-1]
            aud = f"/projects/{num}/locations/{region}/services/{os.environ['K_SERVICE']}"
        _iap_audience = aud
    return _iap_audience


def iap_email(headers: Any) -> str:
    """The caller's email from IAP's signed header (x-goog-iap-jwt-assertion), verified against IAP's public
    keys, issuer and this service's audience. '' when missing or invalid. The plain
    x-goog-authenticated-user-email header isn't trusted: anything else allowed to invoke the service could
    set it."""
    token = headers.get("x-goog-iap-jwt-assertion") or ""
    if not token:
        return ""
    now = time.time()
    hit = _iap_seen.get(token)
    if hit and hit[0] > now:
        return hit[1]
    try:
        from google.auth.transport import requests as gar
        from google.oauth2 import id_token
        claims = id_token.verify_token(token, gar.Request(), audience=iap_audience(), certs_url=IAP_CERTS)
    except Exception:  # noqa: BLE001
        return ""
    if claims.get("iss") != IAP_ISSUER:
        return ""
    email = str(claims.get("email") or "").split(":")[-1].strip().lower()
    if len(_iap_seen) > 256:
        _iap_seen.clear()
    _iap_seen[token] = (float(claims.get("exp", now + 60)), email)
    return email


def can_admin(headers: Any) -> bool:
    """Locally always true. On Cloud Run (K_SERVICE set) behind IAP, the caller's email from IAP's verified
    signed header must be listed in ui.admin_access (user:/serviceAccount: entries, or domain:<d>). Groups
    can't be resolved here."""
    if os.environ.get("AGDEMO_ADMIN_ALL") == "1":
        return True
    if not os.environ.get("K_SERVICE"):
        return True
    email = iap_email(headers)
    if not email:
        return False
    cfg, _ = get_config()
    if cfg is None:
        return False
    for m in cfg.ui.admin_access:
        ml = m.strip().lower()
        if ml.startswith("domain:") and email.endswith("@" + ml[len("domain:"):]):
            return True
        if _norm_member(m) == email:
            return True
    return False
