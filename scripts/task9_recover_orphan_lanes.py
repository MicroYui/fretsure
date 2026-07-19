#!/usr/bin/env python3
"""Quarantine interrupted active lanes so a terminal Task 9 prefix can resume.

This is an operator-only, provider-free amendment.  It never edits the main journal,
coordinator WAL, completed units, or READY lanes.  A plan is generated without mutation;
``--apply`` then requires the exact plan SHA-256, archives every active lane and the prior
abort evidence, creates empty replacement lane WALs, and emits a canonical receipt.

The archived attempts remain part of the billing audit.  They are not observations for the
re-run units and their missing usage must never be treated as zero.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import re
import stat
import sys
import tempfile
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Final, NoReturn, cast

from fretsure.bench.artifacts import (
    BENCHMARK_WAL_VERSION,
    parse_canonical_json_bytes,
    parse_canonical_jsonl_bytes,
    wal_event_sha256,
)
from fretsure.bench.contracts import canonical_json_bytes, require_identifier, require_sha256

RECOVERY_PLAN_VERSION: Final = "benchmark-orphan-lane-recovery-plan@0.1.0"
RECOVERY_RECEIPT_VERSION: Final = "benchmark-orphan-lane-recovery-receipt@0.1.0"
COORDINATOR_VERSION: Final = "benchmark-concurrent-coordinator@0.1.0"
COORDINATOR_DOMAIN: Final = b"fretsure:benchmark-concurrent-coordinator@0.1.0\0"
ZERO_SHA256: Final = "0" * 64
EMPTY_SHA256: Final = hashlib.sha256(b"").hexdigest()
MAX_FILE_BYTES: Final = 256 * 1024 * 1024
MAX_LINES: Final = 2_000_000
MAX_ACTIVE_LANES: Final = 8
EVENT_TYPES: Final = (
    "ATTEMPT_INTENT",
    "ATTEMPT_RESULT",
    "CALL_INTENT",
    "CALL_RESULT",
)
TOKEN_FIELDS: Final = (
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "input_tokens",
    "output_tokens",
)
AUDIT_REASON = re.compile(r"concurrent_audit_([0-9a-f]{64})\Z")
GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")


class RecoveryError(ValueError):
    """Typed refusal for an invalid or ambiguous recovery state."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid Task 9 orphan recovery {field}: {detail}")


def _fail(field: str, detail: str) -> NoReturn:
    raise RecoveryError(field, detail)


def _object(
    value: object,
    field: str,
    keys: frozenset[str] | None = None,
) -> dict[str, object]:
    if type(value) is not dict:
        _fail(field, "must be an exact object")
    result = cast(dict[str, object], value)
    if keys is not None and frozenset(result) != keys:
        _fail(field, "must contain the exact required keys")
    return result


def _integer(
    value: object,
    field: str,
    *,
    minimum: int = 0,
    maximum: int = (1 << 63) - 1,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(field, f"must be an exact integer in {minimum}..{maximum}")
    return value


def _identifier(value: object, field: str) -> str:
    try:
        return require_identifier(value, path=field)
    except ValueError:
        _fail(field, "must be one bounded identifier")


def _sha256(value: object, field: str) -> str:
    try:
        return require_sha256(value, path=field)
    except ValueError:
        _fail(field, "must be one lowercase SHA-256 digest")


def _git_sha(value: object, field: str) -> str:
    if type(value) is not str or GIT_SHA.fullmatch(value) is None:
        _fail(field, "must be one lowercase 40-character Git SHA")
    return value


def _raw_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _regular_bytes(path: Path, field: str, *, allow_empty: bool = False) -> bytes:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise RecoveryError(field, "cannot be statted") from error
    if not stat.S_ISREG(metadata.st_mode):
        _fail(field, "must be one regular file")
    if not 0 <= metadata.st_size <= MAX_FILE_BYTES:
        _fail(field, f"must contain at most {MAX_FILE_BYTES} bytes")
    try:
        data = path.read_bytes()
    except OSError as error:
        raise RecoveryError(field, "cannot be read") from error
    if not allow_empty and not data:
        _fail(field, "must not be empty")
    return data


def _canonical_object(path: Path, field: str) -> tuple[bytes, dict[str, object]]:
    data = _regular_bytes(path, field)
    try:
        value = parse_canonical_json_bytes(data, max_bytes=MAX_FILE_BYTES)
    except (ValueError, RuntimeError) as error:
        raise RecoveryError(field, "must be canonical JSON") from error
    return data, _object(value, field)


def _coordinator_sha(value: object) -> str:
    return hashlib.sha256(COORDINATOR_DOMAIN + canonical_json_bytes(value)).hexdigest()


def _coordinator_state(path: Path) -> tuple[str, tuple[int, ...], dict[str, int]]:
    data = _regular_bytes(path, "coordinator")
    try:
        wires = parse_canonical_jsonl_bytes(
            data,
            max_bytes=MAX_FILE_BYTES,
            max_lines=MAX_LINES,
        )
    except (ValueError, RuntimeError) as error:
        raise RecoveryError("coordinator", "must be canonical JSONL") from error
    previous = ZERO_SHA256
    active: set[int] = set()
    admitted = 0
    counts: Counter[str] = Counter()
    for sequence, value in enumerate(wires):
        wire = _object(
            value,
            f"coordinator[{sequence}]",
            frozenset(
                {"event_type", "payload", "previous_event_sha256", "sequence", "version"}
            ),
        )
        if wire["version"] != COORDINATOR_VERSION:
            _fail(f"coordinator[{sequence}].version", "is unsupported")
        if wire["sequence"] != sequence:
            _fail(f"coordinator[{sequence}].sequence", "is not contiguous")
        if wire["previous_event_sha256"] != previous:
            _fail(f"coordinator[{sequence}].previous_event_sha256", "breaks the hash chain")
        event_type = wire["event_type"]
        if event_type not in ("UNIT_ADMITTED", "UNIT_READY"):
            _fail(f"coordinator[{sequence}].event_type", "is unsupported")
        payload = _object(wire["payload"], f"coordinator[{sequence}].payload")
        index = _integer(payload.get("schedule_index"), "coordinator.schedule_index")
        counts[event_type] += 1
        if event_type == "UNIT_ADMITTED":
            if index != admitted or index in active:
                _fail(f"coordinator[{sequence}]", "admission order is invalid")
            active.add(index)
            admitted += 1
        else:
            if index not in active:
                _fail(f"coordinator[{sequence}]", "READY does not match an active lane")
            active.remove(index)
        previous = _coordinator_sha(wire)
    if not 1 <= len(active) <= MAX_ACTIVE_LANES:
        _fail("coordinator.active_lanes", f"must contain 1..{MAX_ACTIVE_LANES} lanes")
    return _raw_sha256(data), tuple(sorted(active)), dict(sorted(counts.items()))


def _lane_audit(
    path: Path,
    artifact_path: Path,
    *,
    schedule_index: int,
) -> dict[str, object]:
    data = _regular_bytes(path, f"lane[{schedule_index}]", allow_empty=True)
    try:
        wires = parse_canonical_jsonl_bytes(
            data,
            max_bytes=MAX_FILE_BYTES,
            max_lines=MAX_LINES,
        )
    except (ValueError, RuntimeError) as error:
        raise RecoveryError(f"lane[{schedule_index}]", "must be canonical JSONL") from error
    previous = ZERO_SHA256
    counts: Counter[str] = Counter()
    token_totals = {field: 0 for field in TOKEN_FIELDS}
    token_observations = {field: 0 for field in TOKEN_FIELDS}
    complete_usage_records = 0
    for sequence, value in enumerate(wires):
        wire = _object(
            value,
            f"lane[{schedule_index}][{sequence}]",
            frozenset(
                {"event_type", "payload", "previous_event_sha256", "sequence", "version"}
            ),
        )
        if wire["version"] != BENCHMARK_WAL_VERSION:
            _fail(f"lane[{schedule_index}][{sequence}].version", "is unsupported")
        if wire["sequence"] != sequence or wire["previous_event_sha256"] != previous:
            _fail(f"lane[{schedule_index}][{sequence}]", "breaks the WAL hash chain")
        event_type = wire["event_type"]
        if event_type not in EVENT_TYPES:
            _fail(f"lane[{schedule_index}][{sequence}].event_type", "is unsupported")
        counts[event_type] += 1
        payload = _object(wire["payload"], f"lane[{schedule_index}][{sequence}].payload")
        if event_type == "CALL_RESULT":
            provider = _object(
                payload.get("provider"),
                f"lane[{schedule_index}][{sequence}].provider",
            )
            complete = True
            for field in TOKEN_FIELDS:
                raw = provider.get(field)
                if raw is None:
                    complete = False
                    continue
                token_totals[field] += _integer(
                    raw,
                    f"lane[{schedule_index}][{sequence}].provider.{field}",
                    maximum=1_000_000_000,
                )
                token_observations[field] += 1
            if complete:
                complete_usage_records += 1
        previous = wal_event_sha256(wire)
    event_counts = {event_type: counts[event_type] for event_type in EVENT_TYPES}
    open_calls = event_counts["CALL_INTENT"] - event_counts["CALL_RESULT"]
    open_attempts = event_counts["ATTEMPT_INTENT"] - event_counts["ATTEMPT_RESULT"]
    unknown_usage_attempts = event_counts["ATTEMPT_INTENT"] - complete_usage_records
    if min(open_calls, open_attempts, unknown_usage_attempts) < 0:
        _fail(f"lane[{schedule_index}]", "event or usage counts are inconsistent")
    artifact: dict[str, object] | None = None
    if artifact_path.exists():
        artifact_data = _regular_bytes(artifact_path, f"artifact[{schedule_index}]")
        artifact = {
            "byte_length": len(artifact_data),
            "raw_sha256": _raw_sha256(artifact_data),
            "source_path": str(artifact_path),
        }
    return {
        "artifact": artifact,
        "byte_length": len(data),
        "complete_usage_records": complete_usage_records,
        "event_counts": event_counts,
        "final_event_sha256": previous,
        "open_attempts": open_attempts,
        "open_calls": open_calls,
        "raw_sha256": _raw_sha256(data),
        "schedule_index": schedule_index,
        "source_path": str(path),
        "token_observations": token_observations,
        "token_totals": token_totals,
        "unknown_usage_attempts": unknown_usage_attempts,
    }


def _sum_supplement(lanes: tuple[dict[str, object], ...]) -> dict[str, object]:
    event_counts = {event_type: 0 for event_type in EVENT_TYPES}
    token_totals = {field: 0 for field in TOKEN_FIELDS}
    token_observations = {field: 0 for field in TOKEN_FIELDS}
    complete_usage_records = 0
    open_attempts = 0
    open_calls = 0
    unknown_usage_attempts = 0
    for lane in lanes:
        for event_type in EVENT_TYPES:
            event_counts[event_type] += _integer(
                _object(lane["event_counts"], "lane.event_counts")[event_type],
                f"lane.event_counts.{event_type}",
            )
        for field in TOKEN_FIELDS:
            token_totals[field] += _integer(
                _object(lane["token_totals"], "lane.token_totals")[field],
                f"lane.token_totals.{field}",
            )
            token_observations[field] += _integer(
                _object(lane["token_observations"], "lane.token_observations")[field],
                f"lane.token_observations.{field}",
            )
        complete_usage_records += _integer(
            lane["complete_usage_records"], "lane.complete_usage_records"
        )
        open_attempts += _integer(lane["open_attempts"], "lane.open_attempts")
        open_calls += _integer(lane["open_calls"], "lane.open_calls")
        unknown_usage_attempts += _integer(
            lane["unknown_usage_attempts"], "lane.unknown_usage_attempts"
        )
    return {
        "complete_usage_records": complete_usage_records,
        "event_counts": event_counts,
        "open_attempts": open_attempts,
        "open_calls": open_calls,
        "token_observations": token_observations,
        "token_totals": token_totals,
        "unknown_usage_attempts": unknown_usage_attempts,
    }


def _price_supplement(
    supplement: dict[str, object],
    *,
    pricing_contract: Path,
    expected_pricing_sha256: str,
    formal_billing_envelope: Path,
    expected_formal_billing_envelope_sha256: str,
) -> dict[str, object]:
    pricing_sha = _sha256(expected_pricing_sha256, "expected_pricing_sha256")
    envelope_sha = _sha256(
        expected_formal_billing_envelope_sha256,
        "expected_formal_billing_envelope_sha256",
    )
    pricing_bytes, pricing = _canonical_object(pricing_contract, "pricing_contract")
    envelope_bytes, envelope = _canonical_object(
        formal_billing_envelope,
        "formal_billing_envelope",
    )
    if _raw_sha256(pricing_bytes) != pricing_sha:
        _fail("pricing_contract", "does not match its expected SHA-256")
    if _raw_sha256(envelope_bytes) != envelope_sha:
        _fail("formal_billing_envelope", "does not match its expected SHA-256")
    if envelope.get("pricing_contract_raw_sha256") != pricing_sha:
        _fail("formal_billing_envelope", "does not bind the pricing contract")
    if pricing.get("ceil_each_component_per_attempt") is not False:
        _fail("pricing_contract", "requires aggregate component rounding")
    token_unit = _integer(pricing.get("token_unit"), "pricing_contract.token_unit", minimum=1)
    fixed = _integer(
        pricing.get("fixed_microunits_per_attempt"),
        "pricing_contract.fixed_microunits_per_attempt",
    )
    currency = pricing.get("currency")
    if type(currency) is not str or not 3 <= len(currency) <= 8:
        _fail("pricing_contract.currency", "is invalid")
    rates_raw = _object(
        pricing.get("rates_microunits_per_million_tokens"),
        "pricing_contract.rates",
    )
    ceilings_raw = _object(
        envelope.get("billable_token_ceiling_per_attempt"),
        "formal_billing_envelope.ceilings",
    )
    totals = _object(supplement.get("token_totals"), "supplement.token_totals")
    observations = _object(
        supplement.get("token_observations"),
        "supplement.token_observations",
    )
    event_counts = _object(supplement.get("event_counts"), "supplement.event_counts")
    attempts = _integer(
        event_counts.get("ATTEMPT_INTENT"),
        "supplement.event_counts.ATTEMPT_INTENT",
    )
    known_numerator = 0
    tight_upper_numerator = 0
    maximum_attempt_numerator = 0
    for field in TOKEN_FIELDS:
        rate = _integer(rates_raw.get(field), f"pricing_contract.rates.{field}")
        total = _integer(totals.get(field), f"supplement.token_totals.{field}")
        covered = _integer(
            observations.get(field),
            f"supplement.token_observations.{field}",
            maximum=attempts,
        )
        ceiling = _integer(
            ceilings_raw.get(field),
            f"formal_billing_envelope.ceilings.{field}",
        )
        if total > covered * ceiling:
            _fail(
                f"supplement.token_totals.{field}",
                "exceeds the covered-attempt billing ceiling",
            )
        known_numerator += total * rate
        tight_upper_numerator += (total + (attempts - covered) * ceiling) * rate
        maximum_attempt_numerator += ceiling * rate
    complete = _integer(
        supplement.get("complete_usage_records"),
        "supplement.complete_usage_records",
        maximum=attempts,
    )
    unknown = _integer(
        supplement.get("unknown_usage_attempts"),
        "supplement.unknown_usage_attempts",
        maximum=attempts,
    )
    if complete + unknown != attempts:
        _fail(
            "supplement.unknown_usage_attempts",
            "does not partition provider attempts with complete usage records",
        )
    known = attempts * fixed + (known_numerator + token_unit - 1) // token_unit
    per_missing = fixed + (maximum_attempt_numerator + token_unit - 1) // token_unit
    tight_upper = (
        attempts * fixed + (tight_upper_numerator + token_unit - 1) // token_unit
    )
    return {
        "currency": currency,
        "formal_billing_envelope_sha256": envelope_sha,
        "known_microunits": known,
        "maximum_missing_attempt_microunits": per_missing,
        "pricing_contract_sha256": pricing_sha,
        "tight_upper_microunits": tight_upper,
    }


def build_recovery_plan(
    *,
    output_dir: Path,
    pre_call_config: Path,
    expected_pre_call_sha256: str,
    formal_budget_gate: Path,
    expected_formal_budget_gate_sha256: str,
    pricing_contract: Path,
    expected_pricing_sha256: str,
    formal_billing_envelope: Path,
    expected_formal_billing_envelope_sha256: str,
    expected_abort_receipt_sha256: str,
    expected_execution_git_sha: str,
    expected_run_id: str,
    expected_control_rows: int,
    expected_active_lanes: int,
    recovery_id: str,
    authorization_id: str,
) -> dict[str, object]:
    """Return a deterministic, content-free recovery plan without mutation."""

    exact_pre_call_sha = _sha256(expected_pre_call_sha256, "expected_pre_call_sha256")
    exact_gate_sha = _sha256(
        expected_formal_budget_gate_sha256,
        "expected_formal_budget_gate_sha256",
    )
    exact_abort_sha = _sha256(expected_abort_receipt_sha256, "expected_abort_receipt_sha256")
    exact_execution = _git_sha(expected_execution_git_sha, "expected_execution_git_sha")
    exact_run_id = _identifier(expected_run_id, "expected_run_id")
    exact_recovery_id = _identifier(recovery_id, "recovery_id")
    exact_authorization = _identifier(authorization_id, "authorization_id")
    controls = _integer(expected_control_rows, "expected_control_rows", maximum=100_000)
    active_limit = _integer(
        expected_active_lanes,
        "expected_active_lanes",
        minimum=1,
        maximum=MAX_ACTIVE_LANES,
    )
    if (output_dir / "canonical").exists():
        _fail("output_dir", "already contains canonical output")
    pre_call = _regular_bytes(pre_call_config, "pre_call_config")
    gate = _regular_bytes(formal_budget_gate, "formal_budget_gate")
    if _raw_sha256(pre_call) != exact_pre_call_sha:
        _fail("pre_call_config", "does not match its expected SHA-256")
    if _raw_sha256(gate) != exact_gate_sha:
        _fail("formal_budget_gate", "does not match its expected SHA-256")
    abort_path = output_dir / "abort-receipt.json"
    abort_bytes, abort = _canonical_object(abort_path, "abort_receipt")
    if _raw_sha256(abort_bytes) != exact_abort_sha:
        _fail("abort_receipt", "does not match its expected SHA-256")
    if abort.get("status") != "INCOMPLETE" or abort.get("run_id") != exact_run_id:
        _fail("abort_receipt", "does not bind the expected terminal run")
    reason = abort.get("reason_code")
    if type(reason) is not str or (match := AUDIT_REASON.fullmatch(reason)) is None:
        _fail("abort_receipt.reason_code", "must bind one concurrent audit")
    audit_sha = match.group(1)
    audit_name = f"concurrent-abort-audit-{audit_sha}.json"
    audit_path = output_dir / audit_name
    audit_bytes = _regular_bytes(audit_path, "concurrent_abort_audit")
    if _raw_sha256(audit_bytes) != audit_sha:
        _fail("concurrent_abort_audit", "does not match the receipt-bound SHA-256")
    coordinator_path = output_dir / "staging" / "concurrent" / "coordinator.jsonl"
    concurrent_config = _regular_bytes(
        coordinator_path.parent / "config.json",
        "concurrent_config",
    )
    coordinator_sha, active_indices, coordinator_counts = _coordinator_state(coordinator_path)
    if len(active_indices) != active_limit:
        _fail("coordinator.active_lanes", f"must contain exactly {active_limit} lanes")
    lane_root = coordinator_path.parent / "lanes"
    artifact_root = coordinator_path.parent / "unit-artifacts"
    lanes = tuple(
        _lane_audit(
            lane_root / f"{index:08d}.jsonl",
            artifact_root / f"{index:08d}.json",
            schedule_index=index,
        )
        for index in active_indices
    )
    observed_rows = _integer(abort.get("observed_rows"), "abort_receipt.observed_rows")
    observed_calls = _integer(abort.get("observed_calls"), "abort_receipt.observed_calls")
    if observed_rows < controls:
        _fail("abort_receipt.observed_rows", "is smaller than the control-row prefix")
    config_path = output_dir / "config.json"
    journal_path = output_dir / "journal.jsonl"
    config = _regular_bytes(config_path, "config")
    journal = _regular_bytes(journal_path, "journal", allow_empty=True)
    tool_bytes = Path(__file__).resolve().read_bytes()
    supplement = _sum_supplement(lanes)
    supplement["cost"] = _price_supplement(
        supplement,
        pricing_contract=pricing_contract,
        expected_pricing_sha256=expected_pricing_sha256,
        formal_billing_envelope=formal_billing_envelope,
        expected_formal_billing_envelope_sha256=(
            expected_formal_billing_envelope_sha256
        ),
    )
    return {
        "authorization_id": exact_authorization,
        "bindings": {
            "execution_git_sha": exact_execution,
            "formal_budget_gate_sha256": exact_gate_sha,
            "formal_billing_envelope_sha256": _sha256(
                expected_formal_billing_envelope_sha256,
                "expected_formal_billing_envelope_sha256",
            ),
            "pre_call_sha256": exact_pre_call_sha,
            "pricing_contract_sha256": _sha256(
                expected_pricing_sha256,
                "expected_pricing_sha256",
            ),
        },
        "policy": "quarantine_active_lane_and_retry_complete_unit",
        "protocol_deviation": True,
        "recovery_id": exact_recovery_id,
        "run_id": exact_run_id,
        "schema": RECOVERY_PLAN_VERSION,
        "source": {
            "abort_audit_filename": audit_name,
            "abort_audit_sha256": audit_sha,
            "abort_receipt_sha256": exact_abort_sha,
            "config_sha256": _raw_sha256(config),
            "concurrent_config_sha256": _raw_sha256(concurrent_config),
            "control_rows": controls,
            "coordinator_event_counts": coordinator_counts,
            "coordinator_sha256": coordinator_sha,
            "durable_network_units": observed_rows - controls,
            "journal_sha256": _raw_sha256(journal),
            "observed_calls": observed_calls,
            "observed_rows": observed_rows,
        },
        "supplement": supplement,
        "tool_sha256": _raw_sha256(tool_bytes),
        "active_lanes": list(lanes),
    }


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_new(path: Path, data: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail(str(path), "could not be written completely")
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as error:
            raise RecoveryError(str(path), "already exists") from error
        os.unlink(temporary)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _verify_hash(path: Path, expected: str, field: str, *, allow_empty: bool = False) -> None:
    data = _regular_bytes(path, field, allow_empty=allow_empty)
    if _raw_sha256(data) != expected:
        _fail(field, "has drifted from the recovery plan")


def _move_file(source: Path, archive: Path, expected_sha256: str, field: str) -> None:
    if archive.exists():
        _verify_hash(archive, expected_sha256, f"{field}.archive", allow_empty=True)
        if source.exists():
            _fail(field, "exists in both source and quarantine")
        return
    _verify_hash(source, expected_sha256, field, allow_empty=True)
    archive.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.replace(source, archive)
    _fsync_directory(source.parent)
    _fsync_directory(archive.parent)


def _move_lane_and_create_empty(
    source: Path,
    archive: Path,
    expected_sha256: str,
    field: str,
) -> None:
    if archive.exists():
        _verify_hash(archive, expected_sha256, f"{field}.archive", allow_empty=True)
        if source.exists():
            _verify_hash(source, EMPTY_SHA256, f"{field}.replacement", allow_empty=True)
            return
    else:
        _verify_hash(source, expected_sha256, field, allow_empty=True)
        archive.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.replace(source, archive)
        _fsync_directory(source.parent)
        _fsync_directory(archive.parent)
    if not source.exists():
        _write_new(source, b"")


def _verify_source_or_archive(
    source: Path,
    archive: Path,
    expected_sha256: str,
    field: str,
    *,
    replacement_empty: bool = False,
) -> None:
    if archive.exists():
        _verify_hash(archive, expected_sha256, f"{field}.archive", allow_empty=True)
        if replacement_empty:
            if source.exists():
                _verify_hash(source, EMPTY_SHA256, f"{field}.replacement", allow_empty=True)
        elif source.exists():
            _fail(field, "exists in both source and quarantine")
        return
    _verify_hash(source, expected_sha256, field, allow_empty=True)


def _verify_recovery_sources(output_dir: Path, root: Path, plan: dict[str, object]) -> None:
    if (output_dir / "canonical").exists():
        _fail("output_dir", "already contains canonical output")
    source_state = _object(plan.get("source"), "recovery_plan.source")
    _verify_hash(
        output_dir / "config.json",
        _sha256(source_state.get("config_sha256"), "source.config_sha256"),
        "config",
    )
    _verify_hash(
        output_dir / "journal.jsonl",
        _sha256(source_state.get("journal_sha256"), "source.journal_sha256"),
        "journal",
        allow_empty=True,
    )
    _verify_hash(
        output_dir / "staging" / "concurrent" / "config.json",
        _sha256(
            source_state.get("concurrent_config_sha256"),
            "source.concurrent_config_sha256",
        ),
        "concurrent_config",
    )
    _verify_hash(
        output_dir / "staging" / "concurrent" / "coordinator.jsonl",
        _sha256(source_state.get("coordinator_sha256"), "source.coordinator_sha256"),
        "coordinator",
    )
    lanes_raw = plan.get("active_lanes")
    if type(lanes_raw) is not list:
        _fail("recovery_plan.active_lanes", "must be an array")
    for raw_lane in cast(list[object], lanes_raw):
        lane = _object(raw_lane, "recovery_plan.active_lane")
        index = _integer(lane.get("schedule_index"), "lane.schedule_index")
        _verify_source_or_archive(
            output_dir / "staging" / "concurrent" / "lanes" / f"{index:08d}.jsonl",
            root / "lanes" / f"{index:08d}.jsonl",
            _sha256(lane.get("raw_sha256"), "lane.raw_sha256"),
            f"lane[{index}]",
            replacement_empty=True,
        )
        artifact_source = (
            output_dir
            / "staging"
            / "concurrent"
            / "unit-artifacts"
            / f"{index:08d}.json"
        )
        artifact_archive = root / "unit-artifacts" / f"{index:08d}.json"
        artifact_raw = lane.get("artifact")
        if artifact_raw is None:
            if artifact_source.exists() or artifact_archive.exists():
                _fail(f"artifact[{index}]", "appeared after the recovery plan")
        else:
            artifact = _object(artifact_raw, f"lane[{index}].artifact")
            _verify_source_or_archive(
                artifact_source,
                artifact_archive,
                _sha256(artifact.get("raw_sha256"), "artifact.raw_sha256"),
                f"artifact[{index}]",
            )
    audit_name_raw = source_state.get("abort_audit_filename")
    if type(audit_name_raw) is not str or Path(audit_name_raw).name != audit_name_raw:
        _fail("recovery_plan.source.abort_audit_filename", "is invalid")
    _verify_source_or_archive(
        output_dir / audit_name_raw,
        root / audit_name_raw,
        _sha256(source_state.get("abort_audit_sha256"), "source.abort_audit_sha256"),
        "concurrent_abort_audit",
    )
    _verify_source_or_archive(
        output_dir / "abort-receipt.json",
        root / "abort-receipt.json",
        _sha256(source_state.get("abort_receipt_sha256"), "source.abort_receipt_sha256"),
        "abort_receipt",
    )


@contextmanager
def _writer_lock(output_dir: Path) -> Iterator[None]:
    lock_path = output_dir / ".writer.lock"
    try:
        descriptor = os.open(lock_path, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
    except OSError as error:
        raise RecoveryError("writer_lock", "cannot be opened") from error
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RecoveryError("writer_lock", "is held by an active collector") from error
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _recovery_root(output_dir: Path, recovery_id: str) -> Path:
    return output_dir / "staging" / "concurrent" / "orphan-recoveries" / recovery_id


def _load_plan(path: Path) -> tuple[bytes, dict[str, object]]:
    data, plan = _canonical_object(path, "recovery_plan")
    if plan.get("schema") != RECOVERY_PLAN_VERSION:
        _fail("recovery_plan.schema", "is unsupported")
    return data, plan


def _roll_forward(output_dir: Path, root: Path, plan: dict[str, object]) -> dict[str, object]:
    _verify_recovery_sources(output_dir, root, plan)
    lanes_raw = plan.get("active_lanes")
    if type(lanes_raw) is not list:
        _fail("recovery_plan.active_lanes", "must be an array")
    lanes = cast(list[object], lanes_raw)
    for position, raw_lane in enumerate(lanes):
        lane = _object(raw_lane, f"recovery_plan.active_lanes[{position}]")
        index = _integer(lane.get("schedule_index"), "lane.schedule_index")
        source = output_dir / "staging" / "concurrent" / "lanes" / f"{index:08d}.jsonl"
        archive = root / "lanes" / f"{index:08d}.jsonl"
        _move_lane_and_create_empty(
            source,
            archive,
            _sha256(lane.get("raw_sha256"), "lane.raw_sha256"),
            f"lane[{index}]",
        )
        artifact_raw = lane.get("artifact")
        if artifact_raw is not None:
            artifact = _object(artifact_raw, f"lane[{index}].artifact")
            artifact_source = (
                output_dir
                / "staging"
                / "concurrent"
                / "unit-artifacts"
                / f"{index:08d}.json"
            )
            _move_file(
                artifact_source,
                root / "unit-artifacts" / f"{index:08d}.json",
                _sha256(artifact.get("raw_sha256"), "artifact.raw_sha256"),
                f"artifact[{index}]",
            )
    source_state = _object(plan.get("source"), "recovery_plan.source")
    audit_name_raw = source_state.get("abort_audit_filename")
    if type(audit_name_raw) is not str or Path(audit_name_raw).name != audit_name_raw:
        _fail("recovery_plan.source.abort_audit_filename", "is invalid")
    audit_name = audit_name_raw
    _move_file(
        output_dir / audit_name,
        root / audit_name,
        _sha256(
            source_state.get("abort_audit_sha256"),
            "source.abort_audit_sha256",
        ),
        "concurrent_abort_audit",
    )
    # The abort receipt is the transaction commit gate and therefore moves last.
    _move_file(
        output_dir / "abort-receipt.json",
        root / "abort-receipt.json",
        _sha256(
            source_state.get("abort_receipt_sha256"),
            "source.abort_receipt_sha256",
        ),
        "abort_receipt",
    )
    post = {
        "abort_receipt_at_root": False,
        "active_lane_count": len(lanes),
        "active_lane_replacement_sha256": EMPTY_SHA256,
        "config_sha256": _sha256(
            source_state.get("config_sha256"),
            "source.config_sha256",
        ),
        "coordinator_sha256": _sha256(
            source_state.get("coordinator_sha256"),
            "source.coordinator_sha256",
        ),
        "journal_sha256": _sha256(
            source_state.get("journal_sha256"),
            "source.journal_sha256",
        ),
    }
    plan_bytes = canonical_json_bytes(plan)
    return {
        "plan_sha256": _raw_sha256(plan_bytes),
        "post": post,
        "recovery_id": _identifier(plan.get("recovery_id"), "recovery_plan.recovery_id"),
        "schema": RECOVERY_RECEIPT_VERSION,
        "status": "APPLIED",
    }


def check_applied_recovery(
    *,
    output_dir: Path,
    recovery_id: str,
    expected_plan_sha256: str,
) -> dict[str, object]:
    """Verify one applied recovery without changing it."""

    root = _recovery_root(output_dir, _identifier(recovery_id, "recovery_id"))
    plan_bytes, plan = _load_plan(root / "plan.json")
    exact_plan_sha = _sha256(expected_plan_sha256, "expected_plan_sha256")
    if _raw_sha256(plan_bytes) != exact_plan_sha:
        _fail("recovery_plan", "does not match the expected SHA-256")
    receipt_bytes, receipt = _canonical_object(root / "receipt.json", "recovery_receipt")
    if receipt.get("schema") != RECOVERY_RECEIPT_VERSION or receipt.get("status") != "APPLIED":
        _fail("recovery_receipt", "is not one applied receipt")
    if receipt.get("plan_sha256") != exact_plan_sha:
        _fail("recovery_receipt.plan_sha256", "does not bind the plan")
    lanes_raw = plan.get("active_lanes")
    if type(lanes_raw) is not list:
        _fail("recovery_plan.active_lanes", "must be an array")
    for raw_lane in cast(list[object], lanes_raw):
        lane = _object(raw_lane, "recovery_plan.active_lane")
        index = _integer(lane.get("schedule_index"), "lane.schedule_index")
        _verify_hash(
            root / "lanes" / f"{index:08d}.jsonl",
            _sha256(lane.get("raw_sha256"), "lane.raw_sha256"),
            f"lane[{index}].archive",
            allow_empty=True,
        )
        artifact_raw = lane.get("artifact")
        artifact_source = (
            output_dir
            / "staging"
            / "concurrent"
            / "unit-artifacts"
            / f"{index:08d}.json"
        )
        artifact_archive = root / "unit-artifacts" / f"{index:08d}.json"
        if artifact_raw is None:
            if artifact_source.exists() or artifact_archive.exists():
                _fail(f"artifact[{index}]", "must remain absent")
        else:
            artifact = _object(artifact_raw, f"lane[{index}].artifact")
            if artifact_source.exists():
                _fail(f"artifact[{index}]", "still exists at the active source path")
            _verify_hash(
                artifact_archive,
                _sha256(artifact.get("raw_sha256"), "artifact.raw_sha256"),
                f"artifact[{index}].archive",
            )
        _verify_hash(
            output_dir / "staging" / "concurrent" / "lanes" / f"{index:08d}.jsonl",
            EMPTY_SHA256,
            f"lane[{index}].replacement",
            allow_empty=True,
        )
    source = _object(plan.get("source"), "recovery_plan.source")
    if (output_dir / "abort-receipt.json").exists():
        _fail("abort_receipt", "still exists at the output root")
    _verify_hash(
        root / "abort-receipt.json",
        _sha256(source.get("abort_receipt_sha256"), "source.abort_receipt_sha256"),
        "abort_receipt.archive",
    )
    audit_name_raw = source.get("abort_audit_filename")
    if type(audit_name_raw) is not str or Path(audit_name_raw).name != audit_name_raw:
        _fail("recovery_plan.source.abort_audit_filename", "is invalid")
    _verify_hash(
        root / audit_name_raw,
        _sha256(source.get("abort_audit_sha256"), "source.abort_audit_sha256"),
        "concurrent_abort_audit.archive",
    )
    _verify_hash(
        output_dir / "config.json",
        _sha256(source.get("config_sha256"), "source.config_sha256"),
        "config",
    )
    _verify_hash(
        output_dir / "journal.jsonl",
        _sha256(source.get("journal_sha256"), "source.journal_sha256"),
        "journal",
        allow_empty=True,
    )
    _verify_hash(
        output_dir / "staging" / "concurrent" / "config.json",
        _sha256(
            source.get("concurrent_config_sha256"),
            "source.concurrent_config_sha256",
        ),
        "concurrent_config",
    )
    _verify_hash(
        output_dir / "staging" / "concurrent" / "coordinator.jsonl",
        _sha256(source.get("coordinator_sha256"), "source.coordinator_sha256"),
        "coordinator",
    )
    return {
        "plan_sha256": exact_plan_sha,
        "receipt_sha256": _raw_sha256(receipt_bytes),
        "recovery_id": recovery_id,
        "status": "APPLIED",
    }


def apply_recovery(
    *,
    output_dir: Path,
    plan: dict[str, object],
    expected_plan_sha256: str,
) -> dict[str, object]:
    """Apply or roll forward an exact recovery plan under the writer lock."""

    plan_bytes = canonical_json_bytes(plan)
    plan_sha = _raw_sha256(plan_bytes)
    if plan_sha != _sha256(expected_plan_sha256, "expected_plan_sha256"):
        _fail("recovery_plan", "does not match the explicitly approved SHA-256")
    recovery_id = _identifier(plan.get("recovery_id"), "recovery_plan.recovery_id")
    root = _recovery_root(output_dir, recovery_id)
    if (root / "receipt.json").exists():
        return check_applied_recovery(
            output_dir=output_dir,
            recovery_id=recovery_id,
            expected_plan_sha256=plan_sha,
        )
    # Refuse source drift before even preparing the transaction on disk.  The
    # roll-forward repeats this check immediately before the first quarantine move.
    _verify_recovery_sources(output_dir, root, plan)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    plan_path = root / "plan.json"
    if plan_path.exists():
        stored_bytes, stored = _load_plan(plan_path)
        if _raw_sha256(stored_bytes) != plan_sha:
            _fail("recovery_plan", "differs from the prepared transaction")
    else:
        _write_new(plan_path, plan_bytes)
    receipt = _roll_forward(output_dir, root, plan)
    receipt_path = root / "receipt.json"
    if not receipt_path.exists():
        _write_new(receipt_path, canonical_json_bytes(receipt))
    return check_applied_recovery(
        output_dir=output_dir,
        recovery_id=recovery_id,
        expected_plan_sha256=plan_sha,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="task9-recover-orphan-lanes")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pre-call-config", type=Path, required=True)
    parser.add_argument("--expected-pre-call-sha256", required=True)
    parser.add_argument("--formal-budget-gate", type=Path, required=True)
    parser.add_argument("--expected-formal-budget-gate-sha256", required=True)
    parser.add_argument("--pricing-contract", type=Path, required=True)
    parser.add_argument("--expected-pricing-sha256", required=True)
    parser.add_argument("--formal-billing-envelope", type=Path, required=True)
    parser.add_argument("--expected-formal-billing-envelope-sha256", required=True)
    parser.add_argument("--expected-abort-receipt-sha256", required=True)
    parser.add_argument("--expected-execution-git-sha", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--expected-control-rows", type=int, default=503)
    parser.add_argument("--expected-active-lanes", type=int, default=4)
    parser.add_argument("--recovery-id", required=True)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--expected-plan-sha256")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        with _writer_lock(args.output_dir):
            if args.check:
                if args.expected_plan_sha256 is None:
                    _fail("expected_plan_sha256", "is required with --check")
                result = check_applied_recovery(
                    output_dir=args.output_dir,
                    recovery_id=args.recovery_id,
                    expected_plan_sha256=args.expected_plan_sha256,
                )
            else:
                prepared_path = _recovery_root(
                    args.output_dir,
                    _identifier(args.recovery_id, "recovery_id"),
                ) / "plan.json"
                if args.apply and prepared_path.exists():
                    _prepared_bytes, plan = _load_plan(prepared_path)
                else:
                    plan = build_recovery_plan(
                        output_dir=args.output_dir,
                        pre_call_config=args.pre_call_config,
                        expected_pre_call_sha256=args.expected_pre_call_sha256,
                        formal_budget_gate=args.formal_budget_gate,
                        expected_formal_budget_gate_sha256=(
                            args.expected_formal_budget_gate_sha256
                        ),
                        pricing_contract=args.pricing_contract,
                        expected_pricing_sha256=args.expected_pricing_sha256,
                        formal_billing_envelope=args.formal_billing_envelope,
                        expected_formal_billing_envelope_sha256=(
                            args.expected_formal_billing_envelope_sha256
                        ),
                        expected_abort_receipt_sha256=args.expected_abort_receipt_sha256,
                        expected_execution_git_sha=args.expected_execution_git_sha,
                        expected_run_id=args.expected_run_id,
                        expected_control_rows=args.expected_control_rows,
                        expected_active_lanes=args.expected_active_lanes,
                        recovery_id=args.recovery_id,
                        authorization_id=args.authorization_id,
                    )
                plan_sha = _raw_sha256(canonical_json_bytes(plan))
                if args.plan:
                    result = {"plan": plan, "plan_sha256": plan_sha}
                else:
                    if args.expected_plan_sha256 is None:
                        _fail("expected_plan_sha256", "is required with --apply")
                    result = apply_recovery(
                        output_dir=args.output_dir,
                        plan=plan,
                        expected_plan_sha256=args.expected_plan_sha256,
                    )
    except (OSError, RecoveryError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    sys.stdout.buffer.write(canonical_json_bytes(result) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
