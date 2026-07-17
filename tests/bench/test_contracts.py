from __future__ import annotations

import hashlib
import math
from typing import Never

import pytest

import fretsure.bench.contracts as contracts
from fretsure.bench.contracts import (
    BENCHMARK_CORPUS_VERSION,
    BENCHMARK_MANIFEST_VERSION,
    BENCHMARK_NOTEGRAPH_VERSION,
    BENCHMARK_OBSERVATIONS_VERSION,
    BENCHMARK_RECEIPT_VERSION,
    BENCHMARK_REPORT_VERSION,
    BENCHMARK_ROW_VERSION,
    BENCHMARK_SOURCE_CENSUS_VERSION,
    BenchmarkContractCode,
    BenchmarkContractError,
    canonical_json_bytes,
    canonical_sha256,
    require_identifier,
    require_schema_version,
    require_sha256,
)


def _assert_reason(
    error: BenchmarkContractError,
    code: BenchmarkContractCode,
) -> None:
    assert error.detail.startswith(f"{code.value}:")


def test_schema_versions_are_frozen_and_unique() -> None:
    versions = (
        BENCHMARK_NOTEGRAPH_VERSION,
        BENCHMARK_CORPUS_VERSION,
        BENCHMARK_SOURCE_CENSUS_VERSION,
        BENCHMARK_MANIFEST_VERSION,
        BENCHMARK_ROW_VERSION,
        BENCHMARK_OBSERVATIONS_VERSION,
        BENCHMARK_RECEIPT_VERSION,
        BENCHMARK_REPORT_VERSION,
    )

    assert versions == (
        "benchmark-notegraph@0.1.0",
        "benchmark-corpus@0.1.0",
        "benchmark-source-census@0.1.0",
        "benchmark-manifest@0.1.0",
        "benchmark-row@0.1.0",
        "benchmark-observations@0.1.0",
        "benchmark-receipt@0.1.0",
        "benchmark-report@0.1.0",
    )
    assert len(set(versions)) == len(versions)


def test_canonical_json_is_sorted_compact_nfc_utf8() -> None:
    value = {
        "z": [None, True, False, 3, 1.25, "琴"],
        "a": "é",
    }

    assert canonical_json_bytes(value) == ('{"a":"é","z":[null,true,false,3,1.25,"琴"]}'.encode())


def test_canonical_json_ignores_insertion_order() -> None:
    first = {"b": 2, "a": 1}
    second = {"a": 1, "b": 2}

    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_canonical_sha256_is_domain_separated_by_schema() -> None:
    value = {"schema_version": BENCHMARK_ROW_VERSION, "value": 1}
    canonical = canonical_json_bytes(value)
    expected = hashlib.sha256(
        f"fretsure:{BENCHMARK_ROW_VERSION}\0".encode("ascii") + canonical
    ).hexdigest()

    assert canonical_sha256(BENCHMARK_ROW_VERSION, value) == expected
    assert canonical_sha256(BENCHMARK_REPORT_VERSION, value) != expected


@pytest.mark.parametrize(
    "value",
    [
        "benchmark-row@9.9.9",
        "BENCHMARK-row@0.1.0",
        "benchmark-row@0.1",
        "",
        1,
    ],
)
def test_schema_version_helper_rejects_unknown_or_malformed_values(value: object) -> None:
    with pytest.raises(BenchmarkContractError) as caught:
        require_schema_version(value)

    assert caught.value.detail.startswith(("INVALID_TYPE:", "INVALID_VALUE:"))


@pytest.mark.parametrize(
    "value",
    ["", " leading", "trailing ", "slash/value", "反", "a" * 129, 7, True],
)
def test_identifier_helper_is_strict_and_bounded(value: object) -> None:
    with pytest.raises(BenchmarkContractError):
        require_identifier(value)


def test_identifier_helper_accepts_inert_ascii_ids() -> None:
    identifier = "run:abc-1_2.3+z@example"

    assert require_identifier(identifier) == identifier


@pytest.mark.parametrize(
    "value",
    ["A" * 64, "a" * 63, "a" * 65, "g" * 64, b"a" * 64, None],
)
def test_sha256_helper_requires_exact_lowercase_hex(value: object) -> None:
    with pytest.raises(BenchmarkContractError):
        require_sha256(value)


def test_sha256_helper_returns_valid_digest() -> None:
    digest = "0123456789abcdef" * 4

    assert require_sha256(digest) == digest


@pytest.mark.parametrize("value", ["e\u0301", {"e\u0301": 1}])
def test_canonical_json_rejects_non_nfc_strings_and_keys(value: object) -> None:
    with pytest.raises(BenchmarkContractError) as caught:
        canonical_json_bytes(value)

    _assert_reason(caught.value, BenchmarkContractCode.NON_CANONICAL_UNICODE)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_canonical_json_rejects_non_finite_floats(value: float) -> None:
    with pytest.raises(BenchmarkContractError) as caught:
        canonical_json_bytes(value)

    _assert_reason(caught.value, BenchmarkContractCode.NON_FINITE_NUMBER)


class _IntSubclass(int):
    pass


class _StringSubclass(str):
    pass


@pytest.mark.parametrize(
    "value",
    [
        (1, 2),
        b"bytes",
        _IntSubclass(1),
        _StringSubclass("value"),
        {1: "non-string-key"},
    ],
)
def test_canonical_json_accepts_only_exact_json_builtins(value: object) -> None:
    with pytest.raises(BenchmarkContractError) as caught:
        canonical_json_bytes(value)

    _assert_reason(caught.value, BenchmarkContractCode.INVALID_TYPE)


_HOSTILE_CALLS: list[str] = []


class _HostileDict(dict[str, object]):
    def items(self) -> Never:
        _HOSTILE_CALLS.append("items")
        raise AssertionError("hostile mapping hook ran")

    def __repr__(self) -> str:
        _HOSTILE_CALLS.append("repr")
        raise AssertionError("hostile repr hook ran")


class _HostileList(list[object]):
    def __iter__(self) -> Never:
        _HOSTILE_CALLS.append("iter")
        raise AssertionError("hostile list hook ran")


@pytest.mark.parametrize("value", [_HostileDict(), _HostileList()])
def test_hostile_container_subclasses_are_rejected_without_hooks(value: object) -> None:
    _HOSTILE_CALLS.clear()

    with pytest.raises(BenchmarkContractError) as caught:
        canonical_json_bytes(value)

    _assert_reason(caught.value, BenchmarkContractCode.INVALID_TYPE)
    assert _HOSTILE_CALLS == []


def test_canonical_json_rejects_cyclic_containers() -> None:
    value: list[object] = []
    value.append(value)

    with pytest.raises(BenchmarkContractError) as caught:
        canonical_json_bytes(value)

    _assert_reason(caught.value, BenchmarkContractCode.CYCLIC_VALUE)


def test_canonical_json_enforces_depth_before_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contracts, "MAX_BENCHMARK_JSON_DEPTH", 1)

    assert canonical_json_bytes([0]) == b"[0]"
    with pytest.raises(BenchmarkContractError) as caught:
        canonical_json_bytes([[0]])

    _assert_reason(caught.value, BenchmarkContractCode.INPUT_LIMIT_EXCEEDED)


def test_canonical_json_enforces_node_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(contracts, "MAX_BENCHMARK_JSON_NODES", 2)

    assert canonical_json_bytes([0]) == b"[0]"
    with pytest.raises(BenchmarkContractError) as caught:
        canonical_json_bytes([0, 1])

    _assert_reason(caught.value, BenchmarkContractCode.INPUT_LIMIT_EXCEEDED)


def test_canonical_json_enforces_integer_bit_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contracts, "MAX_BENCHMARK_INTEGER_BITS", 3)

    assert canonical_json_bytes(7) == b"7"
    with pytest.raises(BenchmarkContractError) as caught:
        canonical_json_bytes(8)

    _assert_reason(caught.value, BenchmarkContractCode.INPUT_LIMIT_EXCEEDED)


def test_canonical_json_enforces_per_string_and_aggregate_scalar_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contracts, "MAX_BENCHMARK_JSON_STRING_BYTES", 2)
    with pytest.raises(BenchmarkContractError) as caught:
        canonical_json_bytes("琴")
    _assert_reason(caught.value, BenchmarkContractCode.INPUT_LIMIT_EXCEEDED)

    monkeypatch.setattr(contracts, "MAX_BENCHMARK_JSON_STRING_BYTES", 10)
    monkeypatch.setattr(contracts, "MAX_BENCHMARK_JSON_SCALAR_BYTES", 3)
    with pytest.raises(BenchmarkContractError) as caught:
        canonical_json_bytes(["é", "é"])
    _assert_reason(caught.value, BenchmarkContractCode.INPUT_LIMIT_EXCEEDED)


def test_canonical_json_enforces_encoded_byte_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = canonical_json_bytes({"control": "\n\n"})
    monkeypatch.setattr(contracts, "MAX_BENCHMARK_JSON_BYTES", len(expected) - 1)

    with pytest.raises(BenchmarkContractError) as caught:
        canonical_json_bytes({"control": "\n\n"})

    _assert_reason(caught.value, BenchmarkContractCode.INPUT_LIMIT_EXCEEDED)


def test_canonical_json_rejects_lone_surrogates() -> None:
    with pytest.raises(BenchmarkContractError) as caught:
        canonical_json_bytes("\ud800")

    _assert_reason(caught.value, BenchmarkContractCode.INVALID_UNICODE)


def test_contract_error_is_location_bearing_and_does_not_render_value() -> None:
    with pytest.raises(BenchmarkContractError) as caught:
        require_sha256(object(), path="$.digest")

    assert caught.value.field == "$.digest"
    assert caught.value.path == "$.digest"
    _assert_reason(caught.value, BenchmarkContractCode.INVALID_TYPE)
    assert "object" not in str(caught.value)


def test_contract_error_constructor_matches_shared_field_detail_api() -> None:
    error = BenchmarkContractError("item_id", "must be unique")

    assert error.field == "item_id"
    assert error.detail == "must be unique"
    assert str(error) == "invalid item_id: must be unique"
