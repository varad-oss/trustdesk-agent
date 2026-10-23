# TrustDesk developer commands. Every number in docs comes from a target here.
.PHONY: install test test-cov lint typecheck seed demo-tools chat chat-fake serve validate eval-retrieval eval-smoke eval-full eval-rubber-stamp report docker-up

install:
	uv sync --all-extras

test:
	uv run pytest

test-cov:
	uv run pytest --cov=trustdesk --cov=evals --cov-report=term-missing

lint:
	uv run ruff check src evals tests

typecheck:
	uv run mypy src/trustdesk

seed:
	uv run trustdesk seed --reseed

demo-tools:
	uv run trustdesk demo-tools

chat:
	uv run trustdesk chat --defense 3

chat-fake:
	uv run trustdesk chat --defense 3 --provider fake

serve:
	uv run trustdesk serve

# Replays golden actions for every task (no model calls) -> results/validation.json
validate:
	uv run trustdesk eval validate

# Recall@1/@4 and MRR for BM25, dense, hybrid -> results/retrieval.json
eval-retrieval:
	uv run trustdesk eval retrieval

# 3 tasks x 1 trial at D0 and D2 with the configured (paid) model
eval-smoke:
	uv run trustdesk eval run --levels 0,2 --trials 1 --tasks benign-01-refund-damaged-upi,edge-01-final-sale-saree,attack-01-review-upi --run-id smoke-$$(date +%Y%m%d-%H%M%S)

# Full ablation: all tasks x D0-D3 x 3 trials. Runs a pilot, prints measured cost, asks before spending.
eval-full:
	uv run trustdesk eval run --levels 0,1,2,3 --trials 3 --reported

# Defense in depth: approve everything at D3 and show the policy engine still blocks attacks
eval-rubber-stamp:
	uv run trustdesk eval run --levels 3 --trials 1 --approver rubber_stamp --reported --run-id rubber-stamp-$$(date +%Y%m%d)

report:
	uv run trustdesk eval report

docker-up:
	docker compose up --build
