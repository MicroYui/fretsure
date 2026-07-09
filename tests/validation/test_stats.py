import os
from pathlib import Path

from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.oracle.validation.stats import (
    ConfusionMatrix,
    cohen_kappa,
    confusion_from_labeled,
    green_false_accept_upper_bound,
    load_labeled,
)

FIXTURE = str(
    Path(__file__).resolve().parents[2] / "data" / "gold" / "sample_labeled.jsonl"
)


def test_fixture_exists() -> None:
    assert os.path.exists(FIXTURE)


def test_upper_bound_zero_false_accepts_matches_closed_form() -> None:
    cm = ConfusionMatrix(10, 0, 0, 0, 0, 0)
    ub = green_false_accept_upper_bound(cm, 0.975)
    assert abs(ub - (1 - 0.025**0.1)) < 1e-9  # closed form for x=0
    assert abs(ub - 0.3085) < 1e-3


def test_upper_bound_with_a_false_accept_is_positive() -> None:
    cm = ConfusionMatrix(5, 1, 0, 0, 0, 0)  # 1 false accept in 6 GREEN
    ub = green_false_accept_upper_bound(cm)
    assert 0.0 < ub < 1.0


def test_upper_bound_empty_green_is_zero() -> None:
    cm = ConfusionMatrix(0, 0, 3, 3, 0, 0)
    assert green_false_accept_upper_bound(cm) == 0.0


def test_fixture_has_no_green_false_accepts() -> None:
    cm = confusion_from_labeled(load_labeled(FIXTURE), MEDIAN_HAND)
    assert cm.green_unplayable == 0  # the soundness guarantee on the sample
    assert cm.green_playable >= 1  # coverage: some GREEN present
    assert cm.red_unplayable >= 1  # coverage: some RED present


def test_cohen_kappa_perfect_is_one() -> None:
    cm = ConfusionMatrix(5, 0, 0, 5, 0, 0)
    assert abs(cohen_kappa(cm) - 1.0) < 1e-9


def test_cohen_kappa_within_bounds_on_fixture() -> None:
    cm = confusion_from_labeled(load_labeled(FIXTURE), MEDIAN_HAND)
    assert -1.0 <= cohen_kappa(cm) <= 1.0
