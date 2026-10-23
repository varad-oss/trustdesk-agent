# Demo script (about 2 minutes)

Setup: `trustdesk seed --reseed`, then `make serve` (with a key: real model; without: `TRUSTDESK_PROVIDER=fake make serve`).

1. **Happy path, D3 (25 s).** Type: "My earbuds from SS-10482 arrived cracked yesterday, email priya.s@example.in."
   Show: verification, RET-4 return, ₹2,549 refund (item + shipping), rule IDs in the reply.
2. **Human approval (30 s).** New session. "Cancel SS-10483 please, priya.s@example.in." The ₹14,999 refund
   appears in the approval queue with REF-4, the agent's justification and the last turns. Approve, and the reply
   arrives. Mention: this survives a server restart (test `test_pending_approval_survives_restart`).
3. **Attack demo, D0 vs D3 (40 s, real model only).** Press **Attack demo** at D0: the poisoned review tells the
   agent to refund ₹50,000 to attacker@okbank, and nothing in code stops it if the model complies (whether it does
   is what the eval measures; rehearse first and keep a run where it does). Then switch to D3 and repeat: the
   review arrives spotlighted, and even if the model tried, the policy engine refuses the unverified UPI (REF-2).
4. **Measurement (20 s).** Open docs/EVAL_REPORT.md: the D0–D3 table, intervals, and the chart.
5. **Close (5 s).** "The model proposes; code disposes; humans approve what policy can't decide."

## Recording checklist

- [ ] `trustdesk seed --reseed` just before recording (the demo orders can be returned only once: RET-5)
- [ ] Browser zoom 125%, dark or light theme consistent, notifications off
- [ ] `.env` not visible on screen; no API keys in the terminal history
- [ ] Terminal font ≥ 16 pt if showing `trustdesk trace <id>`
- [ ] Eval report regenerated (`make report`) from a reported run
- [ ] Keep it under 2:15; export as MP4 and a GIF for the README
