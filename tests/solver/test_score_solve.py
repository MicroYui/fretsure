from collections.abc import Sequence
from fractions import Fraction as F

import pytest

import fretsure.solver.score as score_module
from fretsure.ir import Note
from fretsure.oracle.core import OracleResult, check_playability
from fretsure.oracle.input import (
    MAX_SOLVER_WORK_UNITS,
    OracleInputCode,
    OracleInputDiagnostic,
    SolverInputError,
)
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.solver.api import Infeasible, solve_fingering
from fretsure.solver.score import (
    MAX_SCORE_SOLVER_AGGREGATE_WORK_UNITS,
    MAX_SCORE_SOLVER_SEGMENTS,
    solve_fingering_score,
)
from fretsure.tab import Tab, TabNote


class _ExplodingSequence(Sequence[Note]):
    def __len__(self) -> int:
        raise AssertionError("strict input validation must not call custom sequence methods")

    def __getitem__(self, index: int) -> Note:
        del index
        raise AssertionError("strict input validation must not call custom sequence methods")


def _stress_case() -> tuple[tuple[Note, ...], tuple[int, ...], Profile]:
    profile = Profile(
        "score-segmentation-test@0.1",
        250.0,
        200.0,
        5_000.0,
        50.0,
        1e-6,
        max_fret=36,
    )
    tuning = (0, 1, 2, 3, 4, 5)
    notes = tuple(
        Note(F(frame), F(1), pitch, "melody" if index == 2 else "harmony")
        for frame in range(19)
        for index, pitch in enumerate((10, 11, 12))
    )
    return notes, tuning, profile


def test_long_score_composition_preserves_each_search_work_gate() -> None:
    notes, tuning, profile = _stress_case()

    with pytest.raises(SolverInputError, match="estimated bounded search work"):
        solve_fingering(notes, tuning, 0, profile, beam=1024)

    result = solve_fingering_score(notes, tuning, 0, profile, beam=1024)

    assert isinstance(result, (Tab, Infeasible))
    if isinstance(result, Tab):
        assert check_playability(result, profile).verdict != "RED"


def test_long_score_composition_solves_a_score_rejected_as_one_search() -> None:
    notes = tuple(Note(F(index), F(1, 4), 64, "melody") for index in range(120))
    tuning = (40, 45, 50, 55, 59, 64)

    with pytest.raises(SolverInputError, match="estimated bounded search work"):
        solve_fingering(notes, tuning, 0, MEDIAN_HAND, beam=16)

    result = solve_fingering_score(notes, tuning, 0, MEDIAN_HAND, beam=16)

    assert isinstance(result, Tab)
    assert len(result.notes) == 120
    assert check_playability(result, MEDIAN_HAND).verdict == "GREEN"


def test_score_composition_does_not_hide_non_work_input_errors() -> None:
    duplicate = (
        Note(F(0), F(1), 60, "melody"),
        Note(F(0), F(2), 60, "harmony"),
    )
    _notes, tuning, profile = _stress_case()

    with pytest.raises(SolverInputError, match="DUPLICATE_ONSET_PITCH"):
        solve_fingering_score(duplicate, tuning, 0, profile)


def test_score_composition_preserves_the_strict_solver_object_boundary() -> None:
    _notes, tuning, profile = _stress_case()

    with pytest.raises(SolverInputError) as captured:
        solve_fingering_score(_ExplodingSequence(), tuning, 0, profile)

    assert [item.code for item in captured.value.diagnostics] == [
        OracleInputCode.SOLVER_NOTES_TYPE
    ]


def test_score_composition_has_an_explicit_aggregate_segment_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = tuple(Note(F(index), F(1, 4), 40, "melody") for index in range(5))
    tuning = (40, 45, 50, 55, 59, 64)
    profile = _stress_case()[2]
    successful_segments = 0

    def fake_solve(
        segment: tuple[Note, ...],
        segment_tuning: tuple[int, ...],
        segment_capo: int,
        _profile: Profile,
        *,
        tempo_bpm: float,
        beats_per_bar: int,
        beam: int,
    ) -> Tab | Infeasible:
        del tempo_bpm, beats_per_bar, beam
        nonlocal successful_segments
        if len(segment) > 1:
            raise SolverInputError(
                (
                    OracleInputDiagnostic(
                        OracleInputCode.SOLVER_WORK_LIMIT,
                        "notes",
                        "forced test split",
                    ),
                )
            )
        successful_segments += 1
        note = segment[0]
        return Tab(
            (TabNote(note.onset, note.duration, 0, 0, 0, "p"),),
            segment_tuning,
            segment_capo,
        )

    monkeypatch.setattr(score_module, "solve_fingering", fake_solve)

    result = solve_fingering_score(notes, tuning, 0, profile)

    assert MAX_SCORE_SOLVER_SEGMENTS == 4
    assert MAX_SCORE_SOLVER_AGGREGATE_WORK_UNITS == 4 * MAX_SOLVER_WORK_UNITS
    assert isinstance(result, Infeasible)
    assert result.reason == "score-level solver segment budget is exhausted"
    assert successful_segments <= MAX_SCORE_SOLVER_SEGMENTS


def test_score_composition_never_releases_a_red_reassembly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = (
        Note(F(0), F(1, 4), 40, "melody"),
        Note(F(1), F(1, 4), 40, "melody"),
    )
    tuning = (40, 45, 50, 55, 59, 64)
    profile = _stress_case()[2]

    def fake_solve(
        segment: tuple[Note, ...],
        segment_tuning: tuple[int, ...],
        segment_capo: int,
        _profile: Profile,
        *,
        tempo_bpm: float,
        beats_per_bar: int,
        beam: int,
    ) -> Tab | Infeasible:
        del tempo_bpm, beats_per_bar, beam
        if len(segment) > 1:
            raise SolverInputError(
                (
                    OracleInputDiagnostic(
                        OracleInputCode.SOLVER_WORK_LIMIT,
                        "notes",
                        "forced test split",
                    ),
                )
            )
        note = segment[0]
        return Tab(
            (TabNote(note.onset, note.duration, 0, 0, 0, "p"),),
            segment_tuning,
            segment_capo,
        )

    def red_result(
        _tab: Tab,
        _profile: Profile,
        *,
        tempo_bpm: float,
        beats_per_bar: int,
    ) -> OracleResult:
        del tempo_bpm, beats_per_bar
        return OracleResult(
            "RED",
            (),
            "test-oracle",
            profile.version,
            profile.fingerprint,
            "test-input",
        )

    monkeypatch.setattr(score_module, "solve_fingering", fake_solve)
    monkeypatch.setattr(score_module, "check_playability", red_result)

    result = solve_fingering_score(notes, tuning, 0, profile)

    assert isinstance(result, Infeasible)
    assert result.reason == (
        "independently bounded score segments failed the full-history oracle gate"
    )
