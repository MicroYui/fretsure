from __future__ import annotations

import copy
import hashlib
import json
import os
import signal
import threading
from concurrent.futures import ALL_COMPLETED
from concurrent.futures import wait as futures_wait
from dataclasses import replace
from pathlib import Path

import pytest

import fretsure.bench.concurrent as concurrent_module
import fretsure.bench.runner as runner_module
from fretsure.bench.artifacts import CompleteUnitReservation
from fretsure.bench.concurrent import CollectionExecutionContract
from fretsure.bench.contracts import canonical_json_bytes
from fretsure.bench.precall import BenchmarkPreCallConfig
from fretsure.bench.preregistration import FORMAL_OPERATIONAL_MAX_IN_FLIGHT_UNITS
from fretsure.bench.runner import (
    BenchmarkV2Config,
    BenchmarkV2Context,
    replay_benchmark_v2,
)


def _operational_pre_call() -> BenchmarkPreCallConfig:
    """Small runtime double carrying the exact frozen operational fields."""

    contract = CollectionExecutionContract.preregistered(
        max_in_flight_units=FORMAL_OPERATIONAL_MAX_IN_FLIGHT_UNITS
    ).to_dict()
    return BenchmarkPreCallConfig(
        canonical_json_bytes(
            {
                "billing_envelope": {
                    "wire": {
                        "billable_token_ceiling_per_attempt": {
                            "cache_creation_input_tokens": 272_000,
                            "cache_read_input_tokens": 272_000,
                            "input_tokens": 272_000,
                            "output_tokens": 128_000,
                        }
                    }
                },
                "collection_execution": {
                    "contract": contract,
                    "request_timeout_seconds": 300.0,
                },
                "model": {
                    "allowed_returned_model_id": "gpt-5.6-sol",
                    "requested_model_id": "gpt-5.6-sol",
                },
                "preregistration": {
                    "budgets": {
                        "provider_policy": {
                            "recorded_attempt_elapsed_overhead_seconds": 10.0,
                        }
                    }
                },
                "run_id": "runner-concurrent-operational-test",
                "schema": "benchmark-pre-call-config@0.4.0",
            }
        )
    )


def _live_like_context(pre_call: BenchmarkPreCallConfig) -> BenchmarkV2Context:
    base = runner_module.build_benchmark_v2_context(
        BenchmarkV2Config(
            family_count=1,
            bars=1,
            bootstrap_repetitions=11,
            sign_flip_draws=11,
            requested_model_id=pre_call.requested_model_id,
            run_id=pre_call.run_id,
        )
    )
    provisional = replace(
        base,
        config=replace(base.config, stub=False),
        requested_model_id=pre_call.requested_model_id,
        pre_call_config=pre_call,
    )
    reservations = tuple(
        runner_module._scheduled_unit_reservation(
            provisional,
            index,
            request_timeout_seconds=pre_call.request_timeout_seconds,
            recorded_attempt_elapsed_overhead_seconds=(
                pre_call.recorded_attempt_elapsed_overhead_seconds
            ),
        )
        for index in range(len(provisional.plan.collection_schedule))
    )
    reservation_fields = (
        "logical_calls",
        "attempts",
        "requested_output_tokens",
        "attempt_reserved_output_tokens",
        "response_text_bytes",
        "transport_response_bytes",
        "wall_microseconds",
    )
    maximum_reservation = CompleteUnitReservation(
        *(max(getattr(value, field) for value in reservations) for field in reservation_fields)
    )
    parameters = copy.deepcopy(base.manifest.parameters)
    parameters["execution"]["mode"] = "live"  # type: ignore[index]
    parameters["model"] = {
        "allowed_returned_model_id": pre_call.allowed_returned_model_id,
        "requested_model_id": pre_call.requested_model_id,
        "returned_model_rule": "exact_equal",
    }
    parameters["pre_call"] = pre_call.to_dict()
    manifest = replace(
        base.manifest,
        stub=False,
        limits=replace(
            base.manifest.limits,
            complete_unit_reservation=maximum_reservation,
        ),
        parameters_json=canonical_json_bytes(parameters),
    )
    return replace(provisional, manifest=manifest)


def _canonical_bytes(path: Path) -> dict[str, bytes]:
    return {value.name: value.read_bytes() for value in sorted((path / "canonical").iterdir())}


def _install_live_like_context(
    monkeypatch: pytest.MonkeyPatch,
    context: BenchmarkV2Context,
) -> None:
    monkeypatch.setattr(
        runner_module,
        "build_benchmark_v2_live_context",
        lambda _config: context,
    )
    monkeypatch.setattr(
        runner_module,
        "benchmark_v2_context_from_manifest",
        lambda manifest: replace(context, manifest=manifest),
    )
    # Kernel fsync ordering is covered by the coordinator/store unit tests. These
    # runner tests exercise restart state transitions without paying hundreds of
    # physical syncs for the fixed 20-unit minimum plan.
    monkeypatch.setattr(runner_module.os, "fsync", lambda _descriptor: None)
    original = runner_module.ObservingLLM

    def deterministic_observing_llm(
        delegate: object,
        sink: object,
        *,
        request_guard: object = None,
    ) -> object:
        return original(
            delegate,  # type: ignore[arg-type]
            sink,  # type: ignore[arg-type]
            clock_ns=lambda: 0,
            request_guard=request_guard,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(runner_module, "ObservingLLM", deterministic_observing_llm)


class _FailingClient:
    def __init__(self, model_id: str, probe: _ConcurrencyProbe | None = None) -> None:
        self._model_id = model_id
        self._probe = probe
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
        if self._probe is not None:
            self._probe.enter_call()
        try:
            raise RuntimeError("deterministic live-like failure")
        finally:
            if self._probe is not None:
                self._probe.leave_call()

    def close(self) -> None:
        self.closes += 1


class _ConcurrencyProbe:
    def __init__(self, worker_count: int) -> None:
        self._lock = threading.Lock()
        self._first_wave = threading.Barrier(worker_count)
        self._worker_count = worker_count
        self._entered = 0
        self.active = 0
        self.peak = 0

    def enter_call(self) -> None:
        with self._lock:
            ordinal = self._entered
            self._entered += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
        if ordinal < self._worker_count:
            self._first_wave.wait(timeout=5)

    def leave_call(self) -> None:
        with self._lock:
            self.active -= 1


class _InjectedDisconnect(BaseException):
    """Model process loss: cleanup handlers do not get to make the run terminal."""


def _factory(
    model_id: str,
    created: list[_FailingClient],
    probe: _ConcurrencyProbe | None = None,
):
    def make() -> _FailingClient:
        client = _FailingClient(model_id, probe)
        created.append(client)
        return client

    return make


def _collect(
    context: BenchmarkV2Context,
    output_dir: Path,
    created: list[_FailingClient],
    *,
    resume: bool = False,
    probe: _ConcurrencyProbe | None = None,
) -> None:
    pre_call = context.pre_call_config
    assert pre_call is not None
    factory = _factory(pre_call.requested_model_id, created, probe)
    with runner_module._deferred_operational_sigint() as stop_requested:
        result = runner_module._collect_operational_concurrent(
            context=context,
            output_dir=output_dir,
            resume=resume,
            agent_llm_factory=factory,
            raw_llm_factory=factory,
            stop_requested=stop_requested,
        )
    assert result.report is None


def test_operational_unit_reservation_includes_bound_attempt_elapsed_overhead() -> None:
    pre_call = _operational_pre_call()
    context = _live_like_context(pre_call)
    raw_index = next(
        index
        for index, unit in enumerate(context.plan.collection_schedule)
        if unit.arm.value == "raw"
    )

    reservation = runner_module._scheduled_unit_reservation(
        context,
        raw_index,
        request_timeout_seconds=pre_call.request_timeout_seconds,
        recorded_attempt_elapsed_overhead_seconds=(
            pre_call.recorded_attempt_elapsed_overhead_seconds
        ),
    )

    assert reservation.attempts == 3
    assert reservation.wall_microseconds == 931_500_000


def test_operational_runner_merges_out_of_order_units_and_rebases_raw_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pre_call = _operational_pre_call()
    context = _live_like_context(pre_call)
    _install_live_like_context(monkeypatch, context)
    worker_count = pre_call.max_in_flight_units
    probe = _ConcurrencyProbe(worker_count)
    created: list[_FailingClient] = []

    original_reservation = runner_module._scheduled_unit_reservation
    bound_attempt_overheads: list[object] = []

    def record_reservation(*args: object, **kwargs: object) -> CompleteUnitReservation:
        bound_attempt_overheads.append(kwargs.get("recorded_attempt_elapsed_overhead_seconds"))
        return original_reservation(*args, **kwargs)  # type: ignore[arg-type]

    original_execute = runner_module._execute_concurrent_unit
    release = {index: threading.Event() for index in range(worker_count)}
    release[worker_count - 1].set()
    completion_order: list[int] = []

    def execute_out_of_order(*args: object, **kwargs: object):
        artifact = original_execute(*args, **kwargs)  # type: ignore[arg-type]
        schedule_index = artifact.schedule_index
        if schedule_index < worker_count:
            assert release[schedule_index].wait(timeout=5)
            completion_order.append(schedule_index)
            if schedule_index:
                release[schedule_index - 1].set()
        return artifact

    merge_order: list[int] = []
    original_append = runner_module._append_rebased_ready_unit
    original_wait = runner_module.wait
    poll_timeouts: list[float | None] = []

    def record_merge(*args: object, **kwargs: object) -> None:
        ready = args[3]
        merge_order.append(ready.schedule_index)  # type: ignore[attr-defined]
        original_append(*args, **kwargs)  # type: ignore[arg-type]

    def record_wait(
        fs: object,
        *,
        timeout: float | None,
        return_when: object,
    ):
        poll_timeouts.append(timeout)
        return original_wait(  # type: ignore[arg-type]
            fs,
            timeout=timeout,
            return_when=return_when,
        )

    monkeypatch.setattr(runner_module, "_scheduled_unit_reservation", record_reservation)
    monkeypatch.setattr(runner_module, "_execute_concurrent_unit", execute_out_of_order)
    monkeypatch.setattr(runner_module, "_append_rebased_ready_unit", record_merge)
    monkeypatch.setattr(runner_module, "wait", record_wait)

    output = tmp_path / "out-of-order"
    _collect(context, output, created, probe=probe)

    progress = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert progress[0]["version"] == "benchmark-progress@0.1.0"
    assert progress[0]["progress"]["completed_rows"] == len(context.plan.items)
    assert progress[-1]["progress"]["completed_rows"] == len(context.manifest.expected_rows)
    assert progress[-1]["eta_seconds"] == {
        "conservative": 0,
        "median": 0,
        "optimistic": 0,
    }

    assert completion_order[:worker_count] == list(reversed(range(worker_count)))
    assert merge_order == list(range(len(context.plan.collection_schedule)))
    assert len(created) == 2 * worker_count
    assert len({id(client) for client in created}) == len(created)
    assert probe.peak == worker_count
    assert probe.peak <= worker_count
    assert probe.active == 0
    assert poll_timeouts and set(poll_timeouts) == {60.0}
    assert bound_attempt_overheads
    assert set(bound_attempt_overheads) == {10.0}
    assert [client.closes for client in created] == [1] * (2 * worker_count)

    rows = [json.loads(line) for line in (output / "canonical/rows.jsonl").read_text().splitlines()]
    raw_rows = [row for row in rows if row["row_type"] == "raw"]
    raw_indices: list[int] = []
    for row in raw_rows:
        outer = row["observation_keys"]
        call = row["payload"]["outcome"]["call"]
        assert len(outer) == 1
        assert call["logical_call_id"] == outer[0]["logical_call_id"]
        assert call["call_index"] == outer[0]["call_index"]
        raw_indices.append(call["call_index"])
    assert raw_indices
    assert min(raw_indices) > 0

    replay = tmp_path / "replay"
    replay_benchmark_v2(
        config_path=output / "canonical/config.json",
        receipt_path=output / "canonical/receipt.json",
        rows_path=output / "canonical/rows.jsonl",
        blobs_path=output / "canonical/blobs.jsonl",
        observations_path=output / "canonical/observations.json",
        output_dir=replay,
    )
    assert (replay / "canonical/report.json").is_file()


def test_operational_resume_recovers_ready_artifacts_before_main_store_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pre_call = _operational_pre_call()
    context = _live_like_context(pre_call)
    _install_live_like_context(monkeypatch, context)
    interrupted = tmp_path / "interrupted-ready"
    expected = tmp_path / "expected-ready"
    original_append = runner_module._append_rebased_ready_unit
    original_wait = runner_module.wait
    injected = False

    def wait_for_whole_wave(
        fs: object,
        *,
        timeout: float | None,
        return_when: object,
    ):
        del timeout, return_when
        return futures_wait(fs, return_when=ALL_COMPLETED)  # type: ignore[arg-type]

    def stop_before_main_commit(*args: object, **kwargs: object) -> None:
        nonlocal injected
        if not injected:
            injected = True
            raise _InjectedDisconnect("injected after durable readiness")
        original_append(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(runner_module, "wait", wait_for_whole_wave)
    monkeypatch.setattr(
        runner_module,
        "_append_rebased_ready_unit",
        stop_before_main_commit,
    )
    first_clients: list[_FailingClient] = []
    with pytest.raises(_InjectedDisconnect, match="after durable readiness"):
        _collect(context, interrupted, first_clients)

    artifacts = sorted((interrupted / "staging/concurrent/unit-artifacts").glob("*.json"))
    assert len(artifacts) == pre_call.max_in_flight_units
    assert all(path.stat().st_size > 0 for path in artifacts)
    assert not (interrupted / "abort-receipt.json").exists()
    assert not (interrupted / "canonical").exists()

    monkeypatch.setattr(runner_module, "wait", original_wait)
    monkeypatch.setattr(runner_module, "_append_rebased_ready_unit", original_append)
    resumed_clients: list[_FailingClient] = []
    _collect(context, interrupted, resumed_clients, resume=True)
    expected_clients: list[_FailingClient] = []
    _collect(context, expected, expected_clients)

    assert _canonical_bytes(interrupted) == _canonical_bytes(expected)
    assert [client.closes for client in first_clients] == [1] * len(first_clients)
    assert [client.closes for client in resumed_clients] == [1] * len(resumed_clients)


def test_operational_sigint_during_main_merge_finishes_atomic_unit_then_resumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pre_call = _operational_pre_call()
    context = _live_like_context(pre_call)
    _install_live_like_context(monkeypatch, context)
    interrupted = tmp_path / "sigint-during-merge"
    expected = tmp_path / "expected-merge"
    previous_handler = signal.getsignal(signal.SIGINT)
    original_append = runner_module._append_rebased_ready_unit
    signaled = False

    def signal_during_append(*args: object, **kwargs: object) -> None:
        nonlocal signaled
        if not signaled:
            signaled = True
            os.kill(os.getpid(), signal.SIGINT)
        original_append(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        runner_module,
        "_append_rebased_ready_unit",
        signal_during_append,
    )
    first_clients: list[_FailingClient] = []
    with pytest.raises(KeyboardInterrupt):
        _collect(context, interrupted, first_clients)

    assert signaled
    assert signal.getsignal(signal.SIGINT) == previous_handler
    assert not (interrupted / "abort-receipt.json").exists()
    with runner_module.ArtifactStore.resume(interrupted, context.manifest) as store:
        assert runner_module._store_has_clean_resume_boundary(store)

    resumed_clients: list[_FailingClient] = []
    _collect(context, interrupted, resumed_clients, resume=True)
    expected_clients: list[_FailingClient] = []
    _collect(context, expected, expected_clients)

    assert _canonical_bytes(interrupted) == _canonical_bytes(expected)
    assert [client.closes for client in first_clients] == [1] * len(first_clients)


def test_operational_sigint_during_mark_ready_finishes_atomic_unit_then_resumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pre_call = _operational_pre_call()
    context = _live_like_context(pre_call)
    _install_live_like_context(monkeypatch, context)
    interrupted = tmp_path / "sigint-during-ready"
    expected = tmp_path / "expected-ready-signal"
    previous_handler = signal.getsignal(signal.SIGINT)
    original_mark_ready = runner_module.ConcurrentUnitCoordinator.mark_ready
    signaled = False

    def signal_during_mark_ready(
        coordinator: runner_module.ConcurrentUnitCoordinator,
        schedule_index: int,
        *,
        unit_artifact_sha256: str | None = None,
    ):
        nonlocal signaled
        if not signaled:
            signaled = True
            os.kill(os.getpid(), signal.SIGINT)
        return original_mark_ready(
            coordinator,
            schedule_index,
            unit_artifact_sha256=unit_artifact_sha256,
        )

    monkeypatch.setattr(
        runner_module.ConcurrentUnitCoordinator,
        "mark_ready",
        signal_during_mark_ready,
    )
    first_clients: list[_FailingClient] = []
    with pytest.raises(KeyboardInterrupt):
        _collect(context, interrupted, first_clients)

    assert signaled
    assert signal.getsignal(signal.SIGINT) == previous_handler
    assert not (interrupted / "abort-receipt.json").exists()
    with runner_module.ArtifactStore.resume(interrupted, context.manifest) as store:
        assert runner_module._store_has_clean_resume_boundary(store)

    resumed_clients: list[_FailingClient] = []
    _collect(context, interrupted, resumed_clients, resume=True)
    expected_clients: list[_FailingClient] = []
    _collect(context, expected, expected_clients)

    assert _canonical_bytes(interrupted) == _canonical_bytes(expected)
    assert [client.closes for client in first_clients] == [1] * len(first_clients)


def test_operational_sigint_during_coordinator_create_finishes_setup_then_resumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pre_call = _operational_pre_call()
    context = _live_like_context(pre_call)
    _install_live_like_context(monkeypatch, context)
    interrupted = tmp_path / "sigint-during-coordinator-create"
    expected = tmp_path / "expected-coordinator-create"
    previous_handler = signal.getsignal(signal.SIGINT)
    original_create_file = concurrent_module._create_file
    signaled = False

    def signal_after_config(path: Path, data: bytes = b"") -> None:
        nonlocal signaled
        original_create_file(path, data)
        if path.name == "config.json" and not signaled:
            signaled = True
            os.kill(os.getpid(), signal.SIGINT)

    monkeypatch.setattr(concurrent_module, "_create_file", signal_after_config)
    first_clients: list[_FailingClient] = []
    with pytest.raises(KeyboardInterrupt):
        _collect(context, interrupted, first_clients)

    assert signaled
    assert signal.getsignal(signal.SIGINT) == previous_handler
    assert (interrupted / "staging/concurrent/config.json").is_file()
    assert (interrupted / "staging/concurrent/coordinator.jsonl").is_file()
    assert not (interrupted / "abort-receipt.json").exists()

    monkeypatch.setattr(concurrent_module, "_create_file", original_create_file)
    resumed_clients: list[_FailingClient] = []
    _collect(context, interrupted, resumed_clients, resume=True)
    expected_clients: list[_FailingClient] = []
    _collect(context, expected, expected_clients)

    assert _canonical_bytes(interrupted) == _canonical_bytes(expected)
    assert [client.closes for client in first_clients] == [1] * len(first_clients)


def test_operational_sigint_after_terminal_work_preserves_completed_result() -> None:
    previous_handler = signal.getsignal(signal.SIGINT)

    with runner_module._deferred_operational_sigint() as stop_requested:
        assert not stop_requested()
        os.kill(os.getpid(), signal.SIGINT)
        assert stop_requested()

    assert signal.getsignal(signal.SIGINT) == previous_handler


def test_operational_keyboard_interrupt_drains_then_resumes_byte_identically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pre_call = _operational_pre_call()
    context = _live_like_context(pre_call)
    _install_live_like_context(monkeypatch, context)
    interrupted = tmp_path / "interrupted-keyboard"
    expected = tmp_path / "expected-keyboard"
    original_wait = runner_module.wait
    injected = False

    def interrupt_once(
        fs: object,
        *,
        timeout: float | None,
        return_when: object,
    ):
        nonlocal injected
        if not injected:
            injected = True
            raise KeyboardInterrupt
        return original_wait(  # type: ignore[arg-type]
            fs,
            timeout=timeout,
            return_when=return_when,
        )

    monkeypatch.setattr(runner_module, "wait", interrupt_once)
    first_clients: list[_FailingClient] = []
    with pytest.raises(KeyboardInterrupt):
        _collect(context, interrupted, first_clients)

    assert not (interrupted / "abort-receipt.json").exists()
    assert not (interrupted / "canonical").exists()
    monkeypatch.setattr(runner_module, "wait", original_wait)
    resumed_clients: list[_FailingClient] = []
    _collect(context, interrupted, resumed_clients, resume=True)
    expected_clients: list[_FailingClient] = []
    _collect(context, expected, expected_clients)

    assert _canonical_bytes(interrupted) == _canonical_bytes(expected)
    assert [client.closes for client in first_clients] == [1] * len(first_clients)
    assert [client.closes for client in resumed_clients] == [1] * len(resumed_clients)


def test_terminal_concurrent_abort_receipt_binds_complete_lane_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pre_call = _operational_pre_call()
    context = _live_like_context(pre_call)
    _install_live_like_context(monkeypatch, context)
    output = tmp_path / "terminal-audit"
    store = runner_module.ArtifactStore.create(output, context.manifest)
    coordinator_root = output / "staging/concurrent"
    reservations = tuple(
        runner_module._scheduled_unit_reservation(
            context,
            index,
            request_timeout_seconds=pre_call.request_timeout_seconds,
            recorded_attempt_elapsed_overhead_seconds=(
                pre_call.recorded_attempt_elapsed_overhead_seconds
            ),
        )
        for index in range(len(context.plan.collection_schedule))
    )
    contract_wire = pre_call.collection_execution_contract
    assert contract_wire is not None
    coordinator = runner_module.ConcurrentUnitCoordinator.create(
        coordinator_root,
        CollectionExecutionContract.from_dict(contract_wire),
        run_id=context.plan.run_id,
        unit_reservations=reservations,
        collection_limits=runner_module._collection_reservation_limits(context),
        lane_policy=runner_module._formal_lane_policy(pre_call),
    )
    try:
        coordinator.admit_next()
        coordinator.admit_next()
    finally:
        coordinator.close()

    try:
        main_journal_sha256 = store.sink.journal_sha256
        receipt = runner_module._abort_operational_with_lane_audit(
            store,
            context,
            coordinator_root,
            "injected_original_reason",
        )
        prefix = "concurrent_audit_"
        assert receipt.reason_code is not None
        assert receipt.reason_code.startswith(prefix)
        audit_sha256 = receipt.reason_code.removeprefix(prefix)
        audit_path = output / f"concurrent-abort-audit-{audit_sha256}.json"
        audit_bytes = audit_path.read_bytes()
        assert hashlib.sha256(audit_bytes).hexdigest() == audit_sha256
        audit = json.loads(audit_bytes)

        assert audit["reason_code"] == "injected_original_reason"
        assert audit["main_journal_sha256"] == main_journal_sha256
        assert [lane["schedule_index"] for lane in audit["lanes"]] == [0, 1]
        for lane in audit["lanes"]:
            lane_path = coordinator_root / "lanes" / f"{lane['schedule_index']:08d}.jsonl"
            lane_bytes = lane_path.read_bytes()
            assert lane["byte_length"] == len(lane_bytes)
            assert lane["raw_sha256"] == hashlib.sha256(lane_bytes).hexdigest()
        for name in ("config.json", "coordinator.jsonl"):
            bound = audit["coordinator"][name]
            data = (coordinator_root / name).read_bytes()
            assert bound == {
                "byte_length": len(data),
                "raw_sha256": hashlib.sha256(data).hexdigest(),
            }

        assert runner_module._write_private_artifact(audit_path, audit_bytes) == audit_sha256
        assert audit_path.read_bytes() == audit_bytes
    finally:
        store.close()
