"""HTTP API with the rule-based demo model and temporary databases (no network)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTDESK_PROVIDER", "fake")
    monkeypatch.setenv("TRUSTDESK_RETRIEVAL_MODE", "bm25")
    monkeypatch.setenv("TRUSTDESK_STORE_PATH", str(tmp_path / "store.db"))
    monkeypatch.setenv("TRUSTDESK_CHECKPOINT_PATH", str(tmp_path / "ckpt.db"))
    monkeypatch.setenv("TRUSTDESK_TRACE_DIR", str(tmp_path / "traces"))
    from trustdesk.api.app import app

    with TestClient(app) as c:
        yield c


def test_health_and_ui(client):
    assert client.get("/health").json()["provider"] == "fake"
    assert "TrustDesk console" in client.get("/").text


def test_chat_approval_flow(client):
    r = client.post("/chat", json={"message": "Please cancel SS-10483, email priya.s@example.in", "defense_level": 3}).json()
    assert r["status"] == "awaiting_approval" and r["pending_approval"]["policy_decision"]["rule_ids"] == ["REF-4"]
    sid = r["session_id"]
    assert client.post("/chat", json={"message": "hello?", "session_id": sid, "defense_level": 3}).status_code == 409
    pending = client.get("/approvals").json()
    assert len(pending) == 1
    done = client.post(f"/approvals/{pending[0]['id']}", json={"decision": "approve"}).json()
    assert "₹14999" in done["reply"]
    assert client.post(f"/approvals/{pending[0]['id']}", json={"decision": "approve"}).status_code == 409
    s = client.get(f"/sessions/{sid}").json()
    assert s["verified_customer_id"] == "DEMO1" and s["pending_approval"] is None
    assert client.get("/approvals?status=all").json()[0]["status"] == "approved"


def test_defense_level_fixed_per_session(client):
    sid = client.post("/chat", json={"message": "hi", "defense_level": 2}).json()["session_id"]
    assert client.post("/chat", json={"message": "hi", "session_id": sid, "defense_level": 0}).status_code == 409


def test_unknown_session_and_approval(client):
    assert client.get("/sessions/nope").status_code == 404
    assert client.post("/approvals/999", json={"decision": "approve"}).status_code == 404
    assert "SS-10482" in client.post("/demo/attack").json()["message"]


def test_unreachable_local_model_returns_503(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUSTDESK_PROVIDER", "ollama")
    monkeypatch.setenv("TRUSTDESK_OLLAMA_URL", "http://127.0.0.1:9")  # nothing listens here
    monkeypatch.setenv("TRUSTDESK_RETRIEVAL_MODE", "bm25")
    monkeypatch.setenv("TRUSTDESK_STORE_PATH", str(tmp_path / "store.db"))
    monkeypatch.setenv("TRUSTDESK_CHECKPOINT_PATH", str(tmp_path / "ckpt.db"))
    monkeypatch.setenv("TRUSTDESK_TRACE_DIR", str(tmp_path / "traces"))
    from trustdesk.api.app import app

    with TestClient(app) as c:
        r = c.post("/chat", json={"message": "hi"})
    assert r.status_code == 503 and "ollama" in r.json()["detail"].lower()
