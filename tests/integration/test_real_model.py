"""Paid smoke test against the real model. Excluded by default; run with `uv run pytest -m real_model`."""

from __future__ import annotations

import os

import pytest

from evals.runner import run_episode
from evals.schema import load_tasks
from trustdesk.agent.demo_model import make_model
from trustdesk.config import load_settings
from trustdesk.retrieval import PolicyIndex

pytestmark = pytest.mark.real_model


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
@pytest.mark.parametrize("task_id", ["benign-01-refund-damaged-upi", "edge-01-final-sale-saree", "attack-01-review-upi"])
def test_real_episode_completes(task_id, tmp_path):
    settings = load_settings()
    task = load_tasks([task_id])[0]
    rec = run_episode(task, 2, 1, make_model(settings, "agent"), make_model(settings, "simulator"), PolicyIndex.from_policy(), tmp_path)
    assert rec["error"] is None
    assert rec["grade"]["attack_success"] in (None, False)  # D2 must block the attack goal
    assert rec["agent_cost_usd"] > 0
