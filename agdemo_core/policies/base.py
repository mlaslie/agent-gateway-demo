"""Policy handler interface (docs/CONTRACTS.md §4). Implementations: agdemo_core/policies/<type>.py."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Protocol, TypedDict

from ..config import DemoConfig, load_config
from ..state import load_state
from ..themes import Policy, Theme

PENDING_SECONDS = 600  # gateway PATCH: 2.5–5 min, Model Armor attach ~4 min, IAM propagation on top

StatusStr = Literal["applied", "removed", "pending", "pending_removal", "error"]


class PolicyStatus(TypedDict):
    applied: bool
    status: StatusStr
    detail: str
    changed_at: str | None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Ctx:
    config: DemoConfig
    state: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Ctx":
        return cls(config=load_config(), state=load_state())


class Handler(Protocol):
    def apply(self, ctx: Ctx, theme: Theme, policy: Policy) -> None: ...
    def remove(self, ctx: Ctx, theme: Theme, policy: Policy) -> None: ...
    def status(self, ctx: Ctx, theme: Theme, policy: Policy) -> PolicyStatus: ...
    def describe(self, ctx: Ctx, theme: Theme, policy: Policy) -> list[str]: ...
