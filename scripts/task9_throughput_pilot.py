#!/usr/bin/env python3
"""Analysis-excluded, resumable Task 9 concurrency smoke blocks.

Each invocation runs exactly the frozen eight-unit Task 8 pilot schedule at one
in-flight level.  A block is operational smoke evidence only: this module never
selects or recommends eight formal lanes.  Repeated independent blocks can be
aggregated for a later human confirmation, while four remains the default.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import math
import os
import queue
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Final, NoReturn, Protocol, cast

import fretsure.bench.runner as runner_module
from fretsure.bench.artifacts import (
    CompleteUnitReservation,
    parse_canonical_json_bytes,
    parse_canonical_jsonl_bytes,
)
from fretsure.bench.concurrent import (
    CollectionExecutionContract,
    ConcurrentExecutionCode,
    ConcurrentExecutionError,
    ConcurrentUnitCoordinator,
    LaneObservationPolicy,
    ReadyUnit,
    UnitPermit,
)
from fretsure.bench.contracts import canonical_json_bytes, require_sha256
from fretsure.bench.experiment import ObservationLedger, sample_pair_id
from fretsure.bench.observe import (
    AttemptIntent,
    CallIntent,
    CallResult,
    CallSequence,
    ObservingLLM,
)
from fretsure.bench.preregistration import (
    FORMAL_OPERATIONAL_RECORDED_ATTEMPT_OVERHEAD_SECONDS,
)
from fretsure.llm.client import (
    MAX_PROXY_TEXT_BYTES_PER_TOKEN,
    MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
    LLMClient,
    LLMModelIdError,
    ProxyLLM,
    close_llm_client,
    require_numeric_loopback_proxy_environment,
    snapshot_llm_model_id,
)

CONFIG_VERSION: Final = "benchmark-task9-throughput-pilot-config@0.1.0"
SUMMARY_VERSION: Final = "benchmark-task9-throughput-pilot-summary@0.1.0"
COMPARISON_VERSION: Final = "benchmark-task9-throughput-comparison@0.1.0"
SEGMENT_VERSION: Final = "benchmark-task9-throughput-segment@0.1.0"
CHECKPOINT_VERSION: Final = "benchmark-task9-throughput-checkpoint@0.1.0"

EVIDENCE_SCOPE: Final = "smoke"
EXCLUDED_FROM_ANALYSIS: Final = True
REQUEST_TIMEOUT_SECONDS: Final = 300
RECORDED_ATTEMPT_ELAPSED_OVERHEAD_SECONDS: Final = (
    FORMAL_OPERATIONAL_RECORDED_ATTEMPT_OVERHEAD_SECONDS
)
PILOT_LEVELS: Final = (2, 4, 8)
UNITS_PER_BLOCK: Final = 8
AGENT_UNITS_PER_BLOCK: Final = 4
RAW_UNITS_PER_BLOCK: Final = 4
MIN_CONFIRMATION_BLOCKS_PER_LEVEL: Final = 8

TASK8_SPEC_RAW_SHA256: Final = "e455a608d4b186f24a2739e009b8f9fe604036fd3a4f34d0ef97d2afb3ab7ad3"
TASK8_PRICING_RAW_SHA256: Final = "7b5ae715a08bb4e1cc7cca32e77db6ffc7e5f000133150194cf70a4b8f62c9b2"

ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_TASK8_SPEC: Final = ROOT / "docs/experiments/2026-07-18-benchmark-v2-pilot-spec.json"
DEFAULT_PRICING_CONTRACT: Final = (
    ROOT / "docs/experiments/2026-07-18-gpt-5.6-sol-pricing-contract-v2.json"
)
DEFAULT_UV_LOCK: Final = ROOT / "uv.lock"
_SEGMENT_DOMAIN: Final = b"fretsure:task9-throughput-segment@0.1.0\0"
_ZERO_SHA256: Final = "0" * 64


class ThroughputPilotError(ValueError):
    """Stable, content-free Task 9 throughput-pilot failure."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid Task 9 throughput pilot {field}: {detail}")


def _fail(field: str, detail: str) -> NoReturn:
    raise ThroughputPilotError(field, detail)


def _exact_object(
    value: object,
    field: str,
    keys: frozenset[str],
) -> dict[str, object]:
    if type(value) is not dict:
        _fail(field, "must be an exact object")
    result = cast(dict[str, object], value)
    if frozenset(result) != keys:
        _fail(field, "must contain the exact keys")
    return result


def _exact_int(
    value: object,
    field: str,
    *,
    minimum: int = 0,
    maximum: int = (1 << 255) - 1,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(field, f"must be an exact integer in {minimum}..{maximum}")
    return value


def _text(value: object, field: str, *, maximum: int = 4_096) -> str:
    if type(value) is not str or not 1 <= len(value) <= maximum or not value.isprintable():
        _fail(field, f"must be one printable string of 1..{maximum} characters")
    return value


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _checked_sha(value: object, field: str) -> str:
    try:
        return require_sha256(value, path=field)
    except ValueError:
        _fail(field, "must be one lowercase SHA-256 digest")


_TASK8_MODULE: ModuleType | None = None


def load_task8_pilot_module() -> ModuleType:
    """Load the adjacent Task 8 implementation without making scripts a package."""

    global _TASK8_MODULE
    if _TASK8_MODULE is not None:
        return _TASK8_MODULE
    path = Path(__file__).with_name("task8_pilot.py")
    spec = importlib.util.spec_from_file_location("fretsure_task9_task8_pilot", path)
    if spec is None or spec.loader is None:
        _fail("task8_pilot", "could not load the frozen Task 8 implementation")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _TASK8_MODULE = module
    return module


@dataclass(frozen=True, slots=True)
class FrozenInputs:
    spec: Any
    pricing: Any
    spec_wire: bytes
    pricing_wire: bytes


_DEFAULT_FROZEN_INPUTS: FrozenInputs | None = None


def load_frozen_inputs(
    *,
    spec_path: Path = DEFAULT_TASK8_SPEC,
    pricing_path: Path = DEFAULT_PRICING_CONTRACT,
) -> FrozenInputs:
    """Read and mechanically bind the immutable Task 8 corpus/schedule/pricing."""

    global _DEFAULT_FROZEN_INPUTS
    is_default = spec_path == DEFAULT_TASK8_SPEC and pricing_path == DEFAULT_PRICING_CONTRACT
    if is_default and _DEFAULT_FROZEN_INPUTS is not None:
        return _DEFAULT_FROZEN_INPUTS
    try:
        spec_wire = spec_path.read_bytes()
        pricing_wire = pricing_path.read_bytes()
    except OSError as error:
        raise ThroughputPilotError("frozen_inputs", "could not read frozen inputs") from error
    if _digest(spec_wire) != TASK8_SPEC_RAW_SHA256:
        _fail("task8_spec", "does not match the frozen Task 8 raw SHA-256")
    if _digest(pricing_wire) != TASK8_PRICING_RAW_SHA256:
        _fail("pricing_contract", "does not match the frozen Task 8 raw SHA-256")
    task8 = cast(Any, load_task8_pilot_module())
    spec = task8.pilot_spec_from_bytes(spec_wire)
    pricing = task8.load_budget_gate_module().token_pricing_contract_from_bytes(pricing_wire)
    schedule = spec.schedule
    if (
        len(schedule) != UNITS_PER_BLOCK
        or sum(unit.arm.value == "agent" for unit in schedule) != AGENT_UNITS_PER_BLOCK
        or sum(unit.arm.value == "raw" for unit in schedule) != RAW_UNITS_PER_BLOCK
    ):
        _fail("task8_spec.schedule", "must remain the frozen four-agent/four-raw block")
    result = FrozenInputs(spec, pricing, spec_wire, pricing_wire)
    if is_default:
        _DEFAULT_FROZEN_INPUTS = result
    return result


def _canonical_output_root(path: Path) -> str:
    if not isinstance(path, Path):
        _fail("output_root", "must be a Path")
    try:
        return str(path.expanduser().resolve(strict=False))
    except OSError as error:
        raise ThroughputPilotError("output_root", "could not resolve the path") from error


def _run_id(level: int, block_index: int, attempt: int, *, stub: bool) -> str:
    mode = "stub-" if stub else ""
    return (
        f"benchmark-v2-task9-throughput-{mode}n{level}-"
        f"block-{block_index:03d}-attempt-{attempt:03d}"
    )


def _config_wire(
    *,
    frozen: FrozenInputs,
    level: int,
    block_index: int,
    collection_attempt: int,
    output_root: str,
    stub: bool,
    execution_git_sha: str,
    analysis_code_sha256: str,
    uv_lock_sha256: str,
) -> dict[str, object]:
    contract = CollectionExecutionContract.preregistered(max_in_flight_units=level)
    maximum = cast(
        int,
        load_task8_pilot_module()
        .load_budget_gate_module()
        .pilot_worst_case_budget(frozen.pricing)["cost_microunits"],
    )
    execution_model = (
        cast(str, load_task8_pilot_module().PILOT_STUB_MODEL_ID)
        if stub
        else cast(str, frozen.spec.requested_model_id)
    )
    return {
        "block_index": block_index,
        "collection_attempt": collection_attempt,
        "cost": {
            "currency": frozen.pricing.currency,
            "maximum_spend_microunits": maximum,
            "pricing_contract": frozen.pricing.to_dict(),
            "pricing_contract_raw_sha256": frozen.pricing.raw_sha256,
            "spend_confirmation": (
                "forbidden_for_stub" if stub else "exact_microunits_required_at_execution"
            ),
        },
        "evidence_scope": EVIDENCE_SCOPE,
        "excluded_from_analysis": EXCLUDED_FROM_ANALYSIS,
        "execution": {
            "analysis_code_sha256": analysis_code_sha256,
            "collection_execution": contract.to_dict(),
            "execution_git_sha": execution_git_sha,
            "recorded_attempt_elapsed_overhead_seconds": (
                RECORDED_ATTEMPT_ELAPSED_OVERHEAD_SECONDS
            ),
            "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
            "unit_checkpoint": CHECKPOINT_VERSION,
            "uv_lock_sha256": uv_lock_sha256,
        },
        "level": level,
        "model": {
            "execution_model_id": execution_model,
            "priced_model_id": frozen.pricing.billing_model_id,
        },
        "output_root": output_root,
        "run_id": _run_id(
            level,
            block_index,
            collection_attempt,
            stub=stub,
        ),
        "schedule": [
            {
                "arm": unit.arm.value,
                "item_id": unit.item_id,
                "item_position": unit.item_position,
                "sample_index": unit.sample_index,
                "schedule_index": unit.schedule_index,
            }
            for unit in frozen.spec.schedule
        ],
        "schema": CONFIG_VERSION,
        "source": {
            "task8_pilot_id": frozen.spec.pilot_id,
            "task8_pilot_spec_raw_sha256": _digest(frozen.spec_wire),
            "task8_pricing_contract_raw_sha256": _digest(frozen.pricing_wire),
        },
        "stub": stub,
    }


@dataclass(frozen=True, slots=True)
class ThroughputPilotConfig:
    """Canonical independent run/root declaration for one eight-unit block."""

    wire_json: bytes

    def __post_init__(self) -> None:
        throughput_config_from_bytes(self.wire_json)

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], parse_canonical_json_bytes(self.wire_json))

    @property
    def run_id(self) -> str:
        return cast(str, self.to_dict()["run_id"])

    @property
    def level(self) -> int:
        return cast(int, self.to_dict()["level"])

    @property
    def block_index(self) -> int:
        return cast(int, self.to_dict()["block_index"])

    @property
    def output_root(self) -> str:
        return cast(str, self.to_dict()["output_root"])

    @property
    def stub(self) -> bool:
        return cast(bool, self.to_dict()["stub"])

    @property
    def execution_model_id(self) -> str:
        model = cast(dict[str, object], self.to_dict()["model"])
        return cast(str, model["execution_model_id"])

    @property
    def maximum_spend_microunits(self) -> int:
        cost = cast(dict[str, object], self.to_dict()["cost"])
        return cast(int, cost["maximum_spend_microunits"])

    @property
    def pricing(self) -> Any:
        cost = cast(dict[str, object], self.to_dict()["cost"])
        task8 = cast(Any, load_task8_pilot_module())
        return task8.load_budget_gate_module().token_pricing_contract_from_dict(
            cost["pricing_contract"]
        )


def build_throughput_config(
    *,
    level: int,
    block_index: int,
    collection_attempt: int,
    output_dir: Path,
    stub: bool,
    execution_git_sha: str,
    analysis_code_sha256: str,
    uv_lock_sha256: str,
    spec_path: Path = DEFAULT_TASK8_SPEC,
    pricing_path: Path = DEFAULT_PRICING_CONTRACT,
) -> ThroughputPilotConfig:
    if type(level) is not int or level not in PILOT_LEVELS:
        _fail("level", "must be exactly 2, 4, or 8")
    exact_block = _exact_int(block_index, "block_index", minimum=1, maximum=999)
    exact_attempt = _exact_int(
        collection_attempt,
        "collection_attempt",
        minimum=1,
        maximum=999,
    )
    if type(stub) is not bool:
        _fail("stub", "must be an exact bool")
    git_sha = _text(execution_git_sha, "execution_git_sha", maximum=40)
    if len(git_sha) != 40 or any(character not in "0123456789abcdef" for character in git_sha):
        _fail("execution_git_sha", "must be a lowercase 40-character Git SHA")
    analysis_sha = _checked_sha(analysis_code_sha256, "analysis_code_sha256")
    lock_sha = _checked_sha(uv_lock_sha256, "uv_lock_sha256")
    frozen = load_frozen_inputs(spec_path=spec_path, pricing_path=pricing_path)
    return ThroughputPilotConfig(
        canonical_json_bytes(
            _config_wire(
                frozen=frozen,
                level=level,
                block_index=exact_block,
                collection_attempt=exact_attempt,
                output_root=_canonical_output_root(output_dir),
                stub=stub,
                execution_git_sha=git_sha,
                analysis_code_sha256=analysis_sha,
                uv_lock_sha256=lock_sha,
            )
        )
    )


def _validate_config(value: object) -> dict[str, object]:
    obj = _exact_object(
        value,
        "$",
        frozenset(
            {
                "block_index",
                "collection_attempt",
                "cost",
                "evidence_scope",
                "excluded_from_analysis",
                "execution",
                "level",
                "model",
                "output_root",
                "run_id",
                "schedule",
                "schema",
                "source",
                "stub",
            }
        ),
    )
    if obj["schema"] != CONFIG_VERSION:
        _fail("schema", "has the wrong version")
    if obj["evidence_scope"] != EVIDENCE_SCOPE or obj["excluded_from_analysis"] is not True:
        _fail("analysis_boundary", "must remain analysis-excluded smoke evidence")
    level = _exact_int(obj["level"], "level", minimum=2, maximum=8)
    if level not in PILOT_LEVELS:
        _fail("level", "must be exactly 2, 4, or 8")
    block = _exact_int(obj["block_index"], "block_index", minimum=1, maximum=999)
    attempt = _exact_int(
        obj["collection_attempt"],
        "collection_attempt",
        minimum=1,
        maximum=999,
    )
    if type(obj["stub"]) is not bool:
        _fail("stub", "must be an exact bool")
    stub = obj["stub"]
    output_root = _text(obj["output_root"], "output_root")
    if output_root != _canonical_output_root(Path(output_root)):
        _fail("output_root", "must be a canonical absolute path")
    if obj["run_id"] != _run_id(level, block, attempt, stub=stub):
        _fail("run_id", "does not match level, block, attempt, and mode")

    frozen = load_frozen_inputs()
    expected = _config_wire(
        frozen=frozen,
        level=level,
        block_index=block,
        collection_attempt=attempt,
        output_root=output_root,
        stub=stub,
        execution_git_sha=cast(
            str,
            _exact_object(
                obj["execution"],
                "execution",
                frozenset(
                    {
                        "analysis_code_sha256",
                        "collection_execution",
                        "execution_git_sha",
                        "recorded_attempt_elapsed_overhead_seconds",
                        "request_timeout_seconds",
                        "unit_checkpoint",
                        "uv_lock_sha256",
                    }
                ),
            )["execution_git_sha"],
        ),
        analysis_code_sha256=cast(
            str, cast(dict[str, object], obj["execution"])["analysis_code_sha256"]
        ),
        uv_lock_sha256=cast(str, cast(dict[str, object], obj["execution"])["uv_lock_sha256"]),
    )
    execution = cast(dict[str, object], obj["execution"])
    git_sha = _text(execution["execution_git_sha"], "execution.execution_git_sha", maximum=40)
    if len(git_sha) != 40 or any(character not in "0123456789abcdef" for character in git_sha):
        _fail("execution.execution_git_sha", "must be a lowercase 40-character Git SHA")
    _checked_sha(execution["analysis_code_sha256"], "execution.analysis_code_sha256")
    _checked_sha(execution["uv_lock_sha256"], "execution.uv_lock_sha256")
    request_timeout = execution["request_timeout_seconds"]
    if type(request_timeout) is not int or request_timeout != REQUEST_TIMEOUT_SECONDS:
        _fail(
            "execution.request_timeout_seconds",
            "must equal the exact frozen integer",
        )
    overhead = execution["recorded_attempt_elapsed_overhead_seconds"]
    if type(overhead) is not float or overhead != RECORDED_ATTEMPT_ELAPSED_OVERHEAD_SECONDS:
        _fail(
            "execution.recorded_attempt_elapsed_overhead_seconds",
            "must equal the exact frozen float",
        )
    if obj != expected:
        _fail("$", "does not match the frozen inputs and derived run contract")
    return obj


def throughput_config_from_bytes(data: object) -> ThroughputPilotConfig:
    if type(data) is not bytes:
        _fail("config", "must be exact bytes")
    try:
        parsed = parse_canonical_json_bytes(data)
    except ValueError as error:
        raise ThroughputPilotError("config", "must be canonical JSON") from error
    _validate_config(parsed)
    instance = object.__new__(ThroughputPilotConfig)
    object.__setattr__(instance, "wire_json", data)
    return instance


def require_exact_spend_confirmation(
    config: ThroughputPilotConfig,
    authorized_maximum_spend_microunits: int | None,
) -> None:
    if config.stub:
        if authorized_maximum_spend_microunits is not None:
            _fail("authorized_maximum_spend_microunits", "stub blocks forbid authorization")
        return
    if (
        type(authorized_maximum_spend_microunits) is not int
        or authorized_maximum_spend_microunits != config.maximum_spend_microunits
    ):
        _fail(
            "authorized_maximum_spend_microunits",
            "must exactly equal this independent block's declared maximum",
        )


def _reservation(
    *,
    logical_calls: int,
    requested_output_tokens: int,
) -> CompleteUnitReservation:
    attempts = logical_calls * 3
    return CompleteUnitReservation(
        logical_calls,
        attempts,
        requested_output_tokens,
        requested_output_tokens * 3,
        requested_output_tokens * MAX_PROXY_TEXT_BYTES_PER_TOKEN,
        attempts * MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
        int(
            (
                attempts * (REQUEST_TIMEOUT_SECONDS + RECORDED_ATTEMPT_ELAPSED_OVERHEAD_SECONDS)
                + logical_calls * 1.5
            )
            * 1_000_000
        ),
    )


def unit_reservations() -> tuple[CompleteUnitReservation, ...]:
    task8 = cast(Any, load_task8_pilot_module())
    frozen = load_frozen_inputs()
    agent = _reservation(
        logical_calls=task8.PILOT_AGENT_CALLS,
        requested_output_tokens=task8.PILOT_AGENT_TOKENS,
    )
    raw = _reservation(
        logical_calls=1,
        requested_output_tokens=task8.PILOT_PROPOSAL_TOKENS,
    )
    return tuple(agent if unit.arm.value == "agent" else raw for unit in frozen.spec.schedule)


def _reservation_sum(
    reservations: tuple[CompleteUnitReservation, ...],
) -> CompleteUnitReservation:
    return CompleteUnitReservation(
        *(
            sum(cast(int, getattr(value, field)) for value in reservations)
            for field in (
                "logical_calls",
                "attempts",
                "requested_output_tokens",
                "attempt_reserved_output_tokens",
                "response_text_bytes",
                "transport_response_bytes",
                "wall_microseconds",
            )
        )
    )


def _lane_policy(config: ThroughputPilotConfig) -> LaneObservationPolicy:
    if config.stub:
        return LaneObservationPolicy()
    pricing = config.pricing
    ceilings = pricing.ceilings
    return LaneObservationPolicy(
        allowed_returned_model_id=config.execution_model_id,
        require_successful_provider_evidence=True,
        billable_token_ceiling_per_attempt=(
            ceilings["input_tokens"],
            ceilings["output_tokens"],
            ceilings["cache_creation_input_tokens"],
            ceilings["cache_read_input_tokens"],
        ),
    )


@dataclass(frozen=True, slots=True)
class UnitCheckpoint:
    schedule_index: int
    wire_json: bytes

    def __post_init__(self) -> None:
        if type(self.schedule_index) is not int or not 0 <= self.schedule_index < 8:
            _fail("unit_checkpoint.schedule_index", "must identify the frozen block")
        if type(self.wire_json) is not bytes:
            _fail("unit_checkpoint.wire_json", "must be exact bytes")
        try:
            value = parse_canonical_json_bytes(self.wire_json)
        except ValueError as error:
            raise ThroughputPilotError(
                "unit_checkpoint.wire_json", "must be canonical JSON"
            ) from error
        obj = _exact_object(
            value,
            "unit_checkpoint",
            frozenset({"schedule_index", "schema", "unit_artifact"}),
        )
        if (
            obj["schema"] != CHECKPOINT_VERSION
            or obj["schedule_index"] != self.schedule_index
            or type(obj["unit_artifact"]) is not dict
        ):
            _fail("unit_checkpoint", "must bind its version and schedule index")


def build_unit_checkpoint(
    schedule_index: int,
    unit_artifact: dict[str, object],
) -> UnitCheckpoint:
    if type(unit_artifact) is not dict:
        _fail("unit_artifact", "must be an exact object")
    return UnitCheckpoint(
        schedule_index,
        canonical_json_bytes(
            {
                "schedule_index": schedule_index,
                "schema": CHECKPOINT_VERSION,
                "unit_artifact": unit_artifact,
            }
        ),
    )


class UnitExecutor(Protocol):
    def __call__(
        self,
        config: ThroughputPilotConfig,
        permit: UnitPermit,
        clients: tuple[LLMClient, LLMClient],
    ) -> UnitCheckpoint: ...


class ClientPairFactory(Protocol):
    def __call__(self) -> tuple[LLMClient, LLMClient]: ...


def _default_pair_factory(config: ThroughputPilotConfig) -> ClientPairFactory:
    task8 = cast(Any, load_task8_pilot_module())

    def factory() -> tuple[LLMClient, LLMClient]:
        if config.stub:
            return (
                task8._PilotStubLLM(config.execution_model_id),
                task8._PilotStubLLM(config.execution_model_id),
            )
        agent = ProxyLLM(
            config.execution_model_id,
            request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
        )
        try:
            raw = ProxyLLM(
                config.execution_model_id,
                request_timeout_seconds=REQUEST_TIMEOUT_SECONDS,
            )
        except BaseException:
            close_llm_client(agent)
            raise
        return agent, raw

    return factory


def create_worker_client_pairs(
    config: ThroughputPilotConfig,
    pair_factory: ClientPairFactory | None = None,
) -> tuple[tuple[LLMClient, LLMClient], ...]:
    if pair_factory is None and not config.stub:
        require_numeric_loopback_proxy_environment()
    factory = _default_pair_factory(config) if pair_factory is None else pair_factory
    if not callable(factory):
        _fail("client_pair_factory", "must be callable")
    pairs: list[tuple[LLMClient, LLMClient]] = []
    try:
        for _worker in range(config.level):
            pair = factory()
            if type(pair) is not tuple or len(pair) != 2:
                _fail("client_pair_factory", "must return two distinct clients per worker")
            pairs.append(pair)
            if pair[0] is pair[1]:
                _fail("client_pair_factory", "must return two distinct clients per worker")
        identities = [id(client) for pair in pairs for client in pair]
        if len(set(identities)) != len(identities):
            _fail("client_pair_factory", "all worker clients must be distinct instances")
        for pair in pairs:
            for client in pair:
                try:
                    model_id = snapshot_llm_model_id(client)
                except LLMModelIdError as error:
                    raise ThroughputPilotError("client.model_id", str(error)) from None
                if model_id != config.execution_model_id:
                    _fail("client.model_id", "does not match the independent block config")
    except BaseException:
        close_worker_client_pairs(tuple(pairs))
        raise
    return tuple(pairs)


def close_worker_client_pairs(
    pairs: Sequence[tuple[LLMClient, LLMClient]],
) -> None:
    first_error: BaseException | None = None
    closed: set[int] = set()
    for pair in reversed(tuple(pairs)):
        for client in reversed(pair):
            if id(client) in closed:
                continue
            closed.add(id(client))
            try:
                close_llm_client(client)
            except BaseException as error:
                if first_error is None:
                    first_error = error
    if first_error is not None:
        raise first_error


def execute_task8_unit(
    config: ThroughputPilotConfig,
    permit: UnitPermit,
    clients: tuple[LLMClient, LLMClient],
) -> UnitCheckpoint:
    """Execute one frozen Task 8 unit and reuse its row materialization."""

    task8 = cast(Any, load_task8_pilot_module())
    frozen = load_frozen_inputs()
    unit = frozen.spec.schedule[permit.schedule_index]
    item = frozen.spec.items[unit.item_position]
    observation_clock = (lambda: 0) if config.stub else None
    if observation_clock is None:
        observed_agent = ObservingLLM(clients[0], permit.sink)
        observed_raw = ObservingLLM(clients[1], permit.sink)
    else:
        observed_agent = ObservingLLM(clients[0], permit.sink, clock_ns=observation_clock)
        observed_raw = ObservingLLM(clients[1], permit.sink, clock_ns=observation_clock)
    if config.stub:
        run_agent: LLMClient = observed_agent
        run_raw: LLMClient = observed_raw
    else:
        input_ceiling = min(
            config.pricing.ceilings[name]
            for name in (
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
        )
        run_agent = task8._PricingBoundLLM(observed_agent, input_ceiling)
        run_raw = task8._PricingBoundLLM(observed_raw, input_ceiling)
    if item.family_id is None or item.cluster_id is None:
        _fail("task8_spec.corpus", "lost frozen family or cluster identity")
    scopes = CallSequence(config.run_id).bind_candidate(
        item_id=item.item_id,
        family_id=item.family_id,
        cluster_id=item.cluster_id,
        pair_id=sample_pair_id(item.item_id, unit.sample_index),
    )
    goal = task8.ArrangeGoal()
    ledger = ObservationLedger(
        permit.sink.intents,
        permit.sink.results,
        permit.sink.attempt_intents,
        permit.sink.attempt_results,
    )
    if unit.arm.value == "agent":
        trajectory = task8.build_candidate_trajectory(
            item.ir,
            task8.goal_at_source_tempo(goal, item),
            run_agent,
            profile=task8.MEDIAN_HAND,
            candidate_index=unit.sample_index,
            max_iters=task8.PILOT_MAX_REPAIRS,
            use_critic=True,
            temperature=task8.EXPERIMENT_TEMPERATURE,
            call_scope_factory=scopes,
        )
        ledger = ObservationLedger(
            permit.sink.intents,
            permit.sink.results,
            permit.sink.attempt_intents,
            permit.sink.attempt_results,
        )
        row, blobs = task8._candidate_row(
            config.run_id,
            item,
            trajectory,
            ledger,
            task8.MEDIAN_HAND,
        )
    else:
        request = task8.build_raw_baseline_request(
            item.ir,
            task8.goal_at_source_tempo(goal, item),
            task8.MEDIAN_HAND,
        )
        outcome = task8.collect_raw_llm_baseline(
            request,
            run_raw,
            task8.MEDIAN_HAND,
            sample_index=unit.sample_index,
            call_scope_factory=scopes,
        )
        ledger = ObservationLedger(
            permit.sink.intents,
            permit.sink.results,
            permit.sink.attempt_intents,
            permit.sink.attempt_results,
        )
        row, blobs = task8._raw_row(
            config.run_id,
            item,
            outcome,
            ledger,
            task8.MEDIAN_HAND,
        )
    artifact = runner_module._ConcurrentUnitArtifact(
        permit.schedule_index,
        row,
        blobs,
    )
    wire = runner_module._concurrent_unit_artifact_bytes(artifact)
    parsed_artifact = parse_canonical_json_bytes(wire)
    if type(parsed_artifact) is not dict:  # pragma: no cover - runner invariant
        raise AssertionError("concurrent unit artifact must encode one object")
    return build_unit_checkpoint(permit.schedule_index, parsed_artifact)


def _write_durable_once(path: Path, data: bytes) -> str:
    return runner_module._write_private_artifact(path, data)


def _segment_hash(wire: dict[str, object]) -> str:
    return _digest(_SEGMENT_DOMAIN + canonical_json_bytes(wire))


@dataclass(frozen=True, slots=True)
class SegmentState:
    event_count: int
    final_sha256: str
    total_active_elapsed_microseconds: int
    clean: bool


def _read_segments(path: Path) -> SegmentState:
    if not path.exists():
        return SegmentState(0, _ZERO_SHA256, 0, True)
    try:
        wires = parse_canonical_jsonl_bytes(path.read_bytes(), max_lines=2_000)
    except (OSError, ValueError) as error:
        raise ThroughputPilotError("segments", "cannot replay canonical JSONL") from error
    previous = _ZERO_SHA256
    total = 0
    expecting = "SEGMENT_STARTED"
    for sequence, raw in enumerate(wires):
        obj = _exact_object(
            raw,
            f"segments[{sequence}]",
            frozenset(
                {
                    "event_type",
                    "payload",
                    "previous_event_sha256",
                    "sequence",
                    "schema",
                }
            ),
        )
        if (
            obj["schema"] != SEGMENT_VERSION
            or obj["sequence"] != sequence
            or obj["previous_event_sha256"] != previous
            or obj["event_type"] != expecting
        ):
            _fail("segments", "has an invalid sequence, hash chain, or lifecycle")
        payload = _exact_object(
            obj["payload"],
            f"segments[{sequence}].payload",
            frozenset({"active_elapsed_microseconds", "ready_units", "status"}),
        )
        _exact_int(payload["ready_units"], "segments.ready_units", maximum=8)
        if expecting == "SEGMENT_STARTED":
            if payload["active_elapsed_microseconds"] != 0 or payload["status"] != "running":
                _fail("segments", "start payload is invalid")
            expecting = "SEGMENT_STOPPED"
        else:
            total += _exact_int(
                payload["active_elapsed_microseconds"],
                "segments.active_elapsed_microseconds",
                maximum=24 * 60 * 60 * 1_000_000,
            )
            if payload["status"] not in {"complete", "failed", "paused"}:
                _fail("segments.status", "is unsupported")
            expecting = "SEGMENT_STARTED"
        previous = _segment_hash(obj)
    return SegmentState(len(wires), previous, total, expecting == "SEGMENT_STARTED")


def _append_segment(
    path: Path,
    state: SegmentState,
    event_type: str,
    *,
    ready_units: int,
    status: str,
    active_elapsed_microseconds: int,
) -> SegmentState:
    if event_type not in {"SEGMENT_STARTED", "SEGMENT_STOPPED"}:
        _fail("segments.event_type", "is unsupported")
    wire: dict[str, object] = {
        "event_type": event_type,
        "payload": {
            "active_elapsed_microseconds": active_elapsed_microseconds,
            "ready_units": ready_units,
            "status": status,
        },
        "previous_event_sha256": state.final_sha256,
        "schema": SEGMENT_VERSION,
        "sequence": state.event_count,
    }
    encoded = canonical_json_bytes(wire) + b"\n"
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            offset = 0
            while offset < len(encoded):
                written = os.write(descriptor, encoded[offset:])
                if written <= 0:
                    raise OSError("short append")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ThroughputPilotError("segments", "durable append failed") from error
    return SegmentState(
        state.event_count + 1,
        _segment_hash(wire),
        state.total_active_elapsed_microseconds
        + (active_elapsed_microseconds if event_type == "SEGMENT_STOPPED" else 0),
        event_type == "SEGMENT_STOPPED",
    )


def _nearest_rank(values: Sequence[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 12)


def _per_hour(count: int, elapsed_microseconds: int) -> float | None:
    if elapsed_microseconds <= 0:
        return None
    return round(count * 3_600_000_000 / elapsed_microseconds, 6)


def _ratio(numerator: object, denominator: object) -> float | None:
    if type(numerator) not in (int, float) or type(denominator) not in (int, float):
        return None
    exact_numerator = float(cast(int | float, numerator))
    exact_denominator = float(cast(int | float, denominator))
    if exact_denominator == 0.0:
        return None
    return round(exact_numerator / exact_denominator, 12)


def _cost_totals(
    config: ThroughputPilotConfig,
    *,
    attempt_count: int,
    results: Sequence[CallResult],
) -> tuple[dict[str, object], dict[str, object]]:
    pricing = config.pricing
    fields = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    known: dict[str, int] = {field: 0 for field in fields}
    covered: dict[str, int] = {field: 0 for field in fields}
    for result in results:
        provider = result.provider
        if not provider.available:
            continue
        for field in fields:
            value = cast(int | None, getattr(provider, field))
            if value is not None:
                known[field] += value
                covered[field] += 1
    missing = {field: attempt_count - covered[field] for field in fields}
    if any(value < 0 for value in missing.values()):
        _fail("summary.usage", "claims more covered attempts than durable attempts")
    upper = {field: known[field] + missing[field] * pricing.ceilings[field] for field in fields}

    def priced(tokens: dict[str, int]) -> int:
        rates = cast(dict[str, int], pricing.rates)
        fixed = cast(int, pricing.fixed_microunits_per_attempt)
        numerator = sum(tokens[field] * rates[field] for field in fields)
        token_cost = (numerator + 999_999) // 1_000_000
        return attempt_count * fixed + token_cost

    known_cost = priced(known)
    tight_cost = priced(upper)
    if tight_cost > config.maximum_spend_microunits:
        _fail("summary.cost", "tight bound exceeds the configured block maximum")
    usage: dict[str, object] = {
        "attempts_with_usage_by_field": covered,
        "known_tokens": known,
        "missing_attempts_by_field": missing,
        "tight_upper_tokens": upper,
    }
    cost: dict[str, object] = {
        "currency": pricing.currency,
        "known": {
            "availability": (
                "complete" if all(value == 0 for value in missing.values()) else "partial"
            ),
            "microunits": known_cost,
        },
        "maximum_spend_microunits": config.maximum_spend_microunits,
        "tight_upper": {"availability": "available", "microunits": tight_cost},
    }
    return usage, cost


@dataclass(frozen=True, slots=True)
class ThroughputSummary:
    wire_json: bytes

    def __post_init__(self) -> None:
        throughput_summary_from_bytes(self.wire_json)

    def to_dict(self) -> dict[str, object]:
        return cast(dict[str, object], parse_canonical_json_bytes(self.wire_json))

    @property
    def run_id(self) -> str:
        return cast(str, self.to_dict()["run_id"])

    @property
    def level(self) -> int:
        return cast(int, self.to_dict()["level"])


def build_summary(
    config: ThroughputPilotConfig,
    ready_units: Iterable[ReadyUnit],
    *,
    active_elapsed_microseconds: int,
    complete: bool,
) -> ThroughputSummary:
    elapsed = _exact_int(
        active_elapsed_microseconds,
        "active_elapsed_microseconds",
        maximum=365 * 24 * 60 * 60 * 1_000_000,
    )
    if type(complete) is not bool:
        _fail("complete", "must be an exact bool")
    units = tuple(sorted(ready_units, key=lambda value: value.schedule_index))
    if any(type(value) is not ReadyUnit for value in units):
        _fail("ready_units", "must contain exact ReadyUnit values")
    if tuple(value.schedule_index for value in units) != tuple(range(len(units))):
        _fail("ready_units", "must form the canonical durable schedule prefix")
    if complete and len(units) != UNITS_PER_BLOCK:
        _fail("ready_units", "a complete block requires all eight frozen units")
    intents: list[CallIntent] = []
    attempt_intents: list[AttemptIntent] = []
    results: list[CallResult] = []
    successful_units = 0
    for unit in units:
        local_intents = [event for event in unit.events if type(event) is CallIntent]
        local_attempts = [event for event in unit.events if type(event) is AttemptIntent]
        local_results = [event for event in unit.events if type(event) is CallResult]
        if any(event.run_id != config.run_id for event in local_intents) or any(
            event.run_id != config.run_id for event in local_attempts
        ):
            _fail("ready_units", "contain observations from a different run")
        intents.extend(local_intents)
        results.extend(local_results)
        attempt_intents.extend(local_attempts)
        if (
            local_results
            and len(local_results) == len(local_intents)
            and all(result.status == "succeeded" for result in local_results)
        ):
            successful_units += 1
    successful_calls = sum(result.status == "succeeded" for result in results)
    failed_calls = sum(result.status == "failed" for result in results)
    if len(results) != len(intents):
        _fail("summary.calls", "durable units contain incomplete logical calls")
    attempts_by_call: dict[tuple[int, str], int] = defaultdict(int)
    for unit in units:
        for event in unit.events:
            if type(event) is AttemptIntent:
                attempts_by_call[(unit.schedule_index, event.logical_call_id)] += 1
    retries = sum(max(0, count - 1) for count in attempts_by_call.values())
    latencies = sorted(result.elapsed_microseconds for result in results)
    returned_ids = sorted(
        {
            result.provider.returned_model_id
            for result in results
            if result.provider.available and result.provider.returned_model_id is not None
        }
    )
    successful_calls_with_provider_evidence = sum(
        result.status == "succeeded"
        and result.provider.available
        and result.provider.status == "succeeded"
        and result.provider.returned_model_id == config.execution_model_id
        for result in results
    )
    if not config.stub and (
        successful_calls_with_provider_evidence != successful_calls
        or any(
            result.provider.returned_model_id is not None
            and result.provider.returned_model_id != config.execution_model_id
            for result in results
        )
    ):
        _fail(
            "summary.provider_evidence",
            "every successful live call requires exact returned-model evidence",
        )
    usage, cost = _cost_totals(
        config,
        attempt_count=len(attempt_intents),
        results=results,
    )
    config_wire = config.to_dict()
    execution = cast(dict[str, object], config_wire["execution"])
    wire: dict[str, object] = {
        "aggregation_basis": {
            "active_elapsed_microseconds": elapsed,
            "failed_calls": failed_calls,
            "latency_microseconds_sorted": latencies,
            "provider_attempts": len(attempt_intents),
            "ready_units": len(units),
            "retries": retries,
            "successful_calls": successful_calls,
            "successful_calls_with_provider_evidence": (successful_calls_with_provider_evidence),
            "successful_units": successful_units,
            "usage": usage,
        },
        "bindings": {
            "analysis_code_sha256": execution["analysis_code_sha256"],
            "config_raw_sha256": _digest(config.wire_json),
            "execution_git_sha": execution["execution_git_sha"],
            "execution_mode": "stub" if config.stub else "live",
            "execution_model_id": config.execution_model_id,
            "output_root": config.output_root,
            "pricing_contract_raw_sha256": config.pricing.raw_sha256,
            "recorded_attempt_elapsed_overhead_seconds": (
                RECORDED_ATTEMPT_ELAPSED_OVERHEAD_SECONDS
            ),
            "task8_pilot_spec_raw_sha256": TASK8_SPEC_RAW_SHA256,
            "uv_lock_sha256": execution["uv_lock_sha256"],
        },
        "block_index": config.block_index,
        "calls": {
            "failed": failed_calls,
            "provider_attempts": len(attempt_intents),
            "retries": retries,
            "retry_rate_per_attempt": _rate(retries, len(attempt_intents)),
            "success_rate": _rate(successful_calls, len(results)),
            "successful": successful_calls,
            "total": len(results),
        },
        "complete": complete,
        "cost": cost,
        "evidence_scope": EVIDENCE_SCOPE,
        "excluded_from_analysis": EXCLUDED_FROM_ANALYSIS,
        "latency": {
            "includes_failed_and_timeout_calls": True,
            "observed_calls": len(latencies),
            "p50_microseconds": _nearest_rank(latencies, 0.50),
            "p95_microseconds": _nearest_rank(latencies, 0.95),
        },
        "level": config.level,
        "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        "returned_model_ids": returned_ids,
        "run_id": config.run_id,
        "schema": SUMMARY_VERSION,
        "throughput": {
            "active_elapsed_microseconds": elapsed,
            "calls_per_hour": _per_hour(len(results), elapsed),
            "units_per_hour": _per_hour(len(units), elapsed),
        },
        "units": {
            "agent_planned": AGENT_UNITS_PER_BLOCK,
            "failed": len(units) - successful_units,
            "planned": UNITS_PER_BLOCK,
            "raw_planned": RAW_UNITS_PER_BLOCK,
            "ready": len(units),
            "successful": successful_units,
        },
    }
    return ThroughputSummary(canonical_json_bytes(wire))


def _validate_summary(value: object) -> dict[str, object]:
    obj = _exact_object(
        value,
        "summary",
        frozenset(
            {
                "aggregation_basis",
                "bindings",
                "block_index",
                "calls",
                "complete",
                "cost",
                "evidence_scope",
                "excluded_from_analysis",
                "latency",
                "level",
                "request_timeout_seconds",
                "returned_model_ids",
                "run_id",
                "schema",
                "throughput",
                "units",
            }
        ),
    )
    request_timeout = obj["request_timeout_seconds"]
    if (
        obj["schema"] != SUMMARY_VERSION
        or obj["evidence_scope"] != EVIDENCE_SCOPE
        or obj["excluded_from_analysis"] is not True
        or type(request_timeout) is not int
        or request_timeout != REQUEST_TIMEOUT_SECONDS
    ):
        _fail("summary", "has the wrong schema or analysis boundary")
    level = _exact_int(obj["level"], "summary.level", minimum=2, maximum=8)
    if level not in PILOT_LEVELS:
        _fail("summary.level", "must be 2, 4, or 8")
    _exact_int(obj["block_index"], "summary.block_index", minimum=1, maximum=999)
    if type(obj["complete"]) is not bool:
        _fail("summary.complete", "must be an exact bool")
    run_id = _text(obj["run_id"], "summary.run_id", maximum=128)
    basis = _exact_object(
        obj["aggregation_basis"],
        "summary.aggregation_basis",
        frozenset(
            {
                "active_elapsed_microseconds",
                "failed_calls",
                "latency_microseconds_sorted",
                "provider_attempts",
                "ready_units",
                "retries",
                "successful_calls",
                "successful_calls_with_provider_evidence",
                "successful_units",
                "usage",
            }
        ),
    )
    _exact_int(
        basis["active_elapsed_microseconds"],
        "summary.active_elapsed_microseconds",
        maximum=365 * 24 * 60 * 60 * 1_000_000,
    )
    for field in (
        "failed_calls",
        "provider_attempts",
        "ready_units",
        "retries",
        "successful_calls",
        "successful_calls_with_provider_evidence",
        "successful_units",
    ):
        _exact_int(basis[field], f"summary.{field}", maximum=1_000_000)
    latencies = basis["latency_microseconds_sorted"]
    if (
        type(latencies) is not list
        or any(type(item) is not int or item < 0 for item in latencies)
        or latencies != sorted(latencies)
    ):
        _fail("summary.latency_microseconds_sorted", "must be sorted nonnegative integers")
    bindings = _exact_object(
        obj["bindings"],
        "summary.bindings",
        frozenset(
            {
                "analysis_code_sha256",
                "config_raw_sha256",
                "execution_git_sha",
                "execution_mode",
                "execution_model_id",
                "output_root",
                "pricing_contract_raw_sha256",
                "recorded_attempt_elapsed_overhead_seconds",
                "task8_pilot_spec_raw_sha256",
                "uv_lock_sha256",
            }
        ),
    )
    _checked_sha(bindings["analysis_code_sha256"], "summary.analysis_code_sha256")
    _checked_sha(bindings["config_raw_sha256"], "summary.config_raw_sha256")
    execution_git_sha = _text(
        bindings["execution_git_sha"],
        "summary.execution_git_sha",
        maximum=40,
    )
    if len(execution_git_sha) != 40 or any(
        character not in "0123456789abcdef" for character in execution_git_sha
    ):
        _fail("summary.execution_git_sha", "must be a lowercase 40-character Git SHA")
    if bindings["execution_mode"] not in {"live", "stub"}:
        _fail("summary.execution_mode", "must be live or stub")
    execution_model_id = _text(
        bindings["execution_model_id"],
        "summary.execution_model_id",
        maximum=128,
    )
    task8 = cast(Any, load_task8_pilot_module())
    expected_stub = bindings["execution_mode"] == "stub"
    expected_prefix = (
        "benchmark-v2-task9-throughput-stub-n"
        if expected_stub
        else "benchmark-v2-task9-throughput-n"
    )
    expected_model = (
        task8.PILOT_STUB_MODEL_ID if expected_stub else load_frozen_inputs().spec.requested_model_id
    )
    if not run_id.startswith(expected_prefix) or execution_model_id != expected_model:
        _fail("summary.execution_mode", "disagrees with run id or execution model")
    _checked_sha(bindings["uv_lock_sha256"], "summary.uv_lock_sha256")
    if bindings["pricing_contract_raw_sha256"] != TASK8_PRICING_RAW_SHA256:
        _fail("summary.pricing_contract_raw_sha256", "is not frozen")
    overhead = bindings["recorded_attempt_elapsed_overhead_seconds"]
    if type(overhead) is not float or overhead != RECORDED_ATTEMPT_ELAPSED_OVERHEAD_SECONDS:
        _fail("summary.recorded_attempt_elapsed_overhead_seconds", "is not frozen")
    if bindings["task8_pilot_spec_raw_sha256"] != TASK8_SPEC_RAW_SHA256:
        _fail("summary.task8_pilot_spec_raw_sha256", "is not frozen")
    output_root = _text(bindings["output_root"], "summary.output_root")
    if output_root != _canonical_output_root(Path(output_root)):
        _fail("summary.output_root", "must be a canonical absolute path")

    successful = cast(int, basis["successful_calls"])
    successful_with_evidence = cast(
        int,
        basis["successful_calls_with_provider_evidence"],
    )
    failed = cast(int, basis["failed_calls"])
    attempts = cast(int, basis["provider_attempts"])
    retries = cast(int, basis["retries"])
    ready = cast(int, basis["ready_units"])
    successful_units = cast(int, basis["successful_units"])
    elapsed = cast(int, basis["active_elapsed_microseconds"])
    exact_latencies = cast(list[int], latencies)
    if successful_units > ready or ready > UNITS_PER_BLOCK:
        _fail("summary.units", "successful and ready counts are inconsistent")
    if successful_with_evidence > successful:
        _fail("summary.provider_evidence", "exceeds successful call count")
    if obj["complete"] is True and ready != UNITS_PER_BLOCK:
        _fail("summary.complete", "requires all eight durable units")
    calls = _exact_object(
        obj["calls"],
        "summary.calls",
        frozenset(
            {
                "failed",
                "provider_attempts",
                "retries",
                "retry_rate_per_attempt",
                "success_rate",
                "successful",
                "total",
            }
        ),
    )
    expected_calls: dict[str, object] = {
        "failed": failed,
        "provider_attempts": attempts,
        "retries": retries,
        "retry_rate_per_attempt": _rate(retries, attempts),
        "success_rate": _rate(successful, successful + failed),
        "successful": successful,
        "total": successful + failed,
    }
    if calls != expected_calls:
        _fail("summary.calls", "does not equal the aggregation basis")
    if len(exact_latencies) != successful + failed:
        _fail("summary.latency", "must include every successful and failed call")
    latency = _exact_object(
        obj["latency"],
        "summary.latency",
        frozenset(
            {
                "includes_failed_and_timeout_calls",
                "observed_calls",
                "p50_microseconds",
                "p95_microseconds",
            }
        ),
    )
    if latency != {
        "includes_failed_and_timeout_calls": True,
        "observed_calls": len(exact_latencies),
        "p50_microseconds": _nearest_rank(exact_latencies, 0.50),
        "p95_microseconds": _nearest_rank(exact_latencies, 0.95),
    }:
        _fail("summary.latency", "does not equal the aggregation basis")
    throughput = _exact_object(
        obj["throughput"],
        "summary.throughput",
        frozenset({"active_elapsed_microseconds", "calls_per_hour", "units_per_hour"}),
    )
    if throughput != {
        "active_elapsed_microseconds": elapsed,
        "calls_per_hour": _per_hour(successful + failed, elapsed),
        "units_per_hour": _per_hour(ready, elapsed),
    }:
        _fail("summary.throughput", "does not equal the aggregation basis")
    units = _exact_object(
        obj["units"],
        "summary.units",
        frozenset(
            {
                "agent_planned",
                "failed",
                "planned",
                "raw_planned",
                "ready",
                "successful",
            }
        ),
    )
    if units != {
        "agent_planned": AGENT_UNITS_PER_BLOCK,
        "failed": ready - successful_units,
        "planned": UNITS_PER_BLOCK,
        "raw_planned": RAW_UNITS_PER_BLOCK,
        "ready": ready,
        "successful": successful_units,
    }:
        _fail("summary.units", "does not equal the aggregation basis")

    usage = _exact_object(
        basis["usage"],
        "summary.usage",
        frozenset(
            {
                "attempts_with_usage_by_field",
                "known_tokens",
                "missing_attempts_by_field",
                "tight_upper_tokens",
            }
        ),
    )
    token_fields = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )

    def token_map(name: str) -> dict[str, int]:
        raw = _exact_object(
            usage[name],
            f"summary.usage.{name}",
            frozenset(token_fields),
        )
        return {
            field: _exact_int(
                raw[field],
                f"summary.usage.{name}.{field}",
                maximum=(1 << 63) - 1,
            )
            for field in token_fields
        }

    covered = token_map("attempts_with_usage_by_field")
    known = token_map("known_tokens")
    missing = token_map("missing_attempts_by_field")
    upper = token_map("tight_upper_tokens")
    pricing = load_frozen_inputs().pricing
    for field in token_fields:
        if covered[field] + missing[field] != attempts:
            _fail("summary.usage", "coverage does not partition provider attempts")
        if known[field] > covered[field] * pricing.ceilings[field]:
            _fail("summary.usage", "known tokens exceed covered-attempt ceilings")
        if upper[field] != known[field] + missing[field] * pricing.ceilings[field]:
            _fail("summary.usage", "tight tokens do not follow the frozen ceilings")

    def price(tokens: dict[str, int]) -> int:
        rates = cast(dict[str, int], pricing.rates)
        fixed = cast(int, pricing.fixed_microunits_per_attempt)
        numerator = sum(tokens[field] * rates[field] for field in token_fields)
        return attempts * fixed + (numerator + 999_999) // 1_000_000

    known_cost = price(known)
    tight_cost = price(upper)
    cost = _exact_object(
        obj["cost"],
        "summary.cost",
        frozenset({"currency", "known", "maximum_spend_microunits", "tight_upper"}),
    )
    expected_cost = {
        "currency": pricing.currency,
        "known": {
            "availability": (
                "complete" if all(value == 0 for value in missing.values()) else "partial"
            ),
            "microunits": known_cost,
        },
        "maximum_spend_microunits": cast(
            int,
            load_task8_pilot_module()
            .load_budget_gate_module()
            .pilot_worst_case_budget(pricing)["cost_microunits"],
        ),
        "tight_upper": {"availability": "available", "microunits": tight_cost},
    }
    if cost != expected_cost:
        _fail("summary.cost", "does not equal frozen pricing over the aggregation basis")
    returned_ids = obj["returned_model_ids"]
    if (
        type(returned_ids) is not list
        or any(type(value) is not str for value in returned_ids)
        or returned_ids != sorted(set(returned_ids))
    ):
        _fail("summary.returned_model_ids", "must be sorted unique model metadata")
    if bindings["execution_mode"] == "live" and (
        successful_with_evidence != successful
        or any(value != execution_model_id for value in returned_ids)
        or (successful > 0 and returned_ids != [execution_model_id])
    ):
        _fail(
            "summary.provider_evidence",
            "does not prove exact model evidence for every successful live call",
        )
    return obj


def throughput_summary_from_bytes(data: object) -> ThroughputSummary:
    if type(data) is not bytes:
        _fail("summary", "must be exact bytes")
    try:
        parsed = parse_canonical_json_bytes(data)
    except ValueError as error:
        raise ThroughputPilotError("summary", "must be canonical JSON") from error
    _validate_summary(parsed)
    instance = object.__new__(ThroughputSummary)
    object.__setattr__(instance, "wire_json", data)
    return instance


def require_summary_matches_config(
    summary: ThroughputSummary,
    config: ThroughputPilotConfig,
) -> None:
    """Reject a completed summary not produced by this exact run declaration."""

    wire = summary.to_dict()
    bindings = cast(dict[str, object], wire["bindings"])
    config_wire = config.to_dict()
    execution = cast(dict[str, object], config_wire["execution"])
    expected_bindings = {
        "analysis_code_sha256": execution["analysis_code_sha256"],
        "config_raw_sha256": _digest(config.wire_json),
        "execution_git_sha": execution["execution_git_sha"],
        "execution_mode": "stub" if config.stub else "live",
        "execution_model_id": config.execution_model_id,
        "output_root": config.output_root,
        "pricing_contract_raw_sha256": config.pricing.raw_sha256,
        "recorded_attempt_elapsed_overhead_seconds": (RECORDED_ATTEMPT_ELAPSED_OVERHEAD_SECONDS),
        "task8_pilot_spec_raw_sha256": TASK8_SPEC_RAW_SHA256,
        "uv_lock_sha256": execution["uv_lock_sha256"],
    }
    if (
        wire["complete"] is not True
        or wire["run_id"] != config.run_id
        or wire["level"] != config.level
        or wire["block_index"] != config.block_index
        or wire["request_timeout_seconds"] != REQUEST_TIMEOUT_SECONDS
        or bindings != expected_bindings
    ):
        _fail("summary", "does not match the current run config and output root")


@dataclass(frozen=True, slots=True)
class PilotRunResult:
    summary: ThroughputSummary
    paused: bool
    ready_units: tuple[ReadyUnit, ...]


def _validate_checkpoint(path: Path, ready: ReadyUnit) -> None:
    if ready.unit_artifact_sha256 is None:
        _fail("unit_checkpoint", "ready unit lacks a checkpoint binding")
    try:
        data = path.read_bytes()
        parsed = parse_canonical_json_bytes(data)
    except (OSError, ValueError) as error:
        raise ThroughputPilotError("unit_checkpoint", "cannot replay canonical JSON") from error
    if type(parsed) is not dict:
        _fail("unit_checkpoint", "must encode one object")
    checkpoint = cast(dict[str, object], parsed)
    if (
        _digest(data) != ready.unit_artifact_sha256
        or checkpoint.get("schema") != CHECKPOINT_VERSION
        or checkpoint.get("schedule_index") != ready.schedule_index
        or type(checkpoint.get("unit_artifact")) is not dict
    ):
        _fail("unit_checkpoint", "does not match durable coordinator readiness")


def _run_parallel_block(
    config: ThroughputPilotConfig,
    coordinator: ConcurrentUnitCoordinator,
    checkpoint_dir: Path,
    client_pairs: tuple[tuple[LLMClient, LLMClient], ...],
    executor_function: UnitExecutor,
    *,
    pause_after_units: int | None,
    stop_requested: Callable[[], bool],
) -> tuple[bool, tuple[ReadyUnit, ...]]:
    if coordinator.in_flight_indices:
        raise ConcurrentExecutionError(
            ConcurrentExecutionCode.FAIL_CLOSED,
            "resume_boundary",
            "an admitted unit lacks durable completion",
        )
    ready_by_index = {unit.schedule_index: unit for unit in coordinator.ready_units}
    if tuple(sorted(ready_by_index)) != tuple(range(len(ready_by_index))):
        raise ConcurrentExecutionError(
            ConcurrentExecutionCode.FAIL_CLOSED,
            "resume_boundary",
            "ready units do not form the frozen schedule prefix",
        )
    for ready in ready_by_index.values():
        _validate_checkpoint(
            checkpoint_dir / f"{ready.schedule_index:08d}.json",
            ready,
        )
    if len(ready_by_index) == UNITS_PER_BLOCK:
        return False, coordinator.ready_units

    pair_queue: queue.SimpleQueue[tuple[LLMClient, LLMClient]] = queue.SimpleQueue()
    for pair in client_pairs:
        pair_queue.put(pair)
    worker_state = __import__("threading").local()

    def initialize() -> None:
        worker_state.clients = pair_queue.get()

    def execute(permit: UnitPermit) -> UnitCheckpoint:
        clients = cast(tuple[LLMClient, LLMClient], worker_state.clients)
        return executor_function(config, permit, clients)

    next_admission = len(coordinator.admitted_indices)
    pending: dict[Future[UnitCheckpoint], int] = {}
    worker_errors: dict[int, BaseException] = {}
    stop_admission = False
    paused = False

    with ThreadPoolExecutor(
        max_workers=config.level,
        thread_name_prefix="fretsure-task9-pilot",
        initializer=initialize,
    ) as pool:
        while len(ready_by_index) < UNITS_PER_BLOCK:
            if stop_requested():
                paused = True
                stop_admission = True
            if pause_after_units is not None and len(ready_by_index) >= pause_after_units:
                paused = True
                stop_admission = True
            while (
                not stop_admission
                and next_admission < UNITS_PER_BLOCK
                and len(pending) < config.level
            ):
                permit = coordinator.admit_next()
                if permit.schedule_index != next_admission:
                    raise ConcurrentExecutionError(
                        ConcurrentExecutionCode.OUT_OF_ORDER,
                        "admission",
                        "did not follow the frozen schedule",
                    )
                pending[pool.submit(execute, permit)] = next_admission
                next_admission += 1
            if not pending:
                break
            done, _not_done = wait(tuple(pending), return_when=FIRST_COMPLETED)
            for future in sorted(done, key=lambda item: pending[item]):
                index = pending.pop(future)
                try:
                    checkpoint = future.result()
                    if checkpoint.schedule_index != index:
                        raise ConcurrentExecutionError(
                            ConcurrentExecutionCode.OUT_OF_ORDER,
                            "worker_result",
                            "does not match the admitted schedule index",
                        )
                    sha = _write_durable_once(
                        checkpoint_dir / f"{index:08d}.json",
                        checkpoint.wire_json,
                    )
                    ready_by_index[index] = coordinator.mark_ready(
                        index,
                        unit_artifact_sha256=sha,
                    )
                except BaseException as error:
                    worker_errors.setdefault(index, error)
                    stop_admission = True
            if worker_errors:
                stop_admission = True

    if worker_errors:
        failed = min(worker_errors)
        raise ConcurrentExecutionError(
            ConcurrentExecutionCode.FAIL_CLOSED,
            f"worker[{failed}]",
            "unit execution failed before durable readiness",
        ) from worker_errors[failed]
    if coordinator.in_flight_indices:
        raise ConcurrentExecutionError(
            ConcurrentExecutionCode.FAIL_CLOSED,
            "interrupt",
            "started work did not drain to durable completion",
        )
    if len(ready_by_index) != len(coordinator.ready_prefix()):
        raise ConcurrentExecutionError(
            ConcurrentExecutionCode.FAIL_CLOSED,
            "ready_prefix",
            "completion did not form a resumable schedule prefix",
        )
    if len(ready_by_index) < UNITS_PER_BLOCK and not paused:
        raise ConcurrentExecutionError(
            ConcurrentExecutionCode.NOT_READY,
            "schedule",
            "block stopped without a requested pause",
        )
    return len(ready_by_index) < UNITS_PER_BLOCK, coordinator.ready_units


def _execute_pilot_block(
    *,
    config: ThroughputPilotConfig,
    output_dir: Path,
    resume: bool = False,
    authorized_maximum_spend_microunits: int | None = None,
    pair_factory: ClientPairFactory | None = None,
    unit_executor: UnitExecutor = execute_task8_unit,
    pause_after_units: int | None = None,
    clock_ns: Callable[[], int] = time.monotonic_ns,
    stop_requested: Callable[[], bool],
) -> PilotRunResult:
    """Run or cleanly resume one independent frozen eight-unit smoke block."""

    if type(config) is not ThroughputPilotConfig:
        _fail("config", "must be an exact ThroughputPilotConfig")
    if type(resume) is not bool:
        _fail("resume", "must be an exact bool")
    if not callable(unit_executor) or not callable(clock_ns):
        _fail("executor", "unit executor and clock must be callable")
    if pause_after_units is not None:
        _exact_int(
            pause_after_units,
            "pause_after_units",
            minimum=1,
            maximum=UNITS_PER_BLOCK - 1,
        )
    exact_root = Path(_canonical_output_root(output_dir))
    if str(exact_root) != config.output_root:
        _fail("output_root", "does not equal the root bound in the config")
    require_exact_spend_confirmation(
        config,
        authorized_maximum_spend_microunits,
    )
    config_path = exact_root / "config.json"
    segment_path = exact_root / "runtime-segments.jsonl"
    coordinator_root = exact_root / "staging" / "concurrent"
    checkpoint_dir = coordinator_root / "unit-checkpoints"
    reservations = unit_reservations()
    contract = CollectionExecutionContract.preregistered(max_in_flight_units=config.level)
    policy = _lane_policy(config)

    if resume:
        if not exact_root.is_dir() or config_path.read_bytes() != config.wire_json:
            _fail("resume", "root does not contain the exact bound config")
        segment_state = _read_segments(segment_path)
        if not segment_state.clean:
            _fail("resume", "prior active segment did not stop durably")
        coordinator = ConcurrentUnitCoordinator.resume(
            coordinator_root,
            contract,
            run_id=config.run_id,
            unit_reservations=reservations,
            collection_limits=_reservation_sum(reservations),
            lane_policy=policy,
        )
    else:
        try:
            exact_root.mkdir(mode=0o700, parents=False, exist_ok=False)
            (exact_root / "staging").mkdir(mode=0o700)
        except FileExistsError:
            _fail("output_root", "fresh block root already exists")
        _write_durable_once(config_path, config.wire_json)
        segment_path.touch(mode=0o600, exist_ok=False)
        runner_module._fsync_directory(exact_root)
        segment_state = SegmentState(0, _ZERO_SHA256, 0, True)
        coordinator = ConcurrentUnitCoordinator.create(
            coordinator_root,
            contract,
            run_id=config.run_id,
            unit_reservations=reservations,
            collection_limits=_reservation_sum(reservations),
            lane_policy=policy,
        )
        checkpoint_dir.mkdir(mode=0o700)
        runner_module._fsync_directory(coordinator_root)

    if not checkpoint_dir.is_dir():
        coordinator.close()
        _fail("unit_checkpoints", "directory is missing")
    if (exact_root / "summary.json").is_file():
        try:
            summary = throughput_summary_from_bytes((exact_root / "summary.json").read_bytes())
            require_summary_matches_config(summary, config)
        finally:
            coordinator.close()
        return PilotRunResult(summary, False, coordinator.ready_units)

    clients: tuple[tuple[LLMClient, LLMClient], ...] = ()
    started = False
    start_ns = 0
    try:
        if len(coordinator.ready_indices) < UNITS_PER_BLOCK:
            clients = create_worker_client_pairs(config, pair_factory)
        start_ns = clock_ns()
        if type(start_ns) is not int or start_ns < 0:
            _fail("clock", "must return a nonnegative exact integer")
        segment_state = _append_segment(
            segment_path,
            segment_state,
            "SEGMENT_STARTED",
            ready_units=len(coordinator.ready_indices),
            status="running",
            active_elapsed_microseconds=0,
        )
        started = True
        paused, ready_units = _run_parallel_block(
            config,
            coordinator,
            checkpoint_dir,
            clients,
            unit_executor,
            pause_after_units=pause_after_units,
            stop_requested=stop_requested,
        )
        stop_ns = clock_ns()
        if type(stop_ns) is not int or stop_ns < start_ns:
            _fail("clock", "moved backwards or returned a non-integer")
        elapsed = (stop_ns - start_ns) // 1_000
        segment_state = _append_segment(
            segment_path,
            segment_state,
            "SEGMENT_STOPPED",
            ready_units=len(ready_units),
            status="paused" if paused else "complete",
            active_elapsed_microseconds=elapsed,
        )
        started = False
        total_elapsed = segment_state.total_active_elapsed_microseconds
        summary = build_summary(
            config,
            ready_units,
            active_elapsed_microseconds=total_elapsed,
            complete=not paused,
        )
        if not paused:
            _write_durable_once(exact_root / "summary.json", summary.wire_json)
        return PilotRunResult(summary, paused, ready_units)
    except BaseException:
        if started:
            try:
                stop_ns = clock_ns()
                elapsed = (
                    0
                    if type(stop_ns) is not int or stop_ns < start_ns
                    else (stop_ns - start_ns) // 1_000
                )
                _append_segment(
                    segment_path,
                    segment_state,
                    "SEGMENT_STOPPED",
                    ready_units=len(coordinator.ready_indices),
                    status="failed",
                    active_elapsed_microseconds=elapsed,
                )
            except BaseException:
                pass
        raise
    finally:
        try:
            close_worker_client_pairs(clients)
        finally:
            coordinator.close()


def execute_pilot_block(
    *,
    config: ThroughputPilotConfig,
    output_dir: Path,
    resume: bool = False,
    authorized_maximum_spend_microunits: int | None = None,
    pair_factory: ClientPairFactory | None = None,
    unit_executor: UnitExecutor = execute_task8_unit,
    pause_after_units: int | None = None,
    clock_ns: Callable[[], int] = time.monotonic_ns,
) -> PilotRunResult:
    """Defer SIGINT across setup, then return one clean resumable pause."""

    result: PilotRunResult | None = None
    try:
        with runner_module._deferred_operational_sigint() as stop_requested:
            result = _execute_pilot_block(
                config=config,
                output_dir=output_dir,
                resume=resume,
                authorized_maximum_spend_microunits=(authorized_maximum_spend_microunits),
                pair_factory=pair_factory,
                unit_executor=unit_executor,
                pause_after_units=pause_after_units,
                clock_ns=clock_ns,
                stop_requested=stop_requested,
            )
    except KeyboardInterrupt:
        if result is None:
            raise
    if result is None:  # pragma: no cover - context-manager invariant
        raise AssertionError("pilot execution returned no result")
    return result


def _pooled_level(
    level: int,
    entries: Sequence[tuple[ThroughputSummary, str]],
) -> dict[str, object]:
    bases = [
        cast(dict[str, object], summary.to_dict()["aggregation_basis"]) for summary, _ in entries
    ]
    latencies = sorted(
        cast(int, value)
        for basis in bases
        for value in cast(list[object], basis["latency_microseconds_sorted"])
    )
    elapsed = sum(cast(int, basis["active_elapsed_microseconds"]) for basis in bases)
    units = sum(cast(int, basis["ready_units"]) for basis in bases)
    successes = sum(cast(int, basis["successful_calls"]) for basis in bases)
    failures = sum(cast(int, basis["failed_calls"]) for basis in bases)
    attempts = sum(cast(int, basis["provider_attempts"]) for basis in bases)
    retries = sum(cast(int, basis["retries"]) for basis in bases)
    costs = [cast(dict[str, object], summary.to_dict()["cost"]) for summary, _ in entries]
    known_cost = sum(
        cast(int, cast(dict[str, object], cost["known"])["microunits"]) for cost in costs
    )
    tight_cost = sum(
        cast(int, cast(dict[str, object], cost["tight_upper"])["microunits"]) for cost in costs
    )
    return {
        "block_count": len(entries),
        "calls": {
            "failed": failures,
            "provider_attempts": attempts,
            "retries": retries,
            "retry_rate_per_attempt": _rate(retries, attempts),
            "success_rate": _rate(successes, successes + failures),
            "successful": successes,
            "total": successes + failures,
        },
        "cost": {
            "known_microunits": known_cost,
            "tight_upper_microunits": tight_cost,
        },
        "latency": {
            "includes_failed_and_timeout_calls": True,
            "p50_microseconds": _nearest_rank(latencies, 0.50),
            "p95_microseconds": _nearest_rank(latencies, 0.95),
        },
        "level": level,
        "summary_raw_sha256": sorted(raw_sha for _summary, raw_sha in entries),
        "throughput": {
            "active_elapsed_microseconds": elapsed,
            "calls_per_hour": _per_hour(successes + failures, elapsed),
            "units_per_hour": _per_hour(units, elapsed),
        },
        "unit_count": units,
    }


def build_comparison(
    summaries: Iterable[ThroughputSummary],
) -> bytes:
    """Aggregate independent blocks without ever auto-selecting eight lanes."""

    entries = tuple((summary, _digest(summary.wire_json)) for summary in summaries)
    if not entries:
        _fail("comparison", "requires at least one summary")
    run_ids: set[str] = set()
    roots: set[str] = set()
    blocks: set[tuple[int, int]] = set()
    grouped: dict[int, list[tuple[ThroughputSummary, str]]] = defaultdict(list)
    common_execution_binding: dict[str, object] | None = None
    for summary, raw_sha in entries:
        if type(summary) is not ThroughputSummary:
            _fail("comparison", "requires exact ThroughputSummary values")
        wire = summary.to_dict()
        if wire["complete"] is not True:
            _fail("comparison", "may aggregate only complete blocks")
        run_id = cast(str, wire["run_id"])
        bindings = cast(dict[str, object], wire["bindings"])
        if bindings["execution_mode"] != "live":
            _fail("comparison", "accepts only live complete summaries")
        execution_binding = {
            "analysis_code_sha256": bindings["analysis_code_sha256"],
            "execution_git_sha": bindings["execution_git_sha"],
            "execution_mode": bindings["execution_mode"],
            "execution_model_id": bindings["execution_model_id"],
            "pricing_contract_raw_sha256": bindings["pricing_contract_raw_sha256"],
            "recorded_attempt_elapsed_overhead_seconds": bindings[
                "recorded_attempt_elapsed_overhead_seconds"
            ],
            "request_timeout_seconds": wire["request_timeout_seconds"],
            "task8_pilot_spec_raw_sha256": bindings["task8_pilot_spec_raw_sha256"],
            "uv_lock_sha256": bindings["uv_lock_sha256"],
        }
        if common_execution_binding is None:
            common_execution_binding = execution_binding
        elif execution_binding != common_execution_binding:
            _fail("comparison", "summaries have mismatched execution bindings")
        root = cast(str, bindings["output_root"])
        block = (summary.level, cast(int, wire["block_index"]))
        if run_id in run_ids or root in roots or block in blocks:
            _fail("comparison", "blocks must have independent run ids, roots, and indices")
        run_ids.add(run_id)
        roots.add(root)
        blocks.add(block)
        grouped[summary.level].append((summary, raw_sha))
    pooled = {level: _pooled_level(level, grouped[level]) for level in sorted(grouped)}
    levels = [pooled[level] for level in sorted(pooled)]
    four_blocks = len(grouped.get(4, ()))
    eight_blocks = len(grouped.get(8, ()))
    replicated = (
        four_blocks >= MIN_CONFIRMATION_BLOCKS_PER_LEVEL
        and eight_blocks >= MIN_CONFIRMATION_BLOCKS_PER_LEVEL
    )
    decision = {
        "automatic_level_selection": None,
        "default_formal_level": 4,
        "level_8_status": (
            "manual_confirm_required" if replicated else "insufficient_independent_blocks"
        ),
        "minimum_blocks_required_for_4_vs_8": (MIN_CONFIRMATION_BLOCKS_PER_LEVEL),
        "recommendation": None,
    }
    four_vs_eight: dict[str, object] | None = None
    if 4 in pooled and 8 in pooled:
        four = pooled[4]
        eight = pooled[8]
        four_calls = cast(dict[str, object], four["calls"])
        eight_calls = cast(dict[str, object], eight["calls"])
        four_latency = cast(dict[str, object], four["latency"])
        eight_latency = cast(dict[str, object], eight["latency"])
        four_throughput = cast(dict[str, object], four["throughput"])
        eight_throughput = cast(dict[str, object], eight["throughput"])
        success_delta: float | None = None
        if type(four_calls["success_rate"]) is float and type(eight_calls["success_rate"]) is float:
            success_delta = round(
                eight_calls["success_rate"] - four_calls["success_rate"],
                12,
            )
        four_vs_eight = {
            "calls_per_hour_ratio_8_over_4": _ratio(
                eight_throughput["calls_per_hour"],
                four_throughput["calls_per_hour"],
            ),
            "p95_latency_ratio_8_over_4": _ratio(
                eight_latency["p95_microseconds"],
                four_latency["p95_microseconds"],
            ),
            "success_rate_delta_8_minus_4": success_delta,
            "units_per_hour_ratio_8_over_4": _ratio(
                eight_throughput["units_per_hour"],
                four_throughput["units_per_hour"],
            ),
        }
    wire = {
        "bindings": common_execution_binding,
        "decision": decision,
        "evidence_scope": (
            "replicated_smoke_for_manual_confirmation" if replicated else EVIDENCE_SCOPE
        ),
        "excluded_from_analysis": EXCLUDED_FROM_ANALYSIS,
        "four_vs_eight": four_vs_eight,
        "levels": levels,
        "schema": COMPARISON_VERSION,
    }
    return canonical_json_bytes(wire)


def _write_cli_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_durable_once(path, data)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="task9-throughput-pilot")
    commands = parser.add_subparsers(dest="command", required=True)
    config = commands.add_parser("config")
    config.add_argument("--level", type=int, required=True, choices=PILOT_LEVELS)
    config.add_argument("--block-index", type=int, required=True)
    config.add_argument("--collection-attempt", type=int, required=True)
    config.add_argument("--output-dir", type=Path, required=True)
    config.add_argument("--output-config", type=Path, required=True)
    config.add_argument("--stub", action="store_true")
    config.add_argument("--execution-git-sha", required=True)
    config.add_argument("--analysis-code-sha256", required=True)
    config.add_argument("--uv-lock-sha256", required=True)

    run = commands.add_parser("run")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--resume", action="store_true")
    run.add_argument("--pause-after-units", type=int)
    run.add_argument("--authorized-maximum-spend-microunits", type=int)

    compare = commands.add_parser("compare")
    compare.add_argument("--summary", type=Path, action="append", required=True)
    compare.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "config":
        config = build_throughput_config(
            level=args.level,
            block_index=args.block_index,
            collection_attempt=args.collection_attempt,
            output_dir=args.output_dir,
            stub=args.stub,
            execution_git_sha=args.execution_git_sha,
            analysis_code_sha256=args.analysis_code_sha256,
            uv_lock_sha256=args.uv_lock_sha256,
        )
        output_config = Path(_canonical_output_root(args.output_config))
        bound_root = Path(config.output_root)
        if output_config == bound_root or bound_root in output_config.parents:
            _fail(
                "output_config",
                "must be outside the bound output directory",
            )
        _write_cli_file(args.output_config, config.wire_json)
        sys.stdout.write(
            f"{args.output_config} {_digest(config.wire_json)}; evidence_scope={EVIDENCE_SCOPE}\n"
        )
        return 0
    if args.command == "run":
        config = throughput_config_from_bytes(args.config.read_bytes())
        result = execute_pilot_block(
            config=config,
            output_dir=args.output_dir,
            resume=args.resume,
            pause_after_units=args.pause_after_units,
            authorized_maximum_spend_microunits=(args.authorized_maximum_spend_microunits),
        )
        sys.stdout.buffer.write(result.summary.wire_json + b"\n")
        return 0
    summaries = tuple(throughput_summary_from_bytes(path.read_bytes()) for path in args.summary)
    comparison = build_comparison(summaries)
    _write_cli_file(args.output, comparison)
    sys.stdout.buffer.write(comparison + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
