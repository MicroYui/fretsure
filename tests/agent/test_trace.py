import inspect
import json
import sys
from fractions import Fraction as F

import pytest

import fretsure.agent.trace as trace_module
from fretsure.agent.trace import Trace, TraceInputError, TraceStep


def test_add_accumulates_in_order() -> None:
    t = Trace()
    t.add("PLAN", "analyze structure", bars=4)
    t.add("EDIT", "drop 5th", onset=F(1, 2), pitch=55)
    assert [s.kind for s in t.steps] == ["PLAN", "EDIT"]
    assert isinstance(t.steps[0], TraceStep)
    assert t.steps[1].data["pitch"] == 55


def test_to_jsonl_one_parseable_line_per_step() -> None:
    t = Trace()
    t.add("ORACLE", "AMBER", verdict="AMBER")
    t.add("EDIT", "octave down", onset=F(1, 2))  # Fraction must serialize
    lines = t.to_jsonl().split("\n")
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["kind"] == "ORACLE"
    assert parsed[1]["data"]["onset"] == "1/2"  # Fraction -> string


def test_empty_trace_jsonl_is_empty() -> None:
    assert Trace().to_jsonl() == ""


def test_deterministic() -> None:
    a = Trace()
    b = Trace()
    for t in (a, b):
        t.add("SOLVE", "ok", n=3)
    assert a.to_jsonl() == b.to_jsonl()


def test_jsonl_is_canonical_across_mapping_insertion_order() -> None:
    a = Trace()
    b = Trace()
    a.add("PLAN", "ordered", z=1, a=2)
    b.add("PLAN", "ordered", a=2, z=1)

    assert a.to_jsonl() == b.to_jsonl()
    assert "NaN" not in a.to_jsonl()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_fail_closed(value: float) -> None:
    trace = Trace()
    trace.add("PLAN", "invalid", value=value)

    with pytest.raises(TraceInputError, match="finite") as caught:
        trace.to_jsonl()

    assert caught.value.path.endswith(".value[0]")


def test_nesting_and_node_limits_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    trace = Trace()
    trace.add("PLAN", "", value=[[["too deep"]]])
    monkeypatch.setattr(trace_module, "MAX_TRACE_JSON_DEPTH", 2)

    with pytest.raises(TraceInputError, match="nesting"):
        trace.to_jsonl()

    monkeypatch.setattr(trace_module, "MAX_TRACE_JSON_DEPTH", 64)
    monkeypatch.setattr(trace_module, "MAX_TRACE_JSON_NODES", 4)
    with pytest.raises(TraceInputError, match="value count"):
        trace.to_jsonl()


def test_scalar_and_output_size_limits_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    trace = Trace()
    trace.add("PLAN", "", value="x" * 17)
    monkeypatch.setattr(trace_module, "MAX_TRACE_SCALAR_BYTES", 16)

    with pytest.raises(TraceInputError, match="byte limit"):
        trace.to_jsonl()

    monkeypatch.setattr(trace_module, "MAX_TRACE_SCALAR_BYTES", 1024)
    monkeypatch.setattr(trace_module, "MAX_TRACE_JSONL_BYTES", 16)
    with pytest.raises(TraceInputError, match="JSONL output"):
        trace.to_jsonl()


def test_escaped_output_budget_is_proved_before_json_encoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = Trace()
    trace.add("PLAN", "\0" * 10)
    monkeypatch.setattr(trace_module, "MAX_TRACE_JSONL_BYTES", 20)

    def forbidden_encoder(*args: object, **kwargs: object) -> str:
        raise AssertionError("over-budget trace reached json.dumps")

    monkeypatch.setattr(trace_module.json, "dumps", forbidden_encoder)
    with pytest.raises(TraceInputError, match="JSONL output"):
        trace.to_jsonl()


_HOSTILE_HOOK_CALLS: list[str] = []


class _HostileMeta(type):
    def __getattribute__(cls, name: str) -> object:
        if name == "__name__":
            _HOSTILE_HOOK_CALLS.append("metaclass name")
            raise AssertionError("type-name hook must not run")
        return super().__getattribute__(name)


class _HostileObject(metaclass=_HostileMeta):
    def __repr__(self) -> str:
        _HOSTILE_HOOK_CALLS.append("repr")
        raise AssertionError("repr must not run")

    def __str__(self) -> str:
        _HOSTILE_HOOK_CALLS.append("str")
        raise AssertionError("str must not run")


def test_arbitrary_objects_fail_closed_without_executing_hooks() -> None:
    _HOSTILE_HOOK_CALLS.clear()
    trace = Trace()
    trace.add("PLAN", "invalid", value=_HostileObject())

    with pytest.raises(TraceInputError) as caught:
        trace.to_jsonl()

    assert caught.value.path.endswith(".value[0]")
    assert _HOSTILE_HOOK_CALLS == []


def test_cyclic_containers_fail_closed() -> None:
    value: list[object] = []
    value.append(value)
    trace = Trace()
    trace.add("PLAN", "invalid", value=value)

    with pytest.raises(TraceInputError, match="cyclic"):
        trace.to_jsonl()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("_numerator", object()),
        ("_denominator", object()),
        ("_denominator", 0),
        ("_denominator", -2),
        ("_numerator", 2),
    ],
)
def test_low_level_corrupted_fraction_is_typed_invalid(
    field: str,
    value: object,
) -> None:
    fraction = F(1, 2)
    object.__setattr__(fraction, field, value)
    trace = Trace()
    trace.add("PLAN", "invalid", value=fraction)

    with pytest.raises(TraceInputError, match="Fraction"):
        trace.to_jsonl()


def test_fraction_rendering_obeys_runtime_integer_string_limit() -> None:
    fraction = F(1 << 3_000, 1)
    trace = Trace()
    trace.add("PLAN", "large fraction", value=fraction)
    previous = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(640)
    try:
        with pytest.raises(TraceInputError, match="runtime integer limit"):
            trace.to_jsonl()
    finally:
        sys.set_int_max_str_digits(previous)


@pytest.mark.parametrize("field", ["_numerator", "_denominator"])
def test_fraction_with_deleted_component_is_typed_invalid(field: str) -> None:
    fraction = F(1, 2)
    object.__delattr__(fraction, field)
    trace = Trace()
    trace.add("PLAN", "invalid", value=fraction)

    with pytest.raises(TraceInputError, match="components are missing"):
        trace.to_jsonl()


def _source_line(function: object, fragment: str) -> int:
    source, start_line = inspect.getsourcelines(function)
    return start_line + next(index for index, line in enumerate(source) if fragment in line)


def test_step_snapshot_is_bounded_if_list_grows_after_length_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = Trace()
    trace.add("PLAN", "one")
    snapshot_line = _source_line(Trace.to_jsonl, "steps = tuple")
    mutated = False

    def grow(frame: object, event: str, arg: object) -> object:
        del arg
        nonlocal mutated
        if (
            not mutated
            and event == "line"
            and getattr(frame, "f_code", None) is Trace.to_jsonl.__code__
            and getattr(frame, "f_lineno", None) == snapshot_line
        ):
            trace.steps.extend([trace.steps[0]] * 10_000)
            mutated = True
        return grow

    monkeypatch.setattr(trace_module, "MAX_TRACE_STEPS", 2)
    sys.settrace(grow)
    try:
        with pytest.raises(TraceInputError, match="step count"):
            trace.to_jsonl()
    finally:
        sys.settrace(None)
    assert mutated


@pytest.mark.parametrize(
    ("value", "fragment"),
    [([], "sequence_items = tuple"), ({}, "dict_items: tuple")],
)
def test_nested_container_snapshot_is_bounded_if_it_grows_before_copy(
    value: object,
    fragment: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if type(value) is list:
        value.append(1)  # type: ignore[union-attr]
    else:
        value["one"] = 1  # type: ignore[index]
    trace = Trace()
    trace.add("PLAN", "one", value=value)
    snapshot_line = _source_line(trace_module._normalize_json_value, fragment)
    mutated = False

    def grow(frame: object, event: str, arg: object) -> object:
        del arg
        nonlocal mutated
        if (
            not mutated
            and event == "line"
            and getattr(frame, "f_code", None)
            is trace_module._normalize_json_value.__code__
            and getattr(frame, "f_lineno", None) == snapshot_line
        ):
            if type(value) is list:
                value.extend([1] * 10_000)
            else:
                value.update({str(index): index for index in range(10_000)})
            mutated = True
        return grow

    monkeypatch.setattr(trace_module, "MAX_TRACE_JSON_NODES", 8)
    sys.settrace(grow)
    try:
        with pytest.raises(TraceInputError, match="value count"):
            trace.to_jsonl()
    finally:
        sys.settrace(None)
    assert mutated
