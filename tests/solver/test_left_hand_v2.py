from __future__ import annotations

from fractions import Fraction as F
from pathlib import Path

import fretsure.solver.api as solver_api
from fretsure.geometry import STANDARD_TUNING, note_pitch
from fretsure.ir import Note
from fretsure.oracle.core import check_playability
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.api import solve_fingering
from fretsure.solver.frames import FrameConfig, Placement
from fretsure.solver.left_hand import advance_left_hand
from fretsure.solver.score import solve_fingering_score
from fretsure.tab import Tab, TabNote


def _melody(pitches: tuple[int, ...]) -> tuple[Note, ...]:
    return tuple(Note(F(index), F(1), pitch, "melody") for index, pitch in enumerate(pitches))


def _chord(pitches: tuple[int, ...]) -> tuple[Note, ...]:
    return tuple(
        Note(
            F(0),
            F(1),
            pitch,
            "melody" if index == len(pitches) - 1 else "harmony",
        )
        for index, pitch in enumerate(pitches)
    )


def _left_shape(tab: Tab) -> tuple[tuple[int, int, int], ...]:
    return tuple(
        (note.string, note.fret, note.left_finger)
        for note in sorted(tab.notes, key=lambda item: (item.onset, item.string))
    )


def test_first_position_arpeggio_uses_contextual_finger_numbers() -> None:
    notes = _melody((48, 55, 60, 64, 52, 55, 60, 64))

    result = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND)

    assert isinstance(result, Tab)
    assert _left_shape(result) == (
        (1, 3, 3),
        (3, 0, 0),
        (4, 1, 1),
        (5, 0, 0),
        (2, 2, 2),
        (3, 0, 0),
        (4, 1, 1),
        (5, 0, 0),
    )


def test_c_major_scale_uses_position_pattern_instead_of_all_finger_one() -> None:
    result = solve_fingering(
        _melody((60, 62, 64, 65, 67, 69, 71, 72)),
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
    )

    assert isinstance(result, Tab)
    assert _left_shape(result) == (
        (4, 1, 1),
        (4, 3, 3),
        (5, 0, 0),
        (5, 1, 1),
        (5, 3, 3),
        (5, 5, 1),
        (5, 7, 3),
        (5, 8, 4),
    )


def test_repeated_fretted_note_keeps_the_same_left_finger() -> None:
    result = solve_fingering(
        _melody((60, 60, 60, 60)),
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
    )

    assert isinstance(result, Tab)
    assert {(note.string, note.fret, note.left_finger) for note in result.notes} == {(4, 1, 1)}


def test_open_frame_preserves_discrete_position() -> None:
    first = FrameConfig((Placement(60, 4, 1, 1, "p"),))
    open_frame = FrameConfig((Placement(64, 5, 0, 0, "p"),))
    first_active = (TabNote(F(0), F(1), 4, 1, 1, "p"),)
    open_active = (TabNote(F(1), F(1), 5, 0, 0, "p"),)

    positioned = advance_left_hand(None, first_active, None, first)
    after_open = advance_left_hand(
        positioned.position,
        open_active,
        first,
        open_frame,
    )

    assert positioned.position == 1
    assert after_open.position == 1
    assert after_open.position_shift_count == 0


def test_same_fret_dyad_does_not_turn_into_an_unnecessary_barre() -> None:
    result = solve_fingering(
        _chord((53, 58)),
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
    )

    assert isinstance(result, Tab)
    assert _left_shape(result) == ((2, 3, 1), (3, 3, 2))


def test_repeated_same_fret_dyad_keeps_the_ergonomic_incumbent() -> None:
    notes = tuple(
        Note(
            F(onset),
            F(1),
            pitch,
            "melody" if pitch == 58 else "harmony",
        )
        for onset in range(4)
        for pitch in (53, 58)
    )

    result = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND)

    assert isinstance(result, Tab)
    assert {
        (note.string, note.fret, note.left_finger) for note in result.notes
    } == {(2, 3, 1), (3, 3, 2)}


def test_standard_f_major_keeps_the_idiomatic_partial_barre() -> None:
    result = solve_fingering(
        _chord((53, 57, 60, 65)),
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
    )

    assert isinstance(result, Tab)
    assert _left_shape(result) == (
        (2, 3, 3),
        (3, 2, 2),
        (4, 1, 1),
        (5, 1, 1),
    )
    assert check_playability(result, MEDIAN_HAND).verdict == "GREEN"


def test_standard_c_major_is_green_in_open_position() -> None:
    result = solve_fingering(
        _chord((48, 52, 55, 60)),
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
    )

    assert isinstance(result, Tab)
    assert _left_shape(result) == (
        (1, 3, 3),
        (2, 2, 2),
        (3, 0, 0),
        (4, 1, 1),
    )
    assert check_playability(result, MEDIAN_HAND).verdict == "GREEN"


def test_left_fingering_preserves_every_source_pitch() -> None:
    notes = _melody((48, 50, 52, 53, 55, 57, 59, 60))
    result = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND)

    assert isinstance(result, Tab)
    actual = sorted(
        note_pitch(note.string, note.fret, result.tuning, result.capo) for note in result.notes
    )
    assert actual == sorted(note.pitch for note in notes)
    assert check_playability(result, MEDIAN_HAND).verdict != "RED"


def test_solver_package_has_no_score_difficulty_dependency() -> None:
    solver_root = Path(solver_api.__file__).parent

    for path in solver_root.glob("*.py"):
        assert "fretsure.difficulty" not in path.read_text(encoding="utf-8"), path.name


def test_ode_to_joy_excerpt_has_stable_contextual_left_fingering() -> None:
    # Public-domain melody with the two-voice excerpt shape from the real
    # MusicXML import that exposed the v1 all-finger-1/refingering failure.
    events = (
        (F(0), F(1), 48, 64),
        (F(1), F(1), 48, 64),
        (F(2), F(1), 48, 65),
        (F(3), F(1), 48, 67),
        (F(4), F(1), 43, 67),
        (F(5), F(1), 43, 65),
        (F(6), F(1), 43, 64),
        (F(7), F(1), 43, 62),
        (F(8), F(1), 48, 60),
        (F(9), F(1), 48, 60),
        (F(10), F(1), 48, 62),
        (F(11), F(1), 48, 64),
        (F(12), F(3, 2), 43, 64),
        (F(27, 2), F(1, 2), 43, 62),
        (F(14), F(2), 43, 62),
    )
    notes = tuple(
        Note(onset, duration, pitch, "bass" if index == 0 else "melody")
        for onset, duration, bass, melody in events
        for index, pitch in enumerate((bass, melody))
    )

    result = solve_fingering_score(
        notes,
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
        tempo_bpm=96.0,
    )

    assert isinstance(result, Tab)
    by_onset = {
        onset: tuple(
            (note.string, note.fret, note.left_finger)
            for note in result.notes
            if note.onset == onset
        )
        for onset, _duration, _bass, _melody in events
    }
    assert by_onset[F(0)] == by_onset[F(1)] == (
        (1, 3, 3),
        (5, 0, 0),
    )
    assert by_onset[F(2)] == ((1, 3, 3), (5, 1, 1))
    assert by_onset[F(3)] == ((1, 3, 3), (5, 3, 4))
    assert by_onset[F(8)] == by_onset[F(9)] == (
        (1, 3, 3),
        (4, 1, 1),
    )
    assert by_onset[F(10)] == ((1, 3, 3), (4, 3, 4))
    assert check_playability(result, MEDIAN_HAND, tempo_bpm=96.0).verdict == "GREEN"

    for shape in by_onset.values():
        for left_index, left in enumerate(shape):
            for right in shape[left_index + 1 :]:
                same_fret = left[1] > 0 and left[1] == right[1]
                assert not (same_fret and left[2] > right[2])
