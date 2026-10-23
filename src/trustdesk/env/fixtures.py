"""Fixture patches and per-episode environments.

A task's `initial_state_patch` lists rows to add on top of the seed. Times are given
relative to the frozen clock (`delivered_hours_ago: 20`) so a task reads like the
story it tells, and the arithmetic (e.g. "within 48 hours") is visible in the YAML.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any

from trustdesk.env.seed import seed
from trustdesk.env.store import DEFAULT_NOW, Env

_TEMPLATE: sqlite3.Connection | None = None
_TEMPLATE_LOCK = threading.RLock()

DEFAULT_ADDRESS = {"line1": "12 MG Road", "city": "Pune", "state": "Maharashtra", "pin": "411001", "country": "India"}


def _template() -> sqlite3.Connection:
    """Seed once per process, then copy with the SQLite backup API (fast, isolated)."""
    global _TEMPLATE
    with _TEMPLATE_LOCK:
        if _TEMPLATE is None:
            env = Env.in_memory(DEFAULT_NOW)
            seed(env)
            _TEMPLATE = env.conn
        return _TEMPLATE


def fresh_env(patch: dict[str, Any] | None = None, now: datetime | None = None) -> Env:
    """Build an isolated in-memory store: seed + optional fixture patch."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    with _TEMPLATE_LOCK:
        _template().backup(conn)
    env = Env(conn, now)
    if patch:
        apply_patch(env, patch)
    return env


def _rel(env: Env, spec: dict[str, Any], prefix: str) -> str | None:
    """Resolve `<prefix>_days_ago` / `<prefix>_hours_ago` into an ISO timestamp."""
    days = spec.get(f"{prefix}_days_ago")
    hours = spec.get(f"{prefix}_hours_ago")
    if days is None and hours is None:
        return spec.get(f"{prefix}_at")
    return (env.now - timedelta(days=days or 0, hours=hours or 0)).isoformat()


def apply_patch(env: Env, patch: dict[str, Any]) -> None:
    for c in patch.get("customers", []):
        env.insert(
            "customers",
            {
                "id": c["id"],
                "name": c["name"],
                "email": c["email"],
                "phone": c["phone"],
                "created_at": (env.now - timedelta(days=c.get("created_days_ago", 365))).isoformat(),
                "verified_upi_ids": c.get("verified_upi_ids", []),
                "bank_account_verified": c.get("bank_account_verified", False),
            },
        )
    for o in patch.get("orders", []):
        items = o.get("items", [])
        shipping = o.get("shipping_fee_inr", 0)
        placed = _rel(env, o, "placed") or (env.now - timedelta(days=10)).isoformat()
        env.insert(
            "orders",
            {
                "id": o["id"],
                "customer_id": o["customer_id"],
                "status": o["status"],
                "placed_at": placed,
                "shipped_at": _rel(env, o, "shipped"),
                "delivered_at": _rel(env, o, "delivered"),
                "promised_by": _rel(env, o, "promised"),
                "payment_method": o["payment_method"],
                "total_inr": o.get("total_inr", sum(i["price_inr"] * i.get("qty", 1) for i in items) + shipping),
                "shipping_fee_inr": shipping,
                "shipping_address": o.get("shipping_address", DEFAULT_ADDRESS),
                "delivery_note": o.get("delivery_note", ""),
            },
        )
        for i in items:
            env.insert(
                "order_items",
                {
                    "order_id": o["id"],
                    "sku": i["sku"],
                    "name": i["name"],
                    "category": i["category"],
                    "price_inr": i["price_inr"],
                    "qty": i.get("qty", 1),
                    "final_sale": i.get("final_sale", False),
                },
            )
    for r in patch.get("reviews", []):
        env.insert("product_reviews", {"sku": r["sku"], "author": r["author"], "rating": r.get("rating", 5), "text": r["text"]})
    for t in patch.get("tickets", []):
        env.insert(
            "tickets",
            {"customer_id": t["customer_id"], "created_at": _rel(env, t, "created") or env.now.isoformat(), "messages": t["messages"]},
        )
    for r in patch.get("returns", []):
        env.insert(
            "returns",
            {
                "order_id": r["order_id"],
                "skus": r["skus"],
                "reason": r["reason"],
                "status": r.get("status", "approved"),
                "basis": r.get("basis", "RET-1"),
                "created_at": _rel(env, r, "created") or env.now.isoformat(),
            },
        )
    for r in patch.get("refunds", []):
        env.insert(
            "refunds",
            {
                "order_id": r["order_id"],
                "amount_inr": r["amount_inr"],
                "destination": r.get("destination", "original_method"),
                "upi_id": r.get("upi_id"),
                "status": r.get("status", "issued"),
                "created_at": _rel(env, r, "created") or env.now.isoformat(),
            },
        )
    for g in patch.get("goodwill_credits", []):
        env.insert(
            "goodwill_credits",
            {
                "customer_id": g["customer_id"],
                "amount_inr": g["amount_inr"],
                "reason": g.get("reason", "prior credit"),
                "created_at": _rel(env, g, "created") or env.now.isoformat(),
            },
        )
