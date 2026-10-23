"""Model adapters behind one interface (`ChatModel.complete`).

Everything runs locally and for free. Internally, messages use one provider-neutral shape:
content blocks (`text`, `tool_use`, `tool_result`) in the widely used Messages layout. Each
adapter translates at the edge, so switching between Ollama, another local OpenAI-compatible
server, or the offline fakes is a config change (`TRUSTDESK_PROVIDER`), not a code change.

This module deliberately has no TrustDesk-specific imports so HopScout / PatchLoop can
copy it.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "thinking_tokens")


@dataclass
class ModelResponse:
    content: list[dict[str, Any]]
    stop_reason: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    latency_s: float = 0.0
    request_id: str | None = None

    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]


class ChatModel(Protocol):
    name: str

    def complete(self, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> ModelResponse: ...


# --- Ollama (local, free) -----------------------------------------------------------------------
THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class OllamaModel:
    """A local model served by Ollama, via its native `/api/chat` endpoint.

    Why native and not Ollama's OpenAI-compatible endpoint: only the native API lets us set
    `num_ctx` (Ollama's default context is too small for our prompt + 12 tool schemas, and
    overflow is silently truncated from the front) and switch "thinking" off per request.
    No temperature is set: we use the model's default sampling, and measure variance with
    repeated trials instead of hiding it.
    """

    def __init__(self, model: str = "qwen3:8b", base_url: str = "http://localhost:11434", num_ctx: int = 16384,
                 think: bool = False, timeout_s: float = 600.0) -> None:
        import httpx

        self.name = model
        self.base_url = base_url.rstrip("/").removesuffix("/v1")
        self.num_ctx = num_ctx
        self.think = think
        self.http = httpx.Client(timeout=timeout_s)

    @staticmethod
    def to_ollama(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Anthropic-style content blocks -> Ollama chat messages (thinking blocks are dropped)."""
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        names: dict[str, str] = {}
        for m in messages:
            content = m["content"]
            if isinstance(content, str):
                out.append({"role": m["role"], "content": content})
                continue
            if m["role"] == "assistant":
                calls = []
                for b in content:
                    if b["type"] == "tool_use":
                        names[b["id"]] = b["name"]
                        calls.append({"function": {"name": b["name"], "arguments": b["input"]}})
                msg: dict[str, Any] = {"role": "assistant", "content": "".join(b.get("text", "") for b in content if b["type"] == "text")}
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
            else:
                for b in content:
                    if b["type"] == "tool_result":
                        out.append({"role": "tool", "content": b["content"], "tool_name": names.get(b["tool_use_id"], "")})
                    elif b["type"] == "text":
                        out.append({"role": "user", "content": b["text"]})
        return out

    def complete(self, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> ModelResponse:
        body: dict[str, Any] = {
            "model": self.name,
            "messages": self.to_ollama(system, messages),
            "stream": False,
            "think": self.think,
            "options": {"num_ctx": self.num_ctx},
        }
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
                for t in tools
            ]
        start = time.perf_counter()
        resp = self.http.post(f"{self.base_url}/api/chat", json=body)
        resp.raise_for_status()
        data = resp.json()
        msg = data.get("message", {})
        content: list[dict[str, Any]] = []
        text = THINK_RE.sub("", msg.get("content") or "").strip()
        if text:
            content.append({"type": "text", "text": text})
        for call in msg.get("tool_calls") or []:
            args = call["function"].get("arguments") or {}
            if isinstance(args, str):
                args = json.loads(args or "{}")
            content.append({"type": "tool_use", "id": f"toolu_{uuid.uuid4().hex[:16]}", "name": call["function"]["name"], "input": args})
        thinking = msg.get("thinking") or ""
        return ModelResponse(
            content=content,
            stop_reason="tool_use" if msg.get("tool_calls") else ("max_tokens" if data.get("done_reason") == "length" else "end_turn"),
            model=self.name,
            usage={
                "input_tokens": data.get("prompt_eval_count", 0),
                "output_tokens": data.get("eval_count", 0),
                "thinking_tokens": len(thinking) // 4,  # Ollama doesn't split counts; rough estimate
            },
            latency_s=time.perf_counter() - start,
        )


# --- OpenAI-compatible (llama.cpp server, LM Studio, vLLM: any local server) --------------------
class OpenAICompatModel:
    """Any local `/chat/completions` server with function calling (llama.cpp, LM Studio, vLLM)."""

    def __init__(self, model: str, base_url: str, timeout_s: float = 600.0) -> None:
        import httpx

        self.name = model
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(timeout=timeout_s)

    @staticmethod
    def to_openai(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for m in messages:
            content = m["content"]
            if isinstance(content, str):
                out.append({"role": m["role"], "content": content})
                continue
            if m["role"] == "assistant":
                text = "".join(b.get("text", "") for b in content if b["type"] == "text")
                calls = [
                    {"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
                    for b in content
                    if b["type"] == "tool_use"
                ]
                msg: dict[str, Any] = {"role": "assistant", "content": text or None}
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
            else:
                for b in content:
                    if b["type"] == "tool_result":
                        out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
                    elif b["type"] == "text":
                        out.append({"role": "user", "content": b["text"]})
        return out

    def complete(self, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> ModelResponse:
        body: dict[str, Any] = {"model": self.name, "messages": self.to_openai(system, messages)}
        if tools:
            body["tools"] = [
                {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
                for t in tools
            ]
        start = time.perf_counter()
        resp = self.http.post(f"{self.base_url}/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()
        choice = data["choices"][0]["message"]
        content: list[dict[str, Any]] = []
        if choice.get("content"):
            content.append({"type": "text", "text": choice["content"]})
        for call in choice.get("tool_calls") or []:
            content.append(
                {"type": "tool_use", "id": call["id"], "name": call["function"]["name"], "input": json.loads(call["function"]["arguments"] or "{}")}
            )
        usage = data.get("usage", {})
        return ModelResponse(
            content=content,
            stop_reason="tool_use" if choice.get("tool_calls") else "end_turn",
            model=self.name,
            usage={"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)},
            latency_s=time.perf_counter() - start,
        )


# --- test / offline models --------------------------------------------------------------------
class ScriptedModel:
    """Replays fixed responses (or calls a function of the transcript). No network; for tests."""

    def __init__(self, script: list[list[dict[str, Any]]] | Callable[[list[dict[str, Any]]], list[dict[str, Any]]], name: str = "fake") -> None:
        self.name = name
        self.script = script
        self.calls = 0

    def complete(self, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> ModelResponse:
        if callable(self.script):
            content = self.script(messages)
        else:
            content = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"
        return ModelResponse(content=content, stop_reason=stop, model=self.name, usage={"input_tokens": 0, "output_tokens": 0})


class CachedModel:
    """Development-only response cache keyed by the full request. Reported runs assert it is off."""

    def __init__(self, inner: ChatModel, cache_dir: Path) -> None:
        self.inner = inner
        self.name = inner.name
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)

    def complete(self, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> ModelResponse:
        key = hashlib.sha256(json.dumps([self.name, system, tools, messages], sort_keys=True, default=str).encode()).hexdigest()
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            data = json.loads(path.read_text())
            return ModelResponse(**data)
        resp = self.inner.complete(system, tools, messages)
        path.write_text(json.dumps(resp.__dict__))
        return resp
