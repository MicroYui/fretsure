from fractions import Fraction as F

from fretsure.oracle.predicates import (
    check_finger_count,
    check_finger_monotonic,
    check_one_string_one_note,
    check_range,
    check_wellformed,
)
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.tab import Tab, TabNote

TUN = (40, 45, 50, 55, 59, 64)


def _t(notes: list[TabNote]) -> Tab:
    return Tab(tuple(notes), TUN, 0)


def test_range_ok_returns_empty() -> None:
    t = _t([TabNote(F(0), F(1), 0, 5, 1, "p")])
    assert check_range(t, MEDIAN_HAND) == []


def test_range_violation_over_maxfret() -> None:
    t = _t([TabNote(F(0), F(1), 0, 99, 1, "p")])
    d = check_range(t, MEDIAN_HAND)
    assert d and d[0].violation_type == "RANGE"
    assert d[0].overage > 0
    assert d[0].offending_notes == (0,)


def test_range_negative_fret_flagged() -> None:
    t = _t([TabNote(F(0), F(1), 0, -1, 1, "p")])
    d = check_range(t, MEDIAN_HAND)
    assert d and d[0].violation_type == "RANGE"


def test_range_absolute_position_with_capo_flagged() -> None:
    # fret 20 + capo 7 = absolute 27, past a 22-fret neck
    t = Tab((TabNote(F(0), F(1), 0, 20, 1, "p"),), TUN, 7)
    d = check_range(t, MEDIAN_HAND)
    assert d and d[0].violation_type == "RANGE"
    assert d[0].overage == 27 - 22


def test_range_ok_with_capo_within_neck() -> None:
    t = Tab((TabNote(F(0), F(1), 0, 10, 1, "p"),), TUN, 5)  # absolute 15 <= 22
    assert check_range(t, MEDIAN_HAND) == []


def test_wellformed_ok() -> None:
    t = _t([TabNote(F(0), F(1), 0, 3, 1, "p"), TabNote(F(0), F(1), 1, 0, 0, "i")])
    assert check_wellformed(t, MEDIAN_HAND) == []


def test_wellformed_fretted_without_finger_flagged() -> None:
    t = _t([TabNote(F(0), F(1), 0, 3, 0, "p")])
    d = check_wellformed(t, MEDIAN_HAND)
    assert d and d[0].violation_type == "MALFORMED_FINGERING"


def test_wellformed_open_with_finger_flagged() -> None:
    t = _t([TabNote(F(0), F(1), 0, 0, 2, "p")])
    d = check_wellformed(t, MEDIAN_HAND)
    assert d and d[0].violation_type == "MALFORMED_FINGERING"


def test_one_string_one_note() -> None:
    t = _t(
        [
            TabNote(F(0), F(1), 2, 3, 1, "i"),
            TabNote(F(0), F(1), 2, 5, 2, "m"),
        ]
    )
    d = check_one_string_one_note(t, MEDIAN_HAND)
    assert d and d[0].violation_type == "ONE_STRING_ONE_NOTE"
    assert set(d[0].offending_notes) == {0, 1}


def test_one_string_one_note_ok_across_frames() -> None:
    # same string but different onsets is fine
    t = _t(
        [
            TabNote(F(0), F(1), 2, 3, 1, "i"),
            TabNote(F(1), F(1), 2, 5, 2, "m"),
        ]
    )
    assert check_one_string_one_note(t, MEDIAN_HAND) == []


def test_finger_count_over_four_distinct_frets() -> None:
    notes = [
        TabNote(F(0), F(1), s, fret, min(fret, 4), "p")
        for s, fret in zip(range(5), [1, 2, 3, 4, 5], strict=True)
    ]
    d = check_finger_count(_t(notes), MEDIAN_HAND)
    assert d and d[0].violation_type == "FINGER_COUNT"


def test_finger_count_barre_same_fret_ok() -> None:
    # 5 notes but all on the same fret (a barre) -> one finger, no violation
    notes = [TabNote(F(0), F(1), s, 2, 1, "p") for s in range(5)]
    assert check_finger_count(_t(notes), MEDIAN_HAND) == []


def test_finger_monotonic_violation() -> None:
    # higher fret assigned a lower finger
    t = _t(
        [
            TabNote(F(0), F(1), 1, 2, 3, "p"),
            TabNote(F(0), F(1), 2, 5, 1, "i"),
        ]
    )
    d = check_finger_monotonic(t, MEDIAN_HAND)
    assert d and d[0].violation_type == "FINGER_MONOTONIC"


def test_finger_monotonic_same_finger_different_fret_violation() -> None:
    # one finger cannot press two different frets (unless barre = same fret)
    t = _t(
        [
            TabNote(F(0), F(1), 1, 2, 2, "p"),
            TabNote(F(0), F(1), 2, 4, 2, "i"),
        ]
    )
    d = check_finger_monotonic(t, MEDIAN_HAND)
    assert d and d[0].violation_type == "FINGER_MONOTONIC"


def test_finger_monotonic_ok() -> None:
    t = _t(
        [
            TabNote(F(0), F(1), 1, 2, 1, "p"),
            TabNote(F(0), F(1), 2, 4, 3, "i"),
        ]
    )
    assert check_finger_monotonic(t, MEDIAN_HAND) == []


def test_measure_beat_from_onset() -> None:
    t = _t([TabNote(F(5), F(1), 0, 99, 1, "p")])  # onset 5, 4/4 -> bar 2 beat 2
    d = check_range(t, MEDIAN_HAND)[0]
    assert d.measure == 2
    assert d.beat == F(2)
