"""Generic evaluation statistics. No TrustDesk imports: copy freely into other projects.

- pass^k (tau-bench): probability that ALL of k i.i.d. trials of a task succeed, estimated
  without bias from n trials with c successes as C(c, k) / C(n, k); averaged over tasks.
- Wilson score interval for a proportion (better than normal approx at small n / extreme p).
- Bootstrap over TASKS (not episodes): trials of one task are correlated, so resampling
  episodes would understate the uncertainty.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence


def pass_hat_k(n: int, c: int, k: int) -> float:
    """Unbiased estimate of P(k independent trials all succeed) from c successes in n trials."""
    if not 0 <= c <= n:
        raise ValueError("need 0 <= c <= n")
    if not 1 <= k <= n:
        raise ValueError("need 1 <= k <= n")
    return math.comb(c, k) / math.comb(n, k)


def mean_pass_hat_k(per_task: Sequence[tuple[int, int]], k: int) -> float:
    """Mean over tasks of pass^k; `per_task` holds (n_trials, n_successes)."""
    return sum(pass_hat_k(n, c, k) for n, c in per_task) / len(per_task)


def wilson_interval(successes: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """95% Wilson score interval by default. Returns (0, 0)... (0, 1) sensibly for n == 0."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def bootstrap_ci(
    groups: Sequence[object],
    statistic: Callable[[Sequence[object]], float],
    n_resamples: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float]:
    """Percentile bootstrap CI, resampling whole groups (e.g. tasks) with replacement."""
    if not groups:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    stats = sorted(statistic([groups[rng.randrange(len(groups))] for _ in groups]) for _ in range(n_resamples))
    lo = stats[int((alpha / 2) * n_resamples)]
    hi = stats[min(n_resamples - 1, int((1 - alpha / 2) * n_resamples))]
    return (lo, hi)


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile, q in [0, 100]."""
    if not values:
        return float("nan")
    xs = sorted(values)
    pos = (len(xs) - 1) * q / 100
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)
