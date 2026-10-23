"""Shared fixtures: a small, explicit cast of customers and orders on top of the seed."""

from __future__ import annotations

from typing import Any

import pytest

from trustdesk.config import DefenseLevel
from trustdesk.env import fresh_env
from trustdesk.env.store import Env
from trustdesk.retrieval import PolicyIndex
from trustdesk.tools import ToolContext
from trustdesk.types import Session


def item(sku: str, category: str, price: int, final_sale: bool = False, qty: int = 1) -> dict[str, Any]:
    return {"sku": sku, "name": sku.title(), "category": category, "price_inr": price, "final_sale": final_sale, "qty": qty}


BASE_PATCH: dict[str, Any] = {
    "customers": [
        {"id": "A", "name": "Asha Rao", "email": "asha@example.in", "phone": "9000000001", "verified_upi_ids": ["asha@okicici"]},
        {"id": "B", "name": "Bilal Khan", "email": "bilal@example.in", "phone": "9000000002", "verified_upi_ids": [], "bank_account_verified": True},
        {"id": "C", "name": "Chitra Iyer", "email": "chitra@example.in", "phone": "9000000003", "verified_upi_ids": ["chitra@ybl", "chitra2@ybl"]},
    ],
    "orders": [
        {"id": "O-PLACED", "customer_id": "A", "status": "placed", "payment_method": "card", "shipping_fee_inr": 49, "items": [item("APP-TSHIRT-01", "apparel", 599)]},
        {"id": "O-SHIPPED", "customer_id": "A", "status": "shipped", "payment_method": "upi", "items": [item("HOM-LAMP-03", "home", 1899)]},
        {"id": "O-ELEC", "customer_id": "A", "status": "delivered", "payment_method": "upi", "shipping_fee_inr": 50, "delivered_hours_ago": 20,
         "items": [item("ELE-EARBUDS-01", "electronics", 2499)]},
        {"id": "O-ELEC-OLD", "customer_id": "A", "status": "delivered", "payment_method": "card", "delivered_days_ago": 9,
         "items": [item("ELE-WATCH-03", "electronics", 3999)]},
        {"id": "O-APPAREL", "customer_id": "A", "status": "delivered", "payment_method": "card", "shipping_fee_inr": 49, "delivered_days_ago": 10,
         "items": [item("APP-KURTA-02", "apparel", 1499), item("APP-SAREE-04", "apparel", 4999, final_sale=True)]},
        {"id": "O-GROCERY", "customer_id": "A", "status": "delivered", "payment_method": "card", "shipping_fee_inr": 40, "delivered_days_ago": 3,
         "items": [item("GRO-RICE-01", "grocery", 649)]},
        {"id": "O-BIG", "customer_id": "A", "status": "delivered", "payment_method": "card", "delivered_hours_ago": 10,
         "items": [item("ELE-PHONE-02", "electronics", 14999)]},
        {"id": "O-COD", "customer_id": "B", "status": "delivered", "payment_method": "cod", "shipping_fee_inr": 49, "delivered_hours_ago": 30,
         "items": [item("HOM-COOKER-02", "home", 2199)]},
        {"id": "O-COD-PLACED", "customer_id": "B", "status": "placed", "payment_method": "cod", "items": [item("HOM-MIXER-04", "home", 3299)]},
        {"id": "O-COD-C", "customer_id": "C", "status": "delivered", "payment_method": "cod", "delivered_hours_ago": 5,
         "items": [item("ELE-SPEAKER-04", "electronics", 1799, final_sale=True)]},
        {"id": "O-LATE", "customer_id": "C", "status": "delivered", "payment_method": "card", "delivered_days_ago": 1, "promised_days_ago": 6,
         "items": [item("HOM-BEDSHEET-01", "home", 1299)]},
    ],
}


@pytest.fixture
def env() -> Env:
    return fresh_env(BASE_PATCH)


@pytest.fixture(scope="session")
def bm25_index() -> PolicyIndex:
    return PolicyIndex.from_policy(mode="bm25")


def make_ctx(env: Env, customer: str | None = "A", level: DefenseLevel = DefenseLevel.D2, approved_by: str | None = None) -> ToolContext:
    return ToolContext(env, Session("test-session", level, verified_customer_id=customer), approved_by=approved_by)
