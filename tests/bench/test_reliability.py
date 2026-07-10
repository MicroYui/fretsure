from math import isclose

import pytest

from fretsure.bench.reliability import pass_at_k, pass_hat_k, pass_hat_k_item, wilson


def test_pass_at_k_boundaries() -> None:
    assert pass_at_k(10, 10, 5) == 1.0
    assert pass_at_k(10, 0, 5) == 0.0


def test_pass_at_k_known_value() -> None:
    # c=1 of n=10, k=5: 1 - C(9,5)/C(10,5) = 1 - 126/252 = 0.5
    assert isclose(pass_at_k(10, 1, 5), 0.5)


def test_pass_at_k_monotone_in_c() -> None:
    vals = [pass_at_k(10, c, 3) for c in range(11)]
    assert all(b >= a for a, b in zip(vals, vals[1:], strict=False))


def test_pass_at_k_invalid_k() -> None:
    with pytest.raises(ValueError):
        pass_at_k(5, 3, 6)


def test_pass_hat_k_item_unbiased() -> None:
    assert pass_hat_k_item(10, 10, 8) == 1.0  # all-success -> reliable
    assert pass_hat_k_item(10, 5, 8) == 0.0  # fewer successes than k
    assert isclose(pass_hat_k_item(10, 9, 8), 9 / 45)  # C(9,8)/C(10,8)=9/45=0.2


def test_pass_hat_k_averages_over_items() -> None:
    got = pass_hat_k([(10, 10), (10, 0)], 8)  # one reliable item, one that never passes
    assert isclose(got, 0.5)


def test_wilson_bounds() -> None:
    lo, hi = wilson(8, 10)
    assert 0.0 <= lo < hi <= 1.0
