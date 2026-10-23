# TrustDesk — notes for Claude Code

Policy-bound support agent for the fictional store ShopSetu. Spec: `docs/SPEC.md`. Plan: `docs/PLAN.md`.
Status and next steps: `docs/PROGRESS.md`. Decisions: `docs/DECISIONS.md`.

## Commands
- Setup: `uv sync --all-extras` (Python 3.12 via uv)
- Tests (offline): `make test` · lint: `make lint` · coverage: `make test-cov`
- Paid smoke tests: `uv run pytest -m real_model` (needs ANTHROPIC_API_KEY)
- Task validator (no model calls): `make validate`
- Retrieval eval: `make eval-retrieval` · Agent evals: `make eval-smoke`, `make eval-full` · Report: `make report`
- Demo without a key: `make chat-fake`, `make serve` with `TRUSTDESK_PROVIDER=fake`

## Layout
- `src/trustdesk/env/` store schema, seed, fixtures (frozen clock) · `policy_engine.py` rules as code
- `src/trustdesk/tools/` 12 tools (read.py, write.py, registry.py) · `security/` spotlighting, masking
- `src/trustdesk/agent/` graph.py (LangGraph), model.py (adapters), prompts.py, runtime.py
- `src/trustdesk/hitl/` approvals · `retrieval/` BM25+dense+RRF · `observability/` traces, pricing
- `src/trustdesk/api/` FastAPI · `ui/index.html` · `cli.py`
- `evals/` tasks/*.yaml, schema, validator, simulator, runner, graders, metrics (generic), report
- `results/` only committed outputs of Makefile commands

## Conventions
- Tools return errors as data (`{"ok": false, "error_code", "message", "hint"}`), never raise.
- Messages are append-only; system prompt and tool list are constant per conversation (thinking blocks).
- Code before `interrupt()` must be side-effect free; writes use `session:tool_use_id` idempotency keys.
- Never weaken tests; never special-case eval tasks; every reported number comes from `results/`.
- No temperature/top_p/top_k and no assistant prefill for Claude Haiku 5.5.
- Secrets only in `.env`.
