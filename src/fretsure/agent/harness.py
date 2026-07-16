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
from fretsure.agent.trace import Trace, TraceStep, target_checkpoint
from fretsure.ir import MusicIR, snapshot_music_ir
from fretsure.llm.client import LLMClient
from fretsure.metrics.fidelity import FaithfulnessGate, Fidelity, faithfulness, fidelity
from fretsure.oracle.core import OracleResult
from fretsure.oracle.input import (
    ensure_boolean_control,
    ensure_candidate_count,
    ensure_repair_iterations,
    ensure_solver_domain,
)
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
    index: int
    is_green: bool
    fidelity: Fidelity
    faithfulness: FaithfulnessGate
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
    candidate_traces: tuple[tuple[TraceStep, ...], ...] = ()


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
    ir = snapshot_music_ir(ir)
    n = ensure_candidate_count(n)
    max_iters = ensure_repair_iterations(max_iters)
    use_critic = ensure_boolean_control(use_critic, path="use_critic")
    notes, tuning, capo, profile, tempo_bpm = ensure_solver_domain(
        ir.notes,
        goal.tuning,
        goal.capo,
        profile,
        tempo_bpm=goal.tempo_bpm,
    )
    ir = MusicIR(notes, tuple(ir.chords), ir.meta)
    goal = ArrangeGoal(
        style=goal.style,
        tier=goal.tier,
        tuning=tuning,
        capo=capo,
        tempo_bpm=tempo_bpm,
        extras=goal.extras,
    )
    trace = Trace()
    slots: list[_Candidate | None] = []
    candidate_traces: list[tuple[TraceStep, ...]] = []
    for i in range(n):
        candidate_trace = Trace()
        temperature = min(1.0, 0.2 * i)
        target = propose_arrangement(
            ir,
            goal,
            llm,
            temperature=temperature,
            profile=profile,
        )
        candidate_trace.add(
            "PROPOSE",
            f"Candidate {i} produced a bounded target-note checkpoint.",
            event="CANDIDATE_PROPOSED",
            candidate_index=i,
            temperature=temperature,
            target_checkpoint=target_checkpoint(target),
        )
        rr = repair(
            target,
            goal.tuning,
            goal.capo,
            profile,
            llm,
            tempo_bpm=goal.tempo_bpm,
            max_iters=max_iters,
            candidate_index=i,
        )
        candidate_trace.steps.extend(rr.trace.steps)
        verdict = rr.oracle.verdict if rr.oracle is not None else "INFEASIBLE"
        candidate_trace.add(
            "SOLVE",
            f"Candidate {i} finished with {verdict}.",
            event="CANDIDATE_FINISHED",
            candidate_index=i,
            iteration=rr.iterations,
            verdict=verdict,
            tab_available=rr.tab is not None,
            repair_iterations=rr.iterations,
        )
        trace.steps.extend(candidate_trace.steps)
        candidate_traces.append(tuple(candidate_trace.steps))
        if rr.tab is None:
            slots.append(None)
            continue
        fid = fidelity(ir, rr.tab)
        faith = faithfulness(ir, rr.tab)
        is_green = rr.oracle is not None and rr.oracle.verdict == "GREEN"
        crit = critique(ir, rr.tab, llm) if (is_green and use_critic) else None
        slots.append(_Candidate(i, is_green, fid, faith, crit, rr))
    return ArrangePool(tuple(slots), trace, n, tuple(candidate_traces))


def _retained_candidate_steps(pool: ArrangePool, index: int) -> tuple[TraceStep, ...]:
    """Return one complete candidate replay, tolerating pre-6A pool fixtures."""

    if len(pool.candidate_traces) == pool.n:
        return pool.candidate_traces[index]
    return tuple(pool.trace.steps)


def _failed_candidate_to_retain(pool: ArrangePool, k: int) -> int | None:
    """Choose one bounded failure replay, preferring a model-call diagnostic."""

    if k == 0:
        return None
    if len(pool.candidate_traces) == pool.n:
        for index, steps in enumerate(pool.candidate_traces[:k]):
            if any(step.event == "MODEL_CALL_FAILED" for step in steps):
                return index
    return 0


def best_of_k(pool: ArrangePool, k: int, *, use_critic: bool = True) -> ArrangeResult:
    """Select the best among the first ``k`` candidates of ``pool`` (paired-safe).

    ``use_critic=False`` selects while ignoring the critic term — used by the paired
    critic ablation to vary only the ranking objective over a fixed pool.
    """
    pool_n = ensure_candidate_count(pool.n, path="pool.n")
    k = min(ensure_candidate_count(k, path="k"), pool_n)
    use_critic = ensure_boolean_control(use_critic, path="use_critic")
    scored = [c for c in pool.candidates[:k] if c is not None]
    trace = Trace()
    if not scored:
        failed_index = _failed_candidate_to_retain(pool, k)
        if failed_index is not None:
            trace.steps.extend(_retained_candidate_steps(pool, failed_index))
        trace.add(
            "SELECT",
            "No candidate returned a tablature result within the bounded search.",
            event="NO_CANDIDATE_SELECTED",
            winner_candidate_index=None,
            candidates_considered=k,
            playability_gate=None,
            faithfulness_passed=None,
        )
        return ArrangeResult(None, None, None, None, trace, k)
    best = max(scored, key=lambda c: _rank(c, use_critic=use_critic))
    # Retain one complete replay in execution order.  This keeps the public
    # trace bounded independently of N while preserving every winner step from
    # proposal through repair and finish.
    trace.steps.extend(_retained_candidate_steps(pool, best.index))
    verdict = best.repair.oracle.verdict if best.repair.oracle is not None else None
    trace.add(
        "SELECT",
        f"Selected candidate {best.index}; playability and fidelity remain separate gates.",
        event="CANDIDATE_SELECTED",
        candidate_index=best.index,
        winner_candidate_index=best.index,
        candidates_considered=k,
        verdict=verdict,
        green_certified=best.is_green,
        playability_gate="passed" if best.is_green else "not_passed",
        faithfulness_passed=best.faithfulness.passed,
        melody_recall=best.fidelity.melody_recall,
        bass_preserved=best.fidelity.bass_preserved,
        harmony_jaccard=best.fidelity.harmony_jaccard,
        critic_status="SCORED" if best.critic is not None else "NOT_RUN",
        critic_overall=best.critic.overall if best.critic is not None else None,
    )
    return ArrangeResult(best.repair.tab, best.repair.oracle, best.fidelity, best.critic, trace, k)


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
