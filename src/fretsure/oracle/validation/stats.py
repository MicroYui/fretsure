"""Fail-closed trust statistics for the oracle's human validation set.

The headline trust number is the GREEN false-accept rate: of the tabs the oracle
certifies GREEN, how many did a real player find unplayable.  Soundness demands
this be close to zero, but observing no failures is not proof that the rate is
zero.  We therefore report a one-sided Clopper-Pearson upper bound and preserve
the GREEN denominator explicitly.  A dataset with no GREEN rows has no estimate
at all; it must never be reported as a perfect ``0.0`` result.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from enum import StrEnum
from itertools import islice
from typing import Literal, Protocol, cast

from scipy.stats import beta, norm

from fretsure.oracle.core import check_playability
from fretsure.oracle.input import (
    OracleInputError,
    ensure_profile,
    oracle_checker_work_upper_bound,
)
from fretsure.oracle.profiles import Profile
from fretsure.tab import (
    MAX_TAB_JSON_BYTES,
    Tab,
    TabSchemaError,
    validated_tab_from_json,
)

LabeledRow = dict[str, object]
GreenFalseAcceptStatus = Literal["estimated", "no_green"]
GreenFalseAcceptMethod = Literal["clopper-pearson-one-sided"]
CohenKappaStatus = Literal["estimated", "undefined"]
CohenKappaUndefinedReason = Literal["no_certified_observations", "degenerate_marginals"]

# Public ceilings are deliberately far above the planned pilot and held-out
# sets, but finite so malformed count data and JSONL cannot trigger unbounded
# exact-statistics or parser work.
MAX_STAT_COUNT = 10_000_000
MAX_LABELED_ROWS = 100_000
MAX_LABELED_JSON_LINE_BYTES = MAX_TAB_JSON_BYTES + 1024 * 1024
MAX_LABELED_INTEGER_TOKEN_CHARS = 128
MAX_LABELED_TOTAL_BYTES = 64 * 1024 * 1024
MAX_LABELED_TOTAL_NOTES = 200_000
MAX_LABELED_CHECKER_WORK = 2_000_000
MAX_LABELED_PHYSICAL_LINES = 1_000_000
MAX_LABELED_JSON_DEPTH = 64
MAX_LABELED_JSON_NODES = 250_000
MAX_LABELED_JSON_SCALAR_BYTES = MAX_LABELED_JSON_LINE_BYTES
MAX_LABELED_TOTAL_JSON_NODES = 1_000_000

class StatisticsInputError(ValueError):
    """A typed failure for invalid statistical parameters or count data."""

    def __init__(self, field: str, value: object, detail: str) -> None:
        self.field = field
        self.value = value
        self.detail = detail
        # The field and contract reason are sufficient; rendering an untrusted
        # value would execute caller-controlled repr/type-name hooks.
        super().__init__(f"invalid {field}: {detail}")


class LabeledDataErrorCode(StrEnum):
    """Stable machine-readable failures for the human-label JSONL contract."""

    INVALID_JSON = "INVALID_JSON"
    ROW_NOT_OBJECT = "ROW_NOT_OBJECT"
    MISSING_FIELD = "MISSING_FIELD"
    INVALID_FIELD_TYPE = "INVALID_FIELD_TYPE"
    INVALID_TAB_SCHEMA = "INVALID_TAB_SCHEMA"
    INVALID_ORACLE_INPUT = "INVALID_ORACLE_INPUT"
    INPUT_LIMIT_EXCEEDED = "INPUT_LIMIT_EXCEEDED"


class LabeledDataError(ValueError):
    """A malformed labeled row, including its source path and row/line location."""

    def __init__(
        self,
        code: LabeledDataErrorCode,
        *,
        path: str,
        line_number: int,
        detail: str,
        field: str | None = None,
        column: int | None = None,
    ) -> None:
        self.code = code
        self.path = path
        self.line_number = line_number
        self.field = field
        self.column = column
        self.detail = detail
        location = f"{path}:{line_number}"
        if column is not None:
            location += f":{column}"
        field_text = f" field={field!r}" if field is not None else ""
        super().__init__(f"{code.value} at {location}:{field_text} {detail}")


class _SourcedLabeledRows(list[LabeledRow]):
    """A list-compatible carrier for physical JSONL source locations.

    Rows remain ordinary dictionaries, so callers see the same public row API.
    Identity plus a canonical content digest prevents stale metadata from being
    applied after callers mutate, reorder, or extend the returned list.
    """

    __slots__ = ("_source_records",)

    def __init__(self) -> None:
        super().__init__()
        self._source_records: list[tuple[LabeledRow, str, int, str]] = []

    def append_sourced(
        self,
        row: LabeledRow,
        *,
        path: str,
        line_number: int,
        content_digest: str,
    ) -> None:
        super().append(row)
        self._source_records.append((row, path, line_number, content_digest))

    def source_for(
        self,
        index: int,
        raw_row: object,
        content_digest: str,
    ) -> tuple[str, int] | None:
        try:
            records = object.__getattribute__(self, "_source_records")
        except (AttributeError, TypeError):
            return None
        if type(records) is not list or not 0 <= index < list.__len__(records):
            return None
        record = list.__getitem__(records, index)
        if type(record) is not tuple or len(record) != 4:
            return None
        row, path, line_number, original_digest = record
        if (
            row is raw_row
            and type(path) is str
            and type(line_number) is int
            and line_number >= 1
            and type(original_digest) is str
            and original_digest == content_digest
        ):
            return path, line_number
        return None


@dataclass(frozen=True)
class ConfusionMatrix:
    green_playable: int
    green_unplayable: int  # GREEN false accepts — the dangerous cell
    red_playable: int  # RED false rejects
    red_unplayable: int
    amber_playable: int
    amber_unplayable: int

    def __post_init__(self) -> None:
        for name in (
            "green_playable",
            "green_unplayable",
            "red_playable",
            "red_unplayable",
            "amber_playable",
            "amber_unplayable",
        ):
            _validate_nonnegative_exact_int(getattr(self, name), name)


def _validate_confusion_matrix(value: object) -> ConfusionMatrix:
    """Revalidate even an exact instance in case low-level code forged it."""

    if type(value) is not ConfusionMatrix:
        raise StatisticsInputError(
            "confusion_matrix",
            value,
            "must be an exact ConfusionMatrix instance",
        )
    matrix = value
    counts: dict[str, int] = {}
    for name in (
        "green_playable",
        "green_unplayable",
        "red_playable",
        "red_unplayable",
        "amber_playable",
        "amber_unplayable",
    ):
        try:
            count = object.__getattribute__(matrix, name)
        except (AttributeError, TypeError) as error:
            raise StatisticsInputError(
                f"confusion_matrix.{name}",
                value,
                "field is missing from a forged ConfusionMatrix",
            ) from error
        _validate_nonnegative_exact_int(count, f"confusion_matrix.{name}")
        counts[name] = count
    return ConfusionMatrix(
        green_playable=counts["green_playable"],
        green_unplayable=counts["green_unplayable"],
        red_playable=counts["red_playable"],
        red_unplayable=counts["red_unplayable"],
        amber_playable=counts["amber_playable"],
        amber_unplayable=counts["amber_unplayable"],
    )


@dataclass(frozen=True)
class GreenFalseAcceptResult:
    """Canonical GREEN false-accept estimate, including the evidence denominator."""

    status: GreenFalseAcceptStatus
    x: int
    n_green: int
    confidence: float
    observed_rate: float | None
    upper_bound: float | None
    method: GreenFalseAcceptMethod


@dataclass(frozen=True)
class CohenKappaResult:
    """Canonical kappa result; undefined cases carry a machine-readable reason."""

    status: CohenKappaStatus
    value: float | None
    n: int
    reason: CohenKappaUndefinedReason | None


class _InvalidJsonValue(ValueError):
    """Internal sentinel used to reject non-standard or ambiguous JSON."""


class _PlainJsonIssue(ValueError):
    """An inert-tree violation found before any generic serializer is called."""

    def __init__(self, *, path: str, detail: str, limit: bool = False) -> None:
        self.path = path
        self.detail = detail
        self.limit = limit
        super().__init__(f"{path}: {detail}")


@dataclass
class _PlainJsonBudget:
    nodes: int = 0
    scalar_bytes: int = 0


class _Digest(Protocol):
    def update(self, data: bytes) -> None: ...


def _validate_nonnegative_exact_int(value: object, field: str) -> int:
    # bool is an int subclass, so an isinstance check would silently accept it.
    if type(value) is not int:
        raise StatisticsInputError(field, value, "must be an exact int (bool is not accepted)")
    if value < 0:
        raise StatisticsInputError(field, value, "must be non-negative")
    if value > MAX_STAT_COUNT:
        raise StatisticsInputError(
            field,
            value,
            f"must not exceed the public count limit {MAX_STAT_COUNT}",
        )
    return value


def _validate_binomial_counts(successes: object, n: object) -> tuple[int, int]:
    valid_successes = _validate_nonnegative_exact_int(successes, "successes")
    valid_n = _validate_nonnegative_exact_int(n, "n")
    if valid_successes > valid_n:
        raise StatisticsInputError("successes", valid_successes, f"must not exceed n={valid_n}")
    return valid_successes, valid_n


def _validate_confidence(confidence: object) -> float:
    confidence_type = type(confidence)
    if confidence_type is not int and confidence_type is not float:
        raise StatisticsInputError(
            "confidence", confidence, "must be a finite real number strictly between 0 and 1"
        )
    try:
        value = float(cast(int | float, confidence))
    except (OverflowError, ValueError) as error:
        raise StatisticsInputError(
            "confidence", confidence, "must be a finite real number strictly between 0 and 1"
        ) from error
    if not math.isfinite(value) or not 0.0 < value < 1.0:
        raise StatisticsInputError(
            "confidence", confidence, "must be a finite real number strictly between 0 and 1"
        )
    return value


def _reject_nonstandard_json_constant(token: str) -> object:
    raise _InvalidJsonValue(f"non-standard JSON numeric constant {token!r}")


def _parse_finite_json_float(token: str) -> float:
    value = float(token)
    if not math.isfinite(value):
        raise _InvalidJsonValue(f"JSON number {token!r} is not finite")
    return value


def _parse_bounded_json_int(token: str) -> int:
    if len(token) > MAX_LABELED_INTEGER_TOKEN_CHARS:
        raise _InvalidJsonValue("JSON integer token exceeds the public character limit")
    return int(token)


def _object_without_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidJsonValue(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _json_member_path(parent: str, key: str) -> str:
    """Build a bounded diagnostic path without rendering an untrusted-sized key."""

    preview = key if len(key) <= 64 else key[:61] + "..."
    return f"{parent}[{preview!r}]"


def _charge_scalar(
    value: str,
    *,
    path: str,
    budget: _PlainJsonBudget,
    max_scalar_bytes: int,
) -> None:
    # Every Unicode code point consumes at least one UTF-8 byte.  Checking the
    # cheap lower bound first prevents a many-megabyte temporary allocation for
    # values that are already known to exceed the remaining budget.
    remaining = max_scalar_bytes - budget.scalar_bytes
    if len(value) > remaining:
        raise _PlainJsonIssue(
            path=path,
            detail=(
                "cumulative JSON scalar bytes exceed the public limit "
                f"{max_scalar_bytes}"
            ),
            limit=True,
        )
    try:
        encoded_size = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise _PlainJsonIssue(
            path=path,
            detail="strict JSON strings must encode as valid UTF-8 (no lone surrogates)",
        ) from error
    if encoded_size > remaining:
        raise _PlainJsonIssue(
            path=path,
            detail=(
                "cumulative JSON scalar bytes exceed the public limit "
                f"{max_scalar_bytes}"
            ),
            limit=True,
        )
    budget.scalar_bytes += encoded_size


def _clone_plain_json(
    value: object,
    *,
    path: str,
    depth: int,
    budget: _PlainJsonBudget,
    max_nodes: int = MAX_LABELED_JSON_NODES,
    max_scalar_bytes: int = MAX_LABELED_JSON_SCALAR_BYTES,
) -> object:
    """Snapshot an exact, inert JSON tree without invoking subclass hooks."""

    if depth > MAX_LABELED_JSON_DEPTH:
        raise _PlainJsonIssue(
            path=path,
            detail=f"JSON nesting exceeds the public depth limit {MAX_LABELED_JSON_DEPTH}",
            limit=True,
        )
    budget.nodes += 1
    if budget.nodes > max_nodes:
        raise _PlainJsonIssue(
            path=path,
            detail=f"JSON value count exceeds the public limit {max_nodes}",
            limit=True,
        )

    value_type = type(value)
    if value is None or value_type is bool:
        return value
    if value_type is str:
        text = cast(str, value)
        _charge_scalar(
            text,
            path=path,
            budget=budget,
            max_scalar_bytes=max_scalar_bytes,
        )
        return text
    if value_type is int:
        integer = cast(int, value)
        # The bit bound avoids asking Python to format an arbitrarily large
        # integer merely to discover that its JSON token is too long.
        if integer.bit_length() > MAX_LABELED_INTEGER_TOKEN_CHARS * 4:
            raise _PlainJsonIssue(
                path=path,
                detail=(
                    "JSON integer token exceeds the public character limit "
                    f"{MAX_LABELED_INTEGER_TOKEN_CHARS}"
                ),
                limit=True,
            )
        token = str(integer)
        if len(token) > MAX_LABELED_INTEGER_TOKEN_CHARS:
            raise _PlainJsonIssue(
                path=path,
                detail=(
                    "JSON integer token exceeds the public character limit "
                    f"{MAX_LABELED_INTEGER_TOKEN_CHARS}"
                ),
                limit=True,
            )
        _charge_scalar(
            token,
            path=path,
            budget=budget,
            max_scalar_bytes=max_scalar_bytes,
        )
        return integer
    if value_type is float:
        number = cast(float, value)
        if not math.isfinite(number):
            raise _PlainJsonIssue(
                path=path,
                detail="strict JSON numbers must be finite",
            )
        _charge_scalar(
            number.hex(),
            path=path,
            budget=budget,
            max_scalar_bytes=max_scalar_bytes,
        )
        return number
    if value_type is list:
        sequence = cast(list[object], value)
        remaining_nodes = max_nodes - budget.nodes
        sequence_snapshot = tuple(sequence[: remaining_nodes + 1])
        if len(sequence_snapshot) > remaining_nodes:
            raise _PlainJsonIssue(
                path=path,
                detail=f"JSON value count exceeds the public limit {max_nodes}",
                limit=True,
            )
        return [
            _clone_plain_json(
                child,
                path=f"{path}[{index}]",
                depth=depth + 1,
                budget=budget,
                max_nodes=max_nodes,
                max_scalar_bytes=max_scalar_bytes,
            )
            for index, child in enumerate(sequence_snapshot)
        ]
    if value_type is dict:
        mapping = cast(dict[object, object], value)
        clone: dict[str, object] = {}
        remaining_nodes = max_nodes - budget.nodes
        try:
            item_snapshot = tuple(
                islice(dict.items(mapping), remaining_nodes + 1)
            )
        except RuntimeError:
            raise _PlainJsonIssue(
                path=path,
                detail="mapping changed while it was being snapshotted",
            ) from None
        if len(item_snapshot) > remaining_nodes:
            raise _PlainJsonIssue(
                path=path,
                detail=f"JSON value count exceeds the public limit {max_nodes}",
                limit=True,
            )
        for key, child in item_snapshot:
            if type(key) is not str:
                raise _PlainJsonIssue(
                    path=path,
                    detail="strict JSON object keys must be exact strings",
                )
            text_key = key
            key_path = _json_member_path(path, text_key)
            _charge_scalar(
                text_key,
                path=key_path,
                budget=budget,
                max_scalar_bytes=max_scalar_bytes,
            )
            clone[text_key] = _clone_plain_json(
                child,
                path=key_path,
                depth=depth + 1,
                budget=budget,
                max_nodes=max_nodes,
                max_scalar_bytes=max_scalar_bytes,
            )
        return clone
    raise _PlainJsonIssue(
        path=path,
        detail=(
            "strict in-memory JSON accepts only exact dict/list containers and "
            "exact str/int/float/bool/null scalars"
        ),
    )


def _update_plain_json_digest(digest: _Digest, value: object) -> None:
    """Hash a previously snapshotted JSON tree with unambiguous type markers."""

    update = digest.update
    value_type = type(value)
    if value is None:
        update(b"N")
    elif value_type is bool:
        update(b"B1" if value else b"B0")
    elif value_type is str:
        encoded = cast(str, value).encode("utf-8")
        update(b"S" + len(encoded).to_bytes(8, "big") + encoded)
    elif value_type is int:
        encoded = str(cast(int, value)).encode("ascii")
        update(b"I" + len(encoded).to_bytes(8, "big") + encoded)
    elif value_type is float:
        encoded = cast(float, value).hex().encode("ascii")
        update(b"F" + len(encoded).to_bytes(8, "big") + encoded)
    elif value_type is list:
        sequence = cast(list[object], value)
        update(b"L" + len(sequence).to_bytes(8, "big"))
        for child in sequence:
            _update_plain_json_digest(digest, child)
    else:
        mapping = cast(dict[str, object], value)
        update(b"D" + len(mapping).to_bytes(8, "big"))
        for key in sorted(mapping):
            _update_plain_json_digest(digest, key)
            _update_plain_json_digest(digest, mapping[key])


def _snapshot_plain_json(value: object) -> tuple[object, str, _PlainJsonBudget]:
    budget = _PlainJsonBudget()
    snapshot = _clone_plain_json(
        value,
        path="$",
        depth=0,
        budget=budget,
        max_nodes=MAX_LABELED_JSON_NODES,
        max_scalar_bytes=MAX_LABELED_JSON_SCALAR_BYTES,
    )
    digest = hashlib.sha256()
    _update_plain_json_digest(digest, snapshot)
    return snapshot, digest.hexdigest(), budget


def _validate_labeled_row(
    raw: object,
    *,
    path: str,
    line_number: int,
) -> tuple[LabeledRow, str, _PlainJsonBudget]:
    if type(raw) is not dict:
        raise LabeledDataError(
            LabeledDataErrorCode.ROW_NOT_OBJECT,
            path=path,
            line_number=line_number,
            detail="each non-blank JSONL line must contain one JSON object",
        )

    try:
        snapshot, content_digest, json_budget = _snapshot_plain_json(raw)
    except _PlainJsonIssue as error:
        raise LabeledDataError(
            (
                LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED
                if error.limit
                else LabeledDataErrorCode.INVALID_FIELD_TYPE
            ),
            path=path,
            line_number=line_number,
            field=error.path,
            detail=error.detail,
        ) from error
    normalized = cast(LabeledRow, snapshot)

    for required in ("tab", "human_playable"):
        if required not in normalized:
            raise LabeledDataError(
                LabeledDataErrorCode.MISSING_FIELD,
                path=path,
                line_number=line_number,
                field=required,
                detail="required field is missing",
            )

    tab = normalized["tab"]
    if type(tab) is not dict:
        raise LabeledDataError(
            LabeledDataErrorCode.INVALID_FIELD_TYPE,
            path=path,
            line_number=line_number,
            field="tab",
            detail="must be a JSON object",
        )
    if any(type(key) is not str for key in tab):
        raise LabeledDataError(
            LabeledDataErrorCode.INVALID_FIELD_TYPE,
            path=path,
            line_number=line_number,
            field="tab",
            detail="all tab object keys must be strings",
        )

    # Exact type is deliberate: 0/1 and strings such as "false" are labels with
    # a different schema, not values to coerce using Python truthiness.
    if type(normalized["human_playable"]) is not bool:
        raise LabeledDataError(
            LabeledDataErrorCode.INVALID_FIELD_TYPE,
            path=path,
            line_number=line_number,
            field="human_playable",
            detail="must be a JSON boolean (no truthiness coercion)",
        )
    return normalized, content_digest, json_budget


def _declared_note_count(row: LabeledRow) -> int | None:
    """Return a safely inspectable JSON-style ``tab.notes`` length, if present."""

    tab = row.get("tab")
    if type(tab) is not dict:
        return None
    notes = tab.get("notes")
    if type(notes) is not list:
        return None
    return len(notes)


def _source_location(
    rows: list[LabeledRow],
    *,
    index: int,
    raw_row: object,
    content_digest: str,
) -> tuple[str, int]:
    if type(rows) is _SourcedLabeledRows:
        source = rows.source_for(index, raw_row, content_digest)
        if source is not None:
            return source
    return "<rows>", index + 1


def _checker_work_units(tab: Tab) -> int:
    """Deterministic, deliberately overcharging checker-work envelope.

    The full oracle evaluates up to three profiles.  Each profile performs many
    linear scans and independent sorts, while right-hand validation is pairwise
    within attack frames.  Charging all three profiles avoids the former case
    where a 20k-note, distinct-onset row cost only 40k units despite taking
    seconds and producing nearly 20k diagnostics.
    """

    attacks: dict[object, int] = {}
    for note in tab.notes:
        attacks[note.onset] = attacks.get(note.onset, 0) + 1
    return oracle_checker_work_upper_bound(
        len(tab.notes),
        tuple(attacks.values()),
    )


def load_labeled(path: str) -> list[LabeledRow]:
    """Load and validate JSONL human labels without truthiness coercion.

    Blank lines are ignored, but diagnostics always retain the physical source
    line.  Non-standard NaN/Infinity values and duplicate object keys are
    rejected rather than accepting JSON with ambiguous semantics.
    """

    rows = _SourcedLabeledRows()
    total_bytes = 0
    total_notes = 0
    total_json_nodes = 0
    with open(path, "rb") as file:
        for line_number, physical_line in enumerate(
            iter(lambda: file.readline(MAX_LABELED_JSON_LINE_BYTES + 1), b""),
            start=1,
        ):
            if line_number > MAX_LABELED_PHYSICAL_LINES:
                raise LabeledDataError(
                    LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED,
                    path=path,
                    line_number=line_number,
                    detail=(
                        "physical JSONL line count exceeds the public limit "
                        f"{MAX_LABELED_PHYSICAL_LINES}"
                    ),
                )
            if len(physical_line) > MAX_LABELED_JSON_LINE_BYTES:
                raise LabeledDataError(
                    LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED,
                    path=path,
                    line_number=line_number,
                    detail=(
                        f"JSONL line exceeds the public byte limit {MAX_LABELED_JSON_LINE_BYTES}"
                    ),
                )
            if len(physical_line) > MAX_LABELED_TOTAL_BYTES - total_bytes:
                raise LabeledDataError(
                    LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED,
                    path=path,
                    line_number=line_number,
                    detail=(
                        "cumulative JSONL bytes exceed the public file limit "
                        f"{MAX_LABELED_TOTAL_BYTES}"
                    ),
                )
            total_bytes += len(physical_line)
            try:
                line = physical_line.decode("utf-8")
            except UnicodeDecodeError as error:
                # Match JSONDecodeError's one-based *character* column, not the
                # UTF-8 byte offset exposed by UnicodeDecodeError.
                valid_prefix = physical_line[: error.start].decode("utf-8")
                raise LabeledDataError(
                    LabeledDataErrorCode.INVALID_JSON,
                    path=path,
                    line_number=line_number,
                    column=len(valid_prefix) + 1,
                    detail="line is not valid UTF-8",
                ) from error
            # JSON permits only SP, TAB, CR and LF as whitespace.  Keeping the
            # original text also keeps JSONDecodeError.colno aligned to the
            # physical source line.
            if not line.strip(" \t\r\n"):
                continue
            if len(rows) >= MAX_LABELED_ROWS:
                raise LabeledDataError(
                    LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED,
                    path=path,
                    line_number=line_number,
                    detail=f"labeled row count exceeds limit {MAX_LABELED_ROWS}",
                )
            try:
                raw: object = json.loads(
                    line,
                    parse_int=_parse_bounded_json_int,
                    parse_float=_parse_finite_json_float,
                    parse_constant=_reject_nonstandard_json_constant,
                    object_pairs_hook=_object_without_duplicate_keys,
                )
            except json.JSONDecodeError as error:
                raise LabeledDataError(
                    LabeledDataErrorCode.INVALID_JSON,
                    path=path,
                    line_number=line_number,
                    column=error.colno,
                    detail=error.msg,
                ) from error
            except _InvalidJsonValue as error:
                raise LabeledDataError(
                    LabeledDataErrorCode.INVALID_JSON,
                    path=path,
                    line_number=line_number,
                    detail=str(error),
                ) from error
            except (OverflowError, RecursionError, TypeError, ValueError) as error:
                raise LabeledDataError(
                    LabeledDataErrorCode.INVALID_JSON,
                    path=path,
                    line_number=line_number,
                    detail=f"invalid JSON value: {type(error).__name__}",
                ) from error
            row, content_digest, json_budget = _validate_labeled_row(
                raw,
                path=path,
                line_number=line_number,
            )
            if json_budget.nodes > MAX_LABELED_TOTAL_JSON_NODES - total_json_nodes:
                raise LabeledDataError(
                    LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED,
                    path=path,
                    line_number=line_number,
                    field="$",
                    detail=(
                        "cumulative JSON value count exceeds the public limit "
                        f"{MAX_LABELED_TOTAL_JSON_NODES}"
                    ),
                )
            declared_notes = _declared_note_count(row)
            if (
                declared_notes is not None
                and declared_notes > MAX_LABELED_TOTAL_NOTES - total_notes
            ):
                raise LabeledDataError(
                    LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED,
                    path=path,
                    line_number=line_number,
                    field="tab.notes",
                    detail=(
                        "cumulative declared Tab note count exceeds the public limit "
                        f"{MAX_LABELED_TOTAL_NOTES}"
                    ),
                )
            if declared_notes is not None:
                total_notes += declared_notes
            total_json_nodes += json_budget.nodes
            rows.append_sourced(
                row,
                path=path,
                line_number=line_number,
                content_digest=content_digest,
            )
    return rows


def _tab_from_labeled_row(
    row: LabeledRow,
    *,
    profile: Profile,
    path: str,
    line_number: int,
) -> Tab:
    """Parse one row's Tab while preserving the labeled-row trust boundary."""

    try:
        encoded_tab = json.dumps(row["tab"], allow_nan=False)
    except (OverflowError, RecursionError, TypeError, ValueError) as error:
        raise LabeledDataError(
            LabeledDataErrorCode.INVALID_TAB_SCHEMA,
            path=path,
            line_number=line_number,
            field="tab",
            detail=(f"tab cannot be represented as strict JSON: {type(error).__name__}: {error}"),
        ) from error

    try:
        return validated_tab_from_json(encoded_tab, profile=profile)
    except TabSchemaError as error:
        raise LabeledDataError(
            LabeledDataErrorCode.INVALID_TAB_SCHEMA,
            path=path,
            line_number=line_number,
            field="tab",
            detail=f"{error.code.value} at {error.path}: {error.message}",
        ) from error
    except OracleInputError as error:
        detail = "; ".join(
            f"{diagnostic.code.value} at {diagnostic.path}: {diagnostic.message}"
            for diagnostic in error.diagnostics
        )
        raise LabeledDataError(
            LabeledDataErrorCode.INVALID_ORACLE_INPUT,
            path=path,
            line_number=line_number,
            field="tab",
            detail=detail,
        ) from error


def confusion_from_labeled(
    rows: list[LabeledRow],
    profile: Profile,
) -> ConfusionMatrix:
    """Evaluate labeled rows after re-validating the public in-memory boundary.

    Only an exact built-in list (or the private list returned by
    :func:`load_labeled`) is accepted.  This keeps iteration inert: accepting a
    generic Iterable would execute attacker-controlled ``__iter__`` before the
    trust boundary could fail closed.  Any invalid row raises
    :class:`LabeledDataError`; no partial confusion matrix is returned.
    """

    rows_type = type(rows)
    if rows_type is not list and rows_type is not _SourcedLabeledRows:
        raise LabeledDataError(
            LabeledDataErrorCode.INVALID_FIELD_TYPE,
            path="<rows>",
            line_number=1,
            field="<rows>",
            detail="rows must be an exact built-in list or the result of load_labeled()",
        )

    profile = ensure_profile(profile)

    row_snapshot = tuple(
        list.__getitem__(rows, slice(0, MAX_LABELED_ROWS + 1))
    )
    if len(row_snapshot) > MAX_LABELED_ROWS:
        raise LabeledDataError(
            LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED,
            path="<rows>",
            line_number=MAX_LABELED_ROWS + 1,
            detail=f"labeled row count exceeds limit {MAX_LABELED_ROWS}",
        )

    counts = {
        ("GREEN", True): 0,
        ("GREEN", False): 0,
        ("RED", True): 0,
        ("RED", False): 0,
        ("AMBER", True): 0,
        ("AMBER", False): 0,
    }
    total_json_scalar_bytes = 0
    total_json_nodes = 0
    detached_rows: list[tuple[object, str, int]] = []
    for index, raw_row in enumerate(row_snapshot):
        # First detach every bounded source tree, before per-row schema
        # validation or any checker work can mutate a later source row.
        if type(raw_row) is not dict:
            raise LabeledDataError(
                LabeledDataErrorCode.ROW_NOT_OBJECT,
                path="<rows>",
                line_number=index + 1,
                detail="each row must contain one exact built-in JSON object",
            )
        try:
            detached, content_digest, json_budget = _snapshot_plain_json(raw_row)
        except _PlainJsonIssue as error:
            raise LabeledDataError(
                (
                    LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED
                    if error.limit
                    else LabeledDataErrorCode.INVALID_FIELD_TYPE
                ),
                path="<rows>",
                line_number=index + 1,
                field=error.path,
                detail=error.detail,
            ) from error
        path, line_number = _source_location(
            rows,
            index=index,
            raw_row=raw_row,
            content_digest=content_digest,
        )
        if json_budget.scalar_bytes > MAX_LABELED_TOTAL_BYTES - total_json_scalar_bytes:
            raise LabeledDataError(
                LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED,
                path=path,
                line_number=line_number,
                field="$",
                detail=(
                    "cumulative in-memory JSON scalar bytes exceed the public limit "
                    f"{MAX_LABELED_TOTAL_BYTES}"
                ),
            )
        if json_budget.nodes > MAX_LABELED_TOTAL_JSON_NODES - total_json_nodes:
            raise LabeledDataError(
                LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED,
                path=path,
                line_number=line_number,
                field="$",
                detail=(
                    "cumulative in-memory JSON value count exceeds the public limit "
                    f"{MAX_LABELED_TOTAL_JSON_NODES}"
                ),
            )
        total_json_scalar_bytes += json_budget.scalar_bytes
        total_json_nodes += json_budget.nodes
        detached_rows.append((detached, path, line_number))

    declared_total_notes = 0
    validated_rows: list[tuple[LabeledRow, str, int]] = []
    for detached, path, line_number in detached_rows:
        row, _digest, _budget = _validate_labeled_row(
            detached,
            path=path,
            line_number=line_number,
        )
        declared_notes = _declared_note_count(row)
        if (
            declared_notes is not None
            and declared_notes > MAX_LABELED_TOTAL_NOTES - declared_total_notes
        ):
            raise LabeledDataError(
                LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED,
                path=path,
                line_number=line_number,
                field="tab.notes",
                detail=(
                    f"cumulative Tab note count exceeds the public limit {MAX_LABELED_TOTAL_NOTES}"
                ),
            )
        if declared_notes is not None:
            declared_total_notes += declared_notes
        validated_rows.append((row, path, line_number))

    total_notes = 0
    checker_work = 0
    for row, path, line_number in validated_rows:
        tab = _tab_from_labeled_row(
            row,
            profile=profile,
            path=path,
            line_number=line_number,
        )
        note_count = len(tab.notes)
        if note_count > MAX_LABELED_TOTAL_NOTES - total_notes:
            raise LabeledDataError(
                LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED,
                path=path,
                line_number=line_number,
                field="tab.notes",
                detail=(
                    f"cumulative Tab note count exceeds the public limit {MAX_LABELED_TOTAL_NOTES}"
                ),
            )
        row_work = _checker_work_units(tab)
        if row_work > MAX_LABELED_CHECKER_WORK - checker_work:
            raise LabeledDataError(
                LabeledDataErrorCode.INPUT_LIMIT_EXCEEDED,
                path=path,
                line_number=line_number,
                field="tab",
                detail=(
                    f"cumulative checker work exceeds the public limit {MAX_LABELED_CHECKER_WORK}"
                ),
            )
        total_notes += note_count
        checker_work += row_work
        verdict = check_playability(tab, profile).verdict
        human_label = row["human_playable"]
        # _validate_labeled_row establishes the exact runtime type.  Keeping the
        # branch explicit also protects this boundary from future schema edits.
        if type(human_label) is not bool:  # pragma: no cover - defensive invariant
            raise AssertionError("validated human_playable label is not bool")
        counts[(verdict, human_label)] += 1
    return ConfusionMatrix(
        green_playable=counts[("GREEN", True)],
        green_unplayable=counts[("GREEN", False)],
        red_playable=counts[("RED", True)],
        red_unplayable=counts[("RED", False)],
        amber_playable=counts[("AMBER", True)],
        amber_unplayable=counts[("AMBER", False)],
    )


def clopper_pearson_upper_bound(
    successes: int,
    n: int,
    confidence: float = 0.975,
) -> float | None:
    """One-sided exact binomial upper bound, or ``None`` with no observations."""

    valid_successes, valid_n = _validate_binomial_counts(successes, n)
    valid_confidence = _validate_confidence(confidence)
    if valid_n == 0:
        return None
    if valid_successes == valid_n:
        return 1.0
    return float(beta.ppf(valid_confidence, valid_successes + 1, valid_n - valid_successes))


def green_false_accept_estimate(
    cm: ConfusionMatrix,
    confidence: float = 0.975,
) -> GreenFalseAcceptResult:
    """Return the canonical GREEN false-accept estimate or explicit no-data state."""

    cm = _validate_confusion_matrix(cm)
    valid_confidence = _validate_confidence(confidence)
    x = cm.green_unplayable
    n_green = cm.green_playable + cm.green_unplayable
    upper_bound = clopper_pearson_upper_bound(x, n_green, valid_confidence)
    if n_green == 0:
        return GreenFalseAcceptResult(
            status="no_green",
            x=0,
            n_green=0,
            confidence=valid_confidence,
            observed_rate=None,
            upper_bound=None,
            method="clopper-pearson-one-sided",
        )
    return GreenFalseAcceptResult(
        status="estimated",
        x=x,
        n_green=n_green,
        confidence=valid_confidence,
        observed_rate=x / n_green,
        upper_bound=upper_bound,
        method="clopper-pearson-one-sided",
    )


def green_false_accept_upper_bound(
    cm: ConfusionMatrix,
    conf: float = 0.975,
) -> float | None:
    """Compatibility helper returning only the bound; no GREEN means ``None``."""

    return green_false_accept_estimate(cm, conf).upper_bound


def cohen_kappa_result(cm: ConfusionMatrix) -> CohenKappaResult:
    """Canonical Cohen kappa for certified verdicts, with undefined states.

    GREEN is treated as playable and RED as unplayable.  AMBER is excluded
    because it is not a certified binary verdict.  Integer arithmetic is used
    until the final division so truly degenerate marginals are detected exactly.
    """

    cm = _validate_confusion_matrix(cm)
    true_positive = cm.green_playable
    false_positive = cm.green_unplayable
    false_negative = cm.red_playable
    true_negative = cm.red_unplayable
    total = true_positive + false_positive + false_negative + true_negative
    if total == 0:
        return CohenKappaResult(
            status="undefined",
            value=None,
            n=0,
            reason="no_certified_observations",
        )

    predicted_positive = true_positive + false_positive
    actual_positive = true_positive + false_negative
    expected_scaled = predicted_positive * actual_positive + (total - predicted_positive) * (
        total - actual_positive
    )
    denominator = total * total - expected_scaled
    if denominator == 0:
        return CohenKappaResult(
            status="undefined",
            value=None,
            n=total,
            reason="degenerate_marginals",
        )

    observed_agreement = true_positive + true_negative
    numerator = observed_agreement * total - expected_scaled
    return CohenKappaResult(
        status="estimated",
        value=numerator / denominator,
        n=total,
        reason=None,
    )


def cohen_kappa(cm: ConfusionMatrix) -> float | None:
    """Compatibility helper returning kappa, or ``None`` when it is undefined."""

    return cohen_kappa_result(cm).value


def wilson_ci(successes: int, n: int, conf: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (clamped to [0, 1])."""

    valid_successes, valid_n = _validate_binomial_counts(successes, n)
    valid_confidence = _validate_confidence(conf)
    if valid_n == 0:
        return (0.0, 1.0)
    z = float(norm.ppf(1 - (1 - valid_confidence) / 2))
    phat = valid_successes / valid_n
    denominator = 1 + z * z / valid_n
    center = (phat + z * z / (2 * valid_n)) / denominator
    margin = (z / denominator) * math.sqrt(
        phat * (1 - phat) / valid_n + z * z / (4 * valid_n * valid_n)
    )
    return (max(0.0, center - margin), min(1.0, center + margin))
