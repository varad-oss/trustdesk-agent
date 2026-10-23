"""Task file format (evals/tasks/*.yaml), validated with Pydantic at load time."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

TASKS_DIR = Path(__file__).parent / "tasks"


class DbAssertion(BaseModel):
    """A check on final DB state. `where` selects rows; then exactly one of the checks applies.

    - equals: some selected row has these column values
    - json_contains: some selected row's JSON column contains these keys/values
    - between: some selected row has each column within [lo, hi]
    - exists: whether any row matches `where`
    - count: number of rows matching `where`
    """

    table: str
    where: dict[str, Any] = Field(default_factory=dict)
    equals: dict[str, Any] | None = None
    json_contains: dict[str, dict[str, Any]] | None = None
    between: dict[str, tuple[float, float]] | None = None
    exists: bool | None = None
    count: int | None = None


class GoalCheck(BaseModel):
    """Deterministic attack-success check (never an LLM judge)."""

    kind: Literal["db", "output_contains", "write_before_verification"] = "db"
    table: str | None = None
    where: dict[str, Any] = Field(default_factory=dict)
    exists: bool = True
    min_value: dict[str, float] | None = None  # e.g. {amount_inr: 201}: some matching row has value >= this
    text: list[str] = Field(default_factory=list)


class Attack(BaseModel):
    vector: Literal["product_review", "delivery_note", "ticket_history", "direct", "fake_system_notice"]
    goal: str
    injected_text: str
    goal_check: GoalCheck


class Expected(BaseModel):
    db_assertions: list[DbAssertion] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    must_cite_rules: list[str] = Field(default_factory=list)
    must_mention: list[str] = Field(default_factory=list)
    approver_decision: Literal["approve", "reject"] | None = None
    expect_escalation: bool | None = None


class GoldenStep(BaseModel):
    tool: str
    args: dict[str, Any]
    approve: bool = False  # this call must go through the tier-2 approval gate at D3


class Probe(BaseModel):
    """An action that must NOT be allowed by policy (checked by the validator at D2)."""

    tool: str
    args: dict[str, Any]
    verified_as: str | None = None


class Task(BaseModel):
    id: str
    category: Literal["benign", "edge", "hitl", "ambiguous", "attack"]
    persona: str
    user_instructions: str
    initial_state_patch: dict[str, Any] = Field(default_factory=dict)
    expected: Expected
    golden_actions: list[GoldenStep] = Field(default_factory=list)
    forbidden_probes: list[Probe] = Field(default_factory=list)
    attack: Attack | None = None
    notes: str = ""

    @property
    def customer_id(self) -> str | None:
        customers = self.initial_state_patch.get("customers", [])
        return customers[0]["id"] if customers else None


def load_tasks(task_ids: list[str] | None = None, directory: Path = TASKS_DIR) -> list[Task]:
    tasks = [Task.model_validate(yaml.safe_load(p.read_text(encoding="utf-8"))) for p in sorted(directory.glob("*.yaml"))]
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate task IDs")
    if task_ids:
        tasks = [t for t in tasks if t.id in set(task_ids)]
    return tasks
