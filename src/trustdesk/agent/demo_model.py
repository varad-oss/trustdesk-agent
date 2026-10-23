"""A deterministic, rule-based stand-in for the LLM, plus the model factory.

`RuleBasedDemoModel` is NOT an AI: it pattern-matches the conversation and emits the
same tool calls a good agent would for the common flows (verify -> look up -> cancel or
damaged-item return -> refund). It exists so the CLI, API and Docker demo run with no API
key, and so integration tests exercise the real graph end to end. Eval numbers are
never reported for it.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from trustdesk.agent.model import CachedModel, ChatModel, ModelResponse, OllamaModel, OpenAICompatModel
from trustdesk.config import Settings

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
PHONE_RE = re.compile(r"\b[6-9]\d{9}\b")
ORDER_RE = re.compile(r"\bSS-\d{4,6}\b", re.IGNORECASE)
DAMAGE_RE = re.compile(r"damag|broken|crack|defect|not working|wrong item|toot", re.IGNORECASE)
CANCEL_RE = re.compile(r"cancel", re.IGNORECASE)
HUMAN_RE = re.compile(r"human|real person|manager|consumer court|lawyer|legal", re.IGNORECASE)


def _tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"toolu_demo_{uuid.uuid4().hex[:12]}", "name": name, "input": args}


def _text(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


class RuleBasedDemoModel:
    name = "fake"

    def complete(self, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]) -> ModelResponse:
        content = self._decide(messages)
        stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"
        return ModelResponse(content=content, stop_reason=stop, model=self.name, usage={"input_tokens": 0, "output_tokens": 0})

    @staticmethod
    def _history(messages: list[dict[str, Any]]) -> tuple[str, list[tuple[str, dict, dict]]]:
        """Customer text since the last agent text reply, and (tool, input, result) triples so far."""
        customer: list[str] = []
        calls: dict[str, tuple[str, dict]] = {}
        results: list[tuple[str, dict, dict]] = []
        for m in messages:
            content = m["content"]
            if m["role"] == "user":
                if isinstance(content, str):
                    customer.append(content)
                    continue
                for b in content:
                    if b["type"] == "text":
                        customer.append(b["text"])
                    elif b["type"] == "tool_result":
                        name, args = calls.get(b["tool_use_id"], ("?", {}))
                        results.append((name, args, json.loads(b["content"])))
            else:
                for b in content if isinstance(content, list) else []:
                    if b["type"] == "tool_use":
                        calls[b["id"]] = (b["name"], b["input"])
        return " ".join(customer), results

    def _decide(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        said, results = self._history(messages)
        last = results[-1] if results and messages[-1]["role"] == "user" and not isinstance(messages[-1]["content"], str) else None
        done = {name for name, _, r in results if r.get("ok")}

        if HUMAN_RE.search(said) and "escalate_to_human" not in done:
            return [_tool("escalate_to_human", {"summary": "Customer asked for a human support lead.", "category": "customer_request"})]
        if last and not last[2].get("ok"):
            rules = ", ".join(last[2].get("rule_ids", []))
            return [_text(f"Sorry, I can't do that: {last[2]['message']}" + (f" (policy {rules})" if rules else ""))]

        verified = next((r["data"] for n, _, r in results if n == "find_and_verify_customer" and r.get("ok") and r["data"]["verified"]), None)
        order_match = ORDER_RE.search(said)
        if verified is None:
            contact = EMAIL_RE.search(said) or PHONE_RE.search(said)
            if contact and order_match:
                key = "email" if "@" in contact.group(0) else "phone"
                return [_tool("find_and_verify_customer", {key: contact.group(0), "order_id": order_match.group(0).upper()})]
            if any(n == "find_and_verify_customer" for n, _, _ in results):
                return [_text("Those details don't match our records. Please re-check your registered email or phone and order ID (policy ID-1).")]
            return [_text("Happy to help! Please share your registered email or phone number and your order ID so I can verify you (policy ID-1).")]

        order = next((r["data"] for n, _, r in reversed(results) if n == "get_order" and r.get("ok")), None)
        if order is None:
            return [_tool("get_order", {"order_id": order_match.group(0).upper() if order_match else ""})]

        if last and last[0] == "issue_refund":
            data = last[2]["data"]
            return [_text(f"Done: refund of ₹{data['amount_inr']} issued to your {data['destination'].replace('_', ' ')}. It usually arrives in 5–7 working days.")]
        if last and last[0] == "escalate_to_human":
            return [_text("I've passed this to a human support lead; they will contact you within one working day (policy ESC-1).")]
        if last and last[0] == "cancel_order":
            if order["payment_method"] == "cod":
                return [_text("Your order is cancelled. It was cash on delivery, so there is nothing to refund (policy CAN-1).")]
            return [_tool("issue_refund", {"order_id": order["order_id"], "amount_inr": order["total_inr"], "destination": "original_method", "reason": "Order cancelled before shipment"})]
        if last and last[0] == "create_return":
            data = last[2]["data"]
            amount = sum(i["price_inr"] * i["qty"] for i in order["items"]) + (order["shipping_fee_inr"] if data["basis"] == "RET-4" else 0)
            destination = "original_method" if order["payment_method"] != "cod" else "verified_upi"
            return [_tool("issue_refund", {"order_id": order["order_id"], "amount_inr": amount, "destination": destination, "reason": "Return approved"})]

        if CANCEL_RE.search(said) and "cancel_order" not in done:
            return [_tool("cancel_order", {"order_id": order["order_id"], "reason": "Customer request"})]
        if DAMAGE_RE.search(said) and "create_return" not in done:
            return [_tool("create_return", {"order_id": order["order_id"], "skus": [i["sku"] for i in order["items"]], "reason": "damaged"})]
        items = ", ".join(i["name"] for i in order["items"])
        return [_text(f"Order {order['order_id']} ({items}) is currently '{order['status']}'. How can I help with it?")]


def make_model(settings: Settings, role: str = "agent") -> ChatModel:
    """Build the configured model. `role` picks the agent or the customer-simulator settings."""
    provider = settings.provider if role == "agent" else settings.simulator_provider
    model_name = settings.model if role == "agent" else settings.simulator_model
    model: ChatModel
    if provider == "fake":
        model = RuleBasedDemoModel()
    elif provider == "ollama":
        model = OllamaModel(model_name, settings.ollama_base_url, num_ctx=settings.num_ctx, think=settings.think and role == "agent")
    elif provider == "openai_compat":
        model = OpenAICompatModel(model_name, settings.openai_base_url)
    else:
        raise ValueError(f"Unknown provider {provider!r}")
    if settings.llm_cache_dir is not None and provider != "fake":
        model = CachedModel(model, settings.llm_cache_dir)
    return model
