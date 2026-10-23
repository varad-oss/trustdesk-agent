"""Tracing: every model call, tool call and approval becomes one event.

Events always go to JSONL (`data/traces/<trace_id>.jsonl`), which is what the eval
trajectories link to. If LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY are set and the
`langfuse` extra is installed, the same events are also sent to Langfuse under the
same trace ID (ADR-005).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger("trustdesk")


def new_trace_id() -> str:
    return uuid.uuid4().hex  # 32 hex chars: also a valid Langfuse/OpenTelemetry trace ID


class Tracer:
    def __init__(self, trace_dir: Path | None = None, use_langfuse: bool | None = None) -> None:
        self.trace_dir = trace_dir
        self._lock = threading.Lock()
        self._langfuse = None
        if use_langfuse is None:
            use_langfuse = bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))
        if use_langfuse:
            try:
                from langfuse import get_client

                self._langfuse = get_client()
            except Exception as exc:  # noqa: BLE001 - tracing must never break the agent
                log.warning("Langfuse unavailable, JSONL only: %s", exc)

    @property
    def langfuse_enabled(self) -> bool:
        return self._langfuse is not None

    def record(self, trace_id: str, kind: str, name: str, data: dict[str, Any]) -> dict[str, Any]:
        event = {"ts": time.time(), "trace_id": trace_id, "kind": kind, "name": name, **data}
        if self.trace_dir is not None:
            self.trace_dir.mkdir(parents=True, exist_ok=True)
            with self._lock, open(self.trace_dir / f"{trace_id}.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, default=str) + "\n")
        if self._langfuse is not None:
            self._send_langfuse(trace_id, kind, name, data)
        return event

    def _send_langfuse(self, trace_id: str, kind: str, name: str, data: dict[str, Any]) -> None:
        try:
            as_type = {"model": "generation", "tool": "tool", "approval": "guardrail"}.get(kind, "span")
            kwargs: dict[str, Any] = {
                "trace_context": {"trace_id": trace_id},
                "name": name,
                "as_type": as_type,
                "metadata": {k: v for k, v in data.items() if k not in {"usage", "cost_usd"}},
            }
            if as_type == "generation":
                kwargs["model"] = data.get("model")
                kwargs["usage_details"] = {k: int(v) for k, v in data.get("usage", {}).items()}
                kwargs["cost_details"] = {"total": float(data.get("cost_usd", 0.0))}
            self._langfuse.start_observation(**kwargs).end()
        except Exception as exc:  # noqa: BLE001
            log.warning("Langfuse send failed: %s", exc)

    def flush(self) -> None:
        if self._langfuse is not None:
            self._langfuse.flush()
