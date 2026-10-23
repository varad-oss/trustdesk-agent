"""Deterministic seed data for ShopSetu (Faker en_IN, fixed seed).

The seed is background noise: realistic customers and orders the agent might stumble
on. Eval tasks never rely on seeded rows; they add their own rows through fixture
patches (`fixtures.py`) so each task's starting state is explicit in its YAML.
"""

from __future__ import annotations

import random
from datetime import timedelta
from typing import Any

from faker import Faker

from trustdesk.env.store import Env

SEED = 42

# sku, name, category, price_inr, final_sale
CATALOG: list[tuple[str, str, str, int, bool]] = [
    ("APP-TSHIRT-01", "Cotton crew-neck T-shirt", "apparel", 599, False),
    ("APP-KURTA-02", "Handloom cotton kurta", "apparel", 1499, False),
    ("APP-JEANS-03", "Slim-fit denim jeans", "apparel", 1999, False),
    ("APP-SAREE-04", "Banarasi silk saree (clearance)", "apparel", 4999, True),
    ("APP-JACKET-05", "Quilted winter jacket", "apparel", 3499, False),
    ("ELE-EARBUDS-01", "Wireless earbuds", "electronics", 2499, False),
    ("ELE-PHONE-02", "Android smartphone 128GB", "electronics", 14999, False),
    ("ELE-WATCH-03", "Fitness smartwatch", "electronics", 3999, False),
    ("ELE-SPEAKER-04", "Bluetooth speaker (open box)", "electronics", 1799, True),
    ("ELE-LAPTOP-05", "14-inch thin laptop", "electronics", 52999, False),
    ("HOM-BEDSHEET-01", "Double bedsheet set", "home", 1299, False),
    ("HOM-COOKER-02", "5L pressure cooker", "home", 2199, False),
    ("HOM-LAMP-03", "Brass table lamp", "home", 1899, False),
    ("HOM-MIXER-04", "750W mixer grinder", "home", 3299, False),
    ("GRO-RICE-01", "Basmati rice 5kg", "grocery", 649, False),
    ("GRO-GHEE-02", "Desi ghee 1L", "grocery", 799, False),
    ("GRO-TEA-03", "Assam tea 500g", "grocery", 299, False),
    ("PER-SHAMPOO-01", "Herbal shampoo 400ml", "personal_care", 349, False),
    ("PER-TRIMMER-02", "Beard trimmer", "personal_care", 1599, False),
    ("PER-CREAM-03", "Sunscreen SPF 50", "personal_care", 499, False),
]

UPI_HANDLES = ["okicici", "oksbi", "okhdfcbank", "ybl", "paytm"]
STATUSES = ["placed", "shipped", "delivered", "delivered", "delivered", "cancelled", "returned"]
PAYMENTS = ["card", "upi", "netbanking", "cod"]


def _address(fake: Faker) -> dict[str, str]:
    return {
        "line1": fake.street_address(),
        "city": fake.city(),
        "state": fake.state(),
        "pin": f"{fake.random_int(110001, 855999)}",
        "country": "India",
    }


def seed(env: Env, n_customers: int = 50, n_orders: int = 200) -> None:
    """Populate `env` with deterministic customers, orders, items and reviews."""
    fake = Faker("en_IN")
    Faker.seed(SEED)
    rng = random.Random(SEED)

    customers: list[dict[str, Any]] = []
    for i in range(1, n_customers + 1):
        name = fake.name()
        handle = name.split()[0].lower()
        customer = {
            "id": f"C{i:03d}",
            "name": name,
            "email": f"{handle}.{i:03d}@example.in",
            "phone": f"9{rng.randint(100000000, 999999999)}",
            "created_at": (env.now - timedelta(days=rng.randint(60, 900))).isoformat(),
            "verified_upi_ids": [f"{handle}{i:03d}@{rng.choice(UPI_HANDLES)}"] if rng.random() < 0.6 else [],
            "bank_account_verified": rng.random() < 0.3,
        }
        env.insert("customers", customer)
        customers.append(customer)

    for j in range(1, n_orders + 1):
        customer = rng.choice(customers)
        status = rng.choice(STATUSES)
        payment = rng.choice(PAYMENTS)
        placed = env.now - timedelta(days=rng.randint(1, 120), hours=rng.randint(0, 23))
        shipped = placed + timedelta(days=rng.randint(1, 3)) if status != "placed" and status != "cancelled" else None
        delivered = (
            shipped + timedelta(days=rng.randint(2, 7)) if status in {"delivered", "returned"} and shipped else None
        )
        if delivered and delivered > env.now:
            delivered = env.now - timedelta(hours=6)
        picks = rng.sample(CATALOG, k=rng.randint(1, 3))
        shipping_fee = 0 if sum(p[3] for p in picks) >= 999 else 49
        order_id = f"SS-{20000 + j}"
        env.insert(
            "orders",
            {
                "id": order_id,
                "customer_id": customer["id"],
                "status": status,
                "placed_at": placed.isoformat(),
                "shipped_at": shipped.isoformat() if shipped else None,
                "delivered_at": delivered.isoformat() if delivered else None,
                "promised_by": (placed + timedelta(days=6)).isoformat(),
                "payment_method": payment,
                "total_inr": sum(p[3] for p in picks) + shipping_fee,
                "shipping_fee_inr": shipping_fee,
                "shipping_address": _address(fake),
                "delivery_note": rng.choice(["", "Leave with security guard", "Call before delivery", ""]),
            },
        )
        for sku, name, category, price, final_sale in picks:
            env.insert(
                "order_items",
                {
                    "order_id": order_id,
                    "sku": sku,
                    "name": name,
                    "category": category,
                    "price_inr": price,
                    "qty": 1,
                    "final_sale": final_sale,
                },
            )

    review_lines = [
        "Good quality for the price.",
        "Delivery was quick, product as described.",
        "Okay product, packaging could be better.",
        "Bahut accha hai, value for money.",
        "Stopped working after a month, disappointed.",
    ]
    for sku, *_ in CATALOG:
        for _k in range(2):
            env.insert(
                "product_reviews",
                {"sku": sku, "author": fake.first_name(), "rating": rng.randint(2, 5), "text": rng.choice(review_lines)},
            )
