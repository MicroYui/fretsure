"""Durable per-unit observation lanes with deterministic canonical rebasing.

The coordinator is deliberately smaller than the benchmark runner.  It owns only
admission order, per-unit observation journals, durable readiness, and the pure
schedule-order merge.  Unit results and model clients remain owned by the caller.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Final, Self, cast

from fretsure.bench.artifacts import (
    ArtifactError,
    CompleteUnitReservation,
    DurableObservationSink,
    ObservationKey,
    parse_canonical_json_bytes,
    parse_canonical_jsonl_bytes,
)
from fretsure.bench.baselines import RawObservationKey
from fretsure.bench.contracts import canonical_json_bytes, require_identifier, require_sha256
from fretsure.bench.observe import AttemptIntent, AttemptResult, CallIntent, CallResult
from fretsure.llm.client import (
    MAX_PROXY_USAGE_TOKENS,
    LLMModelIdError,
    validate_llm_model_id,
)

BENCHMARK_CONCURRENT_COORDINATOR_VERSION: Final = (
    "benchmark-concurrent-coordinator@0.1.0"
)
MAX_CONCURRENT_UNITS: Final = 8

_CONFIG_VERSION: Final = "benchmark-concurrent-config@0.1.0"
_COLLECTION_EXECUTION_PROTOCOL: Final = "benchmark-collection-execution@0.1.0"
_COORDINATOR_WAL_DOMAIN: Final = b"fretsure:benchmark-concurrent-coordinator@0.1.0\0"
_ZERO_SHA256: Final = "0" * 64
_RESERVATION_FIELDS: Final = (
    "logical_calls",
    "attempts",
    "requested_output_tokens",
    "attempt_reserved_output_tokens",
    "response_text_bytes",
    "transport_response_bytes",
    "wall_microseconds",
)
_EXECUTION_KEYS: Final = frozenset(
    {
        "admission_order",
        "canonical_merge_order",
        "client_ownership",
        "completion_order",
        "durability",
        "max_in_flight_units",
        "protocol",
        "resume_boundary",
    }
)
_EXPECTED_EXECUTION_STRINGS: Final = {
    "admission_order": "collection_schedule_index_ascending",
    "canonical_merge_order": (
        "collection_schedule_index_ascending_then_local_call_index"
    ),
    "client_ownership": "one_agent_and_one_raw_client_per_worker",
    "completion_order": "not_semantic",
    "durability": "unit_intent_and_attempt_fsync_before_provider_request",
    "protocol": _COLLECTION_EXECUTION_PROTOCOL,
    "resume_boundary": "completed_durable_unit",
}

JournalEvent = CallIntent | AttemptIntent | AttemptResult | CallResult


class ConcurrentExecutionCode(StrEnum):
    """Stable coordinator failure classes."""

    INVALID_INPUT = "INVALID_INPUT"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    IN_FLIGHT_LIMIT = "IN_FLIGHT_LIMIT"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    NOT_READY = "NOT_READY"
    CORRUPT_COORDINATOR = "CORRUPT_COORDINATOR"
    CORRUPT_LANE = "CORRUPT_LANE"
    FAIL_CLOSED = "FAIL_CLOSED"
    IO_ERROR = "IO_ERROR"


class ConcurrentExecutionError(ValueError):
    """Typed coordinator failure without model prompt or response content."""

    def __init__(self, code: ConcurrentExecutionCode, field: str, detail: str) -> None:
        self.code = code
        self.field = field
        self.detail = detail
        super().__init__(f"concurrent execution {code.value} at {field}: {detail}")


def _fail(
    code: ConcurrentExecutionCode,
    field: str,
    detail: str,
) -> ConcurrentExecutionError:
    return ConcurrentExecutionError(code, field, detail)


def _exact_dict(
    value: object,
    field: str,
    keys: frozenset[str],
) -> dict[str, object]:
    if type(value) is not dict:
        raise _fail(ConcurrentExecutionCode.INVALID_INPUT, field, "must be an exact object")
    exact = cast(dict[str, object], value)
    if frozenset(exact) != keys or len(exact) != len(keys):
        raise _fail(
            ConcurrentExecutionCode.INVALID_INPUT,
            field,
            "must contain the exact keys",
        )
    return exact


def _exact_index(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise _fail(
            ConcurrentExecutionCode.INVALID_INPUT,
            field,
            "must be an exact integer in 0..1000000",
        )
    return value


def _identifier(value: object, field: str) -> str:
    try:
        return require_identifier(value, path=field)
    except ValueError:
        raise _fail(
            ConcurrentExecutionCode.INVALID_INPUT,
            field,
            "must be a bounded identifier",
        ) from None


def _sha256(value: object, field: str) -> str:
    try:
        return require_sha256(value, path=field)
    except ValueError:
        raise _fail(
            ConcurrentExecutionCode.INVALID_INPUT,
            field,
            "must be a lowercase SHA-256 digest",
        ) from None


@dataclass(frozen=True, slots=True)
class CollectionExecutionContract:
    """The operational execution fields frozen in preregistration."""

    protocol: str
    max_in_flight_units: int
    admission_order: str
    completion_order: str
    canonical_merge_order: str
    durability: str
    resume_boundary: str
    client_ownership: str

    def __post_init__(self) -> None:
        values = self.to_dict()
        for field, expected in _EXPECTED_EXECUTION_STRINGS.items():
            if values[field] != expected:
                raise _fail(
                    ConcurrentExecutionCode.INVALID_INPUT,
                    f"contract.{field}",
                    "does not match the preregistered execution protocol",
                )
        if (
            type(self.max_in_flight_units) is not int
            or not 1 <= self.max_in_flight_units <= MAX_CONCURRENT_UNITS
        ):
            raise _fail(
                ConcurrentExecutionCode.INVALID_INPUT,
                "contract.max_in_flight_units",
                f"must be an exact integer in 1..{MAX_CONCURRENT_UNITS}",
            )

    @classmethod
    def from_dict(cls, value: object) -> Self:
        obj = _exact_dict(value, "contract", _EXECUTION_KEYS)
        string_values: dict[str, str] = {}
        for field in _EXPECTED_EXECUTION_STRINGS:
            raw = obj[field]
            if type(raw) is not str:
                raise _fail(
                    ConcurrentExecutionCode.INVALID_INPUT,
                    f"contract.{field}",
                    "must be an exact string",
                )
            string_values[field] = raw
        maximum = obj["max_in_flight_units"]
        if type(maximum) is not int:
            raise _fail(
                ConcurrentExecutionCode.INVALID_INPUT,
                "contract.max_in_flight_units",
                "must be an exact integer",
            )
        return cls(
            protocol=string_values["protocol"],
            max_in_flight_units=maximum,
            admission_order=string_values["admission_order"],
            completion_order=string_values["completion_order"],
            canonical_merge_order=string_values["canonical_merge_order"],
            durability=string_values["durability"],
            resume_boundary=string_values["resume_boundary"],
            client_ownership=string_values["client_ownership"],
        )

    @classmethod
    def preregistered(cls, *, max_in_flight_units: int) -> Self:
        return cls(
            protocol=_COLLECTION_EXECUTION_PROTOCOL,
            max_in_flight_units=max_in_flight_units,
            admission_order=_EXPECTED_EXECUTION_STRINGS["admission_order"],
            completion_order=_EXPECTED_EXECUTION_STRINGS["completion_order"],
            canonical_merge_order=_EXPECTED_EXECUTION_STRINGS["canonical_merge_order"],
            durability=_EXPECTED_EXECUTION_STRINGS["durability"],
            resume_boundary=_EXPECTED_EXECUTION_STRINGS["resume_boundary"],
            client_ownership=_EXPECTED_EXECUTION_STRINGS["client_ownership"],
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "admission_order": self.admission_order,
            "canonical_merge_order": self.canonical_merge_order,
            "client_ownership": self.client_ownership,
            "completion_order": self.completion_order,
            "durability": self.durability,
            "max_in_flight_units": self.max_in_flight_units,
            "protocol": self.protocol,
            "resume_boundary": self.resume_boundary,
        }


@dataclass(frozen=True, slots=True)
class LaneObservationPolicy:
    """Provider-integrity settings replayed identically for every lane."""

    allowed_returned_model_id: str | None = None
    require_successful_provider_evidence: bool = False
    billable_token_ceiling_per_attempt: tuple[int, int, int, int] | None = None

    def __post_init__(self) -> None:
        if self.allowed_returned_model_id is not None:
            try:
                validate_llm_model_id(self.allowed_returned_model_id)
            except LLMModelIdError:
                raise _fail(
                    ConcurrentExecutionCode.INVALID_INPUT,
                    "lane_policy.allowed_returned_model_id",
                    "must be null or one bounded printable model identifier",
                ) from None
        if type(self.require_successful_provider_evidence) is not bool:
            raise _fail(
                ConcurrentExecutionCode.INVALID_INPUT,
                "lane_policy.require_successful_provider_evidence",
                "must be an exact bool",
            )
        if (
            self.require_successful_provider_evidence
            and self.allowed_returned_model_id is None
        ):
            raise _fail(
                ConcurrentExecutionCode.INVALID_INPUT,
                "lane_policy.allowed_returned_model_id",
                "is required with successful provider evidence",
            )
        ceilings = self.billable_token_ceiling_per_attempt
        if ceilings is not None and (
            type(ceilings) is not tuple
            or len(ceilings) != 4
            or any(
                type(value) is not int
                or not 1 <= value <= MAX_PROXY_USAGE_TOKENS
                for value in ceilings
            )
        ):
            raise _fail(
                ConcurrentExecutionCode.INVALID_INPUT,
                "lane_policy.billable_token_ceiling_per_attempt",
                "must be null or four positive exact integers",
            )

    def to_dict(self) -> dict[str, object]:
        ceilings = self.billable_token_ceiling_per_attempt
        return {
            "allowed_returned_model_id": self.allowed_returned_model_id,
            "billable_token_ceiling_per_attempt": (
                None
                if ceilings is None
                else {
                    "cache_creation_input_tokens": ceilings[2],
                    "cache_read_input_tokens": ceilings[3],
                    "input_tokens": ceilings[0],
                    "output_tokens": ceilings[1],
                }
            ),
            "require_successful_provider_evidence": (
                self.require_successful_provider_evidence
            ),
        }

    @classmethod
    def from_dict(cls, value: object) -> Self:
        obj = _exact_dict(
            value,
            "lane_policy",
            frozenset(
                {
                    "allowed_returned_model_id",
                    "billable_token_ceiling_per_attempt",
                    "require_successful_provider_evidence",
                }
            ),
        )
        model = obj["allowed_returned_model_id"]
        if model is not None and type(model) is not str:
            raise _fail(
                ConcurrentExecutionCode.INVALID_INPUT,
                "lane_policy.allowed_returned_model_id",
                "must be null or an exact string",
            )
        required = obj["require_successful_provider_evidence"]
        if type(required) is not bool:
            raise _fail(
                ConcurrentExecutionCode.INVALID_INPUT,
                "lane_policy.require_successful_provider_evidence",
                "must be an exact bool",
            )
        raw_ceilings = obj["billable_token_ceiling_per_attempt"]
        ceilings: tuple[int, int, int, int] | None = None
        if raw_ceilings is not None:
            ceiling_obj = _exact_dict(
                raw_ceilings,
                "lane_policy.billable_token_ceiling_per_attempt",
                frozenset(
                    {
                        "input_tokens",
                        "output_tokens",
                        "cache_creation_input_tokens",
                        "cache_read_input_tokens",
                    }
                ),
            )
            values = tuple(
                ceiling_obj[field]
                for field in (
                    "input_tokens",
                    "output_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                )
            )
            if any(type(item) is not int for item in values):
                raise _fail(
                    ConcurrentExecutionCode.INVALID_INPUT,
                    "lane_policy.billable_token_ceiling_per_attempt",
                    "must contain exact integers",
                )
            ceilings = cast(tuple[int, int, int, int], values)
        return cls(model, required, ceilings)

    def sink_ceilings(self) -> dict[str, int] | None:
        ceilings = self.billable_token_ceiling_per_attempt
        if ceilings is None:
            return None
        return {
            "input_tokens": ceilings[0],
            "output_tokens": ceilings[1],
            "cache_creation_input_tokens": ceilings[2],
            "cache_read_input_tokens": ceilings[3],
        }


_DEFAULT_LANE_POLICY: Final = LaneObservationPolicy()


@dataclass(frozen=True, slots=True)
class UnitPermit:
    """One durable schedule admission and its single-writer observation lane."""

    schedule_index: int
    reservation: CompleteUnitReservation
    journal_path: Path
    sink: DurableObservationSink


@dataclass(frozen=True, slots=True)
class ReadyUnit:
    """One validated terminal lane; unit artifacts remain caller-owned."""

    schedule_index: int
    reservation: CompleteUnitReservation
    journal_path: Path
    journal_sha256: str
    unit_artifact_sha256: str | None
    events: tuple[JournalEvent, ...]

    @property
    def local_call_count(self) -> int:
        return sum(type(event) is CallIntent for event in self.events)


def _reservation_to_dict(value: CompleteUnitReservation) -> dict[str, int]:
    if type(value) is not CompleteUnitReservation:
        raise _fail(
            ConcurrentExecutionCode.INVALID_INPUT,
            "reservation",
            "must be an exact CompleteUnitReservation",
        )
    return {field: cast(int, getattr(value, field)) for field in _RESERVATION_FIELDS}


def _reservation_from_dict(value: object, field: str) -> CompleteUnitReservation:
    obj = _exact_dict(value, field, frozenset(_RESERVATION_FIELDS))
    values: list[int] = []
    for name in _RESERVATION_FIELDS:
        raw = obj[name]
        if type(raw) is not int:
            raise _fail(
                ConcurrentExecutionCode.INVALID_INPUT,
                f"{field}.{name}",
                "must be an exact integer",
            )
        values.append(raw)
    try:
        return CompleteUnitReservation(*values)
    except ValueError as error:
        raise _fail(
            ConcurrentExecutionCode.INVALID_INPUT,
            field,
            "is not a valid complete-unit reservation",
        ) from error


def _validate_reservation_envelope(
    reservations: tuple[CompleteUnitReservation, ...],
    limits: CompleteUnitReservation,
) -> None:
    if type(reservations) is not tuple or not reservations or any(
        type(value) is not CompleteUnitReservation for value in reservations
    ):
        raise _fail(
            ConcurrentExecutionCode.INVALID_INPUT,
            "unit_reservations",
            "must be a non-empty tuple of exact CompleteUnitReservation values",
        )
    if type(limits) is not CompleteUnitReservation:
        raise _fail(
            ConcurrentExecutionCode.INVALID_INPUT,
            "collection_limits",
            "must be an exact CompleteUnitReservation",
        )
    for field in _RESERVATION_FIELDS:
        scheduled = sum(cast(int, getattr(value, field)) for value in reservations)
        available = cast(int, getattr(limits, field))
        if scheduled > available:
            raise _fail(
                ConcurrentExecutionCode.INVALID_INPUT,
                f"collection_limits.{field}",
                "is smaller than the sum of scheduled unit reservations",
            )


def _config_wire(
    contract: CollectionExecutionContract,
    run_id: str,
    reservations: tuple[CompleteUnitReservation, ...],
    limits: CompleteUnitReservation,
    lane_policy: LaneObservationPolicy,
) -> dict[str, object]:
    return {
        "collection_limits": _reservation_to_dict(limits),
        "execution_contract": contract.to_dict(),
        "lane_policy": lane_policy.to_dict(),
        "run_id": run_id,
        "unit_reservations": [_reservation_to_dict(value) for value in reservations],
        "version": _CONFIG_VERSION,
    }


def _coordinator_event_sha256(value: object) -> str:
    return hashlib.sha256(
        _COORDINATOR_WAL_DOMAIN + canonical_json_bytes(value)
    ).hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _create_file(path: Path, data: bytes = b"") -> None:
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
    except FileExistsError:
        raise _fail(
            ConcurrentExecutionCode.ALREADY_EXISTS,
            str(path.name),
            "already exists",
        ) from None
    try:
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(descriptor)
    except OSError as error:
        raise _fail(
            ConcurrentExecutionCode.IO_ERROR,
            str(path.name),
            "durable create failed",
        ) from error
    finally:
        os.close(descriptor)


def rebase_observation_key(value: ObservationKey, call_offset: int) -> ObservationKey:
    """Map one lane-local observation key into the run-wide canonical sequence."""

    if type(value) is not ObservationKey:
        raise _fail(
            ConcurrentExecutionCode.INVALID_INPUT,
            "observation_key",
            "must be an exact ObservationKey",
        )
    offset = _exact_index(call_offset, "call_offset")
    global_index = offset + value.call_index
    if global_index > 1_000_000:
        raise _fail(
            ConcurrentExecutionCode.INVALID_INPUT,
            "call_offset",
            "rebased call index exceeds the observation limit",
        )
    return ObservationKey(f"call:{global_index}", global_index)


def rebase_raw_observation_key(
    value: RawObservationKey,
    call_offset: int,
) -> RawObservationKey:
    """Map one raw unit outcome key into the run-wide canonical sequence."""

    if type(value) is not RawObservationKey:
        raise _fail(
            ConcurrentExecutionCode.INVALID_INPUT,
            "raw_observation_key",
            "must be an exact RawObservationKey",
        )
    local_index = _exact_index(value.call_index, "raw_observation_key.call_index")
    offset = _exact_index(call_offset, "call_offset")
    global_index = offset + local_index
    if global_index > 1_000_000:
        raise _fail(
            ConcurrentExecutionCode.INVALID_INPUT,
            "call_offset",
            "rebased call index exceeds the observation limit",
        )
    return RawObservationKey(value.run_id, f"call:{global_index}", global_index)


def rebase_journal_events(
    events: tuple[JournalEvent, ...],
    *,
    call_offset: int,
    run_id: str,
) -> tuple[JournalEvent, ...]:
    """Purely rebase one validated lane without changing request/result evidence."""

    if type(events) is not tuple or any(
        type(event) not in (CallIntent, AttemptIntent, AttemptResult, CallResult)
        for event in events
    ):
        raise _fail(
            ConcurrentExecutionCode.INVALID_INPUT,
            "events",
            "must contain exact observation journal events",
        )
    offset = _exact_index(call_offset, "call_offset")
    exact_run_id = _identifier(run_id, "run_id")
    rebased: list[JournalEvent] = []
    for event in events:
        global_index = offset + event.call_index
        if global_index > 1_000_000:
            raise _fail(
                ConcurrentExecutionCode.INVALID_INPUT,
                "call_offset",
                "rebased call index exceeds the observation limit",
            )
        logical_call_id = f"call:{global_index}"
        if type(event) is CallIntent:
            rebased.append(
                replace(
                    event,
                    run_id=exact_run_id,
                    logical_call_id=logical_call_id,
                    call_index=global_index,
                )
            )
        elif type(event) is AttemptIntent:
            rebased.append(
                replace(
                    event,
                    run_id=exact_run_id,
                    logical_call_id=logical_call_id,
                    call_index=global_index,
                    attempt_id=f"attempt:{global_index}:{event.attempt_index}",
                )
            )
        elif type(event) is AttemptResult:
            rebased.append(
                replace(
                    event,
                    run_id=exact_run_id,
                    logical_call_id=logical_call_id,
                    call_index=global_index,
                    attempt_id=f"attempt:{global_index}:{event.attempt_index}",
                )
            )
        else:
            rebased.append(
                replace(
                    event,
                    logical_call_id=logical_call_id,
                    call_index=global_index,
                )
            )
    return tuple(rebased)


class ConcurrentUnitCoordinator:
    """Single-writer durable coordinator for independent observation lanes."""

    def __init__(
        self,
        root: Path,
        contract: CollectionExecutionContract,
        run_id: str,
        unit_reservations: tuple[CompleteUnitReservation, ...],
        collection_limits: CompleteUnitReservation,
        lane_policy: LaneObservationPolicy,
        descriptor: int,
        *,
        event_count: int = 0,
        final_event_sha256: str = _ZERO_SHA256,
        admitted_count: int = 0,
        ready: dict[int, ReadyUnit] | None = None,
    ) -> None:
        self._root = root
        self._contract = contract
        self._run_id = run_id
        self._reservations = unit_reservations
        self._limits = collection_limits
        self._lane_policy = lane_policy
        self._descriptor: int | None = descriptor
        self._event_count = event_count
        self._final_event_sha256 = final_event_sha256
        self._admitted_count = admitted_count
        self._ready = {} if ready is None else ready
        self._open_permits: dict[int, UnitPermit] = {}

    @classmethod
    def create(
        cls,
        root: Path,
        contract: CollectionExecutionContract,
        *,
        run_id: str,
        unit_reservations: tuple[CompleteUnitReservation, ...],
        collection_limits: CompleteUnitReservation,
        lane_policy: LaneObservationPolicy = _DEFAULT_LANE_POLICY,
    ) -> Self:
        if not isinstance(root, Path) or type(contract) is not CollectionExecutionContract:
            raise _fail(
                ConcurrentExecutionCode.INVALID_INPUT,
                "create",
                "requires Path and exact CollectionExecutionContract",
            )
        exact_run_id = _identifier(run_id, "run_id")
        if type(lane_policy) is not LaneObservationPolicy:
            raise _fail(
                ConcurrentExecutionCode.INVALID_INPUT,
                "lane_policy",
                "must be an exact LaneObservationPolicy",
            )
        _validate_reservation_envelope(unit_reservations, collection_limits)
        try:
            root.mkdir(mode=0o700, parents=False, exist_ok=False)
            (root / "lanes").mkdir(mode=0o700)
        except FileExistsError:
            raise _fail(
                ConcurrentExecutionCode.ALREADY_EXISTS,
                "root",
                "must be fresh",
            ) from None
        except OSError as error:
            raise _fail(
                ConcurrentExecutionCode.IO_ERROR,
                "root",
                "could not create coordinator directory",
            ) from error
        try:
            config = canonical_json_bytes(
                _config_wire(
                    contract,
                    exact_run_id,
                    unit_reservations,
                    collection_limits,
                    lane_policy,
                )
            )
            _create_file(root / "config.json", config)
            _create_file(root / "coordinator.jsonl")
            _fsync_directory(root / "lanes")
            _fsync_directory(root)
            descriptor = os.open(
                root / "coordinator.jsonl",
                os.O_WRONLY | os.O_APPEND | getattr(os, "O_CLOEXEC", 0),
            )
        except BaseException:
            raise
        return cls(
            root,
            contract,
            exact_run_id,
            unit_reservations,
            collection_limits,
            lane_policy,
            descriptor,
        )

    @classmethod
    def resume(
        cls,
        root: Path,
        contract: CollectionExecutionContract,
        *,
        run_id: str,
        unit_reservations: tuple[CompleteUnitReservation, ...],
        collection_limits: CompleteUnitReservation,
        lane_policy: LaneObservationPolicy = _DEFAULT_LANE_POLICY,
    ) -> Self:
        if not isinstance(root, Path) or type(contract) is not CollectionExecutionContract:
            raise _fail(
                ConcurrentExecutionCode.INVALID_INPUT,
                "resume",
                "requires Path and exact CollectionExecutionContract",
            )
        exact_run_id = _identifier(run_id, "run_id")
        if type(lane_policy) is not LaneObservationPolicy:
            raise _fail(
                ConcurrentExecutionCode.INVALID_INPUT,
                "lane_policy",
                "must be an exact LaneObservationPolicy",
            )
        _validate_reservation_envelope(unit_reservations, collection_limits)
        expected_config = canonical_json_bytes(
            _config_wire(
                contract,
                exact_run_id,
                unit_reservations,
                collection_limits,
                lane_policy,
            )
        )
        try:
            stored_config = (root / "config.json").read_bytes()
            if parse_canonical_json_bytes(stored_config) != parse_canonical_json_bytes(
                expected_config
            ):
                raise _fail(
                    ConcurrentExecutionCode.CORRUPT_COORDINATOR,
                    "config",
                    "does not match the supplied execution contract",
                )
            raw_events = (root / "coordinator.jsonl").read_bytes()
            wires = parse_canonical_jsonl_bytes(
                raw_events,
                max_lines=len(unit_reservations) * 2,
            )
        except ConcurrentExecutionError:
            raise
        except (ArtifactError, OSError, ValueError) as error:
            raise _fail(
                ConcurrentExecutionCode.CORRUPT_COORDINATOR,
                "coordinator",
                "cannot be read as canonical coordinator state",
            ) from error

        previous = _ZERO_SHA256
        admitted_count = 0
        ready_records: dict[int, tuple[str, str | None]] = {}
        active: set[int] = set()
        for sequence, raw_wire in enumerate(wires):
            wire = _exact_dict(
                raw_wire,
                f"coordinator[{sequence}]",
                frozenset(
                    {
                        "event_type",
                        "payload",
                        "previous_event_sha256",
                        "sequence",
                        "version",
                    }
                ),
            )
            if (
                wire["version"] != BENCHMARK_CONCURRENT_COORDINATOR_VERSION
                or wire["sequence"] != sequence
                or wire["previous_event_sha256"] != previous
            ):
                raise _fail(
                    ConcurrentExecutionCode.CORRUPT_COORDINATOR,
                    f"coordinator[{sequence}]",
                    "version, sequence, or hash chain is invalid",
                )
            event_type = wire["event_type"]
            if event_type == "UNIT_ADMITTED":
                payload = _exact_dict(
                    wire["payload"],
                    f"coordinator[{sequence}].payload",
                    frozenset({"reservation", "schedule_index"}),
                )
                index = _exact_index(payload["schedule_index"], "schedule_index")
                if index != admitted_count or index >= len(unit_reservations):
                    raise _fail(
                        ConcurrentExecutionCode.CORRUPT_COORDINATOR,
                        f"coordinator[{sequence}]",
                        "admission order is not the frozen schedule order",
                    )
                reservation = _reservation_from_dict(
                    payload["reservation"],
                    f"coordinator[{sequence}].reservation",
                )
                if reservation != unit_reservations[index]:
                    raise _fail(
                        ConcurrentExecutionCode.CORRUPT_COORDINATOR,
                        f"coordinator[{sequence}].reservation",
                        "does not match the bound schedule reservation",
                    )
                active.add(index)
                admitted_count += 1
                if len(active) > contract.max_in_flight_units:
                    raise _fail(
                        ConcurrentExecutionCode.CORRUPT_COORDINATOR,
                        f"coordinator[{sequence}]",
                        "in-flight unit limit was exceeded",
                    )
            elif event_type == "UNIT_READY":
                payload = _exact_dict(
                    wire["payload"],
                    f"coordinator[{sequence}].payload",
                    frozenset(
                        {
                            "lane_journal_sha256",
                            "schedule_index",
                            "unit_artifact_sha256",
                        }
                    ),
                )
                index = _exact_index(payload["schedule_index"], "schedule_index")
                lane_sha = _sha256(
                    payload["lane_journal_sha256"],
                    "lane_journal_sha256",
                )
                artifact_raw = payload["unit_artifact_sha256"]
                artifact_sha = (
                    None
                    if artifact_raw is None
                    else _sha256(artifact_raw, "unit_artifact_sha256")
                )
                if index not in active or index in ready_records:
                    raise _fail(
                        ConcurrentExecutionCode.CORRUPT_COORDINATOR,
                        f"coordinator[{sequence}]",
                        "readiness does not match one active admitted unit",
                    )
                active.remove(index)
                ready_records[index] = (lane_sha, artifact_sha)
            else:
                raise _fail(
                    ConcurrentExecutionCode.CORRUPT_COORDINATOR,
                    f"coordinator[{sequence}].event_type",
                    "is unsupported",
                )
            previous = _coordinator_event_sha256(wire)

        ready: dict[int, ReadyUnit] = {}
        for index, (journal_sha, artifact_sha) in ready_records.items():
            unit = cls._read_terminal_lane(
                root,
                index,
                unit_reservations[index],
                exact_run_id,
                lane_policy,
                expected_sha256=journal_sha,
            )
            ready[index] = replace(unit, unit_artifact_sha256=artifact_sha)
        for index in active:
            lane_path = cls._lane_path(root, index)
            if not lane_path.exists():
                raise _fail(
                    ConcurrentExecutionCode.CORRUPT_LANE,
                    f"lane[{index}]",
                    "an admitted lane journal is missing",
                )
            try:
                sink = cls._open_lane_sink(
                    lane_path,
                    unit_reservations[index],
                    lane_policy,
                    resume=True,
                )
            except (ArtifactError, OSError, ValueError) as error:
                raise _fail(
                    ConcurrentExecutionCode.CORRUPT_LANE,
                    f"lane[{index}]",
                    "cannot be replayed as a canonical observation WAL",
                ) from error
            try:
                if sink.has_open_attempt or sink.has_open_intent:
                    raise _fail(
                        ConcurrentExecutionCode.FAIL_CLOSED,
                        f"lane[{index}]",
                        "contains a non-terminal provider-call boundary",
                    )
                if sink.journal_events:
                    raise _fail(
                        ConcurrentExecutionCode.FAIL_CLOSED,
                        f"lane[{index}]",
                        "contains observations without durable unit readiness",
                    )
                cls._validate_lane_run_id(sink.journal_events, exact_run_id, index)
            finally:
                sink.close()
        try:
            descriptor = os.open(
                root / "coordinator.jsonl",
                os.O_WRONLY | os.O_APPEND | getattr(os, "O_CLOEXEC", 0),
            )
        except OSError as error:
            raise _fail(
                ConcurrentExecutionCode.IO_ERROR,
                "coordinator",
                "could not reopen coordinator WAL",
            ) from error
        return cls(
            root,
            contract,
            exact_run_id,
            unit_reservations,
            collection_limits,
            lane_policy,
            descriptor,
            event_count=len(wires),
            final_event_sha256=previous,
            admitted_count=admitted_count,
            ready=ready,
        )

    def __enter__(self) -> Self:
        self._require_open()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        for permit in self._open_permits.values():
            permit.sink.close()
        self._open_permits.clear()
        descriptor = self._descriptor
        if descriptor is not None:
            os.close(descriptor)
            self._descriptor = None

    def _require_open(self) -> int:
        if self._descriptor is None:
            raise _fail(ConcurrentExecutionCode.IO_ERROR, "coordinator", "is closed")
        return self._descriptor

    @staticmethod
    def _lane_path(root: Path, schedule_index: int) -> Path:
        return root / "lanes" / f"{schedule_index:08d}.jsonl"

    @staticmethod
    def _open_lane_sink(
        path: Path,
        reservation: CompleteUnitReservation,
        lane_policy: LaneObservationPolicy,
        *,
        resume: bool,
    ) -> DurableObservationSink:
        return DurableObservationSink(
            path,
            max_calls=reservation.logical_calls,
            max_attempts=reservation.attempts,
            max_requested_output_tokens=reservation.requested_output_tokens,
            max_attempt_reserved_output_tokens=(
                reservation.attempt_reserved_output_tokens
            ),
            max_response_text_bytes=reservation.response_text_bytes,
            max_transport_response_bytes=reservation.transport_response_bytes,
            max_wall_microseconds=reservation.wall_microseconds,
            allowed_returned_model_id=lane_policy.allowed_returned_model_id,
            require_successful_provider_evidence=(
                lane_policy.require_successful_provider_evidence
            ),
            billable_token_ceiling_per_attempt=lane_policy.sink_ceilings(),
            resume=resume,
        )

    @staticmethod
    def _validate_lane_run_id(
        events: tuple[JournalEvent, ...],
        run_id: str,
        schedule_index: int,
    ) -> None:
        for event in events:
            event_run_id: str | None
            if type(event) is CallIntent:
                event_run_id = event.run_id
            elif type(event) is AttemptIntent:
                event_run_id = event.run_id
            elif type(event) is AttemptResult:
                event_run_id = event.run_id
            else:
                event_run_id = None
            if event_run_id is not None and event_run_id != run_id:
                raise _fail(
                    ConcurrentExecutionCode.CORRUPT_LANE,
                    f"lane[{schedule_index}].run_id",
                    "does not match the collection run",
                )

    @classmethod
    def _read_terminal_lane(
        cls,
        root: Path,
        schedule_index: int,
        reservation: CompleteUnitReservation,
        run_id: str,
        lane_policy: LaneObservationPolicy,
        *,
        expected_sha256: str | None = None,
    ) -> ReadyUnit:
        path = cls._lane_path(root, schedule_index)
        try:
            sink = cls._open_lane_sink(
                path,
                reservation,
                lane_policy,
                resume=True,
            )
        except (ArtifactError, OSError, ValueError) as error:
            raise _fail(
                ConcurrentExecutionCode.CORRUPT_LANE,
                f"lane[{schedule_index}]",
                "cannot be replayed as a canonical observation WAL",
            ) from error
        try:
            if sink.has_open_attempt or sink.has_open_intent:
                raise _fail(
                    ConcurrentExecutionCode.FAIL_CLOSED,
                    f"lane[{schedule_index}]",
                    "contains a non-terminal provider-call boundary",
                )
            events = sink.journal_events
            if not sink.intents:
                raise _fail(
                    ConcurrentExecutionCode.NOT_READY,
                    f"lane[{schedule_index}]",
                    "contains no terminal logical call",
                )
            cls._validate_lane_run_id(events, run_id, schedule_index)
            journal_sha256 = sink.journal_sha256
            if expected_sha256 is not None and journal_sha256 != expected_sha256:
                raise _fail(
                    ConcurrentExecutionCode.CORRUPT_LANE,
                    f"lane[{schedule_index}].journal_sha256",
                    "does not match the durable UNIT_READY record",
                )
            return ReadyUnit(
                schedule_index,
                reservation,
                path,
                journal_sha256,
                None,
                events,
            )
        finally:
            sink.close()

    def _append(self, event_type: str, payload: dict[str, object]) -> None:
        descriptor = self._require_open()
        wire: dict[str, object] = {
            "event_type": event_type,
            "payload": payload,
            "previous_event_sha256": self._final_event_sha256,
            "sequence": self._event_count,
            "version": BENCHMARK_CONCURRENT_COORDINATOR_VERSION,
        }
        encoded = canonical_json_bytes(wire) + b"\n"
        try:
            offset = 0
            while offset < len(encoded):
                written = os.write(descriptor, encoded[offset:])
                if written <= 0:
                    raise OSError("short write")
                offset += written
            os.fsync(descriptor)
        except OSError as error:
            raise _fail(
                ConcurrentExecutionCode.IO_ERROR,
                "coordinator",
                "durable WAL append failed",
            ) from error
        self._final_event_sha256 = _coordinator_event_sha256(wire)
        self._event_count += 1

    @property
    def admitted_indices(self) -> tuple[int, ...]:
        return tuple(range(self._admitted_count))

    @property
    def ready_indices(self) -> tuple[int, ...]:
        return tuple(sorted(self._ready))

    @property
    def in_flight_indices(self) -> tuple[int, ...]:
        return tuple(
            index for index in range(self._admitted_count) if index not in self._ready
        )

    @property
    def ready_units(self) -> tuple[ReadyUnit, ...]:
        return tuple(self._ready[index] for index in sorted(self._ready))

    def admit_next(self) -> UnitPermit:
        """Fsync the next schedule reservation before returning its lane permit."""

        self._require_open()
        if self._admitted_count - len(self._ready) >= self._contract.max_in_flight_units:
            raise _fail(
                ConcurrentExecutionCode.IN_FLIGHT_LIMIT,
                "admission",
                "maximum in-flight units are already admitted",
            )
        index = self._admitted_count
        if index >= len(self._reservations):
            raise _fail(
                ConcurrentExecutionCode.OUT_OF_ORDER,
                "admission",
                "the complete schedule is already admitted",
            )
        reservation = self._reservations[index]
        path = self._lane_path(self._root, index)
        _create_file(path)
        _fsync_directory(path.parent)
        self._append(
            "UNIT_ADMITTED",
            {
                "reservation": _reservation_to_dict(reservation),
                "schedule_index": index,
            },
        )
        sink = self._open_lane_sink(path, reservation, self._lane_policy, resume=False)
        permit = UnitPermit(index, reservation, path, sink)
        self._admitted_count += 1
        self._open_permits[index] = permit
        return permit

    def resume_permit(self, schedule_index: int) -> UnitPermit:
        """Reopen one admitted, non-ready lane only at a clean terminal boundary."""

        self._require_open()
        index = _exact_index(schedule_index, "schedule_index")
        if index >= self._admitted_count or index in self._ready:
            raise _fail(
                ConcurrentExecutionCode.OUT_OF_ORDER,
                "schedule_index",
                "does not name an active admitted unit",
            )
        existing = self._open_permits.get(index)
        if existing is not None:
            if existing.sink.has_open_attempt or existing.sink.has_open_intent:
                raise _fail(
                    ConcurrentExecutionCode.FAIL_CLOSED,
                    f"lane[{index}]",
                    "contains a non-terminal provider-call boundary",
                )
            if existing.sink.journal_events:
                raise _fail(
                    ConcurrentExecutionCode.FAIL_CLOSED,
                    f"lane[{index}]",
                    "contains observations without durable unit readiness",
                )
            return existing
        path = self._lane_path(self._root, index)
        try:
            sink = self._open_lane_sink(
                path,
                self._reservations[index],
                self._lane_policy,
                resume=True,
            )
        except (ArtifactError, OSError, ValueError) as error:
            raise _fail(
                ConcurrentExecutionCode.CORRUPT_LANE,
                f"lane[{index}]",
                "cannot be resumed as a canonical observation WAL",
            ) from error
        if sink.has_open_attempt or sink.has_open_intent:
            sink.close()
            raise _fail(
                ConcurrentExecutionCode.FAIL_CLOSED,
                f"lane[{index}]",
                "contains a non-terminal provider-call boundary",
            )
        if sink.journal_events:
            sink.close()
            raise _fail(
                ConcurrentExecutionCode.FAIL_CLOSED,
                f"lane[{index}]",
                "contains observations without durable unit readiness",
            )
        self._validate_lane_run_id(sink.journal_events, self._run_id, index)
        permit = UnitPermit(index, self._reservations[index], path, sink)
        self._open_permits[index] = permit
        return permit

    def mark_ready(
        self,
        schedule_index: int,
        *,
        unit_artifact_sha256: str | None = None,
    ) -> ReadyUnit:
        """Validate one terminal lane, then fsync its non-semantic completion."""

        self._require_open()
        index = _exact_index(schedule_index, "schedule_index")
        if index >= self._admitted_count or index in self._ready:
            raise _fail(
                ConcurrentExecutionCode.OUT_OF_ORDER,
                "schedule_index",
                "does not name one active admitted unit",
            )
        artifact_sha = (
            None
            if unit_artifact_sha256 is None
            else _sha256(unit_artifact_sha256, "unit_artifact_sha256")
        )
        permit = self._open_permits.pop(index, None)
        if permit is not None:
            permit.sink.close()
        unit = self._read_terminal_lane(
            self._root,
            index,
            self._reservations[index],
            self._run_id,
            self._lane_policy,
        )
        self._append(
            "UNIT_READY",
            {
                "lane_journal_sha256": unit.journal_sha256,
                "schedule_index": index,
                "unit_artifact_sha256": artifact_sha,
            },
        )
        exact = replace(unit, unit_artifact_sha256=artifact_sha)
        self._ready[index] = exact
        return exact

    def ready_prefix(self) -> tuple[ReadyUnit, ...]:
        """Return only the continuous schedule prefix currently safe to merge."""

        prefix: list[ReadyUnit] = []
        for index in range(len(self._reservations)):
            unit = self._ready.get(index)
            if unit is None:
                break
            prefix.append(unit)
        return tuple(prefix)

    def global_call_offset(self, schedule_index: int) -> int:
        """Return a unit's deterministic offset once every earlier lane is ready."""

        index = _exact_index(schedule_index, "schedule_index")
        prefix = self.ready_prefix()
        if index >= len(prefix):
            raise _fail(
                ConcurrentExecutionCode.NOT_READY,
                "schedule_index",
                "is not in the continuous ready prefix",
            )
        return sum(unit.local_call_count for unit in prefix[:index])

    def merged_events(self) -> tuple[JournalEvent, ...]:
        """Merge the ready prefix by schedule index and lane-local call index."""

        merged: list[JournalEvent] = []
        call_offset = 0
        for unit in self.ready_prefix():
            rebased = rebase_journal_events(
                unit.events,
                call_offset=call_offset,
                run_id=self._run_id,
            )
            merged.extend(rebased)
            call_offset += unit.local_call_count
        return tuple(merged)

    def write_merged_journal(self, path: Path, *, require_all_ready: bool = True) -> str:
        """Write the canonical global WAL through the existing durable sink validator."""

        if not isinstance(path, Path):
            raise _fail(
                ConcurrentExecutionCode.INVALID_INPUT,
                "path",
                "must be a Path",
            )
        prefix = self.ready_prefix()
        if require_all_ready and len(prefix) != len(self._reservations):
            raise _fail(
                ConcurrentExecutionCode.NOT_READY,
                "ready_units",
                "the complete schedule is not one continuous ready prefix",
            )
        _create_file(path)
        _fsync_directory(path.parent)
        sink = DurableObservationSink(
            path,
            max_calls=self._limits.logical_calls,
            max_attempts=self._limits.attempts,
            max_requested_output_tokens=self._limits.requested_output_tokens,
            max_attempt_reserved_output_tokens=(
                self._limits.attempt_reserved_output_tokens
            ),
            max_response_text_bytes=self._limits.response_text_bytes,
            max_transport_response_bytes=self._limits.transport_response_bytes,
            max_wall_microseconds=self._limits.wall_microseconds,
            allowed_returned_model_id=self._lane_policy.allowed_returned_model_id,
            require_successful_provider_evidence=(
                self._lane_policy.require_successful_provider_evidence
            ),
            billable_token_ceiling_per_attempt=self._lane_policy.sink_ceilings(),
        )
        try:
            for event in self.merged_events():
                if type(event) is CallIntent:
                    sink.write_intent(event)
                elif type(event) is AttemptIntent:
                    sink.write_attempt_intent(event)
                elif type(event) is AttemptResult:
                    sink.write_attempt_result(event)
                elif type(event) is CallResult:
                    sink.write_result(event)
                else:  # pragma: no cover - exhaustive internal union
                    raise AssertionError("unsupported observation event")
            return sink.journal_sha256
        finally:
            sink.close()


__all__ = [
    "BENCHMARK_CONCURRENT_COORDINATOR_VERSION",
    "MAX_CONCURRENT_UNITS",
    "CollectionExecutionContract",
    "ConcurrentExecutionCode",
    "ConcurrentExecutionError",
    "ConcurrentUnitCoordinator",
    "LaneObservationPolicy",
    "ReadyUnit",
    "UnitPermit",
    "rebase_journal_events",
    "rebase_observation_key",
    "rebase_raw_observation_key",
]
