"""Model adapters behind one interface (`ChatModel.complete`).

Messages and content blocks use the Anthropic Messages format internally (dicts), so
thinking blocks round-trip byte-for-byte. Adapters for other providers translate at
the edge. Switching provider is a config change (`TRUSTDESK_PROVIDER`), not a code change.

This module deliberately has no TrustDesk-specific imports so HopScout / PatchLoop can
copy it.
"""

from __future__ import annotations

import hashlib
import json
import time
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


# --- Anthropic ------------------------------------------------------------------------------
class AnthropicModel:
    """Claude via the official SDK.

    Rules for Claude Haiku 5.5 (Anthropic migration guide): no temperature/top_p/top_k;
    no assistant prefill (we always end on a user turn); adaptive thinking with
    `output_config.effort`; read content blocks by `type`; pass thinking blocks back
    unchanged; handle `stop_reason == "refusal"`. Automatic prompt caching caches the
    constant tools+system prefix and the growing conversation.
    """

    def __init__(self, model: str = "claude-haiku-5-5", effort: str = "low", max_tokens: int = 8000, timeout_s: float = 90.0) -> None:
        import anthropic

        self.name = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.client = anthropic.Anthropic(timeout=timeout_s, max_retries=4)  # SDK retries 408/409/429/5xx with backoff

    def complete(self, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": self.name,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            kwargs["tools"] = tools
        start = time.perf_counter()
        resp = self.client.messages.create(**kwargs)
        latency = time.perf_counter() - start
        u = resp.usage
        usage = {
            "input_tokens": u.input_tokens,
            "output_tokens": u.output_tokens,
            "cache_read_input_tokens": u.cache_read_input_tokens or 0,
            "cache_creation_input_tokens": u.cache_creation_input_tokens or 0,
            "thinking_tokens": (u.output_tokens_details.thinking_tokens or 0) if u.output_tokens_details else 0,
        }
        return ModelResponse(
            content=[b.model_dump(exclude_none=True) for b in resp.content],
            stop_reason=resp.stop_reason or "end_turn",
            model=resp.model,
            usage=usage,
            latency_s=latency,
            request_id=getattr(resp, "_request_id", None),
        )


# --- OpenAI-compatible (Ollama, vLLM, free tiers) ---------------------------------------------
class OpenAICompatModel:
    """Any `/chat/completions` endpoint with function calling, for cheap development runs."""

    def __init__(self, model: str, base_url: str, api_key: str = "", timeout_s: float = 120.0) -> None:
        import httpx

        self.name = model
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(timeout=timeout_s, headers={"Authorization": f"Bearer {api_key}"} if api_key else {})

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
