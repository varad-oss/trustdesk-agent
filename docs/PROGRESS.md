# Progress

## Status (2026-10-23)

Everything runs locally and for free (ADR-009): `qwen3:8b` on Ollama for the agent and the simulated customer,
fastembed for embeddings, JSONL traces. No API keys or paid services anywhere.

- `make test`: all unit + integration tests pass offline (local-model tests are behind `-m local_model`).
- `make validate`: 36/36 tasks pass golden replay through the real graph (results/validation.json).
- `make eval-retrieval`: measured (results/retrieval.json).
- `make eval-smoke`: run on qwen3:8b (results/smoke-local-*/); not a reported run.
- `make report`: regenerates docs/EVAL_REPORT.md from those files.
- `docker compose up`: verified with the rule-based demo model; the default now talks to the host's Ollama.

## Waiting on Varad

1. **Full ablation (free, slow):** `make eval-lean` (36 tasks × D0–D3 × 1 trial) for a first look, then `make eval-full`
   (× 3 trials, for pass^3). Both start with a pilot that prints the measured seconds/episode and the estimated
   wall-clock time and ask before continuing. Run them when no other project is using Ollama (`ollama ps`).
   Then `make eval-rubber-stamp` and `make report`.
2. **Review the tasks** in `evals/tasks/` and the 25 retrieval questions in `evals/retrieval_qa.yaml`.
3. **Hand-label 10 simulator transcripts** from the run (report the n). Optional: a second local model family for the
   simulator (`ollama pull llama3.2:3b`, `TRUSTDESK_SIM_MODEL=llama3.2:3b`) to reduce correlated errors.
4. **Red-team log:** 5 manual attempts in `docs/RED_TEAM_LOG.md`.
5. **Trace screenshot** into `docs/images/` (web console or `trustdesk trace <id>`). Langfuse only if self-hosted.
6. Push to GitHub so CI runs (offline tests only, free); add the badge URL to the README.

## Open risks

- An 8B local model makes more mistakes than a frontier model; utility numbers will be modest. The policy engine is
  what keeps those mistakes from becoming violations — that is the point the ablation should show.
- Ollama is shared with other projects on this machine; two 8B models loaded together thrash 16 GB RAM.
- The rule-based demo model is only for demos and tests; it is never evaluated.
