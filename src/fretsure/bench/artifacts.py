"""Canonical benchmark-v2 artifacts and a durable single-writer store.

The module deliberately owns storage rather than experiment semantics.  It turns
already validated benchmark values into exact JSON/JSONL, journals observation
events before returning to the caller, and finalizes one immutable evidence bundle.
It does not call Git, a model provider, or report/statistics code.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import stat
import tempfile
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Protocol, Self, cast

from fretsure.bench.contracts import (
    BENCHMARK_MANIFEST_VERSION,
    BENCHMARK_OBSERVATIONS_VERSION,
    BENCHMARK_RECEIPT_VERSION,
    BENCHMARK_REPORT_VERSION,
    BENCHMARK_ROW_VERSION,
    MAX_BENCHMARK_JSON_BYTES,
    BenchmarkContractError,
    canonical_json_bytes,
    canonical_sha256,
    require_identifier,
    require_sha256,
)
from fretsure.bench.observe import (
    AttemptIntent,
    AttemptResult,
    CallFailureCode,
    CallIntent,
    CallResult,
    CallStage,
    InMemoryObservationSink,
    ProviderObservation,
)
from fretsure.llm.client import (
    MAX_PROXY_TEXT_BYTES_PER_TOKEN,
    MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
    MAX_PROXY_USAGE_TOKENS,
    LLMModelIdError,
    validate_llm_model_id,
)

BENCHMARK_BLOB_VERSION: Final = "benchmark-blob@0.1.0"
BENCHMARK_WAL_VERSION: Final = "benchmark-wal@0.1.0"
BENCHMARK_PRIVATE_OBSERVATIONS_VERSION: Final = "benchmark-private-observations@0.1.0"
BENCHMARK_STAGED_UNIT_VERSION: Final = "benchmark-staged-unit@0.1.0"

MAX_JSON_NUMBER_CHARS = 128
MAX_ARTIFACT_JSONL_BYTES = 256 * 1024 * 1024
MAX_ARTIFACT_JSONL_LINE_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_JSONL_LINES = 1_000_000
MAX_ARTIFACT_ROWS = 100_000
MAX_ARTIFACT_BLOBS = 500_000
MAX_ARTIFACT_CALLS = 100_000
MAX_ARTIFACT_ATTEMPTS = 1_600_000
MAX_ARTIFACT_BUDGET = (1 << 63) - 1
MAX_ARTIFACT_REPORT_JSON_BYTES = MAX_BENCHMARK_JSON_BYTES
MAX_ARTIFACT_REPORT_MARKDOWN_BYTES = 16 * 1024 * 1024

_ZERO_SHA256 = "0" * 64
_WAL_DOMAIN = b"fretsure:benchmark-wal@0.1.0\0"
_ROW_TABLE_DOMAIN = b"fretsure:benchmark-row-table@0.1.0\0"
_BLOB_TABLE_DOMAIN = b"fretsure:benchmark-blob-table@0.1.0\0"
_BLOB_DOMAIN = b"fretsure:benchmark-blob@0.1.0\0"
_FORMAL_PRE_CALL_CONFIG_VERSION: Final = "benchmark-pre-call-config@0.2.0"
_PROVIDER_USAGE_FIELDS: Final = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


class ArtifactCode(StrEnum):
    """Stable storage/contract failure categories."""

    INVALID_INPUT = "INVALID_INPUT"
    NON_CANONICAL = "NON_CANONICAL"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    IO_ERROR = "IO_ERROR"
    NOT_REGULAR_FILE = "NOT_REGULAR_FILE"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    LOCKED = "LOCKED"
    HASH_MISMATCH = "HASH_MISMATCH"
    CORRUPT_JOURNAL = "CORRUPT_JOURNAL"
    CORRUPT_UNIT = "CORRUPT_UNIT"
    COVERAGE_MISMATCH = "COVERAGE_MISMATCH"
    INCOMPLETE = "INCOMPLETE"


class ArtifactError(ValueError):
    """Typed artifact failure that never includes untrusted artifact text."""

    def __init__(self, code: ArtifactCode, field: str, detail: str) -> None:
        self.code = code
        self.field = field if type(field) is str and field.isprintable() else "$"
        self.detail = detail
        super().__init__(f"artifact {code.value} at {self.field}: {detail}")


def _error(code: ArtifactCode, field: str, detail: str) -> ArtifactError:
    return ArtifactError(code, field, detail)


def _provider_usage_ceilings(
    value: object,
    field: str,
) -> dict[str, int] | None:
    if value is None:
        return None
    obj = _require_exact_dict(value, field, frozenset(_PROVIDER_USAGE_FIELDS))
    return {
        name: _require_int(
            obj[name],
            f"{field}.{name}",
            minimum=1,
            maximum=MAX_PROXY_USAGE_TOKENS,
        )
        for name in _PROVIDER_USAGE_FIELDS
    }


def _require_exact_dict(
    value: object,
    field: str,
    keys: frozenset[str],
) -> dict[str, object]:
    if type(value) is not dict:
        raise _error(ArtifactCode.INVALID_INPUT, field, "must be an exact object")
    result = cast(dict[str, object], value)
    if frozenset(dict.keys(result)) != keys or len(result) != len(keys):
        raise _error(ArtifactCode.INVALID_INPUT, field, "must contain the exact keys")
    return result


def _require_exact_list(
    value: object,
    field: str,
    *,
    maximum: int,
) -> list[object]:
    if type(value) is not list:
        raise _error(ArtifactCode.INVALID_INPUT, field, "must be an exact array")
    result = cast(list[object], value)
    if len(result) > maximum:
        raise _error(ArtifactCode.LIMIT_EXCEEDED, field, f"count exceeds {maximum}")
    return result


def _require_int(value: object, field: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _error(
            ArtifactCode.INVALID_INPUT,
            field,
            f"must be an exact integer in {minimum}..{maximum}",
        )
    return value


def _require_optional_int(
    value: object,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int | None:
    if value is None:
        return None
    return _require_int(value, field, minimum=minimum, maximum=maximum)


def _identifier(value: object, field: str) -> str:
    try:
        return require_identifier(value, path=field)
    except BenchmarkContractError as error:
        raise _error(ArtifactCode.INVALID_INPUT, field, error.detail) from None


def _sha256(value: object, field: str) -> str:
    try:
        return require_sha256(value, path=field)
    except BenchmarkContractError as error:
        raise _error(ArtifactCode.INVALID_INPUT, field, error.detail) from None


def _model_id(value: object, field: str, *, optional: bool) -> str | None:
    if value is None and optional:
        return None
    try:
        return validate_llm_model_id(value)
    except LLMModelIdError:
        raise _error(
            ArtifactCode.INVALID_INPUT, field, "is not a bounded model identifier"
        ) from None


class _DuplicateKey(ValueError):
    pass


class _InvalidConstant(ValueError):
    pass


class _NumberLimit(ValueError):
    pass


def _json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey
        result[key] = value
    return result


def _parse_int(token: str) -> int:
    if len(token) > MAX_JSON_NUMBER_CHARS:
        raise _NumberLimit
    return int(token)


def _parse_float(token: str) -> float:
    if len(token) > MAX_JSON_NUMBER_CHARS:
        raise _NumberLimit
    value = float(token)
    if not math.isfinite(value):
        raise _InvalidConstant
    return value


def _reject_constant(_token: str) -> object:
    raise _InvalidConstant


def parse_canonical_json_bytes(
    data: object,
    *,
    max_bytes: int = MAX_BENCHMARK_JSON_BYTES,
) -> object:
    """Parse one JSON value and require byte equality with canonical encoding."""

    if type(data) is not bytes:
        raise _error(ArtifactCode.INVALID_INPUT, "$", "input must be exact bytes")
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_ARTIFACT_JSONL_BYTES:
        raise _error(ArtifactCode.INVALID_INPUT, "max_bytes", "is outside the artifact limit")
    exact = data
    if not exact:
        raise _error(ArtifactCode.INVALID_INPUT, "$", "JSON input is empty")
    if len(exact) > max_bytes:
        raise _error(ArtifactCode.LIMIT_EXCEEDED, "$", f"input exceeds {max_bytes} bytes")
    try:
        text = exact.decode("utf-8")
    except UnicodeDecodeError:
        raise _error(ArtifactCode.INVALID_INPUT, "$", "input must be strict UTF-8") from None
    try:
        value = json.loads(
            text,
            object_pairs_hook=_json_object,
            parse_int=_parse_int,
            parse_float=_parse_float,
            parse_constant=_reject_constant,
        )
    except _DuplicateKey:
        raise _error(
            ArtifactCode.INVALID_INPUT, "$", "JSON object contains a duplicate key"
        ) from None
    except _NumberLimit:
        raise _error(ArtifactCode.LIMIT_EXCEEDED, "$", "JSON number token is too long") from None
    except (_InvalidConstant, json.JSONDecodeError, RecursionError, ValueError):
        raise _error(
            ArtifactCode.INVALID_INPUT, "$", "input is not one strict JSON value"
        ) from None
    try:
        canonical = canonical_json_bytes(value)
    except BenchmarkContractError as error:
        raise _error(ArtifactCode.INVALID_INPUT, error.field, error.detail) from None
    if canonical != exact:
        raise _error(ArtifactCode.NON_CANONICAL, "$", "input bytes are not canonical JSON")
    return value


def canonical_jsonl_bytes(
    values: object,
    *,
    max_bytes: int = MAX_ARTIFACT_JSONL_BYTES,
    max_lines: int = MAX_ARTIFACT_JSONL_LINES,
    max_line_bytes: int = MAX_ARTIFACT_JSONL_LINE_BYTES,
) -> bytes:
    """Encode an exact tuple of canonical values as LF-terminated JSONL."""

    if type(values) is not tuple:
        raise _error(ArtifactCode.INVALID_INPUT, "values", "must be an exact tuple")
    exact = cast(tuple[object, ...], values)
    if len(exact) > max_lines:
        raise _error(ArtifactCode.LIMIT_EXCEEDED, "values", f"line count exceeds {max_lines}")
    chunks: list[bytes] = []
    total = 0
    for index, value in enumerate(exact):
        try:
            line = canonical_json_bytes(value)
        except BenchmarkContractError as error:
            raise _error(ArtifactCode.INVALID_INPUT, f"values[{index}]", error.detail) from None
        if len(line) > max_line_bytes:
            raise _error(
                ArtifactCode.LIMIT_EXCEEDED,
                f"values[{index}]",
                f"line exceeds {max_line_bytes} bytes",
            )
        if len(line) + 1 > max_bytes - total:
            raise _error(ArtifactCode.LIMIT_EXCEEDED, "values", f"JSONL exceeds {max_bytes} bytes")
        chunks.extend((line, b"\n"))
        total += len(line) + 1
    return b"".join(chunks)


def parse_canonical_jsonl_bytes(
    data: object,
    *,
    max_bytes: int = MAX_ARTIFACT_JSONL_BYTES,
    max_lines: int = MAX_ARTIFACT_JSONL_LINES,
    max_line_bytes: int = MAX_ARTIFACT_JSONL_LINE_BYTES,
) -> tuple[object, ...]:
    """Parse canonical LF-terminated JSONL without accepting blank lines."""

    if type(data) is not bytes:
        raise _error(ArtifactCode.INVALID_INPUT, "$", "input must be exact bytes")
    exact = data
    if len(exact) > max_bytes:
        raise _error(ArtifactCode.LIMIT_EXCEEDED, "$", f"JSONL exceeds {max_bytes} bytes")
    if not exact:
        return ()
    if not exact.endswith(b"\n"):
        raise _error(ArtifactCode.NON_CANONICAL, "$", "JSONL must end with LF")
    lines = exact[:-1].split(b"\n")
    if len(lines) > max_lines:
        raise _error(ArtifactCode.LIMIT_EXCEEDED, "$", f"line count exceeds {max_lines}")
    parsed: list[object] = []
    for index, line in enumerate(lines):
        if not line:
            raise _error(ArtifactCode.NON_CANONICAL, f"line[{index}]", "blank lines are forbidden")
        if len(line) > max_line_bytes:
            raise _error(
                ArtifactCode.LIMIT_EXCEEDED,
                f"line[{index}]",
                f"line exceeds {max_line_bytes} bytes",
            )
        parsed.append(parse_canonical_json_bytes(line, max_bytes=max_line_bytes))
    return tuple(parsed)


def _read_regular_bytes(path: Path, *, max_bytes: int) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        code = ArtifactCode.NOT_REGULAR_FILE if path.is_symlink() else ArtifactCode.IO_ERROR
        raise _error(code, "path", "artifact file could not be opened") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise _error(ArtifactCode.NOT_REGULAR_FILE, "path", "artifact path is not regular")
        if before.st_size > max_bytes:
            raise _error(ArtifactCode.LIMIT_EXCEEDED, "path", f"file exceeds {max_bytes} bytes")
        chunks: list[bytes] = []
        size = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, max_bytes - size + 1))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > max_bytes:
                raise _error(ArtifactCode.LIMIT_EXCEEDED, "path", f"file exceeds {max_bytes} bytes")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or size != after.st_size:
            raise _error(ArtifactCode.IO_ERROR, "path", "artifact file changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def read_canonical_json(
    path: Path,
    *,
    max_bytes: int = MAX_BENCHMARK_JSON_BYTES,
) -> object:
    return parse_canonical_json_bytes(
        _read_regular_bytes(path, max_bytes=max_bytes), max_bytes=max_bytes
    )


def read_canonical_jsonl(
    path: Path,
    *,
    max_bytes: int = MAX_ARTIFACT_JSONL_BYTES,
    max_lines: int = MAX_ARTIFACT_JSONL_LINES,
    max_line_bytes: int = MAX_ARTIFACT_JSONL_LINE_BYTES,
) -> tuple[object, ...]:
    return parse_canonical_jsonl_bytes(
        _read_regular_bytes(path, max_bytes=max_bytes),
        max_bytes=max_bytes,
        max_lines=max_lines,
        max_line_bytes=max_line_bytes,
    )


class RowType(StrEnum):
    CANDIDATE = "candidate"
    RAW = "raw"
    PURE_SOLVER = "pure_solver"


class BlobKind(StrEnum):
    NOTEGRAPH = "notegraph"
    TARGET = "target"
    TAB = "tab"
    TRACE = "trace"


class CompletionStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True, slots=True)
class ObservationKey:
    logical_call_id: str
    call_index: int

    def __post_init__(self) -> None:
        _identifier(self.logical_call_id, "observation_key.logical_call_id")
        _require_int(self.call_index, "observation_key.call_index", minimum=0, maximum=1_000_000)

    @property
    def sort_key(self) -> tuple[int, str]:
        return (self.call_index, self.logical_call_id)


@dataclass(frozen=True, slots=True)
class RowKey:
    row_type: RowType
    item_id: str
    sample_index: int | None
    candidate_index: int | None
    pair_id: str

    def __post_init__(self) -> None:
        if type(self.row_type) is not RowType:
            raise _error(ArtifactCode.INVALID_INPUT, "row_key.row_type", "must be a RowType")
        _identifier(self.item_id, "row_key.item_id")
        _identifier(self.pair_id, "row_key.pair_id")
        sample = _require_optional_int(
            self.sample_index,
            "row_key.sample_index",
            minimum=0,
            maximum=10_000,
        )
        candidate = _require_optional_int(
            self.candidate_index,
            "row_key.candidate_index",
            minimum=0,
            maximum=10_000,
        )
        if self.row_type is RowType.PURE_SOLVER:
            if sample is not None or candidate is not None:
                raise _error(
                    ArtifactCode.INVALID_INPUT,
                    "row_key",
                    "pure-solver rows require null sample/candidate indices",
                )
        elif sample is None or candidate is None or sample != candidate:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "row_key",
                "candidate/raw rows require equal sample and candidate indices",
            )

    @property
    def sort_key(self) -> tuple[str, str, int, int, str]:
        return (
            self.item_id,
            self.row_type.value,
            -1 if self.sample_index is None else self.sample_index,
            -1 if self.candidate_index is None else self.candidate_index,
            self.pair_id,
        )


@dataclass(frozen=True, slots=True)
class CompleteUnitReservation:
    """Worst-case resources required before one scheduled unit may begin."""

    logical_calls: int
    attempts: int
    requested_output_tokens: int
    attempt_reserved_output_tokens: int
    response_text_bytes: int
    transport_response_bytes: int
    wall_microseconds: int

    def __post_init__(self) -> None:
        for name, value in (
            ("logical_calls", self.logical_calls),
            ("attempts", self.attempts),
            ("requested_output_tokens", self.requested_output_tokens),
            ("attempt_reserved_output_tokens", self.attempt_reserved_output_tokens),
            ("response_text_bytes", self.response_text_bytes),
            ("transport_response_bytes", self.transport_response_bytes),
            ("wall_microseconds", self.wall_microseconds),
        ):
            _require_int(
                value,
                f"limits.complete_unit_reservation.{name}",
                minimum=1,
                maximum=MAX_ARTIFACT_BUDGET,
            )
        if self.attempts < self.logical_calls:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "limits.complete_unit_reservation.attempts",
                "cannot be smaller than logical_calls",
            )
        if self.attempt_reserved_output_tokens < self.requested_output_tokens:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "limits.complete_unit_reservation.attempt_reserved_output_tokens",
                "cannot be smaller than requested_output_tokens",
            )


@dataclass(frozen=True, slots=True)
class ArtifactLimits:
    max_rows: int
    max_blobs: int
    max_calls: int
    max_attempts: int
    max_json_bytes: int
    max_jsonl_line_bytes: int
    max_requested_output_tokens: int = MAX_ARTIFACT_BUDGET
    max_attempt_reserved_output_tokens: int = MAX_ARTIFACT_BUDGET
    max_response_text_bytes: int = MAX_ARTIFACT_BUDGET
    max_transport_response_bytes: int = MAX_ARTIFACT_BUDGET
    max_wall_microseconds: int = MAX_ARTIFACT_BUDGET
    complete_unit_reservation: CompleteUnitReservation | None = None

    def __post_init__(self) -> None:
        _require_int(self.max_rows, "limits.max_rows", minimum=1, maximum=MAX_ARTIFACT_ROWS)
        _require_int(self.max_blobs, "limits.max_blobs", minimum=1, maximum=MAX_ARTIFACT_BLOBS)
        _require_int(self.max_calls, "limits.max_calls", minimum=1, maximum=MAX_ARTIFACT_CALLS)
        _require_int(
            self.max_attempts,
            "limits.max_attempts",
            minimum=self.max_calls,
            maximum=MAX_ARTIFACT_ATTEMPTS,
        )
        _require_int(
            self.max_json_bytes,
            "limits.max_json_bytes",
            minimum=1,
            maximum=MAX_ARTIFACT_JSONL_BYTES,
        )
        _require_int(
            self.max_jsonl_line_bytes,
            "limits.max_jsonl_line_bytes",
            minimum=1,
            maximum=MAX_ARTIFACT_JSONL_LINE_BYTES,
        )
        if self.max_jsonl_line_bytes > self.max_json_bytes:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "limits.max_jsonl_line_bytes",
                "cannot exceed max_json_bytes",
            )
        for name, value in (
            ("max_requested_output_tokens", self.max_requested_output_tokens),
            (
                "max_attempt_reserved_output_tokens",
                self.max_attempt_reserved_output_tokens,
            ),
            ("max_response_text_bytes", self.max_response_text_bytes),
            ("max_transport_response_bytes", self.max_transport_response_bytes),
            ("max_wall_microseconds", self.max_wall_microseconds),
        ):
            _require_int(
                value,
                f"limits.{name}",
                minimum=1,
                maximum=MAX_ARTIFACT_BUDGET,
            )
        reservation = self.complete_unit_reservation
        if reservation is not None:
            if type(reservation) is not CompleteUnitReservation:
                raise _error(
                    ArtifactCode.INVALID_INPUT,
                    "limits.complete_unit_reservation",
                    "must be null or an exact CompleteUnitReservation",
                )
            for name, reserved, maximum in (
                ("logical_calls", reservation.logical_calls, self.max_calls),
                ("attempts", reservation.attempts, self.max_attempts),
                (
                    "requested_output_tokens",
                    reservation.requested_output_tokens,
                    self.max_requested_output_tokens,
                ),
                (
                    "attempt_reserved_output_tokens",
                    reservation.attempt_reserved_output_tokens,
                    self.max_attempt_reserved_output_tokens,
                ),
                (
                    "response_text_bytes",
                    reservation.response_text_bytes,
                    self.max_response_text_bytes,
                ),
                (
                    "transport_response_bytes",
                    reservation.transport_response_bytes,
                    self.max_transport_response_bytes,
                ),
                (
                    "wall_microseconds",
                    reservation.wall_microseconds,
                    self.max_wall_microseconds,
                ),
            ):
                if reserved > maximum:
                    raise _error(
                        ArtifactCode.INVALID_INPUT,
                        f"limits.complete_unit_reservation.{name}",
                        f"cannot exceed limits.{('max_' + name)}",
                    )


def _row_key_to_dict(value: RowKey) -> dict[str, object]:
    if type(value) is not RowKey:
        raise _error(ArtifactCode.INVALID_INPUT, "row_key", "must be an exact RowKey")
    return {
        "candidate_index": value.candidate_index,
        "item_id": value.item_id,
        "pair_id": value.pair_id,
        "row_type": value.row_type.value,
        "sample_index": value.sample_index,
    }


def _row_key_from_dict(value: object, field: str = "row_key") -> RowKey:
    obj = _require_exact_dict(
        value,
        field,
        frozenset({"row_type", "item_id", "sample_index", "candidate_index", "pair_id"}),
    )
    raw_row_type = obj["row_type"]
    if type(raw_row_type) is not str:
        raise _error(ArtifactCode.INVALID_INPUT, f"{field}.row_type", "is not supported")
    try:
        row_type = RowType(raw_row_type)
    except (TypeError, ValueError):
        raise _error(ArtifactCode.INVALID_INPUT, f"{field}.row_type", "is not supported") from None
    return RowKey(
        row_type,
        _identifier(obj["item_id"], f"{field}.item_id"),
        _require_optional_int(
            obj["sample_index"],
            f"{field}.sample_index",
            minimum=0,
            maximum=10_000,
        ),
        _require_optional_int(
            obj["candidate_index"],
            f"{field}.candidate_index",
            minimum=0,
            maximum=10_000,
        ),
        _identifier(obj["pair_id"], f"{field}.pair_id"),
    )


def _limits_to_dict(value: ArtifactLimits) -> dict[str, object]:
    if type(value) is not ArtifactLimits:
        raise _error(ArtifactCode.INVALID_INPUT, "limits", "must be exact ArtifactLimits")
    return {
        "complete_unit_reservation": (
            None
            if value.complete_unit_reservation is None
            else {
                "attempt_reserved_output_tokens": (
                    value.complete_unit_reservation.attempt_reserved_output_tokens
                ),
                "attempts": value.complete_unit_reservation.attempts,
                "logical_calls": value.complete_unit_reservation.logical_calls,
                "requested_output_tokens": (
                    value.complete_unit_reservation.requested_output_tokens
                ),
                "response_text_bytes": value.complete_unit_reservation.response_text_bytes,
                "transport_response_bytes": (
                    value.complete_unit_reservation.transport_response_bytes
                ),
                "wall_microseconds": value.complete_unit_reservation.wall_microseconds,
            }
        ),
        "max_attempts": value.max_attempts,
        "max_attempt_reserved_output_tokens": value.max_attempt_reserved_output_tokens,
        "max_blobs": value.max_blobs,
        "max_calls": value.max_calls,
        "max_json_bytes": value.max_json_bytes,
        "max_jsonl_line_bytes": value.max_jsonl_line_bytes,
        "max_requested_output_tokens": value.max_requested_output_tokens,
        "max_response_text_bytes": value.max_response_text_bytes,
        "max_rows": value.max_rows,
        "max_transport_response_bytes": value.max_transport_response_bytes,
        "max_wall_microseconds": value.max_wall_microseconds,
    }


def _limits_from_dict(value: object) -> ArtifactLimits:
    obj = _require_exact_dict(
        value,
        "limits",
        frozenset(
            {
                "complete_unit_reservation",
                "max_rows",
                "max_blobs",
                "max_calls",
                "max_attempts",
                "max_json_bytes",
                "max_jsonl_line_bytes",
                "max_requested_output_tokens",
                "max_attempt_reserved_output_tokens",
                "max_response_text_bytes",
                "max_transport_response_bytes",
                "max_wall_microseconds",
            }
        ),
    )
    raw_reservation = obj["complete_unit_reservation"]
    reservation: CompleteUnitReservation | None = None
    if raw_reservation is not None:
        reservation_obj = _require_exact_dict(
            raw_reservation,
            "limits.complete_unit_reservation",
            frozenset(
                {
                    "logical_calls",
                    "attempts",
                    "requested_output_tokens",
                    "attempt_reserved_output_tokens",
                    "response_text_bytes",
                    "transport_response_bytes",
                    "wall_microseconds",
                }
            ),
        )
        reservation = CompleteUnitReservation(
            _require_int(
                reservation_obj["logical_calls"],
                "limits.complete_unit_reservation.logical_calls",
                minimum=1,
                maximum=MAX_ARTIFACT_BUDGET,
            ),
            _require_int(
                reservation_obj["attempts"],
                "limits.complete_unit_reservation.attempts",
                minimum=1,
                maximum=MAX_ARTIFACT_BUDGET,
            ),
            _require_int(
                reservation_obj["requested_output_tokens"],
                "limits.complete_unit_reservation.requested_output_tokens",
                minimum=1,
                maximum=MAX_ARTIFACT_BUDGET,
            ),
            _require_int(
                reservation_obj["attempt_reserved_output_tokens"],
                "limits.complete_unit_reservation.attempt_reserved_output_tokens",
                minimum=1,
                maximum=MAX_ARTIFACT_BUDGET,
            ),
            _require_int(
                reservation_obj["response_text_bytes"],
                "limits.complete_unit_reservation.response_text_bytes",
                minimum=1,
                maximum=MAX_ARTIFACT_BUDGET,
            ),
            _require_int(
                reservation_obj["transport_response_bytes"],
                "limits.complete_unit_reservation.transport_response_bytes",
                minimum=1,
                maximum=MAX_ARTIFACT_BUDGET,
            ),
            _require_int(
                reservation_obj["wall_microseconds"],
                "limits.complete_unit_reservation.wall_microseconds",
                minimum=1,
                maximum=MAX_ARTIFACT_BUDGET,
            ),
        )
    return ArtifactLimits(
        _require_int(obj["max_rows"], "limits.max_rows", minimum=1, maximum=MAX_ARTIFACT_ROWS),
        _require_int(obj["max_blobs"], "limits.max_blobs", minimum=1, maximum=MAX_ARTIFACT_BLOBS),
        _require_int(obj["max_calls"], "limits.max_calls", minimum=1, maximum=MAX_ARTIFACT_CALLS),
        _require_int(
            obj["max_attempts"],
            "limits.max_attempts",
            minimum=1,
            maximum=MAX_ARTIFACT_ATTEMPTS,
        ),
        _require_int(
            obj["max_json_bytes"],
            "limits.max_json_bytes",
            minimum=1,
            maximum=MAX_ARTIFACT_JSONL_BYTES,
        ),
        _require_int(
            obj["max_jsonl_line_bytes"],
            "limits.max_jsonl_line_bytes",
            minimum=1,
            maximum=MAX_ARTIFACT_JSONL_LINE_BYTES,
        ),
        _require_int(
            obj["max_requested_output_tokens"],
            "limits.max_requested_output_tokens",
            minimum=1,
            maximum=MAX_ARTIFACT_BUDGET,
        ),
        _require_int(
            obj["max_attempt_reserved_output_tokens"],
            "limits.max_attempt_reserved_output_tokens",
            minimum=1,
            maximum=MAX_ARTIFACT_BUDGET,
        ),
        _require_int(
            obj["max_response_text_bytes"],
            "limits.max_response_text_bytes",
            minimum=1,
            maximum=MAX_ARTIFACT_BUDGET,
        ),
        _require_int(
            obj["max_transport_response_bytes"],
            "limits.max_transport_response_bytes",
            minimum=1,
            maximum=MAX_ARTIFACT_BUDGET,
        ),
        _require_int(
            obj["max_wall_microseconds"],
            "limits.max_wall_microseconds",
            minimum=1,
            maximum=MAX_ARTIFACT_BUDGET,
        ),
        reservation,
    )


@dataclass(frozen=True, slots=True)
class BenchmarkManifest:
    run_id: str
    corpus_sha256: str
    analysis_code_sha256: str
    stub: bool
    expected_rows: tuple[RowKey, ...]
    limits: ArtifactLimits
    parameters_json: bytes

    def __post_init__(self) -> None:
        _identifier(self.run_id, "manifest.run_id")
        _sha256(self.corpus_sha256, "manifest.corpus_sha256")
        _sha256(self.analysis_code_sha256, "manifest.analysis_code_sha256")
        if type(self.stub) is not bool:
            raise _error(ArtifactCode.INVALID_INPUT, "manifest.stub", "must be an exact bool")
        if type(self.expected_rows) is not tuple or any(
            type(value) is not RowKey for value in self.expected_rows
        ):
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "manifest.expected_rows",
                "must contain exact RowKey values",
            )
        if not self.expected_rows or len(self.expected_rows) > self.limits.max_rows:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "manifest.expected_rows",
                "must be nonempty and fit max_rows",
            )
        canonical_rows = tuple(sorted(self.expected_rows, key=lambda value: value.sort_key))
        if self.expected_rows != canonical_rows or len(set(self.expected_rows)) != len(
            self.expected_rows
        ):
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "manifest.expected_rows",
                "must be uniquely sorted by canonical row key",
            )
        if type(self.limits) is not ArtifactLimits:
            raise _error(ArtifactCode.INVALID_INPUT, "manifest.limits", "must be ArtifactLimits")
        if type(self.parameters_json) is not bytes:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "manifest.parameters_json",
                "must be exact canonical bytes",
            )
        parameters = parse_canonical_json_bytes(
            self.parameters_json,
            max_bytes=self.limits.max_json_bytes,
        )
        if type(parameters) is not dict:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "manifest.parameters",
                "must encode one exact object",
            )

    @property
    def parameters(self) -> dict[str, object]:
        return cast(
            dict[str, object],
            parse_canonical_json_bytes(
                self.parameters_json,
                max_bytes=self.limits.max_json_bytes,
            ),
        )


def build_manifest(
    *,
    run_id: str,
    corpus_sha256: str,
    analysis_code_sha256: str,
    stub: bool,
    expected_rows: tuple[RowKey, ...],
    limits: ArtifactLimits,
    parameters: dict[str, object],
) -> BenchmarkManifest:
    if type(parameters) is not dict:
        raise _error(ArtifactCode.INVALID_INPUT, "parameters", "must be an exact object")
    try:
        parameters_json = canonical_json_bytes(parameters)
    except BenchmarkContractError as error:
        raise _error(ArtifactCode.INVALID_INPUT, "parameters", error.detail) from None
    return BenchmarkManifest(
        run_id,
        corpus_sha256,
        analysis_code_sha256,
        stub,
        tuple(sorted(expected_rows, key=lambda value: value.sort_key)),
        limits,
        parameters_json,
    )


def manifest_to_dict(value: BenchmarkManifest) -> dict[str, object]:
    if type(value) is not BenchmarkManifest:
        raise _error(ArtifactCode.INVALID_INPUT, "manifest", "must be exact BenchmarkManifest")
    return {
        "analysis_code_sha256": value.analysis_code_sha256,
        "corpus_sha256": value.corpus_sha256,
        "expected_rows": [_row_key_to_dict(row) for row in value.expected_rows],
        "limits": _limits_to_dict(value.limits),
        "parameters": value.parameters,
        "run_id": value.run_id,
        "schema": BENCHMARK_MANIFEST_VERSION,
        "stub": value.stub,
    }


def manifest_from_dict(value: object) -> BenchmarkManifest:
    obj = _require_exact_dict(
        value,
        "manifest",
        frozenset(
            {
                "schema",
                "run_id",
                "corpus_sha256",
                "analysis_code_sha256",
                "stub",
                "expected_rows",
                "limits",
                "parameters",
            }
        ),
    )
    if obj["schema"] != BENCHMARK_MANIFEST_VERSION:
        raise _error(ArtifactCode.INVALID_INPUT, "manifest.schema", "has the wrong version")
    limits = _limits_from_dict(obj["limits"])
    rows = _require_exact_list(
        obj["expected_rows"],
        "manifest.expected_rows",
        maximum=limits.max_rows,
    )
    if type(obj["stub"]) is not bool:
        raise _error(ArtifactCode.INVALID_INPUT, "manifest.stub", "must be an exact bool")
    parameters = obj["parameters"]
    if type(parameters) is not dict:
        raise _error(ArtifactCode.INVALID_INPUT, "manifest.parameters", "must be exact object")
    return build_manifest(
        run_id=_identifier(obj["run_id"], "manifest.run_id"),
        corpus_sha256=_sha256(obj["corpus_sha256"], "manifest.corpus_sha256"),
        analysis_code_sha256=_sha256(obj["analysis_code_sha256"], "manifest.analysis_code_sha256"),
        stub=obj["stub"],
        expected_rows=tuple(
            _row_key_from_dict(row, f"manifest.expected_rows[{index}]")
            for index, row in enumerate(rows)
        ),
        limits=limits,
        parameters=cast(dict[str, object], parameters),
    )


def manifest_sha256(value: BenchmarkManifest) -> str:
    return canonical_sha256(BENCHMARK_MANIFEST_VERSION, manifest_to_dict(value))


@dataclass(frozen=True, slots=True)
class BlobRef:
    kind: BlobKind
    sha256: str
    byte_length: int

    def __post_init__(self) -> None:
        if type(self.kind) is not BlobKind:
            raise _error(ArtifactCode.INVALID_INPUT, "blob.kind", "must be a BlobKind")
        _sha256(self.sha256, "blob.sha256")
        _require_int(
            self.byte_length,
            "blob.byte_length",
            minimum=1,
            maximum=MAX_BENCHMARK_JSON_BYTES,
        )

    @property
    def sort_key(self) -> tuple[str, str]:
        return (self.sha256, self.kind.value)


@dataclass(frozen=True, slots=True)
class BlobRecord:
    ref: BlobRef
    content_json: bytes

    def __post_init__(self) -> None:
        if type(self.ref) is not BlobRef or type(self.content_json) is not bytes:
            raise _error(ArtifactCode.INVALID_INPUT, "blob", "record fields have invalid types")
        content = parse_canonical_json_bytes(self.content_json)
        if len(self.content_json) != self.ref.byte_length:
            raise _error(ArtifactCode.HASH_MISMATCH, "blob.byte_length", "does not match content")
        if canonical_blob_sha256(self.ref.kind, content) != self.ref.sha256:
            raise _error(ArtifactCode.HASH_MISMATCH, "blob.sha256", "digest does not match content")

    @property
    def content(self) -> object:
        return parse_canonical_json_bytes(self.content_json)


def canonical_blob_sha256(kind: BlobKind, content: object) -> str:
    if type(kind) is not BlobKind:
        raise _error(ArtifactCode.INVALID_INPUT, "blob.kind", "must be a BlobKind")
    try:
        encoded = canonical_json_bytes(content)
    except BenchmarkContractError as error:
        raise _error(ArtifactCode.INVALID_INPUT, "blob.content", error.detail) from None
    return hashlib.sha256(_BLOB_DOMAIN + kind.value.encode("ascii") + b"\0" + encoded).hexdigest()


def build_blob_record(kind: BlobKind, content: object) -> BlobRecord:
    try:
        encoded = canonical_json_bytes(content)
    except BenchmarkContractError as error:
        raise _error(ArtifactCode.INVALID_INPUT, "blob.content", error.detail) from None
    return BlobRecord(BlobRef(kind, canonical_blob_sha256(kind, content), len(encoded)), encoded)


def _blob_ref_to_dict(value: BlobRef) -> dict[str, object]:
    return {"byte_length": value.byte_length, "kind": value.kind.value, "sha256": value.sha256}


def _blob_ref_from_dict(value: object, field: str) -> BlobRef:
    obj = _require_exact_dict(value, field, frozenset({"kind", "sha256", "byte_length"}))
    raw_kind = obj["kind"]
    if type(raw_kind) is not str:
        raise _error(ArtifactCode.INVALID_INPUT, f"{field}.kind", "is unsupported")
    try:
        kind = BlobKind(raw_kind)
    except (TypeError, ValueError):
        raise _error(ArtifactCode.INVALID_INPUT, f"{field}.kind", "is unsupported") from None
    return BlobRef(
        kind,
        _sha256(obj["sha256"], f"{field}.sha256"),
        _require_int(
            obj["byte_length"],
            f"{field}.byte_length",
            minimum=1,
            maximum=MAX_BENCHMARK_JSON_BYTES,
        ),
    )


def blob_ref_to_dict(value: BlobRef) -> dict[str, object]:
    if type(value) is not BlobRef:
        raise _error(ArtifactCode.INVALID_INPUT, "blob_ref", "must be exact BlobRef")
    return _blob_ref_to_dict(value)


def blob_ref_from_dict(value: object) -> BlobRef:
    return _blob_ref_from_dict(value, "blob_ref")


def blob_record_to_dict(value: BlobRecord) -> dict[str, object]:
    if type(value) is not BlobRecord:
        raise _error(ArtifactCode.INVALID_INPUT, "blob", "must be exact BlobRecord")
    return {**_blob_ref_to_dict(value.ref), "content": value.content}


def blob_record_from_dict(value: object) -> BlobRecord:
    obj = _require_exact_dict(
        value,
        "blob",
        frozenset({"kind", "sha256", "byte_length", "content"}),
    )
    ref = _blob_ref_from_dict(
        {key: obj[key] for key in ("kind", "sha256", "byte_length")},
        "blob",
    )
    try:
        content_json = canonical_json_bytes(obj["content"])
    except BenchmarkContractError as error:
        raise _error(ArtifactCode.INVALID_INPUT, "blob.content", error.detail) from None
    return BlobRecord(ref, content_json)


_PAYLOAD_KEYS: Final[dict[RowType, frozenset[str]]] = {
    RowType.CANDIDATE: frozenset({"source", "proposal", "initial", "terminal", "critic", "work"}),
    RowType.RAW: frozenset({"source", "outcome", "score"}),
    RowType.PURE_SOLVER: frozenset({"source", "outcome", "score", "baseline"}),
}


@dataclass(frozen=True, slots=True)
class BenchmarkRow:
    run_id: str
    key: RowKey
    family_id: str
    cluster_id: str
    observation_keys: tuple[ObservationKey, ...]
    blob_refs: tuple[BlobRef, ...]
    payload_json: bytes

    def __post_init__(self) -> None:
        _identifier(self.run_id, "row.run_id")
        if type(self.key) is not RowKey:
            raise _error(ArtifactCode.INVALID_INPUT, "row.key", "must be exact RowKey")
        _identifier(self.family_id, "row.family_id")
        _identifier(self.cluster_id, "row.cluster_id")
        if type(self.observation_keys) is not tuple or any(
            type(value) is not ObservationKey for value in self.observation_keys
        ):
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "row.observation_keys",
                "must contain exact ObservationKey values",
            )
        if self.observation_keys != tuple(
            sorted(self.observation_keys, key=lambda value: value.sort_key)
        ) or len(set(self.observation_keys)) != len(self.observation_keys):
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "row.observation_keys",
                "must be uniquely sorted",
            )
        if type(self.blob_refs) is not tuple or any(
            type(value) is not BlobRef for value in self.blob_refs
        ):
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "row.blob_refs",
                "must contain exact BlobRef values",
            )
        if self.blob_refs != tuple(sorted(self.blob_refs, key=lambda value: value.sort_key)) or len(
            set(self.blob_refs)
        ) != len(self.blob_refs):
            raise _error(ArtifactCode.INVALID_INPUT, "row.blob_refs", "must be uniquely sorted")
        if type(self.payload_json) is not bytes:
            raise _error(ArtifactCode.INVALID_INPUT, "row.payload", "must be canonical bytes")
        payload = parse_canonical_json_bytes(self.payload_json)
        try:
            _require_exact_dict(payload, "row.payload", _PAYLOAD_KEYS[self.key.row_type])
        except ArtifactError:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "row.payload",
                "payload exact keys are required for this row type",
            ) from None
        if self.key.row_type is RowType.PURE_SOLVER and self.observation_keys:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "row.observation_keys",
                "pure-solver rows cannot own model calls",
            )

    @property
    def payload(self) -> dict[str, object]:
        return cast(dict[str, object], parse_canonical_json_bytes(self.payload_json))

    @property
    def sha256(self) -> str:
        return canonical_sha256(BENCHMARK_ROW_VERSION, row_to_dict(self))

    @property
    def sort_key(self) -> tuple[str, str, int, int, str]:
        return self.key.sort_key


def build_row(
    *,
    run_id: str,
    key: RowKey,
    family_id: str,
    cluster_id: str,
    observation_keys: tuple[ObservationKey, ...],
    blob_refs: tuple[BlobRef, ...],
    payload: dict[str, object],
) -> BenchmarkRow:
    if type(payload) is not dict:
        raise _error(ArtifactCode.INVALID_INPUT, "row.payload", "must be an exact object")
    try:
        payload_json = canonical_json_bytes(payload)
    except BenchmarkContractError as error:
        raise _error(ArtifactCode.INVALID_INPUT, "row.payload", error.detail) from None
    return BenchmarkRow(
        run_id,
        key,
        family_id,
        cluster_id,
        tuple(sorted(observation_keys, key=lambda value: value.sort_key)),
        tuple(sorted(blob_refs, key=lambda value: value.sort_key)),
        payload_json,
    )


def _observation_key_to_dict(value: ObservationKey) -> dict[str, object]:
    return {"call_index": value.call_index, "logical_call_id": value.logical_call_id}


def _observation_key_from_dict(value: object, field: str) -> ObservationKey:
    obj = _require_exact_dict(value, field, frozenset({"logical_call_id", "call_index"}))
    return ObservationKey(
        _identifier(obj["logical_call_id"], f"{field}.logical_call_id"),
        _require_int(
            obj["call_index"],
            f"{field}.call_index",
            minimum=0,
            maximum=1_000_000,
        ),
    )


def row_to_dict(value: BenchmarkRow) -> dict[str, object]:
    if type(value) is not BenchmarkRow:
        raise _error(ArtifactCode.INVALID_INPUT, "row", "must be exact BenchmarkRow")
    return {
        "blob_refs": [_blob_ref_to_dict(ref) for ref in value.blob_refs],
        "candidate_index": value.key.candidate_index,
        "cluster_id": value.cluster_id,
        "family_id": value.family_id,
        "item_id": value.key.item_id,
        "observation_keys": [_observation_key_to_dict(key) for key in value.observation_keys],
        "pair_id": value.key.pair_id,
        "payload": value.payload,
        "row_type": value.key.row_type.value,
        "run_id": value.run_id,
        "sample_index": value.key.sample_index,
        "schema": BENCHMARK_ROW_VERSION,
    }


def row_from_dict(value: object) -> BenchmarkRow:
    obj = _require_exact_dict(
        value,
        "row",
        frozenset(
            {
                "schema",
                "run_id",
                "row_type",
                "item_id",
                "family_id",
                "cluster_id",
                "sample_index",
                "candidate_index",
                "pair_id",
                "observation_keys",
                "blob_refs",
                "payload",
            }
        ),
    )
    if obj["schema"] != BENCHMARK_ROW_VERSION:
        raise _error(ArtifactCode.INVALID_INPUT, "row.schema", "has the wrong version")
    key = _row_key_from_dict(
        {
            name: obj[name]
            for name in ("row_type", "item_id", "sample_index", "candidate_index", "pair_id")
        },
        "row.key",
    )
    observations = _require_exact_list(
        obj["observation_keys"],
        "row.observation_keys",
        maximum=MAX_ARTIFACT_CALLS,
    )
    blobs = _require_exact_list(
        obj["blob_refs"],
        "row.blob_refs",
        maximum=MAX_ARTIFACT_BLOBS,
    )
    payload = obj["payload"]
    if type(payload) is not dict:
        raise _error(ArtifactCode.INVALID_INPUT, "row.payload", "must be an exact object")
    return build_row(
        run_id=_identifier(obj["run_id"], "row.run_id"),
        key=key,
        family_id=_identifier(obj["family_id"], "row.family_id"),
        cluster_id=_identifier(obj["cluster_id"], "row.cluster_id"),
        observation_keys=tuple(
            _observation_key_from_dict(value, f"row.observation_keys[{index}]")
            for index, value in enumerate(observations)
        ),
        blob_refs=tuple(
            _blob_ref_from_dict(value, f"row.blob_refs[{index}]")
            for index, value in enumerate(blobs)
        ),
        payload=cast(dict[str, object], payload),
    )


@dataclass(frozen=True, slots=True)
class BenchmarkReceipt:
    run_id: str
    config_sha256: str
    corpus_sha256: str
    journal_sha256: str
    rows_sha256: str | None
    blobs_sha256: str | None
    observations_sha256: str | None
    observed_returned_models: tuple[str, ...]
    analysis_code_sha256: str
    expected_rows: int
    observed_rows: int
    maximum_calls: int
    observed_calls: int
    status: CompletionStatus
    reason_code: str | None

    def __post_init__(self) -> None:
        _identifier(self.run_id, "receipt.run_id")
        for field, value in (
            ("config_sha256", self.config_sha256),
            ("corpus_sha256", self.corpus_sha256),
            ("journal_sha256", self.journal_sha256),
            ("analysis_code_sha256", self.analysis_code_sha256),
        ):
            _sha256(value, f"receipt.{field}")
        for field, optional_value in (
            ("rows_sha256", self.rows_sha256),
            ("blobs_sha256", self.blobs_sha256),
            ("observations_sha256", self.observations_sha256),
        ):
            if optional_value is not None:
                _sha256(optional_value, f"receipt.{field}")
        if type(self.observed_returned_models) is not tuple:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "receipt.observed_returned_models",
                "must be an exact tuple",
            )
        models = tuple(
            cast(str, _model_id(model, "receipt.observed_returned_models", optional=False))
            for model in self.observed_returned_models
        )
        if models != tuple(sorted(set(models))):
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "receipt.observed_returned_models",
                "must be uniquely sorted",
            )
        expected_rows = _require_int(
            self.expected_rows,
            "receipt.expected_rows",
            minimum=0,
            maximum=MAX_ARTIFACT_ROWS,
        )
        observed_rows = _require_int(
            self.observed_rows,
            "receipt.observed_rows",
            minimum=0,
            maximum=MAX_ARTIFACT_ROWS,
        )
        maximum_calls = _require_int(
            self.maximum_calls,
            "receipt.maximum_calls",
            minimum=1,
            maximum=MAX_ARTIFACT_CALLS,
        )
        _require_int(
            self.observed_calls,
            "receipt.observed_calls",
            minimum=0,
            maximum=maximum_calls,
        )
        if type(self.status) is not CompletionStatus:
            raise _error(ArtifactCode.INVALID_INPUT, "receipt.status", "is invalid")
        if self.status is CompletionStatus.COMPLETE:
            if (
                self.rows_sha256 is None
                or self.blobs_sha256 is None
                or self.observations_sha256 is None
                or self.reason_code is not None
                or observed_rows != expected_rows
            ):
                raise _error(
                    ArtifactCode.INVALID_INPUT,
                    "receipt",
                    "COMPLETE fields/counts are inconsistent",
                )
        else:
            if (
                any(
                    value is not None
                    for value in (self.rows_sha256, self.blobs_sha256, self.observations_sha256)
                )
                or self.reason_code is None
            ):
                raise _error(
                    ArtifactCode.INVALID_INPUT,
                    "receipt",
                    "INCOMPLETE receipt cannot own finalized hashes and needs a reason",
                )
            _identifier(self.reason_code, "receipt.reason_code")


def receipt_to_dict(value: BenchmarkReceipt) -> dict[str, object]:
    if type(value) is not BenchmarkReceipt:
        raise _error(ArtifactCode.INVALID_INPUT, "receipt", "must be exact BenchmarkReceipt")
    return {
        "analysis_code_sha256": value.analysis_code_sha256,
        "blobs_sha256": value.blobs_sha256,
        "config_sha256": value.config_sha256,
        "corpus_sha256": value.corpus_sha256,
        "expected_rows": value.expected_rows,
        "journal_sha256": value.journal_sha256,
        "maximum_calls": value.maximum_calls,
        "observations_sha256": value.observations_sha256,
        "observed_calls": value.observed_calls,
        "observed_returned_models": list(value.observed_returned_models),
        "observed_rows": value.observed_rows,
        "reason_code": value.reason_code,
        "rows_sha256": value.rows_sha256,
        "run_id": value.run_id,
        "schema": BENCHMARK_RECEIPT_VERSION,
        "status": value.status.value,
    }


def receipt_from_dict(value: object) -> BenchmarkReceipt:
    obj = _require_exact_dict(
        value,
        "receipt",
        frozenset(
            {
                "schema",
                "run_id",
                "config_sha256",
                "corpus_sha256",
                "journal_sha256",
                "rows_sha256",
                "blobs_sha256",
                "observations_sha256",
                "observed_returned_models",
                "analysis_code_sha256",
                "expected_rows",
                "observed_rows",
                "maximum_calls",
                "observed_calls",
                "status",
                "reason_code",
            }
        ),
    )
    if obj["schema"] != BENCHMARK_RECEIPT_VERSION:
        raise _error(ArtifactCode.INVALID_INPUT, "receipt.schema", "has the wrong version")
    models = _require_exact_list(
        obj["observed_returned_models"],
        "receipt.observed_returned_models",
        maximum=64,
    )
    raw_status = obj["status"]
    if type(raw_status) is not str:
        raise _error(ArtifactCode.INVALID_INPUT, "receipt.status", "is unsupported")
    try:
        status = CompletionStatus(raw_status)
    except (TypeError, ValueError):
        raise _error(ArtifactCode.INVALID_INPUT, "receipt.status", "is unsupported") from None
    reason: str | None
    raw_reason = obj["reason_code"]
    reason = None
    if raw_reason is not None:
        reason = _identifier(raw_reason, "receipt.reason_code")

    def optional_digest(name: str) -> str | None:
        raw = obj[name]
        return None if raw is None else _sha256(raw, f"receipt.{name}")

    return BenchmarkReceipt(
        _identifier(obj["run_id"], "receipt.run_id"),
        _sha256(obj["config_sha256"], "receipt.config_sha256"),
        _sha256(obj["corpus_sha256"], "receipt.corpus_sha256"),
        _sha256(obj["journal_sha256"], "receipt.journal_sha256"),
        optional_digest("rows_sha256"),
        optional_digest("blobs_sha256"),
        optional_digest("observations_sha256"),
        tuple(
            cast(str, _model_id(model, f"receipt.models[{index}]", optional=False))
            for index, model in enumerate(models)
        ),
        _sha256(obj["analysis_code_sha256"], "receipt.analysis_code_sha256"),
        _require_int(
            obj["expected_rows"],
            "receipt.expected_rows",
            minimum=0,
            maximum=MAX_ARTIFACT_ROWS,
        ),
        _require_int(
            obj["observed_rows"],
            "receipt.observed_rows",
            minimum=0,
            maximum=MAX_ARTIFACT_ROWS,
        ),
        _require_int(
            obj["maximum_calls"],
            "receipt.maximum_calls",
            minimum=1,
            maximum=MAX_ARTIFACT_CALLS,
        ),
        _require_int(
            obj["observed_calls"],
            "receipt.observed_calls",
            minimum=0,
            maximum=MAX_ARTIFACT_CALLS,
        ),
        status,
        reason,
    )


def receipt_sha256(value: BenchmarkReceipt) -> str:
    return canonical_sha256(BENCHMARK_RECEIPT_VERSION, receipt_to_dict(value))


def require_complete_receipt(value: BenchmarkReceipt) -> BenchmarkReceipt:
    if type(value) is not BenchmarkReceipt:
        raise _error(ArtifactCode.INVALID_INPUT, "receipt", "must be exact BenchmarkReceipt")
    if value.status is not CompletionStatus.COMPLETE:
        raise _error(ArtifactCode.INCOMPLETE, "receipt.status", "abort receipt cannot enter report")
    return value


def canonical_table_sha256(kind: str, data: bytes) -> str:
    if kind == "rows":
        domain = _ROW_TABLE_DOMAIN
    elif kind == "blobs":
        domain = _BLOB_TABLE_DOMAIN
    else:
        raise _error(ArtifactCode.INVALID_INPUT, "table.kind", "must be rows or blobs")
    if type(data) is not bytes:
        raise _error(ArtifactCode.INVALID_INPUT, "table.data", "must be exact bytes")
    return hashlib.sha256(domain + data).hexdigest()


class _ObservationSource(Protocol):
    @property
    def intents(self) -> tuple[CallIntent, ...]: ...

    @property
    def results(self) -> tuple[CallResult, ...]: ...

    @property
    def attempt_intents(self) -> tuple[AttemptIntent, ...]: ...

    @property
    def attempt_results(self) -> tuple[AttemptResult, ...]: ...


@dataclass(frozen=True, slots=True)
class PrivateObservations:
    run_id: str
    calls_json: tuple[bytes, ...]

    def __post_init__(self) -> None:
        _identifier(self.run_id, "private_observations.run_id")
        if type(self.calls_json) is not tuple or any(
            type(value) is not bytes for value in self.calls_json
        ):
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "private_observations.calls",
                "must contain canonical bytes",
            )
        for index, value in enumerate(self.calls_json):
            parsed = parse_canonical_json_bytes(value)
            if type(parsed) is not dict:
                raise _error(
                    ArtifactCode.INVALID_INPUT,
                    f"private_observations.calls[{index}]",
                    "must encode an object",
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "calls": [parse_canonical_json_bytes(value) for value in self.calls_json],
            "run_id": self.run_id,
            "schema": BENCHMARK_PRIVATE_OBSERVATIONS_VERSION,
        }


@dataclass(frozen=True, slots=True)
class SanitizedObservations:
    run_id: str
    calls_json: tuple[bytes, ...]

    def __post_init__(self) -> None:
        _identifier(self.run_id, "observations.run_id")
        if type(self.calls_json) is not tuple or any(
            type(value) is not bytes for value in self.calls_json
        ):
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "observations.calls",
                "must contain canonical bytes",
            )
        previous = -1
        for index, value in enumerate(self.calls_json):
            parsed = parse_canonical_json_bytes(value)
            obj = _require_exact_dict(
                parsed,
                f"observations.calls[{index}]",
                frozenset(
                    {
                        "logical_call_id",
                        "call_index",
                        "status",
                        "failure_code",
                        "attempts",
                        "retry_count",
                        "returned_model_id",
                        "usage",
                        "elapsed_microseconds",
                    }
                ),
            )
            call_index = _require_int(
                obj["call_index"],
                f"observations.calls[{index}].call_index",
                minimum=0,
                maximum=1_000_000,
            )
            if call_index != previous + 1:
                raise _error(
                    ArtifactCode.INVALID_INPUT,
                    "observations.calls",
                    "call indices must be contiguous",
                )
            previous = call_index

    def to_dict(self) -> dict[str, object]:
        return {
            "calls": [parse_canonical_json_bytes(value) for value in self.calls_json],
            "run_id": self.run_id,
            "schema": BENCHMARK_OBSERVATIONS_VERSION,
        }

    @property
    def sha256(self) -> str:
        return canonical_sha256(BENCHMARK_OBSERVATIONS_VERSION, self.to_dict())


def _sanitized_call_from_dict(value: object, index: int) -> dict[str, object]:
    field = f"observations.calls[{index}]"
    obj = _require_exact_dict(
        value,
        field,
        frozenset(
            {
                "logical_call_id",
                "call_index",
                "status",
                "failure_code",
                "attempts",
                "retry_count",
                "returned_model_id",
                "usage",
                "elapsed_microseconds",
            }
        ),
    )
    status = obj["status"]
    if status not in ("succeeded", "failed"):
        raise _error(ArtifactCode.INVALID_INPUT, f"{field}.status", "is invalid")
    failure_raw = obj["failure_code"]
    failure: str | None = None
    if failure_raw is not None:
        if type(failure_raw) is not str:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                f"{field}.failure_code",
                "is invalid",
            )
        try:
            failure = CallFailureCode(failure_raw).value
        except ValueError:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                f"{field}.failure_code",
                "is invalid",
            ) from None
    if (status == "succeeded") != (failure is None):
        raise _error(
            ArtifactCode.INVALID_INPUT,
            field,
            "status and failure_code are inconsistent",
        )

    raw_attempts = _require_exact_list(
        obj["attempts"],
        f"{field}.attempts",
        maximum=16,
    )
    if not raw_attempts:
        raise _error(
            ArtifactCode.INVALID_INPUT,
            f"{field}.attempts",
            "must contain at least one attempt",
        )
    attempts: list[dict[str, object]] = []
    attempt_ids: set[str] = set()
    for attempt_index, raw_attempt in enumerate(raw_attempts):
        attempt_field = f"{field}.attempts[{attempt_index}]"
        attempt = _require_exact_dict(
            raw_attempt,
            attempt_field,
            frozenset({"attempt_id", "attempt_index", "status", "retryable"}),
        )
        attempt_id = _identifier(attempt["attempt_id"], f"{attempt_field}.attempt_id")
        if attempt_id in attempt_ids:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                f"{field}.attempts",
                "attempt ids must be unique",
            )
        attempt_ids.add(attempt_id)
        terminal_status = attempt["status"]
        if terminal_status not in ("succeeded", "failed"):
            raise _error(
                ArtifactCode.INVALID_INPUT,
                f"{attempt_field}.status",
                "is invalid",
            )
        retryable = attempt["retryable"]
        if type(retryable) is not bool:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                f"{attempt_field}.retryable",
                "must be an exact bool",
            )
        attempts.append(
            {
                "attempt_id": attempt_id,
                "attempt_index": _require_int(
                    attempt["attempt_index"],
                    f"{attempt_field}.attempt_index",
                    minimum=attempt_index,
                    maximum=attempt_index,
                ),
                "retryable": retryable,
                "status": terminal_status,
            }
        )

    retry_count = _require_int(
        obj["retry_count"],
        f"{field}.retry_count",
        minimum=0,
        maximum=15,
    )
    if retry_count != len(attempts) - 1:
        raise _error(
            ArtifactCode.INVALID_INPUT,
            f"{field}.retry_count",
            "does not match the attempt count",
        )
    usage_obj = _require_exact_dict(
        obj["usage"],
        f"{field}.usage",
        frozenset(
            {
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            }
        ),
    )
    usage = {
        name: _require_optional_int(
            usage_obj[name],
            f"{field}.usage.{name}",
            minimum=0,
            maximum=1_000_000_000,
        )
        for name in (
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "input_tokens",
            "output_tokens",
        )
    }
    return {
        "attempts": attempts,
        "call_index": _require_int(
            obj["call_index"],
            f"{field}.call_index",
            minimum=0,
            maximum=1_000_000,
        ),
        "elapsed_microseconds": _require_optional_int(
            obj["elapsed_microseconds"],
            f"{field}.elapsed_microseconds",
            minimum=0,
            maximum=24 * 60 * 60 * 1_000_000,
        ),
        "failure_code": failure,
        "logical_call_id": _identifier(
            obj["logical_call_id"],
            f"{field}.logical_call_id",
        ),
        "retry_count": retry_count,
        "returned_model_id": _model_id(
            obj["returned_model_id"],
            f"{field}.returned_model_id",
            optional=True,
        ),
        "status": status,
        "usage": usage,
    }


def sanitized_observations_from_dict(value: object) -> SanitizedObservations:
    """Parse the public observation artifact with exact fields and bounded values."""

    obj = _require_exact_dict(
        value,
        "observations",
        frozenset({"schema", "run_id", "calls"}),
    )
    if obj["schema"] != BENCHMARK_OBSERVATIONS_VERSION:
        raise _error(
            ArtifactCode.INVALID_INPUT,
            "observations.schema",
            "has the wrong version",
        )
    raw_calls = _require_exact_list(
        obj["calls"],
        "observations.calls",
        maximum=MAX_ARTIFACT_CALLS,
    )
    calls: list[bytes] = []
    logical_call_ids: set[str] = set()
    attempt_ids: set[str] = set()
    for index, raw_call in enumerate(raw_calls):
        call = _sanitized_call_from_dict(raw_call, index)
        logical_call_id = cast(str, call["logical_call_id"])
        if logical_call_id in logical_call_ids:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "observations.calls",
                "logical call ids must be unique",
            )
        logical_call_ids.add(logical_call_id)
        for attempt in cast(list[dict[str, object]], call["attempts"]):
            attempt_id = cast(str, attempt["attempt_id"])
            if attempt_id in attempt_ids:
                raise _error(
                    ArtifactCode.INVALID_INPUT,
                    "observations.calls",
                    "attempt ids must be globally unique",
                )
            attempt_ids.add(attempt_id)
        calls.append(canonical_json_bytes(call))
    return SanitizedObservations(
        _identifier(obj["run_id"], "observations.run_id"),
        tuple(calls),
    )


@dataclass(frozen=True, slots=True)
class FinalizedReport:
    """Report bytes returned by a report layer without importing it here."""

    json_bytes: bytes
    markdown_bytes: bytes

    def __post_init__(self) -> None:
        if type(self.json_bytes) is not bytes:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "report.json_bytes",
                "must be exact bytes",
            )
        json_value = parse_canonical_json_bytes(
            self.json_bytes,
            max_bytes=MAX_ARTIFACT_REPORT_JSON_BYTES,
        )
        if type(json_value) is not dict:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "report.json_bytes",
                "must encode one canonical object",
            )
        if type(self.markdown_bytes) is not bytes:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "report.markdown_bytes",
                "must be exact bytes",
            )
        if not self.markdown_bytes or len(self.markdown_bytes) > MAX_ARTIFACT_REPORT_MARKDOWN_BYTES:
            raise _error(
                ArtifactCode.LIMIT_EXCEEDED,
                "report.markdown_bytes",
                "is empty or exceeds the report limit",
            )
        try:
            markdown = self.markdown_bytes.decode("utf-8")
        except UnicodeDecodeError:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "report.markdown_bytes",
                "must be strict UTF-8",
            ) from None
        if "\r" in markdown or not markdown.endswith("\n"):
            raise _error(
                ArtifactCode.NON_CANONICAL,
                "report.markdown_bytes",
                "must use LF line endings and end with LF",
            )
        if unicodedata.normalize("NFC", markdown) != markdown:
            raise _error(
                ArtifactCode.NON_CANONICAL,
                "report.markdown_bytes",
                "must be NFC-normalized",
            )
        report_digest = canonical_sha256(BENCHMARK_REPORT_VERSION, json_value)
        digest_line = f"- Report digest: `{report_digest}`"
        if digest_line not in markdown.splitlines():
            raise _error(
                ArtifactCode.HASH_MISMATCH,
                "report.markdown_bytes",
                "does not bind the canonical report JSON",
            )


def _publication_table_bytes(
    manifest: BenchmarkManifest,
    rows: tuple[BenchmarkRow, ...],
    blobs: tuple[BlobRecord, ...],
) -> tuple[bytes, bytes]:
    rows_bytes = canonical_jsonl_bytes(
        tuple(row_to_dict(row) for row in rows),
        max_bytes=manifest.limits.max_json_bytes,
        max_lines=manifest.limits.max_rows,
        max_line_bytes=manifest.limits.max_jsonl_line_bytes,
    )
    blobs_bytes = canonical_jsonl_bytes(
        tuple(blob_record_to_dict(blob) for blob in blobs),
        max_bytes=manifest.limits.max_json_bytes,
        max_lines=manifest.limits.max_blobs,
        max_line_bytes=manifest.limits.max_jsonl_line_bytes,
    )
    return rows_bytes, blobs_bytes


def _validate_publication_components(
    manifest: BenchmarkManifest,
    receipt: BenchmarkReceipt,
    rows: tuple[BenchmarkRow, ...],
    blobs: tuple[BlobRecord, ...],
    observations: SanitizedObservations,
) -> None:
    if (
        type(manifest) is not BenchmarkManifest
        or type(receipt) is not BenchmarkReceipt
        or type(rows) is not tuple
        or any(type(row) is not BenchmarkRow for row in rows)
        or type(blobs) is not tuple
        or any(type(blob) is not BlobRecord for blob in blobs)
        or type(observations) is not SanitizedObservations
    ):
        raise _error(
            ArtifactCode.INVALID_INPUT,
            "publication",
            "requires exact artifact values",
        )
    require_complete_receipt(receipt)
    if (
        receipt.run_id != manifest.run_id
        or receipt.config_sha256 != manifest_sha256(manifest)
        or receipt.corpus_sha256 != manifest.corpus_sha256
        or receipt.analysis_code_sha256 != manifest.analysis_code_sha256
    ):
        raise _error(
            ArtifactCode.HASH_MISMATCH,
            "publication.receipt",
            "does not bind the manifest",
        )
    if observations.run_id != manifest.run_id or any(row.run_id != manifest.run_id for row in rows):
        raise _error(
            ArtifactCode.COVERAGE_MISMATCH,
            "publication.run_id",
            "artifacts do not share the manifest run id",
        )
    if rows != tuple(sorted(rows, key=lambda row: row.sort_key)) or len(
        {row.key for row in rows}
    ) != len(rows):
        raise _error(
            ArtifactCode.NON_CANONICAL,
            "publication.rows",
            "rows must be uniquely sorted",
        )
    if blobs != tuple(sorted(blobs, key=lambda blob: blob.ref.sort_key)) or len(
        {blob.ref for blob in blobs}
    ) != len(blobs):
        raise _error(
            ArtifactCode.NON_CANONICAL,
            "publication.blobs",
            "blobs must be uniquely sorted",
        )
    row_keys = tuple(row.key for row in rows)
    if (
        row_keys != manifest.expected_rows
        or receipt.expected_rows != len(manifest.expected_rows)
        or receipt.observed_rows != len(rows)
        or receipt.maximum_calls != manifest.limits.max_calls
    ):
        raise _error(
            ArtifactCode.COVERAGE_MISMATCH,
            "publication.rows",
            "row keys or declared counts do not match the manifest",
        )
    referenced_blobs = {ref for row in rows for ref in row.blob_refs}
    if referenced_blobs != {blob.ref for blob in blobs}:
        raise _error(
            ArtifactCode.COVERAGE_MISMATCH,
            "publication.blobs",
            "blob records must exactly cover row references",
        )
    strict_observations = sanitized_observations_from_dict(observations.to_dict())
    if strict_observations != observations:
        raise _error(
            ArtifactCode.NON_CANONICAL,
            "publication.observations",
            "observation values are not canonical",
        )
    observation_wire = tuple(
        cast(dict[str, object], parse_canonical_json_bytes(call))
        for call in observations.calls_json
    )
    observation_keys = {
        ObservationKey(
            cast(str, call["logical_call_id"]),
            cast(int, call["call_index"]),
        )
        for call in observation_wire
    }
    row_observation_keys = [key for row in rows for key in row.observation_keys]
    if (
        len(set(row_observation_keys)) != len(row_observation_keys)
        or set(row_observation_keys) != observation_keys
        or receipt.observed_calls != len(observation_wire)
    ):
        raise _error(
            ArtifactCode.COVERAGE_MISMATCH,
            "publication.observations",
            "observations must be owned exactly once by the rows",
        )
    returned_models = tuple(
        sorted(
            {
                cast(str, call["returned_model_id"])
                for call in observation_wire
                if call["returned_model_id"] is not None
            }
        )
    )
    if receipt.observed_returned_models != returned_models:
        raise _error(
            ArtifactCode.COVERAGE_MISMATCH,
            "publication.returned_models",
            "receipt models do not match observations",
        )
    rows_bytes, blobs_bytes = _publication_table_bytes(manifest, rows, blobs)
    if (
        receipt.rows_sha256 != canonical_table_sha256("rows", rows_bytes)
        or receipt.blobs_sha256 != canonical_table_sha256("blobs", blobs_bytes)
        or receipt.observations_sha256 != observations.sha256
    ):
        raise _error(
            ArtifactCode.HASH_MISMATCH,
            "publication.hashes",
            "receipt hashes do not match finalized artifacts",
        )


def _validate_report_publication(
    report: FinalizedReport,
    manifest: BenchmarkManifest,
    receipt: BenchmarkReceipt,
    rows: tuple[BenchmarkRow, ...],
    blobs: tuple[BlobRecord, ...],
    observations: SanitizedObservations,
) -> None:
    report_value = parse_canonical_json_bytes(
        report.json_bytes,
        max_bytes=MAX_ARTIFACT_REPORT_JSON_BYTES,
    )
    if type(report_value) is not dict:
        raise _error(
            ArtifactCode.INVALID_INPUT,
            "report.json_bytes",
            "must encode one canonical object",
        )
    report_object = cast(dict[str, object], report_value)
    if (
        report_object.get("schema") != BENCHMARK_REPORT_VERSION
        or report_object.get("run_id") != manifest.run_id
    ):
        raise _error(
            ArtifactCode.COVERAGE_MISMATCH,
            "report",
            "schema or run id does not match the publication",
        )
    raw_bindings = report_object.get("input_bindings")
    if type(raw_bindings) is not dict:
        raise _error(
            ArtifactCode.INVALID_INPUT,
            "report.input_bindings",
            "must be an exact object",
        )
    bindings = cast(dict[str, object], raw_bindings)
    expected: dict[str, object] = {
        "run_id": receipt.run_id,
        "manifest_sha256": manifest_sha256(manifest),
        "config_sha256": receipt.config_sha256,
        "receipt_sha256": receipt_sha256(receipt),
        "receipt_status": receipt.status.value,
        "corpus_sha256": receipt.corpus_sha256,
        "analysis_code_sha256": receipt.analysis_code_sha256,
        "journal_sha256": receipt.journal_sha256,
        "rows_sha256": receipt.rows_sha256,
        "blobs_sha256": receipt.blobs_sha256,
        "observations_sha256": receipt.observations_sha256,
        "expected_rows": receipt.expected_rows,
        "observed_rows": receipt.observed_rows,
        "maximum_calls": receipt.maximum_calls,
        "observed_calls": receipt.observed_calls,
        "row_count": len(rows),
        "blob_count": len(blobs),
        "logical_call_count": len(observations.calls_json),
    }
    if any(bindings.get(name) != value for name, value in expected.items()):
        raise _error(
            ArtifactCode.HASH_MISMATCH,
            "report.input_bindings",
            "does not match the finalized publication inputs",
        )


@dataclass(frozen=True, slots=True)
class FinalizationInputs:
    """Immutable finalized evidence passed to a report callback before publication."""

    manifest: BenchmarkManifest
    receipt: BenchmarkReceipt
    rows: tuple[BenchmarkRow, ...]
    blobs: tuple[BlobRecord, ...]
    observations: SanitizedObservations

    def __post_init__(self) -> None:
        _validate_publication_components(
            self.manifest,
            self.receipt,
            self.rows,
            self.blobs,
            self.observations,
        )


@dataclass(frozen=True, slots=True)
class ReplayBundle:
    """Strict, complete public inputs accepted by full or fast replay."""

    manifest: BenchmarkManifest
    receipt: BenchmarkReceipt
    rows: tuple[BenchmarkRow, ...]
    blobs: tuple[BlobRecord, ...]
    observations: SanitizedObservations

    def __post_init__(self) -> None:
        _validate_publication_components(
            self.manifest,
            self.receipt,
            self.rows,
            self.blobs,
            self.observations,
        )


def load_replay_bundle(
    config: Path,
    receipt: Path,
    rows: Path,
    blobs: Path,
    observations: Path,
) -> ReplayBundle:
    """Load and cross-bind the five public replay inputs without external checks."""

    for field, path in (
        ("config", config),
        ("receipt", receipt),
        ("rows", rows),
        ("blobs", blobs),
        ("observations", observations),
    ):
        if not isinstance(path, Path):
            raise _error(
                ArtifactCode.INVALID_INPUT,
                field,
                "must be a Path",
            )

    config_bytes = _read_regular_bytes(config, max_bytes=MAX_BENCHMARK_JSON_BYTES)
    manifest = manifest_from_dict(
        parse_canonical_json_bytes(config_bytes, max_bytes=MAX_BENCHMARK_JSON_BYTES)
    )
    receipt_bytes = _read_regular_bytes(receipt, max_bytes=MAX_BENCHMARK_JSON_BYTES)
    complete_receipt = receipt_from_dict(
        parse_canonical_json_bytes(receipt_bytes, max_bytes=MAX_BENCHMARK_JSON_BYTES)
    )
    rows_bytes = _read_regular_bytes(rows, max_bytes=manifest.limits.max_json_bytes)
    raw_rows = parse_canonical_jsonl_bytes(
        rows_bytes,
        max_bytes=manifest.limits.max_json_bytes,
        max_lines=manifest.limits.max_rows,
        max_line_bytes=manifest.limits.max_jsonl_line_bytes,
    )
    blobs_bytes = _read_regular_bytes(blobs, max_bytes=manifest.limits.max_json_bytes)
    raw_blobs = parse_canonical_jsonl_bytes(
        blobs_bytes,
        max_bytes=manifest.limits.max_json_bytes,
        max_lines=manifest.limits.max_blobs,
        max_line_bytes=manifest.limits.max_jsonl_line_bytes,
    )
    observations_value = parse_canonical_json_bytes(
        _read_regular_bytes(observations, max_bytes=manifest.limits.max_json_bytes),
        max_bytes=manifest.limits.max_json_bytes,
    )
    bundle = ReplayBundle(
        manifest,
        complete_receipt,
        tuple(row_from_dict(value) for value in raw_rows),
        tuple(blob_record_from_dict(value) for value in raw_blobs),
        sanitized_observations_from_dict(observations_value),
    )
    # Compare against the exact bytes supplied, rather than only their typed
    # round-trips, so the receipt remains the binding authority for replay.
    if complete_receipt.rows_sha256 != canonical_table_sha256(
        "rows", rows_bytes
    ) or complete_receipt.blobs_sha256 != canonical_table_sha256("blobs", blobs_bytes):
        raise _error(
            ArtifactCode.HASH_MISMATCH,
            "replay.tables",
            "receipt hashes do not match the supplied table bytes",
        )
    return bundle


def _joined_observations(
    source: _ObservationSource,
) -> tuple[
    tuple[CallIntent, CallResult, tuple[AttemptIntent, ...], tuple[AttemptResult, ...]], ...
]:
    intents = source.intents
    results = source.results
    attempt_intents = source.attempt_intents
    attempt_results = source.attempt_results
    if (
        type(intents) is not tuple
        or type(results) is not tuple
        or type(attempt_intents) is not tuple
        or type(attempt_results) is not tuple
        or any(type(value) is not CallIntent for value in intents)
        or any(type(value) is not CallResult for value in results)
        or any(type(value) is not AttemptIntent for value in attempt_intents)
        or any(type(value) is not AttemptResult for value in attempt_results)
    ):
        raise _error(ArtifactCode.INVALID_INPUT, "observations", "source tuple types are invalid")
    if len(intents) != len(results) or len(attempt_intents) != len(attempt_results):
        raise _error(ArtifactCode.INCOMPLETE, "observations", "contains unmatched events")
    results_by_key = {(result.call_index, result.logical_call_id): result for result in results}
    if len(results_by_key) != len(results):
        raise _error(ArtifactCode.INVALID_INPUT, "observations.results", "contains duplicates")
    attempts_by_key: dict[tuple[int, str], list[AttemptIntent]] = {}
    terminals_by_key: dict[tuple[int, str], list[AttemptResult]] = {}
    for attempt in attempt_intents:
        attempts_by_key.setdefault((attempt.call_index, attempt.logical_call_id), []).append(
            attempt
        )
    for terminal in attempt_results:
        terminals_by_key.setdefault((terminal.call_index, terminal.logical_call_id), []).append(
            terminal
        )
    joined: list[
        tuple[CallIntent, CallResult, tuple[AttemptIntent, ...], tuple[AttemptResult, ...]]
    ] = []
    seen_attempt_ids: set[str] = set()
    for position, intent in enumerate(intents):
        key = (intent.call_index, intent.logical_call_id)
        if intent.call_index != position:
            raise _error(
                ArtifactCode.INVALID_INPUT, "observations.intents", "indices are noncanonical"
            )
        result = results_by_key.pop(key, None)
        calls = sorted(attempts_by_key.pop(key, []), key=lambda value: value.attempt_index)
        call_results = sorted(terminals_by_key.pop(key, []), key=lambda value: value.attempt_index)
        if result is None or not calls or len(calls) != len(call_results):
            raise _error(ArtifactCode.INCOMPLETE, "observations", "call journal is incomplete")
        for attempt_index, (attempt, terminal) in enumerate(zip(calls, call_results, strict=True)):
            if (
                attempt.attempt_index != attempt_index
                or terminal.attempt_index != attempt_index
                or attempt.attempt_id != terminal.attempt_id
                or attempt.attempt_id in seen_attempt_ids
            ):
                raise _error(ArtifactCode.INVALID_INPUT, "observations.attempts", "do not join")
            seen_attempt_ids.add(attempt.attempt_id)
        joined.append((intent, result, tuple(calls), tuple(call_results)))
    if results_by_key or attempts_by_key or terminals_by_key:
        raise _error(ArtifactCode.INVALID_INPUT, "observations", "contains orphan records")
    return tuple(joined)


def sanitize_observations(
    run_id: str,
    source: _ObservationSource,
    *,
    stub: bool,
) -> tuple[PrivateObservations, SanitizedObservations]:
    """Create private and replay-safe call views from one complete ledger."""

    exact_run = _identifier(run_id, "observations.run_id")
    if type(stub) is not bool:
        raise _error(ArtifactCode.INVALID_INPUT, "observations.stub", "must be exact bool")
    private_calls: list[bytes] = []
    public_calls: list[bytes] = []
    for intent, result, attempts, terminals in _joined_observations(source):
        if intent.run_id != exact_run:
            raise _error(ArtifactCode.INVALID_INPUT, "observations.run_id", "does not match calls")
        provider = result.provider
        attempt_wire = [
            {
                "attempt_id": attempt.attempt_id,
                "attempt_index": attempt.attempt_index,
                "retryable": terminal.retryable,
                "status": terminal.status,
            }
            for attempt, terminal in zip(attempts, terminals, strict=True)
        ]
        usage = {
            "cache_creation_input_tokens": (None if stub else provider.cache_creation_input_tokens),
            "cache_read_input_tokens": None if stub else provider.cache_read_input_tokens,
            "input_tokens": None if stub else provider.input_tokens,
            "output_tokens": None if stub else provider.output_tokens,
        }
        common: dict[str, object] = {
            "attempts": attempt_wire,
            "call_index": intent.call_index,
            "elapsed_microseconds": None if stub else result.elapsed_microseconds,
            "failure_code": None if result.failure_code is None else result.failure_code.value,
            "logical_call_id": intent.logical_call_id,
            "retry_count": len(attempts) - 1,
            "returned_model_id": provider.returned_model_id,
            "status": result.status,
            "usage": usage,
        }
        private = {
            **common,
            "item_id": intent.item_id,
            "max_tokens": intent.max_tokens,
            "pair_id": intent.pair_id,
            "reply_sha256": result.reply_sha256,
            "request_sha256": intent.request_sha256,
            "requested_model_id": intent.requested_model_id,
            "response_id_sha256": None if stub else provider.response_id_sha256,
            "stage": intent.stage.value,
            "stage_ordinal": intent.stage_ordinal,
            "system_sha256": intent.system_sha256,
            "temperature": intent.temperature,
            "user_sha256": intent.user_sha256,
        }
        private_calls.append(canonical_json_bytes(private))
        public_calls.append(canonical_json_bytes(common))
    return (
        PrivateObservations(exact_run, tuple(private_calls)),
        SanitizedObservations(exact_run, tuple(public_calls)),
    )


def _provider_to_dict(value: ProviderObservation) -> dict[str, object]:
    return {
        "attempts": value.attempts,
        "available": value.available,
        "cache_creation_input_tokens": value.cache_creation_input_tokens,
        "cache_read_input_tokens": value.cache_read_input_tokens,
        "input_tokens": value.input_tokens,
        "output_tokens": value.output_tokens,
        "response_id_sha256": value.response_id_sha256,
        "retries": value.retries,
        "returned_model_id": value.returned_model_id,
        "status": value.status,
    }


def _provider_from_dict(value: object) -> ProviderObservation:
    obj = _require_exact_dict(
        value,
        "wal.provider",
        frozenset(
            {
                "available",
                "status",
                "attempts",
                "retries",
                "returned_model_id",
                "response_id_sha256",
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            }
        ),
    )
    if type(obj["available"]) is not bool:
        raise _error(ArtifactCode.INVALID_INPUT, "wal.provider.available", "must be bool")
    status = obj["status"]
    if status is not None and status not in ("succeeded", "failed"):
        raise _error(ArtifactCode.INVALID_INPUT, "wal.provider.status", "is invalid")
    return ProviderObservation(
        obj["available"],
        cast(Any, status),
        _require_optional_int(obj["attempts"], "wal.provider.attempts", minimum=1, maximum=16),
        _require_optional_int(obj["retries"], "wal.provider.retries", minimum=0, maximum=15),
        _model_id(obj["returned_model_id"], "wal.provider.returned_model_id", optional=True),
        None
        if obj["response_id_sha256"] is None
        else _sha256(obj["response_id_sha256"], "wal.provider.response_id_sha256"),
        _require_optional_int(
            obj["input_tokens"], "wal.provider.input_tokens", minimum=0, maximum=1_000_000_000
        ),
        _require_optional_int(
            obj["output_tokens"], "wal.provider.output_tokens", minimum=0, maximum=1_000_000_000
        ),
        _require_optional_int(
            obj["cache_creation_input_tokens"],
            "wal.provider.cache_creation_input_tokens",
            minimum=0,
            maximum=1_000_000_000,
        ),
        _require_optional_int(
            obj["cache_read_input_tokens"],
            "wal.provider.cache_read_input_tokens",
            minimum=0,
            maximum=1_000_000_000,
        ),
    )


def _intent_to_dict(value: CallIntent) -> dict[str, object]:
    return {
        "call_index": value.call_index,
        "candidate_index": value.candidate_index,
        "cluster_id": value.cluster_id,
        "family_id": value.family_id,
        "item_id": value.item_id,
        "logical_call_id": value.logical_call_id,
        "max_tokens": value.max_tokens,
        "pair_id": value.pair_id,
        "request_sha256": value.request_sha256,
        "requested_model_id": value.requested_model_id,
        "run_id": value.run_id,
        "sample_index": value.sample_index,
        "stage": value.stage.value,
        "stage_ordinal": value.stage_ordinal,
        "system_sha256": value.system_sha256,
        "temperature": value.temperature,
        "user_sha256": value.user_sha256,
    }


def _intent_from_dict(value: object) -> CallIntent:
    keys = frozenset(
        {
            "run_id",
            "logical_call_id",
            "call_index",
            "item_id",
            "family_id",
            "cluster_id",
            "pair_id",
            "sample_index",
            "candidate_index",
            "stage",
            "stage_ordinal",
            "requested_model_id",
            "system_sha256",
            "user_sha256",
            "request_sha256",
            "max_tokens",
            "temperature",
        }
    )
    obj = _require_exact_dict(value, "wal.call_intent", keys)
    raw_stage = obj["stage"]
    if type(raw_stage) is not str:
        raise _error(ArtifactCode.INVALID_INPUT, "wal.call_intent.stage", "is invalid")
    try:
        stage = CallStage(raw_stage)
    except (TypeError, ValueError):
        raise _error(ArtifactCode.INVALID_INPUT, "wal.call_intent.stage", "is invalid") from None
    temperature = obj["temperature"]
    if type(temperature) is not float:
        raise _error(
            ArtifactCode.INVALID_INPUT,
            "wal.call_intent.temperature",
            "must be exact float",
        )
    return CallIntent(
        _identifier(obj["run_id"], "wal.call_intent.run_id"),
        _identifier(obj["logical_call_id"], "wal.call_intent.logical_call_id"),
        _require_int(obj["call_index"], "wal.call_intent.call_index", minimum=0, maximum=1_000_000),
        _identifier(obj["item_id"], "wal.call_intent.item_id"),
        _identifier(obj["family_id"], "wal.call_intent.family_id"),
        _identifier(obj["cluster_id"], "wal.call_intent.cluster_id"),
        _identifier(obj["pair_id"], "wal.call_intent.pair_id"),
        _require_int(
            obj["sample_index"], "wal.call_intent.sample_index", minimum=0, maximum=1_000_000
        ),
        _require_int(
            obj["candidate_index"],
            "wal.call_intent.candidate_index",
            minimum=0,
            maximum=1_000_000,
        ),
        stage,
        _require_int(
            obj["stage_ordinal"], "wal.call_intent.stage_ordinal", minimum=0, maximum=1_000_000
        ),
        cast(str, _model_id(obj["requested_model_id"], "wal.requested_model_id", optional=False)),
        _sha256(obj["system_sha256"], "wal.call_intent.system_sha256"),
        _sha256(obj["user_sha256"], "wal.call_intent.user_sha256"),
        _sha256(obj["request_sha256"], "wal.call_intent.request_sha256"),
        _require_int(obj["max_tokens"], "wal.call_intent.max_tokens", minimum=1, maximum=16_384),
        temperature,
    )


def _attempt_intent_to_dict(value: AttemptIntent) -> dict[str, object]:
    return {
        "attempt_id": value.attempt_id,
        "attempt_index": value.attempt_index,
        "call_index": value.call_index,
        "logical_call_id": value.logical_call_id,
        "request_sha256": value.request_sha256,
        "reserved_output_tokens": value.reserved_output_tokens,
        "run_id": value.run_id,
    }


def _attempt_intent_from_dict(value: object) -> AttemptIntent:
    obj = _require_exact_dict(
        value,
        "wal.attempt_intent",
        frozenset(
            {
                "run_id",
                "logical_call_id",
                "call_index",
                "attempt_id",
                "attempt_index",
                "request_sha256",
                "reserved_output_tokens",
            }
        ),
    )
    return AttemptIntent(
        _identifier(obj["run_id"], "wal.attempt_intent.run_id"),
        _identifier(obj["logical_call_id"], "wal.attempt_intent.logical_call_id"),
        _require_int(
            obj["call_index"], "wal.attempt_intent.call_index", minimum=0, maximum=1_000_000
        ),
        _identifier(obj["attempt_id"], "wal.attempt_intent.attempt_id"),
        _require_int(
            obj["attempt_index"], "wal.attempt_intent.attempt_index", minimum=0, maximum=15
        ),
        _sha256(obj["request_sha256"], "wal.attempt_intent.request_sha256"),
        _require_int(
            obj["reserved_output_tokens"],
            "wal.attempt_intent.reserved_output_tokens",
            minimum=1,
            maximum=16_384,
        ),
    )


def _attempt_result_to_dict(value: AttemptResult) -> dict[str, object]:
    return {
        "attempt_id": value.attempt_id,
        "attempt_index": value.attempt_index,
        "call_index": value.call_index,
        "logical_call_id": value.logical_call_id,
        "retryable": value.retryable,
        "run_id": value.run_id,
        "status": value.status,
    }


def _attempt_result_from_dict(value: object) -> AttemptResult:
    obj = _require_exact_dict(
        value,
        "wal.attempt_result",
        frozenset(
            {
                "run_id",
                "logical_call_id",
                "call_index",
                "attempt_id",
                "attempt_index",
                "status",
                "retryable",
            }
        ),
    )
    status_value = obj["status"]
    if status_value not in ("succeeded", "failed"):
        raise _error(ArtifactCode.INVALID_INPUT, "wal.attempt_result.status", "is invalid")
    if type(obj["retryable"]) is not bool:
        raise _error(ArtifactCode.INVALID_INPUT, "wal.attempt_result.retryable", "must be bool")
    return AttemptResult(
        _identifier(obj["run_id"], "wal.attempt_result.run_id"),
        _identifier(obj["logical_call_id"], "wal.attempt_result.logical_call_id"),
        _require_int(
            obj["call_index"], "wal.attempt_result.call_index", minimum=0, maximum=1_000_000
        ),
        _identifier(obj["attempt_id"], "wal.attempt_result.attempt_id"),
        _require_int(
            obj["attempt_index"], "wal.attempt_result.attempt_index", minimum=0, maximum=15
        ),
        cast(Any, status_value),
        obj["retryable"],
    )


def _call_result_to_dict(value: CallResult) -> dict[str, object]:
    return {
        "call_index": value.call_index,
        "elapsed_microseconds": value.elapsed_microseconds,
        "failure_code": None if value.failure_code is None else value.failure_code.value,
        "logical_call_id": value.logical_call_id,
        "provider": _provider_to_dict(value.provider),
        "reply_sha256": value.reply_sha256,
        "status": value.status,
    }


def _call_result_from_dict(value: object) -> CallResult:
    obj = _require_exact_dict(
        value,
        "wal.call_result",
        frozenset(
            {
                "logical_call_id",
                "call_index",
                "status",
                "reply_sha256",
                "elapsed_microseconds",
                "failure_code",
                "provider",
            }
        ),
    )
    status_value = obj["status"]
    if status_value not in ("succeeded", "failed"):
        raise _error(ArtifactCode.INVALID_INPUT, "wal.call_result.status", "is invalid")
    reply = obj["reply_sha256"]
    if reply is not None:
        reply = _sha256(reply, "wal.call_result.reply_sha256")
    failure_raw = obj["failure_code"]
    failure: CallFailureCode | None
    if failure_raw is None:
        failure = None
    else:
        if type(failure_raw) is not str:
            raise _error(ArtifactCode.INVALID_INPUT, "wal.call_result.failure_code", "is invalid")
        try:
            failure = CallFailureCode(failure_raw)
        except (TypeError, ValueError):
            raise _error(
                ArtifactCode.INVALID_INPUT, "wal.call_result.failure_code", "is invalid"
            ) from None
    reply_value = reply
    return CallResult(
        _identifier(obj["logical_call_id"], "wal.call_result.logical_call_id"),
        _require_int(obj["call_index"], "wal.call_result.call_index", minimum=0, maximum=1_000_000),
        cast(Any, status_value),
        reply_value,
        _require_int(
            obj["elapsed_microseconds"],
            "wal.call_result.elapsed_microseconds",
            minimum=0,
            maximum=24 * 60 * 60 * 1_000_000,
        ),
        failure,
        _provider_from_dict(obj["provider"]),
    )


_EVENT_TYPES: Final = frozenset({"CALL_INTENT", "ATTEMPT_INTENT", "ATTEMPT_RESULT", "CALL_RESULT"})


def wal_event_sha256(value: object) -> str:
    try:
        encoded = canonical_json_bytes(value)
    except BenchmarkContractError as error:
        raise _error(ArtifactCode.INVALID_INPUT, "wal.event", error.detail) from None
    return hashlib.sha256(_WAL_DOMAIN + encoded).hexdigest()


def _event_payload(
    event: CallIntent | AttemptIntent | AttemptResult | CallResult,
) -> tuple[str, dict[str, object]]:
    if type(event) is CallIntent:
        return ("CALL_INTENT", _intent_to_dict(event))
    if type(event) is AttemptIntent:
        return ("ATTEMPT_INTENT", _attempt_intent_to_dict(event))
    if type(event) is AttemptResult:
        return ("ATTEMPT_RESULT", _attempt_result_to_dict(event))
    if type(event) is CallResult:
        return ("CALL_RESULT", _call_result_to_dict(event))
    raise _error(ArtifactCode.INVALID_INPUT, "wal.event", "event type is unsupported")


def _event_from_wire(value: object, *, sequence: int, previous: str) -> tuple[object, object]:
    obj = _require_exact_dict(
        value,
        "wal.event",
        frozenset({"version", "sequence", "previous_event_sha256", "event_type", "payload"}),
    )
    if obj["version"] != BENCHMARK_WAL_VERSION:
        raise _error(ArtifactCode.CORRUPT_JOURNAL, "wal.version", "has the wrong version")
    if obj["sequence"] != sequence or obj["previous_event_sha256"] != previous:
        raise _error(ArtifactCode.CORRUPT_JOURNAL, "wal.chain", "sequence/hash chain is broken")
    event_type = obj["event_type"]
    if type(event_type) is not str or event_type not in _EVENT_TYPES:
        raise _error(ArtifactCode.CORRUPT_JOURNAL, "wal.event_type", "is unsupported")
    constructors = {
        "CALL_INTENT": _intent_from_dict,
        "ATTEMPT_INTENT": _attempt_intent_from_dict,
        "ATTEMPT_RESULT": _attempt_result_from_dict,
        "CALL_RESULT": _call_result_from_dict,
    }
    try:
        event = constructors[event_type](obj["payload"])
    except (ArtifactError, ValueError) as error:
        raise _error(
            ArtifactCode.CORRUPT_JOURNAL, "wal.payload", "event payload is invalid"
        ) from error
    return obj, event


def _open_regular_append(path: Path) -> int:
    flags = (
        os.O_WRONLY
        | os.O_APPEND
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise _error(ArtifactCode.NOT_REGULAR_FILE, "journal", "cannot open journal") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise _error(ArtifactCode.NOT_REGULAR_FILE, "journal", "journal is not regular")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class DurableObservationSink(InMemoryObservationSink):
    """In-memory pairing plus an fsynced canonical hash-chained WAL."""

    def __init__(
        self,
        journal_path: Path,
        *,
        max_calls: int,
        max_attempts: int = MAX_ARTIFACT_ATTEMPTS,
        max_requested_output_tokens: int = MAX_ARTIFACT_BUDGET,
        max_attempt_reserved_output_tokens: int = MAX_ARTIFACT_BUDGET,
        max_response_text_bytes: int = MAX_ARTIFACT_BUDGET,
        max_transport_response_bytes: int = MAX_ARTIFACT_BUDGET,
        max_wall_microseconds: int = MAX_ARTIFACT_BUDGET,
        complete_unit_reservation: CompleteUnitReservation | None = None,
        allowed_returned_model_id: str | None = None,
        require_successful_provider_evidence: bool = False,
        billable_token_ceiling_per_attempt: dict[str, int] | None = None,
        resume: bool = False,
    ) -> None:
        super().__init__(max_calls=max_calls)
        if not isinstance(journal_path, Path):
            raise _error(ArtifactCode.INVALID_INPUT, "journal_path", "must be an exact Path")
        if type(resume) is not bool:
            raise _error(ArtifactCode.INVALID_INPUT, "resume", "must be an exact bool")
        self._max_attempts = _require_int(
            max_attempts,
            "max_attempts",
            minimum=max_calls,
            maximum=MAX_ARTIFACT_ATTEMPTS,
        )
        self._max_requested_output_tokens = _require_int(
            max_requested_output_tokens,
            "max_requested_output_tokens",
            minimum=1,
            maximum=MAX_ARTIFACT_BUDGET,
        )
        self._max_attempt_reserved_output_tokens = _require_int(
            max_attempt_reserved_output_tokens,
            "max_attempt_reserved_output_tokens",
            minimum=1,
            maximum=MAX_ARTIFACT_BUDGET,
        )
        self._max_response_text_bytes = _require_int(
            max_response_text_bytes,
            "max_response_text_bytes",
            minimum=1,
            maximum=MAX_ARTIFACT_BUDGET,
        )
        self._max_transport_response_bytes = _require_int(
            max_transport_response_bytes,
            "max_transport_response_bytes",
            minimum=1,
            maximum=MAX_ARTIFACT_BUDGET,
        )
        self._max_wall_microseconds = _require_int(
            max_wall_microseconds,
            "max_wall_microseconds",
            minimum=1,
            maximum=MAX_ARTIFACT_BUDGET,
        )
        if complete_unit_reservation is not None and type(
            complete_unit_reservation
        ) is not CompleteUnitReservation:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "complete_unit_reservation",
                "must be null or an exact CompleteUnitReservation",
            )
        self._complete_unit_reservation = complete_unit_reservation
        self._next_unit_reservation = complete_unit_reservation
        self._allowed_returned_model_id = _model_id(
            allowed_returned_model_id,
            "allowed_returned_model_id",
            optional=True,
        )
        if type(require_successful_provider_evidence) is not bool:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "require_successful_provider_evidence",
                "must be an exact bool",
            )
        if require_successful_provider_evidence and self._allowed_returned_model_id is None:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "allowed_returned_model_id",
                "is required when successful provider evidence is required",
            )
        self._require_successful_provider_evidence = require_successful_provider_evidence
        self._billable_token_ceiling_per_attempt = _provider_usage_ceilings(
            billable_token_ceiling_per_attempt,
            "billable_token_ceiling_per_attempt",
        )
        self._requested_output_tokens = 0
        self._attempt_reserved_output_tokens = 0
        self._response_text_bytes = 0
        self._transport_response_bytes = 0
        self._wall_microseconds = 0
        self._reservation_required = True
        self._journal_path = journal_path
        self._event_count = 0
        self._final_event_sha256 = _ZERO_SHA256
        self._descriptor: int | None = None
        if resume:
            try:
                raw = _read_regular_bytes(journal_path, max_bytes=MAX_ARTIFACT_JSONL_BYTES)
                events = parse_canonical_jsonl_bytes(
                    raw,
                    max_bytes=MAX_ARTIFACT_JSONL_BYTES,
                    max_lines=max_calls * 34,
                    max_line_bytes=MAX_ARTIFACT_JSONL_LINE_BYTES,
                )
                previous = _ZERO_SHA256
                for sequence, wire in enumerate(events):
                    normalized, event = _event_from_wire(
                        wire,
                        sequence=sequence,
                        previous=previous,
                    )
                    if type(event) is CallIntent:
                        self._account_intent(event, require_complete_reservation=False)
                        super().write_intent(event)
                    elif type(event) is AttemptIntent:
                        if len(self.attempt_intents) >= self._max_attempts:
                            raise _error(
                                ArtifactCode.LIMIT_EXCEEDED,
                                "journal.attempts",
                                "attempt count exceeds the configured limit",
                            )
                        self._account_attempt_intent(event)
                        super().write_attempt_intent(event)
                    elif type(event) is AttemptResult:
                        super().write_attempt_result(event)
                    elif type(event) is CallResult:
                        self._account_replayed_result(event)
                        super().write_result(event)
                    else:  # pragma: no cover - exhaustive internal union
                        raise AssertionError("unhandled WAL event")
                    previous = wal_event_sha256(normalized)
                self._event_count = len(events)
                self._final_event_sha256 = previous
            except ArtifactError as error:
                if error.code is ArtifactCode.NOT_REGULAR_FILE:
                    raise
                raise _error(
                    ArtifactCode.CORRUPT_JOURNAL,
                    "journal",
                    "journal cannot be replayed",
                ) from error
            except Exception as error:
                raise _error(
                    ArtifactCode.CORRUPT_JOURNAL,
                    "journal",
                    "journal cannot be replayed",
                ) from error
        else:
            try:
                if _read_regular_bytes(journal_path, max_bytes=1):
                    raise _error(
                        ArtifactCode.ALREADY_EXISTS,
                        "journal",
                        "fresh journal must be empty",
                    )
            except ArtifactError as error:
                if error.code is not ArtifactCode.LIMIT_EXCEEDED:
                    raise
                raise _error(
                    ArtifactCode.ALREADY_EXISTS,
                    "journal",
                    "fresh journal must be empty",
                ) from error
        self._descriptor = _open_regular_append(journal_path)

    def __enter__(self) -> Self:
        if self._descriptor is None:
            raise _error(ArtifactCode.IO_ERROR, "journal", "sink is closed")
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._descriptor is None:
            return
        os.close(self._descriptor)
        self._descriptor = None

    @property
    def final_event_sha256(self) -> str:
        return self._final_event_sha256

    @property
    def journal_sha256(self) -> str:
        raw = _read_regular_bytes(self._journal_path, max_bytes=MAX_ARTIFACT_JSONL_BYTES)
        return hashlib.sha256(_WAL_DOMAIN + raw).hexdigest()

    def _require_writable(self) -> None:
        if self._descriptor is None:
            raise _error(ArtifactCode.IO_ERROR, "journal", "sink is closed")

    def _remaining_supports_complete_unit(self) -> None:
        reservation = self._next_unit_reservation
        if reservation is None or not self._reservation_required:
            return
        remaining = (
            ("logical_calls", self._max_calls - len(self.intents), reservation.logical_calls),
            (
                "attempts",
                self._max_attempts - len(self.attempt_intents),
                reservation.attempts,
            ),
            (
                "requested_output_tokens",
                self._max_requested_output_tokens - self._requested_output_tokens,
                reservation.requested_output_tokens,
            ),
            (
                "attempt_reserved_output_tokens",
                self._max_attempt_reserved_output_tokens
                - self._attempt_reserved_output_tokens,
                reservation.attempt_reserved_output_tokens,
            ),
            (
                "response_text_bytes",
                self._max_response_text_bytes - self._response_text_bytes,
                reservation.response_text_bytes,
            ),
            (
                "transport_response_bytes",
                self._max_transport_response_bytes - self._transport_response_bytes,
                reservation.transport_response_bytes,
            ),
            (
                "wall_microseconds",
                self._max_wall_microseconds - self._wall_microseconds,
                reservation.wall_microseconds,
            ),
        )
        for name, available, required in remaining:
            if available < required:
                raise _error(
                    ArtifactCode.LIMIT_EXCEEDED,
                    f"journal.reservation.{name}",
                    "cannot reserve the next complete candidate unit",
                )

    def _account_intent(
        self,
        intent: CallIntent,
        *,
        require_complete_reservation: bool,
    ) -> None:
        if require_complete_reservation:
            self._remaining_supports_complete_unit()
        requested = intent.max_tokens
        response = requested * MAX_PROXY_TEXT_BYTES_PER_TOKEN
        if self._requested_output_tokens + requested > self._max_requested_output_tokens:
            raise _error(
                ArtifactCode.LIMIT_EXCEEDED,
                "journal.requested_output_tokens",
                "logical requested-output-token ceiling is exhausted",
            )
        if self._response_text_bytes + response > self._max_response_text_bytes:
            raise _error(
                ArtifactCode.LIMIT_EXCEEDED,
                "journal.response_text_bytes",
                "response-text ceiling is exhausted",
            )
        self._requested_output_tokens += requested
        self._response_text_bytes += response
        self._reservation_required = False

    def _account_attempt_intent(self, intent: AttemptIntent) -> None:
        requested = intent.reserved_output_tokens
        if (
            self._attempt_reserved_output_tokens + requested
            > self._max_attempt_reserved_output_tokens
        ):
            raise _error(
                ArtifactCode.LIMIT_EXCEEDED,
                "journal.attempt_reserved_output_tokens",
                "attempt-reserved output-token ceiling is exhausted",
            )
        if (
            self._transport_response_bytes + MAX_PROXY_TRANSPORT_RESPONSE_BYTES
            > self._max_transport_response_bytes
        ):
            raise _error(
                ArtifactCode.LIMIT_EXCEEDED,
                "journal.transport_response_bytes",
                "transport-response ceiling is exhausted",
            )
        self._attempt_reserved_output_tokens += requested
        self._transport_response_bytes += MAX_PROXY_TRANSPORT_RESPONSE_BYTES

    def _account_replayed_result(self, result: CallResult) -> None:
        returned = result.provider.returned_model_id
        if result.status == "succeeded":
            if self._require_successful_provider_evidence:
                if (
                    not result.provider.available
                    or result.provider.status != "succeeded"
                    or returned != self._allowed_returned_model_id
                ):
                    raise _error(
                        ArtifactCode.CORRUPT_JOURNAL,
                        "journal.provider",
                        "successful live call does not satisfy the manifest provider rule",
                    )
            exceeded = self._provider_usage_ceiling_exceeded(
                result.provider,
                requested_output_tokens=self._current_requested_output_tokens(result),
            )
            if exceeded is not None:
                raise _error(
                    ArtifactCode.CORRUPT_JOURNAL,
                    f"journal.provider.{exceeded}",
                    "successful live call exceeds its billable token ceiling",
                )
        if (
            returned is not None
            and self._allowed_returned_model_id is not None
            and returned != self._allowed_returned_model_id
            and result.failure_code is not CallFailureCode.RETURNED_MODEL_MISMATCH
        ):
            raise _error(
                ArtifactCode.CORRUPT_JOURNAL,
                "journal.returned_model_id",
                "does not satisfy the manifest model rule",
            )
        self._wall_microseconds += result.elapsed_microseconds
        if self._wall_microseconds > self._max_wall_microseconds:
            raise _error(
                ArtifactCode.LIMIT_EXCEEDED,
                "journal.wall_microseconds",
                "recorded wall-time ceiling is exceeded",
            )

    def _provider_usage_ceiling_exceeded(
        self,
        provider: ProviderObservation,
        *,
        requested_output_tokens: int | None,
    ) -> str | None:
        ceilings = self._billable_token_ceiling_per_attempt
        if ceilings is None:
            return None
        for field in _PROVIDER_USAGE_FIELDS:
            value = cast(int | None, getattr(provider, field))
            ceiling = ceilings[field]
            if field == "output_tokens" and requested_output_tokens is not None:
                ceiling = min(ceiling, requested_output_tokens)
            if value is not None and value > ceiling:
                return field
        return None

    def _current_requested_output_tokens(self, result: CallResult) -> int | None:
        if not self.intents:
            return None
        intent = self.intents[-1]
        if (
            intent.logical_call_id != result.logical_call_id
            or intent.call_index != result.call_index
        ):
            return None
        return intent.max_tokens

    def mark_unit_committed(
        self,
        next_reservation: CompleteUnitReservation | None = None,
    ) -> None:
        """Require the supplied scheduled-unit reservation before the next intent."""

        self._require_writable()
        if next_reservation is not None and type(
            next_reservation
        ) is not CompleteUnitReservation:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "next_reservation",
                "must be null or an exact CompleteUnitReservation",
            )
        self._next_unit_reservation = (
            self._complete_unit_reservation
            if next_reservation is None
            else next_reservation
        )
        self._reservation_required = True

    def _append(self, event: CallIntent | AttemptIntent | AttemptResult | CallResult) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            raise _error(ArtifactCode.IO_ERROR, "journal", "sink is closed")
        event_type, payload = _event_payload(event)
        wire: dict[str, object] = {
            "event_type": event_type,
            "payload": payload,
            "previous_event_sha256": self._final_event_sha256,
            "sequence": self._event_count,
            "version": BENCHMARK_WAL_VERSION,
        }
        encoded = canonical_json_bytes(wire) + b"\n"
        offset = 0
        try:
            while offset < len(encoded):
                written = os.write(descriptor, encoded[offset:])
                if written <= 0:
                    raise OSError("short journal write")
                offset += written
            os.fsync(descriptor)
        except OSError as error:
            raise _error(ArtifactCode.IO_ERROR, "journal", "durable append failed") from error
        self._final_event_sha256 = wal_event_sha256(wire)
        self._event_count += 1

    def write_intent(self, intent: CallIntent) -> None:
        self._require_writable()
        self._account_intent(intent, require_complete_reservation=True)
        super().write_intent(intent)
        self._append(intent)

    def write_attempt_intent(self, intent: AttemptIntent) -> None:
        self._require_writable()
        if len(self.attempt_intents) >= self._max_attempts:
            raise _error(
                ArtifactCode.LIMIT_EXCEEDED,
                "journal.attempts",
                "attempt count exceeds the configured limit",
            )
        self._account_attempt_intent(intent)
        super().write_attempt_intent(intent)
        self._append(intent)

    def write_attempt_result(self, result: AttemptResult) -> None:
        self._require_writable()
        super().write_attempt_result(result)
        self._append(result)

    def write_result(self, result: CallResult) -> None:
        self._require_writable()
        exact = result
        model_mismatch = (
            exact.provider.returned_model_id is not None
            and self._allowed_returned_model_id is not None
            and exact.provider.returned_model_id != self._allowed_returned_model_id
        )
        missing_live_provider_evidence = (
            exact.status == "succeeded"
            and self._require_successful_provider_evidence
            and (
                not exact.provider.available
                or exact.provider.status != "succeeded"
                or exact.provider.returned_model_id is None
            )
        )
        exceeded_usage_field = (
            self._provider_usage_ceiling_exceeded(
                exact.provider,
                requested_output_tokens=self._current_requested_output_tokens(exact),
            )
            if exact.status == "succeeded"
            else None
        )
        provider_integrity_failure = (
            model_mismatch
            or missing_live_provider_evidence
            or exceeded_usage_field is not None
        )
        if provider_integrity_failure:
            exact = CallResult(
                exact.logical_call_id,
                exact.call_index,
                "failed",
                None,
                exact.elapsed_microseconds,
                (
                    CallFailureCode.RETURNED_MODEL_MISMATCH
                    if model_mismatch
                    else CallFailureCode.PROVIDER_METADATA_INVALID
                ),
                exact.provider,
            )
        super().write_result(exact)
        self._append(exact)
        self._wall_microseconds += exact.elapsed_microseconds
        wall_exceeded = self._wall_microseconds > self._max_wall_microseconds
        if model_mismatch:
            raise _error(
                ArtifactCode.HASH_MISMATCH,
                "journal.returned_model_id",
                "does not equal the manifest's requested model",
            )
        if missing_live_provider_evidence:
            raise _error(
                ArtifactCode.HASH_MISMATCH,
                "journal.provider",
                "successful live call lacks the required provider model evidence",
            )
        if exceeded_usage_field is not None:
            raise _error(
                ArtifactCode.LIMIT_EXCEEDED,
                f"journal.provider.{exceeded_usage_field}",
                "exceeds the formal per-attempt billable token ceiling",
            )
        if wall_exceeded:
            raise _error(
                ArtifactCode.LIMIT_EXCEEDED,
                "journal.wall_microseconds",
                "recorded wall-time ceiling is exceeded",
            )


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(descriptor, data[offset:])
        if written <= 0:
            raise OSError("short artifact write")
        offset += written


def _atomic_create(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    """Create one file atomically without replacing any existing destination."""

    if type(data) is not bytes:
        raise _error(ArtifactCode.INVALID_INPUT, "data", "must be exact bytes")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        _write_all(descriptor, data)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            raise _error(
                ArtifactCode.ALREADY_EXISTS, "path", "destination already exists"
            ) from None
        os.unlink(temporary)
        _fsync_directory(path.parent)
    except ArtifactError:
        raise
    except OSError as error:
        raise _error(ArtifactCode.IO_ERROR, "path", "atomic artifact creation failed") from error
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_create_or_verify_identical(path: Path, data: bytes) -> None:
    """Create once, or accept an earlier byte-identical finalized sidecar."""

    try:
        _atomic_create(path, data)
    except ArtifactError as error:
        if error.code is not ArtifactCode.ALREADY_EXISTS:
            raise
        existing = _read_regular_bytes(path, max_bytes=MAX_ARTIFACT_JSONL_BYTES)
        if existing != data:
            raise _error(
                ArtifactCode.HASH_MISMATCH,
                "path",
                "existing sidecar differs from the deterministic retry",
            ) from None


def _create_empty_file(path: Path) -> None:
    _atomic_create(path, b"")


def _require_directory(path: Path, field: str) -> None:
    try:
        mode = os.lstat(path).st_mode
    except OSError as error:
        raise _error(ArtifactCode.IO_ERROR, field, "directory is unavailable") from error
    if not stat.S_ISDIR(mode) or stat.S_ISLNK(mode):
        raise _error(ArtifactCode.NOT_REGULAR_FILE, field, "path is not a real directory")


def _acquire_lock(path: Path) -> int:
    flags = (
        os.O_RDWR
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise _error(ArtifactCode.NOT_REGULAR_FILE, "lock", "lock file cannot be opened") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise _error(ArtifactCode.NOT_REGULAR_FILE, "lock", "lock is not a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise _error(ArtifactCode.LOCKED, "lock", "another writer owns this output") from None
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@dataclass(frozen=True, slots=True)
class CompletedUnit:
    """Immutable, fully resolved unit exposed to a resuming collector."""

    schedule_index: int
    row: BenchmarkRow
    blobs: tuple[BlobRecord, ...]

    def __post_init__(self) -> None:
        _require_int(
            self.schedule_index,
            "unit.schedule_index",
            minimum=0,
            maximum=MAX_ARTIFACT_ROWS - 1,
        )
        if (
            type(self.row) is not BenchmarkRow
            or type(self.blobs) is not tuple
            or any(type(value) is not BlobRecord for value in self.blobs)
        ):
            raise _error(ArtifactCode.INVALID_INPUT, "unit", "contains invalid typed values")
        refs = tuple(
            sorted((record.ref for record in self.blobs), key=lambda value: value.sort_key)
        )
        if refs != self.row.blob_refs or len(set(refs)) != len(refs):
            raise _error(
                ArtifactCode.COVERAGE_MISMATCH,
                "unit.blobs",
                "must resolve every row blob reference exactly once",
            )


def _unit_to_dict(value: CompletedUnit) -> dict[str, object]:
    return {
        "blobs": [blob_record_to_dict(blob) for blob in value.blobs],
        "row": row_to_dict(value.row),
        "schedule_index": value.schedule_index,
        "version": BENCHMARK_STAGED_UNIT_VERSION,
    }


def _unit_from_dict(value: object, *, maximum_blobs: int) -> CompletedUnit:
    obj = _require_exact_dict(
        value,
        "unit",
        frozenset({"version", "schedule_index", "row", "blobs"}),
    )
    if obj["version"] != BENCHMARK_STAGED_UNIT_VERSION:
        raise _error(ArtifactCode.CORRUPT_UNIT, "unit.version", "has the wrong version")
    blobs = _require_exact_list(obj["blobs"], "unit.blobs", maximum=maximum_blobs)
    try:
        return CompletedUnit(
            _require_int(
                obj["schedule_index"],
                "unit.schedule_index",
                minimum=0,
                maximum=MAX_ARTIFACT_ROWS - 1,
            ),
            row_from_dict(obj["row"]),
            tuple(blob_record_from_dict(blob) for blob in blobs),
        )
    except ArtifactError as error:
        if error.code in {ArtifactCode.LIMIT_EXCEEDED, ArtifactCode.CORRUPT_UNIT}:
            raise
        raise _error(ArtifactCode.CORRUPT_UNIT, "unit", "staged unit is invalid") from error


def _manifest_allowed_returned_model_id(manifest: BenchmarkManifest) -> str | None:
    """Read the runner's exact model rule without imposing it on generic manifests."""

    model = manifest.parameters.get("model")
    if type(model) is not dict:
        return None
    raw = cast(dict[str, object], model).get("allowed_returned_model_id")
    return _model_id(
        raw, "manifest.parameters.model.allowed_returned_model_id", optional=True
    )


def _manifest_billable_token_ceilings(
    manifest: BenchmarkManifest,
) -> dict[str, int] | None:
    """Read a validated live pre-call envelope without importing the cyclic parser."""

    if manifest.stub:
        return None
    pre_call = manifest.parameters.get("pre_call")
    if pre_call is None:
        return None
    if type(pre_call) is not dict:
        raise _error(
            ArtifactCode.INVALID_INPUT,
            "manifest.parameters.pre_call",
            "must be an exact object or null",
        )
    pre_call_obj = cast(dict[str, object], pre_call)
    if pre_call_obj.get("schema") != _FORMAL_PRE_CALL_CONFIG_VERSION:
        return None
    billing = pre_call_obj.get("billing_envelope")
    if type(billing) is not dict:
        raise _error(
            ArtifactCode.INVALID_INPUT,
            "manifest.parameters.pre_call.billing_envelope",
            "must be an exact object",
        )
    wire = cast(dict[str, object], billing).get("wire")
    if type(wire) is not dict:
        raise _error(
            ArtifactCode.INVALID_INPUT,
            "manifest.parameters.pre_call.billing_envelope.wire",
            "must be an exact object",
        )
    return _provider_usage_ceilings(
        cast(dict[str, object], wire).get("billable_token_ceiling_per_attempt"),
        (
            "manifest.parameters.pre_call.billing_envelope.wire."
            "billable_token_ceiling_per_attempt"
        ),
    )


def _manifest_requires_successful_provider_evidence(
    manifest: BenchmarkManifest,
) -> bool:
    if manifest.stub:
        return False
    pre_call = manifest.parameters.get("pre_call")
    return (
        type(pre_call) is dict
        and cast(dict[str, object], pre_call).get("schema")
        == _FORMAL_PRE_CALL_CONFIG_VERSION
    )


class ArtifactStore:
    """Exclusive owner of one fresh or resumable benchmark output directory."""

    def __init__(
        self,
        root: Path,
        manifest: BenchmarkManifest,
        lock_descriptor: int,
        sink: DurableObservationSink,
        units: list[CompletedUnit],
    ) -> None:
        self._root = root
        self._manifest = manifest
        self._lock_descriptor: int | None = lock_descriptor
        self._sink = sink
        self._units = units
        self._closed = False
        self._finalized = (root / "canonical").exists()
        self._aborted = (root / "abort-receipt.json").exists()

    @classmethod
    def create(cls, output_dir: Path, manifest: BenchmarkManifest) -> ArtifactStore:
        if not isinstance(output_dir, Path) or type(manifest) is not BenchmarkManifest:
            raise _error(
                ArtifactCode.INVALID_INPUT, "create", "requires Path and BenchmarkManifest"
            )
        try:
            output_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
        except FileExistsError:
            raise _error(ArtifactCode.ALREADY_EXISTS, "output_dir", "must be fresh") from None
        except OSError as error:
            raise _error(ArtifactCode.IO_ERROR, "output_dir", "could not create output") from error
        os.chmod(output_dir, 0o700)
        staging = output_dir / "staging"
        units = staging / "units"
        staging.mkdir(mode=0o700)
        units.mkdir(mode=0o700)
        os.chmod(staging, 0o700)
        os.chmod(units, 0o700)
        _atomic_create(output_dir / "config.json", canonical_json_bytes(manifest_to_dict(manifest)))
        _create_empty_file(output_dir / ".writer.lock")
        _create_empty_file(output_dir / "journal.jsonl")
        lock_descriptor = _acquire_lock(output_dir / ".writer.lock")
        try:
            sink = DurableObservationSink(
                output_dir / "journal.jsonl",
                max_calls=manifest.limits.max_calls,
                max_attempts=manifest.limits.max_attempts,
                max_requested_output_tokens=manifest.limits.max_requested_output_tokens,
                max_attempt_reserved_output_tokens=(
                    manifest.limits.max_attempt_reserved_output_tokens
                ),
                max_response_text_bytes=manifest.limits.max_response_text_bytes,
                max_transport_response_bytes=manifest.limits.max_transport_response_bytes,
                max_wall_microseconds=manifest.limits.max_wall_microseconds,
                complete_unit_reservation=manifest.limits.complete_unit_reservation,
                allowed_returned_model_id=_manifest_allowed_returned_model_id(manifest),
                require_successful_provider_evidence=(
                    _manifest_requires_successful_provider_evidence(manifest)
                ),
                billable_token_ceiling_per_attempt=(
                    _manifest_billable_token_ceilings(manifest)
                ),
            )
        except BaseException:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
            raise
        return cls(output_dir, manifest, lock_descriptor, sink, [])

    @classmethod
    def resume(cls, output_dir: Path, manifest: BenchmarkManifest) -> ArtifactStore:
        if not isinstance(output_dir, Path) or type(manifest) is not BenchmarkManifest:
            raise _error(
                ArtifactCode.INVALID_INPUT, "resume", "requires Path and BenchmarkManifest"
            )
        _require_directory(output_dir, "output_dir")
        if (output_dir / "canonical").exists():
            raise _error(ArtifactCode.ALREADY_EXISTS, "canonical", "run is already finalized")
        if (output_dir / "abort-receipt.json").exists():
            raise _error(ArtifactCode.INCOMPLETE, "receipt", "aborted run cannot resume")
        lock_descriptor = _acquire_lock(output_dir / ".writer.lock")
        sink: DurableObservationSink | None = None
        try:
            wire = read_canonical_json(
                output_dir / "config.json",
                max_bytes=manifest.limits.max_json_bytes,
            )
            stored = manifest_from_dict(wire)
            if manifest_sha256(stored) != manifest_sha256(manifest):
                raise _error(ArtifactCode.HASH_MISMATCH, "manifest", "resume config differs")
            sink = DurableObservationSink(
                output_dir / "journal.jsonl",
                max_calls=manifest.limits.max_calls,
                max_attempts=manifest.limits.max_attempts,
                max_requested_output_tokens=manifest.limits.max_requested_output_tokens,
                max_attempt_reserved_output_tokens=(
                    manifest.limits.max_attempt_reserved_output_tokens
                ),
                max_response_text_bytes=manifest.limits.max_response_text_bytes,
                max_transport_response_bytes=manifest.limits.max_transport_response_bytes,
                max_wall_microseconds=manifest.limits.max_wall_microseconds,
                complete_unit_reservation=manifest.limits.complete_unit_reservation,
                allowed_returned_model_id=_manifest_allowed_returned_model_id(manifest),
                require_successful_provider_evidence=(
                    _manifest_requires_successful_provider_evidence(manifest)
                ),
                billable_token_ceiling_per_attempt=(
                    _manifest_billable_token_ceilings(manifest)
                ),
                resume=True,
            )
            units = cls._read_units(output_dir, manifest)
            cls._validate_resume_state(manifest, sink, units)
            sink.mark_unit_committed()
            return cls(output_dir, manifest, lock_descriptor, sink, units)
        except BaseException:
            if sink is not None:
                sink.close()
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
            raise

    @staticmethod
    def _read_units(root: Path, manifest: BenchmarkManifest) -> list[CompletedUnit]:
        units_dir = root / "staging" / "units"
        _require_directory(units_dir, "staging.units")
        entries = sorted(units_dir.iterdir(), key=lambda path: path.name)
        if len(entries) > manifest.limits.max_rows:
            raise _error(ArtifactCode.LIMIT_EXCEEDED, "units", "unit count exceeds max_rows")
        units: list[CompletedUnit] = []
        for expected_index, path in enumerate(entries):
            if path.name != f"{expected_index:08d}.json":
                raise _error(ArtifactCode.CORRUPT_UNIT, "units", "filenames are noncanonical")
            try:
                wire = read_canonical_json(path, max_bytes=manifest.limits.max_json_bytes)
                unit = _unit_from_dict(wire, maximum_blobs=manifest.limits.max_blobs)
            except ArtifactError as error:
                if error.code is ArtifactCode.NON_CANONICAL:
                    raise
                raise _error(ArtifactCode.CORRUPT_UNIT, "units", "unit cannot be read") from error
            if unit.schedule_index != expected_index:
                raise _error(ArtifactCode.CORRUPT_UNIT, "units", "indices are noncanonical")
            units.append(unit)
        return units

    @staticmethod
    def _validate_resume_state(
        manifest: BenchmarkManifest,
        sink: DurableObservationSink,
        units: list[CompletedUnit],
    ) -> None:
        if sink.has_open_intent or sink.has_open_attempt:
            raise _error(ArtifactCode.INCOMPLETE, "journal", "contains an orphan intent")
        row_keys: set[RowKey] = set()
        owned_calls: set[ObservationKey] = set()
        for unit in units:
            if unit.row.run_id != manifest.run_id or unit.row.key not in manifest.expected_rows:
                raise _error(ArtifactCode.CORRUPT_UNIT, "unit.row", "does not match manifest")
            if unit.row.key in row_keys:
                raise _error(ArtifactCode.CORRUPT_UNIT, "unit.row", "row key is duplicated")
            row_keys.add(unit.row.key)
            for call in unit.row.observation_keys:
                if call in owned_calls:
                    raise _error(ArtifactCode.CORRUPT_UNIT, "unit.calls", "call is duplicated")
                owned_calls.add(call)
        journal_calls = {
            ObservationKey(intent.logical_call_id, intent.call_index) for intent in sink.intents
        }
        if owned_calls != journal_calls:
            raise _error(
                ArtifactCode.INCOMPLETE,
                "units",
                "terminal calls are not covered by complete staged units",
            )

    def __enter__(self) -> Self:
        if self._closed:
            raise _error(ArtifactCode.IO_ERROR, "store", "is closed")
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._sink.close()
        descriptor = self._lock_descriptor
        if descriptor is not None:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
            self._lock_descriptor = None
        self._closed = True

    def _require_open(self) -> None:
        if self._closed:
            raise _error(ArtifactCode.IO_ERROR, "store", "is closed")
        if self._aborted:
            raise _error(ArtifactCode.INCOMPLETE, "store", "aborted run is terminal")
        if self._finalized:
            raise _error(ArtifactCode.ALREADY_EXISTS, "store", "finalized run is terminal")

    @property
    def sink(self) -> DurableObservationSink:
        self._require_open()
        return self._sink

    @property
    def manifest_sha256(self) -> str:
        return manifest_sha256(self._manifest)

    @property
    def completed_unit_indices(self) -> tuple[int, ...]:
        return tuple(unit.schedule_index for unit in self._units)

    @property
    def completed_units(self) -> tuple[CompletedUnit, ...]:
        """Return detached immutable recovery units in schedule order."""

        return tuple(self._units)

    @property
    def completed_rows(self) -> tuple[BenchmarkRow, ...]:
        """Return the staged rows in schedule order for experiment reconstruction."""

        return tuple(unit.row for unit in self._units)

    def reserve_next_unit(self, reservation: CompleteUnitReservation) -> None:
        """Install the executable schedule's exact next-unit reservation."""

        self._require_open()
        if type(reservation) is not CompleteUnitReservation:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "reservation",
                "must be an exact CompleteUnitReservation",
            )
        maximum = self._manifest.limits.complete_unit_reservation
        if maximum is None:
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "reservation",
                "manifest has no complete-unit reservation contract",
            )
        for field in (
            "logical_calls",
            "attempts",
            "requested_output_tokens",
            "attempt_reserved_output_tokens",
            "response_text_bytes",
            "transport_response_bytes",
            "wall_microseconds",
        ):
            if cast(int, getattr(reservation, field)) > cast(int, getattr(maximum, field)):
                raise _error(
                    ArtifactCode.INVALID_INPUT,
                    f"reservation.{field}",
                    "exceeds the manifest's maximum complete-unit reservation",
                )
        self._sink.mark_unit_committed(reservation)

    def commit_unit(
        self,
        schedule_index: int,
        row: BenchmarkRow,
        blobs: tuple[BlobRecord, ...],
    ) -> None:
        self._require_open()
        if self._finalized:
            raise _error(ArtifactCode.ALREADY_EXISTS, "canonical", "run is finalized")
        if self._sink.has_open_intent or self._sink.has_open_attempt:
            raise _error(ArtifactCode.INCOMPLETE, "journal", "cannot commit around an open call")
        if type(schedule_index) is not int or schedule_index != len(self._units):
            raise _error(ArtifactCode.COVERAGE_MISMATCH, "schedule_index", "must be the next unit")
        if type(row) is not BenchmarkRow or row.run_id != self._manifest.run_id:
            raise _error(ArtifactCode.INVALID_INPUT, "row", "does not match the run")
        if row.key not in self._manifest.expected_rows or any(
            unit.row.key == row.key for unit in self._units
        ):
            raise _error(ArtifactCode.COVERAGE_MISMATCH, "row.key", "is unexpected or duplicated")
        unit = CompletedUnit(schedule_index, row, blobs)
        already_owned = {key for staged in self._units for key in staged.row.observation_keys}
        all_calls = {
            ObservationKey(intent.logical_call_id, intent.call_index)
            for intent in self._sink.intents
        }
        new_calls = all_calls - already_owned
        if set(row.observation_keys) != new_calls:
            raise _error(
                ArtifactCode.COVERAGE_MISMATCH,
                "row.observation_keys",
                "must own exactly the calls since the previous unit",
            )
        path = self._root / "staging" / "units" / f"{schedule_index:08d}.json"
        encoded = canonical_json_bytes(_unit_to_dict(unit))
        if len(encoded) > self._manifest.limits.max_json_bytes:
            raise _error(ArtifactCode.LIMIT_EXCEEDED, "unit", "exceeds manifest max_json_bytes")
        _atomic_create(path, encoded)
        self._units.append(unit)
        self._sink.mark_unit_committed()

    def _complete_receipt(
        self,
        *,
        rows_sha256: str,
        blobs_sha256: str,
        observations: SanitizedObservations,
    ) -> BenchmarkReceipt:
        returned = tuple(
            sorted(
                {
                    result.provider.returned_model_id
                    for result in self._sink.results
                    if result.provider.returned_model_id is not None
                }
            )
        )
        return BenchmarkReceipt(
            self._manifest.run_id,
            manifest_sha256(self._manifest),
            self._manifest.corpus_sha256,
            self._sink.journal_sha256,
            rows_sha256,
            blobs_sha256,
            observations.sha256,
            returned,
            self._manifest.analysis_code_sha256,
            len(self._manifest.expected_rows),
            len(self._units),
            self._manifest.limits.max_calls,
            len(self._sink.intents),
            CompletionStatus.COMPLETE,
            None,
        )

    def finalize(
        self,
        *,
        report_callback: Callable[[FinalizationInputs], FinalizedReport] | None = None,
    ) -> BenchmarkReceipt:
        self._require_open()
        if report_callback is not None and not callable(report_callback):
            raise _error(
                ArtifactCode.INVALID_INPUT,
                "report_callback",
                "must be null or callable",
            )
        if self._finalized or (self._root / "canonical").exists():
            raise _error(ArtifactCode.ALREADY_EXISTS, "canonical", "run is already finalized")
        if self._sink.has_open_intent or self._sink.has_open_attempt:
            raise _error(ArtifactCode.INCOMPLETE, "journal", "contains an orphan intent")
        observed_keys = tuple(
            sorted((unit.row.key for unit in self._units), key=lambda key: key.sort_key)
        )
        if observed_keys != self._manifest.expected_rows:
            raise _error(
                ArtifactCode.COVERAGE_MISMATCH, "rows", "expected row coverage is incomplete"
            )
        self._validate_resume_state(self._manifest, self._sink, self._units)

        rows = tuple(sorted((unit.row for unit in self._units), key=lambda row: row.sort_key))
        blobs_by_ref: dict[BlobRef, BlobRecord] = {}
        for unit in self._units:
            for blob in unit.blobs:
                existing = blobs_by_ref.get(blob.ref)
                if existing is not None and existing != blob:
                    raise _error(ArtifactCode.HASH_MISMATCH, "blobs", "digest collision/drift")
                blobs_by_ref[blob.ref] = blob
        if len(blobs_by_ref) > self._manifest.limits.max_blobs:
            raise _error(ArtifactCode.LIMIT_EXCEEDED, "blobs", "count exceeds manifest limit")
        blobs = tuple(sorted(blobs_by_ref.values(), key=lambda blob: blob.ref.sort_key))
        rows_bytes, blobs_bytes = _publication_table_bytes(self._manifest, rows, blobs)
        private, observations = sanitize_observations(
            self._manifest.run_id,
            self._sink,
            stub=self._manifest.stub,
        )
        observations_bytes = canonical_json_bytes(observations.to_dict())
        if len(observations_bytes) > self._manifest.limits.max_json_bytes:
            raise _error(
                ArtifactCode.LIMIT_EXCEEDED,
                "observations",
                "exceeds manifest max_json_bytes",
            )
        receipt = self._complete_receipt(
            rows_sha256=canonical_table_sha256("rows", rows_bytes),
            blobs_sha256=canonical_table_sha256("blobs", blobs_bytes),
            observations=observations,
        )
        inputs = FinalizationInputs(
            self._manifest,
            receipt,
            rows,
            blobs,
            observations,
        )
        report: FinalizedReport | None = None
        if report_callback is not None:
            report = report_callback(inputs)
            if type(report) is not FinalizedReport:
                raise _error(
                    ArtifactCode.INVALID_INPUT,
                    "report_callback",
                    "must return an exact FinalizedReport",
                )
            _validate_report_publication(
                report,
                self._manifest,
                receipt,
                rows,
                blobs,
                observations,
            )
        _atomic_create_or_verify_identical(
            self._root / "private-observations.json",
            canonical_json_bytes(private.to_dict()),
        )

        staging_root = self._root / "staging"
        temporary = Path(tempfile.mkdtemp(prefix=".canonical.", dir=staging_root))
        os.chmod(temporary, 0o700)
        try:
            _atomic_create(
                temporary / "config.json", canonical_json_bytes(manifest_to_dict(self._manifest))
            )
            _atomic_create(temporary / "rows.jsonl", rows_bytes)
            _atomic_create(temporary / "blobs.jsonl", blobs_bytes)
            _atomic_create(temporary / "observations.json", observations_bytes)
            _atomic_create(
                temporary / "receipt.json", canonical_json_bytes(receipt_to_dict(receipt))
            )
            if report is not None:
                _atomic_create(temporary / "report.json", report.json_bytes)
                _atomic_create(temporary / "report.md", report.markdown_bytes)
            _fsync_directory(temporary)
            try:
                os.rename(temporary, self._root / "canonical")
            except FileExistsError:
                raise _error(ArtifactCode.ALREADY_EXISTS, "canonical", "already exists") from None
            _fsync_directory(self._root)
        except BaseException:
            # The directory is private staging created by this method.  Clean only
            # its own incomplete temporary files; never touch user-provided paths.
            if temporary.exists():
                for child in temporary.iterdir():
                    child.unlink()
                temporary.rmdir()
            raise
        self._finalized = True
        self._sink.close()
        return receipt

    def abort(self, reason_code: str) -> BenchmarkReceipt:
        self._require_open()
        if self._finalized:
            raise _error(ArtifactCode.ALREADY_EXISTS, "canonical", "run is finalized")
        reason = _identifier(reason_code, "receipt.reason_code")
        receipt = BenchmarkReceipt(
            self._manifest.run_id,
            manifest_sha256(self._manifest),
            self._manifest.corpus_sha256,
            self._sink.journal_sha256,
            None,
            None,
            None,
            (),
            self._manifest.analysis_code_sha256,
            len(self._manifest.expected_rows),
            len(self._units),
            self._manifest.limits.max_calls,
            len(self._sink.intents),
            CompletionStatus.INCOMPLETE,
            reason,
        )
        _atomic_create(
            self._root / "abort-receipt.json",
            canonical_json_bytes(receipt_to_dict(receipt)),
        )
        self._aborted = True
        self._sink.close()
        return receipt


def publish_replay_bundle(
    fresh_output: Path,
    bundle: ReplayBundle,
    report: FinalizedReport,
) -> None:
    """Atomically publish one validated replay and its two report renderings."""

    if not isinstance(fresh_output, Path):
        raise _error(ArtifactCode.INVALID_INPUT, "fresh_output", "must be a Path")
    if type(bundle) is not ReplayBundle or type(report) is not FinalizedReport:
        raise _error(
            ArtifactCode.INVALID_INPUT,
            "replay_publication",
            "requires exact ReplayBundle and FinalizedReport values",
        )
    _validate_publication_components(
        bundle.manifest,
        bundle.receipt,
        bundle.rows,
        bundle.blobs,
        bundle.observations,
    )
    _validate_report_publication(
        report,
        bundle.manifest,
        bundle.receipt,
        bundle.rows,
        bundle.blobs,
        bundle.observations,
    )
    rows_bytes, blobs_bytes = _publication_table_bytes(
        bundle.manifest,
        bundle.rows,
        bundle.blobs,
    )
    observations_bytes = canonical_json_bytes(bundle.observations.to_dict())
    if len(observations_bytes) > bundle.manifest.limits.max_json_bytes:
        raise _error(
            ArtifactCode.LIMIT_EXCEEDED,
            "observations",
            "exceeds manifest max_json_bytes",
        )
    files = (
        ("config.json", canonical_json_bytes(manifest_to_dict(bundle.manifest))),
        ("rows.jsonl", rows_bytes),
        ("blobs.jsonl", blobs_bytes),
        ("observations.json", observations_bytes),
        ("receipt.json", canonical_json_bytes(receipt_to_dict(bundle.receipt))),
        ("report.json", report.json_bytes),
        ("report.md", report.markdown_bytes),
    )
    try:
        fresh_output.mkdir(mode=0o700, parents=False, exist_ok=False)
    except FileExistsError:
        raise _error(
            ArtifactCode.ALREADY_EXISTS,
            "fresh_output",
            "must be fresh",
        ) from None
    except OSError as error:
        raise _error(
            ArtifactCode.IO_ERROR,
            "fresh_output",
            "could not create replay output",
        ) from error
    temporary: Path | None = None
    try:
        os.chmod(fresh_output, 0o700)
        temporary = Path(tempfile.mkdtemp(prefix=".canonical.", dir=fresh_output))
        os.chmod(temporary, 0o700)
        for name, data in files:
            _atomic_create(temporary / name, data)
        _fsync_directory(temporary)
        try:
            os.rename(temporary, fresh_output / "canonical")
        except FileExistsError:
            raise _error(
                ArtifactCode.ALREADY_EXISTS,
                "canonical",
                "already exists",
            ) from None
        _fsync_directory(fresh_output)
    except BaseException:
        if temporary is not None and temporary.exists():
            for child in temporary.iterdir():
                child.unlink()
            temporary.rmdir()
        try:
            fresh_output.rmdir()
        except OSError:
            # Preserve an output that stopped being empty; remove only the
            # directory this call created when it still owns the whole node.
            pass
        raise


__all__ = [
    "BENCHMARK_BLOB_VERSION",
    "BENCHMARK_PRIVATE_OBSERVATIONS_VERSION",
    "BENCHMARK_STAGED_UNIT_VERSION",
    "BENCHMARK_WAL_VERSION",
    "ArtifactCode",
    "ArtifactError",
    "ArtifactLimits",
    "ArtifactStore",
    "BenchmarkManifest",
    "BenchmarkReceipt",
    "BenchmarkRow",
    "BlobKind",
    "BlobRecord",
    "BlobRef",
    "CompletedUnit",
    "CompletionStatus",
    "CompleteUnitReservation",
    "DurableObservationSink",
    "FinalizationInputs",
    "FinalizedReport",
    "ObservationKey",
    "PrivateObservations",
    "RowKey",
    "RowType",
    "ReplayBundle",
    "SanitizedObservations",
    "blob_record_from_dict",
    "blob_record_to_dict",
    "blob_ref_from_dict",
    "blob_ref_to_dict",
    "build_blob_record",
    "build_manifest",
    "build_row",
    "canonical_blob_sha256",
    "canonical_jsonl_bytes",
    "canonical_table_sha256",
    "manifest_from_dict",
    "manifest_sha256",
    "manifest_to_dict",
    "load_replay_bundle",
    "parse_canonical_json_bytes",
    "parse_canonical_jsonl_bytes",
    "publish_replay_bundle",
    "read_canonical_json",
    "read_canonical_jsonl",
    "receipt_from_dict",
    "receipt_sha256",
    "receipt_to_dict",
    "require_complete_receipt",
    "row_from_dict",
    "row_to_dict",
    "sanitize_observations",
    "sanitized_observations_from_dict",
    "wal_event_sha256",
]
