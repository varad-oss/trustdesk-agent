# Progress

Build mode: build everything first, learn afterwards (the user's change to the spec). Per-milestone stops are skipped.

## Done (not yet covered by tests)
- Scaffold: uv project (Python 3.12), versions checked 2026-10-23: langgraph 1.2.16, langgraph-checkpoint-sqlite 3.1.2, anthropic 2.1.0, fastembed 0.9.0, rank-bm25 0.2.2, langfuse 5.0.4 (optional), fastapi 0.161.
- M1 store: `env/` (schema, deterministic seed, fixture patches, `fresh_env`), `policy_engine.py`, `security/` (spotlighting, masking), the 12 tools in `tools/` (errors returned as data, idempotency keys).
- Policy doc `docs/policy/ShopSetu_Support_Policy.md` (~2,600 words, rule IDs + EX-1..EX-10).
- M3 retrieval: `retrieval/index.py` (structure-aware chunks, BM25 + fastembed dense + RRF). BM25 smoke-checked.
- M2 parts: `agent/model.py` (Anthropic, OpenAI-compat, Scripted, Cached adapters), `agent/demo_model.py` (rule-based no-key model + `make_model`), `agent/prompts.py`.
- M4 parts: `hitl/approvals.py` (tiers, queue, decision recording).
- Observability: `observability/pricing.py`, `observability/tracing.py` (JSONL + optional Langfuse), `config/pricing.yaml`.

## Next
1. `agent/graph.py`: nodes agent -> tools -> approval_gate (`interrupt(..., response_schema=ApprovalResponse)`) -> finalize; loop detection (same call 3x), max steps, refusal handling; SQLite checkpointer. Use `tools.write.preview` for the D3 dry run, and key writes as `f"{session_id}:{tool_use_id}"`.
2. Conversation runtime + `cli.py` (typer: seed, chat, approvals, eval, demo-tools).
3. Unit tests (engine with Hypothesis properties, tools, retrieval) and integration tests (scripted model: gating, interrupt/resume, restart survival, loop, max steps).
4. evals/: task YAMLs (30+, 10+ attacks, golden actions), validator, simulator, runner, graders, metrics (pass^k, Wilson, bootstrap), report; `evals/retrieval_qa.yaml`.
5. API (FastAPI), minimal UI, Dockerfile/compose, CI, Makefile, CLAUDE.md, ADRs, THREAT_MODEL, README, learning notes.

## Blockers
- No `ANTHROPIC_API_KEY` in the environment: real-model eval runs need a key in `.env`, plus your approval of the pilot cost.
