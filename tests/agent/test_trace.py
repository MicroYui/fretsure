import json
from fractions import Fraction as F

from fretsure.agent.trace import Trace, TraceStep


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
