"""Benchmark baselines.

B1 raw-LLM: ask a frontier LLM for a tab directly and use it *unverified* — the
"do we even need the agent?" control; it may be RED (unplayable). B2 pure-solver:
the rule-stub proposal + deterministic solver with no LLM/repair — the arranging-
free playability ceiling.
"""

import json

from fretsure.agent.arranger import ArrangeGoal
from fretsure.arrange.propose import propose_fingerstyle
from fretsure.ir import MusicIR
from fretsure.llm.client import LLMClient, extract_json
from fretsure.oracle.profiles import Profile
from fretsure.solver.api import Infeasible, solve_fingering
from fretsure.tab import Tab, validated_tab_from_json

_RAW_SYSTEM = (
    "You are a guitar tablature writer. Output ONLY a JSON tab in this exact schema: "
    '{"tuning": [40,45,50,55,59,64], "capo": 0, "notes": [{"onset": "<fraction>", '
    '"duration": "<fraction>", "string": <0-5>, "fret": <int>, "left_finger": <0-4>, '
    '"right_finger": "p|i|m|a"}, ...]}. string 0 = lowest-pitched.'
)


def baseline_raw_llm(
    ir: MusicIR,
    goal: ArrangeGoal,
    llm: LLMClient,
    profile: Profile,
) -> Tab | None:
    melody = "; ".join(
        f"onset={n.onset} pitch={n.pitch}" for n in ir.notes if n.voice == "melody"
    )
    try:
        reply = llm.complete(
            system=_RAW_SYSTEM, user=f"Melody: {melody}\nWrite a fingerstyle tab.", max_tokens=2048
        )
        return validated_tab_from_json(
            json.dumps(extract_json(reply)),
            profile=profile,
            tempo_bpm=goal.tempo_bpm,
        )
    except (ValueError, KeyError, TypeError, RuntimeError):
        return None


def baseline_pure_solver(
    ir: MusicIR, goal: ArrangeGoal, profile: Profile
) -> Tab | Infeasible:
    target = propose_fingerstyle(
        ir,
        goal.tuning,
        goal.capo,
        profile=profile,
        tempo_bpm=goal.tempo_bpm,
    )
    return solve_fingering(target, goal.tuning, goal.capo, profile, tempo_bpm=goal.tempo_bpm)
