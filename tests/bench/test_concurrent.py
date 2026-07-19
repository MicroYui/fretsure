from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import fretsure.bench.concurrent as concurrent_module
from fretsure.bench.artifacts import (
    CompleteUnitReservation,
    DurableObservationSink,
    ObservationKey,
)
from fretsure.bench.baselines import RawObservationKey
from fretsure.bench.concurrent import (
    MAX_CONCURRENT_UNITS,
    CollectionExecutionContract,
    ConcurrentExecutionCode,
    ConcurrentExecutionError,
    ConcurrentUnitCoordinator,
    UnitPermit,
    rebase_observation_key,
    rebase_raw_observation_key,
)
from fretsure.bench.observe import (
    AttemptIntent,
    AttemptResult,
    CallFailureCode,
    CallIntent,
    CallResult,
    CallSequence,
    CallStage,
    InMemoryObservationSink,
    ObservingLLM,
)
from fretsure.bench.preregistration import BENCHMARK_COLLECTION_EXECUTION_VERSION
from fretsure.llm.client import (
    MAX_PROXY_TEXT_BYTES_PER_TOKEN,
    MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
    FakeLLM,
)

RUN_ID = "concurrent-test-run"


class _Clock:
    def __init__(self) -> None:
        self._value = 0

    def __call__(self) -> int:
        self._value += 1_000
        return self._value


def _contract(max_in_flight: int = 4) -> CollectionExecutionContract:
    return CollectionExecutionContract.preregistered(max_in_flight_units=max_in_flight)


def _reservation(max_calls: int = 10, max_tokens: int = 8) -> CompleteUnitReservation:
    attempts = max_calls * 3
    requested = max_calls * max_tokens
    return CompleteUnitReservation(
        logical_calls=max_calls,
        attempts=attempts,
        requested_output_tokens=requested,
        attempt_reserved_output_tokens=requested * 3,
        response_text_bytes=requested * MAX_PROXY_TEXT_BYTES_PER_TOKEN,
        transport_response_bytes=attempts * MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
        wall_microseconds=max_calls * 1_000_000,
    )


def _sum_reservations(
    reservations: tuple[CompleteUnitReservation, ...],
) -> CompleteUnitReservation:
    return CompleteUnitReservation(
        *(
            sum(getattr(value, field) for value in reservations)
            for field in (
                "logical_calls",
                "attempts",
                "requested_output_tokens",
                "attempt_reserved_output_tokens",
                "response_text_bytes",
                "transport_response_bytes",
                "wall_microseconds",
            )
        )
    )


def _record_calls(
    permit: UnitPermit,
    count: int,
    *,
    raw: bool = False,
) -> tuple[CallIntent | AttemptIntent | AttemptResult | CallResult, ...]:
    sequence = CallSequence(RUN_ID)
    scopes = sequence.bind_candidate(
        item_id=f"item-{permit.schedule_index}",
        family_id=f"family-{permit.schedule_index}",
        cluster_id=f"cluster-{permit.schedule_index}",
        pair_id=f"pair-{permit.schedule_index}",
    )
    llm = ObservingLLM(
        FakeLLM([f"reply-{permit.schedule_index}-{index}" for index in range(count)]),
        permit.sink,
        clock_ns=_Clock(),
    )
    for index in range(count):
        if raw:
            stage = CallStage.RAW
            ordinal = 0
        elif index == 0:
            stage = CallStage.PROPOSAL
            ordinal = 0
        elif index == count - 1 and count == 10:
            stage = CallStage.CRITIC
            ordinal = 0
        else:
            stage = CallStage.REPAIR
            ordinal = index - 1
        with scopes(stage.value, permit.schedule_index, ordinal):
            llm.complete(
                system=f"system-{permit.schedule_index}-{index}",
                user=f"user-{permit.schedule_index}-{index}",
                max_tokens=8,
                temperature=0.0,
            )
    return permit.sink.journal_events


def _sample_events() -> tuple[CallIntent | AttemptIntent | AttemptResult | CallResult, ...]:
    sink = InMemoryObservationSink(max_calls=1)
    sequence = CallSequence(RUN_ID)
    scopes = sequence.bind_candidate(
        item_id="item-open",
        family_id="family-open",
        cluster_id="cluster-open",
        pair_id="pair-open",
    )
    llm = ObservingLLM(FakeLLM(["reply"]), sink, clock_ns=_Clock())
    with scopes(CallStage.RAW.value, 0, 0):
        llm.complete(system="system", user="user", max_tokens=8)
    return sink.journal_events


def _coordinator(
    root: Path,
    reservations: tuple[CompleteUnitReservation, ...],
    *,
    maximum: int = 4,
) -> ConcurrentUnitCoordinator:
    return ConcurrentUnitCoordinator.create(
        root,
        _contract(maximum),
        run_id=RUN_ID,
        unit_reservations=reservations,
        collection_limits=_sum_reservations(reservations),
    )


def test_collection_execution_contract_matches_preregistration_and_allows_one_to_eight() -> None:
    wire = {
        "admission_order": "collection_schedule_index_ascending",
        "canonical_merge_order": ("collection_schedule_index_ascending_then_local_call_index"),
        "client_ownership": "one_agent_and_one_raw_client_per_worker",
        "completion_order": "not_semantic",
        "durability": "unit_intent_and_attempt_fsync_before_provider_request",
        "max_in_flight_units": 8,
        "protocol": BENCHMARK_COLLECTION_EXECUTION_VERSION,
        "resume_boundary": "completed_durable_unit",
    }
    assert CollectionExecutionContract.from_dict(wire).to_dict() == wire
    assert MAX_CONCURRENT_UNITS == 8

    for maximum in (0, 9, True):
        with pytest.raises(ConcurrentExecutionError) as caught:
            CollectionExecutionContract.from_dict({**wire, "max_in_flight_units": maximum})
        assert caught.value.code is ConcurrentExecutionCode.INVALID_INPUT

    with pytest.raises(ConcurrentExecutionError) as caught:
        CollectionExecutionContract.from_dict({**wire, "completion_order": "completion_order"})
    assert caught.value.code is ConcurrentExecutionCode.INVALID_INPUT


def test_reservation_sum_is_checked_before_any_coordinator_state_is_created(
    tmp_path: Path,
) -> None:
    reservations = (_reservation(2), _reservation(3))
    limits = replace(_sum_reservations(reservations), logical_calls=4)

    with pytest.raises(ConcurrentExecutionError) as caught:
        ConcurrentUnitCoordinator.create(
            tmp_path / "coordinator",
            _contract(),
            run_id=RUN_ID,
            unit_reservations=reservations,
            collection_limits=limits,
        )

    assert caught.value.code is ConcurrentExecutionCode.INVALID_INPUT
    assert caught.value.field == "collection_limits.logical_calls"
    assert not (tmp_path / "coordinator").exists()


def test_admission_is_schedule_ordered_and_enforces_configured_in_flight_limit(
    tmp_path: Path,
) -> None:
    reservations = tuple(_reservation(1) for _ in range(5))
    with _coordinator(tmp_path / "coordinator", reservations) as coordinator:
        permits = [coordinator.admit_next() for _ in range(4)]
        assert [permit.schedule_index for permit in permits] == [0, 1, 2, 3]
        assert coordinator.in_flight_indices == (0, 1, 2, 3)

        with pytest.raises(ConcurrentExecutionError) as caught:
            coordinator.admit_next()
        assert caught.value.code is ConcurrentExecutionCode.IN_FLIGHT_LIMIT

        _record_calls(permits[2], 1, raw=True)
        coordinator.mark_ready(2)
        fifth = coordinator.admit_next()
        assert fifth.schedule_index == 4
        assert coordinator.admitted_indices == (0, 1, 2, 3, 4)


def test_triple_timeout_lane_becomes_ready_with_recorded_attempt_overhead(
    tmp_path: Path,
) -> None:
    timeout_only_microseconds = 3 * 300_000_000 + 1_500_000
    reservation = CompleteUnitReservation(
        logical_calls=1,
        attempts=3,
        requested_output_tokens=8,
        attempt_reserved_output_tokens=24,
        response_text_bytes=8 * MAX_PROXY_TEXT_BYTES_PER_TOKEN,
        transport_response_bytes=3 * MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
        wall_microseconds=timeout_only_microseconds + 3 * 10_000_000,
    )
    sample = _sample_events()
    intent = cast(CallIntent, sample[0])
    attempt_intent = cast(AttemptIntent, sample[1])
    attempt_result = cast(AttemptResult, sample[2])
    call_result = cast(CallResult, sample[3])

    with _coordinator(
        tmp_path / "triple-timeout",
        (reservation,),
        maximum=1,
    ) as coordinator:
        permit = coordinator.admit_next()
        permit.sink.write_intent(intent)
        for attempt_index in range(3):
            attempt_id = f"attempt:0:{attempt_index}"
            permit.sink.write_attempt_intent(
                replace(
                    attempt_intent,
                    attempt_id=attempt_id,
                    attempt_index=attempt_index,
                )
            )
            permit.sink.write_attempt_result(
                replace(
                    attempt_result,
                    attempt_id=attempt_id,
                    attempt_index=attempt_index,
                    status="failed",
                    retryable=attempt_index < 2,
                )
            )
        permit.sink.write_result(
            replace(
                call_result,
                status="failed",
                reply_sha256=None,
                elapsed_microseconds=timeout_only_microseconds + 1,
                failure_code=CallFailureCode.DELEGATE_FAILED,
            )
        )

        ready = coordinator.mark_ready(0)

    assert ready.local_call_count == 1
    assert ready.journal_sha256 != "0" * 64


def _build_merged_journal(
    root: Path,
    completion_order: tuple[int, ...],
) -> tuple[bytes, bytes, ConcurrentUnitCoordinator]:
    actual_calls = (1, 3, 2)
    reservations = tuple(_reservation(10) for _ in actual_calls)
    coordinator = _coordinator(root, reservations, maximum=3)
    permits = [coordinator.admit_next() for _ in actual_calls]
    for permit, calls in zip(permits, actual_calls, strict=True):
        _record_calls(permit, calls, raw=permit.schedule_index == 0)

    prefix_lengths: list[int] = []
    for index in completion_order:
        coordinator.mark_ready(index, unit_artifact_sha256=f"{index + 1:064x}")
        prefix_lengths.append(len(coordinator.ready_prefix()))
    if completion_order == (2, 0, 1):
        assert prefix_lengths == [0, 1, 3]

    merged = root.parent / f"merged-{'-'.join(map(str, completion_order))}.jsonl"
    coordinator.write_merged_journal(merged)
    return (
        merged.read_bytes(),
        (root / "coordinator.jsonl").read_bytes(),
        coordinator,
    )


def test_completion_order_is_non_semantic_and_merge_rebases_all_ids_deterministically(
    tmp_path: Path,
) -> None:
    merged_a, coordinator_a, first = _build_merged_journal(
        tmp_path / "first",
        (2, 0, 1),
    )
    merged_b, coordinator_b, second = _build_merged_journal(
        tmp_path / "second",
        (0, 1, 2),
    )
    try:
        assert coordinator_a != coordinator_b
        assert merged_a == merged_b
        assert first.global_call_offset(0) == 0
        assert first.global_call_offset(1) == 1
        assert first.global_call_offset(2) == 4

        journal = tmp_path / "merged-copy.jsonl"
        journal.write_bytes(merged_a)
        with DurableObservationSink(
            journal,
            max_calls=30,
            max_attempts=90,
            resume=True,
        ) as sink:
            assert [value.call_index for value in sink.intents] == list(range(6))
            assert [value.logical_call_id for value in sink.intents] == [
                f"call:{index}" for index in range(6)
            ]
            assert [value.logical_call_id for value in sink.results] == [
                f"call:{index}" for index in range(6)
            ]
            assert [value.attempt_id for value in sink.attempt_intents] == [
                f"attempt:{index}:0" for index in range(6)
            ]
            assert [value.attempt_id for value in sink.attempt_results] == [
                f"attempt:{index}:0" for index in range(6)
            ]
            assert all(value.run_id == RUN_ID for value in sink.intents)
            assert all(value.run_id == RUN_ID for value in sink.attempt_intents)
            assert all(value.run_id == RUN_ID for value in sink.attempt_results)

        assert rebase_observation_key(ObservationKey("call:0", 0), 4) == (
            ObservationKey("call:4", 4)
        )
        assert rebase_raw_observation_key(
            RawObservationKey(RUN_ID, "call:0", 0),
            4,
        ) == RawObservationKey(RUN_ID, "call:4", 4)
    finally:
        first.close()
        second.close()


def test_raw_one_and_agent_variable_one_through_ten_calls_merge_to_contiguous_ids(
    tmp_path: Path,
) -> None:
    actual_calls = (1, *range(1, 11))
    reservations = tuple(_reservation(10) for _ in actual_calls)
    with _coordinator(
        tmp_path / "coordinator",
        reservations,
        maximum=MAX_CONCURRENT_UNITS,
    ) as coordinator:
        for index, calls in enumerate(actual_calls):
            permit = coordinator.admit_next()
            assert permit.schedule_index == index
            _record_calls(permit, calls, raw=index == 0)
            ready = coordinator.mark_ready(index)
            assert ready.local_call_count == calls

        intents = [event for event in coordinator.merged_events() if type(event) is CallIntent]
        assert len(intents) == 56
        assert [value.call_index for value in intents] == list(range(56))


def test_empty_unready_and_ready_lanes_recover_without_readmitting(
    tmp_path: Path,
) -> None:
    root = tmp_path / "coordinator"
    reservations = (_reservation(2), _reservation(2))
    coordinator = _coordinator(root, reservations, maximum=2)
    first = coordinator.admit_next()
    second = coordinator.admit_next()
    assert first.schedule_index == 0
    _record_calls(second, 2)
    coordinator.mark_ready(1)
    coordinator.close()

    resumed = ConcurrentUnitCoordinator.resume(
        root,
        _contract(2),
        run_id=RUN_ID,
        unit_reservations=reservations,
        collection_limits=_sum_reservations(reservations),
    )
    try:
        assert resumed.admitted_indices == (0, 1)
        assert list(resumed.ready_indices) == [1]
        assert resumed.in_flight_indices == (0,)
        permit = resumed.resume_permit(0)
        assert not permit.sink.intents
        _record_calls(permit, 1, raw=True)
        resumed.mark_ready(0)
        assert list(resumed.ready_indices) == [0, 1]
        assert len(resumed.ready_prefix()) == 2
        assert len([event for event in resumed.merged_events() if type(event) is CallIntent]) == 3
    finally:
        resumed.close()


def test_resume_fails_closed_when_an_unready_lane_has_terminal_observations(
    tmp_path: Path,
) -> None:
    root = tmp_path / "coordinator"
    reservations = (_reservation(1),)
    coordinator = _coordinator(root, reservations, maximum=1)
    permit = coordinator.admit_next()
    _record_calls(permit, 1, raw=True)

    with pytest.raises(ConcurrentExecutionError) as caught:
        coordinator.resume_permit(0)
    assert caught.value.code is ConcurrentExecutionCode.FAIL_CLOSED
    coordinator.close()

    with pytest.raises(ConcurrentExecutionError) as caught:
        ConcurrentUnitCoordinator.resume(
            root,
            _contract(1),
            run_id=RUN_ID,
            unit_reservations=reservations,
            collection_limits=_sum_reservations(reservations),
        )
    assert caught.value.code is ConcurrentExecutionCode.FAIL_CLOSED


def test_resume_fails_closed_when_an_admitted_lane_has_an_open_attempt(
    tmp_path: Path,
) -> None:
    root = tmp_path / "coordinator"
    reservations = (_reservation(1),)
    coordinator = _coordinator(root, reservations, maximum=1)
    permit = coordinator.admit_next()
    events = _sample_events()
    intent = cast(CallIntent, events[0])
    attempt = cast(AttemptIntent, events[1])
    permit.sink.write_intent(intent)
    permit.sink.write_attempt_intent(attempt)
    coordinator.close()

    with pytest.raises(ConcurrentExecutionError) as caught:
        ConcurrentUnitCoordinator.resume(
            root,
            _contract(1),
            run_id=RUN_ID,
            unit_reservations=reservations,
            collection_limits=_sum_reservations(reservations),
        )
    assert caught.value.code is ConcurrentExecutionCode.FAIL_CLOSED


def test_resume_rejects_corrupt_ready_lane_and_coordinator_hash_chain(
    tmp_path: Path,
) -> None:
    reservation = (_reservation(1),)
    lane_root = tmp_path / "corrupt-lane"
    coordinator = _coordinator(lane_root, reservation, maximum=1)
    permit = coordinator.admit_next()
    _record_calls(permit, 1, raw=True)
    coordinator.mark_ready(0)
    coordinator.close()
    with (lane_root / "lanes" / "00000000.jsonl").open("ab") as stream:
        stream.write(b"not-json\n")

    with pytest.raises(ConcurrentExecutionError) as caught:
        ConcurrentUnitCoordinator.resume(
            lane_root,
            _contract(1),
            run_id=RUN_ID,
            unit_reservations=reservation,
            collection_limits=_sum_reservations(reservation),
        )
    assert caught.value.code is ConcurrentExecutionCode.CORRUPT_LANE

    wal_root = tmp_path / "corrupt-coordinator"
    coordinator = _coordinator(wal_root, reservation, maximum=1)
    permit = coordinator.admit_next()
    _record_calls(permit, 1, raw=True)
    coordinator.mark_ready(0)
    coordinator.close()
    coordinator_path = wal_root / "coordinator.jsonl"
    coordinator_path.write_bytes(
        coordinator_path.read_bytes().replace(b'"sequence":1', b'"sequence":9')
    )

    with pytest.raises(ConcurrentExecutionError) as caught:
        ConcurrentUnitCoordinator.resume(
            wal_root,
            _contract(1),
            run_id=RUN_ID,
            unit_reservations=reservation,
            collection_limits=_sum_reservations(reservation),
        )
    assert caught.value.code is ConcurrentExecutionCode.CORRUPT_COORDINATOR


def test_write_merged_journal_requires_the_complete_contiguous_ready_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reservations = (_reservation(1), _reservation(1))
    with _coordinator(tmp_path / "coordinator", reservations, maximum=2) as coordinator:
        first = coordinator.admit_next()
        second = coordinator.admit_next()
        _record_calls(first, 1, raw=True)
        _record_calls(second, 1, raw=True)
        coordinator.mark_ready(1)

        with pytest.raises(ConcurrentExecutionError) as caught:
            coordinator.write_merged_journal(tmp_path / "merged.jsonl")
        assert caught.value.code is ConcurrentExecutionCode.NOT_READY
        assert not (tmp_path / "merged.jsonl").exists()

        coordinator.mark_ready(0)
        synced_directories: list[Path] = []
        monkeypatch.setattr(
            concurrent_module,
            "_fsync_directory",
            synced_directories.append,
        )
        coordinator.write_merged_journal(tmp_path / "merged.jsonl")
        assert (tmp_path / "merged.jsonl").read_bytes()
        assert synced_directories == [tmp_path]
