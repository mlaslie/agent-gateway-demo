"""Call the theme's orchestrator the way a client does: Agent Runtime `:streamQuery` on the regional
aiplatform endpoint. When the engine has a CLIENT_TO_AGENT gateway attached, the request is governed by that
ingress gateway at the Google Front End (no separate gateway hostname); otherwise it goes straight to Runtime.

The ingress gateway screens request content with Model Armor (a block is a 403 "Model Armor: Prompt violates
content security configurations"). Calls use the backend's own credentials: the operator's ADC locally, the
UI service account on Cloud Run (both need roles/aiplatform.user).
"""
from __future__ import annotations

import json
import time
from functools import lru_cache
from typing import Any

import google.auth
from google.auth.transport.requests import AuthorizedSession

from ..themes import INGRESS_EDGE, Theme
from .rest import SCOPES

# Substrings that identify a Model Armor block in the gateway's response (see ARCHITECTURE.md §Model Armor).
MODEL_ARMOR_MARKERS = ("agdemo-model-armor-block", "model armor", "modelarmor", "sanitization")


@lru_cache(maxsize=1)
def _creds():
    creds, _ = google.auth.default(scopes=SCOPES)
    return creds


def classify(status: int, body: str) -> str:
    low = (body or "").lower()
    if any(m in low for m in MODEL_ARMOR_MARKERS):
        return "blocked"
    # The ingress gateway only blocks on content; any other 401/403 is Agent Runtime IAM, i.e. a setup error.
    return "error"


def _parse(line: str) -> Any:
    line = line.strip()
    if line.startswith("data:"):
        line = line[5:].strip()
    if not line or line.startswith((":", "event:", "id:")):
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def invoke_via_ingress(ctx, theme: Theme, message: str, timeout: float = 120) -> dict[str, Any]:
    cfg = ctx.config
    engine = ((ctx.state or {}).get("themes", {}).get(theme.id, {}).get("orchestrator", {}) or {}).get("engine")
    edge = INGRESS_EDGE
    if not engine:
        from ..state import theme_state
        engine = theme_state(theme.id).get("orchestrator", {}).get("engine")
    if not engine:
        return {"edge": edge, "outcome": "error", "http_status": None, "text": "", "events": [],
                "detail": f"theme {theme.id} has no orchestrator engine (deploy-theme first)"}
    url = f"https://{cfg.region}-aiplatform.googleapis.com/v1/{engine}:streamQuery?alt=sse"
    body = {"class_method": "async_stream_query",
            "input": {"user_id": f"{cfg.prefix}-ingress-user", "message": message}}
    t0 = time.monotonic()
    try:
        s = AuthorizedSession(_creds())
        resp = s.post(url, json=body, stream=True, timeout=timeout)
        status = resp.status_code
        events: list[Any] = []
        texts: list[str] = []
        raw: list[str] = []
        for ln in resp.iter_lines(decode_unicode=True):
            if ln is None:
                continue
            raw.append(ln)
            ev = _parse(ln)
            if ev is None:
                continue
            events.append(ev)
            if isinstance(ev, dict):
                for part in (ev.get("content") or {}).get("parts") or []:
                    if isinstance(part, dict) and part.get("text") and not part.get("thought"):
                        texts.append(part["text"])
        text = "".join(texts)
        raw_body = "\n".join(raw)
    except Exception as e:  # noqa: BLE001
        return {"edge": edge, "outcome": "error", "http_status": None, "text": "", "events": [],
                "detail": f"{type(e).__name__}: {e}"[:800],
                "latency_ms": int((time.monotonic() - t0) * 1000)}
    ms = int((time.monotonic() - t0) * 1000)
    if status >= 400:
        outcome = classify(status, raw_body)
        detail = raw_body[:800]
    else:
        # errors can also arrive inside a 200 stream ({"error": ...} / {"code": 403, ...})
        err = next((e for e in events if isinstance(e, dict) and ("error" in e or e.get("code") in (403, 401))), None)
        if err is not None:
            outcome, detail = classify(int(err.get("code") or 403), json.dumps(err)), json.dumps(err)[:800]
        else:
            outcome, detail = "ok", (text[:300] or "200 OK")
    return {"edge": edge, "outcome": outcome, "http_status": status, "text": text, "events": events,
            "detail": detail, "latency_ms": ms}
