"""Benchmark runners.

The historical ``run_benchmark`` Python API remains available for compatibility.
The public ``fretsure-bench`` command owns the benchmark-v2 artifact workflow:
deterministic stub or live collection, complete-unit resume, and offline replay.
"""

import argparse
import hashlib
import json
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from fretsure.agent.arranger import ArrangeGoal
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
    ArtifactLimits,
    ArtifactStore,
    BenchmarkManifest,
    BenchmarkReceipt,
    BlobRecord,
    FinalizationInputs,
    FinalizedReport,
    ReplayBundle,
    RowKey,
    RowType,
    build_manifest,
    load_replay_bundle,
    publish_replay_bundle,
)
from fretsure.bench.baselines import PureSolverOutcome
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
    ExperimentPlan,
    MatchedPrefix,
    ObservationLedger,
    item_pair_id,
    preflight_experiment,
    run_experiment,
    sample_pair_id,
)
from fretsure.bench.generator import GenConfig, generate_leadsheet
from fretsure.bench.report import (
    BenchmarkReport as BenchmarkV2Report,
)
from fretsure.bench.report import (
    ReplayMode,
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
    LLMClient,
    LLMModelIdError,
    close_llm_client,
    snapshot_llm_model_id,
    validate_llm_model_id,
)
from fretsure.metrics.fidelity import FIDELITY_CHECKER_VERSION
from fretsure.oracle.core import CHECKER_VERSION
from fretsure.oracle.input import ORACLE_INPUT_SCHEMA_VERSION, ensure_profile
from fretsure.oracle.profiles import MEDIAN_HAND, Profile

MAX_BENCHMARK_ITEMS = 1_000
MAX_BENCHMARK_BARS = 64
MAX_BENCHMARK_CORPUS_BARS = 4_096
MAX_BENCHMARK_SEED = (1 << 63) - 1

BENCHMARK_V2_RUN_CONFIG_VERSION = "benchmark-v2-run-config@0.1.0"
BENCHMARK_V2_ANALYSIS_VERSION = "benchmark-v2-analysis@0.1.0"
BENCHMARK_V2_STUB_MODEL_ID = "fretsure-benchmark-stub@0.1.0"
MAX_BENCHMARK_V2_ITEMS = 900
DEFAULT_BENCHMARK_V2_BOOTSTRAP_REPETITIONS = 10_000
DEFAULT_BENCHMARK_V2_SIGN_FLIP_DRAWS = 100_000
MAX_BENCHMARK_V2_SIGN_FLIP_SEED = MAX_BENCHMARK_SEED - 120_100


def _max_v2_bootstrap_seed(family_count: int) -> int:
    derived_offset = max(140_100, max(0, family_count - 1) * 1_000_000 + 50_100)
    return MAX_BENCHMARK_SEED - derived_offset


def _analysis_code_sha256() -> str:
    payload = canonical_json_bytes(
        {
            "checker_version": CHECKER_VERSION,
            "fidelity_checker_version": FIDELITY_CHECKER_VERSION,
            "input_schema_version": ORACLE_INPUT_SCHEMA_VERSION,
            "report_contract": BENCHMARK_V2_ANALYSIS_VERSION,
        }
    )
    return hashlib.sha256(
        f"fretsure:{BENCHMARK_V2_ANALYSIS_VERSION}\0".encode() + payload
    ).hexdigest()


BENCHMARK_V2_ANALYSIS_SHA256 = _analysis_code_sha256()


class BenchmarkInputError(ValueError):
    """Typed failure for benchmark controls outside the finite run envelope."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid benchmark {field}: {detail}")


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


@dataclass(frozen=True, slots=True)
class BenchmarkV2Result:
    receipt: BenchmarkReceipt
    report: BenchmarkV2Report


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


def _v2_limits(family_count: int) -> ArtifactLimits:
    maximum_calls = family_count * 110
    return ArtifactLimits(
        max_rows=family_count * 21,
        max_blobs=family_count * 83,
        max_calls=maximum_calls,
        max_attempts=maximum_calls * 3,
        max_json_bytes=256 * 1024 * 1024,
        max_jsonl_line_bytes=4 * 1024 * 1024,
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
) -> dict[str, object]:
    return {
        "analysis": {
            "analysis_code_sha256": BENCHMARK_V2_ANALYSIS_SHA256,
            "version": BENCHMARK_V2_ANALYSIS_VERSION,
        },
        "corpus": corpus_to_dict(plan.items),
        "experiment": _plan_wire(plan),
        "goal": _goal_wire(goal),
        "model": {"requested_model_id": requested_model_id},
        "procedural": {
            "bars": config.bars,
            "base_seed": config.base_seed,
            "family_count": config.family_count,
            "split": "test",
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


def build_benchmark_v2_context(config: BenchmarkV2Config) -> BenchmarkV2Context:
    """Build one strict procedural v2 plan and its self-contained manifest."""

    if type(config) is not BenchmarkV2Config:
        raise BenchmarkInputError("config", "must be an exact BenchmarkV2Config")
    items = build_primary_procedural_corpus(
        ProceduralCorpusConfig(
            family_count=config.family_count,
            base_seed=config.base_seed,
            bars=config.bars,
            split="test",
        )
    )
    corpus_digest = corpus_sha256(items)
    run_id = _derived_run_id(config, corpus_digest)
    plan = preflight_experiment(items, run_id=run_id, schedule_seed=config.schedule_seed)
    goal = ArrangeGoal()
    profile = MEDIAN_HAND
    requested_model_id = _benchmark_model_id(
        config.requested_model_id
        if config.requested_model_id is not None
        else BENCHMARK_V2_STUB_MODEL_ID
        if config.stub
        else DEFAULT_PROXY_MODEL
    )
    manifest = build_manifest(
        run_id=run_id,
        corpus_sha256=corpus_digest,
        analysis_code_sha256=BENCHMARK_V2_ANALYSIS_SHA256,
        stub=config.stub,
        expected_rows=_expected_v2_rows(plan),
        limits=_v2_limits(len(items)),
        parameters=_v2_parameters(config, plan, goal, profile, requested_model_id),
    )
    return BenchmarkV2Context(config, manifest, plan, goal, profile, requested_model_id)


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
                "experiment",
                "goal",
                "model",
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
    report = _exact_object(
        parameters["report"],
        "parameters.report",
        frozenset({"bootstrap_seed", "bootstrap_repetitions", "sign_flip_seed", "sign_flip_draws"}),
    )
    model = _exact_object(
        parameters["model"],
        "parameters.model",
        frozenset({"requested_model_id"}),
    )
    requested_model_id = _benchmark_model_id(model["requested_model_id"])
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

        return ProxyLLM(context.requested_model_id)

    return live


def _store_ledger(store: ArtifactStore) -> ObservationLedger:
    sink = store.sink
    return ObservationLedger(
        sink.intents,
        sink.results,
        sink.attempt_intents,
        sink.attempt_results,
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


def collect_benchmark_v2(
    *,
    config: BenchmarkV2Config,
    output_dir: Path,
    resume: bool = False,
    agent_llm_factory: Callable[[], LLMClient] | None = None,
    raw_llm_factory: Callable[[], LLMClient] | None = None,
) -> BenchmarkV2Result:
    """Collect or resume one procedural benchmark-v2 artifact directory."""

    if not isinstance(output_dir, Path):
        raise BenchmarkInputError("output_dir", "must be a Path")
    if type(resume) is not bool:
        raise BenchmarkInputError("resume", "must be an exact bool")
    if (agent_llm_factory is None) is not (raw_llm_factory is None):
        raise BenchmarkInputError(
            "llm_factory", "agent and raw factories must be supplied together"
        )
    context = build_benchmark_v2_context(config)
    store_factory = ArtifactStore.resume if resume else ArtifactStore.create
    report_result: BenchmarkV2Report | None = None
    with store_factory(output_dir, context.manifest) as store:
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
        complete = len(staged_rows) == len(context.manifest.expected_rows)
        if complete:
            agent_llm: LLMClient = _NoCallLLM(context.requested_model_id)
            raw_llm: LLMClient = _NoCallLLM(context.requested_model_id)
        else:
            agent_llm, raw_llm = _create_v2_clients(
                context,
                agent_llm_factory,
                raw_llm_factory,
            )

        def pure_complete(item: CorpusItem, outcome: PureSolverOutcome) -> None:
            bundle = pure_outcome_to_row_bundle(
                context.plan,
                context.goal,
                context.profile,
                item,
                outcome,
            )
            store.commit_unit(
                len(store.completed_units),
                bundle.rows[0],
                bundle.blobs,
            )

        def unit_complete(completed: CompletedExperimentUnit) -> None:
            bundle = completed_unit_to_row_bundle(
                context.plan,
                context.goal,
                context.profile,
                completed,
                _store_ledger(store),
            )
            store.commit_unit(
                len(store.completed_units),
                bundle.rows[0],
                bundle.blobs,
            )

        collection = run_experiment(
            context.plan,
            context.goal,
            agent_llm,
            raw_llm,
            context.profile,
            observation_sink=store.sink,
            observation_clock_ns=(lambda: 0) if context.config.stub else None,
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

        receipt = store.finalize(report_callback=report_callback)
    if report_result is None:  # pragma: no cover - callback invariant
        raise AssertionError("finalization did not build a report")
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
    parser.add_argument("--run-id")
    parser.add_argument("--model")
    parser.add_argument("--seed", type=int, default=PRIMARY_PROCEDURAL_BASE_SEED)
    parser.add_argument("--items", type=int, default=1)
    parser.add_argument("--bars", type=int, default=1)
    parser.add_argument("--schedule-seed", type=int, default=0)
    parser.add_argument("--bootstrap-seed", type=int, default=0)
    parser.add_argument(
        "--bootstrap-repetitions",
        type=int,
        default=DEFAULT_BENCHMARK_V2_BOOTSTRAP_REPETITIONS,
    )
    parser.add_argument("--sign-flip-seed", type=int, default=0)
    parser.add_argument(
        "--sign-flip-draws",
        type=int,
        default=DEFAULT_BENCHMARK_V2_SIGN_FLIP_DRAWS,
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
    if replay and (args.stub or args.live or args.resume):
        parser.error("replay flags cannot be combined with collection mode flags")
    if not replay and not (args.stub or args.live):
        parser.error("collection requires exactly one of --stub or --live")
    if args.fast_reaggregate and not replay:
        parser.error("--fast-reaggregate requires replay inputs")

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
        else:
            result = collect_benchmark_v2(
                config=BenchmarkV2Config(
                    family_count=args.items,
                    base_seed=args.seed,
                    bars=args.bars,
                    schedule_seed=args.schedule_seed,
                    bootstrap_seed=args.bootstrap_seed,
                    bootstrap_repetitions=args.bootstrap_repetitions,
                    sign_flip_seed=args.sign_flip_seed,
                    sign_flip_draws=args.sign_flip_draws,
                    stub=args.stub,
                    requested_model_id=args.model,
                    run_id=args.run_id,
                ),
                output_dir=args.output_dir,
                resume=args.resume,
            )
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "report_sha256": result.report.sha256,
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
