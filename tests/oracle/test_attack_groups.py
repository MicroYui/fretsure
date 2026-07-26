"""What an attack group means to the oracle, and what it does not buy.

The point of the field is narrow: it lets a tab say "this is one sweep of the
thumb" instead of "these are five simultaneous plucks", which is the difference
between a chord a guitarist plays every day and a physical impossibility.  It
must not become a way to make anything playable by relabelling it.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from fretsure.geometry import STANDARD_TUNING
from fretsure.oracle.core import check_playability
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.tab import Tab, TabNote

_OPEN_EM = (
    (0, 0, 0),
    (1, 2, 2),
    (2, 2, 3),
    (3, 0, 0),
    (4, 0, 0),
    (5, 0, 0),
)


def _chord(
    fingers: tuple[str, ...],
    groups: tuple[int, ...],
    shape: tuple[tuple[int, int, int], ...] = _OPEN_EM,
) -> Tab:
    return Tab(
        tuple(
            TabNote(Fraction(0), Fraction(2), string, fret, left, finger, group)
            for (string, fret, left), finger, group in zip(shape, fingers, groups, strict=True)
        ),
        STANDARD_TUNING,
        0,
    )


def test_a_strummed_open_chord_is_playable() -> None:
    """The thumb sweeps the bass strings; i-m-a take the top three."""

    tab = _chord(("p", "p", "p", "i", "m", "a"), (1, 1, 1, 0, 0, 0))
    assert check_playability(tab, MEDIAN_HAND).verdict == "GREEN"


def test_the_same_chord_as_six_independent_plucks_is_not() -> None:
    """Six fingers is what it would take, and a hand has four."""

    tab = _chord(("p", "p", "p", "i", "m", "a"), (0, 0, 0, 0, 0, 0))
    assert check_playability(tab, MEDIAN_HAND).verdict == "RED"


def test_a_group_may_not_skip_a_string() -> None:
    """A sweep crosses the strings between its ends; it cannot jump one."""

    tab = _chord(("p", "i", "p", "m", "a", "a"), (1, 0, 1, 0, 0, 0))
    assert check_playability(tab, MEDIAN_HAND).verdict == "RED"


def test_a_group_is_one_finger() -> None:
    """Two fingers labelled as one sweep are two motions wearing one name."""

    tab = _chord(("p", "i", "m", "a", "p", "i"), (1, 1, 0, 0, 0, 0))
    assert check_playability(tab, MEDIAN_HAND).verdict == "RED"


def test_a_group_of_one_is_not_a_gesture() -> None:
    """Nothing is swept, so the label buys nothing and is rejected as malformed."""

    tab = _chord(("p", "i", "m", "a", "p", "i"), (1, 0, 0, 0, 0, 0))
    assert check_playability(tab, MEDIAN_HAND).verdict == "RED"


def test_grouping_does_not_create_fingers() -> None:
    """Five distinct motions still need five fingers, however they are labelled."""

    shape = ((0, 0, 0), (1, 2, 2), (2, 2, 3), (3, 0, 0), (4, 0, 0), (5, 0, 0))
    tab = _chord(("p", "i", "m", "a", "p", "i"), (0, 0, 0, 0, 0, 0), shape)
    assert check_playability(tab, MEDIAN_HAND).verdict == "RED"


def test_a_sweep_still_obeys_the_left_hand() -> None:
    """Relabelling the right hand cannot make an impossible stretch playable."""

    shape = ((0, 1, 1), (1, 5, 2), (2, 9, 3), (3, 13, 4), (4, 0, 0), (5, 0, 0))
    tab = _chord(("p", "p", "p", "p", "i", "m"), (1, 1, 1, 1, 0, 0), shape)
    assert check_playability(tab, MEDIAN_HAND).verdict == "RED"


def test_an_ungrouped_tab_is_judged_exactly_as_before() -> None:
    """The default is not a new semantics; it is the old one, unchanged."""

    plain = Tab(
        tuple(
            TabNote(Fraction(0), Fraction(1), string, fret, left, finger)
            for (string, fret, left), finger in zip(_OPEN_EM[:4], "pima", strict=True)
        ),
        STANDARD_TUNING,
        0,
    )
    grouped = Tab(
        tuple(
            TabNote(note.onset, note.duration, note.string, note.fret, note.left_finger,
                    note.right_finger, 0)
            for note in plain.notes
        ),
        STANDARD_TUNING,
        0,
    )
    assert plain == grouped
    assert check_playability(plain, MEDIAN_HAND) == check_playability(grouped, MEDIAN_HAND)


@pytest.mark.parametrize("group", [1, 7, 20_000])
def test_the_group_label_itself_carries_no_meaning(group: int) -> None:
    """Only which notes share a label matters, never the number chosen."""

    tab = _chord(("p", "p", "p", "i", "m", "a"), (group, group, group, 0, 0, 0))
    assert check_playability(tab, MEDIAN_HAND).verdict == "GREEN"
