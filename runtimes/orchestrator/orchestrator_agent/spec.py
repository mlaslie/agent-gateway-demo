"""ORCHESTRATOR_SPEC loading. Read lazily from the environment so values come from the server, not the deploy host.

ORCHESTRATOR_SPEC is base64 JSON (raw JSON also accepted) of the theme's Orchestrator plus `topology`:

    {"id": "helpdesk-agent", "display_name": "...", "description": "...", "instruction": "...",
     "model": "gemini-2.5-flash", "malicious_payload": "...",
     "topology": [
       {"id": "kb-agent", "kind": "a2a_agent", "url": "https://...run.app", "display_name": "...",
        "description": "...", "skills": [...], "probe_message": "..."},
       {"id": "tickets-mcp", "kind": "mcp_server", "url": "https://...run.app/mcp", "display_name": "...",
        "tools": [{"name": "get_ticket", "read_only": true, "description": "...", "probe_args": {...}}]}]}

ORCHESTRATOR_SPEC_FILE=<path> may be used instead for large specs.
"""
from __future__ import annotations

import base64
import json
import os
from functools import lru_cache
from typing import Any

DEFAULT_MODEL = "gemini-2.5-flash"


@lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    """The parsed spec, or {} when unset (lets the package import without config, e.g. in unit tests)."""
    if path := os.environ.get("ORCHESTRATOR_SPEC_FILE"):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    raw = os.environ.get("ORCHESTRATOR_SPEC", "").strip()
    if not raw:
        return {}
    return json.loads(raw if raw.startswith("{") else base64.b64decode(raw))


def topology() -> list[dict[str, Any]]:
    return load().get("topology", [])


def component(component_id: str) -> dict[str, Any] | None:
    return next((c for c in topology() if c["id"] == component_id), None)


def model() -> str:
    return os.environ.get("MODEL") or load().get("model") or DEFAULT_MODEL


def auth_mode() -> str:
    """'none' (public Cloud Run targets) or 'id_token' (attach a Google ID token per call)."""
    return os.environ.get("AUTH_MODE", "none").strip().lower()
