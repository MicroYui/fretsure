from collections.abc import Sequence
from fractions import Fraction as F
from types import SimpleNamespace

import pytest

import fretsure.solver.score as score_module
from fretsure.geometry import note_pitch
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

    assert [item.code for item in captured.value.diagnostics] == [OracleInputCode.SOLVER_NOTES_TYPE]


def test_score_composition_has_an_explicit_aggregate_segment_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notes = tuple(
        Note(F(index), F(1, 4), 40, "melody")
        for index in range(MAX_SCORE_SOLVER_SEGMENTS + 1)
    )
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
        _collect_full_green_pool: bool,
        _initial_continuation: object,
    ) -> object:
        del _collect_full_green_pool, _initial_continuation
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
        return SimpleNamespace(
            result=Tab(
                (TabNote(note.onset, note.duration, 0, 0, 0, "p"),),
                segment_tuning,
                segment_capo,
            ),
            green_pool=(),
            continuation=object(),
        )

    monkeypatch.setattr(score_module, "_solve_fingering_with_green_pool", fake_solve)

    result = solve_fingering_score(notes, tuning, 0, profile)

    # The advertised bound is a contract, so the number is pinned here too.
    assert MAX_SCORE_SOLVER_SEGMENTS == 32
    assert MAX_SCORE_SOLVER_AGGREGATE_WORK_UNITS == (
        MAX_SCORE_SOLVER_SEGMENTS * MAX_SOLVER_WORK_UNITS
    )
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
        _collect_full_green_pool: bool,
        _initial_continuation: object,
    ) -> object:
        del _collect_full_green_pool, _initial_continuation
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
        return SimpleNamespace(
            result=Tab(
                (TabNote(note.onset, note.duration, 0, 0, 0, "p"),),
                segment_tuning,
                segment_capo,
            ),
            green_pool=(),
            continuation=object(),
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

    monkeypatch.setattr(score_module, "_solve_fingering_with_green_pool", fake_solve)
    monkeypatch.setattr(score_module, "check_playability", red_result)

    result = solve_fingering_score(notes, tuning, 0, profile)

    assert isinstance(result, Infeasible)
    assert result.reason == (
        "independently bounded score segments failed the full-history oracle gate"
    )


def test_score_segments_carry_hand_context_across_carcassi_seam() -> None:
    # Seven source-shaped bars exceed one beam-32 work envelope.  The historical
    # independent-segment implementation restarted at onset 14 on low-E fret 7,
    # producing a RED shift seam.  Context propagation keeps B2 at A-string
    # fret 2, which is where the public-domain edition puts it.
    #
    # The edition also prints finger 2 there, and until `oracle@0.6.0` the solver
    # agreed.  Flooring `d_max` at the width of the neck -- so that two fingers
    # can reach the outer strings at one fret, which they must, since otherwise a
    # G major chord is unplayable -- widened the space enough that the cost
    # function now prefers finger 3 for this note.  The placement is unchanged
    # and the seam property this test exists for still holds; what moved is a
    # fingering choice that used to match the engraver and no longer does.
    #
    # That is a real cost of the change and is recorded here rather than folded
    # into the expected value, because editorial fingerings turned out to be the
    # most reliable evidence available about this verifier and quietly dropping
    # an agreement with one would discard exactly that.
    bars = (
        (48, 55, 60, 64, 52, 55, 60, 64),
        (45, 57, 60, 64, 48, 57, 60, 64),
        (50, 57, 62, 65, 53, 57, 62, 65),
        (43, 55, 59, 65, 47, 55, 62, 65),
        (48, 55, 60, 64, 45, 55, 61, 64),
        (50, 57, 62, 65, 53, 57, 62, 65),
        (43, 55, 60, 64, 43, 55, 59, 65),
    )
    notes = tuple(
        Note(
            F(bar_index * 4) + F(event_index, 2),
            F(2) if event_index in (0, 4) else F(1, 2),
            pitch,
            "bass" if event_index in (0, 4) else "melody",
        )
        for bar_index, bar in enumerate(bars)
        for event_index, pitch in enumerate(bar)
    )

    result = solve_fingering_score(
        notes,
        (40, 45, 50, 55, 59, 64),
        0,
        MEDIAN_HAND,
        beam=32,
    )

    assert isinstance(result, Tab)
    seam_note = next(note for note in result.notes if note.onset == F(14))
    assert note_pitch(seam_note.string, seam_note.fret, result.tuning, result.capo) == 47
    # The seam property: the note is where the edition puts it, not restarted
    # high on the low E string.
    assert (seam_note.string, seam_note.fret) == (1, 2)
    # The finger the edition prints is 2; `oracle@0.6.0` picks 3. Asserted so the
    # divergence is visible and a future cost-function change that restores the
    # agreement fails loudly rather than passing unnoticed.
    assert seam_note.left_finger == 3
    assert check_playability(result, MEDIAN_HAND).verdict != "RED"
