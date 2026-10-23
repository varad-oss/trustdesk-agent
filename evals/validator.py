"""Task validator: proves each task is solvable, and that what it forbids is forbidden.

For every task:
1. Replay the hand-written golden action sequence through the REAL graph at D3 (a scripted
   model emits the golden tool calls; the approver answers with the task's
   `approver_decision`). The expected end state must hold, no forbidden action may run,
   no invariant may break, and the attack goal must not be reached.
2. Every step marked `approve: true` must actually hit the approval gate.
3. Each `forbidden_probe` must NOT be allowed by the policy engine (D2, verified as given).
4. A do-nothing baseline: if the assertions already hold with no actions, the task only
   discriminates through citations/mentions/forbidden actions; flag it if it has none.

Writes results/validation.json. Exit code 1 if any task fails.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from evals.graders import EpisodeView, check_assertion, grade_episode
from evals.schema import Task, load_tasks
from trustdesk.agent.graph import build_graph
from trustdesk.agent.model import ScriptedModel
from trustdesk.agent.runtime import Conversation
from trustdesk.config import REPO_ROOT, DefenseLevel
from trustdesk.env import fresh_env
from trustdesk.policy_engine import evaluate
from trustdesk.retrieval import PolicyIndex
from trustdesk.types import Outcome, Session

RESULTS = REPO_ROOT / "results" / "validation.json"


def _golden_model(task: Task) -> ScriptedModel:
    steps = task.golden_actions

    def script(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        done = sum(1 for m in messages if m["role"] == "assistant" and any(b.get("type") == "tool_use" for b in m["content"]))
        if done < len(steps):
            s = steps[done]
            return [{"type": "tool_use", "id": f"golden_{done}", "name": s.tool, "input": s.args}]
        return [{"type": "text", "text": "golden replay finished"}]

    return ScriptedModel(script)


def validate_task(task: Task, index: PolicyIndex) -> dict[str, Any]:
    problems: list[str] = []
    warnings: list[str] = []
    env = fresh_env(task.initial_state_patch)
    graph = build_graph(env, _golden_model(task), retriever=index, checkpointer=InMemorySaver())
    convo = Conversation(graph, f"validate-{task.id}", DefenseLevel.D3)
    result = convo.send("(golden replay)")
    interrupts = 0
    while result.pending_approval is not None:
        interrupts += 1
        decision = task.expected.approver_decision or "reject"
        result = convo.resume(decision, note="validator")

    expected_interrupts = sum(1 for s in task.golden_actions if s.approve)
    if interrupts != expected_interrupts:
        problems.append(f"expected {expected_interrupts} approval interrupts, saw {interrupts}")

    state = convo.state()
    calls = []
    results: dict[str, dict] = {}
    for m in state["messages"]:
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                results[b["tool_use_id"]] = json.loads(b["content"])
    for i, step in enumerate(task.golden_actions):
        res = results.get(f"golden_{i}", {})
        calls.append({"name": step.tool, "input": step.args, "result": res})
        rejected = step.approve and task.expected.approver_decision != "approve"
        if not res.get("ok") and not rejected:
            problems.append(f"golden step {i} {step.tool} failed: {res.get('error_code')} {res.get('message')}")

    # Citations/mentions can't come from a golden replay; supply them so the check covers the rest.
    texts = [" ".join(task.expected.must_cite_rules + task.expected.must_mention)]
    view = EpisodeView(texts, calls, state.get("verified_customer_id"))
    grade = grade_episode(env, task, view)
    problems += [f"assertion failed: {a}" for a in grade.assertions_failed]
    problems += [f"forbidden action executed by golden: {a}" for a in grade.forbidden_executed]
    problems += [f"invariant: {v}" for v in grade.policy_violations]
    if grade.attack_success:
        problems.append("golden run reaches the attack goal")
    if task.expected.expect_escalation is not None and grade.escalated != task.expected.expect_escalation:
        problems.append("escalation expectation not met by golden")

    for probe in task.forbidden_probes:
        decision = evaluate(probe.tool, probe.args, env, Session("probe", DefenseLevel.D2, probe.verified_as))
        if decision.outcome == Outcome.ALLOW:
            problems.append(f"probe {probe.tool} {probe.args} is ALLOWED by policy")

    baseline = fresh_env(task.initial_state_patch)
    if task.expected.db_assertions and all(check_assertion(baseline, a) for a in task.expected.db_assertions):
        if not (task.expected.must_cite_rules or task.expected.must_mention or task.expected.forbidden_actions):
            problems.append("do-nothing baseline passes and nothing else discriminates")
        else:
            warnings.append("do-nothing baseline satisfies db_assertions (refusal-type task; graded on forbidden actions/citations)")
    if task.attack and baseline is not None and task.attack.goal_check.kind == "db":
        from evals.graders import attack_succeeded

        if attack_succeeded(baseline, task.attack.goal_check, EpisodeView([], [], None)):
            problems.append("attack goal already true in the initial state")
    if task.expected.approver_decision and expected_interrupts == 0 and task.category == "hitl":
        warnings.append("hitl task has approver_decision but no golden approval step")

    return {"id": task.id, "category": task.category, "ok": not problems, "problems": problems, "warnings": warnings,
            "golden_steps": len(task.golden_actions), "approval_interrupts": interrupts}


def main() -> int:
    index = PolicyIndex.from_policy(mode="bm25")
    reports = [validate_task(t, index) for t in load_tasks()]
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    summary = {"n_tasks": len(reports), "n_ok": sum(r["ok"] for r in reports), "by_category": {}, "tasks": reports}
    for r in reports:
        summary["by_category"][r["category"]] = summary["by_category"].get(r["category"], 0) + 1
    RESULTS.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    for r in reports:
        mark = "OK  " if r["ok"] else "FAIL"
        print(f"{mark} {r['id']}" + "".join(f"\n       - {p}" for p in r["problems"]) + "".join(f"\n       ~ {w}" for w in r["warnings"]))
    print(f"\n{summary['n_ok']}/{summary['n_tasks']} tasks valid; by category {summary['by_category']} -> {RESULTS.relative_to(REPO_ROOT)}")
    return 0 if summary["n_ok"] == summary["n_tasks"] else 1


if __name__ == "__main__":
    sys.exit(main())
