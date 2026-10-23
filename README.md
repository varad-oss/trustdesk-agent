# TrustDesk

[![CI](https://github.com/<you>/trustdesk/actions/workflows/ci.yml/badge.svg)](.github/workflows/ci.yml)

**A customer-support agent that can refund, cancel and re-route orders, but can't break policy, even when it is wrong or being manipulated. It asks a human before risky actions, and its prompt-injection defenses are measured, not just claimed.**

Demo video: _to be recorded (see [docs/DEMO.md](docs/DEMO.md))._

## Results

| Defense level | Benign utility | Utility under attack | Attack success rate | pass^3 | $/conversation |
|---|---|---|---|---|---|
| D0 baseline | _not yet measured_ | | | | |
| D1 spotlighting | _not yet measured_ | | | | |
| D2 policy engine in code | _not yet measured_ | | | | |
| D3 + human approval | _not yet measured_ | | | | |

These cells stay empty until `make eval-full` has produced a reported run in `results/`. `make report` then
writes the numbers, with dates, model, task count, trials and 95% intervals, into
[docs/EVAL_REPORT.md](docs/EVAL_REPORT.md). Already measured: the task suite validates 36/36 tasks
([results/validation.json](results/validation.json)), and hybrid policy retrieval reaches the recall@4 shown in
[results/retrieval.json](results/retrieval.json).

## Architecture

```mermaid
flowchart LR
  C[Customer] --> G[LangGraph agent<br/>Claude Haiku 5.5]
  G -->|tool calls| T[12 typed tools]
  T --> PE[Deterministic<br/>policy engine]
  PE --> DB[(ShopSetu SQLite)]
  G -->|tier-2 action| A[approval_gate<br/>interrupt / resume]
  A --> L[Support lead]
  T -. reviews, notes, tickets .-> S[Spotlighting + masking]
```

The model chooses tools; Python decides whether they run. Every write tool asks a deterministic policy engine
first, so refunds cannot exceed what was paid, go to an unverified UPI ID, or touch another customer's order,
whatever the model was told. Risky actions (refunds above ₹5,000, credit above ₹200, frequent-refund customers)
pause the graph with `interrupt()`, survive a restart in a SQLite checkpoint, and resume exactly once after a
human decides. Text from reviews, delivery notes and old tickets is wrapped and datamarked as data. Defense levels
D0–D3 switch these layers on one by one so the eval harness can measure what each buys. More:
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quickstart (no API key needed)

```bash
git clone <repo> && cd trustdesk
docker compose up --build
```

Open http://localhost:8000, log in as `priya.s@example.in` with order `SS-10482`, or press **Attack demo**.
Without a key the rule-based demo model runs (it is not an LLM). With uv instead of Docker: `make install && make chat-fake`.

With a model: copy `.env.example` to `.env`, set `ANTHROPIC_API_KEY`, then `make chat` or `make serve`.

## Evaluating

```bash
make test            # offline unit + integration tests
make validate        # golden replay of every task through the real graph (no model calls)
make eval-retrieval  # recall@1/@4, MRR for BM25, dense, hybrid
make eval-smoke      # 3 tasks x D0, D2 with the real model
make eval-full       # 36 tasks x D0-D3 x 3 trials; pilot first, asks before spending
make report          # regenerates docs/EVAL_REPORT.md and charts from results/
```

Task format and graders: `evals/`. Attack success is judged only from database state and literal output, never by
an LLM judge.

## Design decisions and limitations

- Decisions: [docs/DECISIONS.md](docs/DECISIONS.md) — LangGraph (ADR-001), Claude Haiku 5.5 through a custom model node (ADR-002), BM25 + bge-small + RRF (ADR-003), and deviations from the spec (ADR-007).
- Threat model: [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) — OWASP agentic + LLM top-10 mapping, lethal-trifecta analysis.
- Limitations: synthetic store and policy; static attack suite (adaptive attacks go in [docs/RED_TEAM_LOG.md](docs/RED_TEAM_LOG.md)); LLM-simulated customers add variance; small n (36 tasks); Langfuse integration not yet exercised live.

## How it was built

Implemented with Claude Code as a pair programmer; task design, evaluation design, red-teaming and analysis by me.
