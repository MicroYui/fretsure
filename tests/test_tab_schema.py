"""Adversarial tests for both layers of the Tab JSON boundary.

``tab_from_json`` is a profile-free representation parser and preserves strict
structural round-trips. ``validated_tab_from_json`` is the public canonical
oracle/API adapter: it requires a profile and never returns an invalid Tab.
"""

from __future__ import annotations

import json
from fractions import Fraction
from typing import Any, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

import fretsure.tab as tab_module
from fretsure.oracle.input import OracleInputCode, OracleInputError
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.tab import (
    MAX_FRACTION_COMPONENT_BITS,
    MAX_FRACTION_TOKEN_CHARS,
    MAX_JSON_INTEGER_TOKEN_CHARS,
    MAX_TAB_JSON_BYTES,
    MAX_TAB_NOTES,
    RightFinger,
    Tab,
    TabNote,
    TabSchemaCode,
    TabSchemaError,
    tab_from_json,
    tab_to_json,
    validated_tab_from_json,
)


def _note_obj() -> dict[str, object]:
    return {
        "onset": "0/1",
        "duration": "1/1",
        "string": 0,
        "fret": 3,
        "left_finger": 3,
        "right_finger": "p",
    }


def _tab_obj() -> dict[str, object]:
    return {
        "tuning": [40, 45, 50, 55, 59, 64],
        "capo": 0,
        "notes": [_note_obj()],
    }


def _error(payload: object) -> TabSchemaError:
    with pytest.raises(TabSchemaError) as caught:
        tab_from_json(cast(Any, payload))
    return caught.value


def test_schema_error_has_stable_machine_fields_and_string() -> None:
    error = _error('{"tuning":[],"notes":[]}')

    assert error.code is TabSchemaCode.MISSING_FIELD
    assert error.path == "$.capo"
    assert error.message == "required field is missing"
    assert str(error) == "MISSING_FIELD at $.capo: required field is missing"


def test_parser_details_are_suppressed_from_the_public_error_chain() -> None:
    error = _error("{")
    assert error.__cause__ is None
    assert error.__suppress_context__ is True


@pytest.mark.parametrize("payload", ["", "{", "not json", "[1,", "\ud800"])
def test_malformed_json_is_always_typed(payload: str) -> None:
    error = _error(payload)
    assert error.code is TabSchemaCode.MALFORMED_JSON
    assert error.path == "$"


@given(st.text(max_size=1_000))
def test_arbitrary_json_text_never_leaks_an_untyped_parser_error(payload: str) -> None:
    try:
        result = tab_from_json(payload)
    except TabSchemaError:
        return
    assert isinstance(result, Tab)


def test_non_string_payload_is_typed() -> None:
    error = _error(b'{"tuning":[],"capo":0,"notes":[]}')
    assert error.code is TabSchemaCode.INVALID_PAYLOAD_TYPE
    assert error.path == "$"


@pytest.mark.parametrize("payload", ["[]", "null", "true", '"tab"', "1"])
def test_root_must_be_an_object(payload: str) -> None:
    error = _error(payload)
    assert error.code is TabSchemaCode.INVALID_TYPE
    assert error.path == "$"
    assert error.message == "expected object"


@pytest.mark.parametrize(
    ("payload", "path"),
    [
        ('{"tuning":[],"capo":0,"notes":[],"extra":1}', "$.extra"),
        (
            '{"tuning":[],"capo":0,"notes":['
            '{"onset":"0/1","duration":"1/1","string":0,"fret":0,'
            '"left_finger":0,"right_finger":"p","extra":1}]}',
            "$.notes[0].extra",
        ),
    ],
)
def test_unknown_fields_are_rejected(payload: str, path: str) -> None:
    error = _error(payload)
    assert error.code is TabSchemaCode.UNKNOWN_FIELD
    assert error.path == path
    assert error.message == "field is not allowed"


@pytest.mark.parametrize(
    ("payload", "path"),
    [
        ('{"tuning":[],"notes":[]}', "$.capo"),
        (
            '{"tuning":[],"capo":0,"notes":['
            '{"onset":"0/1","duration":"1/1","string":0,"fret":0,'
            '"right_finger":"p"}]}',
            "$.notes[0].left_finger",
        ),
    ],
)
def test_missing_fields_are_rejected(payload: str, path: str) -> None:
    error = _error(payload)
    assert error.code is TabSchemaCode.MISSING_FIELD
    assert error.path == path
    assert error.message == "required field is missing"


@pytest.mark.parametrize(
    ("payload", "path"),
    [
        ('{"tuning":[],"capo":0,"capo":1,"notes":[]}', "$.capo"),
        (
            '{"tuning":[],"capo":0,"notes":['
            '{"onset":"0/1","onset":"1/1","duration":"1/1",'
            '"string":0,"fret":0,"left_finger":0,"right_finger":"p"}]}',
            "$.notes[0].onset",
        ),
    ],
)
def test_duplicate_object_keys_are_rejected_with_location(payload: str, path: str) -> None:
    error = _error(payload)
    assert error.code is TabSchemaCode.DUPLICATE_KEY
    assert error.path == path
    assert error.message == "object key occurs more than once"


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e9999"])
def test_non_finite_json_numbers_are_rejected(token: str) -> None:
    error = _error(f'{{"tuning":[],"capo":{token},"notes":[]}}')
    assert error.code is TabSchemaCode.NON_FINITE_NUMBER
    assert error.path == "$.capo"
    assert error.message == "non-finite JSON numbers are not allowed"


@pytest.mark.parametrize("value", [True, False, "0", 0.0, None, [], {}])
def test_capo_requires_a_native_json_integer(value: object) -> None:
    obj = _tab_obj()
    obj["capo"] = value
    error = _error(json.dumps(obj))
    assert error.code is TabSchemaCode.INVALID_TYPE
    assert error.path == "$.capo"
    assert error.message == "expected integer"


@pytest.mark.parametrize("value", [True, False, "40", 40.0, None, [], {}])
def test_each_tuning_value_requires_a_native_json_integer(value: object) -> None:
    obj = _tab_obj()
    obj["tuning"] = [value]
    error = _error(json.dumps(obj))
    assert error.code is TabSchemaCode.INVALID_TYPE
    assert error.path == "$.tuning[0]"
    assert error.message == "expected integer"


@pytest.mark.parametrize("field", ["string", "fret", "left_finger"])
@pytest.mark.parametrize("value", [True, False, "1", 1.0, None, [], {}])
def test_note_integer_fields_do_not_coerce(field: str, value: object) -> None:
    obj = _tab_obj()
    note = cast(dict[str, object], cast(list[object], obj["notes"])[0])
    note[field] = value
    error = _error(json.dumps(obj))
    assert error.code is TabSchemaCode.INVALID_TYPE
    assert error.path == f"$.notes[0].{field}"
    assert error.message == "expected integer"


@pytest.mark.parametrize("field", ["onset", "duration"])
@pytest.mark.parametrize("value", [0, 0.5, True, False, None, [], {}])
def test_fraction_fields_require_strings(field: str, value: object) -> None:
    obj = _tab_obj()
    note = cast(dict[str, object], cast(list[object], obj["notes"])[0])
    note[field] = value
    error = _error(json.dumps(obj))
    assert error.code is TabSchemaCode.INVALID_TYPE
    assert error.path == f"$.notes[0].{field}"
    assert error.message == "expected canonical fraction string"


@pytest.mark.parametrize(
    "token",
    [
        "0",
        "0/0",
        "1/-2",
        "+1/2",
        "01/2",
        "1/01",
        "-0/1",
        "2/4",
        " 1/2",
        "1/2 ",
        "1//2",
        "1.0/2",
        "--1/2",
    ],
)
def test_fraction_text_must_be_reduced_canonical_form(token: str) -> None:
    obj = _tab_obj()
    cast(dict[str, object], cast(list[object], obj["notes"])[0])["onset"] = token
    error = _error(json.dumps(obj))
    assert error.code is TabSchemaCode.INVALID_FRACTION
    assert error.path == "$.notes[0].onset"
    assert error.message == "expected reduced num/positive-den canonical form"


@pytest.mark.parametrize(
    ("field", "token"),
    [
        ("onset", "1" * MAX_FRACTION_TOKEN_CHARS + "/1"),
        ("duration", "1/" + "1" * MAX_FRACTION_TOKEN_CHARS),
    ],
)
def test_fraction_token_length_limit_is_typed(field: str, token: str) -> None:
    obj = _tab_obj()
    cast(dict[str, object], cast(list[object], obj["notes"])[0])[field] = token
    error = _error(json.dumps(obj))
    assert error.code is TabSchemaCode.INPUT_LIMIT_EXCEEDED
    assert error.path == f"$.notes[0].{field}"
    assert error.message == "fraction token exceeds character limit"


@pytest.mark.parametrize(
    ("field", "token"),
    [
        ("onset", f"{1 << MAX_FRACTION_COMPONENT_BITS}/1"),
        ("duration", f"1/{(1 << MAX_FRACTION_COMPONENT_BITS) + 1}"),
    ],
)
def test_fraction_component_bit_limit_is_typed(field: str, token: str) -> None:
    obj = _tab_obj()
    cast(dict[str, object], cast(list[object], obj["notes"])[0])[field] = token
    error = _error(json.dumps(obj))
    assert error.code is TabSchemaCode.INPUT_LIMIT_EXCEEDED
    assert error.path == f"$.notes[0].{field}"
    assert error.message == "fraction component exceeds bit-length limit"


@pytest.mark.parametrize("field", ["tuning", "notes"])
@pytest.mark.parametrize("value", [None, {}, "", 0, True])
def test_array_fields_require_json_arrays(field: str, value: object) -> None:
    obj = _tab_obj()
    obj[field] = value
    error = _error(json.dumps(obj))
    assert error.code is TabSchemaCode.INVALID_TYPE
    assert error.path == f"$.{field}"
    assert error.message == "expected array"


@pytest.mark.parametrize("value", [None, {}, [], 0, 1.0, True])
def test_right_finger_requires_a_string(value: object) -> None:
    obj = _tab_obj()
    cast(dict[str, object], cast(list[object], obj["notes"])[0])["right_finger"] = value
    error = _error(json.dumps(obj))
    assert error.code is TabSchemaCode.INVALID_TYPE
    assert error.path == "$.notes[0].right_finger"
    assert error.message == "expected string"


def test_payload_byte_limit_is_checked_before_json_parsing() -> None:
    payload = " " * (MAX_TAB_JSON_BYTES + 1)
    error = _error(payload)
    assert error.code is TabSchemaCode.INPUT_LIMIT_EXCEEDED
    assert error.path == "$"
    assert error.message == "Tab JSON exceeds byte limit"


def test_deep_json_nesting_is_a_typed_resource_failure() -> None:
    error = _error("[" * 2_000 + "]" * 2_000)
    assert error.code is TabSchemaCode.INPUT_LIMIT_EXCEEDED
    assert error.path == "$"


def test_oversized_json_integer_conversion_is_typed_and_located() -> None:
    payload = (
        '{"tuning":[],"capo":'
        + "9" * (MAX_JSON_INTEGER_TOKEN_CHARS + 1)
        + ',"notes":[]}'
    )
    error = _error(payload)
    assert error.code is TabSchemaCode.INPUT_LIMIT_EXCEEDED
    assert error.path == "$.capo"
    assert error.message == "JSON integer token exceeds character limit"


def test_note_count_limit_is_typed_before_note_validation() -> None:
    notes = ",".join("{}" for _ in range(MAX_TAB_NOTES + 1))
    payload = f'{{"tuning":[],"capo":0,"notes":[{notes}]}}'
    assert len(payload.encode("utf-8")) <= MAX_TAB_JSON_BYTES

    error = _error(payload)
    assert error.code is TabSchemaCode.INPUT_LIMIT_EXCEEDED
    assert error.path == "$.notes"
    assert error.message == "Tab note count exceeds limit"


def test_structural_parser_preserves_canonical_out_of_domain_fractions() -> None:
    obj = _tab_obj()
    note = cast(dict[str, object], cast(list[object], obj["notes"])[0])
    note["onset"] = "-1/2"
    note["duration"] = "0/1"

    payload = json.dumps(obj)
    tab = tab_from_json(payload)
    assert tab.notes[0].onset == Fraction(-1, 2)
    assert tab.notes[0].duration == Fraction(0)

    with pytest.raises(OracleInputError) as caught:
        validated_tab_from_json(payload, profile=MEDIAN_HAND)

    codes = {diagnostic.code for diagnostic in caught.value.diagnostics}
    assert OracleInputCode.ONSET_RANGE in codes
    assert OracleInputCode.DURATION_RANGE in codes


def test_canonical_adapter_rejects_semantically_invalid_structural_values() -> None:
    obj = _tab_obj()
    obj["tuning"] = [999]
    obj["capo"] = -1
    note = cast(dict[str, object], cast(list[object], obj["notes"])[0])
    note.update(
        {
            "onset": "-1/2",
            "duration": "-1/3",
            "string": 99,
            "fret": -2,
            "left_finger": 99,
            "right_finger": "not-a-finger",
        }
    )

    payload = json.dumps(obj)
    structural = tab_from_json(payload)
    assert structural.tuning == (999,)
    assert structural.capo == -1

    with pytest.raises(OracleInputError) as caught:
        validated_tab_from_json(payload, profile=MEDIAN_HAND)

    codes = {diagnostic.code for diagnostic in caught.value.diagnostics}
    assert {
        OracleInputCode.TUNING_LENGTH,
        OracleInputCode.CAPO,
        OracleInputCode.ONSET_RANGE,
        OracleInputCode.DURATION_RANGE,
        OracleInputCode.STRING,
        OracleInputCode.FRET_RANGE,
        OracleInputCode.LEFT_FINGER,
        OracleInputCode.RIGHT_FINGER,
    } <= codes


def test_empty_and_non_six_string_tabs_remain_structurally_roundtrippable() -> None:
    tabs = [Tab((), (), 0), Tab((), (60,), -1)]
    assert [tab_from_json(tab_to_json(tab)) for tab in tabs] == tabs
    for structural in tabs:
        with pytest.raises(OracleInputError):
            validated_tab_from_json(tab_to_json(structural), profile=MEDIAN_HAND)


def test_tab_to_json_emits_canonical_fractions_and_roundtrips() -> None:
    tab = Tab(
        notes=(
            TabNote(Fraction(2, 6), Fraction(10, 20), 0, 1, 1, "p"),
            TabNote(Fraction(0), Fraction(1), 1, 0, 0, "i"),
        ),
        tuning=(40, 45, 50, 55, 59, 64),
        capo=0,
    )

    encoded = tab_to_json(tab)
    assert encoded == (
        '{"tuning":[40,45,50,55,59,64],"capo":0,"notes":['
        '{"onset":"1/3","duration":"1/2","string":0,"fret":1,'
        '"left_finger":1,"right_finger":"p"},'
        '{"onset":"0/1","duration":"1/1","string":1,"fret":0,'
        '"left_finger":0,"right_finger":"i"}]}'
    )
    raw = json.loads(encoded)
    assert raw["notes"][0]["onset"] == "1/3"
    assert raw["notes"][0]["duration"] == "1/2"
    assert tab_from_json(encoded) == tab


@st.composite
def _semantic_tabs(draw: st.DrawFn) -> Tab:
    onsets = st.builds(
        Fraction,
        st.integers(min_value=0, max_value=10_000),
        st.integers(min_value=1, max_value=10_000),
    )
    durations = st.builds(
        Fraction,
        st.integers(min_value=1, max_value=10_000),
        st.integers(min_value=1, max_value=10_000),
    )
    notes = st.builds(
        TabNote,
        onset=onsets,
        duration=durations,
        string=st.integers(min_value=0, max_value=5),
        fret=st.integers(min_value=0, max_value=36),
        left_finger=st.integers(min_value=0, max_value=4),
        right_finger=st.sampled_from(cast(tuple[RightFinger, ...], ("p", "i", "m", "a"))),
    )
    return Tab(
        notes=draw(st.lists(notes, min_size=1, max_size=12).map(tuple)),
        tuning=(40, 45, 50, 55, 59, 64),
        capo=0,
    )


@given(_semantic_tabs())
def test_canonical_adapter_roundtrips_semantically_valid_tabs(tab: Tab) -> None:
    assert validated_tab_from_json(tab_to_json(tab), profile=MEDIAN_HAND) == tab


@st.composite
def _structural_tabs(draw: st.DrawFn) -> Tab:
    fractions = st.builds(
        Fraction,
        st.integers(min_value=-10_000, max_value=10_000),
        st.integers(min_value=1, max_value=10_000),
    )
    notes = st.builds(
        TabNote,
        onset=fractions,
        duration=fractions,
        string=st.integers(min_value=-100, max_value=100),
        fret=st.integers(min_value=-100, max_value=100),
        left_finger=st.integers(min_value=-100, max_value=100),
        right_finger=st.text(max_size=8).map(
            lambda value: cast(RightFinger, value)
        ),
    )
    return Tab(
        notes=draw(st.lists(notes, max_size=12).map(tuple)),
        tuning=draw(
            st.lists(
                st.integers(min_value=-1000, max_value=1000), max_size=12
            ).map(tuple)
        ),
        capo=draw(st.integers(min_value=-1000, max_value=1000)),
    )


@given(_structural_tabs())
def test_tab_to_json_roundtrips_arbitrary_structural_tabs(tab: Tab) -> None:
    assert tab_from_json(tab_to_json(tab)) == tab


def test_canonical_adapter_requires_and_uses_caller_profile() -> None:
    profile = Profile("one-fret@0.1", 100.0, 50.0, 500.0, 8.0, 648.0, max_fret=1)
    obj = _tab_obj()
    obj["capo"] = 2

    # Structural parsing remains profile-free; the canonical adapter is explicit.
    assert tab_from_json(json.dumps(obj)).capo == 2
    assert (
        validated_tab_from_json(json.dumps(obj), profile=MEDIAN_HAND).capo == 2
    )
    with pytest.raises(OracleInputError) as caught:
        validated_tab_from_json(json.dumps(obj), profile=profile)
    assert OracleInputCode.CAPO_RANGE in {
        diagnostic.code for diagnostic in caught.value.diagnostics
    }


def test_canonical_adapter_preserves_typed_schema_errors() -> None:
    with pytest.raises(TabSchemaError) as caught:
        validated_tab_from_json("{", profile=MEDIAN_HAND)
    assert caught.value.code is TabSchemaCode.MALFORMED_JSON


@pytest.mark.parametrize(
    ("tab", "path", "message"),
    [
        (
            Tab((TabNote(cast(Any, 0), Fraction(1), 0, 0, 0, "p"),), (40,), 0),
            "$.notes[0].onset",
            "expected Fraction",
        ),
        (
            Tab((TabNote(Fraction(0), Fraction(1), cast(Any, True), 0, 0, "p"),), (40,), 0),
            "$.notes[0].string",
            "expected integer",
        ),
        (
            Tab((TabNote(Fraction(0), Fraction(1), 0, 0, 0, cast(Any, 1)),), (40,), 0),
            "$.notes[0].right_finger",
            "expected string",
        ),
        (Tab((), cast(Any, [40]), 0), "$.tuning", "expected tuple"),
        (Tab(cast(Any, []), (40,), 0), "$.notes", "expected tuple"),
    ],
)
def test_tab_to_json_never_emits_an_unparseable_schema(
    tab: Tab, path: str, message: str
) -> None:
    with pytest.raises(TabSchemaError) as caught:
        tab_to_json(tab)
    assert caught.value.code is TabSchemaCode.INVALID_TYPE
    assert caught.value.path == path
    assert caught.value.message == message


def test_tab_to_json_enforces_fraction_bit_limit() -> None:
    tab = Tab(
        (
            TabNote(
                Fraction(1 << MAX_FRACTION_COMPONENT_BITS, 1),
                Fraction(1),
                0,
                0,
                0,
                "p",
            ),
        ),
        (40,),
        0,
    )
    with pytest.raises(TabSchemaError) as caught:
        tab_to_json(tab)
    assert caught.value.code is TabSchemaCode.INPUT_LIMIT_EXCEEDED
    assert caught.value.path == "$.notes[0].onset"


def test_tab_to_json_enforces_note_count_limit() -> None:
    note = TabNote(Fraction(0), Fraction(1), 0, 0, 0, "p")
    tab = Tab((note,) * (MAX_TAB_NOTES + 1), (40,), 0)
    with pytest.raises(TabSchemaError) as caught:
        tab_to_json(tab)
    assert caught.value.code is TabSchemaCode.INPUT_LIMIT_EXCEEDED
    assert caught.value.path == "$.notes"


def test_tab_to_json_enforces_final_payload_byte_limit() -> None:
    note = TabNote(
        Fraction(0),
        Fraction(1),
        0,
        0,
        0,
        cast(RightFinger, "x" * MAX_TAB_JSON_BYTES),
    )
    with pytest.raises(TabSchemaError) as caught:
        tab_to_json(Tab((note,), (40,), 0))
    assert caught.value.code is TabSchemaCode.INPUT_LIMIT_EXCEEDED
    assert caught.value.path == "$"
    assert caught.value.message == "serialized Tab JSON exceeds byte limit"


def test_tab_to_json_rejects_one_shared_huge_string_before_json_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    huge = "x" * MAX_TAB_JSON_BYTES
    note = TabNote(Fraction(0), Fraction(1), 0, 0, 0, cast(RightFinger, huge))
    tab = Tab((note,) * MAX_TAB_NOTES, (40, 45, 50, 55, 59, 64), 0)

    def forbidden_json_dumps(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("oversized output reached json.dumps")

    monkeypatch.setattr(tab_module.json, "dumps", forbidden_json_dumps)
    with pytest.raises(TabSchemaError) as caught:
        tab_to_json(tab)

    assert caught.value.code is TabSchemaCode.INPUT_LIMIT_EXCEEDED
    assert caught.value.path == "$"
    assert caught.value.message == "serialized Tab JSON exceeds byte limit"


def test_tab_to_json_accounts_for_cumulative_canonical_size_before_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    note = TabNote(
        Fraction(0),
        Fraction(1),
        0,
        0,
        0,
        cast(RightFinger, "\N{GUITAR}" * 20),
    )
    tab = Tab((note,) * 10, (40, 45, 50, 55, 59, 64), 0)
    canonical_size = len(tab_to_json(tab))
    monkeypatch.setattr(tab_module, "MAX_TAB_JSON_BYTES", canonical_size - 1)

    def forbidden_json_dumps(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("over-budget output reached json.dumps")

    monkeypatch.setattr(tab_module.json, "dumps", forbidden_json_dumps)
    with pytest.raises(TabSchemaError) as caught:
        tab_to_json(tab)

    assert caught.value.code is TabSchemaCode.INPUT_LIMIT_EXCEEDED
    assert caught.value.path == "$"


def test_tab_to_json_rejects_impossibly_long_tuning_before_iteration_or_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HostileScalar:
        def __str__(self) -> str:
            raise AssertionError("hostile scalar hook executed")

        def __repr__(self) -> str:
            raise AssertionError("hostile scalar hook executed")

    monkeypatch.setattr(tab_module, "MAX_TAB_JSON_BYTES", 128)

    def forbidden_json_dumps(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("oversized tuning reached json.dumps")

    monkeypatch.setattr(tab_module.json, "dumps", forbidden_json_dumps)
    tab = Tab((), cast(Any, (HostileScalar(),) * 100), 0)
    with pytest.raises(TabSchemaError) as caught:
        tab_to_json(tab)

    assert caught.value.code is TabSchemaCode.INPUT_LIMIT_EXCEEDED
    assert caught.value.path == "$"


@pytest.mark.parametrize("field", ["capo", "tuning", "fret"])
def test_tab_to_json_rejects_giant_integer_before_decimal_conversion_or_encoding(
    field: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    giant = 1 << 1_000_000
    note = TabNote(Fraction(0), Fraction(1), 0, 0, 0, "p")
    tab = Tab((note,), (40, 45, 50, 55, 59, 64), 0)
    expected_path = f"$.{field}"
    if field == "capo":
        object.__setattr__(tab, "capo", giant)
    elif field == "tuning":
        object.__setattr__(tab, "tuning", (giant,))
        expected_path = "$.tuning[0]"
    else:
        object.__setattr__(note, "fret", giant)
        expected_path = "$.notes[0].fret"

    def forbidden_json_dumps(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("giant integer reached json.dumps")

    monkeypatch.setattr(tab_module.json, "dumps", forbidden_json_dumps)
    with pytest.raises(TabSchemaError) as caught:
        tab_to_json(tab)

    assert caught.value.code is TabSchemaCode.INPUT_LIMIT_EXCEEDED
    assert caught.value.path == expected_path
    assert caught.value.message == "JSON integer token exceeds character limit"


def test_tab_to_json_low_level_mutation_never_executes_hostile_scalar_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class HostileScalar:
        def __str__(self) -> str:
            raise AssertionError("hostile scalar hook executed")

        def __repr__(self) -> str:
            raise AssertionError("hostile scalar hook executed")

        def __iter__(self) -> object:
            raise AssertionError("hostile scalar hook executed")

    note = TabNote(Fraction(0), Fraction(1), 0, 0, 0, "p")
    object.__setattr__(note, "right_finger", HostileScalar())

    def forbidden_json_dumps(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("hostile scalar reached json.dumps")

    monkeypatch.setattr(tab_module.json, "dumps", forbidden_json_dumps)
    with pytest.raises(TabSchemaError) as caught:
        tab_to_json(Tab((note,), (40, 45, 50, 55, 59, 64), 0))

    assert caught.value.code is TabSchemaCode.INVALID_TYPE
    assert caught.value.path == "$.notes[0].right_finger"
    assert caught.value.message == "expected string"


@pytest.mark.parametrize("field", ["_numerator", "_denominator"])
def test_tab_to_json_fraction_with_deleted_component_is_typed_invalid(
    field: str,
) -> None:
    onset = Fraction(1, 2)
    object.__delattr__(onset, field)
    note = TabNote(onset, Fraction(1), 0, 0, 0, "p")

    with pytest.raises(TabSchemaError) as caught:
        tab_to_json(Tab((note,), (40, 45, 50, 55, 59, 64), 0))

    assert caught.value.code is TabSchemaCode.INVALID_FRACTION
    assert caught.value.path == "$.notes[0].onset"
