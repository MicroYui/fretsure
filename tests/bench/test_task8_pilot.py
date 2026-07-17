from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path
from typing import cast

import pytest

import fretsure.bench.runner as runner_module
from fretsure.agent.arranger import ArrangeGoal, proposal_output_token_budget
from fretsure.bench.artifacts import (
    ArtifactCode,
    ArtifactError,
    ArtifactStore,
    CompleteUnitReservation,
    sanitized_observations_from_dict,
)
from fretsure.bench.baselines import build_raw_baseline_request
from fretsure.bench.contracts import canonical_json_bytes
from fretsure.bench.corpus import corpus_from_dict, notegraph_sha256
from fretsure.bench.experiment import sample_pair_id
from fretsure.bench.observe import CallSequence, CallStage, ObservingLLM, current_call_context
from fretsure.bench.preregistration import (
    BenchmarkPreregistration,
    preregistration_from_bytes,
)
from fretsure.llm.client import LLMClient
from fretsure.oracle.profiles import MEDIAN_HAND

ROOT = Path(__file__).resolve().parents[2]
FORMAL_PREREG = ROOT / "docs/experiments/2026-07-17-benchmark-v2-prereg.json"
_MODULE_SPEC = importlib.util.spec_from_file_location(
    "task8_pilot", ROOT / "scripts/task8_pilot.py"
)
assert _MODULE_SPEC is not None and _MODULE_SPEC.loader is not None
pilot = importlib.util.module_from_spec(_MODULE_SPEC)
sys.modules[_MODULE_SPEC.name] = pilot
_MODULE_SPEC.loader.exec_module(pilot)


@pytest.fixture(scope="module")
def formal_preregistration() -> BenchmarkPreregistration:
    return preregistration_from_bytes(FORMAL_PREREG.read_bytes())


@pytest.fixture(scope="module")
def spec(formal_preregistration: BenchmarkPreregistration) -> pilot.PilotSpec:
    return pilot.build_pilot_spec(formal_preregistration)


def _available_pre_call(spec: pilot.PilotSpec, *, attempt: int = 1) -> pilot.PilotPreCallConfig:
    budget_gate = pilot.load_budget_gate_module()
    pricing = budget_gate.build_token_pricing_contract(
        billing_provider_id="fixture-provider",
        billing_model_id=spec.requested_model_id,
        currency="USD",
        rates_microunits_per_million_tokens={
            "cache_creation_input_tokens": 1_000_000,
            "cache_read_input_tokens": 1_000_000,
            "input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
        },
        fixed_microunits_per_attempt=0,
        billable_token_ceiling_per_attempt={
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "input_tokens": 4_096,
            "output_tokens": 2_048,
        },
        ceil_each_component_per_attempt=False,
        evidence_source_ref="fixture-price-contract",
        evidence_captured_at_utc="2026-07-18T00:00:00Z",
        evidence_source_sha256="5" * 64,
    )
    return pilot.build_pilot_pre_call_config(
        spec,
        collection_attempt=attempt,
        execution_git_sha="1" * 40,
        analysis_code_sha256="2" * 64,
        uv_lock_sha256="3" * 64,
        pricing_contract_json=pricing.wire_json,
    )


def _canonical_bytes(path: Path) -> dict[str, bytes]:
    return {entry.name: entry.read_bytes() for entry in (path / "canonical").iterdir()}


def test_spec_is_canonical_two_by_two_and_disjoint_from_formal(
    formal_preregistration: BenchmarkPreregistration,
    spec: pilot.PilotSpec,
) -> None:
    assert pilot.pilot_spec_from_bytes(spec.wire_json) == spec
    assert spec.pilot_id == pilot.DEFAULT_PILOT_ID
    assert (
        spec.to_dict()["formal_preregistration_raw_sha256"]
        == pilot.FORMAL_PREREGISTRATION_RAW_SHA256
    )
    assert "formal_preregistration" not in spec.to_dict()
    assert spec.run_id_for_attempt(7) == f"{pilot.DEFAULT_PILOT_ID}-attempt-007"
    assert spec.stub_run_id == f"{pilot.DEFAULT_PILOT_ID}-stub-attempt-001"

    items = spec.items
    assert len(items) == 2
    assert [item.item_id for item in items] == ["pilot-proc-v2-000000", "pilot-proc-v2-000001"]
    assert all(cast(str, item.family_id).startswith("pilot-proc-family-v2-") for item in items)
    assert all(cast(str, item.cluster_id).startswith("pilot-proc-cluster-v2-") for item in items)
    assert all(item.provenance is not None and item.provenance.split == "pilot" for item in items)
    assert all(
        item.provenance is not None
        and item.provenance.generator is not None
        and item.provenance.generator.bars == 2
        for item in items
    )

    formal_wire = formal_preregistration.to_dict()
    formal_corpus = cast(dict[str, object], formal_wire["corpus"])
    formal_items = corpus_from_dict(formal_corpus["snapshot"])
    assert {item.item_id for item in items}.isdisjoint(item.item_id for item in formal_items)
    assert {item.family_id for item in items}.isdisjoint(item.family_id for item in formal_items)
    assert {item.cluster_id for item in items}.isdisjoint(item.cluster_id for item in formal_items)
    assert {notegraph_sha256(item.ir) for item in items}.isdisjoint(
        notegraph_sha256(item.ir) for item in formal_items
    )
    assert {
        cast(str, cast(object, item.provenance).source_sha256)  # type: ignore[union-attr]
        for item in items
    }.isdisjoint(
        item.provenance.source_sha256
        for item in formal_items
        if item.provenance is not None and item.provenance.source_sha256 is not None
    )

    goal = ArrangeGoal()
    for item in items:
        at_tempo = pilot.goal_at_source_tempo(goal, item)
        assert proposal_output_token_budget(item.ir) == 2_048
        assert build_raw_baseline_request(item.ir, at_tempo, MEDIAN_HAND).max_tokens == 2_048

    assert [(unit.arm, unit.item_position, unit.sample_index) for unit in spec.schedule] == [
        ("agent", 0, 0),
        ("raw", 0, 0),
        ("agent", 0, 1),
        ("raw", 0, 1),
        ("agent", 1, 0),
        ("raw", 1, 0),
        ("agent", 1, 1),
        ("raw", 1, 1),
    ]
    budget = spec.to_dict()["budget"]
    assert budget == {
        "full": {
            "active_host_deadline_microseconds": 5_400_000_000,
            "attempt_reserved_output_tokens": 153_600,
            "attempts": 132,
            "logical_calls": 44,
            "provider_timeout_envelope_microseconds": 4_026_000_000,
            "recorded_provider_call_elapsed_ceiling_microseconds": 5_400_000_000,
            "requested_output_tokens": 51_200,
            "response_text_bytes": 1_638_400,
            "transport_response_bytes": 138_412_032,
        },
        "pair_reservation": {
            "attempt_reserved_output_tokens": 38_400,
            "attempts": 33,
            "logical_calls": 11,
            "provider_timeout_envelope_microseconds": 1_006_500_000,
            "requested_output_tokens": 12_800,
            "response_text_bytes": 409_600,
            "transport_response_bytes": 34_603_008,
        },
        "raw_reservation": {
            "attempt_reserved_output_tokens": 6_144,
            "attempts": 3,
            "logical_calls": 1,
            "provider_timeout_envelope_microseconds": 91_500_000,
            "requested_output_tokens": 2_048,
            "response_text_bytes": 65_536,
            "transport_response_bytes": 3_145_728,
        },
    }
    assert spec.to_dict()["timing"] == {
        "active_host_deadline_microseconds": 5_400_000_000,
        "active_host_scope": "single_invocation_non_cumulative_across_resume",
        "provider_timeout_envelope_microseconds": 4_026_000_000,
        "recorded_provider_call_elapsed_ceiling_microseconds": 5_400_000_000,
    }

    tampered = copy.deepcopy(spec.to_dict())
    tampered["budget"]["full"]["logical_calls"] = 45  # type: ignore[index]
    with pytest.raises(pilot.PilotConfigError, match="frozen deterministic pilot spec"):
        pilot.pilot_spec_from_dict(tampered)

    with pytest.raises(pilot.PilotConfigError) as alternate_seed:
        pilot.build_pilot_spec(formal_preregistration, base_seed=1)
    assert alternate_seed.value.field == "base_seed"
    alternate_seed_wire = copy.deepcopy(spec.to_dict())
    alternate_seed_wire["corpus"]["base_seed"] = 1  # type: ignore[index]
    with pytest.raises(pilot.PilotConfigError) as parsed_seed:
        pilot.pilot_spec_from_dict(alternate_seed_wire)
    assert parsed_seed.value.field == "corpus.base_seed"

    with pytest.raises(pilot.PilotConfigError) as alternate_id:
        pilot.build_pilot_spec(formal_preregistration, pilot_id="another-pilot")
    assert alternate_id.value.field == "pilot_id"
    alternate_id_wire = copy.deepcopy(spec.to_dict())
    alternate_id_wire["pilot_id"] = "another-pilot"
    with pytest.raises(pilot.PilotConfigError) as parsed_id:
        pilot.pilot_spec_from_dict(alternate_id_wire)
    assert parsed_id.value.field == "pilot_id"


def test_pre_call_is_attempt_local_priced_and_does_not_claim_authorization(
    spec: pilot.PilotSpec,
) -> None:
    config = _available_pre_call(spec, attempt=7)
    assert pilot.pilot_pre_call_config_from_bytes(config.wire_json) == config
    assert config.run_id == f"{spec.pilot_id}-attempt-007"
    assert config.collection_attempt == 7
    assert config.has_priced_ceiling
    cost = cast(dict[str, object], config.to_dict()["cost"])
    pricing_wire = cast(dict[str, object], cost["pricing_contract"])
    pricing = pilot.load_budget_gate_module().token_pricing_contract_from_dict(pricing_wire)
    expected_worst = pilot.load_budget_gate_module().pilot_worst_case_budget(pricing)
    assert cost["currency"] == "USD"
    assert cost["maximum_spend_microunits"] == expected_worst["cost_microunits"]
    assert cost["pricing_contract_raw_sha256"] == pricing.raw_sha256
    assert cost["worst_case"] == expected_worst
    assert cost["status"] == "available"
    pilot.require_pilot_live_authorization(config)

    unavailable = pilot.build_pilot_pre_call_config(
        spec,
        collection_attempt=1,
        execution_git_sha="1" * 40,
        analysis_code_sha256="2" * 64,
        uv_lock_sha256="3" * 64,
    )
    assert not unavailable.has_priced_ceiling
    with pytest.raises(pilot.PilotConfigError) as caught:
        pilot.require_pilot_live_authorization(unavailable)
    assert caught.value.field == "cost"

    tampered = copy.deepcopy(config.to_dict())
    tampered["run_id"] = "another-run"
    with pytest.raises(pilot.PilotConfigError) as caught:
        pilot.pilot_pre_call_config_from_dict(tampered)
    assert caught.value.field == "run_id"

    tampered_cost = copy.deepcopy(config.to_dict())
    tampered_cost["cost"]["maximum_spend_microunits"] += 1  # type: ignore[index,operator]
    with pytest.raises(pilot.PilotConfigError) as caught:
        pilot.pilot_pre_call_config_from_dict(tampered_cost)
    assert caught.value.field == "cost.maximum_spend_microunits"


def test_pricing_contract_must_match_model_and_2048_ceiling_and_may_be_free(
    spec: pilot.PilotSpec,
) -> None:
    budget_gate = pilot.load_budget_gate_module()

    def contract(*, model: str, output_ceiling: int, rate: int) -> object:
        return budget_gate.build_token_pricing_contract(
            billing_provider_id="fixture-provider",
            billing_model_id=model,
            currency="USD",
            rates_microunits_per_million_tokens={
                "cache_creation_input_tokens": rate,
                "cache_read_input_tokens": rate,
                "input_tokens": rate,
                "output_tokens": rate,
            },
            fixed_microunits_per_attempt=0,
            billable_token_ceiling_per_attempt={
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "input_tokens": 4_096,
                "output_tokens": output_ceiling,
            },
            ceil_each_component_per_attempt=False,
            evidence_source_ref="fixture-price-contract",
            evidence_captured_at_utc="2026-07-18T00:00:00Z",
            evidence_source_sha256="5" * 64,
        )

    kwargs = {
        "collection_attempt": 1,
        "execution_git_sha": "1" * 40,
        "analysis_code_sha256": "2" * 64,
        "uv_lock_sha256": "3" * 64,
    }
    wrong_model = contract(model="another-model", output_ceiling=2_048, rate=1)
    with pytest.raises(pilot.PilotConfigError) as caught:
        pilot.build_pilot_pre_call_config(
            spec,
            pricing_contract_json=wrong_model.wire_json,  # type: ignore[attr-defined]
            **kwargs,
        )
    assert caught.value.field == "cost.pricing_contract.billing_model_id"

    too_small = contract(model=spec.requested_model_id, output_ceiling=2_047, rate=1)
    with pytest.raises(pilot.PilotConfigError) as caught:
        pilot.build_pilot_pre_call_config(
            spec,
            pricing_contract_json=too_small.wire_json,  # type: ignore[attr-defined]
            **kwargs,
        )
    assert caught.value.field.endswith("output_tokens")

    free = contract(model=spec.requested_model_id, output_ceiling=2_048, rate=0)
    free_config = pilot.build_pilot_pre_call_config(
        spec,
        pricing_contract_json=free.wire_json,  # type: ignore[attr-defined]
        **kwargs,
    )
    assert free_config.to_dict()["cost"]["maximum_spend_microunits"] == 0  # type: ignore[index]
    pilot.require_pilot_live_authorization(free_config)


def test_cli_writes_and_checks_the_canonical_spec(
    tmp_path: Path,
    spec: pilot.PilotSpec,
) -> None:
    output = tmp_path / "pilot-spec.json"
    assert (
        pilot.main(
            [
                "--write-spec",
                str(output),
                "--formal-prereg",
                str(FORMAL_PREREG),
            ]
        )
        == 0
    )

    expected_pre_call = _available_pre_call(spec)
    cost = cast(dict[str, object], expected_pre_call.to_dict()["cost"])
    pricing = pilot.load_budget_gate_module().token_pricing_contract_from_dict(
        cost["pricing_contract"]
    )
    pricing_path = tmp_path / "pricing.json"
    pricing_path.write_bytes(pricing.wire_json)
    pre_call_path = tmp_path / "pilot-pre-call.json"
    common = [
        "--spec",
        str(output),
        "--pricing-contract",
        str(pricing_path),
        "--collection-attempt",
        "1",
        "--execution-git-sha",
        "1" * 40,
        "--analysis-code-sha256",
        "2" * 64,
        "--uv-lock-sha256",
        "3" * 64,
    ]
    assert pilot.main(["--write-pre-call", str(pre_call_path), *common]) == 0
    assert pre_call_path.read_bytes() == expected_pre_call.wire_json
    assert pilot.main(["--check-pre-call", str(pre_call_path), *common]) == 0
    assert output.read_bytes() == spec.wire_json
    assert (
        pilot.main(
            [
                "--check-spec",
                str(output),
                "--formal-prereg",
                str(FORMAL_PREREG),
            ]
        )
        == 0
    )


def test_pilot_manifest_has_exact_caps_and_formal_context_rejects_it(
    spec: pilot.PilotSpec,
) -> None:
    context = pilot.build_pilot_stub_context(spec)
    limits = context.manifest.limits
    assert context.manifest.run_id == spec.stub_run_id
    assert len(context.manifest.expected_rows) == 8
    assert limits.max_calls == 44
    assert limits.max_attempts == 132
    assert limits.max_requested_output_tokens == 51_200
    assert limits.max_attempt_reserved_output_tokens == 153_600
    assert limits.max_wall_microseconds == 5_400_000_000
    assert limits.complete_unit_reservation == pilot.PAIR_RESERVATION
    assert context.manifest.parameters["schema"] == pilot.PILOT_RUN_CONFIG_VERSION

    with pytest.raises(runner_module.BenchmarkInputError) as caught:
        runner_module.benchmark_v2_context_from_manifest(context.manifest)
    assert caught.value.field == "parameters"


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
        max_tokens: int = 1_024,
        temperature: float = 0.0,
    ) -> str:
        del system, user, max_tokens, temperature
        context = current_call_context()
        if context is not None and context.stage is CallStage.CRITIC:
            return '{"overall":0.8,"voice_leading":0.7,"bass_motion":0.6,"texture":0.5}'
        if context is not None and context.stage is CallStage.RAW:
            return '{"tuning":[40,45,50,55,59,64],"capo":0,"notes":[]}'
        if context is not None and context.stage is CallStage.REPAIR:
            return '{"edits":[]}'
        return '{"notes":[{"onset":"0","duration":"1","pitch":64,"voice":"melody"}]}'

    def close(self) -> None:
        self.closes += 1


def test_stub_rejects_injection_and_live_fails_before_output(
    tmp_path: Path,
    spec: pilot.PilotSpec,
) -> None:
    forbidden_output = tmp_path / "stub-injection"
    with pytest.raises(pilot.PilotConfigError) as caught:
        pilot.collect_pilot(
            spec=spec,
            output_dir=forbidden_output,
            agent_llm_factory=lambda: _ClosableClient(spec.requested_model_id),
            raw_llm_factory=lambda: _ClosableClient(spec.requested_model_id),
        )
    assert caught.value.field == "llm_factory"
    assert not forbidden_output.exists()

    unavailable = pilot.build_pilot_pre_call_config(
        spec,
        collection_attempt=1,
        execution_git_sha="1" * 40,
        analysis_code_sha256="2" * 64,
        uv_lock_sha256="3" * 64,
    )
    factory_calls = 0

    def forbidden_factory() -> LLMClient:
        nonlocal factory_calls
        factory_calls += 1
        return _ClosableClient(spec.requested_model_id)

    live_output = tmp_path / "live-unpriced"
    with pytest.raises(pilot.PilotConfigError) as caught:
        pilot.collect_pilot(
            pre_call_config=unavailable,
            output_dir=live_output,
            agent_llm_factory=forbidden_factory,
            raw_llm_factory=forbidden_factory,
        )
    assert caught.value.field == "cost"
    assert factory_calls == 0
    assert not live_output.exists()

    priced = _available_pre_call(spec)
    confirmation_output = tmp_path / "missing-explicit-confirmation"
    with pytest.raises(pilot.PilotConfigError) as caught:
        pilot.collect_pilot(
            pre_call_config=priced,
            output_dir=confirmation_output,
            agent_llm_factory=forbidden_factory,
            raw_llm_factory=forbidden_factory,
        )
    assert caught.value.field == "authorized_maximum_spend_microunits"
    assert factory_calls == 0
    assert not confirmation_output.exists()

    tampered = copy.deepcopy(priced.to_dict())
    tampered_cost = cast(dict[str, object], tampered["cost"])
    tampered_cost["maximum_spend_microunits"] = (
        cast(int, tampered_cost["maximum_spend_microunits"]) + 1
    )
    directly_constructed = pilot.PilotPreCallConfig(canonical_json_bytes(tampered))
    direct_output = tmp_path / "direct-constructor-tamper"
    with pytest.raises(pilot.PilotConfigError) as caught:
        pilot.collect_pilot(
            pre_call_config=directly_constructed,
            output_dir=direct_output,
            agent_llm_factory=forbidden_factory,
            raw_llm_factory=forbidden_factory,
            authorized_maximum_spend_microunits=cast(
                int,
                tampered_cost["maximum_spend_microunits"],
            ),
        )
    assert caught.value.field == "cost.maximum_spend_microunits"
    assert factory_calls == 0
    assert not direct_output.exists()

    assert priced.maximum_spend_microunits is not None
    for delta in (-1, 1):
        drift_output = tmp_path / f"confirmation-drift-{delta}"
        with pytest.raises(pilot.PilotConfigError) as caught:
            pilot.collect_pilot(
                pre_call_config=priced,
                output_dir=drift_output,
                agent_llm_factory=forbidden_factory,
                raw_llm_factory=forbidden_factory,
                authorized_maximum_spend_microunits=(
                    priced.maximum_spend_microunits + delta
                ),
            )
        assert caught.value.field == "authorized_maximum_spend_microunits"
        assert not drift_output.exists()
    assert factory_calls == 0

    first = _ClosableClient(spec.requested_model_id)

    def fail_second() -> LLMClient:
        raise RuntimeError("raw factory failed")

    factory_output = tmp_path / "factory-failure"
    with pytest.raises(RuntimeError, match="raw factory failed"):
        pilot.collect_pilot(
            pre_call_config=priced,
            output_dir=factory_output,
            agent_llm_factory=lambda: first,
            raw_llm_factory=fail_second,
            authorized_maximum_spend_microunits=priced.maximum_spend_microunits,
        )
    assert first.closes == 1
    assert not factory_output.exists()


def test_stub_collection_reserves_pair_then_raw_and_publishes_only_five_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    spec: pilot.PilotSpec,
) -> None:
    output = tmp_path / "one-shot"
    seen: list[CompleteUnitReservation] = []
    original = ArtifactStore.reserve_next_unit

    def recording_reservation(store: ArtifactStore, reservation: CompleteUnitReservation) -> None:
        seen.append(reservation)
        original(store, reservation)

    monkeypatch.setattr(ArtifactStore, "reserve_next_unit", recording_reservation)
    result = pilot.collect_pilot(spec=spec, output_dir=output)

    assert not result.paused
    assert result.receipt is not None
    assert result.summary is not None
    assert result.receipt.observed_rows == 8
    assert result.summary.logical_calls == result.receipt.observed_calls
    assert result.summary.latency.available_calls == 0
    assert result.summary.latency.unavailable_calls == result.summary.logical_calls
    assert result.summary.latency.total_if_complete is None
    assert result.summary.usage.input_tokens.total_if_complete is None
    assert result.summary.usage.output_tokens.total_if_complete is None
    assert result.summary.active_host_elapsed_microseconds >= 0
    assert sum(value.logical_calls for value in result.summary.stage_totals) == (
        result.summary.logical_calls
    )
    assert sum(value.retries for value in result.summary.stage_totals) == result.summary.retries
    assert sum(value.requested_output_tokens for value in result.summary.stage_totals) > 0
    assert sum(value.attempt_reserved_output_tokens for value in result.summary.stage_totals) > 0
    assert seen == [
        pilot.PAIR_RESERVATION,
        pilot.RAW_RESERVATION,
        pilot.PAIR_RESERVATION,
        pilot.RAW_RESERVATION,
        pilot.PAIR_RESERVATION,
        pilot.RAW_RESERVATION,
        pilot.PAIR_RESERVATION,
        pilot.RAW_RESERVATION,
    ]
    assert set(_canonical_bytes(output)) == {
        "blobs.jsonl",
        "config.json",
        "observations.json",
        "receipt.json",
        "rows.jsonl",
    }
    assert not (output / "operational-summary.json").exists()


def test_live_completion_writes_budget_gate_operational_usage(
    tmp_path: Path,
    spec: pilot.PilotSpec,
) -> None:
    output = tmp_path / "live-operational-usage"
    agent = _ClosableClient(spec.requested_model_id)
    raw = _ClosableClient(spec.requested_model_id)
    pre_call = _available_pre_call(spec)
    result = pilot.collect_pilot(
        pre_call_config=pre_call,
        output_dir=output,
        agent_llm_factory=lambda: agent,
        raw_llm_factory=lambda: raw,
        authorized_maximum_spend_microunits=pre_call.maximum_spend_microunits,
        observation_clock_ns=lambda: 0,
        host_clock_ns=lambda: 0,
    )

    assert result.summary is not None
    operational = pilot.load_budget_gate_module().operational_usage_from_bytes(
        (output / "operational-summary.json").read_bytes()
    )
    assert operational.pair_count == 4
    assert operational.logical_calls == result.summary.logical_calls
    assert operational.attempts == result.summary.provider_attempts
    assert operational.elapsed_microseconds == 0
    assert operational.usage_covers_all_attempts is True
    assert set(operational.usage.values()) == {None}
    assert set(operational.stage_totals_by_name) == {"proposal_raw", "repair", "critic"}
    assert agent.closes == raw.closes == 1


@pytest.mark.parametrize("pause_after_rows", [1, 2])
def test_clean_resume_after_agent_or_pair_is_byte_identical(
    tmp_path: Path,
    spec: pilot.PilotSpec,
    pause_after_rows: int,
) -> None:
    expected = tmp_path / f"expected-{pause_after_rows}"
    resumed = tmp_path / f"resumed-{pause_after_rows}"
    pilot.collect_pilot(spec=spec, output_dir=expected)

    paused = pilot.collect_pilot(
        spec=spec,
        output_dir=resumed,
        pause_after_rows=pause_after_rows,
    )
    assert paused.paused
    assert paused.receipt is None
    assert paused.summary is None
    assert paused.completed_rows == pause_after_rows
    assert not (resumed / "canonical").exists()

    completed = pilot.collect_pilot(spec=spec, output_dir=resumed, resume=True)
    assert not completed.paused
    assert _canonical_bytes(resumed) == _canonical_bytes(expected)


class _InterruptingClient:
    def __init__(self, model_id: str) -> None:
        self._model_id = model_id

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
        raise KeyboardInterrupt


def test_orphan_wal_is_not_resumed(tmp_path: Path, spec: pilot.PilotSpec) -> None:
    output = tmp_path / "orphan"
    context = pilot.build_pilot_stub_context(spec)
    item = spec.items[0]
    assert item.family_id is not None and item.cluster_id is not None
    with ArtifactStore.create(output, context.manifest) as store:
        sequence = CallSequence(context.manifest.run_id)
        observed = ObservingLLM(_InterruptingClient(context.requested_model_id), store.sink)
        scopes = sequence.bind_candidate(
            item_id=item.item_id,
            family_id=item.family_id,
            cluster_id=item.cluster_id,
            pair_id=sample_pair_id(item.item_id, 0),
        )
        with pytest.raises(KeyboardInterrupt), scopes("proposal", 0, 0):
            observed.complete(system="system", user="user", max_tokens=2_048, temperature=0.8)

    with pytest.raises(ArtifactError) as caught:
        pilot.collect_pilot(spec=spec, output_dir=output, resume=True)
    assert caught.value.code is ArtifactCode.INCOMPLETE


def test_active_host_deadline_is_invocation_local_and_checked_after_each_row(
    tmp_path: Path,
    spec: pilot.PilotSpec,
) -> None:
    output = tmp_path / "host-deadline"
    readings = iter(
        (
            0,
            0,
            0,
            (pilot.ACTIVE_HOST_DEADLINE_MICROSECONDS + 1) * 1_000,
        )
    )

    with pytest.raises(pilot.PilotConfigError) as caught:
        pilot.collect_pilot(
            spec=spec,
            output_dir=output,
            host_clock_ns=lambda: next(readings),
        )
    assert caught.value.field == "active_host_elapsed_microseconds"
    assert output.is_dir()
    assert not (output / "canonical").exists()

    # A new invocation receives a fresh deadline. Durable provider elapsed remains
    # global because ArtifactStore restores it from the WAL.
    resumed = pilot.collect_pilot(
        spec=spec,
        output_dir=output,
        resume=True,
        host_clock_ns=lambda: 0,
    )
    assert resumed.summary is not None
    assert resumed.summary.active_host_elapsed_microseconds == 0


def test_operational_summary_preserves_partial_usage_as_unavailable() -> None:
    observations = sanitized_observations_from_dict(
        {
            "schema": "benchmark-observations@0.1.0",
            "run_id": "pilot-run",
            "calls": [
                {
                    "attempts": [
                        {
                            "attempt_id": "attempt-0",
                            "attempt_index": 0,
                            "retryable": False,
                            "status": "succeeded",
                        }
                    ],
                    "call_index": 0,
                    "elapsed_microseconds": 10,
                    "failure_code": None,
                    "logical_call_id": "call-0",
                    "retry_count": 0,
                    "returned_model_id": "model-a",
                    "status": "succeeded",
                    "usage": {
                        "cache_creation_input_tokens": None,
                        "cache_read_input_tokens": 3,
                        "input_tokens": 11,
                        "output_tokens": 7,
                    },
                },
                {
                    "attempts": [
                        {
                            "attempt_id": "attempt-1",
                            "attempt_index": 0,
                            "retryable": False,
                            "status": "succeeded",
                        }
                    ],
                    "call_index": 1,
                    "elapsed_microseconds": None,
                    "failure_code": None,
                    "logical_call_id": "call-1",
                    "retry_count": 0,
                    "returned_model_id": None,
                    "status": "succeeded",
                    "usage": {
                        "cache_creation_input_tokens": None,
                        "cache_read_input_tokens": None,
                        "input_tokens": None,
                        "output_tokens": 5,
                    },
                },
            ],
        }
    )

    summary = pilot.build_operational_summary(
        observations,
        active_host_elapsed_microseconds=123,
    )
    assert summary.logical_calls == 2
    assert summary.provider_attempts == 2
    assert summary.retries == 0
    assert summary.returned_model.available_calls == 1
    assert summary.returned_model.unavailable_calls == 1
    assert summary.latency.available_calls == 1
    assert summary.latency.total_if_complete is None
    assert summary.usage.input_tokens.available_calls == 1
    assert summary.usage.input_tokens.total_if_complete is None
    assert summary.usage.output_tokens.available_calls == 2
    assert summary.usage.output_tokens.total_if_complete == 12
    assert summary.active_host_elapsed_microseconds == 123
    assert summary.stage_totals == ()
