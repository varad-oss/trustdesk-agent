"""`Env`: one ShopSetu database plus a frozen clock.

Why a frozen clock: return windows (RET-1, RET-4) and fraud windows (FR-1) depend on
"now". Freezing it per environment makes eval tasks reproducible on any day.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from trustdesk.env.schema import SCHEMA_SQL

DEFAULT_NOW = datetime.fromisoformat("2026-10-20T12:00:00+05:30")
JSON_COLUMNS = {"verified_upi_ids", "shipping_address", "skus", "messages", "params", "rule_ids", "excerpt", "result"}


def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in row.keys():
        value = row[key]
        if key in JSON_COLUMNS and isinstance(value, str):
            value = json.loads(value)
        if key in {"bank_account_verified", "final_sale"} and value is not None:
            value = bool(value)
        out[key] = value
    return out


class Env:
    """A database connection with typed-ish helpers. Not an ORM on purpose."""

    def __init__(self, conn: sqlite3.Connection, now: datetime | None = None) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.lock = threading.RLock()
        self.conn.executescript(SCHEMA_SQL)
        stored = self.one("SELECT value FROM meta WHERE key = 'now'")
        if now is not None:
            self.set_now(now)
        elif stored is None:
            self.set_now(DEFAULT_NOW)
        self.now = datetime.fromisoformat(self.one("SELECT value FROM meta WHERE key = 'now'")["value"])

    # --- construction -------------------------------------------------------------
    @classmethod
    def in_memory(cls, now: datetime | None = None) -> Env:
        return cls(sqlite3.connect(":memory:", check_same_thread=False), now)

    @classmethod
    def open(cls, path: Path, now: datetime | None = None) -> Env:
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(sqlite3.connect(str(path), check_same_thread=False), now)

    def set_now(self, now: datetime) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('now', ?)", (now.isoformat(),))
        self.conn.commit()
        self.now = now

    # --- queries ------------------------------------------------------------------
    def all(self, sql: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        with self.lock:
            return [_decode_row(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | list = ()) -> dict[str, Any] | None:
        with self.lock:
            row = self.conn.execute(sql, params).fetchone()
        return _decode_row(row) if row else None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Atomic write block: either every statement lands or none does."""
        with self.lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def insert(self, table: str, row: dict[str, Any]) -> int:
        encoded = {k: (json.dumps(v) if isinstance(v, (list, dict)) else v) for k, v in row.items()}
        cols = ", ".join(encoded)
        marks = ", ".join("?" for _ in encoded)
        with self.transaction() as conn:
            cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(encoded.values()))
            return int(cur.lastrowid or 0)

    # --- domain helpers -------------------------------------------------------------
    def customer(self, customer_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM customers WHERE id = ?", (customer_id,))

    def order(self, order_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM orders WHERE id = ?", (order_id,))

    def items(self, order_id: str) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM order_items WHERE order_id = ? ORDER BY sku", (order_id,))

    def returns_for(self, order_id: str) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM returns WHERE order_id = ? ORDER BY id", (order_id,))

    def refunds_for(self, order_id: str) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM refunds WHERE order_id = ? ORDER BY id", (order_id,))

    def refunds_for_customer_since(self, customer_id: str, since: datetime) -> list[dict[str, Any]]:
        return self.all(
            "SELECT r.* FROM refunds r JOIN orders o ON o.id = r.order_id "
            "WHERE o.customer_id = ? AND r.created_at >= ? AND r.status != 'rejected'",
            (customer_id, since.isoformat()),
        )

    def goodwill_since(self, customer_id: str, since: datetime) -> int:
        row = self.one(
            "SELECT COALESCE(SUM(amount_inr), 0) AS total FROM goodwill_credits WHERE customer_id = ? AND created_at >= ?",
            (customer_id, since.isoformat()),
        )
        return int(row["total"]) if row else 0

    def audit(
        self,
        session_id: str,
        actor: str,
        action: str,
        params: dict[str, Any],
        decision: str,
        rule_ids: list[str] | None = None,
        trace_id: str | None = None,
    ) -> None:
        self.insert(
            "audit_log",
            {
                "ts": self.now.isoformat(),
                "session_id": session_id,
                "actor": actor,
                "action": action,
                "params": params,
                "decision": decision,
                "rule_ids": rule_ids or [],
                "trace_id": trace_id,
            },
        )

    def ago(self, **kwargs: float) -> datetime:
        return self.now - timedelta(**kwargs)
