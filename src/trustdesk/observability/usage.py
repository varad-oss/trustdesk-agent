"""Usage accounting. Models run locally for free, so we track tokens and seconds, not dollars."""

from __future__ import annotations


def add_usage(total: dict[str, float], usage: dict[str, float]) -> dict[str, float]:
    out = dict(total)
    for key, value in usage.items():
        out[key] = out.get(key, 0) + value
    return out
