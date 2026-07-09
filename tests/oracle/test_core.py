from fractions import Fraction as F

from fretsure.oracle.core import CHECKER_VERSION, OracleResult, check_playability
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
