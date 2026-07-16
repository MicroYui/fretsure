"""Explainable execution trace: the sequence of plan/oracle/edit/re-check steps.

Consumed by the Plan 6 "watch the agent think" viewer and by benchmark repair
stats. Serializes to JSONL (Fractions render as strings).
"""

import json
import math
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import islice
from typing import Any, Literal, cast

StepKind = Literal[
    "PLAN", "PROPOSE", "SOLVE", "ORACLE", "REASON", "EDIT", "RECHECK", "SELECT"
]

MAX_TRACE_STEPS = 10_000
MAX_TRACE_JSON_DEPTH = 64
MAX_TRACE_JSON_NODES = 250_000
MAX_TRACE_SCALAR_BYTES = 8 * 1024 * 1024
MAX_TRACE_JSONL_BYTES = 10 * 1024 * 1024
MAX_TRACE_INTEGER_BITS = 4096

_STEP_KINDS = frozenset(
    {"PLAN", "PROPOSE", "SOLVE", "ORACLE", "REASON", "EDIT", "RECHECK", "SELECT"}
)


class TraceInputError(ValueError):
    """A typed failure for data that cannot safely enter the public trace."""

    def __init__(self, path: str, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"invalid trace {path}: {detail}")


@dataclass
class _TraceBudget:
    nodes: int = 0
    scalar_bytes: int = 0


def _consume_scalar(value: str, path: str, budget: _TraceBudget) -> None:
    # Reject obviously oversized strings before allocating their UTF-8 form.
    if len(value) > MAX_TRACE_SCALAR_BYTES:
        raise TraceInputError(
            path,
            f"scalar data exceeds the public byte limit {MAX_TRACE_SCALAR_BYTES}",
        )
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise TraceInputError(path, "strings must contain valid Unicode scalar values") from error
    if size > MAX_TRACE_SCALAR_BYTES - budget.scalar_bytes:
        raise TraceInputError(
            path,
            f"scalar data exceeds the public byte limit {MAX_TRACE_SCALAR_BYTES}",
        )
    budget.scalar_bytes += size


def _consume_node(path: str, budget: _TraceBudget) -> None:
    budget.nodes += 1
    if budget.nodes > MAX_TRACE_JSON_NODES:
        raise TraceInputError(
            path,
            f"JSON value count exceeds the public limit {MAX_TRACE_JSON_NODES}",
        )


def _canonical_fraction(value: Fraction, path: str) -> str:
    try:
        numerator = object.__getattribute__(value, "_numerator")
        denominator = object.__getattribute__(value, "_denominator")
    except (AttributeError, TypeError):
        raise TraceInputError(path, "Fraction components are missing") from None
    if type(numerator) is not int or type(denominator) is not int:
        raise TraceInputError(path, "Fractions must contain exact integer components")
    if (
        int.bit_length(numerator) > MAX_TRACE_INTEGER_BITS
        or int.bit_length(denominator) > MAX_TRACE_INTEGER_BITS
    ):
        raise TraceInputError(
            path,
            f"Fraction components exceed the public bit limit {MAX_TRACE_INTEGER_BITS}",
        )
    if denominator <= 0 or math.gcd(abs(numerator), denominator) != 1:
        raise TraceInputError(path, "Fractions must be reduced with a positive denominator")
    try:
        if denominator == 1:
            return str(numerator)
        return f"{numerator}/{denominator}"
    except (OverflowError, ValueError):
        raise TraceInputError(
            path,
            "Fraction components cannot be rendered within the runtime integer limit",
        ) from None


def _normalize_json_value(
    value: object,
    *,
    path: str,
    depth: int,
    budget: _TraceBudget,
    active_containers: set[int],
) -> object:
    if depth > MAX_TRACE_JSON_DEPTH:
        raise TraceInputError(
            path,
            f"JSON nesting exceeds the public depth limit {MAX_TRACE_JSON_DEPTH}",
        )
    _consume_node(path, budget)

    value_type = type(value)
    if value is None or value_type is bool:
        return value
    if value_type is str:
        string_value = cast(str, value)
        _consume_scalar(string_value, path, budget)
        return string_value
    if value_type is int:
        integer_value = cast(int, value)
        if int.bit_length(integer_value) > MAX_TRACE_INTEGER_BITS:
            raise TraceInputError(
                path,
                f"integer exceeds the public bit limit {MAX_TRACE_INTEGER_BITS}",
            )
        return integer_value
    if value_type is float:
        float_value = cast(float, value)
        if not math.isfinite(float_value):
            raise TraceInputError(path, "numbers must be finite")
        return float_value
    if value_type is Fraction:
        canonical = _canonical_fraction(cast(Fraction, value), path)
        _consume_scalar(canonical, path, budget)
        return canonical

    if value_type is dict:
        mapping = cast(dict[object, object], value)
        container_id = id(value)
        if container_id in active_containers:
            raise TraceInputError(path, "cyclic containers are not accepted")
        if len(mapping) > MAX_TRACE_JSON_NODES - budget.nodes:
            raise TraceInputError(
                path,
                f"JSON value count exceeds the public limit {MAX_TRACE_JSON_NODES}",
            )
        remaining_nodes = MAX_TRACE_JSON_NODES - budget.nodes
        try:
            dict_items: tuple[tuple[object, object], ...] = tuple(
                islice(dict.items(mapping), remaining_nodes + 1)
            )
        except RuntimeError as error:
            raise TraceInputError(path, "mapping changed while it was being read") from error
        if len(dict_items) > MAX_TRACE_JSON_NODES - budget.nodes:
            raise TraceInputError(
                path,
                f"JSON value count exceeds the public limit {MAX_TRACE_JSON_NODES}",
            )
        active_containers.add(container_id)
        try:
            normalized: dict[str, object] = {}
            for index, (key, item) in enumerate(dict_items):
                if type(key) is not str:
                    raise TraceInputError(
                        f"{path}.key[{index}]",
                        "JSON object keys must be exact strings",
                    )
                normalized_key = cast(
                    str,
                    _normalize_json_value(
                        key,
                        path=f"{path}.key[{index}]",
                        depth=depth + 1,
                        budget=budget,
                        active_containers=active_containers,
                    ),
                )
                normalized[normalized_key] = _normalize_json_value(
                    item,
                    path=f"{path}.value[{index}]",
                    depth=depth + 1,
                    budget=budget,
                    active_containers=active_containers,
                )
            return normalized
        finally:
            active_containers.remove(container_id)

    if value_type is list or value_type is tuple:
        sequence = cast(list[object] | tuple[object, ...], value)
        container_id = id(value)
        if container_id in active_containers:
            raise TraceInputError(path, "cyclic containers are not accepted")
        if len(sequence) > MAX_TRACE_JSON_NODES - budget.nodes:
            raise TraceInputError(
                path,
                f"JSON value count exceeds the public limit {MAX_TRACE_JSON_NODES}",
            )
        remaining_nodes = MAX_TRACE_JSON_NODES - budget.nodes
        sequence_items = tuple(sequence[: remaining_nodes + 1])
        if len(sequence_items) > MAX_TRACE_JSON_NODES - budget.nodes:
            raise TraceInputError(
                path,
                f"JSON value count exceeds the public limit {MAX_TRACE_JSON_NODES}",
            )
        active_containers.add(container_id)
        try:
            return [
                _normalize_json_value(
                    item,
                    path=f"{path}[{index}]",
                    depth=depth + 1,
                    budget=budget,
                    active_containers=active_containers,
                )
                for index, item in enumerate(sequence_items)
            ]
        finally:
            active_containers.remove(container_id)

    raise TraceInputError(
        path,
        "only null, booleans, finite numbers, strings, Fractions, lists, tuples, "
        "and string-keyed dictionaries are accepted",
    )


def _json_string_size(value: str, path: str, *, remaining: int) -> int:
    size = 2  # quotes
    if size > remaining:
        raise TraceInputError(path, "JSONL output exceeds the public byte limit")
    for character in value:
        codepoint = ord(character)
        if character in {'"', "\\", "\b", "\f", "\n", "\r", "\t"}:
            size += 2
        elif codepoint < 0x20:
            size += 6
        elif codepoint <= 0x7F:
            size += 1
        elif codepoint <= 0x7FF:
            size += 2
        elif codepoint <= 0xFFFF:
            size += 3
        else:
            size += 4
        if size > remaining:
            raise TraceInputError(path, "JSONL output exceeds the public byte limit")
    return size


def _json_encoded_size(value: object, path: str, *, remaining: int) -> int:
    """Return exact compact UTF-8 JSON size without allocating encoded output."""

    value_type = type(value)
    if value is None:
        size = 4
    elif value_type is bool:
        size = 4 if value else 5
    elif value_type is str:
        return _json_string_size(cast(str, value), path, remaining=remaining)
    elif value_type is int:
        try:
            size = len(str(cast(int, value)))
        except (OverflowError, ValueError):
            raise TraceInputError(
                path,
                "integer cannot be rendered within the runtime limit",
            ) from None
    elif value_type is float:
        size = len(repr(cast(float, value)))
    elif value_type is list:
        sequence = cast(list[object], value)
        total = 2 + max(0, len(sequence) - 1)
        if total > remaining:
            raise TraceInputError(path, "JSONL output exceeds the public byte limit")
        for index, child in enumerate(sequence):
            total += _json_encoded_size(
                child,
                f"{path}[{index}]",
                remaining=remaining - total,
            )
        return total
    elif value_type is dict:
        mapping = cast(dict[str, object], value)
        total = 2 + max(0, len(mapping) - 1)
        if total > remaining:
            raise TraceInputError(path, "JSONL output exceeds the public byte limit")
        for index, (key, child) in enumerate(mapping.items()):
            total += _json_string_size(
                key,
                f"{path}.key[{index}]",
                remaining=remaining - total,
            )
            if total + 1 > remaining:
                raise TraceInputError(path, "JSONL output exceeds the public byte limit")
            total += 1  # colon
            total += _json_encoded_size(
                child,
                f"{path}.value[{index}]",
                remaining=remaining - total,
            )
        return total
    else:  # pragma: no cover - normalization establishes this invariant
        raise TraceInputError(path, "normalized value is not JSON-compatible")
    if size > remaining:
        raise TraceInputError(path, "JSONL output exceeds the public byte limit")
    return size


@dataclass(frozen=True)
class TraceStep:
    kind: StepKind
    detail: str
    data: dict[str, Any]


@dataclass
class Trace:
    steps: list[TraceStep] = field(default_factory=list)

    def add(self, kind: StepKind, detail: str, **data: Any) -> None:
        self.steps.append(TraceStep(kind, detail, data))

    def to_jsonl(self) -> str:
        try:
            raw_steps = object.__getattribute__(self, "steps")
        except (AttributeError, TypeError) as error:
            raise TraceInputError("steps", "field is missing") from error
        if type(raw_steps) is not list:
            raise TraceInputError("steps", "must be an exact list")
        if len(raw_steps) > MAX_TRACE_STEPS:
            raise TraceInputError(
                "steps",
                f"step count exceeds the public limit {MAX_TRACE_STEPS}",
            )
        steps = tuple(raw_steps[: MAX_TRACE_STEPS + 1])
        if len(steps) > MAX_TRACE_STEPS:
            raise TraceInputError(
                "steps",
                f"step count exceeds the public limit {MAX_TRACE_STEPS}",
            )

        budget = _TraceBudget()
        lines: list[str] = []
        encoded_bytes = 0
        for index, step in enumerate(steps):
            step_path = f"steps[{index}]"
            if type(step) is not TraceStep:
                raise TraceInputError(step_path, "must be an exact TraceStep")
            try:
                kind = object.__getattribute__(step, "kind")
                detail = object.__getattribute__(step, "detail")
                data = object.__getattribute__(step, "data")
            except (AttributeError, TypeError) as error:
                raise TraceInputError(step_path, "TraceStep fields are missing") from error
            if type(kind) is not str or kind not in _STEP_KINDS:
                raise TraceInputError(f"{step_path}.kind", "must be a standard trace-step kind")
            if type(detail) is not str:
                raise TraceInputError(f"{step_path}.detail", "must be an exact string")
            normalized_kind = _normalize_json_value(
                kind,
                path=f"{step_path}.kind",
                depth=0,
                budget=budget,
                active_containers=set(),
            )
            normalized_detail = _normalize_json_value(
                detail,
                path=f"{step_path}.detail",
                depth=0,
                budget=budget,
                active_containers=set(),
            )
            normalized_data = _normalize_json_value(
                data,
                path=f"{step_path}.data",
                depth=0,
                budget=budget,
                active_containers=set(),
            )
            payload = {
                "kind": normalized_kind,
                "detail": normalized_detail,
                "data": normalized_data,
            }
            separator_bytes = 1 if lines else 0
            remaining = MAX_TRACE_JSONL_BYTES - encoded_bytes - separator_bytes
            expected_line_bytes = _json_encoded_size(
                payload,
                step_path,
                remaining=remaining,
            )
            try:
                line = json.dumps(
                    payload,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                line_bytes = len(line.encode("utf-8"))
            except (TypeError, ValueError, OverflowError, UnicodeError) as error:
                raise TraceInputError(step_path, "could not be encoded as standard JSON") from error
            if line_bytes != expected_line_bytes:
                raise TraceInputError(
                    step_path,
                    "JSON encoder size disagrees with the canonical preflight",
                )
            if line_bytes + separator_bytes > MAX_TRACE_JSONL_BYTES - encoded_bytes:
                raise TraceInputError(
                    step_path,
                    f"JSONL output exceeds the public byte limit {MAX_TRACE_JSONL_BYTES}",
                )
            encoded_bytes += line_bytes + separator_bytes
            lines.append(line)
        return "\n".join(lines)
