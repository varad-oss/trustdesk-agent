"""Dollar cost from token usage, using prices from config/pricing.yaml (with the date checked)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from trustdesk.config import REPO_ROOT

PRICING_PATH = REPO_ROOT / "config" / "pricing.yaml"


@lru_cache(maxsize=4)
def load_pricing(path: Path = PRICING_PATH) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def cost_usd(model: str, usage: dict[str, int]) -> float:
    """Cost of one call. Unknown models cost 0 and are flagged by the report, not guessed."""
    prices = load_pricing()["models"].get(model)
    if prices is None:
        return 0.0
    per = 1_000_000
    return (
        usage.get("input_tokens", 0) * prices["input"] / per
        + usage.get("output_tokens", 0) * prices["output"] / per  # thinking tokens are billed as output
        + usage.get("cache_read_input_tokens", 0) * prices["cache_read"] / per
        + usage.get("cache_creation_input_tokens", 0) * prices["cache_write"] / per
    )


def add_usage(total: dict[str, float], usage: dict[str, float]) -> dict[str, float]:
    out = dict(total)
    for key, value in usage.items():
        out[key] = out.get(key, 0) + value
    return out
