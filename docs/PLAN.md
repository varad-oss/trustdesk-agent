# Plan

Build mode chosen by Varad (23 Oct 2026): build everything in one pass, then learn from the notes in
`docs/learning/`. Milestones from the spec are kept as the structure; per-milestone stops were skipped.

| # | Milestone | Acceptance criteria (from SPEC §12, lean scope) | Status |
|---|---|---|---|
| M0 | Research, decisions, scaffold | ADR-001/002/003 with versions and dated prices; `make test`; CLAUDE.md | Done (CI workflow written; first GitHub run pending a push) |
| M1 | Environment | Schema, seed, fixtures, policy doc, 12 tools, policy engine, ≥30 unit tests incl. Hypothesis; `make demo-tools` | Done |
| M2 | Agent core | Graph, model adapter, prompts, SQLite checkpointer, CLI chat; fake-model integration tests | Done; real-model CLI conversation needs an API key |
| M3 | Retrieval | Hybrid index with rule-ID citations; results/retrieval.json for 3 methods | Done |
| M4 | Human approval | Tiers, interrupt/resume, CLI approvals, audit log; tests for no-execution-without-approval and restart survival | Done |
| M5 | Eval harness | Schema, validator, simulator, runner, graders, metrics, report; ≥30 validated tasks; `make eval-smoke` | Done in code; 36/36 tasks validate; smoke/baseline runs need an API key |
| M6 | Observability | Langfuse or documented fallback; cost per episode; trace screenshot | JSONL fallback + `trustdesk trace` done; Langfuse code path untested (no keys); screenshot pending |
| M7 | Security | Threat model, attack tasks, D0–D3 flags, ablation run, results with intervals, RED_TEAM_LOG ≥5 | Code, 10 attack tasks and threat model done; ablation run needs your cost OK; red-team entries are yours |
| M8 | API, UI, Docker | FastAPI endpoints, CLI approvals, Dockerfile; UI if time | Done (UI included); `docker compose up` verified locally on 2026-10-23 |
| M9 | Docs and demo | README, ARCHITECTURE with Mermaid, demo script, recording checklist | Done; README results table fills in after the eval run |
