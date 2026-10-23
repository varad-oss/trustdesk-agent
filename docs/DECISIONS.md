# Architecture decision records

Versions were checked on 2026-10-23 against PyPI (latest release) and the installed packages' own APIs
(`inspect.signature` / docstrings), not from memory. Pinned in `pyproject.toml` / `uv.lock`.

| Package | Version | What was verified |
|---|---|---|
| langgraph | 1.2.16 | `interrupt(value, *, response_schema=...)`, `Command(resume=...)`, `StateGraph.compile(checkpointer=...)`, node re-runs from the top on resume (docstring) |
| langgraph-checkpoint-sqlite | 3.1.2 | `SqliteSaver(conn)`, `.setup()` |
| anthropic | 2.1.0 | `messages.create(..., thinking, output_config, cache_control, tools)`; no `temperature` parameter; `usage.output_tokens_details.thinking_tokens` |
| fastembed | 0.9.0 | `TextEmbedding(model_name, cache_dir)`; `BAAI/bge-small-en-v1.5` in supported list |
| rank-bm25 | 0.2.2 | `BM25Okapi(corpus).get_scores(tokens)` |
| langfuse (optional) | 5.0.4 | `get_client()`, `start_observation(trace_context=..., as_type=..., usage_details, cost_details)` |
| fastapi / uvicorn | 0.161.0 / 0.54.0 | standard |
| hypothesis | 6.170.7 | standard |

Python: 3.12 via uv (the machine default is 3.15, for which some binary wheels, e.g. onnxruntime, may lag).

---

## ADR-001 Agent framework: LangGraph

**Context.** We need explicit control flow (agent → tools → approval → agent), a human-approval interrupt that
can be resumed later (possibly in another process), durable checkpoints, any model provider, and tracing.

**Options.**

| Criterion | LangGraph 1.2 | OpenAI Agents SDK | PydanticAI | Google ADK |
|---|---|---|---|---|
| Explicit control flow | Graph of nodes + conditional edges: the state machine is the code | Agent loop with handoffs; flow mostly implicit | Agent loop; graphs via `pydantic-graph` | Agents + workflow agents (sequential/parallel/loop) |
| Approval interrupt + resume | `interrupt()` / `Command(resume=)`, first-class, with `response_schema` | Tool-approval hooks exist but resume across processes is DIY | Deferred tools / human-in-the-loop support, younger | Tool confirmation callbacks; persistence via session services |
| Durable checkpointing | Built-in checkpointers (SQLite, Postgres) keyed by thread | Sessions (conversation memory), not mid-run checkpoints | Message history persistence; durable execution via integrations | Session/state services |
| Provider-agnostic | Yes (we bypass LangChain models entirely, see ADR-002) | OpenAI-first, others via adapters | Yes | Gemini-first, others via LiteLLM |
| Tracing | LangSmith, Langfuse, OpenTelemetry | Built-in tracing | Logfire / OTel | Cloud Trace / OTel |
| Docs maturity | Mature, many examples | Good | Good, smaller | Newer |
| Named in agentic-AI job posts | Most often (my reading of postings; not measured) | Often | Sometimes | Sometimes |

**Decision.** LangGraph. The approval requirement ("pause for a human, survive a restart, resume exactly once")
maps directly onto `interrupt()` + a SQLite checkpointer, and the graph makes the control flow explainable.

**Consequences.** Nodes must be written knowing that a resumed node re-runs from its first line: code before
`interrupt()` is side-effect free, and the write after approval uses an idempotency key
(`src/trustdesk/agent/graph.py::approval_gate`). Non-serialisable objects (DB, model) are closed over by the node
functions rather than stored in state.

## ADR-002 Model and provider: Claude Haiku 5.5 via a custom model node

**Prices (USD per million tokens, checked 2026-10-23** from Anthropic's Claude API model table as cached in the
`claude-api` reference on 2026-10-06; re-check anthropic.com/pricing before publishing): Haiku 5.5 input $0.10,
output $0.50 (prompts ≤ 100K tokens), cache read 0.1× input, 5-minute cache write 1.25× input. Stored in
`config/pricing.yaml` with the date.

**Decision.** `claude-haiku-5-5`, adaptive thinking, `output_config.effort = "low"` to start (to be measured), via
the official `anthropic` SDK in a small adapter (`agent/model.py::AnthropicModel`), not `langchain-anthropic`.

**Why a custom node.** The Haiku 5.5 rules are easy to violate through an abstraction layer: no
`temperature/top_p/top_k`; no assistant prefill; thinking blocks must be passed back byte-for-byte; and when they
are, the system prompt, tool list and earlier messages must not change. Owning the call (≈60 lines) guarantees:
messages stored as raw Anthropic content-block dicts; append-only history (graph reducer `operator.add`); the
system prompt chosen once per conversation from the defense level; one constant tool list
(`tools/registry.py::tool_specs`, deterministic order, also good for caching); `stop_reason == "refusal"`
handled in the graph. Automatic prompt caching (`cache_control` at the top level) caches tools + system + history.

**Portability.** `ChatModel` protocol with `AnthropicModel`, `OpenAICompatModel` (any `/chat/completions`
endpoint, e.g. Ollama), `ScriptedModel` (tests) and `RuleBasedDemoModel` (no-key demo). Chosen by
`TRUSTDESK_PROVIDER`. `agent/model.py` has no TrustDesk imports so later projects can copy it.

## ADR-003 Retrieval: BM25 + fastembed `bge-small-en-v1.5` + RRF, in process

**Decision.** Structure-aware chunks (one `###` rule section each, tagged with rule IDs), BM25 (`rank-bm25`) and
dense embeddings from `BAAI/bge-small-en-v1.5` through `fastembed` (ONNX runtime), fused with reciprocal rank
fusion (k = 60). Everything in memory: 32 chunks do not justify a vector database.

**Why fastembed over sentence-transformers.** Same model family, but ONNX instead of PyTorch: roughly 10× smaller
install, faster CPU start-up, smaller Docker image. Quality is the model's, not the runtime's.

**Fallback.** If the embedding model cannot load (offline), `build_index` falls back to BM25 and reports
`retrieval: bm25` on `/health`. Unit tests use a deterministic fake embedder.

**Measured** (results/retrieval.json, 25 questions): see `docs/EVAL_REPORT.md`. Nothing was tuned on the eval set.

## ADR-004 Storage: plain `sqlite3`

Small schema; per-episode isolation by copying a seeded in-memory template with the SQLite backup API (≈1 ms);
every policy query is visible SQL, which helps when explaining the engine. JSON columns are TEXT, decoded in
`Env`. An ORM would add a layer without removing any complexity here.

## ADR-005 Observability: JSONL traces always, Langfuse optional

Every model call (model, tokens incl. thinking and cache, cost, latency, stop reason), tool call (args, ok, error
code, result size) and approval is a trace event with a 32-hex trace ID. Events go to `data/traces/<trace_id>.jsonl`
(eval runs: `results/<run>/traces/`) and are linked from each episode's trajectory. If `LANGFUSE_PUBLIC_KEY` and
`LANGFUSE_SECRET_KEY` are set and the `langfuse` extra is installed, the same events go to Langfuse under the same
trace ID. **Status:** the Langfuse path is implemented against the v5 SDK surface but not exercised end to end,
because no Langfuse account/keys were available during the build. The JSONL path plus `trustdesk trace <id>` is
the fallback the spec allows. A Langfuse screenshot for the docs is still to do (needs keys).

## ADR-006 Web UI: one static page, vanilla JS, served by FastAPI

No build step, no extra service, works inside the same Docker image. Streamlit would add a second server process
and fight the approval-queue polling model.

## ADR-007 Deviations from the spec (smallest changes; flagged for review)

1. **Idempotency keys come from the runtime, not the model.** Write tools are keyed `"{session_id}:{tool_use_id}"`.
   Model-invented keys could collide or be reused across distinct actions; the tool-use ID is unique per call and
   stable across a crash/resume, which is exactly the retry semantics we need. Domain tables also carry the key
   as a UNIQUE column, so a crash between the write and the idempotency record cannot double-apply.
2. **`cancel_order` does not refund by itself**; the agent calls `issue_refund` afterwards (REF-5 says the refund
   follows the cancellation). The tool result tells the model to do so.
3. **Schema additions:** `orders.promised_by` (needed to decide GW-1 delays deterministically), `returns.basis`
   (RET-1 vs RET-4, needed for REF-3 shipping), and tables `escalations`, `approvals`, `idempotency`, `meta` (frozen clock).
4. **`create_return.customer_attests_unused`** records the RET-2 attestation.
5. **At D2, `needs_approval` becomes an `APPROVAL_REQUIRED` error** (there is no approval queue below D3), so D2 vs
   D3 isolates the value of the human gate.
6. **Eval tasks carry `golden_actions` and `forbidden_probes`** in addition to the spec's fields; `must_mention`
   and `expect_escalation` are optional extra checks.
7. **`evals/` is a top-level package** included in the wheel, so `trustdesk eval …` works from anywhere.

## ADR-008 Spotlighting scheme

Untrusted fields are wrapped as `<untrusted source="review|delivery_note|ticket" id="…">…</untrusted>` and
datamarked: every whitespace run becomes `ˆ` (U+02C6), following Hines et al. 2024 ("spotlighting"). Any
`<untrusted`/`</untrusted` inside the text is replaced so a payload cannot close the block early. The D1+ system
prompt states the hierarchy: system prompt and policy > customer requests within policy > tool data, never
instructions. Spotlighting reduces, but cannot guarantee, injection resistance; D2's code-level enforcement is
what makes the money and identity invariants hold.
