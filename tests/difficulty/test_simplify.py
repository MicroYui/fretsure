from fractions import Fraction as F

import pytest

from fretsure.difficulty.simplify import SimplifyResult, simplify_to_tier
from fretsure.difficulty.tiers import BEGINNER
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import Note
from fretsure.llm.client import FakeLLM

# 3 open notes at one onset: playable but exceeds beginner's max 2 simultaneous
_TARGET = (
    Note(F(0), F(1), 64, "melody"),
    Note(F(0), F(1), 40, "bass"),
    Note(F(0), F(1), 55, "harmony"),
)
_DROP = '{"op": "drop_note", "target_onset": "0", "target_pitch": 55}'


def test_simplify_dense_chord_to_beginner() -> None:
    r = simplify_to_tier(_TARGET, BEGINNER, STANDARD_TUNING, 0, FakeLLM([_DROP]))
    assert isinstance(r, SimplifyResult)
    assert r.tier_result is not None and r.tier_result.meets
    assert r.iterations == 1
    assert 64 in [n.pitch for n in r.target]  # melody preserved
    assert 55 not in [n.pitch for n in r.target]


def test_already_meets_zero_iterations() -> None:
    target = (Note(F(0), F(1), 64, "melody"), Note(F(0), F(1), 40, "bass"))
    r = simplify_to_tier(target, BEGINNER, STANDARD_TUNING, 0, FakeLLM([]))
    assert r.tier_result is not None and r.tier_result.meets
    assert r.iterations == 0


def test_deterministic() -> None:
    a = simplify_to_tier(_TARGET, BEGINNER, STANDARD_TUNING, 0, FakeLLM([_DROP]))
    b = simplify_to_tier(_TARGET, BEGINNER, STANDARD_TUNING, 0, FakeLLM([_DROP]))
    assert a.tab == b.tab and a.iterations == b.iterations


@pytest.mark.integration
def test_real_llm_simplifies_to_beginner() -> None:
    import os

    if not os.environ.get("ANTHROPIC_BASE_URL"):
        pytest.skip("no local LLM proxy configured")
    from fretsure.llm.client import ProxyLLM

    r = simplify_to_tier(_TARGET, BEGINNER, STANDARD_TUNING, 0, ProxyLLM(), max_iters=5)
    assert r.tier_result is not None and r.tier_result.meets
    assert 64 in [n.pitch for n in r.target]  # melody preserved
