# TrustDesk

[![CI](https://github.com/<you>/trustdesk/actions/workflows/ci.yml/badge.svg)](.github/workflows/ci.yml)

**A customer-support agent that can refund, cancel and re-route orders, but can't break policy, even when it is wrong or being manipulated. It asks a human before risky actions, and its prompt-injection defenses are measured, not just claimed.**

Demo video: _to be recorded (see [docs/DEMO.md](docs/DEMO.md))._

## Results

Run [`lean-20261023`](results/lean-20261023/episodes.jsonl) on 2026-10-23: agent and simulated customer both
`qwen3:8b` on Ollama (local, $0), 36 tasks × D0–D3 × **1 trial** (144 episodes). Values are point estimates with
95% Wilson intervals. Full tables, failure taxonomy and chart: [docs/EVAL_REPORT.md](docs/EVAL_REPORT.md).

| Defense level | Episodes with policy violations | Attack success rate | Benign utility | Utility under attack | pass^1 | Model s/conversation |
|---|---|---|---|---|---|---|
| D0 baseline | **0.22** [0.12, 0.38] | 0.10 [0.02, 0.40] | 0.23 [0.11, 0.42] | 0.40 [0.17, 0.69] | 0.28 | 110\* |
| D1 spotlighting | **0.17** [0.08, 0.32] | 0.00 [0.00, 0.28] | 0.27 [0.14, 0.46] | 0.20 [0.06, 0.51] | 0.22 | 20 |
| D2 policy engine in code | **0.00** [0.00, 0.10] | 0.00 [0.00, 0.28] | 0.27 [0.14, 0.46] | 0.10 [0.02, 0.40] | 0.22 | 20 |
| D3 + human approval | **0.00** [0.00, 0.10] | 0.00 [0.00, 0.28] | 0.19 [0.09, 0.38] | 0.20 [0.06, 0.51] | 0.17 | 20 |

What this shows, and what it doesn't:
- **The code-level layers did their job.** With only prompts (D0/D1), the 8B model acted on unverified accounts,
  refunded more than was paid (₹2,499 on a ₹997 order), sent a prepaid refund to the wrong destination and skipped
  REF-4 approval: violations in 22% / 17% of episodes. With the policy engine (D2/D3): 0 of 72.
- **Task success is low at every level (≈0.2–0.3)**: an 8B local model often stops early or uses wrong arguments
  (see the failure taxonomy). Defenses don't make the model smarter; they make its mistakes safe.
- **Attack success was low even at D0** (1 of 10: acting before verification), so this run can't show much
  difference between levels on that metric; with n=10 per level the intervals overlap.
- **Small n, one trial:** utility differences between levels are within noise. pass^3 needs `make eval-full`.
- \*D0 ran first, while another project shared the GPU and Ollama restarted; its time is contention, not D0.

Also measured: 36/36 tasks validate ([results/validation.json](results/validation.json)); hybrid policy retrieval
recall@4 0.96 vs 0.80 BM25 ([results/retrieval.json](results/retrieval.json)).

## Architecture

```mermaid
flowchart LR
  C[Customer] --> G[LangGraph agent<br/>qwen3:8b on Ollama, local]
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

## Quickstart (free: no API keys, no paid services)

```bash
git clone <repo> && cd trustdesk
docker compose up --build
```

Open http://localhost:8000, log in as `priya.s@example.in` with order `SS-10482`, or press **Attack demo**.
With uv instead of Docker: `make install && make chat`. Everything runs on your machine at $0.

With the local LLM: install [Ollama](https://ollama.com), run `make ollama-pull` (qwen3:8b, ~5 GB), then `make chat` or `make serve`. `docker compose up` talks to the Ollama on your machine; `TRUSTDESK_PROVIDER=fake docker compose up` runs without it.

## Evaluating

```bash
make test            # offline unit + integration tests
make validate        # golden replay of every task through the real graph (no model calls)
make eval-retrieval  # recall@1/@4, MRR for BM25, dense, hybrid
make eval-smoke      # 3 tasks x D0, D2 with the local model
make eval-lean       # 36 tasks x D0-D3 x 1 trial
make eval-full       # 36 tasks x D0-D3 x 3 trials; pilot estimates hours first
make report          # regenerates docs/EVAL_REPORT.md and charts from results/
```

Task format and graders: `evals/`. Attack success is judged only from database state and literal output, never by
an LLM judge.

## Design decisions and limitations

- Decisions: [docs/DECISIONS.md](docs/DECISIONS.md) — LangGraph (ADR-001), local qwen3:8b on Ollama through a custom model node (ADR-002), free-only (ADR-009), BM25 + bge-small + RRF (ADR-003), and deviations from the spec (ADR-007).
- Threat model: [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) — OWASP agentic + LLM top-10 mapping, lethal-trifecta analysis.
- Limitations: synthetic store and policy; static attack suite (adaptive attacks go in [docs/RED_TEAM_LOG.md](docs/RED_TEAM_LOG.md)); LLM-simulated customers add variance; small n (36 tasks); an 8B local model (simulator from the same family); Langfuse integration (self-hosted) not yet exercised live.

## How it was built

Implemented with Claude Code as a pair programmer; task design, evaluation design, red-teaming and analysis by me.
