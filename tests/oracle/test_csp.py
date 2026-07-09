import random
from fractions import Fraction as F

from fretsure.oracle.csp import (
    feasible_finger_assignment,
    feasible_finger_assignment_bruteforce,
    feasible_fingerings,
)
from fretsure.oracle.predicates import check_barre, check_fret_span
from fretsure.oracle.profiles import MEDIAN_HAND, SMALL_HAND
from fretsure.tab import Frame, Tab, TabNote

TUN = (40, 45, 50, 55, 59, 64)


def _frame(specs: list[tuple[int, int]]) -> Frame:
    # specs: list of (string, fret); left_finger irrelevant to the search
    return tuple(TabNote(F(0), F(1), s, fr, 0, "p") for s, fr in specs)


def test_all_open_frame_trivially_feasible() -> None:
    fr = _frame([(0, 0), (1, 0)])
    assert feasible_finger_assignment(fr, MEDIAN_HAND) == ()


def test_easy_chord_feasible() -> None:
    fr = _frame([(0, 1), (1, 2)])
    assert feasible_finger_assignment(fr, MEDIAN_HAND) is not None


def test_impossible_stretch_infeasible() -> None:
    # fret 1 and fret 15 in one frame — no hand reaches that
    fr = _frame([(0, 1), (1, 15)])
    assert feasible_finger_assignment(fr, MEDIAN_HAND) is None


def test_same_fret_span_different_position_differs() -> None:
    # same 4-fret span, different position -> different feasibility (mm geometry,
    # not fret count). High up the neck the frets are closer, so it is reachable.
    low = _frame([(0, 1), (1, 5)])  # frets 1..5, near the nut (wide)
    high = _frame([(0, 10), (1, 14)])  # frets 10..14, up the neck (narrow)
    lo_ok = feasible_finger_assignment(low, SMALL_HAND) is not None
    hi_ok = feasible_finger_assignment(high, SMALL_HAND) is not None
    assert hi_ok  # high position is reachable
    assert lo_ok != hi_ok  # the same fret-count span differs by position


def test_nversion_fast_matches_bruteforce() -> None:
    # Differential: the pruned DFS and the exhaustive spec must agree on
    # feasibility for every frame. A bug in one would not be mirrored in the other.
    rng = random.Random(20260709)
    for _ in range(500):
        n = rng.randint(1, 4)
        strings = rng.sample(range(6), n)  # distinct strings
        specs = [(s, rng.randint(1, 14)) for s in strings]
        fr = _frame(specs)
        fast = feasible_finger_assignment(fr, MEDIAN_HAND) is not None
        slow = feasible_finger_assignment_bruteforce(fr, MEDIAN_HAND) is not None
        assert fast == slow, f"disagree on {specs}: fast={fast} slow={slow}"


def test_returned_assignment_is_actually_valid() -> None:
    # whatever the fast search returns must pass the exhaustive validity check
    rng = random.Random(1)
    for _ in range(100):
        n = rng.randint(1, 4)
        strings = rng.sample(range(6), n)
        specs = [(s, rng.randint(1, 12)) for s in strings]
        fr = _frame(specs)
        a = feasible_finger_assignment(fr, MEDIAN_HAND)
        if a is not None:
            # cross-check via bruteforce validity on the exact returned tuple
            from fretsure.oracle.csp import assignment_valid

            fretted = [nt for nt in fr if nt.fret > 0]
            assert assignment_valid(fretted, a, MEDIAN_HAND, capo=0)


def test_feasible_fingerings_lists_valid_assignments() -> None:
    fr = _frame([(0, 2), (1, 4)])
    fings = feasible_fingerings(fr, MEDIAN_HAND)
    assert fings  # at least one
    from fretsure.oracle.csp import assignment_valid

    fretted = [nt for nt in fr if nt.fret > 0]
    assert all(assignment_valid(fretted, a, MEDIAN_HAND, capo=0) for a in fings)


def test_check_fret_span_flags_bad_given_fingering() -> None:
    t = Tab(
        (TabNote(F(0), F(1), 0, 1, 1, "p"), TabNote(F(0), F(1), 1, 15, 2, "i")),
        TUN,
        0,
    )
    d = check_fret_span(t, MEDIAN_HAND)
    assert d and d[0].violation_type == "FRET_SPAN"
    assert d[0].overage > 0


def test_check_fret_span_ok_for_close_notes() -> None:
    t = Tab(
        (TabNote(F(0), F(1), 0, 3, 1, "p"), TabNote(F(0), F(1), 1, 4, 2, "i")),
        TUN,
        0,
    )
    assert check_fret_span(t, MEDIAN_HAND) == []


def test_check_barre_flags_note_under_barre() -> None:
    # finger 1 barres strings 0 and 3 at fret 5; a string-1 note at fret 2 (< 5)
    # sits under the barre -> infeasible
    t = Tab(
        (
            TabNote(F(0), F(1), 0, 5, 1, "p"),
            TabNote(F(0), F(1), 3, 5, 1, "a"),
            TabNote(F(0), F(1), 1, 2, 2, "i"),
        ),
        TUN,
        0,
    )
    d = check_barre(t, MEDIAN_HAND)
    assert d and d[0].violation_type == "BARRE_INFEASIBLE"


def test_check_barre_ok_normal_barre() -> None:
    # finger 1 barres strings 0..1 at fret 2; finger 3 presses string 2 at fret 4 (higher) -> fine
    t = Tab(
        (
            TabNote(F(0), F(1), 0, 2, 1, "p"),
            TabNote(F(0), F(1), 1, 2, 1, "i"),
            TabNote(F(0), F(1), 2, 4, 3, "m"),
        ),
        TUN,
        0,
    )
    assert check_barre(t, MEDIAN_HAND) == []
