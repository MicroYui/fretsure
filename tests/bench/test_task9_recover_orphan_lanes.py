from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from fretsure.bench.artifacts import CompleteUnitReservation
from fretsure.bench.concurrent import CollectionExecutionContract, ConcurrentUnitCoordinator
from fretsure.bench.contracts import canonical_json_bytes
from fretsure.bench.observe import (
    AttemptIntent,
    AttemptResult,
    CallIntent,
    CallResult,
    CallSequence,
    CallStage,
    InMemoryObservationSink,
    ObservingLLM,
)
from fretsure.llm.client import (
    MAX_PROXY_TEXT_BYTES_PER_TOKEN,
    MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
    FakeLLM,
)

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "fretsure_test_task9_recover_orphan_lanes",
    ROOT / "scripts" / "task9_recover_orphan_lanes.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
recovery = cast(Any, MODULE)

RUN_ID = "recovery-test-run"
EXECUTION_SHA = "1" * 40
RECOVERY_ID = "recovery-0001"
AUTHORIZATION_ID = "user-approved-orphan-retry"


class _Clock:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> int:
        self.value += 1_000
        return self.value


def _reservation() -> CompleteUnitReservation:
    return CompleteUnitReservation(
        logical_calls=2,
        attempts=6,
        requested_output_tokens=16,
        attempt_reserved_output_tokens=48,
        response_text_bytes=16 * MAX_PROXY_TEXT_BYTES_PER_TOKEN,
        transport_response_bytes=6 * MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
        wall_microseconds=2_000_000,
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


def _events() -> tuple[CallIntent | AttemptIntent | AttemptResult | CallResult, ...]:
    sink = InMemoryObservationSink(max_calls=1)
    sequence = CallSequence(RUN_ID)
    scope = sequence.bind_candidate(
        item_id="item-private",
        family_id="family-private",
        cluster_id="cluster-private",
        pair_id="pair-private",
    )
    llm = ObservingLLM(FakeLLM(["private-reply"]), sink, clock_ns=_Clock())
    with scope(CallStage.RAW.value, 0, 0):
        llm.complete(system="private-system", user="private-user", max_tokens=8)
    return sink.journal_events


def _fixture(tmp_path: Path) -> dict[str, object]:
    output = tmp_path / "attempt"
    concurrent = output / "staging" / "concurrent"
    output.mkdir(parents=True)
    (output / "staging").mkdir()
    (output / ".writer.lock").write_bytes(b"")
    (output / "config.json").write_bytes(canonical_json_bytes({"run_id": RUN_ID}))
    (output / "journal.jsonl").write_bytes(b"")
    pre_call = tmp_path / "pre-call.json"
    gate = tmp_path / "gate.json"
    pricing = tmp_path / "pricing.json"
    envelope = tmp_path / "envelope.json"
    pre_call.write_bytes(canonical_json_bytes({"kind": "pre-call"}))
    gate.write_bytes(canonical_json_bytes({"kind": "budget-gate"}))
    pricing.write_bytes(
        canonical_json_bytes(
            {
                "ceil_each_component_per_attempt": False,
                "currency": "USD",
                "fixed_microunits_per_attempt": 0,
                "rates_microunits_per_million_tokens": {
                    "cache_creation_input_tokens": 6_250_000,
                    "cache_read_input_tokens": 500_000,
                    "input_tokens": 5_000_000,
                    "output_tokens": 30_000_000,
                },
                "token_unit": 1_000_000,
            }
        )
    )
    pricing_sha = hashlib.sha256(pricing.read_bytes()).hexdigest()
    envelope.write_bytes(
        canonical_json_bytes(
            {
                "billable_token_ceiling_per_attempt": {
                    "cache_creation_input_tokens": 272_000,
                    "cache_read_input_tokens": 272_000,
                    "input_tokens": 272_000,
                    "output_tokens": 128_000,
                },
                "pricing_contract_raw_sha256": pricing_sha,
            }
        )
    )

    reservations = (_reservation(), _reservation())
    contract = CollectionExecutionContract.preregistered(max_in_flight_units=2)
    coordinator = ConcurrentUnitCoordinator.create(
        concurrent,
        contract,
        run_id=RUN_ID,
        unit_reservations=reservations,
        collection_limits=_sum_reservations(reservations),
    )
    first = coordinator.admit_next()
    second = coordinator.admit_next()
    sample = _events()
    first.sink.write_intent(cast(CallIntent, sample[0]))
    first.sink.write_attempt_intent(cast(AttemptIntent, sample[1]))
    for event in sample:
        if type(event) is CallIntent:
            second.sink.write_intent(event)
        elif type(event) is AttemptIntent:
            second.sink.write_attempt_intent(event)
        elif type(event) is AttemptResult:
            second.sink.write_attempt_result(event)
        else:
            assert type(event) is CallResult
            second.sink.write_result(event)
    coordinator.close()
    (concurrent / "unit-artifacts").mkdir()
    artifact = concurrent / "unit-artifacts" / "00000001.json"
    artifact.write_bytes(canonical_json_bytes({"unfinished": True}))

    audit_bytes = canonical_json_bytes({"schema": "test-concurrent-audit", "status": "INCOMPLETE"})
    audit_sha = hashlib.sha256(audit_bytes).hexdigest()
    audit_name = f"concurrent-abort-audit-{audit_sha}.json"
    (output / audit_name).write_bytes(audit_bytes)
    abort_bytes = canonical_json_bytes(
        {
            "observed_calls": 7,
            "observed_rows": 3,
            "reason_code": f"concurrent_audit_{audit_sha}",
            "run_id": RUN_ID,
            "status": "INCOMPLETE",
        }
    )
    (output / "abort-receipt.json").write_bytes(abort_bytes)
    return {
        "abort_sha": hashlib.sha256(abort_bytes).hexdigest(),
        "contract": contract,
        "envelope": envelope,
        "envelope_sha": hashlib.sha256(envelope.read_bytes()).hexdigest(),
        "gate": gate,
        "gate_sha": hashlib.sha256(gate.read_bytes()).hexdigest(),
        "output": output,
        "pre_call": pre_call,
        "pre_call_sha": hashlib.sha256(pre_call.read_bytes()).hexdigest(),
        "pricing": pricing,
        "pricing_sha": pricing_sha,
        "reservations": reservations,
    }


def _plan(values: dict[str, object]) -> dict[str, object]:
    return cast(
        dict[str, object],
        recovery.build_recovery_plan(
            output_dir=values["output"],
            pre_call_config=values["pre_call"],
            expected_pre_call_sha256=values["pre_call_sha"],
            formal_budget_gate=values["gate"],
            expected_formal_budget_gate_sha256=values["gate_sha"],
            pricing_contract=values["pricing"],
            expected_pricing_sha256=values["pricing_sha"],
            formal_billing_envelope=values["envelope"],
            expected_formal_billing_envelope_sha256=values["envelope_sha"],
            expected_abort_receipt_sha256=values["abort_sha"],
            expected_execution_git_sha=EXECUTION_SHA,
            expected_run_id=RUN_ID,
            expected_control_rows=1,
            expected_active_lanes=2,
            recovery_id=RECOVERY_ID,
            authorization_id=AUTHORIZATION_ID,
        ),
    )


def test_recovery_quarantines_only_active_lanes_and_original_resume_accepts(
    tmp_path: Path,
) -> None:
    values = _fixture(tmp_path)
    output = cast(Path, values["output"])
    concurrent = output / "staging" / "concurrent"
    coordinator_before = (concurrent / "coordinator.jsonl").read_bytes()
    journal_before = (output / "journal.jsonl").read_bytes()
    plan = _plan(values)
    plan_sha = hashlib.sha256(canonical_json_bytes(plan)).hexdigest()

    result = recovery.apply_recovery(
        output_dir=output,
        plan=plan,
        expected_plan_sha256=plan_sha,
    )

    assert result["status"] == "APPLIED"
    assert not (output / "abort-receipt.json").exists()
    assert (concurrent / "coordinator.jsonl").read_bytes() == coordinator_before
    assert (output / "journal.jsonl").read_bytes() == journal_before
    root = concurrent / "orphan-recoveries" / RECOVERY_ID
    for index in (0, 1):
        assert (concurrent / "lanes" / f"{index:08d}.jsonl").read_bytes() == b""
        assert (root / "lanes" / f"{index:08d}.jsonl").stat().st_size > 0
    assert not (concurrent / "unit-artifacts" / "00000001.json").exists()
    assert (root / "unit-artifacts" / "00000001.json").exists()

    reservations = cast(tuple[CompleteUnitReservation, ...], values["reservations"])
    resumed = ConcurrentUnitCoordinator.resume(
        concurrent,
        cast(CollectionExecutionContract, values["contract"]),
        run_id=RUN_ID,
        unit_reservations=reservations,
        collection_limits=_sum_reservations(reservations),
    )
    try:
        assert resumed.in_flight_indices == (0, 1)
    finally:
        resumed.close()


def test_recovery_receipt_counts_unknown_usage_without_payloads_and_is_idempotent(
    tmp_path: Path,
) -> None:
    values = _fixture(tmp_path)
    output = cast(Path, values["output"])
    plan = _plan(values)
    plan_sha = hashlib.sha256(canonical_json_bytes(plan)).hexdigest()
    supplement = cast(dict[str, object], plan["supplement"])
    assert supplement["open_attempts"] == 1
    assert supplement["unknown_usage_attempts"] == 2
    cost = cast(dict[str, object], supplement["cost"])
    assert cost["known_microunits"] == 0
    assert cost["maximum_missing_attempt_microunits"] == 7_036_000
    assert cost["tight_upper_microunits"] == 14_072_000

    first = recovery.apply_recovery(
        output_dir=output,
        plan=plan,
        expected_plan_sha256=plan_sha,
    )
    second = recovery.apply_recovery(
        output_dir=output,
        plan=plan,
        expected_plan_sha256=plan_sha,
    )
    assert first == second
    receipt = (
        output
        / "staging"
        / "concurrent"
        / "orphan-recoveries"
        / RECOVERY_ID
        / "receipt.json"
    ).read_bytes()
    assert b"private-system" not in receipt
    assert b"private-user" not in receipt
    assert b"private-reply" not in receipt


def test_supplement_cost_prices_partial_usage_and_fixed_fee_once(
    tmp_path: Path,
) -> None:
    pricing = tmp_path / "pricing-partial.json"
    pricing.write_bytes(
        canonical_json_bytes(
            {
                "ceil_each_component_per_attempt": False,
                "currency": "USD",
                "fixed_microunits_per_attempt": 7,
                "rates_microunits_per_million_tokens": {
                    field: 3 for field in recovery.TOKEN_FIELDS
                },
                "token_unit": 10,
            }
        )
    )
    pricing_sha = hashlib.sha256(pricing.read_bytes()).hexdigest()
    envelope = tmp_path / "envelope-partial.json"
    envelope.write_bytes(
        canonical_json_bytes(
            {
                "billable_token_ceiling_per_attempt": {
                    field: 1 for field in recovery.TOKEN_FIELDS
                },
                "pricing_contract_raw_sha256": pricing_sha,
            }
        )
    )
    supplement = {
        "complete_usage_records": 0,
        "event_counts": {
            "ATTEMPT_INTENT": 3,
            "ATTEMPT_RESULT": 3,
            "CALL_INTENT": 3,
            "CALL_RESULT": 3,
        },
        "token_observations": {
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 3,
            "input_tokens": 2,
            "output_tokens": 1,
        },
        "token_totals": {
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 1,
            "input_tokens": 1,
            "output_tokens": 1,
        },
        "unknown_usage_attempts": 3,
    }

    cost = recovery._price_supplement(
        supplement,
        pricing_contract=pricing,
        expected_pricing_sha256=pricing_sha,
        formal_billing_envelope=envelope,
        expected_formal_billing_envelope_sha256=hashlib.sha256(
            envelope.read_bytes()
        ).hexdigest(),
    )

    # All three fixed fees are known. Per-field missing coverage contributes six
    # ceiling tokens, and the aggregate numerator is rounded only once.
    assert cost["known_microunits"] == 22
    assert cost["maximum_missing_attempt_microunits"] == 9
    assert cost["tight_upper_microunits"] == 24


def test_wrong_plan_hash_makes_no_mutation(tmp_path: Path) -> None:
    values = _fixture(tmp_path)
    output = cast(Path, values["output"])
    plan = _plan(values)
    abort_before = (output / "abort-receipt.json").read_bytes()
    lane_before = (
        output / "staging" / "concurrent" / "lanes" / "00000000.jsonl"
    ).read_bytes()

    with pytest.raises(recovery.RecoveryError, match="explicitly approved SHA-256"):
        recovery.apply_recovery(
            output_dir=output,
            plan=plan,
            expected_plan_sha256="f" * 64,
        )

    assert (output / "abort-receipt.json").read_bytes() == abort_before
    assert (
        output / "staging" / "concurrent" / "lanes" / "00000000.jsonl"
    ).read_bytes() == lane_before


def test_concurrent_config_drift_refuses_before_mutation(tmp_path: Path) -> None:
    values = _fixture(tmp_path)
    output = cast(Path, values["output"])
    plan = _plan(values)
    plan_sha = hashlib.sha256(canonical_json_bytes(plan)).hexdigest()
    concurrent = output / "staging" / "concurrent"
    abort_before = (output / "abort-receipt.json").read_bytes()
    lanes_before = {
        index: (concurrent / "lanes" / f"{index:08d}.jsonl").read_bytes()
        for index in (0, 1)
    }
    (concurrent / "config.json").write_bytes(canonical_json_bytes({"drifted": True}))

    with pytest.raises(recovery.RecoveryError, match="concurrent_config"):
        recovery.apply_recovery(
            output_dir=output,
            plan=plan,
            expected_plan_sha256=plan_sha,
        )

    assert (output / "abort-receipt.json").read_bytes() == abort_before
    assert {
        index: (concurrent / "lanes" / f"{index:08d}.jsonl").read_bytes()
        for index in (0, 1)
    } == lanes_before
    assert not (concurrent / "orphan-recoveries" / RECOVERY_ID).exists()


def test_plan_rejects_canonical_output_without_mutation(tmp_path: Path) -> None:
    values = _fixture(tmp_path)
    output = cast(Path, values["output"])
    (output / "canonical").mkdir()

    with pytest.raises(recovery.RecoveryError, match="already contains canonical"):
        _plan(values)

    assert (output / "abort-receipt.json").exists()


def test_prepared_partial_recovery_rolls_forward(tmp_path: Path) -> None:
    values = _fixture(tmp_path)
    output = cast(Path, values["output"])
    plan = _plan(values)
    plan_bytes = canonical_json_bytes(plan)
    plan_sha = hashlib.sha256(plan_bytes).hexdigest()
    concurrent = output / "staging" / "concurrent"
    root = concurrent / "orphan-recoveries" / RECOVERY_ID
    (root / "lanes").mkdir(parents=True)
    (root / "plan.json").write_bytes(plan_bytes)
    source = concurrent / "lanes" / "00000000.jsonl"
    archive = root / "lanes" / "00000000.jsonl"
    os.replace(source, archive)
    source.write_bytes(b"")

    result = recovery.apply_recovery(
        output_dir=output,
        plan=plan,
        expected_plan_sha256=plan_sha,
    )

    assert result["status"] == "APPLIED"
    assert archive.stat().st_size > 0
    assert source.read_bytes() == b""
    assert not (output / "abort-receipt.json").exists()
