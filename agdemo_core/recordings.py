"""Recorded test runs (docs/CONTRACTS.md §8).

themes/<theme>/recordings/<test_id>/<signature>.json:
{"theme": "helpdesk", "test": "ask-kb", "signature": "allow-kb+gw-egress+ma-off", "recorded_at": "...",
 "events": [{"type": "status", "text": "...", "t_ms": 0}, ...]}
"""
from __future__ import annotations

import copy
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .themes import THEMES_DIR

MAX_GAP_S = 1.5


# --- optional GCS store: AGDEMO_RECORDINGS_URI=gs://bucket/prefix (Cloud Run: survives restarts/redeploys)
def _gcs() -> tuple[str, str] | None:
    uri = os.environ.get("AGDEMO_RECORDINGS_URI", "")
    if not uri.startswith("gs://"):
        return None
    bucket, _, prefix = uri[5:].partition("/")
    return bucket, prefix.strip("/")


def _gcs_session():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
    return AuthorizedSession(creds)


def _gcs_name(prefix: str, theme_id: str, test_id: str, sig: str) -> str:
    return "/".join(x for x in (prefix, theme_id, test_id, f"{sig}.json") if x)


def _gcs_load(theme_id: str, test_id: str, sig: str) -> dict[str, Any] | None:
    from urllib.parse import quote
    bucket, prefix = _gcs()  # type: ignore[misc]
    name = quote(_gcs_name(prefix, theme_id, test_id, sig), safe="")
    try:
        r = _gcs_session().get(f"https://storage.googleapis.com/storage/v1/b/{bucket}/o/{name}?alt=media", timeout=10)
        return r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001 - a missing/unreadable recording just means "simulate"
        return None


def _gcs_save(theme_id: str, test_id: str, sig: str, rec: dict[str, Any]) -> str:
    bucket, prefix = _gcs()  # type: ignore[misc]
    name = _gcs_name(prefix, theme_id, test_id, sig)
    r = _gcs_session().post(f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o",
                            params={"uploadType": "media", "name": name}, data=json.dumps(rec, indent=2),
                            headers={"Content-Type": "application/json"}, timeout=20)
    r.raise_for_status()
    return f"gs://{bucket}/{name}"


def recordings_dir(theme_id: str, themes_dir: Path | None = None) -> Path:
    """themes/<id>/recordings, or $AGDEMO_RECORDINGS_DIR/<id> when set (e.g. a writable volume on Cloud Run)."""
    override = os.environ.get("AGDEMO_RECORDINGS_DIR")
    if override and themes_dir is None:
        return Path(override) / theme_id
    return (themes_dir or THEMES_DIR) / theme_id / "recordings"


def recording_path(theme_id: str, test_id: str, sig: str, themes_dir: Path | None = None) -> Path:
    return recordings_dir(theme_id, themes_dir) / test_id / f"{sig}.json"


def load(theme_id: str, test_id: str, sig: str, themes_dir: Path | None = None) -> dict[str, Any] | None:
    if _gcs() and themes_dir is None:
        rec = _gcs_load(theme_id, test_id, sig)
        if rec is not None and isinstance(rec.get("events"), list):
            return rec
    candidates = [recording_path(theme_id, test_id, sig, themes_dir),
                  (themes_dir or THEMES_DIR) / theme_id / "recordings" / test_id / f"{sig}.json"]
    p = next((c for c in candidates if c.exists()), None)
    if p is None:
        return None
    try:
        rec = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return rec if isinstance(rec.get("events"), list) else None


def save(theme_id: str, test_id: str, sig: str, events: list[dict[str, Any]],
         themes_dir: Path | None = None) -> Path | str:
    rec = {"theme": theme_id, "test": test_id, "signature": sig,
           "recorded_at": datetime.now(timezone.utc).isoformat(), "events": events}
    if _gcs() and themes_dir is None:
        return _gcs_save(theme_id, test_id, sig, rec)
    p = recording_path(theme_id, test_id, sig, themes_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=2))
    tmp.replace(p)
    return p


def list_recordings(theme_id: str, themes_dir: Path | None = None) -> dict[str, list[str]]:
    d = recordings_dir(theme_id, themes_dir)
    if not d.exists():
        return {}
    return {t.name: sorted(f.stem for f in t.glob("*.json")) for t in sorted(d.iterdir()) if t.is_dir()}


class Recorder:
    """Collects SSE events with `t_ms` offsets from creation."""

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.events: list[dict[str, Any]] = []

    def add(self, event: dict[str, Any]) -> dict[str, Any]:
        e = {k: v for k, v in event.items() if k != "t_ms"}
        e["t_ms"] = int((time.monotonic() - self.t0) * 1000)
        self.events.append(e)
        return event


def _mark_replayed(event: dict[str, Any], source: str) -> dict[str, Any]:
    e = copy.deepcopy(event)
    e.pop("t_ms", None)
    if e.get("type") == "edge" and isinstance(e.get("state"), dict):
        e["state"]["source"] = source
    if e.get("type") == "done" and isinstance(e.get("edges"), dict):
        for st in e["edges"].values():
            if isinstance(st, dict):
                st["source"] = source
    return e


def replay(rec: dict[str, Any], source: str = "replayed",
           max_gap_s: float = MAX_GAP_S) -> Iterator[tuple[float, dict[str, Any]]]:
    """Yield (delay_seconds_before, event) keeping relative timing, gaps capped at `max_gap_s`.

    Edge states are re-labelled with `source` ("replayed"). Fallback/error events from the
    recording are dropped; a `done` event is synthesized if the recording lacks one.
    """
    prev = None
    edges: dict[str, Any] = {}
    saw_done = False
    for raw in rec.get("events", []):
        if raw.get("type") in ("fallback", "error"):
            continue
        t = int(raw.get("t_ms", 0) or 0)
        delay = 0.0 if prev is None else min(max_gap_s, max(0.0, (t - prev) / 1000))
        prev = t
        e = _mark_replayed(raw, source)
        if e.get("type") == "edge":
            edges[e["edge"]] = e["state"]
        if e.get("type") == "done":
            saw_done = True
        yield delay, e
    if not saw_done:
        yield 0.1, {"type": "done", "edges": edges}
