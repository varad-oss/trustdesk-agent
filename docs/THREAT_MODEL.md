# Threat model

## Assets

| Asset | Why it matters |
|---|---|
| Money | Refunds and goodwill credit move real value (here: rows in `refunds`, `goodwill_credits`). |
| Customer PII | Emails, phones, addresses, UPI IDs of every customer in the store. |
| Store integrity | Order state (cancellations, address changes), the audit log, the approval queue. |
| Agent configuration | System prompt, tool names/parameters (SEC-2); leaking them helps attackers craft payloads. |

## Actors

1. **Malicious customer** — talks to the agent directly; may lie, pressure, paste fake "system notices",
   or claim to act for someone else.
2. **Content attacker** — controls text the agent later reads but never talks to it: product reviews (public),
   delivery notes (courier/customer), seller messages and old ticket messages.
3. **Honest customer** — must still be served (utility), including under attack.
4. **Support lead** — trusted approver; can be careless (the rubber-stamp experiment).

## Entry points and trust boundaries

```
 customer chat ──(untrusted, but a principal)──► agent ◄──(untrusted DATA)── reviews / delivery notes / tickets
                                                  │
                                    tool calls ───┼──► tools ──► policy engine ──► database   (trusted, code)
                                                  │
                                 approval_gate ◄──┴── support lead                         (trusted human)
```

The model sits inside the untrusted zone: anything it emits is a *request*. The trust boundary is the tool layer.

## Threats mapped to OWASP

| ID | Threat | OWASP | Example task(s) | Defense(s) |
|---|---|---|---|---|
| T1 | Indirect injection in a review redirects a refund | LLM01; ASI01 goal hijack | attack-01 | D1 spotlighting; D2 REF-2 destination check, `resolve_refund_upi` |
| T2 | Delivery note / ticket "override" grants credit | LLM01; ASI06 memory & context poisoning | attack-02, attack-03 | D1; D2 GW-1 (delay required, ₹200 cap); D3 approval |
| T3 | Seller message inflates a refund | ASI06; ASI02 tool misuse | attack-10 | D2 REF-3 cap; D3 REF-4 approval |
| T4 | Customer acts on another customer's order | ASI03 identity & privilege abuse | attack-05 | D2 ID-3 in every write and read tool |
| T5 | Customer extracts another customer's PII | ASI03; LLM02 sensitive disclosure | attack-04 | D2 identity gate on reads + masking (ID-2) |
| T6 | Skip verification ("I'm obviously the owner") | ASI03 | attack-06 | D2 ID-1 gate (writes refuse until verified) |
| T7 | Pasted fake "SYSTEM NOTICE" in chat | LLM01 (direct); ASI01 | attack-07 | D1 hierarchy; D2 REF-2 |
| T8 | Pressure for oversized goodwill | ASI09 human–agent trust exploitation | attack-08 | D2 GW-1 cap → needs_approval; D3 human |
| T9 | Review asks the agent to echo full phone/address | LLM02; LLM01 | attack-09 | D2 masking: the full values never reach the model |
| T10 | Approver rubber-stamps a malicious request | ASI09 | `make eval-rubber-stamp` | Deny rules are evaluated before and after approval; only `needs_approval` can be approved |
| T11 | Retry / crash double-applies a refund | ASI02 | integration tests | Idempotency keys (`session:tool_use_id`), UNIQUE key columns |
| T12 | Prompt/tool disclosure | LLM07 system prompt leakage; SEC-2 | manual red-team | Prompt instruction only (no secrets in the prompt by design) |

## Lethal trifecta analysis

The "lethal trifecta" (Willison): an agent with (1) access to private data, (2) exposure to untrusted content and
(3) a way to send data out can be made to exfiltrate.

| Leg | Present here? | How a defense cuts it |
|---|---|---|
| Private data | Yes: customer records, addresses, UPI IDs | D2 masks PII in tool outputs and gates reads to the verified customer, so the model mostly *never sees* other customers' data or full values. |
| Untrusted content | Yes: reviews, delivery notes, tickets, the chat itself | D1 spotlights it (reduces, does not remove, influence). |
| Exfiltration channel | Partly: (a) the chat reply to the current customer; (b) money movement to an attacker-chosen UPI ID | (b) is cut in code by REF-2 (destinations only from the DB) and REF-3/REF-4. (a) remains: the agent can say anything to the customer it is talking to, which is why (1) is cut at the source rather than trusting the model to stay quiet. There is no web/email/URL-fetch tool, by design. |

Residual risk: at D2+ an injection can still make the agent *say* wrong things, refuse a valid request (utility
loss), or escalate unnecessarily. It cannot move money outside policy or reveal data the tools never returned.
