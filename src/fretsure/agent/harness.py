"""Best-of-N arrange harness — the top-level agent entry point.

Generates N proposals (rising temperature), repairs each to GREEN via the
verifier-guided loop, scores the survivors (playable + faithful + critic taste),
and selects the best — emitting an explainable trace. All LLM use is injected,
so the selection is deterministic under FakeLLM.
"""

from dataclasses import dataclass

from fretsure.agent.arranger import ArrangeGoal, propose_arrangement
from fretsure.agent.critic import CriticScore, critique
from fretsure.agent.repair import RepairResult, repair
from fretsure.agent.trace import Trace
from fretsure.ir import MusicIR
from fretsure.llm.client import LLMClient
from fretsure.metrics.fidelity import Fidelity, fidelity
from fretsure.oracle.core import OracleResult
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.tab import Tab


@dataclass(frozen=True)
class ArrangeResult:
    """Best-of-N arrange result.

    NOTE: ``tab`` may be AMBER (borderline, NOT certified playable) when no
    candidate reached GREEN within budget. Check ``oracle.verdict == "GREEN"``
    before presenting a tab as provably playable.
    """

    tab: Tab | None
    oracle: OracleResult | None
    fidelity: Fidelity | None
    critic: CriticScore | None
    trace: Trace
    candidates_tried: int


@dataclass(frozen=True)
class _Candidate:
    is_green: bool
    fidelity: Fidelity
    critic: CriticScore | None
    repair: RepairResult


def _rank(c: _Candidate) -> tuple[int, float, float, float]:
    # prefer GREEN, then melody preservation, then critic taste, then harmony
    return (
        1 if c.is_green else 0,
        c.fidelity.melody_recall,
        c.critic.overall if c.critic is not None else 0.0,
        c.fidelity.harmony_jaccard,
    )


def arrange(
    ir: MusicIR,
    goal: ArrangeGoal,
    llm: LLMClient,
    *,
    profile: Profile = MEDIAN_HAND,
    n: int = 4,
    max_iters: int = 8,
) -> ArrangeResult:
    trace = Trace()
    scored: list[_Candidate] = []
    for i in range(n):
        temperature = min(1.0, 0.2 * i)
        target = propose_arrangement(ir, goal, llm, temperature=temperature)
        trace.add("PROPOSE", f"candidate {i}", i=i, temperature=temperature)
        rr = repair(
            target, goal.tuning, goal.capo, profile, llm,
            tempo_bpm=goal.tempo_bpm, max_iters=max_iters,
        )
        verdict = rr.oracle.verdict if rr.oracle is not None else "INFEASIBLE"
        trace.add("SOLVE", f"candidate {i}: {verdict}", i=i, verdict=verdict)
        if rr.tab is None:
            continue
        fid = fidelity(ir, rr.tab)
        is_green = rr.oracle is not None and rr.oracle.verdict == "GREEN"
        crit = critique(ir, rr.tab, llm) if is_green else None
        scored.append(_Candidate(is_green, fid, crit, rr))

    if not scored:
        trace.add("SELECT", "no feasible candidate found")
        return ArrangeResult(None, None, None, None, trace, n)

    best = max(scored, key=_rank)
    trace.steps.extend(best.repair.trace.steps)  # surface the winner's repair reasoning
    trace.add(
        "SELECT",
        f"green={best.is_green} melody_recall={best.fidelity.melody_recall:.2f}",
    )
    return ArrangeResult(
        best.repair.tab, best.repair.oracle, best.fidelity, best.critic, trace, n
    )
