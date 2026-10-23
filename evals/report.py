"""Regenerate docs/EVAL_REPORT.md and charts from committed results files. No hand-typed numbers.

Sources: results/validation.json, results/retrieval.json, and one eval run directory
(results/<run_id>/episodes.jsonl + config.json). By default the latest run whose config says
`reported: true` is used; pass a run ID to choose another.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from evals.metrics import bootstrap_ci, mean_pass_hat_k, percentile, wilson_interval
from trustdesk.config import REPO_ROOT

RESULTS = REPO_ROOT / "results"
REPORT = REPO_ROOT / "docs" / "EVAL_REPORT.md"
CHART_DIR = REPO_ROOT / "docs" / "images"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def find_run(run_id: str | None) -> Path | None:
    if run_id:
        return RESULTS / run_id
    runs = sorted((p for p in RESULTS.iterdir() if (p / "config.json").exists() and not p.name.endswith("-pilot")), key=lambda p: p.name)
    reported = [p for p in runs if json.loads((p / "config.json").read_text()).get("settings", {}).get("reported")]
    return (reported or [None])[-1]


def _rate(values: list[bool]) -> str:
    if not values:
        return "–"
    c, n = sum(values), len(values)
    lo, hi = wilson_interval(c, n)
    return f"{c / n:.2f} [{lo:.2f}, {hi:.2f}] (n={n})"


def level_metrics(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [e for e in episodes if "grade" in e]
    benign = [e for e in ok if e["category"] != "attack"]
    attack = [e for e in ok if e["category"] == "attack"]
    by_task: dict[str, list[bool]] = defaultdict(list)
    for e in ok:
        by_task[e["task_id"]].append(e["grade"]["success"])
    per_task = [(len(v), sum(v)) for v in by_task.values()]
    n_trials = min((n for n, _ in per_task), default=0)
    passk = {}
    for k in range(1, n_trials + 1):
        est = mean_pass_hat_k(per_task, k)
        lo, hi = bootstrap_ci(per_task, lambda g, k=k: mean_pass_hat_k(g, k))
        passk[k] = (est, lo, hi)
    escal_expected = [e for e in ok if e["grade"].get("escalation_expected")]
    agent_s = [e.get("agent_model_s", 0.0) for e in ok]
    lat = [e.get("latency_s", 0.0) for e in ok]
    usage = Counter()
    for e in ok:
        usage.update({k: v for k, v in e.get("usage", {}).items() if isinstance(v, (int, float))})
    n = max(len(ok), 1)
    return {
        "episodes": len(episodes),
        "infra_failed": sum(1 for e in episodes if e.get("infra_failed") or e.get("grade", {}).get("failure_class") == "infrastructure_error"),
        "benign_utility": _rate([e["grade"]["success"] for e in benign]),
        "utility_under_attack": _rate([e["grade"]["user_goal"] for e in attack]),
        "asr": _rate([bool(e["grade"]["attack_success"]) for e in attack]),
        "passk": passk,
        "violations": _rate([bool(e["grade"]["policy_violations"]) for e in ok]),
        "escalation": {
            "correct": sum(e["grade"]["escalated"] for e in escal_expected),
            "missed": sum(not e["grade"]["escalated"] for e in escal_expected),
            "unnecessary": sum(e["grade"]["escalated"] and not e["grade"].get("escalation_expected") for e in ok),
        },
        "agent_s_per_episode": sum(agent_s) / n,
        "sim_s_per_episode": sum(e.get("simulator_model_s", 0.0) for e in ok) / n,
        "latency_p50": percentile(lat, 50),
        "latency_p95": percentile(lat, 95),
        "turns": sum(e.get("customer_turns", 0) for e in ok) / n,
        "tool_calls": sum(e.get("tool_calls", 0) for e in ok) / n,
        "tokens": {k: usage[k] / n for k in ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")},
        "failures": Counter(e["grade"]["failure_class"] for e in ok if e["grade"]["failure_class"]),
        "failed_examples": [e["episode_id"] for e in ok if not e["grade"]["success"]][:3],
    }


def _chart(metrics: dict[int, dict[str, Any]], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def val(s: str) -> float:
        return float(s.split()[0]) if s != "–" else 0.0

    levels = sorted(metrics)
    series = [("Benign utility", "benign_utility", "#2a6fdb"), ("Utility under attack", "utility_under_attack", "#29a37a"), ("Attack success rate", "asr", "#d9534f")]
    fig, ax = plt.subplots(figsize=(7, 3.6), dpi=150)
    width = 0.26
    for i, (label, key, color) in enumerate(series):
        xs = [lv + (i - 1) * width for lv in range(len(levels))]
        ax.bar(xs, [val(metrics[lv][key]) for lv in levels], width, label=label, color=color)
    ax.set_xticks(range(len(levels)), [f"D{lv}" for lv in levels])
    ax.set_ylim(0, 1)
    ax.set_ylabel("rate")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, fontsize=8, ncols=3, loc="upper center", bbox_to_anchor=(0.5, 1.15))
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def render(run_dir: Path | None) -> str:
    lines = ["# TrustDesk evaluation report", "", "_Generated by `make report` (evals/report.py) from files in `results/`. Do not edit by hand._", ""]

    val = RESULTS / "validation.json"
    if val.exists():
        v = json.loads(val.read_text())
        lines += ["## Task suite", "", "Source: [`results/validation.json`](../results/validation.json) (`make validate`).", "",
                  f"- {v['n_ok']}/{v['n_tasks']} tasks pass the golden-replay validator.",
                  "- By category: " + ", ".join(f"{k} {n}" for k, n in sorted(v["by_category"].items())), ""]

    ret = RESULTS / "retrieval.json"
    if ret.exists():
        r = json.loads(ret.read_text())
        lines += ["## Policy retrieval", "", f"Source: [`results/retrieval.json`](../results/retrieval.json) (`make eval-retrieval`), "
                  f"{r['n_questions']} questions, embedding model `{r['embedding_model']}`.", "",
                  "| Method | recall@1 | recall@4 | MRR |", "|---|---|---|---|"]
        for m, s in r["methods"].items():
            lines.append(f"| {m} | {s['recall@1']:.2f} | {s['recall@4']:.2f} | {s['mrr']:.2f} |")
        lines.append("")

    if run_dir is None:
        lines += ["## Agent results (D0–D3)", "", "**Not yet measured.** No reported run exists in `results/`. "
                  "Run `make eval-full` (a short pilot first estimates the wall-clock time), then `make report`.", ""]
        return "\n".join(lines)

    cfg = json.loads((run_dir / "config.json").read_text())
    episodes = _load_jsonl(run_dir / "episodes.jsonl")
    by_level: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for e in episodes:
        if "defense_level" in e:
            by_level[e["defense_level"]].append(e)
    metrics = {lv: level_metrics(eps) for lv, eps in sorted(by_level.items())}
    s = cfg.get("settings", {})
    rel = Path(os.path.relpath(run_dir, REPO_ROOT))
    lines += [f"## Agent results — run `{cfg['run_id']}`", "",
              f"Source: [`{rel}/episodes.jsonl`](../{rel}/episodes.jsonl). Date {cfg['created_at'][:10]}; agent `{s.get('provider')}:{s.get('model')}` "
              f"(thinking {'on' if s.get('think') else 'off'}, num_ctx {s.get('num_ctx')}); simulator `{s.get('simulator_provider')}:{s.get('simulator_model')}`; {len(cfg['task_ids'])} tasks × "
              f"{cfg['trials']} trials; approver `{cfg['approver']}`; retrieval `{s.get('retrieval_mode_effective')}`; reported={s.get('reported')}.", "",
              "Proportions show the point estimate, a 95% Wilson interval and n (episodes). pass^k uses the unbiased estimator C(c,k)/C(n,k) "
              "averaged over tasks, with a 95% bootstrap interval over tasks.", "",
              "| Level | Benign utility | Utility under attack | Attack success rate | pass^k | Episodes with policy violations | Agent model s/episode |",
              "|---|---|---|---|---|---|---|"]
    for lv, m in metrics.items():
        pk = "; ".join(f"k={k}: {e:.2f} [{lo:.2f}, {hi:.2f}]" for k, (e, lo, hi) in m["passk"].items())
        lines.append(f"| D{lv} | {m['benign_utility']} | {m['utility_under_attack']} | {m['asr']} | {pk} | {m['violations']} | {m['agent_s_per_episode']:.1f} |")
    lines += ["", "Under D2+ the policy engine makes most invariant violations impossible by construction; the post-hoc checker "
              "(`evals/graders.py::policy_violations`) re-implements the invariants independently, so it remains a real check on the engine.", "",
              "### Efficiency", "", "| Level | Customer turns | Tool calls | Input tok | Output tok | Thinking tok | Cache-read tok | Simulator s/episode | Latency p50 / p95 (s) | Infra failures |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for lv, m in metrics.items():
        t = m["tokens"]
        lines.append(f"| D{lv} | {m['turns']:.1f} | {m['tool_calls']:.1f} | {t['input_tokens']:.0f} | {t['output_tokens']:.0f} | {t['thinking_tokens']:.0f} | "
                     f"{t['cache_read_input_tokens']:.0f} | {m['sim_s_per_episode']:.1f} | {m['latency_p50']:.1f} / {m['latency_p95']:.1f} | {m['infra_failed']} |")
    lines += ["", "### Escalation quality", "", "| Level | Correct | Missed | Unnecessary |", "|---|---|---|---|"]
    for lv, m in metrics.items():
        esc = m["escalation"]
        lines.append(f"| D{lv} | {esc['correct']} | {esc['missed']} | {esc['unnecessary']} |")
    lines += ["", "### Failure taxonomy (automatic first pass; review the examples by hand)", "", "| Level | Failure class | Count |", "|---|---|---|"]
    for lv, m in metrics.items():
        for cls, n in m["failures"].most_common():
            lines.append(f"| D{lv} | {cls} | {n} |")
    lines += ["", "Example failed trajectories:", ""]
    for lv, m in metrics.items():
        for ep in m["failed_examples"]:
            lines.append(f"- D{lv}: [`{ep}`](../{rel}/trajectories/{ep}.jsonl)")
    chart = CHART_DIR / f"ablation_{cfg['run_id']}.png"
    _chart(metrics, chart)
    lines += ["", f"![Utility and attack success by defense level](images/{chart.name})", ""]
    return "\n".join(lines)


def main(run_id: str | None = None) -> None:
    run_dir = find_run(run_id) if RESULTS.exists() else None
    REPORT.write_text(render(run_dir), encoding="utf-8")
    print(f"Wrote {REPORT.relative_to(REPO_ROOT)} from {run_dir.relative_to(REPO_ROOT) if run_dir else 'validation/retrieval only'}")


if __name__ == "__main__":
    import sys

    main(sys.argv[1] if len(sys.argv) > 1 else None)
