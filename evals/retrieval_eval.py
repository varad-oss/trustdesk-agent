"""Retrieval eval: recall@1, recall@4 and MRR for BM25-only, dense-only and hybrid (RRF).

Writes results/retrieval.json. Needs the local embedding model (downloaded once by fastembed).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import yaml

from trustdesk.config import REPO_ROOT
from trustdesk.retrieval import PolicyIndex
from trustdesk.retrieval.index import FastEmbedder

QA_PATH = REPO_ROOT / "evals" / "retrieval_qa.yaml"
OUT = REPO_ROOT / "results" / "retrieval.json"
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"


def score(index: PolicyIndex, qa: list[dict], method: str) -> dict[str, float]:
    r1 = r4 = rr = 0.0
    for item in qa:
        ranking = index.rank(item["q"], method)
        primary = [index.chunks[i].rule_ids[0] if index.chunks[i].rule_ids else "" for i in ranking]
        rank = primary.index(item["rule"]) + 1 if item["rule"] in primary else None
        r1 += rank == 1
        r4 += rank is not None and rank <= 4
        rr += 1 / rank if rank else 0
    n = len(qa)
    return {"recall@1": r1 / n, "recall@4": r4 / n, "mrr": rr / n}


def main() -> None:
    qa = yaml.safe_load(QA_PATH.read_text(encoding="utf-8"))
    index = PolicyIndex.from_policy(embedder=FastEmbedder(EMBEDDING_MODEL), mode="hybrid")
    result = {
        "created_at": datetime.now(UTC).isoformat(),
        "n_questions": len(qa),
        "n_chunks": len(index.chunks),
        "embedding_model": EMBEDDING_MODEL,
        "fusion": "reciprocal rank fusion, k=60",
        "hit_definition": "chunk whose primary (heading) rule ID equals the expected rule",
        "methods": {m: score(index, qa, m) for m in ("bm25", "dense", "hybrid")},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2))
    print(json.dumps(result["methods"], indent=2))


if __name__ == "__main__":
    main()
