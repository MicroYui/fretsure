"""Frozen machine preregistration for benchmark-v2.

This module is deliberately pure: it does not inspect Git, import paths, the
network, or ambient process state.  A caller supplies the already normalized
503-item corpus and receives the one canonical preregistration plus its
human-readable budget view.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Final, NoReturn, cast

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
from fretsure.importers.score import SCORE_INPUT_VERSION
from fretsure.llm.client import (
    DEFAULT_PROXY_MODEL,
    MAX_PROXY_RESPONSE_BYTES,
    MAX_PROXY_TEXT_BYTES_PER_TOKEN,
    MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
    PROXY_CONNECT_TIMEOUT_SECONDS,
)
from fretsure.metrics.fidelity import FIDELITY_CHECKER_VERSION
from fretsure.oracle.core import CHECKER_VERSION
from fretsure.oracle.input import ORACLE_INPUT_SCHEMA_VERSION
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.score import (
    SCORE_SOLVER_VERSION,
)

BENCHMARK_PREREGISTRATION_VERSION: Final = "benchmark-preregistration@0.3.0"
BENCHMARK_PROMPT_CONTRACT_VERSION: Final = "benchmark-prompt-contract@0.1.0"
BENCHMARK_SCHEDULE_VERSION: Final = "benchmark-experiment-schedule@0.1.0"
BENCHMARK_COLLECTION_EXECUTION_VERSION: Final = "benchmark-collection-execution@0.1.0"
PUBLIC_COMPACT_PROPOSAL_VERSION: Final = arranger_module.PROPOSAL_COMPACT_PROTOCOL_VERSION

FORMAL_OPERATIONAL_REQUEST_TIMEOUT_SECONDS: Final = 300.0
FORMAL_OPERATIONAL_RECORDED_ATTEMPT_OVERHEAD_SECONDS: Final = 10.0
FORMAL_OPERATIONAL_RECORDED_ELAPSED_CEILING_SECONDS: Final = 51_840_000
FORMAL_OPERATIONAL_MAX_IN_FLIGHT_UNITS: Final = 4

FORMAL_RUN_ID: Final = "benchmark-v2-formal-20260717"
SCHEDULE_SEED: Final = 2_026_071_700
BOOTSTRAP_SEED: Final = 2_026_071_701
BOOTSTRAP_REPETITIONS: Final = 10_000
SIGN_FLIP_SEED: Final = 2_026_071_702
SIGN_FLIP_DRAWS: Final = 100_000

PRIMARY_FAMILY_COUNT: Final = 500
FULL_CORPUS_COUNT: Final = 503
TASK5_CORPUS_SHA256: Final = "b4e2a1ed05eb07d82bdea18b9105cdd92b564cf864d8acedaa3c37d820848e8b"
TASK5_SOURCE_CENSUS_SHA256: Final = (
    "aa10f8d60b35d1c687806c0426bf50a2d30488d84b1f23317f72fc7dcceee372"
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
_PROMPT_DIGEST_DOMAIN = f"fretsure:{BENCHMARK_PROMPT_CONTRACT_VERSION}\0".encode("ascii")


class PreregistrationError(ValueError):
    """The preregistration or its source corpus differs from the frozen contract."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid benchmark preregistration {field}: {detail}")


def _fail(field: str, detail: str) -> NoReturn:
    raise PreregistrationError(field, detail)


_EXPECTED_TOP_LEVEL_KEYS: Final = frozenset(
    {
        "budgets",
        "collection_execution",
        "corpus",
        "inference",
        "model_and_prompts",
        "run_id",
        "sampling",
        "schedule",
        "schema",
        "versions",
    }
)


def _object(value: object, field: str, keys: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or frozenset(cast(dict[str, object], value)) != keys:
        _fail(field, "must contain the exact frozen keys")
    return cast(dict[str, object], value)


def _object_of(holder: dict[str, object], field: str) -> dict[str, object]:
    value = holder.get(field.rsplit(".", 1)[-1])
    if type(value) is not dict:
        _fail(field, "must be an exact object")
    return cast(dict[str, object], value)


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        _fail(field, "must be an exact nonnegative integer")
    return value


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
    return {
        "algorithm": BENCHMARK_SCHEDULE_VERSION,
        "collection_schedule": units,
        "collection_unit_count": len(units),
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
            + EXPERIMENT_MAX_REPAIR_ITERS * REPAIR_MAX_TOKENS * MAX_PROXY_TEXT_BYTES_PER_TOKEN
            + CRITIC_MAX_TOKENS * MAX_PROXY_TEXT_BYTES_PER_TOKEN
        )
        agent_tokens = (
            proposal_tokens + EXPERIMENT_MAX_REPAIR_ITERS * REPAIR_MAX_TOKENS + CRITIC_MAX_TOKENS
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


def _budget_wire(
    items: tuple[CorpusItem, ...],
    *,
    request_timeout_seconds: float,
    recorded_attempt_overhead_seconds: float | None,
    recorded_provider_elapsed_ceiling_seconds: int,
) -> dict[str, object]:
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
            attempts * request_timeout_seconds * 1_000 + retry_backoff_milliseconds
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
    provider_policy: dict[str, object] = {
        "connect_timeout_seconds": PROXY_CONNECT_TIMEOUT_SECONDS,
        "maximum_attempts_per_logical_call": 3,
        "maximum_response_bytes": MAX_PROXY_RESPONSE_BYTES,
        "maximum_transport_response_bytes": MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
        "request_timeout_seconds": request_timeout_seconds,
        "retry_backoff_seconds": [0.5, 1.0],
    }
    if recorded_attempt_overhead_seconds is not None:
        provider_policy["recorded_attempt_elapsed_overhead_seconds"] = (
            recorded_attempt_overhead_seconds
        )
    return {
        "ceiling_scope": "single_collection_attempt_nontransferable",
        "cost_contract": {
            "maximum_spend": None,
            "reason": "COST_CONTRACT_UNAVAILABLE_BEFORE_EXPLICIT_EXTERNAL_BUDGET_GATE",
            "status": "cost_contract_unavailable",
        },
        "full_corpus": full_totals,
        "recorded_provider_call_elapsed_ceiling_seconds": (
            recorded_provider_elapsed_ceiling_seconds
        ),
        "matched_control_prefix_counts": dict(
            sorted(prefix_counts.items(), key=lambda x: int(x[0]))
        ),
        "per_item": per_item,
        "primary_procedural": primary_totals,
        "provider_policy": provider_policy,
        "reserve_before_next_scheduled_unit": maximum_reservation,
        "storage": {
            "max_blobs": item_count * 83,
            "max_rows": item_count * 21,
        },
    }


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


def _wire(items: tuple[CorpusItem, ...]) -> dict[str, object]:
    """Derive the executable preregistration from the frozen corpus itself."""

    if corpus_sha256(items) != TASK5_CORPUS_SHA256:
        _fail("corpus", "does not match the frozen benchmark corpus identity")
    if len(items) != FULL_CORPUS_COUNT:
        _fail("corpus.count", "must equal 503")
    primary = tuple(item for item in items if item.layer == "procedural")
    if len(primary) != PRIMARY_FAMILY_COUNT or len({item.family_id for item in primary}) != 500:
        _fail("corpus.primary", "must contain 500 independent procedural families")
    wire: dict[str, object] = {
        "budgets": _budget_wire(
            items,
            request_timeout_seconds=FORMAL_OPERATIONAL_REQUEST_TIMEOUT_SECONDS,
            recorded_attempt_overhead_seconds=(
                FORMAL_OPERATIONAL_RECORDED_ATTEMPT_OVERHEAD_SECONDS
            ),
            recorded_provider_elapsed_ceiling_seconds=(
                FORMAL_OPERATIONAL_RECORDED_ELAPSED_CEILING_SECONDS
            ),
        ),
        "collection_execution": {
            "admission_order": "collection_schedule_index_ascending",
            "canonical_merge_order": ("collection_schedule_index_ascending_then_local_call_index"),
            "client_ownership": "one_agent_and_one_raw_client_per_worker",
            "completion_order": "not_semantic",
            "durability": "unit_intent_and_attempt_fsync_before_provider_request",
            "max_in_flight_units": FORMAL_OPERATIONAL_MAX_IN_FLIGHT_UNITS,
            "protocol": BENCHMARK_COLLECTION_EXECUTION_VERSION,
            "resume_boundary": "completed_durable_unit",
        },
        "corpus": {
            "corpus_sha256": TASK5_CORPUS_SHA256,
            "primary": {
                "bars": 4,
                "base_seed": PRIMARY_PROCEDURAL_BASE_SEED,
                "family_count": PRIMARY_FAMILY_COUNT,
                "generator_version": GENERATOR_VERSION,
                "layer": "procedural",
                "split": "test",
            },
            "snapshot": cast(dict[str, object], corpus_to_dict(items)),
            "source_census_sha256": TASK5_SOURCE_CENSUS_SHA256,
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
        "model_and_prompts": {
            "allowed_returned_model_rule": {
                "operator": "exact_equal",
                "value": DEFAULT_PROXY_MODEL,
            },
            "prompts": _prompt_wire(),
            "requested_model": DEFAULT_PROXY_MODEL,
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
        "versions": {
            "collection_execution": BENCHMARK_COLLECTION_EXECUTION_VERSION,
            "arrangement_unison_coalescer": (arranger_module.ARRANGEMENT_UNISON_COALESCER_VERSION),
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
    return wire


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
    """Derive the canonical preregistration for one frozen corpus."""

    snapshots = snapshot_corpus(items)
    return BenchmarkPreregistration(canonical_json_bytes(_wire(snapshots)))


def preregistration_from_dict(value: object) -> BenchmarkPreregistration:
    """Validate the shape and the fields a collection actually consumes.

    This is deliberately structural rather than a byte-for-byte re-derivation.
    The real integrity proof lives in the runner, which regenerates the schedule
    from the corpus and rejects any preregistration whose schedule does not
    match the executable plan.
    """

    if type(value) is not dict:
        _fail("$", "must be an exact object")
    obj = cast(dict[str, object], value)
    if obj.get("schema") != BENCHMARK_PREREGISTRATION_VERSION:
        _fail("schema", "has the wrong version")
    if frozenset(obj) != _EXPECTED_TOP_LEVEL_KEYS:
        _fail("$", "must contain the exact frozen top-level keys")
    corpus = _object(obj["corpus"], "corpus", frozenset({
        "corpus_sha256",
        "primary",
        "snapshot",
        "source_census_sha256",
    }))
    try:
        items = corpus_from_dict(corpus["snapshot"])
    except ValueError as error:
        raise PreregistrationError("corpus.snapshot", "is not a strict corpus") from error
    if (
        corpus_sha256(items) != TASK5_CORPUS_SHA256
        or corpus["corpus_sha256"] != TASK5_CORPUS_SHA256
    ):
        _fail("corpus", "does not match the frozen benchmark corpus identity")
    primary = _object(
        corpus["primary"],
        "corpus.primary",
        frozenset({"bars", "base_seed", "family_count", "generator_version", "layer", "split"}),
    )
    for field, holder, name in (
        ("corpus.primary.bars", primary, "bars"),
        ("corpus.primary.base_seed", primary, "base_seed"),
        ("corpus.primary.family_count", primary, "family_count"),
    ):
        _positive_int(holder[name], field)
    inference = cast(dict[str, object], obj["inference"])
    bootstrap = _object_of(inference, "inference.bootstrap")
    sign_flip = _object_of(inference, "inference.sign_flip")
    for field, holder, name in (
        ("inference.bootstrap.seed", bootstrap, "seed"),
        ("inference.bootstrap.repetitions", bootstrap, "repetitions"),
        ("inference.sign_flip.seed", sign_flip, "seed"),
        ("inference.sign_flip.draws", sign_flip, "draws"),
    ):
        _positive_int(holder.get(name), field)
    schedule = cast(dict[str, object], obj["schedule"])
    _positive_int(schedule.get("schedule_seed"), "schedule.schedule_seed")
    _positive_int(schedule.get("collection_unit_count"), "schedule.collection_unit_count")
    execution = cast(dict[str, object], obj["collection_execution"])
    _positive_int(
        execution.get("max_in_flight_units"), "collection_execution.max_in_flight_units"
    )
    model = _object(
        obj["model_and_prompts"],
        "model_and_prompts",
        frozenset({"allowed_returned_model_rule", "prompts", "requested_model"}),
    )
    if type(model["requested_model"]) is not str or not model["requested_model"]:
        _fail("model_and_prompts.requested_model", "must be one nonempty string")
    if type(obj["run_id"]) is not str or not obj["run_id"]:
        _fail("run_id", "must be one nonempty string")
    return BenchmarkPreregistration(canonical_json_bytes(obj))


def preregistration_from_bytes(data: object) -> BenchmarkPreregistration:
    if type(data) is not bytes:
        _fail("$", "must be exact bytes")
    try:
        parsed = parse_canonical_json_bytes(data)
    except ValueError as error:
        raise PreregistrationError("$", "must be canonical benchmark JSON") from error
    return preregistration_from_dict(parsed)


def artifact_ceilings(
    preregistration: BenchmarkPreregistration,
) -> tuple[dict[str, int], dict[str, int]]:
    """Return the full-run artifact ceilings and the complete-unit reservation.

    Both are pure arithmetic over ``budgets``, which this module derived from the
    corpus itself, so the caller gets detached copies rather than a live view.
    """

    if type(preregistration) is not BenchmarkPreregistration:
        _fail("preregistration", "must be an exact BenchmarkPreregistration")
    budgets = cast(dict[str, object], preregistration.to_dict()["budgets"])
    full = cast(dict[str, object], budgets["full_corpus"])
    reservation = cast(dict[str, object], budgets["reserve_before_next_scheduled_unit"])
    provider = cast(dict[str, object], budgets["provider_policy"])
    wall_ceiling_seconds = cast(int, budgets["recorded_provider_call_elapsed_ceiling_seconds"])
    maximum = {
        "max_attempt_reserved_output_tokens": cast(
            int, full["attempt_reserved_output_tokens"]
        ),
        "max_attempts": cast(int, full["maximum_attempts"]),
        "max_logical_calls": cast(int, full["logical_calls_total"]),
        "max_requested_output_tokens": cast(int, full["requested_output_tokens_total"]),
        "max_response_text_bytes": cast(int, full["response_text_bytes"]),
        "max_transport_response_bytes": cast(int, full["transport_response_bytes"]),
        "max_recorded_provider_call_elapsed_microseconds": wall_ceiling_seconds * 1_000_000,
    }
    attempts = cast(int, reservation["attempts"])
    logical_calls = cast(int, reservation["logical_calls"])
    requested_output_tokens = cast(int, reservation["requested_output_tokens"])
    retry_backoff = cast(list[float], provider["retry_backoff_seconds"])
    unit_wall_seconds = attempts * (
        float(cast(float, provider["request_timeout_seconds"]))
        # The legacy 2026-07-17 protocol predates the recorded-overhead field.
        + float(cast(float, provider.get("recorded_attempt_elapsed_overhead_seconds", 0.0)))
    ) + logical_calls * sum(float(value) for value in retry_backoff)
    unit = {
        "attempt_reserved_output_tokens": requested_output_tokens * 3,
        "attempts": attempts,
        "logical_calls": logical_calls,
        "requested_output_tokens": requested_output_tokens,
        "response_text_bytes": cast(int, reservation["response_text_bytes"]),
        "transport_response_bytes": cast(int, reservation["transport_response_bytes"]),
        "recorded_provider_call_elapsed_microseconds": int(unit_wall_seconds * 1_000_000),
    }
    return maximum, unit


__all__ = [
    "BENCHMARK_COLLECTION_EXECUTION_VERSION",
    "BENCHMARK_PREREGISTRATION_VERSION",
    "FORMAL_OPERATIONAL_RECORDED_ATTEMPT_OVERHEAD_SECONDS",
    "FORMAL_OPERATIONAL_MAX_IN_FLIGHT_UNITS",
    "BenchmarkPreregistration",
    "PreregistrationError",
    "PUBLIC_COMPACT_PROPOSAL_VERSION",
    "artifact_ceilings",
    "build_preregistration",
    "preregistration_from_bytes",
    "preregistration_from_dict",
]
