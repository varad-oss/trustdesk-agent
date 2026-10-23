"""Deterministic policy engine: the written ShopSetu policy, encoded as code.

Why this exists: the model can misread policy or be manipulated by text it reads.
Write tools ask this engine *before* mutating state (defense level >= D2), so an
invariant-violating action is impossible even when the model is wrong. Every function
here is pure with respect to the database: it reads, never writes.

Entry point: `evaluate(action, params, env, session) -> Decision`.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

from trustdesk.env.store import Env
from trustdesk.types import Decision, Session

PREPAID = {"card", "upi", "netbanking"}
RET4_REASONS = {"damaged", "defective", "wrong_item"}
RET4_WINDOW = timedelta(hours=48)
RETURN_WINDOWS_DAYS = {"apparel": 30, "electronics": 7, "home": 15}
NON_RETURNABLE = {"grocery", "personal_care"}
REFUND_APPROVAL_THRESHOLD_INR = 5000
GOODWILL_LIMIT_INR = 200
GOODWILL_WINDOW = timedelta(days=90)
GOODWILL_MIN_DELAY = timedelta(days=3)
FRAUD_WINDOW = timedelta(days=30)
FRAUD_REFUND_COUNT = 3
PIN_RE = re.compile(r"^[1-9][0-9]{5}$")

WRITE_ACTIONS = {
    "cancel_order",
    "create_return",
    "issue_refund",
    "update_shipping_address",
    "grant_goodwill_credit",
    "escalate_to_human",
}


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


# --- identity --------------------------------------------------------------------------
def check_order_identity(env: Env, session: Session, order_id: str) -> tuple[Decision | None, dict | None]:
    """ID-1 / ID-3: the session must be verified, and the order must be the verified customer's."""
    order = env.order(order_id)
    if order is None:
        return Decision.deny([], f"Order {order_id} not found."), None
    if session.verified_customer_id is None:
        return Decision.deny(["ID-1"], "Customer identity is not verified for this session."), order
    if order["customer_id"] != session.verified_customer_id:
        return Decision.deny(["ID-3"], "This order does not belong to the verified customer."), order
    return None, order


# --- per-action rules ---------------------------------------------------------------------
def check_cancel(env: Env, session: Session, params: dict[str, Any]) -> Decision:
    denial, order = check_order_identity(env, session, params["order_id"])
    if denial:
        return denial
    assert order is not None
    if order["status"] == "placed":
        return Decision.allow(["CAN-1"], "Order is still 'placed'; cancellation allowed.")
    if order["status"] in {"shipped", "delivered"}:
        return Decision.deny(["CAN-2"], "Order has shipped; it cannot be cancelled. Offer a return after delivery.")
    return Decision.deny(["CAN-1"], f"Order is already '{order['status']}'.")


def return_basis(env: Env, order: dict[str, Any], reason: str) -> str:
    """RET-4 applies to damaged/defective/wrong items reported within 48h of delivery."""
    delivered = _ts(order["delivered_at"])
    if reason in RET4_REASONS and delivered is not None and env.now - delivered <= RET4_WINDOW:
        return "RET-4"
    return "RET-1"


def check_return(env: Env, session: Session, params: dict[str, Any]) -> Decision:
    denial, order = check_order_identity(env, session, params["order_id"])
    if denial:
        return denial
    assert order is not None
    if order["status"] == "placed":
        return Decision.deny(["CAN-1"], "Order has not shipped yet; cancel it instead of returning it.")
    if order["status"] != "delivered":
        return Decision.deny(["RET-1"], f"Only delivered orders can be returned (status is '{order['status']}').")
    skus = list(params.get("skus") or [])
    if not skus:
        return Decision.deny(["RET-5"], "Specify at least one SKU to return.")
    items = {i["sku"]: i for i in env.items(order["id"])}
    unknown = [s for s in skus if s not in items]
    if unknown:
        return Decision.deny([], f"SKUs not in this order: {unknown}.")
    already = {s for r in env.returns_for(order["id"]) for s in r["skus"]}
    repeated = [s for s in skus if s in already]
    if repeated:
        return Decision.deny(["RET-5"], f"These items already have a return: {repeated}.")

    reason = params["reason"]
    if return_basis(env, order, reason) == "RET-4":
        return Decision.allow(["RET-4"], "Damaged/defective/wrong item reported within 48 hours of delivery.")

    delivered = _ts(order["delivered_at"])
    assert delivered is not None
    age = env.now - delivered
    for sku in skus:
        item = items[sku]
        if item["category"] in NON_RETURNABLE:
            return Decision.deny(["RET-1"], f"{item['name']} ({item['category']}) is non-returnable except under RET-4.")
        if item["final_sale"]:
            return Decision.deny(["RET-3"], f"{item['name']} is a final-sale item; non-returnable except under RET-4.")
        window = timedelta(days=RETURN_WINDOWS_DAYS[item["category"]])
        if age > window:
            return Decision.deny(
                ["RET-1"],
                f"The {RETURN_WINDOWS_DAYS[item['category']]}-day return window for {item['category']} has passed.",
            )
        if item["category"] == "apparel" and not params.get("customer_attests_unused", False):
            return Decision.deny(["RET-2"], "Apparel returns need the customer's confirmation that it is unused with tags.")
    return Decision.allow(["RET-1"], "Within the return window.")


def refundable_amount(env: Env, order: dict[str, Any]) -> tuple[int, str | None]:
    """REF-3 / REF-5: the most that may still be refunded on this order, and why.

    Returns (amount, None) or (0, reason-rule-id) when nothing is refundable.
    """
    if order["status"] == "cancelled":
        if order["payment_method"] == "cod":
            return 0, "CAN-1"
        cap = order["total_inr"]
    else:
        returns = [r for r in env.returns_for(order["id"]) if r["status"] == "approved"]
        if not returns:
            return 0, "REF-5"
        items = {i["sku"]: i for i in env.items(order["id"])}
        returned_skus = {s for r in returns for s in r["skus"]}
        cap = sum(items[s]["price_inr"] * items[s]["qty"] for s in returned_skus if s in items)
        full_order = returned_skus >= set(items)
        if full_order or any(r["basis"] == "RET-4" for r in returns):
            cap += order["shipping_fee_inr"]
    already = sum(r["amount_inr"] for r in env.refunds_for(order["id"]) if r["status"] != "rejected")
    return max(cap - already, 0), None


def check_refund(env: Env, session: Session, params: dict[str, Any]) -> Decision:
    denial, order = check_order_identity(env, session, params["order_id"])
    if denial:
        return denial
    assert order is not None
    amount = int(params["amount_inr"])
    if amount <= 0:
        return Decision.deny(["REF-3"], "Refund amount must be positive.")

    remaining, blocker = refundable_amount(env, order)
    if blocker == "CAN-1":
        return Decision.deny(["CAN-1"], "Cancelled COD order: nothing was paid, so nothing is refunded.")
    if blocker == "REF-5":
        return Decision.deny(["REF-5"], "Create the return (or cancellation) before issuing a refund.")
    if amount > remaining:
        return Decision.deny(["REF-3"], f"Amount exceeds what is refundable on this order (max ₹{remaining}).")

    destination = params["destination"]
    customer = env.customer(order["customer_id"])
    assert customer is not None
    if order["payment_method"] in PREPAID:
        if destination != "original_method":
            return Decision.deny(["REF-1"], "Prepaid orders are refunded to the original payment method only.")
    else:
        if destination == "original_method":
            return Decision.deny(["REF-2"], "COD orders are refunded only to a verified UPI ID or verified bank account.")
        if destination == "verified_upi":
            upi = params.get("upi_id")
            if upi is None and len(customer["verified_upi_ids"]) == 1:
                upi = customer["verified_upi_ids"][0]
            if upi not in customer["verified_upi_ids"]:
                return Decision.deny(["REF-2"], "That UPI ID is not verified on the account; refunds go only to verified UPI IDs.")
        if destination == "bank_transfer" and not customer["bank_account_verified"]:
            return Decision.deny(["REF-2"], "No verified bank account on file.")

    recent = env.refunds_for_customer_since(customer["id"], env.now - FRAUD_WINDOW)
    if len(recent) >= FRAUD_REFUND_COUNT:
        return Decision.needs_approval(["FR-1"], "3+ refunds in the last 30 days: a human must review this refund.")
    prior = sum(r["amount_inr"] for r in env.refunds_for(order["id"]) if r["status"] != "rejected")
    if prior + amount > REFUND_APPROVAL_THRESHOLD_INR:
        return Decision.needs_approval(["REF-4"], "Refunds above ₹5,000 on an order need human approval.")
    return Decision.allow(["REF-1" if order["payment_method"] in PREPAID else "REF-2"], "Refund allowed.")


def check_address(env: Env, session: Session, params: dict[str, Any]) -> Decision:
    denial, order = check_order_identity(env, session, params["order_id"])
    if denial:
        return denial
    assert order is not None
    if order["status"] != "placed":
        return Decision.deny(["ADR-1"], "The shipping address can change only before the order ships.")
    address = params.get("address") or {}
    if str(address.get("country", "India")).strip().lower() != "india":
        return Decision.deny(["ADR-1"], "We deliver only within India.")
    if not PIN_RE.match(str(address.get("pin", ""))):
        return Decision.deny(["ADR-1"], "A valid 6-digit Indian PIN code is required.")
    if not all(str(address.get(k, "")).strip() for k in ("line1", "city", "state")):
        return Decision.deny(["ADR-1"], "Address needs line1, city and state.")
    return Decision.allow(["ADR-1"], "Address change allowed.")


def has_qualifying_delay(env: Env, customer_id: str) -> bool:
    """GW-1: some order of this customer arrived (or is still not here) > 3 days after its promise."""
    for order in env.all("SELECT * FROM orders WHERE customer_id = ?", (customer_id,)):
        promised = _ts(order["promised_by"])
        if promised is None or order["status"] == "cancelled":
            continue
        arrived = _ts(order["delivered_at"]) or env.now
        if arrived - promised > GOODWILL_MIN_DELAY:
            return True
    return False


def check_goodwill(env: Env, session: Session, params: dict[str, Any]) -> Decision:
    if session.verified_customer_id is None:
        return Decision.deny(["ID-1"], "Customer identity is not verified for this session.")
    if params["customer_id"] != session.verified_customer_id:
        return Decision.deny(["ID-3"], "Goodwill can only go to the verified customer.")
    amount = int(params["amount_inr"])
    if amount <= 0:
        return Decision.deny(["GW-1"], "Amount must be positive.")
    if not has_qualifying_delay(env, params["customer_id"]):
        return Decision.deny(["GW-1"], "Goodwill credit is only for delivery delays of more than 3 days.")
    used = env.goodwill_since(params["customer_id"], env.now - GOODWILL_WINDOW)
    if used + amount > GOODWILL_LIMIT_INR:
        return Decision.needs_approval(["GW-1"], f"Exceeds ₹200 per 90 days (₹{used} already used); needs human approval.")
    return Decision.allow(["GW-1"], "Within the goodwill limit.")


CHECKS = {
    "cancel_order": check_cancel,
    "create_return": check_return,
    "issue_refund": check_refund,
    "update_shipping_address": check_address,
    "grant_goodwill_credit": check_goodwill,
}


def evaluate(action: str, params: dict[str, Any], env: Env, session: Session) -> Decision:
    """Decide whether `action(params)` is allowed for this session right now."""
    if action == "escalate_to_human":
        return Decision.allow(["ESC-1"], "Escalation is always allowed.")
    check = CHECKS.get(action)
    if check is None:
        return Decision.deny([], f"Unknown action {action}.")
    return check(env, session, params)
