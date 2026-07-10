from fractions import Fraction as F

import pytest

from fretsure.agent.arranger import ArrangeGoal, propose_arrangement
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import ChordSymbol, Meta, MusicIR, Note
from fretsure.llm.client import FakeLLM


def _meta() -> Meta:
    return Meta("C", (4, 4), 90.0, "t", "t", "PD")


def _leadsheet() -> MusicIR:
    return MusicIR(
        (Note(F(0), F(1), 64, "melody"), Note(F(0), F(1), 40, "bass")),
        (ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 0),),
        _meta(),
    )


_VALID = (
    '{"notes": [{"onset":"0","duration":"1","pitch":64,"voice":"melody"},'
    '{"onset":"0","duration":"1","pitch":47,"voice":"bass"},'
    '{"onset":"0","duration":"1","pitch":55,"voice":"harmony"}]}'
)


def test_parses_valid_arrangement_with_melody() -> None:
    notes = propose_arrangement(_leadsheet(), ArrangeGoal(), FakeLLM([_VALID]))
    pitches = [n.pitch for n in notes]
    assert 64 in pitches and 55 in pitches
    assert any(n.voice == "melody" for n in notes)


def test_bad_json_falls_back_to_rule_stub() -> None:
    notes = propose_arrangement(_leadsheet(), ArrangeGoal(), FakeLLM(["not json at all"]))
    # fallback = propose_fingerstyle -> melody + bass from the IR
    pitches = {n.pitch for n in notes}
    assert 64 in pitches and 40 in pitches


def test_no_melody_in_reply_falls_back() -> None:
    reply = '{"notes": [{"onset":"0","duration":"1","pitch":40,"voice":"bass"}]}'
    notes = propose_arrangement(_leadsheet(), ArrangeGoal(), FakeLLM([reply]))
    assert any(n.voice == "melody" for n in notes)  # fallback restores melody


@pytest.mark.integration
def test_real_llm_proposes_parseable_arrangement() -> None:
    import os

    if not os.environ.get("ANTHROPIC_BASE_URL"):
        pytest.skip("no local LLM proxy configured")
    from fretsure.llm.client import ProxyLLM

    notes = propose_arrangement(_leadsheet(), ArrangeGoal(tuning=STANDARD_TUNING), ProxyLLM())
    assert any(n.voice == "melody" for n in notes)
