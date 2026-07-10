from fractions import Fraction as F

from fretsure.oracle.core import (
    CHECKER_VERSION,
    OracleResult,
    check_playability,
    passes_optimistic,
)
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.tab import Tab, TabNote

TUN = (40, 45, 50, 55, 59, 64)


def _t(notes: list[TabNote]) -> Tab:
    return Tab(tuple(notes), TUN, 0)


# A 2-string barre at fret 2: comfortable even for the pessimistic hand.
GREEN_TAB = _t([TabNote(F(0), F(1), 0, 2, 1, "p"), TabNote(F(0), F(1), 1, 2, 1, "i")])
# Fret 1 and fret 15 in one frame: unreachable even for the optimistic hand.
RED_TAB = _t([TabNote(F(0), F(1), 0, 1, 1, "p"), TabNote(F(0), F(1), 1, 15, 4, "i")])
# ~100.8 mm span between fingers 1 and 4: within optimistic (110) but not
# pessimistic (90) reach -> borderline.
AMBER_TAB = _t([TabNote(F(0), F(1), 0, 1, 1, "p"), TabNote(F(0), F(1), 1, 4, 4, "i")])


def test_green_verdict() -> None:
    assert check_playability(GREEN_TAB, MEDIAN_HAND).verdict == "GREEN"


def test_red_verdict() -> None:
    assert check_playability(RED_TAB, MEDIAN_HAND).verdict == "RED"


def test_amber_verdict() -> None:
    assert check_playability(AMBER_TAB, MEDIAN_HAND).verdict == "AMBER"


def test_version_stamp() -> None:
    r = check_playability(GREEN_TAB, MEDIAN_HAND)
    assert isinstance(r, OracleResult)
    assert r.checker_version == CHECKER_VERSION
    assert r.profile_version == MEDIAN_HAND.version


def test_deterministic() -> None:
    assert check_playability(AMBER_TAB, MEDIAN_HAND) == check_playability(
        AMBER_TAB, MEDIAN_HAND
    )


def test_red_carries_diagnostics() -> None:
    r = check_playability(RED_TAB, MEDIAN_HAND)
    assert r.diagnostics
    assert any(d.violation_type == "FRET_SPAN" for d in r.diagnostics)


def test_green_has_no_diagnostics() -> None:
    assert check_playability(GREEN_TAB, MEDIAN_HAND).diagnostics == ()


def test_passes_optimistic_equivalent_to_not_red() -> None:
    for tab in (GREEN_TAB, RED_TAB, AMBER_TAB):
        fast = passes_optimistic(tab, MEDIAN_HAND)
        slow = check_playability(tab, MEDIAN_HAND).verdict != "RED"
        assert fast == slow


def test_malformed_fingering_is_red() -> None:
    # fret>0 with finger 0 is an invalid exhibited fingering; must never be GREEN
    t = _t([TabNote(F(0), F(1), 0, 1, 0, "p"), TabNote(F(0), F(1), 1, 20, 0, "i")])
    r = check_playability(t, MEDIAN_HAND)
    assert r.verdict == "RED"
    assert any(d.violation_type == "MALFORMED_FINGERING" for d in r.diagnostics)


def test_capo_past_neck_end_is_red() -> None:
    # fret 20 with capo 7 = absolute fret 27, off a 22-fret neck
    t = Tab((TabNote(F(0), F(1), 0, 20, 1, "p"),), TUN, 7)
    r = check_playability(t, MEDIAN_HAND)
    assert r.verdict == "RED"
    assert any(d.violation_type == "RANGE" for d in r.diagnostics)


def test_out_of_domain_finger_is_red() -> None:
    # left_finger 5 does not exist; must not inflate d_max into a false GREEN.
    # fret1<->fret4 span is ~100.8mm > pessimistic d_max(1,4)=90, unreachable.
    t = _t([TabNote(F(0), F(1), 0, 1, 1, "p"), TabNote(F(0), F(1), 1, 4, 5, "i")])
    r = check_playability(t, MEDIAN_HAND)
    assert r.verdict == "RED"
    assert any(d.violation_type == "MALFORMED_FINGERING" for d in r.diagnostics)


def test_invalid_right_finger_is_red_without_crashing() -> None:
    t = _t([TabNote(F(0), F(1), 0, 3, 1, "z")])  # 'z' is not a right finger
    r = check_playability(t, MEDIAN_HAND)
    assert r.verdict == "RED"
    assert any(d.violation_type == "MALFORMED_FINGERING" for d in r.diagnostics)


