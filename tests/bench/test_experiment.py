from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from fractions import Fraction as F

import pytest

import fretsure.agent.tools as agent_tools_module
import fretsure.bench.experiment as experiment_module
import fretsure.solver.api as solver_module
from fretsure.agent.arranger import (
    ArrangeGoal,
    ProposalStatus,
    arrangement_source_context_sha256,
)
from fretsure.agent.critic import CriticStatus
from fretsure.bench.baselines import OPTIONAL_BASELINE_AVAILABILITY
from fretsure.bench.corpus import (
    CorpusItem,
    CorpusProvenance,
    EvidenceAvailability,
    LicenseProvenance,
    ProceduralCorpusConfig,
    build_primary_procedural_corpus,
)
from fretsure.bench.experiment import (
    EXPERIMENT_N_SAMPLES,
    EXPERIMENT_TEMPERATURE,
    RELIABILITY_K_VALUES,
    SEARCH_K_VALUES,
    BudgetLimitDimension,
    BudgetMatchStatus,
    CollectionArm,
    CompletedExperimentUnit,
    CompletedPureSolver,
    ExperimentCollection,
    ExperimentInputError,
    ExperimentPlan,
    ExperimentResumeState,
    ObservationLedger,
    derive_shared_views,
    item_pair_id,
    match_budget_prefix,
    preflight_experiment,
    run_experiment,
    sample_pair_id,
)
from fretsure.bench.observe import CallStage, InMemoryObservationSink
from fretsure.ir import Meta, MusicIR, Note
from fretsure.llm.client import FAKE_LLM_MODEL_ID, FakeLLM, ProxyCallMetadata
from fretsure.metrics.fidelity import FaithfulnessGate
from fretsure.oracle.core import OracleResult
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.solver.api import Infeasible
from fretsure.tab import Tab

_PROPOSAL = '{"notes":[{"onset":"0","duration":"1","pitch":64,"voice":"melody"}]}'
_CRITIC = '{"overall":0.8}'
_RAW_TAB = (
    '{"tuning":[40,45,50,55,59,64],"capo":0,"notes":['
    '{"onset":"0","duration":"1","string":5,"fret":0,'
    '"left_finger":0,"right_finger":"i"}]}'
)
_CANONICAL_RAW_TAB = _RAW_TAB.replace('"0"', '"0/1"').replace('"1"', '"1/1"')
_NO_TAB_PROPOSAL = '{"notes":[{"onset":"0","duration":"1","pitch":127,"voice":"melody"}]}'


def _melody_item(
    *,
    position: int = 1,
    meter: tuple[int, int] = (4, 4),
) -> CorpusItem:
    ir = MusicIR(
        (Note(F(0), F(1), 64, "melody"),),
        (),
        Meta("C", meter, 90.0, "fixture", "fixture", "CC0-1.0"),
    )
    digest = "1" * 64
    return CorpusItem(
        ir=ir,
        layer="public_midi",
        genre="fixture",
        difficulty=0,
        item_id="midi-item",
        family_id="midi-family",
        cluster_id="midi-cluster",
        position=position,
        provenance=CorpusProvenance(
            source_format="midi",
            source_sha256=digest,
            root_sha256=digest,
            router_version="score-input@0.1.0",
            importer_version="midi@0.1.0",
            container_version=None,
            source_url="https://example.invalid/fixture.mid",
            producer=None,
            retrieval_date="2026-07-17",
            license=LicenseProvenance(
                expression="CC0-1.0",
                status="verified",
                redistribution=True,
                derivatives=True,
                provider_submission=True,
            ),
            split="test",
            role_map=(("track:0", "melody"),),
            normalization=("strict-midi-import",),
            generator=None,
        ),
        evidence=EvidenceAvailability(melody=True, bass=False, harmony=False),
        synthetic_complexity="unrated",
        polyphony="monophonic",
        canary="midi-canary",
    )


def _two_items() -> tuple[CorpusItem, ...]:
    procedural = build_primary_procedural_corpus(ProceduralCorpusConfig(family_count=1, bars=1))
    return (*procedural, _melody_item())


class _MetadataFake(FakeLLM):
    @property
    def last_call_metadata(self) -> ProxyCallMetadata | None:
        if not self.calls:
            return None
        return ProxyCallMetadata(
            status="succeeded",
            attempts=1,
            returned_model_id=FAKE_LLM_MODEL_ID,
            response_id_sha256=None,
            input_tokens=3,
            output_tokens=4,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
        )


class _RestoredDurableSink(InMemoryObservationSink):
    pass


class _ClosableClient:
    def __init__(self, model_id: str) -> None:
        self._model_id = model_id
        self.closes = 0

    @property
    def model_id(self) -> str:
        return self._model_id

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        del system, user, max_tokens, temperature
        raise AssertionError("construction failure must happen before a call")

    def close(self) -> None:
        self.closes += 1


@pytest.fixture(scope="module")
def collected() -> tuple[ExperimentCollection, _MetadataFake, _MetadataFake]:
    plan = preflight_experiment(_two_items(), run_id="shared-pool", schedule_seed=17)
    agent = _MetadataFake([reply for _index in range(20) for reply in (_PROPOSAL, _CRITIC)])
    raw = _MetadataFake([_RAW_TAB] * 20)
    collection = run_experiment(
        plan,
        ArrangeGoal(),
        agent,
        raw,
        MEDIAN_HAND,
    )
    return collection, agent, raw


_ResumeSource = tuple[
    ExperimentPlan,
    ExperimentCollection,
    InMemoryObservationSink,
    tuple[CompletedExperimentUnit, ...],
    tuple[int, ...],
    tuple[tuple[str, str], ...],
]


def _clone_closed_sink(
    source: InMemoryObservationSink | ObservationLedger,
    call_count: int,
) -> InMemoryObservationSink:
    clone = _RestoredDurableSink()
    for intent, result in zip(
        source.intents[:call_count],
        source.results[:call_count],
        strict=True,
    ):
        clone.write_intent(intent)
        attempts = sorted(
            (
                value
                for value in source.attempt_intents
                if value.call_index == intent.call_index
            ),
            key=lambda value: value.attempt_index,
        )
        attempt_results = sorted(
            (
                value
                for value in source.attempt_results
                if value.call_index == intent.call_index
            ),
            key=lambda value: value.attempt_index,
        )
        for attempt, attempt_result in zip(attempts, attempt_results, strict=True):
            clone.write_attempt_intent(attempt)
            clone.write_attempt_result(attempt_result)
        clone.write_result(result)
    return clone


def _completed_pure_controls(
    collection: ExperimentCollection,
) -> tuple[CompletedPureSolver, ...]:
    return tuple(
        CompletedPureSolver(
            item.item.item_id,
            arrangement_source_context_sha256(item.item.ir),
            item.pure_solver,
        )
        for item in collection.items
    )


def _completed_collection_units(
    collection: ExperimentCollection,
) -> tuple[CompletedExperimentUnit, ...]:
    completed: list[CompletedExperimentUnit] = []
    for unit in collection.plan.collection_schedule:
        item = collection.items[unit.item_position]
        source_sha256 = arrangement_source_context_sha256(item.item.ir)
        if unit.arm is CollectionArm.AGENT:
            trajectory = next(
                value for value in item.trajectories if value.index == unit.candidate_index
            )
            completed.append(
                CompletedExperimentUnit(
                    unit,
                    source_sha256,
                    trajectory=trajectory,
                )
            )
        else:
            raw_outcome = next(
                value for value in item.raw_outcomes if value.sample_index == unit.candidate_index
            )
            completed.append(
                CompletedExperimentUnit(
                    unit,
                    source_sha256,
                    raw_outcome=raw_outcome,
                )
            )
    return tuple(completed)


def _clone_sink_in_call_order(
    source: InMemoryObservationSink | ObservationLedger,
    original_call_indices: tuple[int, ...],
) -> tuple[InMemoryObservationSink, dict[int, int]]:
    clone = _RestoredDurableSink()
    new_index_by_old: dict[int, int] = {}
    result_by_index = {value.call_index: value for value in source.results}
    for new_index, old_index in enumerate(original_call_indices):
        new_index_by_old[old_index] = new_index
        old_intent = source.intents[old_index]
        logical_call_id = f"call:{new_index}"
        clone.write_intent(
            replace(
                old_intent,
                logical_call_id=logical_call_id,
                call_index=new_index,
            )
        )
        attempts = sorted(
            (
                value
                for value in source.attempt_intents
                if value.call_index == old_index
            ),
            key=lambda value: value.attempt_index,
        )
        attempt_results = sorted(
            (
                value
                for value in source.attempt_results
                if value.call_index == old_index
            ),
            key=lambda value: value.attempt_index,
        )
        for attempt, attempt_result in zip(attempts, attempt_results, strict=True):
            attempt_id = f"attempt:{new_index}:{attempt.attempt_index}"
            clone.write_attempt_intent(
                replace(
                    attempt,
                    logical_call_id=logical_call_id,
                    call_index=new_index,
                    attempt_id=attempt_id,
                )
            )
            clone.write_attempt_result(
                replace(
                    attempt_result,
                    logical_call_id=logical_call_id,
                    call_index=new_index,
                    attempt_id=attempt_id,
                )
            )
        clone.write_result(
            replace(
                result_by_index[old_index],
                logical_call_id=logical_call_id,
                call_index=new_index,
            )
        )
    return clone, new_index_by_old


@pytest.fixture(scope="module")
def resume_source() -> _ResumeSource:
    plan = preflight_experiment(
        (_melody_item(position=0),),
        run_id="resume-source",
        schedule_seed=23,
    )
    sink = InMemoryObservationSink()
    completed: list[CompletedExperimentUnit] = []
    call_boundaries: list[int] = []
    callbacks: list[tuple[str, str]] = []

    def pure_complete(item: CorpusItem, _outcome: object) -> None:
        callbacks.append(("pure", item.item_id))

    def unit_complete(value: CompletedExperimentUnit) -> None:
        completed.append(value)
        call_boundaries.append(len(sink.intents))
        callbacks.append(("unit", value.unit.item_id))

    collection = run_experiment(
        plan,
        ArrangeGoal(),
        FakeLLM([reply for _index in range(10) for reply in (_PROPOSAL, _CRITIC)]),
        FakeLLM([_CANONICAL_RAW_TAB] * 10),
        MEDIAN_HAND,
        observation_sink=sink,
        observation_clock_ns=lambda: 7,
        on_pure_solver_complete=pure_complete,
        on_unit_complete=unit_complete,
    )
    return (
        plan,
        collection,
        sink,
        tuple(completed),
        tuple(call_boundaries),
        tuple(callbacks),
    )


def test_preflight_freezes_deterministic_permuted_interleaving() -> None:
    items = _two_items()
    first = preflight_experiment(items, run_id="schedule-a", schedule_seed=41)
    repeated = preflight_experiment(items, run_id="schedule-a", schedule_seed=41)
    other_seed = preflight_experiment(items, run_id="schedule-a", schedule_seed=42)

    assert first == repeated
    assert first.item_schedules != other_seed.item_schedules
    assert first.n_samples == EXPERIMENT_N_SAMPLES == 10
    assert first.temperature == EXPERIMENT_TEMPERATURE == 0.8
    assert first.search_k == SEARCH_K_VALUES == (1, 2, 4, 8)
    assert first.reliability_k == RELIABILITY_K_VALUES == tuple(range(1, 11))
    assert all(
        set(schedule.candidate_permutation) == set(range(10)) for schedule in first.item_schedules
    )
    for round_index in range(10):
        units = [unit for unit in first.collection_schedule if unit.round_index == round_index]
        assert len(units) == 2 * len(items)
        assert {(unit.item_id, unit.arm) for unit in units} == {
            (item.item_id, arm)
            for item in first.items
            for arm in (CollectionArm.AGENT, CollectionArm.RAW)
        }

    with pytest.raises(ExperimentInputError, match="deterministic interleaving"):
        replace(first, collection_schedule=tuple(reversed(first.collection_schedule)))


def test_public_pair_helpers_preserve_the_frozen_identity_algorithm() -> None:
    assert sample_pair_id("midi-item", 3) == "pair:sample:cd69639d3c460869ef667551"
    assert item_pair_id("selection-4", "midi-item") == (
        "pair:selection-4:db4842e91251c94d93c44cc6"
    )
    assert sample_pair_id("midi-item", 3) != sample_pair_id("midi-item", 4)

    with pytest.raises(ExperimentInputError, match="candidate_index"):
        sample_pair_id("midi-item", True)


def test_formal_preflight_rejects_legacy_corpus_shape() -> None:
    legacy = CorpusItem(
        MusicIR(
            (Note(F(0), F(1), 64, "melody"),),
            (),
            Meta("C", (4, 4), 90.0, "legacy", "legacy", "PD"),
        ),
        "procedural",
        "generated",
        1,
        "legacy-item",
    )

    with pytest.raises(ExperimentInputError, match="corpus is invalid"):
        preflight_experiment((legacy,), run_id="legacy", schedule_seed=1)


def test_matched_budget_uses_calls_and_tokens_and_exposes_all_statuses() -> None:
    plan = preflight_experiment((_melody_item(position=0),), run_id="budget", schedule_seed=1)
    budget = plan.matched_budgets[0]

    assert budget.target_calls == 9
    assert budget.target_tokens == 2_048 + 8 * 1_024
    for control in (budget.no_repair, budget.raw):
        assert control.status is BudgetMatchStatus.EXACT
        assert control.limiting_dimension is BudgetLimitDimension.TOKENS
        assert control.call_quotient == 9
        assert control.token_quotient == control.prefix_samples == 5
        assert control.remaining_calls == 4
        assert control.remaining_tokens == 0

    censored = match_budget_prefix(
        20,
        20_000,
        unit_calls=1,
        unit_tokens=1_000,
    )
    no_fit = match_budget_prefix(0, 1_000, unit_calls=1, unit_tokens=2_000)
    assert censored.status is BudgetMatchStatus.CENSORED
    assert censored.limiting_dimension is BudgetLimitDimension.SAMPLE_CAP
    assert no_fit.status is BudgetMatchStatus.NO_FIT
    assert no_fit.prefix_samples == 0


def test_collection_follows_schedule_and_uses_one_pool_and_one_pure_solve(
    collected: tuple[ExperimentCollection, _MetadataFake, _MetadataFake],
) -> None:
    collection, agent, raw = collected

    assert len(agent.calls) == 2 * 10 * len(collection.items)
    assert len(raw.calls) == 10 * len(collection.items)
    assert all(len(item.trajectories) == len(item.raw_outcomes) == 10 for item in collection.items)
    assert all(item.pure_solver.solver_calls == 1 for item in collection.items)
    assert all(item.pure_solver.llm_calls == 0 for item in collection.items)
    assert len(collection.observations.intents) == len(agent.calls) + len(raw.calls)
    assert all(
        intent.sample_index == intent.candidate_index for intent in collection.observations.intents
    )

    first_calls = [
        intent
        for intent in collection.observations.intents
        if intent.stage in {CallStage.PROPOSAL, CallStage.RAW}
    ]
    assert [
        (
            intent.item_id,
            CollectionArm.AGENT if intent.stage is CallStage.PROPOSAL else CollectionArm.RAW,
            intent.candidate_index,
        )
        for intent in first_calls
    ] == [
        (unit.item_id, unit.arm, unit.candidate_index)
        for unit in collection.plan.collection_schedule
    ]

    for item in collection.items:
        for candidate_index in range(10):
            pair_ids = {
                intent.pair_id
                for intent in collection.observations.intents
                if intent.item_id == item.item.item_id
                and intent.candidate_index == candidate_index
                and intent.stage in {CallStage.PROPOSAL, CallStage.RAW}
            }
            assert len(pair_ids) == 1
        for stage in (CallStage.PROPOSAL, CallStage.RAW):
            assert (
                len(
                    {
                        intent.request_sha256
                        for intent in collection.observations.intents
                        if intent.item_id == item.item.item_id and intent.stage is stage
                    }
                )
                == 1
            )


def test_shared_derivation_makes_no_calls_and_keeps_evidence_strata_separate(
    collected: tuple[ExperimentCollection, _MetadataFake, _MetadataFake],
) -> None:
    collection, agent, raw = collected
    before = (len(agent.calls), len(raw.calls))

    first = derive_shared_views(collection)
    second = derive_shared_views(collection)

    assert first == second
    assert (len(agent.calls), len(raw.calls)) == before
    assert [item.evidence_signature for item in first.items] == [
        "melody+bass+harmony",
        "melody",
    ]
    full, melody = first.items
    assert (melody.position, melody.layer, melody.genre) == (1, "public_midi", "fixture")
    assert (melody.synthetic_complexity, melody.polyphony) == ("unrated", "monophonic")
    assert full.repair_pairs[0].terminal.evaluated_dimensions == (
        "melody",
        "bass_root",
        "harmony",
    )
    assert melody.repair_pairs[0].terminal.evaluated_dimensions == ("melody",)
    assert melody.repair_pairs[0].terminal.bass_root is None
    assert melody.repair_pairs[0].terminal.harmony is None
    assert tuple(point.k for point in melody.reliability) == tuple(range(1, 11))
    assert tuple(pair.k for pair in melody.search_and_critic) == (1, 2, 4, 8)
    assert melody.causal_best_1_vs_4.best_of_1.k == 1
    assert melody.causal_best_1_vs_4.best_of_4.k == 4
    assert all(
        pair.with_critic.critic_status is CriticStatus.LLM_SUCCESS
        for pair in melody.search_and_critic
    )


def test_costs_use_prefix_only_preserve_partial_usage_and_equalize_causal_pool(
    collected: tuple[ExperimentCollection, _MetadataFake, _MetadataFake],
) -> None:
    collection, _agent, _raw = collected
    view = derive_shared_views(collection).items[1]
    prefix_one = view.costs.agent_prefixes[0]

    assert prefix_one.k == 1
    assert prefix_one.with_critic.logical_calls == 2
    assert prefix_one.without_critic.logical_calls == 1
    assert prefix_one.with_critic.provider_attempts == 2
    assert prefix_one.with_critic.logical_requested_output_tokens == 2_048 + 512
    assert prefix_one.with_critic.attempt_reserved_output_tokens == 2_048 + 512
    assert prefix_one.with_critic.provider_usage.input_tokens == 6
    assert prefix_one.with_critic.provider_usage.output_tokens == 8
    assert prefix_one.with_critic.provider_usage.cache_creation_input_tokens is None
    assert prefix_one.with_critic.provider_usage.cache_read_input_tokens is None

    no_repair = dict(view.costs.no_repair_prefixes)[5]
    raw = dict(view.costs.raw_prefixes)[5]
    assert no_repair.logical_calls == raw.logical_calls == 5
    assert no_repair.logical_requested_output_tokens == 5 * 2_048
    assert raw.logical_requested_output_tokens == 5 * 2_048
    assert view.costs.causal_best_1_vs_4.best_of_1 == (view.costs.causal_best_1_vs_4.best_of_4)
    assert view.costs.causal_best_1_vs_4.best_of_1.logical_calls == 8


def test_valid_fallback_scores_primary_but_not_llm_sensitivity_and_no_tab_is_itt() -> None:
    item = _melody_item(position=0)
    plan = preflight_experiment((item,), run_id="fallback-and-no-tab", schedule_seed=1)
    agent_script = (
        ["not-json", _CRITIC]
        + [_NO_TAB_PROPOSAL]
        + ["bad-edit"] * 8
        + [reply for _index in range(8) for reply in (_PROPOSAL, _CRITIC)]
    )
    agent = FakeLLM(agent_script)
    raw = FakeLLM([_RAW_TAB] * 10)

    collection = run_experiment(
        plan,
        ArrangeGoal(),
        agent,
        raw,
        MEDIAN_HAND,
    )
    view = derive_shared_views(collection).items[0]
    fallback = next(pair for pair in view.repair_pairs if pair.terminal.fallback_assisted)
    no_tab = next(pair for pair in view.repair_pairs if not pair.terminal.tab_available)

    assert fallback.terminal.joint_success
    assert not fallback.terminal.llm_generated
    assert not fallback.terminal.llm_success
    assert fallback.terminal.melody_f1 == 1.0
    assert fallback.terminal.bass_root is None and fallback.terminal.harmony is None
    assert not no_tab.terminal.green
    assert not no_tab.terminal.joint_success
    assert no_tab.terminal.melody_f1 is None
    assert no_tab.terminal.bass_root is None and no_tab.terminal.harmony is None

    k1 = view.reliability[0]
    assert k1.terminal_joint.pass_at_k == 0.9
    assert k1.terminal_llm_success.pass_at_k == 0.8
    assert view.costs.agent_prefixes[0].with_critic.provider_usage.input_tokens is None
    assert any(
        trajectory.proposal.status is ProposalStatus.PARSE_VALIDATION_FALLBACK
        for trajectory in collection.items[0].trajectories
    )


def test_raw_and_pure_shared_view_scoring_uses_the_source_meter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = preflight_experiment(
        (_melody_item(position=0, meter=(3, 4)),),
        run_id="three-four-scoring",
        schedule_seed=5,
    )
    collection = run_experiment(
        plan,
        ArrangeGoal(),
        FakeLLM([reply for _index in range(10) for reply in (_PROPOSAL, _CRITIC)]),
        FakeLLM([_CANONICAL_RAW_TAB] * 10),
        MEDIAN_HAND,
    )
    real_check = experiment_module.check_playability
    observed_meters: list[int] = []

    def meter_recording_check(
        tab: object,
        profile: object,
        *,
        tempo_bpm: float,
        beats_per_bar: int = 4,
    ) -> object:
        observed_meters.append(beats_per_bar)
        return real_check(  # type: ignore[arg-type]
            tab,
            profile,
            tempo_bpm=tempo_bpm,
            beats_per_bar=beats_per_bar,
        )

    monkeypatch.setattr(experiment_module, "check_playability", meter_recording_check)
    view = derive_shared_views(collection).items[0]

    assert observed_meters == [3] * 11
    assert all(score.green for score in view.raw_scores)
    assert view.pure_solver_score.green


@pytest.mark.parametrize("meter", [(3, 4), (6, 8)])
def test_agent_collection_threads_source_meter_through_solve_and_both_checks(
    meter: tuple[int, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    beats = meter[0]
    solve_meters: list[int] = []
    tool_check_meters: list[int] = []
    solver_check_meters: list[int] = []
    real_solve = agent_tools_module.solve_fingering
    real_tool_check = agent_tools_module.check_playability
    real_solver_check = solver_module.check_playability

    def recording_solve(
        notes: Sequence[Note],
        tuning: tuple[int, ...],
        capo: int,
        profile: Profile,
        *,
        tempo_bpm: float = 90.0,
        beats_per_bar: int = 4,
        beam: int = 16,
    ) -> Tab | Infeasible:
        solve_meters.append(beats_per_bar)
        return real_solve(
            notes,
            tuning,
            capo,
            profile,
            tempo_bpm=tempo_bpm,
            beats_per_bar=beats_per_bar,
            beam=beam,
        )

    def recording_tool_check(
        tab: Tab,
        profile: Profile,
        *,
        tempo_bpm: float = 90.0,
        beats_per_bar: int = 4,
    ) -> OracleResult:
        tool_check_meters.append(beats_per_bar)
        return real_tool_check(
            tab,
            profile,
            tempo_bpm=tempo_bpm,
            beats_per_bar=beats_per_bar,
        )

    def recording_solver_check(
        tab: Tab,
        profile: Profile,
        *,
        tempo_bpm: float = 90.0,
        beats_per_bar: int = 4,
    ) -> OracleResult:
        solver_check_meters.append(beats_per_bar)
        return real_solver_check(
            tab,
            profile,
            tempo_bpm=tempo_bpm,
            beats_per_bar=beats_per_bar,
        )

    monkeypatch.setattr(agent_tools_module, "solve_fingering", recording_solve)
    monkeypatch.setattr(agent_tools_module, "check_playability", recording_tool_check)
    monkeypatch.setattr(solver_module, "check_playability", recording_solver_check)
    plan = preflight_experiment(
        (_melody_item(position=0, meter=meter),),
        run_id=f"meter-{beats}",
        schedule_seed=5,
    )
    run_experiment(
        plan,
        ArrangeGoal(),
        FakeLLM([reply for _index in range(10) for reply in (_PROPOSAL, _CRITIC)]),
        FakeLLM([_CANONICAL_RAW_TAB] * 10),
        MEDIAN_HAND,
    )

    assert solve_meters == [beats] * 10
    assert tool_check_meters == [beats] * 10
    assert solver_check_meters and set(solver_check_meters) == {beats}


def test_missing_observation_coverage_fails_closed(
    collected: tuple[ExperimentCollection, _MetadataFake, _MetadataFake],
) -> None:
    collection, _agent, _raw = collected
    observations = collection.observations
    incomplete = ObservationLedger(
        observations.intents[:-1],
        observations.results[:-1],
        observations.attempt_intents[:-1],
        observations.attempt_results[:-1],
    )

    with pytest.raises(ExperimentInputError, match="coverage|observation_key"):
        replace(collection, observations=incomplete)


@pytest.mark.parametrize("identity", ["family_id", "cluster_id"])
def test_observations_bind_planned_family_and_cluster(
    collected: tuple[ExperimentCollection, _MetadataFake, _MetadataFake],
    identity: str,
) -> None:
    collection, _agent, _raw = collected
    observations = collection.observations
    first = observations.intents[0]
    changed = (
        replace(first, family_id="different-family")
        if identity == "family_id"
        else replace(first, cluster_id="different-cluster")
    )
    ledger = ObservationLedger(
        (changed, *observations.intents[1:]),
        observations.results,
        observations.attempt_intents,
        observations.attempt_results,
    )

    with pytest.raises(ExperimentInputError, match="family_cluster"):
        replace(collection, observations=ledger)


def test_collection_binds_exact_item_and_raw_observation_run(
    collected: tuple[ExperimentCollection, _MetadataFake, _MetadataFake],
) -> None:
    collection, _agent, _raw = collected
    first = collection.items[0]
    drifted_item = replace(first.item, genre="different-source-label")

    with pytest.raises(ExperimentInputError, match="exact planned source"):
        replace(collection, items=(replace(first, item=drifted_item), *collection.items[1:]))

    outcome = first.raw_outcomes[0]
    wrong_run = replace(
        outcome,
        observation_key=replace(outcome.observation_key, run_id="other-run"),
    )
    changed_raw = (wrong_run, *first.raw_outcomes[1:])
    with pytest.raises(ExperimentInputError, match="run_id"):
        replace(collection, items=(replace(first, raw_outcomes=changed_raw), *collection.items[1:]))


def test_terminal_faithfulness_is_deterministically_rescored(
    collected: tuple[ExperimentCollection, _MetadataFake, _MetadataFake],
) -> None:
    collection, _agent, _raw = collected
    melody_item = collection.items[1]
    drifted = replace(
        melody_item.trajectories[0],
        faithfulness=FaithfulnessGate(
            0.0,
            None,
            None,
            False,
            ("melody",),
            ("bass_root", "harmony"),
        ),
    )
    drifted_item = replace(
        melody_item,
        trajectories=(drifted, *melody_item.trajectories[1:]),
    )
    drifted_collection = replace(
        collection,
        items=(collection.items[0], drifted_item),
    )

    with pytest.raises(ExperimentInputError, match="terminal rescore"):
        derive_shared_views(drifted_collection)


def test_optional_baselines_are_exact_unavailable_records(
    collected: tuple[ExperimentCollection, _MetadataFake, _MetadataFake],
) -> None:
    collection, _agent, _raw = collected
    assert collection.external_baselines == OPTIONAL_BASELINE_AVAILABILITY


def test_run_rejects_one_shared_client_instance_before_collection() -> None:
    plan = preflight_experiment(
        (_melody_item(position=0),),
        run_id="same-client",
        schedule_seed=1,
    )
    client = FakeLLM([])

    with pytest.raises(ExperimentInputError, match="distinct owned client"):
        run_experiment(plan, ArrangeGoal(), client, client, MEDIAN_HAND)
    assert client.calls == []


def test_resume_from_complete_prefix_matches_one_shot_and_continues_call_indices(
    resume_source: _ResumeSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, expected, source_sink, completed, boundaries, _callbacks = resume_source
    prefix_units = 7
    sink = _clone_closed_sink(source_sink, boundaries[prefix_units - 1])
    state = ExperimentResumeState(
        _completed_pure_controls(expected),
        completed[:prefix_units],
    )
    new_units: list[CompletedExperimentUnit] = []
    pure_callbacks: list[str] = []

    def unexpected_pure(*_args: object) -> object:
        raise AssertionError("a restored pure-solver control must not run again")

    monkeypatch.setattr(experiment_module, "run_pure_solver_baseline", unexpected_pure)
    agent = FakeLLM([reply for _index in range(10) for reply in (_PROPOSAL, _CRITIC)])
    raw = FakeLLM([_CANONICAL_RAW_TAB] * 10)
    prior_call_count = len(sink.intents)
    resumed = run_experiment(
        plan,
        ArrangeGoal(),
        agent,
        raw,
        MEDIAN_HAND,
        observation_sink=sink,
        observation_clock_ns=lambda: 7,
        resume_state=state,
        on_pure_solver_complete=lambda item, outcome: pure_callbacks.append(item.item_id),
        on_unit_complete=new_units.append,
    )

    assert resumed == expected
    assert pure_callbacks == []
    assert new_units == list(completed[prefix_units:])
    assert tuple(intent.call_index for intent in sink.intents) == tuple(range(len(sink.intents)))
    assert sink.intents[prior_call_count].call_index == prior_call_count
    remaining = completed[prefix_units:]
    assert len(agent.calls) == 2 * sum(
        value.unit.arm is CollectionArm.AGENT for value in remaining
    )
    assert len(raw.calls) == sum(value.unit.arm is CollectionArm.RAW for value in remaining)


def test_resume_callbacks_follow_pure_then_exact_schedule_order(
    resume_source: _ResumeSource,
) -> None:
    plan, _collection, _sink, completed, _boundaries, callbacks = resume_source

    assert callbacks[0] == ("pure", plan.items[0].item_id)
    assert callbacks[1:] == tuple(("unit", unit.item_id) for unit in plan.collection_schedule)
    assert tuple(value.unit for value in completed) == plan.collection_schedule
    assert all(
        (value.trajectory is not None) is (value.unit.arm is CollectionArm.AGENT)
        and (value.raw_outcome is not None) is (value.unit.arm is CollectionArm.RAW)
        for value in completed
    )

    agent_unit = next(value for value in completed if value.unit.arm is CollectionArm.AGENT)
    raw_unit = next(value for value in completed if value.unit.arm is CollectionArm.RAW)
    with pytest.raises(ExperimentInputError, match="agent unit"):
        CompletedExperimentUnit(
            agent_unit.unit,
            agent_unit.source_context_sha256,
            raw_outcome=raw_unit.raw_outcome,
        )
    with pytest.raises(ExperimentInputError, match="raw unit"):
        CompletedExperimentUnit(
            raw_unit.unit,
            raw_unit.source_context_sha256,
            trajectory=agent_unit.trajectory,
        )


def test_resume_rejects_swapped_pure_item_source_bindings_before_calls(
    collected: tuple[ExperimentCollection, _MetadataFake, _MetadataFake],
) -> None:
    collection, _agent, _raw = collected
    pure = _completed_pure_controls(collection)
    swapped = (
        CompletedPureSolver(
            pure[1].item_id,
            pure[1].source_context_sha256,
            pure[1].outcome,
        ),
        CompletedPureSolver(
            pure[0].item_id,
            pure[0].source_context_sha256,
            pure[0].outcome,
        ),
    )
    agent = FakeLLM([])
    raw = FakeLLM([])

    with pytest.raises(ExperimentInputError, match="bind the planned item and source"):
        run_experiment(
            collection.plan,
            collection.goal,
            agent,
            raw,
            collection.profile,
            observation_sink=InMemoryObservationSink(),
            resume_state=ExperimentResumeState(swapped, ()),
        )

    assert agent.calls == raw.calls == []


@pytest.mark.parametrize("arm", [CollectionArm.AGENT, CollectionArm.RAW])
def test_resume_rejects_swapped_unit_source_bindings_before_calls(
    arm: CollectionArm,
    collected: tuple[ExperimentCollection, _MetadataFake, _MetadataFake],
) -> None:
    collection, _agent, _raw = collected
    completed = list(_completed_collection_units(collection))
    pair: tuple[int, int] | None = None
    for left_index, left in enumerate(completed):
        if left.unit.arm is not arm:
            continue
        for right_index in range(left_index + 1, len(completed)):
            right = completed[right_index]
            if (
                right.unit.arm is arm
                and right.unit.item_id != left.unit.item_id
                and right.unit.candidate_index == left.unit.candidate_index
            ):
                pair = (left_index, right_index)
                break
        if pair is not None:
            break
    assert pair is not None
    left_index, right_index = pair
    left = completed[left_index]
    right = completed[right_index]
    completed[left_index] = CompletedExperimentUnit(
        left.unit,
        right.source_context_sha256,
        trajectory=right.trajectory,
        raw_outcome=right.raw_outcome,
    )
    completed[right_index] = CompletedExperimentUnit(
        right.unit,
        left.source_context_sha256,
        trajectory=left.trajectory,
        raw_outcome=left.raw_outcome,
    )
    sink = _clone_closed_sink(
        collection.observations,
        len(collection.observations.intents),
    )
    agent = FakeLLM([])
    raw = FakeLLM([])

    with pytest.raises(ExperimentInputError, match="bind the planned item source"):
        run_experiment(
            collection.plan,
            collection.goal,
            agent,
            raw,
            collection.profile,
            observation_sink=sink,
            resume_state=ExperimentResumeState(
                _completed_pure_controls(collection),
                tuple(completed),
            ),
        )

    assert agent.calls == raw.calls == []


def test_resume_pure_mask_is_callback_prefix_and_precedes_collection(
    collected: tuple[ExperimentCollection, _MetadataFake, _MetadataFake],
) -> None:
    collection, _agent, _raw = collected
    pure = _completed_pure_controls(collection)
    completed = _completed_collection_units(collection)

    with pytest.raises(ExperimentInputError, match="callback-order prefix"):
        run_experiment(
            collection.plan,
            collection.goal,
            FakeLLM([]),
            FakeLLM([]),
            collection.profile,
            observation_sink=InMemoryObservationSink(),
            resume_state=ExperimentResumeState((None, pure[1]), ()),
        )

    with pytest.raises(ExperimentInputError, match="before the collection prefix"):
        run_experiment(
            collection.plan,
            collection.goal,
            FakeLLM([]),
            FakeLLM([]),
            collection.profile,
            observation_sink=InMemoryObservationSink(),
            resume_state=ExperimentResumeState((pure[0], None), completed[:1]),
        )


def test_resume_accepts_a_partial_pure_callback_prefix(
    collected: tuple[ExperimentCollection, _MetadataFake, _MetadataFake],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection, _agent, _raw = collected
    pure = _completed_pure_controls(collection)
    rerun_items: list[str] = []
    agent = _ClosableClient("resume-pure-prefix")
    raw = _ClosableClient("resume-pure-prefix")

    class CallbackStopped(RuntimeError):
        pass

    def restored_missing(ir: MusicIR, *_args: object) -> object:
        assert ir == collection.items[1].item.ir
        rerun_items.append(collection.items[1].item.item_id)
        return collection.items[1].pure_solver

    monkeypatch.setattr(experiment_module, "run_pure_solver_baseline", restored_missing)

    with pytest.raises(CallbackStopped):
        run_experiment(
            collection.plan,
            collection.goal,
            agent,
            raw,
            collection.profile,
            observation_sink=InMemoryObservationSink(),
            resume_state=ExperimentResumeState((pure[0], None), ()),
            on_pure_solver_complete=lambda *_args: (_ for _ in ()).throw(CallbackStopped),
        )

    assert rerun_items == [collection.items[1].item.item_id]
    assert agent.closes == raw.closes == 1


def test_fully_completed_resume_makes_no_model_or_solver_calls(
    resume_source: _ResumeSource,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan, expected, source_sink, completed, boundaries, _callbacks = resume_source
    sink = _clone_closed_sink(source_sink, boundaries[-1])
    state = ExperimentResumeState(
        _completed_pure_controls(expected),
        completed,
    )
    agent = FakeLLM([])
    raw = FakeLLM([])

    def unexpected_pure(*_args: object) -> object:
        raise AssertionError("a restored pure-solver control must not run again")

    monkeypatch.setattr(experiment_module, "run_pure_solver_baseline", unexpected_pure)
    resumed = run_experiment(
        plan,
        ArrangeGoal(),
        agent,
        raw,
        MEDIAN_HAND,
        observation_sink=sink,
        observation_clock_ns=lambda: 7,
        resume_state=state,
    )

    assert resumed == expected
    assert agent.calls == raw.calls == []


def test_resume_rejects_nonprefix_or_unaccounted_observations_before_new_calls(
    resume_source: _ResumeSource,
) -> None:
    plan, expected, source_sink, completed, boundaries, _callbacks = resume_source
    pure = _completed_pure_controls(expected)

    bad_prefix = ExperimentResumeState(pure, (completed[1],))
    prefix_agent = FakeLLM([])
    prefix_raw = FakeLLM([])
    with pytest.raises(ExperimentInputError, match="continuous prefix"):
        run_experiment(
            plan,
            ArrangeGoal(),
            prefix_agent,
            prefix_raw,
            MEDIAN_HAND,
            observation_sink=InMemoryObservationSink(),
            resume_state=bad_prefix,
        )
    assert prefix_agent.calls == prefix_raw.calls == []

    extra_sink = _clone_closed_sink(source_sink, boundaries[4])
    missing_unit = ExperimentResumeState(pure, completed[:4])
    extra_agent = FakeLLM([])
    extra_raw = FakeLLM([])
    with pytest.raises(ExperimentInputError, match="no extra calls"):
        run_experiment(
            plan,
            ArrangeGoal(),
            extra_agent,
            extra_raw,
            MEDIAN_HAND,
            observation_sink=extra_sink,
            resume_state=missing_unit,
        )
    assert extra_agent.calls == extra_raw.calls == []


def test_resume_rejects_repair_or_critic_calls_moved_across_unit_boundaries(
    resume_source: _ResumeSource,
) -> None:
    plan, expected, source_sink, completed, boundaries, _callbacks = resume_source
    starts = (0, *boundaries[:-1])
    agent_index = next(
        index
        for index, value in enumerate(completed[:-1])
        if value.unit.arm is CollectionArm.AGENT
        and boundaries[index] - starts[index] >= 2
    )
    next_index = agent_index + 1
    agent_calls = tuple(range(starts[agent_index], boundaries[agent_index]))
    next_calls = tuple(range(starts[next_index], boundaries[next_index]))
    moved_call = agent_calls[-1]
    assert source_sink.intents[moved_call].stage in {CallStage.REPAIR, CallStage.CRITIC}
    order = (
        *range(starts[agent_index]),
        *agent_calls[:-1],
        *next_calls,
        moved_call,
    )
    sink, new_index_by_old = _clone_sink_in_call_order(source_sink, tuple(order))
    restored_units: list[CompletedExperimentUnit] = []
    for value in completed[: next_index + 1]:
        if value.raw_outcome is None:
            restored_units.append(value)
            continue
        old_key = value.raw_outcome.observation_key
        new_index = new_index_by_old[old_key.call_index]
        restored_units.append(
            replace(
                value,
                raw_outcome=replace(
                    value.raw_outcome,
                    observation_key=replace(
                        old_key,
                        logical_call_id=f"call:{new_index}",
                        call_index=new_index,
                    ),
                ),
            )
        )
    agent = FakeLLM([])
    raw = FakeLLM([])

    with pytest.raises(ExperimentInputError, match="contiguous ordered call slice"):
        run_experiment(
            plan,
            ArrangeGoal(),
            agent,
            raw,
            MEDIAN_HAND,
            observation_sink=sink,
            resume_state=ExperimentResumeState(
                _completed_pure_controls(expected),
                tuple(restored_units),
            ),
        )

    assert agent.calls == raw.calls == []


def test_resume_rejects_orphan_and_model_drift_without_calling_clients(
    resume_source: _ResumeSource,
) -> None:
    plan, expected, source_sink, completed, boundaries, _callbacks = resume_source
    pure = _completed_pure_controls(expected)
    orphan = InMemoryObservationSink()
    orphan.write_intent(source_sink.intents[0])
    orphan_agent = FakeLLM([])
    orphan_raw = FakeLLM([])

    with pytest.raises(ExperimentInputError, match="orphan intent"):
        run_experiment(
            plan,
            ArrangeGoal(),
            orphan_agent,
            orphan_raw,
            MEDIAN_HAND,
            observation_sink=orphan,
            resume_state=ExperimentResumeState(pure, ()),
        )
    assert orphan_agent.calls == orphan_raw.calls == []

    prefix_units = 3
    restored = _clone_closed_sink(source_sink, boundaries[prefix_units - 1])
    drifted_agent = _ClosableClient("different-model")
    drifted_raw = _ClosableClient("different-model")
    with pytest.raises(ExperimentInputError, match="different requested model"):
        run_experiment(
            plan,
            ArrangeGoal(),
            drifted_agent,
            drifted_raw,
            MEDIAN_HAND,
            observation_sink=restored,
            resume_state=ExperimentResumeState(pure, completed[:prefix_units]),
        )
    assert drifted_agent.closes == drifted_raw.closes == 1


def test_unit_callback_failure_stops_before_the_next_schedule_unit() -> None:
    plan = preflight_experiment(
        (_melody_item(position=0),),
        run_id="callback-stop",
        schedule_seed=29,
    )
    sink = InMemoryObservationSink()
    completed: list[CompletedExperimentUnit] = []

    class CallbackStopped(RuntimeError):
        pass

    def stop_after_three(value: CompletedExperimentUnit) -> None:
        completed.append(value)
        if len(completed) == 3:
            raise CallbackStopped

    with pytest.raises(CallbackStopped):
        run_experiment(
            plan,
            ArrangeGoal(),
            FakeLLM([reply for _index in range(10) for reply in (_PROPOSAL, _CRITIC)]),
            FakeLLM([_RAW_TAB] * 10),
            MEDIAN_HAND,
            observation_sink=sink,
            on_unit_complete=stop_after_three,
        )

    first_calls = tuple(
        (
            intent.item_id,
            CollectionArm.AGENT if intent.stage is CallStage.PROPOSAL else CollectionArm.RAW,
            intent.candidate_index,
        )
        for intent in sink.intents
        if intent.stage in {CallStage.PROPOSAL, CallStage.RAW}
    )
    assert tuple(value.unit for value in completed) == plan.collection_schedule[:3]
    assert first_calls == tuple(
        (unit.item_id, unit.arm, unit.candidate_index)
        for unit in plan.collection_schedule[:3]
    )


def test_pure_callback_failure_stops_before_collection_and_closes_clients() -> None:
    plan = preflight_experiment(
        (_melody_item(position=0),),
        run_id="pure-callback-stop",
        schedule_seed=31,
    )
    sink = InMemoryObservationSink()
    agent = _ClosableClient("callback-model")
    raw = _ClosableClient("callback-model")

    class CallbackStopped(RuntimeError):
        pass

    def stop(_item: CorpusItem, _outcome: object) -> None:
        raise CallbackStopped

    with pytest.raises(CallbackStopped):
        run_experiment(
            plan,
            ArrangeGoal(),
            agent,
            raw,
            MEDIAN_HAND,
            observation_sink=sink,
            on_pure_solver_complete=stop,
        )

    assert sink.intents == ()
    assert agent.closes == raw.closes == 1


def test_run_accepts_durable_sink_subclass_and_deterministic_clock() -> None:
    class DurableTestSink(InMemoryObservationSink):
        pass

    plan = preflight_experiment(
        (_melody_item(position=0),),
        run_id="durable-clock",
        schedule_seed=1,
    )
    sink = DurableTestSink()
    collection = run_experiment(
        plan,
        ArrangeGoal(),
        FakeLLM([reply for _index in range(10) for reply in (_PROPOSAL, _CRITIC)]),
        FakeLLM([_RAW_TAB] * 10),
        MEDIAN_HAND,
        observation_sink=sink,
        observation_clock_ns=lambda: 7,
    )

    assert len(collection.observations.results) == 30
    assert collection.observations.results == sink.results
    assert all(result.elapsed_microseconds == 0 for result in sink.results)


def test_wrapper_construction_failure_closes_both_owned_clients_once() -> None:
    plan = preflight_experiment(
        (_melody_item(position=0),),
        run_id="invalid-raw-model",
        schedule_seed=1,
    )
    agent = _ClosableClient("valid-agent-model")
    raw = _ClosableClient("")

    with pytest.raises(ValueError, match="delegate.model_id"):
        run_experiment(plan, ArrangeGoal(), agent, raw, MEDIAN_HAND)

    assert agent.closes == raw.closes == 1
