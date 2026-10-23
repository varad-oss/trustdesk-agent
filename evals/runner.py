"""Episode runner: tasks x defense levels x trials, with isolation, retries and cost control.

Each episode gets a fresh in-memory store and its own graph thread. The simulated customer
talks to the agent until ###STOP### or 12 customer turns; tier-2 approvals are answered by a
simulated approver (the task's `approver_decision`, or approve-everything in rubber-stamp
mode). One JSONL trajectory per episode goes to results/<run_id>/trajectories/.
"""

from __future__ import annotations

import json
import random
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from evals.graders import EpisodeView, grade_episode
from evals.schema import Task, load_tasks
from evals.simulator import MAX_CUSTOMER_TURNS, CustomerSimulator
from trustdesk.agent.demo_model import make_model
from trustdesk.agent.graph import build_graph
from trustdesk.agent.model import ChatModel
from trustdesk.agent.runtime import Conversation
from trustdesk.config import REPO_ROOT, DefenseLevel, Settings, load_settings
from trustdesk.env import fresh_env
from trustdesk.observability.pricing import cost_usd
from trustdesk.observability.tracing import Tracer
from trustdesk.retrieval import build_index

RESULTS_DIR = REPO_ROOT / "results"
PAID_CALL_CONFIRM_THRESHOLD = 50
INFRA_MODULES = ("anthropic", "httpx", "httpcore", "openai")


@dataclass
class RunConfig:
    run_id: str
    levels: list[int]
    trials: int
    approver: str = "task"  # task | rubber_stamp
    concurrency: int = 4
    max_attempts: int = 3


def _is_infra_error(exc: BaseException) -> bool:
    return type(exc).__module__.split(".")[0] in INFRA_MODULES or isinstance(exc, (TimeoutError, ConnectionError))


def approver_decision(task: Task, mode: str) -> str:
    if mode == "rubber_stamp":
        return "approve"
    return task.expected.approver_decision or "reject"


def _tool_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: dict[str, dict] = {}
    for m in messages:
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if b.get("type") == "tool_result":
                    results[b["tool_use_id"]] = json.loads(b["content"])
    calls = []
    for m in messages:
        if m["role"] == "assistant":
            for b in m["content"]:
                if b.get("type") == "tool_use":
                    calls.append({"name": b["name"], "input": b["input"], "result": results.get(b["id"], {})})
    return calls


def _agent_texts(messages: list[dict[str, Any]]) -> list[str]:
    return [
        "".join(b.get("text", "") for b in m["content"] if b.get("type") == "text")
        for m in messages
        if m["role"] == "assistant" and isinstance(m["content"], list)
    ]


def run_episode(
    task: Task,
    level: int,
    trial: int,
    agent_model: ChatModel,
    sim_model: ChatModel,
    retriever: Any,
    out_dir: Path,
    approver: str = "task",
) -> dict[str, Any]:
    """Run one conversation; never raises (errors are recorded on the episode)."""
    episode_id = f"{task.id}__D{level}__t{trial}"
    env = fresh_env(task.initial_state_patch)
    tracer = Tracer(out_dir / "traces")
    graph = build_graph(env, agent_model, retriever=retriever, tracer=tracer, checkpointer=InMemorySaver())
    convo = Conversation(graph, f"ep-{uuid.uuid4().hex[:8]}", DefenseLevel(level))
    sim = CustomerSimulator(sim_model, task)
    transcript: list[dict[str, Any]] = []
    approvals: list[dict[str, Any]] = []
    error: str | None = None
    started = time.perf_counter()
    turns = 0
    try:
        for turns in range(1, MAX_CUSTOMER_TURNS + 1):
            text, stopped = sim.next_message()
            if text:
                transcript.append({"role": "customer", "text": text})
            if stopped and (not text or turns > 1):
                break
            result = convo.send(text)
            while result.pending_approval is not None:
                decision = approver_decision(task, approver)
                approvals.append({"request": result.pending_approval, "decision": decision})
                result = convo.resume(decision, note=f"simulated approver ({approver})", reviewer="sim-approver")
            reply = result.reply or ""
            transcript.append({"role": "agent", "text": reply, "status": result.status})
            if stopped:
                break
            sim.observe(reply)
    except Exception as exc:  # noqa: BLE001 - recorded, classified, and retried by the caller
        error = f"{type(exc).__module__}.{type(exc).__name__}: {exc}"
        if _is_infra_error(exc):
            raise
    state = convo.state()
    messages = state.get("messages", [])
    view = EpisodeView(_agent_texts(messages), _tool_calls(messages), state.get("verified_customer_id"), error)
    grade = grade_episode(env, task, view)
    usage = state.get("usage", {})
    sim_usage = sim.usage()
    record = {
        "episode_id": episode_id,
        "task_id": task.id,
        "category": task.category,
        "defense_level": level,
        "trial": trial,
        "model": agent_model.name,
        "simulator_model": sim_model.name,
        "approver": approver,
        "trace_id": state.get("trace_id"),
        "customer_turns": turns,
        "tool_calls": len(view.tool_calls),
        "status": state.get("status"),
        "usage": usage,
        "agent_cost_usd": usage.get("cost_usd", 0.0),
        "simulator_cost_usd": cost_usd(sim_model.name, sim_usage),
        "latency_s": time.perf_counter() - started,
        "error": error,
        "grade": grade.to_dict(),
    }
    traj = out_dir / "trajectories" / f"{episode_id}.jsonl"
    traj.parent.mkdir(parents=True, exist_ok=True)
    with open(traj, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "meta", **{k: v for k, v in record.items() if k != "grade"}}, ensure_ascii=False, default=str) + "\n")
        for line in transcript:
            fh.write(json.dumps({"type": "turn", **line}, ensure_ascii=False) + "\n")
        for m in messages:
            fh.write(json.dumps({"type": "message", **m}, ensure_ascii=False, default=str) + "\n")
        for e in state.get("events", []):
            fh.write(json.dumps({"type": "event", **e}, ensure_ascii=False, default=str) + "\n")
        for a in approvals:
            fh.write(json.dumps({"type": "approval", **a}, ensure_ascii=False, default=str) + "\n")
        fh.write(json.dumps({"type": "grade", **grade.to_dict()}, ensure_ascii=False) + "\n")
    return record


def run_with_retries(fn: Callable[[], dict[str, Any]], episode_id: str, max_attempts: int = 3) -> dict[str, Any]:
    """Retry whole episodes on infrastructure errors with exponential backoff and jitter."""
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if attempt == max_attempts or not _is_infra_error(exc):
                return {"episode_id": episode_id, "error": f"{type(exc).__name__}: {exc}", "infra_failed": True}
            time.sleep(min(30.0, 2**attempt) + random.uniform(0, 1))
    raise AssertionError("unreachable")


def run(
    tasks: list[Task],
    config: RunConfig,
    agent_factory: Callable[[], ChatModel],
    sim_factory: Callable[[], ChatModel],
    retriever: Any,
    settings_snapshot: dict[str, Any] | None = None,
) -> Path:
    out_dir = RESULTS_DIR / config.run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "config.json").write_text(
        json.dumps(
            {
                "run_id": config.run_id,
                "created_at": datetime.now(UTC).isoformat(),
                "levels": config.levels,
                "trials": config.trials,
                "approver": config.approver,
                "task_ids": [t.id for t in tasks],
                "settings": settings_snapshot or {},
            },
            indent=2,
            default=str,
        )
    )
    jobs = [(t, lvl, k) for lvl in config.levels for t in tasks for k in range(1, config.trials + 1)]
    with ThreadPoolExecutor(max_workers=config.concurrency) as pool, open(out_dir / "episodes.jsonl", "a", encoding="utf-8") as sink:
        futures = {
            pool.submit(
                run_with_retries,
                lambda t=t, lvl=lvl, k=k: run_episode(t, lvl, k, agent_factory(), sim_factory(), retriever, out_dir, config.approver),
                f"{t.id}__D{lvl}__t{k}",
                config.max_attempts,
            ): (t, lvl, k)
            for t, lvl, k in jobs
        }
        for i, fut in enumerate(as_completed(futures), 1):
            rec = fut.result()
            sink.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            sink.flush()
            g = rec.get("grade", {})
            print(f"[{i}/{len(jobs)}] {rec['episode_id']}: success={g.get('success')} attack={g.get('attack_success')} "
                  f"${rec.get('agent_cost_usd', 0) + rec.get('simulator_cost_usd', 0):.4f} {rec.get('error') or ''}")
    return out_dir


def cli_run(
    levels: str,
    trials: int,
    task_ids: str | None,
    limit: int | None,
    concurrency: int,
    approver: str,
    run_id: str | None,
    reported: bool,
    yes: bool,
) -> None:
    import typer

    settings: Settings = load_settings()
    if reported and settings.llm_cache_dir is not None:
        raise typer.BadParameter("Reported runs must not use the LLM cache: unset TRUSTDESK_LLM_CACHE_DIR.")
    tasks = load_tasks(task_ids.split(",") if task_ids else None)[:limit]
    level_list = [int(x) for x in levels.split(",")]
    retriever = build_index(settings.retrieval_mode)
    run_id = run_id or f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{settings.provider}"
    n_episodes = len(tasks) * len(level_list) * trials
    paid = settings.provider != "fake" or settings.simulator_provider != "fake"
    est_calls = n_episodes * 8  # ~6 agent + ~2 simulator calls per episode; refined by the pilot
    if paid and est_calls > PAID_CALL_CONFIRM_THRESHOLD and not yes:
        pilot_tasks = tasks[:3]
        typer.echo(f"Pilot: {len(pilot_tasks)} episodes at D{max(level_list)} before the full run of {n_episodes} episodes...")
        pilot_dir = run(pilot_tasks, RunConfig(f"{run_id}-pilot", [max(level_list)], 1, approver, concurrency),
                        lambda: make_model(settings, "agent"), lambda: make_model(settings, "simulator"), retriever)
        recs = [json.loads(line) for line in (pilot_dir / "episodes.jsonl").read_text().splitlines()]
        per_ep = sum(r.get("agent_cost_usd", 0) + r.get("simulator_cost_usd", 0) for r in recs) / max(len(recs), 1)
        typer.echo(f"Measured cost per episode: ${per_ep:.4f}. Extrapolated total for {n_episodes} episodes: ${per_ep * n_episodes:.2f}")
        if not typer.confirm("Proceed with the full run?"):
            raise typer.Exit()
    snapshot = settings.model_dump(exclude={"openai_api_key"})
    out = run(tasks, RunConfig(run_id, level_list, trials, approver, concurrency),
              lambda: make_model(settings, "agent"), lambda: make_model(settings, "simulator"), retriever,
              {**snapshot, "reported": reported, "retrieval_mode_effective": retriever.mode})
    typer.echo(f"Results in {out.relative_to(REPO_ROOT)}. Run `trustdesk eval report --run-id {run_id}`.")
