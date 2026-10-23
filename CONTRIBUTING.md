# Contributing

Thanks for your interest. Issues and small pull requests are welcome.

## Setup

```bash
uv sync --all-extras        # Python 3.12, managed by uv
make test                   # offline tests; no model or network needed
make lint
```

For anything that runs the agent, install [Ollama](https://ollama.com) and `ollama pull qwen3:8b`.

## Ground rules

- **Tests are the source of truth.** Don't weaken, skip or delete a test to make a change pass.
- **No special-casing eval tasks.** Fixes must be general; `make validate` must stay green.
- **Every reported number comes from `results/`**, produced by a Makefile command. Never edit `docs/EVAL_REPORT.md` by hand; run `make report`.
- **Free and local.** Don't add a dependency on a paid API or hosted service.
- **Tools return errors as data** and never raise into the agent; code before `interrupt()` must be side-effect free.

## Adding an eval task

1. Add `evals/tasks/<category>-NN-<slug>.yaml` (copy an existing task of the same category).
2. Include `golden_actions` (a correct sequence of tool calls) and `forbidden_probes` (actions policy must refuse).
3. Run `make validate`: the task must replay to its expected end state, and every probe must be refused.

Design background: [docs/DECISIONS.md](docs/DECISIONS.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
