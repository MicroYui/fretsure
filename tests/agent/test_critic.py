from fractions import Fraction as F

import pytest

from fretsure.agent.critic import (
    CRITIC_MAX_TOKENS,
    CriticOutcome,
    CriticScore,
    CriticStatus,
    critique,
    critique_outcome,
)
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import IRInputError, Meta, MusicIR, Note
from fretsure.llm.client import FakeLLM, LLMIntegrityError
from fretsure.tab import Tab, TabNote

_IR = MusicIR((Note(F(0), F(1), 64, "melody"),), (), Meta("C", (4, 4), 90.0, "t", "t", "PD"))
_TAB = Tab((TabNote(F(0), F(1), 5, 0, 0, "a"),), STANDARD_TUNING, 0)


def test_valid_scores_parsed() -> None:
    reply = '{"overall":0.8,"voice_leading":0.7,"bass_motion":0.9,"texture":0.6,"notes":"nice"}'
    s = critique(_IR, _TAB, FakeLLM([reply]))
    assert isinstance(s, CriticScore)
    assert s.overall == 0.8 and s.bass_motion == 0.9 and s.notes == "nice"


def test_critic_uses_the_public_fixed_output_budget() -> None:
    llm = FakeLLM(['{"overall":0.5}'])

    critique(_IR, _TAB, llm)

    assert llm.calls[0]["max_tokens"] == CRITIC_MAX_TOKENS == 512


def test_bad_output_is_neutral() -> None:
    s = critique(_IR, _TAB, FakeLLM(["not json"]))
    assert s.overall == 0.5


def test_critic_outcome_distinguishes_success_parse_fallback_and_call_failure() -> None:
    valid = critique_outcome(_IR, _TAB, FakeLLM(['{"overall":0.5}']))
    malformed = critique_outcome(_IR, _TAB, FakeLLM(["not json"]))

    class FailingLLM:
        model_id = "failing-test"

        def complete(self, **kwargs: object) -> str:
            del kwargs
            raise RuntimeError("redacted by the outcome boundary")

    failed = critique_outcome(_IR, _TAB, FailingLLM())

    assert isinstance(valid, CriticOutcome)
    assert valid.status is CriticStatus.LLM_SUCCESS
    assert malformed.status is CriticStatus.PARSE_VALIDATION_FALLBACK
    assert failed.status is CriticStatus.CALL_FAILURE_FALLBACK
    assert (valid.llm_calls, malformed.llm_calls, failed.llm_calls) == (1, 1, 1)
    assert not valid.fallback_assisted
    assert malformed.fallback_assisted and failed.fallback_assisted
    assert malformed.score.overall == failed.score.overall == 0.5


def test_critic_never_converts_integrity_failure_into_neutral_score() -> None:
    class IntegrityFailingLLM:
        model_id = "integrity-test"

        def complete(self, **kwargs: object) -> str:
            del kwargs
            raise LLMIntegrityError("formal observation failed")

    with pytest.raises(LLMIntegrityError, match="formal observation failed"):
        critique_outcome(_IR, _TAB, IntegrityFailingLLM())


def test_scores_clamped_to_unit() -> None:
    s = critique(_IR, _TAB, FakeLLM(['{"overall": 1.5, "voice_leading": -0.2}']))
    assert s.overall == 1.0 and s.voice_leading == 0.0


@pytest.mark.parametrize(
    "token",
    [
        "true",
        '"0.8"',
        "NaN",
        "Infinity",
        str(1 << 4096),
    ],
)
def test_invalid_critic_numeric_types_and_ranges_are_parse_fallback(token: str) -> None:
    outcome = critique_outcome(_IR, _TAB, FakeLLM([f'{{"overall":{token}}}']))

    assert outcome.status is CriticStatus.PARSE_VALIDATION_FALLBACK
    assert outcome.fallback_assisted
    assert outcome.score.overall == 0.5


def test_critic_rejects_hostile_ir_before_prompt_or_llm() -> None:
    class HostileKey:
        def __format__(self, _spec: str) -> str:
            raise AssertionError("hostile key reached prompt formatting")

    ir = MusicIR(_IR.notes, _IR.chords, Meta("C", (4, 4), 90.0, "t", "t", "PD"))
    object.__setattr__(ir.meta, "key", HostileKey())
    llm = FakeLLM([])

    with pytest.raises(IRInputError, match="meta.key"):
        critique(ir, _TAB, llm)

    assert llm.calls == []


@pytest.mark.integration
def test_real_critic_in_range() -> None:
    import os

    if not os.environ.get("ANTHROPIC_BASE_URL"):
        pytest.skip("no local LLM proxy configured")
    from fretsure.llm.client import ProxyLLM

    s = critique(_IR, _TAB, ProxyLLM())
    assert 0.0 <= s.overall <= 1.0
