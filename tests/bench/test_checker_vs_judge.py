from fractions import Fraction as F

from fretsure.bench.checker_vs_judge import JudgeComparison, checker_vs_judge, llm_judge
from fretsure.geometry import STANDARD_TUNING
from fretsure.llm.client import FakeLLM
from fretsure.tab import Tab, TabNote

# unplayable: fret 1 + fret 15 in one frame (oracle -> RED)
_RED = Tab(
    (TabNote(F(0), F(1), 0, 1, 1, "p"), TabNote(F(0), F(1), 1, 15, 4, "i")), STANDARD_TUNING, 0
)
# playable: 2-string barre at fret 2 (oracle -> GREEN)
_GREEN = Tab(
    (TabNote(F(0), F(1), 0, 2, 1, "p"), TabNote(F(0), F(1), 1, 2, 1, "i")), STANDARD_TUNING, 0
)


def test_llm_judge_parses_verdict() -> None:
    assert llm_judge(_GREEN, FakeLLM(["Yes, PLAYABLE."])) == "PLAYABLE"
    assert llm_judge(_GREEN, FakeLLM(["This is UNPLAYABLE"])) == "UNPLAYABLE"


def test_judge_false_accepts_where_oracle_does_not() -> None:
    # the LLM judge says PLAYABLE for both; the oracle correctly RED-flags the first
    labeled = [(_RED, False), (_GREEN, True)]
    cmp = checker_vs_judge(labeled, FakeLLM(["PLAYABLE", "PLAYABLE"]))
    assert isinstance(cmp, JudgeComparison)
    assert cmp.oracle_false_accept == 0
    assert cmp.judge_false_accept == 1
    assert cmp.judge_false_accept > cmp.oracle_false_accept  # the headline
    assert cmp.oracle_correct == 2 and cmp.judge_correct == 1


def test_mcnemar_computed() -> None:
    cmp = checker_vs_judge([(_RED, False), (_GREEN, True)], FakeLLM(["PLAYABLE", "PLAYABLE"]))
    assert cmp.mcnemar >= 0.0
