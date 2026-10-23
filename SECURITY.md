# Security

TrustDesk is a research and portfolio project. It uses a fictional store with synthetic customers and is not meant to
run in production: the approval queue has no approver authentication and the API is a single-process demo.

## Reporting an issue

If you find a way to make the agent break one of the invariants it is designed to keep, at defense level D2 or D3,
please open an issue with:

- the defense level and model you used;
- the conversation (or the task file) that reproduces it;
- which rule was broken (for example a refund to an unverified UPI ID, REF-2).

Examples: a refund above what was paid, a refund to an unverified destination, an action on another customer's
account, or a high-risk action executed without approval. These are exactly the bugs the project exists to catch, and
confirmed ones will be added to the evaluation suite.

Prompt injections that only change what the agent *says* (without breaking an invariant) are interesting too: they
belong in [docs/RED_TEAM_LOG.md](docs/RED_TEAM_LOG.md) style reports.

Do not include real personal data or API keys in reports. None are needed: everything runs locally on synthetic data.
