# Changelog

## v0.1.0 — 2026-10-23

First public release.

- Policy-bound support agent (LangGraph) over a fictional e-commerce store: 12 typed tools, a deterministic policy
  engine, human approval for high-risk actions with durable interrupt/resume, spotlighting and PII masking.
- Four defense levels (D0–D3) and a τ-bench-style evaluation harness: 36 validated tasks (10 prompt-injection
  attacks), LLM-simulated customers, deterministic graders, pass^k with bootstrap intervals.
- Full ablation on local `qwen3:8b`: policy violations 21% (D0) → 0% (D2, D3); attack success 17% → 0%.
- Hybrid policy retrieval (BM25 + local embeddings + RRF): recall@4 0.96.
- FastAPI service, web console, CLI, Docker Compose; runs entirely locally for free.
