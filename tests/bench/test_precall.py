from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import cast

import pytest

from fretsure.agent.critic import CRITIC_MAX_TOKENS
from fretsure.agent.repair import REPAIR_MAX_TOKENS
from fretsure.bench.contracts import canonical_json_bytes
from fretsure.bench.experiment import EXPERIMENT_MAX_REPAIR_ITERS
from fretsure.bench.precall import (
    BenchmarkPreCallConfig,
    PreCallConfigError,
    build_pre_call_config,
    current_runtime_identity,
    pre_call_artifact_budget,
    pre_call_config_from_bytes,
    pre_call_config_from_dict,
    require_explicit_spend_confirmation,
    require_live_authorization,
    validate_current_runtime,
)
from fretsure.bench.preregistration import (
    BenchmarkPreregistration,
    preregistration_from_bytes,
)
from fretsure.llm.client import (
    MAX_PROXY_TEXT_BYTES_PER_TOKEN,
    MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
)


@pytest.fixture(scope="module")
def preregistration() -> BenchmarkPreregistration:
    root = Path(__file__).resolve().parents[2]
    return preregistration_from_bytes(
        (root / "docs/experiments/2026-07-17-benchmark-v2-prereg.json").read_bytes()
    )


def _config(
    preregistration: BenchmarkPreregistration,
    *,
    cost_status: str = "available",
) -> BenchmarkPreCallConfig:
    formal_billing_envelope: dict[str, object] = {
        "billable_token_ceiling_per_attempt": {
            "cache_creation_input_tokens": 272_000,
            "cache_read_input_tokens": 271_999,
            "input_tokens": 271_998,
            "output_tokens": 16_384,
        },
        "enforcement": {
            "input_upper_bound_method": "utf8_bytes_plus_256",
            "required_before": "before_observation_retry_network",
        },
        "pricing_contract_raw_sha256": "4" * 64,
        "schema": "benchmark-formal-billing-envelope@0.1.0",
        "scope": "formal_collection",
    }
    return build_pre_call_config(
        preregistration,
        collection_attempt=1,
        execution_git_sha="1" * 40,
        uv_lock_sha256="2" * 64,
        analysis_binding_kind="analysis_module_sha256",
        analysis_code_sha256="3" * 64,
        runtime_identity=current_runtime_identity(),
        formal_billing_envelope=formal_billing_envelope,
        formal_billing_envelope_raw_sha256=hashlib.sha256(
            canonical_json_bytes(formal_billing_envelope)
        ).hexdigest(),
        cost_status=cost_status,
        currency=None if cost_status != "available" else "USD",
        maximum_spend_microunits=None if cost_status != "available" else 1_000_000,
        pricing_contract_sha256=None if cost_status != "available" else "4" * 64,
        formal_budget_gate_raw_sha256=(
            None if cost_status != "available" else "5" * 64
        ),
    )


def test_pre_call_round_trip_binds_runtime_model_contracts_and_budget(
    preregistration: BenchmarkPreregistration,
) -> None:
    config = _config(preregistration)

    assert pre_call_config_from_bytes(config.wire_json) == config
    validate_current_runtime(config)
    require_live_authorization(config)
    maximum, reservation = pre_call_artifact_budget(config)
    assert maximum["max_logical_calls"] == 55_330
    assert maximum["max_attempts"] == 165_990
    assert maximum["max_recorded_provider_call_elapsed_microseconds"] == (
        5_184_000_000_000
    )
    assert reservation["logical_calls"] == 10
    assert reservation["attempts"] == 30
    assert reservation["requested_output_tokens"] == 24_672
    assert reservation["recorded_provider_call_elapsed_microseconds"] == 915_000_000
    assert config.requested_model_id == config.allowed_returned_model_id == "gpt-5.6-sol"
    assert config.analysis_code_sha256 == "3" * 64
    assert config.collection_attempt == 1
    assert config.run_id == "benchmark-v2-formal-20260717-attempt-001"
    assert config.to_dict()["budget"]["ceiling_scope"] == (  # type: ignore[index]
        "single_collection_attempt_nontransferable"
    )
    assert config.formal_input_token_ceiling == 271_998
    assert config.formal_output_token_ceiling == 16_384
    assert config.maximum_spend_microunits == 1_000_000
    assert config.formal_budget_gate_raw_sha256 == "5" * 64
    envelope = cast(dict[str, object], config.to_dict()["billing_envelope"])
    assert envelope["raw_sha256"] == hashlib.sha256(
        canonical_json_bytes(envelope["wire"])
    ).hexdigest()


def test_pre_call_rejects_binding_budget_and_model_drift(
    preregistration: BenchmarkPreregistration,
) -> None:
    original = _config(preregistration).to_dict()
    mutations: list[tuple[str, object]] = []

    bad_git = copy.deepcopy(original)
    bad_git["execution"]["execution_git_sha"] = "A" * 40  # type: ignore[index]
    mutations.append(("execution.execution_git_sha", bad_git))

    bad_prereg = copy.deepcopy(original)
    bad_prereg["preregistration_raw_sha256"] = "0" * 64
    mutations.append(("preregistration_raw_sha256", bad_prereg))

    bad_attempt = copy.deepcopy(original)
    bad_attempt["collection_attempt"] = 0
    mutations.append(("collection_attempt", bad_attempt))

    bad_run_id = copy.deepcopy(original)
    bad_run_id["run_id"] = "benchmark-v2-formal-20260717-attempt-002"
    mutations.append(("run_id", bad_run_id))

    bad_model = copy.deepcopy(original)
    bad_model["model"]["allowed_returned_model_id"] = "another-model"  # type: ignore[index]
    mutations.append(("model", bad_model))

    bad_budget = copy.deepcopy(original)
    bad_budget["budget"]["max_logical_calls"] += 1  # type: ignore[index]
    mutations.append(("budget.max_logical_calls", bad_budget))

    bad_envelope_hash = copy.deepcopy(original)
    bad_envelope_hash["billing_envelope"]["raw_sha256"] = "0" * 64  # type: ignore[index]
    mutations.append(("billing_envelope.raw_sha256", bad_envelope_hash))

    bad_enforcement = copy.deepcopy(original)
    bad_enforcement["billing_envelope"]["wire"]["enforcement"][  # type: ignore[index]
        "input_upper_bound_method"
    ] = "tokenizer_estimate"
    mutations.append(
        (
            "billing_envelope.wire.enforcement.input_upper_bound_method",
            bad_enforcement,
        )
    )

    bad_pricing_binding = copy.deepcopy(original)
    bad_pricing_binding["budget"]["cost"]["pricing_contract_sha256"] = "6" * 64  # type: ignore[index]
    mutations.append(("budget.cost.pricing_contract_sha256", bad_pricing_binding))

    for field, value in mutations:
        with pytest.raises(PreCallConfigError) as caught:
            pre_call_config_from_dict(value)
        assert caught.value.field == field


def test_each_attempt_has_a_distinct_nontransferable_pre_call_declaration(
    preregistration: BenchmarkPreregistration,
) -> None:
    first = _config(preregistration)
    second_wire = first.to_dict()
    second_wire["collection_attempt"] = 2
    second_wire["run_id"] = "benchmark-v2-formal-20260717-attempt-002"
    second = pre_call_config_from_dict(second_wire)

    assert first.run_id != second.run_id
    assert first.wire_json != second.wire_json
    assert first.to_dict()["budget"] == second.to_dict()["budget"]


def test_pair_envelopes_and_scheduled_units_reconcile_to_full_budget(
    preregistration: BenchmarkPreregistration,
) -> None:
    wire = preregistration.to_dict()
    budgets = cast(dict[str, object], wire["budgets"])
    per_item = cast(list[dict[str, object]], budgets["per_item"])
    full = cast(dict[str, object], budgets["full_corpus"])
    schedule = cast(dict[str, object], wire["schedule"])
    units = cast(list[dict[str, object]], schedule["collection_schedule"])
    proposal_tokens = {
        cast(str, item["item_id"]): cast(int, item["proposal_raw_max_tokens"])
        for item in per_item
    }

    for item in per_item:
        pair = cast(dict[str, object], item["paired_sample_maximum_envelope"])
        assert pair["logical_calls"] == 11
        assert pair["attempts"] == 33

    calls = 0
    attempts = 0
    tokens = 0
    maximum_agent_tokens = 0
    for unit in units:
        proposal = proposal_tokens[cast(str, unit["item_id"])]
        if unit["arm"] == "agent":
            unit_calls = 1 + EXPERIMENT_MAX_REPAIR_ITERS + 1
            unit_tokens = (
                proposal
                + EXPERIMENT_MAX_REPAIR_ITERS * REPAIR_MAX_TOKENS
                + CRITIC_MAX_TOKENS
            )
            maximum_agent_tokens = max(maximum_agent_tokens, unit_tokens)
        else:
            assert unit["arm"] == "raw"
            unit_calls = 1
            unit_tokens = proposal
        calls += unit_calls
        attempts += unit_calls * 3
        tokens += unit_tokens

    assert calls == full["logical_calls_total"]
    assert attempts == full["maximum_attempts"]
    assert tokens == full["requested_output_tokens_total"]
    assert tokens * 3 == full["attempt_reserved_output_tokens"]
    assert tokens * MAX_PROXY_TEXT_BYTES_PER_TOKEN == full["response_text_bytes"]
    assert attempts * MAX_PROXY_TRANSPORT_RESPONSE_BYTES == full[
        "transport_response_bytes"
    ]
    assert budgets["reserve_before_next_scheduled_unit"] == {
        "attempts": 30,
        "logical_calls": 10,
        "requested_output_tokens": maximum_agent_tokens,
        "response_text_bytes": maximum_agent_tokens * MAX_PROXY_TEXT_BYTES_PER_TOKEN,
        "transport_response_bytes": 30 * MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
    }


def test_cost_unavailable_and_runtime_drift_fail_before_live_authorization(
    preregistration: BenchmarkPreregistration,
) -> None:
    unavailable = _config(preregistration, cost_status="cost_contract_unavailable")
    with pytest.raises(PreCallConfigError) as cost:
        require_live_authorization(unavailable)
    assert cost.value.field == "budget.cost"

    drifted = _config(preregistration).to_dict()
    drifted["execution"]["architecture"] = "different-architecture"  # type: ignore[index]
    parsed = pre_call_config_from_dict(drifted)
    with pytest.raises(PreCallConfigError) as runtime:
        validate_current_runtime(parsed)
    assert runtime.value.field == "execution.architecture"


def test_explicit_spend_confirmation_requires_an_exact_integer_match(
    preregistration: BenchmarkPreregistration,
) -> None:
    config = _config(preregistration)

    require_explicit_spend_confirmation(config, 1_000_000)
    for supplied in (999_999, 1_000_001, 1_000_000.0, True):
        with pytest.raises(PreCallConfigError) as caught:
            require_explicit_spend_confirmation(config, supplied)
        assert caught.value.field == "supplied_maximum_spend_microunits"

    unavailable = _config(preregistration, cost_status="cost_contract_unavailable")
    with pytest.raises(PreCallConfigError) as unavailable_error:
        require_explicit_spend_confirmation(unavailable, 1_000_000)
    assert unavailable_error.value.field == "budget.cost"


def test_public_constructor_cannot_bypass_helper_revalidation(
    preregistration: BenchmarkPreregistration,
) -> None:
    wire = _config(preregistration).to_dict()
    wire["budget"]["cost"]["maximum_spend_microunits"] = 1  # type: ignore[index]
    wire["billing_envelope"]["raw_sha256"] = "0" * 64  # type: ignore[index]
    forged = BenchmarkPreCallConfig(canonical_json_bytes(wire))

    checks = (
        lambda: require_explicit_spend_confirmation(forged, 1),
        lambda: require_live_authorization(forged),
        lambda: validate_current_runtime(forged),
        lambda: pre_call_artifact_budget(forged),
    )
    for check in checks:
        with pytest.raises(PreCallConfigError) as caught:
            check()
        assert caught.value.field == "billing_envelope.raw_sha256"
