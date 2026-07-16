from fractions import Fraction as F
from itertools import combinations

import pytest

import fretsure.solver.api as solver_api
from fretsure.geometry import STANDARD_TUNING, note_pitch
from fretsure.ir import Note
from fretsure.oracle.core import OracleResult, check_playability, passes_optimistic
from fretsure.oracle.input import (
    MAX_SOLVER_BEAM,
    MAX_SOLVER_FINAL_CHECKS,
    OracleInputCode,
)
from fretsure.oracle.profiles import MEDIAN_HAND, Profile, optimistic
from fretsure.solver.api import (
    Infeasible,
    InfeasibleCode,
    SolverInputError,
    solve_fingering,
)
from fretsure.solver.frames import FrameConfig, frame_configs
from fretsure.tab import Tab, TabNote


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


def test_solver_uses_detached_note_snapshot_after_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    onset = F(0)
    source = [Note(onset, F(1), 60, "melody")]
    real_ensure = solver_api.ensure_solver_input

    def mutate_source_after_barrier(*args: object, **kwargs: object) -> object:
        snapshot = real_ensure(*args, **kwargs)
        object.__setattr__(onset, "_denominator", 0)
        source.clear()
        return snapshot

    monkeypatch.setattr(solver_api, "ensure_solver_input", mutate_source_after_barrier)
    result = solver_api.solve_fingering(source, STANDARD_TUNING, 0, MEDIAN_HAND)

    assert isinstance(result, Tab)
    assert _pitches(result) == [60]


def test_melody_plus_bass_solves_non_red_and_preserves() -> None:
    notes = (
        Note(F(0), F(1), 64, "melody"),
        Note(F(0), F(1), 40, "bass"),
        Note(F(1), F(1), 65, "melody"),
        Note(F(1), F(1), 45, "bass"),
        Note(F(2), F(1), 67, "melody"),
        Note(F(2), F(1), 43, "bass"),
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
        isinstance(r, Tab) and check_playability(r, MEDIAN_HAND, tempo_bpm=90.0).verdict != "RED"
    )


def test_fast_single_line_never_returns_red() -> None:
    # a fast chromatic run needs high-position play (compressed frets) to keep
    # shifts feasible; the greedy beam may not find it and returns a safe
    # Infeasible, but it must NEVER return a RED tab (C3 contract).
    notes = tuple(Note(F(i, 8), F(1, 8), p, "melody") for i, p in enumerate([60, 62, 64, 65]))
    r = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND, tempo_bpm=90.0)
    assert isinstance(r, Infeasible) or (
        isinstance(r, Tab) and check_playability(r, MEDIAN_HAND, tempo_bpm=90.0).verdict != "RED"
    )


def test_fast_open_string_run_alternates_fingers_non_red() -> None:
    # fast run on open strings (no shift) isolates repeat-rate: the beam takes the
    # cheap all-open path and must alternate right fingers to stay non-RED.
    notes = tuple(Note(F(i, 8), F(1, 8), p, "melody") for i, p in enumerate([55, 59, 64, 59, 55]))
    r = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND, tempo_bpm=90.0)
    assert isinstance(r, Tab)
    assert check_playability(r, MEDIAN_HAND, tempo_bpm=90.0).verdict != "RED"


def test_two_pitches_only_on_one_string_is_infeasible() -> None:
    # 85 and 86 are each reachable only on the high-E string -> can't co-occur
    notes = (Note(F(0), F(1), 85, "melody"), Note(F(0), F(1), 86, "harmony"))
    result = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND)
    assert isinstance(result, Infeasible)
    assert result.onset == F(0)


def test_sixteen_note_frame_is_fast_typed_infeasible_not_invalid() -> None:
    # Pitch reachability is deliberately irrelevant: frame width is the stable,
    # earlier reason and must bypass candidate generation/search altogether.
    notes = tuple(Note(F(0), F(1), index, "harmony") for index in range(16))
    result = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND)
    assert isinstance(result, Infeasible)
    assert result.code is InfeasibleCode.NO_FRAME_CONFIG
    assert result.onset == F(0)


def test_hundred_note_sequence_uses_only_bounded_final_full_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original = solver_api.check_playability

    def counted_check(
        tab: Tab,
        profile: Profile,
        *,
        tempo_bpm: float = 90.0,
        beats_per_bar: int = 4,
    ) -> OracleResult:
        nonlocal calls
        calls += 1
        return original(
            tab,
            profile,
            tempo_bpm=tempo_bpm,
            beats_per_bar=beats_per_bar,
        )

    monkeypatch.setattr(solver_api, "check_playability", counted_check)
    notes = tuple(Note(F(index), F(1), 60, "melody") for index in range(100))
    result = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND)

    assert isinstance(result, Tab)
    assert 1 <= calls <= 16
    assert check_playability(result, MEDIAN_HAND).verdict != "RED"


def test_long_search_reconstructs_only_complete_finalist_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "notes" not in solver_api._State.__dataclass_fields__
    reconstructed_sizes: list[int] = []
    original = solver_api._reconstruct_notes

    def counted_reconstruction(state: solver_api._State) -> tuple[TabNote, ...]:
        notes = original(state)
        reconstructed_sizes.append(len(notes))
        return notes

    monkeypatch.setattr(solver_api, "_reconstruct_notes", counted_reconstruction)
    notes = tuple(Note(F(index), F(1), 60, "melody") for index in range(500))

    result = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND, beam=1)

    assert isinstance(result, Tab)
    assert reconstructed_sizes == [500]


def test_final_full_checks_have_a_hard_cap_and_keep_first_amber(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checks: list[Tab] = []
    reconstructions = 0
    original_reconstruct = solver_api._reconstruct_notes

    def counted_reconstruction(state: solver_api._State) -> tuple[TabNote, ...]:
        nonlocal reconstructions
        reconstructions += 1
        return original_reconstruct(state)

    def always_amber(
        tab: Tab,
        profile: Profile,
        *,
        tempo_bpm: float = 90.0,
        beats_per_bar: int = 4,
    ) -> OracleResult:
        del tempo_bpm, beats_per_bar
        checks.append(tab)
        return OracleResult(
            "AMBER",
            (),
            "test-oracle",
            profile.version,
            profile.fingerprint,
            "test-input",
        )

    monkeypatch.setattr(solver_api, "_reconstruct_notes", counted_reconstruction)
    monkeypatch.setattr(solver_api, "check_playability", always_amber)
    notes = (
        Note(F(0), F(1), 60, "melody"),
        Note(F(1), F(1), 60, "melody"),
    )

    result = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND, beam=64)

    assert isinstance(result, Tab)
    assert result == checks[0]
    assert len(checks) == reconstructions == MAX_SOLVER_FINAL_CHECKS


def test_final_gate_continues_past_amber_to_prefer_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked: list[Tab] = []

    def amber_then_green(
        tab: Tab,
        profile: Profile,
        *,
        tempo_bpm: float = 90.0,
        beats_per_bar: int = 4,
    ) -> OracleResult:
        del tempo_bpm, beats_per_bar
        checked.append(tab)
        verdict = "AMBER" if len(checked) == 1 else "GREEN"
        return OracleResult(
            verdict,
            (),
            "test-oracle",
            profile.version,
            profile.fingerprint,
            "test-input",
        )

    monkeypatch.setattr(solver_api, "check_playability", amber_then_green)
    notes = (
        Note(F(0), F(1), 60, "melody"),
        Note(F(1), F(1), 60, "melody"),
    )

    result = solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND, beam=8)

    assert result == checked[1]
    assert len(checked) == 2


def test_incremental_rejection_implies_full_optimistic_prefix_failure() -> None:
    """Differential property for the one-sided incremental prefilter."""

    def added(
        config: FrameConfig,
        onset: F,
        duration: F,
    ) -> tuple[TabNote, ...]:
        return tuple(
            TabNote(
                onset,
                duration,
                placement.string,
                placement.fret,
                placement.left_finger,
                placement.right_finger,
            )
            for placement in config.placements
        )

    optimistic_profile = optimistic(MEDIAN_HAND)
    first_configs = frame_configs((57,), STANDARD_TUNING, 0, MEDIAN_HAND, limit=6)
    second_configs = frame_configs((66,), STANDARD_TUNING, 0, MEDIAN_HAND, limit=6)
    empty = solver_api._IncrementalOracleState(
        (),
        (None, None, None, None),
        None,
    )

    for first_config in first_configs:
        for first_duration in (F(1, 8), F(2)):
            first = added(first_config, F(0), first_duration)
            prior = solver_api._advance_oracle_state(
                empty,
                onset=F(0),
                added=first,
                first_note_id=0,
                tuning=STANDARD_TUNING,
                capo=0,
                profile=optimistic_profile,
                tempo_bpm=90.0,
            )
            assert prior is not None
            for onset in (F(1, 16), F(1)):
                for second_config in second_configs:
                    second = added(second_config, onset, F(1))
                    advanced = solver_api._advance_oracle_state(
                        prior,
                        onset=onset,
                        added=second,
                        first_note_id=len(first),
                        tuning=STANDARD_TUNING,
                        capo=0,
                        profile=optimistic_profile,
                        tempo_bpm=90.0,
                    )
                    if advanced is None:
                        assert not passes_optimistic(
                            Tab(first + second, STANDARD_TUNING, 0),
                            MEDIAN_HAND,
                            tempo_bpm=90.0,
                        )


def test_generation_work_limit_fails_before_frame_config_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stress_profile = Profile(
        "stress@0.1",
        250.0,
        200.0,
        5_000.0,
        50.0,
        1e-6,
        max_fret=36,
    )
    tight_tuning = (0, 1, 2, 3, 4, 5)
    pitch_frames = list(combinations(range(10, 37), 3))[:32]
    notes = tuple(
        Note(F(frame), F(1), pitch, "melody" if index == 2 else "harmony")
        for frame, pitches in enumerate(pitch_frames)
        for index, pitch in enumerate(pitches)
    )

    def unexpected_generation(*args: object, **kwargs: object) -> list[FrameConfig]:
        raise AssertionError("config generation ran before its work preflight")

    monkeypatch.setattr(solver_api, "_frame_configs", unexpected_generation)

    with pytest.raises(SolverInputError) as caught:
        solve_fingering(notes, tight_tuning, 0, stress_profile, beam=1)

    assert any(
        item.code is OracleInputCode.SOLVER_WORK_LIMIT
        for item in caught.value.diagnostics
    )


def test_high_branching_selection_work_fails_before_frame_config_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stress_profile = Profile(
        "stress@0.1",
        250.0,
        200.0,
        5_000.0,
        50.0,
        1e-6,
        max_fret=36,
    )
    tight_tuning = (0, 1, 2, 3, 4, 5)
    notes = tuple(
        Note(F(frame), F(1), pitch, "melody" if index == 2 else "harmony")
        for frame in range(19)
        for index, pitch in enumerate((10, 11, 12))
    )

    def unexpected_generation(*args: object, **kwargs: object) -> list[FrameConfig]:
        raise AssertionError("config generation ran before state-selection preflight")

    monkeypatch.setattr(solver_api, "_frame_configs", unexpected_generation)

    with pytest.raises(SolverInputError) as caught:
        solve_fingering(
            notes,
            tight_tuning,
            0,
            stress_profile,
            beam=MAX_SOLVER_BEAM,
        )

    assert any(
        item.code is OracleInputCode.SOLVER_WORK_LIMIT
        and "state_selection=" in item.message
        for item in caught.value.diagnostics
    )


def test_empty_notes_is_typed_infeasible_not_an_empty_certifiable_tab() -> None:
    result = solve_fingering((), STANDARD_TUNING, 0, MEDIAN_HAND)
    assert isinstance(result, Infeasible)
    assert result.code is InfeasibleCode.EMPTY_TARGET
    assert result.onset is None
    assert result.pitches == ()


@pytest.mark.parametrize("beam", [0, -1, True, 1.5, 1025])
def test_invalid_beam_is_typed_input_error_not_silently_clamped(beam: object) -> None:
    notes = _melody([60, 62, 64])
    with pytest.raises(SolverInputError) as caught:
        solve_fingering(  # type: ignore[arg-type]
            notes, STANDARD_TUNING, 0, MEDIAN_HAND, beam=beam
        )
    assert any(item.path == "beam" for item in caught.value.diagnostics)


@pytest.mark.parametrize(
    "bad_note",
    [
        Note(-F(1), F(1), 60, "melody"),
        Note(F(0), F(0), 60, "melody"),
        Note(F(0), F(1), -1, "melody"),
        Note(F(0), F(1), 128, "melody"),
        Note(F(0), F(1), True, "melody"),
        Note(0.0, F(1), 60, "melody"),  # type: ignore[arg-type]
        Note(F(0), 1.0, 60, "melody"),  # type: ignore[arg-type]
        Note(F(0), F(1), 60, "lead"),  # type: ignore[arg-type]
    ],
)
def test_solver_rejects_invalid_note_domain_before_search(bad_note: Note) -> None:
    with pytest.raises(SolverInputError):
        solve_fingering((bad_note,), STANDARD_TUNING, 0, MEDIAN_HAND)


def test_solver_rejects_duplicate_onset_pitch_instead_of_overwriting_duration() -> None:
    notes = (
        Note(F(0), F(1), 60, "melody"),
        Note(F(0), F(2), 60, "harmony"),
    )
    with pytest.raises(SolverInputError) as caught:
        solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND)
    assert any(item.code.value == "DUPLICATE_ONSET_PITCH" for item in caught.value.diagnostics)


def test_solver_reuses_instrument_and_tempo_contract() -> None:
    notes = _melody([60])
    with pytest.raises(SolverInputError) as tuning_error:
        solve_fingering(notes, (40,), 0, MEDIAN_HAND)
    assert any(item.path == "tuning" for item in tuning_error.value.diagnostics)
    with pytest.raises(SolverInputError) as tempo_error:
        solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND, tempo_bpm=float("nan"))
    assert any(item.path == "tempo_bpm" for item in tempo_error.value.diagnostics)


def test_deterministic() -> None:
    notes = _melody([60, 64, 67, 72])
    assert solve_fingering(notes, STANDARD_TUNING, 0, MEDIAN_HAND) == solve_fingering(
        notes, STANDARD_TUNING, 0, MEDIAN_HAND
    )
