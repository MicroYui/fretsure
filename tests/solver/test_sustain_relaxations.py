"""Properties the sustain ladder must hold for early release to stay honest.

Letting go early is the one relaxation in the solver that costs faithfulness
rather than effort, so what constrains it is worth asserting directly: the
score as written is always tried first, every later rung gives up strictly
more, melody is never touched, and no rung may drop below the retention floor.
"""

from __future__ import annotations

from fractions import Fraction

from hypothesis import given
from hypothesis import strategies as st

from fretsure.ir import Note
from fretsure.solver.sustain import (
    SUSTAIN_RETENTION_FLOOR,
    hold_bounds,
    sustain_relaxations,
)


def _held(notes: tuple[Note, ...]) -> Fraction:
    return sum((note.duration for note in notes), Fraction(0))


scores = st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=15),
        st.integers(min_value=1, max_value=8),
        st.integers(min_value=40, max_value=72),
        st.sampled_from(("melody", "bass", "harmony")),
    ),
    min_size=1,
    max_size=12,
).map(
    lambda rows: tuple(
        Note(Fraction(onset), Fraction(length), pitch, voice)
        for onset, length, pitch, voice in rows
    )
)


@given(scores)
def test_first_rung_is_the_score_exactly_as_written(notes: tuple[Note, ...]) -> None:
    assert sustain_relaxations(notes)[0] == notes


@given(scores)
def test_rungs_give_up_progressively_more_sustain(notes: tuple[Note, ...]) -> None:
    ladder = sustain_relaxations(notes)
    held = [_held(rung) for rung in ladder]
    assert held == sorted(held, reverse=True)
    assert len(set(ladder)) == len(ladder)


@given(scores)
def test_no_rung_holds_less_than_the_retention_floor(notes: tuple[Note, ...]) -> None:
    written = _held(notes)
    for rung in sustain_relaxations(notes):
        assert _held(rung) / written >= SUSTAIN_RETENTION_FLOOR


@given(scores)
def test_melody_is_never_released_and_onsets_never_move(notes: tuple[Note, ...]) -> None:
    for rung in sustain_relaxations(notes):
        assert len(rung) == len(notes)
        for relaxed, written in zip(rung, notes, strict=True):
            assert (relaxed.onset, relaxed.pitch, relaxed.voice) == (
                written.onset,
                written.pitch,
                written.voice,
            )
            assert relaxed.duration <= written.duration
            if written.voice == "melody":
                assert relaxed.duration == written.duration


@given(scores)
def test_every_rung_respects_the_derived_hold_floor(notes: tuple[Note, ...]) -> None:
    """The freedom is derived from the target, so no rung may exceed it."""

    bounds = hold_bounds(notes)
    for rung in sustain_relaxations(notes):
        for note, limits in zip(rung, bounds, strict=True):
            assert note.duration >= limits.minimum


def test_a_score_with_no_slack_is_solved_exactly_once() -> None:
    """Every note ends at the next attack, so there is nothing to give up."""

    notes = (
        Note(Fraction(0), Fraction(1), 60, "melody"),
        Note(Fraction(0), Fraction(1), 40, "bass"),
        Note(Fraction(1), Fraction(1), 62, "melody"),
        Note(Fraction(1), Fraction(1), 45, "bass"),
    )
    assert sustain_relaxations(notes) == (notes,)


def test_a_bass_pedal_offers_rungs_down_to_half_its_written_value() -> None:
    """The written whole note is a voice-leading instruction, not a hand.

    The bass may go to half its written value and no further, which is what
    keeps a chord's root sounding when ``bass_root_accuracy`` asks for it.
    """

    notes = (
        Note(Fraction(0), Fraction(4), 40, "bass"),
        *(Note(Fraction(beat, 4), Fraction(1, 4), 60 + beat, "melody") for beat in range(64)),
    )
    ladder = sustain_relaxations(notes)
    assert len(ladder) > 1
    assert min(rung[0].duration for rung in ladder) == Fraction(2)


def test_the_floor_refuses_a_rung_that_would_stop_holding_the_score() -> None:
    """A bass pedal carrying half the score's sustain cannot be given up.

    Without the floor this would be the cheapest possible way to make anything
    playable: keep the notes, stop holding them.
    """

    notes = (
        Note(Fraction(0), Fraction(4), 40, "bass"),
        *(Note(Fraction(beat), Fraction(1), 60 + beat, "melody") for beat in range(4)),
    )
    assert sustain_relaxations(notes) == (notes,)
