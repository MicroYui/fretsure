from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn

import pytest

import fretsure.bench.artifacts as artifacts
from fretsure.bench.artifacts import (
    ArtifactCode,
    ArtifactError,
    ArtifactLimits,
    ArtifactStore,
    BenchmarkManifest,
    BenchmarkReceipt,
    BenchmarkRow,
    BlobKind,
    BlobRecord,
    CompleteUnitReservation,
    CompletionStatus,
    DurableObservationSink,
    FinalizationInputs,
    FinalizedReport,
    ObservationKey,
    ReplayBundle,
    RowKey,
    RowType,
    blob_record_from_dict,
    blob_record_to_dict,
    build_blob_record,
    build_manifest,
    build_row,
    canonical_blob_sha256,
    canonical_jsonl_bytes,
    canonical_table_sha256,
    load_replay_bundle,
    manifest_from_dict,
    manifest_sha256,
    manifest_to_dict,
    parse_canonical_json_bytes,
    parse_canonical_jsonl_bytes,
    publish_replay_bundle,
    read_canonical_json,
    receipt_from_dict,
    receipt_sha256,
    receipt_to_dict,
    require_complete_receipt,
    row_from_dict,
    row_to_dict,
    sanitize_observations,
    sanitized_observations_from_dict,
)
from fretsure.bench.contracts import (
    BENCHMARK_MANIFEST_VERSION,
    BENCHMARK_OBSERVATIONS_VERSION,
    BENCHMARK_REPORT_VERSION,
    BENCHMARK_ROW_VERSION,
    canonical_json_bytes,
    canonical_sha256,
)
from fretsure.bench.observe import (
    CallFailureCode,
    CallSequence,
    CallStage,
    InMemoryObservationSink,
    ObservationSinkError,
    ObservingLLM,
    ProviderObservation,
)
from fretsure.llm.client import ConstantLLM


def _row_key() -> RowKey:
    return RowKey(RowType.CANDIDATE, "item-0", 0, 0, "pair:item-0:0")


def _limits() -> ArtifactLimits:
    return ArtifactLimits(
        max_rows=8,
        max_blobs=16,
        max_calls=16,
        max_attempts=32,
        max_json_bytes=1024 * 1024,
        max_jsonl_line_bytes=128 * 1024,
    )


def _manifest(
    *, run_id: str = "run-0", rows: tuple[RowKey, ...] | None = None
) -> BenchmarkManifest:
    return build_manifest(
        run_id=run_id,
        corpus_sha256="1" * 64,
        analysis_code_sha256="2" * 64,
        stub=True,
        expected_rows=(_row_key(),) if rows is None else rows,
        limits=_limits(),
        parameters={
            "n_samples": 10,
            "temperature": 0.8,
            "versions": {"oracle": "oracle@0.2.0"},
        },
    )


def _blob() -> BlobRecord:
    return build_blob_record(BlobKind.TAB, {"capo": 0, "notes": []})


def _row(
    *,
    run_id: str = "run-0",
    key: RowKey | None = None,
    observations: tuple[ObservationKey, ...] = (),
    blobs: tuple[BlobRecord, ...] | None = None,
) -> BenchmarkRow:
    records = (_blob(),) if blobs is None else blobs
    exact_key = _row_key() if key is None else key
    return build_row(
        run_id=run_id,
        key=exact_key,
        family_id="family-0",
        cluster_id="cluster-0",
        observation_keys=observations,
        blob_refs=tuple(record.ref for record in records),
        payload={
            "critic": {"status": None},
            "initial": {"green": False},
            "proposal": {"status": "LLM_SUCCESS"},
            "source": {"notegraph_sha256": "3" * 64},
            "terminal": {"green": True},
            "work": {"logical_calls": len(observations)},
        },
    )


def _make_call(
    sink: InMemoryObservationSink,
    *,
    run_id: str = "run-0",
    item_id: str = "item-0",
) -> ObservationKey:
    call_index = sink.intent_count
    clocks = iter((1_000_000, 2_000_000))
    llm = ObservingLLM(ConstantLLM("valid"), sink, clock_ns=lambda: next(clocks))
    scopes = CallSequence(run_id, start_call_index=call_index).bind_candidate(
        item_id=item_id,
        family_id="family-0",
        cluster_id="cluster-0",
        pair_id=f"pair:{item_id}:0",
    )
    with scopes(CallStage.PROPOSAL.value, 0, 0):
        assert llm.complete(system="system", user="user", max_tokens=32) == "valid"
    intent = sink.intents_since(call_index)[0]
    return ObservationKey(intent.logical_call_id, intent.call_index)


def _report() -> FinalizedReport:
    value = {"kind": "test-report"}
    digest = canonical_sha256(BENCHMARK_REPORT_VERSION, value)
    return FinalizedReport(
        canonical_json_bytes(value),
        f"# Test report\n\n- Report digest: `{digest}`\n\n琴。\n".encode(),
    )


def _bound_report(inputs: FinalizationInputs | ReplayBundle) -> FinalizedReport:
    manifest = inputs.manifest
    receipt = inputs.receipt
    value: dict[str, object] = {
        "schema": BENCHMARK_REPORT_VERSION,
        "run_id": manifest.run_id,
        "input_bindings": {
            "run_id": receipt.run_id,
            "manifest_sha256": manifest_sha256(manifest),
            "config_sha256": receipt.config_sha256,
            "receipt_sha256": receipt_sha256(receipt),
            "receipt_status": receipt.status.value,
            "corpus_sha256": receipt.corpus_sha256,
            "analysis_code_sha256": receipt.analysis_code_sha256,
            "journal_sha256": receipt.journal_sha256,
            "rows_sha256": receipt.rows_sha256,
            "blobs_sha256": receipt.blobs_sha256,
            "observations_sha256": receipt.observations_sha256,
            "expected_rows": receipt.expected_rows,
            "observed_rows": receipt.observed_rows,
            "maximum_calls": receipt.maximum_calls,
            "observed_calls": receipt.observed_calls,
            "row_count": len(inputs.rows),
            "blob_count": len(inputs.blobs),
            "logical_call_count": len(inputs.observations.calls_json),
        },
    }
    digest = canonical_sha256(BENCHMARK_REPORT_VERSION, value)
    return FinalizedReport(
        canonical_json_bytes(value),
        f"# Bound report\n\n- Report digest: `{digest}`\n".encode(),
    )


def _finalized_run(
    output: Path,
    *,
    run_id: str = "run-0",
) -> tuple[BenchmarkManifest, BenchmarkReceipt, BenchmarkRow, BlobRecord]:
    manifest = _manifest(run_id=run_id)
    blob = _blob()
    with ArtifactStore.create(output, manifest) as store:
        observation = _make_call(store.sink, run_id=run_id)
        row = _row(run_id=run_id, observations=(observation,), blobs=(blob,))
        store.commit_unit(0, row, (blob,))
        receipt = store.finalize()
    return manifest, receipt, row, blob


def _load_from_canonical(canonical: Path) -> ReplayBundle:
    return load_replay_bundle(
        canonical / "config.json",
        canonical / "receipt.json",
        canonical / "rows.jsonl",
        canonical / "blobs.jsonl",
        canonical / "observations.json",
    )


def test_canonical_json_reader_requires_the_input_bytes_themselves_to_be_canonical() -> None:
    expected = {"a": 1, "b": [True, "琴"]}
    encoded = canonical_json_bytes(expected)

    assert parse_canonical_json_bytes(encoded) == expected
    for malformed in (
        b'{"b":[true,"\xe7\x90\xb4"],"a":1}',
        b'{"a":1, "b":[true,"\xe7\x90\xb4"]}',
        b'{"a":1,"a":1}',
        encoded + b"\n",
        encoded + b"{}",
        b'{"a":NaN}',
        b'{"a":1e0}',
    ):
        with pytest.raises(ArtifactError) as caught:
            parse_canonical_json_bytes(malformed)
        assert caught.value.code in {ArtifactCode.INVALID_INPUT, ArtifactCode.NON_CANONICAL}


def test_canonical_json_reader_rejects_invalid_utf8_and_input_over_limit() -> None:
    with pytest.raises(ArtifactError, match="UTF-8"):
        parse_canonical_json_bytes(b'"\xff"')
    with pytest.raises(ArtifactError) as caught:
        parse_canonical_json_bytes(b"{}", max_bytes=1)
    assert caught.value.code is ArtifactCode.LIMIT_EXCEEDED


def test_canonical_jsonl_has_one_canonical_value_per_lf_terminated_line() -> None:
    values = ({"b": 2}, [1, 2], "琴")
    encoded = canonical_jsonl_bytes(values)

    assert encoded == b'{"b":2}\n[1,2]\n"\xe7\x90\xb4"\n'
    assert parse_canonical_jsonl_bytes(encoded) == values
    for malformed in (
        encoded[:-1],
        b"\n",
        b'{"b":2}\n\n',
        b'{"b":2} trailing\n',
        b'{"b":2,"b":3}\n',
    ):
        with pytest.raises(ArtifactError):
            parse_canonical_jsonl_bytes(malformed)


def test_canonical_jsonl_bounds_lines_and_each_physical_line() -> None:
    encoded = canonical_jsonl_bytes(({"a": 1}, {"b": 2}))
    with pytest.raises(ArtifactError) as caught:
        parse_canonical_jsonl_bytes(encoded, max_lines=1)
    assert caught.value.code is ArtifactCode.LIMIT_EXCEEDED
    with pytest.raises(ArtifactError) as caught:
        parse_canonical_jsonl_bytes(encoded, max_line_bytes=6)
    assert caught.value.code is ArtifactCode.LIMIT_EXCEEDED


def test_manifest_round_trip_is_typed_immutable_and_domain_hashed() -> None:
    manifest = _manifest()
    wire = manifest_to_dict(manifest)

    assert wire["schema"] == BENCHMARK_MANIFEST_VERSION
    assert manifest_from_dict(wire) == manifest
    assert manifest_sha256(manifest) == canonical_sha256(BENCHMARK_MANIFEST_VERSION, wire)
    assert manifest.parameters == {
        "n_samples": 10,
        "temperature": 0.8,
        "versions": {"oracle": "oracle@0.2.0"},
    }
    changed = wire | {"unknown": True}
    with pytest.raises(ArtifactError, match="exact keys"):
        manifest_from_dict(changed)


def test_manifest_snapshots_caller_parameters_before_hashing() -> None:
    parameters: dict[str, object] = {"nested": {"value": 1}}
    manifest = build_manifest(
        run_id="run-0",
        corpus_sha256="1" * 64,
        analysis_code_sha256="2" * 64,
        stub=False,
        expected_rows=(_row_key(),),
        limits=_limits(),
        parameters=parameters,
    )
    before = manifest_sha256(manifest)
    parameters["nested"] = {"value": 2}

    assert manifest_sha256(manifest) == before
    assert manifest.parameters == {"nested": {"value": 1}}


def test_blob_record_round_trip_binds_kind_length_and_canonical_content() -> None:
    record = _blob()
    wire = blob_record_to_dict(record)

    assert blob_record_from_dict(wire) == record
    assert record.ref.byte_length == len(canonical_json_bytes(record.content))
    assert record.ref.sha256 == canonical_blob_sha256(record.ref.kind, record.content)
    with pytest.raises(ArtifactError, match="digest"):
        blob_record_from_dict(wire | {"sha256": "0" * 64})


@pytest.mark.parametrize(
    ("row_type", "payload"),
    [
        (
            RowType.CANDIDATE,
            {
                "critic": {},
                "initial": {},
                "proposal": {},
                "source": {},
                "terminal": {},
                "work": {},
            },
        ),
        (RowType.RAW, {"outcome": {}, "score": {}, "source": {}}),
        (
            RowType.PURE_SOLVER,
            {"baseline": {}, "outcome": {}, "score": {}, "source": {}},
        ),
    ],
)
def test_row_variants_round_trip_with_exact_payload_key_contracts(
    row_type: RowType,
    payload: dict[str, object],
) -> None:
    index = None if row_type is RowType.PURE_SOLVER else 0
    key = RowKey(row_type, "item-0", index, index, f"pair:{row_type.value}")
    row = build_row(
        run_id="run-0",
        key=key,
        family_id="family-0",
        cluster_id="cluster-0",
        observation_keys=(),
        blob_refs=(),
        payload=payload,
    )

    assert row_from_dict(row_to_dict(row)) == row
    bad = row_to_dict(row)
    bad_payload = dict(bad["payload"])  # type: ignore[arg-type]
    bad_payload["unknown"] = None
    bad["payload"] = bad_payload
    with pytest.raises(ArtifactError, match="payload exact keys"):
        row_from_dict(bad)


def test_table_hash_is_domain_separated_from_an_individual_row_hash() -> None:
    encoded = canonical_jsonl_bytes((row_to_dict(_row()),))
    expected = hashlib.sha256(b"fretsure:benchmark-row-table@0.1.0\0" + encoded).hexdigest()

    assert canonical_table_sha256("rows", encoded) == expected
    assert canonical_table_sha256("blobs", encoded) != expected


def test_durable_sink_appends_one_fsynced_hash_chained_event_per_observation_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = tmp_path / "journal.jsonl"
    journal.touch(mode=0o600)
    calls: list[int] = []
    real_fsync = os.fsync

    def observed_fsync(descriptor: int) -> None:
        calls.append(descriptor)
        real_fsync(descriptor)

    monkeypatch.setattr(artifacts.os, "fsync", observed_fsync)
    with DurableObservationSink(journal, max_calls=4) as sink:
        _make_call(sink)
        final_hash = sink.final_event_sha256

    events = parse_canonical_jsonl_bytes(journal.read_bytes())
    assert [event["event_type"] for event in events] == [  # type: ignore[index]
        "CALL_INTENT",
        "ATTEMPT_INTENT",
        "ATTEMPT_RESULT",
        "CALL_RESULT",
    ]
    assert events[0]["previous_event_sha256"] == "0" * 64  # type: ignore[index]
    for previous, current in zip(events, events[1:], strict=False):
        previous_hash = artifacts.wal_event_sha256(previous)
        assert current["previous_event_sha256"] == previous_hash  # type: ignore[index]
    assert final_hash == artifacts.wal_event_sha256(events[-1])
    assert len(calls) >= len(events)


def test_durable_sink_replays_constant_time_counts_and_suffixes(tmp_path: Path) -> None:
    journal = tmp_path / "suffixes.jsonl"
    journal.touch(mode=0o600)
    with DurableObservationSink(journal, max_calls=2) as sink:
        _make_call(sink)
        _make_call(sink)

    with DurableObservationSink(journal, max_calls=2, resume=True) as resumed:
        assert (
            resumed.intent_count
            == resumed.result_count
            == resumed.attempt_intent_count
            == resumed.attempt_result_count
            == 2
        )
        assert [value.call_index for value in resumed.intents_since(1)] == [1]
        assert [value.call_index for value in resumed.results_since(1)] == [1]
        assert [value.call_index for value in resumed.attempt_intents_since(1)] == [1]
        assert [value.call_index for value in resumed.attempt_results_since(1)] == [1]


def test_durable_sink_resume_rejects_partial_or_corrupt_hash_chain(tmp_path: Path) -> None:
    journal = tmp_path / "journal.jsonl"
    journal.touch(mode=0o600)
    with DurableObservationSink(journal, max_calls=4) as sink:
        _make_call(sink)
    journal.write_bytes(journal.read_bytes() + b"{")

    with pytest.raises(ArtifactError) as caught:
        DurableObservationSink(journal, max_calls=4, resume=True)
    assert caught.value.code is ArtifactCode.CORRUPT_JOURNAL


def test_durable_sink_enforces_global_attempt_limit_on_fresh_and_resume(
    tmp_path: Path,
) -> None:
    seed = InMemoryObservationSink(max_calls=1)
    _make_call(seed)
    first_intent = seed.attempt_intents[0]
    first_result = replace(seed.attempt_results[0], status="failed", retryable=True)
    second_intent = replace(
        first_intent,
        attempt_id="attempt:0:1",
        attempt_index=1,
    )
    second_result = replace(
        seed.attempt_results[0],
        attempt_id="attempt:0:1",
        attempt_index=1,
    )

    limited_path = tmp_path / "limited.jsonl"
    limited_path.touch(mode=0o600)
    with DurableObservationSink(
        limited_path,
        max_calls=1,
        max_attempts=1,
    ) as limited:
        limited.write_intent(seed.intents[0])
        limited.write_attempt_intent(first_intent)
        limited.write_attempt_result(first_result)
        with pytest.raises(ArtifactError) as caught:
            limited.write_attempt_intent(second_intent)
        assert caught.value.code is ArtifactCode.LIMIT_EXCEEDED

    replay_path = tmp_path / "replay.jsonl"
    replay_path.touch(mode=0o600)
    with DurableObservationSink(
        replay_path,
        max_calls=1,
        max_attempts=2,
    ) as source:
        source.write_intent(seed.intents[0])
        source.write_attempt_intent(first_intent)
        source.write_attempt_result(first_result)
        source.write_attempt_intent(second_intent)
        source.write_attempt_result(second_result)
        source.write_result(seed.results[0])

    with pytest.raises(ArtifactError) as caught:
        DurableObservationSink(
            replay_path,
            max_calls=1,
            max_attempts=1,
            resume=True,
        )
    assert caught.value.code is ArtifactCode.CORRUPT_JOURNAL


def test_durable_sink_reserves_tokens_bytes_and_one_complete_unit_before_intent(
    tmp_path: Path,
) -> None:
    seed = InMemoryObservationSink(max_calls=1)
    _make_call(seed)
    journal = tmp_path / "budget.jsonl"
    journal.touch(mode=0o600)
    reservation = CompleteUnitReservation(
        logical_calls=1,
        attempts=1,
        requested_output_tokens=32,
        attempt_reserved_output_tokens=32,
        response_text_bytes=1024,
        transport_response_bytes=1024 * 1024,
        wall_microseconds=1,
    )
    with DurableObservationSink(
        journal,
        max_calls=1,
        max_attempts=1,
        max_requested_output_tokens=31,
        max_attempt_reserved_output_tokens=32,
        max_response_text_bytes=1024,
        max_transport_response_bytes=1024 * 1024,
        max_wall_microseconds=1,
        complete_unit_reservation=reservation,
    ) as sink:
        with pytest.raises(ArtifactError) as caught:
            sink.write_intent(seed.intents[0])
    assert caught.value.code is ArtifactCode.LIMIT_EXCEEDED
    assert journal.read_bytes() == b""


def test_durable_sink_records_returned_model_mismatch_as_terminal_failure(
    tmp_path: Path,
) -> None:
    seed = InMemoryObservationSink(max_calls=1)
    _make_call(seed)
    provider = ProviderObservation(
        available=True,
        status="succeeded",
        attempts=1,
        retries=0,
        returned_model_id="returned-other-model",
        response_id_sha256=None,
        input_tokens=1,
        output_tokens=1,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
    )
    journal = tmp_path / "model.jsonl"
    journal.touch(mode=0o600)
    with DurableObservationSink(
        journal,
        max_calls=1,
        max_attempts=1,
        allowed_returned_model_id="requested-model",
    ) as sink:
        sink.write_intent(seed.intents[0])
        sink.write_attempt_intent(seed.attempt_intents[0])
        sink.write_attempt_result(seed.attempt_results[0])
        with pytest.raises(ArtifactError) as caught:
            sink.write_result(replace(seed.results[0], provider=provider))
    assert caught.value.code is ArtifactCode.HASH_MISMATCH
    events = parse_canonical_jsonl_bytes(journal.read_bytes())
    terminal = events[-1]["payload"]  # type: ignore[index]
    assert terminal["status"] == "failed"  # type: ignore[index]
    assert terminal["failure_code"] == CallFailureCode.RETURNED_MODEL_MISMATCH.value  # type: ignore[index]


@pytest.mark.parametrize("provider_available", [False, True])
def test_durable_live_sink_requires_non_null_provider_model_evidence(
    tmp_path: Path,
    provider_available: bool,
) -> None:
    seed = InMemoryObservationSink(max_calls=1)
    _make_call(seed)
    provider = (
        ProviderObservation(
            available=True,
            status="succeeded",
            attempts=1,
            retries=0,
            returned_model_id=None,
            response_id_sha256=None,
            input_tokens=1,
            output_tokens=1,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
        )
        if provider_available
        else seed.results[0].provider
    )
    journal = tmp_path / f"missing-provider-{provider_available}.jsonl"
    journal.touch(mode=0o600)

    with DurableObservationSink(
        journal,
        max_calls=1,
        max_attempts=1,
        allowed_returned_model_id="requested-model",
        require_successful_provider_evidence=True,
    ) as sink:
        sink.write_intent(seed.intents[0])
        sink.write_attempt_intent(seed.attempt_intents[0])
        sink.write_attempt_result(seed.attempt_results[0])
        with pytest.raises(ArtifactError) as caught:
            sink.write_result(replace(seed.results[0], provider=provider))

    assert caught.value.code is ArtifactCode.HASH_MISMATCH
    terminal = parse_canonical_jsonl_bytes(journal.read_bytes())[-1]["payload"]  # type: ignore[index]
    assert terminal["status"] == "failed"  # type: ignore[index]
    assert terminal["failure_code"] == CallFailureCode.PROVIDER_METADATA_INVALID.value  # type: ignore[index]


def test_durable_live_sink_rejects_missing_provider_evidence_during_resume(
    tmp_path: Path,
) -> None:
    seed = InMemoryObservationSink(max_calls=1)
    _make_call(seed)
    journal = tmp_path / "resume-missing-provider.jsonl"
    journal.touch(mode=0o600)
    with DurableObservationSink(journal, max_calls=1, max_attempts=1) as source:
        source.write_intent(seed.intents[0])
        source.write_attempt_intent(seed.attempt_intents[0])
        source.write_attempt_result(seed.attempt_results[0])
        source.write_result(seed.results[0])

    with pytest.raises(ArtifactError) as caught:
        DurableObservationSink(
            journal,
            max_calls=1,
            max_attempts=1,
            allowed_returned_model_id="requested-model",
            require_successful_provider_evidence=True,
            resume=True,
        )

    assert caught.value.code is ArtifactCode.CORRUPT_JOURNAL


@pytest.mark.parametrize("failed_call", [False, True])
def test_durable_sink_rechecks_usage_ceiling_during_resume(
    tmp_path: Path,
    failed_call: bool,
) -> None:
    seed = InMemoryObservationSink(max_calls=1)
    _make_call(seed)
    provider = ProviderObservation(
        available=True,
        status="succeeded",
        attempts=1,
        retries=0,
        returned_model_id="requested-model",
        response_id_sha256=None,
        input_tokens=1,
        output_tokens=11,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    result = replace(seed.results[0], provider=provider)
    if failed_call:
        result = replace(
            result,
            status="failed",
            reply_sha256=None,
            failure_code=CallFailureCode.INVALID_REPLY,
        )
    journal = tmp_path / f"resume-usage-ceiling-{failed_call}.jsonl"
    journal.touch(mode=0o600)
    with DurableObservationSink(journal, max_calls=1, max_attempts=1) as source:
        source.write_intent(seed.intents[0])
        source.write_attempt_intent(seed.attempt_intents[0])
        source.write_attempt_result(seed.attempt_results[0])
        source.write_result(result)

    with pytest.raises(ArtifactError) as caught:
        DurableObservationSink(
            journal,
            max_calls=1,
            max_attempts=1,
            billable_token_ceiling_per_attempt={
                "cache_creation_input_tokens": 10,
                "cache_read_input_tokens": 10,
                "input_tokens": 10,
                "output_tokens": 10,
            },
            resume=True,
        )

    assert caught.value.code is ArtifactCode.CORRUPT_JOURNAL
    cause = caught.value.__cause__
    assert isinstance(cause, ArtifactError)
    assert cause.field == "journal.provider.output_tokens"


@pytest.mark.parametrize(
    "pre_call_schema",
    ["benchmark-pre-call-config@0.3.0", "benchmark-pre-call-config@0.4.0"],
)
def test_artifact_store_enforces_formal_per_attempt_usage_ceilings(
    tmp_path: Path,
    pre_call_schema: str,
) -> None:
    base = _manifest()
    ceilings = {
        "cache_creation_input_tokens": 10,
        "cache_read_input_tokens": 10,
        "input_tokens": 10,
        "output_tokens": 10,
    }
    manifest = build_manifest(
        run_id=base.run_id,
        corpus_sha256=base.corpus_sha256,
        analysis_code_sha256=base.analysis_code_sha256,
        stub=False,
        expected_rows=base.expected_rows,
        limits=base.limits,
        parameters={
            "model": {"allowed_returned_model_id": "requested-model"},
            "pre_call": {
                "schema": pre_call_schema,
                "billing_envelope": {"wire": {"billable_token_ceiling_per_attempt": ceilings}},
            },
        },
    )
    seed = InMemoryObservationSink(max_calls=1)
    _make_call(seed)
    provider = ProviderObservation(
        available=True,
        status="succeeded",
        attempts=1,
        retries=0,
        returned_model_id="requested-model",
        response_id_sha256=None,
        input_tokens=11,
        output_tokens=1,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
    )
    output = tmp_path / "formal-usage"

    with ArtifactStore.create(output, manifest) as store:
        store.sink.write_intent(seed.intents[0])
        store.sink.write_attempt_intent(seed.attempt_intents[0])
        store.sink.write_attempt_result(seed.attempt_results[0])
        with pytest.raises(ArtifactError) as caught:
            store.sink.write_result(replace(seed.results[0], provider=provider))

    assert caught.value.code is ArtifactCode.LIMIT_EXCEEDED
    assert caught.value.field == "journal.provider.input_tokens"
    terminal = parse_canonical_jsonl_bytes((output / "journal.jsonl").read_bytes())[-1]["payload"]
    assert terminal["status"] == "failed"  # type: ignore[index]
    assert terminal["failure_code"] == CallFailureCode.PROVIDER_METADATA_INVALID.value  # type: ignore[index]


def test_durable_live_sink_accepts_billable_reasoning_above_visible_output_limit(
    tmp_path: Path,
) -> None:
    seed = InMemoryObservationSink(max_calls=1)
    _make_call(seed)
    assert seed.intents[0].max_tokens == 32
    provider = ProviderObservation(
        available=True,
        status="succeeded",
        attempts=1,
        retries=0,
        returned_model_id="requested-model",
        response_id_sha256=None,
        input_tokens=1,
        output_tokens=33,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    journal = tmp_path / "output-above-request.jsonl"
    journal.touch(mode=0o600)

    with DurableObservationSink(
        journal,
        max_calls=1,
        max_attempts=1,
        allowed_returned_model_id="requested-model",
        billable_token_ceiling_per_attempt={
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 100,
            "input_tokens": 100,
            "output_tokens": 100,
        },
    ) as sink:
        sink.write_intent(seed.intents[0])
        sink.write_attempt_intent(seed.attempt_intents[0])
        sink.write_attempt_result(seed.attempt_results[0])
        sink.write_result(replace(seed.results[0], provider=provider))

    terminal = parse_canonical_jsonl_bytes(journal.read_bytes())[-1]["payload"]
    assert terminal["status"] == "succeeded"  # type: ignore[index]
    assert terminal["provider"]["output_tokens"] == 33  # type: ignore[index]


def test_durable_live_sink_rejects_billable_output_above_envelope(
    tmp_path: Path,
) -> None:
    seed = InMemoryObservationSink(max_calls=1)
    _make_call(seed)
    provider = ProviderObservation(
        available=True,
        status="succeeded",
        attempts=1,
        retries=0,
        returned_model_id="requested-model",
        response_id_sha256=None,
        input_tokens=1,
        output_tokens=101,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    journal = tmp_path / "output-above-envelope.jsonl"
    journal.touch(mode=0o600)

    with DurableObservationSink(
        journal,
        max_calls=1,
        max_attempts=1,
        allowed_returned_model_id="requested-model",
        billable_token_ceiling_per_attempt={
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 100,
            "input_tokens": 100,
            "output_tokens": 100,
        },
    ) as sink:
        sink.write_intent(seed.intents[0])
        sink.write_attempt_intent(seed.attempt_intents[0])
        sink.write_attempt_result(seed.attempt_results[0])
        with pytest.raises(ArtifactError) as caught:
            sink.write_result(replace(seed.results[0], provider=provider))

    assert caught.value.code is ArtifactCode.LIMIT_EXCEEDED
    assert caught.value.field == "journal.provider.output_tokens"
    terminal = parse_canonical_jsonl_bytes(journal.read_bytes())[-1]["payload"]
    assert terminal["status"] == "failed"  # type: ignore[index]
    assert terminal["failure_code"] == (  # type: ignore[index]
        CallFailureCode.PROVIDER_METADATA_INVALID.value
    )


def test_durable_live_sink_rejects_failed_call_billable_output_above_envelope(
    tmp_path: Path,
) -> None:
    seed = InMemoryObservationSink(max_calls=1)
    _make_call(seed)
    provider = ProviderObservation(
        available=True,
        status="succeeded",
        attempts=1,
        retries=0,
        returned_model_id="requested-model",
        response_id_sha256=None,
        input_tokens=1,
        output_tokens=101,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    failed_result = replace(
        seed.results[0],
        status="failed",
        reply_sha256=None,
        failure_code=CallFailureCode.INVALID_REPLY,
        provider=provider,
    )
    journal = tmp_path / "failed-output-above-envelope.jsonl"
    journal.touch(mode=0o600)

    with DurableObservationSink(
        journal,
        max_calls=1,
        max_attempts=1,
        allowed_returned_model_id="requested-model",
        billable_token_ceiling_per_attempt={
            "cache_creation_input_tokens": 100,
            "cache_read_input_tokens": 100,
            "input_tokens": 100,
            "output_tokens": 100,
        },
    ) as sink:
        sink.write_intent(seed.intents[0])
        sink.write_attempt_intent(seed.attempt_intents[0])
        sink.write_attempt_result(seed.attempt_results[0])
        with pytest.raises(ArtifactError) as caught:
            sink.write_result(failed_result)

    assert caught.value.code is ArtifactCode.LIMIT_EXCEEDED
    assert caught.value.field == "journal.provider.output_tokens"
    terminal = parse_canonical_jsonl_bytes(journal.read_bytes())[-1]["payload"]
    assert terminal["status"] == "failed"  # type: ignore[index]
    assert terminal["failure_code"] == (  # type: ignore[index]
        CallFailureCode.PROVIDER_METADATA_INVALID.value
    )


def test_private_to_sanitized_observations_removes_response_id_and_preserves_usage_nulls() -> None:
    memory = InMemoryObservationSink(max_calls=4)
    _make_call(memory)
    provider = ProviderObservation(
        available=True,
        status="succeeded",
        attempts=1,
        retries=0,
        returned_model_id="model-returned",
        response_id_sha256="a" * 64,
        input_tokens=11,
        output_tokens=None,
        cache_creation_input_tokens=7,
        cache_read_input_tokens=None,
    )
    results = (replace(memory.results[0], provider=provider),)
    source = SimpleNamespace(
        intents=memory.intents,
        results=results,
        attempt_intents=memory.attempt_intents,
        attempt_results=memory.attempt_results,
    )

    private, public = sanitize_observations("run-0", source, stub=False)
    private_wire = private.to_dict()
    public_wire = public.to_dict()
    assert private_wire["calls"][0]["response_id_sha256"] == "a" * 64  # type: ignore[index]
    assert "response_id_sha256" not in public_wire["calls"][0]  # type: ignore[index]
    usage = public_wire["calls"][0]["usage"]  # type: ignore[index]
    assert usage == {
        "cache_creation_input_tokens": 7,
        "cache_read_input_tokens": None,
        "input_tokens": 11,
        "output_tokens": None,
    }
    assert public_wire["schema"] == BENCHMARK_OBSERVATIONS_VERSION


def test_stub_sanitization_makes_timing_explicitly_unavailable() -> None:
    memory = InMemoryObservationSink(max_calls=4)
    _make_call(memory)
    provider = ProviderObservation(
        available=True,
        status="succeeded",
        attempts=1,
        retries=0,
        returned_model_id="stub-returned-model",
        response_id_sha256="b" * 64,
        input_tokens=11,
        output_tokens=13,
        cache_creation_input_tokens=17,
        cache_read_input_tokens=19,
    )
    source = SimpleNamespace(
        intents=memory.intents,
        results=(replace(memory.results[0], provider=provider),),
        attempt_intents=memory.attempt_intents,
        attempt_results=memory.attempt_results,
    )

    private, public = sanitize_observations("run-0", source, stub=True)
    private_call = private.to_dict()["calls"][0]  # type: ignore[index]
    public_call = public.to_dict()["calls"][0]  # type: ignore[index]
    assert public_call["elapsed_microseconds"] is None  # type: ignore[index]
    assert public_call["returned_model_id"] == "stub-returned-model"  # type: ignore[index]
    assert public_call["usage"] == {  # type: ignore[index]
        "cache_creation_input_tokens": None,
        "cache_read_input_tokens": None,
        "input_tokens": None,
        "output_tokens": None,
    }
    assert private_call["response_id_sha256"] is None  # type: ignore[index]
    assert private_call["usage"] == public_call["usage"]  # type: ignore[index]


def test_sanitized_observations_strict_parser_round_trips_and_rejects_nested_drift() -> None:
    memory = InMemoryObservationSink(max_calls=4)
    _make_call(memory)
    _private, public = sanitize_observations("run-0", memory, stub=True)

    assert sanitized_observations_from_dict(public.to_dict()) == public

    bad_retry = public.to_dict()
    bad_retry["calls"][0]["retry_count"] = 1  # type: ignore[index]
    with pytest.raises(ArtifactError, match="attempt count"):
        sanitized_observations_from_dict(bad_retry)

    bad_usage = public.to_dict()
    bad_usage["calls"][0]["usage"]["unknown"] = 0  # type: ignore[index]
    with pytest.raises(ArtifactError, match="exact keys"):
        sanitized_observations_from_dict(bad_usage)


@pytest.mark.parametrize(
    ("json_bytes", "markdown_bytes"),
    [
        (b'{"b":2,"a":1}', b"# Report\n"),
        (canonical_json_bytes({"ok": True}), b"# Report\r\n"),
        (canonical_json_bytes({"ok": True}), "# Cafe\u0301\n".encode()),
        (canonical_json_bytes({"ok": True}), b"# Report"),
        (canonical_json_bytes({"ok": True}), b"\xff\n"),
    ],
)
def test_finalized_report_requires_canonical_json_and_utf8_nfc_lf_markdown(
    json_bytes: bytes,
    markdown_bytes: bytes,
) -> None:
    with pytest.raises(ArtifactError):
        FinalizedReport(json_bytes, markdown_bytes)


def test_finalized_report_markdown_digest_binds_the_json() -> None:
    first = {"report": "first"}
    second = {"report": "second"}
    first_digest = canonical_sha256(BENCHMARK_REPORT_VERSION, first)

    with pytest.raises(ArtifactError) as caught:
        FinalizedReport(
            canonical_json_bytes(second),
            f"# Report\n\n- Report digest: `{first_digest}`\n".encode(),
        )

    assert caught.value.code is ArtifactCode.HASH_MISMATCH


def test_artifact_store_fresh_output_modes_lock_and_no_overwrite(tmp_path: Path) -> None:
    output = tmp_path / "run"
    store = ArtifactStore.create(output, _manifest())
    try:
        assert stat.S_IMODE(output.stat().st_mode) == 0o700
        assert stat.S_IMODE((output / "config.json").stat().st_mode) == 0o600
        assert stat.S_IMODE((output / "journal.jsonl").stat().st_mode) == 0o600
        with pytest.raises(ArtifactError) as caught:
            ArtifactStore.resume(output, _manifest())
        assert caught.value.code is ArtifactCode.LOCKED
    finally:
        store.close()

    with pytest.raises(ArtifactError) as caught:
        ArtifactStore.create(output, _manifest())
    assert caught.value.code is ArtifactCode.ALREADY_EXISTS


def test_artifact_store_commits_atomic_units_resumes_and_finalizes_sorted_tables(
    tmp_path: Path,
) -> None:
    output = tmp_path / "run"
    manifest = _manifest()
    blob = _blob()
    with ArtifactStore.create(output, manifest) as store:
        observation = _make_call(store.sink)
        row = _row(observations=(observation,), blobs=(blob,))
        store.commit_unit(0, row, (blob,))

    with ArtifactStore.resume(output, manifest) as resumed:
        assert resumed.completed_unit_indices == (0,)
        assert resumed.completed_rows == (row,)
        assert len(resumed.completed_units) == 1
        assert resumed.completed_units[0].schedule_index == 0
        assert resumed.completed_units[0].row == row
        assert resumed.completed_units[0].blobs == (blob,)
        receipt = resumed.finalize()

    assert receipt.status is CompletionStatus.COMPLETE
    assert require_complete_receipt(receipt) is receipt
    canonical = output / "canonical"
    assert manifest_from_dict(read_canonical_json(canonical / "config.json")) == manifest
    assert parse_canonical_jsonl_bytes((canonical / "rows.jsonl").read_bytes()) == (
        row_to_dict(row),
    )
    assert parse_canonical_jsonl_bytes((canonical / "blobs.jsonl").read_bytes()) == (
        blob_record_to_dict(blob),
    )
    assert receipt_from_dict(read_canonical_json(canonical / "receipt.json")) == receipt
    assert not list(output.rglob("*.tmp"))


def test_artifact_store_resume_commit_uses_cached_ownership_and_is_byte_identical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    second_key = RowKey(RowType.CANDIDATE, "item-1", 0, 0, "pair:item-1:0")
    manifest = _manifest(rows=(_row_key(), second_key))
    blob = _blob()

    def commit(store: ArtifactStore, key: RowKey) -> None:
        observation = _make_call(store.sink, item_id=key.item_id)
        store.commit_unit(
            store.completed_unit_count,
            _row(key=key, observations=(observation,), blobs=(blob,)),
            (blob,),
        )

    uninterrupted = tmp_path / "uninterrupted"
    with ArtifactStore.create(uninterrupted, manifest) as store:
        commit(store, _row_key())
        commit(store, second_key)
        assert store.completed_unit_count == 2
        store.finalize()

    interrupted = tmp_path / "interrupted"
    with ArtifactStore.create(interrupted, manifest) as store:
        commit(store, _row_key())

    class NoHistoricalIteration(list[object]):
        def __iter__(self) -> NoReturn:
            raise AssertionError("commit must not rescan historical staged units")

    def snapshot_forbidden(_sink: object) -> object:
        raise AssertionError("hot paths must not construct full observation snapshots")

    with monkeypatch.context() as guarded:
        guarded.setattr(
            DurableObservationSink,
            "intents",
            property(snapshot_forbidden),
        )
        guarded.setattr(
            DurableObservationSink,
            "attempt_intents",
            property(snapshot_forbidden),
        )
        with ArtifactStore.resume(interrupted, manifest) as resumed:
            resumed._units = NoHistoricalIteration(resumed.completed_units)
            commit(resumed, second_key)
            assert resumed.completed_unit_count == 2

    with ArtifactStore.resume(interrupted, manifest) as resumed:
        resumed.finalize()

    def artifact_bytes(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    assert artifact_bytes(interrupted) == artifact_bytes(uninterrupted)


def test_artifact_store_resume_for_abort_terminalizes_unowned_calls(
    tmp_path: Path,
) -> None:
    output = tmp_path / "incomplete-run"
    manifest = _manifest()
    with ArtifactStore.create(output, manifest) as store:
        _make_call(store.sink)

    with pytest.raises(ArtifactError) as caught:
        ArtifactStore.resume(output, manifest)
    assert caught.value.code is ArtifactCode.INCOMPLETE

    with ArtifactStore.resume_for_abort(output, manifest) as terminal:
        with pytest.raises(ArtifactError) as sink_error:
            _ = terminal.sink
        assert sink_error.value.code is ArtifactCode.INCOMPLETE
        with pytest.raises(ArtifactError) as finalize_error:
            terminal.finalize()
        assert finalize_error.value.code is ArtifactCode.INCOMPLETE
        receipt = terminal.abort("concurrent_audit_fixture")

    assert receipt.status is CompletionStatus.INCOMPLETE
    assert receipt.observed_calls == 1
    assert receipt.observed_rows == 0
    assert receipt.reason_code == "concurrent_audit_fixture"
    assert (output / "abort-receipt.json").is_file()


def test_report_callback_failure_publishes_nothing_and_same_inputs_can_retry(
    tmp_path: Path,
) -> None:
    output = tmp_path / "callback-retry"
    manifest = _manifest()
    blob = _blob()
    seen: list[FinalizationInputs] = []

    def callback(inputs: FinalizationInputs) -> FinalizedReport:
        seen.append(inputs)
        if len(seen) == 1:
            raise RuntimeError("injected report failure")
        return _bound_report(inputs)

    with ArtifactStore.create(output, manifest) as store:
        observation = _make_call(store.sink)
        row = _row(observations=(observation,), blobs=(blob,))
        store.commit_unit(0, row, (blob,))
        with pytest.raises(RuntimeError, match="injected report failure"):
            store.finalize(report_callback=callback)
        assert not (output / "canonical").exists()
        assert not (output / "private-observations.json").exists()

        receipt = store.finalize(report_callback=callback)

    assert seen[0] == seen[1]
    assert seen[1].manifest == manifest
    assert seen[1].receipt == receipt
    canonical = output / "canonical"
    assert {path.name for path in canonical.iterdir()} == {
        "blobs.jsonl",
        "config.json",
        "observations.json",
        "receipt.json",
        "report.json",
        "report.md",
        "rows.jsonl",
    }
    expected_report = _bound_report(seen[1])
    assert (canonical / "report.json").read_bytes() == expected_report.json_bytes
    assert (canonical / "report.md").read_bytes() == expected_report.markdown_bytes


def test_finalize_rejects_unbound_report_and_can_retry_same_inputs(tmp_path: Path) -> None:
    output = tmp_path / "unbound-report"
    manifest = _manifest()
    blob = _blob()

    with ArtifactStore.create(output, manifest) as store:
        observation = _make_call(store.sink)
        store.commit_unit(0, _row(observations=(observation,), blobs=(blob,)), (blob,))
        with pytest.raises(ArtifactError) as caught:
            store.finalize(report_callback=lambda _inputs: _report())
        assert caught.value.code is ArtifactCode.COVERAGE_MISMATCH
        assert not (output / "canonical").exists()

        store.finalize(report_callback=_bound_report)

    assert (output / "canonical" / "report.json").is_file()


def test_resume_rejects_orphan_intent_and_terminal_call_without_committed_unit(
    tmp_path: Path,
) -> None:
    seed = InMemoryObservationSink(max_calls=4)
    _make_call(seed)

    orphan_output = tmp_path / "orphan"
    orphan = ArtifactStore.create(orphan_output, _manifest())
    orphan.sink.write_intent(seed.intents[0])
    orphan.close()
    with pytest.raises(ArtifactError) as caught:
        ArtifactStore.resume(orphan_output, _manifest())
    assert caught.value.code is ArtifactCode.INCOMPLETE

    terminal_output = tmp_path / "terminal"
    terminal = ArtifactStore.create(terminal_output, _manifest())
    _make_call(terminal.sink)
    terminal.close()
    with pytest.raises(ArtifactError) as caught:
        ArtifactStore.resume(terminal_output, _manifest())
    assert caught.value.code is ArtifactCode.INCOMPLETE


def test_resume_rejects_manifest_mismatch_and_corrupt_unit(tmp_path: Path) -> None:
    output = tmp_path / "run"
    manifest = _manifest()
    blob = _blob()
    with ArtifactStore.create(output, manifest) as store:
        observation = _make_call(store.sink)
        store.commit_unit(0, _row(observations=(observation,), blobs=(blob,)), (blob,))

    with pytest.raises(ArtifactError) as caught:
        ArtifactStore.resume(output, _manifest(run_id="other-run"))
    assert caught.value.code is ArtifactCode.HASH_MISMATCH

    unit = next((output / "staging" / "units").iterdir())
    unit.write_bytes(unit.read_bytes() + b"\n")
    with pytest.raises(ArtifactError) as caught:
        ArtifactStore.resume(output, manifest)
    assert caught.value.code in {ArtifactCode.CORRUPT_UNIT, ArtifactCode.NON_CANONICAL}


def test_finalize_rejects_missing_rows_or_unresolved_blob_refs(tmp_path: Path) -> None:
    empty_output = tmp_path / "empty"
    with ArtifactStore.create(empty_output, _manifest()) as store:
        with pytest.raises(ArtifactError) as caught:
            store.finalize()
        assert caught.value.code is ArtifactCode.COVERAGE_MISMATCH

    output = tmp_path / "blob"
    blob = _blob()
    with ArtifactStore.create(output, _manifest()) as store:
        observation = _make_call(store.sink)
        with pytest.raises(ArtifactError) as caught:
            store.commit_unit(0, _row(observations=(observation,), blobs=(blob,)), ())
        assert caught.value.code is ArtifactCode.COVERAGE_MISMATCH


def test_abort_receipt_round_trip_cannot_enter_report(tmp_path: Path) -> None:
    output = tmp_path / "aborted"
    with ArtifactStore.create(output, _manifest()) as store:
        retained_sink = store.sink
        receipt = store.abort("USER_ABORT")
        for operation in (
            lambda: store.sink,
            lambda: store.commit_unit(0, _row(), (_blob(),)),
            store.finalize,
            lambda: store.abort("AGAIN"),
        ):
            with pytest.raises(ArtifactError):
                operation()
        with pytest.raises(ObservationSinkError):
            _make_call(retained_sink)

    assert receipt.status is CompletionStatus.INCOMPLETE
    assert receipt_from_dict(receipt_to_dict(receipt)) == receipt
    with pytest.raises(ArtifactError) as caught:
        require_complete_receipt(receipt)
    assert caught.value.code is ArtifactCode.INCOMPLETE
    assert not (output / "canonical").exists()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_canonical_reader_rejects_symlink_and_fifo_without_blocking(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_bytes(b"{}")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    fifo = tmp_path / "pipe.json"
    os.mkfifo(fifo)

    for path in (link, fifo):
        with pytest.raises(ArtifactError) as caught:
            read_canonical_json(path)
        assert caught.value.code is ArtifactCode.NOT_REGULAR_FILE


@pytest.mark.skipif(os.name != "posix", reason="POSIX unlink preserves an open descriptor")
def test_canonical_reader_uses_the_open_descriptor_if_path_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "replace.json"
    path.write_bytes(b'{"original":true}')
    real_open = os.open
    replaced = False

    def replace_after_open(
        path_arg: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
    ) -> int:
        nonlocal replaced
        descriptor = real_open(path_arg, flags, mode)
        if Path(path_arg) == path:
            path.unlink()
            path.write_bytes(b'{"replacement":true}')
            replaced = True
        return descriptor

    monkeypatch.setattr(artifacts.os, "open", replace_after_open)

    assert read_canonical_json(path) == {"original": True}
    assert replaced


def test_receipt_complete_contract_requires_exact_counts_and_hashes() -> None:
    receipt = BenchmarkReceipt(
        run_id="run-0",
        config_sha256="1" * 64,
        corpus_sha256="2" * 64,
        journal_sha256="3" * 64,
        rows_sha256="4" * 64,
        blobs_sha256="5" * 64,
        observations_sha256="6" * 64,
        observed_returned_models=("model-a",),
        analysis_code_sha256="7" * 64,
        expected_rows=1,
        observed_rows=1,
        maximum_calls=2,
        observed_calls=2,
        status=CompletionStatus.COMPLETE,
        reason_code=None,
    )

    assert receipt_from_dict(receipt_to_dict(receipt)) == receipt
    with pytest.raises(ArtifactError):
        replace(receipt, observed_rows=0)


def test_observation_hash_uses_the_public_observation_schema_domain() -> None:
    memory = InMemoryObservationSink(max_calls=4)
    _make_call(memory)
    _private, public = sanitize_observations("run-0", memory, stub=True)

    assert public.sha256 == canonical_sha256(
        BENCHMARK_OBSERVATIONS_VERSION,
        public.to_dict(),
    )


def test_row_hash_uses_the_frozen_row_schema_domain() -> None:
    row = _row()
    wire = row_to_dict(row)

    assert row.sha256 == canonical_sha256(BENCHMARK_ROW_VERSION, wire)


def test_noncanonical_json_parser_does_not_accept_json_decoder_extensions() -> None:
    for encoded in (b"1 2", b"Infinity", b"-Infinity", b"01", b'{"a":1}\x00'):
        with pytest.raises(ArtifactError):
            parse_canonical_json_bytes(encoded)


def test_json_reader_does_not_depend_on_text_mode_newline_translation(tmp_path: Path) -> None:
    path = tmp_path / "crlf.json"
    path.write_bytes(b'{"a":1}\r\n')

    with pytest.raises(ArtifactError):
        read_canonical_json(path)


def test_receipt_wire_rejects_unknown_keys() -> None:
    incomplete = BenchmarkReceipt(
        run_id="run-0",
        config_sha256="1" * 64,
        corpus_sha256="2" * 64,
        journal_sha256="3" * 64,
        rows_sha256=None,
        blobs_sha256=None,
        observations_sha256=None,
        observed_returned_models=(),
        analysis_code_sha256="4" * 64,
        expected_rows=1,
        observed_rows=0,
        maximum_calls=1,
        observed_calls=0,
        status=CompletionStatus.INCOMPLETE,
        reason_code="ABORTED",
    )
    wire = receipt_to_dict(incomplete)
    wire["extra"] = True

    with pytest.raises(ArtifactError, match="exact keys"):
        receipt_from_dict(wire)


def test_jsonl_table_hash_covers_line_order_and_final_lf() -> None:
    first = canonical_jsonl_bytes(({"a": 1}, {"b": 2}))
    second = canonical_jsonl_bytes(({"b": 2}, {"a": 1}))

    assert canonical_table_sha256("rows", first) != canonical_table_sha256("rows", second)
    assert canonical_table_sha256("rows", first) != canonical_table_sha256("rows", first[:-1])


def test_parse_canonical_json_rejects_oversized_integer_before_materializing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(artifacts, "MAX_JSON_NUMBER_CHARS", 8)

    with pytest.raises(ArtifactError) as caught:
        parse_canonical_json_bytes(b"123456789")
    assert caught.value.code is ArtifactCode.LIMIT_EXCEEDED


def test_artifact_store_rejects_non_directory_output_nodes(tmp_path: Path) -> None:
    regular = tmp_path / "regular"
    regular.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ArtifactError) as caught:
        ArtifactStore.resume(regular, _manifest())
    assert caught.value.code is ArtifactCode.NOT_REGULAR_FILE


def test_canonical_file_digest_matches_stored_finalized_bytes(tmp_path: Path) -> None:
    output = tmp_path / "run"
    manifest = _manifest()
    blob = _blob()
    with ArtifactStore.create(output, manifest) as store:
        observation = _make_call(store.sink)
        store.commit_unit(0, _row(observations=(observation,), blobs=(blob,)), (blob,))
        receipt = store.finalize()

    rows = (output / "canonical" / "rows.jsonl").read_bytes()
    blobs = (output / "canonical" / "blobs.jsonl").read_bytes()
    assert receipt.rows_sha256 == canonical_table_sha256("rows", rows)
    assert receipt.blobs_sha256 == canonical_table_sha256("blobs", blobs)
    assert manifest_sha256(manifest) == receipt.config_sha256


def test_load_replay_bundle_validates_hashes_and_exact_observation_coverage(
    tmp_path: Path,
) -> None:
    output = tmp_path / "source"
    manifest, receipt, row, blob = _finalized_run(output)
    canonical = output / "canonical"

    bundle = _load_from_canonical(canonical)
    assert bundle == ReplayBundle(
        manifest,
        receipt,
        (row,),
        (blob,),
        sanitized_observations_from_dict(read_canonical_json(canonical / "observations.json")),
    )

    changed_payload = row.payload
    changed_payload["terminal"] = {"green": False}
    changed_row = build_row(
        run_id=row.run_id,
        key=row.key,
        family_id=row.family_id,
        cluster_id=row.cluster_id,
        observation_keys=row.observation_keys,
        blob_refs=row.blob_refs,
        payload=changed_payload,
    )
    (canonical / "rows.jsonl").write_bytes(canonical_jsonl_bytes((row_to_dict(changed_row),)))
    with pytest.raises(ArtifactError) as caught:
        _load_from_canonical(canonical)
    assert caught.value.code is ArtifactCode.HASH_MISMATCH

    uncovered_row = build_row(
        run_id=row.run_id,
        key=row.key,
        family_id=row.family_id,
        cluster_id=row.cluster_id,
        observation_keys=(),
        blob_refs=row.blob_refs,
        payload=row.payload,
    )
    uncovered_bytes = canonical_jsonl_bytes((row_to_dict(uncovered_row),))
    (canonical / "rows.jsonl").write_bytes(uncovered_bytes)
    (canonical / "receipt.json").write_bytes(
        canonical_json_bytes(
            receipt_to_dict(
                replace(
                    receipt,
                    rows_sha256=canonical_table_sha256("rows", uncovered_bytes),
                )
            )
        )
    )
    with pytest.raises(ArtifactError) as caught:
        _load_from_canonical(canonical)
    assert caught.value.code is ArtifactCode.COVERAGE_MISMATCH


def test_load_replay_bundle_rejects_returned_model_receipt_drift(tmp_path: Path) -> None:
    output = tmp_path / "returned-model"
    _manifest_value, receipt, _row_value, _blob_value = _finalized_run(output)
    canonical = output / "canonical"
    observations_wire = read_canonical_json(canonical / "observations.json")
    observations_wire["calls"][0]["returned_model_id"] = "changed-model"  # type: ignore[index]
    changed_observations = sanitized_observations_from_dict(observations_wire)
    (canonical / "observations.json").write_bytes(
        canonical_json_bytes(changed_observations.to_dict())
    )
    (canonical / "receipt.json").write_bytes(
        canonical_json_bytes(
            receipt_to_dict(replace(receipt, observations_sha256=changed_observations.sha256))
        )
    )

    with pytest.raises(ArtifactError) as caught:
        _load_from_canonical(canonical)
    assert caught.value.code is ArtifactCode.COVERAGE_MISMATCH


def test_publish_replay_bundle_writes_exact_seven_file_canonical_directory(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    _finalized_run(source)
    source_canonical = source / "canonical"
    bundle = _load_from_canonical(source_canonical)
    destination = tmp_path / "replay"

    report = _bound_report(bundle)
    publish_replay_bundle(destination, bundle, report)

    canonical = destination / "canonical"
    assert {path.name for path in canonical.iterdir()} == {
        "blobs.jsonl",
        "config.json",
        "observations.json",
        "receipt.json",
        "report.json",
        "report.md",
        "rows.jsonl",
    }
    for name in (
        "blobs.jsonl",
        "config.json",
        "observations.json",
        "receipt.json",
        "rows.jsonl",
    ):
        assert (canonical / name).read_bytes() == (source_canonical / name).read_bytes()
    assert (canonical / "report.json").read_bytes() == report.json_bytes
    assert (canonical / "report.md").read_bytes() == report.markdown_bytes
    with pytest.raises(ArtifactError) as caught:
        publish_replay_bundle(destination, bundle, report)
    assert caught.value.code is ArtifactCode.ALREADY_EXISTS


def test_publish_replay_bundle_rename_failure_leaves_no_partial_canonical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    _finalized_run(source)
    bundle = _load_from_canonical(source / "canonical")
    destination = tmp_path / "failed-replay"
    real_rename = os.rename

    def fail_rename(_source: object, _destination: object) -> None:
        raise OSError("injected replay publish failure")

    monkeypatch.setattr(artifacts.os, "rename", fail_rename)
    with pytest.raises(OSError, match="injected replay publish failure"):
        publish_replay_bundle(destination, bundle, _bound_report(bundle))

    assert not (destination / "canonical").exists()
    assert not destination.exists()

    monkeypatch.setattr(artifacts.os, "rename", real_rename)
    report = _bound_report(bundle)
    publish_replay_bundle(destination, bundle, report)
    assert (destination / "canonical" / "report.json").read_bytes() == report.json_bytes


def test_publish_replay_bundle_initialization_failure_removes_fresh_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    _finalized_run(source)
    bundle = _load_from_canonical(source / "canonical")
    report = _bound_report(bundle)
    destination = tmp_path / "failed-initialization"
    real_mkdtemp = artifacts.tempfile.mkdtemp

    def fail_mkdtemp(*_args: object, **_kwargs: object) -> str:
        raise OSError("injected mkdtemp failure")

    monkeypatch.setattr(artifacts.tempfile, "mkdtemp", fail_mkdtemp)
    with pytest.raises(OSError, match="injected mkdtemp failure"):
        publish_replay_bundle(destination, bundle, report)
    assert not destination.exists()

    monkeypatch.setattr(artifacts.tempfile, "mkdtemp", real_mkdtemp)
    publish_replay_bundle(destination, bundle, report)
    assert (destination / "canonical" / "report.json").is_file()


def test_publish_replay_bundle_rejects_report_from_another_run(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _finalized_run(first, run_id="run-first")
    _finalized_run(second, run_id="run-second")
    first_bundle = _load_from_canonical(first / "canonical")
    second_bundle = _load_from_canonical(second / "canonical")
    destination = tmp_path / "mixed"

    with pytest.raises(ArtifactError) as caught:
        publish_replay_bundle(destination, first_bundle, _bound_report(second_bundle))

    assert caught.value.code is ArtifactCode.COVERAGE_MISMATCH
    assert not destination.exists()


def test_store_manifest_file_is_the_exact_domain_hashed_wire_value(tmp_path: Path) -> None:
    output = tmp_path / "run"
    manifest = _manifest()
    with ArtifactStore.create(output, manifest) as store:
        assert (output / "config.json").read_bytes() == canonical_json_bytes(
            manifest_to_dict(manifest)
        )
        assert store.manifest_sha256 == manifest_sha256(manifest)


def test_artifact_error_does_not_embed_untrusted_json_text() -> None:
    secret = "SECRET_UNTRUSTED_PAYLOAD"
    with pytest.raises(ArtifactError) as caught:
        parse_canonical_json_bytes(f'{{"a":"{secret}",}}'.encode())

    assert secret not in str(caught.value)


def test_json_parser_rejects_duplicate_nested_keys() -> None:
    with pytest.raises(ArtifactError, match="duplicate"):
        parse_canonical_json_bytes(b'{"a":{"x":1,"x":2}}')


def test_store_rejects_finalization_twice_without_overwriting(tmp_path: Path) -> None:
    output = tmp_path / "run"
    blob = _blob()
    with ArtifactStore.create(output, _manifest()) as store:
        retained_sink = store.sink
        observation = _make_call(store.sink)
        store.commit_unit(0, _row(observations=(observation,), blobs=(blob,)), (blob,))
        store.finalize()
        with pytest.raises(ArtifactError) as caught:
            store.finalize()
        assert caught.value.code is ArtifactCode.ALREADY_EXISTS
        with pytest.raises(ObservationSinkError):
            _make_call(retained_sink)


def test_finalize_retry_accepts_only_identical_existing_private_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "retry"
    manifest = _manifest()
    blob = _blob()
    store = ArtifactStore.create(output, manifest)
    observation = _make_call(store.sink)
    store.commit_unit(0, _row(observations=(observation,), blobs=(blob,)), (blob,))
    real_rename = os.rename

    def fail_publish(source: object, destination: object) -> None:
        del source, destination
        raise OSError("injected publish failure")

    monkeypatch.setattr(artifacts.os, "rename", fail_publish)
    with pytest.raises(OSError, match="injected publish failure"):
        store.finalize()
    sidecar = (output / "private-observations.json").read_bytes()
    store.close()

    monkeypatch.setattr(artifacts.os, "rename", real_rename)
    with ArtifactStore.resume(output, manifest) as resumed:
        receipt = resumed.finalize()

    assert receipt.status is CompletionStatus.COMPLETE
    assert (output / "private-observations.json").read_bytes() == sidecar


def test_unit_file_contains_no_private_response_identity(tmp_path: Path) -> None:
    output = tmp_path / "run"
    blob = _blob()
    with ArtifactStore.create(output, _manifest()) as store:
        observation = _make_call(store.sink)
        store.commit_unit(0, _row(observations=(observation,), blobs=(blob,)), (blob,))

    unit_bytes = next((output / "staging" / "units").iterdir()).read_bytes()
    assert b"response_id" not in unit_bytes


def test_manifest_wire_hash_is_stable_across_round_trip() -> None:
    manifest = _manifest()
    wire_bytes = canonical_json_bytes(manifest_to_dict(manifest))
    decoded = parse_canonical_json_bytes(wire_bytes)
    restored = manifest_from_dict(decoded)

    assert manifest_sha256(restored) == manifest_sha256(manifest)
    assert json.loads(wire_bytes) == manifest_to_dict(manifest)
