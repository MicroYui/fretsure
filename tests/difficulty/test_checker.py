from fractions import Fraction as F

from fretsure.difficulty.checker import TierResult, check_tier
from fretsure.difficulty.tiers import ADVANCED, BEGINNER
from fretsure.geometry import STANDARD_TUNING
from fretsure.tab import Tab, TabNote

TUN = STANDARD_TUNING


def _t(notes: list[TabNote]) -> Tab:
    return Tab(tuple(notes), TUN, 0)


def test_simple_first_position_meets_beginner() -> None:
    t = _t([TabNote(F(0), F(1), 3, 2, 1, "p"), TabNote(F(1), F(1), 4, 1, 1, "i")])
    r = check_tier(t, BEGINNER)
    assert isinstance(r, TierResult)
    assert r.meets and r.playable == "GREEN" and r.tier_violations == ()


def test_high_position_does_not_meet_beginner() -> None:
    t = _t([TabNote(F(0), F(1), 0, 8, 1, "p")])  # fret 8 > beginner max_position/max_fret 5
    r = check_tier(t, BEGINNER)
    assert not r.meets
    assert check_tier(t, ADVANCED).meets  # advanced allows it


def test_playable_but_barre_fails_beginner() -> None:
    # a 2-string barre at fret 2: geometrically fine, but beginners can't barre
    t = _t([TabNote(F(0), F(1), 0, 2, 1, "p"), TabNote(F(0), F(1), 1, 2, 1, "i")])
    r = check_tier(t, BEGINNER)
    assert not r.meets
    assert any("barre" in v for v in r.tier_violations)


def test_unreachable_stretch_not_playable_any_tier() -> None:
    t = _t([TabNote(F(0), F(1), 0, 1, 1, "p"), TabNote(F(0), F(1), 1, 15, 4, "i")])
    assert not check_tier(t, ADVANCED).meets  # RED under geometry
