# Progress

## Status (2026-10-23)

Everything that can be built and verified without paid API calls is built and tested.

- `make test`: all unit + integration tests pass offline (counts in the latest test output; real-model tests are behind `-m real_model`).
- `make validate`: 36/36 tasks pass golden replay through the real graph (results/validation.json).
- `make eval-retrieval`: measured (results/retrieval.json).
- `make report`: regenerates docs/EVAL_REPORT.md from those files.

## Waiting on Varad

1. **API key**: put `ANTHROPIC_API_KEY` in `.env`, then `make eval-smoke` (≈6 episodes on Haiku 5.5).
2. **Full ablation**: `make eval-full` runs a 3-episode pilot, prints the measured $/episode and the extrapolated total for
   36 tasks × D0–D3 × 3 trials (432 episodes), and asks before spending. Then `make eval-rubber-stamp` and `make report`.
3. **Review the tasks** in `evals/tasks/` and the 25 retrieval questions in `evals/retrieval_qa.yaml` (the spec says you author/verify them).
4. **Hand-label 10 simulator transcripts** from the full run (report the n).
5. **Red-team log**: 5 manual attempts in `docs/RED_TEAM_LOG.md`.
6. **Langfuse** (optional, time-boxed): add keys to `.env`, run one chat, take a trace screenshot into `docs/images/`.
7. Push to GitHub so CI runs; add the badge URL to the README.

## Open risks

- No real-model numbers yet: prompt quality, effort level and simulator behaviour are unmeasured.
- The rule-based demo model is only for demos and tests; it is not an evaluation subject.
- The Langfuse integration is written against the v5 SDK but has not been run against a live project.
