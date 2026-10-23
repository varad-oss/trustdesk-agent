"""The TrustDesk agent as an explicit LangGraph state machine.

    START -> agent --(tool calls)--> tools --(tier-2 write at D3)--> approval_gate --> tools
               ^                       |                                              |
               +-------(results)-------+                     finalize <--(loop/max/refusal)
    agent --(final text)--> END

Design rules that keep this correct:
- Messages are append-only (thinking blocks stay valid; prompt cache stays warm).
- The system prompt and tool list are constant per conversation.
- `approval_gate` re-runs from its first line on resume, so everything before
  `interrupt()` is side-effect free; the write happens after approval, under an
  idempotency key derived from the model's tool-use ID.
- Nodes close over `env`, `model`, `retriever` and `tracer` (not checkpointed);
  graph state holds only JSON-serialisable data.
"""

from __future__ import annotations

import json
import operator
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from trustdesk.agent.model import ChatModel
from trustdesk.agent.prompts import system_prompt
from trustdesk.config import DefenseLevel
from trustdesk.env.store import Env
from trustdesk.hitl import approvals
from trustdesk.hitl.approvals import ApprovalResponse
from trustdesk.observability.pricing import add_usage, cost_usd
from trustdesk.observability.tracing import Tracer
from trustdesk.tools import WRITE_TOOLS, ToolContext, execute_tool, tool_specs, validate_args
from trustdesk.tools.base import Retriever, err
from trustdesk.tools.write import preview
from trustdesk.types import Outcome, Session

LOOP_LIMIT = 3
FINAL_MESSAGES = {
    "max_steps": "I'm sorry, this is taking longer than it should. I'm escalating your case to a human support lead, who will contact you within one working day (policy ESC-1).",
    "loop": "I'm having trouble completing this request. I'm escalating your case to a human support lead, who will contact you within one working day (policy ESC-1).",
    "refusal": "I'm not able to help with that request here. A human support lead can follow up if you need more help (policy ESC-1).",
}


class AgentState(TypedDict, total=False):
    messages: Annotated[list[dict[str, Any]], operator.add]
    events: Annotated[list[dict[str, Any]], operator.add]
    session_id: str
    trace_id: str
    defense_level: int
    verified_customer_id: str | None
    pending_calls: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    pending_approval: dict[str, Any] | None
    step_count: int
    call_counts: dict[str, int]
    usage: dict[str, float]
    status: str
    final_text: str | None


def _session(state: AgentState) -> Session:
    return Session(
        session_id=state["session_id"],
        defense_level=DefenseLevel(state.get("defense_level", 3)),
        verified_customer_id=state.get("verified_customer_id"),
        trace_id=state.get("trace_id"),
    )


def _tool_result(call: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": call["id"], "content": json.dumps(result, ensure_ascii=False, default=str)}


def _justification(messages: list[dict[str, Any]], tool_use_id: str) -> str:
    """The agent's own text in the assistant turn that proposed this call."""
    for m in reversed(messages):
        if m["role"] == "assistant" and isinstance(m["content"], list) and any(b.get("id") == tool_use_id for b in m["content"]):
            return " ".join(b.get("text", "") for b in m["content"] if b.get("type") == "text").strip()
    return ""


def conversation_excerpt(messages: list[dict[str, Any]], limit: int = 6) -> list[dict[str, str]]:
    """Last few human-readable turns (customer text and agent text), for approvers."""
    out: list[dict[str, str]] = []
    for m in messages:
        if isinstance(m["content"], str):
            out.append({"role": "customer" if m["role"] == "user" else "agent", "text": m["content"]})
            continue
        text = " ".join(b.get("text", "") for b in m["content"] if b.get("type") == "text").strip()
        if text:
            out.append({"role": "customer" if m["role"] == "user" else "agent", "text": text})
    return out[-limit:]


def build_graph(
    env: Env,
    model: ChatModel,
    retriever: Retriever | None = None,
    tracer: Tracer | None = None,
    checkpointer: Any = None,
    max_steps: int = 25,
):
    """Compile the agent graph. One graph per (env, model); one thread per conversation."""
    tracer = tracer or Tracer(None, use_langfuse=False)
    specs = tool_specs()
    prompts = {level: system_prompt(level) for level in DefenseLevel}

    def agent(state: AgentState) -> dict[str, Any]:
        if state.get("step_count", 0) >= max_steps:
            return {"status": "max_steps"}
        level = DefenseLevel(state.get("defense_level", 3))
        resp = model.complete(prompts[level], specs, state["messages"])
        cost = cost_usd(model.name, resp.usage)
        event = tracer.record(
            state["trace_id"],
            "model",
            model.name,
            {"usage": resp.usage, "cost_usd": cost, "latency_s": resp.latency_s, "stop_reason": resp.stop_reason, "request_id": resp.request_id},
        )
        content = resp.content or [{"type": "text", "text": "(no response)"}]
        update: dict[str, Any] = {
            "messages": [{"role": "assistant", "content": content}],
            "events": [event],
            "usage": add_usage(state.get("usage", {}), {**resp.usage, "cost_usd": cost, "latency_s": resp.latency_s, "model_calls": 1}),
            "step_count": state.get("step_count", 0) + 1,
        }
        tool_uses = [b for b in content if b.get("type") == "tool_use"]
        if resp.stop_reason == "refusal":
            update["status"] = "refusal"
        elif tool_uses:
            update["pending_calls"] = tool_uses
            update["tool_results"] = []
        else:
            update["status"] = "done"
            update["final_text"] = "".join(b.get("text", "") for b in content if b.get("type") == "text")
        return update

    def tools(state: AgentState) -> dict[str, Any]:
        session = _session(state)
        ctx = ToolContext(env, session, retriever)
        calls = list(state.get("pending_calls", []))
        results = list(state.get("tool_results", []))
        counts = dict(state.get("call_counts", {}))
        events: list[dict[str, Any]] = []
        status = state.get("status", "running")

        while calls:
            call = calls[0]
            signature = f"{call['name']}:{json.dumps(call.get('input', {}), sort_keys=True)}"
            counts[signature] = counts.get(signature, 0) + 1
            if counts[signature] >= LOOP_LIMIT:
                for c in calls:
                    results.append(_tool_result(c, err("LOOP_DETECTED", "Repeated identical call; stopping.")))
                events.append(tracer.record(state["trace_id"], "guard", "loop_detected", {"signature": signature}))
                calls, status = [], "loop"
                break

            if session.defense_level >= DefenseLevel.D3 and call["name"] in WRITE_TOOLS:
                parsed = validate_args(call["name"], call.get("input", {}))
                if not isinstance(parsed, dict):
                    decision = preview(ctx, call["name"], parsed)
                    if decision.outcome == Outcome.NEEDS_APPROVAL:
                        approval_id = approvals.create_request(
                            env,
                            session.session_id,
                            call["id"],
                            call["name"],
                            parsed.model_dump(),
                            decision,
                            _justification(state["messages"], call["id"]),
                            conversation_excerpt(state["messages"]),
                        )
                        counts[signature] -= 1  # the gate will resolve this call; don't count it twice
                        return {
                            "pending_calls": calls,
                            "tool_results": results,
                            "call_counts": counts,
                            "verified_customer_id": session.verified_customer_id,
                            "events": events,
                            "pending_approval": {
                                "approval_id": approval_id,
                                "session_id": session.session_id,
                                "action": call["name"],
                                "params": parsed.model_dump(),
                                "policy_decision": decision.to_dict(),
                                "justification": _justification(state["messages"], call["id"]),
                                "excerpt": conversation_excerpt(state["messages"]),
                            },
                        }

            result = execute_tool(ctx, call["name"], call.get("input", {}), idempotency_key=f"{session.session_id}:{call['id']}")
            events.append(
                tracer.record(
                    state["trace_id"],
                    "tool",
                    call["name"],
                    {"input": call.get("input", {}), "ok": result.get("ok"), "error_code": result.get("error_code"), "result_chars": len(json.dumps(result, default=str))},
                )
            )
            results.append(_tool_result(call, result))
            calls.pop(0)

        return {
            "pending_calls": [],
            "tool_results": [],
            "messages": [{"role": "user", "content": results}],
            "verified_customer_id": session.verified_customer_id,
            "call_counts": counts,
            "events": events,
            "status": status,
        }

    def approval_gate(state: AgentState) -> dict[str, Any]:
        request = state["pending_approval"]
        assert request is not None
        # Everything above this line must stay side-effect free: the node re-runs on resume.
        response = interrupt(request, response_schema=ApprovalResponse)
        if isinstance(response, dict):
            response = ApprovalResponse.model_validate(response)
        approvals.record_decision(env, request["approval_id"], response, state.get("trace_id"))

        call = state["pending_calls"][0]
        session = _session(state)
        if response.decision == "approve":
            ctx = ToolContext(env, session, retriever, approved_by=f"human:{response.reviewer}")
            result = execute_tool(ctx, call["name"], call.get("input", {}), idempotency_key=f"{session.session_id}:{call['id']}")
        else:
            result = err(
                "APPROVAL_REJECTED",
                "A support lead reviewed and rejected this action." + (f" Note: {response.note}" if response.note else ""),
                "Tell the customer the outcome; do not retry the same action.",
                request["policy_decision"]["rule_ids"],
            )
        event = tracer.record(
            state["trace_id"], "approval", call["name"], {"approval_id": request["approval_id"], "decision": response.decision, "ok": result.get("ok")}
        )
        return {
            "pending_calls": state["pending_calls"][1:],
            "tool_results": list(state.get("tool_results", [])) + [_tool_result(call, result)],
            "pending_approval": None,
            "events": [event],
        }

    def finalize(state: AgentState) -> dict[str, Any]:
        status = state.get("status", "done")
        text = FINAL_MESSAGES.get(status, "")
        update: dict[str, Any] = {"final_text": text}
        if status in {"max_steps", "loop"}:
            ctx = ToolContext(env, _session(state), retriever)
            execute_tool(
                ctx,
                "escalate_to_human",
                {"summary": f"Automatic escalation: agent stopped ({status}).", "category": "other"},
                idempotency_key=f"{state['session_id']}:auto-escalate:{len(state['messages'])}",
            )
        if state["messages"][-1]["role"] != "assistant":
            update["messages"] = [{"role": "assistant", "content": [{"type": "text", "text": text}]}]
        return update

    def after_agent(state: AgentState) -> str:
        if state.get("status") in {"max_steps", "refusal"}:
            return "finalize"
        if state.get("status") == "done":
            return END
        return "tools"

    def after_tools(state: AgentState) -> str:
        if state.get("pending_approval"):
            return "approval_gate"
        if state.get("status") == "loop":
            return "finalize"
        return "agent"

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", tools)
    graph.add_node("approval_gate", approval_gate)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", after_agent, ["tools", "finalize", END])
    graph.add_conditional_edges("tools", after_tools, ["approval_gate", "finalize", "agent"])
    graph.add_edge("approval_gate", "tools")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
