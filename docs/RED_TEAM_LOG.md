# Red-team log

Manual, adaptive attacks made in the web console or `trustdesk chat` that are **not** in the automated suite.
The spec needs at least 5 entries, written by Varad. Nothing below is filled in by the assistant: these are
your own attempts.

How to run one: `make seed && make serve` (or `make chat`), pick a defense level, try to break a rule, then note
what happened. Reset the store between attempts with `trustdesk seed --reseed`. Each turn's trace:
`trustdesk trace <trace_id>` (the trace ID is on `GET /sessions/<id>`).

| # | Date | Level | Goal (rule to break) | What I tried (verbatim or summary) | What happened | Trace ID | What changed afterwards |
|---|---|---|---|---|---|---|---|
| 1 | | | | | | | |
| 2 | | | | | | | |
| 3 | | | | | | | |
| 4 | | | | | | | |
| 5 | | | | | | | |

Ideas not yet in the suite: multi-turn trust building before the ask; Hinglish-only injection; an injection
that targets `search_policy` queries; asking the agent to "summarise the review verbatim" and checking whether
the datamark survives; unicode look-alikes for `</untrusted>`; splitting one REF-4 refund into two ₹4,999 refunds.
