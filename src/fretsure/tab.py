"""Guitar tablature representation.

A :class:`Tab` is a fully-fingered arrangement the oracle *verifies*. The
fingering solver (Plan 2) reverse-searches assignments; this module only
represents and (de)serializes them.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from fretsure.oracle.profiles import Profile

# Public, hard ceilings for the untrusted JSON boundary.  They intentionally
# align the note count with the current importer envelope while independently
# bounding the wire representation and arbitrary-precision rational work.
MAX_TAB_JSON_BYTES = 10 * 1024 * 1024
MAX_TAB_NOTES = 20_000
MAX_FRACTION_TOKEN_CHARS = 128
MAX_JSON_INTEGER_TOKEN_CHARS = 128
MAX_FRACTION_COMPONENT_BITS = 256

# A JSON string cannot contain more source code points than the whole encoded
# payload has bytes: even an unescaped character costs one byte.  Keeping this
# explicit lets the serializer reject a mutated, oversized scalar before it
# scans it or hands it to ``json.dumps``.
MAX_JSON_STRING_CHARS = MAX_TAB_JSON_BYTES

# Converting an arbitrary-precision int to decimal is itself attacker-controlled
# work.  This bit ceiling admits every positive integer whose canonical decimal
# token fits the public character limit; the exact token-length check below also
# accounts for a negative sign.
_MAX_JSON_INTEGER_COMPONENT_BITS = (10**MAX_JSON_INTEGER_TOKEN_CHARS - 1).bit_length()


class TabSchemaCode(StrEnum):
    """Stable machine-readable failure codes for Tab JSON."""

    INVALID_PAYLOAD_TYPE = "INVALID_PAYLOAD_TYPE"
    INPUT_LIMIT_EXCEEDED = "INPUT_LIMIT_EXCEEDED"
    MALFORMED_JSON = "MALFORMED_JSON"
    NON_FINITE_NUMBER = "NON_FINITE_NUMBER"
    DUPLICATE_KEY = "DUPLICATE_KEY"
    INVALID_TYPE = "INVALID_TYPE"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    MISSING_FIELD = "MISSING_FIELD"
    INVALID_FRACTION = "INVALID_FRACTION"


class TabSchemaError(ValueError):
    """Typed, location-bearing rejection at the Tab JSON boundary.

    ``code``, ``path`` and ``message`` are deliberately separate so callers can
    return a structured diagnostic without parsing exception text.
    """

    def __init__(self, code: TabSchemaCode, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(f"{code.value} at {path}: {message}")

RightFinger = Literal["p", "i", "m", "a"]  # thumb / index / middle / ring


@dataclass(frozen=True)
class TabNote:
    onset: Fraction
    duration: Fraction
    string: int  # 0 = lowest-pitched string (6th, low E) .. 5 = highest (1st, high E)
    fret: int  # 0 = open
    left_finger: int  # 0..4, 0 = open
    right_finger: RightFinger


@dataclass(frozen=True)
class Tab:
    notes: tuple[TabNote, ...]
    tuning: tuple[int, ...]  # open-string MIDI, low -> high
    capo: int  # capo fret, 0 = none


# A Frame is the set of TabNotes sounding at one onset — the oracle/solver unit.
Frame = tuple[TabNote, ...]

_ROOT_FIELDS = ("tuning", "capo", "notes")
_NOTE_FIELDS = ("onset", "duration", "string", "fret", "left_finger", "right_finger")
_PATH_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_CANONICAL_FRACTION = re.compile(
    r"(?P<numerator>0|-?[1-9][0-9]*)/(?P<denominator>[1-9][0-9]*)\Z"
)
_TAB_JSON_FIXED_BYTES = len('{"tuning":[],"capo":,"notes":[]}')
_NOTE_JSON_FIXED_BYTES = len(
    '{"onset":,"duration":,"string":,"fret":,"left_finger":,"right_finger":}'
)


class _JSONObjectPairs:
    """Keep object pairs until paths are known, so duplicate errors are located."""

    __slots__ = ("pairs",)

    def __init__(self, pairs: list[tuple[str, object]]) -> None:
        self.pairs = tuple(pairs)


class _JSONInteger:
    __slots__ = ("token",)

    def __init__(self, token: str) -> None:
        self.token = token


class _NonFiniteJSONNumber:
    __slots__ = ()


def _parse_json_float(token: str) -> float | _NonFiniteJSONNumber:
    value = float(token)
    if not math.isfinite(value):
        return _NonFiniteJSONNumber()
    return value


def _parse_json_constant(_token: str) -> _NonFiniteJSONNumber:
    # CPython's decoder accepts NaN/Infinity by default even though JSON does
    # not.  A sentinel lets us report its eventual JSON path.
    return _NonFiniteJSONNumber()


def _field_path(parent: str, key: str) -> str:
    if _PATH_IDENTIFIER.fullmatch(key):
        return f"{parent}.{key}"
    return f"{parent}[{json.dumps(key, ensure_ascii=True)}]"


def _materialize_json(value: object, path: str) -> object:
    """Turn decoder sentinels into plain values while retaining exact paths."""
    if isinstance(value, _NonFiniteJSONNumber):
        raise TabSchemaError(
            TabSchemaCode.NON_FINITE_NUMBER,
            path,
            "non-finite JSON numbers are not allowed",
        )
    if isinstance(value, _JSONInteger):
        if len(value.token) > MAX_JSON_INTEGER_TOKEN_CHARS:
            raise TabSchemaError(
                TabSchemaCode.INPUT_LIMIT_EXCEEDED,
                path,
                "JSON integer token exceeds character limit",
            )
        try:
            return int(value.token)
        except ValueError:
            raise TabSchemaError(
                TabSchemaCode.INPUT_LIMIT_EXCEEDED,
                path,
                "JSON integer exceeds runtime conversion limit",
            ) from None
    if isinstance(value, _JSONObjectPairs):
        result: dict[str, object] = {}
        for key, child in value.pairs:
            child_path = _field_path(path, key)
            if key in result:
                raise TabSchemaError(
                    TabSchemaCode.DUPLICATE_KEY,
                    child_path,
                    "object key occurs more than once",
                )
            if path == "$" and key == "notes" and isinstance(child, list):
                if len(child) > MAX_TAB_NOTES:
                    raise TabSchemaError(
                        TabSchemaCode.INPUT_LIMIT_EXCEEDED,
                        child_path,
                        "Tab note count exceeds limit",
                    )
            result[key] = _materialize_json(child, child_path)
        return result
    if isinstance(value, list):
        return [_materialize_json(child, f"{path}[{index}]") for index, child in enumerate(value)]
    return value


def _require_object(value: object, path: str) -> dict[str, object]:
    if type(value) is not dict:
        raise TabSchemaError(TabSchemaCode.INVALID_TYPE, path, "expected object")
    return cast(dict[str, object], value)


def _require_array(value: object, path: str) -> list[object]:
    if type(value) is not list:
        raise TabSchemaError(TabSchemaCode.INVALID_TYPE, path, "expected array")
    return cast(list[object], value)


def _require_integer(value: object, path: str) -> int:
    # bool is an int subclass in Python; exact type is required at the boundary.
    if type(value) is not int:
        raise TabSchemaError(TabSchemaCode.INVALID_TYPE, path, "expected integer")
    return value


def _require_string(value: object, path: str) -> str:
    if type(value) is not str:
        raise TabSchemaError(TabSchemaCode.INVALID_TYPE, path, "expected string")
    return value


def _read_serialization_field(value: object, name: str, path: str) -> object:
    """Read an exact dataclass field without trusting a user-supplied hook."""
    try:
        return object.__getattribute__(value, name)
    except (AttributeError, TypeError):
        raise TabSchemaError(
            TabSchemaCode.MISSING_FIELD,
            path,
            "required field is missing",
        ) from None


def _integer_to_token(value: object, path: str) -> tuple[int, str]:
    """Return a safe integer and its bounded canonical JSON token."""
    integer = _require_integer(value, path)
    if integer.bit_length() > _MAX_JSON_INTEGER_COMPONENT_BITS:
        raise TabSchemaError(
            TabSchemaCode.INPUT_LIMIT_EXCEEDED,
            path,
            "JSON integer token exceeds character limit",
        )
    try:
        token = str(integer)
    except (ValueError, OverflowError):
        raise TabSchemaError(
            TabSchemaCode.INPUT_LIMIT_EXCEEDED,
            path,
            "JSON integer exceeds runtime conversion limit",
        ) from None
    if len(token) > MAX_JSON_INTEGER_TOKEN_CHARS:
        raise TabSchemaError(
            TabSchemaCode.INPUT_LIMIT_EXCEEDED,
            path,
            "JSON integer token exceeds character limit",
        )
    return integer, token


def _serialized_size_error() -> TabSchemaError:
    return TabSchemaError(
        TabSchemaCode.INPUT_LIMIT_EXCEEDED,
        "$",
        "serialized Tab JSON exceeds byte limit",
    )


def _canonical_json_string_bytes(
    value: object,
    path: str,
    *,
    remaining: int,
) -> tuple[str, int]:
    """Size an ``ensure_ascii=True`` JSON string without allocating its token."""
    string = _require_string(value, path)
    if len(string) > MAX_JSON_STRING_CHARS:
        raise TabSchemaError(
            TabSchemaCode.INPUT_LIMIT_EXCEEDED,
            path,
            "JSON string exceeds character limit",
        )

    # Quotes plus at least one byte per source code point.  This catches one
    # enormous scalar (including a shared scalar repeated by many notes) before
    # walking it character by character.
    if len(string) + 2 > remaining:
        raise _serialized_size_error()

    encoded_bytes = 2
    for character in string:
        codepoint = ord(character)
        if character in {'"', "\\"} or character in {"\b", "\f", "\n", "\r", "\t"}:
            encoded_bytes += 2
        elif codepoint < 0x20 or codepoint <= 0xFFFF and codepoint >= 0x80:
            encoded_bytes += 6
        elif codepoint > 0xFFFF:
            # CPython's canonical ensure-ASCII spelling is a UTF-16 surrogate
            # pair (two ``\\uXXXX`` escapes).
            encoded_bytes += 12
        else:
            encoded_bytes += 1
        if encoded_bytes > remaining:
            raise _serialized_size_error()
    return string, encoded_bytes


def _check_fields(obj: dict[str, object], path: str, required: tuple[str, ...]) -> None:
    allowed = frozenset(required)
    for key in obj:
        if key not in allowed:
            raise TabSchemaError(
                TabSchemaCode.UNKNOWN_FIELD,
                _field_path(path, key),
                "field is not allowed",
            )
    for key in required:
        if key not in obj:
            raise TabSchemaError(
                TabSchemaCode.MISSING_FIELD,
                _field_path(path, key),
                "required field is missing",
            )


def _fraction_from_token(value: object, path: str) -> Fraction:
    if type(value) is not str:
        raise TabSchemaError(
            TabSchemaCode.INVALID_TYPE,
            path,
            "expected canonical fraction string",
        )
    token = value
    if len(token) > MAX_FRACTION_TOKEN_CHARS:
        raise TabSchemaError(
            TabSchemaCode.INPUT_LIMIT_EXCEEDED,
            path,
            "fraction token exceeds character limit",
        )
    match = _CANONICAL_FRACTION.fullmatch(token)
    if match is None:
        raise TabSchemaError(
            TabSchemaCode.INVALID_FRACTION,
            path,
            "expected reduced num/positive-den canonical form",
        )
    numerator = int(match.group("numerator"))
    denominator = int(match.group("denominator"))
    if (
        numerator.bit_length() > MAX_FRACTION_COMPONENT_BITS
        or denominator.bit_length() > MAX_FRACTION_COMPONENT_BITS
    ):
        raise TabSchemaError(
            TabSchemaCode.INPUT_LIMIT_EXCEEDED,
            path,
            "fraction component exceeds bit-length limit",
        )
    fraction = Fraction(numerator, denominator)
    if f"{fraction.numerator}/{fraction.denominator}" != token:
        raise TabSchemaError(
            TabSchemaCode.INVALID_FRACTION,
            path,
            "expected reduced num/positive-den canonical form",
        )
    return fraction


def _fraction_to_token(value: object, path: str) -> str:
    if type(value) is not Fraction:
        raise TabSchemaError(TabSchemaCode.INVALID_TYPE, path, "expected Fraction")
    fraction = value
    try:
        numerator = object.__getattribute__(fraction, "_numerator")
        denominator = object.__getattribute__(fraction, "_denominator")
    except (AttributeError, TypeError):
        raise TabSchemaError(
            TabSchemaCode.INVALID_FRACTION,
            path,
            "Fraction components are missing",
        ) from None
    # ``Fraction`` is immutable through its public API, but callers can still
    # corrupt private slots with low-level mutation.  Never invoke arithmetic or
    # formatting hooks from such injected values.
    if type(numerator) is not int or type(denominator) is not int:
        raise TabSchemaError(
            TabSchemaCode.INVALID_TYPE,
            path,
            "expected Fraction with integer components",
        )
    if (
        numerator.bit_length() > MAX_FRACTION_COMPONENT_BITS
        or denominator.bit_length() > MAX_FRACTION_COMPONENT_BITS
    ):
        raise TabSchemaError(
            TabSchemaCode.INPUT_LIMIT_EXCEEDED,
            path,
            "fraction component exceeds bit-length limit",
        )
    if denominator <= 0 or math.gcd(abs(numerator), denominator) != 1:
        raise TabSchemaError(
            TabSchemaCode.INVALID_FRACTION,
            path,
            "expected reduced num/positive-den canonical form",
        )
    token = f"{numerator}/{denominator}"
    if len(token) > MAX_FRACTION_TOKEN_CHARS:
        raise TabSchemaError(
            TabSchemaCode.INPUT_LIMIT_EXCEEDED,
            path,
            "fraction token exceeds character limit",
        )
    return token


def frames(tab: Tab) -> list[Frame]:
    """Group notes by onset (ascending); within a frame, sort by string."""
    by_onset: dict[Fraction, list[TabNote]] = {}
    for n in tab.notes:
        by_onset.setdefault(n.onset, []).append(n)
    return [
        tuple(sorted(by_onset[onset], key=lambda n: n.string))
        for onset in sorted(by_onset)
    ]


_NormalizedNote = tuple[str, str, int, int, int, str]


def _normalize_note_for_json(
    value: object,
    path: str,
    *,
    remaining: int,
) -> tuple[_NormalizedNote, int]:
    if type(value) is not TabNote:
        raise TabSchemaError(TabSchemaCode.INVALID_TYPE, path, "expected TabNote")

    onset = _fraction_to_token(
        _read_serialization_field(value, "onset", f"{path}.onset"),
        f"{path}.onset",
    )
    duration = _fraction_to_token(
        _read_serialization_field(value, "duration", f"{path}.duration"),
        f"{path}.duration",
    )
    string, string_token = _integer_to_token(
        _read_serialization_field(value, "string", f"{path}.string"),
        f"{path}.string",
    )
    fret, fret_token = _integer_to_token(
        _read_serialization_field(value, "fret", f"{path}.fret"),
        f"{path}.fret",
    )
    left_finger, left_finger_token = _integer_to_token(
        _read_serialization_field(value, "left_finger", f"{path}.left_finger"),
        f"{path}.left_finger",
    )

    non_right_finger_bytes = (
        _NOTE_JSON_FIXED_BYTES
        + len(onset)
        + 2
        + len(duration)
        + 2
        + len(string_token)
        + len(fret_token)
        + len(left_finger_token)
    )
    if non_right_finger_bytes > remaining:
        raise _serialized_size_error()
    right_finger, right_finger_bytes = _canonical_json_string_bytes(
        _read_serialization_field(value, "right_finger", f"{path}.right_finger"),
        f"{path}.right_finger",
        remaining=remaining - non_right_finger_bytes,
    )
    return (
        (onset, duration, string, fret, left_finger, right_finger),
        non_right_finger_bytes + right_finger_bytes,
    )


def tab_to_json(tab: Tab) -> str:
    """Serialize a structurally valid Tab to deterministic, round-trippable JSON.

    This validates representation types and resource limits, not playability
    ranges.  It either returns JSON accepted by :func:`tab_from_json` or raises
    :class:`TabSchemaError`.
    """
    if type(tab) is not Tab:
        raise TabSchemaError(TabSchemaCode.INVALID_TYPE, "$", "expected Tab")
    tuning_value = _read_serialization_field(tab, "tuning", "$.tuning")
    capo_value = _read_serialization_field(tab, "capo", "$.capo")
    notes_value = _read_serialization_field(tab, "notes", "$.notes")
    if type(tuning_value) is not tuple:
        raise TabSchemaError(TabSchemaCode.INVALID_TYPE, "$.tuning", "expected tuple")
    if type(notes_value) is not tuple:
        raise TabSchemaError(TabSchemaCode.INVALID_TYPE, "$.notes", "expected tuple")
    if len(notes_value) > MAX_TAB_NOTES:
        raise TabSchemaError(
            TabSchemaCode.INPUT_LIMIT_EXCEEDED,
            "$.notes",
            "Tab note count exceeds limit",
        )

    capo, capo_token = _integer_to_token(capo_value, "$.capo")
    tuning_commas = max(0, len(tuning_value) - 1)
    note_commas = max(0, len(notes_value) - 1)
    encoded_size = (
        _TAB_JSON_FIXED_BYTES + len(capo_token) + tuning_commas + note_commas
    )

    # Every integer token costs at least one byte.  Every note has two shortest
    # fraction strings (``"0/1"``), three one-byte integers, and an empty JSON
    # string.  This lower bound rejects an impossible item count in O(1), before
    # iterating an oversized tuning or allocating any normalized note objects.
    minimum_note_bytes = _NOTE_JSON_FIXED_BYTES + 5 + 5 + 1 + 1 + 1 + 2
    if (
        encoded_size
        + len(tuning_value)
        + len(notes_value) * minimum_note_bytes
        > MAX_TAB_JSON_BYTES
    ):
        raise _serialized_size_error()

    for index, pitch in enumerate(tuning_value):
        _, token = _integer_to_token(pitch, f"$.tuning[{index}]")
        encoded_size += len(token)
        if encoded_size > MAX_TAB_JSON_BYTES:
            raise _serialized_size_error()
    note_prefix_size = encoded_size

    # First pass: validate and account for the exact canonical encoding.  In
    # particular, do not build the large list of note dictionaries or call the
    # JSON encoder until the whole output has been proved to fit.
    for index, note in enumerate(notes_value):
        path = f"$.notes[{index}]"
        _, note_bytes = _normalize_note_for_json(
            note,
            path,
            remaining=MAX_TAB_JSON_BYTES - encoded_size,
        )
        encoded_size += note_bytes

    notes: list[dict[str, object]] = []
    normalized_encoded_size = note_prefix_size
    for index, note in enumerate(notes_value):
        path = f"$.notes[{index}]"
        normalized, note_bytes = _normalize_note_for_json(
            note,
            path,
            remaining=MAX_TAB_JSON_BYTES - normalized_encoded_size,
        )
        normalized_encoded_size += note_bytes
        onset, duration, string, fret, left_finger, right_finger = normalized
        notes.append(
            {
                "onset": onset,
                "duration": duration,
                "string": string,
                "fret": fret,
                "left_finger": left_finger,
                "right_finger": right_finger,
            }
        )

    obj = {
        "tuning": tuning_value,
        "capo": capo,
        "notes": notes,
    }
    try:
        encoded = json.dumps(
            obj,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (OverflowError, RecursionError, TypeError, ValueError):
        raise TabSchemaError(
            TabSchemaCode.INPUT_LIMIT_EXCEEDED,
            "$",
            "Tab cannot be serialized within JSON limits",
        ) from None
    # ``ensure_ascii`` guarantees one output byte per character.  Retain this
    # defense-in-depth check even though the preflight above is exact.
    if len(encoded) > MAX_TAB_JSON_BYTES:
        raise _serialized_size_error()
    return encoded


def tab_from_json(s: str) -> Tab:
    """Parse strict structural Tab JSON without coercion or parser leakage.

    This representation-layer parser deliberately does not choose an oracle
    profile or assign input-domain meaning.  Untrusted oracle/API callers must
    use :func:`validated_tab_from_json` instead.
    """
    if type(s) is not str:
        raise TabSchemaError(
            TabSchemaCode.INVALID_PAYLOAD_TYPE,
            "$",
            "expected JSON text as str",
        )
    try:
        payload_bytes = len(s.encode("utf-8"))
    except UnicodeEncodeError:
        raise TabSchemaError(
            TabSchemaCode.MALFORMED_JSON,
            "$",
            "JSON text is not valid Unicode",
        ) from None
    if payload_bytes > MAX_TAB_JSON_BYTES:
        raise TabSchemaError(
            TabSchemaCode.INPUT_LIMIT_EXCEEDED,
            "$",
            "Tab JSON exceeds byte limit",
        )

    try:
        decoded = json.loads(
            s,
            object_pairs_hook=_JSONObjectPairs,
            parse_int=_JSONInteger,
            parse_float=_parse_json_float,
            parse_constant=_parse_json_constant,
        )
    except RecursionError:
        raise TabSchemaError(
            TabSchemaCode.INPUT_LIMIT_EXCEEDED,
            "$",
            "JSON nesting exceeds runtime limit",
        ) from None
    except (json.JSONDecodeError, OverflowError, TypeError, ValueError):
        raise TabSchemaError(
            TabSchemaCode.MALFORMED_JSON,
            "$",
            "invalid JSON syntax",
        ) from None

    try:
        materialized = _materialize_json(decoded, "$")
    except RecursionError:
        raise TabSchemaError(
            TabSchemaCode.INPUT_LIMIT_EXCEEDED,
            "$",
            "JSON nesting exceeds runtime limit",
        ) from None
    obj = _require_object(materialized, "$")
    _check_fields(obj, "$", _ROOT_FIELDS)

    tuning_values = _require_array(obj["tuning"], "$.tuning")
    tuning = tuple(
        _require_integer(value, f"$.tuning[{index}]")
        for index, value in enumerate(tuning_values)
    )
    capo = _require_integer(obj["capo"], "$.capo")
    note_values = _require_array(obj["notes"], "$.notes")
    if len(note_values) > MAX_TAB_NOTES:
        raise TabSchemaError(
            TabSchemaCode.INPUT_LIMIT_EXCEEDED,
            "$.notes",
            "Tab note count exceeds limit",
        )

    notes: list[TabNote] = []
    for index, value in enumerate(note_values):
        path = f"$.notes[{index}]"
        note = _require_object(value, path)
        _check_fields(note, path, _NOTE_FIELDS)
        right_finger = _require_string(note["right_finger"], f"{path}.right_finger")
        notes.append(
            TabNote(
                onset=_fraction_from_token(note["onset"], f"{path}.onset"),
                duration=_fraction_from_token(note["duration"], f"{path}.duration"),
                string=_require_integer(note["string"], f"{path}.string"),
                fret=_require_integer(note["fret"], f"{path}.fret"),
                left_finger=_require_integer(note["left_finger"], f"{path}.left_finger"),
                right_finger=cast(RightFinger, right_finger),
            )
        )
    return Tab(notes=tuple(notes), tuning=tuning, capo=capo)


def validated_tab_from_json(
    s: str,
    *,
    profile: Profile,
    tempo_bpm: float = 90.0,
    beats_per_bar: int = 4,
) -> Tab:
    """Parse JSON and enforce the explicit profile's canonical input domain.

    Schema failures raise :class:`TabSchemaError`; semantic failures raise the
    shared :class:`~fretsure.oracle.input.OracleInputError`.  Requiring
    ``profile`` prevents a generic representation parser from silently choosing
    a player/instrument model.
    """

    tab = tab_from_json(s)

    # Local import avoids a module cycle: oracle.input imports Tab/TabNote.
    from fretsure.oracle.input import ensure_oracle_input

    validated_tab, _, _, _ = ensure_oracle_input(
        tab,
        profile,
        tempo_bpm=tempo_bpm,
        beats_per_bar=beats_per_bar,
    )
    return validated_tab
