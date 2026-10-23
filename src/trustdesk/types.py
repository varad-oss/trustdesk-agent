"""Small shared types used by the policy engine, tools and agent graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from trustdesk.config import DefenseLevel


class Outcome(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    NEEDS_APPROVAL = "needs_approval"


@dataclass(frozen=True)
class Decision:
    """Result of a policy check. `rule_ids` cite the written policy (docs/policy)."""

    outcome: Outcome
    rule_ids: list[str] = field(default_factory=list)
    message: str = ""

    @classmethod
    def allow(cls, rule_ids: list[str] | None = None, message: str = "allowed") -> Decision:
        return cls(Outcome.ALLOW, rule_ids or [], message)

    @classmethod
    def deny(cls, rule_ids: list[str], message: str) -> Decision:
        return cls(Outcome.DENY, rule_ids, message)

    @classmethod
    def needs_approval(cls, rule_ids: list[str], message: str) -> Decision:
        return cls(Outcome.NEEDS_APPROVAL, rule_ids, message)

    def to_dict(self) -> dict:
        return {"outcome": self.outcome.value, "rule_ids": self.rule_ids, "message": self.message}


@dataclass
class Session:
    """Per-conversation facts the tool layer needs. Lives in graph state between calls."""

    session_id: str
    defense_level: DefenseLevel = DefenseLevel.D3
    verified_customer_id: str | None = None
    trace_id: str | None = None
