from collections.abc import Sequence
from contextlib import AbstractContextManager
from fractions import Fraction as F
from typing import Literal

import pytest

import fretsure.bench.baselines as baseline_module
from fretsure.agent.arranger import (
    ArrangeGoal,
    arrangement_source_context,
    arrangement_source_context_sha256,
    proposal_output_token_budget,
    propose_arrangement,
)
from fretsure.bench.baselines import (
    B3_AVAILABILITY,
    B4_AVAILABILITY,
    LICENSE_AUDITED_REPRODUCIBLE_ADAPTER_ABSENT,
    OPTIONAL_BASELINE_AVAILABILITY,
    PURE_SOLVER_BASELINE_SLOTS,
    RAW_BASELINE_TEMPERATURE,
    BaselineAvailabilityStatus,
    BaselineId,
    PureSolverStatus,
    RawCallScopeError,
    RawParseCode,
    RawStatus,
    baseline_pure_solver,
    baseline_raw_llm,
    build_raw_baseline_request,
    collect_raw_llm_baseline,
    repeat_pure_solver_outcome,
    run_pure_solver_baseline,
)
from fretsure.bench.generator import GenConfig, generate_leadsheet
from fretsure.bench.observe import (
    CallFailureCode,
    CallSequence,
    CallStage,
    CandidateCallScopes,
    InMemoryObservationSink,
    ObservingLLM,
)
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import Meta, MusicIR, Note
from fretsure.llm.client import FakeLLM, LLMIntegrityError
from fretsure.oracle.core import check_playability
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.solver.api import Infeasible, solve_fingering
from fretsure.tab import Tab

_IR = MusicIR(
    (Note(F(0), F(1), 64, "melody"), Note(F(0), F(1), 40, "bass")),
    (),
    Meta("C", (4, 4), 90.0, "t", "t", "PD"),
)

# A schema/domain-valid raw tab that is physically RED (fret 1 + fret 15).
_RAW_RED = (
    '{"tuning":[40,45,50,55,59,64],"capo":0,"notes":['
    '{"onset":"0/1","duration":"1/1","string":0,"fret":1,'
    '"left_finger":1,"right_finger":"p"},'
    '{"onset":"0/1","duration":"1/1","string":1,"fret":15,'
    '"left_finger":4,"right_finger":"i"}]}'
)


def _scopes(run_id: str = "run-1") -> CallSequence:
    return CallSequence(run_id)


def _bound_scopes(run_id: str = "run-1") -> CandidateCallScopes:
    return _scopes(run_id).bind_candidate(
        item_id="item-1",
        family_id="family-1",
        cluster_id="cluster-1",
        pair_id="pair-1",
    )


def test_raw_llm_baseline_can_produce_unplayable() -> None:
    tab = baseline_raw_llm(_IR, ArrangeGoal(), FakeLLM([_RAW_RED]), MEDIAN_HAND)
    assert isinstance(tab, Tab)
    assert check_playability(tab, MEDIAN_HAND).verdict == "RED"


def test_raw_llm_baseline_bad_output_is_none() -> None:
    assert (
        baseline_raw_llm(
            _IR,
            ArrangeGoal(),
            FakeLLM(["not a tab"]),
            MEDIAN_HAND,
        )
        is None
    )


def test_formal_raw_valid_tab_remains_unverified_and_can_be_red() -> None:
    request = build_raw_baseline_request(_IR, ArrangeGoal(), MEDIAN_HAND)
    sink = InMemoryObservationSink()
    observed = ObservingLLM(
        FakeLLM([f"prose before {_RAW_RED} prose after"]), sink, clock_ns=lambda: 0
    )
    scopes = _scopes().bind_candidate(
        item_id="item-1",
        family_id="family-1",
        cluster_id="cluster-1",
        pair_id="pair-1",
    )

    outcome = collect_raw_llm_baseline(
        request,
        observed,
        MEDIAN_HAND,
        sample_index=3,
        call_scope_factory=scopes,
    )

    assert outcome.status is RawStatus.VALID_TAB
    assert outcome.parse_code is None and outcome.call_failure_code is None
    assert outcome.tab is not None
    assert check_playability(outcome.tab, MEDIAN_HAND).verdict == "RED"
    assert not outcome.fallback_assisted
    assert outcome.llm_calls == 1
    assert [(row.stage, row.sample_index, row.candidate_index) for row in sink.intents] == [
        (CallStage.RAW, 3, 3)
    ]
    assert sink.intents[0].stage_ordinal == 0
    assert outcome.observation_key.logical_call_id == sink.intents[0].logical_call_id
    assert outcome.observation_key.call_index == sink.intents[0].call_index
    assert not hasattr(outcome, "input_tokens") and not hasattr(outcome, "output_tokens")


def test_successful_observation_can_honestly_end_as_parse_failed() -> None:
    request = build_raw_baseline_request(_IR, ArrangeGoal(), MEDIAN_HAND)
    sink = InMemoryObservationSink()
    observed = ObservingLLM(FakeLLM(["not a tab"]), sink, clock_ns=lambda: 0)

    outcome = collect_raw_llm_baseline(
        request,
        observed,
        MEDIAN_HAND,
        sample_index=0,
        call_scope_factory=_bound_scopes(),
    )

    assert outcome.status is RawStatus.PARSE_FAILED
    assert outcome.parse_code is RawParseCode.NO_JSON_OBJECT
    assert outcome.call_failure_code is None and outcome.tab is None
    assert sink.results[0].status == "succeeded"
    assert sink.results[0].failure_code is None


def test_raw_strict_parser_preserves_duplicate_key_rejection() -> None:
    duplicate = _RAW_RED.replace('"capo":0', '"capo":0,"capo":0', 1)
    request = build_raw_baseline_request(_IR, ArrangeGoal(), MEDIAN_HAND)
    sink = InMemoryObservationSink()
    observed = ObservingLLM(FakeLLM([duplicate]), sink, clock_ns=lambda: 0)

    outcome = collect_raw_llm_baseline(
        request,
        observed,
        MEDIAN_HAND,
        sample_index=1,
        call_scope_factory=_bound_scopes(),
    )

    assert outcome.status is RawStatus.PARSE_FAILED
    assert outcome.parse_code is RawParseCode.TAB_SCHEMA_INVALID
    assert sink.results[0].status == "succeeded"


def test_raw_separates_domain_and_requested_instrument_failures() -> None:
    domain_invalid = _RAW_RED.replace(
        '"tuning":[40,45,50,55,59,64]',
        '"tuning":[40,45,50,55,59]',
    )
    instrument_mismatch = _RAW_RED.replace('"capo":0', '"capo":1', 1)
    request = build_raw_baseline_request(_IR, ArrangeGoal(), MEDIAN_HAND)

    domain = collect_raw_llm_baseline(
        request,
        FakeLLM([domain_invalid]),
        MEDIAN_HAND,
        sample_index=0,
        call_scope_factory=_bound_scopes(),
    )
    mismatch = collect_raw_llm_baseline(
        request,
        FakeLLM([instrument_mismatch]),
        MEDIAN_HAND,
        sample_index=1,
        call_scope_factory=_bound_scopes("run-2"),
    )

    assert domain.parse_code is RawParseCode.TAB_DOMAIN_INVALID
    assert mismatch.parse_code is RawParseCode.INSTRUMENT_MISMATCH


class _FailingLLM:
    model_id = "failing-test"

    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        del system, user, max_tokens, temperature
        self.calls += 1
        raise RuntimeError("private transport detail")


def test_raw_call_failure_is_one_itt_outcome_without_retry_or_fallback() -> None:
    request = build_raw_baseline_request(_IR, ArrangeGoal(), MEDIAN_HAND)
    delegate = _FailingLLM()
    sink = InMemoryObservationSink()
    observed = ObservingLLM(delegate, sink, clock_ns=lambda: 0)

    outcome = collect_raw_llm_baseline(
        request,
        observed,
        MEDIAN_HAND,
        sample_index=2,
        call_scope_factory=_bound_scopes(),
    )

    assert outcome.status is RawStatus.CALL_FAILED
    assert outcome.call_failure_code is CallFailureCode.DELEGATE_FAILED
    assert outcome.parse_code is None and outcome.tab is None
    assert outcome.llm_calls == delegate.calls == 1
    assert sink.results[0].status == "failed"


def test_raw_integrity_failure_is_never_converted_to_itt_call_failure() -> None:
    class IntegrityFailingLLM:
        model_id = "integrity-test"

        def complete(
            self,
            *,
            system: str,
            user: str,
            max_tokens: int = 1024,
            temperature: float = 0.0,
        ) -> str:
            del system, user, max_tokens, temperature
            raise LLMIntegrityError("formal observation failed")

    request = build_raw_baseline_request(_IR, ArrangeGoal(), MEDIAN_HAND)
    with pytest.raises(LLMIntegrityError, match="formal observation failed"):
        collect_raw_llm_baseline(
            request,
            IntegrityFailingLLM(),
            MEDIAN_HAND,
            sample_index=0,
            call_scope_factory=_bound_scopes(),
        )


def test_raw_scope_rejects_a_sample_candidate_identity_mismatch_before_call() -> None:
    scopes = _bound_scopes()

    class WrongIdentity:
        def __call__(
            self,
            stage: Literal["raw"],
            candidate_index: int,
            stage_ordinal: int,
        ) -> AbstractContextManager[object]:
            return scopes(stage, candidate_index + 1, stage_ordinal)

    llm = FakeLLM([_RAW_RED])
    request = build_raw_baseline_request(_IR, ArrangeGoal(), MEDIAN_HAND)

    with pytest.raises(RawCallScopeError, match="wrong identity"):
        collect_raw_llm_baseline(
            request,
            llm,
            MEDIAN_HAND,
            sample_index=0,
            call_scope_factory=WrongIdentity(),
        )
    assert llm.calls == []


def test_raw_and_proposal_share_source_context_capacity_and_item_controls() -> None:
    goal = ArrangeGoal(capo=2, tempo_bpm=72.0)
    request = build_raw_baseline_request(_IR, goal, MEDIAN_HAND)
    proposal = FakeLLM(['{"notes":[{"onset":"0","duration":"1","pitch":64,"voice":"melody"}]}'])

    propose_arrangement(_IR, goal, proposal, temperature=RAW_BASELINE_TEMPERATURE)

    source_context = arrangement_source_context(_IR)
    assert request.user.startswith(f"{source_context}\n")
    assert proposal.calls[0]["user"].startswith(f"{source_context}\n")
    assert request.source_context_sha256 == arrangement_source_context_sha256(_IR)
    assert request.max_tokens == proposal.calls[0]["max_tokens"]
    assert request.max_tokens == proposal_output_token_budget(_IR)
    assert request.temperature == proposal.calls[0]["temperature"] == RAW_BASELINE_TEMPERATURE
    assert request.tuning == STANDARD_TUNING and request.capo == 2
    assert request.tempo_bpm == 72.0 and request.beats_per_bar == 4
    assert "Requested tuning (open-string MIDI, low to high): [40,45,50,55,59,64]" in request.user
    assert "Requested capo: 2" in request.user


def test_raw_passes_effective_tempo_and_source_meter_to_domain_validator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = build_raw_baseline_request(
        MusicIR(_IR.notes, (), Meta("C", (3, 4), 90.0, "t", "t", "PD")),
        ArrangeGoal(tempo_bpm=66.0),
        MEDIAN_HAND,
    )
    captured: dict[str, object] = {}
    expected_tab = baseline_raw_llm(_IR, ArrangeGoal(), FakeLLM([_RAW_RED]), MEDIAN_HAND)
    assert isinstance(expected_tab, Tab)

    def validating_stub(
        payload: str,
        *,
        profile: Profile,
        tempo_bpm: float = 90.0,
        beats_per_bar: int = 4,
    ) -> Tab:
        captured.update(
            payload=payload,
            profile=profile,
            tempo_bpm=tempo_bpm,
            beats_per_bar=beats_per_bar,
        )
        return expected_tab

    monkeypatch.setattr(baseline_module, "validated_tab_from_json", validating_stub)
    outcome = collect_raw_llm_baseline(
        request,
        FakeLLM([_RAW_RED]),
        MEDIAN_HAND,
        sample_index=0,
        call_scope_factory=_bound_scopes(),
    )

    assert outcome.status is RawStatus.VALID_TAB
    assert captured["tempo_bpm"] == 66.0
    assert captured["beats_per_bar"] == 3


def test_pure_solver_baseline_never_red() -> None:
    ir = generate_leadsheet(GenConfig(seed=1, bars=4))
    result = baseline_pure_solver(ir, ArrangeGoal(tuning=STANDARD_TUNING), MEDIAN_HAND)
    if isinstance(result, Tab):
        assert check_playability(result, MEDIAN_HAND).verdict != "RED"
    else:
        assert isinstance(result, Infeasible)


def test_pure_solver_deterministic() -> None:
    ir = generate_leadsheet(GenConfig(seed=2, bars=4))
    a = baseline_pure_solver(ir, ArrangeGoal(), MEDIAN_HAND)
    b = baseline_pure_solver(ir, ArrangeGoal(), MEDIAN_HAND)
    assert a == b


def test_pure_solver_runs_once_and_ten_slots_are_same_outcome_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def counted_solve(
        notes: Sequence[Note],
        tuning: tuple[int, ...],
        capo: int,
        profile: Profile,
        *,
        tempo_bpm: float = 90.0,
        beats_per_bar: int = 4,
        beam: int = 16,
    ) -> Tab | Infeasible:
        nonlocal calls
        calls += 1
        return solve_fingering(
            notes,
            tuning,
            capo,
            profile,
            tempo_bpm=tempo_bpm,
            beats_per_bar=beats_per_bar,
            beam=beam,
        )

    monkeypatch.setattr(baseline_module, "solve_fingering", counted_solve)
    outcome = run_pure_solver_baseline(_IR, ArrangeGoal(), MEDIAN_HAND)
    slots = repeat_pure_solver_outcome(outcome)

    assert calls == outcome.solver_calls == 1
    assert outcome.llm_calls == 0
    assert outcome.status in {PureSolverStatus.TAB, PureSolverStatus.INFEASIBLE}
    assert len(slots) == PURE_SOLVER_BASELINE_SLOTS == 10
    assert all(slot is outcome for slot in slots)


def test_b3_b4_are_exact_unavailable_records_not_fabricated_rows() -> None:
    assert OPTIONAL_BASELINE_AVAILABILITY == (B3_AVAILABILITY, B4_AVAILABILITY)
    assert [row.baseline_id for row in OPTIONAL_BASELINE_AVAILABILITY] == [
        BaselineId.B3,
        BaselineId.B4,
    ]
    assert all(
        row.status is BaselineAvailabilityStatus.UNAVAILABLE
        and row.reason == LICENSE_AUDITED_REPRODUCIBLE_ADAPTER_ABSENT
        for row in OPTIONAL_BASELINE_AVAILABILITY
    )
    assert all(
        not hasattr(row, "tab") and not hasattr(row, "llm_calls")
        for row in OPTIONAL_BASELINE_AVAILABILITY
    )
