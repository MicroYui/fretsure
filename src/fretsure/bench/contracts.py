"""Strict, bounded contracts shared by benchmark-v2 artifacts.

Benchmark evidence is persisted and replayed outside the process that created it.
This module therefore accepts only inert, exact JSON builtins; validates Unicode,
numeric, depth, node, scalar, and encoded-byte limits before an artifact is trusted;
and provides one domain-separated hashing rule for every versioned artifact.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum
from itertools import islice
from typing import cast

BENCHMARK_NOTEGRAPH_VERSION = "benchmark-notegraph@0.1.0"
BENCHMARK_CORPUS_VERSION = "benchmark-corpus@0.1.0"
BENCHMARK_SOURCE_CENSUS_VERSION = "benchmark-source-census@0.1.0"
BENCHMARK_MANIFEST_VERSION = "benchmark-manifest@0.1.0"
BENCHMARK_ROW_VERSION = "benchmark-row@0.1.0"
BENCHMARK_OBSERVATIONS_VERSION = "benchmark-observations@0.1.0"
BENCHMARK_RECEIPT_VERSION = "benchmark-receipt@0.1.0"
BENCHMARK_REPORT_VERSION = "benchmark-report@0.1.0"

BENCHMARK_SCHEMA_VERSIONS = (
    BENCHMARK_NOTEGRAPH_VERSION,
    BENCHMARK_CORPUS_VERSION,
    BENCHMARK_SOURCE_CENSUS_VERSION,
    BENCHMARK_MANIFEST_VERSION,
    BENCHMARK_ROW_VERSION,
    BENCHMARK_OBSERVATIONS_VERSION,
    BENCHMARK_RECEIPT_VERSION,
    BENCHMARK_REPORT_VERSION,
)
_BENCHMARK_SCHEMA_VERSION_SET = frozenset(BENCHMARK_SCHEMA_VERSIONS)

# These limits cover a complete finalized artifact rather than one source score.
# Individual corpus rows and blobs gain tighter schema-specific limits downstream.
MAX_BENCHMARK_JSON_BYTES = 64 * 1024 * 1024
MAX_BENCHMARK_JSON_DEPTH = 64
MAX_BENCHMARK_JSON_NODES = 1_000_000
MAX_BENCHMARK_JSON_STRING_BYTES = 1 * 1024 * 1024
MAX_BENCHMARK_JSON_SCALAR_BYTES = 64 * 1024 * 1024
MAX_BENCHMARK_INTEGER_BITS = 256
MAX_BENCHMARK_IDENTIFIER_CHARS = 128

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+\-]*\Z")
_LOWER_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class BenchmarkContractCode(StrEnum):
    """Stable failure categories for benchmark artifact boundaries."""

    INVALID_TYPE = "INVALID_TYPE"
    INVALID_VALUE = "INVALID_VALUE"
    INVALID_UNICODE = "INVALID_UNICODE"
    NON_CANONICAL_UNICODE = "NON_CANONICAL_UNICODE"
    NON_FINITE_NUMBER = "NON_FINITE_NUMBER"
    INPUT_LIMIT_EXCEEDED = "INPUT_LIMIT_EXCEEDED"
    CYCLIC_VALUE = "CYCLIC_VALUE"


def _safe_path(path: object) -> str:
    if type(path) is str and 1 <= len(path) <= 512 and path.isprintable():
        return path
    return "$"


class BenchmarkContractError(ValueError):
    """Typed, location-bearing failure that never renders an untrusted value."""

    def __init__(self, field: str, detail: str) -> None:
        safe_field = _safe_path(field)
        self.field = safe_field
        # ``path`` remains a read-compatible alias for location-oriented callers.
        self.path = safe_field
        self.detail = detail
        super().__init__(f"invalid {safe_field}: {detail}")


@dataclass(slots=True)
class _Budget:
    nodes: int = 0
    scalar_bytes: int = 0


def _fail(
    code: BenchmarkContractCode,
    path: str,
    detail: str,
) -> BenchmarkContractError:
    return BenchmarkContractError(path, f"{code.value}: {detail}")


def require_schema_version(value: object, *, path: str = "$.schema_version") -> str:
    """Return one known benchmark schema version without coercion."""

    if type(value) is not str:
        raise _fail(
            BenchmarkContractCode.INVALID_TYPE,
            path,
            "schema version must be an exact string",
        )
    version = value
    if version not in _BENCHMARK_SCHEMA_VERSION_SET:
        raise _fail(
            BenchmarkContractCode.INVALID_VALUE,
            path,
            "schema version is not in the frozen benchmark registry",
        )
    return version


def require_identifier(value: object, *, path: str = "$.identifier") -> str:
    """Return one bounded inert ASCII artifact identifier."""

    if type(value) is not str:
        raise _fail(
            BenchmarkContractCode.INVALID_TYPE,
            path,
            "identifier must be an exact string",
        )
    identifier = value
    if (
        not 1 <= len(identifier) <= MAX_BENCHMARK_IDENTIFIER_CHARS
        or _IDENTIFIER.fullmatch(identifier) is None
    ):
        raise _fail(
            BenchmarkContractCode.INVALID_VALUE,
            path,
            (
                "identifier must be bounded ASCII and contain only letters, digits, "
                "dot, underscore, colon, at, plus, or hyphen"
            ),
        )
    return identifier


def require_sha256(value: object, *, path: str = "$.sha256") -> str:
    """Return one exact lowercase hexadecimal SHA-256 digest."""

    if type(value) is not str:
        raise _fail(
            BenchmarkContractCode.INVALID_TYPE,
            path,
            "sha256 must be an exact string",
        )
    digest = value
    if _LOWER_SHA256.fullmatch(digest) is None:
        raise _fail(
            BenchmarkContractCode.INVALID_VALUE,
            path,
            "sha256 must contain exactly 64 lowercase hexadecimal characters",
        )
    return digest


def _consume_node(path: str, budget: _Budget) -> None:
    budget.nodes += 1
    if budget.nodes > MAX_BENCHMARK_JSON_NODES:
        raise _fail(
            BenchmarkContractCode.INPUT_LIMIT_EXCEEDED,
            path,
            f"JSON value count exceeds limit {MAX_BENCHMARK_JSON_NODES}",
        )


def _consume_string(value: str, path: str, budget: _Budget) -> str:
    # UTF-8 uses at least one byte per code point.  This character check rejects
    # obviously oversized values before allocating their encoded representation.
    if len(value) > MAX_BENCHMARK_JSON_STRING_BYTES:
        raise _fail(
            BenchmarkContractCode.INPUT_LIMIT_EXCEEDED,
            path,
            f"JSON string exceeds byte limit {MAX_BENCHMARK_JSON_STRING_BYTES}",
        )
    if not unicodedata.is_normalized("NFC", value):
        raise _fail(
            BenchmarkContractCode.NON_CANONICAL_UNICODE,
            path,
            "JSON strings must already be normalized to NFC",
        )
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        raise _fail(
            BenchmarkContractCode.INVALID_UNICODE,
            path,
            "JSON strings must contain valid Unicode scalar values",
        ) from None
    size = len(encoded)
    if size > MAX_BENCHMARK_JSON_STRING_BYTES:
        raise _fail(
            BenchmarkContractCode.INPUT_LIMIT_EXCEEDED,
            path,
            f"JSON string exceeds byte limit {MAX_BENCHMARK_JSON_STRING_BYTES}",
        )
    if size > MAX_BENCHMARK_JSON_SCALAR_BYTES - budget.scalar_bytes:
        raise _fail(
            BenchmarkContractCode.INPUT_LIMIT_EXCEEDED,
            path,
            f"JSON scalar bytes exceed limit {MAX_BENCHMARK_JSON_SCALAR_BYTES}",
        )
    budget.scalar_bytes += size
    return value


def _key_path(parent: str, key: str) -> str:
    rendered = json.dumps(key, ensure_ascii=True, allow_nan=False)
    return f"{parent}[{rendered}]"


def _normalize_json_value(
    value: object,
    *,
    path: str,
    depth: int,
    budget: _Budget,
    active_containers: set[int],
) -> object:
    if depth > MAX_BENCHMARK_JSON_DEPTH:
        raise _fail(
            BenchmarkContractCode.INPUT_LIMIT_EXCEEDED,
            path,
            f"JSON nesting exceeds depth limit {MAX_BENCHMARK_JSON_DEPTH}",
        )
    _consume_node(path, budget)

    value_type = type(value)
    if value is None or value_type is bool:
        return value
    if value_type is str:
        return _consume_string(cast(str, value), path, budget)
    if value_type is int:
        integer = cast(int, value)
        if int.bit_length(integer) > MAX_BENCHMARK_INTEGER_BITS:
            raise _fail(
                BenchmarkContractCode.INPUT_LIMIT_EXCEEDED,
                path,
                f"integer exceeds bit limit {MAX_BENCHMARK_INTEGER_BITS}",
            )
        return integer
    if value_type is float:
        number = cast(float, value)
        if not math.isfinite(number):
            raise _fail(
                BenchmarkContractCode.NON_FINITE_NUMBER,
                path,
                "JSON numbers must be finite",
            )
        return number

    if value_type is dict:
        mapping = cast(dict[object, object], value)
        identity = id(value)
        if identity in active_containers:
            raise _fail(
                BenchmarkContractCode.CYCLIC_VALUE,
                path,
                "cyclic JSON containers are not accepted",
            )
        remaining_nodes = MAX_BENCHMARK_JSON_NODES - budget.nodes
        if dict.__len__(mapping) > remaining_nodes // 2:
            raise _fail(
                BenchmarkContractCode.INPUT_LIMIT_EXCEEDED,
                path,
                f"JSON value count exceeds limit {MAX_BENCHMARK_JSON_NODES}",
            )
        max_items = remaining_nodes // 2
        try:
            items = tuple(islice(dict.items(mapping), max_items + 1))
        except RuntimeError:
            raise _fail(
                BenchmarkContractCode.INVALID_VALUE,
                path,
                "mapping changed while it was being read",
            ) from None
        if len(items) > max_items:
            raise _fail(
                BenchmarkContractCode.INPUT_LIMIT_EXCEEDED,
                path,
                f"JSON value count exceeds limit {MAX_BENCHMARK_JSON_NODES}",
            )

        active_containers.add(identity)
        try:
            normalized: dict[str, object] = {}
            for index, (key, child) in enumerate(items):
                key_location = f"{path}.key[{index}]"
                _consume_node(key_location, budget)
                if type(key) is not str:
                    raise _fail(
                        BenchmarkContractCode.INVALID_TYPE,
                        key_location,
                        "JSON object keys must be exact strings",
                    )
                normalized_key = _consume_string(key, key_location, budget)
                normalized[normalized_key] = _normalize_json_value(
                    child,
                    path=_key_path(path, normalized_key),
                    depth=depth + 1,
                    budget=budget,
                    active_containers=active_containers,
                )
            return normalized
        finally:
            active_containers.remove(identity)

    if value_type is list:
        sequence = cast(list[object], value)
        identity = id(value)
        if identity in active_containers:
            raise _fail(
                BenchmarkContractCode.CYCLIC_VALUE,
                path,
                "cyclic JSON containers are not accepted",
            )
        remaining_nodes = MAX_BENCHMARK_JSON_NODES - budget.nodes
        if list.__len__(sequence) > remaining_nodes:
            raise _fail(
                BenchmarkContractCode.INPUT_LIMIT_EXCEEDED,
                path,
                f"JSON value count exceeds limit {MAX_BENCHMARK_JSON_NODES}",
            )
        children = tuple(sequence[: remaining_nodes + 1])
        if len(children) > remaining_nodes:
            raise _fail(
                BenchmarkContractCode.INPUT_LIMIT_EXCEEDED,
                path,
                f"JSON value count exceeds limit {MAX_BENCHMARK_JSON_NODES}",
            )

        active_containers.add(identity)
        try:
            return [
                _normalize_json_value(
                    child,
                    path=f"{path}[{index}]",
                    depth=depth + 1,
                    budget=budget,
                    active_containers=active_containers,
                )
                for index, child in enumerate(children)
            ]
        finally:
            active_containers.remove(identity)

    raise _fail(
        BenchmarkContractCode.INVALID_TYPE,
        path,
        "only exact null, bool, int, finite float, string, list, and dict values are accepted",
    )


def _encode_canonical(value: object) -> bytes:
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        check_circular=False,
    )
    chunks: list[bytes] = []
    size = 0
    try:
        encoded_chunks = encoder.iterencode(value)
        for chunk in encoded_chunks:
            encoded = chunk.encode("utf-8")
            if len(encoded) > MAX_BENCHMARK_JSON_BYTES - size:
                raise _fail(
                    BenchmarkContractCode.INPUT_LIMIT_EXCEEDED,
                    "$",
                    f"canonical JSON exceeds byte limit {MAX_BENCHMARK_JSON_BYTES}",
                )
            chunks.append(encoded)
            size += len(encoded)
    except BenchmarkContractError:
        raise
    except (OverflowError, RecursionError, TypeError, UnicodeEncodeError, ValueError):
        raise _fail(
            BenchmarkContractCode.INVALID_VALUE,
            "$",
            "value could not be encoded as canonical JSON",
        ) from None
    return b"".join(chunks)


def canonical_json_bytes(value: object) -> bytes:
    """Return strict deterministic UTF-8 JSON bytes for one inert artifact value."""

    try:
        normalized = _normalize_json_value(
            value,
            path="$",
            depth=0,
            budget=_Budget(),
            active_containers=set(),
        )
    except RecursionError:
        raise _fail(
            BenchmarkContractCode.INPUT_LIMIT_EXCEEDED,
            "$",
            "JSON nesting exceeds the runtime recursion limit",
        ) from None
    return _encode_canonical(normalized)


def canonical_sha256(schema_version: object, value: object) -> str:
    """Hash canonical bytes with the frozen schema-version domain prefix."""

    version = require_schema_version(schema_version)
    digest = hashlib.sha256()
    digest.update(f"fretsure:{version}\0".encode("ascii"))
    digest.update(canonical_json_bytes(value))
    return digest.hexdigest()


__all__ = [
    "BENCHMARK_CORPUS_VERSION",
    "BENCHMARK_MANIFEST_VERSION",
    "BENCHMARK_NOTEGRAPH_VERSION",
    "BENCHMARK_OBSERVATIONS_VERSION",
    "BENCHMARK_RECEIPT_VERSION",
    "BENCHMARK_REPORT_VERSION",
    "BENCHMARK_ROW_VERSION",
    "BENCHMARK_SOURCE_CENSUS_VERSION",
    "BENCHMARK_SCHEMA_VERSIONS",
    "MAX_BENCHMARK_IDENTIFIER_CHARS",
    "MAX_BENCHMARK_INTEGER_BITS",
    "MAX_BENCHMARK_JSON_BYTES",
    "MAX_BENCHMARK_JSON_DEPTH",
    "MAX_BENCHMARK_JSON_NODES",
    "MAX_BENCHMARK_JSON_SCALAR_BYTES",
    "MAX_BENCHMARK_JSON_STRING_BYTES",
    "BenchmarkContractCode",
    "BenchmarkContractError",
    "canonical_json_bytes",
    "canonical_sha256",
    "require_identifier",
    "require_schema_version",
    "require_sha256",
]
