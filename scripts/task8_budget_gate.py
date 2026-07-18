#!/usr/bin/env python3
"""Build or verify the offline Task 8 formal budget-gate artifact.

This repository-only tool performs exact arithmetic over explicit artifacts.  It does
not discover prices, inspect Git, authorize collection, contact a proxy, or call a
provider.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, NoReturn, cast

from fretsure.bench.artifacts import parse_canonical_json_bytes
from fretsure.bench.contracts import canonical_json_bytes, require_identifier, require_sha256
from fretsure.bench.preregistration import (
    BenchmarkPreregistration,
    preregistration_from_bytes,
)
from fretsure.llm.client import MAX_PROXY_USAGE_TOKENS, validate_llm_model_id

TOKEN_PRICING_CONTRACT_VERSION: Final = "benchmark-token-pricing-contract@0.1.0"
FORMAL_BILLING_ENVELOPE_VERSION: Final = "benchmark-formal-billing-envelope@0.2.0"
OPERATIONAL_USAGE_VERSION: Final = "benchmark-operational-usage@0.1.0"
FORMAL_BUDGET_GATE_VERSION: Final = "benchmark-formal-budget-gate@0.3.0"

TOKEN_UNIT: Final = 1_000_000
FORMAL_PAIRED_SAMPLES: Final = 5_030
MAX_PILOT_PAIRS: Final = 4
MAX_ATTEMPTS_PER_CALL: Final = 3
MAX_PILOT_LOGICAL_CALLS: Final = 44
MAX_PILOT_ATTEMPTS: Final = 132
MAX_PILOT_REQUESTED_OUTPUT_TOKENS: Final = 51_200
MAX_PILOT_ATTEMPT_RESERVED_OUTPUT_TOKENS: Final = 153_600
MAX_PILOT_HOST_WALL_MICROSECONDS: Final = 5_400_000_000
MAX_EXACT_INTEGER: Final = (1 << 255) - 1

STAGE_NAMES: Final[tuple[str, ...]] = ("proposal_raw", "repair", "critic")
_STAGE_NAME_SET = frozenset(STAGE_NAMES)
STAGE_MAX_OUTPUT_TOKENS: Final[dict[str, int]] = {
    "critic": 512,
    "proposal_raw": 2_048,
    "repair": 1_024,
}

TOKEN_FIELDS: Final[tuple[str, ...]] = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
_TOKEN_FIELD_SET = frozenset(TOKEN_FIELDS)
_CURRENCY = re.compile(r"[A-Z]{3,8}\Z")
_UTC_SECONDS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")

PILOT_CALL_TEMPLATES: Final[tuple[tuple[str, int, int], ...]] = (
    ("proposal_raw", 8, 2_048),
    ("repair", 32, 1_024),
    ("critic", 4, 512),
)

FORMAL_INPUT_UPPER_BOUND_METHOD: Final = "utf8_bytes_plus_256"
FORMAL_ENFORCEMENT_POINT: Final = "before_observation_retry_network"
FORMAL_ENVELOPE_SCOPE: Final = "formal_collection"
FORMAL_SHORT_CONTEXT_MAX_INPUT_TOKENS: Final = 272_000
FORMAL_INPUT_BILLING_FIELDS: Final = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


class Task8BudgetGateError(ValueError):
    """Typed failure for one invalid Task 8 pricing or budget artifact."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid Task 8 budget {field}: {detail}")


def _fail(field: str, detail: str) -> NoReturn:
    raise Task8BudgetGateError(field, detail)


def _object(
    value: object,
    field: str,
    keys: frozenset[str] | None = None,
) -> dict[str, object]:
    if type(value) is not dict:
        _fail(field, "must be an exact object")
    result = cast(dict[str, object], value)
    if keys is not None and frozenset(result) != keys:
        _fail(field, "must contain the exact required keys")
    return result


def _integer(
    value: object,
    field: str,
    *,
    minimum: int = 0,
    maximum: int = MAX_EXACT_INTEGER,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(field, f"must be an exact integer in {minimum}..{maximum}")
    return value


def _optional_integer(value: object, field: str) -> int | None:
    if value is None:
        return None
    return _integer(value, field)


def _identifier(value: object, field: str) -> str:
    try:
        return require_identifier(value, path=field)
    except ValueError:
        _fail(field, "must be one bounded artifact identifier")


def _model(value: object, field: str) -> str:
    try:
        return validate_llm_model_id(value)
    except ValueError:
        _fail(field, "must be one bounded model identifier")


def _sha256(value: object, field: str) -> str:
    try:
        return require_sha256(value, path=field)
    except ValueError:
        _fail(field, "must be one lowercase SHA-256 digest")


def _text(value: object, field: str, *, maximum: int) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= maximum
        or not value.isprintable()
    ):
        _fail(field, f"must be one printable string of 1..{maximum} characters")
    return value


def _currency(value: object, field: str = "currency") -> str:
    if type(value) is not str or _CURRENCY.fullmatch(value) is None:
        _fail(field, "must be an uppercase 3..8 character currency identifier")
    return value


def _captured_utc(value: object, field: str) -> str:
    if type(value) is not str or _UTC_SECONDS.fullmatch(value) is None:
        _fail(field, "must use exact UTC seconds form YYYY-MM-DDTHH:MM:SSZ")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        _fail(field, "must be a real UTC calendar timestamp")
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        _fail(field, "must be canonical UTC seconds")
    return value


def _raw_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_canonical(data: object, field: str) -> object:
    try:
        return parse_canonical_json_bytes(data)
    except ValueError:
        _fail(field, "must be exact canonical JSON bytes")


def _token_map(value: object, field: str) -> dict[str, int]:
    raw = _object(value, field, _TOKEN_FIELD_SET)
    return {
        name: _integer(raw[name], f"{field}.{name}")
        for name in TOKEN_FIELDS
    }


def _optional_token_map(value: object, field: str) -> dict[str, int | None]:
    raw = _object(value, field, _TOKEN_FIELD_SET)
    return {
        name: _optional_integer(raw[name], f"{field}.{name}")
        for name in TOKEN_FIELDS
    }


def _pricing_wire(value: object) -> dict[str, object]:
    obj = _object(
        value,
        "$",
        frozenset(
            {
                "billable_token_ceiling_per_attempt",
                "billing_model_id",
                "billing_provider_id",
                "ceil_each_component_per_attempt",
                "currency",
                "evidence",
                "fixed_microunits_per_attempt",
                "rates_microunits_per_million_tokens",
                "schema",
                "token_unit",
            }
        ),
    )
    if obj["schema"] != TOKEN_PRICING_CONTRACT_VERSION:
        _fail("schema", "has the wrong pricing-contract version")
    if obj["token_unit"] != TOKEN_UNIT:
        _fail("token_unit", "must equal one million tokens")
    provider = _identifier(obj["billing_provider_id"], "billing_provider_id")
    model = _model(obj["billing_model_id"], "billing_model_id")
    currency = _currency(obj["currency"])
    rates = _token_map(
        obj["rates_microunits_per_million_tokens"],
        "rates_microunits_per_million_tokens",
    )
    ceilings = _token_map(
        obj["billable_token_ceiling_per_attempt"],
        "billable_token_ceiling_per_attempt",
    )
    fixed = _integer(
        obj["fixed_microunits_per_attempt"],
        "fixed_microunits_per_attempt",
    )
    ceil_each = obj["ceil_each_component_per_attempt"]
    if type(ceil_each) is not bool:
        _fail("ceil_each_component_per_attempt", "must be an exact bool")
    evidence = _object(
        obj["evidence"],
        "evidence",
        frozenset({"captured_at_utc", "source_ref", "source_sha256"}),
    )
    captured = _captured_utc(evidence["captured_at_utc"], "evidence.captured_at_utc")
    source_ref = _text(evidence["source_ref"], "evidence.source_ref", maximum=2_048)
    source_sha = _sha256(evidence["source_sha256"], "evidence.source_sha256")
    return {
        "billable_token_ceiling_per_attempt": ceilings,
        "billing_model_id": model,
        "billing_provider_id": provider,
        "ceil_each_component_per_attempt": ceil_each,
        "currency": currency,
        "evidence": {
            "captured_at_utc": captured,
            "source_ref": source_ref,
            "source_sha256": source_sha,
        },
        "fixed_microunits_per_attempt": fixed,
        "rates_microunits_per_million_tokens": rates,
        "schema": TOKEN_PRICING_CONTRACT_VERSION,
        "token_unit": TOKEN_UNIT,
    }


@dataclass(frozen=True, slots=True)
class TokenPricingContract:
    """One canonical externally supplied pricing contract; no discovery occurs here."""

    wire_json: bytes

    def __post_init__(self) -> None:
        _pricing_wire(_parse_canonical(self.wire_json, "pricing_contract"))

    def to_dict(self) -> dict[str, object]:
        return _pricing_wire(_parse_canonical(self.wire_json, "pricing_contract"))

    @property
    def raw_sha256(self) -> str:
        return _raw_sha256(self.wire_json)

    @property
    def billing_provider_id(self) -> str:
        return cast(str, self.to_dict()["billing_provider_id"])

    @property
    def billing_model_id(self) -> str:
        return cast(str, self.to_dict()["billing_model_id"])

    @property
    def currency(self) -> str:
        return cast(str, self.to_dict()["currency"])

    @property
    def rates(self) -> dict[str, int]:
        return cast(dict[str, int], self.to_dict()["rates_microunits_per_million_tokens"])

    @property
    def ceilings(self) -> dict[str, int]:
        return cast(dict[str, int], self.to_dict()["billable_token_ceiling_per_attempt"])

    @property
    def fixed_microunits_per_attempt(self) -> int:
        return cast(int, self.to_dict()["fixed_microunits_per_attempt"])

    @property
    def ceil_each_component_per_attempt(self) -> bool:
        return cast(bool, self.to_dict()["ceil_each_component_per_attempt"])


def build_token_pricing_contract(
    *,
    billing_provider_id: str,
    billing_model_id: str,
    currency: str,
    rates_microunits_per_million_tokens: Mapping[str, int],
    fixed_microunits_per_attempt: int,
    billable_token_ceiling_per_attempt: Mapping[str, int],
    ceil_each_component_per_attempt: bool,
    evidence_source_ref: str,
    evidence_captured_at_utc: str,
    evidence_source_sha256: str,
) -> TokenPricingContract:
    wire = {
        "billable_token_ceiling_per_attempt": dict(billable_token_ceiling_per_attempt),
        "billing_model_id": billing_model_id,
        "billing_provider_id": billing_provider_id,
        "ceil_each_component_per_attempt": ceil_each_component_per_attempt,
        "currency": currency,
        "evidence": {
            "captured_at_utc": evidence_captured_at_utc,
            "source_ref": evidence_source_ref,
            "source_sha256": evidence_source_sha256,
        },
        "fixed_microunits_per_attempt": fixed_microunits_per_attempt,
        "rates_microunits_per_million_tokens": dict(
            rates_microunits_per_million_tokens
        ),
        "schema": TOKEN_PRICING_CONTRACT_VERSION,
        "token_unit": TOKEN_UNIT,
    }
    exact = _pricing_wire(wire)
    return TokenPricingContract(canonical_json_bytes(exact))


def token_pricing_contract_from_dict(value: object) -> TokenPricingContract:
    return TokenPricingContract(canonical_json_bytes(_pricing_wire(value)))


def token_pricing_contract_from_bytes(data: object) -> TokenPricingContract:
    exact = _pricing_wire(_parse_canonical(data, "pricing_contract"))
    return TokenPricingContract(canonical_json_bytes(exact))


def _formal_billing_envelope_wire(value: object) -> dict[str, object]:
    obj = _object(
        value,
        "$",
        frozenset(
            {
                "billable_token_ceiling_per_attempt",
                "enforcement",
                "output_usage_contract",
                "pricing_contract_raw_sha256",
                "schema",
                "scope",
            }
        ),
    )
    if obj["schema"] != FORMAL_BILLING_ENVELOPE_VERSION:
        _fail("schema", "has the wrong formal-billing-envelope version")
    if obj["scope"] != FORMAL_ENVELOPE_SCOPE:
        _fail("scope", "must equal formal_collection")
    pricing_sha = _sha256(
        obj["pricing_contract_raw_sha256"],
        "pricing_contract_raw_sha256",
    )
    ceilings = _token_map(
        obj["billable_token_ceiling_per_attempt"],
        "billable_token_ceiling_per_attempt",
    )
    for name in FORMAL_INPUT_BILLING_FIELDS:
        if ceilings[name] > FORMAL_SHORT_CONTEXT_MAX_INPUT_TOKENS:
            _fail(
                f"billable_token_ceiling_per_attempt.{name}",
                "exceeds the frozen short-context pricing band",
            )
    if ceilings["output_tokens"] > MAX_PROXY_USAGE_TOKENS:
        _fail(
            "billable_token_ceiling_per_attempt.output_tokens",
            "exceeds the bounded reported-usage parser ceiling",
        )
    output_contract = _object(
        obj["output_usage_contract"],
        "output_usage_contract",
        frozenset(
            {
                "billing_field",
                "captured_at_utc",
                "includes_non_visible_tokens",
                "maximum_tokens",
                "model_id",
                "source_model_ref",
                "source_token_counting_ref",
            }
        ),
    )
    if output_contract["billing_field"] != "output_tokens":
        _fail("output_usage_contract.billing_field", "must equal output_tokens")
    captured_at = _captured_utc(
        output_contract["captured_at_utc"],
        "output_usage_contract.captured_at_utc",
    )
    includes_non_visible = output_contract["includes_non_visible_tokens"]
    if includes_non_visible is not True:
        _fail(
            "output_usage_contract.includes_non_visible_tokens",
            "must explicitly acknowledge non-visible generated tokens",
        )
    output_maximum = _integer(
        output_contract["maximum_tokens"],
        "output_usage_contract.maximum_tokens",
        minimum=1,
        maximum=MAX_PROXY_USAGE_TOKENS,
    )
    if output_maximum != ceilings["output_tokens"]:
        _fail(
            "output_usage_contract.maximum_tokens",
            "must equal the billable output-token ceiling",
        )
    output_model = _model(output_contract["model_id"], "output_usage_contract.model_id")
    source_model_ref = _text(
        output_contract["source_model_ref"],
        "output_usage_contract.source_model_ref",
        maximum=2_048,
    )
    source_token_counting_ref = _text(
        output_contract["source_token_counting_ref"],
        "output_usage_contract.source_token_counting_ref",
        maximum=2_048,
    )
    enforcement = _object(
        obj["enforcement"],
        "enforcement",
        frozenset({"input_upper_bound_method", "required_before"}),
    )
    if enforcement["input_upper_bound_method"] != FORMAL_INPUT_UPPER_BOUND_METHOD:
        _fail(
            "enforcement.input_upper_bound_method",
            "must use the frozen conservative UTF-8 byte bound",
        )
    if enforcement["required_before"] != FORMAL_ENFORCEMENT_POINT:
        _fail(
            "enforcement.required_before",
            "must require enforcement before observation, retry, or network",
        )
    return {
        "billable_token_ceiling_per_attempt": ceilings,
        "enforcement": {
            "input_upper_bound_method": FORMAL_INPUT_UPPER_BOUND_METHOD,
            "required_before": FORMAL_ENFORCEMENT_POINT,
        },
        "output_usage_contract": {
            "billing_field": "output_tokens",
            "captured_at_utc": captured_at,
            "includes_non_visible_tokens": True,
            "maximum_tokens": output_maximum,
            "model_id": output_model,
            "source_model_ref": source_model_ref,
            "source_token_counting_ref": source_token_counting_ref,
        },
        "pricing_contract_raw_sha256": pricing_sha,
        "schema": FORMAL_BILLING_ENVELOPE_VERSION,
        "scope": FORMAL_ENVELOPE_SCOPE,
    }


@dataclass(frozen=True, slots=True)
class FormalBillingEnvelope:
    """One workload envelope bound to rates without rewriting pilot provenance."""

    wire_json: bytes

    def __post_init__(self) -> None:
        _formal_billing_envelope_wire(
            _parse_canonical(self.wire_json, "formal_billing_envelope")
        )

    def to_dict(self) -> dict[str, object]:
        return _formal_billing_envelope_wire(
            _parse_canonical(self.wire_json, "formal_billing_envelope")
        )

    @property
    def raw_sha256(self) -> str:
        return _raw_sha256(self.wire_json)

    @property
    def pricing_contract_raw_sha256(self) -> str:
        return cast(str, self.to_dict()["pricing_contract_raw_sha256"])

    @property
    def ceilings(self) -> dict[str, int]:
        return cast(dict[str, int], self.to_dict()["billable_token_ceiling_per_attempt"])

    @property
    def output_usage_contract(self) -> dict[str, object]:
        return cast(dict[str, object], self.to_dict()["output_usage_contract"])


def build_formal_billing_envelope(
    *,
    pricing_contract_raw_sha256: str,
    billable_token_ceiling_per_attempt: Mapping[str, int],
    output_usage_contract: Mapping[str, object],
) -> FormalBillingEnvelope:
    wire = {
        "billable_token_ceiling_per_attempt": dict(
            billable_token_ceiling_per_attempt
        ),
        "enforcement": {
            "input_upper_bound_method": FORMAL_INPUT_UPPER_BOUND_METHOD,
            "required_before": FORMAL_ENFORCEMENT_POINT,
        },
        "output_usage_contract": dict(output_usage_contract),
        "pricing_contract_raw_sha256": pricing_contract_raw_sha256,
        "schema": FORMAL_BILLING_ENVELOPE_VERSION,
        "scope": FORMAL_ENVELOPE_SCOPE,
    }
    return FormalBillingEnvelope(canonical_json_bytes(_formal_billing_envelope_wire(wire)))


def formal_billing_envelope_from_dict(value: object) -> FormalBillingEnvelope:
    return FormalBillingEnvelope(
        canonical_json_bytes(_formal_billing_envelope_wire(value))
    )


def formal_billing_envelope_from_bytes(data: object) -> FormalBillingEnvelope:
    exact = _formal_billing_envelope_wire(
        _parse_canonical(data, "formal_billing_envelope")
    )
    return FormalBillingEnvelope(canonical_json_bytes(exact))


def _validate_formal_envelope_basis(
    pricing: TokenPricingContract,
    envelope: FormalBillingEnvelope,
) -> None:
    if type(envelope) is not FormalBillingEnvelope:
        _fail("formal_billing_envelope", "must be an exact FormalBillingEnvelope")
    if envelope.pricing_contract_raw_sha256 != pricing.raw_sha256:
        _fail(
            "formal_billing_envelope.pricing_contract_raw_sha256",
            "does not bind the supplied pricing contract",
        )
    if envelope.output_usage_contract["model_id"] != pricing.billing_model_id:
        _fail(
            "formal_billing_envelope.output_usage_contract.model_id",
            "does not equal the pricing contract model",
        )
    if envelope.ceilings["output_tokens"] != pricing.ceilings["output_tokens"]:
        _fail(
            "formal_billing_envelope.billable_token_ceiling_per_attempt.output_tokens",
            "does not equal the corrected pricing-contract output ceiling",
        )


@dataclass(frozen=True, slots=True)
class OperationalStageTotals:
    """Exact operational resource totals for one frozen pilot call stage."""

    stage: str
    logical_calls: int
    retries: int
    requested_output_tokens: int
    attempt_reserved_output_tokens: int

    def __post_init__(self) -> None:
        if self.stage not in _STAGE_NAME_SET:
            _fail("operational_usage.stage_totals.stage", "has an unknown stage")
        calls = _integer(
            self.logical_calls,
            f"operational_usage.stage_totals.{self.stage}.logical_calls",
        )
        _integer(
            self.retries,
            f"operational_usage.stage_totals.{self.stage}.retries",
            maximum=calls * (MAX_ATTEMPTS_PER_CALL - 1),
        )
        requested = _integer(
            self.requested_output_tokens,
            f"operational_usage.stage_totals.{self.stage}.requested_output_tokens",
        )
        _integer(
            self.attempt_reserved_output_tokens,
            (
                "operational_usage.stage_totals."
                f"{self.stage}.attempt_reserved_output_tokens"
            ),
            minimum=requested,
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "attempt_reserved_output_tokens": self.attempt_reserved_output_tokens,
            "logical_calls": self.logical_calls,
            "requested_output_tokens": self.requested_output_tokens,
            "retries": self.retries,
        }


def _operational_stage_map(
    stage_totals: tuple[OperationalStageTotals, ...],
) -> dict[str, OperationalStageTotals]:
    if type(stage_totals) is not tuple:
        _fail("operational_usage.stage_totals", "must be an exact tuple")
    result: dict[str, OperationalStageTotals] = {}
    for stage_total in stage_totals:
        if type(stage_total) is not OperationalStageTotals:
            _fail(
                "operational_usage.stage_totals",
                "must contain exact OperationalStageTotals values",
            )
        if stage_total.stage in result:
            _fail("operational_usage.stage_totals", "contains a duplicate stage")
        result[stage_total.stage] = stage_total
    if frozenset(result) != _STAGE_NAME_SET:
        _fail("operational_usage.stage_totals", "must contain all frozen stages")
    return result


@dataclass(frozen=True, slots=True)
class OperationalUsage:
    """Exact pilot totals; nullable or incomplete provider usage never means zero."""

    pair_count: int
    logical_calls: int
    attempts: int
    requested_output_tokens: int
    attempt_reserved_output_tokens: int
    elapsed_microseconds: int
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None
    stage_totals: tuple[OperationalStageTotals, ...]
    usage_covers_all_attempts: bool

    def __post_init__(self) -> None:
        _validate_operational_usage(self)

    @property
    def retries(self) -> int:
        return self.attempts - self.logical_calls

    @property
    def usage(self) -> dict[str, int | None]:
        return {
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }

    @property
    def stage_totals_by_name(self) -> dict[str, OperationalStageTotals]:
        return _operational_stage_map(self.stage_totals)

    def to_dict(self) -> dict[str, object]:
        stage_map = self.stage_totals_by_name
        return {
            "attempt_reserved_output_tokens": self.attempt_reserved_output_tokens,
            "attempts": self.attempts,
            "elapsed_microseconds": self.elapsed_microseconds,
            "logical_calls": self.logical_calls,
            "pair_count": self.pair_count,
            "requested_output_tokens": self.requested_output_tokens,
            "schema": OPERATIONAL_USAGE_VERSION,
            "stage_totals": {
                name: stage_map[name].to_dict() for name in STAGE_NAMES
            },
            "usage": self.usage,
            "usage_covers_all_attempts": self.usage_covers_all_attempts,
        }

    @property
    def wire_json(self) -> bytes:
        return canonical_json_bytes(self.to_dict())

    @property
    def raw_sha256(self) -> str:
        return _raw_sha256(self.wire_json)


def _validate_operational_usage(value: OperationalUsage) -> None:
    pairs = _integer(
        value.pair_count,
        "operational_usage.pair_count",
        minimum=1,
        maximum=MAX_PILOT_PAIRS,
    )
    calls = _integer(
        value.logical_calls,
        "operational_usage.logical_calls",
        minimum=1,
        maximum=pairs * 11,
    )
    attempts = _integer(
        value.attempts,
        "operational_usage.attempts",
        minimum=calls,
        maximum=calls * MAX_ATTEMPTS_PER_CALL,
    )
    requested = _integer(
        value.requested_output_tokens,
        "operational_usage.requested_output_tokens",
        minimum=1,
        maximum=pairs * 12_800,
    )
    reserved = _integer(
        value.attempt_reserved_output_tokens,
        "operational_usage.attempt_reserved_output_tokens",
        minimum=requested,
        maximum=requested * MAX_ATTEMPTS_PER_CALL,
    )
    _integer(
        value.elapsed_microseconds,
        "operational_usage.elapsed_microseconds",
        maximum=MAX_PILOT_HOST_WALL_MICROSECONDS,
    )
    if type(value.usage_covers_all_attempts) is not bool:
        _fail("operational_usage.usage_covers_all_attempts", "must be an exact bool")
    for name, amount in value.usage.items():
        _optional_integer(amount, f"operational_usage.usage.{name}")
    if (
        value.output_tokens is not None
        and value.output_tokens > value.attempt_reserved_output_tokens
    ):
        _fail(
            "operational_usage.usage.output_tokens",
            "cannot exceed attempt-reserved output tokens",
        )
    stage_map = value.stage_totals_by_name
    stage_call_limits = {
        "critic": pairs,
        "proposal_raw": pairs * 2,
        "repair": pairs * 8,
    }
    for name in STAGE_NAMES:
        stage = stage_map[name]
        if name == "proposal_raw" and stage.logical_calls == 0:
            _fail(
                "operational_usage.stage_totals.proposal_raw.logical_calls",
                "must include at least one proposal/raw call for projection",
            )
        if stage.logical_calls > stage_call_limits[name]:
            _fail(
                f"operational_usage.stage_totals.{name}.logical_calls",
                "exceeds the frozen pilot stage ceiling",
            )
        max_tokens = STAGE_MAX_OUTPUT_TOKENS[name]
        if stage.requested_output_tokens != stage.logical_calls * max_tokens:
            _fail(
                f"operational_usage.stage_totals.{name}.requested_output_tokens",
                "must equal calls times the frozen output ceiling",
            )
        if stage.attempt_reserved_output_tokens != (
            stage.logical_calls + stage.retries
        ) * max_tokens:
            _fail(
                (
                    "operational_usage.stage_totals."
                    f"{name}.attempt_reserved_output_tokens"
                ),
                "must include the exact retry reservation",
            )
    if sum(stage.logical_calls for stage in stage_map.values()) != calls:
        _fail("operational_usage.stage_totals", "logical calls do not reconcile")
    if sum(stage.retries for stage in stage_map.values()) != attempts - calls:
        _fail("operational_usage.stage_totals", "retries do not reconcile")
    if sum(stage.requested_output_tokens for stage in stage_map.values()) != requested:
        _fail("operational_usage.stage_totals", "requested tokens do not reconcile")
    if (
        sum(stage.attempt_reserved_output_tokens for stage in stage_map.values())
        != reserved
    ):
        _fail("operational_usage.stage_totals", "reserved tokens do not reconcile")
    if attempts > MAX_PILOT_ATTEMPTS:
        _fail("operational_usage.attempts", "exceeds the Task 8 pilot ceiling")


def _operational_stage_totals_from_dict(value: object) -> tuple[OperationalStageTotals, ...]:
    raw = _object(value, "operational_usage.stage_totals", _STAGE_NAME_SET)
    values: list[OperationalStageTotals] = []
    keys = frozenset(
        {
            "attempt_reserved_output_tokens",
            "logical_calls",
            "requested_output_tokens",
            "retries",
        }
    )
    for name in STAGE_NAMES:
        stage = _object(raw[name], f"operational_usage.stage_totals.{name}", keys)
        values.append(
            OperationalStageTotals(
                stage=name,
                logical_calls=_integer(
                    stage["logical_calls"],
                    f"operational_usage.stage_totals.{name}.logical_calls",
                ),
                retries=_integer(
                    stage["retries"],
                    f"operational_usage.stage_totals.{name}.retries",
                ),
                requested_output_tokens=_integer(
                    stage["requested_output_tokens"],
                    (
                        "operational_usage.stage_totals."
                        f"{name}.requested_output_tokens"
                    ),
                ),
                attempt_reserved_output_tokens=_integer(
                    stage["attempt_reserved_output_tokens"],
                    (
                        "operational_usage.stage_totals."
                        f"{name}.attempt_reserved_output_tokens"
                    ),
                ),
            )
        )
    return tuple(values)


def operational_usage_from_dict(value: object) -> OperationalUsage:
    obj = _object(
        value,
        "operational_usage",
        frozenset(
            {
                "attempt_reserved_output_tokens",
                "attempts",
                "elapsed_microseconds",
                "logical_calls",
                "pair_count",
                "requested_output_tokens",
                "schema",
                "stage_totals",
                "usage",
                "usage_covers_all_attempts",
            }
        ),
    )
    if obj["schema"] != OPERATIONAL_USAGE_VERSION:
        _fail("operational_usage.schema", "has the wrong version")
    usage = _optional_token_map(obj["usage"], "operational_usage.usage")
    covers_all_attempts = obj["usage_covers_all_attempts"]
    if type(covers_all_attempts) is not bool:
        _fail("operational_usage.usage_covers_all_attempts", "must be an exact bool")
    return OperationalUsage(
        pair_count=_integer(obj["pair_count"], "operational_usage.pair_count"),
        logical_calls=_integer(obj["logical_calls"], "operational_usage.logical_calls"),
        attempts=_integer(obj["attempts"], "operational_usage.attempts"),
        requested_output_tokens=_integer(
            obj["requested_output_tokens"],
            "operational_usage.requested_output_tokens",
        ),
        attempt_reserved_output_tokens=_integer(
            obj["attempt_reserved_output_tokens"],
            "operational_usage.attempt_reserved_output_tokens",
        ),
        elapsed_microseconds=_integer(
            obj["elapsed_microseconds"],
            "operational_usage.elapsed_microseconds",
        ),
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        cache_creation_input_tokens=usage["cache_creation_input_tokens"],
        cache_read_input_tokens=usage["cache_read_input_tokens"],
        stage_totals=_operational_stage_totals_from_dict(obj["stage_totals"]),
        usage_covers_all_attempts=covers_all_attempts,
    )


def operational_usage_from_bytes(data: object) -> OperationalUsage:
    return operational_usage_from_dict(_parse_canonical(data, "pilot_summary"))


@dataclass(frozen=True, slots=True)
class CostEstimate:
    availability: str
    microunits: int | None

    def to_dict(self) -> dict[str, object]:
        return {"availability": self.availability, "microunits": self.microunits}


def _ceil_div(numerator: int, denominator: int) -> int:
    if numerator < 0 or denominator <= 0:
        _fail("arithmetic", "ceil division requires nonnegative numerator and positive divisor")
    return (numerator + denominator - 1) // denominator


def _validate_usage_against_ceilings(
    ceilings: Mapping[str, int],
    *,
    attempts: int,
    usage: Mapping[str, int | None],
    field: str,
) -> None:
    for name in TOKEN_FIELDS:
        amount = usage[name]
        if amount is not None and amount > attempts * ceilings[name]:
            _fail(f"{field}.{name}", "exceeds attempts times the billable ceiling")


def _validate_usage_against_pricing(
    pricing: TokenPricingContract,
    *,
    attempts: int,
    usage: Mapping[str, int | None],
    field: str,
) -> None:
    _validate_usage_against_ceilings(
        pricing.ceilings,
        attempts=attempts,
        usage=usage,
        field=field,
    )


def _cost_for_totals(
    pricing: TokenPricingContract,
    *,
    attempts: int,
    usage: Mapping[str, int | None],
    usage_covers_all_attempts: bool,
) -> CostEstimate:
    if not usage_covers_all_attempts:
        return CostEstimate("incomplete_attempt_usage", None)
    if any(usage[name] is None for name in TOKEN_FIELDS):
        return CostEstimate("usage_unavailable", None)
    if pricing.ceil_each_component_per_attempt:
        return CostEstimate("per_attempt_usage_breakdown_unavailable", None)
    numerator = sum(
        cast(int, usage[name]) * pricing.rates[name]
        for name in TOKEN_FIELDS
    )
    cost = (
        attempts * pricing.fixed_microunits_per_attempt
        + _ceil_div(numerator, TOKEN_UNIT)
    )
    return CostEstimate("available", cost)


def cost_for_usage(
    pricing: TokenPricingContract,
    usage: OperationalUsage,
) -> CostEstimate:
    if type(pricing) is not TokenPricingContract:
        _fail("pricing_contract", "must be an exact TokenPricingContract")
    if type(usage) is not OperationalUsage:
        _fail("operational_usage", "must be an exact OperationalUsage")
    _validate_usage_against_pricing(
        pricing,
        attempts=usage.attempts,
        usage=usage.usage,
        field="operational_usage.usage",
    )
    return _cost_for_totals(
        pricing,
        attempts=usage.attempts,
        usage=usage.usage,
        usage_covers_all_attempts=usage.usage_covers_all_attempts,
    )


@dataclass(frozen=True, slots=True)
class _CallTemplate:
    stage: str
    count: int
    max_output_tokens: int

    def to_dict(self) -> dict[str, object]:
        return {
            "count": self.count,
            "max_output_tokens": self.max_output_tokens,
            "stage": self.stage,
        }


def _attempt_cost(
    pricing: TokenPricingContract,
    *,
    ceilings: Mapping[str, int],
) -> int:
    amounts = {
        "cache_creation_input_tokens": ceilings["cache_creation_input_tokens"],
        "cache_read_input_tokens": ceilings["cache_read_input_tokens"],
        "input_tokens": ceilings["input_tokens"],
        "output_tokens": ceilings["output_tokens"],
    }
    if pricing.ceil_each_component_per_attempt:
        variable = sum(
            _ceil_div(amounts[name] * pricing.rates[name], TOKEN_UNIT)
            for name in TOKEN_FIELDS
        )
    else:
        variable = _ceil_div(
            sum(amounts[name] * pricing.rates[name] for name in TOKEN_FIELDS),
            TOKEN_UNIT,
        )
    return pricing.fixed_microunits_per_attempt + variable


def _templates_cost(
    pricing: TokenPricingContract,
    templates: Sequence[_CallTemplate],
    *,
    attempts_per_call: int,
    ceilings: Mapping[str, int],
) -> int:
    for template in templates:
        if template.max_output_tokens > ceilings["output_tokens"]:
            _fail(
                "billing_envelope.billable_token_ceiling_per_attempt.output_tokens",
                "is below a frozen call-template output ceiling",
            )
    if pricing.ceil_each_component_per_attempt:
        return sum(
            template.count
            * attempts_per_call
            * _attempt_cost(
                pricing,
                ceilings=ceilings,
            )
            for template in templates
        )
    attempts = sum(template.count * attempts_per_call for template in templates)
    token_totals = {
        "cache_creation_input_tokens": (
            attempts * ceilings["cache_creation_input_tokens"]
        ),
        "cache_read_input_tokens": attempts * ceilings["cache_read_input_tokens"],
        "input_tokens": attempts * ceilings["input_tokens"],
        "output_tokens": attempts * ceilings["output_tokens"],
    }
    variable = _ceil_div(
        sum(token_totals[name] * pricing.rates[name] for name in TOKEN_FIELDS),
        TOKEN_UNIT,
    )
    return attempts * pricing.fixed_microunits_per_attempt + variable


def _resource_dict(
    *,
    paired_samples: int,
    logical_calls: int,
    attempts: int,
    requested_output_tokens: int,
    attempt_reserved_output_tokens: int,
    provider_timeout_envelope_microseconds: int,
    runner_recorded_elapsed_ceiling_microseconds: int,
    host_wall_microseconds: int | None,
    host_wall_availability: str,
    stage_totals: Mapping[str, object],
) -> dict[str, object]:
    return {
        "attempt_reserved_output_tokens": attempt_reserved_output_tokens,
        "attempts": attempts,
        "host_wall_availability": host_wall_availability,
        "host_wall_microseconds": host_wall_microseconds,
        "logical_calls": logical_calls,
        "max_retries": attempts - logical_calls,
        "paired_samples": paired_samples,
        "provider_timeout_envelope_microseconds": (
            provider_timeout_envelope_microseconds
        ),
        "requested_output_tokens": requested_output_tokens,
        "runner_recorded_elapsed_ceiling_microseconds": (
            runner_recorded_elapsed_ceiling_microseconds
        ),
        "stage_totals": dict(stage_totals),
    }


def _worst_stage_totals(
    templates: Sequence[_CallTemplate],
    *,
    attempts_per_call: int,
) -> dict[str, object]:
    calls = {name: 0 for name in STAGE_NAMES}
    requested = {name: 0 for name in STAGE_NAMES}
    for template in templates:
        stage = template.stage.split(":", maxsplit=1)[0]
        if stage not in _STAGE_NAME_SET:
            _fail("call_templates.stage", "has an unknown stage")
        calls[stage] += template.count
        requested[stage] += template.count * template.max_output_tokens
    return {
        name: {
            "attempt_reserved_output_tokens": requested[name] * attempts_per_call,
            "logical_calls": calls[name],
            "max_retries": calls[name] * (attempts_per_call - 1),
            "requested_output_tokens": requested[name],
        }
        for name in STAGE_NAMES
    }


def pilot_worst_case_budget(pricing: TokenPricingContract) -> dict[str, object]:
    if type(pricing) is not TokenPricingContract:
        _fail("pricing_contract", "must be an exact TokenPricingContract")
    templates = tuple(_CallTemplate(*values) for values in PILOT_CALL_TEMPLATES)
    template_wire = [template.to_dict() for template in templates]
    stage_totals = _worst_stage_totals(
        templates,
        attempts_per_call=MAX_ATTEMPTS_PER_CALL,
    )
    return {
        "call_template_sha256": _raw_sha256(canonical_json_bytes(template_wire)),
        "call_templates": template_wire,
        "cost_microunits": _templates_cost(
            pricing,
            templates,
            attempts_per_call=MAX_ATTEMPTS_PER_CALL,
            ceilings=pricing.ceilings,
        ),
        "cost_status": "mechanical_worst_case_not_authorization",
        "resources": _resource_dict(
            paired_samples=MAX_PILOT_PAIRS,
            logical_calls=MAX_PILOT_LOGICAL_CALLS,
            attempts=MAX_PILOT_ATTEMPTS,
            requested_output_tokens=MAX_PILOT_REQUESTED_OUTPUT_TOKENS,
            attempt_reserved_output_tokens=MAX_PILOT_ATTEMPT_RESERVED_OUTPUT_TOKENS,
            provider_timeout_envelope_microseconds=4_026_000_000,
            runner_recorded_elapsed_ceiling_microseconds=(
                MAX_PILOT_HOST_WALL_MICROSECONDS
            ),
            host_wall_microseconds=MAX_PILOT_HOST_WALL_MICROSECONDS,
            host_wall_availability="available",
            stage_totals=stage_totals,
        ),
    }


def _prereg_wire(preregistration: BenchmarkPreregistration) -> dict[str, object]:
    # BenchmarkPreregistration already validated these immutable canonical bytes.
    # A plain decode avoids repeating the expensive 503-item schema walk.
    parsed: object = json.loads(preregistration.wire_json)
    return _object(parsed, "preregistration")


def _prereg_model(preregistration: BenchmarkPreregistration) -> str:
    wire = _prereg_wire(preregistration)
    model = _object(wire["model_and_prompts"], "preregistration.model_and_prompts")
    return _model(
        model["requested_model"],
        "preregistration.model_and_prompts.requested_model",
    )


def _formal_templates(
    preregistration: BenchmarkPreregistration,
) -> tuple[tuple[_CallTemplate, ...], dict[str, object], int]:
    wire = _prereg_wire(preregistration)
    sampling = _object(wire["sampling"], "preregistration.sampling")
    samples = _integer(
        sampling["n_samples"],
        "preregistration.sampling.n_samples",
        minimum=1,
    )
    repairs = _integer(
        sampling["max_repair_iters"],
        "preregistration.sampling.max_repair_iters",
    )
    budgets = _object(wire["budgets"], "preregistration.budgets")
    provider = _object(
        budgets["provider_policy"],
        "preregistration.budgets.provider_policy",
    )
    attempts_per_call = _integer(
        provider["maximum_attempts_per_logical_call"],
        "preregistration.budgets.provider_policy.maximum_attempts_per_logical_call",
        minimum=1,
    )
    per_item_raw = budgets["per_item"]
    if type(per_item_raw) is not list:
        _fail("preregistration.budgets.per_item", "must be an exact array")
    templates: list[_CallTemplate] = []
    for index, value in enumerate(cast(list[object], per_item_raw)):
        item = _object(value, f"preregistration.budgets.per_item[{index}]")
        item_id = _identifier(
            item["item_id"],
            f"preregistration.budgets.per_item[{index}].item_id",
        )
        proposal_tokens = _integer(
            item["proposal_raw_max_tokens"],
            f"preregistration.budgets.per_item[{index}].proposal_raw_max_tokens",
            minimum=1,
        )
        templates.extend(
            (
                _CallTemplate(f"proposal_raw:{item_id}", samples * 2, proposal_tokens),
                _CallTemplate(f"repair:{item_id}", samples * repairs, 1_024),
                _CallTemplate(f"critic:{item_id}", samples, 512),
            )
        )
    return tuple(templates), budgets, attempts_per_call


def formal_worst_case_budget(
    preregistration: BenchmarkPreregistration,
    pricing: TokenPricingContract,
    formal_billing_envelope: FormalBillingEnvelope,
) -> dict[str, object]:
    if type(preregistration) is not BenchmarkPreregistration:
        _fail("preregistration", "must be an exact BenchmarkPreregistration")
    if type(pricing) is not TokenPricingContract:
        _fail("pricing_contract", "must be an exact TokenPricingContract")
    _validate_formal_envelope_basis(pricing, formal_billing_envelope)
    requested_model = _prereg_model(preregistration)
    if pricing.billing_model_id != requested_model:
        _fail("pricing.billing_model_id", "does not match the preregistered model")
    templates, budgets, attempts_per_call = _formal_templates(preregistration)
    full = _object(budgets["full_corpus"], "preregistration.budgets.full_corpus")
    calls = _integer(full["logical_calls_total"], "formal.logical_calls", minimum=1)
    attempts = _integer(full["maximum_attempts"], "formal.attempts", minimum=calls)
    requested = _integer(
        full["requested_output_tokens_total"],
        "formal.requested_output_tokens",
        minimum=1,
    )
    attempt_reserved = _integer(
        full["attempt_reserved_output_tokens"],
        "formal.attempt_reserved_output_tokens",
        minimum=requested,
    )
    elapsed_ms = _integer(
        full["provider_timeout_envelope_milliseconds"],
        "formal.provider_timeout_envelope_milliseconds",
        minimum=1,
    )
    runner_elapsed_seconds = _integer(
        budgets["recorded_provider_call_elapsed_ceiling_seconds"],
        "formal.recorded_provider_call_elapsed_ceiling_seconds",
        minimum=1,
    )
    template_calls = sum(template.count for template in templates)
    template_requested = sum(
        template.count * template.max_output_tokens for template in templates
    )
    if (
        template_calls != calls
        or attempts != calls * attempts_per_call
        or template_requested != requested
        or attempt_reserved != requested * attempts_per_call
    ):
        _fail("formal.call_templates", "do not reconcile to the preregistered full corpus")
    template_wire = [template.to_dict() for template in templates]
    stage_totals = _worst_stage_totals(
        templates,
        attempts_per_call=attempts_per_call,
    )
    return {
        "call_template_sha256": _raw_sha256(canonical_json_bytes(template_wire)),
        "cost_microunits": _templates_cost(
            pricing,
            templates,
            attempts_per_call=attempts_per_call,
            ceilings=formal_billing_envelope.ceilings,
        ),
        "cost_status": "mechanical_worst_case_not_authorization",
        "resources": _resource_dict(
            paired_samples=FORMAL_PAIRED_SAMPLES,
            logical_calls=calls,
            attempts=attempts,
            requested_output_tokens=requested,
            attempt_reserved_output_tokens=attempt_reserved,
            provider_timeout_envelope_microseconds=elapsed_ms * 1_000,
            runner_recorded_elapsed_ceiling_microseconds=(
                runner_elapsed_seconds * 1_000_000
            ),
            host_wall_microseconds=None,
            host_wall_availability="unavailable",
            stage_totals=stage_totals,
        ),
    }


def _scale(value: int, pair_count: int) -> int:
    return _ceil_div(value * FORMAL_PAIRED_SAMPLES, pair_count)


def _projection(
    usage: OperationalUsage,
    pricing: TokenPricingContract,
    formal_billing_envelope: FormalBillingEnvelope,
    formal: Mapping[str, object],
) -> dict[str, object]:
    formal_resources = _object(formal["resources"], "formal.resources")
    formal_stages = _object(
        formal_resources["stage_totals"],
        "formal.resources.stage_totals",
        _STAGE_NAME_SET,
    )
    observed_stages = usage.stage_totals_by_name
    proposal_formal = _object(
        formal_stages["proposal_raw"],
        "formal.resources.stage_totals.proposal_raw",
    )
    proposal_observed = observed_stages["proposal_raw"]
    proposal_calls = _integer(
        proposal_formal["logical_calls"],
        "formal.resources.stage_totals.proposal_raw.logical_calls",
    )
    proposal_requested = _integer(
        proposal_formal["requested_output_tokens"],
        "formal.resources.stage_totals.proposal_raw.requested_output_tokens",
    )
    proposal_projected_retries = _ceil_div(
        proposal_calls * proposal_observed.retries,
        proposal_observed.logical_calls,
    )
    projected_stages: dict[str, object] = {
        "proposal_raw": {
            "attempt_reserved_output_tokens": proposal_requested
            + _ceil_div(
                proposal_requested * proposal_observed.retries,
                proposal_observed.logical_calls,
            ),
            "logical_calls": proposal_calls,
            "projected_retries": proposal_projected_retries,
            "requested_output_tokens": proposal_requested,
        }
    }
    for name in ("repair", "critic"):
        observed = observed_stages[name]
        projected_calls = _scale(observed.logical_calls, usage.pair_count)
        projected_retries = _scale(observed.retries, usage.pair_count)
        max_output_tokens = STAGE_MAX_OUTPUT_TOKENS[name]
        projected_stages[name] = {
            "attempt_reserved_output_tokens": (
                projected_calls + projected_retries
            )
            * max_output_tokens,
            "logical_calls": projected_calls,
            "projected_retries": projected_retries,
            "requested_output_tokens": projected_calls * max_output_tokens,
        }
    for name in STAGE_NAMES:
        projected_stage = _object(
            projected_stages[name],
            f"projection.stage_totals.{name}",
        )
        formal_stage = _object(
            formal_stages[name],
            f"formal.resources.stage_totals.{name}",
        )
        for projected_name, formal_name in (
            ("logical_calls", "logical_calls"),
            ("projected_retries", "max_retries"),
            ("requested_output_tokens", "requested_output_tokens"),
            (
                "attempt_reserved_output_tokens",
                "attempt_reserved_output_tokens",
            ),
        ):
            projected = _integer(
                projected_stage[projected_name],
                f"projection.stage_totals.{name}.{projected_name}",
            )
            maximum = _integer(
                formal_stage[formal_name],
                f"formal.resources.stage_totals.{name}.{formal_name}",
            )
            if projected > maximum:
                _fail(
                    f"projection.stage_totals.{name}.{projected_name}",
                    "exceeds the formal worst-case stage field",
                )
    projected_stage_objects = [
        _object(projected_stages[name], f"projection.stage_totals.{name}")
        for name in STAGE_NAMES
    ]
    projected_calls = sum(
        _integer(stage["logical_calls"], "projection.stage.logical_calls")
        for stage in projected_stage_objects
    )
    projected_retries = sum(
        _integer(stage["projected_retries"], "projection.stage.projected_retries")
        for stage in projected_stage_objects
    )
    projected_requested = sum(
        _integer(
            stage["requested_output_tokens"],
            "projection.stage.requested_output_tokens",
        )
        for stage in projected_stage_objects
    )
    projected_reserved = sum(
        _integer(
            stage["attempt_reserved_output_tokens"],
            "projection.stage.attempt_reserved_output_tokens",
        )
        for stage in projected_stage_objects
    )
    projected_elapsed = _scale(usage.elapsed_microseconds, usage.pair_count)
    projected_resources: dict[str, object] = {
        "attempt_reserved_output_tokens": projected_reserved,
        "attempts": projected_calls + projected_retries,
        "host_wall_availability": "unavailable",
        "host_wall_microseconds": None,
        "logical_calls": projected_calls,
        "paired_samples": FORMAL_PAIRED_SAMPLES,
        "projected_provider_elapsed_microseconds": projected_elapsed,
        "projected_retries": projected_retries,
        "requested_output_tokens": projected_requested,
        "stage_totals": projected_stages,
    }
    for projected_name, formal_name in (
        ("logical_calls", "logical_calls"),
        ("attempts", "attempts"),
        ("projected_retries", "max_retries"),
        ("requested_output_tokens", "requested_output_tokens"),
        ("attempt_reserved_output_tokens", "attempt_reserved_output_tokens"),
        (
            "projected_provider_elapsed_microseconds",
            "runner_recorded_elapsed_ceiling_microseconds",
        ),
    ):
        projected = _integer(
            projected_resources[projected_name],
            f"projection.{projected_name}",
        )
        maximum = _integer(
            formal_resources[formal_name],
            f"formal.resources.{formal_name}",
        )
        if projected > maximum:
            _fail(
                f"projection.{projected_name}",
                "exceeds the formal worst-case resource field",
            )
    projected_usage = {
        name: (
            None
            if usage.usage[name] is None
            else _scale(cast(int, usage.usage[name]), usage.pair_count)
        )
        for name in TOKEN_FIELDS
    }
    projected_attempts = cast(int, projected_resources["attempts"])
    _validate_usage_against_ceilings(
        formal_billing_envelope.ceilings,
        attempts=projected_attempts,
        usage=projected_usage,
        field="projection.usage",
    )
    projected_cost = _cost_for_totals(
        pricing,
        attempts=projected_attempts,
        usage=projected_usage,
        usage_covers_all_attempts=usage.usage_covers_all_attempts,
    )
    formal_cost = _integer(formal["cost_microunits"], "formal.cost_microunits")
    if projected_cost.microunits is not None and projected_cost.microunits > formal_cost:
        _fail("projection.cost", "exceeds the formal mechanical worst-case cost")
    return {
        "cost": projected_cost.to_dict(),
        "resources": projected_resources,
        "status": "projection_not_authorization",
        "usage": projected_usage,
    }


@dataclass(frozen=True, slots=True)
class FormalBudgetGate:
    """One canonical gate artifact; its contents explicitly do not authorize calls."""

    wire_json: bytes

    def __post_init__(self) -> None:
        _basic_gate_wire(_parse_canonical(self.wire_json, "formal_budget_gate"))

    def to_dict(self) -> dict[str, object]:
        return _basic_gate_wire(_parse_canonical(self.wire_json, "formal_budget_gate"))

    @property
    def raw_sha256(self) -> str:
        return _raw_sha256(self.wire_json)


def _basic_gate_wire(value: object) -> dict[str, object]:
    obj = _object(
        value,
        "$",
        frozenset(
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
    if obj["schema"] != FORMAL_BUDGET_GATE_VERSION:
        _fail("schema", "has the wrong formal-budget-gate version")
    _object(obj["bindings"], "bindings")
    _object(obj["billing"], "billing")
    _object(obj["pilot"], "pilot")
    _object(obj["formal"], "formal")
    _object(
        obj["external_ceiling"],
        "external_ceiling",
        frozenset({"maximum_spend_microunits", "status"}),
    )
    if obj["authorization_statement"] != (
        "pricing_actual_and_projected_costs_do_not_authorize_collection"
    ):
        _fail("authorization_statement", "must explicitly deny automatic authorization")
    return obj


def build_formal_budget_gate(
    *,
    preregistration: BenchmarkPreregistration,
    pricing_contract: TokenPricingContract,
    expected_pricing_contract_sha256: str,
    formal_billing_envelope: FormalBillingEnvelope,
    expected_formal_billing_envelope_sha256: str,
    operational_usage: OperationalUsage,
    pilot_receipt_sha256: str,
    pilot_summary_sha256: str,
    formal_maximum_spend_microunits: int | None,
) -> FormalBudgetGate:
    if type(preregistration) is not BenchmarkPreregistration:
        _fail("preregistration", "must be an exact BenchmarkPreregistration")
    if type(pricing_contract) is not TokenPricingContract:
        _fail("pricing_contract", "must be an exact TokenPricingContract")
    if type(formal_billing_envelope) is not FormalBillingEnvelope:
        _fail("formal_billing_envelope", "must be an exact FormalBillingEnvelope")
    if type(operational_usage) is not OperationalUsage:
        _fail("operational_usage", "must be an exact OperationalUsage")
    expected_pricing = _sha256(
        expected_pricing_contract_sha256,
        "expected_pricing_contract_sha256",
    )
    if pricing_contract.raw_sha256 != expected_pricing:
        _fail("pricing_contract_sha256", "does not match the externally expected pricing hash")
    expected_formal_envelope = _sha256(
        expected_formal_billing_envelope_sha256,
        "expected_formal_billing_envelope_sha256",
    )
    if formal_billing_envelope.raw_sha256 != expected_formal_envelope:
        _fail(
            "formal_billing_envelope_sha256",
            "does not match the externally expected formal envelope hash",
        )
    _validate_formal_envelope_basis(pricing_contract, formal_billing_envelope)
    receipt_sha = _sha256(pilot_receipt_sha256, "pilot_receipt_sha256")
    summary_sha = _sha256(pilot_summary_sha256, "pilot_summary_sha256")
    if operational_usage.raw_sha256 != summary_sha:
        _fail("pilot_summary_sha256", "does not bind the exact operational summary")
    requested_model = _prereg_model(preregistration)
    if pricing_contract.billing_model_id != requested_model:
        _fail("pricing.billing_model_id", "does not match the preregistered model")
    _validate_usage_against_pricing(
        pricing_contract,
        attempts=operational_usage.attempts,
        usage=operational_usage.usage,
        field="operational_usage.usage",
    )
    pilot_worst = pilot_worst_case_budget(pricing_contract)
    formal_worst = formal_worst_case_budget(
        preregistration,
        pricing_contract,
        formal_billing_envelope,
    )
    formal_cost = _integer(
        formal_worst["cost_microunits"],
        "formal.cost_microunits",
    )
    if formal_maximum_spend_microunits is None:
        ceiling_status = "authorization_required"
    else:
        declared = _integer(
            formal_maximum_spend_microunits,
            "formal.maximum_spend_microunits",
        )
        if declared != formal_cost:
            _fail(
                "formal.maximum_spend_microunits",
                "must equal the mechanical formal worst-case cost",
            )
        ceiling_status = "external_ceiling_declared"
    actual_cost = cost_for_usage(pricing_contract, operational_usage)
    wire: dict[str, object] = {
        "authorization_statement": (
            "pricing_actual_and_projected_costs_do_not_authorize_collection"
        ),
        "billing": {
            "billing_model_id": pricing_contract.billing_model_id,
            "billing_provider_id": pricing_contract.billing_provider_id,
            "currency": pricing_contract.currency,
            "formal_input_upper_bound_method": FORMAL_INPUT_UPPER_BOUND_METHOD,
            "token_unit": TOKEN_UNIT,
        },
        "bindings": {
            "formal_billing_envelope_raw_sha256": formal_billing_envelope.raw_sha256,
            "pilot_receipt_sha256": receipt_sha,
            "pilot_summary_sha256": summary_sha,
            "pricing_contract_raw_sha256": pricing_contract.raw_sha256,
            "preregistration_raw_sha256": _raw_sha256(preregistration.wire_json),
        },
        "external_ceiling": {
            "maximum_spend_microunits": formal_maximum_spend_microunits,
            "status": ceiling_status,
        },
        "formal": {
            "paired_sample_count": FORMAL_PAIRED_SAMPLES,
            "pilot_informed_projection": _projection(
                operational_usage,
                pricing_contract,
                formal_billing_envelope,
                formal_worst,
            ),
            "worst_case_remaining": formal_worst,
        },
        "pilot": {
            "actual_cost": actual_cost.to_dict(),
            "observed": operational_usage.to_dict(),
            "worst_case": pilot_worst,
        },
        "schema": FORMAL_BUDGET_GATE_VERSION,
    }
    return FormalBudgetGate(canonical_json_bytes(wire))


def formal_budget_gate_from_dict(
    value: object,
    *,
    preregistration: BenchmarkPreregistration,
    pricing_contract: TokenPricingContract,
    expected_pricing_contract_sha256: str,
    formal_billing_envelope: FormalBillingEnvelope,
    expected_formal_billing_envelope_sha256: str,
    operational_usage: OperationalUsage,
    pilot_receipt_sha256: str,
    pilot_summary_sha256: str,
) -> FormalBudgetGate:
    obj = _basic_gate_wire(value)
    external = _object(obj["external_ceiling"], "external_ceiling")
    maximum = _optional_integer(
        external["maximum_spend_microunits"],
        "external_ceiling.maximum_spend_microunits",
    )
    expected = build_formal_budget_gate(
        preregistration=preregistration,
        pricing_contract=pricing_contract,
        expected_pricing_contract_sha256=expected_pricing_contract_sha256,
        formal_billing_envelope=formal_billing_envelope,
        expected_formal_billing_envelope_sha256=(
            expected_formal_billing_envelope_sha256
        ),
        operational_usage=operational_usage,
        pilot_receipt_sha256=pilot_receipt_sha256,
        pilot_summary_sha256=pilot_summary_sha256,
        formal_maximum_spend_microunits=maximum,
    )
    if obj != expected.to_dict():
        _fail("$", "formal budget gate differs from the mechanically recomputed artifact")
    return expected


def formal_budget_gate_from_bytes(
    data: object,
    *,
    preregistration: BenchmarkPreregistration,
    pricing_contract: TokenPricingContract,
    expected_pricing_contract_sha256: str,
    formal_billing_envelope: FormalBillingEnvelope,
    expected_formal_billing_envelope_sha256: str,
    operational_usage: OperationalUsage,
    pilot_receipt_sha256: str,
    pilot_summary_sha256: str,
) -> FormalBudgetGate:
    return formal_budget_gate_from_dict(
        _parse_canonical(data, "formal_budget_gate"),
        preregistration=preregistration,
        pricing_contract=pricing_contract,
        expected_pricing_contract_sha256=expected_pricing_contract_sha256,
        formal_billing_envelope=formal_billing_envelope,
        expected_formal_billing_envelope_sha256=(
            expected_formal_billing_envelope_sha256
        ),
        operational_usage=operational_usage,
        pilot_receipt_sha256=pilot_receipt_sha256,
        pilot_summary_sha256=pilot_summary_sha256,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--prereg", type=Path, required=True)
    parser.add_argument("--pricing-contract", type=Path, required=True)
    parser.add_argument("--expected-pricing-sha256", required=True)
    parser.add_argument("--formal-billing-envelope", type=Path, required=True)
    parser.add_argument("--expected-formal-billing-envelope-sha256", required=True)
    parser.add_argument("--pilot-summary", type=Path, required=True)
    parser.add_argument("--pilot-receipt", type=Path, required=True)
    parser.add_argument("--formal-maximum-spend-microunits", type=int)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _check(path: Path, expected: bytes) -> None:
    try:
        observed = path.read_bytes()
    except OSError as error:
        raise Task8BudgetGateError("output", "required artifact is unreadable") from error
    if observed != expected:
        _fail("output", "generated artifact differs byte-for-byte")


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
    except FileExistsError:
        if path.read_bytes() != data:
            _fail("output", "already exists with different bytes")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        preregistration = preregistration_from_bytes(args.prereg.read_bytes())
        pricing = token_pricing_contract_from_bytes(args.pricing_contract.read_bytes())
        formal_envelope = formal_billing_envelope_from_bytes(
            args.formal_billing_envelope.read_bytes()
        )
        usage = operational_usage_from_bytes(args.pilot_summary.read_bytes())
        receipt_sha = _raw_sha256(args.pilot_receipt.read_bytes())
        artifact = build_formal_budget_gate(
            preregistration=preregistration,
            pricing_contract=pricing,
            expected_pricing_contract_sha256=args.expected_pricing_sha256,
            formal_billing_envelope=formal_envelope,
            expected_formal_billing_envelope_sha256=(
                args.expected_formal_billing_envelope_sha256
            ),
            operational_usage=usage,
            pilot_receipt_sha256=receipt_sha,
            pilot_summary_sha256=usage.raw_sha256,
            formal_maximum_spend_microunits=args.formal_maximum_spend_microunits,
        )
        if args.check:
            _check(args.output, artifact.wire_json)
        else:
            _write(args.output, artifact.wire_json)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    external = cast(dict[str, object], artifact.to_dict()["external_ceiling"])
    formal = cast(dict[str, object], artifact.to_dict()["formal"])
    worst = cast(dict[str, object], formal["worst_case_remaining"])
    summary = {
        "authorization_status": external["status"],
        "formal_worst_case_cost_microunits": worst["cost_microunits"],
        "formal_billing_envelope_sha256": formal_envelope.raw_sha256,
        "gate_sha256": artifact.raw_sha256,
        "output": str(args.output),
        "pricing_contract_sha256": pricing.raw_sha256,
    }
    print(canonical_json_bytes(summary).decode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
