from __future__ import annotations

import importlib.util
import os
import signal
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from fretsure.bench.artifacts import parse_canonical_json_bytes, parse_canonical_jsonl_bytes
from fretsure.bench.concurrent import rebase_journal_events
from fretsure.bench.contracts import canonical_json_bytes
from fretsure.bench.observe import (
    CallResult,
    CallSequence,
    ObservingLLM,
    ProviderObservation,
)

ROOT = Path(__file__).resolve().parents[2]
MODULE_SPEC = importlib.util.spec_from_file_location(
    "task9_throughput_pilot",
    ROOT / "scripts/task9_throughput_pilot.py",
)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
pilot = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = pilot
MODULE_SPEC.loader.exec_module(pilot)


class DummyClient:
    def __init__(self, model_id: str) -> None:
        self._model_id = model_id
        self.close_count = 0

    @property
    def model_id(self) -> str:
        return self._model_id

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1_024,
        temperature: float = 0.0,
    ) -> str:
        del system, user, max_tokens, temperature
        return "{}"

    def close(self) -> None:
        self.close_count += 1


def _config(
    root: Path,
    *,
    level: int = 2,
    block_index: int = 1,
    stub: bool = True,
    execution_git_sha: str = "1" * 40,
    analysis_code_sha256: str = "2" * 64,
    uv_lock_sha256: str = "3" * 64,
) -> pilot.ThroughputPilotConfig:
    return pilot.build_throughput_config(
        level=level,
        block_index=block_index,
        collection_attempt=1,
        output_dir=root,
        stub=stub,
        execution_git_sha=execution_git_sha,
        analysis_code_sha256=analysis_code_sha256,
        uv_lock_sha256=uv_lock_sha256,
    )


def _pair_factory(
    config: pilot.ThroughputPilotConfig,
    created: list[DummyClient] | None = None,
) -> Any:
    def factory() -> tuple[DummyClient, DummyClient]:
        pair = (
            DummyClient(config.execution_model_id),
            DummyClient(config.execution_model_id),
        )
        if created is not None:
            created.extend(pair)
        return pair

    return factory


def _checkpoint_only(
    _config: pilot.ThroughputPilotConfig,
    permit: Any,
    _clients: tuple[DummyClient, DummyClient],
) -> pilot.UnitCheckpoint:
    return pilot.build_unit_checkpoint(
        permit.schedule_index,
        {"test_artifact": "metadata-only"},
    )


def _empty_checkpoint(
    config: pilot.ThroughputPilotConfig,
    permit: Any,
    clients: tuple[DummyClient, DummyClient],
) -> pilot.UnitCheckpoint:
    frozen = pilot.load_frozen_inputs()
    unit = frozen.spec.schedule[permit.schedule_index]
    item = frozen.spec.items[unit.item_position]
    ticks = iter((0, 0))
    observed = ObservingLLM(clients[0], permit.sink, clock_ns=lambda: next(ticks))
    scopes = CallSequence(config.run_id).bind_candidate(
        item_id=item.item_id,
        family_id=item.family_id,
        cluster_id=item.cluster_id,
        pair_id=f"empty-pair-{permit.schedule_index}",
    )
    with scopes("raw", unit.sample_index, 0):
        observed.complete(system="system", user="user", max_tokens=1)
    return _checkpoint_only(config, permit, clients)


def _ready_for_config(
    ready_units: tuple[Any, ...],
    config: pilot.ThroughputPilotConfig,
    *,
    provider_model_id: str | None = None,
) -> tuple[Any, ...]:
    result = []
    for unit in ready_units:
        events = rebase_journal_events(
            unit.events,
            call_offset=0,
            run_id=config.run_id,
        )
        if provider_model_id is not None:
            events = tuple(
                replace(
                    event,
                    provider=ProviderObservation(
                        available=True,
                        status="succeeded",
                        attempts=1,
                        retries=0,
                        returned_model_id=provider_model_id,
                        response_id_sha256="a" * 64,
                        input_tokens=0,
                        output_tokens=0,
                        cache_creation_input_tokens=0,
                        cache_read_input_tokens=0,
                    ),
                )
                if type(event) is CallResult and event.status == "succeeded"
                else event
                for event in events
            )
        result.append(replace(unit, events=events))
    return tuple(result)


def test_out_of_order_completion_is_durable_but_summary_is_schedule_canonical(
    tmp_path: Path,
) -> None:
    root = tmp_path / "out-of-order"
    config = _config(root)
    release_zero = threading.Event()

    def execute(
        _config: pilot.ThroughputPilotConfig,
        permit: Any,
        _clients: tuple[DummyClient, DummyClient],
    ) -> pilot.UnitCheckpoint:
        if permit.schedule_index == 0:
            assert release_zero.wait(timeout=5)
            time.sleep(0.05)
        elif permit.schedule_index == 1:
            release_zero.set()
        return _empty_checkpoint(_config, permit, _clients)

    result = pilot.execute_pilot_block(
        config=config,
        output_dir=root,
        pair_factory=_pair_factory(config),
        unit_executor=execute,
    )

    events = parse_canonical_jsonl_bytes(
        (root / "staging/concurrent/coordinator.jsonl").read_bytes(),
        max_lines=16,
    )
    ready_order = [
        cast(dict[str, object], event["payload"])["schedule_index"]
        for event in events
        if event["event_type"] == "UNIT_READY"
    ]
    assert ready_order.index(1) < ready_order.index(0)
    assert tuple(unit.schedule_index for unit in result.ready_units) == tuple(range(8))
    assert result.summary.to_dict()["evidence_scope"] == "smoke"

    elapsed = cast(
        int,
        cast(dict[str, object], result.summary.to_dict()["aggregation_basis"])[
            "active_elapsed_microseconds"
        ],
    )
    rebuilt = pilot.build_summary(
        config,
        reversed(result.ready_units),
        active_elapsed_microseconds=elapsed,
        complete=True,
    )
    assert rebuilt.wire_json == result.summary.wire_json


def test_parallel_peak_reaches_level_and_every_worker_owns_one_distinct_pair(
    tmp_path: Path,
) -> None:
    root = tmp_path / "peak"
    config = _config(root, level=4)
    created: list[DummyClient] = []
    lock = threading.Lock()
    barrier = threading.Barrier(4)
    active = 0
    peak = 0
    thread_pair: dict[int, tuple[int, int]] = {}

    def execute(
        _config: pilot.ThroughputPilotConfig,
        permit: Any,
        clients: tuple[DummyClient, DummyClient],
    ) -> pilot.UnitCheckpoint:
        nonlocal active, peak
        identity = (id(clients[0]), id(clients[1]))
        thread_id = threading.get_ident()
        with lock:
            previous = thread_pair.setdefault(thread_id, identity)
            assert previous == identity
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=5)
        with lock:
            active -= 1
        return _empty_checkpoint(_config, permit, clients)

    result = pilot.execute_pilot_block(
        config=config,
        output_dir=root,
        pair_factory=_pair_factory(config, created),
        unit_executor=execute,
    )

    assert not result.paused
    assert peak == 4
    assert len(created) == 8
    assert len({id(client) for client in created}) == 8
    assert all(client.close_count == 1 for client in created)
    assert len(set(thread_pair.values())) == 4


def test_clean_resume_accumulates_elapsed_and_cost_instead_of_resetting(
    tmp_path: Path,
) -> None:
    root = tmp_path / "resume"
    config = _config(root)
    first_ticks = iter((0, 5_000_000_000))
    paused = pilot.execute_pilot_block(
        config=config,
        output_dir=root,
        pause_after_units=2,
        clock_ns=lambda: next(first_ticks),
    )
    assert paused.paused
    paused_wire = paused.summary.to_dict()
    paused_cost = cast(
        int,
        cast(
            dict[str, object],
            cast(dict[str, object], paused_wire["cost"])["tight_upper"],
        )["microunits"],
    )
    assert paused_cost > 0

    second_ticks = iter((10_000_000_000, 12_000_000_000))
    finished = pilot.execute_pilot_block(
        config=config,
        output_dir=root,
        resume=True,
        clock_ns=lambda: next(second_ticks),
    )
    wire = finished.summary.to_dict()
    throughput = cast(dict[str, object], wire["throughput"])
    final_cost = cast(
        int,
        cast(
            dict[str, object],
            cast(dict[str, object], wire["cost"])["tight_upper"],
        )["microunits"],
    )
    assert not finished.paused
    assert throughput["active_elapsed_microseconds"] == 7_000_000
    assert final_cost > paused_cost
    assert len((root / "runtime-segments.jsonl").read_text().splitlines()) == 4


def test_duplicate_client_instances_fail_before_any_unit_runs(tmp_path: Path) -> None:
    config = _config(tmp_path / "duplicate-clients", level=4)
    shared = DummyClient(config.execution_model_id)

    with pytest.raises(pilot.ThroughputPilotError, match="distinct"):
        pilot.create_worker_client_pairs(config, lambda: (shared, shared))

    assert shared.close_count == 1


def test_live_config_binds_independent_root_and_requires_exact_spend(
    tmp_path: Path,
) -> None:
    root = tmp_path / "live-declaration-only"
    config = pilot.build_throughput_config(
        level=8,
        block_index=3,
        collection_attempt=7,
        output_dir=root,
        stub=False,
        execution_git_sha="1" * 40,
        analysis_code_sha256="2" * 64,
        uv_lock_sha256="3" * 64,
    )
    wire = config.to_dict()
    execution = cast(dict[str, object], wire["execution"])
    assert wire["run_id"] == ("benchmark-v2-task9-throughput-n8-block-003-attempt-007")
    assert wire["output_root"] == str(root.resolve())
    assert wire["excluded_from_analysis"] is True
    assert wire["evidence_scope"] == "smoke"
    assert execution["request_timeout_seconds"] == 300
    assert execution["recorded_attempt_elapsed_overhead_seconds"] == 10.0
    assert config.maximum_spend_microunits == 513_232_896

    reservations = pilot.unit_reservations()
    raw_reservation = next(
        reservation for reservation in reservations if reservation.logical_calls == 1
    )
    assert raw_reservation.attempts == 3
    assert raw_reservation.wall_microseconds == 931_500_000

    with pytest.raises(pilot.ThroughputPilotError, match="exactly equal"):
        pilot.require_exact_spend_confirmation(config, None)
    with pytest.raises(pilot.ThroughputPilotError, match="exactly equal"):
        pilot.require_exact_spend_confirmation(
            config,
            config.maximum_spend_microunits - 1,
        )
    pilot.require_exact_spend_confirmation(
        config,
        config.maximum_spend_microunits,
    )

    for bad_value in (9.0, 10):
        tampered = config.to_dict()
        tampered["execution"][  # type: ignore[index]
            "recorded_attempt_elapsed_overhead_seconds"
        ] = bad_value
        with pytest.raises(pilot.ThroughputPilotError, match="frozen float"):
            pilot.throughput_config_from_bytes(canonical_json_bytes(tampered))

    tampered_timeout = config.to_dict()
    tampered_timeout["execution"]["request_timeout_seconds"] = 300.0  # type: ignore[index]
    with pytest.raises(pilot.ThroughputPilotError, match="frozen integer"):
        pilot.throughput_config_from_bytes(canonical_json_bytes(tampered_timeout))


def test_config_cli_rejects_config_file_inside_bound_run_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "bound-root"

    with pytest.raises(pilot.ThroughputPilotError, match="outside the bound"):
        pilot.main(
            (
                "config",
                "--level",
                "2",
                "--block-index",
                "1",
                "--collection-attempt",
                "1",
                "--output-dir",
                str(root),
                "--output-config",
                str(root / "config.json"),
                "--stub",
                "--execution-git-sha",
                "1" * 40,
                "--analysis-code-sha256",
                "2" * 64,
                "--uv-lock-sha256",
                "3" * 64,
            )
        )

    assert not root.exists()


def test_sigint_during_fresh_and_resume_setup_returns_resumable_pause(
    tmp_path: Path,
) -> None:
    root = tmp_path / "setup-sigint"
    config = _config(root)
    previous_handler = signal.getsignal(signal.SIGINT)

    def interrupting_factory() -> Any:
        sent = False

        def factory() -> tuple[DummyClient, DummyClient]:
            nonlocal sent
            if not sent:
                sent = True
                os.kill(os.getpid(), signal.SIGINT)
            return (
                DummyClient(config.execution_model_id),
                DummyClient(config.execution_model_id),
            )

        return factory

    first = pilot.execute_pilot_block(
        config=config,
        output_dir=root,
        pair_factory=interrupting_factory(),
        unit_executor=_empty_checkpoint,
    )
    assert first.paused
    assert not first.ready_units
    assert signal.getsignal(signal.SIGINT) == previous_handler

    second = pilot.execute_pilot_block(
        config=config,
        output_dir=root,
        resume=True,
        pair_factory=interrupting_factory(),
        unit_executor=_empty_checkpoint,
    )
    assert second.paused
    assert not second.ready_units
    assert signal.getsignal(signal.SIGINT) == previous_handler

    finished = pilot.execute_pilot_block(
        config=config,
        output_dir=root,
        resume=True,
        pair_factory=_pair_factory(config),
        unit_executor=_empty_checkpoint,
    )
    assert not finished.paused
    assert len(finished.ready_units) == 8
    assert len((root / "runtime-segments.jsonl").read_text().splitlines()) == 6


def test_sigint_during_active_units_drains_and_resumes(tmp_path: Path) -> None:
    root = tmp_path / "active-sigint"
    config = _config(root)
    sent = threading.Event()
    send_lock = threading.Lock()

    def execute(
        _config: pilot.ThroughputPilotConfig,
        permit: Any,
        clients: tuple[DummyClient, DummyClient],
    ) -> pilot.UnitCheckpoint:
        with send_lock:
            if not sent.is_set():
                sent.set()
                os.kill(os.getpid(), signal.SIGINT)
        return _empty_checkpoint(_config, permit, clients)

    paused = pilot.execute_pilot_block(
        config=config,
        output_dir=root,
        pair_factory=_pair_factory(config),
        unit_executor=execute,
    )
    assert paused.paused
    assert 1 <= len(paused.ready_units) <= config.level

    finished = pilot.execute_pilot_block(
        config=config,
        output_dir=root,
        resume=True,
        pair_factory=_pair_factory(config),
        unit_executor=_empty_checkpoint,
    )
    assert not finished.paused
    assert len(finished.ready_units) == 8


def test_default_live_pair_closes_first_client_when_second_constructor_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path / "pair-constructor", stub=False)
    created: list[DummyClient] = []

    def fake_proxy(
        model_id: str,
        *,
        request_timeout_seconds: float,
    ) -> DummyClient:
        assert request_timeout_seconds == 300
        if created:
            raise RuntimeError("second constructor failed")
        client = DummyClient(model_id)
        created.append(client)
        return client

    monkeypatch.setattr(pilot, "ProxyLLM", fake_proxy)
    factory = pilot._default_pair_factory(config)

    with pytest.raises(RuntimeError, match="second constructor"):
        factory()

    assert len(created) == 1
    assert created[0].close_count == 1


def test_default_live_workers_require_numeric_loopback_before_client_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path / "numeric-loopback", stub=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:8317/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")

    with pytest.raises(ValueError, match="must use numeric loopback"):
        pilot.create_worker_client_pairs(config)


class _FailingClient(DummyClient):
    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1_024,
        temperature: float = 0.0,
    ) -> str:
        del system, user, max_tokens, temperature
        raise RuntimeError("redacted test failure")


def test_failed_timeout_like_calls_enter_rate_denominator_and_p95(
    tmp_path: Path,
) -> None:
    root = tmp_path / "failures"
    config = _config(root, level=4)
    frozen = pilot.load_frozen_inputs()

    def execute(
        _config: pilot.ThroughputPilotConfig,
        permit: Any,
        _clients: tuple[DummyClient, DummyClient],
    ) -> pilot.UnitCheckpoint:
        unit = frozen.spec.schedule[permit.schedule_index]
        item = frozen.spec.items[unit.item_position]
        delegate: DummyClient = (
            _FailingClient(_config.execution_model_id)
            if permit.schedule_index % 2
            else DummyClient(_config.execution_model_id)
        )
        duration = 300_000_000_000 if permit.schedule_index % 2 else 10_000_000_000
        ticks = iter((0, duration))
        observed = ObservingLLM(delegate, permit.sink, clock_ns=lambda: next(ticks))
        scopes = CallSequence(_config.run_id).bind_candidate(
            item_id=item.item_id,
            family_id=item.family_id,
            cluster_id=item.cluster_id,
            pair_id=f"timeout-pair-{permit.schedule_index}",
        )
        with scopes("raw", unit.sample_index, 0):
            try:
                observed.complete(system="system", user="user", max_tokens=1)
            except Exception:
                pass
        return _checkpoint_only(_config, permit, _clients)

    result = pilot.execute_pilot_block(
        config=config,
        output_dir=root,
        pair_factory=_pair_factory(config),
        unit_executor=execute,
    )
    wire = result.summary.to_dict()
    calls = cast(dict[str, object], wire["calls"])
    latency = cast(dict[str, object], wire["latency"])
    assert calls["total"] == 8
    assert calls["successful"] == 4
    assert calls["failed"] == 4
    assert calls["success_rate"] == 0.5
    assert latency["observed_calls"] == 8
    assert latency["p50_microseconds"] == 10_000_000
    assert latency["p95_microseconds"] == 300_000_000
    assert latency["includes_failed_and_timeout_calls"] is True


def test_comparison_never_auto_recommends_eight_and_requires_replicated_blocks(
    tmp_path: Path,
) -> None:
    seed_root = tmp_path / "seed"
    seed_config = _config(seed_root)
    seed = pilot.execute_pilot_block(
        config=seed_config,
        output_dir=seed_root,
        pair_factory=_pair_factory(seed_config),
        unit_executor=_empty_checkpoint,
    )
    summaries: list[pilot.ThroughputSummary] = []
    for level in (4, 8):
        for block_index in range(1, 9):
            config = _config(
                tmp_path / f"n{level}-block-{block_index}",
                level=level,
                block_index=block_index,
                stub=False,
            )
            summaries.append(
                pilot.build_summary(
                    config,
                    _ready_for_config(
                        seed.ready_units,
                        config,
                        provider_model_id=config.execution_model_id,
                    ),
                    active_elapsed_microseconds=1_000_000,
                    complete=True,
                )
            )
    comparison = cast(
        dict[str, object],
        parse_canonical_json_bytes(pilot.build_comparison(summaries)),
    )
    decision = cast(dict[str, object], comparison["decision"])
    assert comparison["evidence_scope"] == "replicated_smoke_for_manual_confirmation"
    assert decision == {
        "automatic_level_selection": None,
        "default_formal_level": 4,
        "level_8_status": "manual_confirm_required",
        "minimum_blocks_required_for_4_vs_8": 8,
        "recommendation": None,
    }

    one_block = cast(
        dict[str, object],
        parse_canonical_json_bytes(pilot.build_comparison(summaries[:1])),
    )
    one_decision = cast(dict[str, object], one_block["decision"])
    assert one_block["evidence_scope"] == "smoke"
    assert one_decision["default_formal_level"] == 4
    assert one_decision["level_8_status"] == "insufficient_independent_blocks"


def test_summary_parser_recomputes_latency_instead_of_trusting_claimed_p95(
    tmp_path: Path,
) -> None:
    root = tmp_path / "summary-tamper"
    config = _config(root)
    result = pilot.execute_pilot_block(
        config=config,
        output_dir=root,
        pair_factory=_pair_factory(config),
        unit_executor=_empty_checkpoint,
    )
    tampered = result.summary.to_dict()
    latency = cast(dict[str, object], tampered["latency"])
    latency["p95_microseconds"] = 1

    with pytest.raises(pilot.ThroughputPilotError, match="aggregation basis"):
        pilot.throughput_summary_from_bytes(canonical_json_bytes(tampered))


def test_summary_parser_rejects_attempt_elapsed_overhead_binding_drift(
    tmp_path: Path,
) -> None:
    root = tmp_path / "overhead-binding-tamper"
    config = _config(root)
    result = pilot.execute_pilot_block(
        config=config,
        output_dir=root,
        pair_factory=_pair_factory(config),
        unit_executor=_empty_checkpoint,
    )
    for bad_value in (9.0, 10):
        tampered = result.summary.to_dict()
        bindings = cast(dict[str, object], tampered["bindings"])
        bindings["recorded_attempt_elapsed_overhead_seconds"] = bad_value

        with pytest.raises(pilot.ThroughputPilotError, match="not frozen"):
            pilot.throughput_summary_from_bytes(canonical_json_bytes(tampered))

    tampered_timeout = result.summary.to_dict()
    tampered_timeout["request_timeout_seconds"] = 300.0
    with pytest.raises(pilot.ThroughputPilotError, match="analysis boundary"):
        pilot.throughput_summary_from_bytes(canonical_json_bytes(tampered_timeout))


def test_summary_parser_rejects_known_tokens_above_covered_attempt_ceiling(
    tmp_path: Path,
) -> None:
    root = tmp_path / "usage-ceiling-tamper"
    config = _config(root)
    result = pilot.execute_pilot_block(
        config=config,
        output_dir=root,
        pair_factory=_pair_factory(config),
        unit_executor=_empty_checkpoint,
    )
    tampered = result.summary.to_dict()
    basis = cast(dict[str, object], tampered["aggregation_basis"])
    usage = cast(dict[str, object], basis["usage"])
    known = cast(dict[str, object], usage["known_tokens"])
    known["input_tokens"] = 1

    with pytest.raises(pilot.ThroughputPilotError, match="covered-attempt ceilings"):
        pilot.throughput_summary_from_bytes(canonical_json_bytes(tampered))


def test_completed_root_rejects_summary_swapped_from_another_run(
    tmp_path: Path,
) -> None:
    root_a = tmp_path / "summary-root-a"
    root_b = tmp_path / "summary-root-b"
    config_a = _config(root_a, block_index=1)
    config_b = _config(root_b, block_index=2)
    result_a = pilot.execute_pilot_block(
        config=config_a,
        output_dir=root_a,
        pair_factory=_pair_factory(config_a),
        unit_executor=_empty_checkpoint,
    )
    result_b = pilot.execute_pilot_block(
        config=config_b,
        output_dir=root_b,
        pair_factory=_pair_factory(config_b),
        unit_executor=_empty_checkpoint,
    )
    assert result_a.summary.run_id != result_b.summary.run_id
    (root_a / "summary.json").write_bytes(result_b.summary.wire_json)

    with pytest.raises(pilot.ThroughputPilotError, match="current run config"):
        pilot.execute_pilot_block(
            config=config_a,
            output_dir=root_a,
            resume=True,
            pair_factory=_pair_factory(config_a),
            unit_executor=_empty_checkpoint,
        )


def test_comparison_rejects_stub_summary_even_when_complete(tmp_path: Path) -> None:
    root = tmp_path / "stub-comparison"
    config = _config(root, level=4)
    result = pilot.execute_pilot_block(
        config=config,
        output_dir=root,
        pair_factory=_pair_factory(config),
        unit_executor=_empty_checkpoint,
    )

    with pytest.raises(pilot.ThroughputPilotError, match="only live"):
        pilot.build_comparison((result.summary,))


def test_live_summary_rejects_missing_or_wrong_provider_model_evidence(
    tmp_path: Path,
) -> None:
    seed_root = tmp_path / "provider-seed"
    seed_config = _config(seed_root)
    seed = pilot.execute_pilot_block(
        config=seed_config,
        output_dir=seed_root,
        pair_factory=_pair_factory(seed_config),
        unit_executor=_empty_checkpoint,
    )
    live_config = _config(
        tmp_path / "provider-live",
        level=4,
        stub=False,
    )

    with pytest.raises(pilot.ThroughputPilotError, match="provider_evidence"):
        pilot.build_summary(
            live_config,
            _ready_for_config(seed.ready_units, live_config),
            active_elapsed_microseconds=1_000_000,
            complete=True,
        )
    with pytest.raises(pilot.ThroughputPilotError, match="provider_evidence"):
        pilot.build_summary(
            live_config,
            _ready_for_config(
                seed.ready_units,
                live_config,
                provider_model_id="wrong-model",
            ),
            active_elapsed_microseconds=1_000_000,
            complete=True,
        )


def test_comparison_rejects_mismatched_execution_bindings(tmp_path: Path) -> None:
    seed_root = tmp_path / "binding-seed"
    seed_config = _config(seed_root)
    seed = pilot.execute_pilot_block(
        config=seed_config,
        output_dir=seed_root,
        pair_factory=_pair_factory(seed_config),
        unit_executor=_empty_checkpoint,
    )
    config_four = _config(
        tmp_path / "binding-four",
        level=4,
        block_index=1,
        stub=False,
    )
    config_eight = _config(
        tmp_path / "binding-eight",
        level=8,
        block_index=1,
        stub=False,
        analysis_code_sha256="9" * 64,
    )
    summary_four = pilot.build_summary(
        config_four,
        _ready_for_config(
            seed.ready_units,
            config_four,
            provider_model_id=config_four.execution_model_id,
        ),
        active_elapsed_microseconds=1_000_000,
        complete=True,
    )
    summary_eight = pilot.build_summary(
        config_eight,
        _ready_for_config(
            seed.ready_units,
            config_eight,
            provider_model_id=config_eight.execution_model_id,
        ),
        active_elapsed_microseconds=1_000_000,
        complete=True,
    )

    with pytest.raises(pilot.ThroughputPilotError, match="mismatched execution"):
        pilot.build_comparison((summary_four, summary_eight))
