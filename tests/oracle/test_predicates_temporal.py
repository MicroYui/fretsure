from fractions import Fraction as F

from fretsure.oracle.predicates import check_shift_speed, check_sustain
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.tab import Tab, TabNote

TUN = (40, 45, 50, 55, 59, 64)


def _t(notes: list[TabNote]) -> Tab:
    return Tab(tuple(notes), TUN, 0)


def test_shift_speed_fast_jump_flagged() -> None:
    # fret 1 -> fret 12, a sixteenth apart, fast tempo: the hand cannot travel that fast
    t = _t([TabNote(F(0), F(1), 0, 1, 1, "p"), TabNote(F(1, 4), F(1), 0, 12, 1, "p")])
    d = check_shift_speed(t, MEDIAN_HAND, tempo_bpm=120.0)
    assert d and d[0].violation_type == "SHIFT_SPEED"
    assert d[0].overage > 0


def test_shift_speed_slow_ok() -> None:
    # same jump but four beats apart at the same tempo: plenty of time
    t = _t([TabNote(F(0), F(1), 0, 1, 1, "p"), TabNote(F(4), F(1), 0, 12, 1, "p")])
    assert check_shift_speed(t, MEDIAN_HAND, tempo_bpm=120.0) == []


def test_shift_bridged_by_open_frame_still_charged() -> None:
    # fret 2 -> (open-only frame) -> fret 20, all within a tiny interval: the open
    # frame must not reset the hand position and hide the impossible shift.
    t = _t(
        [
            TabNote(F(0), F(1), 0, 2, 1, "p"),
            TabNote(F(1, 64), F(1), 3, 0, 0, "i"),  # open-only bridging frame
            TabNote(F(2, 64), F(1), 0, 20, 1, "p"),
        ]
    )
    d = check_shift_speed(t, MEDIAN_HAND, tempo_bpm=200.0)
    assert any(x.violation_type == "SHIFT_SPEED" for x in d)


def test_shift_speed_monotonic_in_tempo() -> None:
    # faster tempo can only add shift violations, never remove them
    t = _t([TabNote(F(0), F(1), 0, 1, 1, "p"), TabNote(F(1, 2), F(1), 0, 12, 1, "p")])
    slow = check_shift_speed(t, MEDIAN_HAND, tempo_bpm=40.0)
    fast = check_shift_speed(t, MEDIAN_HAND, tempo_bpm=200.0)
    assert len(fast) >= len(slow)


def test_shift_speed_guide_finger_relaxes() -> None:
    # both frames keep finger 1 on (string0, fret1): the hand is anchored, no shift
    t = _t(
        [
            TabNote(F(0), F(1), 0, 1, 1, "p"),
            TabNote(F(0), F(1), 1, 3, 3, "i"),
            TabNote(F(1, 4), F(1), 0, 1, 1, "p"),
            TabNote(F(1, 4), F(1), 1, 12, 4, "a"),
        ]
    )
    assert check_shift_speed(t, MEDIAN_HAND, tempo_bpm=120.0) == []


def test_sustain_same_finger_diff_fret_overlap_flagged() -> None:
    # finger 1 held at fret 3 (beats 0-2) while also needed at fret 5 (beats 1-2)
    t = _t([TabNote(F(0), F(2), 0, 3, 1, "p"), TabNote(F(1), F(1), 1, 5, 1, "i")])
    d = check_sustain(t, MEDIAN_HAND)
    assert d and d[0].violation_type == "SUSTAIN_CONFLICT"
    assert set(d[0].offending_notes) == {0, 1}


def test_sustain_barre_same_fret_ok() -> None:
    # same finger, same fret, different strings = a held barre, not a conflict
    t = _t([TabNote(F(0), F(2), 0, 2, 1, "p"), TabNote(F(1), F(1), 1, 2, 1, "i")])
    assert check_sustain(t, MEDIAN_HAND) == []


def test_sustain_no_overlap_ok() -> None:
    t = _t([TabNote(F(0), F(1), 0, 3, 1, "p"), TabNote(F(2), F(1), 1, 5, 1, "i")])
    assert check_sustain(t, MEDIAN_HAND) == []
