"""Frozen machine preregistration for benchmark-v2.

This module is deliberately pure: it does not inspect Git, import paths, the
network, or ambient process state.  A caller supplies the already normalized
503-item corpus and receives the one canonical preregistration plus its
human-readable budget view.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Final, NoReturn, cast

import numpy as np
from scipy.stats import binom

import fretsure.agent.arranger as arranger_module
import fretsure.agent.critic as critic_module
import fretsure.agent.repair as repair_module
import fretsure.bench.baselines as baselines_module
from fretsure.agent.arranger import proposal_output_token_budget
from fretsure.agent.critic import CRITIC_MAX_TOKENS
from fretsure.agent.repair import REPAIR_MAX_TOKENS
from fretsure.agent.trace import TRACE_SCHEMA_VERSION
from fretsure.bench.artifacts import parse_canonical_json_bytes
from fretsure.bench.contracts import (
    BENCHMARK_CORPUS_VERSION,
    BENCHMARK_MANIFEST_VERSION,
    BENCHMARK_NOTEGRAPH_VERSION,
    BENCHMARK_OBSERVATIONS_VERSION,
    BENCHMARK_RECEIPT_VERSION,
    BENCHMARK_REPORT_VERSION,
    BENCHMARK_ROW_VERSION,
    canonical_json_bytes,
)
from fretsure.bench.corpus import (
    PRIMARY_PROCEDURAL_BASE_SEED,
    CorpusItem,
    corpus_from_dict,
    corpus_sha256,
    corpus_to_dict,
    datasheet,
    snapshot_corpus,
)
from fretsure.bench.experiment import (
    EXPERIMENT_MAX_REPAIR_ITERS,
    EXPERIMENT_N_SAMPLES,
    EXPERIMENT_TEMPERATURE,
    RELIABILITY_K_VALUES,
    SEARCH_K_VALUES,
    match_budget_prefix,
)
from fretsure.bench.generator import GENERATOR_VERSION
from fretsure.bench.public_adapters import (
    BENCHMARK_PUBLIC_ADAPTER_VERSION,
    BENCHMARK_PUBLIC_ROUTER_VERSION,
)
from fretsure.bench.stats import (
    PowerEstimate,
    PowerGateStatus,
    PowerMethod,
    evaluate_power_gate,
    wilson_interval,
)
from fretsure.importers.score import SCORE_INPUT_VERSION
from fretsure.llm.client import (
    DEFAULT_PROXY_MODEL,
    MAX_PROXY_RESPONSE_BYTES,
    MAX_PROXY_TEXT_BYTES_PER_TOKEN,
    MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
    PROXY_CONNECT_TIMEOUT_SECONDS,
    PROXY_REQUEST_TIMEOUT_SECONDS,
)
from fretsure.metrics.fidelity import FIDELITY_CHECKER_VERSION
from fretsure.oracle.core import CHECKER_VERSION
from fretsure.oracle.input import MAX_SOLVER_WORK_UNITS, ORACLE_INPUT_SCHEMA_VERSION
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.score import (
    MAX_SCORE_SOLVER_AGGREGATE_WORK_UNITS,
    MAX_SCORE_SOLVER_SEGMENTS,
    SCORE_SOLVER_VERSION,
)

BENCHMARK_PREREGISTRATION_VERSION: Final = "benchmark-preregistration@0.1.0"
BENCHMARK_PROMPT_CONTRACT_VERSION: Final = "benchmark-prompt-contract@0.1.0"
BENCHMARK_SCHEDULE_VERSION: Final = "benchmark-experiment-schedule@0.1.0"
BENCHMARK_POWER_VERSION: Final = "benchmark-power@0.1.0"
PUBLIC_COMPACT_PROPOSAL_VERSION: Final = arranger_module.PROPOSAL_COMPACT_PROTOCOL_VERSION

FORMAL_RUN_ID: Final = "benchmark-v2-formal-20260717"
PLAN_GIT_SHA: Final = "44927517958ecd3b9868bafb7bfe6133be25cc8e"
TARGET_PACKAGE_VERSION: Final = "0.6.0"
SCHEDULE_SEED: Final = 2_026_071_700
BOOTSTRAP_SEED: Final = 2_026_071_701
BOOTSTRAP_REPETITIONS: Final = 10_000
SIGN_FLIP_SEED: Final = 2_026_071_702
SIGN_FLIP_DRAWS: Final = 100_000
POWER_SEED: Final = 2_026_071_703
POWER_REPETITIONS: Final = 100_000

PRIMARY_FAMILY_COUNT: Final = 500
FULL_CORPUS_COUNT: Final = 503
TASK5_CORPUS_SHA256: Final = (
    "b4e2a1ed05eb07d82bdea18b9105cdd92b564cf864d8acedaa3c37d820848e8b"
)
TASK5_SOURCE_CENSUS_SHA256: Final = (
    "aa10f8d60b35d1c687806c0426bf50a2d30488d84b1f23317f72fc7dcceee372"
)
TASK5_CORPUS_FILE_SHA256: Final = (
    "be32ceaf3abd0ad027667eb2dc78f08511f4f63bd78ac0e40f9d718dfead1f4c"
)
TASK5_DATASHEET_FILE_SHA256: Final = (
    "88a3863c6c382b3348adbfc08bf23a9a8678e2be5a1a4584d021a4cd36990be8"
)
TASK5_SOURCE_CENSUS_FILE_SHA256: Final = (
    "2c29a3ce7d4d528fecb854e585de44096531bff0c83cd8e7f7ca546fe6efd263"
)
TASK5_CONTAMINATION_FILE_SHA256: Final = (
    "64bcda562f72a0c7867b49521c2430e6be4ea15ab67fef39baba99ba913c75f5"
)

PUBLIC_PROPOSAL_TOKENS: Final[dict[str, int]] = {
    "public-classical-beethoven-op48-5": 6_464,
    "public-midi-bwv775": 14_304,
    "public-midi-bwv774": 15_968,
}
PUBLIC_EVENT_COUNTS: Final[dict[str, int]] = {
    "public-classical-beethoven-op48-5": 198,
    "public-midi-bwv775": 443,
    "public-midi-bwv774": 495,
}

_SCHEDULE_DOMAIN = f"fretsure:{BENCHMARK_SCHEDULE_VERSION}\0".encode("ascii")
_SCHEDULE_DIGEST_DOMAIN = b"fretsure:benchmark-preregistered-schedule@0.1.0\0"
_PROMPT_DIGEST_DOMAIN = f"fretsure:{BENCHMARK_PROMPT_CONTRACT_VERSION}\0".encode("ascii")
_POWER_SIMULATION_DOMAIN = b"fretsure:benchmark-repair-power-simulation@0.1.0\0"
_SEARCH_POWER_DOMAIN = b"fretsure:benchmark-search-power@0.1.0\0"


class PreregistrationError(ValueError):
    """The preregistration or its source corpus differs from the frozen contract."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid benchmark preregistration {field}: {detail}")


def _fail(field: str, detail: str) -> NoReturn:
    raise PreregistrationError(field, detail)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _domain_sha256(domain: bytes, value: object) -> str:
    return hashlib.sha256(domain + canonical_json_bytes(value)).hexdigest()


def _hash_fields(seed: int, *fields: object) -> bytes:
    digest = hashlib.sha256()
    digest.update(_SCHEDULE_DOMAIN)
    digest.update(seed.to_bytes(8, "big"))
    for field in fields:
        encoded = str(field).encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.digest()


def _candidate_permutation(item_id: str) -> tuple[int, ...]:
    return tuple(
        sorted(
            range(EXPERIMENT_N_SAMPLES),
            key=lambda index: _hash_fields(SCHEDULE_SEED, "candidate", item_id, index),
        )
    )


def _schedule_wire(items: tuple[CorpusItem, ...]) -> dict[str, object]:
    permutations = tuple(
        {
            "candidate_permutation": list(_candidate_permutation(item.item_id)),
            "item_id": item.item_id,
        }
        for item in items
    )
    units: list[dict[str, object]] = []
    for round_index in range(EXPERIMENT_N_SAMPLES):
        round_units: list[dict[str, object]] = [
            {
                "arm": arm,
                "candidate_index": _candidate_permutation(item.item_id)[round_index],
                "item_id": item.item_id,
                "item_position": item_position,
                "round_index": round_index,
            }
            for item_position, item in enumerate(items)
            for arm in ("agent", "raw")
        ]
        round_units.sort(
            key=lambda unit: _hash_fields(
                SCHEDULE_SEED,
                "interleave",
                unit["round_index"],
                unit["item_id"],
                unit["arm"],
                unit["candidate_index"],
            )
        )
        units.extend(round_units)
    schedule_body: dict[str, object] = {
        "collection_schedule": units,
        "item_permutations": list(permutations),
    }
    return {
        "algorithm": BENCHMARK_SCHEDULE_VERSION,
        "collection_schedule": units,
        "collection_unit_count": len(units),
        "digest_sha256": _domain_sha256(_SCHEDULE_DIGEST_DOMAIN, schedule_body),
        "item_permutations": list(permutations),
        "schedule_seed": SCHEDULE_SEED,
    }


def _proposal_tokens(item: CorpusItem) -> tuple[int, str, int]:
    events = len(item.ir.notes) + len(item.ir.chords)
    tokens = proposal_output_token_budget(item.ir)
    strategy = arranger_module.arrangement_output_protocol(item.ir).value
    if item.layer == "procedural":
        return tokens, strategy, events
    expected_events = PUBLIC_EVENT_COUNTS.get(item.item_id)
    expected_tokens = PUBLIC_PROPOSAL_TOKENS.get(item.item_id)
    if expected_events is None or expected_tokens is None or events != expected_events:
        _fail("corpus.public", "public item identity or event count is not frozen")
    calculated = 128 + 32 * events
    if calculated != expected_tokens or calculated != tokens or calculated > 16_384:
        _fail("corpus.public", "compact public proposal token rule is inconsistent")
    if strategy != PUBLIC_COMPACT_PROPOSAL_VERSION:
        _fail("corpus.public", "public item did not select the compact protocol")
    return calculated, strategy, events


def _per_item_budget(items: tuple[CorpusItem, ...]) -> tuple[list[dict[str, object]], int]:
    result: list[dict[str, object]] = []
    proposal_sum = 0
    for item in items:
        proposal_tokens, strategy, events = _proposal_tokens(item)
        proposal_sum += proposal_tokens
        target_tokens = proposal_tokens + EXPERIMENT_MAX_REPAIR_ITERS * REPAIR_MAX_TOKENS
        matched = match_budget_prefix(
            1 + EXPERIMENT_MAX_REPAIR_ITERS,
            target_tokens,
            unit_calls=1,
            unit_tokens=proposal_tokens,
        )
        complete_tokens = (
            2 * proposal_tokens
            + EXPERIMENT_MAX_REPAIR_ITERS * REPAIR_MAX_TOKENS
            + CRITIC_MAX_TOKENS
        )
        complete_response_bytes = (
            2 * proposal_tokens * MAX_PROXY_TEXT_BYTES_PER_TOKEN
            + EXPERIMENT_MAX_REPAIR_ITERS
            * REPAIR_MAX_TOKENS
            * MAX_PROXY_TEXT_BYTES_PER_TOKEN
            + CRITIC_MAX_TOKENS * MAX_PROXY_TEXT_BYTES_PER_TOKEN
        )
        agent_tokens = (
            proposal_tokens
            + EXPERIMENT_MAX_REPAIR_ITERS * REPAIR_MAX_TOKENS
            + CRITIC_MAX_TOKENS
        )
        agent_response_bytes = agent_tokens * MAX_PROXY_TEXT_BYTES_PER_TOKEN
        agent_envelope = {
            "attempts": 30,
            "logical_calls": 10,
            "requested_output_tokens": agent_tokens,
            "response_text_bytes": agent_response_bytes,
            "transport_response_bytes": 30 * MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
        }
        raw_envelope = {
            "attempts": 3,
            "logical_calls": 1,
            "requested_output_tokens": proposal_tokens,
            "response_text_bytes": proposal_tokens * MAX_PROXY_TEXT_BYTES_PER_TOKEN,
            "transport_response_bytes": 3 * MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
        }
        result.append(
            {
                "paired_sample_maximum_envelope": {
                    "attempts": 33,
                    "logical_calls": 11,
                    "requested_output_tokens": complete_tokens,
                    "response_text_bytes": complete_response_bytes,
                    "transport_response_bytes": 33 * MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
                },
                "event_count": events,
                "item_id": item.item_id,
                "matched_control": {
                    "limiting_dimension": matched.limiting_dimension.value,
                    "prefix_samples": matched.prefix_samples,
                    "remaining_calls": matched.remaining_calls,
                    "remaining_tokens": matched.remaining_tokens,
                    "spent_calls": matched.spent_calls,
                    "spent_tokens": matched.spent_tokens,
                    "status": matched.status.value,
                    "target_calls": matched.target_calls,
                    "target_tokens": matched.target_tokens,
                },
                "proposal_raw_max_tokens": proposal_tokens,
                "proposal_strategy": strategy,
                "scheduled_unit_envelopes": {
                    "agent": agent_envelope,
                    "raw": raw_envelope,
                },
            }
        )
    return result, proposal_sum


def _budget_wire(items: tuple[CorpusItem, ...]) -> dict[str, object]:
    per_item, proposal_sum = _per_item_budget(items)
    item_count = len(items)
    primary_items = tuple(item for item in items if item.layer == "procedural")
    primary_per_item, primary_proposal_sum = _per_item_budget(primary_items)

    def totals(count: int, proposal_total: int) -> dict[str, object]:
        calls = {
            "critic": count * 10,
            "proposal": count * 10,
            "raw": count * 10,
            "repair": count * 10 * EXPERIMENT_MAX_REPAIR_ITERS,
        }
        tokens = {
            "critic": calls["critic"] * CRITIC_MAX_TOKENS,
            "proposal": 10 * proposal_total,
            "raw": 10 * proposal_total,
            "repair": calls["repair"] * REPAIR_MAX_TOKENS,
        }
        total_calls = sum(calls.values())
        total_tokens = sum(tokens.values())
        response_text = sum(value * MAX_PROXY_TEXT_BYTES_PER_TOKEN for value in tokens.values())
        attempts = total_calls * 3
        retry_backoff_milliseconds = total_calls * 1_500
        provider_timeout_milliseconds = int(
            attempts * PROXY_REQUEST_TIMEOUT_SECONDS * 1_000
            + retry_backoff_milliseconds
        )
        return {
            "attempt_reserved_output_tokens": total_tokens * 3,
            "logical_calls_by_stage": calls,
            "logical_calls_total": total_calls,
            "maximum_attempts": attempts,
            "provider_timeout_envelope_milliseconds": provider_timeout_milliseconds,
            "requested_output_tokens_by_stage": tokens,
            "requested_output_tokens_total": total_tokens,
            "response_text_bytes": response_text,
            "transport_response_bytes": attempts * MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
        }

    primary_totals = totals(len(primary_items), primary_proposal_sum)
    full_totals = totals(item_count, proposal_sum)
    prefix_counts: dict[str, int] = {}
    for value in per_item:
        matched = cast(dict[str, object], value["matched_control"])
        prefix = str(matched["prefix_samples"])
        prefix_counts[prefix] = prefix_counts.get(prefix, 0) + 1
    maximum_reservation = max(
        (
            cast(
                dict[str, object],
                cast(dict[str, object], value["scheduled_unit_envelopes"])["agent"],
            )
            for value in per_item
        ),
        key=lambda value: cast(int, value["requested_output_tokens"]),
    )
    return {
        "ceiling_scope": "single_collection_attempt_nontransferable",
        "cost_contract": {
            "maximum_spend": None,
            "reason": "COST_CONTRACT_UNAVAILABLE_BEFORE_EXPLICIT_EXTERNAL_BUDGET_GATE",
            "status": "cost_contract_unavailable",
        },
        "full_corpus": full_totals,
        "recorded_provider_call_elapsed_ceiling_seconds": 5_184_000,
        "matched_control_prefix_counts": dict(
            sorted(prefix_counts.items(), key=lambda x: int(x[0]))
        ),
        "per_item": per_item,
        "primary_procedural": primary_totals,
        "provider_policy": {
            "connect_timeout_seconds": PROXY_CONNECT_TIMEOUT_SECONDS,
            "maximum_attempts_per_logical_call": 3,
            "maximum_response_bytes": MAX_PROXY_RESPONSE_BYTES,
            "maximum_transport_response_bytes": MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
            "request_timeout_seconds": PROXY_REQUEST_TIMEOUT_SECONDS,
            "retry_backoff_seconds": [0.5, 1.0],
        },
        "reserve_before_next_scheduled_unit": maximum_reservation,
        "storage": {
            "max_blobs": item_count * 83,
            "max_rows": item_count * 21,
        },
    }


def _search_power_exact(
    *, family_count: int, discordance: float, delta: float, alpha: float
) -> float:
    if not 0.0 < delta <= discordance <= 1.0:
        _fail("power.search", "discordance and delta are inconsistent")
    improved_given_discordance = (discordance + delta) / (2.0 * discordance)
    power = 0.0
    for discordant in range(family_count + 1):
        critical: int | None = None
        for improved in range(discordant + 1):
            if float(binom.sf(improved - 1, discordant, 0.5)) < alpha:
                critical = improved
                break
        conditional = (
            0.0
            if critical is None
            else float(
                binom.sf(critical - 1, discordant, improved_given_discordance)
            )
        )
        power += float(binom.pmf(discordant, family_count, discordance)) * conditional
    return power


def _repair_power_simulation(*, icc: float) -> dict[str, object]:
    if not 0.0 <= icc <= 1.0:
        _fail("power.repair.icc", "must be in 0..1")
    rng = np.random.Generator(np.random.PCG64(POWER_SEED))
    batch_size = 256
    rejected = 0
    digest = hashlib.sha256()
    digest.update(_POWER_SIMULATION_DOMAIN)
    digest.update(canonical_json_bytes({
        "alpha": 0.025,
        "batch_size": batch_size,
        "candidates_per_family": EXPERIMENT_N_SAMPLES,
        "family_count": PRIMARY_FAMILY_COUNT,
        "icc": icc,
        "p_positive": 0.55,
        "repetitions": POWER_REPETITIONS,
        "seed": POWER_SEED,
    }))
    log_alpha = math.log(0.025)
    completed = 0
    while completed < POWER_REPETITIONS:
        current = min(batch_size, POWER_REPETITIONS - completed)
        common = rng.random((current, PRIMARY_FAMILY_COUNT)) < icc
        shared = np.where(
            rng.random((current, PRIMARY_FAMILY_COUNT)) < 0.55,
            10,
            -10,
        ).astype(np.int16)
        independent_positive = np.count_nonzero(
            rng.random((current, PRIMARY_FAMILY_COUNT, EXPERIMENT_N_SAMPLES)) < 0.55,
            axis=2,
        )
        independent = (2 * independent_positive - EXPERIMENT_N_SAMPLES).astype(np.int16)
        family_sums = np.where(common, shared, independent).astype("<i2", copy=False)
        observed = family_sums.astype(np.int64).sum(axis=1)
        squared = (family_sums.astype(np.int64) ** 2).sum(axis=1)
        # For the exact weighted family sign-flip tail, Hoeffding gives
        # p <= exp(-T^2/(2*sum(w_i^2))).  Counting only certified rejections is
        # therefore a conservative lower bound on the power of that same test.
        eligible = (observed > 0) & (squared > 0)
        log_bound = np.zeros(current, dtype=np.float64)
        log_bound[eligible] = -(
            observed[eligible].astype(np.float64) ** 2
        ) / (2.0 * squared[eligible])
        certified = eligible & (log_bound < log_alpha)
        rejected += int(np.count_nonzero(certified))
        digest.update(family_sums.tobytes(order="C"))
        digest.update(certified.astype(np.uint8).tobytes(order="C"))
        completed += current
    estimate = rejected / POWER_REPETITIONS
    mc_se = math.sqrt(estimate * (1.0 - estimate) / POWER_REPETITIONS)
    interval = wilson_interval(rejected, POWER_REPETITIONS).interval
    if interval is None:  # pragma: no cover - positive fixed repetitions
        raise AssertionError("simulation interval must be available")
    return {
        "certified_rejections": rejected,
        "estimate": estimate,
        "estimate_kind": "conservative_lower_bound_for_exact_family_sign_flip",
        "icc": icc,
        "mc_se": mc_se,
        "repetitions": POWER_REPETITIONS,
        "seed": POWER_SEED,
        "simulation_sha256": digest.hexdigest(),
        "wilson_95": {"lower": interval.lower, "upper": interval.upper},
    }


_FROZEN_POWER_CACHE: dict[str, object] | None = None


def _power_wire() -> dict[str, object]:
    global _FROZEN_POWER_CACHE
    if _FROZEN_POWER_CACHE is not None:
        return _FROZEN_POWER_CACHE
    search_power = _search_power_exact(
        family_count=PRIMARY_FAMILY_COUNT,
        discordance=0.15,
        delta=0.05,
        alpha=0.025,
    )
    repair = _repair_power_simulation(icc=0.25)
    repair_power = cast(float, repair["estimate"])
    estimates = (
        PowerEstimate(
            "repair_joint",
            PRIMARY_FAMILY_COUNT,
            0.025,
            repair_power,
            PowerMethod.SIMULATION,
            "repair-common-shock-rademacher@0.1.0",
        ),
        PowerEstimate(
            "search_best4_joint",
            PRIMARY_FAMILY_COUNT,
            0.025,
            search_power,
            PowerMethod.EXACT,
            "search-mcnemar-multinomial@0.1.0",
        ),
    )
    gate = evaluate_power_gate(
        estimates,
        required_tests=("repair_joint", "search_best4_joint"),
        initial_family_count=PRIMARY_FAMILY_COUNT,
        target_power=0.8,
        required_alpha=0.025,
    )
    if gate.status is not PowerGateStatus.PASS or gate.selected_family_count != 500:
        _fail("power.gate", "the frozen 500-family design does not pass")
    search_sensitivity = [
        {
            "discordance": discordance,
            "power": _search_power_exact(
                family_count=PRIMARY_FAMILY_COUNT,
                discordance=discordance,
                delta=0.05,
                alpha=0.025,
            ),
        }
        for discordance in (0.10, 0.15, 0.20)
    ]
    repair_sensitivity = [
        _repair_power_simulation(icc=icc) for icc in (0.10, 0.25, 0.40)
    ]
    search_contract = {
        "alpha": 0.025,
        "delta": 0.05,
        "discordance": 0.15,
        "family_count": PRIMARY_FAMILY_COUNT,
        "improved_probability": 0.10,
        "method": "exact_mcnemar_multinomial_sum",
        "power": search_power,
        "worsened_probability": 0.05,
    }
    result: dict[str, object] = {
        "gate": {
            "minimum_power": gate.minimum_power,
            "per_test_alpha": gate.per_test_alpha,
            "required_tests": list(gate.required_tests),
            "selected_family_count": gate.selected_family_count,
            "status": gate.status.value,
            "target_power": gate.target_power,
        },
        "initial_family_count": PRIMARY_FAMILY_COUNT,
        "repair": {
            "alpha": 0.025,
            "candidates_per_family": EXPERIMENT_N_SAMPLES,
            "dgp": "repair-common-shock-rademacher@0.1.0",
            "family_delta_mean": 0.10,
            "frozen_simulation": repair,
            "icc": 0.25,
            "p_positive": 0.55,
            "sensitivity": repair_sensitivity,
            "test": "one_sided_family_sign_flip",
        },
        "schema": BENCHMARK_POWER_VERSION,
        "search": {
            **search_contract,
            "calculation_sha256": _domain_sha256(_SEARCH_POWER_DOMAIN, search_contract),
            "dgp": "search-mcnemar-multinomial@0.1.0",
            "sensitivity": search_sensitivity,
            "test": "one_sided_exact_mcnemar",
        },
    }
    _FROZEN_POWER_CACHE = result
    return result


def _prompt_contract(
    stage: str,
    system_prompt_sha256: str,
    renderer: str,
    output_protocol_version: str | None,
    tokens: object,
    temp: float,
) -> dict[str, object]:
    body = {
        "output_protocol_version": output_protocol_version,
        "stage": stage,
        "system_prompt_sha256": system_prompt_sha256,
        "user_renderer_version": renderer,
    }
    return {
        "max_tokens": tokens,
        "output_protocol_version": output_protocol_version,
        "stage": stage,
        "system_prompt_sha256": system_prompt_sha256,
        "template_sha256": _domain_sha256(_PROMPT_DIGEST_DOMAIN, body),
        "temperature": temp,
        "user_renderer_version": renderer,
        "version": BENCHMARK_PROMPT_CONTRACT_VERSION,
    }


def _prompt_wire() -> list[dict[str, object]]:
    repair_system_sha256 = _domain_sha256(
        _PROMPT_DIGEST_DOMAIN,
        {"stage": "repair", "system_prompt": repair_module._SYSTEM},
    )
    critic_system_sha256 = _domain_sha256(
        _PROMPT_DIGEST_DOMAIN,
        {"stage": "critic", "system_prompt": critic_module._CRITIC_SYSTEM},
    )
    return [
        _prompt_contract(
            "proposal_object",
            arranger_module.PROPOSAL_OBJECT_SYSTEM_SHA256,
            "arrangement-proposal-object-user@0.1.0",
            arranger_module.PROPOSAL_OBJECT_PROTOCOL_VERSION,
            "per_item_proposal_raw_max_tokens",
            0.8,
        ),
        _prompt_contract(
            "proposal_compact",
            arranger_module.PROPOSAL_COMPACT_SYSTEM_SHA256,
            "arrangement-proposal-compact-user@0.1.0",
            arranger_module.PROPOSAL_COMPACT_PROTOCOL_VERSION,
            "per_item_proposal_raw_max_tokens",
            0.8,
        ),
        _prompt_contract(
            "raw_object",
            baselines_module.RAW_OBJECT_SYSTEM_SHA256,
            "raw-tab-object-user@0.1.0",
            baselines_module.RAW_OBJECT_PROTOCOL_VERSION,
            "per_item_proposal_raw_max_tokens",
            0.8,
        ),
        _prompt_contract(
            "raw_compact",
            baselines_module.RAW_COMPACT_SYSTEM_SHA256,
            "raw-tab-compact-user@0.1.0",
            baselines_module.RAW_COMPACT_PROTOCOL_VERSION,
            "per_item_proposal_raw_max_tokens",
            0.8,
        ),
        _prompt_contract(
            "repair",
            repair_system_sha256,
            "oracle-diagnostics-edit-user@0.1.0",
            None,
            REPAIR_MAX_TOKENS,
            0.0,
        ),
        _prompt_contract(
            "critic",
            critic_system_sha256,
            "critic-ascii-user@0.1.0",
            None,
            CRITIC_MAX_TOKENS,
            0.0,
        ),
    ]


def _ordered_bindings(corpus_wire: dict[str, object]) -> list[dict[str, object]]:
    raw_items = corpus_wire.get("items")
    if type(raw_items) is not list:
        _fail("corpus.snapshot.items", "must be an exact array")
    result: list[dict[str, object]] = []
    for raw in cast(list[object], raw_items):
        if type(raw) is not dict:
            _fail("corpus.snapshot.items", "must contain exact objects")
        entry = cast(dict[str, object], raw)
        item = entry.get("item")
        if type(item) is not dict:
            _fail("corpus.snapshot.items.item", "must be an exact object")
        value = cast(dict[str, object], item)
        evidence = cast(dict[str, object], value["evidence"])
        signature = "+".join(
            name for name in ("melody", "bass", "harmony") if evidence[name] is True
        )
        result.append(
            {
                "cluster_id": value["cluster_id"],
                "evidence_signature": signature,
                "family_id": value["family_id"],
                "item_id": value["item_id"],
                "item_sha256": entry["item_sha256"],
                "layer": value["layer"],
                "notegraph_sha256": value["notegraph_sha256"],
                "polyphony": value["polyphony"],
                "position": value["position"],
                "synthetic_complexity": value["synthetic_complexity"],
            }
        )
    return result


def _public_bindings(items: tuple[CorpusItem, ...]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for item in items:
        if not item.layer.startswith("public_"):
            continue
        provenance = item.provenance
        evidence = item.evidence
        if provenance is None or evidence is None:
            _fail("corpus.public", "public provenance/evidence is unavailable")
        result.append(
            {
                "evidence_signature": evidence.signature,
                "item_id": item.item_id,
                "layer": item.layer,
                "license": {
                    "derivatives": provenance.license.derivatives,
                    "expression": provenance.license.expression,
                    "provider_submission": provenance.license.provider_submission,
                    "redistribution": provenance.license.redistribution,
                },
                "retrieval_date": provenance.retrieval_date,
                "root_sha256": provenance.root_sha256,
                "source_sha256": provenance.source_sha256,
                "source_url": provenance.source_url,
            }
        )
    return result


def _arms_wire() -> list[dict[str, object]]:
    return [
        {"arm": "initial", "definition": "iteration_zero_of_each_frozen_target"},
        {"arm": "terminal", "definition": "terminal_state_after_up_to_eight_repairs"},
        {"arm": "full", "definition": "terminal_repaired_best_of_4_critic_enabled"},
        {"arm": "critic_off", "definition": "same_repaired_prefix_selected_without_critic"},
        {"arm": "critic_on", "definition": "same_repaired_prefix_selected_with_critic"},
        {"arm": "raw_llm", "definition": "direct_unverified_tab_without_fallback_or_repair"},
        {"arm": "pure_solver", "definition": "deterministic_once_per_item"},
        {"arm": "terminal_llm_only", "definition": "fallback_assisted_counts_as_failure"},
        {
            "arm": "matched_no_repair",
            "definition": "largest_preregistered_prefix_fitting_repair_budget",
        },
        {
            "arm": "matched_raw",
            "definition": "largest_preregistered_prefix_fitting_repair_budget",
        },
    ]


def _wire(items: tuple[CorpusItem, ...]) -> dict[str, object]:
    corpus_wire = cast(dict[str, object], corpus_to_dict(items))
    corpus_file_sha = _sha256_bytes(canonical_json_bytes(corpus_wire))
    datasheet_file_sha = _sha256_bytes(canonical_json_bytes(datasheet(items)))
    if corpus_sha256(items) != TASK5_CORPUS_SHA256 or corpus_file_sha != TASK5_CORPUS_FILE_SHA256:
        _fail("corpus", "does not match the frozen Task 5 corpus")
    if datasheet_file_sha != TASK5_DATASHEET_FILE_SHA256:
        _fail("corpus.datasheet", "does not match the frozen Task 5 datasheet")
    if len(items) != FULL_CORPUS_COUNT:
        _fail("corpus.count", "must equal 503")
    primary = tuple(item for item in items if item.layer == "procedural")
    if len(primary) != PRIMARY_FAMILY_COUNT or len({item.family_id for item in primary}) != 500:
        _fail("corpus.primary", "must contain 500 independent procedural families")
    return {
        "arms": _arms_wire(),
        "budgets": _budget_wire(items),
        "corpus": {
            "artifact_sha256": {
                "contamination.json": TASK5_CONTAMINATION_FILE_SHA256,
                "corpus.json": TASK5_CORPUS_FILE_SHA256,
                "datasheet.json": TASK5_DATASHEET_FILE_SHA256,
                "source-census.json": TASK5_SOURCE_CENSUS_FILE_SHA256,
            },
            "contamination_clean": {
                "cross_stratum": True,
                "procedural": True,
                "real": True,
            },
            "corpus_sha256": TASK5_CORPUS_SHA256,
            "counts": datasheet(items),
            "ordered_bindings": _ordered_bindings(corpus_wire),
            "primary": {
                "bars": 4,
                "base_seed": PRIMARY_PROCEDURAL_BASE_SEED,
                "family_count": PRIMARY_FAMILY_COUNT,
                "generator_version": GENERATOR_VERSION,
                "layer": "procedural",
                "split": "test",
            },
            "public_secondary": _public_bindings(items),
            "snapshot": corpus_wire,
            "source_census_sha256": TASK5_SOURCE_CENSUS_SHA256,
        },
        "decisions": {
            "cheap_remedy_guards": {
                "alternative": "positive",
                "decision": (
                    "point>=0.05_and_holm_p<0.05_and_lower97.5>0_"
                    "else_not_kept_or_inconclusive"
                ),
                "holm_family": ["no_repair", "raw_llm"],
                "sesoi": 0.05,
            },
            "critic": {
                "decision": "HUMAN_BLOCKED_PROBATION",
                "inferential_p_value": False,
            },
            "repair": {
                "alternative": "positive",
                "decision": (
                    "point>=0.10_and_confirmatory_holm_p<0.05_and_lower97.5>0_"
                    "and_both_guards_pass"
                ),
                "sesoi": 0.10,
            },
            "search": {
                "alternative": "positive",
                "decision": (
                    "point>=0.05_and_confirmatory_holm_p<0.05_and_lower97.5>0_"
                    "and_best4_nondominated"
                ),
                "sesoi": 0.05,
                "unknown_cost_decision": "PROBATION_COST_UNKNOWN",
            },
        },
        "evidence_status": "PRE_OUTCOME_SOFTWARE_ONLY",
        "gate_commands": {
            "assumed_runner_flags_requiring_alignment": ["--prereg", "--pre-call-config"],
            "full_replay": [
                "uv", "run", "fretsure-bench",
                "--replay-config", "<config>",
                "--replay-receipt", "<receipt>",
                "--replay-rows", "<rows>",
                "--replay-blobs", "<blobs>",
                "--replay-observations", "<sanitized-observations>",
                "--output-dir", "<fresh-replay>",
            ],
            "live": [
                "uv", "run", "fretsure-bench", "--live",
                "--pre-call-config", "<pre-call-config>",
                "--output-dir", "<fresh-live>",
            ],
            "offline_gates": [
                "uv run pytest -q -m 'not integration'",
                "uv run pytest -q -m integration",
                "uv run ruff check .",
                "uv run mypy --strict src",
                "uv run mypy --strict scripts/build_benchmark_corpus.py",
                "uv run mypy --strict scripts/build_benchmark_prereg.py",
                "uv lock --check",
                "uv run python scripts/check_markdown_links.py",
                "git diff --check",
                "uv build",
                "uv run python scripts/audit_distributions.py",
                "uv run python scripts/smoke_distributions.py",
            ],
            "stub_a": [
                "uv", "run", "fretsure-bench", "--stub",
                "--prereg", "docs/experiments/2026-07-17-benchmark-v2-prereg.json",
                "--output-dir", "<fresh-a>",
            ],
            "stub_b": [
                "uv", "run", "fretsure-bench", "--stub",
                "--prereg", "docs/experiments/2026-07-17-benchmark-v2-prereg.json",
                "--output-dir", "<fresh-b>",
            ],
        },
        "inference": {
            "binary_intervals": ["wilson_95", "clopper_pearson_95"],
            "bootstrap": {
                "cluster_unit": "whole_cluster_within_frozen_stratum",
                "equal_family_weight": True,
                "quantile": "type_7_linear",
                "repetitions": BOOTSTRAP_REPETITIONS,
                "seed": BOOTSTRAP_SEED,
                "strata": ["layer", "evidence_signature", "synthetic_complexity", "polyphony"],
            },
            "confirmatory_holm_family": ["repair_joint", "search_best4_joint"],
            "mcnemar": "one_sided_exact_with_matched_odds_ratio_exact_95",
            "sign_flip": {
                "draws": SIGN_FLIP_DRAWS,
                "exact_nonzero_family_max": 20,
                "monte_carlo_correction": "(extreme+1)/(draws+1)",
                "seed": SIGN_FLIP_SEED,
            },
        },
        "itt_missingness": {
            "binary_denominator": "all_structurally_applicable_scheduled_outcomes",
            "continuous_fidelity": {
                "conditional": "mean_among_scored_structurally_applicable_outcomes",
                "failure_inclusive": "zero_for_structurally_applicable_failed_or_unscored_outcomes",
            },
            "fallback": {
                "llm_only_sensitivity": "failure",
                "primary_end_to_end": "score_valid_fallback_normally_and_tag_fallback_assisted",
            },
            "no_tab_or_invalid": "failure_in_every_structurally_applicable_binary_denominator",
            "orphan_intent": {
                "abandoned_attempt_analysis": "excluded_in_full",
                "artifact_requirement": "fresh_output_directory",
                "authorization": "new_pre_call_config_and_cost_authorization_required",
                "budget_scope": "single_collection_attempt_nontransferable",
                "complete_attempt_selection": (
                    "lowest_numbered_complete_attempt_only_no_replacement_after_complete"
                ),
                "formal_experiment_id": "preregistration.run_id",
                "next_attempt": "strictly_higher_positive_collection_attempt",
                "partial_outcome_use": "forbidden_for_restart_selection",
                "run_id_derivation": "<formal_experiment_id>-attempt-{collection_attempt:03d}",
            },
            "structural_dimensions": "frozen_from_source_evidence_before_calls",
            "transport_parse_scoring_failures": "remain_in_itt_denominator",
        },
        "model_and_prompts": {
            "allowed_returned_model_rule": {
                "operator": "exact_equal",
                "value": DEFAULT_PROXY_MODEL,
            },
            "prompts": _prompt_wire(),
            "requested_model": DEFAULT_PROXY_MODEL,
        },
        "package_target_version": TARGET_PACKAGE_VERSION,
        "plan_receipt_git_sha": PLAN_GIT_SHA,
        "power": _power_wire(),
        "pre_call_manifest_requirements": {
            "analysis_binding": (
                "analysis_module_digest_or_installed_wheel_RECORD_digest_including_"
                "bound_proposal_raw_protocol_constants"
            ),
            "execution_git_sha": (
                "required_external_clean_runner_ready_gate_value_not_stored_in_prereg"
            ),
            "forbidden_runtime_discovery": ["git", "subprocess", "ambient_import_path_inspection"],
            "prereg_file_sha256": "required_raw_file_digest_not_stored_in_prereg",
            "required_runtime_fields": ["package", "python", "os", "architecture"],
            "uv_lock_sha256": "required_from_runner_ready_tree",
        },
        "run_id": FORMAL_RUN_ID,
        "sampling": {
            "critic_temperature": 0.0,
            "max_repair_iters": EXPERIMENT_MAX_REPAIR_ITERS,
            "n_samples": EXPERIMENT_N_SAMPLES,
            "proposal_temperature": EXPERIMENT_TEMPERATURE,
            "raw_temperature": EXPERIMENT_TEMPERATURE,
            "reliability_k": list(RELIABILITY_K_VALUES),
            "repair_temperature": 0.0,
            "search_k": list(SEARCH_K_VALUES),
            "selection_full": "repaired_best_of_4_critic_enabled",
        },
        "schedule": _schedule_wire(items),
        "schema": BENCHMARK_PREREGISTRATION_VERSION,
        "unit_contract": {
            "candidate_index": "ordered_index_within_one_preregistered_ten_proposal_pool",
            "independent_unit": "family_id_cluster_id",
            "public_windows_if_any": "nested_within_original_family_never_new_inferential_units",
            "repeated_observations": 10,
            "solver_target": {
                "aggregate_admitted_segment_search_work_limit": (
                    MAX_SCORE_SOLVER_AGGREGATE_WORK_UNITS
                ),
                "full_score_reassembly_gate": "oracle_RED_returns_Infeasible",
                "long_score_split": "deterministic_complete_onset_frames_only",
                "maximum_segments": MAX_SCORE_SOLVER_SEGMENTS,
                "per_segment_solver_work_limit": MAX_SOLVER_WORK_UNITS,
                "source_event_budget_basis": (
                    "original_source_notes_plus_chords_before_target_coalescing"
                ),
                "unison_coalescing": (
                    "same_onset_pitch_solver_target_only_source_prompt_fidelity_unchanged"
                ),
            },
        },
        "versions": {
            "arrangement_unison_coalescer": (
                arranger_module.ARRANGEMENT_UNISON_COALESCER_VERSION
            ),
            "corpus": BENCHMARK_CORPUS_VERSION,
            "fidelity": FIDELITY_CHECKER_VERSION,
            "manifest": BENCHMARK_MANIFEST_VERSION,
            "notegraph": BENCHMARK_NOTEGRAPH_VERSION,
            "observations": BENCHMARK_OBSERVATIONS_VERSION,
            "oracle": CHECKER_VERSION,
            "profile_fingerprint": MEDIAN_HAND.fingerprint,
            "profile_version": MEDIAN_HAND.version,
            "public_adapter": BENCHMARK_PUBLIC_ADAPTER_VERSION,
            "public_router": BENCHMARK_PUBLIC_ROUTER_VERSION,
            "proposal_compact_protocol": arranger_module.PROPOSAL_COMPACT_PROTOCOL_VERSION,
            "proposal_object_protocol": arranger_module.PROPOSAL_OBJECT_PROTOCOL_VERSION,
            "raw_compact_protocol": baselines_module.RAW_COMPACT_PROTOCOL_VERSION,
            "raw_object_protocol": baselines_module.RAW_OBJECT_PROTOCOL_VERSION,
            "receipt": BENCHMARK_RECEIPT_VERSION,
            "report": BENCHMARK_REPORT_VERSION,
            "row": BENCHMARK_ROW_VERSION,
            "score_input": SCORE_INPUT_VERSION,
            "score_solver_composition": SCORE_SOLVER_VERSION,
            "tab_input": ORACLE_INPUT_SCHEMA_VERSION,
            "trace": TRACE_SCHEMA_VERSION,
        },
    }


@dataclass(frozen=True, slots=True)
class BenchmarkPreregistration:
    """One canonical, immutable preregistration byte string."""

    wire_json: bytes

    def __post_init__(self) -> None:
        if type(self.wire_json) is not bytes:
            _fail("wire_json", "must be exact bytes")
        parsed = parse_canonical_json_bytes(self.wire_json)
        if type(parsed) is not dict:
            _fail("wire_json", "must encode one canonical object")

    def to_dict(self) -> dict[str, object]:
        value = parse_canonical_json_bytes(self.wire_json)
        if type(value) is not dict:  # pragma: no cover - constructor invariant
            raise AssertionError("preregistration must encode an object")
        return cast(dict[str, object], value)


def build_preregistration(items: object) -> BenchmarkPreregistration:
    snapshots = snapshot_corpus(items)
    return BenchmarkPreregistration(canonical_json_bytes(_wire(snapshots)))


def preregistration_from_dict(value: object) -> BenchmarkPreregistration:
    if type(value) is not dict:
        _fail("$", "must be an exact object")
    obj = cast(dict[str, object], value)
    expected_top = frozenset(
        {
            "arms", "budgets", "corpus", "decisions", "evidence_status",
            "gate_commands", "inference", "itt_missingness", "model_and_prompts",
            "package_target_version", "plan_receipt_git_sha", "power",
            "pre_call_manifest_requirements", "run_id", "sampling", "schedule",
            "schema", "unit_contract", "versions",
        }
    )
    if frozenset(obj) != expected_top:
        _fail("$", "must contain the exact frozen top-level keys")
    if obj.get("schema") != BENCHMARK_PREREGISTRATION_VERSION:
        _fail("schema", "has the wrong version")
    corpus = obj.get("corpus")
    if type(corpus) is not dict:
        _fail("corpus", "must be an exact object")
    snapshot = cast(dict[str, object], corpus).get("snapshot")
    try:
        items = corpus_from_dict(snapshot)
    except ValueError as error:
        raise PreregistrationError("corpus.snapshot", "is not a strict corpus") from error
    expected = _wire(items)
    if obj != expected:
        _fail("$", "content differs from the frozen deterministic preregistration")
    return BenchmarkPreregistration(canonical_json_bytes(obj))


def preregistration_from_bytes(data: object) -> BenchmarkPreregistration:
    if type(data) is not bytes:
        _fail("$", "must be exact bytes")
    try:
        parsed = parse_canonical_json_bytes(data)
    except ValueError as error:
        raise PreregistrationError("$", "must be canonical benchmark JSON") from error
    return preregistration_from_dict(parsed)


def budget_markdown(preregistration: BenchmarkPreregistration) -> str:
    if type(preregistration) is not BenchmarkPreregistration:
        _fail("preregistration", "must be an exact BenchmarkPreregistration")
    wire = preregistration.to_dict()
    budget = cast(dict[str, object], wire["budgets"])
    primary = cast(dict[str, object], budget["primary_procedural"])
    full = cast(dict[str, object], budget["full_corpus"])
    primary_calls = cast(dict[str, int], primary["logical_calls_by_stage"])
    primary_tokens = cast(dict[str, int], primary["requested_output_tokens_by_stage"])
    full_calls = cast(dict[str, int], full["logical_calls_by_stage"])
    full_tokens = cast(dict[str, int], full["requested_output_tokens_by_stage"])
    reservation = cast(dict[str, object], budget["reserve_before_next_scheduled_unit"])
    prefixes = cast(dict[str, int], budget["matched_control_prefix_counts"])
    public = cast(list[object], cast(dict[str, object], wire["corpus"])["public_secondary"])
    public_budget = {
        cast(str, value["item_id"]): PUBLIC_PROPOSAL_TOKENS[cast(str, value["item_id"])]
        for value in cast(list[dict[str, object]], public)
    }
    primary_call_total = cast(int, primary["logical_calls_total"])
    primary_token_total = cast(int, primary["requested_output_tokens_total"])
    primary_attempts = cast(int, primary["maximum_attempts"])
    primary_reserved = cast(int, primary["attempt_reserved_output_tokens"])
    primary_response = cast(int, primary["response_text_bytes"])
    primary_transport = cast(int, primary["transport_response_bytes"])
    full_call_total = cast(int, full["logical_calls_total"])
    full_token_total = cast(int, full["requested_output_tokens_total"])
    full_attempts = cast(int, full["maximum_attempts"])
    full_reserved = cast(int, full["attempt_reserved_output_tokens"])
    full_response = cast(int, full["response_text_bytes"])
    full_transport = cast(int, full["transport_response_bytes"])
    full_timeout = cast(int, full["provider_timeout_envelope_milliseconds"])
    provider_elapsed = cast(
        int,
        budget["recorded_provider_call_elapsed_ceiling_seconds"],
    )
    reserve_calls = cast(int, reservation["logical_calls"])
    reserve_attempts = cast(int, reservation["attempts"])
    reserve_tokens = cast(int, reservation["requested_output_tokens"])
    reserve_response = cast(int, reservation["response_text_bytes"])
    reserve_transport = cast(int, reservation["transport_response_bytes"])
    lines = [
        "# Benchmark v2 preregistered budget",
        "",
        "Date: 2026-07-17<br>",
        "Status: frozen before proxy outcomes; not collection authorization",
        "",
        "The independent primary unit is one procedural family. Ten proposal slots are nested",
        "repeated observations, not 5,000 independent families. Unknown provider usage and price",
        "remain unavailable rather than zero.",
        "",
        "## Frozen collection shape",
        "",
        "- Primary: 500 procedural families; secondary: 3 licensed public works.",
        "- Ten agent candidates and ten raw-baseline calls per item, temperature `0.8`.",
        "- Maximum eight repair calls and one conditional critic call per agent candidate.",
        "- One deterministic pure-solver row per item makes no provider call.",
        "",
        "## Primary procedural maximum",
        "",
        "| Stage | Logical calls | Requested output tokens |",
        "|---|---:|---:|",
    ]
    for stage in ("proposal", "repair", "critic", "raw"):
        lines.append(
            f"| {stage} | {primary_calls[stage]:,} | {primary_tokens[stage]:,} |"
        )
    lines.extend(
        [
            f"| **Total** | **{primary_call_total:,}** | **{primary_token_total:,}** |",
            "",
            f"- Provider attempts: `{primary_attempts:,}`.",
            f"- Attempt-reserved output tokens: `{primary_reserved:,}`.",
            f"- Bounded response text: `{primary_response:,}` bytes.",
            f"- Raw transport envelope: `{primary_transport:,}` bytes.",
            "",
            "## Full 503-item maximum",
            "",
            "| Stage | Logical calls | Requested output tokens |",
            "|---|---:|---:|",
        ]
    )
    for stage in ("proposal", "repair", "critic", "raw"):
        lines.append(f"| {stage} | {full_calls[stage]:,} | {full_tokens[stage]:,} |")
    lines.extend(
        [
            f"| **Total** | **{full_call_total:,}** | **{full_token_total:,}** |",
            "",
            f"- Maximum attempts: `{full_attempts:,}`.",
            f"- Attempt-reserved output tokens: `{full_reserved:,}`.",
            f"- Bounded response text: `{full_response:,}` bytes.",
            f"- Raw transport envelope: `{full_transport:,}` bytes.",
            f"- Timeout-derived provider envelope: `{full_timeout:,}` ms.",
            (
                "- Recorded provider-call elapsed ceiling: "
                f"`{provider_elapsed:,}` seconds."
            ),
            "  This sums durable call-result elapsed time; it is not host wall time and",
            "  excludes local solver, serialization, replay, and report CPU time.",
            "",
            "## Lossless public compact proposal contract",
            "",
            f"The version `{PUBLIC_COMPACT_PROPOSAL_VERSION}` uses `128 + 32 × source events`,",
            "with a 16,384-token cap. It changes the wire representation, not the normalized",
            "notegraph: no event is truncated, and every work remains one inferential family.",
            f"Long-score solving admits at most `{MAX_SCORE_SOLVER_SEGMENTS}` bounded searches:",
            f"`{MAX_SOLVER_WORK_UNITS:,}` estimated work units per admitted segment and",
            f"`{MAX_SCORE_SOLVER_AGGREGATE_WORK_UNITS:,}` across admitted segment searches.",
            "Rejected oversized preflights and the final full-history oracle are control",
            "work outside that estimate; they do not authorize another segment search.",
            "",
            "| Public item | Events | Proposal/raw tokens |",
            "|---|---:|---:|",
        ]
    )
    for item_id in (
        "public-classical-beethoven-op48-5",
        "public-midi-bwv775",
        "public-midi-bwv774",
    ):
        lines.append(
            f"| `{item_id}` | {PUBLIC_EVENT_COUNTS[item_id]} | {public_budget[item_id]:,} |"
        )
    lines.extend(
        [
            "",
            "## Scheduled-unit reservation and matched controls",
            "",
            "Before starting the next preregistered schedule unit, reserve its exact arm",
            "envelope. The maximum single-unit envelope is the agent arm:",
            f"`{reserve_calls}` logical calls,",
            f"`{reserve_attempts}` attempts and `{reserve_tokens:,}` requested output tokens,",
            f"plus `{reserve_response:,}` response-text bytes and",
            f"`{reserve_transport:,}` transport bytes.",
            "An agent+raw pair has a separate 11-call/33-attempt maximum envelope; it is",
            "not the ArtifactStore atomic unit. Summed schedule-unit envelopes equal the",
            "full preregistered totals.",
            "",
            "Matched no-repair/raw prefix counts across all 503 items:",
            "",
        ]
    )
    lines.extend(f"- `m={prefix}`: {count} items" for prefix, count in prefixes.items())
    lines.extend(
        [
            "",
            "## External cost gate",
            "",
            "`cost_contract_unavailable`: maximum spend is null until a verifiable price contract",
            "and explicit user authorization exist. This preregistration authorizes no provider",
            "call. Later pilot and formal configs may lower these ceilings but may not raise",
            "them without a new preregistration.",
            "All ceilings apply to one numbered collection attempt and are non-transferable.",
            "After an orphan, a higher attempt needs a fresh pre-call config and cost",
            "authorization that accounts for prior consumed spend; partial outcomes cannot",
            "be inspected to choose whether to restart.",
            "",
            "## Runner alignment note",
            "",
            "The frozen command shapes use `--prereg` for stub collection and `--pre-call-config`",
            "for live collection. Runner work must implement or deliberately map those exact",
            "flags before either gate is claimed.",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "BENCHMARK_PREREGISTRATION_VERSION",
    "BenchmarkPreregistration",
    "PreregistrationError",
    "PUBLIC_COMPACT_PROPOSAL_VERSION",
    "budget_markdown",
    "build_preregistration",
    "preregistration_from_bytes",
    "preregistration_from_dict",
]
