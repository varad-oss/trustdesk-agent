"""Episode runner: tasks x defense levels x trials, with isolation, retries and a time estimate.

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
from trustdesk.observability.tracing import Tracer
from trustdesk.retrieval import build_index

RESULTS_DIR = REPO_ROOT / "results"
MAX_CONSECUTIVE_INFRA_FAILURES = 3  # e.g. the model server died: stop instead of failing every remaining episode
CONFIRM_IF_LONGER_THAN_MIN = 20  # local models are free but slow: confirm long runs after a pilot
INFRA_MODULES = ("httpx", "httpcore")


class ModelServerDown(RuntimeError):
    """Raised when episodes keep failing on infrastructure; finished episodes stay on disk."""


def check_model_server(settings: Settings) -> str | None:
    """Return a problem description if the configured local model server can't serve the run."""
    import httpx

    for provider, model in ((settings.provider, settings.model), (settings.simulator_provider, settings.simulator_model)):
        if provider != "ollama":
            continue
        try:
            tags = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=5).json()
        except httpx.HTTPError as exc:
            return f"Ollama is not reachable at {settings.ollama_base_url} ({type(exc).__name__}). Start it with `ollama serve`."
        if not any(m["name"] == model for m in tags.get("models", [])):
            return f"Model {model} is not pulled in Ollama. Run `ollama pull {model}`."
    return None


def completed_episodes(out_dir: Path) -> list[dict[str, Any]]:
    """Episodes already finished cleanly (graded, no error) in a previous attempt of this run."""
    path = out_dir / "episodes.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if "grade" in r and not r.get("error")]


@dataclass
class RunConfig:
    run_id: str
    levels: list[int]
    trials: int
    approver: str = "task"  # task | rubber_stamp
    concurrency: int = 4
    max_attempts: int = 3
    resume: bool = False


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
        "agent_model_s": usage.get("latency_s", 0.0),
        "simulator_model_s": sim_usage.get("latency_s", 0.0),
        "simulator_usage": sim_usage,
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
    done: set[str] = set()
    if config.resume:
        kept = completed_episodes(out_dir)
        done = {r["episode_id"] for r in kept}
        # Drop failed attempts so the report counts each episode once; they are re-run below.
        (out_dir / "episodes.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in kept))
        print(f"Resuming {config.run_id}: {len(done)} episodes already done.")
    config_path = out_dir / "config.json"
    created_at = json.loads(config_path.read_text())["created_at"] if config.resume and config_path.exists() else datetime.now(UTC).isoformat()
    config_path.write_text(
        json.dumps(
            {
                "run_id": config.run_id,
                "created_at": created_at,
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
    jobs = [(t, lvl, k) for lvl in config.levels for t in tasks for k in range(1, config.trials + 1) if f"{t.id}__D{lvl}__t{k}" not in done]
    infra_streak = 0
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
                  f"{rec.get('latency_s', 0):.0f}s {rec.get('error') or ''}", flush=True)
            infra_streak = infra_streak + 1 if rec.get("infra_failed") else 0
            if infra_streak >= MAX_CONSECUTIVE_INFRA_FAILURES:
                for pending in futures:
                    pending.cancel()
                raise ModelServerDown(
                    f"{infra_streak} episodes in a row failed on infrastructure (last: {rec.get('error')}). "
                    f"Fix the model server, then rerun with --resume --run-id {config.run_id}."
                )
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
    resume: bool = False,
) -> None:
    import typer

    settings: Settings = load_settings()
    problem = check_model_server(settings)
    if problem:
        typer.secho(problem, fg="red")
        raise typer.Exit(1)
    if reported and settings.llm_cache_dir is not None:
        raise typer.BadParameter("Reported runs must not use the LLM cache: unset TRUSTDESK_LLM_CACHE_DIR.")
    tasks = load_tasks(task_ids.split(",") if task_ids else None)[:limit]
    level_list = [int(x) for x in levels.split(",")]
    retriever = build_index(settings.retrieval_mode)
    run_id = run_id or f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{settings.provider}"
    n_episodes = len(tasks) * len(level_list) * trials
    uses_model = settings.provider != "fake" or settings.simulator_provider != "fake"
    if uses_model and n_episodes > 6 and not yes and not resume:
        pilot_tasks = tasks[:3]
        typer.echo(f"Pilot: {len(pilot_tasks)} episodes at D{max(level_list)} before the full run of {n_episodes} episodes...")
        pilot_dir = run(pilot_tasks, RunConfig(f"{run_id}-pilot", [max(level_list)], 1, approver, concurrency),
                        lambda: make_model(settings, "agent"), lambda: make_model(settings, "simulator"), retriever)
        recs = [json.loads(line) for line in (pilot_dir / "episodes.jsonl").read_text().splitlines()]
        per_ep = sum(r.get("latency_s", 0) for r in recs) / max(len(recs), 1)
        minutes = per_ep * n_episodes / 60 / max(concurrency, 1)
        typer.echo(f"Measured {per_ep:.0f}s per episode (cost $0, local model). Estimated wall-clock for {n_episodes} episodes "
                   f"at concurrency {concurrency}: {minutes:.0f} min (Ollama may serialise requests; see OLLAMA_NUM_PARALLEL).")
        if minutes > CONFIRM_IF_LONGER_THAN_MIN and not typer.confirm("Proceed with the full run?"):
            raise typer.Exit()
    snapshot = settings.model_dump()
    out = run(tasks, RunConfig(run_id, level_list, trials, approver, concurrency, resume=resume),
              lambda: make_model(settings, "agent"), lambda: make_model(settings, "simulator"), retriever,
              {**snapshot, "reported": reported, "retrieval_mode_effective": retriever.mode})
    typer.echo(f"Results in {out.relative_to(REPO_ROOT)}. Run `trustdesk eval report --run-id {run_id}`.")
