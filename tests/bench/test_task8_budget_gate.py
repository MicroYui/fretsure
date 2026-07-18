from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from fretsure.bench.artifacts import parse_canonical_json_bytes
from fretsure.bench.contracts import canonical_json_bytes
from fretsure.bench.preregistration import preregistration_from_bytes

ROOT = Path(__file__).resolve().parents[2]
PREREG_PATH = ROOT / "docs" / "experiments" / "2026-07-17-benchmark-v2-prereg.json"
PRICING_SOURCE_PATH = (
    ROOT / "docs" / "experiments" / "2026-07-18-gpt-5.6-sol-pricing-source.json"
)
PRICING_CONTRACT_PATH = (
    ROOT / "docs" / "experiments" / "2026-07-18-gpt-5.6-sol-pricing-contract.json"
)
SPEC = importlib.util.spec_from_file_location(
    "fretsure_test_task8_budget_gate",
    ROOT / "scripts" / "task8_budget_gate.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
gate = cast(Any, MODULE)


def _rates(
    *,
    input_tokens: int = 1_000_000,
    output_tokens: int = 2_000_000,
    cache_creation_input_tokens: int = 500_000,
    cache_read_input_tokens: int = 250_000,
) -> dict[str, int]:
    return {
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }


def _ceilings(*, output_tokens: int = 16_384) -> dict[str, int]:
    return {
        "cache_creation_input_tokens": 50,
        "cache_read_input_tokens": 20,
        "input_tokens": 100,
        "output_tokens": output_tokens,
    }


def _pricing(
    *,
    model: str = "gpt-5.6-sol",
    currency: str = "USD",
    rates: dict[str, int] | None = None,
    ceilings: dict[str, int] | None = None,
    fixed: int = 3,
    ceil_each: bool = False,
) -> Any:
    return gate.build_token_pricing_contract(
        billing_provider_id="local-proxy",
        billing_model_id=model,
        currency=currency,
        rates_microunits_per_million_tokens=_rates() if rates is None else rates,
        fixed_microunits_per_attempt=fixed,
        billable_token_ceiling_per_attempt=(
            _ceilings() if ceilings is None else ceilings
        ),
        ceil_each_component_per_attempt=ceil_each,
        evidence_source_ref="provider-price-sheet:2026-07-18",
        evidence_captured_at_utc="2026-07-18T03:04:05Z",
        evidence_source_sha256="a" * 64,
    )


def _stage(
    stage: str,
    *,
    logical_calls: int,
    retries: int,
    max_output_tokens: int,
) -> Any:
    return gate.OperationalStageTotals(
        stage=stage,
        logical_calls=logical_calls,
        retries=retries,
        requested_output_tokens=logical_calls * max_output_tokens,
        attempt_reserved_output_tokens=(logical_calls + retries)
        * max_output_tokens,
    )


def _max_stage_totals(pair_count: int) -> tuple[Any, ...]:
    return (
        _stage(
            "proposal_raw",
            logical_calls=pair_count * 2,
            retries=pair_count * 4,
            max_output_tokens=2_048,
        ),
        _stage(
            "repair",
            logical_calls=pair_count * 8,
            retries=pair_count * 16,
            max_output_tokens=1_024,
        ),
        _stage(
            "critic",
            logical_calls=pair_count,
            retries=pair_count * 2,
            max_output_tokens=512,
        ),
    )


def _usage(
    *,
    pair_count: int = 4,
    logical_calls: int = 44,
    attempts: int = 132,
    requested: int = 51_200,
    reserved: int = 153_600,
    elapsed: int = 4_026_000_000,
    input_tokens: int | None = 13_200,
    output_tokens: int | None = 20_000,
    cache_creation_input_tokens: int | None = 2_000,
    cache_read_input_tokens: int | None = 1_000,
    stage_totals: tuple[Any, ...] | None = None,
    usage_covers_all_attempts: bool = True,
) -> Any:
    return gate.OperationalUsage(
        pair_count=pair_count,
        logical_calls=logical_calls,
        attempts=attempts,
        requested_output_tokens=requested,
        attempt_reserved_output_tokens=reserved,
        elapsed_microseconds=elapsed,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        stage_totals=(
            _max_stage_totals(pair_count) if stage_totals is None else stage_totals
        ),
        usage_covers_all_attempts=usage_covers_all_attempts,
    )


@pytest.fixture(scope="module")
def preregistration() -> Any:
    return preregistration_from_bytes(PREREG_PATH.read_bytes())


def test_pricing_contract_is_exact_canonical_and_raw_hash_bound() -> None:
    pricing = _pricing()
    wire = pricing.to_dict()

    assert wire == {
        "billable_token_ceiling_per_attempt": _ceilings(),
        "billing_model_id": "gpt-5.6-sol",
        "billing_provider_id": "local-proxy",
        "ceil_each_component_per_attempt": False,
        "currency": "USD",
        "evidence": {
            "captured_at_utc": "2026-07-18T03:04:05Z",
            "source_ref": "provider-price-sheet:2026-07-18",
            "source_sha256": "a" * 64,
        },
        "fixed_microunits_per_attempt": 3,
        "rates_microunits_per_million_tokens": _rates(),
        "schema": "benchmark-token-pricing-contract@0.1.0",
        "token_unit": 1_000_000,
    }
    assert pricing.wire_json == canonical_json_bytes(wire)
    assert pricing.raw_sha256 == hashlib.sha256(pricing.wire_json).hexdigest()
    assert gate.token_pricing_contract_from_bytes(pricing.wire_json) == pricing

    noncanonical = json.dumps(wire, indent=2).encode()
    with pytest.raises(gate.Task8BudgetGateError, match="canonical"):
        gate.token_pricing_contract_from_bytes(noncanonical)


def test_committed_openai_reference_price_is_canonical_and_mechanical() -> None:
    source_bytes = PRICING_SOURCE_PATH.read_bytes()
    source = cast(dict[str, object], parse_canonical_json_bytes(source_bytes))
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    assert source_sha256 == "6293e6c59908b53335e4725f3a36434966ee2e8a083cd79513b2f46746144b0f"
    assert source["model_id"] == "gpt-5.6-sol"
    assert source["service_tier"] == "standard"
    assert source["source_pricing_ref"] == "https://developers.openai.com/api/docs/pricing"

    pricing = gate.token_pricing_contract_from_bytes(PRICING_CONTRACT_PATH.read_bytes())
    assert pricing.billing_provider_id == "openai-standard-reference"
    assert pricing.rates == {
        "cache_creation_input_tokens": 6_250_000,
        "cache_read_input_tokens": 500_000,
        "input_tokens": 5_000_000,
        "output_tokens": 30_000_000,
    }
    assert pricing.ceilings == {
        "cache_creation_input_tokens": 4_096,
        "cache_read_input_tokens": 4_096,
        "input_tokens": 4_096,
        "output_tokens": 2_048,
    }
    evidence = cast(dict[str, object], pricing.to_dict()["evidence"])
    assert evidence["source_sha256"] == source_sha256
    assert gate.pilot_worst_case_budget(pricing)["cost_microunits"] == 10_960_896


@pytest.mark.parametrize(
    ("mutation", "field"),
    [
        (lambda wire: wire.update(currency="usd"), "currency"),
        (
            lambda wire: cast(dict[str, object], wire["evidence"]).update(
                captured_at_utc="2026-07-18 03:04:05"
            ),
            "captured_at_utc",
        ),
        (
            lambda wire: cast(
                dict[str, object], wire["rates_microunits_per_million_tokens"]
            ).update(input_tokens=-1),
            "input_tokens",
        ),
        (lambda wire: wire.update(token_unit=1_000), "token_unit"),
    ],
)
def test_pricing_contract_rejects_currency_timestamp_rate_and_unit_drift(
    mutation: Any,
    field: str,
) -> None:
    wire = _pricing().to_dict()
    mutation(wire)
    with pytest.raises(gate.Task8BudgetGateError) as caught:
        gate.token_pricing_contract_from_dict(wire)
    assert caught.value.field.endswith(field)


def test_actual_cost_uses_one_exact_ceil_and_null_is_never_zero() -> None:
    pricing = _pricing()
    usage = _usage(
        pair_count=1,
        logical_calls=3,
        attempts=5,
        requested=4_608,
        reserved=7_168,
        elapsed=10,
        input_tokens=1,
        output_tokens=1,
        cache_creation_input_tokens=1,
        cache_read_input_tokens=1,
        stage_totals=(
            _stage(
                "proposal_raw",
                logical_calls=2,
                retries=1,
                max_output_tokens=2_048,
            ),
            _stage(
                "repair",
                logical_calls=0,
                retries=0,
                max_output_tokens=1_024,
            ),
            _stage(
                "critic",
                logical_calls=1,
                retries=1,
                max_output_tokens=512,
            ),
        ),
    )

    cost = gate.cost_for_usage(pricing, usage)
    # ceil((1*1_000_000 + 1*2_000_000 + 1*500_000 + 1*250_000)/1e6)
    # plus five fixed 3-microunit attempt charges.
    assert cost.to_dict() == {"availability": "available", "microunits": 19}

    missing = gate.cost_for_usage(
        pricing,
        _usage(output_tokens=None),
    )
    assert missing.to_dict() == {
        "availability": "usage_unavailable",
        "microunits": None,
    }

    incomplete = gate.cost_for_usage(
        pricing,
        _usage(usage_covers_all_attempts=False),
    )
    assert incomplete.to_dict() == {
        "availability": "incomplete_attempt_usage",
        "microunits": None,
    }


def test_per_attempt_rounding_requires_per_attempt_usage_for_actual_cost() -> None:
    estimate = gate.cost_for_usage(_pricing(ceil_each=True), _usage())
    assert estimate.to_dict() == {
        "availability": "per_attempt_usage_breakdown_unavailable",
        "microunits": None,
    }


def test_pilot_and_formal_worst_case_iterate_exact_call_templates(
    preregistration: Any,
) -> None:
    pricing = _pricing()
    pilot = gate.pilot_worst_case_budget(pricing)
    formal = gate.formal_worst_case_budget(preregistration, pricing)

    assert pilot["resources"] == {
        "attempt_reserved_output_tokens": 153_600,
        "attempts": 132,
        "host_wall_availability": "available",
        "host_wall_microseconds": 5_400_000_000,
        "logical_calls": 44,
        "max_retries": 88,
        "paired_samples": 4,
        "provider_timeout_envelope_microseconds": 4_026_000_000,
        "requested_output_tokens": 51_200,
        "runner_recorded_elapsed_ceiling_microseconds": 5_400_000_000,
        "stage_totals": {
            "critic": {
                "attempt_reserved_output_tokens": 6_144,
                "logical_calls": 4,
                "max_retries": 8,
                "requested_output_tokens": 2_048,
            },
            "proposal_raw": {
                "attempt_reserved_output_tokens": 49_152,
                "logical_calls": 8,
                "max_retries": 16,
                "requested_output_tokens": 16_384,
            },
            "repair": {
                "attempt_reserved_output_tokens": 98_304,
                "logical_calls": 32,
                "max_retries": 64,
                "requested_output_tokens": 32_768,
            },
        },
    }
    assert pilot["call_templates"] == [
        {"count": 8, "max_output_tokens": 2_048, "stage": "proposal_raw"},
        {"count": 32, "max_output_tokens": 1_024, "stage": "repair"},
        {"count": 4, "max_output_tokens": 512, "stage": "critic"},
    ]
    assert pilot["cost_microunits"] == 324_756

    resources = formal["resources"]
    assert resources == {
        "attempt_reserved_output_tokens": 278_714_880,
        "attempts": 165_990,
        "host_wall_availability": "unavailable",
        "host_wall_microseconds": None,
        "logical_calls": 55_330,
        "max_retries": 110_660,
        "paired_samples": 5_030,
        "provider_timeout_envelope_microseconds": 5_062_695_000_000,
        "requested_output_tokens": 92_904_960,
        "runner_recorded_elapsed_ceiling_microseconds": 5_184_000_000_000,
        "stage_totals": {
            "critic": {
                "attempt_reserved_output_tokens": 7_726_080,
                "logical_calls": 5_030,
                "max_retries": 10_060,
                "requested_output_tokens": 2_575_360,
            },
            "proposal_raw": {
                "attempt_reserved_output_tokens": 147_371_520,
                "logical_calls": 10_060,
                "max_retries": 20_120,
                "requested_output_tokens": 49_123_840,
            },
            "repair": {
                "attempt_reserved_output_tokens": 123_617_280,
                "logical_calls": 40_240,
                "max_retries": 80_480,
                "requested_output_tokens": 41_205_760,
            },
        },
    }
    assert formal["cost_microunits"] == 579_506_430


def test_per_attempt_per_component_rounding_is_applied_to_worst_case() -> None:
    pricing = _pricing(
        rates=_rates(
            input_tokens=1,
            output_tokens=1,
            cache_creation_input_tokens=1,
            cache_read_input_tokens=1,
        ),
        fixed=2,
        ceil_each=True,
    )
    pilot = gate.pilot_worst_case_budget(pricing)
    # Four positive token components each ceil to one microunit, plus fixed two.
    assert pilot["cost_microunits"] == 132 * 6


def test_global_rounding_is_once_across_all_worst_case_components() -> None:
    pricing = _pricing(
        rates=_rates(
            input_tokens=1,
            output_tokens=1,
            cache_creation_input_tokens=1,
            cache_read_input_tokens=1,
        ),
        fixed=0,
        ceil_each=False,
    )
    assert gate.pilot_worst_case_budget(pricing)["cost_microunits"] == 1


def test_gate_scales_projection_with_ceil_and_never_subtracts_pilot_from_formal(
    preregistration: Any,
) -> None:
    pricing = _pricing()
    usage = _usage()
    artifact = gate.build_formal_budget_gate(
        preregistration=preregistration,
        pricing_contract=pricing,
        expected_pricing_contract_sha256=pricing.raw_sha256,
        operational_usage=usage,
        pilot_receipt_sha256="b" * 64,
        pilot_summary_sha256=usage.raw_sha256,
        formal_maximum_spend_microunits=None,
    )
    wire = artifact.to_dict()
    formal = cast(dict[str, object], wire["formal"])
    worst = cast(dict[str, object], formal["worst_case_remaining"])
    projection = cast(dict[str, object], formal["pilot_informed_projection"])

    assert cast(dict[str, object], worst["resources"])["logical_calls"] == 55_330
    assert projection["resources"] == {
        "attempt_reserved_output_tokens": 278_714_880,
        "attempts": 165_990,
        "host_wall_availability": "unavailable",
        "host_wall_microseconds": None,
        "logical_calls": 55_330,
        "paired_samples": 5_030,
        "projected_provider_elapsed_microseconds": 5_062_695_000_000,
        "projected_retries": 110_660,
        "requested_output_tokens": 92_904_960,
        "stage_totals": {
            "critic": {
                "attempt_reserved_output_tokens": 7_726_080,
                "logical_calls": 5_030,
                "projected_retries": 10_060,
                "requested_output_tokens": 2_575_360,
            },
            "proposal_raw": {
                "attempt_reserved_output_tokens": 147_371_520,
                "logical_calls": 10_060,
                "projected_retries": 20_120,
                "requested_output_tokens": 49_123_840,
            },
            "repair": {
                "attempt_reserved_output_tokens": 123_617_280,
                "logical_calls": 40_240,
                "projected_retries": 80_480,
                "requested_output_tokens": 41_205_760,
            },
        },
    }
    assert projection["usage"] == {
        "cache_creation_input_tokens": 2_515_000,
        "cache_read_input_tokens": 1_257_500,
        "input_tokens": 16_599_000,
        "output_tokens": 25_150_000,
    }
    assert projection["status"] == "projection_not_authorization"
    assert wire["external_ceiling"] == {
        "maximum_spend_microunits": None,
        "status": "authorization_required",
    }
    assert wire["authorization_statement"] == (
        "pricing_actual_and_projected_costs_do_not_authorize_collection"
    )


def test_gate_null_usage_propagates_to_actual_and_projection(
    preregistration: Any,
) -> None:
    pricing = _pricing()
    usage = _usage(cache_read_input_tokens=None)
    artifact = gate.build_formal_budget_gate(
        preregistration=preregistration,
        pricing_contract=pricing,
        expected_pricing_contract_sha256=pricing.raw_sha256,
        operational_usage=usage,
        pilot_receipt_sha256="b" * 64,
        pilot_summary_sha256=usage.raw_sha256,
        formal_maximum_spend_microunits=None,
    )
    wire = artifact.to_dict()
    pilot = cast(dict[str, object], wire["pilot"])
    formal = cast(dict[str, object], wire["formal"])
    projection = cast(dict[str, object], formal["pilot_informed_projection"])

    assert pilot["actual_cost"] == {
        "availability": "usage_unavailable",
        "microunits": None,
    }
    assert cast(dict[str, object], projection["usage"])[
        "cache_read_input_tokens"
    ] is None
    assert projection["cost"] == {
        "availability": "usage_unavailable",
        "microunits": None,
    }

    incomplete_usage = _usage(usage_covers_all_attempts=False)
    incomplete_artifact = gate.build_formal_budget_gate(
        preregistration=preregistration,
        pricing_contract=pricing,
        expected_pricing_contract_sha256=pricing.raw_sha256,
        operational_usage=incomplete_usage,
        pilot_receipt_sha256="b" * 64,
        pilot_summary_sha256=incomplete_usage.raw_sha256,
        formal_maximum_spend_microunits=None,
    ).to_dict()
    incomplete_pilot = cast(dict[str, object], incomplete_artifact["pilot"])
    incomplete_formal = cast(dict[str, object], incomplete_artifact["formal"])
    incomplete_projection = cast(
        dict[str, object], incomplete_formal["pilot_informed_projection"]
    )
    unavailable = {
        "availability": "incomplete_attempt_usage",
        "microunits": None,
    }
    assert incomplete_pilot["actual_cost"] == unavailable
    assert incomplete_projection["cost"] == unavailable


def test_operational_stage_totals_are_exact_and_reconcile_to_aggregates() -> None:
    usage = _usage()
    assert gate.operational_usage_from_bytes(usage.wire_json) == usage

    changed = usage.to_dict()
    repair = cast(dict[str, object], changed["stage_totals"])["repair"]
    cast(dict[str, object], repair)["requested_output_tokens"] = 1
    with pytest.raises(gate.Task8BudgetGateError, match="stage_totals"):
        gate.operational_usage_from_dict(changed)


def test_gate_rejects_pricing_hash_model_ceiling_and_projection_errors(
    preregistration: Any,
) -> None:
    pricing = _pricing()
    other_model_pricing = _pricing(model="other-model")
    low_output_ceiling_pricing = _pricing(ceilings=_ceilings(output_tokens=1_000))
    usage = _usage()
    excessive_elapsed_usage = _usage(elapsed=5_400_000_000)
    kwargs = {
        "preregistration": preregistration,
        "pricing_contract": pricing,
        "expected_pricing_contract_sha256": pricing.raw_sha256,
        "operational_usage": usage,
        "pilot_receipt_sha256": "b" * 64,
        "pilot_summary_sha256": usage.raw_sha256,
        "formal_maximum_spend_microunits": None,
    }

    for changed, field in (
        ({**kwargs, "expected_pricing_contract_sha256": "0" * 64}, "pricing"),
        (
            {
                **kwargs,
                "pricing_contract": other_model_pricing,
                "expected_pricing_contract_sha256": other_model_pricing.raw_sha256,
            },
            "model",
        ),
        (
            {
                **kwargs,
                "pricing_contract": low_output_ceiling_pricing,
                "expected_pricing_contract_sha256": low_output_ceiling_pricing.raw_sha256,
            },
            "output_tokens",
        ),
        ({**kwargs, "formal_maximum_spend_microunits": 1}, "maximum_spend"),
        (
            {
                **kwargs,
                "operational_usage": excessive_elapsed_usage,
                "pilot_summary_sha256": excessive_elapsed_usage.raw_sha256,
            },
            "projection",
        ),
    ):
        with pytest.raises(gate.Task8BudgetGateError, match=field):
            gate.build_formal_budget_gate(**changed)


def test_positive_external_ceiling_must_equal_mechanical_formal_worst_case(
    preregistration: Any,
) -> None:
    pricing = _pricing()
    usage = _usage()
    formal_cost = gate.formal_worst_case_budget(preregistration, pricing)[
        "cost_microunits"
    ]
    artifact = gate.build_formal_budget_gate(
        preregistration=preregistration,
        pricing_contract=pricing,
        expected_pricing_contract_sha256=pricing.raw_sha256,
        operational_usage=usage,
        pilot_receipt_sha256="b" * 64,
        pilot_summary_sha256=usage.raw_sha256,
        formal_maximum_spend_microunits=formal_cost,
    )
    assert artifact.to_dict()["external_ceiling"] == {
        "maximum_spend_microunits": formal_cost,
        "status": "external_ceiling_declared",
    }


def test_exact_zero_external_ceiling_is_declared_not_treated_as_missing(
    preregistration: Any,
) -> None:
    pricing = _pricing(
        rates=_rates(
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        ),
        fixed=0,
    )
    usage = _usage()
    assert gate.formal_worst_case_budget(preregistration, pricing)[
        "cost_microunits"
    ] == 0
    artifact = gate.build_formal_budget_gate(
        preregistration=preregistration,
        pricing_contract=pricing,
        expected_pricing_contract_sha256=pricing.raw_sha256,
        operational_usage=usage,
        pilot_receipt_sha256="b" * 64,
        pilot_summary_sha256=usage.raw_sha256,
        formal_maximum_spend_microunits=0,
    )
    assert artifact.to_dict()["external_ceiling"] == {
        "maximum_spend_microunits": 0,
        "status": "external_ceiling_declared",
    }


def test_gate_round_trip_recomputes_bindings_and_rejects_tampering(
    preregistration: Any,
) -> None:
    pricing = _pricing()
    usage = _usage()
    artifact = gate.build_formal_budget_gate(
        preregistration=preregistration,
        pricing_contract=pricing,
        expected_pricing_contract_sha256=pricing.raw_sha256,
        operational_usage=usage,
        pilot_receipt_sha256="b" * 64,
        pilot_summary_sha256=usage.raw_sha256,
        formal_maximum_spend_microunits=None,
    )
    assert gate.formal_budget_gate_from_bytes(
        artifact.wire_json,
        preregistration=preregistration,
        pricing_contract=pricing,
        expected_pricing_contract_sha256=pricing.raw_sha256,
        operational_usage=usage,
        pilot_receipt_sha256="b" * 64,
        pilot_summary_sha256=usage.raw_sha256,
    ) == artifact

    changed = copy.deepcopy(artifact.to_dict())
    changed["billing"]["currency"] = "EUR"
    with pytest.raises(gate.Task8BudgetGateError, match="differs"):
        gate.formal_budget_gate_from_bytes(
            canonical_json_bytes(changed),
            preregistration=preregistration,
            pricing_contract=pricing,
            expected_pricing_contract_sha256=pricing.raw_sha256,
            operational_usage=usage,
            pilot_receipt_sha256="b" * 64,
            pilot_summary_sha256=usage.raw_sha256,
        )


def test_cli_generates_and_checks_exact_artifact(
    tmp_path: Path,
    preregistration: Any,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pricing = _pricing()
    usage = _usage()
    prereg_path = tmp_path / "prereg.json"
    pricing_path = tmp_path / "pricing.json"
    usage_path = tmp_path / "usage.json"
    receipt_path = tmp_path / "receipt.json"
    output = tmp_path / "gate.json"
    prereg_path.write_bytes(preregistration.wire_json)
    pricing_path.write_bytes(pricing.wire_json)
    usage_path.write_bytes(usage.wire_json)
    receipt_path.write_bytes(b"pilot receipt fixture")
    # This test owns CLI mechanics, not the preregistration module's expensive
    # 503-item validator, which already produced this strict session fixture.
    monkeypatch.setattr(
        gate,
        "preregistration_from_bytes",
        lambda data: preregistration
        if data == preregistration.wire_json
        else pytest.fail("CLI read unexpected preregistration bytes"),
    )
    argv = [
        "--prereg",
        str(prereg_path),
        "--pricing-contract",
        str(pricing_path),
        "--expected-pricing-sha256",
        pricing.raw_sha256,
        "--pilot-summary",
        str(usage_path),
        "--pilot-receipt",
        str(receipt_path),
        "--output",
        str(output),
    ]

    assert gate.main(argv) == 0
    generated = gate.formal_budget_gate_from_bytes(
        output.read_bytes(),
        preregistration=preregistration,
        pricing_contract=pricing,
        expected_pricing_contract_sha256=pricing.raw_sha256,
        operational_usage=usage,
        pilot_receipt_sha256=hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
        pilot_summary_sha256=usage.raw_sha256,
    )
    summary = json.loads(capsys.readouterr().out)
    assert summary["gate_sha256"] == generated.raw_sha256
    assert summary["authorization_status"] == "authorization_required"

    assert gate.main([*argv, "--check"]) == 0
    capsys.readouterr()
    output.write_bytes(output.read_bytes() + b"\n")
    assert gate.main([*argv, "--check"]) == 1
    assert "differs byte-for-byte" in capsys.readouterr().err
