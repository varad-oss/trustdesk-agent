"""Estimator tests against hand-computed values."""

from __future__ import annotations

import math

import pytest

from evals.metrics import bootstrap_ci, mean_pass_hat_k, pass_hat_k, percentile, wilson_interval


def test_pass_hat_k_hand_values():
    assert pass_hat_k(3, 3, 3) == 1.0
    assert pass_hat_k(3, 2, 1) == pytest.approx(2 / 3)
    assert pass_hat_k(3, 2, 2) == pytest.approx(1 / 3)  # C(2,2)/C(3,2) = 1/3
    assert pass_hat_k(3, 2, 3) == 0.0
    assert pass_hat_k(4, 3, 2) == pytest.approx(3 / 6)  # C(3,2)/C(4,2)
    assert pass_hat_k(5, 0, 1) == 0.0


def test_pass_hat_k_is_monotone_in_k():
    vals = [pass_hat_k(5, 4, k) for k in range(1, 6)]
    assert vals == sorted(vals, reverse=True)


def test_pass_hat_k_rejects_bad_input():
    with pytest.raises(ValueError):
        pass_hat_k(3, 4, 1)
    with pytest.raises(ValueError):
        pass_hat_k(3, 1, 4)


def test_mean_over_tasks():
    assert mean_pass_hat_k([(3, 3), (3, 0)], 1) == 0.5
    assert mean_pass_hat_k([(3, 3), (3, 2)], 2) == pytest.approx((1 + 1 / 3) / 2)


def test_wilson_hand_value():
    lo, hi = wilson_interval(8, 10)
    assert lo == pytest.approx(0.4902, abs=1e-3) and hi == pytest.approx(0.9433, abs=1e-3)
    assert wilson_interval(0, 10)[0] == 0.0 and wilson_interval(10, 10)[1] == pytest.approx(1.0)
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_bootstrap_resamples_groups_and_brackets_mean():
    groups = [0.0, 1.0, 1.0, 1.0]
    lo, hi = bootstrap_ci(groups, lambda g: sum(g) / len(g), n_resamples=500, seed=1)
    assert 0.0 <= lo <= 0.75 <= hi <= 1.0
    assert bootstrap_ci([1.0, 1.0], lambda g: sum(g) / len(g)) == (1.0, 1.0)
    assert all(math.isnan(x) for x in bootstrap_ci([], lambda g: 0.0))


def test_percentile():
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([5], 95) == 5
