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


def test_held_bass_under_moving_melody_non_red() -> None:
    # a held bass under a moving melody must not reuse the same finger (C1)
    notes = (
        Note(F(0), F(4), 43, "bass"),  # G2 held four beats
        Note(F(1), F(1), 66, "melody"),  # F#4
    )
    r = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND)
    assert isinstance(r, Tab)
    assert check_playability(r, MEDIAN_HAND).verdict != "RED"


def test_never_returns_a_red_tab_on_fast_pivot() -> None:
    # fretted -> open -> fretted must not hide a too-fast shift (C2). The input is
    # physically too fast, so the honest answer is Infeasible, never a RED tab.
    notes = (
        Note(F(9, 10), F(1, 10), 41, "melody"),
        Note(F(1), F(1, 10), 64, "melody"),
        Note(F(11, 10), F(1, 10), 70, "melody"),
    )
    r = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND, tempo_bpm=90.0)
    assert isinstance(r, Infeasible) or (
        isinstance(r, Tab)
        and check_playability(r, MEDIAN_HAND, tempo_bpm=90.0).verdict != "RED"
    )


def test_fast_single_line_never_returns_red() -> None:
    # a fast chromatic run needs high-position play (compressed frets) to keep
    # shifts feasible; the greedy beam may not find it and returns a safe
    # Infeasible, but it must NEVER return a RED tab (C3 contract).
    notes = tuple(
        Note(F(i, 8), F(1, 8), p, "melody") for i, p in enumerate([60, 62, 64, 65])
    )
    r = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND, tempo_bpm=90.0)
    assert isinstance(r, Infeasible) or (
        isinstance(r, Tab)
        and check_playability(r, MEDIAN_HAND, tempo_bpm=90.0).verdict != "RED"
    )


def test_fast_open_string_run_alternates_fingers_non_red() -> None:
    # fast run on open strings (no shift) isolates repeat-rate: the beam takes the
    # cheap all-open path and must alternate right fingers to stay non-RED.
    notes = tuple(
        Note(F(i, 8), F(1, 8), p, "melody") for i, p in enumerate([55, 59, 64, 59, 55])
    )
    r = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND, tempo_bpm=90.0)
    assert isinstance(r, Tab)
    assert check_playability(r, MEDIAN_HAND, tempo_bpm=90.0).verdict != "RED"


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
