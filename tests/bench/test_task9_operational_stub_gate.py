from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

import fretsure.bench.runner as runner_module
from fretsure.bench.artifacts import CompleteUnitReservation
from fretsure.bench.concurrent import CollectionExecutionContract
from fretsure.bench.contracts import canonical_json_bytes
from fretsure.bench.preregistration import BenchmarkPreregistration
from fretsure.bench.runner import BenchmarkV2Config

ROOT = Path(__file__).resolve().parents[2]
MODULE_SPEC = importlib.util.spec_from_file_location(
    "task9_operational_stub_gate",
    ROOT / "scripts/task9_operational_stub_gate.py",
)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
gate = importlib.util.module_from_spec(MODULE_SPEC)
sys.modules[MODULE_SPEC.name] = gate
MODULE_SPEC.loader.exec_module(gate)


def _minimal_operational_preregistration() -> BenchmarkPreregistration:
    return BenchmarkPreregistration(
        canonical_json_bytes(
            {
                "budgets": {
                    "provider_policy": {
                        "recorded_attempt_elapsed_overhead_seconds": 10.0,
                        "request_timeout_seconds": 300.0,
                    }
                },
                "collection_execution": CollectionExecutionContract.preregistered(
                    max_in_flight_units=4
                ).to_dict(),
            }
        )
    )


def test_operational_stub_context_stays_offline_and_binds_four_lanes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = runner_module.build_benchmark_v2_context(
        BenchmarkV2Config(
            family_count=1,
            bars=1,
            bootstrap_repetitions=11,
            sign_flip_draws=11,
        )
    )
    monkeypatch.setattr(
        gate.runner_module,
        "build_benchmark_v2_preregistered_context",
        lambda _preregistration: base,
    )

    context = gate.build_operational_stub_context(_minimal_operational_preregistration())

    assert context.config.stub is True
    assert context.pre_call_config is not None
    assert context.pre_call_config.max_in_flight_units == 4
    assert context.pre_call_config.request_timeout_seconds == 300.0
    assert context.pre_call_config.recorded_attempt_elapsed_overhead_seconds == 10.0
    assert context.pre_call_config.requested_model_id == context.requested_model_id


def test_operational_stub_collect_uses_production_coordinator_without_factories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = runner_module.build_benchmark_v2_context(
        BenchmarkV2Config(
            family_count=1,
            bars=1,
            bootstrap_repetitions=11,
            sign_flip_draws=11,
        )
    )
    captured: dict[str, object] = {}
    sentinel = object()
    monkeypatch.setattr(gate, "build_operational_stub_context", lambda _prereg: context)

    def collect(**kwargs: object) -> object:
        captured.update(kwargs)
        stop_requested = cast(object, kwargs["stop_requested"])
        assert callable(stop_requested)
        assert not stop_requested()
        return sentinel

    monkeypatch.setattr(gate.runner_module, "_collect_operational_concurrent", collect)

    result = gate.collect_operational_stub(
        preregistration=_minimal_operational_preregistration(),
        output_dir=tmp_path / "gate",
        resume=True,
    )

    assert result is sentinel
    assert captured["context"] is context
    assert captured["output_dir"] == tmp_path / "gate"
    assert captured["resume"] is True
    assert captured["agent_llm_factory"] is None
    assert captured["raw_llm_factory"] is None


def test_operational_stub_two_runs_publish_byte_identical_canonical_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = runner_module.build_benchmark_v2_context(
        BenchmarkV2Config(
            family_count=1,
            bars=1,
            bootstrap_repetitions=11,
            sign_flip_draws=11,
        )
    )
    preregistration = _minimal_operational_preregistration()
    monkeypatch.setattr(
        gate.runner_module,
        "build_benchmark_v2_preregistered_context",
        lambda _preregistration: base,
    )
    context = gate.build_operational_stub_context(preregistration)
    reservations = tuple(
        runner_module._scheduled_unit_reservation(
            context,
            index,
            request_timeout_seconds=300.0,
            recorded_attempt_elapsed_overhead_seconds=10.0,
        )
        for index in range(len(context.plan.collection_schedule))
    )
    fields = (
        "logical_calls",
        "attempts",
        "requested_output_tokens",
        "attempt_reserved_output_tokens",
        "response_text_bytes",
        "transport_response_bytes",
        "wall_microseconds",
    )
    maximum_reservation = CompleteUnitReservation(
        *(max(getattr(value, field) for value in reservations) for field in fields)
    )
    context = replace(
        context,
        manifest=replace(
            context.manifest,
            limits=replace(
                context.manifest.limits,
                complete_unit_reservation=maximum_reservation,
            ),
        ),
    )
    monkeypatch.setattr(gate, "build_operational_stub_context", lambda _prereg: context)
    monkeypatch.setattr(gate.runner_module.os, "fsync", lambda _descriptor: None)
    first = tmp_path / "first"
    second = tmp_path / "second"

    gate.collect_operational_stub(
        preregistration=preregistration,
        output_dir=first,
    )
    gate.collect_operational_stub(
        preregistration=preregistration,
        output_dir=second,
    )

    first_canonical = {
        path.name: path.read_bytes() for path in sorted((first / "canonical").iterdir())
    }
    second_canonical = {
        path.name: path.read_bytes() for path in sorted((second / "canonical").iterdir())
    }
    assert first_canonical == second_canonical
