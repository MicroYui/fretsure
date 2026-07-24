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

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from fretsure.agent.arranger import (
    ArrangeGoal,
    ProposalOutcome,
    arrangement_solver_ir,
    propose_arrangement_outcome,
)
from fretsure.agent.critic import CriticOutcome, CriticScore, critique_outcome
from fretsure.agent.model_calls import ModelCallScopeFactory
from fretsure.agent.repair import RepairSnapshot, repair
from fretsure.agent.tools import solve_and_check
from fretsure.agent.trace import (
    StepKind,
    Trace,
    TraceEvent,
    TraceStep,
    oracle_detail,
    oracle_trace_payload,
    target_checkpoint,
)
from fretsure.arrange.propose import propose_fingerstyle
from fretsure.ir import MusicIR, Note, snapshot_music_ir
from fretsure.llm.client import LLMClient
from fretsure.metrics.fidelity import FaithfulnessGate, Fidelity, faithfulness, fidelity
from fretsure.oracle.core import OracleResult
from fretsure.oracle.input import (
    MAX_AGENT_CANDIDATES,
    MAX_AGENT_REPAIR_ITERS,
    ensure_boolean_control,
    ensure_candidate_count,
    ensure_repair_iterations,
    ensure_solver_domain,
)
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.solver.api import Infeasible
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


class CandidateStatus(StrEnum):
    """Stable terminal state for one candidate-pool slot."""

    GREEN = "GREEN"
    NON_GREEN_TAB = "NON_GREEN_TAB"
    NO_TAB = "NO_TAB"


@dataclass(frozen=True, slots=True)
class CandidateWorkCounts:
    """Bounded logical work performed for one candidate.

    Provider-internal retries are intentionally not inferred here; these are the
    calls made by the agent policy and deterministic solver.
    """

    proposal_llm_calls: int
    repair_llm_calls: int
    critic_llm_calls: int
    solver_calls: int

    def __post_init__(self) -> None:
        bounds = (
            ("proposal_llm_calls", self.proposal_llm_calls, 1),
            ("repair_llm_calls", self.repair_llm_calls, MAX_AGENT_REPAIR_ITERS),
            ("critic_llm_calls", self.critic_llm_calls, 1),
            ("solver_calls", self.solver_calls, MAX_AGENT_REPAIR_ITERS + 1),
        )
        for name, value, maximum in bounds:
            minimum = 1 if name == "solver_calls" else 0
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be an exact integer in {minimum}..{maximum}")

    @property
    def total_llm_calls(self) -> int:
        return self.proposal_llm_calls + self.repair_llm_calls + self.critic_llm_calls


def _canonical_trace_data(data: object) -> bytes:
    if type(data) is not dict:
        raise ValueError("trace snapshot data must be an exact object")
    try:
        return json.dumps(
            data,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeError):
        raise ValueError("trace snapshot data must be canonical JSON") from None


@dataclass(frozen=True, slots=True)
class TraceStepSnapshot:
    """Deeply immutable, canonically encoded copy of one public trace step."""

    kind: StepKind
    detail: str
    data_json: bytes
    event: TraceEvent | None
    candidate_index: int | None
    iteration: int | None

    def __post_init__(self) -> None:
        if type(self.data_json) is not bytes:
            raise ValueError("trace snapshot data_json must be exact bytes")
        step = self.to_trace_step()
        if _canonical_trace_data(step.data) != self.data_json:
            raise ValueError("trace snapshot data_json must use canonical encoding")

    @classmethod
    def from_trace_step(cls, step: TraceStep) -> "TraceStepSnapshot":
        if type(step) is not TraceStep:
            raise ValueError("trace snapshot source must be an exact TraceStep")
        return cls(
            step.kind,
            step.detail,
            _canonical_trace_data(step.data),
            step.event,
            step.candidate_index,
            step.iteration,
        )

    def to_trace_step(self) -> TraceStep:
        try:
            raw = json.loads(self.data_json)
        except (json.JSONDecodeError, UnicodeError):
            raise ValueError("trace snapshot data_json is not valid JSON") from None
        if type(raw) is not dict:
            raise ValueError("trace snapshot data_json must encode an object")
        return TraceStep(
            self.kind,
            self.detail,
            cast(dict[str, Any], raw),
            self.event,
            self.candidate_index,
            self.iteration,
        )


@dataclass(frozen=True, slots=True)
class CandidateTrajectory:
    """Immutable, lossless outcome for one ordered candidate-pool slot."""

    index: int
    temperature: float
    proposal: ProposalOutcome
    initial_target: tuple[Note, ...]
    iteration_zero: RepairSnapshot
    terminal: RepairSnapshot
    status: CandidateStatus
    is_green: bool
    fidelity: Fidelity | None
    faithfulness: FaithfulnessGate | None
    critic_outcome: CriticOutcome | None
    trace_snapshots: tuple[TraceStepSnapshot, ...]
    work: CandidateWorkCounts

    def __post_init__(self) -> None:
        if type(self.index) is not int or not 0 <= self.index < MAX_AGENT_CANDIDATES:
            raise ValueError("index must be an exact bounded candidate index")
        if (
            type(self.temperature) is not float
            or not math.isfinite(self.temperature)
            or not 0.0 <= self.temperature <= 1.0
        ):
            raise ValueError("temperature must be an exact finite float in 0..1")
        if type(self.initial_target) is not tuple or self.initial_target != self.proposal.target:
            raise ValueError("initial_target must match the proposal target")
        if self.iteration_zero.target != self.initial_target:
            raise ValueError("iteration_zero must solve the initial target")
        if type(self.status) is not CandidateStatus or type(self.is_green) is not bool:
            raise ValueError("candidate status fields are malformed")
        if type(self.trace_snapshots) is not tuple or not self.trace_snapshots:
            raise ValueError("trace_snapshots must be a non-empty exact tuple")
        if any(type(step) is not TraceStepSnapshot for step in self.trace_snapshots):
            raise ValueError("trace_snapshots must contain exact TraceStepSnapshot values")
        if self.work.proposal_llm_calls != self.proposal.llm_calls:
            raise ValueError("proposal work count disagrees with proposal outcome")
        if self.work.solver_calls != self.terminal.iteration + 1:
            raise ValueError("solver work count disagrees with terminal iteration")
        repair_events = sum(
            step.event in {"REPAIR_EDIT_PROPOSED", "MODEL_EDIT_INVALID", "MODEL_CALL_FAILED"}
            for step in self.trace_steps
        )
        if self.work.repair_llm_calls != repair_events:
            raise ValueError("repair work count disagrees with candidate trace")
        expected_critic_calls = (
            self.critic_outcome.llm_calls if self.critic_outcome is not None else 0
        )
        if self.work.critic_llm_calls != expected_critic_calls:
            raise ValueError("critic work count disagrees with critic outcome")

        has_tab = self.terminal.tab is not None
        has_fidelity = self.fidelity is not None
        has_faithfulness = self.faithfulness is not None
        if has_fidelity is not has_faithfulness or has_tab is not has_fidelity:
            raise ValueError("only candidates with tabs may carry fidelity scores")
        oracle_green = self.terminal.oracle is not None and self.terminal.oracle.verdict == "GREEN"
        expected_status = (
            CandidateStatus.GREEN
            if oracle_green
            else CandidateStatus.NON_GREEN_TAB
            if has_tab
            else CandidateStatus.NO_TAB
        )
        if self.status is not expected_status or self.is_green is not oracle_green:
            raise ValueError("candidate status disagrees with its terminal result")
        if self.critic_outcome is not None and not self.is_green:
            raise ValueError("only a GREEN candidate may carry a critic score")

    @property
    def critic(self) -> CriticScore | None:
        """Compatibility score view; benchmark code should retain ``critic_outcome``."""

        return self.critic_outcome.score if self.critic_outcome is not None else None

    @property
    def trace_steps(self) -> tuple[TraceStep, ...]:
        """Return detached public-trace copies without exposing stored mutable dicts."""

        return tuple(step.to_trace_step() for step in self.trace_snapshots)


@dataclass(frozen=True, slots=True)
class DeterministicBaseline:
    """One zero-model fallback target and its bounded solver result."""

    target: tuple[Note, ...]
    tab: Tab | None
    oracle: OracleResult | None
    infeasible: Infeasible | None
    fidelity: Fidelity | None
    faithfulness: FaithfulnessGate | None

    def __post_init__(self) -> None:
        if type(self.target) is not tuple:
            raise ValueError("baseline target must be an exact tuple")
        has_tab = self.tab is not None
        if has_tab:
            if (
                type(self.tab) is not Tab
                or type(self.oracle) is not OracleResult
                or self.infeasible is not None
                or type(self.fidelity) is not Fidelity
                or type(self.faithfulness) is not FaithfulnessGate
            ):
                raise ValueError("a solved baseline requires both gates and no failure")
        elif (
            self.oracle is not None
            or type(self.infeasible) is not Infeasible
            or self.fidelity is not None
            or self.faithfulness is not None
        ):
            raise ValueError("an unsolved baseline requires one bounded-search failure")


@dataclass(frozen=True)
class ArrangePool:
    """An ordered pool of repaired candidates (index 0 = the greedy temp-0 draw).

    ``candidates[i]`` is ``None`` when proposal ``i`` had no feasible arrangement,
    so slot order (and thus "best-of-1 == the greedy draw") is preserved.  The
    deterministic baseline is stored separately and consumes neither a model slot
    nor a candidate index; selection consults it only when the requested model
    prefix contains no tablature result.
    """

    candidates: tuple[CandidateTrajectory | None, ...]
    trace: Trace
    n: int
    candidate_traces: tuple[tuple[TraceStep, ...], ...] = ()
    trajectories: tuple[CandidateTrajectory, ...] = ()
    baseline: DeterministicBaseline | None = None


def _rank(
    c: CandidateTrajectory, *, use_critic: bool = True
) -> tuple[int, float, float, float, float]:
    # prefer GREEN, then melody preservation, then bass preservation (both are
    # faithfulness to the input, which the joint gate scores), then critic taste,
    # then harmony. Bass sits above critic so we never trade the bass for taste.
    # use_critic=False zeroes the critic term (for the paired critic ablation).
    assert c.fidelity is not None
    critic = c.critic.overall if (use_critic and c.critic is not None) else 0.0
    return (
        1 if c.is_green else 0,
        c.fidelity.melody_recall,
        c.fidelity.bass_preserved,
        critic,
        c.fidelity.harmony_jaccard,
    )


def _validate_temperature(value: object, *, path: str) -> float:
    if type(value) is not float or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{path} must be an exact finite float in 0..1")
    return value


def _resolve_temperature_schedule(
    n: int,
    *,
    temperature: float | None,
    temperature_schedule: tuple[float, ...] | None,
) -> tuple[float, ...]:
    if temperature is not None and temperature_schedule is not None:
        raise ValueError("temperature and temperature_schedule are mutually exclusive")
    if temperature is not None:
        exact = _validate_temperature(temperature, path="temperature")
        return (exact,) * n
    if temperature_schedule is not None:
        if type(temperature_schedule) is not tuple or len(temperature_schedule) != n:
            raise ValueError("temperature_schedule must be an exact tuple with one value per slot")
        return tuple(
            _validate_temperature(value, path=f"temperature_schedule[{index}]")
            for index, value in enumerate(temperature_schedule)
        )
    return tuple(min(1.0, 0.2 * index) for index in range(n))


def _deterministic_baseline(
    source_ir: MusicIR,
    solver_ir: MusicIR,
    goal: ArrangeGoal,
    profile: Profile,
) -> DeterministicBaseline:
    """Solve the rule target once without consuming a model candidate slot."""

    target = propose_fingerstyle(
        solver_ir,
        goal.tuning,
        goal.capo,
        profile=profile,
        tempo_bpm=goal.tempo_bpm,
    )
    solved, oracle = solve_and_check(
        target,
        goal.tuning,
        goal.capo,
        profile,
        tempo_bpm=goal.tempo_bpm,
        beats_per_bar=source_ir.meta.time_sig[0],
    )
    if isinstance(solved, Infeasible):
        return DeterministicBaseline(target, None, None, solved, None, None)
    assert oracle is not None
    return DeterministicBaseline(
        target,
        solved,
        oracle,
        None,
        fidelity(source_ir, solved),
        faithfulness(source_ir, solved),
    )


def build_candidate_trajectory(
    ir: MusicIR,
    goal: ArrangeGoal,
    llm: LLMClient,
    *,
    profile: Profile = MEDIAN_HAND,
    candidate_index: int = 0,
    max_iters: int = 8,
    use_critic: bool = True,
    temperature: float | None = None,
    call_scope_factory: ModelCallScopeFactory | None = None,
) -> CandidateTrajectory:
    """Run the exact production primitive for one bounded candidate slot."""

    source_ir = snapshot_music_ir(ir)
    solver_ir = arrangement_solver_ir(source_ir)
    candidate_index = ensure_candidate_count(candidate_index, path="candidate_index")
    # A pool of at most MAX_AGENT_CANDIDATES has indices 0..MAX-1. Reusing the
    # count validator keeps the failure typed and rejects bool before arithmetic.
    ensure_candidate_count(candidate_index + 1, path="candidate_index")
    max_iters = ensure_repair_iterations(max_iters)
    use_critic = ensure_boolean_control(use_critic, path="use_critic")
    notes, tuning, capo, profile, tempo_bpm = ensure_solver_domain(
        solver_ir.notes,
        goal.tuning,
        goal.capo,
        profile,
        tempo_bpm=goal.tempo_bpm,
    )
    solver_ir = MusicIR(notes, tuple(solver_ir.chords), solver_ir.meta)
    goal = ArrangeGoal(
        style=goal.style,
        tier=goal.tier,
        tuning=tuning,
        capo=capo,
        tempo_bpm=tempo_bpm,
        extras=goal.extras,
    )
    temperature = (
        min(1.0, 0.2 * candidate_index)
        if temperature is None
        else _validate_temperature(temperature, path="temperature")
    )
    proposal = propose_arrangement_outcome(
        source_ir,
        goal,
        llm,
        temperature=temperature,
        profile=profile,
        call_scope_factory=call_scope_factory,
        candidate_index=candidate_index,
    )
    target = proposal.target
    candidate_trace = Trace()
    candidate_trace.add(
        "PROPOSE",
        f"Candidate {candidate_index} produced a bounded target-note checkpoint.",
        event="CANDIDATE_PROPOSED",
        candidate_index=candidate_index,
        temperature=temperature,
        target_checkpoint=target_checkpoint(target),
    )
    repaired = repair(
        target,
        goal.tuning,
        goal.capo,
        profile,
        llm,
        tempo_bpm=goal.tempo_bpm,
        beats_per_bar=source_ir.meta.time_sig[0],
        max_iters=max_iters,
        candidate_index=candidate_index,
        call_scope_factory=call_scope_factory,
    )
    candidate_trace.steps.extend(repaired.trace.steps)
    verdict = repaired.oracle.verdict if repaired.oracle is not None else "INFEASIBLE"
    candidate_trace.add(
        "SOLVE",
        f"Candidate {candidate_index} finished with {verdict}.",
        event="CANDIDATE_FINISHED",
        candidate_index=candidate_index,
        iteration=repaired.iterations,
        verdict=verdict,
        tab_available=repaired.tab is not None,
        repair_iterations=repaired.iterations,
    )

    if repaired.tab is None:
        candidate_fidelity = None
        candidate_faithfulness = None
        is_green = False
        candidate_critic_outcome: CriticOutcome | None = None
        status = CandidateStatus.NO_TAB
    else:
        candidate_fidelity = fidelity(source_ir, repaired.tab)
        candidate_faithfulness = faithfulness(source_ir, repaired.tab)
        is_green = repaired.oracle is not None and repaired.oracle.verdict == "GREEN"
        candidate_critic_outcome = (
            critique_outcome(
                source_ir,
                repaired.tab,
                llm,
                call_scope_factory=call_scope_factory,
                candidate_index=candidate_index,
            )
            if (is_green and use_critic)
            else None
        )
        status = CandidateStatus.GREEN if is_green else CandidateStatus.NON_GREEN_TAB

    work = CandidateWorkCounts(
        proposal_llm_calls=proposal.llm_calls,
        repair_llm_calls=repaired.model_calls,
        critic_llm_calls=(
            candidate_critic_outcome.llm_calls if candidate_critic_outcome is not None else 0
        ),
        solver_calls=repaired.solve_calls,
    )
    return CandidateTrajectory(
        index=candidate_index,
        temperature=temperature,
        proposal=proposal,
        initial_target=target,
        iteration_zero=repaired.iteration_zero,
        terminal=repaired.terminal,
        status=status,
        is_green=is_green,
        fidelity=candidate_fidelity,
        faithfulness=candidate_faithfulness,
        critic_outcome=candidate_critic_outcome,
        trace_snapshots=tuple(
            TraceStepSnapshot.from_trace_step(step) for step in candidate_trace.steps
        ),
        work=work,
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
    temperature: float | None = None,
    temperature_schedule: tuple[float, ...] | None = None,
    call_scope_factory: ModelCallScopeFactory | None = None,
) -> ArrangePool:
    """Build one ordered pool while retaining a trajectory for every slot."""

    source_ir = snapshot_music_ir(ir)
    solver_ir = arrangement_solver_ir(source_ir)
    n = ensure_candidate_count(n)
    max_iters = ensure_repair_iterations(max_iters)
    use_critic = ensure_boolean_control(use_critic, path="use_critic")
    temperatures = _resolve_temperature_schedule(
        n,
        temperature=temperature,
        temperature_schedule=temperature_schedule,
    )
    notes, tuning, capo, profile, tempo_bpm = ensure_solver_domain(
        solver_ir.notes,
        goal.tuning,
        goal.capo,
        profile,
        tempo_bpm=goal.tempo_bpm,
    )
    solver_ir = MusicIR(notes, tuple(solver_ir.chords), solver_ir.meta)
    goal = ArrangeGoal(
        style=goal.style,
        tier=goal.tier,
        tuning=tuning,
        capo=capo,
        tempo_bpm=tempo_bpm,
        extras=goal.extras,
    )
    trace = Trace()
    slots: list[CandidateTrajectory | None] = []
    candidate_traces: list[tuple[TraceStep, ...]] = []
    trajectories: list[CandidateTrajectory] = []
    for candidate_index in range(n):
        trajectory = build_candidate_trajectory(
            source_ir,
            goal,
            llm,
            profile=profile,
            candidate_index=candidate_index,
            max_iters=max_iters,
            use_critic=use_critic,
            temperature=temperatures[candidate_index],
            call_scope_factory=call_scope_factory,
        )
        trajectories.append(trajectory)
        candidate_traces.append(trajectory.trace_steps)
        trace.steps.extend(trajectory.trace_steps)
        slots.append(trajectory if trajectory.terminal.tab is not None else None)
    # Every non-empty prefix already has a model result when slot zero has a
    # tab, so avoid an unnecessary full-score solve on the normal success path.
    baseline = (
        _deterministic_baseline(source_ir, solver_ir, goal, profile)
        if slots and slots[0] is None
        else None
    )
    return ArrangePool(
        tuple(slots),
        trace,
        n,
        tuple(candidate_traces),
        tuple(trajectories),
        baseline,
    )


def _retained_candidate_steps(pool: ArrangePool, index: int) -> tuple[TraceStep, ...]:
    """Return one complete candidate replay, tolerating pre-6A pool fixtures."""

    if len(pool.trajectories) == pool.n:
        return pool.trajectories[index].trace_steps
    if len(pool.candidate_traces) == pool.n:
        return pool.candidate_traces[index]
    return tuple(pool.trace.steps)


def _failed_candidate_to_retain(pool: ArrangePool, k: int) -> int | None:
    """Choose one bounded failure replay, preferring a model-call diagnostic."""

    if k == 0:
        return None
    if len(pool.trajectories) == pool.n:
        candidate_steps = tuple(item.trace_steps for item in pool.trajectories[:k])
    else:
        candidate_steps = pool.candidate_traces[:k]
    if len(candidate_steps) == k:
        for index, steps in enumerate(candidate_steps):
            if any(step.event == "MODEL_CALL_FAILED" for step in steps):
                return index
    return 0


def _add_selection_step(
    trace: Trace,
    *,
    candidate_index: int | None,
    candidates_considered: int,
    verdict: str,
    candidate_fidelity: Fidelity,
    candidate_faithfulness: FaithfulnessGate,
    critic: CriticScore | None,
) -> None:
    green = verdict == "GREEN"
    detail = (
        f"Selected candidate {candidate_index}; playability and fidelity remain separate gates."
        if candidate_index is not None
        else "Selected the deterministic baseline after the model candidates returned no tablature."
    )
    trace.add(
        "SELECT",
        detail,
        event="CANDIDATE_SELECTED",
        candidate_index=candidate_index,
        winner_candidate_index=candidate_index,
        candidates_considered=candidates_considered,
        verdict=verdict,
        green_certified=green,
        playability_gate="passed" if green else "not_passed",
        faithfulness_passed=candidate_faithfulness.passed,
        ranking_melody_recall=candidate_fidelity.melody_recall,
        ranking_bass_preserved=candidate_fidelity.bass_preserved,
        ranking_harmony_jaccard=candidate_fidelity.harmony_jaccard,
        melody_f1=candidate_faithfulness.melody_f1,
        bass_root_accuracy=candidate_faithfulness.bass_root,
        harmony_jaccard=candidate_faithfulness.harmony,
        evaluated_dimensions=candidate_faithfulness.evaluated_dimensions,
        unavailable_dimensions=candidate_faithfulness.unavailable_dimensions,
        critic_status="SCORED" if critic is not None else "NOT_RUN",
        critic_overall=critic.overall if critic is not None else None,
    )


def _select_baseline(
    baseline: DeterministicBaseline,
    trace: Trace,
    candidates_considered: int,
) -> ArrangeResult:
    assert baseline.tab is not None
    assert baseline.oracle is not None
    assert baseline.fidelity is not None
    assert baseline.faithfulness is not None
    target_state = target_checkpoint(baseline.target)
    target_digest = target_state["sha256"]
    assert type(target_digest) is str
    trace.add(
        "PROPOSE",
        "Built a deterministic baseline target from the validated source.",
        event="PROPOSE",
        policy="deterministic_baseline",
        model_calls=0,
        target_checkpoint=target_state,
    )
    trace.add(
        "SOLVE",
        "Solver returned a tablature candidate.",
        event="SOLVER_RETURNED_TAB",
        candidate_index=None,
        iteration=0,
        status="TAB",
        target_sha256=target_digest,
        target_note_count=len(baseline.target),
    )
    trace.add(
        "ORACLE",
        oracle_detail(baseline.oracle),
        event="PLAYABILITY_CHECKED",
        candidate_index=None,
        iteration=0,
        **oracle_trace_payload(
            baseline.oracle,
            baseline.tab,
            terminal_reason=("GREEN" if baseline.oracle.verdict == "GREEN" else None),
        ),
    )
    _add_selection_step(
        trace,
        candidate_index=None,
        candidates_considered=candidates_considered,
        verdict=baseline.oracle.verdict,
        candidate_fidelity=baseline.fidelity,
        candidate_faithfulness=baseline.faithfulness,
        critic=None,
    )
    return ArrangeResult(
        baseline.tab,
        baseline.oracle,
        baseline.fidelity,
        None,
        trace,
        candidates_considered,
    )


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
        if k > 0 and pool.baseline is not None and pool.baseline.tab is not None:
            return _select_baseline(pool.baseline, trace, k)
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
    assert best.fidelity is not None
    assert best.faithfulness is not None
    verdict = best.terminal.oracle.verdict if best.terminal.oracle is not None else None
    assert verdict is not None
    _add_selection_step(
        trace,
        candidate_index=best.index,
        candidates_considered=k,
        verdict=verdict,
        candidate_fidelity=best.fidelity,
        candidate_faithfulness=best.faithfulness,
        critic=best.critic,
    )
    return ArrangeResult(
        best.terminal.tab,
        best.terminal.oracle,
        best.fidelity,
        best.critic,
        trace,
        k,
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
    temperature: float | None = None,
    temperature_schedule: tuple[float, ...] | None = None,
    call_scope_factory: ModelCallScopeFactory | None = None,
) -> ArrangeResult:
    pool = arrange_pool(
        ir,
        goal,
        llm,
        profile=profile,
        n=n,
        max_iters=max_iters,
        use_critic=use_critic,
        temperature=temperature,
        temperature_schedule=temperature_schedule,
        call_scope_factory=call_scope_factory,
    )
    return best_of_k(pool, pool.n)
