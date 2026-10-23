"""The real graph driven by a scripted fake model (no network)."""

from __future__ import annotations

import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from tests.conftest import BASE_PATCH
from trustdesk.agent.demo_model import RuleBasedDemoModel
from trustdesk.agent.graph import build_graph
from trustdesk.agent.model import ScriptedModel
from trustdesk.agent.runtime import Conversation, sqlite_checkpointer
from trustdesk.config import DefenseLevel
from trustdesk.env import fresh_env
from trustdesk.hitl import approvals


def call(name, args, cid):
    return {"type": "tool_use", "id": cid, "name": name, "input": args}


def text(t):
    return {"type": "text", "text": t}


VERIFY = call("find_and_verify_customer", {"email": "asha@example.in", "order_id": "O-BIG"}, "t1")
RETURN = call("create_return", {"order_id": "O-BIG", "skus": ["ELE-PHONE-02"], "reason": "defective"}, "t2")
BIG_REFUND = call("issue_refund", {"order_id": "O-BIG", "amount_inr": 14999, "destination": "original_method", "reason": "RET-4"}, "t3")
BIG_REFUND_SCRIPT = [[VERIFY], [text("Creating the return."), RETURN], [text("Refund needs approval (REF-4)."), BIG_REFUND], [text("Final answer.")]]


def convo(model, level=DefenseLevel.D3, env=None, checkpointer=None, max_steps=25):
    env = env or fresh_env(BASE_PATCH)
    graph = build_graph(env, model, checkpointer=checkpointer or InMemorySaver(), max_steps=max_steps)
    return env, Conversation(graph, "sess-1", level)


def tool_results(state):
    out = {}
    for m in state["messages"]:
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                out[b["tool_use_id"]] = json.loads(b["content"])
    return out


def test_write_before_verification_is_refused():
    env, c = convo(ScriptedModel([[call("cancel_order", {"order_id": "O-PLACED", "reason": "x"}, "a")], [text("Please verify.")]]))
    r = c.send("cancel O-PLACED")
    assert r.reply == "Please verify."
    assert tool_results(c.state())["a"]["error_code"] == "NOT_VERIFIED"
    assert env.order("O-PLACED")["status"] == "placed"


def test_tier2_pauses_and_never_executes_without_approval():
    env, c = convo(ScriptedModel(BIG_REFUND_SCRIPT))
    r = c.send("phone defective")
    assert r.status == "awaiting_approval"
    assert r.pending_approval["policy_decision"]["rule_ids"] == ["REF-4"]
    assert r.pending_approval["justification"] == "Refund needs approval (REF-4)."
    assert env.refunds_for("O-BIG") == []
    assert approvals.list_requests(env)[0]["action"] == "issue_refund"
    with pytest.raises(RuntimeError):
        c.send("hello?")  # chat is blocked while an approval is pending


def test_approve_resumes_and_executes_once():
    env, c = convo(ScriptedModel(BIG_REFUND_SCRIPT))
    c.send("phone defective")
    r = c.resume("approve", "looks fine")
    assert r.reply == "Final answer."
    refunds = env.refunds_for("O-BIG")
    assert len(refunds) == 1 and refunds[0]["approved_by"] == "human:support-lead"
    assert approvals.list_requests(env, "approved")[0]["reviewer_note"] == "looks fine"
    actors = [row["actor"] for row in env.all("SELECT actor FROM audit_log WHERE action='issue_refund'")]
    assert actors == ["human", "agent"]


def test_reject_path_returns_error_to_model():
    env, c = convo(ScriptedModel(BIG_REFUND_SCRIPT))
    c.send("phone defective")
    c.resume("reject", "suspicious")
    assert env.refunds_for("O-BIG") == []
    assert tool_results(c.state())["t3"]["error_code"] == "APPROVAL_REJECTED"


def test_d2_has_no_gate_and_returns_approval_required():
    env, c = convo(ScriptedModel(BIG_REFUND_SCRIPT), level=DefenseLevel.D2)
    r = c.send("phone defective")
    assert r.status == "done" and env.refunds_for("O-BIG") == []
    assert tool_results(c.state())["t3"]["error_code"] == "APPROVAL_REQUIRED"


def test_rubber_stamp_cannot_approve_denied_action():
    """Deny decisions never reach the approval gate: the engine blocks them first."""
    bad = call("issue_refund", {"order_id": "O-BIG", "amount_inr": 99999, "destination": "original_method", "reason": "x"}, "t3")
    env, c = convo(ScriptedModel([[VERIFY], [RETURN], [bad], [text("done")]]))
    r = c.send("refund")
    assert r.pending_approval is None and env.refunds_for("O-BIG") == []
    assert tool_results(c.state())["t3"]["rule_ids"] == ["REF-3"]


def test_pending_approval_survives_restart(tmp_path):
    db = tmp_path / "ckpt.db"
    env = fresh_env(BASE_PATCH)
    _, c1 = convo(ScriptedModel(BIG_REFUND_SCRIPT), env=env, checkpointer=sqlite_checkpointer(db))
    assert c1.send("phone defective").status == "awaiting_approval"
    c1.graph.checkpointer.conn.close()  # "process dies"

    model2 = ScriptedModel([[text("Final answer after restart.")]])
    _, c2 = convo(model2, env=env, checkpointer=sqlite_checkpointer(db))
    assert c2.pending_approval()["action"] == "issue_refund"
    r = c2.resume("approve")
    assert r.reply == "Final answer after restart."
    assert len(env.refunds_for("O-BIG")) == 1


def test_resume_twice_does_not_double_refund(tmp_path):
    """Crash after the approved write but before the checkpoint: replay hits the idempotency key."""
    env, c = convo(ScriptedModel(BIG_REFUND_SCRIPT))
    c.send("phone defective")
    c.resume("approve")
    from trustdesk.tools import ToolContext, execute_tool
    from trustdesk.types import Session

    replay = execute_tool(ToolContext(env, Session("sess-1", DefenseLevel.D3, "A"), approved_by="human:x"), "issue_refund", BIG_REFUND["input"], "sess-1:t3")
    assert replay["idempotent_replay"] and len(env.refunds_for("O-BIG")) == 1


def test_loop_detection_escalates():
    same = call("get_order", {"order_id": "O-ELEC"}, "x")
    model = ScriptedModel(lambda msgs: [{**same, "id": f"x{len(msgs)}"}])
    env, c = convo(model)
    r = c.send("hi")
    assert r.status == "loop" and "escalating" in r.reply
    assert env.one("SELECT COUNT(*) n FROM escalations")["n"] == 1


def test_max_steps_stops_gracefully():
    model = ScriptedModel(lambda msgs: [call("search_policy", {"query": f"q{len(msgs)}"}, f"s{len(msgs)}")])
    env, c = convo(model, max_steps=4)
    r = c.send("hi")
    assert r.status == "max_steps" and "escalating" in r.reply
    assert model.calls == 4
    assert c.state()["messages"][-1]["role"] == "assistant"


def test_tool_error_recovery():
    model = ScriptedModel([[call("issue_refund", {"order_id": "O-ELEC"}, "e1")], [VERIFY], [text("Recovered.")]])
    _, c = convo(model)
    assert c.send("x").reply == "Recovered."
    assert tool_results(c.state())["e1"]["error_code"] == "INVALID_ARGUMENTS"


def test_multi_turn_keeps_verification_and_appends_only():
    model = ScriptedModel([[VERIFY], [text("Verified.")], [call("get_order", {"order_id": "O-ELEC"}, "g")], [text("Here it is.")]])
    _, c = convo(model)
    c.send("hi")
    before = list(c.state()["messages"])
    c.send("show order")
    after = c.state()["messages"]
    assert after[: len(before)] == before  # earlier messages never edited
    assert tool_results(c.state())["g"]["ok"]


def test_refusal_stop_reason_finalizes():
    class Refuser:
        name = "fake"

        def complete(self, system, tools, messages):
            from trustdesk.agent.model import ModelResponse

            return ModelResponse([], "refusal", "fake")

    _, c = convo(Refuser())
    r = c.send("x")
    assert r.status == "refusal" and "not able" in r.reply


def test_demo_model_end_to_end_ret4():
    from trustdesk.cli import DEMO_PATCH

    env, c = convo(RuleBasedDemoModel(), env=fresh_env(DEMO_PATCH))
    r = c.send("My earbuds arrived damaged. priya.s@example.in SS-10482")
    assert "₹2549" in r.reply
    assert env.refunds_for("SS-10482")[0]["amount_inr"] == 2549
