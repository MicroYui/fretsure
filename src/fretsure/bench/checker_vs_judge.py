"""checker-vs-LLM-judge experiment — the benchmark's central rhetoric.

Playability is a physical fact, so it is scored by the deterministic oracle, not
an LLM judge. This experiment quantifies WHY: on labeled tabs (including
adversarial near-misses), the LLM judge false-accepts unplayable tabs at a higher
rate than the oracle. The LLM is injected, so it is deterministic under FakeLLM.
"""

from dataclasses import dataclass

from fretsure.llm.client import LLMClient
from fretsure.oracle.core import check_playability
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.render.ascii import render_ascii
from fretsure.tab import Tab

_JUDGE_SYSTEM = (
    "You are judging whether a guitar tab is physically playable by human hands. "
    "Reply with exactly one word: PLAYABLE or UNPLAYABLE."
)


def llm_judge(tab: Tab, llm: LLMClient) -> str:
    reply = llm.complete(system=_JUDGE_SYSTEM, user=render_ascii(tab), max_tokens=16).upper()
    if "UNPLAYABLE" in reply:
        return "UNPLAYABLE"
    if "PLAYABLE" in reply:
        return "PLAYABLE"
    return "UNPLAYABLE"


@dataclass(frozen=True)
class JudgeComparison:
    oracle_false_accept: int  # oracle says playable but human says unplayable
    judge_false_accept: int  # judge says playable but human says unplayable
    oracle_correct: int
    judge_correct: int
    n: int
    mcnemar: float


def checker_vs_judge(
    labeled: list[tuple[Tab, bool]], llm: LLMClient, profile: Profile = MEDIAN_HAND
) -> JudgeComparison:
    oracle_fa = judge_fa = oracle_ok = judge_ok = 0
    b = c = 0  # discordant pairs for McNemar (oracle-only-correct, judge-only-correct)
    for tab, human_playable in labeled:
        oracle_playable = check_playability(tab, profile).verdict == "GREEN"
        judge_playable = llm_judge(tab, llm) == "PLAYABLE"
        if oracle_playable and not human_playable:
            oracle_fa += 1
        if judge_playable and not human_playable:
            judge_fa += 1
        o_correct = oracle_playable == human_playable
        j_correct = judge_playable == human_playable
        oracle_ok += int(o_correct)
        judge_ok += int(j_correct)
        if o_correct and not j_correct:
            b += 1
        if j_correct and not o_correct:
            c += 1
    stat = (abs(b - c) - 1) ** 2 / (b + c) if (b + c) > 0 else 0.0
    return JudgeComparison(oracle_fa, judge_fa, oracle_ok, judge_ok, len(labeled), stat)
