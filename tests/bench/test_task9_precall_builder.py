from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, cast

from fretsure.bench.contracts import canonical_json_bytes
from fretsure.bench.precall import pre_call_config_from_bytes

ROOT = Path(__file__).resolve().parents[2]
PREREG = ROOT / "docs/experiments/2026-07-17-benchmark-v2-prereg.json"
PRICING = ROOT / "docs/experiments/2026-07-18-gpt-5.6-sol-pricing-contract-v2.json"
ENVELOPE = (
    ROOT / "docs/experiments/2026-07-18-gpt-5.6-sol-formal-billing-envelope.json"
)
SPEC = importlib.util.spec_from_file_location(
    "fretsure_test_task9_precall_builder",
    ROOT / "scripts/build_benchmark_precall.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
builder = cast(Any, MODULE)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _gate() -> dict[str, object]:
    maximum = 1_167_905_640_000
    return {
        "authorization_statement": (
            "pricing_actual_and_projected_costs_do_not_authorize_collection"
        ),
        "billing": {
            "billing_model_id": "gpt-5.6-sol",
            "billing_provider_id": "openai-standard-reference",
            "currency": "USD",
            "formal_input_upper_bound_method": "utf8_bytes_plus_256",
            "token_unit": 1_000_000,
        },
        "bindings": {
            "formal_billing_envelope_raw_sha256": _sha(ENVELOPE),
            "pricing_contract_raw_sha256": _sha(PRICING),
            "pilot_receipt_sha256": "1" * 64,
            "pilot_summary_sha256": "2" * 64,
            "preregistration_raw_sha256": _sha(PREREG),
        },
        "external_ceiling": {
            "maximum_spend_microunits": maximum,
            "status": "external_ceiling_declared",
        },
        "formal": {
            "paired_sample_count": 5_030,
            "pilot_informed_projection": {},
            "worst_case_remaining": {"cost_microunits": maximum},
        },
        "pilot": {},
        "schema": "benchmark-formal-budget-gate@0.3.0",
    }


def _args(gate: Path, output: Path, *, check: bool = False) -> list[str]:
    values = [
        "--prereg",
        str(PREREG),
        "--pricing-contract",
        str(PRICING),
        "--expected-pricing-sha256",
        _sha(PRICING),
        "--formal-billing-envelope",
        str(ENVELOPE),
        "--expected-formal-billing-envelope-sha256",
        _sha(ENVELOPE),
        "--formal-budget-gate",
        str(gate),
        "--expected-formal-budget-gate-sha256",
        _sha(gate),
        "--collection-attempt",
        "1",
        "--execution-git-sha",
        "1" * 40,
        "--uv-lock-sha256",
        "2" * 64,
        "--analysis-binding-kind",
        "analysis_module_sha256",
        "--analysis-code-sha256",
        "3" * 64,
        "--output",
        str(output),
    ]
    return ["--check", *values] if check else values


def test_builder_writes_and_checks_one_gate_bound_pre_call(tmp_path: Path) -> None:
    gate = tmp_path / "gate.json"
    gate.write_bytes(canonical_json_bytes(_gate()))
    output = tmp_path / "pre-call.json"

    assert builder.main(_args(gate, output)) == 0
    config = pre_call_config_from_bytes(output.read_bytes())
    assert config.run_id == "benchmark-v2-formal-20260717-attempt-001"
    assert config.maximum_spend_microunits == 1_167_905_640_000
    assert config.formal_input_token_ceiling == 272_000
    assert config.formal_output_token_ceiling == 128_000
    assert config.formal_budget_gate_raw_sha256 == _sha(gate)
    assert builder.main(_args(gate, output, check=True)) == 0

    occupied = tmp_path / "occupied-pre-call.json"
    original = b"existing historical pre-call bytes"
    occupied.write_bytes(original)
    assert builder.main(_args(gate, occupied)) == 1
    assert occupied.read_bytes() == original


def test_builder_rejects_a_non_authorizing_or_inconsistent_gate(
    tmp_path: Path,
) -> None:
    wire = _gate()
    cast(dict[str, object], wire["external_ceiling"])["status"] = (
        "authorization_required"
    )
    gate = tmp_path / "gate.json"
    gate.write_bytes(canonical_json_bytes(wire))
    output = tmp_path / "must-not-exist.json"

    assert builder.main(_args(gate, output)) == 1
    assert not output.exists()

    wire = _gate()
    wire["authorization_statement"] = "wrong-statement"
    gate.write_bytes(canonical_json_bytes(wire))

    assert builder.main(_args(gate, output)) == 1
    assert not output.exists()
