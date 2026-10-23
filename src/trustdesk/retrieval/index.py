"""Hybrid policy retrieval: BM25 + dense embeddings, fused with reciprocal rank fusion.

Why hybrid: rule IDs and exact terms ("COD", "final sale") are lexical, while customer
phrasing ("my phone came broken") is semantic. BM25 catches the first, embeddings the
second, and RRF combines ranks without having to calibrate their score scales.
The corpus is ~30 chunks, so everything lives in memory (no vector database).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from rank_bm25 import BM25Okapi

from trustdesk.config import REPO_ROOT

POLICY_PATH = REPO_ROOT / "docs" / "policy" / "ShopSetu_Support_Policy.md"
RULE_ID_RE = re.compile(r"\b(?:ID|CAN|RET|REF|GW|ADR|FR|ESC|SEC|TONE|EX)-\d+\b")
TOKEN_RE = re.compile(r"[a-z0-9₹]+(?:-[0-9]+)?")
STOPWORDS = set(["a", "an", "the", "to", "of", "and", "or", "for", "in", "on", "is", "are", "be", "by", "it", "this", "that", "with", "as", "at", "from", "not", "no", "do", "i", "my", "me", "you", "your", "we", "our"])
RRF_K = 60


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    section: str
    title: str
    rule_ids: list[str]
    text: str


def chunk_policy(markdown: str) -> list[Chunk]:
    """Structure-aware chunking: one `###` rule section per chunk, tagged with its rule IDs."""
    chunks: list[Chunk] = []
    section = ""
    title = ""
    body: list[str] = []

    def flush() -> None:
        if title:
            text = "\n".join(body).strip()
            ids = RULE_ID_RE.findall(title)
            if ids and ids[0].startswith("EX-"):
                # Worked examples are tagged with the rules they illustrate, so citing them is useful.
                ids += [r for r in dict.fromkeys(RULE_ID_RE.findall(text)) if r not in ids]
            chunks.append(Chunk(f"c{len(chunks):02d}", section, title, ids, text))

    for line in markdown.splitlines():
        if line.startswith("## "):
            flush()
            section, title, body = line[3:].strip(), "", []
        elif line.startswith("### "):
            flush()
            title, body = line[4:].strip(), []
        elif title:
            body.append(line)
    flush()
    return chunks


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray: ...


class FastEmbedder:
    """Local ONNX embeddings via fastembed (ADR-003). Model downloads once (~70 MB)."""

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5") -> None:
        import os

        from fastembed import TextEmbedding

        self.model = TextEmbedding(model_name=model_name, cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"))

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = np.array(list(self.model.embed(texts)), dtype=np.float32)
        return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


class PolicyIndex:
    """`search(query, k)` returns passages with rule IDs; `rank(query, method)` is for the eval."""

    def __init__(self, chunks: list[Chunk], embedder: Embedder | None = None, mode: str = "hybrid") -> None:
        self.chunks = chunks
        self.mode = mode if embedder is not None else "bm25"
        self.bm25 = BM25Okapi([tokenize(f"{c.title} {c.text}") for c in chunks])
        self.embedder = embedder
        self.matrix = embedder.embed([f"{c.section}. {c.title}. {c.text}" for c in chunks]) if embedder else None

    @classmethod
    def from_policy(cls, path: Path = POLICY_PATH, embedder: Embedder | None = None, mode: str = "hybrid") -> PolicyIndex:
        return cls(chunk_policy(path.read_text(encoding="utf-8")), embedder, mode)

    def _bm25_rank(self, query: str) -> list[int]:
        scores = self.bm25.get_scores(tokenize(query))
        return [int(i) for i in np.argsort(-scores, kind="stable")]

    def _dense_rank(self, query: str) -> list[int]:
        assert self.embedder is not None and self.matrix is not None
        q = self.embedder.embed([query])[0]
        return [int(i) for i in np.argsort(-(self.matrix @ q), kind="stable")]

    def rank(self, query: str, method: str | None = None) -> list[int]:
        method = method or self.mode
        if method == "bm25":
            return self._bm25_rank(query)
        if method == "dense":
            return self._dense_rank(query)
        fused: dict[int, float] = {}
        for ranking in (self._bm25_rank(query), self._dense_rank(query)):
            for pos, idx in enumerate(ranking):
                fused[idx] = fused.get(idx, 0.0) + 1.0 / (RRF_K + pos + 1)
        return sorted(fused, key=lambda i: (-fused[i], i))

    def search(self, query: str, k: int = 4) -> list[dict[str, Any]]:
        return [
            {"rule_ids": c.rule_ids, "section": c.section, "title": c.title, "text": c.text}
            for c in (self.chunks[i] for i in self.rank(query)[:k])
        ]


def build_index(mode: str = "hybrid") -> PolicyIndex:
    """Build the app's index; falls back to BM25-only when the embedding model is unavailable."""
    if mode == "bm25":
        return PolicyIndex.from_policy(mode="bm25")
    try:
        return PolicyIndex.from_policy(embedder=FastEmbedder(), mode=mode)
    except Exception:  # noqa: BLE001 - offline / no model: keep working lexically
        return PolicyIndex.from_policy(mode="bm25")
