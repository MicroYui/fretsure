"""Benchmark runners.

The historical ``run_benchmark`` Python API remains available for compatibility.
The public ``fretsure-bench`` command owns the benchmark-v2 artifact workflow:
deterministic stub or live collection, complete-unit resume, and offline replay.
"""

import argparse
import hashlib
import json
import os
import queue
import signal
import sys
import tempfile
import threading
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from types import FrameType
from typing import Any, cast

from fretsure.agent.arranger import (
    ARRANGEMENT_UNISON_COALESCER_VERSION,
    PROPOSAL_COMPACT_PROTOCOL_VERSION,
    PROPOSAL_OBJECT_PROTOCOL_VERSION,
    ArrangeGoal,
    arrangement_source_context_sha256,
)
from fretsure.agent.critic import CRITIC_MAX_TOKENS
from fretsure.agent.repair import REPAIR_MAX_TOKENS
from fretsure.bench.ablation import (
    AblationConfig,
    ConfigMetrics,
    LLMFactory,
    PairedBestOfN,
    PairedCritic,
    leave_one_out,
    paired_best_of_n,
    paired_critic,
)
from fretsure.bench.artifacts import (
    ArtifactCode,
    ArtifactError,
    ArtifactLimits,
    ArtifactStore,
    BenchmarkManifest,
    BenchmarkReceipt,
    BenchmarkRow,
    BlobRecord,
    CompleteUnitReservation,
    FinalizationInputs,
    FinalizedReport,
    ReplayBundle,
    RowKey,
    RowType,
    blob_record_from_dict,
    blob_record_to_dict,
    build_manifest,
    build_row,
    load_replay_bundle,
    parse_canonical_json_bytes,
    publish_replay_bundle,
    row_from_dict,
    row_to_dict,
)
from fretsure.bench.baselines import (
    RAW_COMPACT_PROTOCOL_VERSION,
    RAW_OBJECT_PROTOCOL_VERSION,
    PureSolverOutcome,
    run_pure_solver_baseline,
)
from fretsure.bench.concurrent import (
    CollectionExecutionContract,
    ConcurrentExecutionCode,
    ConcurrentExecutionError,
    ConcurrentUnitCoordinator,
    LaneObservationPolicy,
    ReadyUnit,
    UnitPermit,
    rebase_journal_events,
    rebase_observation_key,
)
from fretsure.bench.contracts import canonical_json_bytes
from fretsure.bench.corpus import (
    PRIMARY_PROCEDURAL_BASE_SEED,
    CorpusItem,
    ProceduralCorpusConfig,
    build_primary_procedural_corpus,
    corpus_from_dict,
    corpus_sha256,
    corpus_to_dict,
)
from fretsure.bench.experiment import (
    EXPERIMENT_N_SAMPLES,
    CompletedExperimentUnit,
    CompletedPureSolver,
    ExperimentInputError,
    ExperimentPlan,
    MatchedPrefix,
    ObservationLedger,
    assemble_experiment_collection,
    execute_scheduled_unit,
    item_pair_id,
    preflight_experiment,
    run_experiment,
    sample_pair_id,
)
from fretsure.bench.generator import GenConfig, generate_leadsheet
from fretsure.bench.observe import (
    AttemptIntent,
    AttemptResult,
    CallIntent,
    CallResult,
    CallSequence,
    ObservingLLM,
)
from fretsure.bench.precall import (
    BenchmarkPreCallConfig,
    pre_call_artifact_budget,
    pre_call_config_from_bytes,
    preregistered_artifact_budget,
    require_explicit_spend_confirmation,
    require_live_authorization,
    validate_current_runtime,
)
from fretsure.bench.preregistration import (
    BenchmarkPreregistration,
    preregistration_from_bytes,
    preregistration_from_dict,
)
from fretsure.bench.progress import ProgressConfig, ProgressReporter
from fretsure.bench.report import (
    BenchmarkReport as BenchmarkV2Report,
)
from fretsure.bench.report import (
    ReplayMode,
    ReportInputError,
    build_benchmark_report,
    collection_to_row_bundle,
    completed_unit_to_row_bundle,
    publication_bindings_from_artifacts,
    pure_outcome_to_row_bundle,
    report_to_markdown,
    resume_state_from_rows,
)
from fretsure.llm.client import (
    DEFAULT_PROXY_MODEL,
    MAX_PROXY_OUTPUT_TOKENS,
    MAX_PROXY_TEXT_BYTES_PER_TOKEN,
    MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
    PROXY_REQUEST_TIMEOUT_SECONDS,
    LLMClient,
    LLMIntegrityError,
    LLMModelIdError,
    close_llm_client,
    require_numeric_loopback_proxy_environment,
    snapshot_llm_model_id,
    validate_llm_model_id,
)
from fretsure.metrics.fidelity import FIDELITY_CHECKER_VERSION
from fretsure.oracle.core import CHECKER_VERSION
from fretsure.oracle.input import (
    MAX_SOLVER_WORK_UNITS,
    ORACLE_INPUT_SCHEMA_VERSION,
    ensure_profile,
)
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.solver.score import (
    MAX_SCORE_SOLVER_AGGREGATE_WORK_UNITS,
    MAX_SCORE_SOLVER_SEGMENTS,
    SCORE_SOLVER_VERSION,
)

MAX_BENCHMARK_ITEMS = 1_000
MAX_BENCHMARK_BARS = 64
MAX_BENCHMARK_CORPUS_BARS = 4_096
MAX_BENCHMARK_SEED = (1 << 63) - 1

BENCHMARK_V2_RUN_CONFIG_VERSION = "benchmark-v2-run-config@0.1.0"
BENCHMARK_V2_ANALYSIS_VERSION = "benchmark-v2-analysis@0.2.0"
BENCHMARK_V2_STUB_MODEL_ID = "fretsure-benchmark-stub@0.1.0"
MAX_BENCHMARK_V2_ITEMS = 900
DEFAULT_BENCHMARK_V2_BOOTSTRAP_REPETITIONS = 10_000
DEFAULT_BENCHMARK_V2_SIGN_FLIP_DRAWS = 100_000
MAX_BENCHMARK_V2_SIGN_FLIP_SEED = MAX_BENCHMARK_SEED - 120_100
_OPERATIONAL_PROGRESS_POLL_SECONDS = 60.0


def _max_v2_bootstrap_seed(family_count: int) -> int:
    derived_offset = max(140_100, max(0, family_count - 1) * 1_000_000 + 50_100)
    return MAX_BENCHMARK_SEED - derived_offset


def _analysis_contract_sha256(
    preregistration: BenchmarkPreregistration | None = None,
) -> str:
    contract: dict[str, object] = {
        "arrangement_unison_coalescer": ARRANGEMENT_UNISON_COALESCER_VERSION,
        "checker_version": CHECKER_VERSION,
        "fidelity_checker_version": FIDELITY_CHECKER_VERSION,
        "input_schema_version": ORACLE_INPUT_SCHEMA_VERSION,
        "proposal_compact_protocol": PROPOSAL_COMPACT_PROTOCOL_VERSION,
        "proposal_object_protocol": PROPOSAL_OBJECT_PROTOCOL_VERSION,
        "raw_compact_protocol": RAW_COMPACT_PROTOCOL_VERSION,
        "raw_object_protocol": RAW_OBJECT_PROTOCOL_VERSION,
        "report_contract": BENCHMARK_V2_ANALYSIS_VERSION,
        "score_solver_aggregate_work_limit": MAX_SCORE_SOLVER_AGGREGATE_WORK_UNITS,
        "score_solver_composition": SCORE_SOLVER_VERSION,
        "score_solver_maximum_segments": MAX_SCORE_SOLVER_SEGMENTS,
        "score_solver_per_segment_work_limit": MAX_SOLVER_WORK_UNITS,
    }
    if preregistration is not None:
        preregistered = preregistration.to_dict()
        model = cast(dict[str, object], preregistered["model_and_prompts"])
        contract["preregistered_prompts"] = model["prompts"]
        contract["preregistered_versions"] = preregistered["versions"]
    payload = canonical_json_bytes(contract)
    return hashlib.sha256(b"fretsure:benchmark-v2-analysis-contract@0.1.0\0" + payload).hexdigest()


BENCHMARK_V2_ANALYSIS_CONTRACT_SHA256 = _analysis_contract_sha256()


class BenchmarkInputError(ValueError):
    """Typed failure for benchmark controls outside the finite run envelope."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid benchmark {field}: {detail}")


FORMAL_PROVIDER_MESSAGE_OVERHEAD_TOKENS = 256


class FormalRequestCeilingError(LLMIntegrityError):
    """A live request falls outside its pre-call billing envelope."""

    def __init__(self, field: str, upper_bound: int, ceiling: int) -> None:
        self.field = field
        self.upper_bound = upper_bound
        self.ceiling = ceiling
        super().__init__(
            f"formal request {field} upper bound {upper_bound} exceeds ceiling {ceiling}"
        )


class _OperationalResumeClientUnavailable(RuntimeError):
    """A resumable output remains untouched because worker clients could not open."""


@dataclass(frozen=True, slots=True)
class BenchmarkV2Config:
    """Small deterministic collection config embedded with the full corpus snapshot."""

    family_count: int = 1
    base_seed: int = PRIMARY_PROCEDURAL_BASE_SEED
    bars: int = 1
    schedule_seed: int = 0
    bootstrap_seed: int = 0
    bootstrap_repetitions: int = DEFAULT_BENCHMARK_V2_BOOTSTRAP_REPETITIONS
    sign_flip_seed: int = 0
    sign_flip_draws: int = DEFAULT_BENCHMARK_V2_SIGN_FLIP_DRAWS
    stub: bool = True
    requested_model_id: str | None = None
    run_id: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.family_count) is not int
            or not 1 <= self.family_count <= MAX_BENCHMARK_V2_ITEMS
        ):
            raise BenchmarkInputError(
                "family_count", f"must be an exact integer in 1..{MAX_BENCHMARK_V2_ITEMS}"
            )
        for name, value in (
            ("base_seed", self.base_seed),
            ("schedule_seed", self.schedule_seed),
        ):
            if type(value) is not int or not 0 <= value <= MAX_BENCHMARK_SEED:
                raise BenchmarkInputError(name, "must be an exact integer in 0..2^63-1")
        maximum_bootstrap_seed = _max_v2_bootstrap_seed(self.family_count)
        if (
            type(self.bootstrap_seed) is not int
            or not 0 <= self.bootstrap_seed <= maximum_bootstrap_seed
        ):
            raise BenchmarkInputError(
                "bootstrap_seed",
                f"must be an exact integer in 0..{maximum_bootstrap_seed}",
            )
        if (
            type(self.sign_flip_seed) is not int
            or not 0 <= self.sign_flip_seed <= MAX_BENCHMARK_V2_SIGN_FLIP_SEED
        ):
            raise BenchmarkInputError(
                "sign_flip_seed",
                f"must be an exact integer in 0..{MAX_BENCHMARK_V2_SIGN_FLIP_SEED}",
            )
        if type(self.bars) is not int or not 1 <= self.bars <= MAX_BENCHMARK_BARS:
            raise BenchmarkInputError(
                "bars", f"must be an exact integer in 1..{MAX_BENCHMARK_BARS}"
            )
        if self.family_count * self.bars > MAX_BENCHMARK_CORPUS_BARS:
            raise BenchmarkInputError(
                "family_count*bars", f"must not exceed {MAX_BENCHMARK_CORPUS_BARS}"
            )
        if (
            type(self.bootstrap_repetitions) is not int
            or not 1 <= self.bootstrap_repetitions <= 100_000
        ):
            raise BenchmarkInputError(
                "bootstrap_repetitions", "must be an exact integer in 1..100000"
            )
        if type(self.sign_flip_draws) is not int or not 1 <= self.sign_flip_draws <= 1_000_000:
            raise BenchmarkInputError("sign_flip_draws", "must be an exact integer in 1..1000000")
        if type(self.stub) is not bool:
            raise BenchmarkInputError("stub", "must be an exact bool")
        if self.requested_model_id is not None:
            _benchmark_model_id(self.requested_model_id)
        if self.run_id is not None and (
            type(self.run_id) is not str
            or not self.run_id
            or len(self.run_id) > 128
            or not self.run_id.isprintable()
        ):
            raise BenchmarkInputError("run_id", "must be null or one bounded printable string")


@dataclass(frozen=True, slots=True)
class BenchmarkV2Context:
    config: BenchmarkV2Config
    manifest: BenchmarkManifest
    plan: ExperimentPlan
    goal: ArrangeGoal
    profile: Profile
    requested_model_id: str
    preregistration: BenchmarkPreregistration | None = None
    pre_call_config: BenchmarkPreCallConfig | None = None


@dataclass(frozen=True, slots=True)
class BenchmarkV2Result:
    receipt: BenchmarkReceipt
    report: BenchmarkV2Report | None


def _benchmark_model_id(value: object) -> str:
    try:
        return validate_llm_model_id(value)
    except LLMModelIdError as error:
        raise BenchmarkInputError("llm_model_id", str(error)) from None


def _exact_object(value: object, field: str, keys: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise BenchmarkInputError(field, "must contain the exact frozen keys")
    return value


def _exact_int(value: object, field: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise BenchmarkInputError(field, f"must be an exact integer in {minimum}..{maximum}")
    return value


def _matched_prefix_wire(value: MatchedPrefix) -> dict[str, object]:
    return {
        "call_quotient": value.call_quotient,
        "limiting_dimension": value.limiting_dimension.value,
        "prefix_samples": value.prefix_samples,
        "remaining_calls": value.remaining_calls,
        "remaining_tokens": value.remaining_tokens,
        "spent_calls": value.spent_calls,
        "spent_tokens": value.spent_tokens,
        "status": value.status.value,
        "target_calls": value.target_calls,
        "target_tokens": value.target_tokens,
        "token_quotient": value.token_quotient,
        "unit_calls": value.unit_calls,
        "unit_tokens": value.unit_tokens,
    }


def _plan_wire(plan: ExperimentPlan) -> dict[str, object]:
    return {
        "collection_schedule": [
            {
                "arm": unit.arm.value,
                "candidate_index": unit.candidate_index,
                "item_id": unit.item_id,
                "item_position": unit.item_position,
                "round_index": unit.round_index,
            }
            for unit in plan.collection_schedule
        ],
        "item_schedules": [
            {
                "candidate_permutation": list(schedule.candidate_permutation),
                "item_id": schedule.item_id,
            }
            for schedule in plan.item_schedules
        ],
        "matched_budgets": [
            {
                "item_id": budget.item_id,
                "no_repair": _matched_prefix_wire(budget.no_repair),
                "proposal_tokens": budget.proposal_tokens,
                "raw": _matched_prefix_wire(budget.raw),
                "repair_tokens": budget.repair_tokens,
                "target_calls": budget.target_calls,
                "target_tokens": budget.target_tokens,
            }
            for budget in plan.matched_budgets
        ],
        "max_repair_iters": plan.max_repair_iters,
        "n_samples": plan.n_samples,
        "reliability_k": list(plan.reliability_k),
        "schedule_seed": plan.schedule_seed,
        "search_k": list(plan.search_k),
        "temperature": plan.temperature,
    }


def _goal_wire(goal: ArrangeGoal) -> dict[str, object]:
    return {
        "capo": goal.capo,
        "extras": dict(sorted(goal.extras.items())),
        "style": goal.style,
        "tempo_bpm": goal.tempo_bpm,
        "tempo_policy": "source_item",
        "tier": goal.tier,
        "tuning": list(goal.tuning),
    }


def _expected_v2_rows(plan: ExperimentPlan) -> tuple[RowKey, ...]:
    rows: list[RowKey] = []
    for item in plan.items:
        rows.append(
            RowKey(
                RowType.PURE_SOLVER,
                item.item_id,
                None,
                None,
                item_pair_id("pure-solver", item.item_id),
            )
        )
        for index in range(EXPERIMENT_N_SAMPLES):
            pair_id = sample_pair_id(item.item_id, index)
            rows.append(RowKey(RowType.CANDIDATE, item.item_id, index, index, pair_id))
            rows.append(RowKey(RowType.RAW, item.item_id, index, index, pair_id))
    return tuple(sorted(rows, key=lambda value: value.sort_key))


def _reservation_from_wire(value: dict[str, int]) -> CompleteUnitReservation:
    return CompleteUnitReservation(
        value["logical_calls"],
        value["attempts"],
        value["requested_output_tokens"],
        value["attempt_reserved_output_tokens"],
        value["response_text_bytes"],
        value["transport_response_bytes"],
        value["recorded_provider_call_elapsed_microseconds"],
    )


def _v2_limits(
    item_count: int,
    *,
    preregistration: BenchmarkPreregistration | None = None,
    pre_call_config: BenchmarkPreCallConfig | None = None,
) -> ArtifactLimits:
    if preregistration is not None and pre_call_config is not None:
        maximum, reservation = pre_call_artifact_budget(pre_call_config)
    elif preregistration is not None:
        maximum, reservation = preregistered_artifact_budget(preregistration)
    else:
        maximum_calls = item_count * 110
        return ArtifactLimits(
            max_rows=item_count * 21,
            max_blobs=item_count * 83,
            max_calls=maximum_calls,
            max_attempts=maximum_calls * 3,
            max_json_bytes=256 * 1024 * 1024,
            max_jsonl_line_bytes=4 * 1024 * 1024,
        )
    return ArtifactLimits(
        max_rows=item_count * 21,
        max_blobs=item_count * 83,
        max_calls=maximum["max_logical_calls"],
        max_attempts=maximum["max_attempts"],
        max_json_bytes=256 * 1024 * 1024,
        max_jsonl_line_bytes=4 * 1024 * 1024,
        max_requested_output_tokens=maximum["max_requested_output_tokens"],
        max_attempt_reserved_output_tokens=maximum["max_attempt_reserved_output_tokens"],
        max_response_text_bytes=maximum["max_response_text_bytes"],
        max_transport_response_bytes=maximum["max_transport_response_bytes"],
        max_wall_microseconds=maximum["max_recorded_provider_call_elapsed_microseconds"],
        complete_unit_reservation=_reservation_from_wire(reservation),
    )


def _derived_run_id(config: BenchmarkV2Config, corpus_digest: str) -> str:
    if config.run_id is not None:
        return config.run_id
    digest = hashlib.sha256(
        b"fretsure:benchmark-v2-run-id@0.1.0\0"
        + canonical_json_bytes(
            {
                "base_seed": config.base_seed,
                "corpus_sha256": corpus_digest,
                "schedule_seed": config.schedule_seed,
                "stub": config.stub,
            }
        )
    ).hexdigest()
    return f"benchmark-v2-{digest[:24]}"


def _v2_parameters(
    config: BenchmarkV2Config,
    plan: ExperimentPlan,
    goal: ArrangeGoal,
    profile: Profile,
    requested_model_id: str,
    *,
    preregistration: BenchmarkPreregistration | None = None,
    pre_call_config: BenchmarkPreCallConfig | None = None,
    procedural_parameters: dict[str, object] | None = None,
) -> dict[str, object]:
    if pre_call_config is None:
        analysis_sha256 = _analysis_contract_sha256(preregistration)
        analysis_kind = (
            "preregistered_analysis_contract_sha256"
            if preregistration is not None
            else "software_stub_analysis_contract_sha256"
        )
        analysis_parameters: dict[str, object] = {
            "analysis_contract_sha256": analysis_sha256,
            "binding_kind": analysis_kind,
            "version": BENCHMARK_V2_ANALYSIS_VERSION,
        }
        execution: dict[str, object] = {
            "analysis_binding": {
                "kind": analysis_kind,
                "sha256": analysis_sha256,
            },
            "execution_git_sha": None,
            "mode": "stub",
        }
    else:
        execution = cast(dict[str, object], pre_call_config.to_dict()["execution"])
        external_binding = cast(dict[str, object], execution["analysis_binding"])
        analysis_sha256 = pre_call_config.analysis_code_sha256
        analysis_parameters = {
            "analysis_code_sha256": analysis_sha256,
            "binding_kind": external_binding["kind"],
            "version": BENCHMARK_V2_ANALYSIS_VERSION,
        }
    allowed_returned_model_id = (
        requested_model_id if pre_call_config is None else pre_call_config.allowed_returned_model_id
    )
    pre_call_wire: dict[str, object] | None = None
    if pre_call_config is not None:
        pre_call_wire = pre_call_config.to_dict()
        del pre_call_wire["preregistration"]
    return {
        "analysis": analysis_parameters,
        "corpus": (
            corpus_to_dict(plan.items)
            if preregistration is None
            else {"source": "parameters.preregistration.wire.corpus.snapshot"}
        ),
        "experiment": (
            _plan_wire(plan)
            if preregistration is None
            else {"source": "parameters.preregistration.wire.schedule"}
        ),
        "goal": _goal_wire(goal),
        "execution": execution,
        "model": {
            "allowed_returned_model_id": allowed_returned_model_id,
            "requested_model_id": requested_model_id,
            "returned_model_rule": "exact_equal",
        },
        "pre_call": pre_call_wire,
        "preregistration": (
            None
            if preregistration is None
            else {
                "raw_sha256": hashlib.sha256(preregistration.wire_json).hexdigest(),
                "wire": preregistration.to_dict(),
            }
        ),
        "procedural": {
            **(
                {
                    "bars": config.bars,
                    "base_seed": config.base_seed,
                    "family_count": config.family_count,
                    "split": "test",
                }
                if procedural_parameters is None
                else procedural_parameters
            ),
        },
        "profile": {
            "fingerprint": profile.fingerprint,
            "version": profile.version,
        },
        "report": {
            "bootstrap_repetitions": config.bootstrap_repetitions,
            "bootstrap_seed": config.bootstrap_seed,
            "sign_flip_draws": config.sign_flip_draws,
            "sign_flip_seed": config.sign_flip_seed,
        },
        "schema": BENCHMARK_V2_RUN_CONFIG_VERSION,
    }


def _build_context_from_items(
    config: BenchmarkV2Config,
    items: tuple[CorpusItem, ...],
    *,
    preregistration: BenchmarkPreregistration | None = None,
    pre_call_config: BenchmarkPreCallConfig | None = None,
    procedural_parameters: dict[str, object] | None = None,
) -> BenchmarkV2Context:
    corpus_digest = corpus_sha256(items)
    run_id = _derived_run_id(config, corpus_digest)
    plan = preflight_experiment(items, run_id=run_id, schedule_seed=config.schedule_seed)
    goal = ArrangeGoal()
    profile = MEDIAN_HAND
    requested_model_id = _benchmark_model_id(
        pre_call_config.requested_model_id
        if pre_call_config is not None
        else config.requested_model_id
        if config.requested_model_id is not None
        else BENCHMARK_V2_STUB_MODEL_ID
        if config.stub
        else DEFAULT_PROXY_MODEL
    )
    analysis_code_sha256 = (
        _analysis_contract_sha256(preregistration)
        if pre_call_config is None
        else pre_call_config.analysis_code_sha256
    )
    manifest = build_manifest(
        run_id=run_id,
        corpus_sha256=corpus_digest,
        analysis_code_sha256=analysis_code_sha256,
        stub=config.stub,
        expected_rows=_expected_v2_rows(plan),
        limits=_v2_limits(
            len(items),
            preregistration=preregistration,
            pre_call_config=pre_call_config,
        ),
        parameters=_v2_parameters(
            config,
            plan,
            goal,
            profile,
            requested_model_id,
            preregistration=preregistration,
            pre_call_config=pre_call_config,
            procedural_parameters=procedural_parameters,
        ),
    )
    return BenchmarkV2Context(
        config,
        manifest,
        plan,
        goal,
        profile,
        requested_model_id,
        preregistration,
        pre_call_config,
    )


def build_benchmark_v2_context(config: BenchmarkV2Config) -> BenchmarkV2Context:
    """Build one strict procedural v2 plan and its self-contained manifest."""

    if type(config) is not BenchmarkV2Config:
        raise BenchmarkInputError("config", "must be an exact BenchmarkV2Config")
    if not config.stub:
        raise BenchmarkInputError(
            "pre_call_config",
            "live collection requires a validated pre-call config",
        )
    items = build_primary_procedural_corpus(
        ProceduralCorpusConfig(
            family_count=config.family_count,
            base_seed=config.base_seed,
            bars=config.bars,
            split="test",
        )
    )
    return _build_context_from_items(config, items)


def _preregistration_context(
    preregistration: BenchmarkPreregistration,
    *,
    stub: bool,
    pre_call_config: BenchmarkPreCallConfig | None = None,
) -> BenchmarkV2Context:
    if type(preregistration) is not BenchmarkPreregistration:
        raise BenchmarkInputError("preregistration", "must be an exact BenchmarkPreregistration")
    if (pre_call_config is None) != stub:
        raise BenchmarkInputError(
            "pre_call_config", "stub must omit and live must provide pre-call config"
        )
    wire = preregistration.to_dict()
    corpus = _exact_object(
        wire["corpus"],
        "preregistration.corpus",
        frozenset(
            {
                "artifact_sha256",
                "contamination_clean",
                "corpus_sha256",
                "counts",
                "ordered_bindings",
                "primary",
                "public_secondary",
                "snapshot",
                "source_census_sha256",
            }
        ),
    )
    items = corpus_from_dict(corpus["snapshot"])
    primary = _exact_object(
        corpus["primary"],
        "preregistration.corpus.primary",
        frozenset({"bars", "base_seed", "family_count", "generator_version", "layer", "split"}),
    )
    inference = cast(dict[str, object], wire["inference"])
    bootstrap = cast(dict[str, object], inference["bootstrap"])
    sign_flip = cast(dict[str, object], inference["sign_flip"])
    schedule = cast(dict[str, object], wire["schedule"])
    formal_run_id = cast(str, wire["run_id"])
    config = BenchmarkV2Config(
        family_count=len(items),
        base_seed=cast(int, primary["base_seed"]),
        bars=cast(int, primary["bars"]),
        schedule_seed=cast(int, schedule["schedule_seed"]),
        bootstrap_seed=cast(int, bootstrap["seed"]),
        bootstrap_repetitions=cast(int, bootstrap["repetitions"]),
        sign_flip_seed=cast(int, sign_flip["seed"]),
        sign_flip_draws=cast(int, sign_flip["draws"]),
        stub=stub,
        requested_model_id=(
            BENCHMARK_V2_STUB_MODEL_ID
            if stub
            else cast(str, cast(dict[str, object], wire["model_and_prompts"])["requested_model"])
        ),
        run_id=(
            f"{formal_run_id}-stub-attempt-001"
            if stub
            else cast(BenchmarkPreCallConfig, pre_call_config).run_id
        ),
    )
    context = _build_context_from_items(
        config,
        items,
        preregistration=preregistration,
        pre_call_config=pre_call_config,
        procedural_parameters={
            "bars": primary["bars"],
            "base_seed": primary["base_seed"],
            "family_count": primary["family_count"],
            "split": primary["split"],
        },
    )
    plan_wire = _plan_wire(context.plan)
    if (
        plan_wire["collection_schedule"] != schedule["collection_schedule"]
        or [
            {
                "candidate_permutation": value["candidate_permutation"],
                "item_id": value["item_id"],
            }
            for value in cast(list[dict[str, object]], plan_wire["item_schedules"])
        ]
        != schedule["item_permutations"]
    ):
        raise BenchmarkInputError(
            "preregistration.schedule", "does not match the executable experiment plan"
        )
    return context


def build_benchmark_v2_preregistered_context(
    preregistration: BenchmarkPreregistration,
) -> BenchmarkV2Context:
    """Build the frozen mixed-corpus offline stub context."""

    return _preregistration_context(preregistration, stub=True)


def build_benchmark_v2_live_context(
    pre_call_config: BenchmarkPreCallConfig,
) -> BenchmarkV2Context:
    """Build a live context only from one fully validated pre-call config."""

    if type(pre_call_config) is not BenchmarkPreCallConfig:
        raise BenchmarkInputError("pre_call_config", "must be an exact BenchmarkPreCallConfig")
    validated = pre_call_config_from_bytes(pre_call_config.wire_json)
    validate_current_runtime(validated)
    require_live_authorization(validated)
    return _preregistration_context(
        validated.preregistration,
        stub=False,
        pre_call_config=validated,
    )


def benchmark_v2_context_from_manifest(manifest: BenchmarkManifest) -> BenchmarkV2Context:
    """Rebuild and compare every deterministic collection input from one config."""

    if type(manifest) is not BenchmarkManifest:
        raise BenchmarkInputError("manifest", "must be an exact BenchmarkManifest")
    parameters = _exact_object(
        manifest.parameters,
        "parameters",
        frozenset(
            {
                "schema",
                "analysis",
                "corpus",
                "execution",
                "experiment",
                "goal",
                "model",
                "pre_call",
                "preregistration",
                "procedural",
                "profile",
                "report",
            }
        ),
    )
    if parameters["schema"] != BENCHMARK_V2_RUN_CONFIG_VERSION:
        raise BenchmarkInputError("parameters.schema", "has the wrong version")
    procedural = _exact_object(
        parameters["procedural"],
        "parameters.procedural",
        frozenset({"family_count", "base_seed", "bars", "split"}),
    )
    if procedural["split"] != "test":
        raise BenchmarkInputError("parameters.procedural.split", "must equal test")
    report = _exact_object(
        parameters["report"],
        "parameters.report",
        frozenset({"bootstrap_seed", "bootstrap_repetitions", "sign_flip_seed", "sign_flip_draws"}),
    )
    model = _exact_object(
        parameters["model"],
        "parameters.model",
        frozenset({"allowed_returned_model_id", "requested_model_id", "returned_model_rule"}),
    )
    requested_model_id = _benchmark_model_id(model["requested_model_id"])
    if (
        model["returned_model_rule"] != "exact_equal"
        or _benchmark_model_id(model["allowed_returned_model_id"]) != requested_model_id
    ):
        raise BenchmarkInputError(
            "parameters.model", "must use the exact requested/returned model rule"
        )
    raw_preregistration = parameters["preregistration"]
    raw_pre_call = parameters["pre_call"]
    if raw_preregistration is not None:
        corpus_reference = _exact_object(
            parameters["corpus"],
            "parameters.corpus",
            frozenset({"source"}),
        )
        experiment_reference = _exact_object(
            parameters["experiment"],
            "parameters.experiment",
            frozenset({"source"}),
        )
        if corpus_reference["source"] != ("parameters.preregistration.wire.corpus.snapshot"):
            raise BenchmarkInputError(
                "parameters.corpus.source", "does not name the embedded corpus snapshot"
            )
        if experiment_reference["source"] != ("parameters.preregistration.wire.schedule"):
            raise BenchmarkInputError(
                "parameters.experiment.source", "does not name the embedded schedule"
            )
        preregistration_binding = _exact_object(
            raw_preregistration,
            "parameters.preregistration",
            frozenset({"raw_sha256", "wire"}),
        )
        preregistration = preregistration_from_dict(preregistration_binding["wire"])
        expected_sha = hashlib.sha256(preregistration.wire_json).hexdigest()
        if preregistration_binding["raw_sha256"] != expected_sha:
            raise BenchmarkInputError(
                "parameters.preregistration.raw_sha256",
                "does not bind the embedded preregistration",
            )
        if manifest.stub:
            if raw_pre_call is not None:
                raise BenchmarkInputError(
                    "parameters.pre_call", "stub manifests must not contain a pre-call config"
                )
            context = build_benchmark_v2_preregistered_context(preregistration)
        else:
            if raw_pre_call is None:
                raise BenchmarkInputError(
                    "parameters.pre_call", "live manifests require a pre-call config"
                )
            if type(raw_pre_call) is not dict:
                raise BenchmarkInputError(
                    "parameters.pre_call", "must be one exact pre-call binding object"
                )
            complete_pre_call = dict(cast(dict[str, object], raw_pre_call))
            complete_pre_call["preregistration"] = preregistration.to_dict()
            pre_call = pre_call_config_from_bytes(canonical_json_bytes(complete_pre_call))
            if pre_call.preregistration != preregistration:
                raise BenchmarkInputError(
                    "parameters.pre_call", "binds a different preregistration"
                )
            context = build_benchmark_v2_live_context(pre_call)
        if manifest != context.manifest:
            raise BenchmarkInputError(
                "manifest", "does not match the preregistered executable context"
            )
        return context
    if raw_pre_call is not None or not manifest.stub:
        raise BenchmarkInputError("parameters.pre_call", "scalar contexts are offline stub-only")
    experiment = _exact_object(
        parameters["experiment"],
        "parameters.experiment",
        frozenset(
            {
                "collection_schedule",
                "item_schedules",
                "matched_budgets",
                "max_repair_iters",
                "n_samples",
                "reliability_k",
                "schedule_seed",
                "search_k",
                "temperature",
            }
        ),
    )
    config = BenchmarkV2Config(
        family_count=_exact_int(
            procedural["family_count"],
            "parameters.procedural.family_count",
            minimum=1,
            maximum=MAX_BENCHMARK_V2_ITEMS,
        ),
        base_seed=_exact_int(
            procedural["base_seed"],
            "parameters.procedural.base_seed",
            minimum=0,
            maximum=MAX_BENCHMARK_SEED,
        ),
        bars=_exact_int(
            procedural["bars"],
            "parameters.procedural.bars",
            minimum=1,
            maximum=MAX_BENCHMARK_BARS,
        ),
        schedule_seed=_exact_int(
            experiment["schedule_seed"],
            "parameters.experiment.schedule_seed",
            minimum=0,
            maximum=MAX_BENCHMARK_SEED,
        ),
        bootstrap_seed=_exact_int(
            report["bootstrap_seed"],
            "parameters.report.bootstrap_seed",
            minimum=0,
            maximum=MAX_BENCHMARK_SEED,
        ),
        bootstrap_repetitions=_exact_int(
            report["bootstrap_repetitions"],
            "parameters.report.bootstrap_repetitions",
            minimum=1,
            maximum=100_000,
        ),
        sign_flip_seed=_exact_int(
            report["sign_flip_seed"],
            "parameters.report.sign_flip_seed",
            minimum=0,
            maximum=MAX_BENCHMARK_SEED,
        ),
        sign_flip_draws=_exact_int(
            report["sign_flip_draws"],
            "parameters.report.sign_flip_draws",
            minimum=1,
            maximum=1_000_000,
        ),
        stub=manifest.stub,
        requested_model_id=requested_model_id,
        run_id=manifest.run_id,
    )
    context = build_benchmark_v2_context(config)
    parsed_corpus = corpus_from_dict(parameters["corpus"])
    if parsed_corpus != context.plan.items or manifest != context.manifest:
        raise BenchmarkInputError(
            "manifest", "does not match the deterministic corpus, plan, or frozen parameters"
        )
    return context


def _validate_controls(
    seed: object,
    items: object,
    bars: object,
    paired: object,
) -> tuple[int, int, int, bool]:
    if type(seed) is not int or not -MAX_BENCHMARK_SEED <= seed <= MAX_BENCHMARK_SEED:
        raise BenchmarkInputError("seed", "must be an exact signed 63-bit integer")
    if type(items) is not int or not 1 <= items <= MAX_BENCHMARK_ITEMS:
        raise BenchmarkInputError(
            "items",
            f"must be an exact integer in 1..{MAX_BENCHMARK_ITEMS}",
        )
    if type(bars) is not int or not 1 <= bars <= MAX_BENCHMARK_BARS:
        raise BenchmarkInputError(
            "bars",
            f"must be an exact integer in 1..{MAX_BENCHMARK_BARS}",
        )
    if items * bars > MAX_BENCHMARK_CORPUS_BARS:
        raise BenchmarkInputError(
            "items*bars",
            f"must not exceed {MAX_BENCHMARK_CORPUS_BARS}",
        )
    if type(paired) is not bool:
        raise BenchmarkInputError("paired", "must be an exact bool")
    return seed, items, bars, paired


@dataclass(frozen=True)
class BenchReport:
    seed: int
    n_items: int
    full: ConfigMetrics
    ablation: dict[str, ConfigMetrics]
    checker_version: str
    fidelity_checker_version: str
    profile_version: str
    profile_fingerprint: str
    input_schema_version: str
    llm_model_id: str
    paired: PairedBestOfN | None = None
    paired_crit: PairedCritic | None = None


def _corpus(seed: int, items: int, bars: int) -> list[CorpusItem]:
    return [
        CorpusItem(
            generate_leadsheet(GenConfig(seed=seed * 10007 + i, bars=bars)),
            "procedural",
            "generated",
            2,
            f"gen{seed}-{i}",
        )
        for i in range(items)
    ]


def run_benchmark(
    *,
    seed: int,
    items: int,
    llm_factory: LLMFactory,
    profile: Profile = MEDIAN_HAND,
    bars: int = 2,
    paired: bool = False,
    llm_model_id: str | None = None,
) -> BenchReport:
    """Rebuild the procedural corpus and run the full agent + leave-one-out ablation.

    NOTE ON HEADLINES: the ablation deltas (repair/critic/best-of-N "earn existence")
    only appear with a STOCHASTIC LLM (ProxyLLM) on a corpus whose proposals are
    sometimes infeasible. Under ``--stub``/ConstantLLM the rule-stub fallback is
    already GREEN with no repair, so every arm ties `full` — a flat ablation there
    is expected, not evidence that a capability is worthless. best_of_n>=2 so the
    best-of-N arm is a real ablation.

    ``paired`` additionally runs the paired best-of-N ablation (best-of-1 vs
    best-of-N on one shared proposal pool), which — unlike the unpaired ``-best_of_n``
    arm — is not confounded by independent stochastic draws.
    """
    seed, items, bars, paired = _validate_controls(seed, items, bars, paired)
    if llm_model_id is not None:
        _benchmark_model_id(llm_model_id)
    profile = ensure_profile(profile)
    corpus = _corpus(seed, items, bars)
    goal = ArrangeGoal()
    observed_model_id: str | None = None

    def checked_factory() -> LLMClient:
        nonlocal observed_model_id
        llm = llm_factory()
        try:
            try:
                actual_model_id = snapshot_llm_model_id(llm)
            except LLMModelIdError as error:
                raise BenchmarkInputError("llm_model_id", str(error)) from None
            if llm_model_id is not None and actual_model_id != llm_model_id:
                raise BenchmarkInputError(
                    "llm_model_id",
                    f"expected {llm_model_id!r}, factory returned {actual_model_id!r}",
                )
            if observed_model_id is not None and actual_model_id != observed_model_id:
                raise BenchmarkInputError(
                    "llm_model_id",
                    "factory returned inconsistent model ids across benchmark arms",
                )
        except Exception:
            close_llm_client(llm)
            raise
        observed_model_id = actual_model_id
        return llm

    loo = leave_one_out(
        corpus,
        goal,
        checked_factory,
        profile,
        base=AblationConfig(best_of_n=2),
    )
    pbn = paired_best_of_n(corpus, goal, checked_factory, profile, n=2) if paired else None
    pcr = paired_critic(corpus, goal, checked_factory, profile, n=2) if paired else None
    if observed_model_id is None:  # items >= 1 means every valid run constructs an LLM
        raise RuntimeError("benchmark did not construct an LLM")
    return BenchReport(
        seed=seed,
        n_items=items,
        full=loo["full"],
        ablation=loo,
        checker_version=CHECKER_VERSION,
        fidelity_checker_version=FIDELITY_CHECKER_VERSION,
        profile_version=profile.version,
        profile_fingerprint=profile.fingerprint,
        input_schema_version=ORACLE_INPUT_SCHEMA_VERSION,
        llm_model_id=observed_model_id,
        paired=pbn,
        paired_crit=pcr,
    )


def report_to_dict(report: BenchReport) -> dict[str, Any]:
    out: dict[str, Any] = {
        "seed": report.seed,
        "n_items": report.n_items,
        "checker_version": report.checker_version,
        "fidelity_checker_version": report.fidelity_checker_version,
        "profile_version": report.profile_version,
        "profile_fingerprint": report.profile_fingerprint,
        "input_schema_version": report.input_schema_version,
        "llm_model_id": report.llm_model_id,
        "ablation": {name: asdict(m) for name, m in report.ablation.items()},
    }
    if report.paired is not None:
        p = report.paired
        out["paired_best_of_n"] = {
            "n": p.n,
            "best_of_1": asdict(p.best_of_1),
            "best_of_n": asdict(p.best_of_n),
            "green_delta": p.green_delta,
            "joint_delta": p.joint_delta,
            "items": p.items,
        }
    if report.paired_crit is not None:
        c = report.paired_crit
        out["paired_critic"] = {
            "n": c.n,
            "without_critic": asdict(c.without_critic),
            "with_critic": asdict(c.with_critic),
            "green_delta": c.green_delta,
            "joint_delta": c.joint_delta,
            "taste_without": c.taste_without,
            "taste_with": c.taste_with,
            "taste_delta": c.taste_delta,
            "items": c.items,
        }
    return out


class _DeterministicStubLLM:
    """Observed non-provider stub; every logical call takes the normal failure path."""

    def __init__(self, model_id: str) -> None:
        self._model_id = _benchmark_model_id(model_id)

    @property
    def model_id(self) -> str:
        return self._model_id

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        del system, user, max_tokens, temperature
        raise RuntimeError("deterministic benchmark stub")

    def close(self) -> None:
        return None


class _NoCallLLM(_DeterministicStubLLM):
    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        del system, user, max_tokens, temperature
        raise AssertionError("a fully restored collection cannot call a model")


def _default_v2_client_factory(context: BenchmarkV2Context) -> Callable[[], LLMClient]:
    if context.config.stub:
        return lambda: _DeterministicStubLLM(context.requested_model_id)

    def live() -> LLMClient:
        from fretsure.llm.client import ProxyLLM

        timeout_seconds = (
            PROXY_REQUEST_TIMEOUT_SECONDS
            if context.pre_call_config is None
            else context.pre_call_config.request_timeout_seconds
        )
        return ProxyLLM(
            context.requested_model_id,
            request_timeout_seconds=timeout_seconds,
        )

    return live


def _store_ledger(store: ArtifactStore) -> ObservationLedger:
    sink = store.sink
    return ObservationLedger(
        sink.intents,
        sink.results,
        sink.attempt_intents,
        sink.attempt_results,
    )


_ObservationLedgerOffsets = tuple[int, int, int, int]


def _store_ledger_offsets(store: ArtifactStore) -> _ObservationLedgerOffsets:
    sink = store.sink
    return (
        sink.intent_count,
        sink.result_count,
        sink.attempt_intent_count,
        sink.attempt_result_count,
    )


def _store_ledger_since(
    store: ArtifactStore,
    offsets: _ObservationLedgerOffsets,
) -> ObservationLedger:
    """Detach only the complete observation suffix written after ``offsets``."""

    sink = store.sink
    intent_offset, result_offset, attempt_intent_offset, attempt_result_offset = offsets
    return ObservationLedger(
        sink.intents_since(intent_offset),
        sink.results_since(result_offset),
        sink.attempt_intents_since(attempt_intent_offset),
        sink.attempt_results_since(attempt_result_offset),
    )


def _create_v2_clients(
    context: BenchmarkV2Context,
    agent_factory: Callable[[], LLMClient] | None,
    raw_factory: Callable[[], LLMClient] | None,
) -> tuple[LLMClient, LLMClient]:
    default_factory = _default_v2_client_factory(context)
    make_agent = default_factory if agent_factory is None else agent_factory
    make_raw = default_factory if raw_factory is None else raw_factory
    if not callable(make_agent) or not callable(make_raw):
        raise BenchmarkInputError("llm_factory", "factories must be callable")
    agent = make_agent()
    try:
        raw = make_raw()
    except BaseException:
        close_llm_client(agent)
        raise
    if agent is raw:
        close_llm_client(agent)
        raise BenchmarkInputError("llm_factory", "factories must return distinct clients")
    try:
        agent_model_id = snapshot_llm_model_id(agent)
        raw_model_id = snapshot_llm_model_id(raw)
        if (
            agent_model_id != context.requested_model_id
            or raw_model_id != context.requested_model_id
        ):
            raise BenchmarkInputError(
                "llm_model_id",
                "both factories must return the model frozen in the manifest",
            )
    except BaseException as error:
        try:
            close_llm_client(raw)
        finally:
            close_llm_client(agent)
        if isinstance(error, LLMModelIdError):
            raise BenchmarkInputError("llm_model_id", str(error)) from None
        raise
    return agent, raw


def _staged_blobs(store: ArtifactStore) -> tuple[BlobRecord, ...]:
    by_ref: dict[object, BlobRecord] = {}
    for unit in store.completed_units:
        for blob in unit.blobs:
            previous = by_ref.setdefault(blob.ref, blob)
            if previous != blob:
                raise BenchmarkInputError("staging.blobs", "one digest has conflicting content")
    return tuple(sorted(by_ref.values(), key=lambda value: value.ref.sort_key))


def _scheduled_unit_reservation(
    context: BenchmarkV2Context,
    schedule_index: int,
    *,
    request_timeout_seconds: float | None = None,
    recorded_attempt_elapsed_overhead_seconds: float = 0.0,
) -> CompleteUnitReservation:
    if type(schedule_index) is not int or not 0 <= schedule_index < len(
        context.plan.collection_schedule
    ):
        raise BenchmarkInputError("schedule_index", "does not identify one planned collection unit")
    unit = context.plan.collection_schedule[schedule_index]
    proposal_tokens = context.plan.matched_budgets[unit.item_position].proposal_tokens
    if unit.arm.value == "raw":
        logical_calls = 1
        requested_tokens = proposal_tokens
    else:
        logical_calls = 1 + context.plan.max_repair_iters + 1
        requested_tokens = (
            proposal_tokens + context.plan.max_repair_iters * REPAIR_MAX_TOKENS + CRITIC_MAX_TOKENS
        )
    attempts = logical_calls * 3
    if request_timeout_seconds is not None:
        timeout_seconds = request_timeout_seconds
    elif context.pre_call_config is not None:
        timeout_seconds = context.pre_call_config.request_timeout_seconds
    else:
        timeout_seconds = PROXY_REQUEST_TIMEOUT_SECONDS
    return CompleteUnitReservation(
        logical_calls,
        attempts,
        requested_tokens,
        requested_tokens * 3,
        requested_tokens * MAX_PROXY_TEXT_BYTES_PER_TOKEN,
        attempts * MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
        int(
            (
                attempts * (timeout_seconds + recorded_attempt_elapsed_overhead_seconds)
                + logical_calls * 1.5
            )
            * 1_000_000
        ),
    )


def _configure_next_unit_reservation(
    store: ArtifactStore,
    context: BenchmarkV2Context,
) -> None:
    if context.manifest.limits.complete_unit_reservation is None:
        return
    schedule_index = store.completed_unit_count - len(context.plan.items)
    if not 0 <= schedule_index < len(context.plan.collection_schedule):
        return
    reservation = _scheduled_unit_reservation(context, schedule_index)
    store.reserve_next_unit(reservation)


def _formal_observation_request_guard(
    config: BenchmarkPreCallConfig,
) -> Callable[[bytes, bytes, int], None]:
    """Freeze one envelope check for every request in a formal collection."""

    input_ceiling = config.formal_input_token_ceiling
    output_ceiling = min(
        config.formal_output_token_ceiling,
        MAX_PROXY_OUTPUT_TOKENS,
    )

    def guard(system_utf8: bytes, user_utf8: bytes, max_tokens: int) -> None:
        input_upper_bound = (
            len(system_utf8) + len(user_utf8) + FORMAL_PROVIDER_MESSAGE_OVERHEAD_TOKENS
        )
        if input_upper_bound > input_ceiling:
            raise FormalRequestCeilingError(
                "input_tokens",
                input_upper_bound,
                input_ceiling,
            )
        if max_tokens > output_ceiling:
            raise FormalRequestCeilingError(
                "output_tokens",
                max_tokens,
                output_ceiling,
            )

    return guard


def _store_has_clean_resume_boundary(store: ArtifactStore) -> bool:
    sink = store.sink
    if sink.has_open_intent or sink.has_open_attempt:
        return False
    owned = {
        (key.logical_call_id, key.call_index)
        for row in store.completed_rows
        for key in row.observation_keys
    }
    observed = {(intent.logical_call_id, intent.call_index) for intent in sink.intents}
    return owned == observed


_CONCURRENT_UNIT_ARTIFACT_VERSION = "benchmark-concurrent-unit-artifact@0.1.0"
_CONCURRENT_ABORT_AUDIT_VERSION = "benchmark-concurrent-abort-audit@0.1.0"


@contextmanager
def _deferred_operational_sigint() -> Iterator[Callable[[], bool]]:
    """Turn main-thread SIGINT into a flag across concurrent durable transitions."""

    requested = threading.Event()
    if threading.current_thread() is not threading.main_thread():
        yield requested.is_set
        return

    previous = signal.getsignal(signal.SIGINT)

    def defer(_signum: int, _frame: FrameType | None) -> None:
        requested.set()

    signal.signal(signal.SIGINT, defer)
    try:
        yield requested.is_set
    except BaseException:
        signal.signal(signal.SIGINT, previous)
        raise
    else:
        signal.signal(signal.SIGINT, previous)


@dataclass(frozen=True, slots=True)
class _ConcurrentUnitArtifact:
    schedule_index: int
    row: BenchmarkRow
    blobs: tuple[BlobRecord, ...]

    def __post_init__(self) -> None:
        if (
            type(self.schedule_index) is not int
            or self.schedule_index < 0
            or type(self.row) is not BenchmarkRow
            or type(self.blobs) is not tuple
            or any(type(blob) is not BlobRecord for blob in self.blobs)
        ):
            raise BenchmarkInputError("concurrent_unit_artifact", "contains invalid unit fields")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(
        path,
        os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_private_artifact(path: Path, data: bytes) -> str:
    if not isinstance(path, Path) or type(data) is not bytes:
        raise BenchmarkInputError("concurrent_unit_artifact", "requires one Path and exact bytes")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError("short concurrent unit artifact write")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            try:
                existing = path.read_bytes()
            except OSError as error:
                raise BenchmarkInputError(
                    "concurrent_unit_artifact", "existing destination is unreadable"
                ) from error
            if existing != data:
                raise BenchmarkInputError(
                    "concurrent_unit_artifact",
                    "destination already exists with different bytes",
                ) from None
            return hashlib.sha256(data).hexdigest()
        os.unlink(temporary)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return hashlib.sha256(data).hexdigest()


def _file_binding(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {
        "byte_length": len(data),
        "raw_sha256": hashlib.sha256(data).hexdigest(),
    }


def _abort_operational_with_lane_audit(
    store: ArtifactStore,
    context: BenchmarkV2Context,
    coordinator_root: Path,
    reason_code: str,
) -> BenchmarkReceipt:
    lanes_root = coordinator_root / "lanes"
    lane_bindings: list[dict[str, object]] = []
    if lanes_root.is_dir():
        for path in sorted(lanes_root.iterdir(), key=lambda value: value.name):
            if not path.is_file() or path.suffix != ".jsonl":
                continue
            try:
                schedule_index = int(path.stem)
            except ValueError:
                continue
            lane_bindings.append(
                {
                    **_file_binding(path),
                    "schedule_index": schedule_index,
                }
            )
    coordinator_bindings: dict[str, object] = {}
    for name in ("config.json", "coordinator.jsonl"):
        path = coordinator_root / name
        coordinator_bindings[name] = None if not path.is_file() else _file_binding(path)
    audit = canonical_json_bytes(
        {
            "coordinator": coordinator_bindings,
            "lanes": lane_bindings,
            "main_journal_sha256": store.journal_sha256,
            "reason_code": reason_code,
            "run_id": context.plan.run_id,
            "version": _CONCURRENT_ABORT_AUDIT_VERSION,
        }
    )
    audit_sha256 = hashlib.sha256(audit).hexdigest()
    audit_sha256 = _write_private_artifact(
        coordinator_root.parent.parent / f"concurrent-abort-audit-{audit_sha256}.json",
        audit,
    )
    return store.abort(f"concurrent_audit_{audit_sha256}")


def _concurrent_unit_artifact_bytes(
    artifact: _ConcurrentUnitArtifact,
) -> bytes:
    return canonical_json_bytes(
        {
            "blobs": [blob_record_to_dict(blob) for blob in artifact.blobs],
            "row": row_to_dict(artifact.row),
            "schedule_index": artifact.schedule_index,
            "version": _CONCURRENT_UNIT_ARTIFACT_VERSION,
        }
    )


def _verify_concurrent_unit_artifact(
    path: Path,
    *,
    schedule_index: int,
    expected_sha256: str,
) -> _ConcurrentUnitArtifact:
    try:
        data = path.read_bytes()
        parsed = parse_canonical_json_bytes(data)
    except (OSError, ValueError) as error:
        raise ConcurrentExecutionError(
            ConcurrentExecutionCode.CORRUPT_LANE,
            f"unit_artifact[{schedule_index}]",
            "cannot be read as canonical JSON",
        ) from error
    if hashlib.sha256(data).hexdigest() != expected_sha256 or type(parsed) is not dict:
        raise ConcurrentExecutionError(
            ConcurrentExecutionCode.CORRUPT_LANE,
            f"unit_artifact[{schedule_index}]",
            "does not match its durable readiness binding",
        )
    obj = cast(dict[str, object], parsed)
    if (
        frozenset(obj) != frozenset({"version", "schedule_index", "row", "blobs"})
        or obj["version"] != _CONCURRENT_UNIT_ARTIFACT_VERSION
        or obj["schedule_index"] != schedule_index
        or type(obj["blobs"]) is not list
    ):
        raise ConcurrentExecutionError(
            ConcurrentExecutionCode.CORRUPT_LANE,
            f"unit_artifact[{schedule_index}]",
            "has invalid identity fields",
        )
    try:
        row = row_from_dict(obj["row"])
        blobs = tuple(blob_record_from_dict(blob) for blob in cast(list[object], obj["blobs"]))
    except ValueError as error:
        raise ConcurrentExecutionError(
            ConcurrentExecutionCode.CORRUPT_LANE,
            f"unit_artifact[{schedule_index}]",
            "contains an invalid row bundle",
        ) from error
    if tuple(sorted((blob.ref for blob in blobs), key=lambda value: value.sort_key)) != (
        row.blob_refs
    ):
        raise ConcurrentExecutionError(
            ConcurrentExecutionCode.CORRUPT_LANE,
            f"unit_artifact[{schedule_index}]",
            "does not resolve its row blob references exactly",
        )
    return _ConcurrentUnitArtifact(schedule_index, row, blobs)


def _collection_reservation_limits(
    context: BenchmarkV2Context,
) -> CompleteUnitReservation:
    limits = context.manifest.limits
    return CompleteUnitReservation(
        limits.max_calls,
        limits.max_attempts,
        limits.max_requested_output_tokens,
        limits.max_attempt_reserved_output_tokens,
        limits.max_response_text_bytes,
        limits.max_transport_response_bytes,
        limits.max_wall_microseconds,
    )


def _formal_lane_policy(config: BenchmarkPreCallConfig) -> LaneObservationPolicy:
    envelope = cast(dict[str, object], config.to_dict()["billing_envelope"])
    wire = cast(dict[str, object], envelope["wire"])
    ceilings = cast(dict[str, int], wire["billable_token_ceiling_per_attempt"])
    return LaneObservationPolicy(
        allowed_returned_model_id=config.allowed_returned_model_id,
        require_successful_provider_evidence=True,
        billable_token_ceiling_per_attempt=(
            ceilings["input_tokens"],
            ceilings["output_tokens"],
            ceilings["cache_creation_input_tokens"],
            ceilings["cache_read_input_tokens"],
        ),
    )


def _close_v2_client_pairs(
    pairs: Sequence[tuple[LLMClient, LLMClient]],
) -> None:
    first_error: BaseException | None = None
    closed: set[int] = set()
    for agent, raw in reversed(tuple(pairs)):
        for client in (raw, agent):
            identity = id(client)
            if identity in closed:
                continue
            closed.add(identity)
            try:
                close_llm_client(client)
            except BaseException as error:
                if first_error is None:
                    first_error = error
    if first_error is not None:
        raise first_error


def _create_v2_worker_client_pairs(
    context: BenchmarkV2Context,
    worker_count: int,
    agent_factory: Callable[[], LLMClient] | None,
    raw_factory: Callable[[], LLMClient] | None,
) -> tuple[tuple[LLMClient, LLMClient], ...]:
    if type(worker_count) is not int or not 1 <= worker_count <= 8:
        raise BenchmarkInputError("worker_count", "must be an exact integer in 1..8")
    pairs: list[tuple[LLMClient, LLMClient]] = []
    try:
        for _index in range(worker_count):
            pairs.append(_create_v2_clients(context, agent_factory, raw_factory))
        identities = [id(client) for pair in pairs for client in pair]
        if len(set(identities)) != len(identities):
            raise BenchmarkInputError("llm_factory", "workers require distinct client instances")
    except BaseException:
        _close_v2_client_pairs(pairs)
        raise
    return tuple(pairs)


def _execute_concurrent_unit(
    context: BenchmarkV2Context,
    permit: UnitPermit,
    clients: tuple[LLMClient, LLMClient],
    request_guard: Callable[[bytes, bytes, int], None],
) -> _ConcurrentUnitArtifact:
    schedule_index = permit.schedule_index
    unit = context.plan.collection_schedule[schedule_index]
    if context.config.stub:
        observed_agent = ObservingLLM(
            clients[0],
            permit.sink,
            clock_ns=lambda: 0,
            request_guard=request_guard,
        )
        observed_raw = ObservingLLM(
            clients[1],
            permit.sink,
            clock_ns=lambda: 0,
            request_guard=request_guard,
        )
    else:
        observed_agent = ObservingLLM(
            clients[0],
            permit.sink,
            request_guard=request_guard,
        )
        observed_raw = ObservingLLM(
            clients[1],
            permit.sink,
            request_guard=request_guard,
        )
    completed = execute_scheduled_unit(
        context.plan,
        context.goal,
        context.profile,
        unit,
        observed_agent,
        observed_raw,
        CallSequence(context.plan.run_id),
    )
    ledger = ObservationLedger(
        permit.sink.intents,
        permit.sink.results,
        permit.sink.attempt_intents,
        permit.sink.attempt_results,
    )
    bundle = completed_unit_to_row_bundle(
        context.plan,
        context.goal,
        context.profile,
        completed,
        ledger,
    )
    return _ConcurrentUnitArtifact(
        schedule_index,
        bundle.rows[0],
        bundle.blobs,
    )


def _rebased_concurrent_row(
    row: BenchmarkRow,
    call_offset: int,
) -> BenchmarkRow:
    payload = row.payload
    if row.key.row_type is RowType.CANDIDATE:
        work = cast(dict[str, object], payload["work"])
        calls = cast(list[dict[str, object]], work["calls"])
    elif row.key.row_type is RowType.RAW:
        outcome = cast(dict[str, object], payload["outcome"])
        calls = [cast(dict[str, object], outcome["call"])]
    else:
        raise BenchmarkInputError("concurrent_unit_artifact", "cannot contain a pure-solver row")
    for call in calls:
        local_index = cast(int, call["call_index"])
        if type(local_index) is not int or call["logical_call_id"] != f"call:{local_index}":
            raise BenchmarkInputError(
                "concurrent_unit_artifact", "contains a non-local call identity"
            )
        global_index = call_offset + local_index
        call["call_index"] = global_index
        call["logical_call_id"] = f"call:{global_index}"
    return build_row(
        run_id=row.run_id,
        key=row.key,
        family_id=row.family_id,
        cluster_id=row.cluster_id,
        observation_keys=tuple(
            rebase_observation_key(key, call_offset) for key in row.observation_keys
        ),
        blob_refs=row.blob_refs,
        payload=payload,
    )


def _append_rebased_ready_unit(
    store: ArtifactStore,
    context: BenchmarkV2Context,
    ready: ReadyUnit,
    artifact: _ConcurrentUnitArtifact,
) -> None:
    schedule_index = ready.schedule_index
    if artifact.schedule_index != schedule_index:
        raise BenchmarkInputError(
            "concurrent_unit_artifact", "does not match its ready schedule index"
        )
    expected_schedule_index = store.completed_unit_count - len(context.plan.items)
    if schedule_index != expected_schedule_index:
        raise ConcurrentExecutionError(
            ConcurrentExecutionCode.OUT_OF_ORDER,
            "canonical_merge",
            "ready unit is not the next main-journal schedule index",
        )
    call_offset = store.sink.intent_count
    store.reserve_next_unit(ready.reservation)
    for event in rebase_journal_events(
        ready.events,
        call_offset=call_offset,
        run_id=context.plan.run_id,
    ):
        if type(event) is CallIntent:
            store.sink.write_intent(event)
        elif type(event) is AttemptIntent:
            store.sink.write_attempt_intent(event)
        elif type(event) is AttemptResult:
            store.sink.write_attempt_result(event)
        elif type(event) is CallResult:
            store.sink.write_result(event)
        else:  # pragma: no cover - exhaustive internal union
            raise AssertionError("unsupported observation event")
    row = _rebased_concurrent_row(artifact.row, call_offset)
    new_calls = {
        (intent.logical_call_id, intent.call_index)
        for intent in store.sink.intents_since(call_offset)
    }
    if {(key.logical_call_id, key.call_index) for key in row.observation_keys} != new_calls:
        raise BenchmarkInputError(
            "concurrent_unit_artifact",
            "rebased row does not own exactly the appended unit calls",
        )
    store.commit_unit(store.completed_unit_count, row, artifact.blobs)


def _collect_missing_pure_controls(
    store: ArtifactStore,
    context: BenchmarkV2Context,
    restored: tuple[CompletedPureSolver | None, ...],
    stop_requested: Callable[[], bool],
) -> tuple[CompletedPureSolver, ...]:
    if len(restored) != len(context.plan.items):
        raise BenchmarkInputError(
            "resume_state.pure_solver_outcomes", "does not cover the planned items"
        )
    completed = list(restored)
    for index, item in enumerate(context.plan.items):
        if stop_requested():
            raise KeyboardInterrupt
        if completed[index] is not None:
            continue
        item_goal = replace(
            context.goal,
            tempo_bpm=item.ir.meta.tempo_bpm,
            extras=dict(context.goal.extras),
        )
        outcome = run_pure_solver_baseline(item.ir, item_goal, context.profile)
        exact = CompletedPureSolver(
            item.item_id,
            arrangement_source_context_sha256(item.ir),
            outcome,
        )
        bundle = pure_outcome_to_row_bundle(
            context.plan,
            context.goal,
            context.profile,
            item,
            outcome,
        )
        store.commit_unit(store.completed_unit_count, bundle.rows[0], bundle.blobs)
        completed[index] = exact
        if stop_requested():
            raise KeyboardInterrupt
    if any(value is None for value in completed):  # pragma: no cover - invariant
        raise AssertionError("every pure control must be complete")
    return cast(tuple[CompletedPureSolver, ...], tuple(completed))


def _run_operational_schedule(
    store: ArtifactStore,
    context: BenchmarkV2Context,
    coordinator: ConcurrentUnitCoordinator,
    unit_artifact_dir: Path,
    client_pairs: tuple[tuple[LLMClient, LLMClient], ...],
    request_guard: Callable[[bytes, bytes, int], None],
    stop_requested: Callable[[], bool],
    progress: ProgressReporter,
    *,
    committed_count: int,
) -> tuple[CompletedExperimentUnit, ...]:
    schedule = context.plan.collection_schedule
    total_units = len(schedule)
    admitted_count = len(coordinator.admitted_indices)
    ready_indices = coordinator.ready_indices
    active_indices = coordinator.in_flight_indices
    if (
        committed_count > len(coordinator.ready_prefix())
        or set(ready_indices) | set(active_indices) != set(range(admitted_count))
        or set(ready_indices) & set(active_indices)
    ):
        raise ConcurrentExecutionError(
            ConcurrentExecutionCode.FAIL_CLOSED,
            "resume_boundary",
            "coordinator is not aligned to the completed durable unit prefix",
        )
    ready_by_index = {unit.schedule_index: unit for unit in coordinator.ready_units}
    if (
        sum(ready_by_index[index].local_call_count for index in range(committed_count))
        != store.sink.intent_count
    ):
        raise ConcurrentExecutionError(
            ConcurrentExecutionCode.CORRUPT_COORDINATOR,
            "resume_boundary",
            "coordinator call prefix differs from the main journal",
        )
    recovered_artifacts: dict[int, _ConcurrentUnitArtifact] = {}
    for unit in coordinator.ready_units:
        if unit.unit_artifact_sha256 is None:
            raise ConcurrentExecutionError(
                ConcurrentExecutionCode.CORRUPT_COORDINATOR,
                f"unit_artifact[{unit.schedule_index}]",
                "ready unit lacks its durable artifact binding",
            )
        artifact = _verify_concurrent_unit_artifact(
            unit_artifact_dir / f"{unit.schedule_index:08d}.json",
            schedule_index=unit.schedule_index,
            expected_sha256=unit.unit_artifact_sha256,
        )
        if unit.schedule_index >= committed_count:
            recovered_artifacts[unit.schedule_index] = artifact

    completed_by_index = recovered_artifacts
    merged_count = committed_count

    def merge_ready(schedule_index: int) -> None:
        ready = ready_by_index[schedule_index]
        artifact = completed_by_index[schedule_index]
        _append_rebased_ready_unit(
            store,
            context,
            ready,
            artifact,
        )
        del completed_by_index[schedule_index]
        progress.tick(
            completed_units=schedule_index + 1,
            completed_calls=store.sink.intent_count,
        )

    while merged_count in completed_by_index and merged_count in ready_by_index:
        merge_ready(merged_count)
        merged_count += 1
    if stop_requested():
        raise KeyboardInterrupt
    interrupted: bool = False
    if merged_count == total_units:
        restored = resume_state_from_rows(
            context.plan,
            context.goal,
            context.profile,
            store.completed_rows,
            _staged_blobs(store),
        )
        return restored.completed_units

    worker_count = len(client_pairs)
    if worker_count < 1:
        raise ConcurrentExecutionError(
            ConcurrentExecutionCode.NOT_READY,
            "workers",
            "unfinished collection requires initialized worker clients",
        )

    pair_queue: queue.SimpleQueue[tuple[LLMClient, LLMClient]] = queue.SimpleQueue()
    for pair in client_pairs:
        pair_queue.put(pair)
    worker_state = threading.local()

    def initialize_worker() -> None:
        worker_state.clients = pair_queue.get()

    def execute(permit: UnitPermit) -> _ConcurrentUnitArtifact:
        clients = cast(tuple[LLMClient, LLMClient], worker_state.clients)
        return _execute_concurrent_unit(
            context,
            permit,
            clients,
            request_guard,
        )

    pending: dict[Future[_ConcurrentUnitArtifact], int] = {}
    worker_errors: dict[int, BaseException] = {}
    main_error: BaseException | None = None
    next_admission = admitted_count
    stop_admission = interrupted
    maximum_admitted_window = worker_count * 2

    with ThreadPoolExecutor(
        max_workers=worker_count,
        thread_name_prefix="fretsure-benchmark",
        initializer=initialize_worker,
    ) as executor:
        for schedule_index in active_indices:
            pending[
                executor.submit(
                    execute,
                    coordinator.resume_permit(schedule_index),
                )
            ] = schedule_index
        while merged_count < total_units:
            try:
                while merged_count in completed_by_index and merged_count in ready_by_index:
                    merge_ready(merged_count)
                    merged_count += 1

                if stop_requested():
                    interrupted = True
                    stop_admission = True
                if merged_count == total_units:
                    break

                while (
                    not stop_admission
                    and not stop_requested()
                    and next_admission < total_units
                    and len(pending) < worker_count
                    and next_admission - merged_count < maximum_admitted_window
                ):
                    permit = coordinator.admit_next()
                    if permit.schedule_index != next_admission:
                        raise ConcurrentExecutionError(
                            ConcurrentExecutionCode.OUT_OF_ORDER,
                            "admission",
                            "coordinator did not admit the next schedule index",
                        )
                    pending[executor.submit(execute, permit)] = next_admission
                    next_admission += 1

                if stop_requested():
                    interrupted = True
                    stop_admission = True
                if not pending:
                    if interrupted or worker_errors or main_error is not None:
                        break
                    raise ConcurrentExecutionError(
                        ConcurrentExecutionCode.NOT_READY,
                        "schedule",
                        "collection cannot advance to the next ready unit",
                    )

                done, _not_done = wait(
                    tuple(pending),
                    timeout=_OPERATIONAL_PROGRESS_POLL_SECONDS,
                    return_when=FIRST_COMPLETED,
                )
                for future in sorted(done, key=lambda value: pending[value]):
                    schedule_index = pending.pop(future)
                    try:
                        artifact = future.result()
                    except BaseException as error:
                        worker_errors.setdefault(schedule_index, error)
                        stop_admission = True
                        continue
                    if artifact.schedule_index != schedule_index:
                        main_error = ConcurrentExecutionError(
                            ConcurrentExecutionCode.OUT_OF_ORDER,
                            "worker_result",
                            "does not match its admitted schedule index",
                        )
                        stop_admission = True
                        continue
                    try:
                        encoded = _concurrent_unit_artifact_bytes(artifact)
                        artifact_sha256 = _write_private_artifact(
                            unit_artifact_dir / f"{schedule_index:08d}.json",
                            encoded,
                        )
                        ready_unit = coordinator.mark_ready(
                            schedule_index,
                            unit_artifact_sha256=artifact_sha256,
                        )
                        completed_by_index[schedule_index] = artifact
                        ready_by_index[schedule_index] = ready_unit
                    except BaseException as error:
                        if main_error is None:
                            main_error = error
                        stop_admission = True

                progress.tick(
                    completed_units=merged_count,
                    completed_calls=store.sink.intent_count,
                )

                first_failed = min(worker_errors, default=total_units)
                while (
                    main_error is None
                    and merged_count < first_failed
                    and merged_count in completed_by_index
                    and merged_count in ready_by_index
                ):
                    merge_ready(merged_count)
                    merged_count += 1
                if stop_requested():
                    interrupted = True
                    stop_admission = True
            except KeyboardInterrupt:
                interrupted = True
                stop_admission = True

            if stop_admission and not pending:
                break

    if main_error is not None:
        raise main_error
    if worker_errors:
        failed_index = min(worker_errors)
        raise ConcurrentExecutionError(
            ConcurrentExecutionCode.FAIL_CLOSED,
            f"worker[{failed_index}]",
            "scheduled unit execution failed before durable readiness",
        ) from worker_errors[failed_index]
    if interrupted:
        if coordinator.in_flight_indices or merged_count != next_admission:
            raise ConcurrentExecutionError(
                ConcurrentExecutionCode.FAIL_CLOSED,
                "interrupt",
                "admitted work did not drain to a completed durable prefix",
            )
        raise KeyboardInterrupt
    if merged_count != total_units:
        raise ConcurrentExecutionError(
            ConcurrentExecutionCode.NOT_READY,
            "schedule",
            "collection ended before every scheduled unit was merged",
        )
    restored = resume_state_from_rows(
        context.plan,
        context.goal,
        context.profile,
        store.completed_rows,
        _staged_blobs(store),
    )
    return restored.completed_units


def _collect_operational_concurrent(
    *,
    context: BenchmarkV2Context,
    output_dir: Path,
    resume: bool,
    agent_llm_factory: Callable[[], LLMClient] | None,
    raw_llm_factory: Callable[[], LLMClient] | None,
    stop_requested: Callable[[], bool],
) -> BenchmarkV2Result:
    pre_call = context.pre_call_config
    if pre_call is None or pre_call.collection_execution_contract is None:
        raise BenchmarkInputError(
            "pre_call_config", "operational concurrency requires a bound contract"
        )
    contract = CollectionExecutionContract.from_dict(pre_call.collection_execution_contract)
    worker_count = pre_call.max_in_flight_units
    if contract.max_in_flight_units != worker_count:
        raise BenchmarkInputError(
            "pre_call_config.collection_execution",
            "worker count differs from the bound execution contract",
        )
    client_pairs: tuple[tuple[LLMClient, LLMClient], ...] = ()
    if not resume:
        client_pairs = _create_v2_worker_client_pairs(
            context,
            worker_count,
            agent_llm_factory,
            raw_llm_factory,
        )
    store_factory = ArtifactStore.resume if resume else ArtifactStore.create
    coordinator_root = output_dir / "staging" / "concurrent"
    try:
        try:
            store = store_factory(output_dir, context.manifest)
        except ArtifactError:
            if (
                not resume
                or not output_dir.is_dir()
                or (output_dir / "canonical").exists()
                or (output_dir / "abort-receipt.json").exists()
            ):
                raise
            terminal_store = ArtifactStore.resume_for_abort(
                output_dir,
                context.manifest,
            )
            try:
                _abort_operational_with_lane_audit(
                    terminal_store,
                    context,
                    coordinator_root,
                    "resume_integrity_failure",
                )
            finally:
                terminal_store.close()
            raise
        try:
            if stop_requested():
                raise KeyboardInterrupt
            staged_rows = store.completed_rows
            restored = (
                None
                if not staged_rows
                else resume_state_from_rows(
                    context.plan,
                    context.goal,
                    context.profile,
                    staged_rows,
                    _staged_blobs(store),
                )
            )
            pure_state = (
                (None,) * len(context.plan.items)
                if restored is None
                else restored.pure_solver_outcomes
            )
            completed_pure = _collect_missing_pure_controls(
                store,
                context,
                pure_state,
                stop_requested,
            )
            completed_prefix = () if restored is None else restored.completed_units
            committed_count = len(completed_prefix)
            if store.completed_unit_count != len(context.plan.items) + committed_count:
                raise BenchmarkInputError(
                    "staging", "rows do not form pure controls plus one schedule prefix"
                )

            operational_timeout_seconds = pre_call.request_timeout_seconds
            operational_attempt_overhead_seconds = (
                pre_call.recorded_attempt_elapsed_overhead_seconds
            )
            reservations = tuple(
                _scheduled_unit_reservation(
                    context,
                    index,
                    request_timeout_seconds=operational_timeout_seconds,
                    recorded_attempt_elapsed_overhead_seconds=(
                        operational_attempt_overhead_seconds
                    ),
                )
                for index in range(len(context.plan.collection_schedule))
            )
            coordinator_exists = coordinator_root.exists()
            if not coordinator_exists and committed_count:
                raise ConcurrentExecutionError(
                    ConcurrentExecutionCode.FAIL_CLOSED,
                    "resume_boundary",
                    "completed rows exist without their concurrency coordinator",
                )
            coordinator_factory = (
                ConcurrentUnitCoordinator.resume
                if coordinator_exists
                else ConcurrentUnitCoordinator.create
            )
            coordinator = coordinator_factory(
                coordinator_root,
                contract,
                run_id=context.plan.run_id,
                unit_reservations=reservations,
                collection_limits=_collection_reservation_limits(context),
                lane_policy=_formal_lane_policy(pre_call),
            )
            try:
                if stop_requested():
                    raise KeyboardInterrupt
                unit_artifact_dir = coordinator_root / "unit-artifacts"
                if unit_artifact_dir.exists():
                    if not unit_artifact_dir.is_dir():
                        raise ConcurrentExecutionError(
                            ConcurrentExecutionCode.CORRUPT_COORDINATOR,
                            "unit_artifacts",
                            "must be a directory",
                        )
                elif coordinator.ready_indices or coordinator.in_flight_indices:
                    raise ConcurrentExecutionError(
                        ConcurrentExecutionCode.CORRUPT_COORDINATOR,
                        "unit_artifacts",
                        "is missing for an admitted coordinator",
                    )
                else:
                    unit_artifact_dir.mkdir(mode=0o700)
                    _fsync_directory(coordinator_root)

                needs_workers = bool(coordinator.in_flight_indices) or len(
                    coordinator.admitted_indices
                ) < len(context.plan.collection_schedule)
                if needs_workers and not client_pairs:
                    try:
                        client_pairs = _create_v2_worker_client_pairs(
                            context,
                            worker_count,
                            agent_llm_factory,
                            raw_llm_factory,
                        )
                    except Exception as error:
                        raise _OperationalResumeClientUnavailable(
                            "operational resume could not initialize worker clients"
                        ) from error

                progress = ProgressReporter(
                    sys.stderr,
                    ProgressConfig(
                        context.plan.run_id,
                        len(context.plan.collection_schedule),
                        resume_completed_units=committed_count,
                        resume_completed_calls=store.sink.intent_count,
                        completed_control_rows=len(context.plan.items),
                    ),
                )
                completed_units = _run_operational_schedule(
                    store,
                    context,
                    coordinator,
                    unit_artifact_dir,
                    client_pairs,
                    _formal_observation_request_guard(pre_call),
                    stop_requested,
                    progress,
                    committed_count=committed_count,
                )
            finally:
                coordinator.close()

            collection = assemble_experiment_collection(
                context.plan,
                context.goal,
                context.profile,
                completed_pure,
                completed_units,
                _store_ledger(store),
            )
            complete_bundle = collection_to_row_bundle(collection)
            if (
                tuple(sorted(store.completed_rows, key=lambda value: value.sort_key))
                != complete_bundle.rows
                or _staged_blobs(store) != complete_bundle.blobs
            ):
                raise BenchmarkInputError(
                    "staging", "concurrent rows/blobs differ from the complete collection"
                )
            receipt = store.finalize()
        except _OperationalResumeClientUnavailable:
            raise
        except KeyboardInterrupt:
            if not _store_has_clean_resume_boundary(store):
                _abort_operational_with_lane_audit(
                    store,
                    context,
                    coordinator_root,
                    "interrupted_with_unowned_observation",
                )
            raise
        except (
            ArtifactError,
            BenchmarkInputError,
            ConcurrentExecutionError,
            ExperimentInputError,
            LLMIntegrityError,
            ReportInputError,
        ) as error:
            _abort_operational_with_lane_audit(
                store,
                context,
                coordinator_root,
                _formal_abort_reason(error),
            )
            raise
        except Exception:
            _abort_operational_with_lane_audit(
                store,
                context,
                coordinator_root,
                "unexpected_concurrent_execution_failure",
            )
            raise
        finally:
            store.close()
    finally:
        _close_v2_client_pairs(client_pairs)
    return BenchmarkV2Result(receipt, None)


def _formal_abort_reason(error: BaseException) -> str:
    if isinstance(error, FormalRequestCeilingError):
        return "formal_billing_envelope_violation"
    if isinstance(error, LLMIntegrityError):
        return "provider_integrity_failure"
    if isinstance(error, ArtifactError):
        return {
            ArtifactCode.LIMIT_EXCEEDED: "collection_budget_exhausted",
            ArtifactCode.HASH_MISMATCH: "provider_or_artifact_mismatch",
            ArtifactCode.COVERAGE_MISMATCH: "expected_key_coverage_failure",
        }.get(error.code, "artifact_integrity_failure")
    if isinstance(error, ExperimentInputError):
        return "experiment_integrity_failure"
    if isinstance(error, ReportInputError):
        return "report_input_integrity_failure"
    if isinstance(error, ConcurrentExecutionError):
        return "concurrent_execution_integrity_failure"
    return "collection_integrity_failure"


def collect_benchmark_v2(
    *,
    config: BenchmarkV2Config | None = None,
    preregistration: BenchmarkPreregistration | None = None,
    pre_call_config: BenchmarkPreCallConfig | None = None,
    output_dir: Path,
    resume: bool = False,
    agent_llm_factory: Callable[[], LLMClient] | None = None,
    raw_llm_factory: Callable[[], LLMClient] | None = None,
    authorized_maximum_spend_microunits: int | None = None,
) -> BenchmarkV2Result:
    """Collect one stub run or one explicitly authorized raw-only live run."""

    if not isinstance(output_dir, Path):
        raise BenchmarkInputError("output_dir", "must be a Path")
    if type(resume) is not bool:
        raise BenchmarkInputError("resume", "must be an exact bool")
    if (agent_llm_factory is None) is not (raw_llm_factory is None):
        raise BenchmarkInputError(
            "llm_factory", "agent and raw factories must be supplied together"
        )
    selected = sum(value is not None for value in (config, preregistration, pre_call_config))
    if selected != 1:
        raise BenchmarkInputError(
            "collection_config",
            "requires exactly one scalar config, preregistration, or pre-call config",
        )
    if pre_call_config is not None:
        if type(pre_call_config) is not BenchmarkPreCallConfig:
            raise BenchmarkInputError("pre_call_config", "must be an exact BenchmarkPreCallConfig")
        pre_call_config = pre_call_config_from_bytes(pre_call_config.wire_json)
        require_explicit_spend_confirmation(
            pre_call_config,
            authorized_maximum_spend_microunits,
        )
    elif authorized_maximum_spend_microunits is not None:
        raise BenchmarkInputError(
            "authorized_maximum_spend_microunits",
            "stub collection must not supply a spend authorization",
        )
    if config is not None:
        context = build_benchmark_v2_context(config)
    elif preregistration is not None:
        context = build_benchmark_v2_preregistered_context(preregistration)
    else:
        assert pre_call_config is not None
        context = build_benchmark_v2_live_context(pre_call_config)
    if context.config.stub and agent_llm_factory is not None:
        raise BenchmarkInputError("llm_factory", "stub collection does not accept client factories")
    if context.config.stub:
        observation_request_guard: Callable[[bytes, bytes, int], None] | None = None
    else:
        assert context.pre_call_config is not None
        observation_request_guard = _formal_observation_request_guard(context.pre_call_config)

    operational_concurrent = (
        not context.config.stub
        and context.pre_call_config is not None
        and context.pre_call_config.collection_execution_contract is not None
    )
    if operational_concurrent:
        if agent_llm_factory is None:
            require_numeric_loopback_proxy_environment()
        with _deferred_operational_sigint() as stop_requested:
            return _collect_operational_concurrent(
                context=context,
                output_dir=output_dir,
                resume=resume,
                agent_llm_factory=agent_llm_factory,
                raw_llm_factory=raw_llm_factory,
                stop_requested=stop_requested,
            )

    prepared_clients: tuple[LLMClient, LLMClient] | None = None
    clients_transferred = False
    if not context.config.stub:
        # Configuration and both proxy clients are proven usable before a fresh
        # output node is created.  No model method is called by this preflight.
        prepared_clients = _create_v2_clients(
            context,
            agent_llm_factory,
            raw_llm_factory,
        )
    store_factory = ArtifactStore.resume if resume else ArtifactStore.create
    report_result: BenchmarkV2Report | None = None
    try:
        store = store_factory(output_dir, context.manifest)
        try:
            staged_rows = store.completed_rows
            staged_blobs = _staged_blobs(store)
            resume_state = (
                None
                if not staged_rows
                else resume_state_from_rows(
                    context.plan,
                    context.goal,
                    context.profile,
                    staged_rows,
                    staged_blobs,
                )
            )
            _configure_next_unit_reservation(store, context)
            complete = len(staged_rows) == len(context.manifest.expected_rows)
            if complete:
                if prepared_clients is not None:
                    close_llm_client(prepared_clients[1])
                    close_llm_client(prepared_clients[0])
                    prepared_clients = None
                agent_llm: LLMClient = _NoCallLLM(context.requested_model_id)
                raw_llm: LLMClient = _NoCallLLM(context.requested_model_id)
            elif prepared_clients is not None:
                agent_llm, raw_llm = prepared_clients
            else:
                agent_llm, raw_llm = _create_v2_clients(
                    context,
                    agent_llm_factory,
                    raw_llm_factory,
                )

            ledger_offsets = _store_ledger_offsets(store)

            def pure_complete(item: CorpusItem, outcome: PureSolverOutcome) -> None:
                bundle = pure_outcome_to_row_bundle(
                    context.plan,
                    context.goal,
                    context.profile,
                    item,
                    outcome,
                )
                store.commit_unit(
                    store.completed_unit_count,
                    bundle.rows[0],
                    bundle.blobs,
                )
                _configure_next_unit_reservation(store, context)

            def unit_complete(completed: CompletedExperimentUnit) -> None:
                nonlocal ledger_offsets
                bundle = completed_unit_to_row_bundle(
                    context.plan,
                    context.goal,
                    context.profile,
                    completed,
                    _store_ledger_since(store, ledger_offsets),
                )
                store.commit_unit(
                    store.completed_unit_count,
                    bundle.rows[0],
                    bundle.blobs,
                )
                ledger_offsets = _store_ledger_offsets(store)
                _configure_next_unit_reservation(store, context)

            clients_transferred = True
            collection = run_experiment(
                context.plan,
                context.goal,
                agent_llm,
                raw_llm,
                context.profile,
                observation_sink=store.sink,
                observation_clock_ns=(lambda: 0) if context.config.stub else None,
                observation_request_guard=observation_request_guard,
                resume_state=resume_state,
                on_pure_solver_complete=pure_complete,
                on_unit_complete=unit_complete,
            )
            complete_bundle = collection_to_row_bundle(collection)
            if (
                tuple(sorted(store.completed_rows, key=lambda value: value.sort_key))
                != complete_bundle.rows
                or _staged_blobs(store) != complete_bundle.blobs
            ):
                raise BenchmarkInputError(
                    "staging", "incremental rows/blobs differ from the complete collection"
                )

            def report_callback(inputs: FinalizationInputs) -> FinalizedReport:
                nonlocal report_result
                bindings = publication_bindings_from_artifacts(inputs.manifest, inputs.receipt)
                report_result = build_benchmark_report(
                    context.plan,
                    context.goal,
                    context.profile,
                    inputs.rows,
                    inputs.blobs,
                    inputs.observations,
                    publication_bindings=bindings,
                    mode=ReplayMode.FULL_RESCORE,
                    bootstrap_seed=context.config.bootstrap_seed,
                    bootstrap_repetitions=context.config.bootstrap_repetitions,
                    sign_flip_seed=context.config.sign_flip_seed,
                    sign_flip_draws=context.config.sign_flip_draws,
                )
                markdown = report_to_markdown(report_result).encode("utf-8")
                return FinalizedReport(report_result.wire_json, markdown)

            if context.config.stub:
                receipt = store.finalize(report_callback=report_callback)
            else:
                # Formal collection publishes only the five hash-bound raw inputs.
                # Reports are produced later by two independent offline replays.
                receipt = store.finalize()
        except KeyboardInterrupt:
            if not context.config.stub and not _store_has_clean_resume_boundary(store):
                store.abort("interrupted_with_unowned_observation")
            raise
        except (
            ArtifactError,
            BenchmarkInputError,
            ExperimentInputError,
            LLMIntegrityError,
            ReportInputError,
        ) as error:
            if not context.config.stub:
                store.abort(_formal_abort_reason(error))
            raise
        except Exception:
            if not context.config.stub and not _store_has_clean_resume_boundary(store):
                store.abort("unexpected_unowned_observation")
            raise
        finally:
            store.close()
        if context.config.stub and report_result is None:  # pragma: no cover - invariant
            raise AssertionError("stub finalization did not build a report")
    finally:
        if prepared_clients is not None and not clients_transferred:
            try:
                close_llm_client(prepared_clients[1])
            finally:
                close_llm_client(prepared_clients[0])
    return BenchmarkV2Result(receipt, report_result)


def replay_benchmark_v2(
    *,
    config_path: Path,
    receipt_path: Path,
    rows_path: Path,
    blobs_path: Path,
    observations_path: Path,
    output_dir: Path,
    mode: ReplayMode = ReplayMode.FULL_RESCORE,
) -> BenchmarkV2Result:
    """Validate five public inputs and publish a model-free deterministic replay."""

    if type(mode) is not ReplayMode:
        raise BenchmarkInputError("mode", "must be an exact ReplayMode")
    bundle: ReplayBundle = load_replay_bundle(
        config_path,
        receipt_path,
        rows_path,
        blobs_path,
        observations_path,
    )
    context = benchmark_v2_context_from_manifest(bundle.manifest)
    bindings = publication_bindings_from_artifacts(bundle.manifest, bundle.receipt)
    report = build_benchmark_report(
        context.plan,
        context.goal,
        context.profile,
        bundle.rows,
        bundle.blobs,
        bundle.observations,
        publication_bindings=bindings,
        mode=mode,
        bootstrap_seed=context.config.bootstrap_seed,
        bootstrap_repetitions=context.config.bootstrap_repetitions,
        sign_flip_seed=context.config.sign_flip_seed,
        sign_flip_draws=context.config.sign_flip_draws,
    )
    publish_replay_bundle(
        output_dir,
        bundle,
        FinalizedReport(report.wire_json, report_to_markdown(report).encode("utf-8")),
    )
    return BenchmarkV2Result(bundle.receipt, report)


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fretsure-bench")
    parser.add_argument("--output-dir", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--stub", action="store_true", help="deterministic offline collection")
    mode.add_argument("--live", action="store_true", help="collect through the configured proxy")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--prereg", type=Path)
    parser.add_argument("--pre-call-config", type=Path)
    parser.add_argument("--authorized-maximum-spend-microunits", type=int)
    parser.add_argument("--run-id")
    parser.add_argument("--model")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--items", type=int)
    parser.add_argument("--bars", type=int)
    parser.add_argument("--schedule-seed", type=int)
    parser.add_argument("--bootstrap-seed", type=int)
    parser.add_argument(
        "--bootstrap-repetitions",
        type=int,
    )
    parser.add_argument("--sign-flip-seed", type=int)
    parser.add_argument(
        "--sign-flip-draws",
        type=int,
    )
    parser.add_argument("--replay-config", type=Path)
    parser.add_argument("--replay-receipt", type=Path)
    parser.add_argument("--replay-rows", type=Path)
    parser.add_argument("--replay-blobs", type=Path)
    parser.add_argument("--replay-observations", type=Path)
    parser.add_argument(
        "--fast-reaggregate",
        action="store_true",
        help="explicitly trust stored scores instead of the default full rescore",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)
    replay_paths = (
        args.replay_config,
        args.replay_receipt,
        args.replay_rows,
        args.replay_blobs,
        args.replay_observations,
    )
    replay = any(value is not None for value in replay_paths)
    if replay and any(value is None for value in replay_paths):
        parser.error("replay requires all five --replay-* inputs")
    if replay and (
        args.stub
        or args.live
        or args.resume
        or args.prereg is not None
        or args.pre_call_config is not None
        or args.authorized_maximum_spend_microunits is not None
    ):
        parser.error("replay flags cannot be combined with collection mode flags")
    if not replay and not (args.stub or args.live):
        parser.error("collection requires exactly one of --stub or --live")
    if args.fast_reaggregate and not replay:
        parser.error("--fast-reaggregate requires replay inputs")
    scalar_values = (
        args.run_id,
        args.model,
        args.seed,
        args.items,
        args.bars,
        args.schedule_seed,
        args.bootstrap_seed,
        args.bootstrap_repetitions,
        args.sign_flip_seed,
        args.sign_flip_draws,
    )
    if args.stub and args.pre_call_config is not None:
        parser.error("--stub cannot be combined with --pre-call-config")
    if args.stub and args.authorized_maximum_spend_microunits is not None:
        parser.error("--stub cannot be combined with spend authorization")
    if args.live and args.prereg is not None:
        parser.error("--live cannot be combined with --prereg")
    if args.live and args.pre_call_config is None and not replay:
        parser.error("--live requires --pre-call-config")
    if args.live and args.authorized_maximum_spend_microunits is None and not replay:
        parser.error("--live requires --authorized-maximum-spend-microunits")
    if args.stub and args.prereg is not None and any(value is not None for value in scalar_values):
        parser.error("--prereg cannot be combined with scalar collection controls")
    if args.live and any(value is not None for value in scalar_values):
        parser.error("--pre-call-config cannot be combined with scalar collection controls")

    try:
        if replay:
            result = replay_benchmark_v2(
                config_path=cast(Path, args.replay_config),
                receipt_path=cast(Path, args.replay_receipt),
                rows_path=cast(Path, args.replay_rows),
                blobs_path=cast(Path, args.replay_blobs),
                observations_path=cast(Path, args.replay_observations),
                output_dir=args.output_dir,
                mode=(
                    ReplayMode.FAST_REAGGREGATE
                    if args.fast_reaggregate
                    else ReplayMode.FULL_RESCORE
                ),
            )
        elif args.prereg is not None:
            preregistration = preregistration_from_bytes(args.prereg.read_bytes())
            result = collect_benchmark_v2(
                preregistration=preregistration,
                output_dir=args.output_dir,
                resume=args.resume,
            )
        elif args.pre_call_config is not None:
            pre_call = pre_call_config_from_bytes(args.pre_call_config.read_bytes())
            result = collect_benchmark_v2(
                pre_call_config=pre_call,
                output_dir=args.output_dir,
                resume=args.resume,
                authorized_maximum_spend_microunits=(args.authorized_maximum_spend_microunits),
            )
        else:
            result = collect_benchmark_v2(
                config=BenchmarkV2Config(
                    family_count=1 if args.items is None else args.items,
                    base_seed=(PRIMARY_PROCEDURAL_BASE_SEED if args.seed is None else args.seed),
                    bars=1 if args.bars is None else args.bars,
                    schedule_seed=0 if args.schedule_seed is None else args.schedule_seed,
                    bootstrap_seed=(0 if args.bootstrap_seed is None else args.bootstrap_seed),
                    bootstrap_repetitions=(
                        DEFAULT_BENCHMARK_V2_BOOTSTRAP_REPETITIONS
                        if args.bootstrap_repetitions is None
                        else args.bootstrap_repetitions
                    ),
                    sign_flip_seed=(0 if args.sign_flip_seed is None else args.sign_flip_seed),
                    sign_flip_draws=(
                        DEFAULT_BENCHMARK_V2_SIGN_FLIP_DRAWS
                        if args.sign_flip_draws is None
                        else args.sign_flip_draws
                    ),
                    stub=True,
                    requested_model_id=args.model,
                    run_id=args.run_id,
                ),
                output_dir=args.output_dir,
                resume=args.resume,
            )
    except KeyboardInterrupt:
        return 130
    except (LLMIntegrityError, OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "report_sha256": (None if result.report is None else result.report.sha256),
                "run_id": result.receipt.run_id,
                "status": result.receipt.status.value,
            },
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
