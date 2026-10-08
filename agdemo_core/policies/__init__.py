"""Policy handlers registry. `get_handler(policy.type)` -> Handler (see base.py)."""
from __future__ import annotations

import importlib

from .base import PENDING_SECONDS, Ctx, Handler, PolicyStatus  # noqa: F401

_MODULES = {
    "gateway_attach": "gateway_attach",
    "a2a_allow": "egress_allow",
    "mcp_server_allow": "egress_allow",
    "mcp_tool_allow": "egress_allow",
    "ingress_allow": "ingress_allow",
}


def get_handler(policy_type: str) -> Handler:
    mod = importlib.import_module(f"{__name__}.{_MODULES[policy_type]}")
    return mod.HANDLER


def model_armor_handler():
    """Module exposing set(ctx, enabled: bool) and status(ctx) -> {"enabled": bool, "status": str, "detail": str}."""
    return importlib.import_module(f"{__name__}.model_armor")
