# Interview notes — every design decision in one line

Suggested study order: M1 → M2 → M4 → M5 → M7, then M3, M6, M8, M0, M9. For each note: read the "Key concepts", open
the files it names, run "Verify it yourself", then do the break-it exercise.

| Decision | One-line justification |
|---|---|
| LangGraph state machine | Explicit control flow + durable `interrupt()`/resume for human approval. |
| Custom Anthropic model node | Guarantees Haiku 5.5 rules: no sampling params, no prefill, thinking blocks unchanged, constant prefix. |
| Append-only messages | Keeps thinking blocks valid and the prompt cache warm. |
| Constant tool list; permissions in tools | Changing tools mid-conversation breaks thinking replay; code enforcement is stronger anyway. |
| Deterministic policy engine | Money and identity invariants hold even when the model is wrong or injected. |
| Engine decides tier 2 | One source of truth for what needs a human (REF-4, GW-1, FR-1). |
| Deny never reaches a human | A tired approver can't approve a policy violation (defense in depth). |
| Pure code before `interrupt()` | LangGraph re-runs the node on resume. |
| Idempotency key = session + tool_use_id | Retries replay; distinct calls don't collide; UNIQUE columns cover crash windows. |
| Errors as data with hints | The model can recover (verify, ask, explain) instead of crashing. |
| Frozen clock per env | Time-window rules are reproducible. |
| Fixture patches with relative times | Each task's world is explicit in its YAML. |
| Spotlighting + datamarking (D1) | Lowers injection success cheaply; not relied on for invariants. |
| PII masking at the tool layer | Data the model never sees can't be leaked. |
| Refund destination from DB | Attacker-supplied UPI IDs can't become destinations. |
| Hybrid retrieval with RRF | Lexical IDs + semantic phrasing, no score calibration. |
| fastembed (ONNX) over sentence-transformers | Same model quality, far smaller install. |
| No vector DB | ~32 chunks fit in memory. |
| τ-bench end-state grading | Robust to phrasing; grades what actually happened. |
| No LLM judge for attacks | A judge can be injected; goals are checkable facts. |
| Golden-replay validator | Proves every task is solvable and its forbidden actions are forbidden. |
| pass^k with unbiased estimator | Measures reliability across repeated trials. |
| Bootstrap over tasks | Trials of one task are correlated. |
| Independent invariant checker | Catches bugs in the engine itself. |
| Utility under attack + ASR | A refuse-everything agent must not look "secure". |
| Pilot before paid runs | Measured $/episode before committing budget. |
| JSONL traces, Langfuse optional | Results reproducible from committed files. |
| Rule-based demo model | Quickstart and Docker demo work with no key; never evaluated. |
| Plain sqlite3 | Small schema; visible SQL; fast per-episode copies. |
| Vanilla JS console | No build step, same container. |
