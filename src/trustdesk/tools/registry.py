"""Tool registry: one list of tools, their schemas for the model, and a single dispatcher.

The tool list is constant for every conversation (required when thinking blocks are
replayed: the tools prefix must not change). Permissions are enforced inside the
tools, never by hiding tools from the model.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from trustdesk.tools import read, write
from trustdesk.tools.base import ToolContext, err


@dataclass(frozen=True)
class ToolDef:
    name: str
    input_model: type[BaseModel]
    fn: Callable[..., dict[str, Any]]
    is_write: bool


TOOLS: list[ToolDef] = [
    ToolDef("find_and_verify_customer", read.FindAndVerifyCustomerInput, read.find_and_verify_customer, False),
    ToolDef("get_order", read.GetOrderInput, read.get_order, False),
    ToolDef("list_orders", read.ListOrdersInput, read.list_orders, False),
    ToolDef("get_product_reviews", read.GetProductReviewsInput, read.get_product_reviews, False),
    ToolDef("get_ticket_history", read.GetTicketHistoryInput, read.get_ticket_history, False),
    ToolDef("search_policy", read.SearchPolicyInput, read.search_policy, False),
    ToolDef("cancel_order", write.CancelOrderInput, write.cancel_order, True),
    ToolDef("create_return", write.CreateReturnInput, write.create_return, True),
    ToolDef("issue_refund", write.IssueRefundInput, write.issue_refund, True),
    ToolDef("update_shipping_address", write.UpdateShippingAddressInput, write.update_shipping_address, True),
    ToolDef("grant_goodwill_credit", write.GrantGoodwillCreditInput, write.grant_goodwill_credit, True),
    ToolDef("escalate_to_human", write.EscalateToHumanInput, write.escalate_to_human, True),
]
BY_NAME = {t.name: t for t in TOOLS}
WRITE_TOOLS = {t.name for t in TOOLS if t.is_write}


def tool_specs() -> list[dict[str, Any]]:
    """Anthropic-format tool definitions, deterministic order (keeps the prompt cache warm)."""
    specs = []
    for t in TOOLS:
        schema = t.input_model.model_json_schema()
        schema.pop("title", None)
        schema.pop("description", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        specs.append({"name": t.name, "description": inspect.cleandoc(t.input_model.__doc__ or ""), "input_schema": schema})
    return specs


def validate_args(name: str, raw_args: dict[str, Any]) -> BaseModel | dict[str, Any]:
    """Parse model-supplied arguments; returns the model or an error result."""
    tool = BY_NAME.get(name)
    if tool is None:
        return err("UNKNOWN_TOOL", f"No tool named {name}.", f"Available tools: {', '.join(BY_NAME)}.")
    try:
        return tool.input_model.model_validate(raw_args)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return err("INVALID_ARGUMENTS", problems, "Fix the arguments to match the tool schema and retry.")


def execute_tool(ctx: ToolContext, name: str, raw_args: dict[str, Any], idempotency_key: str | None = None) -> dict[str, Any]:
    """Run one tool call. Never raises into the agent: unexpected errors become data too."""
    parsed = validate_args(name, raw_args)
    if isinstance(parsed, dict):
        return parsed
    tool = BY_NAME[name]
    if not tool.is_write:
        try:
            return tool.fn(ctx, parsed)
        except Exception as exc:  # noqa: BLE001 - tool errors are data for the model
            return err("TOOL_ERROR", f"{type(exc).__name__}: {exc}", "Try again or escalate.")

    key = idempotency_key or f"{ctx.session.session_id}:{name}:{json.dumps(raw_args, sort_keys=True)}"
    previous = ctx.env.one("SELECT result FROM idempotency WHERE key = ?", (key,))
    if previous is not None:
        return {**previous["result"], "idempotent_replay": True}
    try:
        result = tool.fn(ctx, parsed, key)
    except sqlite3.IntegrityError as exc:
        # The domain row carries the same UNIQUE key: the write landed before a crash,
        # but its idempotency record did not. Report it as already applied.
        if "idempotency_key" in str(exc):
            return {"ok": True, "data": {"note": "already applied"}, "idempotent_replay": True}
        return err("TOOL_ERROR", f"IntegrityError: {exc}", "Try again or escalate.")
    except Exception as exc:  # noqa: BLE001
        return err("TOOL_ERROR", f"{type(exc).__name__}: {exc}", "Try again or escalate.")
    if result.get("ok"):
        ctx.env.insert("idempotency", {"key": key, "tool": name, "result": result, "created_at": ctx.env.now.isoformat()})
    return result
