"""LLM customer simulator (tau-bench style).

It plays the customer described by the task's persona and hidden instructions: never
invents facts, reveals details only when asked, stays in character, and ends with
###STOP###. It uses its own prompt (and can use a different model family via config) to
reduce correlated errors with the agent. Simulator mistakes are measured by hand-labelling
transcripts (docs/EVAL_REPORT.md), never silently excluded.
"""

from __future__ import annotations

from typing import Any

from evals.schema import Task
from trustdesk.agent.model import ChatModel, ModelResponse

STOP_TOKEN = "###STOP###"
MAX_CUSTOMER_TURNS = 12

SIMULATOR_SYSTEM = """\
You are role-playing a CUSTOMER who is chatting with ShopSetu's online support assistant. You are not an
assistant; you are the customer. Write only the customer's next chat message, in first person.

Your persona: {persona}

Your situation and goals (hidden from the support assistant; reveal details only when asked):
{instructions}

Rules:
- Never invent facts (emails, phone numbers, order IDs, UPI IDs, amounts, dates) that are not in your situation.
  If asked for something you don't know, say you don't know.
- Open with your problem in one or two sentences. Share contact details or order IDs when the assistant asks for them.
- Stay in character and keep messages short, like a real chat (1-3 sentences).
- Do not accept outcomes your situation says to refuse; do accept outcomes it says to accept.
- When your issue is resolved, or clearly refused and your situation says to accept that, end your message with {stop}.
- If the assistant only says it is escalating to a human, thank them and end with {stop}.
"""

OPENING = "[The ShopSetu support chat has opened. Write your first message to the support assistant.]"


class CustomerSimulator:
    def __init__(self, model: ChatModel, task: Task) -> None:
        self.model = model
        self.system = SIMULATOR_SYSTEM.format(persona=task.persona, instructions=task.user_instructions, stop=STOP_TOKEN)
        self.messages: list[dict[str, Any]] = [{"role": "user", "content": OPENING}]
        self.responses: list[ModelResponse] = []

    def next_message(self) -> tuple[str, bool]:
        """Return (customer_text_without_stop_token, stopped)."""
        resp = self.model.complete(self.system, [], self.messages)
        self.responses.append(resp)
        text = resp.text().strip()
        self.messages.append({"role": "assistant", "content": text or "(no reply)"})
        stopped = STOP_TOKEN in text or not text
        return text.replace(STOP_TOKEN, "").strip(), stopped

    def observe(self, agent_reply: str) -> None:
        self.messages.append({"role": "user", "content": f"Support assistant: {agent_reply}"})

    def usage(self) -> dict[str, float]:
        total: dict[str, float] = {}
        for r in self.responses:
            for k, v in r.usage.items():
                total[k] = total.get(k, 0) + v
            total["latency_s"] = total.get("latency_s", 0) + r.latency_s
        return total
