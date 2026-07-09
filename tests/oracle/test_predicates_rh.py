from fractions import Fraction as F

from fretsure.oracle.predicates import check_right_hand
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.tab import RightFinger, Tab, TabNote

TUN = (40, 45, 50, 55, 59, 64)


def _t(notes: list[TabNote]) -> Tab:
    return Tab(tuple(notes), TUN, 0)


def test_one_finger_two_strings_flagged() -> None:
    t = _t([TabNote(F(0), F(1), 0, 0, 0, "p"), TabNote(F(0), F(1), 3, 0, 0, "p")])
    d = check_right_hand(t, MEDIAN_HAND)
    assert d and d[0].violation_type == "RIGHT_HAND"


def test_string_order_inverted_flagged() -> None:
    # low string plucked by 'a', high string by 'p' -> finger/string order clash
    t = _t([TabNote(F(0), F(1), 0, 0, 0, "a"), TabNote(F(0), F(1), 5, 0, 0, "p")])
    d = check_right_hand(t, MEDIAN_HAND)
    assert d and d[0].violation_type == "RIGHT_HAND"


def test_over_four_simultaneous_flagged() -> None:
    fingers: list[RightFinger] = ["p", "i", "m", "a", "p"]
    t = _t([TabNote(F(0), F(1), s, 0, 0, rf) for s, rf in zip(range(5), fingers, strict=True)])
    assert any(x.violation_type == "RIGHT_HAND" for x in check_right_hand(t, MEDIAN_HAND))


def test_repeat_too_fast_flagged() -> None:
    # same finger a sixteenth apart at 120 bpm -> exceeds r_max (8 Hz)
    t = _t([TabNote(F(0), F(1), 0, 0, 0, "p"), TabNote(F(1, 16), F(1), 0, 0, 0, "p")])
    assert any(
        x.violation_type == "RIGHT_HAND"
        for x in check_right_hand(t, MEDIAN_HAND, tempo_bpm=120.0)
    )


def test_repeat_slow_ok() -> None:
    # same finger a half note apart at 90 bpm -> well within r_max
    t = _t([TabNote(F(0), F(1), 0, 0, 0, "p"), TabNote(F(2), F(1), 0, 0, 0, "p")])
    assert check_right_hand(t, MEDIAN_HAND, tempo_bpm=90.0) == []


def test_valid_arpeggio_ok() -> None:
    fingers: list[RightFinger] = ["p", "i", "m", "a"]
    t = _t([TabNote(F(0), F(1), s, 0, 0, rf) for s, rf in zip([0, 3, 4, 5], fingers, strict=True)])
    assert check_right_hand(t, MEDIAN_HAND) == []
