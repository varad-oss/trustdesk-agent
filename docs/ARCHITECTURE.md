# Architecture

## Components

```mermaid
flowchart LR
  subgraph Clients
    CLI[trustdesk CLI<br/>chat / approvals / eval]
    UI[Web console<br/>ui/index.html]
    SIM[Eval runner +<br/>customer simulator]
  end
  API[FastAPI<br/>api/app.py]
  subgraph Agent["Agent (LangGraph)"]
    G[graph.py<br/>agent → tools → approval_gate → finalize]
    M[model.py<br/>Ollama / local OpenAI-compat / fake]
    P[prompts.py<br/>constant per defense level]
  end
  subgraph Guardrails["Guardrails in code"]
    T[tools/<br/>12 typed tools, errors as data]
    PE[policy_engine.py<br/>deterministic rules]
    SEC[security/<br/>spotlighting, masking]
    H[hitl/approvals.py<br/>tier-2 queue]
  end
  R[retrieval/<br/>BM25 + bge-small + RRF]
  DB[(ShopSetu SQLite<br/>orders, refunds, audit_log, approvals)]
  CK[(Checkpointer<br/>SQLite)]
  TR[(Traces<br/>JSONL / Langfuse)]
  UI --> API --> G
  CLI --> G
  SIM --> G
  G --> M
  G --> P
  G --> T
  T --> PE
  T --> SEC
  T --> R
  G --> H
  T --> DB
  PE --> DB
  H --> DB
  G --> CK
  G --> TR
```

The model proposes; code disposes. Nothing the model writes reaches the database except through a tool, and at
D2+ every write tool asks the policy engine first. Approval requests and the audit log live in the same database,
so the CLI, the API and the graph see one queue.

## Agent state graph

```mermaid
stateDiagram-v2
  [*] --> agent
  agent --> tools: tool_use blocks
  agent --> [*]: final text (status=done)
  agent --> finalize: max steps / refusal
  tools --> agent: all calls done (results appended)
  tools --> approval_gate: tier-2 write at D3 (needs_approval)
  tools --> finalize: same call 3× (loop)
  approval_gate --> tools: approved → execute with idempotency key / rejected → error result
  finalize --> [*]: escalation message (+ escalate_to_human for loop / max steps)
```

State (`AgentState` in `agent/graph.py`): append-only `messages` and `events`; `session_id`, `trace_id`,
`defense_level`, `verified_customer_id`; `pending_calls`, `tool_results`, `pending_approval`; `step_count`,
`call_counts` (loop detection); cumulative `usage` (tokens, model seconds); `status`, `final_text`.

## Approval sequence (D3)

```mermaid
sequenceDiagram
  actor C as Customer
  participant G as Graph (tools node)
  participant PE as Policy engine
  participant Q as approvals table
  participant AG as approval_gate
  actor L as Support lead
  C->>G: "my ₹14,999 phone is defective"
  G->>PE: preview issue_refund(14999)
  PE-->>G: needs_approval [REF-4]
  G->>Q: insert pending request (idempotent on tool_use_id)
  G->>AG: route
  AG-->>C: interrupt() — run pauses, checkpoint saved
  Note over AG: process may restart here; state is in SQLite
  L->>AG: Command(resume={decision: approve, note})
  AG->>AG: node re-runs from top (pure), interrupt() returns decision
  AG->>Q: record decision + audit_log (human)
  AG->>PE: execute issue_refund with approved_by=human (deny rules still apply)
  AG->>G: tool_result appended → agent writes the reply
  G-->>C: "Refund of ₹14,999 approved and issued"
```

## Defense levels in code

| Level | Where it lives |
|---|---|
| D0 | `prompts.py` (policy summary only); tools return raw text; `tools/write.py::_guard` skips checks |
| D1 | `security/spotlight.py::wrap_untrusted` on reviews, delivery notes, tickets; `prompts.INSTRUCTION_HIERARCHY` |
| D2 | `_guard` → `policy_engine.evaluate`; identity gate in read tools (`tools/read.py::_gate_customer`); `resolve_refund_upi` (destinations from DB); `security/masking.py` |
| D3 | `graph.py::tools` previews writes; `needs_approval` → `approval_gate` + `hitl/approvals.py` |

## Explaining it in five sentences

TrustDesk is a LangGraph state machine where a local LLM (qwen3:8b on Ollama) chooses tools and plain Python decides whether they
run. Every write tool calls a deterministic policy engine that encodes the written support policy, so refunds
cannot exceed what was paid, go to unverified UPI IDs, or touch another customer's order, whatever the model was
told. Actions the policy marks as needing a human pause the graph with `interrupt()`, persist in a SQLite
checkpoint, and resume exactly once after approval thanks to idempotency keys. Text from reviews, delivery notes
and old tickets is spotlighted as data, and defense levels D0–D3 let the eval harness measure what each layer buys.
The harness replays 36 validated tasks with a simulated customer and grades only database state and literal output,
never with an LLM judge.
