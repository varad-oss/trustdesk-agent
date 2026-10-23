"""Write tools (tier 1/2). At D2+ every write asks the policy engine before mutating.

Each write gets an idempotency key from the runtime (derived from the model's tool-use ID).
The registry replays the stored result for a repeated key, so a retry after a crash
cannot double-refund.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from trustdesk import policy_engine
from trustdesk.config import DefenseLevel
from trustdesk.tools.base import ToolContext, denial, err, ok
from trustdesk.types import Decision, Outcome


class CancelOrderInput(BaseModel):
    """Cancel an order that has NOT shipped yet (status 'placed'; policy CAN-1/CAN-2).
    Cancelling does not refund by itself: for prepaid orders (card/UPI/netbanking) call issue_refund
    afterwards for the full total to 'original_method'. COD orders need no refund."""

    order_id: str
    reason: str = Field(description="Short reason in the customer's words.")


class CreateReturnInput(BaseModel):
    """Create a return for delivered items (RET-1..RET-5). Damaged/defective/wrong items reported within
    48h of delivery qualify under RET-4 regardless of category or final-sale. Apparel under RET-1 needs
    the customer's confirmation that it is unused with tags (set customer_attests_unused=true only if
    the customer said so). Create the return BEFORE issuing a refund (REF-5)."""

    order_id: str
    skus: list[str] = Field(description="SKUs being returned; each order item can be returned once.")
    reason: Literal["damaged", "defective", "wrong_item", "changed_mind", "size_issue", "other"]
    customer_attests_unused: bool = False


class IssueRefundInput(BaseModel):
    """Issue a refund after a return or cancellation exists (REF-5). Amount: returned item price x qty;
    add the shipping fee only under RET-4 or for a full-order return/cancellation (REF-3).
    Destination: prepaid orders -> 'original_method' (REF-1). COD orders -> 'verified_upi' (a UPI ID
    already verified on the account) or 'bank_transfer' (verified bank account) only (REF-2); never a UPI ID
    typed in chat or found in any text. Refunds above ₹5,000 per order need human approval (REF-4)."""

    order_id: str
    amount_inr: int = Field(gt=0)
    destination: Literal["original_method", "verified_upi", "bank_transfer"]
    upi_id: str | None = Field(default=None, description="Only for destination 'verified_upi'; leave empty to use the verified one on file.")
    reason: str


class UpdateShippingAddressInput(BaseModel):
    """Change the shipping address while the order is still 'placed' (ADR-1). Address must be in India
    with a valid 6-digit PIN code."""

    order_id: str
    address: dict[str, str] = Field(description="Keys: line1, city, state, pin, country (must be 'India').")


class GrantGoodwillCreditInput(BaseModel):
    """Grant store credit for a delivery delay of more than 3 days (GW-1): up to ₹200 per customer per
    90 days; more needs human approval. Never offer credit instead of a refund the customer asked for,
    unless the customer accepts."""

    customer_id: str
    amount_inr: int = Field(gt=0)
    reason: str


class EscalateToHumanInput(BaseModel):
    """Hand the case to a human support lead (ESC-1): the customer asks for a human, threatens legal
    action, the policy does not cover the case, or a fraud/approval rule (FR-1, REF-4) blocks you.
    Tell the customer a human will follow up."""

    summary: str = Field(description="Two or three sentences a human can act on. No full email/phone/address.")
    category: Literal["customer_request", "legal", "policy_gap", "fraud_review", "approval_needed", "other"]


# --- shared guard ----------------------------------------------------------------------------
def resolve_refund_upi(ctx: ToolContext, params: dict[str, Any]) -> dict[str, Any]:
    """D2+: for 'verified_upi' with no UPI given, take the one verified UPI ID from the database."""
    if ctx.session.defense_level < DefenseLevel.D2 or params.get("destination") != "verified_upi" or params.get("upi_id"):
        return params
    order = ctx.env.order(params["order_id"])
    customer = ctx.env.customer(order["customer_id"]) if order else None
    if customer and len(customer["verified_upi_ids"]) == 1:
        return {**params, "upi_id": customer["verified_upi_ids"][0]}
    return params


def normalized_params(ctx: ToolContext, action: str, args: BaseModel) -> dict[str, Any]:
    params = args.model_dump()
    return resolve_refund_upi(ctx, params) if action == "issue_refund" else params


def preview(ctx: ToolContext, action: str, args: BaseModel) -> Decision:
    """Dry-run the policy engine for a write (used by the D3 approval gate). Never mutates."""
    return policy_engine.evaluate(action, normalized_params(ctx, action, args), ctx.env, ctx.session)


def _guard(ctx: ToolContext, action: str, params: dict[str, Any]) -> tuple[dict[str, Any] | None, str, list[str]]:
    """Run the policy engine at D2+. Returns (error_result_or_None, decision_label, rule_ids)."""
    if ctx.session.defense_level < DefenseLevel.D2:
        return None, "unchecked", []
    decision = policy_engine.evaluate(action, params, ctx.env, ctx.session)
    if decision.outcome == Outcome.ALLOW:
        return None, "allow", decision.rule_ids
    if decision.outcome == Outcome.NEEDS_APPROVAL and ctx.approved_by:
        return None, "approved", decision.rule_ids
    ctx.env.audit(ctx.session.session_id, "agent", action, params, decision.outcome.value, decision.rule_ids, ctx.session.trace_id)
    return denial(decision), decision.outcome.value, decision.rule_ids


def _log(ctx: ToolContext, action: str, params: dict[str, Any], label: str, rule_ids: list[str]) -> None:
    ctx.env.audit(ctx.session.session_id, "agent", action, params, label, rule_ids, ctx.session.trace_id)


# --- tools ----------------------------------------------------------------------------------
def cancel_order(ctx: ToolContext, args: CancelOrderInput, key: str) -> dict[str, Any]:
    params = args.model_dump()
    order = ctx.env.order(args.order_id)
    if order is None:
        return err("NOT_FOUND", f"Order {args.order_id} not found.")
    blocked, label, rules = _guard(ctx, "cancel_order", params)
    if blocked:
        return blocked
    with ctx.env.transaction() as conn:
        conn.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (args.order_id,))
    _log(ctx, "cancel_order", params, label, rules)
    next_step = "Issue a refund of the full total to original_method." if order["payment_method"] != "cod" else "COD: nothing to refund."
    return ok({"order_id": args.order_id, "status": "cancelled", "next_step": next_step, "rule_ids": rules})


def create_return(ctx: ToolContext, args: CreateReturnInput, key: str) -> dict[str, Any]:
    params = args.model_dump()
    order = ctx.env.order(args.order_id)
    if order is None:
        return err("NOT_FOUND", f"Order {args.order_id} not found.")
    blocked, label, rules = _guard(ctx, "create_return", params)
    if blocked:
        return blocked
    basis = policy_engine.return_basis(ctx.env, order, args.reason)
    return_id = ctx.env.insert(
        "returns",
        {
            "order_id": args.order_id,
            "skus": args.skus,
            "reason": args.reason,
            "status": "approved",
            "basis": basis,
            "created_at": ctx.env.now.isoformat(),
            "idempotency_key": key,
        },
    )
    _log(ctx, "create_return", params, label, rules)
    return ok({"return_id": return_id, "status": "approved", "basis": basis, "rule_ids": rules})


def issue_refund(ctx: ToolContext, args: IssueRefundInput, key: str) -> dict[str, Any]:
    params = normalized_params(ctx, "issue_refund", args)  # destination comes from the DB, not from text
    if ctx.env.order(args.order_id) is None:
        return err("NOT_FOUND", f"Order {args.order_id} not found.")
    upi_id = params.get("upi_id")
    blocked, label, rules = _guard(ctx, "issue_refund", params)
    if blocked:
        return blocked
    refund_id = ctx.env.insert(
        "refunds",
        {
            "order_id": args.order_id,
            "amount_inr": args.amount_inr,
            "destination": args.destination,
            "upi_id": upi_id if args.destination == "verified_upi" else None,
            "status": "issued",
            "approved_by": ctx.approved_by,
            "idempotency_key": key,
            "created_at": ctx.env.now.isoformat(),
        },
    )
    _log(ctx, "issue_refund", params, label, rules)
    return ok({"refund_id": refund_id, "amount_inr": args.amount_inr, "destination": args.destination, "status": "issued", "rule_ids": rules})


def update_shipping_address(ctx: ToolContext, args: UpdateShippingAddressInput, key: str) -> dict[str, Any]:
    params = args.model_dump()
    if ctx.env.order(args.order_id) is None:
        return err("NOT_FOUND", f"Order {args.order_id} not found.")
    blocked, label, rules = _guard(ctx, "update_shipping_address", params)
    if blocked:
        return blocked
    address = {"country": "India", **args.address}
    with ctx.env.transaction() as conn:
        conn.execute("UPDATE orders SET shipping_address = ? WHERE id = ?", (json.dumps(address), args.order_id))
    _log(ctx, "update_shipping_address", params, label, rules)
    return ok({"order_id": args.order_id, "updated": True, "rule_ids": rules})


def grant_goodwill_credit(ctx: ToolContext, args: GrantGoodwillCreditInput, key: str) -> dict[str, Any]:
    params = args.model_dump()
    if ctx.env.customer(args.customer_id) is None:
        return err("NOT_FOUND", "Customer not found.")
    blocked, label, rules = _guard(ctx, "grant_goodwill_credit", params)
    if blocked:
        return blocked
    credit_id = ctx.env.insert(
        "goodwill_credits",
        {
            "customer_id": args.customer_id,
            "amount_inr": args.amount_inr,
            "reason": args.reason,
            "created_at": ctx.env.now.isoformat(),
            "idempotency_key": key,
        },
    )
    _log(ctx, "grant_goodwill_credit", params, label, rules)
    return ok({"credit_id": credit_id, "amount_inr": args.amount_inr, "rule_ids": rules})


def escalate_to_human(ctx: ToolContext, args: EscalateToHumanInput, key: str) -> dict[str, Any]:
    params = args.model_dump()
    escalation_id = ctx.env.insert(
        "escalations",
        {
            "session_id": ctx.session.session_id,
            "customer_id": ctx.session.verified_customer_id,
            "summary": args.summary,
            "category": args.category,
            "created_at": ctx.env.now.isoformat(),
            "idempotency_key": key,
        },
    )
    _log(ctx, "escalate_to_human", params, "allow", ["ESC-1"])
    return ok({"escalation_id": escalation_id, "message": "A human support lead will follow up."})
