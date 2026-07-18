"""Explainable execution trace: the sequence of plan/oracle/edit/re-check steps.

Consumed by the Plan 6 "watch the agent think" viewer and by benchmark repair
stats. Serializes to JSONL (Fractions render as strings).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from fractions import Fraction
from itertools import islice
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from fretsure.agent.edit_dsl import Edit
    from fretsure.ir import Note
    from fretsure.oracle.core import OracleResult
    from fretsure.solver.api import Infeasible
    from fretsure.tab import Tab

StepKind = Literal[
    "PLAN", "PROPOSE", "SOLVE", "ORACLE", "REASON", "EDIT", "RECHECK", "SELECT"
]

TraceEvent = Literal[
    "PLAN",
    "PROPOSE",
    "SOLVE",
    "ORACLE",
    "REASON",
    "EDIT",
    "RECHECK",
    "SELECT",
    "PIPELINE_CONFIGURED",
    "CANDIDATE_PROPOSED",
    "CANDIDATE_FINISHED",
    "SOLVER_RETURNED_TAB",
    "SOLVER_RETURNED_NO_TAB",
    "PLAYABILITY_CHECKED",
    "TIER_CHECKED",
    "REPAIR_EDIT_PROPOSED",
    "MODEL_CALL_FAILED",
    "EDIT_APPLIED",
    "EDIT_REJECTED",
    "MODEL_EDIT_INVALID",
    "RECHECK_STARTED",
    "CANDIDATE_SELECTED",
    "NO_CANDIDATE_SELECTED",
]

TRACE_SCHEMA_VERSION = "agent-trace@0.2.0"
TRACE_CHECKPOINT_SCHEMA_VERSION = "trace-checkpoint@0.1.0"

MAX_TRACE_STEPS = 10_000
MAX_TRACE_JSON_DEPTH = 64
MAX_TRACE_JSON_NODES = 250_000
MAX_TRACE_SCALAR_BYTES = 8 * 1024 * 1024
MAX_TRACE_JSONL_BYTES = 10 * 1024 * 1024
MAX_TRACE_INTEGER_BITS = 4096
MAX_TRACE_CHECKPOINT_NOTES = 512
MAX_TRACE_CHECKPOINT_BYTES = 128 * 1024
MAX_TRACE_DIAGNOSTICS_PER_STEP = 128
MAX_TRACE_EMBEDDED_STATE_BYTES = 512 * 1024

# Product-event values are deliberately narrower than the generic JSON budgets.
# These constants mirror the already-public agent/oracle envelopes without
# importing those modules into the trace leaf and creating a dependency cycle.
MAX_TRACE_AGENT_CANDIDATES = 64
MAX_TRACE_REPAIR_ITERATIONS = 64
MAX_TRACE_DOMAIN_NOTES = 20_000
MAX_TRACE_STABLE_STRING_BYTES = 1024
MAX_TRACE_TEMPO_BPM = 1_000.0
MAX_TRACE_SUPPORTED_FRET = 36

_TRACE_FIDELITY_DIMENSIONS = ("melody", "bass_root", "harmony")
_TRACE_FIDELITY_THRESHOLDS = {
    "melody": 0.9,
    "bass_root": 0.7,
    "harmony": 0.6,
}

_STABLE_CODE = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_SENSITIVE_KEY_PARTS = frozenset(
    {
        "api_key",
        "apikey",
        "auth_token",
        "authtoken",
        "authorization",
        "credential",
        "credentials",
        "exception",
        "exception_text",
        "exceptiontext",
        "model_prompt",
        "modelprompt",
        "model_reply",
        "modelreply",
        "model_response",
        "modelresponse",
        "password",
        "prompt",
        "raw_prompt",
        "rawprompt",
        "raw_reply",
        "rawreply",
        "raw_response",
        "rawresponse",
        "reply",
        "secret",
        "stack_trace",
        "stacktrace",
        "system",
        "system_prompt",
        "systemprompt",
        "traceback",
        "user",
        "user_prompt",
        "userprompt",
    }
)
_SENSITIVE_CONTENT = (
    re.compile(r"(?i)\bbearer\s+[^\s,;]+"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|auth[_-]?token|access[_-]?token|password|secret|"
        r"credential)\s*[:=]\s*[^\s,;]+"
    ),
    re.compile(r"(?i)-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----"),
    re.compile(r"(?i)traceback\s*\(most recent call last\)"),
    re.compile(r"\b[A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception):\s"),
    re.compile(r"(?:/Users/|/private/|/home/|[A-Za-z]:\\\\)"),
    re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:[^/\s@]+@"),
)

_STEP_KINDS = frozenset(
    {"PLAN", "PROPOSE", "SOLVE", "ORACLE", "REASON", "EDIT", "RECHECK", "SELECT"}
)

_EVENT_KINDS: dict[str, str] = {
    # Kind-named events preserve the original Trace.add surface for internal and
    # historical callers. Only events in PRODUCT_TRACE_EVENTS have a frozen
    # product payload below; the Plan 6A pipeline emits those product events.
    "PLAN": "PLAN",
    "PROPOSE": "PROPOSE",
    "SOLVE": "SOLVE",
    "ORACLE": "ORACLE",
    "REASON": "REASON",
    "EDIT": "EDIT",
    "RECHECK": "RECHECK",
    "SELECT": "SELECT",
    "PIPELINE_CONFIGURED": "PLAN",
    "CANDIDATE_PROPOSED": "PROPOSE",
    "CANDIDATE_FINISHED": "SOLVE",
    "SOLVER_RETURNED_TAB": "SOLVE",
    "SOLVER_RETURNED_NO_TAB": "SOLVE",
    "PLAYABILITY_CHECKED": "ORACLE",
    "TIER_CHECKED": "ORACLE",
    "REPAIR_EDIT_PROPOSED": "REASON",
    "MODEL_CALL_FAILED": "REASON",
    "EDIT_APPLIED": "EDIT",
    "EDIT_REJECTED": "EDIT",
    "MODEL_EDIT_INVALID": "EDIT",
    "RECHECK_STARTED": "RECHECK",
    "CANDIDATE_SELECTED": "SELECT",
    "NO_CANDIDATE_SELECTED": "SELECT",
}

_PRODUCT_EVENT_FIELDS: dict[str, frozenset[str]] = {
    "PIPELINE_CONFIGURED": frozenset(
        {
            "llm_model_id",
            "source_tempo_bpm",
            "effective_tempo_bpm",
            "time_signature",
            "tuning",
            "capo",
            "profile",
            "checker_version",
            "profile_version",
            "profile_fingerprint",
            "input_schema_version",
            "fidelity_checker_version",
            "candidates",
            "max_repair_iterations",
            "critic_enabled",
        }
    ),
    "CANDIDATE_PROPOSED": frozenset({"temperature", "target_checkpoint"}),
    "CANDIDATE_FINISHED": frozenset(
        {"verdict", "tab_available", "repair_iterations"}
    ),
    "SOLVER_RETURNED_TAB": frozenset(
        {"status", "target_sha256", "target_note_count"}
    ),
    "SOLVER_RETURNED_NO_TAB": frozenset(
        {
            "status",
            "target_sha256",
            "target_note_count",
            "infeasible",
            "terminal_reason",
        }
    ),
    "PLAYABILITY_CHECKED": frozenset(
        {
            "diagnostics",
            "diagnostic_count",
            "diagnostics_complete",
            "diagnostics_sha256",
            "verdict",
            "tab_checkpoint",
            "checker_version",
            "profile_version",
            "profile_fingerprint",
            "input_schema_version",
            "terminal_reason",
        }
    ),
    "TIER_CHECKED": frozenset(
        {
            "tier",
            "meets",
            "tier_violation_count",
            "target_sha256",
            "tab_checkpoint",
            "terminal_reason",
        }
    ),
    "REPAIR_EDIT_PROPOSED": frozenset(
        {"edit", "based_on_diagnostic_codes"}
    ),
    "MODEL_CALL_FAILED": frozenset({"reason_code", "target_sha256"}),
    "EDIT_APPLIED": frozenset(
        {
            "edit",
            "status",
            "reason_code",
            "before_target_sha256",
            "after_target_sha256",
            "state_changed",
        }
    ),
    "EDIT_REJECTED": frozenset(
        {
            "edit",
            "status",
            "reason_code",
            "before_target_sha256",
            "after_target_sha256",
            "state_changed",
        }
    ),
    "MODEL_EDIT_INVALID": frozenset(
        {
            "edit",
            "status",
            "reason_code",
            "before_target_sha256",
            "after_target_sha256",
            "state_changed",
        }
    ),
    "RECHECK_STARTED": frozenset({"trigger", "target_checkpoint"}),
    "CANDIDATE_SELECTED": frozenset(
        {
            "winner_candidate_index",
            "candidates_considered",
            "verdict",
            "green_certified",
            "playability_gate",
            "faithfulness_passed",
            "ranking_melody_recall",
            "ranking_bass_preserved",
            "ranking_harmony_jaccard",
            "melody_f1",
            "bass_root_accuracy",
            "harmony_jaccard",
            "evaluated_dimensions",
            "unavailable_dimensions",
            "critic_status",
            "critic_overall",
        }
    ),
    "NO_CANDIDATE_SELECTED": frozenset(
        {
            "winner_candidate_index",
            "candidates_considered",
            "playability_gate",
            "faithfulness_passed",
        }
    ),
}

PRODUCT_TRACE_EVENTS = frozenset(_PRODUCT_EVENT_FIELDS)


class TraceInputError(ValueError):
    """A typed failure for data that cannot safely enter the public trace."""

    def __init__(self, path: str, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"invalid trace {path}: {detail}")


def _stable_string(
    value: object,
    *,
    path: str,
    allowed: frozenset[str] | None = None,
    code: bool = False,
) -> str:
    if type(value) is not str:
        raise TraceInputError(path, "must be an exact string")
    text = value
    if not text or not text.isprintable():
        raise TraceInputError(path, "must be a non-empty printable string")
    try:
        encoded_size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise TraceInputError(path, "must contain valid Unicode scalar values") from None
    if encoded_size > MAX_TRACE_STABLE_STRING_BYTES:
        raise TraceInputError(path, "stable string exceeds its public byte limit")
    if allowed is not None and text not in allowed:
        raise TraceInputError(path, "value is outside the frozen event vocabulary")
    if code and _STABLE_CODE.fullmatch(text) is None:
        raise TraceInputError(path, "must be an uppercase stable code")
    return text


def _bounded_integer(
    value: object,
    *,
    path: str,
    minimum: int = 0,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise TraceInputError(
            path,
            f"must be an exact integer in {minimum}..{maximum}",
        )
    return value


def _unit_float(value: object, *, path: str) -> float:
    if type(value) is not float or not math.isfinite(value):
        raise TraceInputError(path, "must be an exact finite float")
    result = value
    if not 0.0 <= result <= 1.0:
        raise TraceInputError(path, "must be within 0.0..1.0")
    return result


def _validate_fidelity_availability(data: dict[str, object], *, path: str) -> None:
    dimensions: dict[str, tuple[str, ...]] = {}
    for field_name in ("evaluated_dimensions", "unavailable_dimensions"):
        raw = data[field_name]
        if type(raw) not in (list, tuple):
            raise TraceInputError(
                f"{path}.{field_name}", "must be an exact dimension sequence"
            )
        sequence = tuple(cast(list[object] | tuple[object, ...], raw))
        if any(type(value) is not str for value in sequence):
            raise TraceInputError(
                f"{path}.{field_name}", "dimension names must be exact strings"
            )
        values = cast(tuple[str, ...], sequence)
        canonical = tuple(
            dimension for dimension in _TRACE_FIDELITY_DIMENSIONS if dimension in values
        )
        if values != canonical:
            raise TraceInputError(
                f"{path}.{field_name}",
                "dimensions must be unique and canonically ordered",
            )
        dimensions[field_name] = values

    evaluated = dimensions["evaluated_dimensions"]
    unavailable = dimensions["unavailable_dimensions"]
    if set(evaluated).isdisjoint(unavailable) is False or set(evaluated) | set(
        unavailable
    ) != set(_TRACE_FIDELITY_DIMENSIONS):
        raise TraceInputError(path, "fidelity dimensions must form a complete partition")

    score_fields = {
        "melody": "melody_f1",
        "bass_root": "bass_root_accuracy",
        "harmony": "harmony_jaccard",
    }
    evaluated_scores: list[bool] = []
    for dimension, score_field in score_fields.items():
        score = data[score_field]
        if dimension in evaluated:
            normalized = _unit_float(score, path=f"{path}.{score_field}")
            evaluated_scores.append(
                normalized >= _TRACE_FIDELITY_THRESHOLDS[dimension]
            )
        elif score is not None:
            raise TraceInputError(
                f"{path}.{score_field}",
                "must be null when the source dimension is unavailable",
            )
    expected_passed = bool(evaluated) and all(evaluated_scores)
    if data["faithfulness_passed"] is not expected_passed:
        raise TraceInputError(
            f"{path}.faithfulness_passed",
            "disagrees with available scores and frozen thresholds",
        )


def _canonical_fraction_parts(value: object, *, path: str) -> tuple[int, int]:
    if type(value) is not str:
        raise TraceInputError(path, "must be a canonical fraction string")
    token = value
    if not 3 <= len(token) <= 2 * MAX_TRACE_INTEGER_BITS or token.count("/") != 1:
        raise TraceInputError(path, "must be a canonical fraction string")
    numerator_token, denominator_token = token.split("/", 1)
    if (
        not numerator_token
        or not denominator_token
        or (numerator_token.startswith("-") and len(numerator_token) == 1)
        or any(character not in "0123456789" for character in numerator_token.lstrip("-"))
        or any(character not in "0123456789" for character in denominator_token)
        or (len(numerator_token.lstrip("-")) > 1 and numerator_token.lstrip("-").startswith("0"))
        or (len(denominator_token) > 1 and denominator_token.startswith("0"))
        or numerator_token == "-0"
    ):
        raise TraceInputError(path, "must be a canonical fraction string")
    try:
        numerator = int(numerator_token)
        denominator = int(denominator_token)
    except (ValueError, OverflowError):
        raise TraceInputError(path, "fraction exceeds the runtime integer limit") from None
    if (
        denominator <= 0
        or numerator.bit_length() > MAX_TRACE_INTEGER_BITS
        or denominator.bit_length() > MAX_TRACE_INTEGER_BITS
        or math.gcd(abs(numerator), denominator) != 1
    ):
        raise TraceInputError(path, "must be reduced with a positive denominator")
    return numerator, denominator


def _validate_public_trace_content(value: object, *, path: str) -> None:
    """Reject high-confidence secret/raw-exception material before publication."""

    value_type = type(value)
    if value_type is str:
        text = cast(str, value)
        if any(pattern.search(text) is not None for pattern in _SENSITIVE_CONTENT):
            raise TraceInputError(path, "contains sensitive or raw exception content")
        return
    if value_type is dict:
        for index, (key, child) in enumerate(cast(dict[object, object], value).items()):
            if type(key) is not str:
                # The JSON normalizer owns the stable error for non-string keys.
                continue
            camel_separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
            normalized = re.sub(
                r"[^a-z0-9]+", "_", camel_separated.lower()
            ).strip("_")
            bounded_key = f"_{normalized}_"
            if any(
                f"_{sensitive_part}_" in bounded_key
                for sensitive_part in _SENSITIVE_KEY_PARTS
            ):
                raise TraceInputError(
                    f"{path}.key[{index}]",
                    "sensitive trace fields are not public",
                )
            _validate_public_trace_content(child, path=f"{path}.{key}")
        return
    if value_type in (list, tuple):
        for index, child in enumerate(cast(list[object] | tuple[object, ...], value)):
            _validate_public_trace_content(child, path=f"{path}[{index}]")


def _validate_event_data(event: str, data: object, *, path: str) -> None:
    expected = _PRODUCT_EVENT_FIELDS.get(event)
    if expected is None:
        if type(data) is dict:
            generic = cast(dict[object, object], data)
            if "target_checkpoint" in generic:
                _validate_checkpoint(
                    generic["target_checkpoint"],
                    path=f"{path}.target_checkpoint",
                    expected_type="target",
                )
            if "tab_checkpoint" in generic:
                _validate_checkpoint(
                    generic["tab_checkpoint"],
                    path=f"{path}.tab_checkpoint",
                    expected_type="tab",
                )
        return
    if type(data) is not dict:
        raise TraceInputError(path, "product-event data must be an exact object")
    raw_keys = tuple(cast(dict[object, object], data))
    if any(type(key) is not str for key in raw_keys):
        raise TraceInputError(path, "product-event field names must be exact strings")
    actual = frozenset(cast(str, key) for key in raw_keys)
    if actual == expected:
        _validate_product_payload(event, cast(dict[str, object], data), path=path)
        return
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing:
        raise TraceInputError(path, f"product event is missing fields: {', '.join(missing)}")
    if unknown:
        raise TraceInputError(path, f"product event has unknown fields: {', '.join(unknown)}")
    raise AssertionError("field-set mismatch must have missing or unknown members")


_CHECKPOINT_FIELDS = frozenset(
    {
        "checkpoint_schema_version",
        "type",
        "sha256",
        "note_count",
        "complete",
        "state_bytes",
        "state",
        "omission",
    }
)
_EDIT_FIELDS = frozenset({"op", "target_onset", "target_pitch", "arg"})
_DIAGNOSTIC_FIELDS = frozenset(
    {
        "code",
        "measure",
        "beat",
        "offending_note_indices",
        "overage",
        "suggested_relaxations",
        "message",
    }
)


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_target_checkpoint_state(state: dict[str, object], *, path: str) -> int:
    if frozenset(state) != {"notes"} or type(state.get("notes")) is not list:
        raise TraceInputError(path, "target checkpoint state does not match its schema")
    notes = cast(list[object], state["notes"])
    if len(notes) > MAX_TRACE_DOMAIN_NOTES:
        raise TraceInputError(path, "target checkpoint note count exceeds its domain limit")
    identities: set[tuple[int, int, int]] = set()
    for index, value in enumerate(notes):
        note_path = f"{path}.notes[{index}]"
        if type(value) is not dict:
            raise TraceInputError(note_path, "target note must be an exact object")
        note = cast(dict[str, object], value)
        if frozenset(note) != {"onset", "duration", "pitch", "voice"}:
            raise TraceInputError(note_path, "target note fields do not match its schema")
        onset = _canonical_fraction_parts(note["onset"], path=f"{note_path}.onset")
        duration = _canonical_fraction_parts(
            note["duration"], path=f"{note_path}.duration"
        )
        if onset[0] < 0 or duration[0] <= 0:
            raise TraceInputError(note_path, "target onset/duration are outside their domain")
        pitch = _bounded_integer(
            note["pitch"], path=f"{note_path}.pitch", maximum=127
        )
        _stable_string(
            note["voice"],
            path=f"{note_path}.voice",
            allowed=frozenset({"melody", "bass", "harmony"}),
        )
        identity = (onset[0], onset[1], pitch)
        if identity in identities:
            raise TraceInputError(note_path, "target checkpoint contains a duplicate onset/pitch")
        identities.add(identity)
    return len(notes)


def _validate_tab_checkpoint_state(state: dict[str, object], *, path: str) -> int:
    if frozenset(state) != {"tuning", "capo", "notes"}:
        raise TraceInputError(path, "Tab checkpoint state does not match its schema")
    tuning_value = state["tuning"]
    notes_value = state["notes"]
    if type(tuning_value) is not list or type(notes_value) is not list:
        raise TraceInputError(path, "Tab checkpoint arrays must be exact lists")
    tuning = cast(list[object], tuning_value)
    notes = cast(list[object], notes_value)
    if len(tuning) != 6:
        raise TraceInputError(f"{path}.tuning", "must contain exactly six pitches")
    pitches = [
        _bounded_integer(value, path=f"{path}.tuning[{index}]", maximum=127)
        for index, value in enumerate(tuning)
    ]
    if any(left >= right for left, right in zip(pitches, pitches[1:], strict=False)):
        raise TraceInputError(f"{path}.tuning", "pitches must be strictly increasing")
    _bounded_integer(
        state["capo"],
        path=f"{path}.capo",
        maximum=MAX_TRACE_SUPPORTED_FRET,
    )
    if len(notes) > MAX_TRACE_DOMAIN_NOTES:
        raise TraceInputError(path, "Tab checkpoint note count exceeds its domain limit")
    for index, value in enumerate(notes):
        note_path = f"{path}.notes[{index}]"
        if type(value) is not dict:
            raise TraceInputError(note_path, "Tab note must be an exact object")
        note = cast(dict[str, object], value)
        if frozenset(note) != {
            "onset",
            "duration",
            "string",
            "fret",
            "left_finger",
            "right_finger",
        }:
            raise TraceInputError(note_path, "Tab note fields do not match its schema")
        onset = _canonical_fraction_parts(note["onset"], path=f"{note_path}.onset")
        duration = _canonical_fraction_parts(
            note["duration"], path=f"{note_path}.duration"
        )
        if onset[0] < 0 or duration[0] <= 0:
            raise TraceInputError(note_path, "Tab onset/duration are outside their domain")
        _bounded_integer(note["string"], path=f"{note_path}.string", maximum=5)
        fret = _bounded_integer(
            note["fret"],
            path=f"{note_path}.fret",
            maximum=MAX_TRACE_SUPPORTED_FRET,
        )
        left_finger = _bounded_integer(
            note["left_finger"], path=f"{note_path}.left_finger", maximum=4
        )
        if (fret == 0) != (left_finger == 0):
            raise TraceInputError(
                note_path,
                "open/fretted state and left finger are inconsistent",
            )
        _stable_string(
            note["right_finger"],
            path=f"{note_path}.right_finger",
            allowed=frozenset({"p", "i", "m", "a"}),
        )
    return len(notes)


def _validate_checkpoint(value: object, *, path: str, expected_type: str) -> None:
    if type(value) is not dict:
        raise TraceInputError(path, "checkpoint must be an exact object")
    checkpoint = cast(dict[str, object], value)
    if frozenset(checkpoint) != _CHECKPOINT_FIELDS:
        raise TraceInputError(path, "checkpoint fields do not match its schema")
    if checkpoint["checkpoint_schema_version"] != TRACE_CHECKPOINT_SCHEMA_VERSION:
        raise TraceInputError(path, "checkpoint schema version is unsupported")
    if checkpoint["type"] != expected_type:
        raise TraceInputError(path, f"checkpoint type must be {expected_type}")
    if not _is_sha256(checkpoint["sha256"]):
        raise TraceInputError(path, "checkpoint sha256 must be lowercase hexadecimal")
    note_count = checkpoint["note_count"]
    state_bytes = checkpoint["state_bytes"]
    complete = checkpoint["complete"]
    if type(note_count) is not int or note_count < 0:
        raise TraceInputError(path, "checkpoint note_count must be non-negative")
    if type(state_bytes) is not int or state_bytes < 0:
        raise TraceInputError(path, "checkpoint state_bytes must be non-negative")
    if type(complete) is not bool:
        raise TraceInputError(path, "checkpoint complete must be an exact boolean")
    if complete:
        if type(checkpoint["state"]) is not dict or checkpoint["omission"] is not None:
            raise TraceInputError(path, "complete checkpoint must carry only full state")
        if note_count > MAX_TRACE_CHECKPOINT_NOTES or state_bytes > MAX_TRACE_CHECKPOINT_BYTES:
            raise TraceInputError(path, "complete checkpoint exceeds an embedding limit")
        state = cast(dict[str, object], checkpoint["state"])
        actual_notes = (
            _validate_target_checkpoint_state(state, path=f"{path}.state")
            if expected_type == "target"
            else _validate_tab_checkpoint_state(state, path=f"{path}.state")
        )
        encoded = _canonical_payload(state)
        if actual_notes != note_count:
            raise TraceInputError(path, "checkpoint note_count does not match its state")
        if len(encoded) != state_bytes:
            raise TraceInputError(path, "checkpoint state_bytes does not match its state")
        if hashlib.sha256(encoded).hexdigest() != checkpoint["sha256"]:
            raise TraceInputError(path, "checkpoint sha256 does not match its state")
    else:
        if checkpoint["state"] is not None or type(checkpoint["omission"]) is not dict:
            raise TraceInputError(path, "omitted checkpoint must carry a typed omission")
        omission = cast(dict[str, object], checkpoint["omission"])
        code = omission.get("code")
        if code == "NOTE_LIMIT":
            if omission != {
                "code": "NOTE_LIMIT",
                "limit_notes": MAX_TRACE_CHECKPOINT_NOTES,
            } or note_count <= MAX_TRACE_CHECKPOINT_NOTES:
                raise TraceInputError(path, "NOTE_LIMIT omission is inconsistent")
        elif code == "BYTE_LIMIT":
            if omission != {
                "code": "BYTE_LIMIT",
                "limit_bytes": MAX_TRACE_CHECKPOINT_BYTES,
            } or not (
                note_count <= MAX_TRACE_CHECKPOINT_NOTES
                and state_bytes > MAX_TRACE_CHECKPOINT_BYTES
            ):
                raise TraceInputError(path, "BYTE_LIMIT omission is inconsistent")
        elif code == "TRACE_BUDGET":
            if omission != {
                "code": "TRACE_BUDGET",
                "limit_bytes": MAX_TRACE_EMBEDDED_STATE_BYTES,
            }:
                raise TraceInputError(path, "TRACE_BUDGET omission is inconsistent")
        else:
            raise TraceInputError(path, "checkpoint omission code is unsupported")


def _validate_edit(value: object, *, path: str) -> None:
    if type(value) is not dict:
        raise TraceInputError(path, "edit must be an exact object")
    edit = cast(dict[str, object], value)
    if frozenset(edit) != _EDIT_FIELDS:
        raise TraceInputError(path, "edit fields do not match its schema")
    if edit["op"] not in {"drop_note", "drop_inner", "octave_shift", "revoice"}:
        raise TraceInputError(path, "edit op is unsupported")
    onset = _canonical_fraction_parts(edit["target_onset"], path=f"{path}.target_onset")
    if onset[0] < 0:
        raise TraceInputError(path, "edit target onset must be non-negative")
    _bounded_integer(edit["target_pitch"], path=f"{path}.target_pitch", maximum=127)
    arg = edit["arg"]
    if type(arg) is not int:
        raise TraceInputError(f"{path}.arg", "must be an exact integer")
    if (
        (edit["op"] in {"drop_note", "drop_inner"} and arg != 0)
        or (edit["op"] == "octave_shift" and arg not in {-12, 12})
        or (edit["op"] == "revoice" and not 0 <= arg <= 127)
    ):
        raise TraceInputError(f"{path}.arg", "is outside the selected edit operation domain")


def _validate_diagnostics(data: dict[str, object], *, path: str) -> None:
    diagnostics = data["diagnostics"]
    count = data["diagnostic_count"]
    complete = data["diagnostics_complete"]
    if type(diagnostics) is not list:
        raise TraceInputError(path, "diagnostics must be an exact list")
    if len(diagnostics) > MAX_TRACE_DIAGNOSTICS_PER_STEP:
        raise TraceInputError(path, "diagnostic preview exceeds its public limit")
    if type(count) is not int or count < len(diagnostics):
        raise TraceInputError(path, "diagnostic_count is inconsistent")
    if type(complete) is not bool or complete != (count == len(diagnostics)):
        raise TraceInputError(path, "diagnostics_complete is inconsistent")
    if not _is_sha256(data["diagnostics_sha256"]):
        raise TraceInputError(path, "diagnostics_sha256 must be lowercase hexadecimal")
    for index, value in enumerate(diagnostics):
        row_path = f"{path}.diagnostics[{index}]"
        if type(value) is not dict or frozenset(value) != _DIAGNOSTIC_FIELDS:
            raise TraceInputError(row_path, "diagnostic fields do not match its schema")
        diagnostic = cast(dict[str, object], value)
        code = _stable_string(diagnostic["code"], path=f"{row_path}.code", code=True)
        measure = _bounded_integer(
            diagnostic["measure"],
            path=f"{row_path}.measure",
            minimum=1,
            maximum=MAX_TRACE_DOMAIN_NOTES,
        )
        beat = _stable_string(diagnostic["beat"], path=f"{row_path}.beat")
        _canonical_fraction_parts(beat, path=f"{row_path}.beat")
        indices = diagnostic["offending_note_indices"]
        if type(indices) is not list or len(indices) > MAX_TRACE_DOMAIN_NOTES:
            raise TraceInputError(row_path, "offending_note_indices must be a bounded list")
        seen_indices: set[int] = set()
        for note_index, note_value in enumerate(cast(list[object], indices)):
            normalized_index = _bounded_integer(
                note_value,
                path=f"{row_path}.offending_note_indices[{note_index}]",
                maximum=MAX_TRACE_DOMAIN_NOTES - 1,
            )
            if normalized_index in seen_indices:
                raise TraceInputError(row_path, "offending note indices must be unique")
            seen_indices.add(normalized_index)
        overage = diagnostic["overage"]
        if type(overage) is not float or not math.isfinite(overage) or overage < 0:
            raise TraceInputError(f"{row_path}.overage", "must be a non-negative finite float")
        relaxations = diagnostic["suggested_relaxations"]
        if type(relaxations) is not list or len(relaxations) > MAX_TRACE_DIAGNOSTICS_PER_STEP:
            raise TraceInputError(row_path, "suggested_relaxations must be a bounded list")
        for relaxation_index, relaxation in enumerate(cast(list[object], relaxations)):
            _stable_string(
                relaxation,
                path=f"{row_path}.suggested_relaxations[{relaxation_index}]",
            )
        expected_message = (
            f"Checker reported {code} at measure {measure}, beat {beat}."
            if overage == 0.0
            else f"Checker reported {code} at measure {measure}, beat {beat}; "
            f"overage {overage:.6g} in checker-defined units."
        )
        if diagnostic["message"] != expected_message:
            raise TraceInputError(f"{row_path}.message", "does not match the stable formatter")
    if complete and hashlib.sha256(_canonical_payload(diagnostics)).hexdigest() != data[
        "diagnostics_sha256"
    ]:
        raise TraceInputError(path, "complete diagnostic digest does not match its rows")


def _validate_infeasible(value: object, *, path: str) -> None:
    if type(value) is not dict:
        raise TraceInputError(path, "infeasible result must be an exact object")
    result = cast(dict[str, object], value)
    if frozenset(result) != {"code", "onset", "pitches", "bounded_search"}:
        raise TraceInputError(path, "infeasible fields do not match its schema")
    _stable_string(
        result["code"],
        path=f"{path}.code",
        allowed=frozenset(
            {
                "EMPTY_TARGET",
                "UNREACHABLE_PITCH",
                "NO_FRAME_CONFIG",
                "NO_NON_RED_EXTENSION",
            }
        ),
    )
    if result["onset"] is not None:
        onset = _canonical_fraction_parts(result["onset"], path=f"{path}.onset")
        if onset[0] < 0:
            raise TraceInputError(f"{path}.onset", "must be non-negative")
    pitches = result["pitches"]
    if type(pitches) is not list or len(pitches) > 64:
        raise TraceInputError(f"{path}.pitches", "must be a bounded exact list")
    for index, pitch in enumerate(cast(list[object], pitches)):
        _bounded_integer(pitch, path=f"{path}.pitches[{index}]", maximum=127)
    if result["bounded_search"] is not True:
        raise TraceInputError(f"{path}.bounded_search", "must be exactly true")


def _validate_product_payload(
    event: str, data: dict[str, object], *, path: str
) -> None:
    if "target_checkpoint" in data:
        _validate_checkpoint(
            data["target_checkpoint"],
            path=f"{path}.target_checkpoint",
            expected_type="target",
        )
    if "tab_checkpoint" in data:
        _validate_checkpoint(
            data["tab_checkpoint"],
            path=f"{path}.tab_checkpoint",
            expected_type="tab",
        )
    if event == "PLAYABILITY_CHECKED":
        _validate_diagnostics(data, path=path)
    if event == "REPAIR_EDIT_PROPOSED":
        _validate_edit(data["edit"], path=f"{path}.edit")
    if event in {"EDIT_APPLIED", "EDIT_REJECTED"}:
        _validate_edit(data["edit"], path=f"{path}.edit")
    if event == "MODEL_EDIT_INVALID" and data["edit"] is not None:
        raise TraceInputError(f"{path}.edit", "invalid model edit must be null")
    for key in (
        "target_sha256",
        "before_target_sha256",
        "after_target_sha256",
    ):
        if key in data and not _is_sha256(data[key]):
            raise TraceInputError(f"{path}.{key}", "must be lowercase SHA-256")
    if event == "EDIT_APPLIED" and (
        data["status"] != "applied"
        or data["reason_code"] is not None
        or data["state_changed"] is not True
    ):
        raise TraceInputError(path, "EDIT_APPLIED status fields are inconsistent")
    if event in {"EDIT_REJECTED", "MODEL_EDIT_INVALID"} and (
        data["state_changed"] is not False
    ):
        raise TraceInputError(path, "rejected edit status fields are inconsistent")

    if event == "PIPELINE_CONFIGURED":
        _stable_string(data["llm_model_id"], path=f"{path}.llm_model_id")
        for name in ("source_tempo_bpm", "effective_tempo_bpm"):
            value = data[name]
            if (
                type(value) is not float
                or not math.isfinite(value)
                or not 1.0 <= value <= MAX_TRACE_TEMPO_BPM
            ):
                raise TraceInputError(f"{path}.{name}", "must be a finite supported tempo")
        _stable_string(
            data["time_signature"],
            path=f"{path}.time_signature",
            allowed=frozenset({"4/4"}),
        )
        tuning = data["tuning"]
        if type(tuning) not in (list, tuple) or len(
            cast(list[object] | tuple[object, ...], tuning)
        ) != 6:
            raise TraceInputError(f"{path}.tuning", "must be an exact six-pitch sequence")
        pitches = [
            _bounded_integer(value, path=f"{path}.tuning[{index}]", maximum=127)
            for index, value in enumerate(cast(list[object] | tuple[object, ...], tuning))
        ]
        if any(left >= right for left, right in zip(pitches, pitches[1:], strict=False)):
            raise TraceInputError(f"{path}.tuning", "must be strictly increasing")
        _bounded_integer(
            data["capo"], path=f"{path}.capo", maximum=MAX_TRACE_SUPPORTED_FRET
        )
        for name in (
            "profile",
            "checker_version",
            "profile_version",
            "input_schema_version",
            "fidelity_checker_version",
        ):
            _stable_string(data[name], path=f"{path}.{name}")
        if not _is_sha256(data["profile_fingerprint"]):
            raise TraceInputError(f"{path}.profile_fingerprint", "must be lowercase SHA-256")
        _bounded_integer(
            data["candidates"],
            path=f"{path}.candidates",
            minimum=1,
            maximum=MAX_TRACE_AGENT_CANDIDATES,
        )
        _bounded_integer(
            data["max_repair_iterations"],
            path=f"{path}.max_repair_iterations",
            maximum=MAX_TRACE_REPAIR_ITERATIONS,
        )
        if type(data["critic_enabled"]) is not bool:
            raise TraceInputError(f"{path}.critic_enabled", "must be an exact boolean")
    elif event == "CANDIDATE_PROPOSED":
        temperature = data["temperature"]
        if (
            type(temperature) is not float
            or not math.isfinite(temperature)
            or not 0.0 <= temperature <= 1.0
        ):
            raise TraceInputError(f"{path}.temperature", "must be a float in 0.0..1.0")
    elif event == "CANDIDATE_FINISHED":
        verdict = _stable_string(
            data["verdict"],
            path=f"{path}.verdict",
            allowed=frozenset({"GREEN", "AMBER", "RED", "INFEASIBLE"}),
        )
        if type(data["tab_available"]) is not bool:
            raise TraceInputError(f"{path}.tab_available", "must be an exact boolean")
        _bounded_integer(
            data["repair_iterations"],
            path=f"{path}.repair_iterations",
            maximum=MAX_TRACE_REPAIR_ITERATIONS,
        )
        if data["tab_available"] != (verdict != "INFEASIBLE"):
            raise TraceInputError(path, "candidate verdict and Tab availability disagree")
    elif event == "SOLVER_RETURNED_TAB":
        if data["status"] != "TAB":
            raise TraceInputError(f"{path}.status", "must be exactly TAB")
        _bounded_integer(
            data["target_note_count"],
            path=f"{path}.target_note_count",
            maximum=MAX_TRACE_DOMAIN_NOTES,
        )
    elif event == "SOLVER_RETURNED_NO_TAB":
        if data["status"] != "NO_TAB":
            raise TraceInputError(f"{path}.status", "must be exactly NO_TAB")
        _bounded_integer(
            data["target_note_count"],
            path=f"{path}.target_note_count",
            maximum=MAX_TRACE_DOMAIN_NOTES,
        )
        _validate_infeasible(data["infeasible"], path=f"{path}.infeasible")
        if data["terminal_reason"] not in {None, "BUDGET_EXHAUSTED"}:
            raise TraceInputError(f"{path}.terminal_reason", "is outside its vocabulary")
    elif event == "PLAYABILITY_CHECKED":
        verdict = _stable_string(
            data["verdict"],
            path=f"{path}.verdict",
            allowed=frozenset({"GREEN", "AMBER", "RED"}),
        )
        for name in ("checker_version", "profile_version", "input_schema_version"):
            _stable_string(data[name], path=f"{path}.{name}")
        if not _is_sha256(data["profile_fingerprint"]):
            raise TraceInputError(f"{path}.profile_fingerprint", "must be lowercase SHA-256")
        terminal_reason = data["terminal_reason"]
        if terminal_reason not in {None, "GREEN", "BUDGET_EXHAUSTED"}:
            raise TraceInputError(f"{path}.terminal_reason", "is outside its vocabulary")
        if (verdict == "GREEN") != (terminal_reason == "GREEN"):
            raise TraceInputError(path, "GREEN verdict and terminal reason disagree")
        diagnostic_count = cast(int, data["diagnostic_count"])
        # Verdicts compare optimistic/pessimistic profiles, while public
        # diagnostics localize the median profile. An AMBER result may therefore
        # have either zero or non-zero median diagnostics.
        if (verdict == "GREEN" and diagnostic_count != 0) or (
            verdict == "RED" and diagnostic_count == 0
        ):
            raise TraceInputError(path, "verdict and diagnostic count disagree")
    elif event == "TIER_CHECKED":
        _stable_string(data["tier"], path=f"{path}.tier")
        if type(data["meets"]) is not bool:
            raise TraceInputError(f"{path}.meets", "must be an exact boolean")
        violation_count = _bounded_integer(
            data["tier_violation_count"],
            path=f"{path}.tier_violation_count",
            maximum=MAX_TRACE_DOMAIN_NOTES,
        )
        terminal_reason = data["terminal_reason"]
        if terminal_reason not in {None, "TIER_MET", "BUDGET_EXHAUSTED"}:
            raise TraceInputError(f"{path}.terminal_reason", "is outside its vocabulary")
        if data["meets"] != (terminal_reason == "TIER_MET"):
            raise TraceInputError(path, "tier result and terminal reason disagree")
        if data["meets"] != (violation_count == 0):
            raise TraceInputError(path, "tier result and violation count disagree")
    elif event == "REPAIR_EDIT_PROPOSED":
        codes = data["based_on_diagnostic_codes"]
        if type(codes) is not list or len(codes) > MAX_TRACE_DIAGNOSTICS_PER_STEP:
            raise TraceInputError(
                f"{path}.based_on_diagnostic_codes",
                "must be a bounded list",
            )
        seen_codes: set[str] = set()
        for index, value in enumerate(cast(list[object], codes)):
            code = _stable_string(
                value,
                path=f"{path}.based_on_diagnostic_codes[{index}]",
                code=True,
            )
            if code in seen_codes:
                raise TraceInputError(path, "diagnostic codes must be unique")
            seen_codes.add(code)
    elif event == "MODEL_CALL_FAILED":
        _stable_string(
            data["reason_code"],
            path=f"{path}.reason_code",
            allowed=frozenset({"LLM_TRANSPORT_FAILURE"}),
        )
    elif event == "EDIT_REJECTED":
        _stable_string(
            data["reason_code"],
            path=f"{path}.reason_code",
            allowed=frozenset({"MELODY_PROTECTED", "TARGET_NOT_FOUND"}),
        )
        expected_status = (
            "rejected" if data["reason_code"] == "MELODY_PROTECTED" else "noop"
        )
        if data["status"] != expected_status:
            raise TraceInputError(path, "rejected edit status and reason disagree")
        if data["before_target_sha256"] != data["after_target_sha256"]:
            raise TraceInputError(path, "rejected edit must preserve the target digest")
    elif event == "MODEL_EDIT_INVALID":
        _stable_string(
            data["reason_code"],
            path=f"{path}.reason_code",
            allowed=frozenset({"NO_JSON_OBJECT", "INVALID_EDIT_SCHEMA"}),
        )
        if data["status"] != "unparseable":
            raise TraceInputError(path, "invalid model edit status must be unparseable")
        if data["before_target_sha256"] != data["after_target_sha256"]:
            raise TraceInputError(path, "invalid edit must preserve the target digest")
    elif event == "EDIT_APPLIED":
        if data["before_target_sha256"] == data["after_target_sha256"]:
            raise TraceInputError(path, "applied edit must change the target digest")
    elif event == "RECHECK_STARTED":
        _stable_string(
            data["trigger"],
            path=f"{path}.trigger",
            allowed=frozenset({"MODEL_EDIT_INVALID", "EDIT_APPLIED", "EDIT_REJECTED"}),
        )
    elif event == "CANDIDATE_SELECTED":
        winner = _bounded_integer(
            data["winner_candidate_index"],
            path=f"{path}.winner_candidate_index",
            maximum=MAX_TRACE_AGENT_CANDIDATES - 1,
        )
        considered = _bounded_integer(
            data["candidates_considered"],
            path=f"{path}.candidates_considered",
            minimum=1,
            maximum=MAX_TRACE_AGENT_CANDIDATES,
        )
        if winner >= considered:
            raise TraceInputError(path, "winner must be among considered candidates")
        verdict = _stable_string(
            data["verdict"],
            path=f"{path}.verdict",
            allowed=frozenset({"GREEN", "AMBER", "RED"}),
        )
        if type(data["green_certified"]) is not bool or data["green_certified"] != (
            verdict == "GREEN"
        ):
            raise TraceInputError(path, "winner verdict and GREEN certification disagree")
        playability_gate = _stable_string(
            data["playability_gate"],
            path=f"{path}.playability_gate",
            allowed=frozenset({"passed", "not_passed"}),
        )
        if (playability_gate == "passed") != (verdict == "GREEN"):
            raise TraceInputError(path, "winner verdict and playability gate disagree")
        if type(data["faithfulness_passed"]) is not bool:
            raise TraceInputError(f"{path}.faithfulness_passed", "must be an exact boolean")
        for name in (
            "ranking_melody_recall",
            "ranking_bass_preserved",
            "ranking_harmony_jaccard",
        ):
            _unit_float(data[name], path=f"{path}.{name}")
        _validate_fidelity_availability(data, path=path)
        critic_status = _stable_string(
            data["critic_status"],
            path=f"{path}.critic_status",
            allowed=frozenset({"SCORED", "NOT_RUN"}),
        )
        critic_overall = data["critic_overall"]
        if critic_status == "SCORED":
            _unit_float(critic_overall, path=f"{path}.critic_overall")
        elif critic_overall is not None:
            raise TraceInputError(path, "NOT_RUN critic must not contain a score")
    elif event == "NO_CANDIDATE_SELECTED":
        if data["winner_candidate_index"] is not None:
            raise TraceInputError(path, "no-selection winner must be null")
        if data["playability_gate"] is not None or data["faithfulness_passed"] is not None:
            raise TraceInputError(path, "no-selection gates must be null")
        _bounded_integer(
            data["candidates_considered"],
            path=f"{path}.candidates_considered",
            minimum=0,
            maximum=MAX_TRACE_AGENT_CANDIDATES,
        )


def _validate_product_context(
    event: str,
    detail: str,
    data: dict[str, object],
    *,
    candidate_index: int | None,
    iteration: int | None,
    path: str,
) -> None:
    if event not in PRODUCT_TRACE_EVENTS:
        return
    if event in {"PIPELINE_CONFIGURED", "NO_CANDIDATE_SELECTED"} and (
        candidate_index is not None or iteration is not None
    ):
        raise TraceInputError(path, "event must be pipeline-scoped")
    if event == "CANDIDATE_PROPOSED" and (
        candidate_index is None or iteration is not None
    ):
        raise TraceInputError(path, "proposal must identify only its candidate")
    if event == "CANDIDATE_FINISHED":
        if candidate_index is None or iteration != data["repair_iterations"]:
            raise TraceInputError(path, "candidate finish context is inconsistent")
    if event == "CANDIDATE_SELECTED":
        if candidate_index != data["winner_candidate_index"] or iteration is not None:
            raise TraceInputError(path, "selection context is inconsistent")
    if event in {
        "SOLVER_RETURNED_TAB",
        "SOLVER_RETURNED_NO_TAB",
        "PLAYABILITY_CHECKED",
        "TIER_CHECKED",
    } and iteration is None:
        raise TraceInputError(path, "solver/checker event must identify an iteration")
    if event in {
        "REPAIR_EDIT_PROPOSED",
        "MODEL_CALL_FAILED",
        "EDIT_APPLIED",
        "EDIT_REJECTED",
        "MODEL_EDIT_INVALID",
        "RECHECK_STARTED",
    } and (iteration is None or iteration < 1):
        raise TraceInputError(path, "repair event must identify a positive iteration")
    _validate_product_detail(
        event,
        detail,
        data,
        candidate_index=candidate_index,
        path=f"{path}.detail",
    )


def _validate_product_detail(
    event: str,
    detail: str,
    data: dict[str, object],
    *,
    candidate_index: int | None,
    path: str,
) -> None:
    expected: set[str]
    if event == "PIPELINE_CONFIGURED":
        expected = {"pipeline configured from source metadata and explicit options"}
    elif event == "CANDIDATE_PROPOSED":
        expected = {f"Candidate {candidate_index} produced a bounded target-note checkpoint."}
    elif event == "CANDIDATE_FINISHED":
        expected = {f"Candidate {candidate_index} finished with {data['verdict']}."}
    elif event == "SOLVER_RETURNED_TAB":
        expected = {"Solver returned a tablature candidate."}
    elif event == "SOLVER_RETURNED_NO_TAB":
        infeasible = cast(dict[str, object], data["infeasible"])
        location = (
            "an unspecified onset"
            if infeasible["onset"] is None
            else f"onset {infeasible['onset']}"
        )
        expected = {
            "The bounded fingering search returned no candidate "
            f"({infeasible['code']}) at {location}."
        }
    elif event == "PLAYABILITY_CHECKED":
        count = cast(int, data["diagnostic_count"])
        noun = "diagnostic" if count == 1 else "diagnostics"
        expected = {f"Oracle returned {data['verdict']} with {count} {noun}."}
    elif event == "TIER_CHECKED":
        expected = {
            "The deterministic tier checker returned "
            f"meets={data['meets']} for {data['tier']}."
        }
    elif event == "REPAIR_EDIT_PROPOSED":
        edit = cast(dict[str, object], data["edit"])
        strategy = {
            "drop_note": "Thin one non-melody voice",
            "drop_inner": "Thin one inner voice",
            "octave_shift": "Move one non-melody note by an octave",
            "revoice": "Revoice one non-melody note",
        }[cast(str, edit["op"])]
        expected = {
            f"{strategy} at onset {edit['target_onset']} and recheck pitch "
            f"{edit['target_pitch']}."
        }
    elif event == "MODEL_CALL_FAILED":
        expected = {
            "The model call failed; repair stopped without exposing transport details.",
            "The model call failed; simplification stopped without transport details.",
        }
    elif event == "MODEL_EDIT_INVALID":
        expected = {
            "The model response did not contain an accepted JSON edit.",
            "The model JSON did not satisfy the edit schema.",
        }
    elif event == "EDIT_APPLIED":
        expected = {
            "The targeted edit was applied to the repair state.",
            "The targeted edit was applied to the simplification state.",
        }
    elif event == "EDIT_REJECTED":
        expected = (
            {"The edit was rejected because melody notes are protected."}
            if data["reason_code"] == "MELODY_PROTECTED"
            else {"The edit matched no target note and changed no state."}
        )
    elif event == "RECHECK_STARTED":
        expected = {
            "Recheck the unchanged target after the rejected model response.",
            "Recheck the unchanged target after the rejected edit.",
            "Run the bounded solver and oracle again for the post-edit target.",
            "Run the bounded solver and tier checker again for the post-edit target.",
        }
    elif event == "CANDIDATE_SELECTED":
        expected = {
            f"Selected candidate {candidate_index}; playability and fidelity remain separate gates."
        }
    else:
        expected = {"No candidate returned a tablature result within the bounded search."}
    if detail not in expected:
        raise TraceInputError(path, "does not match the frozen product-event formatter")


@dataclass
class _TraceBudget:
    nodes: int = 0
    scalar_bytes: int = 0


@dataclass
class _EmbeddedStateBudget:
    bytes_used: int = 0


def _snapshot_value(
    items: tuple[tuple[object, object], ...], name: str
) -> object | None:
    for key, value in items:
        if type(key) is str and key == name:
            return value
    return None


def _checkpoint_uses_aggregate_budget(
    items: tuple[tuple[object, object], ...], budget: _EmbeddedStateBudget
) -> bool:
    if (
        _snapshot_value(items, "checkpoint_schema_version")
        != TRACE_CHECKPOINT_SCHEMA_VERSION
        or _snapshot_value(items, "complete") is not True
    ):
        return False
    state_bytes = _snapshot_value(items, "state_bytes")
    if type(state_bytes) is not int or state_bytes < 0:
        return False
    if state_bytes > MAX_TRACE_EMBEDDED_STATE_BYTES - budget.bytes_used:
        return True
    budget.bytes_used += state_bytes
    return False


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
    embedded_budget: _EmbeddedStateBudget,
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
        omit_checkpoint_state = _checkpoint_uses_aggregate_budget(
            dict_items, embedded_budget
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
                        embedded_budget=embedded_budget,
                        active_containers=active_containers,
                    ),
                )
                if omit_checkpoint_state:
                    if key == "complete":
                        item = False
                    elif key == "state":
                        item = None
                    elif key == "omission":
                        item = {
                            "code": "TRACE_BUDGET",
                            "limit_bytes": MAX_TRACE_EMBEDDED_STATE_BYTES,
                        }
                normalized[normalized_key] = _normalize_json_value(
                    item,
                    path=f"{path}.value[{index}]",
                    depth=depth + 1,
                    budget=budget,
                    embedded_budget=embedded_budget,
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
                    embedded_budget=embedded_budget,
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


def canonical_fraction_token(value: Fraction, *, path: str = "fraction") -> str:
    """Return the trace contract's canonical ``numerator/denominator`` token."""

    if type(value) is not Fraction:
        raise TraceInputError(path, "must be an exact Fraction")
    rendered = _canonical_fraction(value, path)
    return rendered if "/" in rendered else f"{rendered}/1"


def _canonical_payload(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        return encoded.encode("ascii")
    except (OverflowError, TypeError, ValueError, UnicodeError) as error:
        raise TraceInputError("checkpoint", "state is not canonical JSON") from error


def _checkpoint(
    checkpoint_type: Literal["target", "tab"],
    state: dict[str, object],
    *,
    note_count: int,
) -> dict[str, object]:
    encoded = _canonical_payload(state)
    digest = hashlib.sha256(encoded).hexdigest()
    complete = True
    omission: dict[str, object] | None = None
    if note_count > MAX_TRACE_CHECKPOINT_NOTES:
        complete = False
        omission = {
            "code": "NOTE_LIMIT",
            "limit_notes": MAX_TRACE_CHECKPOINT_NOTES,
        }
    elif len(encoded) > MAX_TRACE_CHECKPOINT_BYTES:
        complete = False
        omission = {
            "code": "BYTE_LIMIT",
            "limit_bytes": MAX_TRACE_CHECKPOINT_BYTES,
        }
    return {
        "checkpoint_schema_version": TRACE_CHECKPOINT_SCHEMA_VERSION,
        "type": checkpoint_type,
        "sha256": digest,
        "note_count": note_count,
        "complete": complete,
        "state_bytes": len(encoded),
        "state": state if complete else None,
        "omission": omission,
    }


def target_checkpoint(target: tuple[Note, ...]) -> dict[str, object]:
    """Snapshot one repair target without exposing prompts or model output."""

    from fretsure.ir import Note

    if type(target) is not tuple:
        raise TraceInputError("target", "must be an exact tuple")
    notes: list[dict[str, object]] = []
    for index, note in enumerate(target):
        path = f"target[{index}]"
        if type(note) is not Note:
            raise TraceInputError(path, "must be an exact Note")
        try:
            onset = object.__getattribute__(note, "onset")
            duration = object.__getattribute__(note, "duration")
            pitch = object.__getattribute__(note, "pitch")
            voice = object.__getattribute__(note, "voice")
        except (AttributeError, TypeError) as error:
            raise TraceInputError(path, "Note fields are missing") from error
        if type(pitch) is not int or type(voice) is not str:
            raise TraceInputError(path, "Note pitch/voice types are invalid")
        if not 0 <= pitch <= 127 or voice not in {"melody", "bass", "harmony"}:
            raise TraceInputError(path, "Note pitch/voice values are outside the IR domain")
        notes.append(
            {
                "onset": canonical_fraction_token(onset, path=f"{path}.onset"),
                "duration": canonical_fraction_token(
                    duration, path=f"{path}.duration"
                ),
                "pitch": pitch,
                "voice": voice,
            }
        )
    return _checkpoint("target", {"notes": notes}, note_count=len(notes))


def tab_checkpoint(tab: Tab) -> dict[str, object]:
    """Snapshot a canonical Tab, explicitly omitting oversized state."""

    from fretsure.tab import Tab, tab_to_json

    if type(tab) is not Tab:
        raise TraceInputError("tab", "must be an exact Tab")
    try:
        notes = object.__getattribute__(tab, "notes")
    except (AttributeError, TypeError) as error:
        raise TraceInputError("tab.notes", "field is missing") from error
    if type(notes) is not tuple:
        raise TraceInputError("tab.notes", "must be an exact tuple")
    encoded = tab_to_json(tab)
    try:
        state = json.loads(encoded)
    except (TypeError, ValueError, RecursionError) as error:  # pragma: no cover
        raise TraceInputError("tab", "canonical Tab JSON could not be decoded") from error
    if type(state) is not dict:  # pragma: no cover - tab_to_json invariant
        raise TraceInputError("tab", "canonical Tab JSON root must be an object")
    return _checkpoint("tab", cast(dict[str, object], state), note_count=len(notes))


def _diagnostic_row(value: object, *, index: int) -> dict[str, object]:
    from fretsure.oracle.diagnostics import Diagnostic

    path = f"diagnostics[{index}]"
    if type(value) is not Diagnostic:
        raise TraceInputError(path, "must be an exact Diagnostic")
    diagnostic = value
    code = diagnostic.violation_type
    beat = canonical_fraction_token(diagnostic.beat, path=f"{path}.beat")
    overage = diagnostic.overage
    if not math.isfinite(overage):
        raise TraceInputError(f"{path}.overage", "must be finite")
    if overage == 0.0:
        message = f"Checker reported {code} at measure {diagnostic.measure}, beat {beat}."
    else:
        message = (
            f"Checker reported {code} at measure {diagnostic.measure}, beat {beat}; "
            f"overage {overage:.6g} in checker-defined units."
        )
    return {
        "code": code,
        "measure": diagnostic.measure,
        "beat": beat,
        "offending_note_indices": list(diagnostic.offending_notes),
        "overage": overage,
        "suggested_relaxations": list(diagnostic.suggested_relaxations),
        "message": message,
    }


def diagnostics_payload(result: OracleResult) -> dict[str, object]:
    """Return bounded diagnostics plus a digest of the complete ordered set."""

    from fretsure.oracle.core import OracleResult

    if type(result) is not OracleResult:
        raise TraceInputError("oracle", "must be an exact OracleResult")
    diagnostics = result.diagnostics
    digest = hashlib.sha256()
    digest.update(b"[")
    preview: list[dict[str, object]] = []
    for index, diagnostic in enumerate(diagnostics):
        row = _diagnostic_row(diagnostic, index=index)
        if index:
            digest.update(b",")
        digest.update(_canonical_payload(row))
        if index < MAX_TRACE_DIAGNOSTICS_PER_STEP:
            preview.append(row)
    digest.update(b"]")
    return {
        "diagnostics": preview,
        "diagnostic_count": len(diagnostics),
        "diagnostics_complete": len(diagnostics) <= MAX_TRACE_DIAGNOSTICS_PER_STEP,
        "diagnostics_sha256": digest.hexdigest(),
    }


def oracle_trace_payload(
    result: OracleResult,
    tab: Tab,
    *,
    terminal_reason: str | None,
) -> dict[str, object]:
    payload = diagnostics_payload(result)
    payload.update(
        {
            "verdict": result.verdict,
            "tab_checkpoint": tab_checkpoint(tab),
            "checker_version": result.checker_version,
            "profile_version": result.profile_version,
            "profile_fingerprint": result.profile_fingerprint,
            "input_schema_version": result.input_schema_version,
            "terminal_reason": terminal_reason,
        }
    )
    return payload


def oracle_detail(result: OracleResult) -> str:
    count = len(result.diagnostics)
    noun = "diagnostic" if count == 1 else "diagnostics"
    return f"Oracle returned {result.verdict} with {count} {noun}."


def infeasible_trace_payload(value: Infeasible) -> dict[str, object]:
    from fretsure.solver.api import Infeasible

    if type(value) is not Infeasible:
        raise TraceInputError("infeasible", "must be an exact Infeasible")
    onset = (
        None
        if value.onset is None
        else canonical_fraction_token(value.onset, path="infeasible.onset")
    )
    return {
        "code": value.code.value,
        "onset": onset,
        "pitches": list(value.pitches),
        "bounded_search": True,
    }


def infeasible_detail(value: Infeasible) -> str:
    location = (
        "an unspecified onset"
        if value.onset is None
        else f"onset {canonical_fraction_token(value.onset, path='infeasible.onset')}"
    )
    return (
        "The bounded fingering search returned no candidate "
        f"({value.code.value}) at {location}."
    )


def edit_trace_payload(edit: Edit) -> dict[str, object]:
    from fretsure.agent.edit_dsl import Edit

    if type(edit) is not Edit:
        raise TraceInputError("edit", "must be an exact Edit")
    return {
        "op": edit.op,
        "target_onset": canonical_fraction_token(
            edit.target_onset, path="edit.target_onset"
        ),
        "target_pitch": edit.target_pitch,
        "arg": edit.arg,
    }


def edit_detail(edit: Edit) -> str:
    strategies = {
        "drop_note": "Thin one non-melody voice",
        "drop_inner": "Thin one inner voice",
        "octave_shift": "Move one non-melody note by an octave",
        "revoice": "Revoice one non-melody note",
    }
    strategy = strategies[edit.op]
    return (
        f"{strategy} at onset {canonical_fraction_token(edit.target_onset)} "
        f"and recheck pitch {edit.target_pitch}."
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
    event: TraceEvent | None = None
    candidate_index: int | None = None
    iteration: int | None = None


def _encode_trace_steps(
    steps: tuple[TraceStep, ...],
) -> tuple[list[dict[str, object]], list[str]]:
    budget = _TraceBudget()
    embedded_budget = _EmbeddedStateBudget()
    rows: list[dict[str, object]] = []
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
            event = object.__getattribute__(step, "event")
            candidate_index = object.__getattribute__(step, "candidate_index")
            iteration = object.__getattribute__(step, "iteration")
        except (AttributeError, TypeError) as error:
            raise TraceInputError(step_path, "TraceStep fields are missing") from error
        if type(kind) is not str or kind not in _STEP_KINDS:
            raise TraceInputError(
                f"{step_path}.kind", "must be a standard trace-step kind"
            )
        if type(detail) is not str:
            raise TraceInputError(f"{step_path}.detail", "must be an exact string")
        if event is None:
            event = kind
        if type(event) is not str or event not in _EVENT_KINDS:
            raise TraceInputError(
                f"{step_path}.event", "must be a standard trace event"
            )
        if _EVENT_KINDS[event] != kind:
            raise TraceInputError(
                f"{step_path}.event",
                "event is not valid for this trace-step kind",
            )
        _validate_event_data(event, data, path=f"{step_path}.data")
        for name, value in (
            ("candidate_index", candidate_index),
            ("iteration", iteration),
        ):
            if value is not None and (
                type(value) is not int or not 0 <= value <= MAX_TRACE_STEPS
            ):
                raise TraceInputError(
                    f"{step_path}.{name}",
                    f"must be null or an exact integer in 0..{MAX_TRACE_STEPS}",
                )
        normalized_kind = _normalize_json_value(
            kind,
            path=f"{step_path}.kind",
            depth=0,
            budget=budget,
            embedded_budget=embedded_budget,
            active_containers=set(),
        )
        normalized_event = _normalize_json_value(
            event,
            path=f"{step_path}.event",
            depth=0,
            budget=budget,
            embedded_budget=embedded_budget,
            active_containers=set(),
        )
        normalized_detail = _normalize_json_value(
            detail,
            path=f"{step_path}.detail",
            depth=0,
            budget=budget,
            embedded_budget=embedded_budget,
            active_containers=set(),
        )
        normalized_data = _normalize_json_value(
            data,
            path=f"{step_path}.data",
            depth=0,
            budget=budget,
            embedded_budget=embedded_budget,
            active_containers=set(),
        )
        assert type(normalized_detail) is str
        assert type(normalized_data) is dict
        _validate_public_trace_content(normalized_detail, path=f"{step_path}.detail")
        _validate_public_trace_content(normalized_data, path=f"{step_path}.data")
        _validate_event_data(event, normalized_data, path=f"{step_path}.data")
        _validate_product_context(
            event,
            normalized_detail,
            cast(dict[str, object], normalized_data),
            candidate_index=candidate_index,
            iteration=iteration,
            path=step_path,
        )
        payload: dict[str, object] = {
            "trace_schema_version": TRACE_SCHEMA_VERSION,
            "seq": index,
            "kind": normalized_kind,
            "event": normalized_event,
            "candidate_index": candidate_index,
            "iteration": iteration,
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
            raise TraceInputError(
                step_path, "could not be encoded as standard JSON"
            ) from error
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
        rows.append(payload)
        lines.append(line)
    return rows, lines


@dataclass
class Trace:
    steps: list[TraceStep] = field(default_factory=list)

    def add(
        self,
        kind: StepKind,
        detail: str,
        *,
        event: TraceEvent | None = None,
        candidate_index: int | None = None,
        iteration: int | None = None,
        **data: Any,
    ) -> None:
        if type(kind) is not str or kind not in _STEP_KINDS:
            raise TraceInputError("kind", "must be a standard trace-step kind")
        if type(detail) is not str:
            raise TraceInputError("detail", "must be an exact string")
        effective_event = kind if event is None else event
        if type(effective_event) is not str or effective_event not in _EVENT_KINDS:
            raise TraceInputError("event", "must be a standard trace event")
        if _EVENT_KINDS[effective_event] != kind:
            raise TraceInputError("event", "event is not valid for this trace-step kind")
        for name, value in (
            ("candidate_index", candidate_index),
            ("iteration", iteration),
        ):
            if value is not None and (
                type(value) is not int or not 0 <= value <= MAX_TRACE_STEPS
            ):
                raise TraceInputError(
                    name,
                    f"must be null or an exact integer in 0..{MAX_TRACE_STEPS}",
                )
        _validate_event_data(effective_event, data, path="data")
        snapshot = _normalize_json_value(
            data,
            path="data",
            depth=0,
            budget=_TraceBudget(),
            embedded_budget=_EmbeddedStateBudget(),
            active_containers=set(),
        )
        assert type(snapshot) is dict
        _validate_public_trace_content(detail, path="detail")
        _validate_public_trace_content(snapshot, path="data")
        _validate_event_data(effective_event, snapshot, path="data")
        _validate_product_context(
            effective_event,
            detail,
            cast(dict[str, object], snapshot),
            candidate_index=candidate_index,
            iteration=iteration,
            path="step",
        )
        self.steps.append(
            TraceStep(
                kind,
                detail,
                cast(dict[str, Any], snapshot),
                cast(TraceEvent, effective_event),
                candidate_index,
                iteration,
            )
        )

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
        _rows, lines = _encode_trace_steps(steps)
        return "\n".join(lines)

    def to_wire(self) -> dict[str, object]:
        """Return the same bounded public contract as JSONL without reparsing it."""

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
        rows, _lines = _encode_trace_steps(steps)
        return {"schema_version": TRACE_SCHEMA_VERSION, "steps": rows}

    def to_public_dict(self) -> dict[str, object]:
        """Compatibility spelling for service/application serializers."""

        return self.to_wire()
