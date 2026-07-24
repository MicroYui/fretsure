from fractions import Fraction as F
from itertools import product

import pytest

from fretsure.geometry import STANDARD_TUNING, note_pitch
from fretsure.ir import Note
from fretsure.oracle.core import check_playability
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.api import Infeasible, solve_fingering
from fretsure.solver.candidates import candidates
from fretsure.solver.cost import (
    QualityCost,
    advance_hand_window,
    config_hand_window,
)
from fretsure.solver.frames import FrameConfig, Placement
from fretsure.tab import Tab, TabNote

# Two artificial string spacings expose exactly two positions for pitches 55..59:
# frets 15..19 on string 0 and frets 1..5 on string 1.  The remaining strings
# are above the target range.  This is a generic solver fixture, not a song rule.
_TWO_POSITION_TUNING = (40, 54, 70, 80, 90, 100)


def _melody(pitches: tuple[int, ...]) -> tuple[Note, ...]:
    return tuple(Note(F(index), F(1), pitch, "melody") for index, pitch in enumerate(pitches))


def _tab_events(tab: Tab) -> tuple[tuple[F, F, int], ...]:
    return tuple(
        sorted(
            (
                note.onset,
                note.duration,
                note_pitch(note.string, note.fret, tab.tuning, tab.capo),
            )
            for note in tab.notes
        )
    )


def _source_events(notes: tuple[Note, ...]) -> tuple[tuple[F, F, int], ...]:
    return tuple(sorted((note.onset, note.duration, note.pitch) for note in notes))


def _two_tigers_melody() -> tuple[Note, ...]:
    # Public-domain Frere Jacques / Two Tigers melody: 32 attacks over 32 beats.
    # The <=5 assertion below is a regression expectation for this fixture only;
    # neither this melody nor that bound belongs in the production objective.
    events = (
        ((60, F(1)), (62, F(1)), (64, F(1)), (60, F(1))) * 2
        + ((64, F(1)), (65, F(1)), (67, F(2))) * 2
        + (
            (67, F(1, 2)),
            (69, F(1, 2)),
            (67, F(1, 2)),
            (65, F(1, 2)),
            (64, F(1)),
            (60, F(1)),
        )
        * 2
        + ((60, F(1)), (55, F(1)), (60, F(2))) * 2
    )
    onset = F(0)
    notes: list[Note] = []
    for pitch, duration in events:
        notes.append(Note(onset, duration, pitch, "melody"))
        onset += duration
    assert len(notes) == 32
    return tuple(notes)


def test_quality_order_is_lexicographic_not_a_weighted_unit_mix() -> None:
    # This deliberately gives the low path absurdly worse lower-priority values.
    # A weighted sum could let millimetres or finger counts overturn position;
    # the declared lexicographic objective must not.
    low_1_to_3 = QualityCost(3, F(4), 9, 10_000, 99, 99)
    high_15_to_17 = QualityCost(17, F(32), 0, 0, 0, 0)

    assert low_1_to_3 < high_15_to_17


@pytest.mark.parametrize(
    "worse",
    [
        QualityCost(4, F(0), 0, 0, 0, 0),
        QualityCost(3, F(5), 0, 0, 0, 0),
        QualityCost(3, F(4), 2, 0, 0, 0),
        QualityCost(3, F(4), 1, 11, 0, 0),
        QualityCost(3, F(4), 1, 10, 3, 0),
        QualityCost(3, F(4), 1, 10, 2, 2),
    ],
)
def test_each_quality_dimension_only_breaks_ties_after_earlier_dimensions(
    worse: QualityCost,
) -> None:
    baseline = QualityCost(3, F(4), 1, 10, 2, 1)

    assert baseline < worse


def test_open_frame_preserves_the_previous_hand_window() -> None:
    low = FrameConfig((Placement(55, 1, 1, 1, "p"),))
    high = FrameConfig((Placement(55, 0, 15, 1, "p"),))
    low_window = config_hand_window(low, 0, MEDIAN_HAND)
    high_window = config_hand_window(high, 0, MEDIAN_HAND)
    assert low_window is not None
    assert high_window is not None

    after_open = advance_hand_window(low_window, None)
    direct_jump = advance_hand_window(low_window, high_window)
    jump_after_open = advance_hand_window(after_open[0], high_window)

    assert after_open == (low_window, False, 0)
    assert jump_after_open == direct_jump
    assert direct_jump[1]
    assert direct_jump[2] > 0


def test_solver_prefers_1_to_3_over_15_to_17() -> None:
    notes = _melody((55, 57))
    result = solve_fingering(notes, _TWO_POSITION_TUNING, 0, MEDIAN_HAND, beam=16)

    assert isinstance(result, Tab)
    assert [(note.string, note.fret) for note in result.notes] == [(1, 1), (1, 3)]
    assert check_playability(result, MEDIAN_HAND).verdict == "GREEN"


@pytest.mark.parametrize(
    "pitches",
    [
        (55, 57, 59),
        (59, 55, 58),
        (56, 59, 57, 55),
        (58, 56, 55, 59),
    ],
)
def test_bounded_solver_matches_exhaustive_lexicographic_position_search(
    pitches: tuple[int, ...],
) -> None:
    """Differential check against every geometry path in a tiny search space."""

    positions = tuple(
        tuple(candidates(pitch, _TWO_POSITION_TUNING, 0, MEDIAN_HAND.max_fret))
        for pitch in pitches
    )
    assert all(len(frame) == 2 for frame in positions)
    paths = tuple(product(*positions))

    # The production contract gates on GREEN before applying quality ordering.
    # Three-beat onset spacing makes every path GREEN, isolating objective order
    # from the hard playability gate.  Use a simple monophonic fingering whose
    # LH/RH choices do not distinguish these geometry paths.
    def geometry_tab(path: tuple[tuple[int, int], ...]) -> Tab:
        return Tab(
            tuple(
                TabNote(F(3 * index), F(1), string, fret, 1, "a")
                for index, (string, fret) in enumerate(path)
            ),
            _TWO_POSITION_TUNING,
            0,
        )

    green_paths = tuple(
        path
        for path in paths
        if check_playability(geometry_tab(path), MEDIAN_HAND).verdict == "GREEN"
    )
    assert green_paths == paths

    # Unit durations make fret exposure the sum of frets.  In this fixture that
    # pair uniquely identifies the globally best position path before any lower
    # priority shift/finger/string tie-break is consulted.
    def independent_prefix(path: tuple[tuple[int, int], ...]) -> tuple[int, int]:
        frets = tuple(fret for _string, fret in path)
        return (max(frets), sum(frets))

    expected = min(green_paths, key=independent_prefix)
    assert (
        sum(
            independent_prefix(path) == independent_prefix(expected)
            for path in green_paths
        )
        == 1
    )

    notes = tuple(
        Note(F(3 * index), F(1), pitch, "melody")
        for index, pitch in enumerate(pitches)
    )
    result = solve_fingering(notes, _TWO_POSITION_TUNING, 0, MEDIAN_HAND, beam=16)

    assert isinstance(result, Tab)
    actual = tuple((note.string, note.fret) for note in result.notes)
    assert actual == expected
    assert _tab_events(result) == _source_events(notes)


def test_two_tigers_stays_green_and_naturally_remains_in_open_position() -> None:
    notes = _two_tigers_melody()
    result = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND, beam=16)

    assert isinstance(result, Tab)
    assert check_playability(result, MEDIAN_HAND).verdict == "GREEN"
    assert _tab_events(result) == _source_events(notes)
    assert max(note.fret for note in result.notes) <= 5


def test_high_position_remains_available_when_the_pitch_requires_it() -> None:
    pitch = STANDARD_TUNING[-1] + 20
    reachable = candidates(pitch, STANDARD_TUNING, 0, MEDIAN_HAND.max_fret)
    assert reachable == [(5, 20)]
    notes = (Note(F(0), F(1), pitch, "melody"),)

    result = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND, beam=16)

    assert not isinstance(result, Infeasible)
    assert [(note.string, note.fret) for note in result.notes] == reachable
    assert check_playability(result, MEDIAN_HAND).verdict == "GREEN"
    assert _tab_events(result) == _source_events(notes)
