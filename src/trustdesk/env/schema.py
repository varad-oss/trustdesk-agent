"""SQLite schema for the ShopSetu store.

Plain `sqlite3` (ADR-004): the schema is small, tests need fresh in-memory copies per
episode, and raw SQL keeps every query visible when explaining the policy engine.
JSON columns are stored as TEXT and decoded in `Env` helpers.
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    phone TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    verified_upi_ids TEXT NOT NULL DEFAULT '[]',
    bank_account_verified INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    status TEXT NOT NULL CHECK (status IN ('placed','shipped','delivered','cancelled','returned')),
    placed_at TEXT NOT NULL,
    shipped_at TEXT,
    delivered_at TEXT,
    promised_by TEXT,
    payment_method TEXT NOT NULL CHECK (payment_method IN ('card','upi','netbanking','cod')),
    total_inr INTEGER NOT NULL,
    shipping_fee_inr INTEGER NOT NULL DEFAULT 0,
    shipping_address TEXT NOT NULL,
    delivery_note TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS order_items (
    order_id TEXT NOT NULL REFERENCES orders(id),
    sku TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('apparel','electronics','home','grocery','personal_care')),
    price_inr INTEGER NOT NULL,
    qty INTEGER NOT NULL DEFAULT 1,
    final_sale INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (order_id, sku)
);

CREATE TABLE IF NOT EXISTS returns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES orders(id),
    skus TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('damaged','defective','wrong_item','changed_mind','size_issue','other')),
    status TEXT NOT NULL,
    basis TEXT NOT NULL DEFAULT 'RET-1',
    created_at TEXT NOT NULL,
    idempotency_key TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS refunds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES orders(id),
    amount_inr INTEGER NOT NULL,
    destination TEXT NOT NULL CHECK (destination IN ('original_method','verified_upi','bank_transfer')),
    upi_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('issued','pending_approval','rejected')),
    approved_by TEXT,
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS goodwill_credits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    amount_inr INTEGER NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    idempotency_key TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS product_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL,
    author TEXT NOT NULL,
    rating INTEGER NOT NULL,
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL REFERENCES customers(id),
    created_at TEXT NOT NULL,
    messages TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS escalations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    customer_id TEXT,
    summary TEXT NOT NULL,
    category TEXT NOT NULL,
    created_at TEXT NOT NULL,
    idempotency_key TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    tool_use_id TEXT NOT NULL UNIQUE,
    action TEXT NOT NULL,
    params TEXT NOT NULL,
    decision TEXT NOT NULL,
    rule_ids TEXT NOT NULL,
    justification TEXT NOT NULL DEFAULT '',
    excerpt TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected')),
    reviewer_note TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS idempotency (
    key TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    session_id TEXT NOT NULL,
    actor TEXT NOT NULL CHECK (actor IN ('agent','human','system')),
    action TEXT NOT NULL,
    params TEXT NOT NULL,
    decision TEXT NOT NULL,
    rule_ids TEXT NOT NULL DEFAULT '[]',
    trace_id TEXT
);
"""
