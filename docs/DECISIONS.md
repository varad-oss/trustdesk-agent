# Architecture decision records

Versions were checked on 2026-10-23 against PyPI (latest release) and the installed packages' own APIs
(`inspect.signature` / docstrings), not from memory. Pinned in `pyproject.toml` / `uv.lock`.

| Package | Version | What was verified |
|---|---|---|
| langgraph | 1.2.16 | `interrupt(value, *, response_schema=...)`, `Command(resume=...)`, `StateGraph.compile(checkpointer=...)`, node re-runs from the top on resume (docstring) |
| langgraph-checkpoint-sqlite | 3.1.2 | `SqliteSaver(conn)`, `.setup()` |
| Ollama (server) | 0.40.8 | native `POST /api/chat` with `tools`, `think`, `options.num_ctx`; response `message.tool_calls[].function.arguments` (object), `prompt_eval_count`, `eval_count`; `qwen3:8b` capabilities `completion, tools, thinking`, context 40,960 |
| fastembed | 0.9.0 | `TextEmbedding(model_name, cache_dir)`; `BAAI/bge-small-en-v1.5` in supported list |
| rank-bm25 | 0.2.2 | `BM25Okapi(corpus).get_scores(tokens)` |
| langfuse (optional, self-hosted only) | 5.0.4 | `get_client()`, `start_observation(trace_context=..., as_type=..., usage_details)` |
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

## ADR-002 Model and provider: local `qwen3:8b` on Ollama (free), via a custom model node

**Constraint (from Varad, 2026-10-23): everything runs for free — no paid API, no paid service.** This replaces the
spec's Claude Haiku 5.5 choice; see ADR-009.

**Decision.** `qwen3:8b` (Qwen3 8B, Apache-2.0, ~5 GB at Q4) served by Ollama on the local machine (Apple M5, 16 GB
RAM), for both the agent and the customer simulator. Thinking mode off by default (`TRUSTDESK_THINK=false`): it is
much faster, and whether thinking helps is a measurement to make, not an assumption. Context window requested
explicitly: `num_ctx = 16384` (the system prompt + 12 tool schemas are ≈3K tokens and conversations grow).

**Why the native Ollama API in a custom adapter** (`agent/model.py::OllamaModel`), not Ollama's OpenAI-compatible
endpoint or `langchain-ollama`: only the native API lets us set `num_ctx` per request (Ollama otherwise uses a small
default and silently truncates the *front* of the prompt — the system prompt and policy) and turn thinking off. Owning
≈80 lines also keeps our message format provider-neutral (content blocks), append-only, and testable.

**Why qwen3:8b.** Among models that fit 16 GB with room for a 16K context, it has native tool calling (Ollama lists the
`tools` capability), handles Hinglish acceptably, and was already installed. Measured on this machine: ~16 output
tokens/s; a single tool-call turn is a few seconds when Ollama is otherwise idle.

**Costs.** $0. The harness therefore records tokens and seconds (model time per episode, latency p50/p95) instead
of dollars; `config/pricing.yaml` was removed.

**Portability.** `ChatModel` protocol with `OllamaModel`, `OpenAICompatModel` (any *local* `/chat/completions`
server: llama.cpp, LM Studio, vLLM), `ScriptedModel` (tests) and `RuleBasedDemoModel` (offline demo). Chosen by
`TRUSTDESK_PROVIDER`; the model by `TRUSTDESK_MODEL`. `agent/model.py` has no TrustDesk imports.

**Kept from the original design** (still good practice with local models): append-only messages, a system prompt
and tool list that are constant per conversation (Ollama reuses its KV cache for an unchanged prompt prefix —
`prompt_eval_cached_count` in responses), permissions enforced in tools rather than by changing the tool list.

**Known trade-offs.** An 8B local model makes more tool-argument and policy mistakes than a frontier model; this is
what the D0–D3 ablation measures, and the policy engine keeps those mistakes from becoming violations. The simulator
uses the same model family as the agent, so their errors may correlate (a limitation to report; a second local
family such as `llama3.2:3b` can be set with `TRUSTDESK_SIM_MODEL`). If several projects share one Ollama server,
calls queue and models swap in and out of memory; run evals when Ollama is otherwise idle.

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

Every model call (model, input/output tokens, latency, stop reason), tool call (args, ok, error
code, result size) and approval is a trace event with a 32-hex trace ID. Events go to `data/traces/<trace_id>.jsonl`
(eval runs: `results/<run>/traces/`) and are linked from each episode's trajectory. If `LANGFUSE_PUBLIC_KEY` and
`LANGFUSE_SECRET_KEY` are set and the `langfuse` extra is installed, the same events go to a **self-hosted** Langfuse
(free, Docker Compose; never the paid cloud) under the same trace ID. **Status:** the Langfuse path is implemented
against the v5 SDK surface but not exercised end to end; self-hosting it is optional. The JSONL path plus `trustdesk trace <id>` is
the fallback the spec allows. A trace screenshot for the docs can come from `trustdesk trace <id>` or the web console.

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

## ADR-009 Free and local only

**Context.** Varad's requirement (2026-10-23): no paid API or paid service anywhere — development, tests, evals,
demo, or CI.

**Decision.** Ollama + `qwen3:8b` for agent and simulator (ADR-002); local embeddings via fastembed (ADR-003); JSONL
traces, with Langfuse only if self-hosted (ADR-005); CI runs only offline tests and the validator. The `anthropic`
dependency and Claude adapter were removed. The spec's "ask before spending" rule becomes "estimate wall-clock time
from a pilot and ask before long runs" (`evals/runner.py::cli_run`).

**Consequences.** Reported numbers describe an 8B local model, not a frontier API model; they will be lower and
noisier, which makes the value of code-level enforcement easier to see. Full runs take hours, so `make eval-lean`
(1 trial) exists for a first look; pass^3 needs `make eval-full`.
