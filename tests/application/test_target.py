from __future__ import annotations

import json
from fractions import Fraction as F

import pytest

import fretsure.application.target as target_module
from fretsure.application.target import (
    TARGET_INPUT_SCHEMA_VERSION,
    TargetInputCode,
    TargetInputError,
    target_from_json,
    target_to_json,
)
from fretsure.ir import Note


def _payload(**note_changes: object) -> str:
    note: dict[str, object] = {
        "onset": "0/1",
        "duration": "1/1",
        "pitch": 60,
        "voice": "melody",
    }
    note.update(note_changes)
    return json.dumps({"notes": [note]})


def _error(payload: object) -> TargetInputError:
    with pytest.raises(TargetInputError) as caught:
        target_from_json(payload)  # type: ignore[arg-type]
    return caught.value


def test_schema_version_is_semantically_frozen() -> None:
    assert TARGET_INPUT_SCHEMA_VERSION == "target-input@0.1.0"


def test_valid_target_is_detached_and_sorted_canonically() -> None:
    source = (
        '{"notes":['
        '{"voice":"melody","pitch":62,"duration":"1/2","onset":"1/2"},'
        '{"onset":"0/1","duration":"1/1","pitch":48,"voice":"bass"}'
        "]}"
    )
    notes = target_from_json(source)
    assert notes == (
        Note(F(0), F(1), 48, "bass"),
        Note(F(1, 2), F(1, 2), 62, "melody"),
    )


@pytest.mark.parametrize("payload", [b'{"notes":[]}', None, 1, {}, []])
def test_payload_requires_an_exact_string(payload: object) -> None:
    error = _error(payload)
    assert error.code is TargetInputCode.INVALID_PAYLOAD_TYPE
    assert error.path == "$"


@pytest.mark.parametrize("payload", ["", "{", "not-json", "\ud800"])
def test_malformed_json_is_typed_without_parser_text(payload: str) -> None:
    error = _error(payload)
    assert error.code is TargetInputCode.MALFORMED_JSON
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


def test_duplicate_keys_are_rejected_at_the_exact_path() -> None:
    error = _error(
        '{"notes":[{"onset":"0/1","duration":"1/1","pitch":60,'
        '"pitch":61,"voice":"melody"}]}'
    )
    assert error.code is TargetInputCode.DUPLICATE_KEY
    assert error.path == "$.notes[0].pitch"


@pytest.mark.parametrize(
    ("payload", "path"),
    [
        ('{"notes":[],"extra":1}', "$.extra"),
        (
            '{"notes":[{"onset":"0/1","duration":"1/1","pitch":60,'
            '"voice":"melody","extra":1}]}',
            "$.notes[0].extra",
        ),
    ],
)
def test_unknown_fields_are_rejected(payload: str, path: str) -> None:
    error = _error(payload)
    assert error.code is TargetInputCode.UNKNOWN_FIELD
    assert error.path == path


def test_missing_fields_are_rejected() -> None:
    error = _error('{"notes":[{"onset":"0/1"}]}')
    assert error.code is TargetInputCode.MISSING_FIELD
    assert error.path == "$.notes[0].duration"


@pytest.mark.parametrize("value", [True, False, "60", 60.0, None, [], {}])
def test_pitch_never_coerces(value: object) -> None:
    error = _error(_payload(pitch=value))
    assert error.code is TargetInputCode.INVALID_TYPE
    assert error.path == "$.notes[0].pitch"


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e9999"])
def test_non_finite_numbers_are_rejected_explicitly(token: str) -> None:
    error = _error(
        '{"notes":[{"onset":"0/1","duration":"1/1","pitch":'
        f'{token},"voice":"melody"}}]}}'
    )
    assert error.code is TargetInputCode.NON_FINITE_NUMBER
    assert error.path == "$.notes[0].pitch"


def test_oversized_float_token_is_bounded_before_conversion() -> None:
    error = _error(
        '{"notes":[{"onset":"0/1","duration":"1/1","pitch":0.'
        + "1" * 129
        + ',"voice":"melody"}]}'
    )
    assert error.code is TargetInputCode.INPUT_LIMIT_EXCEEDED
    assert error.path == "$.notes[0].pitch"


@pytest.mark.parametrize(
    ("field", "token", "code"),
    [
        ("onset", "2/4", TargetInputCode.INVALID_FRACTION),
        ("onset", "-1/1", TargetInputCode.INVALID_VALUE),
        ("duration", "0/1", TargetInputCode.INVALID_VALUE),
        ("duration", 1, TargetInputCode.INVALID_TYPE),
    ],
)
def test_fractions_are_exact_canonical_and_in_domain(
    field: str,
    token: object,
    code: TargetInputCode,
) -> None:
    error = _error(_payload(**{field: token}))
    assert error.code is code
    assert error.path == f"$.notes[0].{field}"


@pytest.mark.parametrize(
    ("field", "value"),
    [("pitch", 128), ("pitch", -1), ("voice", "inner"), ("voice", 1)],
)
def test_note_value_domain_is_explicit(field: str, value: object) -> None:
    error = _error(_payload(**{field: value}))
    assert error.code in {TargetInputCode.INVALID_TYPE, TargetInputCode.INVALID_VALUE}


def test_duplicate_onset_pitch_is_rejected_even_if_other_fields_differ() -> None:
    error = _error(
        '{"notes":['
        '{"onset":"0/1","duration":"1/1","pitch":60,"voice":"melody"},'
        '{"onset":"0/1","duration":"2/1","pitch":60,"voice":"harmony"}'
        "]}"
    )
    assert error.code is TargetInputCode.INVALID_VALUE
    assert error.path == "$.notes[1]"


def test_byte_limit_precedes_json_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(target_module, "MAX_TARGET_JSON_BYTES", 8)
    error = _error('{"notes":[]}')
    assert error.code is TargetInputCode.INPUT_LIMIT_EXCEEDED
    assert error.path == "$"


def test_explicit_depth_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(target_module, "MAX_TARGET_JSON_DEPTH", 1)
    error = _error('{"notes":[[]]}')
    assert error.code is TargetInputCode.INPUT_LIMIT_EXCEEDED
    assert "depth limit" in error.detail


def test_explicit_node_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(target_module, "MAX_TARGET_JSON_NODES", 5)
    error = _error(_payload())
    assert error.code is TargetInputCode.INPUT_LIMIT_EXCEEDED
    assert "value count" in error.detail


def test_empty_target_is_valid_input_for_an_honest_not_found_outcome() -> None:
    assert target_from_json('{"notes":[]}') == ()


def test_serializer_is_deterministic_and_round_trips() -> None:
    notes = (
        Note(F(0), F(1), 60, "melody"),
        Note(F(1, 2), F(1, 2), 48, "bass"),
    )
    encoded = target_to_json(notes)
    assert encoded == (
        '{"notes":[{"onset":"0/1","duration":"1/1","pitch":60,'
        '"voice":"melody"},{"onset":"1/2","duration":"1/2",'
        '"pitch":48,"voice":"bass"}]}'
    )
    assert target_from_json(encoded) == notes


def test_serializer_canonicalizes_note_order() -> None:
    notes = (
        Note(F(2), F(1), 64, "melody"),
        Note(F(0), F(1), 48, "bass"),
    )
    encoded = target_to_json(notes)
    assert encoded.index('"pitch":48') < encoded.index('"pitch":64')
    expected = tuple(sorted(notes, key=lambda note: (note.onset, note.pitch)))
    assert target_from_json(encoded) == expected


def test_serializer_requires_an_exact_tuple() -> None:
    error = _error_from_serializer([Note(F(0), F(1), 60, "melody")])
    assert error.code is TargetInputCode.INVALID_TYPE


def _error_from_serializer(notes: object) -> TargetInputError:
    with pytest.raises(TargetInputError) as caught:
        target_to_json(notes)  # type: ignore[arg-type]
    return caught.value
