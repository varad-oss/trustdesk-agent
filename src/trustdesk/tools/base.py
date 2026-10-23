"""Shared plumbing for tools: context object and the errors-as-data result shape."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from trustdesk.env.store import Env
from trustdesk.types import Decision, Session


class Retriever(Protocol):
    def search(self, query: str, k: int = 4) -> list[dict[str, Any]]: ...


@dataclass
class ToolContext:
    """Everything a tool may touch. Tools never import global state."""

    env: Env
    session: Session
    retriever: Retriever | None = None
    approved_by: str | None = None  # set only when a human approved this exact call (D3)


def ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def err(error_code: str, message: str, hint: str = "", rule_ids: list[str] | None = None) -> dict[str, Any]:
    """Errors are returned to the model as data so it can recover, never raised."""
    out: dict[str, Any] = {"ok": False, "error_code": error_code, "message": message, "hint": hint}
    if rule_ids:
        out["rule_ids"] = rule_ids
    return out


def denial(decision: Decision) -> dict[str, Any]:
    if decision.outcome.value == "needs_approval":
        return err(
            "APPROVAL_REQUIRED",
            decision.message,
            "This needs a human support lead. Tell the customer, and use escalate_to_human.",
            decision.rule_ids,
        )
    code = "NOT_VERIFIED" if decision.rule_ids == ["ID-1"] else "POLICY_DENIED"
    hint = (
        "Ask for the registered email or phone and an order ID, then call find_and_verify_customer."
        if code == "NOT_VERIFIED"
        else "Nothing was changed. If you used a wrong argument (e.g. destination or amount), correct it and retry; "
        "otherwise explain the refusal to the customer and cite the rule ID. Never say it was done."
    )
    return err(code, decision.message, hint, decision.rule_ids)
