from fractions import Fraction as F

import pytest

from fretsure.agent.critic import CriticScore, critique
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import Meta, MusicIR, Note
from fretsure.llm.client import FakeLLM
from fretsure.tab import Tab, TabNote

_IR = MusicIR((Note(F(0), F(1), 64, "melody"),), (), Meta("C", (4, 4), 90.0, "t", "t", "PD"))
_TAB = Tab((TabNote(F(0), F(1), 5, 0, 0, "a"),), STANDARD_TUNING, 0)


def test_valid_scores_parsed() -> None:
    reply = '{"overall":0.8,"voice_leading":0.7,"bass_motion":0.9,"texture":0.6,"notes":"nice"}'
    s = critique(_IR, _TAB, FakeLLM([reply]))
    assert isinstance(s, CriticScore)
    assert s.overall == 0.8 and s.bass_motion == 0.9 and s.notes == "nice"


def test_bad_output_is_neutral() -> None:
    s = critique(_IR, _TAB, FakeLLM(["not json"]))
    assert s.overall == 0.5


def test_scores_clamped_to_unit() -> None:
    s = critique(_IR, _TAB, FakeLLM(['{"overall": 1.5, "voice_leading": -0.2}']))
    assert s.overall == 1.0 and s.voice_leading == 0.0


@pytest.mark.integration
def test_real_critic_in_range() -> None:
    import os

    if not os.environ.get("ANTHROPIC_BASE_URL"):
        pytest.skip("no local LLM proxy configured")
    from fretsure.llm.client import ProxyLLM

    s = critique(_IR, _TAB, ProxyLLM())
    assert 0.0 <= s.overall <= 1.0
