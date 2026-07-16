from __future__ import annotations

import sys
from dataclasses import replace
from fractions import Fraction as F
from itertools import combinations
from typing import Any, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

import fretsure.oracle.core as oracle_core
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import Note
from fretsure.oracle.core import check_playability, passes_optimistic
from fretsure.oracle.input import (
    MAX_NOTES_PER_ONSET,
    MAX_SOLVER_BEAM,
    MAX_SOLVER_FRAME_CONFIGS,
    MAX_TAB_NOTES,
    ORACLE_INPUT_SCHEMA_VERSION,
    OracleInputCode,
    OracleInputError,
    SolverInputError,
    oracle_checker_work_upper_bound,
    solver_frame_config_count_upper_bound,
    solver_frame_generation_work_upper_bound,
    validate_oracle_input,
    validate_solver_domain,
    validate_solver_input,
)
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.tab import Tab, TabNote


def _note(**changes: object) -> TabNote:
    values: dict[str, object] = {
        "onset": F(0),
        "duration": F(1),
        "string": 0,
        "fret": 3,
        "left_finger": 1,
        "right_finger": "p",
    }
    values.update(changes)
    return TabNote(**values)  # type: ignore[arg-type]


def _tab(note: TabNote | None = None, **changes: object) -> Tab:
    values: dict[str, object] = {
        "notes": (_note() if note is None else note,),
        "tuning": STANDARD_TUNING,
        "capo": 0,
    }
    values.update(changes)
    return Tab(**values)  # type: ignore[arg-type]


def _error(
    tab: object,
    *,
    profile: object = MEDIAN_HAND,
    tempo_bpm: object = 90.0,
    beats_per_bar: object = 4,
) -> OracleInputError:
    with pytest.raises(OracleInputError) as caught:
        check_playability(  # type: ignore[arg-type]
            tab,
            profile,  # type: ignore[arg-type]
            tempo_bpm=tempo_bpm,  # type: ignore[arg-type]
            beats_per_bar=beats_per_bar,  # type: ignore[arg-type]
        )
    return caught.value


def _codes(error: OracleInputError) -> set[OracleInputCode]:
    return {diagnostic.code for diagnostic in error.diagnostics}


def test_input_schema_is_semantically_versioned() -> None:
    assert ORACLE_INPUT_SCHEMA_VERSION == "tab-input@0.2.0"


def test_empty_tab_is_invalid_not_green_or_red() -> None:
    tab = Tab((), STANDARD_TUNING, 0)
    error = _error(tab)
    assert OracleInputCode.EMPTY_TAB in _codes(error)
    assert error.diagnostics[0].path == "tab.notes"


def test_wrong_top_level_types_are_typed_and_do_not_crash() -> None:
    assert OracleInputCode.TAB_TYPE in _codes(_error(object()))
    assert OracleInputCode.PROFILE_TYPE in _codes(_error(_tab(), profile=object()))


@pytest.mark.parametrize(
    ("tuning", "code"),
    [
        ([], OracleInputCode.TUNING_TYPE),
        ((), OracleInputCode.TUNING_LENGTH),
        ((40, 45, 50, 55, 59), OracleInputCode.TUNING_LENGTH),
        ((40, 45, 50, 55, 59, 64, 69), OracleInputCode.TUNING_LENGTH),
        ((40, 45, 50, 55, 59, True), OracleInputCode.TUNING_PITCH),
        ((40, 45, 50, 55, 59, 128), OracleInputCode.TUNING_PITCH),
        ((40, 45, 50, 55, 55, 64), OracleInputCode.TUNING_ORDER),
        ((40, 45, 50, 55, 64, 59), OracleInputCode.TUNING_ORDER),
    ],
)
def test_tuning_contract_is_fail_closed(tuning: object, code: OracleInputCode) -> None:
    assert code in _codes(_error(_tab(tuning=tuning)))


@pytest.mark.parametrize("capo", [-1, 1.5, True, "0"])
def test_capo_requires_an_exact_nonnegative_int(capo: object) -> None:
    assert OracleInputCode.CAPO in _codes(_error(_tab(capo=capo)))


def test_capo_must_fit_profile_and_midi_envelope() -> None:
    too_high = replace(MEDIAN_HAND, max_fret=10)
    assert OracleInputCode.CAPO_RANGE in _codes(_error(_tab(capo=11), profile=too_high))
    high_tuning = (100, 105, 110, 115, 120, 125)
    assert OracleInputCode.CAPO_RANGE in _codes(_error(_tab(tuning=high_tuning, capo=3)))


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("onset", -F(1), OracleInputCode.ONSET_RANGE),
        ("onset", 0.0, OracleInputCode.ONSET_TYPE),
        ("duration", F(0), OracleInputCode.DURATION_RANGE),
        ("duration", -F(1), OracleInputCode.DURATION_RANGE),
        ("duration", 1.0, OracleInputCode.DURATION_TYPE),
        ("string", -1, OracleInputCode.STRING),
        ("string", 6, OracleInputCode.STRING),
        ("string", True, OracleInputCode.STRING),
        ("string", "0", OracleInputCode.STRING),
        ("fret", -1, OracleInputCode.FRET_RANGE),
        ("fret", 1.0, OracleInputCode.FRET_TYPE),
        ("fret", True, OracleInputCode.FRET_TYPE),
        ("fret", 37, OracleInputCode.FRET_RANGE),
        ("left_finger", -1, OracleInputCode.LEFT_FINGER),
        ("left_finger", 5, OracleInputCode.LEFT_FINGER),
        ("left_finger", True, OracleInputCode.LEFT_FINGER),
        ("right_finger", "z", OracleInputCode.RIGHT_FINGER),
        ("right_finger", [], OracleInputCode.RIGHT_FINGER),
    ],
)
def test_note_domain_errors_are_typed(field: str, value: object, code: OracleInputCode) -> None:
    assert code in _codes(_error(_tab(_note(**{field: value}))))


def test_fraction_component_bit_limit_prevents_arithmetic_dos() -> None:
    huge = F(1 << 300, 1)
    assert OracleInputCode.FRACTION_TOO_LARGE in _codes(_error(_tab(_note(onset=huge))))


def test_low_level_corrupted_fraction_is_typed_without_executing_hooks() -> None:
    calls: list[str] = []

    class HostileInt(int):
        def __abs__(self) -> int:
            calls.append("abs")
            raise AssertionError("hostile integer hook executed")

        def __eq__(self, other: object) -> bool:
            calls.append("eq")
            raise AssertionError("hostile integer hook executed")

    onset = F(0)
    object.__setattr__(onset, "_numerator", HostileInt(0))
    error = _error(_tab(_note(onset=onset)))

    assert _codes(error) == {OracleInputCode.FRACTION_INVALID}
    assert calls == []


def test_sounding_midi_must_remain_in_range() -> None:
    tuning = (90, 95, 100, 105, 110, 115)
    tab = _tab(_note(string=5, fret=20), tuning=tuning)
    assert OracleInputCode.SOUNDING_PITCH_RANGE in _codes(_error(tab))


@pytest.mark.parametrize(
    "tempo",
    [
        0,
        -1,
        True,
        "90",
        float("nan"),
        float("inf"),
        -float("inf"),
        1001.0,
        1 << 10_000,
    ],
)
def test_tempo_contract_rejects_nonphysical_or_unstable_values(tempo: object) -> None:
    assert OracleInputCode.TEMPO in _codes(_error(_tab(), tempo_bpm=tempo))


def test_tempo_subclass_is_rejected_before_overloaded_arithmetic() -> None:
    class HostileTempo(float):
        def __rtruediv__(self, _other: object) -> float:
            raise AssertionError("hostile tempo arithmetic executed")

    error = _error(_tab(), tempo_bpm=HostileTempo(90.0))
    assert _codes(error) == {OracleInputCode.TEMPO}


def test_forged_profile_is_typed_invalid_before_transform_or_geometry() -> None:
    class HostileFloat(float):
        def __mul__(self, _other: object) -> float:
            raise AssertionError("hostile profile arithmetic executed")

    profile = replace(MEDIAN_HAND)
    object.__setattr__(profile, "hand_span_mm", HostileFloat(100.0))
    error = _error(_tab(), profile=profile)
    assert _codes(error) == {OracleInputCode.PROFILE_INVALID}


def test_forged_extreme_scale_is_typed_invalid_before_geometry() -> None:
    profile = replace(MEDIAN_HAND)
    object.__setattr__(profile, "string_length_mm", sys.float_info.max)
    error = _error(_tab(), profile=profile)
    assert _codes(error) == {OracleInputCode.PROFILE_INVALID}


@pytest.mark.parametrize("beats", [0, -1, True, 4.0, "4", 33])
def test_beats_per_bar_has_a_safe_exact_integer_envelope(beats: object) -> None:
    assert OracleInputCode.BEATS_PER_BAR in _codes(_error(_tab(), beats_per_bar=beats))


def test_validation_collects_deterministic_paths_before_predicates() -> None:
    tab = _tab(_note(onset=0.0, duration=0.0, string=99, right_finger=[]))
    first = validate_oracle_input(tab, MEDIAN_HAND, tempo_bpm=0.0, beats_per_bar=0)
    second = validate_oracle_input(tab, MEDIAN_HAND, tempo_bpm=0.0, beats_per_bar=0)
    assert first == second
    assert [item.path for item in first] == sorted(item.path for item in first)


def test_fast_path_raises_the_same_typed_error_as_full_check() -> None:
    tab = _tab(_note(string=-1))
    with pytest.raises(OracleInputError) as full:
        check_playability(tab, MEDIAN_HAND)
    with pytest.raises(OracleInputError) as fast:
        passes_optimistic(tab, MEDIAN_HAND)
    assert fast.value.diagnostics == full.value.diagnostics


def test_checker_uses_detached_tab_snapshot_after_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _tab(_note(fret=3, left_finger=0))
    real_all_diagnostics = oracle_core._all_diagnostics

    def mutate_source_after_barrier(
        snapshot: Tab,
        profile: Profile,
        *,
        tempo_bpm: float,
        beats_per_bar: int,
    ) -> list[object]:
        source.__dict__["notes"] = ()
        return cast(
            list[object],
            real_all_diagnostics(
                snapshot,
                profile,
                tempo_bpm=tempo_bpm,
                beats_per_bar=beats_per_bar,
            ),
        )

    monkeypatch.setattr(oracle_core, "_all_diagnostics", mutate_source_after_barrier)
    result = oracle_core.check_playability(source, MEDIAN_HAND)

    assert result.verdict == "RED"
    assert any(d.violation_type == "MALFORMED_FINGERING" for d in result.diagnostics)


def test_checker_detaches_fraction_components_before_predicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    onset = F(0)
    source = _tab(_note(onset=onset, fret=3, left_finger=0))
    real_all_diagnostics = oracle_core._all_diagnostics

    def mutate_source_after_barrier(
        snapshot: Tab,
        profile: Profile,
        *,
        tempo_bpm: float,
        beats_per_bar: int,
    ) -> list[object]:
        object.__setattr__(onset, "_denominator", 0)
        return cast(
            list[object],
            real_all_diagnostics(
                snapshot,
                profile,
                tempo_bpm=tempo_bpm,
                beats_per_bar=beats_per_bar,
            ),
        )

    monkeypatch.setattr(oracle_core, "_all_diagnostics", mutate_source_after_barrier)
    result = oracle_core.check_playability(source, MEDIAN_HAND)

    assert result.verdict == "RED"


def test_well_typed_but_infeasible_fingering_remains_a_red_judgment() -> None:
    # The schema is valid; the exhibited fingering is physically malformed.
    tab = _tab(_note(fret=3, left_finger=0))
    result = check_playability(tab, MEDIAN_HAND)
    assert result.verdict == "RED"
    assert any(d.violation_type == "MALFORMED_FINGERING" for d in result.diagnostics)


def test_profile_relative_neck_range_remains_red_not_invalid() -> None:
    # Fret 23 is inside the public physical envelope (<=36), but outside the
    # median profile's 22-fret instrument model, so this is a model judgment.
    tab = _tab(_note(fret=23, left_finger=1))
    result = check_playability(tab, MEDIAN_HAND)
    assert result.verdict == "RED"
    assert any(d.violation_type == "RANGE" for d in result.diagnostics)


def test_note_count_limit_rejects_before_any_verdict() -> None:
    note = _note()
    tab = _tab(notes=(note,) * (MAX_TAB_NOTES + 1))
    error = _error(tab)
    assert OracleInputCode.TOO_MANY_NOTES in _codes(error)


def test_frame_width_limit_rejects_before_predicates() -> None:
    notes = tuple(
        _note(string=index % 6, right_finger=("p", "i", "m", "a")[index % 4])
        for index in range(MAX_NOTES_PER_ONSET + 1)
    )
    error = _error(_tab(notes=notes))
    assert OracleInputCode.FRAME_TOO_LARGE in _codes(error)


class _ExplodingSequence:
    """A hostile duck-typed Sequence must never execute at the boundary."""

    def __len__(self) -> int:
        raise AssertionError("validator executed untrusted __len__")

    def __getitem__(self, _index: object) -> object:
        raise AssertionError("validator executed untrusted __getitem__")


def test_solver_rejects_custom_sequence_without_executing_it() -> None:
    hostile = _ExplodingSequence()
    diagnostics = validate_solver_input(
        hostile,
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
    )
    assert [item.code for item in diagnostics] == [OracleInputCode.SOLVER_NOTES_TYPE]


def test_solver_rejects_corrupted_fraction_with_typed_diagnostic() -> None:
    duration = F(1)
    object.__setattr__(duration, "_denominator", object())
    diagnostics = validate_solver_input(
        (Note(F(0), duration, 60, "melody"),),
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
    )

    assert {item.code for item in diagnostics} == {
        OracleInputCode.FRACTION_INVALID
    }


def test_solver_work_limit_is_typed_before_search() -> None:
    stress_profile = Profile(
        "stress@0.1",
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
    diagnostics = validate_solver_input(
        notes,
        tuning,
        0,
        stress_profile,
        beam=MAX_SOLVER_BEAM,
    )

    work = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.code is OracleInputCode.SOLVER_WORK_LIMIT
    ]
    assert len(work) == 1
    assert work[0].path == "notes"
    assert "state_selection=" in work[0].message


def test_solver_domain_validation_does_not_price_a_hypothetical_search() -> None:
    stress_profile = Profile(
        "stress@0.1",
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

    assert validate_solver_domain(notes, tuning, 0, stress_profile) == ()
    assert any(
        diagnostic.code is OracleInputCode.SOLVER_WORK_LIMIT
        for diagnostic in validate_solver_input(
            notes,
            tuning,
            0,
            stress_profile,
            beam=MAX_SOLVER_BEAM,
        )
    )


def test_solver_work_envelope_preserves_long_narrow_open_chord_search() -> None:
    notes = tuple(
        Note(F(frame), F(1), pitch, "melody" if index == 3 else "harmony")
        for frame in range(500)
        for index, pitch in enumerate((40, 45, 50, 55))
    )

    diagnostics = validate_solver_input(
        notes,
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
        beam=4,
    )

    assert not any(
        item.code is OracleInputCode.SOLVER_WORK_LIMIT for item in diagnostics
    )


def test_final_checker_work_bound_charges_profiles_sorts_and_frame_pairs() -> None:
    assert oracle_checker_work_upper_bound(2_000, (4,) * 500) == 889_024


def test_checker_work_bound_charges_active_sounding_pairs_per_frame() -> None:
    # Six one-note attacks can still form six-note sounding frames through
    # sustain.  The bound therefore charges 6**2 active pairs per onset in
    # addition to the one attack pair at each onset.
    assert oracle_checker_work_upper_bound(6, (1,) * 6) == 3_112


def test_solver_generation_bound_counts_distinct_pitch_frames_once() -> None:
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
    assert solver_frame_config_count_upper_bound(
        (10, 11, 12), tight_tuning, 0, stress_profile
    ) == MAX_SOLVER_FRAME_CONFIGS
    assert solver_frame_generation_work_upper_bound(
        (10, 11, 12), tight_tuning, 0, stress_profile
    ) == 454_176

    pitch_frames = list(combinations(range(10, 37), 3))[:32]
    distinct = tuple(
        Note(F(frame), F(1), pitch, "melody" if index == 2 else "harmony")
        for frame, pitches in enumerate(pitch_frames)
        for index, pitch in enumerate(pitches)
    )
    repeated = tuple(
        Note(F(frame), F(1), pitch, "melody" if index == 2 else "harmony")
        for frame in range(32)
        for index, pitch in enumerate((10, 11, 12))
    )

    distinct_diagnostics = validate_solver_input(
        distinct,
        tight_tuning,
        0,
        stress_profile,
        beam=1,
    )
    repeated_diagnostics = validate_solver_input(
        repeated,
        tight_tuning,
        0,
        stress_profile,
        beam=1,
    )

    assert any(
        item.code is OracleInputCode.SOLVER_WORK_LIMIT
        for item in distinct_diagnostics
    )
    assert not any(
        item.code is OracleInputCode.SOLVER_WORK_LIMIT
        for item in repeated_diagnostics
    )


def test_solver_generation_bound_charges_raw_same_string_cartesian_product() -> None:
    same_string_profile = replace(MEDIAN_HAND, version="same-string@0.1", max_fret=1)

    # Both pitches are individually reachable, but only on string zero.  The
    # frame has no distinct-string geometry, yet frame_configs still visits its
    # one raw Cartesian combo before rejecting it.
    assert solver_frame_generation_work_upper_bound(
        (0, 1), (0, 10, 20, 30, 40, 50), 0, same_string_profile
    ) == 1


_SCALAR_JUNK = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-10_000, max_value=10_000),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=20),
    st.binary(max_size=20),
)


@given(
    onset=_SCALAR_JUNK,
    duration=_SCALAR_JUNK,
    string=_SCALAR_JUNK,
    fret=_SCALAR_JUNK,
    left_finger=_SCALAR_JUNK,
    right_finger=_SCALAR_JUNK,
)
def test_fuzzed_tab_note_fields_are_either_typed_invalid_or_judged(
    onset: object,
    duration: object,
    string: object,
    fret: object,
    left_finger: object,
    right_finger: object,
) -> None:
    note = TabNote(
        cast(Any, onset),
        cast(Any, duration),
        cast(Any, string),
        cast(Any, fret),
        cast(Any, left_finger),
        cast(Any, right_finger),
    )
    tab = _tab(note)
    first = validate_oracle_input(tab, MEDIAN_HAND)
    second = validate_oracle_input(tab, MEDIAN_HAND)
    assert first == second
    if first:
        with pytest.raises(OracleInputError) as caught:
            check_playability(tab, MEDIAN_HAND)
        assert caught.value.diagnostics == first
    else:
        assert check_playability(tab, MEDIAN_HAND).verdict in {"GREEN", "AMBER", "RED"}


@given(
    notes=st.one_of(
        _SCALAR_JUNK,
        st.lists(_SCALAR_JUNK, max_size=8),
        st.tuples(*([_SCALAR_JUNK] * 4)),
    ),
    tuning=st.one_of(
        _SCALAR_JUNK,
        st.lists(_SCALAR_JUNK, max_size=8),
        st.tuples(*([_SCALAR_JUNK] * 6)),
    ),
    capo=_SCALAR_JUNK,
    tempo=_SCALAR_JUNK,
    beam=_SCALAR_JUNK,
)
def test_fuzzed_solver_boundary_never_leaks_an_untyped_failure(
    notes: object,
    tuning: object,
    capo: object,
    tempo: object,
    beam: object,
) -> None:
    first = validate_solver_input(
        notes,
        tuning,
        capo,
        MEDIAN_HAND,
        tempo_bpm=tempo,
        beam=beam,
    )
    second = validate_solver_input(
        notes,
        tuning,
        capo,
        MEDIAN_HAND,
        tempo_bpm=tempo,
        beam=beam,
    )
    assert first == second
    if first:
        with pytest.raises(SolverInputError):
            from fretsure.solver.api import solve_fingering

            solve_fingering(  # type: ignore[arg-type]
                notes,
                tuning,
                capo,
                MEDIAN_HAND,
                tempo_bpm=tempo,
                beam=beam,
            )
    else:
        assert type(notes) in (list, tuple)
        assert all(type(note) is Note for note in cast(list[object] | tuple[object, ...], notes))
