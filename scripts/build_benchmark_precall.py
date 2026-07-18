#!/usr/bin/env python3
"""Generate or verify one gate-bound benchmark-v2 formal pre-call declaration."""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn, cast

from fretsure.bench.artifacts import parse_canonical_json_bytes
from fretsure.bench.precall import (
    build_pre_call_config,
    current_runtime_identity,
)
from fretsure.bench.preregistration import preregistration_from_bytes

FORMAL_BUDGET_GATE_VERSION = "benchmark-formal-budget-gate@0.2.0"
FORMAL_INPUT_UPPER_BOUND_METHOD = "utf8_bytes_plus_256"


def _fail(detail: str) -> NoReturn:
    raise ValueError(detail)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _expected_sha256(value: object, field: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        _fail(f"{field} must be one lowercase SHA-256 digest")
    return value


def _object(
    value: object,
    field: str,
    *,
    keys: frozenset[str] | None = None,
) -> dict[str, object]:
    if type(value) is not dict:
        _fail(f"{field} must be an exact object")
    exact = cast(dict[str, object], value)
    if keys is not None and frozenset(exact) != keys:
        _fail(f"{field} must contain the exact keys")
    return exact


def _canonical_object(data: bytes, field: str) -> dict[str, object]:
    value = parse_canonical_json_bytes(data)
    return _object(value, field)


def _read_bound_artifact(path: Path, expected_sha256: str, field: str) -> bytes:
    data = path.read_bytes()
    expected = _expected_sha256(expected_sha256, f"expected_{field}_sha256")
    if _sha256(data) != expected:
        _fail(f"{field} does not match its expected SHA-256")
    return data


def _authorized_gate(
    gate_bytes: bytes,
    *,
    preregistration_sha256: str,
    pricing_sha256: str,
    envelope_sha256: str,
    requested_model_id: str,
    currency: str,
) -> int:
    gate = _object(
        _canonical_object(gate_bytes, "formal_budget_gate"),
        "formal_budget_gate",
        keys=frozenset(
            {
                "authorization_statement",
                "billing",
                "bindings",
                "external_ceiling",
                "formal",
                "pilot",
                "schema",
            }
        ),
    )
    if gate["schema"] != FORMAL_BUDGET_GATE_VERSION:
        _fail("formal_budget_gate has the wrong schema")
    bindings = _object(gate["bindings"], "formal_budget_gate.bindings")
    expected_bindings = {
        "formal_billing_envelope_raw_sha256": envelope_sha256,
        "pilot_pricing_contract_raw_sha256": pricing_sha256,
        "preregistration_raw_sha256": preregistration_sha256,
    }
    for field, expected in expected_bindings.items():
        if bindings.get(field) != expected:
            _fail(f"formal_budget_gate.bindings.{field} differs from its input")
    billing = _object(gate["billing"], "formal_budget_gate.billing")
    if billing.get("billing_model_id") != requested_model_id:
        _fail("formal_budget_gate billing model differs from preregistration")
    if billing.get("currency") != currency:
        _fail("formal_budget_gate currency differs from pricing contract")
    if billing.get("formal_input_upper_bound_method") != FORMAL_INPUT_UPPER_BOUND_METHOD:
        _fail("formal_budget_gate has the wrong input upper-bound method")
    external = _object(
        gate["external_ceiling"],
        "formal_budget_gate.external_ceiling",
        keys=frozenset({"maximum_spend_microunits", "status"}),
    )
    maximum = external["maximum_spend_microunits"]
    if external["status"] != "external_ceiling_declared":
        _fail("formal_budget_gate does not contain an externally declared ceiling")
    if type(maximum) is not int or maximum <= 0:
        _fail("formal_budget_gate maximum spend must be one positive integer")
    formal = _object(gate["formal"], "formal_budget_gate.formal")
    worst = _object(
        formal.get("worst_case_remaining"),
        "formal_budget_gate.formal.worst_case_remaining",
    )
    if worst.get("cost_microunits") != maximum:
        _fail("formal_budget_gate external ceiling differs from its mechanical worst case")
    return maximum


def _artifact(args: argparse.Namespace) -> bytes:
    preregistration_bytes = args.prereg.read_bytes()
    preregistration = preregistration_from_bytes(preregistration_bytes)
    preregistration_sha256 = _sha256(preregistration_bytes)
    pricing_bytes = _read_bound_artifact(
        args.pricing_contract,
        args.expected_pricing_sha256,
        "pricing_contract",
    )
    envelope_bytes = _read_bound_artifact(
        args.formal_billing_envelope,
        args.expected_formal_billing_envelope_sha256,
        "formal_billing_envelope",
    )
    gate_bytes = _read_bound_artifact(
        args.formal_budget_gate,
        args.expected_formal_budget_gate_sha256,
        "formal_budget_gate",
    )
    pricing = _canonical_object(pricing_bytes, "pricing_contract")
    envelope = _canonical_object(envelope_bytes, "formal_billing_envelope")
    model = _object(
        preregistration.to_dict()["model_and_prompts"],
        "preregistration.model_and_prompts",
    )
    requested_model_id = model.get("requested_model")
    if type(requested_model_id) is not str:
        _fail("preregistration requested model is invalid")
    if pricing.get("billing_model_id") != requested_model_id:
        _fail("pricing contract billing model differs from preregistration")
    currency = pricing.get("currency")
    if type(currency) is not str or not currency:
        _fail("pricing contract currency is invalid")
    pricing_sha256 = _sha256(pricing_bytes)
    envelope_sha256 = _sha256(envelope_bytes)
    if envelope.get("pricing_contract_raw_sha256") != pricing_sha256:
        _fail("formal billing envelope differs from the pricing contract")
    maximum = _authorized_gate(
        gate_bytes,
        preregistration_sha256=preregistration_sha256,
        pricing_sha256=pricing_sha256,
        envelope_sha256=envelope_sha256,
        requested_model_id=requested_model_id,
        currency=currency,
    )
    pre_call = build_pre_call_config(
        preregistration,
        collection_attempt=args.collection_attempt,
        execution_git_sha=args.execution_git_sha,
        uv_lock_sha256=args.uv_lock_sha256,
        analysis_binding_kind=args.analysis_binding_kind,
        analysis_code_sha256=args.analysis_code_sha256,
        runtime_identity=current_runtime_identity(),
        formal_billing_envelope=envelope,
        formal_billing_envelope_raw_sha256=envelope_sha256,
        cost_status="available",
        currency=currency,
        maximum_spend_microunits=maximum,
        pricing_contract_sha256=pricing_sha256,
        formal_budget_gate_raw_sha256=_sha256(gate_bytes),
    )
    return pre_call.wire_json


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--pricing-contract", type=Path, required=True)
    parser.add_argument("--expected-pricing-sha256", required=True)
    parser.add_argument("--formal-billing-envelope", type=Path, required=True)
    parser.add_argument("--expected-formal-billing-envelope-sha256", required=True)
    parser.add_argument("--formal-budget-gate", type=Path, required=True)
    parser.add_argument("--expected-formal-budget-gate-sha256", required=True)
    parser.add_argument("--collection-attempt", type=int, required=True)
    parser.add_argument("--execution-git-sha", required=True)
    parser.add_argument("--uv-lock-sha256", required=True)
    parser.add_argument(
        "--analysis-binding-kind",
        choices=("analysis_module_sha256", "wheel_record_sha256"),
        required=True,
    )
    parser.add_argument("--analysis-code-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        artifact = _artifact(args)
        if args.check:
            if args.output.read_bytes() != artifact:
                _fail("generated pre-call differs byte-for-byte from output")
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(artifact)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    parsed = _canonical_object(artifact, "pre_call")
    budget = _object(parsed["budget"], "pre_call.budget")
    cost = _object(budget["cost"], "pre_call.budget.cost")
    print(
        "benchmark pre-call OK "
        f"(sha256={_sha256(artifact)}, run_id={parsed['run_id']}, "
        f"maximum_spend_microunits={cost['maximum_spend_microunits']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
