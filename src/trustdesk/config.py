"""Runtime configuration, read once from the environment (and `.env`).

Why one module: every entry point (CLI, API, eval runner) needs the same knobs, and
keeping them in one typed object makes "change config, not code" (provider
portability, defense level) a single obvious place.
"""

from __future__ import annotations

import os
from enum import IntEnum
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")


class DefenseLevel(IntEnum):
    """Cumulative defense levels (section 6.2 of the spec)."""

    D0 = 0  # baseline: prompt only, raw untrusted text, no checks in write tools
    D1 = 1  # + spotlighting / datamarking + instruction hierarchy
    D2 = 2  # + policy engine, identity gate, DB-only refund destinations, PII masking
    D3 = 3  # + human approval for tier-2 actions


class Settings(BaseModel):
    provider: str = Field(default="ollama", description="ollama | openai_compat | fake (all free, all local)")
    model: str = "qwen3:8b"
    think: bool = False
    num_ctx: int = 16384
    simulator_provider: str = "ollama"
    simulator_model: str = "qwen3:8b"
    ollama_base_url: str = "http://localhost:11434"
    openai_base_url: str = "http://localhost:8080/v1"
    defense_level: DefenseLevel = DefenseLevel.D3
    max_steps: int = 25
    retrieval_mode: str = Field(default="hybrid", description="hybrid | bm25 | dense")
    store_path: Path = REPO_ROOT / "data" / "shopsetu.db"
    checkpoint_path: Path = REPO_ROOT / "data" / "checkpoints.db"
    trace_dir: Path = REPO_ROOT / "data" / "traces"
    llm_cache_dir: Path | None = None


def load_settings() -> Settings:
    """Build settings from TRUSTDESK_* environment variables."""
    env = os.environ
    cache_dir = env.get("TRUSTDESK_LLM_CACHE_DIR")
    return Settings(
        provider=env.get("TRUSTDESK_PROVIDER", "ollama"),
        model=env.get("TRUSTDESK_MODEL", "qwen3:8b"),
        think=env.get("TRUSTDESK_THINK", "false").lower() == "true",
        num_ctx=int(env.get("TRUSTDESK_NUM_CTX", "16384")),
        simulator_provider=env.get("TRUSTDESK_SIM_PROVIDER", env.get("TRUSTDESK_PROVIDER", "ollama")),
        simulator_model=env.get("TRUSTDESK_SIM_MODEL", "qwen3:8b"),
        ollama_base_url=env.get("TRUSTDESK_OLLAMA_URL", "http://localhost:11434"),
        openai_base_url=env.get("TRUSTDESK_OPENAI_BASE_URL", "http://localhost:8080/v1"),
        defense_level=DefenseLevel(int(env.get("TRUSTDESK_DEFENSE_LEVEL", "3"))),
        max_steps=int(env.get("TRUSTDESK_MAX_STEPS", "25")),
        retrieval_mode=env.get("TRUSTDESK_RETRIEVAL_MODE", "hybrid"),
        store_path=Path(env.get("TRUSTDESK_STORE_PATH", str(REPO_ROOT / "data" / "shopsetu.db"))),
        checkpoint_path=Path(env.get("TRUSTDESK_CHECKPOINT_PATH", str(REPO_ROOT / "data" / "checkpoints.db"))),
        trace_dir=Path(env.get("TRUSTDESK_TRACE_DIR", str(REPO_ROOT / "data" / "traces"))),
        llm_cache_dir=Path(cache_dir) if cache_dir else None,
    )
