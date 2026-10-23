FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed \
    TRUSTDESK_STORE_PATH=/app/data/shopsetu.db \
    TRUSTDESK_CHECKPOINT_PATH=/app/data/checkpoints.db \
    TRUSTDESK_TRACE_DIR=/app/data/traces

COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /usr/local/bin/uv
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY evals ./evals
COPY docs/policy ./docs/policy
COPY config ./config
RUN uv sync --frozen --no-dev --extra langfuse

# Bake the small embedding model into the image so hybrid retrieval works offline.
RUN uv run python -c "from trustdesk.retrieval.index import FastEmbedder; FastEmbedder()"

EXPOSE 8000
CMD ["sh", "-c", "uv run --no-sync trustdesk seed && uv run --no-sync trustdesk serve --host 0.0.0.0 --port 8000"]
