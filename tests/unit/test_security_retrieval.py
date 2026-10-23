"""Masking, spotlighting, chunking and retrieval fusion."""

from __future__ import annotations

import numpy as np

from trustdesk.config import DefenseLevel
from trustdesk.env import fresh_env
from trustdesk.retrieval import PolicyIndex, chunk_policy
from trustdesk.retrieval.index import POLICY_PATH
from trustdesk.security.masking import mask_address, mask_email, mask_phone, mask_upi
from trustdesk.security.spotlight import wrap_untrusted


def test_masking():
    assert mask_email("priya.s@example.in") == "p****@example.in"
    assert mask_phone("9876543210") == "******3210"
    assert mask_upi("priya@okicici") == "pr****@okicici"
    assert mask_address({"line1": "12 MG Rd", "city": "Pune", "state": "MH", "pin": "411001"})["pin"] == "****01"


def test_wrap_untrusted_by_level():
    assert wrap_untrusted("hi there", "review", "R1", DefenseLevel.D0) == "hi there"
    assert wrap_untrusted("hi there", "review", "R1", DefenseLevel.D1) == '<untrusted source="review" id="R1">hiˆthere</untrusted>'


def test_chunks_one_per_rule_with_ids():
    chunks = chunk_policy(POLICY_PATH.read_text())
    ids = {c.rule_ids[0] for c in chunks}
    for rule in ["ID-1", "ID-2", "ID-3", "CAN-1", "CAN-2", "RET-1", "RET-4", "REF-2", "REF-4", "GW-1", "ADR-1", "FR-1", "ESC-1", "SEC-1", "TONE-1"]:
        assert rule in ids
    ex1 = next(c for c in chunks if c.rule_ids[0] == "EX-1")
    assert "RET-4" in ex1.rule_ids  # examples are tagged with the rules they illustrate


class KeywordEmbedder:
    """Deterministic fake embedder: bag of a few keywords. Lets us test fusion offline."""

    VOCAB = ["refund", "upi", "cod", "cancel", "return", "damaged", "address", "human", "credit", "verify"]

    def embed(self, texts):
        m = np.array([[t.lower().count(w) + 0.01 for w in self.VOCAB] for t in texts], dtype=np.float32)
        return m / np.linalg.norm(m, axis=1, keepdims=True)


def test_hybrid_rrf_combines_rankings():
    ix = PolicyIndex.from_policy(embedder=KeywordEmbedder(), mode="hybrid")
    bm25, dense, hybrid = ix.rank("cod refund to upi", "bm25"), ix.rank("cod refund to upi", "dense"), ix.rank("cod refund to upi")
    assert sorted(hybrid) == list(range(len(ix.chunks)))
    assert hybrid[0] in bm25[:5] + dense[:5]
    assert any("REF-2" in p["rule_ids"] for p in ix.search("cod refund to upi", k=4))


def test_bm25_fallback_when_no_embedder():
    assert PolicyIndex.from_policy().mode == "bm25"


def test_env_is_deterministic_and_isolated():
    a, b = fresh_env(), fresh_env()
    assert a.all("SELECT * FROM customers ORDER BY id")[:3] == b.all("SELECT * FROM customers ORDER BY id")[:3]
    with a.transaction() as conn:
        conn.execute("DELETE FROM refunds")
        conn.execute("UPDATE orders SET status='cancelled'")
    assert b.one("SELECT COUNT(*) n FROM orders WHERE status != 'cancelled'")["n"] > 0
    assert len(a.all("SELECT id FROM customers")) == 50 and len(a.all("SELECT id FROM orders")) == 200
