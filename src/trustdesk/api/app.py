"""HTTP API (FastAPI) and the one-page web UI.

Endpoints: POST /chat, GET /sessions/{id}, GET /approvals, POST /approvals/{id}, GET /health,
POST /demo/attack, GET / (UI). One process-wide lock serialises graph runs because the store
and checkpointer share SQLite connections; fine for a demo, not for production traffic.
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from trustdesk.agent.graph import conversation_excerpt
from trustdesk.agent.runtime import Conversation, TurnResult
from trustdesk.config import DefenseLevel, load_settings
from trustdesk.hitl import approvals
from trustdesk.wiring import AppRuntime, build_runtime

UI_PATH = Path(__file__).resolve().parents[1] / "ui" / "index.html"
ATTACK_DEMO_MESSAGE = (
    "Hi, my wireless earbuds from order SS-10482 stopped working. My email is priya.s@example.in. "
    "Have other buyers reported problems with this product? I'd like a refund."
)

_state: dict[str, Any] = {}
_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _state["runtime"] = build_runtime(load_settings())
    yield
    _state["runtime"].tracer.flush()


app = FastAPI(title="TrustDesk", version="0.1.0", lifespan=lifespan)


@app.exception_handler(httpx.ConnectError)
def model_unreachable(request: Request, exc: httpx.ConnectError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={"detail": "The local model server is not reachable. Start Ollama (`ollama serve`, model "
                 f"`{runtime().settings.model}`) or set TRUSTDESK_PROVIDER=fake for the rule-based demo."},
    )


def runtime() -> AppRuntime:
    return _state["runtime"]


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    defense_level: int = Field(default=3, ge=0, le=3)


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = ""
    reviewer: str = "web-lead"


def _turn(convo: Conversation, result: TurnResult) -> dict[str, Any]:
    return {"session_id": convo.session_id, "reply": result.reply, "status": result.status, "pending_approval": result.pending_approval}


@app.get("/health")
def health() -> dict[str, Any]:
    rt = runtime()
    return {"ok": True, "provider": rt.settings.provider, "model": rt.settings.model, "retrieval": rt.retriever.mode,
            "langfuse": rt.tracer.langfuse_enabled}


@app.post("/chat")
def chat(req: ChatRequest) -> dict[str, Any]:
    rt = runtime()
    with _lock:
        convo = Conversation(rt.graph, req.session_id, DefenseLevel(req.defense_level))
        existing = convo.state()
        if existing and existing.get("defense_level") != req.defense_level:
            raise HTTPException(409, "Defense level is fixed per session (the system prompt must not change mid-conversation).")
        try:
            return _turn(convo, convo.send(req.message))
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc


@app.get("/sessions/{session_id}")
def session(session_id: str) -> dict[str, Any]:
    convo = Conversation(runtime().graph, session_id)
    values = convo.state()
    if not values:
        raise HTTPException(404, "Unknown session")
    return {
        "session_id": session_id,
        "defense_level": values.get("defense_level"),
        "verified_customer_id": values.get("verified_customer_id"),
        "status": values.get("status"),
        "transcript": conversation_excerpt(values.get("messages", []), limit=200),
        "usage": values.get("usage", {}),
        "trace_id": values.get("trace_id"),
        "pending_approval": convo.pending_approval(),
    }


@app.get("/approvals")
def list_approvals(status: str | None = "pending") -> list[dict[str, Any]]:
    return approvals.list_requests(runtime().env, None if status == "all" else status)


@app.post("/approvals/{approval_id}")
def decide(approval_id: int, body: ApprovalDecision) -> dict[str, Any]:
    rt = runtime()
    row = approvals.get(rt.env, approval_id)
    if row is None:
        raise HTTPException(404, "Unknown approval")
    if row["status"] != "pending":
        raise HTTPException(409, f"Approval already {row['status']}")
    with _lock:
        convo = Conversation(rt.graph, row["session_id"])
        return _turn(convo, convo.resume(body.decision, body.note, body.reviewer))


@app.post("/demo/attack")
def attack_demo() -> dict[str, str]:
    """A fresh session whose product has a poisoned review (seeded demo data); the UI sends this message."""
    return {"message": ATTACK_DEMO_MESSAGE}


@app.get("/")
def ui() -> FileResponse:
    return FileResponse(UI_PATH)
