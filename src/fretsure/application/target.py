"""Strict, bounded JSON contract for solver target notes.

The arrangement model's internal parser is deliberately permissive because it
must recover from model prose.  Public MCP/API callers need the opposite
contract: exact JSON types, canonical fractions, no duplicate/unknown fields,
and hard resource ceilings before solver work begins.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from typing import cast

from fretsure.ir import MAX_IR_FRACTION_COMPONENT_BITS, MAX_IR_NOTES, Note, VoiceRole

TARGET_INPUT_SCHEMA_VERSION = "target-input@0.1.0"
MAX_TARGET_JSON_BYTES = 10 * 1024 * 1024
MAX_TARGET_JSON_DEPTH = 64
MAX_TARGET_JSON_NODES = 250_000
MAX_TARGET_FRACTION_TOKEN_CHARS = 128
MAX_TARGET_INTEGER_TOKEN_CHARS = 128

_ROOT_FIELDS = ("notes",)
_NOTE_FIELDS = ("onset", "duration", "pitch", "voice")
_VOICES = frozenset({"melody", "bass", "harmony"})
_PATH_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_CANONICAL_FRACTION = re.compile(
    r"(?P<numerator>0|-?[1-9][0-9]*)/(?P<denominator>[1-9][0-9]*)\Z"
)


class TargetInputCode(StrEnum):
    """Stable machine-readable target-boundary failures."""

    INVALID_PAYLOAD_TYPE = "INVALID_PAYLOAD_TYPE"
    INPUT_LIMIT_EXCEEDED = "INPUT_LIMIT_EXCEEDED"
    MALFORMED_JSON = "MALFORMED_JSON"
    NON_FINITE_NUMBER = "NON_FINITE_NUMBER"
    DUPLICATE_KEY = "DUPLICATE_KEY"
    INVALID_TYPE = "INVALID_TYPE"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    INVALID_FRACTION = "INVALID_FRACTION"
    INVALID_VALUE = "INVALID_VALUE"


class TargetInputError(ValueError):
    """Typed, location-bearing rejection for target JSON."""

    def __init__(self, code: TargetInputCode, path: str, detail: str) -> None:
        self.code = code
        self.path = path
        self.detail = detail
        super().__init__(f"{code.value} at {path}: {detail}")


class _JSONObjectPairs:
    __slots__ = ("pairs",)

    def __init__(self, pairs: list[tuple[str, object]]) -> None:
        self.pairs = tuple(pairs)


class _JSONInteger:
    __slots__ = ("token",)

    def __init__(self, token: str) -> None:
        self.token = token


class _JSONFloat:
    __slots__ = ("token",)

    def __init__(self, token: str) -> None:
        self.token = token


class _JSONConstant:
    __slots__ = ()


@dataclass(slots=True)
class _JSONBudget:
    nodes: int = 0


def _field_path(parent: str, key: str) -> str:
    if _PATH_IDENTIFIER.fullmatch(key):
        return f"{parent}.{key}"
    return f"{parent}[{json.dumps(key, ensure_ascii=True)}]"


def _materialize(
    value: object,
    path: str,
    *,
    depth: int,
    budget: _JSONBudget,
) -> object:
    if depth > MAX_TARGET_JSON_DEPTH:
        raise TargetInputError(
            TargetInputCode.INPUT_LIMIT_EXCEEDED,
            path,
            f"JSON nesting exceeds depth limit {MAX_TARGET_JSON_DEPTH}",
        )
    budget.nodes += 1
    if budget.nodes > MAX_TARGET_JSON_NODES:
        raise TargetInputError(
            TargetInputCode.INPUT_LIMIT_EXCEEDED,
            path,
            f"JSON value count exceeds limit {MAX_TARGET_JSON_NODES}",
        )
    if isinstance(value, _JSONConstant):
        raise TargetInputError(
            TargetInputCode.NON_FINITE_NUMBER,
            path,
            "non-finite JSON numbers are not allowed",
        )
    if isinstance(value, _JSONFloat):
        if len(value.token) > MAX_TARGET_INTEGER_TOKEN_CHARS:
            raise TargetInputError(
                TargetInputCode.INPUT_LIMIT_EXCEEDED,
                path,
                "JSON number token exceeds character limit",
            )
        try:
            finite = math.isfinite(float(value.token))
        except (OverflowError, ValueError):
            finite = False
        if not finite:
            raise TargetInputError(
                TargetInputCode.NON_FINITE_NUMBER,
                path,
                "non-finite JSON numbers are not allowed",
            )
        # Keep a sentinel rather than a Python float: every target numeric field
        # is either a canonical fraction string or an exact JSON integer.
        return value
    if isinstance(value, _JSONInteger):
        if len(value.token) > MAX_TARGET_INTEGER_TOKEN_CHARS:
            raise TargetInputError(
                TargetInputCode.INPUT_LIMIT_EXCEEDED,
                path,
                "JSON integer token exceeds character limit",
            )
        try:
            return int(value.token)
        except ValueError:
            raise TargetInputError(
                TargetInputCode.INPUT_LIMIT_EXCEEDED,
                path,
                "JSON integer exceeds runtime conversion limit",
            ) from None
    if isinstance(value, _JSONObjectPairs):
        result: dict[str, object] = {}
        for key, child in value.pairs:
            child_path = _field_path(path, key)
            if key in result:
                raise TargetInputError(
                    TargetInputCode.DUPLICATE_KEY,
                    child_path,
                    "object key occurs more than once",
                )
            if path == "$" and key == "notes" and type(child) is list:
                if len(child) > MAX_IR_NOTES:
                    raise TargetInputError(
                        TargetInputCode.INPUT_LIMIT_EXCEEDED,
                        child_path,
                        "target note count exceeds limit",
                    )
            result[key] = _materialize(
                child,
                child_path,
                depth=depth + 1,
                budget=budget,
            )
        return result
    if type(value) is list:
        sequence = cast(list[object], value)
        return [
            _materialize(
                child,
                f"{path}[{index}]",
                depth=depth + 1,
                budget=budget,
            )
            for index, child in enumerate(sequence)
        ]
    return value


def _require_object(value: object, path: str) -> dict[str, object]:
    if type(value) is not dict:
        raise TargetInputError(TargetInputCode.INVALID_TYPE, path, "expected object")
    return cast(dict[str, object], value)


def _require_array(value: object, path: str) -> list[object]:
    if type(value) is not list:
        raise TargetInputError(TargetInputCode.INVALID_TYPE, path, "expected array")
    return cast(list[object], value)


def _require_integer(value: object, path: str) -> int:
    if type(value) is not int:
        raise TargetInputError(TargetInputCode.INVALID_TYPE, path, "expected integer")
    return value


def _require_string(value: object, path: str) -> str:
    if type(value) is not str:
        raise TargetInputError(TargetInputCode.INVALID_TYPE, path, "expected string")
    return value


def _check_fields(
    value: dict[str, object], path: str, required: tuple[str, ...]
) -> None:
    allowed = frozenset(required)
    for key in value:
        if key not in allowed:
            raise TargetInputError(
                TargetInputCode.UNKNOWN_FIELD,
                _field_path(path, key),
                "field is not allowed",
            )
    for key in required:
        if key not in value:
            raise TargetInputError(
                TargetInputCode.MISSING_FIELD,
                _field_path(path, key),
                "required field is missing",
            )


def _fraction(value: object, path: str, *, positive: bool) -> Fraction:
    token = _require_string(value, path)
    if len(token) > MAX_TARGET_FRACTION_TOKEN_CHARS:
        raise TargetInputError(
            TargetInputCode.INPUT_LIMIT_EXCEEDED,
            path,
            "fraction token exceeds character limit",
        )
    match = _CANONICAL_FRACTION.fullmatch(token)
    if match is None:
        raise TargetInputError(
            TargetInputCode.INVALID_FRACTION,
            path,
            "expected reduced num/positive-den canonical form",
        )
    numerator = int(match.group("numerator"))
    denominator = int(match.group("denominator"))
    if (
        numerator.bit_length() > MAX_IR_FRACTION_COMPONENT_BITS
        or denominator.bit_length() > MAX_IR_FRACTION_COMPONENT_BITS
    ):
        raise TargetInputError(
            TargetInputCode.INPUT_LIMIT_EXCEEDED,
            path,
            "fraction component exceeds bit-length limit",
        )
    fraction = Fraction(numerator, denominator)
    if f"{fraction.numerator}/{fraction.denominator}" != token:
        raise TargetInputError(
            TargetInputCode.INVALID_FRACTION,
            path,
            "expected reduced num/positive-den canonical form",
        )
    if (positive and fraction <= 0) or (not positive and fraction < 0):
        relation = "positive" if positive else "non-negative"
        raise TargetInputError(
            TargetInputCode.INVALID_VALUE,
            path,
            f"must be {relation}",
        )
    return fraction


def _fraction_to_token(value: object, path: str, *, positive: bool) -> tuple[Fraction, str]:
    if type(value) is not Fraction:
        raise TargetInputError(
            TargetInputCode.INVALID_TYPE,
            path,
            "expected exact Fraction",
        )
    try:
        numerator = object.__getattribute__(value, "_numerator")
        denominator = object.__getattribute__(value, "_denominator")
    except (AttributeError, TypeError):
        raise TargetInputError(
            TargetInputCode.INVALID_FRACTION,
            path,
            "Fraction components are missing",
        ) from None
    if type(numerator) is not int or type(denominator) is not int:
        raise TargetInputError(
            TargetInputCode.INVALID_TYPE,
            path,
            "Fraction components must be exact integers",
        )
    if (
        numerator.bit_length() > MAX_IR_FRACTION_COMPONENT_BITS
        or denominator.bit_length() > MAX_IR_FRACTION_COMPONENT_BITS
    ):
        raise TargetInputError(
            TargetInputCode.INPUT_LIMIT_EXCEEDED,
            path,
            "fraction component exceeds bit-length limit",
        )
    if denominator <= 0 or math.gcd(abs(numerator), denominator) != 1:
        raise TargetInputError(
            TargetInputCode.INVALID_FRACTION,
            path,
            "expected reduced num/positive-den canonical form",
        )
    if (positive and numerator <= 0) or (not positive and numerator < 0):
        relation = "positive" if positive else "non-negative"
        raise TargetInputError(TargetInputCode.INVALID_VALUE, path, f"must be {relation}")
    token = f"{numerator}/{denominator}"
    if len(token) > MAX_TARGET_FRACTION_TOKEN_CHARS:
        raise TargetInputError(
            TargetInputCode.INPUT_LIMIT_EXCEEDED,
            path,
            "fraction token exceeds character limit",
        )
    return Fraction(numerator, denominator), token


def target_from_json(payload: str) -> tuple[Note, ...]:
    """Parse and detach one public solver target.

    Textual whitespace and object-key order are insignificant JSON syntax; all
    represented scalar values are canonical and no implicit coercion occurs.
    """

    if type(payload) is not str:
        raise TargetInputError(
            TargetInputCode.INVALID_PAYLOAD_TYPE,
            "$",
            "expected JSON text as str",
        )
    try:
        payload_size = len(payload.encode("utf-8"))
    except UnicodeEncodeError:
        raise TargetInputError(
            TargetInputCode.MALFORMED_JSON,
            "$",
            "JSON text is not valid Unicode",
        ) from None
    if payload_size > MAX_TARGET_JSON_BYTES:
        raise TargetInputError(
            TargetInputCode.INPUT_LIMIT_EXCEEDED,
            "$",
            "target JSON exceeds byte limit",
        )

    try:
        decoded = json.loads(
            payload,
            object_pairs_hook=_JSONObjectPairs,
            parse_int=_JSONInteger,
            parse_float=_JSONFloat,
            parse_constant=lambda _token: _JSONConstant(),
        )
    except RecursionError:
        raise TargetInputError(
            TargetInputCode.INPUT_LIMIT_EXCEEDED,
            "$",
            "JSON nesting exceeds runtime limit",
        ) from None
    except (json.JSONDecodeError, OverflowError, TypeError, ValueError):
        raise TargetInputError(
            TargetInputCode.MALFORMED_JSON,
            "$",
            "invalid JSON syntax",
        ) from None

    try:
        root = _require_object(
            _materialize(decoded, "$", depth=0, budget=_JSONBudget()),
            "$",
        )
    except RecursionError:
        raise TargetInputError(
            TargetInputCode.INPUT_LIMIT_EXCEEDED,
            "$",
            "JSON nesting exceeds runtime limit",
        ) from None
    _check_fields(root, "$", _ROOT_FIELDS)
    raw_notes = _require_array(root["notes"], "$.notes")
    if len(raw_notes) > MAX_IR_NOTES:
        raise TargetInputError(
            TargetInputCode.INPUT_LIMIT_EXCEEDED,
            "$.notes",
            "target note count exceeds limit",
        )

    notes: list[Note] = []
    seen: set[tuple[Fraction, int]] = set()
    for index, value in enumerate(raw_notes):
        path = f"$.notes[{index}]"
        item = _require_object(value, path)
        _check_fields(item, path, _NOTE_FIELDS)
        onset = _fraction(item["onset"], f"{path}.onset", positive=False)
        duration = _fraction(item["duration"], f"{path}.duration", positive=True)
        pitch = _require_integer(item["pitch"], f"{path}.pitch")
        if not 0 <= pitch <= 127:
            raise TargetInputError(
                TargetInputCode.INVALID_VALUE,
                f"{path}.pitch",
                "must be a MIDI integer in 0..127",
            )
        voice = _require_string(item["voice"], f"{path}.voice")
        if voice not in _VOICES:
            raise TargetInputError(
                TargetInputCode.INVALID_VALUE,
                f"{path}.voice",
                "must be melody, bass, or harmony",
            )
        identity = (onset, pitch)
        if identity in seen:
            raise TargetInputError(
                TargetInputCode.INVALID_VALUE,
                path,
                "duplicate onset/pitch target is ambiguous",
            )
        seen.add(identity)
        notes.append(Note(onset, duration, pitch, cast(VoiceRole, voice)))
    return tuple(sorted(notes, key=lambda note: (note.onset, note.pitch)))


def target_to_json(notes: tuple[Note, ...]) -> str:
    """Serialize exact target notes to deterministic accepted JSON."""

    if type(notes) is not tuple:
        raise TargetInputError(
            TargetInputCode.INVALID_TYPE,
            "$.notes",
            "expected tuple of Note objects",
        )
    if len(notes) > MAX_IR_NOTES:
        raise TargetInputError(
            TargetInputCode.INPUT_LIMIT_EXCEEDED,
            "$.notes",
            "target note count exceeds limit",
        )
    normalized: list[tuple[Fraction, Fraction, int, VoiceRole]] = []
    seen: set[tuple[Fraction, int]] = set()
    for index, note in enumerate(notes):
        path = f"$.notes[{index}]"
        if type(note) is not Note:
            raise TargetInputError(TargetInputCode.INVALID_TYPE, path, "expected Note")
        try:
            onset = object.__getattribute__(note, "onset")
            duration = object.__getattribute__(note, "duration")
            pitch = object.__getattribute__(note, "pitch")
            voice = object.__getattribute__(note, "voice")
        except (AttributeError, TypeError):
            raise TargetInputError(
                TargetInputCode.MISSING_FIELD,
                path,
                "Note fields are missing",
            ) from None
        onset_snapshot, _ = _fraction_to_token(
            onset,
            f"{path}.onset",
            positive=False,
        )
        duration_snapshot, _ = _fraction_to_token(
            duration,
            f"{path}.duration",
            positive=True,
        )
        if type(pitch) is not int or not 0 <= pitch <= 127:
            raise TargetInputError(
                TargetInputCode.INVALID_VALUE,
                f"{path}.pitch",
                "must be a MIDI integer in 0..127",
            )
        if type(voice) is not str or voice not in _VOICES:
            raise TargetInputError(
                TargetInputCode.INVALID_VALUE,
                f"{path}.voice",
                "must be melody, bass, or harmony",
            )
        identity = (onset_snapshot, pitch)
        if identity in seen:
            raise TargetInputError(
                TargetInputCode.INVALID_VALUE,
                path,
                "duplicate onset/pitch target is ambiguous",
            )
        seen.add(identity)
        normalized.append(
            (onset_snapshot, duration_snapshot, pitch, cast(VoiceRole, voice))
        )

    wire_notes: list[dict[str, object]] = []
    for onset, duration, pitch, voice in sorted(
        normalized,
        key=lambda item: (item[0], item[2]),
    ):
        wire_notes.append(
            {
                "onset": f"{onset.numerator}/{onset.denominator}",
                "duration": f"{duration.numerator}/{duration.denominator}",
                "pitch": pitch,
                "voice": voice,
            }
        )
    encoded = json.dumps(
        {"notes": wire_notes},
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    )
    if len(encoded) > MAX_TARGET_JSON_BYTES:
        raise TargetInputError(
            TargetInputCode.INPUT_LIMIT_EXCEEDED,
            "$",
            "target JSON exceeds byte limit",
        )
    return encoded


__all__ = [
    "MAX_TARGET_FRACTION_TOKEN_CHARS",
    "MAX_TARGET_INTEGER_TOKEN_CHARS",
    "MAX_TARGET_JSON_DEPTH",
    "MAX_TARGET_JSON_BYTES",
    "MAX_TARGET_JSON_NODES",
    "TARGET_INPUT_SCHEMA_VERSION",
    "TargetInputCode",
    "TargetInputError",
    "target_from_json",
    "target_to_json",
]
