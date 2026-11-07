# Changelog

## Unreleased

- Video course: a free 2 h 18 min course that teaches agentic AI from scratch by taking TrustDesk apart, linked from
  the README with chapter timestamps.
- Web console redesign: defence-level stepper that explains each level, held actions shown as approval slips (amount,
  rule, the agent's justification, the conversation so far), session details (verified customer, model calls,
  tokens), model status, sample conversations, a working-indicator with elapsed time, dark mode and a phone layout.
- Fix: the rule-based demo model captured a sentence's trailing full stop as part of an email address, so
  verification failed and the attack demo looped when run with `TRUSTDESK_PROVIDER=fake`.

## v0.1.0 — 2026-10-23

First public release.

- Policy-bound support agent (LangGraph) over a fictional e-commerce store: 12 typed tools, a deterministic policy
  engine, human approval for high-risk actions with durable interrupt/resume, spotlighting and PII masking.
- Four defense levels (D0–D3) and a τ-bench-style evaluation harness: 36 validated tasks (10 prompt-injection
  attacks), LLM-simulated customers, deterministic graders, pass^k with bootstrap intervals.
- Full ablation on local `qwen3:8b`: policy violations 21% (D0) → 0% (D2, D3); attack success 17% → 0%.
- Hybrid policy retrieval (BM25 + local embeddings + RRF): recall@4 0.96.
- FastAPI service, web console, CLI, Docker Compose; runs entirely locally for free.
