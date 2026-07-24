from fractions import Fraction as F

import pytest

from fretsure.geometry import STANDARD_TUNING, note_pitch
from fretsure.ir import Note
from fretsure.oracle.core import check_playability
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.api import Infeasible, solve_fingering
from fretsure.tab import Tab, TabNote


def _source_events(notes: tuple[Note, ...]) -> tuple[tuple[F, F, int], ...]:
    return tuple((note.onset, note.duration, note.pitch) for note in notes)


def _tab_events(tab: Tab) -> tuple[tuple[F, F, int], ...]:
    return tuple(
        (
            note.onset,
            note.duration,
            note_pitch(note.string, note.fret, tab.tuning, tab.capo),
        )
        for note in tab.notes
    )


def test_default_beam_keeps_a_green_alternating_finger_path() -> None:
    """A cheaper AMBER RH pattern must not crowd out a known GREEN one."""

    pitches = (64, 63) * 8
    notes = tuple(
        Note(F(index, 2), F(1, 2), pitch, "melody")
        for index, pitch in enumerate(pitches)
    )
    witness = Tab(
        tuple(
            TabNote(
                note.onset,
                note.duration,
                5 if note.pitch == 64 else 4,
                0 if note.pitch == 64 else 4,
                0 if note.pitch == 64 else 4,
                "i" if index % 2 == 0 else "m",
            )
            for index, note in enumerate(notes)
        ),
        STANDARD_TUNING,
        0,
    )

    assert tuple(note.right_finger for note in witness.notes) == ("i", "m") * 8
    assert _tab_events(witness) == _source_events(notes)
    assert (
        check_playability(witness, MEDIAN_HAND, tempo_bpm=240.0).verdict
        == "GREEN"
    )

    # Use the public default beam (currently 16): the regression is about the
    # bounded search retaining a certified path, not any particular position.
    result = solve_fingering(
        notes,
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
        tempo_bpm=240.0,
    )

    assert not isinstance(result, Infeasible)
    assert _tab_events(result) == _source_events(notes)
    assert (
        check_playability(result, MEDIAN_HAND, tempo_bpm=240.0).verdict
        == "GREEN"
    )


_TWO_POSITION_TUNING = (40, 54, 70, 80, 90, 100)


def _two_note_position_tab(*, string: int) -> Tab:
    pitches = (55, 58)
    duration = F(1, 16)
    return Tab(
        tuple(
            TabNote(
                F(index, 16),
                duration,
                string,
                pitch - _TWO_POSITION_TUNING[string],
                1,
                "i" if index == 0 else "m",
            )
            for index, pitch in enumerate(pitches)
        ),
        _TWO_POSITION_TUNING,
        0,
    )


@pytest.mark.parametrize("beam", (2, 16, 32))
def test_green_path_dominates_a_lower_position_amber_path(beam: int) -> None:
    """Playability colour precedes quality even when the GREEN path is higher."""

    notes = (
        Note(F(0), F(1, 16), 55, "melody"),
        Note(F(1, 16), F(1, 16), 58, "melody"),
    )
    lower_position = _two_note_position_tab(string=1)
    compressed_higher_position = _two_note_position_tab(string=0)

    assert (
        check_playability(lower_position, MEDIAN_HAND, tempo_bpm=90.0).verdict
        == "AMBER"
    )
    assert (
        check_playability(
            compressed_higher_position,
            MEDIAN_HAND,
            tempo_bpm=90.0,
        ).verdict
        == "GREEN"
    )

    result = solve_fingering(
        notes,
        _TWO_POSITION_TUNING,
        0,
        MEDIAN_HAND,
        tempo_bpm=90.0,
        beam=beam,
    )

    assert not isinstance(result, Infeasible)
    assert _tab_events(result) == _source_events(notes)
    assert (
        check_playability(result, MEDIAN_HAND, tempo_bpm=90.0).verdict
        == "GREEN"
    )
