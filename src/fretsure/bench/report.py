"""Strict benchmark-v2 row construction, replay scoring, and aggregation.

Rows retain the deterministic state needed to resume collection and to replay a
report without contacting an LLM.  ``FULL_RESCORE`` reruns solver, oracle, and
faithfulness checks before aggregation; ``FAST_REAGGREGATE`` is explicitly labeled
and trusts the stored score payload while still validating every row/blob binding.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from dataclasses import dataclass, fields, replace
from enum import Enum, StrEnum
from fractions import Fraction
from typing import Any, Final, Literal, NoReturn, cast

from fretsure.agent.arranger import (
    ArrangeGoal,
    ProposalOutcome,
    ProposalStatus,
    arrangement_solver_ir,
    arrangement_source_context_sha256,
)
from fretsure.agent.critic import CRITIC_MAX_TOKENS, CriticOutcome, CriticScore, CriticStatus
from fretsure.agent.harness import (
    ArrangePool,
    CandidateStatus,
    CandidateTrajectory,
    CandidateWorkCounts,
    TraceStepSnapshot,
    best_of_k,
)
from fretsure.agent.repair import REPAIR_MAX_TOKENS, RepairSnapshot
from fretsure.agent.trace import StepKind, Trace, TraceEvent, TraceStep
from fretsure.arrange.propose import propose_fingerstyle
from fretsure.bench.artifacts import (
    BenchmarkManifest,
    BenchmarkReceipt,
    BenchmarkRow,
    BlobKind,
    BlobRecord,
    BlobRef,
    CompletionStatus,
    ObservationKey,
    RowKey,
    RowType,
    SanitizedObservations,
    blob_record_to_dict,
    build_blob_record,
    build_row,
    canonical_jsonl_bytes,
    canonical_table_sha256,
    manifest_sha256,
    parse_canonical_json_bytes,
    receipt_sha256,
    row_to_dict,
)
from fretsure.bench.baselines import (
    OPTIONAL_BASELINE_AVAILABILITY,
    PureSolverOutcome,
    PureSolverStatus,
    RawLLMOutcome,
    RawObservationKey,
    RawParseCode,
    RawStatus,
    build_raw_baseline_request,
)
from fretsure.bench.contracts import (
    BENCHMARK_REPORT_VERSION,
    canonical_json_bytes,
    canonical_sha256,
)
from fretsure.bench.corpus import (
    CorpusItem,
    corpus_sha256,
    ir_to_notegraph,
    notegraph_sha256,
    notegraph_to_ir,
)
from fretsure.bench.experiment import (
    EXPERIMENT_N_SAMPLES,
    FULL_SELECTION_K,
    RELIABILITY_K_VALUES,
    SEARCH_K_VALUES,
    BudgetMatchStatus,
    CollectionArm,
    CompletedExperimentUnit,
    CompletedPureSolver,
    ExperimentCollection,
    ExperimentPlan,
    ExperimentResumeState,
    ObservationLedger,
    item_pair_id,
    sample_pair_id,
)
from fretsure.bench.observe import CallFailureCode, CallIntent, CallResult, CallStage
from fretsure.bench.reliability import pass_at_k, pass_hat_k_item
from fretsure.bench.stats import (
    EstimateStatus,
    FamilyBootstrapEndpoint,
    FamilyDelta,
    FamilyValue,
    NamedPValue,
    clopper_pearson_interval,
    exact_mcnemar,
    family_cluster_bootstrap_mean,
    family_cluster_bootstrap_means,
    holm_adjust,
    paired_sign_flip_test,
    wilson_interval,
)
from fretsure.ir import Note
from fretsure.metrics.fidelity import (
    FAITHFULNESS_DIMENSIONS,
    FaithfulnessDimension,
    FaithfulnessGate,
    Fidelity,
    faithfulness,
    faithfulness_dimensions,
    fidelity,
)
from fretsure.oracle.core import CHECKER_VERSION, OracleResult, check_playability
from fretsure.oracle.input import ORACLE_INPUT_SCHEMA_VERSION, ensure_profile
from fretsure.oracle.profiles import LARGE_HAND, SMALL_HAND, Profile
from fretsure.solver.api import Infeasible, InfeasibleCode
from fretsure.solver.score import solve_fingering_score as solve_fingering
from fretsure.tab import Tab, tab_to_json, validated_tab_from_json

REPAIR_SESOI: Final = 0.10
SEARCH_SESOI: Final = 0.05
CHEAP_GUARD_SESOI: Final = 0.05
_FRACTION = re.compile(r"-?(?:0|[1-9][0-9]*)(?:/[1-9][0-9]*)?\Z")


class ReportInputError(ValueError):
    """A row, blob, observation, or replay input violated the report contract."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid benchmark report {field}: {detail}")


class ReplayMode(StrEnum):
    FULL_RESCORE = "full_rescore"
    FAST_REAGGREGATE = "fast_reaggregate"


class CapabilityDecision(StrEnum):
    KEEP = "KEEP"
    NOT_KEPT = "NOT_KEPT"
    INCONCLUSIVE = "INCONCLUSIVE"
    PROBATION_COST_UNKNOWN = "PROBATION_COST_UNKNOWN"
    HUMAN_BLOCKED_PROBATION = "HUMAN_BLOCKED_PROBATION"


def _fail(field: str, detail: str) -> NoReturn:
    raise ReportInputError(field, detail)


def _exact_dict(value: object, field: str, keys: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict:
        _fail(field, "must be an exact object")
    result = cast(dict[object, object], value)
    if frozenset(result) != keys or any(type(key) is not str for key in result):
        _fail(field, "must contain the exact keys")
    return cast(dict[str, object], result)


def _exact_list(value: object, field: str, *, maximum: int = 10_000) -> list[object]:
    if type(value) is not list or len(value) > maximum:
        _fail(field, f"must be an exact bounded array of at most {maximum} values")
    return cast(list[object], value)


def _text(value: object, field: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if type(value) is not str or not value or len(value) > 4096:
        _fail(field, "must be one nonempty bounded string")
    return value


def _boolean(value: object, field: str) -> bool:
    if type(value) is not bool:
        _fail(field, "must be an exact bool")
    return value


def _integer(
    value: object,
    field: str,
    *,
    minimum: int = 0,
    maximum: int = 1_000_000,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(field, f"must be an exact integer in {minimum}..{maximum}")
    return value


def _optional_integer(value: object, field: str) -> int | None:
    return None if value is None else _integer(value, field)


def _number(
    value: object,
    field: str,
    *,
    nullable: bool = False,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> float | None:
    if value is None and nullable:
        return None
    if type(value) is not int and type(value) is not float:
        _fail(field, "must be an exact finite number")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        _fail(field, f"must be finite in {minimum}..{maximum}")
    return result


def _sha(value: object, field: str, *, nullable: bool = False) -> str | None:
    text = _text(value, field, nullable=nullable)
    if text is None:
        return None
    if re.fullmatch(r"[0-9a-f]{64}", text) is None:
        _fail(field, "must be one lowercase SHA-256 digest")
    return text


def _fraction_token(value: Fraction) -> str:
    return (
        str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"
    )


def _parse_fraction(value: object, field: str, *, positive: bool) -> Fraction:
    token = _text(value, field)
    assert token is not None
    if _FRACTION.fullmatch(token) is None:
        _fail(field, "must be a canonical fraction token")
    result = Fraction(token)
    if _fraction_token(result) != token:
        _fail(field, "fraction token must be reduced and canonical")
    if (positive and result <= 0) or (not positive and result < 0):
        _fail(field, "fraction has the wrong sign")
    return result


@dataclass(frozen=True, slots=True)
class ArtifactRowBundle:
    rows: tuple[BenchmarkRow, ...]
    blobs: tuple[BlobRecord, ...]

    def __post_init__(self) -> None:
        if self.rows != tuple(sorted(self.rows, key=lambda value: value.sort_key)):
            _fail("rows", "must be canonically sorted")
        if len({value.key for value in self.rows}) != len(self.rows):
            _fail("rows", "row keys must be unique")
        if self.blobs != tuple(sorted(self.blobs, key=lambda value: value.ref.sort_key)):
            _fail("blobs", "must be canonically sorted")
        if len({value.ref for value in self.blobs}) != len(self.blobs):
            _fail("blobs", "blob references must be unique")


@dataclass(frozen=True, slots=True)
class ProfileVerdict:
    profile_version: str
    profile_fingerprint: str
    verdict: str | None


@dataclass(frozen=True, slots=True)
class CheckpointScore:
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
    profiles: tuple[ProfileVerdict, ...]


@dataclass(frozen=True, slots=True)
class NamedCheckpoint:
    name: str
    score: CheckpointScore

    @property
    def profiles(self) -> tuple[ProfileVerdict, ...]:
        return self.score.profiles


@dataclass(frozen=True, slots=True)
class RescoredRow:
    row: BenchmarkRow
    checkpoints: tuple[NamedCheckpoint, ...]
    trajectory: CandidateTrajectory | None
    raw_outcome: RawLLMOutcome | None
    pure_outcome: PureSolverOutcome | None


@dataclass(frozen=True, slots=True)
class RescoredRowBundle:
    mode: ReplayMode
    rows: tuple[RescoredRow, ...]


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    run_id: str
    mode: ReplayMode
    wire_json: bytes

    @property
    def sha256(self) -> str:
        return canonical_sha256(BENCHMARK_REPORT_VERSION, report_to_dict(self))


@dataclass(frozen=True, slots=True)
class ReportPublicationBindings:
    run_id: str
    manifest_sha256: str
    config_sha256: str
    receipt_sha256: str
    receipt_status: str
    corpus_sha256: str
    analysis_code_sha256: str
    journal_sha256: str
    rows_sha256: str
    blobs_sha256: str
    observations_sha256: str
    expected_rows: int
    observed_rows: int
    maximum_calls: int
    observed_calls: int

    def __post_init__(self) -> None:
        _text(self.run_id, "publication_bindings.run_id")
        for name in (
            "manifest_sha256",
            "config_sha256",
            "receipt_sha256",
            "corpus_sha256",
            "analysis_code_sha256",
            "journal_sha256",
            "rows_sha256",
            "blobs_sha256",
            "observations_sha256",
        ):
            _sha(getattr(self, name), f"publication_bindings.{name}")
        if self.receipt_status != CompletionStatus.COMPLETE.value:
            _fail("publication_bindings.receipt_status", "must be COMPLETE")
        if self.manifest_sha256 != self.config_sha256:
            _fail(
                "publication_bindings.config_sha256",
                "must identify the exact canonical manifest/config",
            )
        for name in ("expected_rows", "observed_rows", "maximum_calls", "observed_calls"):
            _integer(
                getattr(self, name),
                f"publication_bindings.{name}",
                maximum=1_000_000,
            )
        if self.expected_rows != self.observed_rows:
            _fail("publication_bindings.rows", "COMPLETE counts must match")
        if self.observed_calls > self.maximum_calls:
            _fail("publication_bindings.calls", "observed calls exceed the maximum")


def publication_bindings_from_artifacts(
    manifest: BenchmarkManifest,
    receipt: BenchmarkReceipt,
) -> ReportPublicationBindings:
    """Validate and bind the finalized manifest/receipt evidence for one report."""

    if type(manifest) is not BenchmarkManifest or type(receipt) is not BenchmarkReceipt:
        _fail("publication_bindings", "requires exact manifest and receipt values")
    manifest_digest = manifest_sha256(manifest)
    if (
        receipt.status is not CompletionStatus.COMPLETE
        or receipt.run_id != manifest.run_id
        or receipt.config_sha256 != manifest_digest
        or receipt.corpus_sha256 != manifest.corpus_sha256
        or receipt.analysis_code_sha256 != manifest.analysis_code_sha256
        or receipt.expected_rows != len(manifest.expected_rows)
        or receipt.rows_sha256 is None
        or receipt.blobs_sha256 is None
        or receipt.observations_sha256 is None
    ):
        _fail("publication_bindings", "manifest and COMPLETE receipt do not agree")
    return ReportPublicationBindings(
        receipt.run_id,
        manifest_digest,
        receipt.config_sha256,
        receipt_sha256(receipt),
        receipt.status.value,
        receipt.corpus_sha256,
        receipt.analysis_code_sha256,
        receipt.journal_sha256,
        receipt.rows_sha256,
        receipt.blobs_sha256,
        receipt.observations_sha256,
        receipt.expected_rows,
        receipt.observed_rows,
        receipt.maximum_calls,
        receipt.observed_calls,
    )


def _json_ready(value: object) -> object:
    if value is None or type(value) in {str, int, float, bool}:
        return value
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {
            field.name: _json_ready(getattr(value, field.name))
            for field in fields(cast(Any, value))
        }
    if type(value) is tuple or type(value) is list:
        return [_json_ready(child) for child in cast(tuple[object, ...] | list[object], value)]
    if type(value) is dict:
        return {
            str(key): _json_ready(child) for key, child in cast(dict[object, object], value).items()
        }
    _fail("wire", "contains a non-JSON value")


def _target_content(target: tuple[Note, ...]) -> dict[str, object]:
    return {
        "notes": [
            {
                "duration": _fraction_token(note.duration),
                "onset": _fraction_token(note.onset),
                "pitch": note.pitch,
                "voice": note.voice,
            }
            for note in target
        ]
    }


def _parse_target(value: object, field: str) -> tuple[Note, ...]:
    obj = _exact_dict(value, field, frozenset({"notes"}))
    rows = _exact_list(obj["notes"], f"{field}.notes", maximum=20_000)
    notes: list[Note] = []
    for index, raw in enumerate(rows):
        path = f"{field}.notes[{index}]"
        note = _exact_dict(raw, path, frozenset({"onset", "duration", "pitch", "voice"}))
        voice = _text(note["voice"], f"{path}.voice")
        if voice not in {"melody", "bass", "harmony"}:
            _fail(f"{path}.voice", "is unsupported")
        notes.append(
            Note(
                _parse_fraction(note["onset"], f"{path}.onset", positive=False),
                _parse_fraction(note["duration"], f"{path}.duration", positive=True),
                _integer(note["pitch"], f"{path}.pitch", maximum=127),
                cast(Any, voice),
            )
        )
    return tuple(notes)


def _tab_content(tab: Tab) -> dict[str, object]:
    parsed = json.loads(tab_to_json(tab))
    if type(parsed) is not dict:  # pragma: no cover - tab serializer invariant
        raise AssertionError("tab serializer did not return an object")
    return cast(dict[str, object], parsed)


def _parse_tab(value: object, item: CorpusItem, profile: Profile, field: str) -> Tab:
    if type(value) is not dict:
        _fail(field, "must be one tab object")
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    try:
        return validated_tab_from_json(
            encoded,
            profile=profile,
            tempo_bpm=item.ir.meta.tempo_bpm,
            beats_per_bar=item.ir.meta.time_sig[0],
        )
    except (ValueError, TypeError) as error:
        raise ReportInputError(field, "tab blob is invalid") from error


def _trace_content(trajectory: CandidateTrajectory) -> list[dict[str, object]]:
    return [
        {
            "candidate_index": step.candidate_index,
            "data": step.data,
            "detail": step.detail,
            "event": step.event,
            "iteration": step.iteration,
            "kind": step.kind,
        }
        for step in trajectory.trace_steps
    ]


def _parse_trace(value: object, field: str) -> tuple[TraceStepSnapshot, ...]:
    rows = _exact_list(value, field)
    snapshots: list[TraceStepSnapshot] = []
    steps: list[TraceStep] = []
    for index, raw in enumerate(rows):
        path = f"{field}[{index}]"
        obj = _exact_dict(
            raw,
            path,
            frozenset({"kind", "detail", "data", "event", "candidate_index", "iteration"}),
        )
        data = obj["data"]
        if type(data) is not dict:
            _fail(f"{path}.data", "must be an exact object")
        kind = _text(obj["kind"], f"{path}.kind")
        event = _text(obj["event"], f"{path}.event", nullable=True)
        step = TraceStep(
            cast(StepKind, kind),
            cast(str, _text(obj["detail"], f"{path}.detail")),
            cast(dict[str, Any], data),
            cast(TraceEvent | None, event),
            _optional_integer(obj["candidate_index"], f"{path}.candidate_index"),
            _optional_integer(obj["iteration"], f"{path}.iteration"),
        )
        steps.append(step)
        snapshots.append(TraceStepSnapshot.from_trace_step(step))
    # The public encoder performs the complete event/data/context validation.
    Trace(list(steps)).to_jsonl()
    return tuple(snapshots)


def _profile_verdicts(
    tab: Tab | None, item: CorpusItem, median: Profile
) -> tuple[ProfileVerdict, ...]:
    profiles = (SMALL_HAND, median, LARGE_HAND)
    if len({value.fingerprint for value in profiles}) != 3:
        _fail("profile", "small/median/large fingerprints must be distinct")
    return tuple(
        ProfileVerdict(
            value.version,
            value.fingerprint,
            None
            if tab is None
            else check_playability(
                tab,
                value,
                tempo_bpm=item.ir.meta.tempo_bpm,
                beats_per_bar=item.ir.meta.time_sig[0],
            ).verdict,
        )
        for value in profiles
    )


def _checkpoint_score(
    item: CorpusItem,
    tab: Tab | None,
    *,
    fallback_assisted: bool,
    llm_generated: bool,
    profile: Profile,
) -> CheckpointScore:
    evaluated = faithfulness_dimensions(item.ir)
    unavailable = tuple(value for value in FAITHFULNESS_DIMENSIONS if value not in evaluated)
    gate = None if tab is None else faithfulness(item.ir, tab)
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
    green = oracle is not None and oracle.verdict == "GREEN"
    passed = gate is not None and gate.passed
    joint = green and passed
    return CheckpointScore(
        item.evidence.signature if item.evidence is not None else "none",
        evaluated,
        unavailable,
        tab is not None,
        None if gate is None else gate.melody_f1,
        None if gate is None else gate.bass_root,
        None if gate is None else gate.harmony,
        passed,
        green,
        joint,
        fallback_assisted,
        llm_generated,
        joint and llm_generated,
        _profile_verdicts(tab, item, profile),
    )


def _score_to_dict(value: CheckpointScore) -> dict[str, object]:
    return cast(dict[str, object], _json_ready(value))


def _score_from_dict(value: object, field: str) -> CheckpointScore:
    obj = _exact_dict(
        value,
        field,
        frozenset(
            {
                "evidence_signature",
                "evaluated_dimensions",
                "unavailable_dimensions",
                "tab_available",
                "melody_f1",
                "bass_root",
                "harmony",
                "faithfulness_passed",
                "green",
                "joint_success",
                "fallback_assisted",
                "llm_generated",
                "llm_success",
                "profiles",
            }
        ),
    )

    def dimensions(name: str) -> tuple[FaithfulnessDimension, ...]:
        raw = _exact_list(obj[name], f"{field}.{name}", maximum=3)
        if any(value not in FAITHFULNESS_DIMENSIONS for value in raw):
            _fail(f"{field}.{name}", "contains an unsupported dimension")
        return cast(tuple[FaithfulnessDimension, ...], tuple(raw))

    profile_rows = _exact_list(obj["profiles"], f"{field}.profiles", maximum=3)
    profiles: list[ProfileVerdict] = []
    for index, raw in enumerate(profile_rows):
        path = f"{field}.profiles[{index}]"
        row = _exact_dict(
            raw,
            path,
            frozenset({"profile_version", "profile_fingerprint", "verdict"}),
        )
        verdict = _text(row["verdict"], f"{path}.verdict", nullable=True)
        if verdict not in {None, "GREEN", "AMBER", "RED"}:
            _fail(f"{path}.verdict", "is unsupported")
        profiles.append(
            ProfileVerdict(
                cast(str, _text(row["profile_version"], f"{path}.profile_version")),
                cast(str, _sha(row["profile_fingerprint"], f"{path}.profile_fingerprint")),
                verdict,
            )
        )
    result = CheckpointScore(
        cast(str, _text(obj["evidence_signature"], f"{field}.evidence_signature")),
        dimensions("evaluated_dimensions"),
        dimensions("unavailable_dimensions"),
        _boolean(obj["tab_available"], f"{field}.tab_available"),
        _number(obj["melody_f1"], f"{field}.melody_f1", nullable=True),
        _number(obj["bass_root"], f"{field}.bass_root", nullable=True),
        _number(obj["harmony"], f"{field}.harmony", nullable=True),
        _boolean(obj["faithfulness_passed"], f"{field}.faithfulness_passed"),
        _boolean(obj["green"], f"{field}.green"),
        _boolean(obj["joint_success"], f"{field}.joint_success"),
        _boolean(obj["fallback_assisted"], f"{field}.fallback_assisted"),
        _boolean(obj["llm_generated"], f"{field}.llm_generated"),
        _boolean(obj["llm_success"], f"{field}.llm_success"),
        tuple(profiles),
    )
    if set(result.evaluated_dimensions) | set(result.unavailable_dimensions) != set(
        FAITHFULNESS_DIMENSIONS
    ) or not set(result.evaluated_dimensions).isdisjoint(result.unavailable_dimensions):
        _fail(field, "dimension availability must form an exact partition")
    if len(result.profiles) != 3:
        _fail(f"{field}.profiles", "must contain small, median, and large")
    return result


def _validate_stored_score_contract(
    score: CheckpointScore,
    item: CorpusItem,
    profile: Profile,
    field: str,
    *,
    fallback_assisted: bool,
    llm_generated: bool,
) -> None:
    if item.evidence is None:
        _fail("item.evidence", "must be snapshotted")
    evaluated = faithfulness_dimensions(item.ir)
    unavailable = tuple(value for value in FAITHFULNESS_DIMENSIONS if value not in evaluated)
    expected_profiles = tuple(
        (value.version, value.fingerprint) for value in (SMALL_HAND, profile, LARGE_HAND)
    )
    actual_profiles = tuple(
        (value.profile_version, value.profile_fingerprint) for value in score.profiles
    )
    if (
        score.evidence_signature != item.evidence.signature
        or score.evaluated_dimensions != evaluated
        or score.unavailable_dimensions != unavailable
        or score.fallback_assisted is not fallback_assisted
        or score.llm_generated is not llm_generated
        or actual_profiles != expected_profiles
    ):
        _fail(field, "stored evidence/profile/provenance contract drifted")


def _source_blob(item: CorpusItem) -> BlobRecord:
    return build_blob_record(BlobKind.NOTEGRAPH, ir_to_notegraph(item.ir))


def _source_to_dict(item: CorpusItem, blob: BlobRecord) -> dict[str, object]:
    if item.position is None or item.evidence is None:
        _fail("item", "planned corpus identities and evidence are required")
    return {
        "evidence_signature": item.evidence.signature,
        "genre": item.genre,
        "layer": item.layer,
        "notegraph_blob_sha256": blob.ref.sha256,
        "notegraph_sha256": notegraph_sha256(item.ir),
        "polyphony": item.polyphony,
        "position": item.position,
        "synthetic_complexity": item.synthetic_complexity,
        "tempo_bpm": item.ir.meta.tempo_bpm,
    }


def _solver_to_dict(snapshot: RepairSnapshot) -> dict[str, object]:
    if snapshot.tab is not None:
        return {
            "code": None,
            "kind": "tab",
            "onset": None,
            "pitches": [],
            "reason": None,
        }
    assert snapshot.infeasible is not None
    return {
        "code": snapshot.infeasible.code.value,
        "kind": "infeasible",
        "onset": (
            None
            if snapshot.infeasible.onset is None
            else _fraction_token(snapshot.infeasible.onset)
        ),
        "pitches": list(snapshot.infeasible.pitches),
        "reason": snapshot.infeasible.reason,
    }


def _infeasible_to_dict(value: Infeasible | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "code": value.code.value,
        "onset": None if value.onset is None else _fraction_token(value.onset),
        "pitches": list(value.pitches),
        "reason": value.reason,
    }


def _fidelity_to_dict(value: Fidelity | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "bass_preserved": value.bass_preserved,
        "harmony_jaccard": value.harmony_jaccard,
        "melody_recall": value.melody_recall,
    }


def _diagnostic_codes(snapshot: RepairSnapshot) -> list[str]:
    if snapshot.infeasible is not None:
        return [snapshot.infeasible.code.value]
    assert snapshot.oracle is not None
    return [value.violation_type for value in snapshot.oracle.diagnostics]


def _snapshot_payload(
    item: CorpusItem,
    snapshot: RepairSnapshot,
    *,
    fallback_assisted: bool,
    llm_generated: bool,
    ranking: Fidelity | None,
    profile: Profile,
) -> tuple[dict[str, object], tuple[BlobRecord, ...]]:
    target_blob = build_blob_record(BlobKind.TARGET, _target_content(snapshot.target))
    blobs = [target_blob]
    tab_blob: BlobRecord | None = None
    if snapshot.tab is not None:
        tab_blob = build_blob_record(BlobKind.TAB, _tab_content(snapshot.tab))
        blobs.append(tab_blob)
    return (
        {
            "diagnostic_codes": _diagnostic_codes(snapshot),
            "iteration": snapshot.iteration,
            "ranking_fidelity": _fidelity_to_dict(ranking),
            "score": _score_to_dict(
                _checkpoint_score(
                    item,
                    snapshot.tab,
                    fallback_assisted=fallback_assisted,
                    llm_generated=llm_generated,
                    profile=profile,
                )
            ),
            "solver": _solver_to_dict(snapshot),
            "tab_blob_sha256": None if tab_blob is None else tab_blob.ref.sha256,
            "target_blob_sha256": target_blob.ref.sha256,
            "verdict": None if snapshot.oracle is None else snapshot.oracle.verdict,
        },
        tuple(blobs),
    )


@dataclass(frozen=True, slots=True)
class _JoinedCall:
    intent: CallIntent
    result: CallResult
    provider_attempts: int
    attempt_reserved_output_tokens: int


def _joined_calls(ledger: ObservationLedger) -> tuple[_JoinedCall, ...]:
    results = {(value.call_index, value.logical_call_id): value for value in ledger.results}
    attempts: dict[tuple[int, str], list[int]] = defaultdict(list)
    for value in ledger.attempt_intents:
        attempts[(value.call_index, value.logical_call_id)].append(value.reserved_output_tokens)
    joined: list[_JoinedCall] = []
    for intent in ledger.intents:
        key = (intent.call_index, intent.logical_call_id)
        result = results.get(key)
        reservations = attempts.get(key)
        if result is None or reservations is None:
            _fail("observations", "contains an unjoined call")
        joined.append(_JoinedCall(intent, result, len(reservations), sum(reservations)))
    return tuple(joined)


def _call_to_dict(value: _JoinedCall) -> dict[str, object]:
    return {
        "attempt_reserved_output_tokens": value.attempt_reserved_output_tokens,
        "call_index": value.intent.call_index,
        "failure_code": (
            None if value.result.failure_code is None else value.result.failure_code.value
        ),
        "logical_call_id": value.intent.logical_call_id,
        "provider_attempts": value.provider_attempts,
        "reply_sha256": value.result.reply_sha256,
        "request_sha256": value.intent.request_sha256,
        "requested_model_id": value.intent.requested_model_id,
        "requested_output_tokens": value.intent.max_tokens,
        "retry_count": value.provider_attempts - 1,
        "returned_model_id": value.result.provider.returned_model_id,
        "stage": value.intent.stage.value,
        "stage_ordinal": value.intent.stage_ordinal,
        "status": value.result.status,
        "system_sha256": value.intent.system_sha256,
        "usage": {
            "cache_creation_input_tokens": (value.result.provider.cache_creation_input_tokens),
            "cache_read_input_tokens": value.result.provider.cache_read_input_tokens,
            "input_tokens": value.result.provider.input_tokens,
            "output_tokens": value.result.provider.output_tokens,
        },
        "user_sha256": value.intent.user_sha256,
    }


def _candidate_calls(
    joined: tuple[_JoinedCall, ...], item_id: str, candidate_index: int
) -> tuple[_JoinedCall, ...]:
    return tuple(
        value
        for value in joined
        if value.intent.item_id == item_id
        and value.intent.candidate_index == candidate_index
        and value.intent.stage in {CallStage.PROPOSAL, CallStage.REPAIR, CallStage.CRITIC}
    )


def _edit_counts(trajectory: CandidateTrajectory) -> dict[str, int]:
    steps = trajectory.trace_steps
    return {
        "applied": sum(step.event == "EDIT_APPLIED" for step in steps),
        "invalid": sum(step.event == "MODEL_EDIT_INVALID" for step in steps),
        "no_op": sum(
            step.event == "EDIT_REJECTED" and step.data.get("status") == "noop" for step in steps
        ),
        "rejected": sum(
            step.event == "EDIT_REJECTED" and step.data.get("status") == "rejected"
            for step in steps
        ),
    }


def _deduplicate_blobs(values: list[BlobRecord]) -> tuple[BlobRecord, ...]:
    by_ref: dict[BlobRef, BlobRecord] = {}
    for value in values:
        previous = by_ref.setdefault(value.ref, value)
        if previous != value:
            _fail("blobs", "one reference has conflicting content")
    return tuple(sorted(by_ref.values(), key=lambda value: value.ref.sort_key))


def _candidate_row_bundle(
    run_id: str,
    item: CorpusItem,
    trajectory: CandidateTrajectory,
    joined: tuple[_JoinedCall, ...],
    profile: Profile,
) -> tuple[BenchmarkRow, tuple[BlobRecord, ...]]:
    if item.family_id is None or item.cluster_id is None:
        _fail("item", "planned family and cluster identities are required")
    source_blob = _source_blob(item)
    calls = _candidate_calls(joined, item.item_id, trajectory.index)
    if len(calls) != trajectory.work.total_llm_calls:
        _fail("candidate.work", "logical call count does not match observations")
    fallback = trajectory.proposal.fallback_assisted
    llm_generated = trajectory.proposal.status is ProposalStatus.LLM_SUCCESS
    initial, initial_blobs = _snapshot_payload(
        item,
        trajectory.iteration_zero,
        fallback_assisted=fallback,
        llm_generated=llm_generated,
        ranking=(
            None
            if trajectory.iteration_zero.tab is None
            else fidelity(item.ir, trajectory.iteration_zero.tab)
        ),
        profile=profile,
    )
    terminal, terminal_blobs = _snapshot_payload(
        item,
        trajectory.terminal,
        fallback_assisted=fallback,
        llm_generated=llm_generated,
        ranking=trajectory.fidelity,
        profile=profile,
    )
    trace_blob = build_blob_record(BlobKind.TRACE, _trace_content(trajectory))
    critic = (
        None
        if trajectory.critic_outcome is None
        else {
            "bass_motion": trajectory.critic_outcome.score.bass_motion,
            "llm_calls": trajectory.critic_outcome.llm_calls,
            "overall": trajectory.critic_outcome.score.overall,
            "status": trajectory.critic_outcome.status.value,
            "texture": trajectory.critic_outcome.score.texture,
            "voice_leading": trajectory.critic_outcome.score.voice_leading,
        }
    )
    all_blobs = _deduplicate_blobs([source_blob, trace_blob, *initial_blobs, *terminal_blobs])
    payload: dict[str, object] = {
        "critic": critic,
        "initial": initial,
        "proposal": {
            "fallback_assisted": fallback,
            "llm_calls": trajectory.proposal.llm_calls,
            "status": trajectory.proposal.status.value,
            "target_blob_sha256": initial["target_blob_sha256"],
        },
        "source": _source_to_dict(item, source_blob),
        "terminal": terminal,
        "work": {
            "calls": [_call_to_dict(value) for value in calls],
            "critic_llm_calls": trajectory.work.critic_llm_calls,
            "edit_counts": _edit_counts(trajectory),
            "logical_calls": trajectory.work.total_llm_calls,
            "proposal_llm_calls": trajectory.work.proposal_llm_calls,
            "repair_llm_calls": trajectory.work.repair_llm_calls,
            "solver_calls": trajectory.work.solver_calls,
            "termination_reason": {
                CandidateStatus.GREEN: "GREEN_CERTIFIED",
                CandidateStatus.NON_GREEN_TAB: "NON_GREEN_TAB",
                CandidateStatus.NO_TAB: "NO_TAB",
            }[trajectory.status],
        },
    }
    row = build_row(
        run_id=run_id,
        key=RowKey(
            RowType.CANDIDATE,
            item.item_id,
            trajectory.index,
            trajectory.index,
            sample_pair_id(item.item_id, trajectory.index),
        ),
        family_id=item.family_id,
        cluster_id=item.cluster_id,
        observation_keys=tuple(
            ObservationKey(value.intent.logical_call_id, value.intent.call_index) for value in calls
        ),
        blob_refs=tuple(value.ref for value in all_blobs),
        payload=payload,
    )
    return row, all_blobs


def _raw_row_bundle(
    run_id: str,
    item: CorpusItem,
    outcome: RawLLMOutcome,
    joined: tuple[_JoinedCall, ...],
    profile: Profile,
) -> tuple[BenchmarkRow, tuple[BlobRecord, ...]]:
    if item.family_id is None or item.cluster_id is None:
        _fail("item", "planned family and cluster identities are required")
    source_blob = _source_blob(item)
    calls = tuple(
        value
        for value in joined
        if value.intent.item_id == item.item_id
        and value.intent.candidate_index == outcome.sample_index
        and value.intent.stage is CallStage.RAW
    )
    if len(calls) != 1:
        _fail("raw.work", "requires exactly one observation")
    blobs = [source_blob]
    tab_blob: BlobRecord | None = None
    if outcome.tab is not None:
        tab_blob = build_blob_record(BlobKind.TAB, _tab_content(outcome.tab))
        blobs.append(tab_blob)
    score = _checkpoint_score(
        item,
        outcome.tab,
        fallback_assisted=False,
        llm_generated=outcome.status is RawStatus.VALID_TAB,
        profile=profile,
    )
    payload: dict[str, object] = {
        "outcome": {
            "call": _call_to_dict(calls[0]),
            "call_failure_code": (
                None if outcome.call_failure_code is None else outcome.call_failure_code.value
            ),
            "llm_calls": outcome.llm_calls,
            "parse_code": None if outcome.parse_code is None else outcome.parse_code.value,
            "source_context_sha256": outcome.source_context_sha256,
            "status": outcome.status.value,
            "tab_blob_sha256": None if tab_blob is None else tab_blob.ref.sha256,
        },
        "score": _score_to_dict(score),
        "source": _source_to_dict(item, source_blob),
    }
    exact_blobs = _deduplicate_blobs(blobs)
    call = calls[0]
    row = build_row(
        run_id=run_id,
        key=RowKey(
            RowType.RAW,
            item.item_id,
            outcome.sample_index,
            outcome.sample_index,
            sample_pair_id(item.item_id, outcome.sample_index),
        ),
        family_id=item.family_id,
        cluster_id=item.cluster_id,
        observation_keys=(ObservationKey(call.intent.logical_call_id, call.intent.call_index),),
        blob_refs=tuple(value.ref for value in exact_blobs),
        payload=payload,
    )
    return row, exact_blobs


def _goal_at_source_tempo(goal: ArrangeGoal, item: CorpusItem) -> ArrangeGoal:
    return replace(goal, tempo_bpm=item.ir.meta.tempo_bpm, extras=dict(goal.extras))


def _pure_row_bundle(
    run_id: str,
    item: CorpusItem,
    outcome: PureSolverOutcome,
    goal: ArrangeGoal,
    profile: Profile,
) -> tuple[BenchmarkRow, tuple[BlobRecord, ...]]:
    if item.family_id is None or item.cluster_id is None:
        _fail("item", "planned family and cluster identities are required")
    source_blob = _source_blob(item)
    exact_goal = _goal_at_source_tempo(goal, item)
    solver_ir = arrangement_solver_ir(item.ir)
    target = propose_fingerstyle(
        solver_ir,
        exact_goal.tuning,
        exact_goal.capo,
        profile=profile,
        tempo_bpm=exact_goal.tempo_bpm,
    )
    target_blob = build_blob_record(BlobKind.TARGET, _target_content(target))
    blobs = [source_blob, target_blob]
    tab_blob: BlobRecord | None = None
    if outcome.tab is not None:
        tab_blob = build_blob_record(BlobKind.TAB, _tab_content(outcome.tab))
        blobs.append(tab_blob)
    payload: dict[str, object] = {
        "baseline": {"baseline_id": "B2", "llm_calls": 0, "solver_calls": 1},
        "outcome": {
            "infeasible": _infeasible_to_dict(outcome.infeasible),
            "status": outcome.status.value,
            "tab_blob_sha256": None if tab_blob is None else tab_blob.ref.sha256,
            "target_blob_sha256": target_blob.ref.sha256,
        },
        "score": _score_to_dict(
            _checkpoint_score(
                item,
                outcome.tab,
                fallback_assisted=False,
                llm_generated=False,
                profile=profile,
            )
        ),
        "source": _source_to_dict(item, source_blob),
    }
    exact_blobs = _deduplicate_blobs(blobs)
    row = build_row(
        run_id=run_id,
        key=RowKey(
            RowType.PURE_SOLVER,
            item.item_id,
            None,
            None,
            item_pair_id("pure-solver", item.item_id),
        ),
        family_id=item.family_id,
        cluster_id=item.cluster_id,
        observation_keys=(),
        blob_refs=tuple(value.ref for value in exact_blobs),
        payload=payload,
    )
    return row, exact_blobs


def collection_to_row_bundle(collection: ExperimentCollection) -> ArtifactRowBundle:
    """Materialize one complete collection as strict replayable rows and blobs."""

    if type(collection) is not ExperimentCollection:
        _fail("collection", "must be an exact ExperimentCollection")
    joined = _joined_calls(collection.observations)
    rows: list[BenchmarkRow] = []
    blobs: list[BlobRecord] = []
    for item in collection.items:
        pure_row, pure_blobs = _pure_row_bundle(
            collection.plan.run_id,
            item.item,
            item.pure_solver,
            collection.goal,
            collection.profile,
        )
        rows.append(pure_row)
        blobs.extend(pure_blobs)
        for trajectory in item.trajectories:
            row, values = _candidate_row_bundle(
                collection.plan.run_id,
                item.item,
                trajectory,
                joined,
                collection.profile,
            )
            rows.append(row)
            blobs.extend(values)
        for outcome in item.raw_outcomes:
            row, values = _raw_row_bundle(
                collection.plan.run_id,
                item.item,
                outcome,
                joined,
                collection.profile,
            )
            rows.append(row)
            blobs.extend(values)
    return ArtifactRowBundle(
        tuple(sorted(rows, key=lambda value: value.sort_key)),
        _deduplicate_blobs(blobs),
    )


def pure_outcome_to_row_bundle(
    plan: ExperimentPlan,
    goal: ArrangeGoal,
    profile: Profile,
    item: CorpusItem,
    outcome: PureSolverOutcome,
) -> ArtifactRowBundle:
    """Build the single durable row emitted by one pure-solver callback."""

    if type(plan) is not ExperimentPlan or type(item) is not CorpusItem:
        _fail("pure_callback", "requires exact plan and item values")
    planned = next((value for value in plan.items if value.item_id == item.item_id), None)
    if planned is None or planned != item:
        _fail("pure_callback.item", "does not match the planned source snapshot")
    exact_profile = ensure_profile(profile)
    row, blobs = _pure_row_bundle(plan.run_id, item, outcome, goal, exact_profile)
    return ArtifactRowBundle((row,), blobs)


def completed_pure_solver_to_row_bundle(
    plan: ExperimentPlan,
    goal: ArrangeGoal,
    profile: Profile,
    completed: CompletedPureSolver,
) -> ArtifactRowBundle:
    """Build one pure row from the source-bound resume wrapper."""

    if type(completed) is not CompletedPureSolver:
        _fail("pure_callback", "must be an exact CompletedPureSolver")
    item = next((value for value in plan.items if value.item_id == completed.item_id), None)
    if item is None:
        _fail("pure_callback.item_id", "is not present in the plan")
    if completed.source_context_sha256 != arrangement_source_context_sha256(item.ir):
        _fail("pure_callback.source_context_sha256", "does not bind the planned source")
    return pure_outcome_to_row_bundle(plan, goal, profile, item, completed.outcome)


def completed_unit_to_row_bundle(
    plan: ExperimentPlan,
    goal: ArrangeGoal,
    profile: Profile,
    completed: CompletedExperimentUnit,
    current_ledger: ObservationLedger,
) -> ArtifactRowBundle:
    """Build exactly one row from the just-completed schedule unit.

    The unit's logical calls must be the complete contiguous suffix of the current
    ledger.  This makes the callback safe for immediate unit commits without any
    dependence on a later complete collection.
    """

    if type(plan) is not ExperimentPlan or type(completed) is not CompletedExperimentUnit:
        _fail("unit_callback", "requires exact plan and completed-unit values")
    if type(current_ledger) is not ObservationLedger:
        _fail("unit_callback.ledger", "must be an exact ObservationLedger")
    unit = completed.unit
    if (
        unit.item_position >= len(plan.items)
        or plan.items[unit.item_position].item_id != unit.item_id
    ):
        _fail("unit_callback.unit", "does not identify its planned source position")
    item = plan.items[unit.item_position]
    if completed.source_context_sha256 != arrangement_source_context_sha256(item.ir):
        _fail("unit_callback.source_context_sha256", "does not bind the planned source")
    exact_profile = ensure_profile(profile)
    joined = _joined_calls(current_ledger)
    if unit.arm is CollectionArm.AGENT:
        if completed.trajectory is None:
            _fail("unit_callback.trajectory", "is missing")
        selected = _candidate_calls(joined, unit.item_id, unit.candidate_index)
        row, blobs = _candidate_row_bundle(
            plan.run_id,
            item,
            completed.trajectory,
            joined,
            exact_profile,
        )
    else:
        if completed.raw_outcome is None:
            _fail("unit_callback.raw_outcome", "is missing")
        selected = tuple(
            value
            for value in joined
            if value.intent.item_id == unit.item_id
            and value.intent.candidate_index == unit.candidate_index
            and value.intent.stage is CallStage.RAW
        )
        row, blobs = _raw_row_bundle(
            plan.run_id,
            item,
            completed.raw_outcome,
            joined,
            exact_profile,
        )
    indices = tuple(value.intent.call_index for value in selected)
    expected = tuple(range(len(joined) - len(selected), len(joined)))
    if not selected or indices != expected:
        _fail(
            "unit_callback.observations",
            "unit calls must be the complete contiguous ledger suffix",
        )
    return ArtifactRowBundle((row,), blobs)


def _normalized_inputs(
    plan: ExperimentPlan,
    rows: tuple[BenchmarkRow, ...],
    blobs: tuple[BlobRecord, ...],
) -> tuple[tuple[BenchmarkRow, ...], tuple[BlobRecord, ...]]:
    if type(plan) is not ExperimentPlan:
        _fail("plan", "must be an exact ExperimentPlan")
    if type(rows) is not tuple or any(type(value) is not BenchmarkRow for value in rows):
        _fail("rows", "must contain exact BenchmarkRow values")
    if type(blobs) is not tuple or any(type(value) is not BlobRecord for value in blobs):
        _fail("blobs", "must contain exact BlobRecord values")
    ordered_rows = tuple(sorted(rows, key=lambda value: value.sort_key))
    ordered_blobs = tuple(sorted(blobs, key=lambda value: value.ref.sort_key))
    if len({value.key for value in ordered_rows}) != len(ordered_rows):
        _fail("rows", "row keys must be unique")
    if len({value.ref for value in ordered_blobs}) != len(ordered_blobs):
        _fail("blobs", "blob references must be unique")
    if any(value.run_id != plan.run_id for value in ordered_rows):
        _fail("rows.run_id", "does not match the plan")
    return ordered_rows, ordered_blobs


def _blob_index(blobs: tuple[BlobRecord, ...]) -> dict[tuple[BlobKind, str], BlobRecord]:
    return {(value.ref.kind, value.ref.sha256): value for value in blobs}


def _owned_blob(
    row: BenchmarkRow,
    index: dict[tuple[BlobKind, str], BlobRecord],
    kind: BlobKind,
    sha256: object,
    field: str,
) -> BlobRecord:
    digest = _sha(sha256, field)
    assert digest is not None
    record = index.get((kind, digest))
    if record is None:
        _fail(field, "does not identify an available blob")
    if record.ref not in row.blob_refs:
        _fail(field, "identifies a blob not owned by the row")
    return record


def _item_index(plan: ExperimentPlan) -> dict[str, CorpusItem]:
    return {value.item_id: value for value in plan.items}


def _validate_row_identity(row: BenchmarkRow, item: CorpusItem) -> None:
    if item.family_id is None or item.cluster_id is None:
        _fail("plan.items", "family and cluster identities are required")
    if row.family_id != item.family_id or row.cluster_id != item.cluster_id:
        _fail("row.identity", "family or cluster does not match the plan")
    expected_pair = (
        item_pair_id("pure-solver", item.item_id)
        if row.key.row_type is RowType.PURE_SOLVER
        else sample_pair_id(item.item_id, cast(int, row.key.candidate_index))
    )
    if row.key.pair_id != expected_pair:
        _fail("row.key.pair_id", "does not match the frozen pair identity")
    if (
        row.key.row_type is not RowType.PURE_SOLVER
        and cast(int, row.key.candidate_index) >= EXPERIMENT_N_SAMPLES
    ):
        _fail("row.key.candidate_index", "is outside the frozen sample count")


def _parse_source(
    value: object,
    row: BenchmarkRow,
    item: CorpusItem,
    blobs: dict[tuple[BlobKind, str], BlobRecord],
    field: str,
) -> None:
    obj = _exact_dict(
        value,
        field,
        frozenset(
            {
                "position",
                "layer",
                "genre",
                "synthetic_complexity",
                "polyphony",
                "evidence_signature",
                "notegraph_sha256",
                "notegraph_blob_sha256",
                "tempo_bpm",
            }
        ),
    )
    record = _owned_blob(
        row,
        blobs,
        BlobKind.NOTEGRAPH,
        obj["notegraph_blob_sha256"],
        f"{field}.notegraph_blob_sha256",
    )
    if notegraph_to_ir(record.content) != item.ir:
        _fail(field, "notegraph blob does not match the planned source")
    evidence = item.evidence.signature if item.evidence is not None else None
    expected = {
        "position": item.position,
        "layer": item.layer,
        "genre": item.genre,
        "synthetic_complexity": item.synthetic_complexity,
        "polyphony": item.polyphony,
        "evidence_signature": evidence,
        "notegraph_sha256": notegraph_sha256(item.ir),
        "notegraph_blob_sha256": record.ref.sha256,
        "tempo_bpm": item.ir.meta.tempo_bpm,
    }
    if obj != expected:
        _fail(field, "metadata does not match the planned source")


def _parse_infeasible(value: object, field: str, *, nullable: bool) -> Infeasible | None:
    if value is None and nullable:
        return None
    obj = _exact_dict(value, field, frozenset({"code", "onset", "reason", "pitches"}))
    code_text = _text(obj["code"], f"{field}.code")
    try:
        code = InfeasibleCode(cast(str, code_text))
    except ValueError:
        _fail(f"{field}.code", "is unsupported")
    onset = (
        None
        if obj["onset"] is None
        else _parse_fraction(obj["onset"], f"{field}.onset", positive=False)
    )
    pitches_raw = _exact_list(obj["pitches"], f"{field}.pitches", maximum=128)
    pitches = tuple(
        _integer(value, f"{field}.pitches[{index}]", maximum=127)
        for index, value in enumerate(pitches_raw)
    )
    return Infeasible(
        code,
        onset,
        cast(str, _text(obj["reason"], f"{field}.reason")),
        pitches,
    )


def _parse_solver(value: object, field: str) -> tuple[str, Infeasible | None]:
    obj = _exact_dict(
        value,
        field,
        frozenset({"kind", "code", "onset", "reason", "pitches"}),
    )
    kind = _text(obj["kind"], f"{field}.kind")
    if kind == "tab":
        if (
            any(obj[name] is not None for name in ("code", "onset", "reason"))
            or obj["pitches"] != []
        ):
            _fail(field, "tab solver state contains infeasibility fields")
        return "tab", None
    if kind != "infeasible":
        _fail(f"{field}.kind", "must be tab or infeasible")
    return "infeasible", _parse_infeasible(
        {
            "code": obj["code"],
            "onset": obj["onset"],
            "pitches": obj["pitches"],
            "reason": obj["reason"],
        },
        field,
        nullable=False,
    )


def _parse_fidelity(value: object, field: str, *, nullable: bool) -> Fidelity | None:
    if value is None and nullable:
        return None
    obj = _exact_dict(
        value,
        field,
        frozenset({"melody_recall", "bass_preserved", "harmony_jaccard"}),
    )
    return Fidelity(
        cast(float, _number(obj["melody_recall"], f"{field}.melody_recall")),
        cast(float, _number(obj["bass_preserved"], f"{field}.bass_preserved")),
        cast(float, _number(obj["harmony_jaccard"], f"{field}.harmony_jaccard")),
    )


def _stored_oracle(
    score: CheckpointScore, profile: Profile, verdict: str | None
) -> OracleResult | None:
    if not score.tab_available:
        return None
    if verdict is None:
        _fail("snapshot.verdict", "a stored tab requires one verdict")
    return OracleResult(
        cast(Any, verdict),
        (),
        CHECKER_VERSION,
        profile.version,
        profile.fingerprint,
        ORACLE_INPUT_SCHEMA_VERSION,
    )


def _snapshot_from_payload(
    value: object,
    *,
    field: str,
    row: BenchmarkRow,
    item: CorpusItem,
    goal: ArrangeGoal,
    profile: Profile,
    blobs: dict[tuple[BlobKind, str], BlobRecord],
    fallback_assisted: bool,
    llm_generated: bool,
    full: bool,
) -> tuple[RepairSnapshot, CheckpointScore, Fidelity | None]:
    obj = _exact_dict(
        value,
        field,
        frozenset(
            {
                "iteration",
                "target_blob_sha256",
                "tab_blob_sha256",
                "solver",
                "verdict",
                "diagnostic_codes",
                "ranking_fidelity",
                "score",
            }
        ),
    )
    iteration = _integer(obj["iteration"], f"{field}.iteration", maximum=8)
    target_record = _owned_blob(
        row,
        blobs,
        BlobKind.TARGET,
        obj["target_blob_sha256"],
        f"{field}.target_blob_sha256",
    )
    target = _parse_target(target_record.content, f"{field}.target")
    solver_kind, stored_infeasible = _parse_solver(obj["solver"], f"{field}.solver")
    tab: Tab | None = None
    if solver_kind == "tab":
        tab_record = _owned_blob(
            row,
            blobs,
            BlobKind.TAB,
            obj["tab_blob_sha256"],
            f"{field}.tab_blob_sha256",
        )
        tab = _parse_tab(tab_record.content, item, profile, f"{field}.tab")
    elif obj["tab_blob_sha256"] is not None:
        _fail(f"{field}.tab_blob_sha256", "must be null for an infeasible solve")
    stored_score = _score_from_dict(obj["score"], f"{field}.score")
    _validate_stored_score_contract(
        stored_score,
        item,
        profile,
        f"{field}.score",
        fallback_assisted=fallback_assisted,
        llm_generated=llm_generated,
    )
    ranking = _parse_fidelity(obj["ranking_fidelity"], f"{field}.ranking_fidelity", nullable=True)
    diagnostics = _exact_list(obj["diagnostic_codes"], f"{field}.diagnostic_codes", maximum=10_000)
    if any(type(value) is not str or not value for value in diagnostics):
        _fail(f"{field}.diagnostic_codes", "must contain nonempty strings")
    verdict = _text(obj["verdict"], f"{field}.verdict", nullable=True)
    if verdict not in {None, "GREEN", "AMBER", "RED"}:
        _fail(f"{field}.verdict", "is unsupported")
    if full:
        exact_goal = _goal_at_source_tempo(goal, item)
        solved = solve_fingering(
            target,
            exact_goal.tuning,
            exact_goal.capo,
            profile,
            tempo_bpm=exact_goal.tempo_bpm,
            beats_per_bar=item.ir.meta.time_sig[0],
        )
        if isinstance(solved, Tab):
            if tab != solved or stored_infeasible is not None:
                _fail(f"{field}.solver", "stored solver result drifted")
            oracle = check_playability(
                solved,
                profile,
                tempo_bpm=item.ir.meta.tempo_bpm,
                beats_per_bar=item.ir.meta.time_sig[0],
            )
            infeasible = None
        else:
            if tab is not None or solved != stored_infeasible:
                _fail(f"{field}.solver", "stored solver result drifted")
            oracle = None
            infeasible = solved
        actual_codes = (
            [infeasible.code.value]
            if infeasible is not None
            else [value.violation_type for value in cast(OracleResult, oracle).diagnostics]
        )
        if diagnostics != actual_codes or verdict != (None if oracle is None else oracle.verdict):
            _fail(field, "stored oracle values drifted")
        actual_score = _checkpoint_score(
            item,
            tab,
            fallback_assisted=fallback_assisted,
            llm_generated=llm_generated,
            profile=profile,
        )
        if stored_score != actual_score:
            _fail(f"{field}.score", "stored score drifted")
        actual_ranking = None if tab is None else fidelity(item.ir, tab)
        if ranking != actual_ranking:
            _fail(f"{field}.ranking_fidelity", "stored ranking fidelity drifted")
    else:
        oracle = _stored_oracle(stored_score, profile, verdict)
        infeasible = stored_infeasible
        if stored_score.tab_available is not (tab is not None):
            _fail(f"{field}.score", "tab availability is inconsistent")
    return RepairSnapshot(iteration, target, tab, oracle, infeasible), stored_score, ranking


def _parse_work(
    value: object,
    row: BenchmarkRow,
    field: str,
    *,
    expected_proposal_tokens: int,
) -> CandidateWorkCounts:
    obj = _exact_dict(
        value,
        field,
        frozenset(
            {
                "logical_calls",
                "proposal_llm_calls",
                "repair_llm_calls",
                "critic_llm_calls",
                "solver_calls",
                "edit_counts",
                "calls",
                "termination_reason",
            }
        ),
    )
    edit = _exact_dict(
        obj["edit_counts"],
        f"{field}.edit_counts",
        frozenset({"applied", "invalid", "no_op", "rejected"}),
    )
    for name, raw in edit.items():
        _integer(raw, f"{field}.edit_counts.{name}", maximum=8)
    raw_calls = _exact_list(obj["calls"], f"{field}.calls", maximum=10)
    keys: list[ObservationKey] = []
    stage_counts: dict[str, int] = defaultdict(int)
    stage_sequence: list[tuple[str, int]] = []
    for index, raw in enumerate(raw_calls):
        path = f"{field}.calls[{index}]"
        call = _exact_dict(
            raw,
            path,
            frozenset(
                {
                    "logical_call_id",
                    "call_index",
                    "stage",
                    "stage_ordinal",
                    "requested_output_tokens",
                    "attempt_reserved_output_tokens",
                    "provider_attempts",
                    "requested_model_id",
                    "returned_model_id",
                    "system_sha256",
                    "user_sha256",
                    "request_sha256",
                    "reply_sha256",
                    "status",
                    "failure_code",
                    "retry_count",
                    "usage",
                }
            ),
        )
        stage = _text(call["stage"], f"{path}.stage")
        if stage not in {"proposal", "repair", "critic"}:
            _fail(f"{path}.stage", "is unsupported for a candidate")
        stage_counts[stage] += 1
        keys.append(
            ObservationKey(
                cast(str, _text(call["logical_call_id"], f"{path}.logical_call_id")),
                _integer(call["call_index"], f"{path}.call_index"),
            )
        )
        stage_ordinal = _integer(call["stage_ordinal"], f"{path}.stage_ordinal", maximum=8)
        stage_sequence.append((stage, stage_ordinal))
        requested_tokens = _integer(
            call["requested_output_tokens"],
            f"{path}.requested_output_tokens",
            minimum=1,
            maximum=1_000_000,
        )
        expected_tokens = {
            "proposal": expected_proposal_tokens,
            "repair": REPAIR_MAX_TOKENS,
            "critic": CRITIC_MAX_TOKENS,
        }[stage]
        if requested_tokens != expected_tokens:
            _fail(f"{path}.requested_output_tokens", "does not match the frozen policy")
        _integer(
            call["attempt_reserved_output_tokens"],
            f"{path}.attempt_reserved_output_tokens",
            minimum=1,
            maximum=16_000_000,
        )
        _integer(
            call["provider_attempts"],
            f"{path}.provider_attempts",
            minimum=1,
            maximum=16,
        )
        _text(call["requested_model_id"], f"{path}.requested_model_id")
        _text(
            call["returned_model_id"],
            f"{path}.returned_model_id",
            nullable=True,
        )
        _sha(call["system_sha256"], f"{path}.system_sha256")
        _sha(call["user_sha256"], f"{path}.user_sha256")
        _sha(call["request_sha256"], f"{path}.request_sha256")
        _sha(call["reply_sha256"], f"{path}.reply_sha256", nullable=True)
        status = _text(call["status"], f"{path}.status")
        if status not in {"succeeded", "failed"}:
            _fail(f"{path}.status", "is unsupported")
        failure = _text(call["failure_code"], f"{path}.failure_code", nullable=True)
        if (status == "failed") is not (failure is not None):
            _fail(f"{path}.failure_code", "does not match call status")
        if _integer(call["retry_count"], f"{path}.retry_count", maximum=15) != (
            cast(int, call["provider_attempts"]) - 1
        ):
            _fail(f"{path}.retry_count", "does not match provider attempts")
        usage = _exact_dict(
            call["usage"],
            f"{path}.usage",
            frozenset(
                {
                    "input_tokens",
                    "output_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                }
            ),
        )
        for usage_name, usage_value in usage.items():
            _optional_integer(usage_value, f"{path}.usage.{usage_name}")
    if tuple(sorted(keys, key=lambda child: child.sort_key)) != row.observation_keys:
        _fail(f"{field}.calls", "does not match the row observation keys")
    logical = _integer(obj["logical_calls"], f"{field}.logical_calls", maximum=10)
    values = CandidateWorkCounts(
        _integer(obj["proposal_llm_calls"], f"{field}.proposal_llm_calls", maximum=1),
        _integer(obj["repair_llm_calls"], f"{field}.repair_llm_calls", maximum=8),
        _integer(obj["critic_llm_calls"], f"{field}.critic_llm_calls", maximum=1),
        _integer(obj["solver_calls"], f"{field}.solver_calls", minimum=1, maximum=9),
    )
    if logical != len(raw_calls) or logical != values.total_llm_calls:
        _fail(field, "logical call totals are inconsistent")
    if (
        stage_counts["proposal"] != values.proposal_llm_calls
        or stage_counts["repair"] != values.repair_llm_calls
        or stage_counts["critic"] != values.critic_llm_calls
    ):
        _fail(field, "stage call totals are inconsistent")
    expected_sequence = [
        *(("proposal", 0),) * values.proposal_llm_calls,
        *(("repair", index) for index in range(values.repair_llm_calls)),
        *(("critic", 0),) * values.critic_llm_calls,
    ]
    if stage_sequence != expected_sequence or tuple(value.call_index for value in keys) != tuple(
        sorted(value.call_index for value in keys)
    ):
        _fail(f"{field}.calls", "stage order or ordinal sequence is noncanonical")
    if _text(obj["termination_reason"], f"{field}.termination_reason") not in {
        "GREEN_CERTIFIED",
        "NON_GREEN_TAB",
        "NO_TAB",
    }:
        _fail(f"{field}.termination_reason", "is unsupported")
    return values


def _candidate_from_row(
    plan: ExperimentPlan,
    goal: ArrangeGoal,
    profile: Profile,
    row: BenchmarkRow,
    item: CorpusItem,
    blobs: dict[tuple[BlobKind, str], BlobRecord],
    *,
    full: bool,
) -> RescoredRow:
    payload = _exact_dict(
        row.payload,
        "candidate",
        frozenset({"source", "proposal", "initial", "terminal", "critic", "work"}),
    )
    _parse_source(payload["source"], row, item, blobs, "candidate.source")
    proposal_obj = _exact_dict(
        payload["proposal"],
        "candidate.proposal",
        frozenset({"status", "llm_calls", "fallback_assisted", "target_blob_sha256"}),
    )
    status_text = _text(proposal_obj["status"], "candidate.proposal.status")
    try:
        proposal_status = ProposalStatus(cast(str, status_text))
    except ValueError:
        _fail("candidate.proposal.status", "is unsupported")
    proposal_calls = _integer(proposal_obj["llm_calls"], "candidate.proposal.llm_calls", maximum=1)
    fallback = _boolean(proposal_obj["fallback_assisted"], "candidate.proposal.fallback_assisted")
    if fallback is not (
        proposal_status
        in {
            ProposalStatus.PARSE_VALIDATION_FALLBACK,
            ProposalStatus.CALL_FAILURE_FALLBACK,
        }
    ):
        _fail("candidate.proposal", "fallback provenance is inconsistent")
    llm_generated = proposal_status is ProposalStatus.LLM_SUCCESS
    initial, initial_score, _initial_ranking = _snapshot_from_payload(
        payload["initial"],
        field="candidate.initial",
        row=row,
        item=item,
        goal=goal,
        profile=profile,
        blobs=blobs,
        fallback_assisted=fallback,
        llm_generated=llm_generated,
        full=full,
    )
    terminal, terminal_score, terminal_ranking = _snapshot_from_payload(
        payload["terminal"],
        field="candidate.terminal",
        row=row,
        item=item,
        goal=goal,
        profile=profile,
        blobs=blobs,
        fallback_assisted=fallback,
        llm_generated=llm_generated,
        full=full,
    )
    proposal_target_sha = _sha(
        proposal_obj["target_blob_sha256"], "candidate.proposal.target_blob_sha256"
    )
    initial_obj = cast(dict[str, object], payload["initial"])
    if proposal_target_sha != initial_obj["target_blob_sha256"]:
        _fail("candidate.proposal.target_blob_sha256", "does not match iteration zero")
    proposal = ProposalOutcome(initial.target, proposal_status, proposal_calls)
    critic: CriticOutcome | None = None
    if payload["critic"] is not None:
        critic_obj = _exact_dict(
            payload["critic"],
            "candidate.critic",
            frozenset(
                {
                    "status",
                    "llm_calls",
                    "overall",
                    "voice_leading",
                    "bass_motion",
                    "texture",
                }
            ),
        )
        critic_status_text = _text(critic_obj["status"], "candidate.critic.status")
        try:
            critic_status = CriticStatus(cast(str, critic_status_text))
        except ValueError:
            _fail("candidate.critic.status", "is unsupported")
        critic = CriticOutcome(
            CriticScore(
                cast(float, _number(critic_obj["overall"], "candidate.critic.overall")),
                cast(
                    float,
                    _number(critic_obj["voice_leading"], "candidate.critic.voice_leading"),
                ),
                cast(
                    float,
                    _number(critic_obj["bass_motion"], "candidate.critic.bass_motion"),
                ),
                cast(float, _number(critic_obj["texture"], "candidate.critic.texture")),
                "restored benchmark critic score",
            ),
            critic_status,
            _integer(critic_obj["llm_calls"], "candidate.critic.llm_calls", minimum=1),
        )
    budget = next(value for value in plan.matched_budgets if value.item_id == item.item_id)
    work = _parse_work(
        payload["work"],
        row,
        "candidate.work",
        expected_proposal_tokens=budget.proposal_tokens,
    )
    trace_refs = [value for value in row.blob_refs if value.kind is BlobKind.TRACE]
    if len(trace_refs) != 1:
        _fail("candidate.trace", "requires exactly one trace blob")
    trace_record = blobs.get((BlobKind.TRACE, trace_refs[0].sha256))
    if trace_record is None:
        _fail("candidate.trace", "blob is unavailable")
    trace = _parse_trace(trace_record.content, "candidate.trace")
    if work.proposal_llm_calls != proposal.llm_calls:
        _fail("candidate.work", "proposal count disagrees with proposal outcome")
    if work.solver_calls != terminal.iteration + 1:
        _fail("candidate.work", "solver count disagrees with terminal iteration")
    edit_obj = cast(dict[str, object], cast(dict[str, object], payload["work"])["edit_counts"])
    trace_steps = tuple(value.to_trace_step() for value in trace)
    if edit_obj != {
        "applied": sum(value.event == "EDIT_APPLIED" for value in trace_steps),
        "invalid": sum(value.event == "MODEL_EDIT_INVALID" for value in trace_steps),
        "no_op": sum(
            value.event == "EDIT_REJECTED" and value.data.get("status") == "noop"
            for value in trace_steps
        ),
        "rejected": sum(
            value.event == "EDIT_REJECTED" and value.data.get("status") == "rejected"
            for value in trace_steps
        ),
    }:
        _fail("candidate.work.edit_counts", "does not match the trace")
    faithfulness_gate = (
        None
        if terminal.tab is None
        else FaithfulnessGate(
            terminal_score.melody_f1,
            terminal_score.bass_root,
            terminal_score.harmony,
            terminal_score.faithfulness_passed,
            terminal_score.evaluated_dimensions,
            terminal_score.unavailable_dimensions,
        )
    )
    candidate_status = (
        CandidateStatus.GREEN
        if terminal.oracle is not None and terminal.oracle.verdict == "GREEN"
        else CandidateStatus.NON_GREEN_TAB
        if terminal.tab is not None
        else CandidateStatus.NO_TAB
    )
    expected_termination = {
        CandidateStatus.GREEN: "GREEN_CERTIFIED",
        CandidateStatus.NON_GREEN_TAB: "NON_GREEN_TAB",
        CandidateStatus.NO_TAB: "NO_TAB",
    }[candidate_status]
    if cast(dict[str, object], payload["work"])["termination_reason"] != expected_termination:
        _fail("candidate.work.termination_reason", "disagrees with the terminal outcome")
    if critic is not None and candidate_status is not CandidateStatus.GREEN:
        _fail("candidate.critic", "only a GREEN terminal may carry a critic")
    trajectory = CandidateTrajectory(
        cast(int, row.key.candidate_index),
        plan.temperature,
        proposal,
        proposal.target,
        initial,
        terminal,
        candidate_status,
        candidate_status is CandidateStatus.GREEN,
        terminal_ranking,
        faithfulness_gate,
        critic,
        trace,
        work,
    )
    return RescoredRow(
        row,
        (NamedCheckpoint("initial", initial_score), NamedCheckpoint("terminal", terminal_score)),
        trajectory,
        None,
        None,
    )


def _raw_from_row(
    plan: ExperimentPlan,
    goal: ArrangeGoal,
    profile: Profile,
    row: BenchmarkRow,
    item: CorpusItem,
    blobs: dict[tuple[BlobKind, str], BlobRecord],
    *,
    full: bool,
) -> RescoredRow:
    payload = _exact_dict(
        row.payload,
        "raw",
        frozenset({"source", "outcome", "score"}),
    )
    _parse_source(payload["source"], row, item, blobs, "raw.source")
    obj = _exact_dict(
        payload["outcome"],
        "raw.outcome",
        frozenset(
            {
                "status",
                "parse_code",
                "call_failure_code",
                "llm_calls",
                "source_context_sha256",
                "tab_blob_sha256",
                "call",
            }
        ),
    )
    status_text = _text(obj["status"], "raw.outcome.status")
    try:
        status = RawStatus(cast(str, status_text))
    except ValueError:
        _fail("raw.outcome.status", "is unsupported")
    parse_code: RawParseCode | None = None
    if obj["parse_code"] is not None:
        try:
            parse_code = RawParseCode(cast(str, _text(obj["parse_code"], "raw.outcome.parse_code")))
        except ValueError:
            _fail("raw.outcome.parse_code", "is unsupported")
    failure_code: CallFailureCode | None = None
    if obj["call_failure_code"] is not None:
        try:
            failure_code = CallFailureCode(
                cast(str, _text(obj["call_failure_code"], "raw.outcome.call_failure_code"))
            )
        except ValueError:
            _fail("raw.outcome.call_failure_code", "is unsupported")
    expected_source_context = build_raw_baseline_request(
        item.ir,
        _goal_at_source_tempo(goal, item),
        profile,
    ).source_context_sha256
    stored_source_context = _sha(obj["source_context_sha256"], "raw.outcome.source_context_sha256")
    if stored_source_context != expected_source_context:
        _fail(
            "raw.outcome.source_context_sha256",
            "does not bind the frozen source-tempo raw request",
        )
    tab: Tab | None = None
    if obj["tab_blob_sha256"] is not None:
        record = _owned_blob(
            row,
            blobs,
            BlobKind.TAB,
            obj["tab_blob_sha256"],
            "raw.outcome.tab_blob_sha256",
        )
        tab = _parse_tab(record.content, item, profile, "raw.tab")
    stored_score = _score_from_dict(payload["score"], "raw.score")
    _validate_stored_score_contract(
        stored_score,
        item,
        profile,
        "raw.score",
        fallback_assisted=False,
        llm_generated=status is RawStatus.VALID_TAB,
    )
    if full:
        actual = _checkpoint_score(
            item,
            tab,
            fallback_assisted=False,
            llm_generated=status is RawStatus.VALID_TAB,
            profile=profile,
        )
        if stored_score != actual:
            _fail("raw.score", "stored score drifted")
    elif stored_score.tab_available is not (tab is not None):
        _fail("raw.score", "tab availability is inconsistent")
    if len(row.observation_keys) != 1:
        _fail("raw.observation_keys", "requires exactly one logical call")
    key = row.observation_keys[0]
    call = _exact_dict(
        obj["call"],
        "raw.outcome.call",
        frozenset(
            {
                "logical_call_id",
                "call_index",
                "stage",
                "stage_ordinal",
                "requested_output_tokens",
                "attempt_reserved_output_tokens",
                "provider_attempts",
                "requested_model_id",
                "returned_model_id",
                "system_sha256",
                "user_sha256",
                "request_sha256",
                "reply_sha256",
                "status",
                "failure_code",
                "retry_count",
                "usage",
            }
        ),
    )
    call_key = ObservationKey(
        cast(
            str,
            _text(call["logical_call_id"], "raw.outcome.call.logical_call_id"),
        ),
        _integer(call["call_index"], "raw.outcome.call.call_index"),
    )
    if call_key != key:
        _fail("raw.outcome.call", "does not match the row observation key")
    if (
        _text(call["stage"], "raw.outcome.call.stage") != "raw"
        or _integer(call["stage_ordinal"], "raw.outcome.call.stage_ordinal") != 0
    ):
        _fail("raw.outcome.call", "must identify the raw stage at ordinal zero")
    budget = next(value for value in plan.matched_budgets if value.item_id == item.item_id)
    if (
        _integer(
            call["requested_output_tokens"],
            "raw.outcome.call.requested_output_tokens",
            minimum=1,
        )
        != budget.proposal_tokens
    ):
        _fail("raw.outcome.call.requested_output_tokens", "does not match the frozen policy")
    _integer(
        call["attempt_reserved_output_tokens"],
        "raw.outcome.call.attempt_reserved_output_tokens",
        minimum=1,
        maximum=16_000_000,
    )
    provider_attempts = _integer(
        call["provider_attempts"],
        "raw.outcome.call.provider_attempts",
        minimum=1,
        maximum=16,
    )
    _text(call["requested_model_id"], "raw.outcome.call.requested_model_id")
    _text(call["returned_model_id"], "raw.outcome.call.returned_model_id", nullable=True)
    _sha(call["system_sha256"], "raw.outcome.call.system_sha256")
    _sha(call["user_sha256"], "raw.outcome.call.user_sha256")
    _sha(call["request_sha256"], "raw.outcome.call.request_sha256")
    reply_sha = _sha(call["reply_sha256"], "raw.outcome.call.reply_sha256", nullable=True)
    call_status = _text(call["status"], "raw.outcome.call.status")
    if call_status not in {"succeeded", "failed"}:
        _fail("raw.outcome.call.status", "is unsupported")
    call_failure = _text(call["failure_code"], "raw.outcome.call.failure_code", nullable=True)
    if (call_status == "failed") is not (call_failure is not None):
        _fail("raw.outcome.call.failure_code", "does not match call status")
    if _integer(call["retry_count"], "raw.outcome.call.retry_count", maximum=15) != (
        provider_attempts - 1
    ):
        _fail("raw.outcome.call.retry_count", "does not match provider attempts")
    usage = _exact_dict(
        call["usage"],
        "raw.outcome.call.usage",
        frozenset(
            {
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            }
        ),
    )
    for usage_name, usage_value in usage.items():
        _optional_integer(usage_value, f"raw.outcome.call.usage.{usage_name}")
    if (status is RawStatus.CALL_FAILED) is not (reply_sha is None):
        _fail("raw.outcome.call.reply_sha256", "does not match the raw call status")
    if (status is RawStatus.CALL_FAILED) is not (call_status == "failed"):
        _fail("raw.outcome.call.status", "does not match the raw outcome")
    outcome = RawLLMOutcome(
        cast(int, row.key.sample_index),
        status,
        tab,
        parse_code,
        failure_code,
        _integer(obj["llm_calls"], "raw.outcome.llm_calls", minimum=1, maximum=1),
        expected_source_context,
        RawObservationKey(row.run_id, key.logical_call_id, key.call_index),
    )
    return RescoredRow(row, (NamedCheckpoint("score", stored_score),), None, outcome, None)


def _pure_from_row(
    goal: ArrangeGoal,
    profile: Profile,
    row: BenchmarkRow,
    item: CorpusItem,
    blobs: dict[tuple[BlobKind, str], BlobRecord],
    *,
    full: bool,
) -> RescoredRow:
    payload = _exact_dict(
        row.payload,
        "pure_solver",
        frozenset({"source", "outcome", "score", "baseline"}),
    )
    _parse_source(payload["source"], row, item, blobs, "pure_solver.source")
    baseline = _exact_dict(
        payload["baseline"],
        "pure_solver.baseline",
        frozenset({"baseline_id", "llm_calls", "solver_calls"}),
    )
    if baseline != {"baseline_id": "B2", "llm_calls": 0, "solver_calls": 1}:
        _fail("pure_solver.baseline", "must identify one deterministic B2 solve")
    obj = _exact_dict(
        payload["outcome"],
        "pure_solver.outcome",
        frozenset({"status", "target_blob_sha256", "tab_blob_sha256", "infeasible"}),
    )
    target_record = _owned_blob(
        row,
        blobs,
        BlobKind.TARGET,
        obj["target_blob_sha256"],
        "pure_solver.outcome.target_blob_sha256",
    )
    target = _parse_target(target_record.content, "pure_solver.target")
    tab: Tab | None = None
    if obj["tab_blob_sha256"] is not None:
        record = _owned_blob(
            row,
            blobs,
            BlobKind.TAB,
            obj["tab_blob_sha256"],
            "pure_solver.outcome.tab_blob_sha256",
        )
        tab = _parse_tab(record.content, item, profile, "pure_solver.tab")
    infeasible = _parse_infeasible(
        obj["infeasible"], "pure_solver.outcome.infeasible", nullable=True
    )
    status_text = _text(obj["status"], "pure_solver.outcome.status")
    try:
        status = PureSolverStatus(cast(str, status_text))
    except ValueError:
        _fail("pure_solver.outcome.status", "is unsupported")
    stored_score = _score_from_dict(payload["score"], "pure_solver.score")
    _validate_stored_score_contract(
        stored_score,
        item,
        profile,
        "pure_solver.score",
        fallback_assisted=False,
        llm_generated=False,
    )
    if full:
        exact_goal = _goal_at_source_tempo(goal, item)
        solver_ir = arrangement_solver_ir(item.ir)
        expected_target = propose_fingerstyle(
            solver_ir,
            exact_goal.tuning,
            exact_goal.capo,
            profile=profile,
            tempo_bpm=exact_goal.tempo_bpm,
        )
        if target != expected_target:
            _fail("pure_solver.target", "stored deterministic target drifted")
        solved = solve_fingering(
            expected_target,
            exact_goal.tuning,
            exact_goal.capo,
            profile,
            tempo_bpm=exact_goal.tempo_bpm,
            beats_per_bar=item.ir.meta.time_sig[0],
        )
        if isinstance(solved, Tab):
            if tab != solved or infeasible is not None:
                _fail("pure_solver.outcome", "stored solver result drifted")
        elif tab is not None or infeasible != solved:
            _fail("pure_solver.outcome", "stored solver result drifted")
        actual = _checkpoint_score(
            item,
            tab,
            fallback_assisted=False,
            llm_generated=False,
            profile=profile,
        )
        if stored_score != actual:
            _fail("pure_solver.score", "stored score drifted")
    elif stored_score.tab_available is not (tab is not None):
        _fail("pure_solver.score", "tab availability is inconsistent")
    outcome = PureSolverOutcome(status, tab, infeasible)
    return RescoredRow(row, (NamedCheckpoint("score", stored_score),), None, None, outcome)


def rescore_row_bundle(
    plan: ExperimentPlan,
    goal: ArrangeGoal,
    profile: Profile,
    rows: tuple[BenchmarkRow, ...],
    blobs: tuple[BlobRecord, ...],
    *,
    mode: ReplayMode = ReplayMode.FULL_RESCORE,
) -> RescoredRowBundle:
    """Strictly parse rows and either recompute or explicitly trust stored scores."""

    if type(goal) is not ArrangeGoal:
        _fail("goal", "must be an exact ArrangeGoal")
    exact_profile = ensure_profile(profile)
    if type(mode) is not ReplayMode:
        _fail("mode", "must be an exact ReplayMode")
    ordered_rows, ordered_blobs = _normalized_inputs(plan, rows, blobs)
    items = _item_index(plan)
    index = _blob_index(ordered_blobs)
    rescored: list[RescoredRow] = []
    full = mode is ReplayMode.FULL_RESCORE
    for row in ordered_rows:
        item = items.get(row.key.item_id)
        if item is None:
            _fail("row.key.item_id", "is not present in the plan")
        _validate_row_identity(row, item)
        for ref in row.blob_refs:
            if (ref.kind, ref.sha256) not in index:
                _fail("row.blob_refs", "contains an unavailable blob")
        if row.key.row_type is RowType.CANDIDATE:
            rescored.append(
                _candidate_from_row(plan, goal, exact_profile, row, item, index, full=full)
            )
        elif row.key.row_type is RowType.RAW:
            rescored.append(_raw_from_row(plan, goal, exact_profile, row, item, index, full=full))
        else:
            rescored.append(_pure_from_row(goal, exact_profile, row, item, index, full=full))
    observation_keys = tuple(key for row in ordered_rows for key in row.observation_keys)
    if len(set(observation_keys)) != len(observation_keys):
        _fail("rows.observation_keys", "one logical call is owned by multiple rows")
    ordered_keys = tuple(sorted(observation_keys, key=lambda value: value.sort_key))
    if tuple(value.call_index for value in ordered_keys) != tuple(range(len(ordered_keys))):
        _fail("rows.observation_keys", "must form one contiguous call-index prefix")
    requested_models: set[str] = set()
    for row in ordered_rows:
        payload = row.payload
        if row.key.row_type is RowType.CANDIDATE:
            work = cast(dict[str, object], payload["work"])
            calls = cast(list[object], work["calls"])
        elif row.key.row_type is RowType.RAW:
            outcome = cast(dict[str, object], payload["outcome"])
            calls = [outcome["call"]]
        else:
            calls = []
        for raw in calls:
            requested_models.add(cast(str, cast(dict[str, object], raw)["requested_model_id"]))
    if len(requested_models) > 1:
        _fail("rows.requested_model_id", "all collected arms must share one requested model")
    return RescoredRowBundle(mode, tuple(rescored))


def resume_state_from_rows(
    plan: ExperimentPlan,
    goal: ArrangeGoal,
    profile: Profile,
    rows: tuple[BenchmarkRow, ...],
    blobs: tuple[BlobRecord, ...],
) -> ExperimentResumeState:
    """Restore deterministic controls and one exact continuous schedule prefix."""

    rescored = rescore_row_bundle(
        plan,
        goal,
        profile,
        rows,
        blobs,
        mode=ReplayMode.FULL_RESCORE,
    )
    by_identity = {
        (value.row.key.row_type, value.row.key.item_id, value.row.key.candidate_index): value
        for value in rescored.rows
    }
    pure_slots: list[CompletedPureSolver | None] = []
    pure_gap = False
    for item in plan.items:
        value = by_identity.get((RowType.PURE_SOLVER, item.item_id, None))
        if value is None:
            pure_slots.append(None)
            pure_gap = True
            continue
        if pure_gap:
            _fail("rows", "pure-solver rows must form the planned item callback prefix")
        pure_outcome = value.pure_outcome
        if pure_outcome is None:
            _fail("rows", "pure-solver row did not restore a pure outcome")
        pure_slots.append(
            CompletedPureSolver(
                item.item_id,
                arrangement_source_context_sha256(item.ir),
                pure_outcome,
            )
        )
    completed: list[CompletedExperimentUnit] = []
    gap = False
    next_call_index = 0
    used: set[RowKey] = {
        value.row.key for value in rescored.rows if value.row.key.row_type is RowType.PURE_SOLVER
    }
    for unit in plan.collection_schedule:
        row_type = RowType.CANDIDATE if unit.arm is CollectionArm.AGENT else RowType.RAW
        value = by_identity.get((row_type, unit.item_id, unit.candidate_index))
        if value is None:
            gap = True
            continue
        if gap:
            _fail("rows", "completed collection rows must form one schedule prefix")
        call_indices = tuple(key.call_index for key in value.row.observation_keys)
        expected_call_indices = tuple(range(next_call_index, next_call_index + len(call_indices)))
        if not call_indices or call_indices != expected_call_indices:
            _fail(
                "rows.observation_keys",
                "collection-unit call slices must follow the global schedule prefix",
            )
        next_call_index += len(call_indices)
        if unit.arm is CollectionArm.AGENT and (
            value.trajectory is None or value.trajectory.work.proposal_llm_calls != 1
        ):
            _fail(
                "rows.observation_keys",
                "an agent unit requires proposal0 before repair/critic calls",
            )
        used.add(value.row.key)
        completed.append(
            CompletedExperimentUnit(
                unit,
                arrangement_source_context_sha256(plan.items[unit.item_position].ir),
                trajectory=value.trajectory,
                raw_outcome=value.raw_outcome,
            )
        )
    if used != {value.row.key for value in rescored.rows}:
        _fail("rows", "contains a row outside the restorable schedule")
    if completed and any(value is None for value in pure_slots):
        _fail(
            "rows",
            "a nonempty collection prefix requires every prior pure-solver callback",
        )
    return ExperimentResumeState(tuple(pure_slots), tuple(completed))


def report_to_dict(report: BenchmarkReport) -> dict[str, object]:
    if type(report) is not BenchmarkReport:
        _fail("report", "must be an exact BenchmarkReport")
    value = parse_canonical_json_bytes(report.wire_json)
    if type(value) is not dict:
        _fail("report", "wire payload must encode an object")
    result = cast(dict[str, object], value)
    if result.get("schema") != BENCHMARK_REPORT_VERSION:
        _fail("report.schema", "is unsupported")
    if result.get("run_id") != report.run_id or result.get("mode") != report.mode.value:
        _fail("report", "wrapper fields disagree with the wire payload")
    return result


@dataclass(frozen=True, slots=True)
class _ItemEvaluation:
    item: CorpusItem
    candidates: tuple[RescoredRow, ...]
    raw: tuple[RescoredRow, ...]
    pure: RescoredRow


def _checkpoint(row: RescoredRow, name: str) -> CheckpointScore:
    for value in row.checkpoints:
        if value.name == name:
            return value.score
    _fail("rescored.checkpoints", f"is missing {name}")


def _complete_item_evaluations(
    plan: ExperimentPlan, rescored: RescoredRowBundle
) -> tuple[_ItemEvaluation, ...]:
    grouped: dict[str, list[RescoredRow]] = defaultdict(list)
    for row in rescored.rows:
        grouped[row.row.key.item_id].append(row)
    if set(grouped) != {value.item_id for value in plan.items}:
        _fail("rows", "a complete report must cover every planned item")
    result: list[_ItemEvaluation] = []
    for item in plan.items:
        values = grouped[item.item_id]
        candidates = tuple(
            sorted(
                (value for value in values if value.row.key.row_type is RowType.CANDIDATE),
                key=lambda value: cast(int, value.row.key.candidate_index),
            )
        )
        raw = tuple(
            sorted(
                (value for value in values if value.row.key.row_type is RowType.RAW),
                key=lambda value: cast(int, value.row.key.candidate_index),
            )
        )
        pure = tuple(value for value in values if value.row.key.row_type is RowType.PURE_SOLVER)
        if (
            len(candidates) != EXPERIMENT_N_SAMPLES
            or tuple(value.row.key.candidate_index for value in candidates)
            != tuple(range(EXPERIMENT_N_SAMPLES))
            or len(raw) != EXPERIMENT_N_SAMPLES
            or tuple(value.row.key.candidate_index for value in raw)
            != tuple(range(EXPERIMENT_N_SAMPLES))
            or len(pure) != 1
        ):
            _fail("rows", f"item {item.item_id} does not contain the exact 10+10+1 rows")
        result.append(_ItemEvaluation(item, candidates, raw, pure[0]))
    return tuple(result)


def _stratum_tuple(item: CorpusItem) -> tuple[str, str, str, str]:
    if item.evidence is None:
        _fail("item.evidence", "must be snapshotted")
    return (
        item.layer,
        item.evidence.signature,
        item.synthetic_complexity,
        item.polyphony,
    )


def _stratum_identifier(item: CorpusItem) -> str:
    encoded = "\0".join(_stratum_tuple(item)).encode("utf-8")
    return f"stratum:{hashlib.sha256(encoded).hexdigest()[:24]}"


def _family_values(
    items: tuple[_ItemEvaluation, ...],
    values: dict[str, float],
) -> tuple[FamilyValue, ...]:
    grouped: dict[str, list[tuple[_ItemEvaluation, float]]] = defaultdict(list)
    for item in items:
        family = item.item.family_id
        cluster = item.item.cluster_id
        if family is None or cluster is None:
            _fail("item.identity", "family and cluster are required")
        grouped[family].append((item, values[item.item.item_id]))
    result: list[FamilyValue] = []
    for family, members in sorted(grouped.items()):
        clusters = {cast(str, value.item.cluster_id) for value, _score in members}
        strata = {_stratum_identifier(value.item) for value, _score in members}
        if len(clusters) != 1 or len(strata) != 1:
            _fail("families", "one family cannot cross clusters or report strata")
        result.append(
            FamilyValue(
                family,
                next(iter(clusters)),
                next(iter(strata)),
                math.fsum(score for _value, score in members) / len(members),
            )
        )
    return tuple(result)


def _bootstrap_wire(
    values: tuple[FamilyValue, ...], *, seed: int, repetitions: int
) -> dict[str, object]:
    return cast(
        dict[str, object],
        _json_ready(
            family_cluster_bootstrap_mean(
                values,
                seed=seed,
                repetitions=repetitions,
            )
        ),
    )


def _selection(
    item: _ItemEvaluation,
    k: int,
    *,
    use_critic: bool,
    profile: Profile,
) -> tuple[int | None, CheckpointScore]:
    trajectories = tuple(cast(CandidateTrajectory, value.trajectory) for value in item.candidates)
    pool = ArrangePool(
        candidates=tuple(
            value if value.terminal.tab is not None else None for value in trajectories
        ),
        trace=Trace(),
        n=len(trajectories),
        candidate_traces=tuple(value.trace_steps for value in trajectories),
        trajectories=trajectories,
    )
    result = best_of_k(pool, k, use_critic=use_critic)
    selected = tuple(
        value.candidate_index for value in result.trace.steps if value.event == "CANDIDATE_SELECTED"
    )
    if not selected:
        fallback = any(value.proposal.fallback_assisted for value in trajectories[:k])
        return None, _checkpoint_score(
            item.item,
            None,
            fallback_assisted=fallback,
            llm_generated=False,
            profile=profile,
        )
    winner = selected[-1]
    if type(winner) is not int or not 0 <= winner < k:
        _fail("selection", "winner is outside the selected prefix")
    return winner, _checkpoint(item.candidates[winner], "terminal")


_RELIABILITY_PREDICATES: Final[tuple[str, ...]] = (
    "initial_green",
    "initial_joint",
    "terminal_green",
    "terminal_joint",
    "terminal_llm_success",
    "raw_green",
    "raw_joint",
)


def _predicate_count(item: _ItemEvaluation, predicate: str) -> int:
    if predicate.startswith("initial_"):
        scores = tuple(_checkpoint(value, "initial") for value in item.candidates)
        attribute = predicate.removeprefix("initial_")
    elif predicate.startswith("terminal_"):
        scores = tuple(_checkpoint(value, "terminal") for value in item.candidates)
        attribute = predicate.removeprefix("terminal_")
    else:
        scores = tuple(_checkpoint(value, "score") for value in item.raw)
        attribute = predicate.removeprefix("raw_")
    field = {
        "green": "green",
        "joint": "joint_success",
        "llm_success": "llm_success",
    }[attribute]
    return sum(bool(getattr(value, field)) for value in scores)


def _reliability_wire(
    items: tuple[_ItemEvaluation, ...],
    *,
    seed: int,
    repetitions: int,
) -> list[dict[str, object]]:
    metadata: dict[tuple[int, str], tuple[dict[str, int], str, str]] = {}
    endpoints: list[FamilyBootstrapEndpoint] = []
    for k in RELIABILITY_K_VALUES:
        for predicate in _RELIABILITY_PREDICATES:
            counts = {item.item.item_id: _predicate_count(item, predicate) for item in items}
            pass_any = {
                item.item.item_id: pass_at_k(EXPERIMENT_N_SAMPLES, counts[item.item.item_id], k)
                for item in items
            }
            pass_all = {
                item.item.item_id: pass_hat_k_item(
                    EXPERIMENT_N_SAMPLES, counts[item.item.item_id], k
                )
                for item in items
            }
            any_name = f"k{k}:{predicate}:pass-at-k"
            all_name = f"k{k}:{predicate}:pass-all-k"
            endpoints.extend(
                (
                    FamilyBootstrapEndpoint(any_name, _family_values(items, pass_any)),
                    FamilyBootstrapEndpoint(all_name, _family_values(items, pass_all)),
                )
            )
            metadata[(k, predicate)] = (counts, any_name, all_name)
    estimates = {
        name: cast(dict[str, object], _json_ready(value))
        for name, value in family_cluster_bootstrap_means(
            tuple(endpoints),
            seed=seed,
            repetitions=repetitions,
        )
    }
    result: list[dict[str, object]] = []
    for k in RELIABILITY_K_VALUES:
        predicates: dict[str, object] = {}
        for predicate in _RELIABILITY_PREDICATES:
            counts, any_name, all_name = metadata[(k, predicate)]
            predicates[predicate] = {
                "family_count": len({cast(str, value.item.family_id) for value in items}),
                "sample_successes": sum(counts.values()),
                "sample_denominator": len(items) * EXPERIMENT_N_SAMPLES,
                "pass_at_k": estimates[any_name],
                "pass_all_k": estimates[all_name],
            }
        result.append({"k": k, "predicates": predicates})
    return result


def _arm_scores(item: _ItemEvaluation, arm: str) -> tuple[CheckpointScore, ...]:
    if arm == "initial":
        return tuple(_checkpoint(value, "initial") for value in item.candidates)
    if arm == "terminal":
        return tuple(_checkpoint(value, "terminal") for value in item.candidates)
    if arm == "raw":
        return tuple(_checkpoint(value, "score") for value in item.raw)
    if arm == "pure_solver":
        return (_checkpoint(item.pure, "score"),)
    _fail("arm", "is unsupported")


def _fidelity_wire(
    items: tuple[_ItemEvaluation, ...],
    *,
    seed: int,
    repetitions: int,
) -> dict[str, object]:
    score_field = {
        "melody": "melody_f1",
        "bass_root": "bass_root",
        "harmony": "harmony",
    }
    result: dict[str, object] = {}
    for arm_index, arm in enumerate(("initial", "terminal", "raw", "pure_solver")):
        dimensions: dict[str, object] = {}
        for dimension_index, (dimension, field) in enumerate(score_field.items()):
            applicable = 0
            scored = 0
            family_applicable: dict[str, list[float]] = defaultdict(list)
            family_scored: dict[str, list[float]] = defaultdict(list)
            family_meta: dict[str, _ItemEvaluation] = {}
            for item in items:
                family = cast(str, item.item.family_id)
                family_meta.setdefault(family, item)
                for score in _arm_scores(item, arm):
                    if dimension not in score.evaluated_dimensions:
                        continue
                    applicable += 1
                    value = cast(float | None, getattr(score, field))
                    family_applicable[family].append(0.0 if value is None else value)
                    if value is not None:
                        scored += 1
                        family_scored[family].append(value)

            def values_for(
                groups: dict[str, list[float]],
                family_meta: dict[str, _ItemEvaluation] = family_meta,
            ) -> tuple[FamilyValue, ...]:
                values: list[FamilyValue] = []
                for family, scores in sorted(groups.items()):
                    if not scores:
                        continue
                    item = family_meta[family]
                    values.append(
                        FamilyValue(
                            family,
                            cast(str, item.item.cluster_id),
                            _stratum_identifier(item.item),
                            math.fsum(scores) / len(scores),
                        )
                    )
                return tuple(values)

            offset = arm_index * 31 + dimension_index
            dimensions[dimension] = {
                "applicable_outcomes": applicable,
                "scored_outcomes": scored,
                "failed_or_unscored_outcomes": applicable - scored,
                "conditional_scored": _bootstrap_wire(
                    values_for(family_scored),
                    seed=seed + 10_000 + offset,
                    repetitions=repetitions,
                ),
                "failure_inclusive": _bootstrap_wire(
                    values_for(family_applicable),
                    seed=seed + 20_000 + offset,
                    repetitions=repetitions,
                ),
            }
        result[arm] = dimensions
    return result


def _binary_arm_wire(
    items: tuple[_ItemEvaluation, ...],
    arm: str,
    field: Literal["green", "joint_success", "llm_success"],
    *,
    seed: int,
    repetitions: int,
) -> dict[str, object]:
    values: dict[str, float] = {}
    successes = 0
    denominator = 0
    for item in items:
        scores = _arm_scores(item, arm)
        count = sum(bool(getattr(value, field)) for value in scores)
        successes += count
        denominator += len(scores)
        values[item.item.item_id] = count / len(scores)
    return {
        "successes": successes,
        "denominator": denominator,
        "equal_family": _bootstrap_wire(
            _family_values(items, values), seed=seed, repetitions=repetitions
        ),
    }


def _one_outcome_rate(successes: int, denominator: int) -> dict[str, object]:
    return {
        "numerator": successes,
        "denominator": denominator,
        "wilson_95": _json_ready(wilson_interval(successes, denominator)),
        "clopper_pearson_95": _json_ready(clopper_pearson_interval(successes, denominator)),
    }


def _family_binary_rate(values: tuple[FamilyValue, ...]) -> dict[str, object]:
    if any(value.value not in {0.0, 1.0} for value in values):
        _fail("family_binary_rate", "requires exactly one binary outcome per family")
    return _one_outcome_rate(sum(value.value == 1.0 for value in values), len(values))


def _profile_sensitivity(
    items: tuple[_ItemEvaluation, ...], median: Profile
) -> list[dict[str, object]]:
    profiles = (SMALL_HAND, median, LARGE_HAND)
    result: list[dict[str, object]] = []
    for profile in profiles:
        arms: dict[str, object] = {}
        for arm in ("initial", "terminal", "raw", "pure_solver"):
            verdicts = [
                next(
                    value.verdict
                    for value in score.profiles
                    if value.profile_fingerprint == profile.fingerprint
                )
                for item in items
                for score in _arm_scores(item, arm)
            ]
            arms[arm] = {
                "GREEN": verdicts.count("GREEN"),
                "AMBER": verdicts.count("AMBER"),
                "RED": verdicts.count("RED"),
                "unavailable": verdicts.count(None),
                "denominator": len(verdicts),
            }
        result.append(
            {
                "profile_fingerprint": profile.fingerprint,
                "profile_version": profile.version,
                "arms": arms,
            }
        )
    return result


def _stratum_wire(
    items: tuple[_ItemEvaluation, ...],
    *,
    profile: Profile,
    seed: int,
    repetitions: int,
) -> dict[str, object]:
    first = items[0].item
    layer, evidence, complexity, polyphony = _stratum_tuple(first)
    pure_scores = tuple(_checkpoint(value.pure, "score") for value in items)
    pure_joint = sum(value.joint_success for value in pure_scores)
    search_curve: list[dict[str, object]] = []
    for offset, k in enumerate(SEARCH_K_VALUES):
        selected = {
            item.item.item_id: float(
                _selection(item, k, use_critic=True, profile=profile)[1].joint_success
            )
            for item in items
        }
        family_selected = _family_values(items, selected)
        search_curve.append(
            {
                "k": k,
                "selected_joint_rate": _family_binary_rate(family_selected),
                "terminal_joint": _bootstrap_wire(
                    family_selected,
                    seed=seed + 30_000 + offset,
                    repetitions=repetitions,
                ),
            }
        )
    return {
        "layer": layer,
        "evidence_signature": evidence,
        "synthetic_complexity": complexity,
        "polyphony": polyphony,
        "genres": sorted({value.item.genre for value in items}),
        "item_count": len(items),
        "family_count": len({cast(str, value.item.family_id) for value in items}),
        "cluster_count": len({cast(str, value.item.cluster_id) for value in items}),
        "reliability": _reliability_wire(items, seed=seed, repetitions=repetitions),
        "binary_outcomes": {
            arm: {
                field: _binary_arm_wire(
                    items,
                    arm,
                    cast(Any, field),
                    seed=seed + 40_000 + arm_index * 7 + field_index,
                    repetitions=repetitions,
                )
                for field_index, field in enumerate(("green", "joint_success", "llm_success"))
            }
            for arm_index, arm in enumerate(("initial", "terminal", "raw", "pure_solver"))
        },
        "fidelity": _fidelity_wire(items, seed=seed, repetitions=repetitions),
        "search_curve": search_curve,
        "baselines": {
            "raw_llm": {
                "green": _binary_arm_wire(
                    items,
                    "raw",
                    "green",
                    seed=seed + 50_001,
                    repetitions=repetitions,
                ),
                "joint": _binary_arm_wire(
                    items,
                    "raw",
                    "joint_success",
                    seed=seed + 50_002,
                    repetitions=repetitions,
                ),
            },
            "pure_solver": {
                **_one_outcome_rate(pure_joint, len(pure_scores)),
                "baseline_id": "B2",
                "solver_calls_per_item": 1,
                "llm_calls_per_item": 0,
            },
        },
    }


def _call_wire_index(rows: tuple[BenchmarkRow, ...]) -> dict[ObservationKey, dict[str, object]]:
    result: dict[ObservationKey, dict[str, object]] = {}
    for row in rows:
        payload = row.payload
        if row.key.row_type is RowType.CANDIDATE:
            work = cast(dict[str, object], payload["work"])
            calls = cast(list[object], work["calls"])
        elif row.key.row_type is RowType.RAW:
            outcome = cast(dict[str, object], payload["outcome"])
            calls = [outcome["call"]]
        else:
            calls = []
        for raw in calls:
            call = cast(dict[str, object], raw)
            key = ObservationKey(cast(str, call["logical_call_id"]), cast(int, call["call_index"]))
            if key in result:
                _fail("rows.calls", "contains a duplicate logical call")
            result[key] = call
    return result


def _observation_index(
    observations: SanitizedObservations,
    run_id: str,
    calls: dict[ObservationKey, dict[str, object]],
) -> dict[ObservationKey, dict[str, object]]:
    if type(observations) is not SanitizedObservations or observations.run_id != run_id:
        _fail("observations", "must be the sanitized records for this run")
    result: dict[ObservationKey, dict[str, object]] = {}
    usage_keys = frozenset(
        {
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        }
    )
    for index, encoded in enumerate(observations.calls_json):
        path = f"observations.calls[{index}]"
        obj = _exact_dict(
            parse_canonical_json_bytes(encoded),
            path,
            frozenset(
                {
                    "logical_call_id",
                    "call_index",
                    "status",
                    "failure_code",
                    "attempts",
                    "retry_count",
                    "returned_model_id",
                    "usage",
                    "elapsed_microseconds",
                }
            ),
        )
        key = ObservationKey(
            cast(str, _text(obj["logical_call_id"], f"{path}.logical_call_id")),
            _integer(obj["call_index"], f"{path}.call_index"),
        )
        call = calls.get(key)
        if call is None or key in result:
            _fail(path, "does not bind exactly one row call")
        attempts = _exact_list(obj["attempts"], f"{path}.attempts", maximum=16)
        for attempt_index, raw_attempt in enumerate(attempts):
            attempt = _exact_dict(
                raw_attempt,
                f"{path}.attempts[{attempt_index}]",
                frozenset({"attempt_id", "attempt_index", "status", "retryable"}),
            )
            if (
                _integer(
                    attempt["attempt_index"], f"{path}.attempts[{attempt_index}].attempt_index"
                )
                != attempt_index
            ):
                _fail(f"{path}.attempts", "indices are noncanonical")
            _text(attempt["attempt_id"], f"{path}.attempts[{attempt_index}].attempt_id")
            attempt_status = _text(attempt["status"], f"{path}.attempts[{attempt_index}].status")
            if attempt_status not in {"succeeded", "failed"}:
                _fail(f"{path}.attempts[{attempt_index}].status", "is unsupported")
            _boolean(attempt["retryable"], f"{path}.attempts[{attempt_index}].retryable")
        usage = _exact_dict(obj["usage"], f"{path}.usage", usage_keys)
        for name, value in usage.items():
            _optional_integer(value, f"{path}.usage.{name}")
        status = _text(obj["status"], f"{path}.status")
        if status not in {"succeeded", "failed"}:
            _fail(f"{path}.status", "is unsupported")
        failure = _text(obj["failure_code"], f"{path}.failure_code", nullable=True)
        returned = _text(obj["returned_model_id"], f"{path}.returned_model_id", nullable=True)
        retry_count = _integer(obj["retry_count"], f"{path}.retry_count", maximum=15)
        elapsed = obj["elapsed_microseconds"]
        if elapsed is not None:
            _integer(elapsed, f"{path}.elapsed_microseconds", maximum=86_400_000_000)
        if (
            status != call["status"]
            or failure != call["failure_code"]
            or returned != call["returned_model_id"]
            or retry_count != call["retry_count"]
            or len(attempts) != call["provider_attempts"]
            or usage != call["usage"]
        ):
            _fail(path, "status/retry/model/usage metadata disagrees with its row call")
        result[key] = obj
    if set(result) != set(calls):
        _fail("observations", "does not exactly cover all row logical calls")
    return result


def _cost_summary_wire(
    keys: tuple[ObservationKey, ...],
    calls: dict[ObservationKey, dict[str, object]],
    observations: dict[ObservationKey, dict[str, object]],
    *,
    divisor: int = 1,
) -> dict[str, object]:
    selected = tuple(sorted(keys, key=lambda value: value.sort_key))
    if len(set(selected)) != len(selected) or divisor <= 0:
        _fail("cost", "call keys or divisor are invalid")

    def divided(value: int) -> int | float:
        return value if divisor == 1 else value / divisor

    def total(name: str) -> int | float:
        return divided(sum(cast(int, calls[key][name]) for key in selected))

    usage_names = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    provider_usage: dict[str, float | None] = {}
    for name in usage_names:
        values = [cast(dict[str, object], observations[key]["usage"])[name] for key in selected]
        provider_usage[name] = (
            None
            if any(value is None for value in values)
            else divided(sum(cast(int, value) for value in values))
        )
    elapsed_values = [observations[key]["elapsed_microseconds"] for key in selected]
    elapsed = (
        None
        if any(value is None for value in elapsed_values)
        else divided(sum(cast(int, value) for value in elapsed_values))
    )
    complete_provider_tokens = (
        None
        if any(value is None for value in provider_usage.values())
        else math.fsum(cast(int | float, value) for value in provider_usage.values())
    )
    return {
        "logical_calls": divided(len(selected)),
        "provider_attempts": total("provider_attempts"),
        "logical_requested_output_tokens": total("requested_output_tokens"),
        "attempt_reserved_output_tokens": total("attempt_reserved_output_tokens"),
        "elapsed_microseconds": elapsed,
        "provider_usage": provider_usage,
        "complete_provider_tokens": complete_provider_tokens,
    }


def _family_delta_values(values: tuple[FamilyValue, ...]) -> tuple[FamilyDelta, ...]:
    return tuple(FamilyDelta(value.family_id, value.value) for value in values)


def _pareto_cost_vector(point: dict[str, object]) -> tuple[float, float, float] | None:
    cost = cast(dict[str, object], point["cost"])
    values = (
        cost["logical_calls"],
        cost["complete_provider_tokens"],
        cost["elapsed_microseconds"],
    )
    if any(value is None for value in values):
        return None
    return cast(
        tuple[float, float, float],
        tuple(float(cast(int | float, value)) for value in values),
    )


def _pareto_nondominated(
    points: list[dict[str, object]], target_k: int
) -> tuple[bool | None, bool]:
    target = next(value for value in points if value["k"] == target_k)
    target_vector = _pareto_cost_vector(target)
    cost_unknown = target_vector is None or any(
        _pareto_cost_vector(value) is None for value in points
    )
    if cost_unknown:
        return None, False
    assert target_vector is not None
    target_quality = cast(float, target["joint_success"])
    for point in points:
        if point is target:
            continue
        vector = _pareto_cost_vector(point)
        assert vector is not None
        quality = cast(float, point["joint_success"])
        if (
            quality >= target_quality
            and all(left <= right for left, right in zip(vector, target_vector, strict=True))
            and (
                quality > target_quality
                or any(left < right for left, right in zip(vector, target_vector, strict=True))
            )
        ):
            return False, True
    return True, True


def _holm_by_name(values: tuple[NamedPValue, ...]) -> dict[str, float]:
    return {value.name: value.p_value for value in holm_adjust(values)}


def _primary_items(items: tuple[_ItemEvaluation, ...]) -> tuple[_ItemEvaluation, ...]:
    return tuple(
        value
        for value in items
        if value.item.layer == "procedural"
        and value.item.evidence is not None
        and value.item.evidence.signature == "melody+bass+harmony"
    )


def _decision_ready(
    bootstrap: dict[str, object],
    *,
    sesoi: float,
    adjusted_p: float | None,
) -> bool:
    point = bootstrap.get("point")
    lower = bootstrap.get("one_sided_97_5_lower")
    return (
        type(point) is float
        and point >= sesoi
        and type(lower) is float
        and lower > 0.0
        and adjusted_p is not None
        and adjusted_p < 0.05
    )


def _selection_maps(
    items: tuple[_ItemEvaluation, ...], profile: Profile, k: int, *, use_critic: bool
) -> tuple[dict[str, int | None], dict[str, CheckpointScore]]:
    winners: dict[str, int | None] = {}
    scores: dict[str, CheckpointScore] = {}
    for item in items:
        winner, score = _selection(item, k, use_critic=use_critic, profile=profile)
        winners[item.item.item_id] = winner
        scores[item.item.item_id] = score
    return winners, scores


def _inference_wire(
    plan: ExperimentPlan,
    profile: Profile,
    items: tuple[_ItemEvaluation, ...],
    calls: dict[ObservationKey, dict[str, object]],
    observations: dict[ObservationKey, dict[str, object]],
    *,
    bootstrap_seed: int,
    bootstrap_repetitions: int,
    sign_flip_seed: int,
    sign_flip_draws: int,
) -> dict[str, object]:
    primary = _primary_items(items)
    if not primary:
        return {
            "population": {
                "layer": "procedural",
                "evidence_signature": "melody+bass+harmony",
                "family_count": 0,
                "status": EstimateStatus.NO_DATA.value,
            },
            "repair": {"decision": CapabilityDecision.INCONCLUSIVE.value},
            "guards": {
                "no_repair": {"decision": CapabilityDecision.INCONCLUSIVE.value},
                "raw_llm": {"decision": CapabilityDecision.INCONCLUSIVE.value},
                "holm_family": [],
            },
            "search": {"decision": CapabilityDecision.INCONCLUSIVE.value},
            "critic": {"decision": CapabilityDecision.HUMAN_BLOCKED_PROBATION.value},
            "confirmatory_holm": [],
        }
    family_count = len({cast(str, value.item.family_id) for value in primary})

    repair_item_delta: dict[str, float] = {}
    repair_improved = 0
    repair_worsened = 0
    for item in primary:
        deltas = []
        for candidate in item.candidates:
            initial = _checkpoint(candidate, "initial").joint_success
            terminal = _checkpoint(candidate, "terminal").joint_success
            deltas.append(float(terminal) - float(initial))
            repair_improved += int(terminal and not initial)
            repair_worsened += int(initial and not terminal)
        repair_item_delta[item.item.item_id] = math.fsum(deltas) / len(deltas)
    repair_values = _family_values(primary, repair_item_delta)
    repair_bootstrap = _bootstrap_wire(
        repair_values,
        seed=bootstrap_seed + 100_000,
        repetitions=bootstrap_repetitions,
    )
    repair_signflip = paired_sign_flip_test(
        _family_delta_values(repair_values),
        seed=sign_flip_seed + 100_000,
        draws=sign_flip_draws,
    )

    search_winners_1, search_scores_1 = _selection_maps(primary, profile, 1, use_critic=True)
    search_winners_4, search_scores_4 = _selection_maps(
        primary, profile, FULL_SELECTION_K, use_critic=True
    )
    search_item_delta = {
        item.item.item_id: float(search_scores_4[item.item.item_id].joint_success)
        - float(search_scores_1[item.item.item_id].joint_success)
        for item in primary
    }
    search_values = _family_values(primary, search_item_delta)
    search_bootstrap = _bootstrap_wire(
        search_values,
        seed=bootstrap_seed + 110_000,
        repetitions=bootstrap_repetitions,
    )
    search_improved = sum(value.value > 0.0 for value in search_values)
    search_worsened = sum(value.value < 0.0 for value in search_values)
    search_mcnemar = exact_mcnemar(search_improved, search_worsened)

    confirmatory_raw = tuple(
        value
        for value in (
            None
            if repair_signflip.p_value is None
            else NamedPValue("repair_joint", repair_signflip.p_value),
            NamedPValue("search_best4_joint", search_mcnemar.p_value),
        )
        if value is not None
    )
    confirmatory_adjusted = _holm_by_name(confirmatory_raw)

    budget_by_item = {value.item_id: value for value in plan.matched_budgets}
    guard_specs = (
        ("no_repair", "initial"),
        ("raw_llm", "raw"),
    )
    guard_intermediate: dict[str, dict[str, object]] = {}
    guard_p_values: list[NamedPValue] = []
    for guard_offset, (guard_name, arm) in enumerate(guard_specs):
        item_deltas: dict[str, float] = {}
        statuses: list[str] = []
        prefixes: list[int] = []
        for item in primary:
            budget = budget_by_item[item.item.item_id]
            matched = budget.no_repair if arm == "initial" else budget.raw
            statuses.append(matched.status.value)
            prefixes.append(matched.prefix_samples)
            terminal_count = _predicate_count(item, "terminal_joint")
            control_count = _predicate_count(
                item, "initial_joint" if arm == "initial" else "raw_joint"
            )
            terminal_pass = pass_at_k(EXPERIMENT_N_SAMPLES, terminal_count, 1)
            control_pass = (
                0.0
                if matched.prefix_samples == 0
                else pass_at_k(
                    EXPERIMENT_N_SAMPLES,
                    control_count,
                    matched.prefix_samples,
                )
            )
            item_deltas[item.item.item_id] = terminal_pass - control_pass
        exact_budget = all(value == BudgetMatchStatus.EXACT.value for value in statuses)
        values = _family_values(primary, item_deltas)
        bootstrap = _bootstrap_wire(
            values,
            seed=bootstrap_seed + 120_000 + guard_offset,
            repetitions=bootstrap_repetitions,
        )
        signflip = paired_sign_flip_test(
            _family_delta_values(values),
            seed=sign_flip_seed + 120_000 + guard_offset,
            draws=sign_flip_draws,
        )
        if signflip.p_value is not None:
            guard_p_values.append(NamedPValue(guard_name, signflip.p_value))
        guard_intermediate[guard_name] = {
            "budget_match_statuses": sorted(set(statuses)),
            "prefix_samples": sorted(set(prefixes)),
            "budget_exact": exact_budget,
            "sesoi": CHEAP_GUARD_SESOI,
            "bootstrap": bootstrap,
            "sign_flip": _json_ready(signflip),
        }
    guard_adjusted = _holm_by_name(tuple(guard_p_values))
    guard_passes: dict[str, bool] = {}
    for guard_name, _arm in guard_specs:
        value = guard_intermediate[guard_name]
        adjusted = guard_adjusted.get(guard_name)
        bootstrap_value = cast(dict[str, object], value["bootstrap"])
        point = bootstrap_value.get("point")
        inconclusive = (
            not bool(value["budget_exact"])
            or 0 in cast(list[int], value["prefix_samples"])
            or point is None
            or point == 0.0
        )
        passes = bool(value["budget_exact"]) and _decision_ready(
            bootstrap_value,
            sesoi=CHEAP_GUARD_SESOI,
            adjusted_p=adjusted,
        )
        guard_passes[guard_name] = passes
        value["holm_adjusted_p"] = adjusted
        value["decision"] = (
            CapabilityDecision.KEEP.value
            if passes
            else CapabilityDecision.INCONCLUSIVE.value
            if inconclusive
            else CapabilityDecision.NOT_KEPT.value
        )

    prefix_points: list[dict[str, object]] = []
    for k in SEARCH_K_VALUES:
        keys = tuple(
            key
            for item in primary
            for row in item.candidates
            if cast(int, row.row.key.candidate_index) < k
            for key in row.row.observation_keys
        )
        cost = _cost_summary_wire(
            keys,
            calls,
            observations,
            divisor=family_count,
        )
        selected = {
            item.item.item_id: float(
                _selection(item, k, use_critic=True, profile=profile)[1].joint_success
            )
            for item in primary
        }
        selected_family_values = _family_values(primary, selected)
        quality = _bootstrap_wire(
            selected_family_values,
            seed=bootstrap_seed + 130_000 + k,
            repetitions=bootstrap_repetitions,
        )["point"]
        prefix_points.append(
            {
                "k": k,
                "joint_success": quality,
                "selected_joint_rate": _family_binary_rate(selected_family_values),
                "cost": cost,
            }
        )

    target_point = next(value for value in prefix_points if value["k"] == FULL_SELECTION_K)
    best_one_point = next(value for value in prefix_points if value["k"] == 1)
    nondominated, cost_complete = _pareto_nondominated(prefix_points, FULL_SELECTION_K)
    cost_unknown = not cost_complete

    repair_adjusted = confirmatory_adjusted.get("repair_joint")
    repair_pass = _decision_ready(
        repair_bootstrap, sesoi=REPAIR_SESOI, adjusted_p=repair_adjusted
    ) and all(guard_passes.values())
    repair_decision = (
        CapabilityDecision.KEEP
        if repair_pass
        else CapabilityDecision.INCONCLUSIVE
        if any(
            value["decision"] == CapabilityDecision.INCONCLUSIVE.value
            for value in guard_intermediate.values()
        )
        else CapabilityDecision.NOT_KEPT
    )
    search_adjusted = confirmatory_adjusted.get("search_best4_joint")
    if cost_unknown:
        search_decision = CapabilityDecision.PROBATION_COST_UNKNOWN
    elif (
        _decision_ready(
            search_bootstrap,
            sesoi=SEARCH_SESOI,
            adjusted_p=search_adjusted,
        )
        and nondominated
    ):
        search_decision = CapabilityDecision.KEEP
    else:
        search_decision = CapabilityDecision.NOT_KEPT

    without_winners, without_scores = _selection_maps(
        primary, profile, FULL_SELECTION_K, use_critic=False
    )
    with_winners, with_scores = _selection_maps(primary, profile, FULL_SELECTION_K, use_critic=True)
    critic_self_delta: dict[str, float] = {}
    critic_joint_delta: dict[str, float] = {}
    critic_fidelity_deltas: dict[str, dict[str, float]] = {
        dimension: {} for dimension in FAITHFULNESS_DIMENSIONS
    }
    critic_conditional_deltas: dict[str, dict[str, float]] = {
        dimension: {} for dimension in FAITHFULNESS_DIMENSIONS
    }
    critic_applicable: dict[str, int] = defaultdict(int)
    critic_paired_scored: dict[str, int] = defaultdict(int)
    score_fields = {
        "melody": "melody_f1",
        "bass_root": "bass_root",
        "harmony": "harmony",
    }
    for item in primary:
        item_id = item.item.item_id

        def critic_score(index: int | None, current_item: _ItemEvaluation = item) -> float:
            if index is None:
                return 0.0
            outcome = cast(CandidateTrajectory, current_item.candidates[index].trajectory).critic
            return 0.0 if outcome is None else outcome.overall

        critic_self_delta[item_id] = critic_score(with_winners[item_id]) - critic_score(
            without_winners[item_id]
        )
        critic_joint_delta[item_id] = float(with_scores[item_id].joint_success) - float(
            without_scores[item_id].joint_success
        )
        for dimension, field in score_fields.items():
            if dimension not in with_scores[item_id].evaluated_dimensions:
                continue
            critic_applicable[dimension] += 1
            with_value = cast(float | None, getattr(with_scores[item_id], field))
            without_value = cast(float | None, getattr(without_scores[item_id], field))
            critic_fidelity_deltas[dimension][item_id] = (with_value or 0.0) - (
                without_value or 0.0
            )
            if with_value is not None and without_value is not None:
                critic_paired_scored[dimension] += 1
                critic_conditional_deltas[dimension][item_id] = with_value - without_value

    critic_fidelity_wire: dict[str, object] = {}
    for offset, dimension in enumerate(FAITHFULNESS_DIMENSIONS):
        conditional_items = tuple(
            item for item in primary if item.item.item_id in critic_conditional_deltas[dimension]
        )
        critic_fidelity_wire[dimension] = {
            "applicable_pairs": critic_applicable[dimension],
            "paired_scored_pairs": critic_paired_scored[dimension],
            "failure_inclusive_delta": _bootstrap_wire(
                _family_values(primary, critic_fidelity_deltas[dimension]),
                seed=bootstrap_seed + 140_010 + offset,
                repetitions=bootstrap_repetitions,
            ),
            "conditional_scored_delta": _bootstrap_wire(
                _family_values(
                    conditional_items,
                    critic_conditional_deltas[dimension],
                ),
                seed=bootstrap_seed + 140_020 + offset,
                repetitions=bootstrap_repetitions,
            ),
        }

    without_joint_values = _family_values(
        primary,
        {
            item.item.item_id: float(without_scores[item.item.item_id].joint_success)
            for item in primary
        },
    )
    with_joint_values = _family_values(
        primary,
        {
            item.item.item_id: float(with_scores[item.item.item_id].joint_success)
            for item in primary
        },
    )

    return {
        "population": {
            "layer": "procedural",
            "evidence_signature": "melody+bass+harmony",
            "family_count": family_count,
            "status": EstimateStatus.ESTIMATED.value,
        },
        "repair": {
            "sesoi": REPAIR_SESOI,
            "candidate_discordance": {
                "improved": repair_improved,
                "worsened": repair_worsened,
                "denominator": len(primary) * EXPERIMENT_N_SAMPLES,
            },
            "bootstrap": repair_bootstrap,
            "sign_flip": _json_ready(repair_signflip),
            "holm_adjusted_p": repair_adjusted,
            "decision": repair_decision.value,
        },
        "guards": {
            **guard_intermediate,
            "holm_family": [
                {"name": value.name, "adjusted_p": guard_adjusted.get(value.name)}
                for value in sorted(guard_p_values, key=lambda child: child.name)
            ],
        },
        "search": {
            "sesoi": SEARCH_SESOI,
            "best_of_1_winners": search_winners_1,
            "best_of_4_winners": search_winners_4,
            "bootstrap": search_bootstrap,
            "mcnemar": _json_ready(search_mcnemar),
            "matched_odds_ratio": _json_ready(search_mcnemar.odds_ratio),
            "holm_adjusted_p": search_adjusted,
            "causal_shared_first4_cost": {
                "best_of_1": target_point["cost"],
                "best_of_4": target_point["cost"],
                "equal_cost": True,
            },
            "deployment_cost": {
                "best_of_1_prefix_1": best_one_point["cost"],
                "best_of_4_prefix_4": target_point["cost"],
            },
            "deployment_pareto": {
                "points": prefix_points,
                "best_of_4_nondominated": nondominated,
                "cost_complete": not cost_unknown,
            },
            "decision": search_decision.value,
        },
        "critic": {
            "k": FULL_SELECTION_K,
            "self_score_delta": _bootstrap_wire(
                _family_values(primary, critic_self_delta),
                seed=bootstrap_seed + 140_001,
                repetitions=bootstrap_repetitions,
            ),
            "joint_delta": _bootstrap_wire(
                _family_values(primary, critic_joint_delta),
                seed=bootstrap_seed + 140_002,
                repetitions=bootstrap_repetitions,
            ),
            "selected_joint_rates": {
                "without_critic": _family_binary_rate(without_joint_values),
                "with_critic": _family_binary_rate(with_joint_values),
            },
            "fidelity_side_effects": critic_fidelity_wire,
            "external_human_evidence": "not_collected",
            "decision": CapabilityDecision.HUMAN_BLOCKED_PROBATION.value,
        },
        "confirmatory_holm": [
            {
                "name": value.name,
                "raw_p": value.p_value,
                "adjusted_p": confirmatory_adjusted[value.name],
            }
            for value in sorted(confirmatory_raw, key=lambda child: child.name)
        ],
    }


def build_benchmark_report(
    plan: ExperimentPlan,
    goal: ArrangeGoal,
    profile: Profile,
    rows: tuple[BenchmarkRow, ...],
    blobs: tuple[BlobRecord, ...],
    observations: SanitizedObservations,
    *,
    publication_bindings: ReportPublicationBindings,
    mode: ReplayMode = ReplayMode.FULL_RESCORE,
    bootstrap_seed: int = 0,
    bootstrap_repetitions: int = 10_000,
    sign_flip_seed: int = 0,
    sign_flip_draws: int = 100_000,
) -> BenchmarkReport:
    """Build the deterministic canonical report without any model call."""

    if type(publication_bindings) is not ReportPublicationBindings:
        _fail("publication_bindings", "must be exact ReportPublicationBindings")
    if publication_bindings.run_id != plan.run_id:
        _fail("publication_bindings.run_id", "does not match the plan")
    if publication_bindings.corpus_sha256 != corpus_sha256(plan.items):
        _fail("publication_bindings.corpus_sha256", "does not match the planned corpus")

    valid_bootstrap_seed = _integer(bootstrap_seed, "bootstrap_seed", maximum=(1 << 63) - 1)
    valid_bootstrap_repetitions = _integer(
        bootstrap_repetitions,
        "bootstrap_repetitions",
        minimum=1,
        maximum=100_000,
    )
    valid_sign_flip_seed = _integer(sign_flip_seed, "sign_flip_seed", maximum=(1 << 63) - 1)
    valid_sign_flip_draws = _integer(
        sign_flip_draws,
        "sign_flip_draws",
        minimum=1,
        maximum=1_000_000,
    )
    maximum_seed = (1 << 63) - 1
    bootstrap_offset = max(
        140_100,
        max(0, len(plan.items) - 1) * 1_000_000 + 50_100,
    )
    if valid_bootstrap_seed > maximum_seed - bootstrap_offset:
        _fail("bootstrap_seed", "does not leave room for frozen derived offsets")
    if valid_sign_flip_seed > maximum_seed - 120_100:
        _fail("sign_flip_seed", "does not leave room for frozen derived offsets")
    rescored = rescore_row_bundle(
        plan,
        goal,
        profile,
        rows,
        blobs,
        mode=mode,
    )
    item_values = _complete_item_evaluations(plan, rescored)
    ordered_rows, ordered_blobs = _normalized_inputs(plan, rows, blobs)
    call_wires = _call_wire_index(ordered_rows)
    observed = _observation_index(observations, plan.run_id, call_wires)
    grouped: dict[tuple[str, str, str, str], list[_ItemEvaluation]] = defaultdict(list)
    for item in item_values:
        grouped[_stratum_tuple(item.item)].append(item)
    strata = [
        _stratum_wire(
            tuple(sorted(values, key=lambda value: value.item.item_id)),
            profile=profile,
            seed=valid_bootstrap_seed + index * 1_000_000,
            repetitions=valid_bootstrap_repetitions,
        )
        for index, (_key, values) in enumerate(sorted(grouped.items()))
    ]
    all_call_keys = tuple(sorted(call_wires, key=lambda value: value.sort_key))
    usage = _cost_summary_wire(all_call_keys, call_wires, observed)
    requested_models = sorted(
        {cast(str, value["requested_model_id"]) for value in call_wires.values()}
    )
    returned_models = sorted(
        {
            cast(str, value["returned_model_id"])
            for value in call_wires.values()
            if value["returned_model_id"] is not None
        }
    )
    rows_bytes = canonical_jsonl_bytes(tuple(row_to_dict(value) for value in ordered_rows))
    blobs_bytes = canonical_jsonl_bytes(
        tuple(blob_record_to_dict(value) for value in ordered_blobs)
    )
    actual_rows_sha256 = canonical_table_sha256("rows", rows_bytes)
    actual_blobs_sha256 = canonical_table_sha256("blobs", blobs_bytes)
    if (
        publication_bindings.rows_sha256 != actual_rows_sha256
        or publication_bindings.blobs_sha256 != actual_blobs_sha256
        or publication_bindings.observations_sha256 != observations.sha256
        or publication_bindings.expected_rows != len(ordered_rows)
        or publication_bindings.observed_rows != len(ordered_rows)
        or publication_bindings.observed_calls != len(call_wires)
    ):
        _fail(
            "publication_bindings",
            "receipt table hashes or completion counts disagree with report inputs",
        )
    report_object: dict[str, object] = {
        "schema": BENCHMARK_REPORT_VERSION,
        "run_id": plan.run_id,
        "mode": mode.value,
        "replay_policy": (
            "deterministic_solver_oracle_faithfulness_rescore"
            if mode is ReplayMode.FULL_RESCORE
            else "explicit_trust_of_stored_scores"
        ),
        "checker": {
            "checker_version": CHECKER_VERSION,
            "input_schema_version": ORACLE_INPUT_SCHEMA_VERSION,
            "confirmatory_profile_version": profile.version,
            "confirmatory_profile_fingerprint": profile.fingerprint,
        },
        "input_bindings": {
            **cast(dict[str, object], _json_ready(publication_bindings)),
            "rows_sha256": actual_rows_sha256,
            "blobs_sha256": actual_blobs_sha256,
            "observations_sha256": observations.sha256,
            "row_count": len(ordered_rows),
            "blob_count": len(ordered_blobs),
            "logical_call_count": len(call_wires),
        },
        "sampling": {
            "n_samples": plan.n_samples,
            "temperature": plan.temperature,
            "max_repair_iters": plan.max_repair_iters,
            "reliability_k": list(plan.reliability_k),
            "search_k": list(plan.search_k),
            "schedule_seed": plan.schedule_seed,
        },
        "strata": strata,
        "profile_sensitivity": _profile_sensitivity(item_values, profile),
        "inference": _inference_wire(
            plan,
            profile,
            item_values,
            call_wires,
            observed,
            bootstrap_seed=valid_bootstrap_seed,
            bootstrap_repetitions=valid_bootstrap_repetitions,
            sign_flip_seed=valid_sign_flip_seed,
            sign_flip_draws=valid_sign_flip_draws,
        ),
        "optional_baselines": [
            {
                "baseline_id": value.baseline_id.value,
                "reason": value.reason,
                "status": value.status.value,
            }
            for value in OPTIONAL_BASELINE_AVAILABILITY
        ],
        "usage": {
            **usage,
            "requested_model_ids": requested_models,
            "returned_model_ids": returned_models,
        },
        "estimator_configuration": {
            "bootstrap_seed": valid_bootstrap_seed,
            "bootstrap_repetitions": valid_bootstrap_repetitions,
            "bootstrap_quantile": "type_7",
            "sign_flip_seed": valid_sign_flip_seed,
            "sign_flip_draws": valid_sign_flip_draws,
            "confirmatory_holm_family": ["repair_joint", "search_best4_joint"],
            "guard_holm_family": ["no_repair", "raw_llm"],
        },
    }
    wire = canonical_json_bytes(report_object)
    return BenchmarkReport(plan.run_id, mode, wire)


def report_to_markdown(report: BenchmarkReport) -> str:
    """Render a deterministic human-readable view of existing report fields."""

    wire = report_to_dict(report)
    inference = cast(dict[str, object], wire["inference"])
    repair = cast(dict[str, object], inference["repair"])
    search = cast(dict[str, object], inference["search"])
    critic = cast(dict[str, object], inference["critic"])
    lines = [
        "# FretSure benchmark report",
        "",
        f"- Schema: `{wire['schema']}`",
        f"- Run: `{wire['run_id']}`",
        f"- Replay mode: `{wire['mode']}`",
        f"- Report digest: `{report.sha256}`",
        "",
        "## Capability decisions",
        "",
        "| Capability | Decision |",
        "|---|---|",
        f"| Repair | {repair['decision']} |",
        f"| Best-of-4 search | {search['decision']} |",
        f"| Critic | {critic['decision']} |",
        "",
        "## Strata",
        "",
        "| Layer | Evidence | Complexity | Polyphony | Items | Families |",
        "|---|---|---|---|---:|---:|",
    ]
    for raw in cast(list[object], wire["strata"]):
        stratum = cast(dict[str, object], raw)
        lines.append(
            "| "
            + " | ".join(
                str(stratum[name])
                for name in (
                    "layer",
                    "evidence_signature",
                    "synthetic_complexity",
                    "polyphony",
                    "item_count",
                    "family_count",
                )
            )
            + " |"
        )
    usage = cast(dict[str, object], wire["usage"])
    lines.extend(
        (
            "",
            "## Operational summary",
            "",
            f"- Logical calls: {usage['logical_calls']}",
            f"- Provider attempts: {usage['provider_attempts']}",
            f"- Elapsed microseconds: {usage['elapsed_microseconds']}",
            "",
        )
    )
    return "\n".join(lines)
