from __future__ import annotations

from fractions import Fraction as F

from fretsure.ir import Note
from fretsure.solver.sustain import repair_repeated_pitch_holds


def test_repeated_pitch_ends_at_the_next_attack_of_that_pitch() -> None:
    notes = (
        Note(F(0), F(4), 60, "bass"),
        Note(F(1), F(1), 60, "bass"),
    )

    repaired = repair_repeated_pitch_holds(notes)

    assert repaired[0].duration == F(1)
    assert repaired[1].duration == F(1)


def test_a_pitch_still_sounds_at_the_moment_it_is_re_attacked() -> None:
    """The clip is faithfulness-neutral: something is always sounding there."""

    notes = (Note(F(0), F(4), 55, "harmony"), Note(F(2), F(2), 55, "harmony"))

    repaired = repair_repeated_pitch_holds(notes)

    first, second = repaired
    assert first.onset + first.duration == second.onset
    assert second.onset + second.duration == F(4)


def test_different_pitches_keep_their_full_notated_hold() -> None:
    """Two voices ringing together is ordinary guitar writing, not a defect."""

    notes = (
        Note(F(0), F(4), 43, "bass"),
        Note(F(1), F(1), 64, "melody"),
        Note(F(2), F(2), 67, "harmony"),
    )

    assert repair_repeated_pitch_holds(notes) == notes


def test_repair_is_idempotent() -> None:
    notes = (
        Note(F(0), F(8), 48, "bass"),
        Note(F(2), F(8), 48, "bass"),
        Note(F(6), F(1), 48, "bass"),
    )

    once = repair_repeated_pitch_holds(notes)

    assert repair_repeated_pitch_holds(once) == once
    assert [note.duration for note in once] == [F(2), F(4), F(1)]


def test_only_the_nearest_later_attack_bounds_the_hold() -> None:
    notes = (
        Note(F(0), F(10), 52, "bass"),
        Note(F(3), F(1), 52, "bass"),
        Note(F(7), F(1), 52, "bass"),
    )

    repaired = repair_repeated_pitch_holds(notes)

    assert repaired[0].duration == F(3)
