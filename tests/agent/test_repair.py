from fractions import Fraction as F

import pytest

from fretsure.agent.repair import repair
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import Note
from fretsure.llm.client import FakeLLM
from fretsure.oracle.profiles import MEDIAN_HAND

# 85 (melody) + 86 (harmony) each reach only the high-E string -> infeasible together.
_INFEASIBLE = (Note(F(0), F(1), 85, "melody"), Note(F(0), F(1), 86, "harmony"))
_DROP_86 = '{"op": "drop_note", "target_onset": "0", "target_pitch": 86}'
_DROP_85 = '{"op": "drop_note", "target_onset": "0", "target_pitch": 85}'  # melody -> protected


def test_already_green_returns_immediately() -> None:
    target = (Note(F(0), F(1), 60, "melody"),)
    r = repair(target, STANDARD_TUNING, 0, MEDIAN_HAND, FakeLLM([]))
    assert r.oracle is not None and r.oracle.verdict == "GREEN"
    assert r.iterations == 0


def test_repair_drops_harmony_to_reach_green() -> None:
    r = repair(_INFEASIBLE, STANDARD_TUNING, 0, MEDIAN_HAND, FakeLLM([_DROP_86]))
    assert r.oracle is not None and r.oracle.verdict == "GREEN"
    assert r.tab is not None
    assert r.iterations == 1
    assert 85 in [n.pitch for n in r.target]  # melody preserved
    assert 86 not in [n.pitch for n in r.target]
    assert any(s.kind == "EDIT" for s in r.trace.steps)


def test_melody_protected_edit_is_skipped_then_valid_edit_applied() -> None:
    r = repair(_INFEASIBLE, STANDARD_TUNING, 0, MEDIAN_HAND, FakeLLM([_DROP_85, _DROP_86]))
    assert r.oracle is not None and r.oracle.verdict == "GREEN"
    assert r.iterations == 2  # first attempt protected, second worked
    assert any("protect" in s.detail.lower() for s in r.trace.steps)


def test_max_iters_stops_without_crash() -> None:
    r = repair(_INFEASIBLE, STANDARD_TUNING, 0, MEDIAN_HAND, FakeLLM([_DROP_85] * 3), max_iters=2)
    assert r.iterations == 2
    assert r.tab is None and r.infeasible is not None  # never reached GREEN


def test_deterministic() -> None:
    a = repair(_INFEASIBLE, STANDARD_TUNING, 0, MEDIAN_HAND, FakeLLM([_DROP_86]))
    b = repair(_INFEASIBLE, STANDARD_TUNING, 0, MEDIAN_HAND, FakeLLM([_DROP_86]))
    assert a.tab == b.tab and a.iterations == b.iterations


@pytest.mark.integration
def test_repair_with_real_llm_reaches_green() -> None:
    import os

    if not os.environ.get("ANTHROPIC_BASE_URL"):
        pytest.skip("no local LLM proxy configured")
    from fretsure.llm.client import ProxyLLM

    r = repair(_INFEASIBLE, STANDARD_TUNING, 0, MEDIAN_HAND, ProxyLLM(), max_iters=5)
    assert r.oracle is not None and r.oracle.verdict == "GREEN"
    assert 85 in [n.pitch for n in r.target]  # melody preserved by the real LLM
