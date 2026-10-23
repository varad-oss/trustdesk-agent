"""Builds the app's long-lived objects from settings (used by the CLI and the HTTP API)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trustdesk.agent.demo_model import make_model
from trustdesk.agent.graph import build_graph
from trustdesk.agent.runtime import sqlite_checkpointer
from trustdesk.config import Settings
from trustdesk.env import Env, apply_patch, seed
from trustdesk.env.demo import DEMO_PATCH
from trustdesk.observability.tracing import Tracer
from trustdesk.retrieval import PolicyIndex, build_index


@dataclass
class AppRuntime:
    settings: Settings
    env: Env
    graph: Any
    retriever: PolicyIndex
    tracer: Tracer


def open_store(settings: Settings, reseed: bool = False) -> Env:
    """Open the shared store, seeding it on first use."""
    if reseed and settings.store_path.exists():
        settings.store_path.unlink()
    env = Env.open(settings.store_path)
    if env.one("SELECT COUNT(*) AS n FROM customers")["n"] == 0:
        seed(env)
    if env.customer("DEMO1") is None:
        apply_patch(env, DEMO_PATCH)
    return env


def build_runtime(settings: Settings) -> AppRuntime:
    env = open_store(settings)
    retriever = build_index(settings.retrieval_mode)
    tracer = Tracer(settings.trace_dir)
    graph = build_graph(
        env,
        make_model(settings, "agent"),
        retriever=retriever,
        tracer=tracer,
        checkpointer=sqlite_checkpointer(settings.checkpoint_path),
        max_steps=settings.max_steps,
    )
    return AppRuntime(settings, env, graph, retriever, tracer)
