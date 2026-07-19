from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import replace
from fractions import Fraction as F

import pytest

import fretsure.bench.report as report_module
from fretsure.agent.arranger import ArrangeGoal, arrangement_source_context_sha256
from fretsure.bench.artifacts import (
    BenchmarkRow,
    BlobKind,
    ObservationKey,
    RowType,
    SanitizedObservations,
    blob_record_to_dict,
    build_blob_record,
    build_row,
    canonical_jsonl_bytes,
    canonical_table_sha256,
    row_to_dict,
    sanitize_observations,
)
from fretsure.bench.corpus import (
    CorpusItem,
    CorpusProvenance,
    EvidenceAvailability,
    LicenseProvenance,
    ProceduralCorpusConfig,
    build_primary_procedural_corpus,
    corpus_sha256,
)
from fretsure.bench.experiment import (
    CollectionArm,
    CompletedExperimentUnit,
    CompletedPureSolver,
    ExperimentCollection,
    ItemMatchedBudget,
    ObservationLedger,
    ScheduledUnit,
    derive_shared_views,
    preflight_experiment,
    run_experiment,
)
from fretsure.bench.observe import (
    AttemptIntent,
    AttemptResult,
    InMemoryObservationSink,
)
from fretsure.bench.report import (
    CapabilityDecision,
    ReplayMode,
    ReportInputError,
    ReportPublicationBindings,
    build_benchmark_report,
    collection_to_row_bundle,
    completed_pure_solver_to_row_bundle,
    completed_unit_to_row_bundle,
    report_to_dict,
    report_to_markdown,
    rescore_row_bundle,
    resume_state_from_rows,
)
from fretsure.ir import Meta, MusicIR, Note
from fretsure.llm.client import FAKE_LLM_MODEL_ID, FakeLLM, ProxyCallMetadata
from fretsure.oracle.profiles import LARGE_HAND, MEDIAN_HAND, SMALL_HAND

_PROPOSAL = '{"notes":[{"onset":"0","duration":"1","pitch":64,"voice":"melody"}]}'
_CRITIC = '{"overall":0.8,"voice_leading":0.7,"bass_motion":0.6,"texture":0.5}'
_RAW_TAB = (
    '{"tuning":[40,45,50,55,59,64],"capo":0,"notes":['
    '{"onset":"0","duration":"1","string":5,"fret":0,'
    '"left_finger":0,"right_finger":"i"}]}'
)


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


def _melody_item() -> CorpusItem:
    digest = "1" * 64
    return CorpusItem(
        ir=MusicIR(
            (Note(F(0), F(1), 64, "melody"),),
            (),
            Meta("C", (4, 4), 90.0, "fixture", "fixture", "CC0-1.0"),
        ),
        layer="public_midi",
        genre="fixture",
        difficulty=0,
        item_id="midi-item",
        family_id="midi-family",
        cluster_id="midi-cluster",
        position=1,
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


@pytest.fixture(scope="module")
def report_source() -> tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations]:
    procedural = build_primary_procedural_corpus(ProceduralCorpusConfig(family_count=1, bars=1))
    plan = preflight_experiment(
        (*procedural, _melody_item()),
        run_id="report-fixture",
        schedule_seed=17,
    )
    sink = InMemoryObservationSink()
    collection = run_experiment(
        plan,
        ArrangeGoal(),
        _MetadataFake([reply for _index in range(20) for reply in (_PROPOSAL, _CRITIC)]),
        _MetadataFake([_RAW_TAB] * 20),
        MEDIAN_HAND,
        observation_sink=sink,
        observation_clock_ns=lambda: 7,
    )
    _private, public = sanitize_observations(plan.run_id, sink, stub=False)
    return collection, sink, public


def _clone_sink(source: InMemoryObservationSink) -> InMemoryObservationSink:
    clone = InMemoryObservationSink()
    attempts: dict[int, list[tuple[AttemptIntent, AttemptResult]]] = {}
    for intent, result in zip(source.attempt_intents, source.attempt_results, strict=True):
        attempts.setdefault(intent.call_index, []).append((intent, result))
    results = {value.call_index: value for value in source.results}
    for intent in source.intents:
        clone.write_intent(intent)
        for attempt, result in attempts[intent.call_index]:
            clone.write_attempt_intent(attempt)
            clone.write_attempt_result(result)
        clone.write_result(results[intent.call_index])
    return clone


def _publication_bindings(
    plan: report_module.ExperimentPlan,
    bundle: report_module.ArtifactRowBundle,
    observations: SanitizedObservations,
) -> ReportPublicationBindings:
    rows_bytes = canonical_jsonl_bytes(tuple(row_to_dict(value) for value in bundle.rows))
    blobs_bytes = canonical_jsonl_bytes(tuple(blob_record_to_dict(value) for value in bundle.blobs))
    calls = sum(len(value.observation_keys) for value in bundle.rows)
    return ReportPublicationBindings(
        bundle.rows[0].run_id,
        "a" * 64,
        "a" * 64,
        "b" * 64,
        "COMPLETE",
        corpus_sha256(plan.items),
        "d" * 64,
        "e" * 64,
        canonical_table_sha256("rows", rows_bytes),
        canonical_table_sha256("blobs", blobs_bytes),
        observations.sha256,
        len(bundle.rows),
        len(bundle.rows),
        max(1, calls),
        calls,
    )


def test_collection_rows_bind_nested_payload_blobs_observations_and_work(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
) -> None:
    collection, _sink, _observations = report_source
    bundle = collection_to_row_bundle(collection)

    assert len(bundle.rows) == len(collection.items) * 21
    assert {row.key.row_type for row in bundle.rows} == {
        RowType.CANDIDATE,
        RowType.RAW,
        RowType.PURE_SOLVER,
    }
    assert {blob.ref.kind for blob in bundle.blobs} == {
        BlobKind.NOTEGRAPH,
        BlobKind.TARGET,
        BlobKind.TAB,
        BlobKind.TRACE,
    }
    candidate = next(row for row in bundle.rows if row.key.row_type is RowType.CANDIDATE)
    assert candidate.payload["proposal"]["status"] == "LLM_SUCCESS"  # type: ignore[index]
    assert candidate.payload["source"]["position"] in (0, 1)  # type: ignore[index]
    assert candidate.payload["initial"]["target_blob_sha256"]  # type: ignore[index]
    assert candidate.payload["terminal"]["score"]["profiles"]  # type: ignore[index]
    assert candidate.payload["work"]["logical_calls"] == len(  # type: ignore[index]
        candidate.observation_keys
    )
    assert candidate.payload["work"]["solver_calls"] >= 1  # type: ignore[index]
    assert set(candidate.payload["work"]["edit_counts"]) == {  # type: ignore[index]
        "applied",
        "invalid",
        "no_op",
        "rejected",
    }
    raw = next(row for row in bundle.rows if row.key.row_type is RowType.RAW)
    pure = next(row for row in bundle.rows if row.key.row_type is RowType.PURE_SOLVER)
    assert len(raw.observation_keys) == 1
    assert pure.observation_keys == ()
    assert pure.payload["baseline"] == {  # type: ignore[index]
        "baseline_id": "B2",
        "llm_calls": 0,
        "solver_calls": 1,
    }


def test_collection_row_bundle_indexes_joined_calls_once_without_byte_drift(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection, _sink, _observations = report_source
    original_joined_calls = report_module._joined_calls
    joined_iterations = 0

    class _CountingJoined(tuple[report_module._JoinedCall, ...]):
        def __iter__(self) -> Iterator[report_module._JoinedCall]:
            nonlocal joined_iterations
            joined_iterations += 1
            return super().__iter__()

    def counted_joined_calls(ledger: ObservationLedger) -> _CountingJoined:
        return _CountingJoined(original_joined_calls(ledger))

    monkeypatch.setattr(report_module, "_joined_calls", counted_joined_calls)
    bundle = collection_to_row_bundle(collection)
    rows_bytes = canonical_jsonl_bytes(tuple(row_to_dict(value) for value in bundle.rows))
    blobs_bytes = canonical_jsonl_bytes(
        tuple(blob_record_to_dict(value) for value in bundle.blobs)
    )

    assert joined_iterations == 1
    assert hashlib.sha256(rows_bytes).hexdigest() == (
        "6931e13d8625d288d198b7c03aee3bbf0001cf7c2f758814ed49ff5f204f5c95"
    )
    assert hashlib.sha256(blobs_bytes).hexdigest() == (
        "0d074e5ce058b79e6026ae16a9a0a70a45dd9a45c69985f21b73321135fa26b2"
    )


def test_rows_restore_a_complete_or_prefix_resume_state(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
) -> None:
    collection, source_sink, _observations = report_source
    bundle = collection_to_row_bundle(collection)
    state = resume_state_from_rows(
        collection.plan,
        collection.goal,
        collection.profile,
        bundle.rows,
        bundle.blobs,
    )

    assert len(state.pure_solver_outcomes) == len(collection.items)
    assert len(state.completed_units) == len(collection.plan.collection_schedule)
    assert tuple(value.unit for value in state.completed_units) == (
        collection.plan.collection_schedule
    )

    resumed = run_experiment(
        collection.plan,
        collection.goal,
        FakeLLM([]),
        FakeLLM([]),
        collection.profile,
        observation_sink=_clone_sink(source_sink),
        observation_clock_ns=lambda: 7,
        resume_state=state,
    )
    assert derive_shared_views(resumed) == derive_shared_views(collection)

    rows_by_key = {row.key: row for row in bundle.rows}
    prefix_keys = {row.key for row in bundle.rows if row.key.row_type is RowType.PURE_SOLVER}
    for unit in collection.plan.collection_schedule[:3]:
        row_type = RowType.CANDIDATE if unit.arm is CollectionArm.AGENT else RowType.RAW
        prefix_keys.add(
            next(
                key
                for key in rows_by_key
                if key.row_type is row_type
                and key.item_id == unit.item_id
                and key.candidate_index == unit.candidate_index
            )
        )
    prefix = resume_state_from_rows(
        collection.plan,
        collection.goal,
        collection.profile,
        tuple(rows_by_key[key] for key in prefix_keys),
        bundle.blobs,
    )
    assert len(prefix.completed_units) == 3


def test_resume_indexes_matched_budgets_once(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
) -> None:
    collection, _sink, _observations = report_source
    bundle = collection_to_row_bundle(collection)
    original_budgets = collection.plan.matched_budgets
    budget_iterations = 0

    class _CountingBudgets(tuple[ItemMatchedBudget, ...]):
        def __iter__(self) -> Iterator[ItemMatchedBudget]:
            nonlocal budget_iterations
            budget_iterations += 1
            return super().__iter__()

    object.__setattr__(
        collection.plan,
        "matched_budgets",
        _CountingBudgets(original_budgets),
    )
    try:
        state = resume_state_from_rows(
            collection.plan,
            collection.goal,
            collection.profile,
            bundle.rows,
            bundle.blobs,
        )
    finally:
        object.__setattr__(collection.plan, "matched_budgets", original_budgets)

    assert len(state.completed_units) == len(collection.plan.collection_schedule)
    assert budget_iterations == 1


def test_resume_hashes_each_source_context_once(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection, _sink, _observations = report_source
    bundle = collection_to_row_bundle(collection)
    original = report_module.arrangement_source_context_sha256
    calls = 0

    def counted(*args: object, **kwargs: object) -> str:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(report_module, "arrangement_source_context_sha256", counted)
    state = resume_state_from_rows(
        collection.plan,
        collection.goal,
        collection.profile,
        bundle.rows,
        bundle.blobs,
    )

    assert len(state.completed_units) == len(collection.plan.collection_schedule)
    assert calls == len(collection.plan.items)


def test_full_rescore_rechecks_stored_values_and_fast_mode_is_explicit(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection, _sink, _observations = report_source
    bundle = collection_to_row_bundle(collection)
    full = rescore_row_bundle(
        collection.plan,
        collection.goal,
        collection.profile,
        bundle.rows,
        bundle.blobs,
        mode=ReplayMode.FULL_RESCORE,
    )
    assert full.mode is ReplayMode.FULL_RESCORE
    assert {
        profile.profile_fingerprint
        for row in full.rows
        for checkpoint in row.checkpoints
        for profile in checkpoint.profiles
    } == {SMALL_HAND.fingerprint, MEDIAN_HAND.fingerprint, LARGE_HAND.fingerprint}

    candidate = next(row for row in bundle.rows if row.key.row_type is RowType.CANDIDATE)
    payload = candidate.payload
    terminal = dict(payload["terminal"])  # type: ignore[arg-type]
    score = dict(terminal["score"])  # type: ignore[arg-type]
    score["green"] = not score["green"]
    terminal["score"] = score
    payload["terminal"] = terminal
    drifted = build_row(
        run_id=candidate.run_id,
        key=candidate.key,
        family_id=candidate.family_id,
        cluster_id=candidate.cluster_id,
        observation_keys=candidate.observation_keys,
        blob_refs=candidate.blob_refs,
        payload=payload,
    )
    rows = tuple(drifted if row.key == candidate.key else row for row in bundle.rows)
    with pytest.raises(ReportInputError, match="stored score"):
        rescore_row_bundle(
            collection.plan,
            collection.goal,
            collection.profile,
            rows,
            bundle.blobs,
            mode=ReplayMode.FULL_RESCORE,
        )

    monkeypatch.setattr(
        report_module,
        "solve_fingering",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("solver called")),
    )
    for name in ("check_playability", "faithfulness", "fidelity", "propose_fingerstyle"):
        monkeypatch.setattr(
            report_module,
            name,
            lambda *_args, _name=name, **_kwargs: (_ for _ in ()).throw(
                AssertionError(f"{_name} called")
            ),
        )
    fast = rescore_row_bundle(
        collection.plan,
        collection.goal,
        collection.profile,
        rows,
        bundle.blobs,
        mode=ReplayMode.FAST_REAGGREGATE,
    )
    assert fast.mode is ReplayMode.FAST_REAGGREGATE


def test_full_rescore_memoizes_unique_source_target_and_tab_semantics(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection, _sink, _observations = report_source
    bundle = collection_to_row_bundle(collection)
    solve_calls = 0
    source_parse_calls = 0
    target_parse_calls = 0
    tab_parse_calls = 0
    original_solve = report_module.solve_fingering
    original_source_parse = report_module.notegraph_to_ir
    original_target_parse = report_module._parse_target
    original_tab_parse = report_module._parse_tab

    def counted_solve(*args: object, **kwargs: object) -> object:
        nonlocal solve_calls
        solve_calls += 1
        return original_solve(*args, **kwargs)  # type: ignore[arg-type]

    def counted_source_parse(*args: object, **kwargs: object) -> object:
        nonlocal source_parse_calls
        source_parse_calls += 1
        return original_source_parse(*args, **kwargs)  # type: ignore[arg-type]

    def counted_target_parse(*args: object, **kwargs: object) -> object:
        nonlocal target_parse_calls
        target_parse_calls += 1
        return original_target_parse(*args, **kwargs)  # type: ignore[arg-type]

    def counted_tab_parse(*args: object, **kwargs: object) -> object:
        nonlocal tab_parse_calls
        tab_parse_calls += 1
        return original_tab_parse(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(report_module, "solve_fingering", counted_solve)
    monkeypatch.setattr(report_module, "notegraph_to_ir", counted_source_parse)
    monkeypatch.setattr(report_module, "_parse_target", counted_target_parse)
    monkeypatch.setattr(report_module, "_parse_tab", counted_tab_parse)
    rescored = rescore_row_bundle(
        collection.plan,
        collection.goal,
        collection.profile,
        bundle.rows,
        bundle.blobs,
        mode=ReplayMode.FULL_RESCORE,
    )

    target_semantics: set[tuple[str, str]] = set()
    source_semantics: set[tuple[str, str]] = set()
    tab_semantics: set[tuple[str, str]] = set()
    for row in bundle.rows:
        source = row.payload["source"]  # type: ignore[index]
        source_semantics.add((row.key.item_id, source["notegraph_blob_sha256"]))  # type: ignore[index]
        if row.key.row_type is RowType.CANDIDATE:
            for checkpoint in ("initial", "terminal"):
                payload = row.payload[checkpoint]  # type: ignore[index]
                target_semantics.add((row.key.item_id, payload["target_blob_sha256"]))  # type: ignore[index]
        elif row.key.row_type is RowType.PURE_SOLVER:
            outcome = row.payload["outcome"]  # type: ignore[index]
            target_semantics.add((row.key.item_id, outcome["target_blob_sha256"]))  # type: ignore[index]
        for ref in row.blob_refs:
            if ref.kind is BlobKind.TAB:
                tab_semantics.add((row.key.item_id, ref.sha256))

    assert len(rescored.rows) == len(bundle.rows)
    assert solve_calls == len(target_semantics)
    assert source_parse_calls == len(source_semantics)
    assert target_parse_calls == len(target_semantics)
    assert tab_parse_calls == len(tab_semantics)
    assert solve_calls < len(collection.items) * 21


@pytest.mark.parametrize(
    ("field", "expected_error"),
    (
        ("diagnostic_codes", "stored oracle values"),
        ("verdict", "stored oracle values"),
        ("ranking_fidelity", "stored ranking fidelity"),
    ),
)
def test_full_rescore_cache_hit_still_checks_every_stored_snapshot_field(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
    field: str,
    expected_error: str,
) -> None:
    collection, _sink, _observations = report_source
    bundle = collection_to_row_bundle(collection)
    candidate = next(row for row in bundle.rows if row.key.row_type is RowType.CANDIDATE)
    payload = candidate.payload
    terminal = dict(payload["terminal"])  # type: ignore[arg-type]
    if field == "diagnostic_codes":
        terminal[field] = [*terminal[field], "FORGED"]  # type: ignore[misc]
    elif field == "verdict":
        terminal[field] = "RED" if terminal[field] != "RED" else "GREEN"
    else:
        ranking = dict(terminal[field])  # type: ignore[arg-type]
        ranking["melody_recall"] = 0.0 if ranking["melody_recall"] != 0.0 else 1.0
        terminal[field] = ranking
    payload["terminal"] = terminal
    changed = build_row(
        run_id=candidate.run_id,
        key=candidate.key,
        family_id=candidate.family_id,
        cluster_id=candidate.cluster_id,
        observation_keys=candidate.observation_keys,
        blob_refs=candidate.blob_refs,
        payload=payload,
    )

    with pytest.raises(ReportInputError, match=expected_error):
        rescore_row_bundle(
            collection.plan,
            collection.goal,
            collection.profile,
            tuple(changed if row.key == candidate.key else row for row in bundle.rows),
            bundle.blobs,
            mode=ReplayMode.FULL_RESCORE,
        )


def test_rescore_cache_key_separates_source_goal_profile_and_target_blob(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection, _sink, _observations = report_source
    bundle = collection_to_row_bundle(collection)
    candidate = next(row for row in bundle.rows if row.key.row_type is RowType.CANDIDATE)
    target_sha = candidate.payload["initial"]["target_blob_sha256"]  # type: ignore[index]
    target_record = next(
        blob
        for blob in bundle.blobs
        if blob.ref.kind is BlobKind.TARGET and blob.ref.sha256 == target_sha
    )
    target = report_module._parse_target(target_record.content, "target")
    other_record = build_blob_record(
        BlobKind.TARGET,
        report_module._target_content((Note(F(0), F(1), 67, "melody"),)),
    )
    other_target = report_module._parse_target(other_record.content, "other_target")
    first_item, second_item = collection.plan.items
    exact_goal = report_module._goal_at_source_tempo(collection.goal, first_item)
    cache = report_module._RescoreCache()
    solve_calls = 0
    original_solve = report_module.solve_fingering

    def counted_solve(*args: object, **kwargs: object) -> object:
        nonlocal solve_calls
        solve_calls += 1
        return original_solve(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(report_module, "solve_fingering", counted_solve)
    for _repeat in range(2):
        report_module._cached_solved_target(
            cache, first_item, target_record, target, exact_goal, collection.profile
        )
    report_module._cached_solved_target(
        cache,
        first_item,
        target_record,
        target,
        replace(exact_goal, capo=1),
        collection.profile,
    )
    report_module._cached_solved_target(
        cache, first_item, target_record, target, exact_goal, SMALL_HAND
    )
    report_module._cached_solved_target(
        cache, first_item, other_record, other_target, exact_goal, collection.profile
    )
    report_module._cached_solved_target(
        cache,
        second_item,
        target_record,
        target,
        report_module._goal_at_source_tempo(collection.goal, second_item),
        collection.profile,
    )

    assert solve_calls == 5


def test_checkpoint_scoring_reuses_each_profile_oracle_across_provenance_scores(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection, _sink, _observations = report_source
    item = collection.items[0].item
    tab = next(
        trajectory.terminal.tab
        for trajectory in collection.items[0].trajectories
        if trajectory.terminal.tab is not None
    )
    tab_record = build_blob_record(BlobKind.TAB, report_module._tab_content(tab))
    check_calls = 0
    original_check = report_module.check_playability

    def counted_check(*args: object, **kwargs: object) -> object:
        nonlocal check_calls
        check_calls += 1
        return original_check(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(report_module, "check_playability", counted_check)
    report_module._checkpoint_score(
        item,
        tab,
        fallback_assisted=False,
        llm_generated=True,
        profile=collection.profile,
    )
    assert check_calls == 3

    check_calls = 0
    cache = report_module._RescoreCache()
    for fallback, generated in ((False, True), (True, False)):
        report_module._cached_checkpoint_score(
            cache,
            item,
            tab_record,
            tab,
            collection.profile,
            fallback_assisted=fallback,
            llm_generated=generated,
        )
    assert check_calls == 3


def test_collection_materialization_reuses_unique_tab_profile_oracles(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection, _sink, _observations = report_source
    expected = collection_to_row_bundle(collection)
    items = {item.item_id: item for item in collection.plan.items}
    oracle_keys = {
        (
            row.key.item_id,
            ref.sha256,
            profile.fingerprint,
            items[row.key.item_id].ir.meta.tempo_bpm,
            items[row.key.item_id].ir.meta.time_sig[0],
        )
        for row in expected.rows
        for ref in row.blob_refs
        if ref.kind is BlobKind.TAB
        for profile in (SMALL_HAND, collection.profile, LARGE_HAND)
    }
    check_calls = 0
    original_check = report_module.check_playability

    def counted_check(*args: object, **kwargs: object) -> object:
        nonlocal check_calls
        check_calls += 1
        return original_check(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(report_module, "check_playability", counted_check)
    actual = collection_to_row_bundle(collection)

    assert actual == expected
    assert check_calls == len(oracle_keys)


def test_report_aggregates_separate_strata_inference_baselines_usage_and_wire(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
) -> None:
    collection, _sink, observations = report_source
    bundle = collection_to_row_bundle(collection)
    report = build_benchmark_report(
        collection.plan,
        collection.goal,
        collection.profile,
        bundle.rows,
        bundle.blobs,
        observations,
        publication_bindings=_publication_bindings(collection.plan, bundle, observations),
        mode=ReplayMode.FULL_RESCORE,
        bootstrap_seed=41,
        bootstrap_repetitions=101,
        sign_flip_seed=43,
        sign_flip_draws=101,
    )
    wire = report_to_dict(report)

    assert hashlib.sha256(report.wire_json).hexdigest() == (
        "8d57ca8b783798c02c02d256db6687b97ced77cb7ca370e4885484e76a9feed8"
    )
    assert report == build_benchmark_report(
        collection.plan,
        collection.goal,
        collection.profile,
        tuple(reversed(bundle.rows)),
        tuple(reversed(bundle.blobs)),
        observations,
        publication_bindings=_publication_bindings(collection.plan, bundle, observations),
        mode=ReplayMode.FULL_RESCORE,
        bootstrap_seed=41,
        bootstrap_repetitions=101,
        sign_flip_seed=43,
        sign_flip_draws=101,
    )
    assert wire["schema"] == "benchmark-report@0.1.0"
    assert wire["mode"] == ReplayMode.FULL_RESCORE.value
    assert len(wire["strata"]) == 2
    signatures = {value["evidence_signature"] for value in wire["strata"]}
    assert signatures == {"melody", "melody+bass+harmony"}
    for stratum in wire["strata"]:
        assert [point["k"] for point in stratum["reliability"]] == list(range(1, 11))
        assert set(stratum["reliability"][0]["predicates"]) == {
            "initial_green",
            "initial_joint",
            "raw_green",
            "raw_joint",
            "terminal_green",
            "terminal_joint",
            "terminal_llm_success",
        }
        assert stratum["baselines"]["pure_solver"]["denominator"] == 1
    assert wire["optional_baselines"] == [
        {
            "baseline_id": "B3",
            "reason": "LICENSE_AUDITED_REPRODUCIBLE_ADAPTER_ABSENT",
            "status": "unavailable",
        },
        {
            "baseline_id": "B4",
            "reason": "LICENSE_AUDITED_REPRODUCIBLE_ADAPTER_ABSENT",
            "status": "unavailable",
        },
    ]
    assert wire["inference"]["critic"]["decision"] == (
        CapabilityDecision.HUMAN_BLOCKED_PROBATION.value
    )
    assert wire["inference"]["search"]["decision"] == (
        CapabilityDecision.PROBATION_COST_UNKNOWN.value
    )
    usage = wire["usage"]["provider_usage"]
    assert usage["input_tokens"] is not None
    assert usage["output_tokens"] is not None
    assert usage["cache_creation_input_tokens"] is None
    assert usage["cache_read_input_tokens"] is None
    assert json.dumps(wire, allow_nan=False, sort_keys=True, separators=(",", ":"))
    assert report_to_markdown(report) == report_to_markdown(report)
    assert "Best-of-4 search" in report_to_markdown(report)


def test_report_nested_payload_parser_rejects_unknown_keys(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
) -> None:
    collection, _sink, _observations = report_source
    bundle = collection_to_row_bundle(collection)
    row = next(value for value in bundle.rows if value.key.row_type is RowType.RAW)
    payload = row.payload
    payload["score"] = {**payload["score"], "unknown": True}  # type: ignore[dict-item]
    changed = build_row(
        run_id=row.run_id,
        key=row.key,
        family_id=row.family_id,
        cluster_id=row.cluster_id,
        observation_keys=row.observation_keys,
        blob_refs=row.blob_refs,
        payload=payload,
    )

    with pytest.raises(ReportInputError, match="exact keys"):
        rescore_row_bundle(
            collection.plan,
            collection.goal,
            collection.profile,
            tuple(changed if value.key == row.key else value for value in bundle.rows),
            bundle.blobs,
            mode=ReplayMode.FAST_REAGGREGATE,
        )


def test_reliability_uses_one_shared_bootstrap_schedule_per_stratum(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collection, _sink, observations = report_source
    bundle = collection_to_row_bundle(collection)
    original = report_module.family_cluster_bootstrap_means
    calls = 0

    def counted(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(report_module, "family_cluster_bootstrap_means", counted)
    build_benchmark_report(
        collection.plan,
        collection.goal,
        collection.profile,
        bundle.rows,
        bundle.blobs,
        observations,
        publication_bindings=_publication_bindings(collection.plan, bundle, observations),
        bootstrap_repetitions=11,
        sign_flip_draws=11,
    )

    assert calls == 2


def test_pareto_uses_complete_token_sum_not_componentwise_dominance() -> None:
    def point(k: int, input_tokens: int, output_tokens: int) -> dict[str, object]:
        return {
            "k": k,
            "joint_success": 0.5,
            "cost": {
                "logical_calls": 2,
                "complete_provider_tokens": input_tokens + output_tokens,
                "elapsed_microseconds": 10,
                "provider_usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        }

    # k=2 crosses components (more input, less output) but has a lower complete sum.
    nondominated, complete = report_module._pareto_nondominated(  # noqa: SLF001
        [point(2, 8, 0), point(4, 1, 9)],
        4,
    )

    assert complete is True
    assert nondominated is False


def test_cost_summary_preserves_reported_usage_but_marks_retries_incomplete() -> None:
    key = ObservationKey("call-0", 0)
    calls = {
        key: {
            "provider_attempts": 2,
            "requested_output_tokens": 10,
            "attempt_reserved_output_tokens": 20,
        }
    }
    observations = {
        key: {
            "elapsed_microseconds": 30,
            "usage": {
                "input_tokens": 7,
                "output_tokens": 3,
                "cache_creation_input_tokens": 2,
                "cache_read_input_tokens": 1,
            },
        }
    }

    summary = report_module._cost_summary_wire(  # noqa: SLF001
        (key,),
        calls,
        observations,
    )

    assert summary["provider_usage"] == {
        "input_tokens": 7,
        "output_tokens": 3,
        "cache_creation_input_tokens": 2,
        "cache_read_input_tokens": 1,
    }
    assert "usage_covers_all_attempts" not in summary
    assert summary["complete_provider_tokens"] is None

    missing_usage = {
        key: {
            **observations[key],
            "usage": {
                **observations[key]["usage"],
                "cache_read_input_tokens": None,
            },
        }
    }
    no_retry_calls = {key: {**calls[key], "provider_attempts": 1}}
    missing_summary = report_module._cost_summary_wire(  # noqa: SLF001
        (key,),
        no_retry_calls,
        missing_usage,
    )
    assert "usage_covers_all_attempts" not in missing_summary
    assert missing_summary["complete_provider_tokens"] is None


def test_incremental_helpers_emit_exact_source_bound_single_rows(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
) -> None:
    collection, sink, _observations = report_source
    complete = collection_to_row_bundle(collection)
    unit = collection.plan.collection_schedule[0]
    item = collection.items[unit.item_position]
    existing = next(
        row
        for row in complete.rows
        if row.key.item_id == unit.item_id
        and row.key.candidate_index == unit.candidate_index
        and row.key.row_type
        is (RowType.CANDIDATE if unit.arm is CollectionArm.AGENT else RowType.RAW)
    )
    end = max(value.call_index for value in existing.observation_keys) + 1
    ledger = ObservationLedger(
        tuple(value for value in sink.intents if value.call_index < end),
        tuple(value for value in sink.results if value.call_index < end),
        tuple(value for value in sink.attempt_intents if value.call_index < end),
        tuple(value for value in sink.attempt_results if value.call_index < end),
    )
    completed = CompletedExperimentUnit(
        unit,
        arrangement_source_context_sha256(item.item.ir),
        trajectory=(
            item.trajectories[unit.candidate_index] if unit.arm is CollectionArm.AGENT else None
        ),
        raw_outcome=(
            item.raw_outcomes[unit.candidate_index] if unit.arm is CollectionArm.RAW else None
        ),
    )
    incremental = completed_unit_to_row_bundle(
        collection.plan,
        collection.goal,
        collection.profile,
        completed,
        ledger,
    )

    assert incremental.rows == (existing,)
    assert {value.ref for value in incremental.blobs} == set(existing.blob_refs)
    with pytest.raises(ReportInputError, match="source_context_sha256"):
        completed_unit_to_row_bundle(
            collection.plan,
            collection.goal,
            collection.profile,
            replace(completed, source_context_sha256="0" * 64),
            ledger,
        )

    pure = CompletedPureSolver(
        item.item.item_id,
        arrangement_source_context_sha256(item.item.ir),
        item.pure_solver,
    )
    pure_incremental = completed_pure_solver_to_row_bundle(
        collection.plan,
        collection.goal,
        collection.profile,
        pure,
    )
    assert pure_incremental.rows == (
        next(
            row
            for row in complete.rows
            if row.key.item_id == item.item.item_id and row.key.row_type is RowType.PURE_SOLVER
        ),
    )


def test_incremental_unit_helper_accepts_global_index_suffix_but_requires_tail(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
) -> None:
    collection, sink, _observations = report_source
    complete = collection_to_row_bundle(collection)

    def row_for(unit: ScheduledUnit) -> BenchmarkRow:
        row_type = RowType.CANDIDATE if unit.arm is CollectionArm.AGENT else RowType.RAW
        return next(
            row
            for row in complete.rows
            if row.key.item_id == unit.item_id
            and row.key.candidate_index == unit.candidate_index
            and row.key.row_type is row_type
        )

    def ledger_between(start: int, end: int) -> ObservationLedger:
        return ObservationLedger(
            tuple(value for value in sink.intents if start <= value.call_index < end),
            tuple(value for value in sink.results if start <= value.call_index < end),
            tuple(value for value in sink.attempt_intents if start <= value.call_index < end),
            tuple(value for value in sink.attempt_results if start <= value.call_index < end),
        )

    unit = collection.plan.collection_schedule[1]
    item = collection.items[unit.item_position]
    existing = row_for(unit)
    start = min(value.call_index for value in existing.observation_keys)
    end = max(value.call_index for value in existing.observation_keys) + 1
    assert start > 0
    completed = CompletedExperimentUnit(
        unit,
        arrangement_source_context_sha256(item.item.ir),
        trajectory=(
            item.trajectories[unit.candidate_index] if unit.arm is CollectionArm.AGENT else None
        ),
        raw_outcome=(
            item.raw_outcomes[unit.candidate_index] if unit.arm is CollectionArm.RAW else None
        ),
    )

    incremental = completed_unit_to_row_bundle(
        collection.plan,
        collection.goal,
        collection.profile,
        completed,
        ledger_between(start, end),
    )
    assert incremental.rows == (existing,)

    following = row_for(collection.plan.collection_schedule[2])
    following_end = max(value.call_index for value in following.observation_keys) + 1
    with pytest.raises(ReportInputError, match="contiguous ledger suffix"):
        completed_unit_to_row_bundle(
            collection.plan,
            collection.goal,
            collection.profile,
            completed,
            ledger_between(start, following_end),
        )


def test_pure_row_helper_uses_the_frozen_item_position_without_plan_scan(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
) -> None:
    collection, _sink, _observations = report_source
    original_items = collection.plan.items
    item_iterations = 0

    class _CountingItems(tuple[CorpusItem, ...]):
        def __iter__(self) -> Iterator[CorpusItem]:
            nonlocal item_iterations
            item_iterations += 1
            return super().__iter__()

    object.__setattr__(collection.plan, "items", _CountingItems(original_items))
    try:
        bundle = report_module.pure_outcome_to_row_bundle(
            collection.plan,
            collection.goal,
            collection.profile,
            collection.items[0].item,
            collection.items[0].pure_solver,
        )
    finally:
        object.__setattr__(collection.plan, "items", original_items)

    assert len(bundle.rows) == 1
    assert item_iterations == 0


def test_report_rejects_row_observation_metadata_drift(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
) -> None:
    collection, _sink, observations = report_source
    bundle = collection_to_row_bundle(collection)
    row = next(value for value in bundle.rows if value.key.row_type is RowType.CANDIDATE)
    payload = row.payload
    work = dict(payload["work"])  # type: ignore[arg-type]
    calls = list(work["calls"])  # type: ignore[arg-type]
    call = dict(calls[0])  # type: ignore[arg-type]
    usage = dict(call["usage"])  # type: ignore[arg-type]
    usage["input_tokens"] = usage["input_tokens"] + 1  # type: ignore[operator]
    call["usage"] = usage
    calls[0] = call
    work["calls"] = calls
    payload["work"] = work
    changed = build_row(
        run_id=row.run_id,
        key=row.key,
        family_id=row.family_id,
        cluster_id=row.cluster_id,
        observation_keys=row.observation_keys,
        blob_refs=row.blob_refs,
        payload=payload,
    )
    changed_bundle = report_module.ArtifactRowBundle(
        tuple(changed if value.key == row.key else value for value in bundle.rows),
        bundle.blobs,
    )

    with pytest.raises(ReportInputError, match="metadata disagrees"):
        build_benchmark_report(
            collection.plan,
            collection.goal,
            collection.profile,
            changed_bundle.rows,
            changed_bundle.blobs,
            observations,
            publication_bindings=_publication_bindings(
                collection.plan, changed_bundle, observations
            ),
            mode=ReplayMode.FAST_REAGGREGATE,
            bootstrap_repetitions=11,
            sign_flip_draws=11,
        )


def test_raw_replay_binds_the_frozen_source_context_in_fast_and_full_modes(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
) -> None:
    collection, _sink, _observations = report_source
    bundle = collection_to_row_bundle(collection)
    row = next(value for value in bundle.rows if value.key.row_type is RowType.RAW)
    payload = row.payload
    outcome = dict(payload["outcome"])  # type: ignore[arg-type]
    outcome["source_context_sha256"] = "0" * 64
    payload["outcome"] = outcome
    changed = build_row(
        run_id=row.run_id,
        key=row.key,
        family_id=row.family_id,
        cluster_id=row.cluster_id,
        observation_keys=row.observation_keys,
        blob_refs=row.blob_refs,
        payload=payload,
    )
    rows = tuple(changed if value.key == row.key else value for value in bundle.rows)

    for mode in (ReplayMode.FULL_RESCORE, ReplayMode.FAST_REAGGREGATE):
        with pytest.raises(ReportInputError, match="source_context_sha256"):
            rescore_row_bundle(
                collection.plan,
                collection.goal,
                collection.profile,
                rows,
                bundle.blobs,
                mode=mode,
            )


def test_resume_requires_pure_callback_prefix_and_all_pure_before_collection(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
) -> None:
    collection, _sink, _observations = report_source
    bundle = collection_to_row_bundle(collection)
    by_item_type = {(row.key.item_id, row.key.row_type): row for row in bundle.rows}
    first_item, second_item = collection.plan.items[:2]
    late_pure = by_item_type[(second_item.item_id, RowType.PURE_SOLVER)]

    with pytest.raises(ReportInputError, match="pure-solver rows must form"):
        resume_state_from_rows(
            collection.plan,
            collection.goal,
            collection.profile,
            (late_pure,),
            bundle.blobs,
        )

    unit = collection.plan.collection_schedule[0]
    unit_type = RowType.CANDIDATE if unit.arm is CollectionArm.AGENT else RowType.RAW
    unit_row = next(
        row
        for row in bundle.rows
        if row.key.row_type is unit_type
        and row.key.item_id == unit.item_id
        and row.key.candidate_index == unit.candidate_index
    )
    first_pure = by_item_type[(first_item.item_id, RowType.PURE_SOLVER)]
    with pytest.raises(ReportInputError, match="requires every prior pure-solver"):
        resume_state_from_rows(
            collection.plan,
            collection.goal,
            collection.profile,
            tuple(sorted((first_pure, unit_row), key=lambda value: value.sort_key)),
            bundle.blobs,
        )


def test_resume_rejects_swapped_cross_unit_call_index_slices(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
) -> None:
    collection, _sink, _observations = report_source
    bundle = collection_to_row_bundle(collection)
    raw_positions = [
        (position, unit)
        for position, unit in enumerate(collection.plan.collection_schedule)
        if unit.arm is CollectionArm.RAW
    ]
    (_first_position, first_unit), (second_position, second_unit) = raw_positions[:2]
    prefix_units = collection.plan.collection_schedule[: second_position + 1]

    def scheduled_row(exact: ScheduledUnit) -> BenchmarkRow:
        row_type = RowType.CANDIDATE if exact.arm is CollectionArm.AGENT else RowType.RAW
        return next(
            row
            for row in bundle.rows
            if row.key.row_type is row_type
            and row.key.item_id == exact.item_id
            and row.key.candidate_index == exact.candidate_index
        )

    first_row = scheduled_row(first_unit)
    second_row = scheduled_row(second_unit)
    assert len(first_row.observation_keys) == len(second_row.observation_keys) == 1

    def with_call_identity(
        row: BenchmarkRow,
        source: BenchmarkRow,
    ) -> BenchmarkRow:
        payload = row.payload
        outcome = dict(payload["outcome"])  # type: ignore[arg-type]
        call = dict(outcome["call"])  # type: ignore[arg-type]
        call["logical_call_id"] = source.observation_keys[0].logical_call_id
        call["call_index"] = source.observation_keys[0].call_index
        outcome["call"] = call
        payload["outcome"] = outcome
        return build_row(
            run_id=row.run_id,
            key=row.key,
            family_id=row.family_id,
            cluster_id=row.cluster_id,
            observation_keys=source.observation_keys,
            blob_refs=row.blob_refs,
            payload=payload,
        )

    swapped = {
        first_row.key: with_call_identity(first_row, second_row),
        second_row.key: with_call_identity(second_row, first_row),
    }
    prefix_rows = [row for row in bundle.rows if row.key.row_type is RowType.PURE_SOLVER] + [
        swapped.get(scheduled_row(unit).key, scheduled_row(unit)) for unit in prefix_units
    ]

    with pytest.raises(ReportInputError, match="global schedule prefix"):
        resume_state_from_rows(
            collection.plan,
            collection.goal,
            collection.profile,
            tuple(sorted(prefix_rows, key=lambda value: value.sort_key)),
            bundle.blobs,
        )


@pytest.mark.parametrize("seed_field", ["bootstrap_seed", "sign_flip_seed"])
def test_report_seed_bounds_reserve_frozen_derived_offsets(
    report_source: tuple[ExperimentCollection, InMemoryObservationSink, SanitizedObservations],
    seed_field: str,
) -> None:
    collection, _sink, observations = report_source
    bundle = collection_to_row_bundle(collection)
    kwargs = {
        "publication_bindings": _publication_bindings(collection.plan, bundle, observations),
        "bootstrap_repetitions": 11,
        "sign_flip_draws": 11,
        seed_field: (1 << 63) - 1,
    }

    with pytest.raises(ReportInputError, match="derived offsets"):
        build_benchmark_report(
            collection.plan,
            collection.goal,
            collection.profile,
            bundle.rows,
            bundle.blobs,
            observations,
            **kwargs,  # type: ignore[arg-type]
        )
