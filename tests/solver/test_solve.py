from fractions import Fraction as F

from fretsure.geometry import STANDARD_TUNING, note_pitch
from fretsure.ir import Note
from fretsure.oracle.core import check_playability
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.api import Infeasible, solve_fingering
from fretsure.tab import Tab


def _pitches(tab: Tab) -> list[int]:
    return sorted(note_pitch(n.string, n.fret, tab.tuning, tab.capo) for n in tab.notes)


def _melody(pitches: list[int]) -> tuple[Note, ...]:
    return tuple(Note(F(i), F(1), p, "melody") for i, p in enumerate(pitches))


def test_scale_solves_non_red_and_preserves_pitch() -> None:
    notes = _melody([60, 62, 64, 65, 67, 69, 71, 72])  # C major scale, quarter notes
    result = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND)
    assert isinstance(result, Tab)
    assert check_playability(result, MEDIAN_HAND).verdict != "RED"
    assert _pitches(result) == sorted(n.pitch for n in notes)


def test_melody_plus_bass_solves_non_red_and_preserves() -> None:
    notes = (
        Note(F(0), F(1), 64, "melody"), Note(F(0), F(1), 40, "bass"),
        Note(F(1), F(1), 65, "melody"), Note(F(1), F(1), 45, "bass"),
        Note(F(2), F(1), 67, "melody"), Note(F(2), F(1), 43, "bass"),
    )
    result = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND)
    assert isinstance(result, Tab)
    assert check_playability(result, MEDIAN_HAND).verdict != "RED"
    assert _pitches(result) == sorted(n.pitch for n in notes)


def test_two_pitches_only_on_one_string_is_infeasible() -> None:
    # 85 and 86 are each reachable only on the high-E string -> can't co-occur
    notes = (Note(F(0), F(1), 85, "melody"), Note(F(0), F(1), 86, "harmony"))
    result = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND)
    assert isinstance(result, Infeasible)
    assert result.onset == F(0)


def test_empty_notes_gives_empty_tab() -> None:
    result = solve_fingering((), STANDARD_TUNING, 0, MEDIAN_HAND)
    assert isinstance(result, Tab)
    assert result.notes == ()


def test_deterministic() -> None:
    notes = _melody([60, 64, 67, 72])
    assert solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND) == solve_fingering(
        notes, STANDARD_TUNING, 0, MEDIAN_HAND
    )
