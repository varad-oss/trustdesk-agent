"""Live progress view for an eval run: bar, ETA, per-level scoreboard. Read-only and lightweight.

It re-reads results/<run_id>/episodes.jsonl every few seconds (standard library only, no model,
no database), so it costs a few MB of memory and does not slow the run it watches.
"""

from __future__ import annotations

import json
import shutil
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from trustdesk.config import REPO_ROOT

RESULTS = REPO_ROOT / "results"
RECENT = 10  # episodes used for the speed estimate (recent pace beats the all-time average)


def _bar(fraction: float, width: int) -> str:
    filled = int(round(fraction * width))
    return "█" * filled + "░" * (width - filled)


def _fmt(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"


def snapshot(run_dir: Path) -> str:
    cfg = json.loads((run_dir / "config.json").read_text())
    total = len(cfg["task_ids"]) * len(cfg["levels"]) * cfg["trials"]
    rows = []
    path = run_dir / "episodes.jsonl"
    if path.exists():
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    good = {r["episode_id"]: r for r in rows if "grade" in r and not r.get("error")}
    failed = [r for r in rows if r.get("error")]
    done = len(good)
    ordered = sorted(good.values(), key=lambda r: r.get("episode_id"))
    recent = [r["latency_s"] for r in list(good.values())[-RECENT:]] or [0]
    pace = statistics.median(recent)  # median: one episode stuck behind a dying server shouldn't skew the ETA
    remaining = max(total - done, 0) * pace
    width = max(min(shutil.get_terminal_size((80, 20)).columns - 30, 60), 10)

    lines = [
        f"\033[1mTrustDesk eval · {cfg['run_id']}\033[0m   model {cfg['settings'].get('model', '?')} (local, $0)",
        "",
        f"  {_bar(done / total if total else 0, width)}  {done}/{total}  ({100 * done / max(total, 1):.0f}%)",
        "",
        f"  pace     {pace:.0f} s/episode (median of last {len(recent)})",
        f"  left     ~{_fmt(remaining)}   →  done around {(datetime.now() + timedelta(seconds=remaining)):%a %H:%M}",
        f"  retries  {len(failed)} failed attempt(s) on record" + ("  (re-run on --resume)" if failed else ""),
        "",
        "  level   done   benign ok   attacks blocked   policy violations",
    ]
    by_level: dict[int, list[dict]] = defaultdict(list)
    for r in ordered:
        by_level[r["defense_level"]].append(r)
    per_level_total = total // max(len(cfg["levels"]), 1)
    for lv in cfg["levels"]:
        rs = by_level.get(lv, [])
        benign = [r for r in rs if r["category"] != "attack"]
        attacks = [r for r in rs if r["category"] == "attack"]
        ok = sum(r["grade"]["success"] for r in benign)
        blocked = sum(not r["grade"]["attack_success"] for r in attacks)
        viol = sum(bool(r["grade"]["policy_violations"]) for r in rs)
        lines.append(f"  D{lv}    {len(rs):>3}/{per_level_total:<3}  {ok:>3}/{len(benign):<3}      {blocked:>3}/{len(attacks):<3}           {viol:>3}")
    if rows:
        last = rows[-1]
        mark = "✗ error" if last.get("error") else ("✓ success" if last["grade"]["success"] else "· failed")
        lines += ["", f"  last     {last['episode_id']}  {mark}  {last.get('latency_s', 0):.0f}s"]
    lines += ["", "  (counts so far are not results: wait for `make report`.  Ctrl-C to close this view; the run keeps going.)"]
    return "\n".join(lines)


def watch(run_id: str, interval: float = 5.0) -> None:
    run_dir = RESULTS / run_id
    if not (run_dir / "config.json").exists():
        sys.exit(f"No run at {run_dir}")
    try:
        while True:
            text = snapshot(run_dir)
            sys.stdout.write("\033[H\033[2J" + text + "\n")
            sys.stdout.flush()
            cfg = json.loads((run_dir / "config.json").read_text())
            total = len(cfg["task_ids"]) * len(cfg["levels"]) * cfg["trials"]
            if text and f"{total}/{total}" in text.splitlines()[2]:
                print("\n  Run complete.")
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        print()
