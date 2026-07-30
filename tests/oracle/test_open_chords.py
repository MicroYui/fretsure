"""The verifier has to certify the chords a guitarist learns in week one.

It did not. `median@0.1` returned AMBER on a G major and `small@0.1` returned
RED, because the outermost string centres sit 52.5 mm apart at one fret while
`d_max` allowed the (2, 3) and (3, 4) finger pairs 50.0 mm and 45.0 mm. A hand
model in which two fingers cannot touch the outer strings at the same fret does
not describe a strict guitarist; it describes something that cannot play the
instrument.

Nothing caught it for two reasons, both worth keeping in view:

* Every test of the span rule used synthetic shapes chosen to exercise the
  arithmetic, so none of them was a chord anybody plays.
* The guard that should have objected was scoring the other way.
  `replay_negative_tabs.py` treats movement toward GREEN over 1,718 raw-LLM tabs
  as the verifier weakening, and eleven of those tabs are open-position chords.
  Fixing the defect therefore registered as eleven *false certifications*, which
  is how five separate attempts to widen this model came to be recorded as
  costing more than they bought.

These tests are deliberately about named chords rather than about millimetres.
A future profile may legitimately move any constant here; none of them may make
a G major unplayable.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from fretsure.geometry import NECK_WIDTH_MM, STANDARD_TUNING, STRING_SPACING_MM, d_max
from fretsure.oracle.core import check_playability
from fretsure.oracle.predicates import check_finger_monotonic
from fretsure.oracle.profiles import LARGE_HAND, MEDIAN_HAND, SMALL_HAND
from fretsure.tab import Tab, TabNote

PROFILES = (SMALL_HAND, MEDIAN_HAND, LARGE_HAND)


def _chord(*placements: tuple[int, int, int]) -> Tab:
    """A chord held for one bar, spelled as the oracle requires.

    Five and six note chords are one right-hand gesture -- the thumb sweeps a run
    of adjacent low strings while i, m and a take the top three -- so anything
    wider than four notes is written that way. Spelling it as independent plucks
    would fail on the right hand and hide whatever the left hand is doing.
    """

    ordered = sorted(placements, key=lambda p: p[0])
    sweep = len(ordered) - 3 if len(ordered) > 4 else 0
    notes = []
    for index, (string, fret, finger) in enumerate(ordered):
        if index < sweep:
            right, group = "p", 1
        else:
            right, group = ("p", "i", "m", "a")[min(index - sweep + (1 if sweep else 0), 3)], 0
        notes.append(TabNote(Fraction(0), Fraction(4), string, fret, finger, right, group))
    return Tab(tuple(notes), STANDARD_TUNING, 0)


# (name, placements as (string, fret, left finger); string 0 is the low E)
OPEN_CHORDS = (
    ("G major", ((0, 3, 2), (1, 2, 1), (2, 0, 0), (3, 0, 0), (4, 0, 0), (5, 3, 3))),
    ("E minor", ((0, 0, 0), (1, 2, 2), (2, 2, 3), (3, 0, 0), (4, 0, 0), (5, 0, 0))),
    ("A major", ((1, 0, 0), (2, 2, 1), (3, 2, 2), (4, 2, 3), (5, 0, 0))),
    ("D major", ((2, 0, 0), (3, 2, 1), (4, 3, 3), (5, 2, 2))),
    ("C major", ((1, 3, 3), (2, 2, 2), (3, 0, 0), (4, 1, 1), (5, 0, 0))),
)


@pytest.mark.parametrize("name,placements", OPEN_CHORDS, ids=[c[0] for c in OPEN_CHORDS])
@pytest.mark.parametrize("profile", PROFILES, ids=[p.version for p in PROFILES])
def test_a_first_week_chord_is_certified(name: str, placements, profile) -> None:
    """GREEN, not AMBER. Declining to certify a G major is still a wrong answer.

    C major on the small hand was an xfail here until `oracle@0.7.0`: it needs
    75.6 mm between index and ring, of which 68.7 mm is along the neck, and the
    old profile allowed 72.9 mm once the pessimistic transform had shrunk it.
    Raising the span to admit ordinary stretch technique cleared it, which is
    the sort of thing a defect recorded rather than papered over lets you notice.
    """

    result = check_playability(_chord(*placements), profile)
    assert result.verdict == "GREEN", (
        name,
        profile.version,
        sorted({str(d.violation_type) for d in result.diagnostics}),
    )


@pytest.mark.parametrize("profile", PROFILES, ids=[p.version for p in PROFILES])
def test_every_finger_pair_reaches_across_the_neck(profile) -> None:
    """The floor, stated directly rather than through a chord.

    This is a fact about the instrument, not about the hand: the strings are
    where they are, and a guitarist puts fingers on the outer two at one fret.
    No span constant may contradict it, however small the modelled hand.
    """

    for low in range(1, 5):
        for high in range(low + 1, 5):
            assert d_max(low, high, profile.hand_span_mm) >= NECK_WIDTH_MM, (
                profile.version,
                (low, high),
            )


def test_the_floor_does_not_touch_the_barre_case() -> None:
    """Same-finger geometry is a barre and is checked elsewhere.

    Flooring it would silently license one finger to hold two frets, which is a
    different claim entirely and one no barre predicate would then catch.
    """

    for finger in range(1, 5):
        assert d_max(finger, finger, MEDIAN_HAND.hand_span_mm) == 0.0


def test_the_neck_width_follows_the_string_spacing() -> None:
    """Derived, not a second copy of the number."""

    assert NECK_WIDTH_MM == (len(STANDARD_TUNING) - 1) * STRING_SPACING_MM


def test_a_stretch_along_the_neck_is_still_refused() -> None:
    """The floor must not have turned into a general loosening.

    It raises the allowance to the width of the neck and no further, so a wide
    stretch *along* the neck -- which is what the span rule exists to catch --
    has to keep failing.
    """

    impossible = Tab(
        (
            TabNote(Fraction(0), Fraction(4), 0, 1, 1, "p"),
            TabNote(Fraction(0), Fraction(4), 1, 12, 2, "i"),
        ),
        STANDARD_TUNING,
        0,
    )
    result = check_playability(impossible, MEDIAN_HAND)
    assert result.verdict == "RED"
    assert "FRET_SPAN" in {str(d.violation_type) for d in result.diagnostics}


def test_the_wrist_slants_and_the_opposite_cross_still_does_not() -> None:
    """The exemption is directional, and the direction is the whole point.

    A finger reaching toward the treble strings lands nearer the nut, so a
    higher-numbered finger there is the hand's own geometry. The mirror image --
    a higher-numbered finger nearer the nut on the *bass* side -- is fingers
    passing through one another, and stays refused. Editors write the first 50
    times to the second's 13.
    """

    slant = Tab(
        (
            TabNote(Fraction(0), Fraction(1), 1, 5, 2, "p"),
            TabNote(Fraction(0), Fraction(1), 4, 3, 3, "i"),
        ),
        STANDARD_TUNING,
        0,
    )
    cross = Tab(
        (
            TabNote(Fraction(0), Fraction(1), 4, 5, 2, "i"),
            TabNote(Fraction(0), Fraction(1), 1, 3, 3, "p"),
        ),
        STANDARD_TUNING,
        0,
    )
    assert check_finger_monotonic(slant, MEDIAN_HAND) == []
    assert [str(d.violation_type) for d in check_finger_monotonic(cross, MEDIAN_HAND)] == [
        "FINGER_MONOTONIC"
    ]


def test_a_barre_across_two_frets_is_still_refused_on_the_slant() -> None:
    """One finger, two frets, is a different claim and the exemption misses it.

    The slant excuses fingers being out of order; it says nothing about a single
    finger stopping two different frets, which no direction across the strings
    makes possible.
    """

    two_frets_one_finger = Tab(
        (
            TabNote(Fraction(0), Fraction(1), 1, 5, 3, "p"),
            TabNote(Fraction(0), Fraction(1), 4, 3, 3, "i"),
        ),
        STANDARD_TUNING,
        0,
    )
    assert [
        str(d.violation_type) for d in check_finger_monotonic(two_frets_one_finger, MEDIAN_HAND)
    ] == ["FINGER_MONOTONIC"]


def test_the_span_rule_still_bounds_how_far_back_the_slant_may_sit() -> None:
    """No fret-count bound is needed here because one already exists elsewhere.

    `d_max` bounds any two fingers, and positionally: four frets of setback in
    first position, nine around the twelfth, because frets narrow up the neck.
    Editors write one to five. A second bound inside the monotonic rule would be
    a third statement of the same longitudinal limit.
    """

    reachable = Tab(
        (
            TabNote(Fraction(0), Fraction(1), 1, 5, 2, "p"),
            TabNote(Fraction(0), Fraction(1), 4, 2, 4, "i"),
        ),
        STANDARD_TUNING,
        0,
    )
    too_far = Tab(
        (
            TabNote(Fraction(0), Fraction(1), 1, 14, 2, "p"),
            TabNote(Fraction(0), Fraction(1), 4, 2, 4, "i"),
        ),
        STANDARD_TUNING,
        0,
    )
    assert check_finger_monotonic(too_far, MEDIAN_HAND) == [], (
        "the slant exemption itself must not be what refuses this"
    )
    assert check_playability(reachable, MEDIAN_HAND).verdict != "RED"
    result = check_playability(too_far, MEDIAN_HAND)
    assert result.verdict == "RED"
    assert "FRET_SPAN" in {str(d.violation_type) for d in result.diagnostics}
