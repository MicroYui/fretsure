"""Shared-pool collection and pure benchmark-v2 experiment derivations.

The collection boundary creates exactly one ten-slot agent pool, one ten-slot
raw-LLM control, and one deterministic pure-solver control per item.  Every
repair, search, critic, reliability, and cost comparison below is derived from
those frozen outcomes; none of the view helpers owns an LLM client.

The schedule seed is deliberately supplied by the caller.  Task 7 will freeze
the formal seed; this module only guarantees that a supplied seed produces a
deterministic, outcome-independent permutation and interleaving.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterable
from contextlib import ExitStack
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final, Literal

from fretsure.agent.arranger import (
    ArrangeGoal,
    ProposalStatus,
    arrangement_source_context_sha256,
    proposal_output_token_budget,
)
from fretsure.agent.critic import CRITIC_MAX_TOKENS, CriticStatus
from fretsure.agent.harness import (
    ArrangePool,
    CandidateTrajectory,
    best_of_k,
    build_candidate_trajectory,
)
from fretsure.agent.repair import REPAIR_MAX_TOKENS
from fretsure.agent.trace import Trace
from fretsure.bench.baselines import (
    OPTIONAL_BASELINE_AVAILABILITY,
    BaselineAvailability,
    PureSolverOutcome,
    RawBaselineRequest,
    RawLLMOutcome,
    RawStatus,
    build_raw_baseline_request,
    collect_raw_llm_baseline,
    run_pure_solver_baseline,
)
from fretsure.bench.contracts import require_identifier, require_sha256
from fretsure.bench.corpus import CorpusItem, snapshot_corpus
from fretsure.bench.observe import (
    AttemptIntent,
    AttemptResult,
    CallIntent,
    CallResult,
    CallSequence,
    CallStage,
    InMemoryObservationSink,
    ObservationRequestGuard,
    ObservingLLM,
)
from fretsure.bench.reliability import pass_at_k, pass_hat_k_item
from fretsure.llm.client import LLMClient, managed_llm_client
from fretsure.metrics.fidelity import (
    FAITHFULNESS_DIMENSIONS,
    FaithfulnessDimension,
    FaithfulnessGate,
    faithfulness,
    faithfulness_dimensions,
)
from fretsure.oracle.core import OracleResult, check_playability
from fretsure.oracle.input import ensure_profile
from fretsure.oracle.profiles import Profile
from fretsure.tab import Tab

EXPERIMENT_N_SAMPLES: Final = 10
EXPERIMENT_TEMPERATURE: Final = 0.8
EXPERIMENT_MAX_REPAIR_ITERS: Final = 8
SEARCH_K_VALUES: Final[tuple[int, ...]] = (1, 2, 4, 8)
RELIABILITY_K_VALUES: Final[tuple[int, ...]] = tuple(range(1, 11))
FULL_SELECTION_K: Final = 4

_SCHEDULE_DOMAIN = b"fretsure:benchmark-experiment-schedule@0.1.0\0"
_PAIR_DOMAIN = b"fretsure:benchmark-experiment-pair@0.1.0\0"


class ExperimentInputError(ValueError):
    """Typed fail-closed error for malformed plans or incomplete collections."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid experiment {field}: {detail}")


class CollectionArm(StrEnum):
    AGENT = "agent"
    RAW = "raw"


class BudgetMatchStatus(StrEnum):
    EXACT = "exact"
    CENSORED = "censored"
    NO_FIT = "no_fit"


class BudgetLimitDimension(StrEnum):
    CALLS = "calls"
    TOKENS = "tokens"
    TIE = "tie"
    SAMPLE_CAP = "sample_cap"


@dataclass(frozen=True, slots=True)
class MatchedPrefix:
    """Largest pre-call control prefix that fits one full repair budget."""

    status: BudgetMatchStatus
    limiting_dimension: BudgetLimitDimension
    target_calls: int
    target_tokens: int
    unit_calls: int
    unit_tokens: int
    call_quotient: int
    token_quotient: int
    prefix_samples: int
    spent_calls: int
    spent_tokens: int
    remaining_calls: int
    remaining_tokens: int

    def __post_init__(self) -> None:
        values = (
            self.target_calls,
            self.target_tokens,
            self.unit_calls,
            self.unit_tokens,
            self.call_quotient,
            self.token_quotient,
            self.prefix_samples,
            self.spent_calls,
            self.spent_tokens,
            self.remaining_calls,
            self.remaining_tokens,
        )
        if any(type(value) is not int or value < 0 for value in values):
            raise ExperimentInputError(
                "matched_prefix", "token counts must be exact nonnegative ints"
            )
        if type(self.status) is not BudgetMatchStatus:
            raise ExperimentInputError("matched_prefix.status", "must be a BudgetMatchStatus")
        if type(self.limiting_dimension) is not BudgetLimitDimension:
            raise ExperimentInputError(
                "matched_prefix.limiting_dimension", "must be a BudgetLimitDimension"
            )
        if self.unit_calls == 0 or self.unit_tokens == 0:
            raise ExperimentInputError("matched_prefix.unit", "must be positive")
        if not 0 <= self.prefix_samples <= EXPERIMENT_N_SAMPLES:
            raise ExperimentInputError("matched_prefix.prefix_samples", "is outside 0..10")
        if (
            self.call_quotient != self.target_calls // self.unit_calls
            or self.token_quotient != self.target_tokens // self.unit_tokens
            or self.prefix_samples
            != min(EXPERIMENT_N_SAMPLES, self.call_quotient, self.token_quotient)
        ):
            raise ExperimentInputError(
                "matched_prefix.quotient", "does not select the largest prefix"
            )
        if (
            self.spent_calls != self.prefix_samples * self.unit_calls
            or self.spent_tokens != self.prefix_samples * self.unit_tokens
            or self.remaining_calls != self.target_calls - self.spent_calls
            or self.remaining_tokens != self.target_tokens - self.spent_tokens
        ):
            raise ExperimentInputError("matched_prefix.spent", "does not match the prefix")
        if self.remaining_calls < 0 or self.remaining_tokens < 0:
            raise ExperimentInputError("matched_prefix.remaining", "cannot be negative")
        if self.status is BudgetMatchStatus.NO_FIT:
            if self.prefix_samples != 0:
                raise ExperimentInputError("matched_prefix", "no-fit fields are inconsistent")
        elif self.status is BudgetMatchStatus.CENSORED:
            if self.prefix_samples != EXPERIMENT_N_SAMPLES or (
                self.remaining_calls == 0 and self.remaining_tokens == 0
            ):
                raise ExperimentInputError("matched_prefix", "censored fields are inconsistent")
        elif self.prefix_samples == 0:
            raise ExperimentInputError("matched_prefix", "exact fields are not maximal")

        expected_limit = (
            BudgetLimitDimension.SAMPLE_CAP
            if self.prefix_samples == EXPERIMENT_N_SAMPLES
            and self.call_quotient >= EXPERIMENT_N_SAMPLES
            and self.token_quotient >= EXPERIMENT_N_SAMPLES
            else BudgetLimitDimension.CALLS
            if self.call_quotient < self.token_quotient
            else BudgetLimitDimension.TOKENS
            if self.token_quotient < self.call_quotient
            else BudgetLimitDimension.TIE
        )
        if self.limiting_dimension is not expected_limit:
            raise ExperimentInputError("matched_prefix.limiting_dimension", "is inconsistent")


@dataclass(frozen=True, slots=True)
class ItemMatchedBudget:
    item_id: str
    proposal_tokens: int
    repair_tokens: int
    target_calls: int
    target_tokens: int
    no_repair: MatchedPrefix
    raw: MatchedPrefix


@dataclass(frozen=True, slots=True)
class ItemSchedule:
    item_id: str
    candidate_permutation: tuple[int, ...]

    def __post_init__(self) -> None:
        require_identifier(self.item_id, path="item_schedule.item_id")
        if (
            type(self.candidate_permutation) is not tuple
            or self.candidate_permutation != tuple(self.candidate_permutation)
            or set(self.candidate_permutation) != set(range(EXPERIMENT_N_SAMPLES))
            or len(self.candidate_permutation) != EXPERIMENT_N_SAMPLES
            or any(type(value) is not int for value in self.candidate_permutation)
        ):
            raise ExperimentInputError(
                "item_schedule.candidate_permutation",
                "must be one exact permutation of 0..9",
            )


@dataclass(frozen=True, slots=True)
class ScheduledUnit:
    round_index: int
    item_position: int
    item_id: str
    arm: CollectionArm
    candidate_index: int

    def __post_init__(self) -> None:
        if type(self.round_index) is not int or not 0 <= self.round_index < EXPERIMENT_N_SAMPLES:
            raise ExperimentInputError("schedule.round_index", "is outside 0..9")
        if type(self.item_position) is not int or self.item_position < 0:
            raise ExperimentInputError("schedule.item_position", "must be nonnegative")
        require_identifier(self.item_id, path="schedule.item_id")
        if type(self.arm) is not CollectionArm:
            raise ExperimentInputError("schedule.arm", "must be an exact collection arm")
        if type(self.candidate_index) is not int or not 0 <= self.candidate_index < 10:
            raise ExperimentInputError("schedule.candidate_index", "is outside 0..9")


@dataclass(frozen=True, slots=True)
class ExperimentPlan:
    run_id: str
    schedule_seed: int
    items: tuple[CorpusItem, ...]
    item_schedules: tuple[ItemSchedule, ...]
    collection_schedule: tuple[ScheduledUnit, ...]
    matched_budgets: tuple[ItemMatchedBudget, ...]
    n_samples: int = EXPERIMENT_N_SAMPLES
    temperature: float = EXPERIMENT_TEMPERATURE
    max_repair_iters: int = EXPERIMENT_MAX_REPAIR_ITERS
    search_k: tuple[int, ...] = SEARCH_K_VALUES
    reliability_k: tuple[int, ...] = RELIABILITY_K_VALUES

    def __post_init__(self) -> None:
        require_identifier(self.run_id, path="plan.run_id")
        if type(self.schedule_seed) is not int or not 0 <= self.schedule_seed < 2**63:
            raise ExperimentInputError("plan.schedule_seed", "must be an exact int in 0..2^63-1")
        if type(self.items) is not tuple or any(
            type(item) is not CorpusItem for item in self.items
        ):
            raise ExperimentInputError("plan.items", "must contain exact CorpusItem values")
        if type(self.item_schedules) is not tuple or any(
            type(schedule) is not ItemSchedule for schedule in self.item_schedules
        ):
            raise ExperimentInputError(
                "plan.item_schedules", "must contain exact ItemSchedule values"
            )
        if type(self.collection_schedule) is not tuple or any(
            type(unit) is not ScheduledUnit for unit in self.collection_schedule
        ):
            raise ExperimentInputError(
                "plan.collection_schedule", "must contain exact ScheduledUnit values"
            )
        if type(self.matched_budgets) is not tuple or any(
            type(budget) is not ItemMatchedBudget for budget in self.matched_budgets
        ):
            raise ExperimentInputError(
                "plan.matched_budgets", "must contain exact ItemMatchedBudget values"
            )
        if type(self.n_samples) is not int or self.n_samples != EXPERIMENT_N_SAMPLES:
            raise ExperimentInputError(
                "plan.n_samples", "benchmark-v2 requires exactly ten samples"
            )
        if type(self.temperature) is not float or self.temperature != EXPERIMENT_TEMPERATURE:
            raise ExperimentInputError(
                "plan.temperature", "benchmark-v2 requires exact temperature 0.8"
            )
        if (
            type(self.max_repair_iters) is not int
            or self.max_repair_iters != EXPERIMENT_MAX_REPAIR_ITERS
        ):
            raise ExperimentInputError(
                "plan.max_repair_iters", "benchmark-v2 requires eight repair passes"
            )
        if (
            type(self.search_k) is not tuple
            or type(self.reliability_k) is not tuple
            or self.search_k != SEARCH_K_VALUES
            or self.reliability_k != RELIABILITY_K_VALUES
        ):
            raise ExperimentInputError("plan.k", "search and reliability k sets are frozen")
        if self.n_samples < max((*self.search_k, *self.reliability_k)):
            raise ExperimentInputError("plan.n_samples", "must cover every requested k")
        item_ids = tuple(item.item_id for item in self.items)
        if len(item_ids) != len(set(item_ids)) or not item_ids:
            raise ExperimentInputError("plan.items", "must contain unique nonempty item ids")
        expected_item_schedules = tuple(
            ItemSchedule(item.item_id, _candidate_permutation(self.schedule_seed, item.item_id))
            for item in self.items
        )
        if self.item_schedules != expected_item_schedules:
            raise ExperimentInputError(
                "plan.item_schedules", "do not match the deterministic permutation"
            )
        expected_budgets = tuple(_item_matched_budget(item) for item in self.items)
        if self.matched_budgets != expected_budgets:
            raise ExperimentInputError("plan.matched_budgets", "do not match the pre-call budget")
        expected_schedule = _build_collection_schedule(
            self.items,
            self.item_schedules,
            self.schedule_seed,
        )
        if self.collection_schedule != expected_schedule:
            raise ExperimentInputError(
                "plan.collection_schedule", "does not match the deterministic interleaving"
            )


def _hash_fields(domain: bytes, seed: int, *fields: object) -> bytes:
    digest = hashlib.sha256()
    digest.update(domain)
    digest.update(seed.to_bytes(8, "big"))
    for field in fields:
        encoded = str(field).encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.digest()


def _pair_id(kind: str, item_id: str, candidate_index: int | None = None) -> str:
    digest = hashlib.sha256()
    digest.update(_PAIR_DOMAIN)
    for field in (kind, item_id, "" if candidate_index is None else str(candidate_index)):
        encoded = field.encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return f"pair:{kind}:{digest.hexdigest()[:24]}"


def sample_pair_id(item_id: object, candidate_index: object) -> str:
    """Return the frozen cross-arm identity for one item/candidate sample."""

    exact_item_id = require_identifier(item_id, path="sample_pair_id.item_id")
    if (
        type(candidate_index) is not int
        or not 0 <= candidate_index < EXPERIMENT_N_SAMPLES
    ):
        raise ExperimentInputError(
            "sample_pair_id.candidate_index", "must be an exact integer in 0..9"
        )
    return _pair_id("sample", exact_item_id, candidate_index)


def item_pair_id(kind: object, item_id: object) -> str:
    """Return one frozen item-level identity for a named derived comparison."""

    exact_kind = require_identifier(kind, path="item_pair_id.kind")
    exact_item_id = require_identifier(item_id, path="item_pair_id.item_id")
    return _pair_id(exact_kind, exact_item_id)


def _candidate_permutation(seed: int, item_id: str) -> tuple[int, ...]:
    return tuple(
        sorted(
            range(EXPERIMENT_N_SAMPLES),
            key=lambda index: _hash_fields(_SCHEDULE_DOMAIN, seed, "candidate", item_id, index),
        )
    )


def _proposal_request_tokens(item: CorpusItem) -> int:
    try:
        return proposal_output_token_budget(item.ir)
    except ValueError:
        raise ExperimentInputError(
            f"items[{item.item_id}].proposal_tokens",
            "source exceeds the frozen proposal output capacity",
        ) from None


def match_budget_prefix(
    target_calls: int,
    target_tokens: int,
    *,
    unit_calls: int,
    unit_tokens: int,
) -> MatchedPrefix:
    if (
        type(target_calls) is not int
        or target_calls < 0
        or type(target_tokens) is not int
        or target_tokens < 0
        or type(unit_calls) is not int
        or unit_calls <= 0
        or type(unit_tokens) is not int
        or unit_tokens <= 0
    ):
        raise ExperimentInputError(
            "matched_prefix", "budgets require exact nonnegative targets and positive units"
        )
    call_quotient = target_calls // unit_calls
    token_quotient = target_tokens // unit_tokens
    prefix = min(EXPERIMENT_N_SAMPLES, call_quotient, token_quotient)
    spent_calls = prefix * unit_calls
    spent = prefix * unit_tokens
    if prefix == 0:
        status = BudgetMatchStatus.NO_FIT
    elif prefix == EXPERIMENT_N_SAMPLES and (spent_calls < target_calls or spent < target_tokens):
        status = BudgetMatchStatus.CENSORED
    else:
        status = BudgetMatchStatus.EXACT
    limiting_dimension = (
        BudgetLimitDimension.SAMPLE_CAP
        if prefix == EXPERIMENT_N_SAMPLES
        and call_quotient >= EXPERIMENT_N_SAMPLES
        and token_quotient >= EXPERIMENT_N_SAMPLES
        else BudgetLimitDimension.CALLS
        if call_quotient < token_quotient
        else BudgetLimitDimension.TOKENS
        if token_quotient < call_quotient
        else BudgetLimitDimension.TIE
    )
    return MatchedPrefix(
        status=status,
        limiting_dimension=limiting_dimension,
        target_calls=target_calls,
        target_tokens=target_tokens,
        unit_calls=unit_calls,
        unit_tokens=unit_tokens,
        call_quotient=call_quotient,
        token_quotient=token_quotient,
        prefix_samples=prefix,
        spent_calls=spent_calls,
        spent_tokens=spent,
        remaining_calls=target_calls - spent_calls,
        remaining_tokens=target_tokens - spent,
    )


def _item_matched_budget(item: CorpusItem) -> ItemMatchedBudget:
    proposal_tokens = _proposal_request_tokens(item)
    repair_tokens = EXPERIMENT_MAX_REPAIR_ITERS * REPAIR_MAX_TOKENS
    target_calls = 1 + EXPERIMENT_MAX_REPAIR_ITERS
    target_tokens = proposal_tokens + repair_tokens
    no_repair = match_budget_prefix(
        target_calls,
        target_tokens,
        unit_calls=1,
        unit_tokens=proposal_tokens,
    )
    raw = match_budget_prefix(
        target_calls,
        target_tokens,
        unit_calls=1,
        unit_tokens=proposal_tokens,
    )
    return ItemMatchedBudget(
        item.item_id,
        proposal_tokens,
        repair_tokens,
        target_calls,
        target_tokens,
        no_repair,
        raw,
    )


def _build_collection_schedule(
    items: tuple[CorpusItem, ...],
    schedules: tuple[ItemSchedule, ...],
    seed: int,
) -> tuple[ScheduledUnit, ...]:
    units: list[ScheduledUnit] = []
    # Round-wise interleaving prevents one item or arm from occupying the full
    # early/late run while preserving each item's preregistered draw assignment.
    for round_index in range(EXPERIMENT_N_SAMPLES):
        round_units = [
            ScheduledUnit(
                round_index,
                item_position,
                item.item_id,
                arm,
                schedules[item_position].candidate_permutation[round_index],
            )
            for item_position, item in enumerate(items)
            for arm in (CollectionArm.AGENT, CollectionArm.RAW)
        ]
        round_units.sort(
            key=lambda unit: _hash_fields(
                _SCHEDULE_DOMAIN,
                seed,
                "interleave",
                unit.round_index,
                unit.item_id,
                unit.arm.value,
                unit.candidate_index,
            )
        )
        units.extend(round_units)
    return tuple(units)


def preflight_experiment(
    items: object,
    *,
    run_id: str,
    schedule_seed: int,
) -> ExperimentPlan:
    """Validate inputs and freeze every outcome-independent collection decision."""

    try:
        exact_run_id = require_identifier(run_id, path="run_id")
        snapshots = snapshot_corpus(items, allow_legacy=False)
    except ValueError as error:
        if isinstance(error, ExperimentInputError):
            raise
        raise ExperimentInputError("preflight", "run id or corpus is invalid") from error
    if type(schedule_seed) is not int or not 0 <= schedule_seed < 2**63:
        raise ExperimentInputError("schedule_seed", "must be an exact int in 0..2^63-1")
    if not snapshots:
        raise ExperimentInputError("items", "must contain at least one arrangement item")
    for index, item in enumerate(snapshots):
        if item.evidence is None or item.evidence.signature == "none":
            raise ExperimentInputError(
                f"items[{index}].evidence",
                "experiment items require at least one source-evidence dimension",
            )

    schedules = tuple(
        ItemSchedule(item.item_id, _candidate_permutation(schedule_seed, item.item_id))
        for item in snapshots
    )
    budgets = tuple(_item_matched_budget(item) for item in snapshots)
    return ExperimentPlan(
        run_id=exact_run_id,
        schedule_seed=schedule_seed,
        items=snapshots,
        item_schedules=schedules,
        collection_schedule=_build_collection_schedule(snapshots, schedules, schedule_seed),
        matched_budgets=budgets,
    )


@dataclass(frozen=True, slots=True)
class ObservationLedger:
    """Detached complete observation records used for exact joins and costs."""

    intents: tuple[CallIntent, ...]
    results: tuple[CallResult, ...]
    attempt_intents: tuple[AttemptIntent, ...]
    attempt_results: tuple[AttemptResult, ...]

    def __post_init__(self) -> None:
        _joined_observations(self)


@dataclass(frozen=True, slots=True)
class CompletedPureSolver:
    """One persisted pure-solver callback bound to its exact source item."""

    item_id: str
    source_context_sha256: str
    outcome: PureSolverOutcome

    def __post_init__(self) -> None:
        require_identifier(self.item_id, path="completed_pure_solver.item_id")
        require_sha256(
            self.source_context_sha256,
            path="completed_pure_solver.source_context_sha256",
        )
        if type(self.outcome) is not PureSolverOutcome:
            raise ExperimentInputError(
                "completed_pure_solver.outcome",
                "must be an exact PureSolverOutcome",
            )


@dataclass(frozen=True, slots=True)
class CompletedExperimentUnit:
    """One fully collected schedule unit that may seed a prefix-only resume."""

    unit: ScheduledUnit
    source_context_sha256: str
    trajectory: CandidateTrajectory | None = None
    raw_outcome: RawLLMOutcome | None = None

    def __post_init__(self) -> None:
        if type(self.unit) is not ScheduledUnit:
            raise ExperimentInputError("completed_unit.unit", "must be an exact ScheduledUnit")
        require_sha256(
            self.source_context_sha256,
            path="completed_unit.source_context_sha256",
        )
        if self.unit.arm is CollectionArm.AGENT:
            if type(self.trajectory) is not CandidateTrajectory or self.raw_outcome is not None:
                raise ExperimentInputError(
                    "completed_unit",
                    "an agent unit requires exactly one CandidateTrajectory",
                )
            if self.trajectory.index != self.unit.candidate_index:
                raise ExperimentInputError(
                    "completed_unit.trajectory",
                    "candidate index does not match the scheduled unit",
                )
            return
        if type(self.raw_outcome) is not RawLLMOutcome or self.trajectory is not None:
            raise ExperimentInputError(
                "completed_unit",
                "a raw unit requires exactly one RawLLMOutcome",
            )
        if self.raw_outcome.sample_index != self.unit.candidate_index:
            raise ExperimentInputError(
                "completed_unit.raw_outcome",
                "sample index does not match the scheduled unit",
            )


@dataclass(frozen=True, slots=True)
class ExperimentResumeState:
    """Completed deterministic controls and one continuous schedule prefix."""

    pure_solver_outcomes: tuple[CompletedPureSolver | None, ...]
    completed_units: tuple[CompletedExperimentUnit, ...]

    def __post_init__(self) -> None:
        if type(self.pure_solver_outcomes) is not tuple or any(
            value is not None and type(value) is not CompletedPureSolver
            for value in self.pure_solver_outcomes
        ):
            raise ExperimentInputError(
                "resume_state.pure_solver_outcomes",
                "must be an exact tuple of optional CompletedPureSolver values",
            )
        if type(self.completed_units) is not tuple or any(
            type(value) is not CompletedExperimentUnit for value in self.completed_units
        ):
            raise ExperimentInputError(
                "resume_state.completed_units",
                "must be an exact tuple of CompletedExperimentUnit values",
            )


@dataclass(frozen=True, slots=True)
class ItemCollection:
    item: CorpusItem
    trajectories: tuple[CandidateTrajectory, ...]
    raw_outcomes: tuple[RawLLMOutcome, ...]
    pure_solver: PureSolverOutcome

    def __post_init__(self) -> None:
        if type(self.item) is not CorpusItem:
            raise ExperimentInputError("collection.item", "must be an exact CorpusItem")
        if type(self.trajectories) is not tuple or len(self.trajectories) != 10:
            raise ExperimentInputError("collection.trajectories", "must contain exactly ten slots")
        if any(type(value) is not CandidateTrajectory for value in self.trajectories):
            raise ExperimentInputError("collection.trajectories", "must contain exact trajectories")
        if tuple(value.index for value in self.trajectories) != tuple(range(10)):
            raise ExperimentInputError(
                "collection.trajectories",
                "must cover candidate indices 0..9 exactly once in prefix order",
            )
        if any(value.temperature != EXPERIMENT_TEMPERATURE for value in self.trajectories):
            raise ExperimentInputError(
                "collection.trajectories", "all slots require temperature 0.8"
            )
        if any(value.work.proposal_llm_calls != 1 for value in self.trajectories):
            raise ExperimentInputError(
                "collection.trajectories",
                "formal slots require one actual proposal call; bypasses are excluded",
            )
        if any(
            (value.is_green and value.critic_outcome is None)
            or (not value.is_green and value.critic_outcome is not None)
            for value in self.trajectories
        ):
            raise ExperimentInputError(
                "collection.trajectories",
                "full collection requires critic outcomes for every GREEN slot only",
            )
        if type(self.raw_outcomes) is not tuple or len(self.raw_outcomes) != 10:
            raise ExperimentInputError("collection.raw_outcomes", "must contain exactly ten slots")
        if any(type(value) is not RawLLMOutcome for value in self.raw_outcomes):
            raise ExperimentInputError("collection.raw_outcomes", "must contain exact raw outcomes")
        if tuple(value.sample_index for value in self.raw_outcomes) != tuple(range(10)):
            raise ExperimentInputError(
                "collection.raw_outcomes",
                "must cover sample indices 0..9 exactly once in prefix order",
            )
        if type(self.pure_solver) is not PureSolverOutcome:
            raise ExperimentInputError("collection.pure_solver", "must be an exact outcome")
        if self.pure_solver.solver_calls != 1 or self.pure_solver.llm_calls != 0:
            raise ExperimentInputError(
                "collection.pure_solver",
                "must represent one deterministic solver call and no LLM calls",
            )


@dataclass(frozen=True, slots=True)
class ExperimentCollection:
    plan: ExperimentPlan
    goal: ArrangeGoal
    profile: Profile
    items: tuple[ItemCollection, ...]
    observations: ObservationLedger
    external_baselines: tuple[BaselineAvailability, ...] = OPTIONAL_BASELINE_AVAILABILITY

    def __post_init__(self) -> None:
        if type(self.plan) is not ExperimentPlan:
            raise ExperimentInputError("collection.plan", "must be an exact ExperimentPlan")
        if type(self.goal) is not ArrangeGoal:
            raise ExperimentInputError("collection.goal", "must be an exact ArrangeGoal")
        if type(self.profile) is not Profile:
            raise ExperimentInputError("collection.profile", "must be an exact Profile")
        if type(self.items) is not tuple or len(self.items) != len(self.plan.items):
            raise ExperimentInputError("collection.items", "do not cover the plan")
        if tuple(value.item for value in self.items) != self.plan.items:
            raise ExperimentInputError(
                "collection.items", "do not match the exact planned source snapshots"
            )
        if type(self.observations) is not ObservationLedger:
            raise ExperimentInputError("collection.observations", "must be an exact ledger")
        if self.external_baselines != OPTIONAL_BASELINE_AVAILABILITY:
            raise ExperimentInputError(
                "collection.external_baselines",
                "B3/B4 must remain explicitly unavailable until adapters exist",
            )
        _validate_collection_observation_coverage(self)


@dataclass(frozen=True, slots=True)
class _JoinedCall:
    intent: CallIntent
    result: CallResult
    attempt_intents: tuple[AttemptIntent, ...]
    attempt_results: tuple[AttemptResult, ...]


def _joined_observations(ledger: ObservationLedger) -> tuple[_JoinedCall, ...]:
    if type(ledger.intents) is not tuple or any(
        type(value) is not CallIntent for value in ledger.intents
    ):
        raise ExperimentInputError("observations.intents", "must contain exact intents")
    if type(ledger.results) is not tuple or any(
        type(value) is not CallResult for value in ledger.results
    ):
        raise ExperimentInputError("observations.results", "must contain exact results")
    if type(ledger.attempt_intents) is not tuple or any(
        type(value) is not AttemptIntent for value in ledger.attempt_intents
    ):
        raise ExperimentInputError(
            "observations.attempt_intents", "must contain exact attempt intents"
        )
    if type(ledger.attempt_results) is not tuple or any(
        type(value) is not AttemptResult for value in ledger.attempt_results
    ):
        raise ExperimentInputError(
            "observations.attempt_results", "must contain exact attempt results"
        )
    if len(ledger.intents) != len(ledger.results):
        raise ExperimentInputError("observations", "logical intents/results are incomplete")
    if len(ledger.attempt_intents) != len(ledger.attempt_results):
        raise ExperimentInputError("observations", "attempt intents/results are incomplete")

    results_by_key: dict[tuple[int, str], CallResult] = {}
    for call_result in ledger.results:
        key = (call_result.call_index, call_result.logical_call_id)
        if key in results_by_key:
            raise ExperimentInputError("observations.results", "contains a duplicate terminal")
        results_by_key[key] = call_result

    attempt_intents_by_call: dict[tuple[int, str], list[AttemptIntent]] = {}
    for attempt_intent in ledger.attempt_intents:
        key = (attempt_intent.call_index, attempt_intent.logical_call_id)
        attempt_intents_by_call.setdefault(key, []).append(attempt_intent)
    attempt_results_by_call: dict[tuple[int, str], list[AttemptResult]] = {}
    for attempt_result in ledger.attempt_results:
        key = (attempt_result.call_index, attempt_result.logical_call_id)
        attempt_results_by_call.setdefault(key, []).append(attempt_result)

    joined: list[_JoinedCall] = []
    seen_logical: set[tuple[int, str]] = set()
    seen_attempt_ids: set[str] = set()
    for position, call_intent in enumerate(ledger.intents):
        key = (call_intent.call_index, call_intent.logical_call_id)
        if key in seen_logical or call_intent.call_index != position:
            raise ExperimentInputError(
                "observations.intents", "call indices or logical ids are duplicate/noncanonical"
            )
        seen_logical.add(key)
        terminal = results_by_key.pop(key, None)
        if terminal is None:
            raise ExperimentInputError("observations", "logical intent has no matching result")
        attempt_intents = sorted(
            attempt_intents_by_call.pop(key, []), key=lambda value: value.attempt_index
        )
        attempt_results = sorted(
            attempt_results_by_call.pop(key, []), key=lambda value: value.attempt_index
        )
        if len(attempt_intents) != len(attempt_results) or not attempt_intents:
            raise ExperimentInputError("observations", "logical call has incomplete attempts")
        for attempt_index, (attempt, attempt_terminal) in enumerate(
            zip(attempt_intents, attempt_results, strict=True)
        ):
            if (
                attempt.attempt_index != attempt_index
                or attempt_terminal.attempt_index != attempt_index
                or attempt.attempt_id != attempt_terminal.attempt_id
                or attempt.attempt_id in seen_attempt_ids
                or attempt.run_id != call_intent.run_id
                or attempt_terminal.run_id != call_intent.run_id
                or attempt.request_sha256 != call_intent.request_sha256
                or attempt.reserved_output_tokens != call_intent.max_tokens
            ):
                raise ExperimentInputError("observations.attempts", "do not exactly join")
            seen_attempt_ids.add(attempt.attempt_id)
        if terminal.provider.available and terminal.provider.attempts != len(attempt_intents):
            raise ExperimentInputError(
                "observations.provider.attempts", "disagrees with the attempt journal"
            )
        joined.append(
            _JoinedCall(
                call_intent,
                terminal,
                tuple(attempt_intents),
                tuple(attempt_results),
            )
        )
    if results_by_key or attempt_intents_by_call or attempt_results_by_call:
        raise ExperimentInputError("observations", "contains orphan or extra records")
    return tuple(joined)


_SemanticCallKey = tuple[str, int, CallStage, int, str]
_LogicalCallKey = tuple[str, int]


@dataclass(frozen=True, slots=True)
class _ObservationIndex:
    joined: tuple[_JoinedCall, ...]
    actual: frozenset[_SemanticCallKey]
    actual_call_keys: frozenset[_LogicalCallKey]
    requested_model_ids: frozenset[str]
    semantic_by_call_key: dict[_LogicalCallKey, _SemanticCallKey]


def _index_observations(plan: ExperimentPlan, ledger: ObservationLedger) -> _ObservationIndex:
    """Validate plan-bound observation fields shared by full and resumed runs."""

    joined = _joined_observations(ledger)
    planned_items = {item.item_id: item for item in plan.items}
    budget_by_item = {budget.item_id: budget for budget in plan.matched_budgets}
    actual: set[_SemanticCallKey] = set()
    actual_call_keys: set[_LogicalCallKey] = set()
    requested_model_ids: set[str] = set()
    semantic_by_call_key: dict[_LogicalCallKey, _SemanticCallKey] = {}
    for call in joined:
        intent = call.intent
        if intent.run_id != plan.run_id:
            raise ExperimentInputError("observations.run_id", "does not match the plan")
        key = (
            intent.item_id,
            intent.candidate_index,
            intent.stage,
            intent.stage_ordinal,
            intent.pair_id,
        )
        if key in actual:
            raise ExperimentInputError("observations", "contains duplicate semantic call keys")
        actual.add(key)
        requested_model_ids.add(intent.requested_model_id)
        call_key = (intent.logical_call_id, intent.call_index)
        if call_key in actual_call_keys:
            raise ExperimentInputError("observations", "contains duplicate logical call keys")
        actual_call_keys.add(call_key)
        semantic_by_call_key[call_key] = key
        budget = budget_by_item.get(intent.item_id)
        planned_item = planned_items.get(intent.item_id)
        if budget is None or planned_item is None:
            raise ExperimentInputError("observations.item_id", "is outside the plan")
        if (
            intent.family_id != planned_item.family_id
            or intent.cluster_id != planned_item.cluster_id
        ):
            raise ExperimentInputError(
                "observations.family_cluster",
                "does not match the planned item identities",
            )
        expected_tokens = {
            CallStage.PROPOSAL: budget.proposal_tokens,
            CallStage.REPAIR: REPAIR_MAX_TOKENS,
            CallStage.CRITIC: CRITIC_MAX_TOKENS,
            CallStage.RAW: budget.proposal_tokens,
        }[intent.stage]
        expected_temperature = (
            EXPERIMENT_TEMPERATURE
            if intent.stage in {CallStage.PROPOSAL, CallStage.RAW}
            else 0.0
        )
        if intent.max_tokens != expected_tokens or intent.temperature != expected_temperature:
            raise ExperimentInputError(
                "observations.request_policy",
                "does not match the frozen per-stage token/temperature policy",
            )
    return _ObservationIndex(
        joined,
        frozenset(actual),
        frozenset(actual_call_keys),
        frozenset(requested_model_ids),
        semantic_by_call_key,
    )


def _expected_agent_keys(
    item: ItemCollection,
) -> set[tuple[str, int, CallStage, int, str]]:
    return {
        key
        for trajectory in item.trajectories
        for key in _expected_trajectory_keys(item.item.item_id, trajectory)
    }


def _expected_trajectory_keys(
    item_id: str,
    trajectory: CandidateTrajectory,
) -> set[tuple[str, int, CallStage, int, str]]:
    return set(_expected_trajectory_sequence(item_id, trajectory))


def _expected_trajectory_sequence(
    item_id: str,
    trajectory: CandidateTrajectory,
) -> tuple[_SemanticCallKey, ...]:
    expected: list[_SemanticCallKey] = []
    pair_id = sample_pair_id(item_id, trajectory.index)
    if trajectory.work.proposal_llm_calls:
        expected.append((item_id, trajectory.index, CallStage.PROPOSAL, 0, pair_id))
    for ordinal in range(trajectory.work.repair_llm_calls):
        expected.append((item_id, trajectory.index, CallStage.REPAIR, ordinal, pair_id))
    if trajectory.work.critic_llm_calls:
        expected.append((item_id, trajectory.index, CallStage.CRITIC, 0, pair_id))
    return tuple(expected)


def _raw_observation_key(outcome: RawLLMOutcome) -> tuple[str, int] | None:
    key = outcome.observation_key
    if key is None:
        return None
    return (key.logical_call_id, key.call_index)


def _validate_collection_observation_coverage(collection: ExperimentCollection) -> None:
    indexed = _index_observations(collection.plan, collection.observations)
    joined = indexed.joined

    expected: set[tuple[str, int, CallStage, int, str]] = set()
    raw_call_keys: set[tuple[str, int]] = set()
    for item in collection.items:
        expected.update(_expected_agent_keys(item))
        raw_request = build_raw_baseline_request(
            item.item.ir,
            _goal_at_source_tempo(collection.goal, item.item),
            collection.profile,
        )
        for outcome in item.raw_outcomes:
            pair_id = sample_pair_id(item.item.item_id, outcome.sample_index)
            if outcome.llm_calls != 1:
                raise ExperimentInputError("raw_outcome.llm_calls", "must equal one")
            expected.add((item.item.item_id, outcome.sample_index, CallStage.RAW, 0, pair_id))
            observation_key = _raw_observation_key(outcome)
            if observation_key is None or observation_key in raw_call_keys:
                raise ExperimentInputError(
                    "raw_outcome.observation_key", "is missing or duplicated"
                )
            raw_call_keys.add(observation_key)
            if outcome.observation_key.run_id != collection.plan.run_id:
                raise ExperimentInputError(
                    "raw_outcome.observation_key.run_id", "does not match the plan"
                )
            if outcome.source_context_sha256 != raw_request.source_context_sha256:
                raise ExperimentInputError(
                    "raw_outcome.source_context_sha256",
                    "does not bind the item source facts",
                )
            if indexed.semantic_by_call_key.get(observation_key) != (
                item.item.item_id,
                outcome.sample_index,
                CallStage.RAW,
                0,
                pair_id,
            ):
                raise ExperimentInputError(
                    "raw_outcome.observation_key",
                    "does not exactly join its scheduled raw call",
                )
    if indexed.actual != expected:
        raise ExperimentInputError(
            "observations.coverage", "has missing, duplicate, or extra experiment calls"
        )
    if len(indexed.requested_model_ids) != 1:
        raise ExperimentInputError(
            "observations.requested_model_id",
            "agent and raw calls must share one requested model",
        )
    if not raw_call_keys.issubset(indexed.actual_call_keys):
        raise ExperimentInputError(
            "raw_outcome.observation_key", "does not join an observed raw call"
        )
    scheduled_first_calls = tuple(
        (
            call.intent.item_id,
            CollectionArm.AGENT if call.intent.stage is CallStage.PROPOSAL else CollectionArm.RAW,
            call.intent.candidate_index,
        )
        for call in joined
        if call.intent.stage in {CallStage.PROPOSAL, CallStage.RAW}
    )
    expected_first_calls = tuple(
        (unit.item_id, unit.arm, unit.candidate_index)
        for unit in collection.plan.collection_schedule
    )
    if scheduled_first_calls != expected_first_calls:
        raise ExperimentInputError(
            "observations.schedule", "does not follow the frozen unit interleaving"
        )
    for item in collection.items:
        for stage in (CallStage.PROPOSAL, CallStage.RAW):
            digests = {
                call.intent.request_sha256
                for call in joined
                if call.intent.item_id == item.item.item_id and call.intent.stage is stage
            }
            if len(digests) != 1:
                raise ExperimentInputError(
                    "observations.request_sha256",
                    "same-item proposal/raw slots must repeat one frozen request",
                )


def _validate_completed_trajectory(trajectory: CandidateTrajectory) -> None:
    if trajectory.temperature != EXPERIMENT_TEMPERATURE:
        raise ExperimentInputError(
            "resume_state.completed_units",
            "agent trajectories must retain temperature 0.8",
        )
    if trajectory.work.proposal_llm_calls != 1:
        raise ExperimentInputError(
            "resume_state.completed_units",
            "agent trajectories require one actual proposal call",
        )
    if (trajectory.is_green and trajectory.critic_outcome is None) or (
        not trajectory.is_green and trajectory.critic_outcome is not None
    ):
        raise ExperimentInputError(
            "resume_state.completed_units",
            "critic coverage does not match the completed formal trajectory",
        )


def _validate_resume_state(
    plan: ExperimentPlan,
    state: ExperimentResumeState,
    sink: InMemoryObservationSink,
    raw_requests: tuple[RawBaselineRequest, ...],
) -> tuple[ObservationLedger, str | None]:
    """Validate one closed ledger against an exact continuous unit prefix."""

    if type(state) is not ExperimentResumeState:
        raise ExperimentInputError(
            "resume_state", "must be an exact ExperimentResumeState"
        )
    if len(state.pure_solver_outcomes) != len(plan.items):
        raise ExperimentInputError(
            "resume_state.pure_solver_outcomes",
            "must contain one optional slot for every planned item",
        )
    saw_missing_pure = False
    for index, completed_pure in enumerate(state.pure_solver_outcomes):
        if completed_pure is None:
            saw_missing_pure = True
            continue
        if saw_missing_pure:
            raise ExperimentInputError(
                "resume_state.pure_solver_outcomes",
                "must be one continuous callback-order prefix",
            )
        item = plan.items[index]
        if (
            completed_pure.item_id != item.item_id
            or completed_pure.source_context_sha256
            != arrangement_source_context_sha256(item.ir)
        ):
            raise ExperimentInputError(
                "resume_state.pure_solver_outcomes",
                "does not bind the planned item and source facts",
            )
        pure_outcome = completed_pure.outcome
        if pure_outcome.solver_calls != 1 or pure_outcome.llm_calls != 0:
            raise ExperimentInputError(
                "resume_state.pure_solver_outcomes",
                "each restored control must represent one solver call and no LLM calls",
            )
    if state.completed_units and saw_missing_pure:
        raise ExperimentInputError(
            "resume_state.pure_solver_outcomes",
            "all pure controls must complete before the collection prefix",
        )
    if len(state.completed_units) > len(plan.collection_schedule):
        raise ExperimentInputError(
            "resume_state.completed_units", "is longer than the planned schedule"
        )
    expected_prefix = plan.collection_schedule[: len(state.completed_units)]
    if tuple(value.unit for value in state.completed_units) != expected_prefix:
        raise ExperimentInputError(
            "resume_state.completed_units",
            "must be one exact continuous prefix of the collection schedule",
        )
    if sink.has_open_intent or sink.has_open_attempt:
        raise ExperimentInputError(
            "observation_sink", "resumed observations must have no orphan intent"
        )

    ledger = ObservationLedger(
        sink.intents,
        sink.results,
        sink.attempt_intents,
        sink.attempt_results,
    )
    indexed = _index_observations(plan, ledger)
    expected: set[_SemanticCallKey] = set()
    expected_sequence: list[_SemanticCallKey] = []
    raw_call_keys: set[_LogicalCallKey] = set()
    for completed in state.completed_units:
        unit = completed.unit
        item = plan.items[unit.item_position]
        pair_id = sample_pair_id(item.item_id, unit.candidate_index)
        if completed.source_context_sha256 != arrangement_source_context_sha256(item.ir):
            raise ExperimentInputError(
                "resume_state.completed_units",
                "completed unit does not bind the planned item source facts",
            )
        if unit.arm is CollectionArm.AGENT:
            trajectory = completed.trajectory
            assert trajectory is not None
            _validate_completed_trajectory(trajectory)
            unit_sequence = _expected_trajectory_sequence(item.item_id, trajectory)
            expected.update(unit_sequence)
            expected_sequence.extend(unit_sequence)
            continue

        raw_outcome = completed.raw_outcome
        assert raw_outcome is not None
        if raw_outcome.llm_calls != 1:
            raise ExperimentInputError(
                "resume_state.completed_units", "raw outcomes require one logical call"
            )
        # ``raw_requests`` is built from the exact plan item, goal, and profile before
        # any client is entered.  Its source digest binds restored raw rows to that item.
        request = raw_requests[unit.item_position]
        if raw_outcome.source_context_sha256 != request.source_context_sha256:
            raise ExperimentInputError(
                "resume_state.completed_units",
                "raw outcome does not bind the planned item source facts",
            )
        raw_semantic_key = (
            item.item_id,
            unit.candidate_index,
            CallStage.RAW,
            0,
            pair_id,
        )
        expected.add(raw_semantic_key)
        expected_sequence.append(raw_semantic_key)
        observation_key = _raw_observation_key(raw_outcome)
        if observation_key is None or observation_key in raw_call_keys:
            raise ExperimentInputError(
                "resume_state.completed_units",
                "raw observation keys are missing or duplicated",
            )
        raw_call_keys.add(observation_key)
        if raw_outcome.observation_key.run_id != plan.run_id:
            raise ExperimentInputError(
                "resume_state.completed_units",
                "raw observation run id does not match the plan",
            )
        if indexed.semantic_by_call_key.get(observation_key) != (
            item.item_id,
            unit.candidate_index,
            CallStage.RAW,
            0,
            pair_id,
        ):
            raise ExperimentInputError(
                "resume_state.completed_units",
                "raw outcome does not join its restored observation",
            )

    if indexed.actual != expected:
        raise ExperimentInputError(
            "resume_state.observations",
            "must exactly cover the completed unit prefix with no extra calls",
        )
    actual_sequence = tuple(
        (
            call.intent.item_id,
            call.intent.candidate_index,
            call.intent.stage,
            call.intent.stage_ordinal,
            call.intent.pair_id,
        )
        for call in indexed.joined
    )
    if actual_sequence != tuple(expected_sequence):
        raise ExperimentInputError(
            "resume_state.observations",
            "must preserve each completed unit as one contiguous ordered call slice",
        )
    if not raw_call_keys.issubset(indexed.actual_call_keys):
        raise ExperimentInputError(
            "resume_state.observations", "contains an unjoined raw observation"
        )
    scheduled_first_calls = tuple(
        (
            call.intent.item_id,
            CollectionArm.AGENT if call.intent.stage is CallStage.PROPOSAL else CollectionArm.RAW,
            call.intent.candidate_index,
        )
        for call in indexed.joined
        if call.intent.stage in {CallStage.PROPOSAL, CallStage.RAW}
    )
    expected_first_calls = tuple(
        (unit.item_id, unit.arm, unit.candidate_index) for unit in expected_prefix
    )
    if scheduled_first_calls != expected_first_calls:
        raise ExperimentInputError(
            "resume_state.observations", "does not follow the completed unit prefix"
        )
    for item in plan.items:
        for stage in (CallStage.PROPOSAL, CallStage.RAW):
            digests = {
                call.intent.request_sha256
                for call in indexed.joined
                if call.intent.item_id == item.item_id and call.intent.stage is stage
            }
            if len(digests) > 1:
                raise ExperimentInputError(
                    "resume_state.observations",
                    "same-item restored requests do not share one policy digest",
                )
    if not indexed.requested_model_ids:
        return ledger, None
    if len(indexed.requested_model_ids) != 1:
        raise ExperimentInputError(
            "resume_state.observations",
            "restored agent and raw calls must share one requested model",
        )
    return ledger, next(iter(indexed.requested_model_ids))


def _goal_at_source_tempo(goal: ArrangeGoal, item: CorpusItem) -> ArrangeGoal:
    return replace(goal, tempo_bpm=item.ir.meta.tempo_bpm, extras=dict(goal.extras))


def run_experiment(
    plan: ExperimentPlan,
    goal: ArrangeGoal,
    agent_llm: LLMClient,
    raw_llm: LLMClient,
    profile: Profile,
    *,
    observation_sink: InMemoryObservationSink | None = None,
    observation_clock_ns: Callable[[], int] | None = None,
    observation_request_guard: ObservationRequestGuard | None = None,
    resume_state: ExperimentResumeState | None = None,
    on_pure_solver_complete: Callable[[CorpusItem, PureSolverOutcome], None] | None = None,
    on_unit_complete: Callable[[CompletedExperimentUnit], None] | None = None,
) -> ExperimentCollection:
    """Collect every scheduled unit once and own both supplied clients.

    Agent candidates are built one at a time so the frozen schedule can interleave
    items and the raw arm.  The resulting slots are then restored to candidate-index
    order for every shared-prefix derivation.  Raw client ownership contexts are
    entered before observation wrappers are constructed, so both clients close exactly
    once even if either wrapper rejects model provenance during construction.  Resume
    accepts only a continuous prefix whose restored ledger contains no open or extra
    call; each newly completed deterministic control or schedule unit is synchronously
    reported before collection advances.
    """

    if type(plan) is not ExperimentPlan:
        raise ExperimentInputError("plan", "must be an exact ExperimentPlan")
    if type(goal) is not ArrangeGoal:
        raise ExperimentInputError("goal", "must be an exact ArrangeGoal")
    if agent_llm is raw_llm:
        raise ExperimentInputError(
            "llm_clients", "agent and raw arms require distinct owned client instances"
        )
    for field, callback in (
        ("on_pure_solver_complete", on_pure_solver_complete),
        ("on_unit_complete", on_unit_complete),
    ):
        if callback is not None and not callable(callback):
            raise ExperimentInputError(field, "must be null or callable")
    exact_profile = ensure_profile(profile)
    sink = observation_sink if observation_sink is not None else InMemoryObservationSink()
    if not isinstance(sink, InMemoryObservationSink):
        raise ExperimentInputError(
            "observation_sink", "must be a readable in-memory ledger or durable subclass"
        )
    goals = tuple(_goal_at_source_tempo(goal, item) for item in plan.items)
    raw_requests = tuple(
        build_raw_baseline_request(item.ir, goals[index], exact_profile)
        for index, item in enumerate(plan.items)
    )
    for index, request in enumerate(raw_requests):
        budget = plan.matched_budgets[index]
        if request.max_tokens != budget.proposal_tokens:
            raise ExperimentInputError(
                f"matched_budgets[{index}]",
                "does not match the shared proposal/raw request policy",
            )

    resumed_model_id: str | None = None
    completed_prefix: tuple[CompletedExperimentUnit, ...] = ()
    pure_slots: list[PureSolverOutcome | None] = [None] * len(plan.items)
    if resume_state is None:
        if sink.intents or sink.results or sink.attempt_intents or sink.attempt_results:
            raise ExperimentInputError("observation_sink", "must be fresh for one run")
        if sink.has_open_intent or sink.has_open_attempt:
            raise ExperimentInputError("observation_sink", "fresh sink has an open intent")
    else:
        _ledger, resumed_model_id = _validate_resume_state(
            plan,
            resume_state,
            sink,
            raw_requests,
        )
        completed_prefix = resume_state.completed_units
        pure_slots = [
            None if completed is None else completed.outcome
            for completed in resume_state.pure_solver_outcomes
        ]

    with ExitStack() as clients:
        owned_agent = clients.enter_context(managed_llm_client(agent_llm))
        owned_raw = clients.enter_context(managed_llm_client(raw_llm))
        observed_agent = (
            ObservingLLM(owned_agent, sink, request_guard=observation_request_guard)
            if observation_clock_ns is None
            else ObservingLLM(
                owned_agent,
                sink,
                clock_ns=observation_clock_ns,
                request_guard=observation_request_guard,
            )
        )
        observed_raw = (
            ObservingLLM(owned_raw, sink, request_guard=observation_request_guard)
            if observation_clock_ns is None
            else ObservingLLM(
                owned_raw,
                sink,
                clock_ns=observation_clock_ns,
                request_guard=observation_request_guard,
            )
        )
        if observed_agent.model_id != observed_raw.model_id:
            raise ExperimentInputError(
                "requested_model_id", "agent and raw arms must use the same model"
            )
        if resumed_model_id is not None and observed_agent.model_id != resumed_model_id:
            raise ExperimentInputError(
                "requested_model_id",
                "restored observations use a different requested model",
            )

        sequence = CallSequence(plan.run_id, start_call_index=len(sink.intents))
        for index, item in enumerate(plan.items):
            if pure_slots[index] is not None:
                continue
            outcome = run_pure_solver_baseline(item.ir, goals[index], exact_profile)
            pure_slots[index] = outcome
            if on_pure_solver_complete is not None:
                on_pure_solver_complete(item, outcome)

        trajectories_by_item: list[list[CandidateTrajectory]] = [[] for _item in plan.items]
        raw_by_item: list[list[RawLLMOutcome]] = [[] for _item in plan.items]
        for completed in completed_prefix:
            unit = completed.unit
            if unit.arm is CollectionArm.AGENT:
                trajectory = completed.trajectory
                assert trajectory is not None
                trajectories_by_item[unit.item_position].append(trajectory)
            else:
                raw_outcome = completed.raw_outcome
                assert raw_outcome is not None
                raw_by_item[unit.item_position].append(raw_outcome)

        for unit in plan.collection_schedule[len(completed_prefix) :]:
            item = plan.items[unit.item_position]
            family_id = item.family_id
            cluster_id = item.cluster_id
            if family_id is None or cluster_id is None:
                raise ExperimentInputError("plan.items", "corpus identities were not snapshotted")
            if unit.arm is CollectionArm.AGENT:
                scopes = sequence.bind_candidate(
                    item_id=item.item_id,
                    family_id=family_id,
                    cluster_id=cluster_id,
                    pair_id=sample_pair_id(item.item_id, unit.candidate_index),
                )
                trajectory = build_candidate_trajectory(
                    item.ir,
                    goals[unit.item_position],
                    observed_agent,
                    profile=exact_profile,
                    candidate_index=unit.candidate_index,
                    max_iters=plan.max_repair_iters,
                    use_critic=True,
                    temperature=plan.temperature,
                    call_scope_factory=scopes,
                )
                trajectories_by_item[unit.item_position].append(trajectory)
                completed = CompletedExperimentUnit(
                    unit,
                    arrangement_source_context_sha256(item.ir),
                    trajectory=trajectory,
                )
            else:
                scopes = sequence.bind_candidate(
                    item_id=item.item_id,
                    family_id=family_id,
                    cluster_id=cluster_id,
                    pair_id=sample_pair_id(item.item_id, unit.candidate_index),
                )
                raw_outcome = collect_raw_llm_baseline(
                    raw_requests[unit.item_position],
                    observed_raw,
                    exact_profile,
                    sample_index=unit.candidate_index,
                    call_scope_factory=scopes,
                )
                raw_by_item[unit.item_position].append(raw_outcome)
                completed = CompletedExperimentUnit(
                    unit,
                    arrangement_source_context_sha256(item.ir),
                    raw_outcome=raw_outcome,
                )
            if on_unit_complete is not None:
                on_unit_complete(completed)

        if any(outcome is None for outcome in pure_slots):  # pragma: no cover - invariant
            raise AssertionError("every pure-solver control must be available at finalization")
        pure = tuple(outcome for outcome in pure_slots if outcome is not None)

        item_collections = tuple(
            ItemCollection(
                item=plan.items[index],
                trajectories=tuple(
                    sorted(trajectories_by_item[index], key=lambda value: value.index)
                ),
                raw_outcomes=tuple(
                    sorted(raw_by_item[index], key=lambda value: value.sample_index)
                ),
                pure_solver=pure[index],
            )
            for index in range(len(plan.items))
        )
        ledger = ObservationLedger(
            sink.intents,
            sink.results,
            sink.attempt_intents,
            sink.attempt_results,
        )
        return ExperimentCollection(
            plan=plan,
            goal=replace(goal, extras=dict(goal.extras)),
            profile=exact_profile,
            items=item_collections,
            observations=ledger,
        )


@dataclass(frozen=True, slots=True)
class ScoredCheckpoint:
    """ITT score at one initial, terminal, raw, or deterministic checkpoint."""

    evidence_signature: str
    evaluated_dimensions: tuple[FaithfulnessDimension, ...]
    unavailable_dimensions: tuple[FaithfulnessDimension, ...]
    tab_available: bool
    melody_f1: float | None
    bass_root: float | None
    harmony: float | None
    faithfulness_passed: bool
    green: bool
    joint_success: bool
    fallback_assisted: bool
    llm_generated: bool
    llm_success: bool

    def __post_init__(self) -> None:
        if type(self.evidence_signature) is not str or not self.evidence_signature:
            raise ExperimentInputError("checkpoint.evidence_signature", "must be nonempty")
        if self.evaluated_dimensions != tuple(
            value for value in FAITHFULNESS_DIMENSIONS if value in self.evaluated_dimensions
        ):
            raise ExperimentInputError("checkpoint.evaluated_dimensions", "is noncanonical")
        if self.unavailable_dimensions != tuple(
            value for value in FAITHFULNESS_DIMENSIONS if value in self.unavailable_dimensions
        ):
            raise ExperimentInputError("checkpoint.unavailable_dimensions", "is noncanonical")
        if set(self.evaluated_dimensions) | set(self.unavailable_dimensions) != set(
            FAITHFULNESS_DIMENSIONS
        ) or not set(self.evaluated_dimensions).isdisjoint(self.unavailable_dimensions):
            raise ExperimentInputError("checkpoint.dimensions", "must form an exact partition")
        for name, value in (
            ("melody_f1", self.melody_f1),
            ("bass_root", self.bass_root),
            ("harmony", self.harmony),
        ):
            if value is not None and (
                type(value) is not float or not math.isfinite(value) or not 0.0 <= value <= 1.0
            ):
                raise ExperimentInputError(f"checkpoint.{name}", "must be null or a score in 0..1")
        bools = (
            self.tab_available,
            self.faithfulness_passed,
            self.green,
            self.joint_success,
            self.fallback_assisted,
            self.llm_generated,
            self.llm_success,
        )
        if any(type(value) is not bool for value in bools):
            raise ExperimentInputError("checkpoint", "status fields must be exact bools")
        scores = (self.melody_f1, self.bass_root, self.harmony)
        if not self.tab_available and any(score is not None for score in scores):
            raise ExperimentInputError("checkpoint", "no-tab outcomes require nullable scores")
        if self.joint_success is not (self.green and self.faithfulness_passed):
            raise ExperimentInputError("checkpoint.joint_success", "disagrees with its gates")
        if self.fallback_assisted and self.llm_generated:
            raise ExperimentInputError(
                "checkpoint.llm_generated", "fallback output is not model-generated"
            )
        if self.llm_success is not (self.joint_success and self.llm_generated):
            raise ExperimentInputError("checkpoint.llm_success", "disagrees with its gates")


@dataclass(frozen=True, slots=True)
class RepairPair:
    pair_id: str
    item_id: str
    candidate_index: int
    initial: ScoredCheckpoint
    terminal: ScoredCheckpoint


@dataclass(frozen=True, slots=True)
class SelectionOutcome:
    pair_id: str
    item_id: str
    k: int
    use_critic: bool
    winner_candidate_index: int | None
    score: ScoredCheckpoint
    critic_status: CriticStatus | None
    critic_score: float | None


@dataclass(frozen=True, slots=True)
class CriticSelectionPair:
    pair_id: str
    item_id: str
    k: int
    without_critic: SelectionOutcome
    with_critic: SelectionOutcome


@dataclass(frozen=True, slots=True)
class SearchSelectionPair:
    pair_id: str
    item_id: str
    best_of_1: SelectionOutcome
    best_of_4: SelectionOutcome


@dataclass(frozen=True, slots=True)
class PredicateReliability:
    pass_at_k: float
    pass_all_k: float


@dataclass(frozen=True, slots=True)
class ItemReliabilityPoint:
    k: int
    initial_green: PredicateReliability
    initial_joint: PredicateReliability
    terminal_green: PredicateReliability
    terminal_joint: PredicateReliability
    terminal_llm_success: PredicateReliability
    raw_green: PredicateReliability
    raw_joint: PredicateReliability


@dataclass(frozen=True, slots=True)
class ProviderUsageTotals:
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None


@dataclass(frozen=True, slots=True)
class CostSummary:
    logical_calls: int
    provider_attempts: int
    logical_requested_output_tokens: int
    attempt_reserved_output_tokens: int
    elapsed_microseconds: int
    provider_usage: ProviderUsageTotals


@dataclass(frozen=True, slots=True)
class PrefixCost:
    k: int
    with_critic: CostSummary
    without_critic: CostSummary


@dataclass(frozen=True, slots=True)
class CausalSearchCost:
    """Equal first-four-pool charge for the best-of-1/4 selector contrast."""

    pair_id: str
    best_of_1: CostSummary
    best_of_4: CostSummary

    def __post_init__(self) -> None:
        if self.best_of_1 != self.best_of_4:
            raise ExperimentInputError("causal_search_cost", "both selectors require equal cost")


@dataclass(frozen=True, slots=True)
class ItemCostViews:
    agent_prefixes: tuple[PrefixCost, ...]
    no_repair_prefixes: tuple[tuple[int, CostSummary], ...]
    raw_prefixes: tuple[tuple[int, CostSummary], ...]
    causal_best_1_vs_4: CausalSearchCost


@dataclass(frozen=True, slots=True)
class ItemSharedViews:
    item_id: str
    family_id: str
    cluster_id: str
    position: int
    layer: str
    genre: str
    synthetic_complexity: str
    polyphony: str
    evidence_signature: str
    repair_pairs: tuple[RepairPair, ...]
    search_and_critic: tuple[CriticSelectionPair, ...]
    causal_best_1_vs_4: SearchSelectionPair
    reliability: tuple[ItemReliabilityPoint, ...]
    raw_scores: tuple[ScoredCheckpoint, ...]
    pure_solver_score: ScoredCheckpoint
    costs: ItemCostViews
    matched_budget: ItemMatchedBudget


@dataclass(frozen=True, slots=True)
class SharedExperimentViews:
    run_id: str
    items: tuple[ItemSharedViews, ...]


def _evidence_signature(item: CorpusItem) -> str:
    if item.evidence is None:
        raise ExperimentInputError("item.evidence", "was not snapshotted")
    return item.evidence.signature


def _gate_scores(gate: FaithfulnessGate | None) -> tuple[float | None, float | None, float | None]:
    if gate is None:
        return (None, None, None)
    return (gate.melody_f1, gate.bass_root, gate.harmony)


def _score_checkpoint(
    item: CorpusItem,
    tab: Tab | None,
    oracle: OracleResult | None,
    *,
    fallback_assisted: bool,
    llm_generated: bool,
    gate: FaithfulnessGate | None = None,
) -> ScoredCheckpoint:
    evaluated = faithfulness_dimensions(item.ir)
    unavailable = tuple(
        dimension for dimension in FAITHFULNESS_DIMENSIONS if dimension not in evaluated
    )
    if tab is None:
        gate = None
    elif gate is None:
        gate = faithfulness(item.ir, tab)
    if gate is not None and gate.evaluated_dimensions != evaluated:
        raise ExperimentInputError("checkpoint.faithfulness", "evidence partition drifted")
    melody_f1, bass_root, harmony = _gate_scores(gate)
    passed = gate is not None and gate.passed
    green = oracle is not None and oracle.verdict == "GREEN"
    joint = green and passed
    return ScoredCheckpoint(
        evidence_signature=_evidence_signature(item),
        evaluated_dimensions=evaluated,
        unavailable_dimensions=unavailable,
        tab_available=tab is not None,
        melody_f1=melody_f1,
        bass_root=bass_root,
        harmony=harmony,
        faithfulness_passed=passed,
        green=green,
        joint_success=joint,
        fallback_assisted=fallback_assisted,
        llm_generated=llm_generated,
        llm_success=joint and llm_generated,
    )


def _trajectory_scores(
    item: CorpusItem, trajectory: CandidateTrajectory
) -> tuple[ScoredCheckpoint, ScoredCheckpoint]:
    fallback = trajectory.proposal.fallback_assisted
    llm_generated = trajectory.proposal.status is ProposalStatus.LLM_SUCCESS
    initial = _score_checkpoint(
        item,
        trajectory.iteration_zero.tab,
        trajectory.iteration_zero.oracle,
        fallback_assisted=fallback,
        llm_generated=llm_generated,
    )
    recomputed_terminal_gate = (
        None if trajectory.terminal.tab is None else faithfulness(item.ir, trajectory.terminal.tab)
    )
    if recomputed_terminal_gate != trajectory.faithfulness:
        raise ExperimentInputError(
            "trajectory.faithfulness", "does not match deterministic terminal rescore"
        )
    terminal = _score_checkpoint(
        item,
        trajectory.terminal.tab,
        trajectory.terminal.oracle,
        fallback_assisted=fallback,
        llm_generated=llm_generated,
        gate=recomputed_terminal_gate,
    )
    return initial, terminal


def _score_unverified_tab(
    item: CorpusItem,
    tab: Tab | None,
    profile: Profile,
    *,
    llm_generated: bool,
) -> ScoredCheckpoint:
    oracle = (
        None
        if tab is None
        else check_playability(
            tab,
            profile,
            tempo_bpm=item.ir.meta.tempo_bpm,
            beats_per_bar=item.ir.meta.time_sig[0],
        )
    )
    return _score_checkpoint(
        item,
        tab,
        oracle,
        fallback_assisted=False,
        llm_generated=llm_generated,
    )


def _arrange_pool(item: ItemCollection) -> ArrangePool:
    trajectories = item.trajectories
    return ArrangePool(
        candidates=tuple(
            trajectory if trajectory.terminal.tab is not None else None
            for trajectory in trajectories
        ),
        trace=Trace(),
        n=len(trajectories),
        candidate_traces=tuple(trajectory.trace_steps for trajectory in trajectories),
        trajectories=trajectories,
    )


def _winner_index(result_trace: Trace) -> int | None:
    selected = [
        step.candidate_index for step in result_trace.steps if step.event == "CANDIDATE_SELECTED"
    ]
    if not selected:
        return None
    winner = selected[-1]
    if type(winner) is not int or not 0 <= winner < EXPERIMENT_N_SAMPLES:
        raise ExperimentInputError("selection.winner", "is outside the collected pool")
    return winner


def _selection(
    item: ItemCollection,
    pool: ArrangePool,
    k: int,
    *,
    use_critic: bool,
) -> SelectionOutcome:
    result = best_of_k(pool, k, use_critic=use_critic)
    winner = _winner_index(result.trace)
    if winner is None:
        fallback = any(
            trajectory.proposal.fallback_assisted for trajectory in item.trajectories[:k]
        )
        score = _score_checkpoint(
            item.item,
            None,
            None,
            fallback_assisted=fallback,
            llm_generated=False,
        )
        critic_score = None
        critic_status = None
    else:
        trajectory = item.trajectories[winner]
        _initial, score = _trajectory_scores(item.item, trajectory)
        critic_score = trajectory.critic.overall if trajectory.critic is not None else None
        critic_status = (
            trajectory.critic_outcome.status if trajectory.critic_outcome is not None else None
        )
    return SelectionOutcome(
        pair_id=item_pair_id(f"selection-{k}", item.item.item_id),
        item_id=item.item.item_id,
        k=k,
        use_critic=use_critic,
        winner_candidate_index=winner,
        score=score,
        critic_status=critic_status,
        critic_score=critic_score,
    )


def _predicate_reliability(n: int, successes: int, k: int) -> PredicateReliability:
    return PredicateReliability(
        pass_at_k=pass_at_k(n, successes, k),
        pass_all_k=pass_hat_k_item(n, successes, k),
    )


def _reliability_points(
    repairs: tuple[RepairPair, ...],
    raw_scores: tuple[ScoredCheckpoint, ...],
) -> tuple[ItemReliabilityPoint, ...]:
    n = len(repairs)
    counts = (
        sum(pair.initial.green for pair in repairs),
        sum(pair.initial.joint_success for pair in repairs),
        sum(pair.terminal.green for pair in repairs),
        sum(pair.terminal.joint_success for pair in repairs),
        sum(pair.terminal.llm_success for pair in repairs),
        sum(score.green for score in raw_scores),
        sum(score.joint_success for score in raw_scores),
    )
    return tuple(
        ItemReliabilityPoint(
            k,
            *(_predicate_reliability(n, count, k) for count in counts),
        )
        for k in RELIABILITY_K_VALUES
    )


def _cost_summary(calls: Iterable[_JoinedCall]) -> CostSummary:
    selected = tuple(calls)
    logical_calls = len(selected)
    provider_attempts = sum(len(call.attempt_intents) for call in selected)
    logical_tokens = sum(call.intent.max_tokens for call in selected)
    attempt_tokens = sum(
        attempt.reserved_output_tokens for call in selected for attempt in call.attempt_intents
    )
    elapsed = sum(call.result.elapsed_microseconds for call in selected)

    def usage_total(
        field: Literal[
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ],
    ) -> int | None:
        if not selected:
            return 0
        total = 0
        for call in selected:
            if not call.result.provider.available:
                return None
            value = getattr(call.result.provider, field)
            if type(value) is not int:
                return None
            total += value
        return total

    usage = ProviderUsageTotals(
        usage_total("input_tokens"),
        usage_total("output_tokens"),
        usage_total("cache_creation_input_tokens"),
        usage_total("cache_read_input_tokens"),
    )
    return CostSummary(
        logical_calls,
        provider_attempts,
        logical_tokens,
        attempt_tokens,
        elapsed,
        usage,
    )


def _cost_views(
    item: ItemCollection,
    joined: tuple[_JoinedCall, ...],
) -> ItemCostViews:
    item_calls = tuple(call for call in joined if call.intent.item_id == item.item.item_id)

    def agent_prefix(k: int, *, critic: bool) -> CostSummary:
        return _cost_summary(
            call
            for call in item_calls
            if call.intent.candidate_index < k
            and call.intent.stage in {CallStage.PROPOSAL, CallStage.REPAIR, CallStage.CRITIC}
            and (critic or call.intent.stage is not CallStage.CRITIC)
        )

    def raw_prefix(k: int) -> CostSummary:
        return _cost_summary(
            call
            for call in item_calls
            if call.intent.candidate_index < k and call.intent.stage is CallStage.RAW
        )

    def no_repair_prefix(k: int) -> CostSummary:
        return _cost_summary(
            call
            for call in item_calls
            if call.intent.candidate_index < k and call.intent.stage is CallStage.PROPOSAL
        )

    prefixes = tuple(
        PrefixCost(k, agent_prefix(k, critic=True), agent_prefix(k, critic=False))
        for k in SEARCH_K_VALUES
    )
    no_repair_prefixes = tuple((k, no_repair_prefix(k)) for k in RELIABILITY_K_VALUES)
    raw_prefixes = tuple((k, raw_prefix(k)) for k in RELIABILITY_K_VALUES)
    equal_first_four = agent_prefix(FULL_SELECTION_K, critic=True)
    causal = CausalSearchCost(
        item_pair_id("causal-search-1-4", item.item.item_id),
        equal_first_four,
        equal_first_four,
    )
    return ItemCostViews(prefixes, no_repair_prefixes, raw_prefixes, causal)


def derive_shared_views(collection: ExperimentCollection) -> SharedExperimentViews:
    """Purely derive all headline software views from one complete collection."""

    if type(collection) is not ExperimentCollection:
        raise ExperimentInputError("collection", "must be an exact ExperimentCollection")
    _validate_collection_observation_coverage(collection)
    joined = _joined_observations(collection.observations)
    budget_by_item = {value.item_id: value for value in collection.plan.matched_budgets}
    views: list[ItemSharedViews] = []
    for item in collection.items:
        item_id = item.item.item_id
        family_id = item.item.family_id
        cluster_id = item.item.cluster_id
        position = item.item.position
        if family_id is None or cluster_id is None or position is None:
            raise ExperimentInputError("collection.item", "corpus identities were not snapshotted")
        repairs = tuple(
            RepairPair(
                sample_pair_id(item_id, trajectory.index),
                item_id,
                trajectory.index,
                *_trajectory_scores(item.item, trajectory),
            )
            for trajectory in item.trajectories
        )
        raw_scores = tuple(
            _score_unverified_tab(
                item.item,
                outcome.tab,
                collection.profile,
                llm_generated=outcome.status is RawStatus.VALID_TAB,
            )
            for outcome in item.raw_outcomes
        )
        pure_score = _score_unverified_tab(
            item.item,
            item.pure_solver.tab,
            collection.profile,
            llm_generated=False,
        )
        pool = _arrange_pool(item)
        selections = tuple(
            CriticSelectionPair(
                item_pair_id(f"critic-{k}", item_id),
                item_id,
                k,
                _selection(item, pool, k, use_critic=False),
                _selection(item, pool, k, use_critic=True),
            )
            for k in SEARCH_K_VALUES
        )
        selection_by_k = {value.k: value.with_critic for value in selections}
        causal_pair = SearchSelectionPair(
            item_pair_id("causal-search-1-4", item_id),
            item_id,
            selection_by_k[1],
            selection_by_k[4],
        )
        views.append(
            ItemSharedViews(
                item_id=item_id,
                family_id=family_id,
                cluster_id=cluster_id,
                position=position,
                layer=item.item.layer,
                genre=item.item.genre,
                synthetic_complexity=item.item.synthetic_complexity,
                polyphony=item.item.polyphony,
                evidence_signature=_evidence_signature(item.item),
                repair_pairs=repairs,
                search_and_critic=selections,
                causal_best_1_vs_4=causal_pair,
                reliability=_reliability_points(repairs, raw_scores),
                raw_scores=raw_scores,
                pure_solver_score=pure_score,
                costs=_cost_views(item, joined),
                matched_budget=budget_by_item[item_id],
            )
        )
    return SharedExperimentViews(collection.plan.run_id, tuple(views))


__all__ = [
    "EXPERIMENT_MAX_REPAIR_ITERS",
    "EXPERIMENT_N_SAMPLES",
    "EXPERIMENT_TEMPERATURE",
    "FULL_SELECTION_K",
    "RELIABILITY_K_VALUES",
    "SEARCH_K_VALUES",
    "BudgetLimitDimension",
    "BudgetMatchStatus",
    "CausalSearchCost",
    "CollectionArm",
    "CompletedExperimentUnit",
    "CompletedPureSolver",
    "CostSummary",
    "CriticSelectionPair",
    "ExperimentCollection",
    "ExperimentInputError",
    "ExperimentPlan",
    "ExperimentResumeState",
    "ItemCollection",
    "ItemCostViews",
    "ItemMatchedBudget",
    "ItemReliabilityPoint",
    "ItemSchedule",
    "ItemSharedViews",
    "MatchedPrefix",
    "ObservationLedger",
    "PredicateReliability",
    "PrefixCost",
    "ProviderUsageTotals",
    "RepairPair",
    "ScheduledUnit",
    "ScoredCheckpoint",
    "SearchSelectionPair",
    "SelectionOutcome",
    "SharedExperimentViews",
    "derive_shared_views",
    "item_pair_id",
    "match_budget_prefix",
    "preflight_experiment",
    "run_experiment",
    "sample_pair_id",
]
