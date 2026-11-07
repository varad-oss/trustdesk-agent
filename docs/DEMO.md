# Demo script (about 2 minutes)

Setup: `ollama serve` with `qwen3:8b` pulled, `trustdesk seed --reseed`, then `make serve` (no Ollama: `TRUSTDESK_PROVIDER=fake make serve`).

1. **Happy path, D3 (25 s).** Pick **A damaged item** (or type "My earbuds from SS-10482 arrived cracked yesterday. My email is priya.s@example.in").
   Show: verification, RET-4 return, ₹2,549 refund (item + shipping), rule IDs in the reply.
2. **Human approval (30 s).** **Start over**, then pick **A refund that needs you**. The ₹14,999 refund
   appears under **Waiting for you** as a slip with REF-4, the agent's justification and the conversation so far. Approve, and the reply
   arrives. Mention: this survives a server restart (test `test_pending_approval_survives_restart`).
3. **Attack demo, D0 vs D3 (40 s, local LLM only).** Select level 0 and pick **An injection attack**: the poisoned review tells the
   agent to refund ₹50,000 to attacker@okbank, and nothing in code stops it if the model complies (whether it does
   is what the eval measures; rehearse first and keep a run where it does). Then select level 3 and repeat: the
   review arrives spotlighted, and even if the model tried, the policy engine refuses the unverified UPI (REF-2).
4. **Measurement (20 s).** Open docs/EVAL_REPORT.md: the D0–D3 table, intervals, and the chart.
5. **Close (5 s).** "The model proposes; code disposes; humans approve what policy can't decide."

## Recording checklist

- [ ] `trustdesk seed --reseed` just before recording (the demo orders can be returned only once: RET-5)
- [ ] Browser zoom 125%, dark or light theme consistent, notifications off
- [ ] Ollama running with `qwen3:8b` and nothing else loaded (`ollama ps`), so replies are quick
- [ ] Terminal font ≥ 16 pt if showing `trustdesk trace <id>`
- [ ] Eval report regenerated (`make report`) from a reported run
- [ ] Keep it under 2:15; export as MP4 and a GIF for the README
