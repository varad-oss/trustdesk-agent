"""Command-line interface: `trustdesk chat | approvals | eval | seed | demo-tools | serve`."""

from __future__ import annotations

import json
from typing import Any

import httpx
import typer

from trustdesk.config import DefenseLevel, load_settings
from trustdesk.env.demo import DEMO_PATCH

app = typer.Typer(help="TrustDesk: policy-bound support agent for the fictional ShopSetu store.", no_args_is_help=True)
approvals_app = typer.Typer(help="Human approval queue for tier-2 actions.", no_args_is_help=True)
eval_app = typer.Typer(help="Evaluation harness.", no_args_is_help=True)
app.add_typer(approvals_app, name="approvals")
app.add_typer(eval_app, name="eval")


def _print(obj: Any) -> None:
    typer.echo(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


@app.command()
def seed(reseed: bool = typer.Option(False, help="Delete and rebuild the store.")) -> None:
    """Create (or rebuild) the ShopSetu store with seed data plus the demo customers."""
    from trustdesk.env import apply_patch
    from trustdesk.wiring import open_store

    settings = load_settings()
    env = open_store(settings, reseed=reseed)
    if env.customer("DEMO1") is None:
        apply_patch(env, DEMO_PATCH)
    counts = {t: env.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in ("customers", "orders", "order_items", "product_reviews")}
    typer.echo(f"Store at {settings.store_path}: {counts}")
    typer.echo("Demo login: priya.s@example.in + SS-10482 (damaged earbuds) or SS-10483 (cancellable phone).")


@app.command("demo-tools")
def demo_tools() -> None:
    """Call each of the 12 tools once against a fresh in-memory store (defense level D3)."""
    from trustdesk.env import fresh_env
    from trustdesk.retrieval import PolicyIndex
    from trustdesk.tools import ToolContext, execute_tool
    from trustdesk.types import Session

    env = fresh_env(DEMO_PATCH)
    ctx = ToolContext(env, Session("demo-tools", DefenseLevel.D3), PolicyIndex.from_policy(mode="bm25"))
    calls = [
        ("find_and_verify_customer", {"email": "priya.s@example.in", "order_id": "SS-10482"}),
        ("get_order", {"order_id": "SS-10482"}),
        ("list_orders", {"customer_id": "DEMO1"}),
        ("get_product_reviews", {"sku": "ELE-EARBUDS-01", "limit": 2}),
        ("get_ticket_history", {"customer_id": "DEMO1"}),
        ("search_policy", {"query": "earbuds arrived cracked", "k": 2}),
        ("create_return", {"order_id": "SS-10482", "skus": ["ELE-EARBUDS-01"], "reason": "damaged"}),
        ("issue_refund", {"order_id": "SS-10482", "amount_inr": 2549, "destination": "original_method", "reason": "RET-4 damaged"}),
        ("update_shipping_address", {"order_id": "SS-10483", "address": {"line1": "4 Park St", "city": "Kolkata", "state": "West Bengal", "pin": "700016"}}),
        ("cancel_order", {"order_id": "SS-10483", "reason": "Ordered by mistake"}),
        ("grant_goodwill_credit", {"customer_id": "DEMO1", "amount_inr": 100, "reason": "Delay"}),
        ("escalate_to_human", {"summary": "Demo escalation.", "category": "other"}),
    ]
    for name, args in calls:
        typer.secho(f"\n>>> {name}({json.dumps(args, ensure_ascii=False)})", fg="cyan")
        _print(execute_tool(ctx, name, args, idempotency_key=f"demo:{name}"))


@app.command()
def chat(
    defense: int = typer.Option(3, min=0, max=3, help="Defense level D0-D3."),
    session: str | None = typer.Option(None, help="Resume an existing session ID."),
    provider: str | None = typer.Option(None, help="ollama | openai_compat | fake (overrides TRUSTDESK_PROVIDER)."),
) -> None:
    """Chat with TrustDesk in the terminal. Tier-2 actions pause for your approval."""
    from trustdesk.agent.runtime import Conversation
    from trustdesk.wiring import build_runtime

    settings = load_settings()
    if provider:
        settings.provider = provider
    runtime = build_runtime(settings)
    convo = Conversation(runtime.graph, session, DefenseLevel(defense))
    typer.secho(f"TrustDesk [{settings.provider}:{settings.model if settings.provider != 'fake' else 'rule-based demo'}, local, $0] "
                f"session={convo.session_id} defense=D{defense}. Type 'quit' to exit.", fg="green")

    def handle(result) -> None:
        while result.pending_approval is not None:
            req = result.pending_approval
            typer.secho("\n[APPROVAL NEEDED]", fg="yellow", bold=True)
            _print({k: req[k] for k in ("approval_id", "action", "params", "policy_decision", "justification")})
            choice = typer.prompt("approve / reject / defer", default="defer")
            if choice not in {"approve", "reject"}:
                typer.echo(f"Deferred. Later: trustdesk approvals approve {req['approval_id']}")
                raise typer.Exit()
            result = convo.resume(choice, note="decided in CLI chat", reviewer="cli-lead")
        typer.secho(f"agent> {result.reply}", fg="blue")

    if convo.pending_approval() is not None:
        handle(convo._result(0))
    while True:
        text = typer.prompt("you")
        if text.strip().lower() in {"quit", "exit"}:
            break
        try:
            handle(convo.send(text))
        except httpx.ConnectError:
            typer.secho(f"Can't reach Ollama at {settings.ollama_base_url}. Run `ollama serve` and `ollama pull {settings.model}`, "
                        "or use --provider fake.", fg="red")
            raise typer.Exit(1) from None


@approvals_app.command("list")
def approvals_list(all_: bool = typer.Option(False, "--all", help="Include decided requests.")) -> None:
    from trustdesk.hitl import approvals
    from trustdesk.wiring import open_store

    env = open_store(load_settings())
    rows = approvals.list_requests(env, None if all_ else "pending")
    if not rows:
        typer.echo("No approval requests.")
    for r in rows:
        typer.echo(f"#{r['id']} [{r['status']}] session={r['session_id']} {r['action']} {json.dumps(r['params'], ensure_ascii=False)} rules={r['rule_ids']}")


def _decide(approval_id: int, decision: str, note: str) -> None:
    from trustdesk.agent.runtime import Conversation
    from trustdesk.hitl import approvals
    from trustdesk.wiring import build_runtime

    runtime = build_runtime(load_settings())
    row = approvals.get(runtime.env, approval_id)
    if row is None or row["status"] != "pending":
        typer.secho(f"Approval #{approval_id} is not pending.", fg="red")
        raise typer.Exit(1)
    convo = Conversation(runtime.graph, row["session_id"])
    result = convo.resume(decision, note=note, reviewer="cli-lead")
    typer.echo(f"#{approval_id} {decision}d. Agent reply to customer: {result.reply}")


@approvals_app.command("approve")
def approvals_approve(approval_id: int, note: str = typer.Option("", help="Note for the audit log.")) -> None:
    _decide(approval_id, "approve", note)


@approvals_app.command("reject")
def approvals_reject(approval_id: int, note: str = typer.Option("", help="Reason for the audit log.")) -> None:
    _decide(approval_id, "reject", note)


@eval_app.command("validate")
def eval_validate() -> None:
    """Replay every task's golden actions and check its expectations (no model calls)."""
    from evals.validator import main

    raise typer.Exit(main())


@eval_app.command("retrieval")
def eval_retrieval() -> None:
    """Recall@1/@4 and MRR for BM25, dense and hybrid on evals/retrieval_qa.yaml."""
    from evals.retrieval_eval import main

    main()


@eval_app.command("run")
def eval_run(
    levels: str = typer.Option("0,2", help="Comma-separated defense levels."),
    trials: int = typer.Option(1),
    tasks: str | None = typer.Option(None, help="Comma-separated task IDs (default: all)."),
    limit: int | None = typer.Option(None, help="Run only the first N tasks."),
    concurrency: int = typer.Option(4),
    approver: str = typer.Option("task", help="task | rubber_stamp"),
    run_id: str | None = typer.Option(None),
    reported: bool = typer.Option(False, help="A run whose numbers go into docs: asserts the LLM cache is off."),
    yes: bool = typer.Option(False, "--yes", help="Skip the pilot and time confirmation."),
    resume: bool = typer.Option(False, "--resume", help="Continue a run: keep cleanly finished episodes, redo the rest (needs --run-id)."),
) -> None:
    """Run episodes and write results/<run_id>/."""
    from evals.runner import cli_run

    if resume and not run_id:
        raise typer.BadParameter("--resume needs --run-id")
    cli_run(levels, trials, tasks, limit, concurrency, approver, run_id, reported, yes, resume)


@eval_app.command("progress")
def eval_progress(run_id: str, interval: float = typer.Option(5.0, help="Seconds between refreshes.")) -> None:
    """Live progress bar, ETA and per-level scoreboard for a running eval (read-only, lightweight)."""
    from evals.progress import watch

    watch(run_id, interval)


@eval_app.command("report")
def eval_report(run_id: str | None = typer.Option(None, help="Defaults to the latest reported run.")) -> None:
    """Regenerate docs/EVAL_REPORT.md and charts from results/."""
    from evals.report import main

    main(run_id)


@app.command()
def trace(trace_id: str, directory: str | None = typer.Option(None, help="Trace directory (default: TRUSTDESK_TRACE_DIR).")) -> None:
    """Pretty-print one JSONL trace: model calls (tokens, latency), tool calls and approvals."""
    from pathlib import Path

    path = Path(directory or load_settings().trace_dir) / f"{trace_id}.jsonl"
    if not path.exists():
        typer.secho(f"No trace at {path}", fg="red")
        raise typer.Exit(1)
    total = 0.0  # seconds spent in model calls
    for line in path.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e["kind"] == "model":
            u = e.get("usage", {})
            total += e.get("latency_s", 0.0)
            typer.echo(f"MODEL    {e['name']:<28} in={u.get('input_tokens', 0):>6} out={u.get('output_tokens', 0):>5} "
                       f"think={u.get('thinking_tokens', 0):>5} cache_read={u.get('cache_read_input_tokens', 0):>6} "
                       f"{e.get('latency_s', 0):.2f}s stop={e.get('stop_reason')}")
        elif e["kind"] == "tool":
            status = "ok" if e.get("ok") else f"ERR {e.get('error_code')}"
            typer.echo(f"TOOL     {e['name']:<28} {status:<22} {json.dumps(e.get('input', {}), ensure_ascii=False)[:90]}")
        else:
            typer.echo(f"{e['kind'].upper():<8} {e['name']:<28} {json.dumps({k: v for k, v in e.items() if k not in {'ts', 'trace_id', 'kind', 'name'}}, ensure_ascii=False)[:110]}")
    typer.echo(f"total model time {total:.1f}s (local model: $0)")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Run the HTTP API and web UI."""
    import uvicorn

    uvicorn.run("trustdesk.api.app:app", host=host, port=port)


if __name__ == "__main__":
    app()
