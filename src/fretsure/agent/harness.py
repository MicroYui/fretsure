"""Best-of-N arrange harness — the top-level agent entry point.

Generates N proposals (rising temperature), repairs each to GREEN via the
verifier-guided loop, scores the survivors (playable + faithful + critic taste),
and selects the best — emitting an explainable trace. All LLM use is injected,
so the selection is deterministic under FakeLLM.

The pool build (:func:`arrange_pool`) and the selection (:func:`best_of_k`) are
split so best-of-N can be measured *paired*: build one proposal pool, then
compare best-of-1 vs best-of-N on the SAME pool, isolating selection breadth
from the stochastic-draw noise that confounds an unpaired ablation.
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
    before presenting a tab as carrying a model-relative GREEN certification.
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


@dataclass(frozen=True)
class ArrangePool:
    """An ordered pool of repaired candidates (index 0 = the greedy temp-0 draw).

    ``candidates[i]`` is ``None`` when proposal ``i`` had no feasible arrangement,
    so slot order (and thus "best-of-1 == the greedy draw") is preserved.
    """

    candidates: tuple[_Candidate | None, ...]
    trace: Trace
    n: int


def _rank(c: _Candidate, *, use_critic: bool = True) -> tuple[int, float, float, float, float]:
    # prefer GREEN, then melody preservation, then bass preservation (both are
    # faithfulness to the input, which the joint gate scores), then critic taste,
    # then harmony. Bass sits above critic so we never trade the bass for taste.
    # use_critic=False zeroes the critic term (for the paired critic ablation).
    critic = c.critic.overall if (use_critic and c.critic is not None) else 0.0
    return (
        1 if c.is_green else 0,
        c.fidelity.melody_recall,
        c.fidelity.bass_preserved,
        critic,
        c.fidelity.harmony_jaccard,
    )


def arrange_pool(
    ir: MusicIR,
    goal: ArrangeGoal,
    llm: LLMClient,
    *,
    profile: Profile = MEDIAN_HAND,
    n: int = 4,
    max_iters: int = 8,
    use_critic: bool = True,
) -> ArrangePool:
    """Build the ordered pool of N repaired candidates (no selection)."""
    n = max(0, n)  # n<=0 -> empty pool (no proposals, no LLM calls), matching best-of-0
    trace = Trace()
    slots: list[_Candidate | None] = []
    for i in range(n):
        temperature = min(1.0, 0.2 * i)
        target = propose_arrangement(
            ir,
            goal,
            llm,
            temperature=temperature,
        )
        trace.add("PROPOSE", f"candidate {i}", i=i, temperature=temperature)
        rr = repair(
            target, goal.tuning, goal.capo, profile, llm,
            tempo_bpm=goal.tempo_bpm, max_iters=max_iters,
        )
        verdict = rr.oracle.verdict if rr.oracle is not None else "INFEASIBLE"
        trace.add("SOLVE", f"candidate {i}: {verdict}", i=i, verdict=verdict)
        if rr.tab is None:
            slots.append(None)
            continue
        fid = fidelity(ir, rr.tab)
        is_green = rr.oracle is not None and rr.oracle.verdict == "GREEN"
        crit = critique(ir, rr.tab, llm) if (is_green and use_critic) else None
        slots.append(_Candidate(is_green, fid, crit, rr))
    return ArrangePool(tuple(slots), trace, n)


def best_of_k(pool: ArrangePool, k: int, *, use_critic: bool = True) -> ArrangeResult:
    """Select the best among the first ``k`` candidates of ``pool`` (paired-safe).

    ``use_critic=False`` selects while ignoring the critic term — used by the paired
    critic ablation to vary only the ranking objective over a fixed pool.
    """
    k = max(0, min(k, pool.n))  # k=0 (empty pool) -> no-candidate result, candidates_tried=0
    scored = [c for c in pool.candidates[:k] if c is not None]
    trace = Trace()
    trace.steps.extend(pool.trace.steps)
    if not scored:
        trace.add("SELECT", "no feasible candidate found")
        return ArrangeResult(None, None, None, None, trace, k)
    best = max(scored, key=lambda c: _rank(c, use_critic=use_critic))
    trace.steps.extend(best.repair.trace.steps)  # surface the winner's repair reasoning
    trace.add(
        "SELECT",
        f"green={best.is_green} melody_recall={best.fidelity.melody_recall:.2f}",
    )
    return ArrangeResult(
        best.repair.tab, best.repair.oracle, best.fidelity, best.critic, trace, k
    )


def arrange(
    ir: MusicIR,
    goal: ArrangeGoal,
    llm: LLMClient,
    *,
    profile: Profile = MEDIAN_HAND,
    n: int = 4,
    max_iters: int = 8,
    use_critic: bool = True,
) -> ArrangeResult:
    pool = arrange_pool(
        ir, goal, llm, profile=profile, n=n, max_iters=max_iters, use_critic=use_critic
    )
    return best_of_k(pool, pool.n)
