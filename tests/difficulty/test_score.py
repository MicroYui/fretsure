from fractions import Fraction as F

from fretsure.difficulty.score import measured_tier
from fretsure.geometry import STANDARD_TUNING
from fretsure.tab import Tab, TabNote

TUN = STANDARD_TUNING


def _t(notes: list[TabNote]) -> Tab:
    return Tab(tuple(notes), TUN, 0)


def test_simple_first_position_is_beginner() -> None:
    t = _t([TabNote(F(0), F(1), 3, 2, 1, "p"), TabNote(F(1), F(1), 4, 1, 1, "i")])
    assert measured_tier(t) == "beginner"


def test_mid_position_is_intermediate() -> None:
    t = _t([TabNote(F(0), F(1), 0, 8, 1, "p")])  # fret 8: past beginner, within intermediate
    assert measured_tier(t) == "intermediate"


def test_barre_is_at_least_intermediate() -> None:
    t = _t([TabNote(F(0), F(1), 0, 2, 1, "p"), TabNote(F(0), F(1), 1, 2, 1, "i")])
    assert measured_tier(t) in ("intermediate", "advanced")  # barre bars beginner


def test_unplayable_is_above_advanced() -> None:
    t = _t([TabNote(F(0), F(1), 0, 1, 1, "p"), TabNote(F(0), F(1), 1, 15, 4, "i")])
    assert measured_tier(t) == "above_advanced"


def test_deterministic() -> None:
    t = _t([TabNote(F(0), F(1), 0, 8, 1, "p")])
    assert measured_tier(t) == measured_tier(t)
