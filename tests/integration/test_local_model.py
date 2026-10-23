"""Smoke test against the real local model (Ollama, free). Excluded by default; run with `uv run pytest -m local_model`."""

from __future__ import annotations

import httpx
import pytest

from evals.runner import run_episode
from evals.schema import load_tasks
from trustdesk.agent.demo_model import make_model
from trustdesk.config import load_settings
from trustdesk.retrieval import PolicyIndex

pytestmark = pytest.mark.local_model


def _ollama_has(model: str, base_url: str) -> bool:
    try:
        tags = httpx.get(f"{base_url}/api/tags", timeout=2).json()
    except httpx.HTTPError:
        return False
    return any(m["name"] == model for m in tags.get("models", []))


@pytest.mark.parametrize("task_id", ["benign-01-refund-damaged-upi", "attack-01-review-upi"])
def test_local_episode_completes(task_id, tmp_path):
    settings = load_settings()
    if not _ollama_has(settings.model, settings.ollama_base_url):
        pytest.skip(f"Ollama with {settings.model} not running at {settings.ollama_base_url}")
    task = load_tasks([task_id])[0]
    rec = run_episode(task, 2, 1, make_model(settings, "agent"), make_model(settings, "simulator"), PolicyIndex.from_policy(), tmp_path)
    assert rec["error"] is None
    assert rec["tool_calls"] > 0
    assert rec["grade"]["attack_success"] in (None, False)  # D2 must block the attack goal whatever the model does
    assert not rec["grade"]["policy_violations"]
