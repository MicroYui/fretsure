#!/usr/bin/env python3
"""Task 8 operational proxy pilot without changing the frozen formal wheel.

The pilot deliberately lives outside ``src/``.  It reuses the shipped benchmark
artifact/WAL and row materialization code, but has its own corpus, manifest schema,
run id, schedule, and pre-call declaration.  Pilot outcomes are never passed to the
formal benchmark report builder.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import sys
import time
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from types import ModuleType
from typing import Final, NoReturn, Protocol, cast

import fretsure.bench.report as report_module
from fretsure.agent.arranger import (
    ArrangeGoal,
    proposal_output_token_budget,
)
from fretsure.agent.critic import CRITIC_MAX_TOKENS
from fretsure.agent.harness import CandidateTrajectory, build_candidate_trajectory
from fretsure.agent.repair import REPAIR_MAX_TOKENS
from fretsure.bench.artifacts import (
    ArtifactLimits,
    ArtifactStore,
    BenchmarkManifest,
    BenchmarkReceipt,
    BenchmarkRow,
    BlobRecord,
    CompleteUnitReservation,
    RowKey,
    RowType,
    SanitizedObservations,
    build_manifest,
    load_replay_bundle,
    parse_canonical_json_bytes,
)
from fretsure.bench.baselines import (
    RawLLMOutcome,
    build_raw_baseline_request,
    collect_raw_llm_baseline,
)
from fretsure.bench.contracts import canonical_json_bytes, require_identifier
from fretsure.bench.corpus import (
    CorpusItem,
    ProceduralCorpusConfig,
    build_primary_procedural_corpus,
    corpus_from_dict,
    corpus_sha256,
    corpus_to_dict,
    notegraph_sha256,
)
from fretsure.bench.experiment import (
    EXPERIMENT_MAX_REPAIR_ITERS,
    EXPERIMENT_TEMPERATURE,
    ObservationLedger,
    sample_pair_id,
)
from fretsure.bench.observe import (
    CallSequence,
    CallStage,
    ObservingLLM,
    current_call_context,
)
from fretsure.bench.preregistration import (
    BenchmarkPreregistration,
    preregistration_from_bytes,
)
from fretsure.llm.client import (
    MAX_PROXY_TEXT_BYTES_PER_TOKEN,
    MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
    PROXY_REQUEST_TIMEOUT_SECONDS,
    LLMClient,
    LLMModelIdError,
    ProxyLLM,
    close_llm_client,
    managed_llm_client,
    snapshot_llm_model_id,
)
from fretsure.oracle.profiles import MEDIAN_HAND, Profile

PILOT_SPEC_VERSION: Final = "benchmark-v2-operational-pilot-spec@0.1.0"
PILOT_PRE_CALL_VERSION: Final = "benchmark-v2-operational-pilot-pre-call@0.1.0"
PILOT_RUN_CONFIG_VERSION: Final = "benchmark-v2-operational-pilot-run-config@0.1.0"
PILOT_PURPOSE: Final = "operational_only"
DEFAULT_PILOT_ID: Final = "benchmark-v2-operational-pilot-20260718"
DEFAULT_PILOT_BASE_SEED: Final = 20_260_718
PILOT_STUB_MODEL_ID: Final = "fretsure-task8-pilot-stub@0.1.0"
FORMAL_PREREGISTRATION_RAW_SHA256: Final = (
    "ad9129edfb47634085f7bfd5557ca76f59eb8358865a1742bfcba69fa0c1362b"
)
FORMAL_REQUESTED_MODEL_ID: Final = "gpt-5.6-sol"

PILOT_FAMILY_COUNT: Final = 2
PILOT_BARS: Final = 2
PILOT_SAMPLES_PER_FAMILY: Final = 2
PILOT_PAIR_COUNT: Final = PILOT_FAMILY_COUNT * PILOT_SAMPLES_PER_FAMILY
PILOT_EXPECTED_ROWS: Final = PILOT_PAIR_COUNT * 2

PILOT_PROPOSAL_TOKENS: Final = 2_048
PILOT_REPAIR_TOKENS: Final = REPAIR_MAX_TOKENS
PILOT_CRITIC_TOKENS: Final = CRITIC_MAX_TOKENS
PILOT_MAX_REPAIRS: Final = EXPERIMENT_MAX_REPAIR_ITERS
PILOT_AGENT_CALLS: Final = 1 + PILOT_MAX_REPAIRS + 1
PILOT_AGENT_TOKENS: Final = (
    PILOT_PROPOSAL_TOKENS + PILOT_MAX_REPAIRS * PILOT_REPAIR_TOKENS + PILOT_CRITIC_TOKENS
)

PILOT_PAIR_LOGICAL_CALLS: Final = PILOT_AGENT_CALLS + 1
PILOT_PAIR_ATTEMPTS: Final = PILOT_PAIR_LOGICAL_CALLS * 3
PILOT_PAIR_REQUESTED_OUTPUT_TOKENS: Final = PILOT_AGENT_TOKENS + PILOT_PROPOSAL_TOKENS
PILOT_PAIR_ATTEMPT_RESERVED_OUTPUT_TOKENS: Final = PILOT_PAIR_REQUESTED_OUTPUT_TOKENS * 3
PILOT_FULL_LOGICAL_CALLS: Final = PILOT_PAIR_LOGICAL_CALLS * PILOT_PAIR_COUNT
PILOT_FULL_ATTEMPTS: Final = PILOT_PAIR_ATTEMPTS * PILOT_PAIR_COUNT
PILOT_FULL_REQUESTED_OUTPUT_TOKENS: Final = PILOT_PAIR_REQUESTED_OUTPUT_TOKENS * PILOT_PAIR_COUNT
PILOT_FULL_ATTEMPT_RESERVED_OUTPUT_TOKENS: Final = (
    PILOT_PAIR_ATTEMPT_RESERVED_OUTPUT_TOKENS * PILOT_PAIR_COUNT
)
RECORDED_PROVIDER_ELAPSED_CEILING_MICROSECONDS: Final = 5_400_000_000
ACTIVE_HOST_DEADLINE_MICROSECONDS: Final = 5_400_000_000
PILOT_PROVIDER_TIMEOUT_ENVELOPE_MICROSECONDS: Final = 4_026_000_000


def _reservation_wall_microseconds(logical_calls: int, attempts: int) -> int:
    return int((attempts * PROXY_REQUEST_TIMEOUT_SECONDS + logical_calls * 1.5) * 1_000_000)


PAIR_RESERVATION: Final = CompleteUnitReservation(
    PILOT_PAIR_LOGICAL_CALLS,
    PILOT_PAIR_ATTEMPTS,
    PILOT_PAIR_REQUESTED_OUTPUT_TOKENS,
    PILOT_PAIR_ATTEMPT_RESERVED_OUTPUT_TOKENS,
    PILOT_PAIR_REQUESTED_OUTPUT_TOKENS * MAX_PROXY_TEXT_BYTES_PER_TOKEN,
    PILOT_PAIR_ATTEMPTS * MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
    _reservation_wall_microseconds(PILOT_PAIR_LOGICAL_CALLS, PILOT_PAIR_ATTEMPTS),
)
RAW_RESERVATION: Final = CompleteUnitReservation(
    1,
    3,
    PILOT_PROPOSAL_TOKENS,
    PILOT_PROPOSAL_TOKENS * 3,
    PILOT_PROPOSAL_TOKENS * MAX_PROXY_TEXT_BYTES_PER_TOKEN,
    3 * MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
    _reservation_wall_microseconds(1, 3),
)

_FULL_RESPONSE_TEXT_BYTES: Final = (
    PILOT_FULL_REQUESTED_OUTPUT_TOKENS * MAX_PROXY_TEXT_BYTES_PER_TOKEN
)
_FULL_TRANSPORT_RESPONSE_BYTES: Final = PILOT_FULL_ATTEMPTS * MAX_PROXY_TRANSPORT_RESPONSE_BYTES
_MAX_JSON_BYTES: Final = 64 * 1024 * 1024
_MAX_JSONL_LINE_BYTES: Final = 4 * 1024 * 1024
_MAX_BLOBS: Final = 64
_MAX_COLLECTION_ATTEMPT: Final = 999


class PilotConfigError(ValueError):
    """A pilot spec, pre-call declaration, or invocation was invalid."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid Task 8 pilot {field}: {detail}")


def _fail(field: str, detail: str) -> NoReturn:
    raise PilotConfigError(field, detail)


def _object(value: object, field: str, keys: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict:
        _fail(field, "must be an exact object")
    result = cast(dict[str, object], value)
    if frozenset(result) != keys:
        _fail(field, "must contain the exact keys")
    return result


def _integer(
    value: object,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(field, f"must be an exact integer in {minimum}..{maximum}")
    return value


def _text(value: object, field: str, *, maximum: int = 256) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= maximum
        or not value.isprintable()
    ):
        _fail(field, f"must be one printable exact string of 1..{maximum} characters")
    return value


def _sha256(value: object, field: str) -> str:
    text = _text(value, field, maximum=64)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        _fail(field, "must be one lowercase SHA-256 digest")
    return text


def _git_sha(value: object, field: str) -> str:
    text = _text(value, field, maximum=40)
    if len(text) != 40 or any(character not in "0123456789abcdef" for character in text):
        _fail(field, "must be one lowercase 40-character Git SHA declaration")
    return text


def _identifier(value: object, field: str) -> str:
    try:
        return require_identifier(value, path=field)
    except ValueError:
        _fail(field, "must be one bounded benchmark identifier")


def _attempt(value: object) -> int:
    return _integer(
        value,
        "collection_attempt",
        minimum=1,
        maximum=_MAX_COLLECTION_ATTEMPT,
    )


class PilotArm(StrEnum):
    AGENT = "agent"
    RAW = "raw"


@dataclass(frozen=True, slots=True)
class PilotScheduledUnit:
    schedule_index: int
    item_position: int
    item_id: str
    sample_index: int
    arm: PilotArm

    def __post_init__(self) -> None:
        _integer(self.schedule_index, "schedule.schedule_index", minimum=0, maximum=7)
        _integer(self.item_position, "schedule.item_position", minimum=0, maximum=1)
        _identifier(self.item_id, "schedule.item_id")
        _integer(self.sample_index, "schedule.sample_index", minimum=0, maximum=1)
        if type(self.arm) is not PilotArm:
            _fail("schedule.arm", "must be an exact pilot arm")


def _unit_to_dict(unit: PilotScheduledUnit) -> dict[str, object]:
    return {
        "arm": unit.arm.value,
        "item_id": unit.item_id,
        "item_position": unit.item_position,
        "sample_index": unit.sample_index,
        "schedule_index": unit.schedule_index,
    }


def _unit_from_dict(value: object, index: int) -> PilotScheduledUnit:
    obj = _object(
        value,
        f"schedule[{index}]",
        frozenset({"arm", "item_id", "item_position", "sample_index", "schedule_index"}),
    )
    try:
        arm = PilotArm(_text(obj["arm"], f"schedule[{index}].arm", maximum=5))
    except (TypeError, ValueError):
        _fail(f"schedule[{index}].arm", "is unsupported")
    return PilotScheduledUnit(
        _integer(obj["schedule_index"], f"schedule[{index}].schedule_index", minimum=0, maximum=7),
        _integer(obj["item_position"], f"schedule[{index}].item_position", minimum=0, maximum=1),
        _identifier(obj["item_id"], f"schedule[{index}].item_id"),
        _integer(obj["sample_index"], f"schedule[{index}].sample_index", minimum=0, maximum=1),
        arm,
    )


@dataclass(frozen=True, slots=True)
class PilotSpec:
    """One canonical operational-pilot preregistration."""

    wire_json: bytes

    def __post_init__(self) -> None:
        if type(self.wire_json) is not bytes:
            _fail("wire_json", "must be exact bytes")
        try:
            parsed = parse_canonical_json_bytes(self.wire_json)
        except ValueError as error:
            raise PilotConfigError("wire_json", "must be canonical benchmark JSON") from error
        if type(parsed) is not dict:
            _fail("wire_json", "must encode one canonical object")

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], parse_canonical_json_bytes(self.wire_json))

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.wire_json).hexdigest()

    @property
    def pilot_id(self) -> str:
        return cast(str, self.to_dict()["pilot_id"])

    @property
    def base_seed(self) -> int:
        corpus = cast(dict[str, object], self.to_dict()["corpus"])
        return cast(int, corpus["base_seed"])

    @property
    def items(self) -> tuple[CorpusItem, ...]:
        corpus = cast(dict[str, object], self.to_dict()["corpus"])
        return corpus_from_dict(corpus["snapshot"])

    @property
    def corpus_sha256(self) -> str:
        corpus = cast(dict[str, object], self.to_dict()["corpus"])
        return cast(str, corpus["corpus_sha256"])

    @property
    def schedule(self) -> tuple[PilotScheduledUnit, ...]:
        raw = cast(list[object], self.to_dict()["schedule"])
        return tuple(_unit_from_dict(value, index) for index, value in enumerate(raw))

    @property
    def requested_model_id(self) -> str:
        model = cast(dict[str, object], self.to_dict()["model"])
        return cast(str, model["requested_model_id"])

    def run_id_for_attempt(self, collection_attempt: object) -> str:
        exact_attempt = _attempt(collection_attempt)
        return f"{self.pilot_id}-attempt-{exact_attempt:03d}"

    @property
    def stub_run_id(self) -> str:
        return f"{self.pilot_id}-stub-attempt-001"


def goal_at_source_tempo(goal: ArrangeGoal, item: CorpusItem) -> ArrangeGoal:
    """Detach one goal with the source tempo used by agent and raw arms."""

    if type(goal) is not ArrangeGoal or type(item) is not CorpusItem:
        _fail("goal", "requires exact ArrangeGoal and CorpusItem values")
    return replace(goal, tempo_bpm=item.ir.meta.tempo_bpm, extras=dict(goal.extras))


def _pilot_items(base_seed: int) -> tuple[CorpusItem, ...]:
    generated = build_primary_procedural_corpus(
        ProceduralCorpusConfig(
            family_count=PILOT_FAMILY_COUNT,
            base_seed=base_seed,
            bars=PILOT_BARS,
            split="pilot",
        )
    )
    result: list[CorpusItem] = []
    for index, item in enumerate(generated):
        suffix = f"{index:06d}"
        provenance = item.provenance
        if provenance is None or provenance.generator is None:
            _fail("corpus", "generated pilot item lost generator provenance")
        result.append(
            replace(
                item,
                item_id=f"pilot-proc-v2-{suffix}",
                family_id=f"pilot-proc-family-v2-{suffix}",
                cluster_id=f"pilot-proc-cluster-v2-{suffix}",
                canary=f"fretsure-task8-pilot-canary-{suffix}-{provenance.source_sha256}",
            )
        )
    return tuple(result)


def _formal_items(preregistration: BenchmarkPreregistration) -> tuple[CorpusItem, ...]:
    wire = preregistration.to_dict()
    corpus = cast(dict[str, object], wire["corpus"])
    return corpus_from_dict(corpus["snapshot"])


def _prove_disjoint(
    pilot_items: tuple[CorpusItem, ...],
    formal_items: tuple[CorpusItem, ...],
) -> None:
    for field, pilot_values, formal_values in (
        (
            "item_id",
            {item.item_id for item in pilot_items},
            {item.item_id for item in formal_items},
        ),
        (
            "family_id",
            {item.family_id for item in pilot_items},
            {item.family_id for item in formal_items},
        ),
        (
            "cluster_id",
            {item.cluster_id for item in pilot_items},
            {item.cluster_id for item in formal_items},
        ),
        (
            "notegraph_sha256",
            {notegraph_sha256(item.ir) for item in pilot_items},
            {notegraph_sha256(item.ir) for item in formal_items},
        ),
        (
            "source_sha256",
            {item.provenance.source_sha256 for item in pilot_items if item.provenance is not None},
            {item.provenance.source_sha256 for item in formal_items if item.provenance is not None},
        ),
    ):
        if pilot_values & formal_values:
            _fail(f"corpus.exclusion.{field}", "overlaps the formal preregistered corpus")


def _schedule(items: tuple[CorpusItem, ...]) -> tuple[PilotScheduledUnit, ...]:
    units: list[PilotScheduledUnit] = []
    for item_position, item in enumerate(items):
        for sample_index in range(PILOT_SAMPLES_PER_FAMILY):
            for arm in (PilotArm.AGENT, PilotArm.RAW):
                units.append(
                    PilotScheduledUnit(len(units), item_position, item.item_id, sample_index, arm)
                )
    return tuple(units)


def _reservation_to_dict(value: CompleteUnitReservation) -> dict[str, int]:
    return {
        "attempt_reserved_output_tokens": value.attempt_reserved_output_tokens,
        "attempts": value.attempts,
        "logical_calls": value.logical_calls,
        "provider_timeout_envelope_microseconds": value.wall_microseconds,
        "requested_output_tokens": value.requested_output_tokens,
        "response_text_bytes": value.response_text_bytes,
        "transport_response_bytes": value.transport_response_bytes,
    }


def _budget_wire() -> dict[str, object]:
    return {
        "full": {
            "active_host_deadline_microseconds": ACTIVE_HOST_DEADLINE_MICROSECONDS,
            "attempt_reserved_output_tokens": PILOT_FULL_ATTEMPT_RESERVED_OUTPUT_TOKENS,
            "attempts": PILOT_FULL_ATTEMPTS,
            "logical_calls": PILOT_FULL_LOGICAL_CALLS,
            "provider_timeout_envelope_microseconds": (
                PILOT_PROVIDER_TIMEOUT_ENVELOPE_MICROSECONDS
            ),
            "recorded_provider_call_elapsed_ceiling_microseconds": (
                RECORDED_PROVIDER_ELAPSED_CEILING_MICROSECONDS
            ),
            "requested_output_tokens": PILOT_FULL_REQUESTED_OUTPUT_TOKENS,
            "response_text_bytes": _FULL_RESPONSE_TEXT_BYTES,
            "transport_response_bytes": _FULL_TRANSPORT_RESPONSE_BYTES,
        },
        "pair_reservation": _reservation_to_dict(PAIR_RESERVATION),
        "raw_reservation": _reservation_to_dict(RAW_RESERVATION),
    }


def _spec_wire(
    *,
    pilot_id: str,
    base_seed: int,
) -> dict[str, object]:
    items = _pilot_items(base_seed)
    goal = ArrangeGoal()
    token_preflight: list[dict[str, object]] = []
    for item in items:
        proposal_tokens = proposal_output_token_budget(item.ir)
        raw_tokens = build_raw_baseline_request(
            item.ir,
            goal_at_source_tempo(goal, item),
            MEDIAN_HAND,
        ).max_tokens
        if proposal_tokens != PILOT_PROPOSAL_TOKENS or raw_tokens != PILOT_PROPOSAL_TOKENS:
            _fail(
                f"corpus.items[{item.item_id}].max_tokens",
                "proposal and raw calls must both retain the 2,048-token cap",
            )
        token_preflight.append(
            {
                "item_id": item.item_id,
                "proposal_max_tokens": proposal_tokens,
                "raw_max_tokens": raw_tokens,
            }
        )

    return {
        "budget": _budget_wire(),
        "corpus": {
            "bars": PILOT_BARS,
            "base_seed": base_seed,
            "corpus_sha256": corpus_sha256(items),
            "family_count": PILOT_FAMILY_COUNT,
            "samples_per_family": PILOT_SAMPLES_PER_FAMILY,
            "snapshot": corpus_to_dict(items),
            "split": "pilot",
            "token_preflight": token_preflight,
        },
        "formal_preregistration_raw_sha256": FORMAL_PREREGISTRATION_RAW_SHA256,
        "model": {
            "allowed_returned_model_id": FORMAL_REQUESTED_MODEL_ID,
            "requested_model_id": FORMAL_REQUESTED_MODEL_ID,
            "returned_model_rule": "exact_equal",
        },
        "pilot_id": pilot_id,
        "purpose": PILOT_PURPOSE,
        "schedule": [_unit_to_dict(unit) for unit in _schedule(items)],
        "schema": PILOT_SPEC_VERSION,
        "timing": {
            "active_host_deadline_microseconds": ACTIVE_HOST_DEADLINE_MICROSECONDS,
            "active_host_scope": "single_invocation_non_cumulative_across_resume",
            "provider_timeout_envelope_microseconds": (
                PILOT_PROVIDER_TIMEOUT_ENVELOPE_MICROSECONDS
            ),
            "recorded_provider_call_elapsed_ceiling_microseconds": (
                RECORDED_PROVIDER_ELAPSED_CEILING_MICROSECONDS
            ),
        },
    }


def build_pilot_spec(
    formal_preregistration: BenchmarkPreregistration,
    *,
    pilot_id: str = DEFAULT_PILOT_ID,
    base_seed: int = DEFAULT_PILOT_BASE_SEED,
) -> PilotSpec:
    if type(formal_preregistration) is not BenchmarkPreregistration:
        _fail("formal_preregistration", "must be an exact BenchmarkPreregistration")
    exact_pilot_id = _identifier(pilot_id, "pilot_id")
    exact_seed = _integer(base_seed, "base_seed", minimum=0, maximum=(1 << 63) - 1)
    if exact_pilot_id != DEFAULT_PILOT_ID:
        _fail("pilot_id", "must equal the frozen Task 8 pilot id")
    if exact_seed != DEFAULT_PILOT_BASE_SEED:
        _fail("base_seed", "must equal the frozen Task 8 pilot seed")
    if hashlib.sha256(formal_preregistration.wire_json).hexdigest() != (
        FORMAL_PREREGISTRATION_RAW_SHA256
    ):
        _fail("formal_preregistration", "does not match the frozen Task 7 preregistration")
    formal_wire = formal_preregistration.to_dict()
    formal_model = cast(dict[str, object], formal_wire["model_and_prompts"])
    if formal_model["requested_model"] != FORMAL_REQUESTED_MODEL_ID:
        _fail("formal_preregistration.model", "does not match the frozen pilot model")
    _prove_disjoint(_pilot_items(exact_seed), _formal_items(formal_preregistration))
    return PilotSpec(
        canonical_json_bytes(
            _spec_wire(
                pilot_id=exact_pilot_id,
                base_seed=exact_seed,
            )
        )
    )


def pilot_spec_from_dict(value: object) -> PilotSpec:
    obj = _object(
        value,
        "$",
        frozenset(
            {
                "budget",
                "corpus",
                "formal_preregistration_raw_sha256",
                "model",
                "pilot_id",
                "purpose",
                "schedule",
                "schema",
                "timing",
            }
        ),
    )
    if obj["schema"] != PILOT_SPEC_VERSION:
        _fail("schema", "has the wrong version")
    if obj["purpose"] != PILOT_PURPOSE:
        _fail("purpose", "must remain operational_only")
    if (
        _sha256(
            obj["formal_preregistration_raw_sha256"],
            "formal_preregistration_raw_sha256",
        )
        != FORMAL_PREREGISTRATION_RAW_SHA256
    ):
        _fail(
            "formal_preregistration_raw_sha256",
            "does not bind the frozen Task 7 preregistration",
        )
    corpus = _object(
        obj["corpus"],
        "corpus",
        frozenset(
            {
                "bars",
                "base_seed",
                "corpus_sha256",
                "family_count",
                "samples_per_family",
                "snapshot",
                "split",
                "token_preflight",
            }
        ),
    )
    pilot_id = _identifier(obj["pilot_id"], "pilot_id")
    base_seed = _integer(corpus["base_seed"], "corpus.base_seed", minimum=0, maximum=(1 << 63) - 1)
    if pilot_id != DEFAULT_PILOT_ID:
        _fail("pilot_id", "must equal the frozen Task 8 pilot id")
    if base_seed != DEFAULT_PILOT_BASE_SEED:
        _fail("corpus.base_seed", "must equal the frozen Task 8 pilot seed")
    expected = _spec_wire(pilot_id=pilot_id, base_seed=base_seed)
    if obj != expected:
        _fail("$", "content differs from the frozen deterministic pilot spec")
    return PilotSpec(canonical_json_bytes(obj))


def pilot_spec_from_bytes(data: object) -> PilotSpec:
    if type(data) is not bytes:
        _fail("$", "must be exact bytes")
    try:
        value = parse_canonical_json_bytes(data)
    except ValueError as error:
        raise PilotConfigError("$", "must be canonical benchmark JSON") from error
    return pilot_spec_from_dict(value)


@dataclass(frozen=True, slots=True)
class PilotPreCallConfig:
    """Canonical attempt-local live declaration; parsing is not user authorization."""

    wire_json: bytes

    def __post_init__(self) -> None:
        if type(self.wire_json) is not bytes:
            _fail("wire_json", "must be exact bytes")
        parsed = parse_canonical_json_bytes(self.wire_json)
        if type(parsed) is not dict:
            _fail("wire_json", "must encode one canonical object")

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], parse_canonical_json_bytes(self.wire_json))

    @property
    def spec(self) -> PilotSpec:
        return pilot_spec_from_dict(self.to_dict()["pilot_spec"])

    @property
    def run_id(self) -> str:
        return cast(str, self.to_dict()["run_id"])

    @property
    def collection_attempt(self) -> int:
        return cast(int, self.to_dict()["collection_attempt"])

    @property
    def requested_model_id(self) -> str:
        model = cast(dict[str, object], self.to_dict()["model"])
        return cast(str, model["requested_model_id"])

    @property
    def analysis_code_sha256(self) -> str:
        execution = cast(dict[str, object], self.to_dict()["execution"])
        return cast(str, execution["analysis_code_sha256"])

    @property
    def has_priced_ceiling(self) -> bool:
        cost = cast(dict[str, object], self.to_dict()["cost"])
        return cost["status"] == "available"

    @property
    def maximum_spend_microunits(self) -> int | None:
        cost = cast(dict[str, object], self.to_dict()["cost"])
        value = cost["maximum_spend_microunits"]
        return None if value is None else cast(int, value)


class _PricingContractView(Protocol):
    wire_json: bytes
    raw_sha256: str
    billing_model_id: str
    currency: str
    ceilings: dict[str, int]

    def to_dict(self) -> dict[str, object]: ...


class _OperationalUsageView(Protocol):
    wire_json: bytes


_BUDGET_GATE_MODULE: ModuleType | None = None


def load_budget_gate_module() -> ModuleType:
    """Load the adjacent offline budget tool without requiring ``scripts`` packaging."""

    global _BUDGET_GATE_MODULE
    if _BUDGET_GATE_MODULE is not None:
        return _BUDGET_GATE_MODULE
    path = Path(__file__).with_name("task8_budget_gate.py")
    module_spec = importlib.util.spec_from_file_location("task8_budget_gate", path)
    if module_spec is None or module_spec.loader is None:
        _fail("pricing_contract", "could not load the adjacent budget-gate module")
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = module
    module_spec.loader.exec_module(module)
    _BUDGET_GATE_MODULE = module
    return module


def _pricing_from_bytes(data: bytes) -> _PricingContractView:
    function = cast(
        Callable[[object], _PricingContractView],
        load_budget_gate_module().token_pricing_contract_from_bytes,
    )
    return function(data)


def _pricing_from_dict(value: object) -> _PricingContractView:
    function = cast(
        Callable[[object], _PricingContractView],
        load_budget_gate_module().token_pricing_contract_from_dict,
    )
    return function(value)


def _pilot_worst_case(pricing: _PricingContractView) -> dict[str, object]:
    function = cast(
        Callable[[object], dict[str, object]],
        load_budget_gate_module().pilot_worst_case_budget,
    )
    worst = function(pricing)
    resources = _object(
        worst.get("resources"),
        "cost.worst_case.resources",
        frozenset(
            {
                "attempt_reserved_output_tokens",
                "attempts",
                "host_wall_availability",
                "host_wall_microseconds",
                "logical_calls",
                "max_retries",
                "paired_samples",
                "provider_timeout_envelope_microseconds",
                "requested_output_tokens",
                "runner_recorded_elapsed_ceiling_microseconds",
                "stage_totals",
            }
        ),
    )
    expected = {
        "attempt_reserved_output_tokens": PILOT_FULL_ATTEMPT_RESERVED_OUTPUT_TOKENS,
        "attempts": PILOT_FULL_ATTEMPTS,
        "host_wall_availability": "available",
        "host_wall_microseconds": ACTIVE_HOST_DEADLINE_MICROSECONDS,
        "logical_calls": PILOT_FULL_LOGICAL_CALLS,
        "max_retries": PILOT_FULL_ATTEMPTS - PILOT_FULL_LOGICAL_CALLS,
        "paired_samples": PILOT_PAIR_COUNT,
        "provider_timeout_envelope_microseconds": (
            PILOT_PROVIDER_TIMEOUT_ENVELOPE_MICROSECONDS
        ),
        "requested_output_tokens": PILOT_FULL_REQUESTED_OUTPUT_TOKENS,
        "runner_recorded_elapsed_ceiling_microseconds": (
            RECORDED_PROVIDER_ELAPSED_CEILING_MICROSECONDS
        ),
    }
    if any(resources[name] != value for name, value in expected.items()):
        _fail(
            "cost.worst_case.resources",
            "does not match the frozen pilot resource envelope",
        )
    return worst


def _unavailable_cost_wire() -> dict[str, object]:
    return {
        "currency": None,
        "maximum_spend_microunits": None,
        "pricing_contract": None,
        "pricing_contract_raw_sha256": None,
        "status": "cost_contract_unavailable",
        "worst_case": None,
    }


def _available_cost_wire(spec: PilotSpec, pricing_contract_json: bytes) -> dict[str, object]:
    pricing = _pricing_from_bytes(pricing_contract_json)
    if pricing.billing_model_id != spec.requested_model_id:
        _fail("cost.pricing_contract.billing_model_id", "does not equal the pilot model")
    if pricing.ceilings.get("output_tokens", -1) < PILOT_PROPOSAL_TOKENS:
        _fail(
            "cost.pricing_contract.billable_token_ceiling_per_attempt.output_tokens",
            "must cover every 2,048-token proposal/raw request",
        )
    worst = _pilot_worst_case(pricing)
    maximum = _integer(
        worst.get("cost_microunits"),
        "cost.worst_case.cost_microunits",
        minimum=0,
        maximum=(1 << 63) - 1,
    )
    return {
        "currency": pricing.currency,
        "maximum_spend_microunits": maximum,
        "pricing_contract": pricing.to_dict(),
        "pricing_contract_raw_sha256": pricing.raw_sha256,
        "status": "available",
        "worst_case": worst,
    }


def _validate_cost(value: object, spec: PilotSpec) -> dict[str, object]:
    cost = _object(
        value,
        "cost",
        frozenset(
            {
                "currency",
                "maximum_spend_microunits",
                "pricing_contract",
                "pricing_contract_raw_sha256",
                "status",
                "worst_case",
            }
        ),
    )
    status = cost["status"]
    if status == "cost_contract_unavailable":
        if any(
            cost[field] is not None
            for field in (
                "currency",
                "maximum_spend_microunits",
                "pricing_contract",
                "pricing_contract_raw_sha256",
                "worst_case",
            )
        ):
            _fail("cost", "unavailable cost fields must all be null")
        return cost
    if status != "available":
        _fail("cost.status", "must be available or cost_contract_unavailable")
    pricing = _pricing_from_dict(cost["pricing_contract"])
    if pricing.billing_model_id != spec.requested_model_id:
        _fail("cost.pricing_contract.billing_model_id", "does not equal the pilot model")
    if pricing.ceilings.get("output_tokens", -1) < PILOT_PROPOSAL_TOKENS:
        _fail(
            "cost.pricing_contract.billable_token_ceiling_per_attempt.output_tokens",
            "must cover every 2,048-token proposal/raw request",
        )
    if (
        _sha256(
            cost["pricing_contract_raw_sha256"],
            "cost.pricing_contract_raw_sha256",
        )
        != pricing.raw_sha256
    ):
        _fail("cost.pricing_contract_raw_sha256", "does not bind the canonical contract")
    if cost["currency"] != pricing.currency:
        _fail("cost.currency", "does not equal the pricing contract currency")
    expected_worst = _pilot_worst_case(pricing)
    if cost["worst_case"] != expected_worst:
        _fail("cost.worst_case", "does not equal the mechanically recomputed pilot worst case")
    expected_maximum = _integer(
        expected_worst.get("cost_microunits"),
        "cost.worst_case.cost_microunits",
        minimum=0,
        maximum=(1 << 63) - 1,
    )
    actual_maximum = _integer(
        cost["maximum_spend_microunits"],
        "cost.maximum_spend_microunits",
        minimum=0,
        maximum=(1 << 63) - 1,
    )
    if actual_maximum != expected_maximum:
        _fail(
            "cost.maximum_spend_microunits",
            "must exactly equal the mechanically recomputed pilot worst case",
        )
    return cost


def build_pilot_pre_call_config(
    spec: PilotSpec,
    *,
    collection_attempt: int,
    execution_git_sha: str,
    analysis_code_sha256: str,
    uv_lock_sha256: str,
    pricing_contract_json: bytes | None = None,
) -> PilotPreCallConfig:
    if type(spec) is not PilotSpec:
        _fail("spec", "must be an exact PilotSpec")
    attempt = _attempt(collection_attempt)
    wire: dict[str, object] = {
        "collection_attempt": attempt,
        "cost": (
            _unavailable_cost_wire()
            if pricing_contract_json is None
            else _available_cost_wire(spec, pricing_contract_json)
        ),
        "execution": {
            "analysis_code_sha256": analysis_code_sha256,
            "execution_git_sha": execution_git_sha,
            "uv_lock_sha256": uv_lock_sha256,
        },
        "mode": "live_operational_pilot",
        "model": {
            "allowed_returned_model_id": spec.requested_model_id,
            "requested_model_id": spec.requested_model_id,
            "returned_model_rule": "exact_equal",
        },
        "pilot_spec": spec.to_dict(),
        "pilot_spec_raw_sha256": spec.sha256,
        "run_id": spec.run_id_for_attempt(attempt),
        "schema": PILOT_PRE_CALL_VERSION,
    }
    return pilot_pre_call_config_from_dict(wire)


def pilot_pre_call_config_from_dict(value: object) -> PilotPreCallConfig:
    obj = _object(
        value,
        "$",
        frozenset(
            {
                "collection_attempt",
                "cost",
                "execution",
                "mode",
                "model",
                "pilot_spec",
                "pilot_spec_raw_sha256",
                "run_id",
                "schema",
            }
        ),
    )
    if obj["schema"] != PILOT_PRE_CALL_VERSION:
        _fail("schema", "has the wrong version")
    if obj["mode"] != "live_operational_pilot":
        _fail("mode", "must equal live_operational_pilot")
    spec = pilot_spec_from_dict(obj["pilot_spec"])
    if _sha256(obj["pilot_spec_raw_sha256"], "pilot_spec_raw_sha256") != spec.sha256:
        _fail("pilot_spec_raw_sha256", "does not bind the embedded pilot spec")
    attempt = _attempt(obj["collection_attempt"])
    if obj["run_id"] != spec.run_id_for_attempt(attempt):
        _fail("run_id", "does not equal the pilot id derived from collection_attempt")
    execution = _object(
        obj["execution"],
        "execution",
        frozenset({"analysis_code_sha256", "execution_git_sha", "uv_lock_sha256"}),
    )
    _git_sha(execution["execution_git_sha"], "execution.execution_git_sha")
    _sha256(execution["analysis_code_sha256"], "execution.analysis_code_sha256")
    _sha256(execution["uv_lock_sha256"], "execution.uv_lock_sha256")
    model = _object(
        obj["model"],
        "model",
        frozenset({"allowed_returned_model_id", "requested_model_id", "returned_model_rule"}),
    )
    if model != {
        "allowed_returned_model_id": spec.requested_model_id,
        "requested_model_id": spec.requested_model_id,
        "returned_model_rule": "exact_equal",
    }:
        _fail("model", "does not match the pilot's formal exact-model binding")
    _validate_cost(obj["cost"], spec)
    return PilotPreCallConfig(canonical_json_bytes(obj))


def pilot_pre_call_config_from_bytes(data: object) -> PilotPreCallConfig:
    if type(data) is not bytes:
        _fail("$", "must be exact bytes")
    try:
        value = parse_canonical_json_bytes(data)
    except ValueError as error:
        raise PilotConfigError("$", "must be canonical benchmark JSON") from error
    return pilot_pre_call_config_from_dict(value)


def _validated_pre_call_config(config: object) -> PilotPreCallConfig:
    if type(config) is not PilotPreCallConfig:
        _fail("pre_call_config", "must be an exact PilotPreCallConfig")
    exact = config
    return pilot_pre_call_config_from_dict(exact.to_dict())


def require_pilot_live_authorization(config: PilotPreCallConfig) -> None:
    """Require a priced declaration; this parser does not prove human authorization."""

    validated = _validated_pre_call_config(config)
    if not validated.has_priced_ceiling:
        _fail("cost", "live pilot requires an externally accepted priced spend ceiling")


def require_explicit_spend_confirmation(
    config: PilotPreCallConfig,
    authorized_maximum_spend_microunits: object,
) -> int:
    """Match one caller-supplied ceiling; this does not prove who supplied it."""

    validated = _validated_pre_call_config(config)
    if not validated.has_priced_ceiling:
        _fail("cost", "live pilot requires an externally accepted priced spend ceiling")
    expected = validated.maximum_spend_microunits
    if expected is None:  # pragma: no cover - authorization helper invariant
        raise AssertionError("priced config must expose its maximum spend")
    actual = _integer(
        authorized_maximum_spend_microunits,
        "authorized_maximum_spend_microunits",
        minimum=0,
        maximum=(1 << 63) - 1,
    )
    if actual != expected:
        _fail(
            "authorized_maximum_spend_microunits",
            "must exactly equal the pre-call mechanical maximum",
        )
    return actual


def _limits() -> ArtifactLimits:
    return ArtifactLimits(
        max_rows=PILOT_EXPECTED_ROWS,
        max_blobs=_MAX_BLOBS,
        max_calls=PILOT_FULL_LOGICAL_CALLS,
        max_attempts=PILOT_FULL_ATTEMPTS,
        max_json_bytes=_MAX_JSON_BYTES,
        max_jsonl_line_bytes=_MAX_JSONL_LINE_BYTES,
        max_requested_output_tokens=PILOT_FULL_REQUESTED_OUTPUT_TOKENS,
        max_attempt_reserved_output_tokens=PILOT_FULL_ATTEMPT_RESERVED_OUTPUT_TOKENS,
        max_response_text_bytes=_FULL_RESPONSE_TEXT_BYTES,
        max_transport_response_bytes=_FULL_TRANSPORT_RESPONSE_BYTES,
        max_wall_microseconds=RECORDED_PROVIDER_ELAPSED_CEILING_MICROSECONDS,
        complete_unit_reservation=PAIR_RESERVATION,
    )


def _row_key(unit: PilotScheduledUnit) -> RowKey:
    row_type = RowType.CANDIDATE if unit.arm is PilotArm.AGENT else RowType.RAW
    return RowKey(
        row_type,
        unit.item_id,
        unit.sample_index,
        unit.sample_index,
        sample_pair_id(unit.item_id, unit.sample_index),
    )


def _manifest_parameters(
    spec: PilotSpec,
    pre_call_config: PilotPreCallConfig | None,
    *,
    requested_model_id: str,
) -> dict[str, object]:
    pre_call: dict[str, object] | None = None
    if pre_call_config is not None:
        pre_call = pre_call_config.to_dict()
        del pre_call["pilot_spec"]
    return {
        "model": {
            "allowed_returned_model_id": requested_model_id,
            "requested_model_id": requested_model_id,
            "returned_model_rule": "exact_equal",
        },
        "pilot_spec": {"raw_sha256": spec.sha256, "wire": spec.to_dict()},
        "pre_call": pre_call,
        "purpose": PILOT_PURPOSE,
        "schema": PILOT_RUN_CONFIG_VERSION,
    }


@dataclass(frozen=True, slots=True)
class PilotContext:
    spec: PilotSpec
    pre_call_config: PilotPreCallConfig | None
    manifest: BenchmarkManifest
    requested_model_id: str
    stub: bool


def _build_context(
    spec: PilotSpec,
    *,
    pre_call_config: PilotPreCallConfig | None,
    stub: bool,
) -> PilotContext:
    requested_model_id = PILOT_STUB_MODEL_ID if stub else spec.requested_model_id
    run_id = spec.stub_run_id if stub else cast(PilotPreCallConfig, pre_call_config).run_id
    analysis_sha = (
        hashlib.sha256(b"fretsure:task8-pilot-stub-analysis@0.1.0\0" + spec.wire_json).hexdigest()
        if stub
        else cast(PilotPreCallConfig, pre_call_config).analysis_code_sha256
    )
    manifest = build_manifest(
        run_id=run_id,
        corpus_sha256=spec.corpus_sha256,
        analysis_code_sha256=analysis_sha,
        stub=stub,
        expected_rows=tuple(
            sorted((_row_key(unit) for unit in spec.schedule), key=lambda key: key.sort_key)
        ),
        limits=_limits(),
        parameters=_manifest_parameters(
            spec,
            pre_call_config,
            requested_model_id=requested_model_id,
        ),
    )
    return PilotContext(spec, pre_call_config, manifest, requested_model_id, stub)


def build_pilot_stub_context(spec: PilotSpec) -> PilotContext:
    if type(spec) is not PilotSpec:
        _fail("spec", "must be an exact PilotSpec")
    return _build_context(spec, pre_call_config=None, stub=True)


def build_pilot_live_context(config: PilotPreCallConfig) -> PilotContext:
    validated = _validated_pre_call_config(config)
    return _build_context(validated.spec, pre_call_config=validated, stub=False)


class _PilotStubLLM:
    def __init__(self, model_id: str) -> None:
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1_024,
        temperature: float = 0.0,
    ) -> str:
        del system, user, max_tokens, temperature
        context = current_call_context()
        if context is not None and context.stage is CallStage.CRITIC:
            return '{"bass_motion":0.6,"overall":0.8,"texture":0.5,"voice_leading":0.7}'
        if context is not None and context.stage is CallStage.RAW:
            return '{"capo":0,"notes":[],"tuning":[40,45,50,55,59,64]}'
        if context is not None and context.stage is CallStage.REPAIR:
            return '{"edits":[]}'
        return '{"notes":[{"duration":"1","onset":"0","pitch":64,"voice":"melody"}]}'

    def close(self) -> None:
        return None


LLMFactory = Callable[[], LLMClient]


def _create_clients(
    context: PilotContext,
    agent_factory: LLMFactory | None,
    raw_factory: LLMFactory | None,
) -> tuple[LLMClient, LLMClient]:
    if (agent_factory is None) is not (raw_factory is None):
        _fail("llm_factory", "agent and raw factories must be supplied together")
    if context.stub:
        if agent_factory is not None:
            _fail("llm_factory", "stub pilot does not accept client injection")

        def make_stub() -> LLMClient:
            return _PilotStubLLM(context.requested_model_id)

        make_agent: LLMFactory = make_stub
        make_raw: LLMFactory = make_stub
    else:

        def make_live() -> LLMClient:
            return ProxyLLM(context.requested_model_id)

        make_agent = make_live if agent_factory is None else agent_factory
        make_raw = make_live if raw_factory is None else raw_factory
    if not callable(make_agent) or not callable(make_raw):
        _fail("llm_factory", "factories must be callable")
    agent = make_agent()
    try:
        raw = make_raw()
    except BaseException:
        close_llm_client(agent)
        raise
    if agent is raw:
        close_llm_client(agent)
        _fail("llm_factory", "factories must return distinct clients")
    try:
        agent_model = snapshot_llm_model_id(agent)
        raw_model = snapshot_llm_model_id(raw)
        if agent_model != context.requested_model_id or raw_model != context.requested_model_id:
            _fail("llm_model_id", "both clients must match the model frozen in the manifest")
    except BaseException as error:
        try:
            close_llm_client(raw)
        finally:
            close_llm_client(agent)
        if isinstance(error, LLMModelIdError):
            raise PilotConfigError("llm_model_id", str(error)) from None
        raise
    return agent, raw


def _ledger(store: ArtifactStore) -> ObservationLedger:
    sink = store.sink
    return ObservationLedger(
        sink.intents,
        sink.results,
        sink.attempt_intents,
        sink.attempt_results,
    )


def _candidate_row(
    run_id: str,
    item: CorpusItem,
    trajectory: CandidateTrajectory,
    ledger: ObservationLedger,
    profile: Profile,
) -> tuple[BenchmarkRow, tuple[BlobRecord, ...]]:
    joined = report_module._joined_calls(ledger)
    row, blobs = report_module._candidate_row_bundle(run_id, item, trajectory, joined, profile)
    return row, blobs


def _raw_row(
    run_id: str,
    item: CorpusItem,
    outcome: RawLLMOutcome,
    ledger: ObservationLedger,
    profile: Profile,
) -> tuple[BenchmarkRow, tuple[BlobRecord, ...]]:
    joined = report_module._joined_calls(ledger)
    row, blobs = report_module._raw_row_bundle(run_id, item, outcome, joined, profile)
    return row, blobs


def _validate_staged_prefix(store: ArtifactStore, schedule: tuple[PilotScheduledUnit, ...]) -> None:
    actual = tuple(unit.row.key for unit in store.completed_units)
    expected = tuple(_row_key(unit) for unit in schedule[: len(actual)])
    if actual != expected:
        _fail("staging.rows", "must form the exact pilot schedule prefix")


def _read_clock_ns(clock: Callable[[], int], field: str) -> int:
    try:
        value = clock()
    except Exception:
        _fail(field, "clock failed")
    if type(value) is not int or value < 0:
        _fail(field, "clock must return an exact nonnegative nanosecond integer")
    return value


def _write_canonical_file(path: Path, data: bytes) -> None:
    if not isinstance(path, Path) or type(data) is not bytes:
        _fail("output_file", "requires Path and exact bytes")
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
    except FileExistsError:
        if path.read_bytes() != data:
            _fail("output_file", "already exists with different bytes")
    except OSError as error:
        raise PilotConfigError("output_file", "could not publish canonical bytes") from error


def _active_elapsed_microseconds(
    clock: Callable[[], int],
    start_ns: int,
    *,
    enforce: bool,
) -> int:
    now = _read_clock_ns(clock, "host_clock_ns")
    if now < start_ns:
        _fail("active_host_elapsed_microseconds", "monotonic clock moved backwards")
    elapsed = (now - start_ns) // 1_000
    if enforce and elapsed > ACTIVE_HOST_DEADLINE_MICROSECONDS:
        _fail(
            "active_host_elapsed_microseconds",
            "this active invocation exceeded its 90-minute host deadline",
        )
    return elapsed


@dataclass(frozen=True, slots=True)
class AvailabilityCount:
    available_calls: int
    unavailable_calls: int


@dataclass(frozen=True, slots=True)
class MetricAvailability:
    available_calls: int
    unavailable_calls: int
    total_if_complete: int | None


@dataclass(frozen=True, slots=True)
class UsageAvailability:
    input_tokens: MetricAvailability
    output_tokens: MetricAvailability
    cache_creation_input_tokens: MetricAvailability
    cache_read_input_tokens: MetricAvailability


@dataclass(frozen=True, slots=True)
class StageOperationalTotals:
    stage: str
    logical_calls: int
    retries: int
    requested_output_tokens: int
    attempt_reserved_output_tokens: int


@dataclass(frozen=True, slots=True)
class OperationalPilotSummary:
    run_id: str
    logical_calls: int
    provider_attempts: int
    retries: int
    returned_model: AvailabilityCount
    returned_model_ids: tuple[str, ...]
    latency: MetricAvailability
    usage: UsageAvailability
    stage_totals: tuple[StageOperationalTotals, ...]
    active_host_elapsed_microseconds: int

    def to_dict(self) -> dict[str, object]:
        def count(value: AvailabilityCount) -> dict[str, int]:
            return {
                "available_calls": value.available_calls,
                "unavailable_calls": value.unavailable_calls,
            }

        def metric(value: MetricAvailability) -> dict[str, int | None]:
            return {
                "available_calls": value.available_calls,
                "total_if_complete": value.total_if_complete,
                "unavailable_calls": value.unavailable_calls,
            }

        return {
            "active_host_elapsed_microseconds": self.active_host_elapsed_microseconds,
            "latency": metric(self.latency),
            "logical_calls": self.logical_calls,
            "provider_attempts": self.provider_attempts,
            "retries": self.retries,
            "returned_model": count(self.returned_model),
            "returned_model_ids": list(self.returned_model_ids),
            "run_id": self.run_id,
            "stage_totals": [
                {
                    "attempt_reserved_output_tokens": value.attempt_reserved_output_tokens,
                    "logical_calls": value.logical_calls,
                    "requested_output_tokens": value.requested_output_tokens,
                    "retries": value.retries,
                    "stage": value.stage,
                }
                for value in self.stage_totals
            ],
            "usage": {
                "cache_creation_input_tokens": metric(self.usage.cache_creation_input_tokens),
                "cache_read_input_tokens": metric(self.usage.cache_read_input_tokens),
                "input_tokens": metric(self.usage.input_tokens),
                "output_tokens": metric(self.usage.output_tokens),
            },
        }


def _metric(values: list[int | None]) -> MetricAvailability:
    available = [value for value in values if value is not None]
    return MetricAvailability(
        len(available),
        len(values) - len(available),
        sum(available) if len(available) == len(values) else None,
    )


def _stage_totals(rows: tuple[BenchmarkRow, ...]) -> tuple[StageOperationalTotals, ...]:
    if not rows:
        return ()
    totals = {
        stage: {
            "logical_calls": 0,
            "retries": 0,
            "requested_output_tokens": 0,
            "attempt_reserved_output_tokens": 0,
        }
        for stage in ("proposal", "repair", "critic", "raw")
    }
    for row in rows:
        payload = row.payload
        if row.key.row_type is RowType.CANDIDATE:
            work = cast(dict[str, object], payload["work"])
            calls = cast(list[dict[str, object]], work["calls"])
        elif row.key.row_type is RowType.RAW:
            outcome = cast(dict[str, object], payload["outcome"])
            calls = [cast(dict[str, object], outcome["call"])]
        else:
            _fail("rows", "pilot rows may contain only candidate and raw units")
        for call in calls:
            stage = cast(str, call["stage"])
            if stage not in totals:
                _fail("rows.calls.stage", "is not a pilot provider-call stage")
            current = totals[stage]
            current["logical_calls"] += 1
            current["retries"] += cast(int, call["retry_count"])
            current["requested_output_tokens"] += cast(int, call["requested_output_tokens"])
            current["attempt_reserved_output_tokens"] += cast(
                int, call["attempt_reserved_output_tokens"]
            )
    return tuple(
        StageOperationalTotals(
            stage,
            value["logical_calls"],
            value["retries"],
            value["requested_output_tokens"],
            value["attempt_reserved_output_tokens"],
        )
        for stage, value in totals.items()
    )


def build_operational_summary(
    observations: SanitizedObservations,
    *,
    rows: tuple[BenchmarkRow, ...] = (),
    active_host_elapsed_microseconds: int = 0,
) -> OperationalPilotSummary:
    if type(observations) is not SanitizedObservations:
        _fail("observations", "must be exact SanitizedObservations")
    active_elapsed = _integer(
        active_host_elapsed_microseconds,
        "active_host_elapsed_microseconds",
        minimum=0,
        maximum=ACTIVE_HOST_DEADLINE_MICROSECONDS,
    )
    if type(rows) is not tuple or any(type(row) is not BenchmarkRow for row in rows):
        _fail("rows", "must contain exact BenchmarkRow values")
    calls = cast(list[dict[str, object]], observations.to_dict()["calls"])
    returned = [cast(str | None, call["returned_model_id"]) for call in calls]
    elapsed = [cast(int | None, call["elapsed_microseconds"]) for call in calls]
    usage_values: dict[str, list[int | None]] = {
        "input_tokens": [],
        "output_tokens": [],
        "cache_creation_input_tokens": [],
        "cache_read_input_tokens": [],
    }
    provider_attempts = 0
    retries = 0
    for call in calls:
        provider_attempts += len(cast(list[object], call["attempts"]))
        retries += cast(int, call["retry_count"])
        usage = cast(dict[str, object], call["usage"])
        for field in usage_values:
            usage_values[field].append(cast(int | None, usage[field]))
    stages = _stage_totals(rows)
    if rows and (
        sum(value.logical_calls for value in stages) != len(calls)
        or sum(value.retries for value in stages) != retries
    ):
        _fail("rows", "stage totals do not reconcile with sanitized observations")
    return OperationalPilotSummary(
        observations.run_id,
        len(calls),
        provider_attempts,
        retries,
        AvailabilityCount(
            sum(value is not None for value in returned),
            sum(value is None for value in returned),
        ),
        tuple(sorted({value for value in returned if value is not None})),
        _metric(elapsed),
        UsageAvailability(
            _metric(usage_values["input_tokens"]),
            _metric(usage_values["output_tokens"]),
            _metric(usage_values["cache_creation_input_tokens"]),
            _metric(usage_values["cache_read_input_tokens"]),
        ),
        stages,
        active_elapsed,
    )


def _budget_usage_bytes(summary: OperationalPilotSummary) -> bytes | None:
    elapsed = summary.latency.total_if_complete
    if elapsed is None or not summary.stage_totals:
        return None
    by_stage = {value.stage: value for value in summary.stage_totals}

    def combined(*names: str) -> dict[str, int]:
        values = [by_stage[name] for name in names]
        return {
            "attempt_reserved_output_tokens": sum(
                value.attempt_reserved_output_tokens for value in values
            ),
            "logical_calls": sum(value.logical_calls for value in values),
            "requested_output_tokens": sum(value.requested_output_tokens for value in values),
            "retries": sum(value.retries for value in values),
        }

    usage = {
        "cache_creation_input_tokens": summary.usage.cache_creation_input_tokens.total_if_complete,
        "cache_read_input_tokens": summary.usage.cache_read_input_tokens.total_if_complete,
        "input_tokens": summary.usage.input_tokens.total_if_complete,
        "output_tokens": summary.usage.output_tokens.total_if_complete,
    }
    stage_totals = {
        "critic": combined("critic"),
        "proposal_raw": combined("proposal", "raw"),
        "repair": combined("repair"),
    }
    wire: dict[str, object] = {
        "attempt_reserved_output_tokens": sum(
            (value["attempt_reserved_output_tokens"] for value in stage_totals.values()),
            start=0,
        ),
        "attempts": summary.provider_attempts,
        "elapsed_microseconds": elapsed,
        "logical_calls": summary.logical_calls,
        "pair_count": PILOT_PAIR_COUNT,
        "requested_output_tokens": sum(
            (value["requested_output_tokens"] for value in stage_totals.values()),
            start=0,
        ),
        "schema": "benchmark-operational-usage@0.1.0",
        "stage_totals": stage_totals,
        "usage": usage,
        "usage_covers_all_attempts": summary.retries == 0,
    }
    function = cast(
        Callable[[object], _OperationalUsageView],
        load_budget_gate_module().operational_usage_from_dict,
    )
    return function(wire).wire_json


@dataclass(frozen=True, slots=True)
class PilotResult:
    receipt: BenchmarkReceipt | None
    summary: OperationalPilotSummary | None
    paused: bool
    completed_rows: int
    active_host_elapsed_microseconds: int


def collect_pilot(
    *,
    output_dir: Path,
    spec: PilotSpec | None = None,
    pre_call_config: PilotPreCallConfig | None = None,
    resume: bool = False,
    pause_after_rows: int | None = None,
    agent_llm_factory: LLMFactory | None = None,
    raw_llm_factory: LLMFactory | None = None,
    authorized_maximum_spend_microunits: int | None = None,
    observation_clock_ns: Callable[[], int] | None = None,
    host_clock_ns: Callable[[], int] = time.monotonic_ns,
) -> PilotResult:
    """Collect or cleanly resume one operational pilot without building a report."""

    if not isinstance(output_dir, Path):
        _fail("output_dir", "must be a Path")
    if type(resume) is not bool:
        _fail("resume", "must be an exact bool")
    if (spec is None) == (pre_call_config is None):
        _fail("collection_config", "requires exactly one pilot spec or live pre-call config")
    if pause_after_rows is not None:
        _integer(
            pause_after_rows,
            "pause_after_rows",
            minimum=1,
            maximum=PILOT_EXPECTED_ROWS,
        )
    if not callable(host_clock_ns):
        _fail("host_clock_ns", "must be callable")
    if observation_clock_ns is not None and not callable(observation_clock_ns):
        _fail("observation_clock_ns", "must be null or callable")

    if spec is not None:
        context = build_pilot_stub_context(spec)
        if authorized_maximum_spend_microunits is not None:
            _fail(
                "authorized_maximum_spend_microunits",
                "stub pilot forbids a live spend confirmation",
            )
        if agent_llm_factory is not None or raw_llm_factory is not None:
            _fail("llm_factory", "stub pilot does not accept client injection")
    else:
        assert pre_call_config is not None
        context = build_pilot_live_context(pre_call_config)
        validated_pre_call = context.pre_call_config
        assert validated_pre_call is not None
        require_explicit_spend_confirmation(
            validated_pre_call,
            authorized_maximum_spend_microunits,
        )
        if (agent_llm_factory is None) is not (raw_llm_factory is None):
            _fail("llm_factory", "agent and raw factories must be supplied together")

    start_ns = _read_clock_ns(host_clock_ns, "host_clock_ns")
    agent, raw = _create_clients(context, agent_llm_factory, raw_llm_factory)
    with ExitStack() as clients:
        owned_agent = clients.enter_context(managed_llm_client(agent))
        owned_raw = clients.enter_context(managed_llm_client(raw))
        _active_elapsed_microseconds(host_clock_ns, start_ns, enforce=True)

        store_factory = ArtifactStore.resume if resume else ArtifactStore.create
        with store_factory(output_dir, context.manifest) as store:
            schedule = context.spec.schedule
            items = context.spec.items
            _validate_staged_prefix(store, schedule)
            if pause_after_rows is not None and len(store.completed_units) >= pause_after_rows:
                elapsed = _active_elapsed_microseconds(host_clock_ns, start_ns, enforce=True)
                return PilotResult(None, None, True, len(store.completed_units), elapsed)

            observation_clock = (
                observation_clock_ns
                if observation_clock_ns is not None
                else (lambda: 0)
                if context.stub
                else None
            )
            observed_agent = (
                ObservingLLM(owned_agent, store.sink)
                if observation_clock is None
                else ObservingLLM(owned_agent, store.sink, clock_ns=observation_clock)
            )
            observed_raw = (
                ObservingLLM(owned_raw, store.sink)
                if observation_clock is None
                else ObservingLLM(owned_raw, store.sink, clock_ns=observation_clock)
            )
            sequence = CallSequence(
                context.manifest.run_id,
                start_call_index=len(store.sink.intents),
            )
            goal = ArrangeGoal()
            raw_requests = tuple(
                build_raw_baseline_request(
                    item.ir,
                    goal_at_source_tempo(goal, item),
                    MEDIAN_HAND,
                )
                for item in items
            )

            for unit in schedule[len(store.completed_units) :]:
                _active_elapsed_microseconds(host_clock_ns, start_ns, enforce=True)
                store.reserve_next_unit(
                    PAIR_RESERVATION if unit.arm is PilotArm.AGENT else RAW_RESERVATION
                )
                item = items[unit.item_position]
                if item.family_id is None or item.cluster_id is None:
                    _fail("corpus", "pilot identities were not snapshotted")
                scopes = sequence.bind_candidate(
                    item_id=item.item_id,
                    family_id=item.family_id,
                    cluster_id=item.cluster_id,
                    pair_id=sample_pair_id(item.item_id, unit.sample_index),
                )
                if unit.arm is PilotArm.AGENT:
                    trajectory = build_candidate_trajectory(
                        item.ir,
                        goal_at_source_tempo(goal, item),
                        observed_agent,
                        profile=MEDIAN_HAND,
                        candidate_index=unit.sample_index,
                        max_iters=PILOT_MAX_REPAIRS,
                        use_critic=True,
                        temperature=EXPERIMENT_TEMPERATURE,
                        call_scope_factory=scopes,
                    )
                    row, raw_blobs = _candidate_row(
                        context.manifest.run_id,
                        item,
                        trajectory,
                        _ledger(store),
                        MEDIAN_HAND,
                    )
                else:
                    outcome = collect_raw_llm_baseline(
                        raw_requests[unit.item_position],
                        observed_raw,
                        MEDIAN_HAND,
                        sample_index=unit.sample_index,
                        call_scope_factory=scopes,
                    )
                    row, raw_blobs = _raw_row(
                        context.manifest.run_id,
                        item,
                        outcome,
                        _ledger(store),
                        MEDIAN_HAND,
                    )
                store.commit_unit(
                    len(store.completed_units),
                    row,
                    raw_blobs,
                )
                elapsed = _active_elapsed_microseconds(host_clock_ns, start_ns, enforce=True)
                if pause_after_rows is not None and len(store.completed_units) >= pause_after_rows:
                    return PilotResult(None, None, True, len(store.completed_units), elapsed)

            receipt = store.finalize()
            completed_rows = len(store.completed_units)

        active_elapsed = _active_elapsed_microseconds(host_clock_ns, start_ns, enforce=True)

    canonical = output_dir / "canonical"
    bundle = load_replay_bundle(
        canonical / "config.json",
        canonical / "receipt.json",
        canonical / "rows.jsonl",
        canonical / "blobs.jsonl",
        canonical / "observations.json",
    )
    if bundle.manifest != context.manifest or bundle.receipt != receipt:
        _fail("canonical", "finalized pilot bundle differs from its invocation context")
    summary = build_operational_summary(
        bundle.observations,
        rows=bundle.rows,
        active_host_elapsed_microseconds=active_elapsed,
    )
    budget_usage = _budget_usage_bytes(summary)
    if context.stub:
        if budget_usage is not None:
            _fail("operational_summary", "stub timing must remain unavailable")
    else:
        if budget_usage is None:
            _fail("operational_summary", "live pilot timing must be available")
        _write_canonical_file(output_dir / "operational-summary.json", budget_usage)
    return PilotResult(receipt, summary, False, completed_rows, active_elapsed)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="task8-pilot")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--stub", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--write-spec", type=Path)
    mode.add_argument("--check-spec", type=Path)
    mode.add_argument("--write-pre-call", type=Path)
    mode.add_argument("--check-pre-call", type=Path)
    parser.add_argument(
        "--formal-prereg",
        type=Path,
        default=(
            Path(__file__).resolve().parents[1]
            / "docs/experiments/2026-07-17-benchmark-v2-prereg.json"
        ),
    )
    parser.add_argument("--spec", type=Path)
    parser.add_argument("--pre-call-config", type=Path)
    parser.add_argument("--pricing-contract", type=Path)
    parser.add_argument("--collection-attempt", type=int)
    parser.add_argument("--execution-git-sha")
    parser.add_argument("--analysis-code-sha256")
    parser.add_argument("--uv-lock-sha256")
    parser.add_argument("--authorized-maximum-spend-microunits", type=int)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--pause-after-rows", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.write_spec is not None or args.check_spec is not None:
        formal = preregistration_from_bytes(args.formal_prereg.read_bytes())
        expected = build_pilot_spec(formal)
        if args.write_spec is not None:
            _write_canonical_file(args.write_spec, expected.wire_json)
            checked_path = args.write_spec
        else:
            checked_path = args.check_spec
            assert checked_path is not None
            observed = pilot_spec_from_bytes(checked_path.read_bytes())
            if observed != expected:
                _fail("check_spec", "does not equal the canonical spec from frozen formal input")
        sys.stdout.write(f"{checked_path} {expected.sha256}\n")
        return 0
    if args.write_pre_call is not None or args.check_pre_call is not None:
        required = {
            "--spec": args.spec,
            "--pricing-contract": args.pricing_contract,
            "--collection-attempt": args.collection_attempt,
            "--execution-git-sha": args.execution_git_sha,
            "--analysis-code-sha256": args.analysis_code_sha256,
            "--uv-lock-sha256": args.uv_lock_sha256,
        }
        missing = [name for name, value in required.items() if value is None]
        if missing:
            parser.error(
                "--write-pre-call/--check-pre-call require " + ", ".join(missing)
            )
        assert args.spec is not None
        assert args.pricing_contract is not None
        assert args.collection_attempt is not None
        assert args.execution_git_sha is not None
        assert args.analysis_code_sha256 is not None
        assert args.uv_lock_sha256 is not None
        exact_spec = pilot_spec_from_bytes(args.spec.read_bytes())
        expected_pre_call = build_pilot_pre_call_config(
            exact_spec,
            collection_attempt=args.collection_attempt,
            execution_git_sha=args.execution_git_sha,
            analysis_code_sha256=args.analysis_code_sha256,
            uv_lock_sha256=args.uv_lock_sha256,
            pricing_contract_json=args.pricing_contract.read_bytes(),
        )
        if args.write_pre_call is not None:
            _write_canonical_file(args.write_pre_call, expected_pre_call.wire_json)
            checked_path = args.write_pre_call
        else:
            checked_path = args.check_pre_call
            assert checked_path is not None
            observed_pre_call = pilot_pre_call_config_from_bytes(checked_path.read_bytes())
            if observed_pre_call != expected_pre_call:
                _fail(
                    "check_pre_call",
                    "does not equal the canonical declaration from explicit inputs",
                )
        sys.stdout.write(
            f"{checked_path} priced declaration only; explicit live confirmation still required\n"
        )
        return 0
    if args.output_dir is None:
        parser.error("--stub/--live require --output-dir")
    raw_spec = None if args.spec is None else pilot_spec_from_bytes(args.spec.read_bytes())
    raw_pre_call = (
        None
        if args.pre_call_config is None
        else pilot_pre_call_config_from_bytes(args.pre_call_config.read_bytes())
    )
    if args.stub:
        if raw_spec is None or raw_pre_call is not None:
            parser.error("--stub requires --spec and forbids --pre-call-config")
        result = collect_pilot(
            spec=raw_spec,
            output_dir=args.output_dir,
            resume=args.resume,
            pause_after_rows=args.pause_after_rows,
            authorized_maximum_spend_microunits=(
                args.authorized_maximum_spend_microunits
            ),
        )
    else:
        if raw_pre_call is None:
            parser.error("--live requires --pre-call-config")
        if raw_spec is not None and raw_spec != raw_pre_call.spec:
            parser.error("--spec differs from the spec embedded in --pre-call-config")
        result = collect_pilot(
            pre_call_config=raw_pre_call,
            output_dir=args.output_dir,
            resume=args.resume,
            pause_after_rows=args.pause_after_rows,
            authorized_maximum_spend_microunits=(
                args.authorized_maximum_spend_microunits
            ),
        )
    output: dict[str, object] = {
        "active_host_elapsed_microseconds": result.active_host_elapsed_microseconds,
        "completed_rows": result.completed_rows,
        "paused": result.paused,
        "receipt": None if result.receipt is None else {"run_id": result.receipt.run_id},
        "summary": None if result.summary is None else result.summary.to_dict(),
    }
    sys.stdout.buffer.write(canonical_json_bytes(output) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
