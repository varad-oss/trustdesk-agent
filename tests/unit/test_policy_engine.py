"""Policy engine: one test per rule branch, plus Hypothesis invariants."""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.conftest import BASE_PATCH
from trustdesk.config import DefenseLevel
from trustdesk.env import fresh_env
from trustdesk.policy_engine import evaluate, refundable_amount
from trustdesk.tools import execute_tool
from trustdesk.types import Outcome, Session


def s(customer: str | None = "A") -> Session:
    return Session("t", DefenseLevel.D2, verified_customer_id=customer)


def outcome(env, action, params, customer="A"):
    return evaluate(action, params, env, s(customer))


# --- identity -----------------------------------------------------------------------------------
def test_unverified_session_is_denied_id1(env):
    d = outcome(env, "cancel_order", {"order_id": "O-PLACED", "reason": "x"}, customer=None)
    assert d.outcome == Outcome.DENY and d.rule_ids == ["ID-1"]


def test_other_customers_order_is_denied_id3(env):
    d = outcome(env, "cancel_order", {"order_id": "O-COD-PLACED", "reason": "x"}, customer="A")
    assert d.outcome == Outcome.DENY and d.rule_ids == ["ID-3"]


def test_unknown_order_is_denied(env):
    assert outcome(env, "cancel_order", {"order_id": "NOPE", "reason": "x"}).outcome == Outcome.DENY


# --- cancellation -----------------------------------------------------------------------------------
def test_cancel_placed_allowed(env):
    assert outcome(env, "cancel_order", {"order_id": "O-PLACED", "reason": "x"}).outcome == Outcome.ALLOW


def test_cancel_shipped_denied_can2(env):
    d = outcome(env, "cancel_order", {"order_id": "O-SHIPPED", "reason": "x"})
    assert d.outcome == Outcome.DENY and d.rule_ids == ["CAN-2"]


# --- returns -----------------------------------------------------------------------------------------
def test_damaged_within_48h_is_ret4(env):
    d = outcome(env, "create_return", {"order_id": "O-ELEC", "skus": ["ELE-EARBUDS-01"], "reason": "damaged"})
    assert d.outcome == Outcome.ALLOW and d.rule_ids == ["RET-4"]


def test_electronics_changed_mind_after_7_days_denied_ret1(env):
    d = outcome(env, "create_return", {"order_id": "O-ELEC-OLD", "skus": ["ELE-WATCH-03"], "reason": "changed_mind"})
    assert d.outcome == Outcome.DENY and d.rule_ids == ["RET-1"]


def test_damaged_after_48h_falls_back_to_ret1_window(env):
    d = outcome(env, "create_return", {"order_id": "O-ELEC-OLD", "skus": ["ELE-WATCH-03"], "reason": "damaged"})
    assert d.outcome == Outcome.DENY and d.rule_ids == ["RET-1"]


def test_apparel_needs_attestation_ret2(env):
    d = outcome(env, "create_return", {"order_id": "O-APPAREL", "skus": ["APP-KURTA-02"], "reason": "size_issue"})
    assert d.outcome == Outcome.DENY and d.rule_ids == ["RET-2"]
    d = outcome(env, "create_return", {"order_id": "O-APPAREL", "skus": ["APP-KURTA-02"], "reason": "size_issue", "customer_attests_unused": True})
    assert d.outcome == Outcome.ALLOW


def test_final_sale_denied_ret3(env):
    d = outcome(env, "create_return", {"order_id": "O-APPAREL", "skus": ["APP-SAREE-04"], "reason": "changed_mind", "customer_attests_unused": True})
    assert d.outcome == Outcome.DENY and d.rule_ids == ["RET-3"]


def test_final_sale_damaged_within_48h_allowed_ret4(env):
    d = outcome(env, "create_return", {"order_id": "O-COD-C", "skus": ["ELE-SPEAKER-04"], "reason": "defective"}, customer="C")
    assert d.outcome == Outcome.ALLOW and d.rule_ids == ["RET-4"]


def test_grocery_non_returnable_ret1(env):
    d = outcome(env, "create_return", {"order_id": "O-GROCERY", "skus": ["GRO-RICE-01"], "reason": "changed_mind"})
    assert d.outcome == Outcome.DENY and d.rule_ids == ["RET-1"]


def test_return_of_placed_order_points_to_cancel(env):
    d = outcome(env, "create_return", {"order_id": "O-PLACED", "skus": ["APP-TSHIRT-01"], "reason": "changed_mind"})
    assert d.outcome == Outcome.DENY and d.rule_ids == ["CAN-1"]


def test_second_return_same_item_denied_ret5(env):
    ctx_session = s()
    from trustdesk.tools import ToolContext

    ctx = ToolContext(env, ctx_session)
    assert execute_tool(ctx, "create_return", {"order_id": "O-ELEC", "skus": ["ELE-EARBUDS-01"], "reason": "damaged"}, "k1")["ok"]
    d = outcome(env, "create_return", {"order_id": "O-ELEC", "skus": ["ELE-EARBUDS-01"], "reason": "damaged"})
    assert d.outcome == Outcome.DENY and d.rule_ids == ["RET-5"]


def test_unknown_sku_denied(env):
    assert outcome(env, "create_return", {"order_id": "O-ELEC", "skus": ["X"], "reason": "damaged"}).outcome == Outcome.DENY


# --- refunds -----------------------------------------------------------------------------------------
def _return(env, order_id, skus, reason, customer="A"):
    from trustdesk.tools import ToolContext

    r = execute_tool(ToolContext(env, s(customer)), "create_return", {"order_id": order_id, "skus": skus, "reason": reason}, f"ret:{order_id}")
    assert r["ok"], r


def test_refund_before_return_denied_ref5(env):
    d = outcome(env, "issue_refund", {"order_id": "O-ELEC", "amount_inr": 2549, "destination": "original_method"})
    assert d.outcome == Outcome.DENY and d.rule_ids == ["REF-5"]


def test_ret4_refund_includes_shipping(env):
    _return(env, "O-ELEC", ["ELE-EARBUDS-01"], "damaged")
    assert refundable_amount(env, env.order("O-ELEC")) == (2549, None)
    assert outcome(env, "issue_refund", {"order_id": "O-ELEC", "amount_inr": 2549, "destination": "original_method"}).outcome == Outcome.ALLOW


def test_refund_over_paid_denied_ref3(env):
    _return(env, "O-ELEC", ["ELE-EARBUDS-01"], "damaged")
    d = outcome(env, "issue_refund", {"order_id": "O-ELEC", "amount_inr": 2550, "destination": "original_method"})
    assert d.outcome == Outcome.DENY and d.rule_ids == ["REF-3"]


def test_prepaid_refund_to_upi_denied_ref1(env):
    _return(env, "O-ELEC", ["ELE-EARBUDS-01"], "damaged")
    d = outcome(env, "issue_refund", {"order_id": "O-ELEC", "amount_inr": 100, "destination": "verified_upi", "upi_id": "asha@okicici"})
    assert d.outcome == Outcome.DENY and d.rule_ids == ["REF-1"]


def test_cod_refund_to_unverified_upi_denied_ref2(env):
    _return(env, "O-COD", ["HOM-COOKER-02"], "damaged", customer="B")
    d = outcome(env, "issue_refund", {"order_id": "O-COD", "amount_inr": 100, "destination": "verified_upi", "upi_id": "attacker@okbank"}, customer="B")
    assert d.outcome == Outcome.DENY and d.rule_ids == ["REF-2"]


def test_cod_refund_to_original_method_denied_ref2(env):
    _return(env, "O-COD", ["HOM-COOKER-02"], "damaged", customer="B")
    d = outcome(env, "issue_refund", {"order_id": "O-COD", "amount_inr": 100, "destination": "original_method"}, customer="B")
    assert d.outcome == Outcome.DENY and d.rule_ids == ["REF-2"]


def test_cod_refund_to_verified_bank_allowed(env):
    _return(env, "O-COD", ["HOM-COOKER-02"], "damaged", customer="B")
    d = outcome(env, "issue_refund", {"order_id": "O-COD", "amount_inr": 2248, "destination": "bank_transfer"}, customer="B")
    assert d.outcome == Outcome.ALLOW


def test_cod_ambiguous_upi_requires_explicit_choice(env):
    _return(env, "O-COD-C", ["ELE-SPEAKER-04"], "defective", customer="C")
    d = outcome(env, "issue_refund", {"order_id": "O-COD-C", "amount_inr": 1799, "destination": "verified_upi"}, customer="C")
    assert d.outcome == Outcome.DENY  # two verified UPIs: the engine never guesses
    d = outcome(env, "issue_refund", {"order_id": "O-COD-C", "amount_inr": 1799, "destination": "verified_upi", "upi_id": "chitra2@ybl"}, customer="C")
    assert d.outcome == Outcome.ALLOW


def test_large_refund_needs_approval_ref4(env):
    _return(env, "O-BIG", ["ELE-PHONE-02"], "defective")
    d = outcome(env, "issue_refund", {"order_id": "O-BIG", "amount_inr": 14999, "destination": "original_method"})
    assert d.outcome == Outcome.NEEDS_APPROVAL and d.rule_ids == ["REF-4"]


def test_frequent_refunds_need_approval_fr1():
    patch = {**BASE_PATCH, "refunds": [{"order_id": "O-ELEC-OLD", "amount_inr": 10, "created_days_ago": d} for d in (2, 5, 9)]}
    env = fresh_env(patch)
    _return(env, "O-ELEC", ["ELE-EARBUDS-01"], "damaged")
    d = outcome(env, "issue_refund", {"order_id": "O-ELEC", "amount_inr": 100, "destination": "original_method"})
    assert d.outcome == Outcome.NEEDS_APPROVAL and d.rule_ids == ["FR-1"]


def test_cancelled_cod_has_nothing_to_refund(env):
    with env.transaction() as conn:
        conn.execute("UPDATE orders SET status='cancelled' WHERE id='O-COD-PLACED'")
    d = outcome(env, "issue_refund", {"order_id": "O-COD-PLACED", "amount_inr": 100, "destination": "bank_transfer"}, customer="B")
    assert d.outcome == Outcome.DENY and d.rule_ids == ["CAN-1"]


def test_cancelled_prepaid_refunds_full_total(env):
    with env.transaction() as conn:
        conn.execute("UPDATE orders SET status='cancelled' WHERE id='O-PLACED'")
    assert refundable_amount(env, env.order("O-PLACED")) == (648, None)


def test_ret1_partial_return_excludes_shipping(env):
    from trustdesk.tools import ToolContext

    r = execute_tool(ToolContext(env, s()), "create_return",
                     {"order_id": "O-APPAREL", "skus": ["APP-KURTA-02"], "reason": "size_issue", "customer_attests_unused": True}, "k")
    assert r["ok"]
    assert refundable_amount(env, env.order("O-APPAREL")) == (1499, None)


# --- address / goodwill / escalation ------------------------------------------------------------------------
def test_address_valid_change_allowed(env):
    addr = {"line1": "1 Main", "city": "Pune", "state": "MH", "pin": "411001", "country": "India"}
    assert outcome(env, "update_shipping_address", {"order_id": "O-PLACED", "address": addr}).outcome == Outcome.ALLOW


def test_address_bad_pin_or_abroad_denied(env):
    for addr in ({"line1": "1", "city": "Pune", "state": "MH", "pin": "01234"}, {"line1": "1", "city": "Dubai", "state": "DU", "pin": "411001", "country": "UAE"}):
        d = outcome(env, "update_shipping_address", {"order_id": "O-PLACED", "address": addr})
        assert d.outcome == Outcome.DENY and d.rule_ids == ["ADR-1"]


def test_address_after_shipping_denied(env):
    addr = {"line1": "1", "city": "Pune", "state": "MH", "pin": "411001"}
    assert outcome(env, "update_shipping_address", {"order_id": "O-SHIPPED", "address": addr}).rule_ids == ["ADR-1"]


def test_goodwill_requires_delay(env):
    d = outcome(env, "grant_goodwill_credit", {"customer_id": "A", "amount_inr": 100, "reason": "x"})
    assert d.outcome == Outcome.DENY and d.rule_ids == ["GW-1"]


def test_goodwill_limits(env):
    assert outcome(env, "grant_goodwill_credit", {"customer_id": "C", "amount_inr": 200, "reason": "late"}, "C").outcome == Outcome.ALLOW
    d = outcome(env, "grant_goodwill_credit", {"customer_id": "C", "amount_inr": 500, "reason": "late"}, "C")
    assert d.outcome == Outcome.NEEDS_APPROVAL and d.rule_ids == ["GW-1"]


def test_goodwill_other_customer_denied(env):
    assert outcome(env, "grant_goodwill_credit", {"customer_id": "A", "amount_inr": 100, "reason": "x"}, "C").rule_ids == ["ID-3"]


def test_escalation_always_allowed(env):
    assert outcome(env, "escalate_to_human", {"summary": "x", "category": "other"}, None).outcome == Outcome.ALLOW


# --- Hypothesis invariants ----------------------------------------------------------------------------------
refund_attempt = st.fixed_dictionaries(
    {
        "order_id": st.sampled_from(["O-ELEC", "O-BIG", "O-COD", "O-APPAREL", "O-PLACED"]),
        "amount_inr": st.integers(min_value=1, max_value=20000),
        "destination": st.sampled_from(["original_method", "verified_upi", "bank_transfer"]),
        "upi_id": st.sampled_from([None, "asha@okicici", "attacker@okbank", "bilal.new@okaxis"]),
        "customer": st.sampled_from(["A", "B"]),
        "approved": st.booleans(),
    }
)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(attempts=st.lists(refund_attempt, min_size=1, max_size=12), do_returns=st.booleans())
def test_property_refunds_never_exceed_paid_and_never_to_unverified_upi(attempts, do_returns):
    """At D2+, no sequence of refund attempts (even human-approved) breaks REF-2 or REF-3."""
    from trustdesk.tools import ToolContext

    env = fresh_env(BASE_PATCH)
    if do_returns:
        _return(env, "O-ELEC", ["ELE-EARBUDS-01"], "damaged")
        _return(env, "O-BIG", ["ELE-PHONE-02"], "defective")
        _return(env, "O-COD", ["HOM-COOKER-02"], "damaged", customer="B")
    for i, a in enumerate(attempts):
        ctx = ToolContext(env, s(a["customer"]), approved_by="human:prop" if a["approved"] else None)
        args = {k: a[k] for k in ("order_id", "amount_inr", "destination", "upi_id")} | {"reason": "prop"}
        execute_tool(ctx, "issue_refund", args, f"p{i}")

    for order in env.all("SELECT * FROM orders WHERE id IN ('O-ELEC','O-BIG','O-COD','O-APPAREL','O-PLACED')"):
        refunds = [r for r in env.refunds_for(order["id"]) if r["status"] == "issued"]
        assert sum(r["amount_inr"] for r in refunds) <= order["total_inr"]
        customer = env.customer(order["customer_id"])
        for r in refunds:
            if r["upi_id"] is not None:
                assert r["upi_id"] in customer["verified_upi_ids"]
            if order["payment_method"] != "cod":
                assert r["destination"] == "original_method"


@settings(max_examples=40, deadline=None)
@given(customer=st.sampled_from(["A", "B", "C", None]), order_id=st.sampled_from(["O-PLACED", "O-COD-PLACED", "O-ELEC", "O-COD", "O-COD-C"]),
       action=st.sampled_from(["cancel_order", "create_return", "issue_refund"]))
def test_property_never_acts_on_someone_elses_order(customer, order_id, action):
    env = fresh_env(BASE_PATCH)
    params = {"order_id": order_id, "reason": "damaged", "skus": [i["sku"] for i in env.items(order_id)], "amount_inr": 10, "destination": "bank_transfer"}
    d = evaluate(action, params, env, s(customer))
    if customer != env.order(order_id)["customer_id"]:
        assert d.outcome == Outcome.DENY and d.rule_ids[0] in {"ID-1", "ID-3"}
