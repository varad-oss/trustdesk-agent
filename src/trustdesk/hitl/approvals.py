"""Approval queue for tier-2 actions (stored in the store DB so CLI, API and graph share it).

Risk tiers (section 5): tier 0 = reads, tier 1 = writes the policy engine allows,
tier 2 = anything the engine returns as `needs_approval` (REF-4 refunds > ₹5,000,
GW-1 credit > ₹200, FR-1 cases). Deny decisions never reach a human: a rubber-stamp
approver cannot approve what the engine forbids (defense in depth).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from trustdesk.env.store import Env
from trustdesk.types import Decision, Outcome


class ApprovalResponse(BaseModel):
    """What a human (or simulated approver) sends to resume the graph."""

    decision: Literal["approve", "reject"]
    note: str = ""
    reviewer: str = "support-lead"


def tier(is_write: bool, decision: Decision | None) -> int:
    if not is_write:
        return 0
    if decision is not None and decision.outcome == Outcome.NEEDS_APPROVAL:
        return 2
    return 1


def create_request(
    env: Env,
    session_id: str,
    tool_use_id: str,
    action: str,
    params: dict[str, Any],
    decision: Decision,
    justification: str,
    excerpt: list[dict[str, str]],
) -> int:
    """Insert a pending approval, idempotent on the tool-use ID (safe if the node re-runs)."""
    existing = env.one("SELECT id FROM approvals WHERE tool_use_id = ?", (tool_use_id,))
    if existing:
        return int(existing["id"])
    return env.insert(
        "approvals",
        {
            "session_id": session_id,
            "tool_use_id": tool_use_id,
            "action": action,
            "params": params,
            "decision": decision.outcome.value,
            "rule_ids": decision.rule_ids,
            "justification": justification,
            "excerpt": excerpt,
            "status": "pending",
            "created_at": env.now.isoformat(),
        },
    )


def record_decision(env: Env, approval_id: int, response: ApprovalResponse, trace_id: str | None = None) -> None:
    """Mark the request decided and audit it. Only the first decision counts (idempotent)."""
    status = "approved" if response.decision == "approve" else "rejected"
    with env.transaction() as conn:
        cur = conn.execute(
            "UPDATE approvals SET status = ?, reviewer_note = ?, decided_at = ? WHERE id = ? AND status = 'pending'",
            (status, response.note, env.now.isoformat(), approval_id),
        )
        changed = cur.rowcount
    if changed:
        row = env.one("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        assert row is not None
        env.audit(row["session_id"], "human", row["action"], row["params"], status, row["rule_ids"], trace_id)


def get(env: Env, approval_id: int) -> dict[str, Any] | None:
    return env.one("SELECT * FROM approvals WHERE id = ?", (approval_id,))


def list_requests(env: Env, status: str | None = "pending") -> list[dict[str, Any]]:
    if status is None:
        return env.all("SELECT * FROM approvals ORDER BY id")
    return env.all("SELECT * FROM approvals WHERE status = ? ORDER BY id", (status,))
