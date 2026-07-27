"""What choosing a capo may and may not do to a score.

A capo is the only lever measured so far that buys refused repertoire without
touching the verifier: it changes which notes need a finger, never what the
oracle considers playable, and it preserves every pitch exactly. That makes the
properties worth pinning, because the temptation with a free lunch is to stop
checking whether it is still free.
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import Note
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.score import (
    DEFAULT_CAPO_LADDER,
    capo_candidates,
    solve_fingering_score,
    solve_fingering_score_choosing_capo,
)
from fretsure.tab import Tab


def _melody(*pitches: int) -> tuple[Note, ...]:
    return tuple(
        Note(Fraction(index), Fraction(1), pitch, "melody")
        for index, pitch in enumerate(pitches)
    )


def test_the_requested_capo_is_always_tried_first() -> None:
    """A score that already solves must solve identically and at no extra cost."""

    notes = _melody(64, 62, 60)
    assert capo_candidates(notes, STANDARD_TUNING, 0)[0] == 0
    assert capo_candidates(notes, STANDARD_TUNING, 3)[0] == 3


def test_a_position_the_lowest_note_cannot_survive_is_not_a_candidate() -> None:
    """Below the new open pitch there is nowhere on the string to put the note."""

    # E2 is the open sixth string, so any capo at all puts it out of reach.
    on_the_floor = _melody(40, 52, 55)
    assert capo_candidates(on_the_floor, STANDARD_TUNING, 0) == (0,)

    # An octave up, every rung of the ladder survives.
    clear_of_it = _melody(64, 67, 71)
    assert capo_candidates(clear_of_it, STANDARD_TUNING, 0) == (0, *DEFAULT_CAPO_LADDER)


def test_candidates_never_repeat_the_requested_position() -> None:
    notes = _melody(64, 67, 71)
    candidates = capo_candidates(notes, STANDARD_TUNING, 3)
    assert candidates[0] == 3
    assert candidates.count(3) == 1


def test_an_empty_score_offers_only_what_was_asked_for() -> None:
    assert capo_candidates((), STANDARD_TUNING, 2) == (2,)


def test_a_solvable_score_is_untouched_by_the_ladder() -> None:
    """The ladder must be inert on anything that already works."""

    notes = _melody(64, 62, 60, 59)
    plain = solve_fingering_score(notes, STANDARD_TUNING, 0, MEDIAN_HAND)
    laddered = solve_fingering_score_choosing_capo(
        notes, STANDARD_TUNING, 0, MEDIAN_HAND
    )
    assert isinstance(plain, Tab)
    assert laddered == plain


def test_the_reported_capo_is_the_one_that_was_used() -> None:
    """`Tab.capo` carries the choice, so a renderer cannot miss it.

    Choosing a capo silently would hide a setup the player has to actually make
    -- the same defect as substituting a deterministic proposal without saying
    so, recorded in the skill-registry ablation.
    """

    notes = _melody(64, 62, 60, 59)
    solved = solve_fingering_score_choosing_capo(
        notes, STANDARD_TUNING, 2, MEDIAN_HAND, ladder=(4,)
    )
    assert isinstance(solved, Tab)
    assert solved.capo == 2


@pytest.mark.parametrize("ladder", [(), (1,), (1, 2, 3, 4, 5)])
def test_pitches_are_preserved_whatever_the_ladder(ladder: tuple[int, ...]) -> None:
    """A capo is an instrument decision, not an edit to the music."""

    notes = _melody(64, 62, 60, 59)
    solved = solve_fingering_score_choosing_capo(
        notes, STANDARD_TUNING, 0, MEDIAN_HAND, ladder=ladder
    )
    assert isinstance(solved, Tab)
    sounded = sorted(
        STANDARD_TUNING[note.string] + solved.capo + note.fret for note in solved.notes
    )
    assert sounded == sorted(note.pitch for note in notes)


def test_a_score_no_position_can_play_reports_the_position_it_was_asked_for() -> None:
    """Reporting the last rung tried would describe a score nobody asked about."""

    # More simultaneous notes than the instrument has strings: unplayable at any
    # capo, and the reason must be about the score as given.
    impossible = tuple(
        Note(Fraction(0), Fraction(1), pitch, "melody")
        for pitch in (60, 62, 64, 65, 67, 69, 71)
    )
    result = solve_fingering_score_choosing_capo(
        impossible, STANDARD_TUNING, 0, MEDIAN_HAND
    )
    assert not isinstance(result, Tab)
    assert result.reason
