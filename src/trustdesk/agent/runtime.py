"""A thin conversation API over the compiled graph, shared by CLI, HTTP API and eval runner."""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from trustdesk.config import DefenseLevel
from trustdesk.observability.tracing import new_trace_id


@dataclass
class TurnResult:
    """What one user turn (or one approval resume) produced."""

    reply: str | None
    pending_approval: dict[str, Any] | None = None
    status: str = "done"
    events: list[dict[str, Any]] = field(default_factory=list)


def sqlite_checkpointer(path: Path) -> SqliteSaver:
    """Durable checkpointer: pending approvals survive a process restart."""
    path.parent.mkdir(parents=True, exist_ok=True)
    saver = SqliteSaver(sqlite3.connect(str(path), check_same_thread=False))
    saver.setup()
    return saver


class Conversation:
    def __init__(self, graph: Any, session_id: str | None = None, defense_level: DefenseLevel = DefenseLevel.D3) -> None:
        self.graph = graph
        self.session_id = session_id or f"s-{uuid.uuid4().hex[:10]}"
        self.defense_level = defense_level
        self.config = {"configurable": {"thread_id": self.session_id}, "recursion_limit": 200}

    # --- state -------------------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        return dict(self.graph.get_state(self.config).values)

    def pending_approval(self) -> dict[str, Any] | None:
        snapshot = self.graph.get_state(self.config)
        for task in snapshot.tasks:
            for intr in task.interrupts:
                return intr.value
        return None

    def _result(self, events_before: int) -> TurnResult:
        values = self.state()
        pending = self.pending_approval()
        events = values.get("events", [])[events_before:]
        if pending is not None:
            return TurnResult(None, pending, "awaiting_approval", events)
        return TurnResult(values.get("final_text"), None, values.get("status", "done"), events)

    # --- actions -------------------------------------------------------------------------------
    def send(self, text: str) -> TurnResult:
        if self.pending_approval() is not None:
            raise RuntimeError("An approval is pending; resolve it before sending another message.")
        values = self.state()
        before = len(values.get("events", []))
        turn: dict[str, Any] = {
            "messages": [{"role": "user", "content": text}],
            "step_count": 0,
            "status": "running",
            "call_counts": {},
            "final_text": None,
        }
        if not values:
            turn.update(
                {
                    "session_id": self.session_id,
                    "trace_id": new_trace_id(),
                    "defense_level": int(self.defense_level),
                    "verified_customer_id": None,
                    "usage": {},
                }
            )
        self.graph.invoke(turn, self.config)
        return self._result(before)

    def resume(self, decision: str, note: str = "", reviewer: str = "support-lead") -> TurnResult:
        before = len(self.state().get("events", []))
        self.graph.invoke(Command(resume={"decision": decision, "note": note, "reviewer": reviewer}), self.config)
        return self._result(before)
