from fractions import Fraction as F

from fretsure.difficulty.tiers import ADVANCED, BEGINNER, INTERMEDIATE, tier_violations
from fretsure.geometry import STANDARD_TUNING
from fretsure.tab import Tab, TabNote

TUN = STANDARD_TUNING


def _t(notes: list[TabNote]) -> Tab:
    return Tab(tuple(notes), TUN, 0)


def test_tier_ordering_widens() -> None:
    assert BEGINNER.max_position < INTERMEDIATE.max_position < ADVANCED.max_position
    assert not BEGINNER.allow_barre and ADVANCED.allow_barre


def test_beginner_rejects_high_position() -> None:
    t = _t([TabNote(F(0), F(1), 0, 8, 1, "p")])  # fret 8 > beginner max_position 5
    assert any("position" in v for v in tier_violations(t, BEGINNER))
    assert tier_violations(t, ADVANCED) == []


def test_beginner_rejects_barre() -> None:
    t = _t([TabNote(F(0), F(1), 0, 2, 1, "p"), TabNote(F(0), F(1), 1, 2, 1, "i")])  # finger 1 barre
    assert any("barre" in v for v in tier_violations(t, BEGINNER))
    assert not any("barre" in v for v in tier_violations(t, INTERMEDIATE))


def test_beginner_rejects_dense_frame() -> None:
    notes = [TabNote(F(0), F(1), s, 2, 1, "p") for s in range(3)]  # 3 simultaneous > 2
    assert any("simultaneous" in v for v in tier_violations(_t(notes), BEGINNER))


def test_simple_first_position_clean_for_beginner() -> None:
    t = _t([TabNote(F(0), F(1), 3, 2, 1, "p"), TabNote(F(1), F(1), 4, 1, 1, "i")])
    assert tier_violations(t, BEGINNER) == []


def test_deterministic() -> None:
    t = _t([TabNote(F(0), F(1), 0, 8, 1, "p")])
    assert tier_violations(t, BEGINNER) == tier_violations(t, BEGINNER)
