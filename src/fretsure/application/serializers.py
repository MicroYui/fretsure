"""Deterministic wire serializers for application-service contracts."""

from __future__ import annotations

import json
import math
from fractions import Fraction
from typing import cast

import fretsure
import fretsure.agent.trace as trace_module
from fretsure.agent.skills import ARRANGEMENT_SKILL_REGISTRY_VERSION
from fretsure.application.contracts import (
    PROFILE_REGISTRY_VERSION,
    SERVICE_VERSION,
    ApplicationCode,
    ApplicationError,
    ArrangeOptions,
    ArrangeOutcome,
    CheckOptions,
    CheckOutcome,
    DifficultyOptions,
    DifficultyOutcome,
    FingeringEditOutcome,
    RenderOptions,
    RenderOutcome,
    SectionRegenerationOutcome,
    ServiceCapabilities,
    SolveOptions,
    SolveOutcome,
    VerifiedAlternative,
)
from fretsure.application.editable_target import (
    EDITABLE_TARGET_SCHEMA_VERSION,
    editable_target_to_wire,
)
from fretsure.application.target import (
    MAX_TARGET_JSON_BYTES,
    MAX_TARGET_JSON_DEPTH,
    MAX_TARGET_JSON_NODES,
    TARGET_INPUT_SCHEMA_VERSION,
)
from fretsure.arrange.revision import SECTION_REGENERATION_VERSION
from fretsure.arrange.style_profiles import (
    STYLE_PROFILE_SHA256,
    STYLE_PROFILE_VERSION,
)
from fretsure.arrange.styles import (
    ARRANGEMENT_STYLE_REGISTRY_VERSION,
    ARRANGEMENT_STYLES,
)
from fretsure.difficulty.checker import DIFFICULTY_CHECKER_VERSION
from fretsure.difficulty.estimate import (
    PUBLISHED_GRADE_ESTIMATOR_VERSION,
    PUBLISHED_GRADE_MODEL_SHA256,
    PUBLISHED_GRADE_TRAINING_SCOPE,
)
from fretsure.difficulty.tiers import (
    ADVANCED,
    BEGINNER,
    INTERMEDIATE,
    Tier,
    snapshot_tier,
)
from fretsure.geometry import STANDARD_TUNING
from fretsure.importers import SCORE_FORMAT_REGISTRY, SCORE_INPUT_VERSION, SCORE_SUFFIXES
from fretsure.importers.contracts import ImportDiagnostic, ImportSuccess, SourceLocation
from fretsure.metrics.fidelity import FIDELITY_CHECKER_VERSION, FaithfulnessGate
from fretsure.oracle.core import CHECKER_VERSION, OracleResult
from fretsure.oracle.diagnostics import Diagnostic
from fretsure.oracle.input import (
    MAX_AGENT_CANDIDATES,
    MAX_AGENT_REPAIR_ITERS,
    MAX_BEATS_PER_BAR,
    MAX_SOLVER_BEAM,
    MAX_TEMPO_BPM,
    MIN_TEMPO_BPM,
    ORACLE_INPUT_SCHEMA_VERSION,
)
from fretsure.oracle.profiles import (
    LARGE_HAND,
    MEDIAN_HAND,
    SMALL_HAND,
    Profile,
    validated_profile_snapshot,
)
from fretsure.render.ascii import render_ascii
from fretsure.render.contracts import (
    GUITAR_PRO_EXPORT_VERSION,
    MUSICXML_TAB_EXPORT_VERSION,
    PDF_TAB_EXPORT_VERSION,
)
from fretsure.solver.api import FINGERING_SOLVER_VERSION, Infeasible, InfeasibleCode
from fretsure.solver.left_hand import LEFT_HAND_MODEL_VERSION
from fretsure.solver.score import SCORE_SOLVER_VERSION
from fretsure.solver.score_supervision import (
    PUBLISHED_FINGERING_FEATURE_SCHEMA,
    PUBLISHED_FINGERING_MODEL_SHA256,
    PUBLISHED_FINGERING_RANKER_VERSION,
)
from fretsure.solver.technique import (
    TECHNIQUE_PROFILE_REGISTRY_VERSION,
    TECHNIQUE_PROFILES,
)
from fretsure.tab import MAX_TAB_JSON_BYTES, Tab, tab_to_json

Wire = dict[str, object]

_CANDIDATE_SELECTED_DATA_FIELDS = frozenset(
    {
        "winner_candidate_index",
        "candidates_considered",
        "verdict",
        "green_certified",
        "playability_gate",
        "faithfulness_passed",
        "ranking_melody_recall",
        "ranking_bass_preserved",
        "ranking_harmony_jaccard",
        "melody_f1",
        "bass_root_accuracy",
        "harmony_jaccard",
        "evaluated_dimensions",
        "unavailable_dimensions",
        "critic_status",
        "critic_overall",
    }
)

_INFEASIBLE_MESSAGES = {
    InfeasibleCode.EMPTY_TARGET: "the target contains no notes to finger",
    InfeasibleCode.UNREACHABLE_PITCH: (
        "at least one target pitch is unreachable with this instrument configuration"
    ),
    InfeasibleCode.NO_FRAME_CONFIG: (
        "the bounded solver found no admissible fingering for one target frame"
    ),
    InfeasibleCode.NO_NON_RED_EXTENSION: (
        "the bounded solver found no non-RED continuation within its search budget"
    ),
}


def _serialization_error(path: str) -> ApplicationError:
    return ApplicationError(
        ApplicationCode.SERIALIZATION_FAILED,
        path,
        "application result could not be serialized safely",
    )


def _fraction_token(value: Fraction | None, *, path: str) -> str | None:
    if value is None:
        return None
    if type(value) is not Fraction:
        raise _serialization_error(path)
    try:
        numerator = object.__getattribute__(value, "_numerator")
        denominator = object.__getattribute__(value, "_denominator")
    except (AttributeError, TypeError):
        raise _serialization_error(path) from None
    if type(numerator) is not int or type(denominator) is not int or denominator <= 0:
        raise _serialization_error(path)
    return f"{numerator}/{denominator}"


def _profile_wire(name: str, profile: Profile) -> Wire:
    snapshot = validated_profile_snapshot(profile)
    return {
        "name": name,
        "version": snapshot.version,
        "fingerprint": snapshot.fingerprint,
        "calibration_status": "placeholder_pending_human_calibration",
    }


def _location_wire(location: SourceLocation | None) -> Wire | None:
    if location is None:
        return None
    if type(location) is not SourceLocation:
        raise _serialization_error("source.warnings.location")
    string_fields = (
        location.part_id,
        location.measure,
        location.voice,
        location.element,
        location.archive_member,
    )
    if any(value is not None and type(value) is not str for value in string_fields):
        raise _serialization_error("source.warnings.location")
    index_fields = (location.track_index, location.event_index, location.tick)
    if any(value is not None and (type(value) is not int or value < 0) for value in index_fields):
        raise _serialization_error("source.warnings.location")
    if location.channel is not None and (
        type(location.channel) is not int or not 1 <= location.channel <= 16
    ):
        raise _serialization_error("source.warnings.location")
    return {
        "part_id": location.part_id,
        "measure": location.measure,
        "voice": location.voice,
        "element": location.element,
        "archive_member": location.archive_member,
        "track_index": location.track_index,
        "event_index": location.event_index,
        "channel": location.channel,
        "tick": location.tick,
    }


def _import_diagnostic_wire(diagnostic: ImportDiagnostic) -> Wire:
    return {
        "code": diagnostic.code.value,
        "severity": diagnostic.severity.value,
        "message": diagnostic.message,
        "location": _location_wire(diagnostic.location),
    }


def _source_wire(imported: ImportSuccess) -> Wire:
    provenance = imported.provenance
    if provenance is None or type(provenance.source_format) is not str:
        raise _serialization_error("source.format")
    expected_importer = SCORE_FORMAT_REGISTRY.get(provenance.source_format)
    if expected_importer is None:
        raise _serialization_error("source.format")
    if imported.importer_version != expected_importer:
        raise _serialization_error("source.importer_version")
    return {
        "filename": provenance.source_filename,
        "format": provenance.source_format,
        "raw_sha256": provenance.raw_sha256,
        "root_member": provenance.root_member,
        "root_sha256": provenance.root_sha256,
        "container_version": provenance.container_version,
        "importer_version": imported.importer_version,
        "warnings": [_import_diagnostic_wire(item) for item in imported.warnings],
    }


def _score_summary_wire(imported: ImportSuccess) -> Wire:
    ir = imported.ir
    voice_counts = {
        voice: sum(1 for note in ir.notes if note.voice == voice)
        for voice in ("melody", "bass", "harmony")
    }
    return {
        "title": ir.meta.title,
        "key": ir.meta.key,
        "time_signature": {
            "numerator": ir.meta.time_sig[0],
            "denominator": ir.meta.time_sig[1],
        },
        "source_tempo_bpm": ir.meta.tempo_bpm,
        "duration_beats": _fraction_token(
            ir.meta.duration_beats,
            path="score.duration_beats",
        ),
        "note_count": len(ir.notes),
        "voice_counts": voice_counts,
        "chord_count": len(ir.chords),
        "source_description": ir.meta.source,
        "rights_or_license": ir.meta.license,
    }


def _tab_wire(tab: Tab | None) -> Wire | None:
    if tab is None:
        return None
    decoded = json.loads(tab_to_json(tab))
    if type(decoded) is not dict:
        raise _serialization_error("tab")
    return cast(Wire, decoded)


def _diagnostic_wire(diagnostic: Diagnostic) -> Wire:
    if not math.isfinite(diagnostic.overage):
        raise _serialization_error("playability.diagnostics.overage")
    return {
        "measure": diagnostic.measure,
        "beat": _fraction_token(diagnostic.beat, path="playability.diagnostics.beat"),
        "violation_type": diagnostic.violation_type,
        "offending_notes": list(diagnostic.offending_notes),
        "overage": diagnostic.overage,
        "suggested_relaxations": list(diagnostic.suggested_relaxations),
    }


def _playability_wire(oracle: OracleResult | None) -> Wire | None:
    if oracle is None:
        return None
    return {
        "verdict": oracle.verdict,
        "meaning": "versioned_model_relative_not_a_real_player_guarantee",
        "diagnostics": [_diagnostic_wire(item) for item in oracle.diagnostics],
        "checker_version": oracle.checker_version,
        "profile_version": oracle.profile_version,
        "profile_fingerprint": oracle.profile_fingerprint,
        "input_schema_version": oracle.input_schema_version,
    }


def _faithfulness_wire(gate: FaithfulnessGate | None) -> Wire | None:
    if gate is None:
        return None
    if type(gate) is not FaithfulnessGate:
        raise _serialization_error("faithfulness")
    try:
        snapshot = FaithfulnessGate(
            melody_f1=gate.melody_f1,
            bass_root=gate.bass_root,
            harmony=gate.harmony,
            passed=gate.passed,
            evaluated_dimensions=gate.evaluated_dimensions,
            unavailable_dimensions=gate.unavailable_dimensions,
        )
    except (AttributeError, TypeError, ValueError):
        raise _serialization_error("faithfulness") from None
    return {
        "melody_f1": snapshot.melody_f1,
        "bass_root_accuracy": snapshot.bass_root,
        "harmony_jaccard": snapshot.harmony,
        "evaluated_dimensions": list(snapshot.evaluated_dimensions),
        "unavailable_dimensions": list(snapshot.unavailable_dimensions),
        "passed": snapshot.passed,
        "checker_version": FIDELITY_CHECKER_VERSION,
    }


def _canonical_plain_object(value: object, *, path: str) -> Wire:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        decoded = json.loads(encoded)
    except (OverflowError, TypeError, ValueError, UnicodeError):
        raise _serialization_error(path) from None
    if type(decoded) is not dict:
        raise _serialization_error(path)
    return cast(Wire, decoded)


def _trace_wire(document_json: str) -> Wire:
    if type(document_json) is not str:
        raise _serialization_error("trace")
    try:
        decoded = json.loads(document_json)
    except (json.JSONDecodeError, RecursionError):
        raise _serialization_error("trace") from None
    wire = _canonical_plain_object(decoded, path="trace")
    if wire.get("schema_version") != _trace_schema_version() or type(wire.get("steps")) is not list:
        raise _serialization_error("trace")
    return wire


def _validate_trace_faithfulness_binding(
    trace: Wire,
    faithfulness: Wire | None,
) -> None:
    raw_steps = trace.get("steps")
    if type(raw_steps) is not list:
        raise _serialization_error("trace")
    selections = [
        step
        for step in raw_steps
        if type(step) is dict and step.get("event") == "CANDIDATE_SELECTED"
    ]
    if faithfulness is None:
        if selections:
            raise _serialization_error("trace.faithfulness")
        return
    if len(selections) != 1:
        raise _serialization_error("trace.faithfulness")
    data = selections[0].get("data")
    if type(data) is not dict or set(data) != _CANDIDATE_SELECTED_DATA_FIELDS:
        raise _serialization_error("trace.faithfulness")
    expected = {
        "melody_f1": faithfulness["melody_f1"],
        "bass_root_accuracy": faithfulness["bass_root_accuracy"],
        "harmony_jaccard": faithfulness["harmony_jaccard"],
        "evaluated_dimensions": faithfulness["evaluated_dimensions"],
        "unavailable_dimensions": faithfulness["unavailable_dimensions"],
        "faithfulness_passed": faithfulness["passed"],
    }
    for field_name, expected_value in expected.items():
        actual = data[field_name]
        if type(actual) is not type(expected_value) or actual != expected_value:
            raise _serialization_error("trace.faithfulness")


def _trace_schema_version() -> str:
    value = getattr(trace_module, "TRACE_SCHEMA_VERSION", None)
    if type(value) is not str:
        raise _serialization_error("trace.schema_version")
    return value


def _base_stamps(profile: Profile) -> Wire:
    snapshot = validated_profile_snapshot(profile)
    return {
        "package_version": fretsure.__version__,
        "service_version": SERVICE_VERSION,
        "profile_registry_version": PROFILE_REGISTRY_VERSION,
        "arrangement_style_registry_version": ARRANGEMENT_STYLE_REGISTRY_VERSION,
        "arrangement_style_profile_version": STYLE_PROFILE_VERSION,
        "arrangement_style_profile_sha256": STYLE_PROFILE_SHA256,
        "technique_profile_registry_version": TECHNIQUE_PROFILE_REGISTRY_VERSION,
        "arrangement_skill_registry_version": ARRANGEMENT_SKILL_REGISTRY_VERSION,
        "profile_version": snapshot.version,
        "profile_fingerprint": snapshot.fingerprint,
        "oracle_checker_version": CHECKER_VERSION,
        "oracle_input_schema_version": ORACLE_INPUT_SCHEMA_VERSION,
        "fidelity_checker_version": FIDELITY_CHECKER_VERSION,
        "fingering_solver_version": FINGERING_SOLVER_VERSION,
        "score_solver_version": SCORE_SOLVER_VERSION,
        "left_hand_model_version": LEFT_HAND_MODEL_VERSION,
        "published_fingering_ranker_version": PUBLISHED_FINGERING_RANKER_VERSION,
        "published_fingering_model_sha256": PUBLISHED_FINGERING_MODEL_SHA256,
        "published_fingering_feature_schema": PUBLISHED_FINGERING_FEATURE_SCHEMA,
        "target_input_schema_version": TARGET_INPUT_SCHEMA_VERSION,
        "editable_target_schema_version": EDITABLE_TARGET_SCHEMA_VERSION,
        "section_regeneration_version": SECTION_REGENERATION_VERSION,
        "trace_schema_version": _trace_schema_version(),
    }


def _arrange_options_wire(
    options: ArrangeOptions,
    profile: Profile,
    *,
    source_tempo_bpm: float,
    effective_tempo_bpm: float,
) -> Wire:
    return {
        "profile": _profile_wire(options.profile, profile),
        "style": options.style,
        "difficulty_tier": options.difficulty_tier,
        "technique_profile": options.technique_profile,
        "tuning": list(STANDARD_TUNING),
        "capo": 0,
        "candidate_count": options.n,
        "max_repair_iterations": options.max_iters,
        "critic_enabled": options.use_critic,
        "tempo_override_bpm": options.tempo_bpm,
        "source_tempo_bpm": source_tempo_bpm,
        "effective_tempo_bpm": effective_tempo_bpm,
    }


def _check_options_wire(options: CheckOptions, profile: Profile) -> Wire:
    return {
        "profile": _profile_wire(options.profile, profile),
        "tempo_bpm": options.tempo_bpm,
        "beats_per_bar": options.beats_per_bar,
    }


def _difficulty_options_wire(options: DifficultyOptions, tier: Tier) -> Wire:
    return {
        "tier": tier.name,
        "tempo_bpm": options.tempo_bpm,
        "beats_per_bar": options.beats_per_bar,
    }


def _difficulty_tier_wire(tier: Tier) -> Wire:
    snapshot = snapshot_tier(tier)
    return {
        "name": snapshot.name,
        "profile": _profile_wire(snapshot.name, snapshot.profile),
        "constraints": {
            "max_simultaneous": snapshot.max_simultaneous,
            "allow_barre": snapshot.allow_barre,
            "max_position": snapshot.max_position,
            "max_shifts_per_bar": snapshot.max_shifts_per_bar,
        },
    }


def _solve_options_wire(options: SolveOptions, profile: Profile) -> Wire:
    return {
        "profile": _profile_wire(options.profile, profile),
        "tuning": list(options.tuning),
        "capo": options.capo,
        "tempo_bpm": options.tempo_bpm,
        "beam": options.beam,
    }


def _render_options_wire(options: RenderOptions, profile: Profile) -> Wire:
    return {
        "format": options.format,
        "validation_profile": _profile_wire(options.profile, profile),
        "validation_tempo_bpm": options.tempo_bpm,
        "validation_beats_per_bar": options.beats_per_bar,
    }


def _infeasible_wire(value: Infeasible | None) -> Wire | None:
    if value is None:
        return None
    return {
        "code": value.code.value,
        "onset": _fraction_token(value.onset, path="infeasible.onset"),
        "pitches": list(value.pitches),
        "message": _INFEASIBLE_MESSAGES[value.code],
        "claim": "bounded_search_result_not_an_unsatisfiability_proof",
    }


def _verified_alternative_wire(value: VerifiedAlternative) -> Wire:
    if type(value) is not VerifiedAlternative:
        raise _serialization_error("alternatives")
    return {
        "candidate_index": value.candidate_index,
        "tab": _tab_wire(value.tab),
        "ascii": value.ascii,
        "playability": _playability_wire(value.oracle),
        "faithfulness": _faithfulness_wire(value.faithfulness),
        "work": {
            "model_calls": value.model_calls,
            "trial_solver_calls": value.solver_calls,
            "proposed_additions": value.proposed_additions,
            "accepted_additions": value.accepted_additions,
        },
        "proposal_status": value.proposal_status,
        "observed_critic": {
            "status": value.critic_status,
            "overall": value.critic_overall,
            "meaning": "machine_observation_not_human_musicality_evidence",
        },
    }


def arrange_outcome_to_wire(outcome: ArrangeOutcome) -> Wire:
    """Serialize a full arrangement with independent product gates."""

    if type(outcome) is not ArrangeOutcome:
        raise _serialization_error("outcome")
    try:
        stamps = _base_stamps(outcome.profile)
        stamps.update(
            {
                "score_input_version": SCORE_INPUT_VERSION,
                "importer_version": outcome.imported.importer_version,
                "model_id": outcome.model_id,
            }
        )
        faithfulness = _faithfulness_wire(outcome.faithfulness)
        trace = _trace_wire(outcome.trace_document_json)
        _validate_trace_faithfulness_binding(trace, faithfulness)
        return {
            "service_version": SERVICE_VERSION,
            "status": outcome.status,
            "source": _source_wire(outcome.imported),
            "score": _score_summary_wire(outcome.imported),
            "options": _arrange_options_wire(
                outcome.options,
                outcome.profile,
                source_tempo_bpm=outcome.source_tempo_bpm,
                effective_tempo_bpm=outcome.effective_tempo_bpm,
            ),
            "model": {"model_id": outcome.model_id},
            "editable_target": (
                None if outcome.target is None else editable_target_to_wire(outcome.target)
            ),
            "tab": _tab_wire(outcome.tab),
            "ascii": outcome.ascii,
            "playability": _playability_wire(outcome.oracle),
            "faithfulness": faithfulness,
            "alternatives": [_verified_alternative_wire(item) for item in outcome.alternatives],
            "trace": trace,
            "stamps": stamps,
        }
    except ApplicationError:
        raise
    except Exception:
        raise _serialization_error("outcome") from None


def check_outcome_to_wire(outcome: CheckOutcome) -> Wire:
    if type(outcome) is not CheckOutcome:
        raise _serialization_error("outcome")
    try:
        return {
            "service_version": SERVICE_VERSION,
            "status": "checked",
            "options": _check_options_wire(outcome.options, outcome.profile),
            "tab": _tab_wire(outcome.tab),
            "playability": _playability_wire(outcome.oracle),
            "stamps": _base_stamps(outcome.profile),
        }
    except ApplicationError:
        raise
    except Exception:
        raise _serialization_error("outcome") from None


def section_regeneration_outcome_to_wire(
    outcome: SectionRegenerationOutcome,
) -> Wire:
    if type(outcome) is not SectionRegenerationOutcome:
        raise _serialization_error("outcome")
    try:
        return {
            "service_version": SERVICE_VERSION,
            "status": outcome.status,
            "selection": {
                "start_measure": outcome.selection.start_measure,
                "end_measure": outcome.selection.end_measure,
                "locked_voices": list(outcome.selection.locked_voices),
            },
            "options": {
                "profile": _profile_wire(outcome.options.profile, outcome.profile),
                "style": outcome.options.style,
                "difficulty_tier": outcome.options.difficulty_tier,
                "technique_profile": outcome.options.technique_profile,
                "tempo_bpm": outcome.options.tempo_bpm,
            },
            "model": {"model_id": outcome.model_id},
            "editable_target": editable_target_to_wire(outcome.target),
            "tab": _tab_wire(outcome.tab),
            "ascii": outcome.ascii,
            "playability": _playability_wire(outcome.oracle),
            "faithfulness": _faithfulness_wire(outcome.faithfulness),
            "revision": {
                "schema_version": SECTION_REGENERATION_VERSION,
                "proposal_status": outcome.proposal_status,
                "model_calls": outcome.model_calls,
                "reason": outcome.reason,
            },
            "stamps": _base_stamps(outcome.profile),
        }
    except ApplicationError:
        raise
    except Exception:
        raise _serialization_error("outcome") from None


def fingering_edit_outcome_to_wire(outcome: FingeringEditOutcome) -> Wire:
    if type(outcome) is not FingeringEditOutcome:
        raise _serialization_error("outcome")
    try:
        note = outcome.tab.notes[outcome.note_index]
        return {
            "service_version": SERVICE_VERSION,
            "status": outcome.status,
            "options": _check_options_wire(outcome.options, outcome.profile),
            "tab": _tab_wire(outcome.tab),
            "ascii": outcome.ascii,
            "playability": _playability_wire(outcome.oracle),
            "attempted_playability": _playability_wire(outcome.attempted_oracle),
            "edit": {
                "note_index": outcome.note_index,
                "onset": _fraction_token(note.onset, path="edit.onset"),
                "string": note.string,
                "fret": note.fret,
                "before_finger": outcome.before_finger,
                "requested_finger": outcome.requested_finger,
                "reason": outcome.reason,
            },
            "stamps": _base_stamps(outcome.profile),
        }
    except ApplicationError:
        raise
    except Exception:
        raise _serialization_error("outcome") from None


def difficulty_outcome_to_wire(outcome: DifficultyOutcome) -> Wire:
    if type(outcome) is not DifficultyOutcome:
        raise _serialization_error("outcome")
    try:
        stamps = _base_stamps(outcome.tier.profile)
        stamps["difficulty_checker_version"] = DIFFICULTY_CHECKER_VERSION
        stamps["published_grade_estimator_version"] = PUBLISHED_GRADE_ESTIMATOR_VERSION
        stamps["published_grade_model_sha256"] = PUBLISHED_GRADE_MODEL_SHA256
        return {
            "service_version": SERVICE_VERSION,
            "status": "checked",
            "options": _difficulty_options_wire(outcome.options, outcome.tier),
            "tab": _tab_wire(outcome.tab),
            "tier": _difficulty_tier_wire(outcome.tier),
            "difficulty": {
                "checker_version": DIFFICULTY_CHECKER_VERSION,
                "meets": outcome.result.meets,
                "playable": outcome.result.playable,
                "tier_violations": list(outcome.result.tier_violations),
            },
            "published_grade": {
                "model_version": outcome.published_grade.model_version,
                "model_sha256": PUBLISHED_GRADE_MODEL_SHA256,
                "grade_system": outcome.published_grade.grade_system,
                "estimated_grade": outcome.published_grade.estimated_grade,
                "likely_interval": {
                    "lower": outcome.published_grade.likely_interval[0],
                    "upper": outcome.published_grade.likely_interval[1],
                },
                "band": outcome.published_grade.band,
                "confidence": outcome.published_grade.confidence,
                "burden_percentile": outcome.published_grade.burden_percentile,
                "feature_percentiles": {
                    name: value
                    for name, value in outcome.published_grade.feature_percentiles
                },
                "training_scope": PUBLISHED_GRADE_TRAINING_SCOPE,
                "meaning": "corpus_calibrated_estimate_not_a_playability_guarantee",
            },
            "stamps": stamps,
        }
    except ApplicationError:
        raise
    except Exception:
        raise _serialization_error("outcome") from None


def solve_outcome_to_wire(outcome: SolveOutcome) -> Wire:
    if type(outcome) is not SolveOutcome:
        raise _serialization_error("outcome")
    try:
        return {
            "service_version": SERVICE_VERSION,
            "status": outcome.status,
            "search_complete": outcome.search_complete,
            "max_solutions": outcome.max_solutions,
            "options": _solve_options_wire(outcome.options, outcome.profile),
            "tab": _tab_wire(outcome.tab),
            "ascii": None if outcome.tab is None else render_ascii(outcome.tab),
            "playability": _playability_wire(outcome.oracle),
            "infeasible": _infeasible_wire(outcome.infeasible),
            "stamps": _base_stamps(outcome.profile),
        }
    except ApplicationError:
        raise
    except Exception:
        raise _serialization_error("outcome") from None


def render_outcome_to_wire(outcome: RenderOutcome) -> Wire:
    if type(outcome) is not RenderOutcome:
        raise _serialization_error("outcome")
    try:
        return {
            "service_version": SERVICE_VERSION,
            "status": "rendered",
            "options": _render_options_wire(outcome.options, outcome.profile),
            "tab": _tab_wire(outcome.tab),
            "format": outcome.options.format,
            "content": outcome.content,
            "stamps": _base_stamps(outcome.profile),
        }
    except ApplicationError:
        raise
    except Exception:
        raise _serialization_error("outcome") from None


def capabilities_to_wire(value: ServiceCapabilities) -> Wire:
    if type(value) is not ServiceCapabilities:
        raise _serialization_error("capabilities")
    try:
        profile = validated_profile_snapshot(MEDIAN_HAND)
        stamps = _base_stamps(profile)
        stamps["score_input_version"] = SCORE_INPUT_VERSION
        stamps["musicxml_tab_export_version"] = MUSICXML_TAB_EXPORT_VERSION
        stamps["guitar_pro_export_version"] = GUITAR_PRO_EXPORT_VERSION
        stamps["pdf_tab_export_version"] = PDF_TAB_EXPORT_VERSION
        stamps["difficulty_checker_version"] = DIFFICULTY_CHECKER_VERSION
        registry = dict(value.score_format_registry)
        tiers = (BEGINNER, INTERMEDIATE, ADVANCED)
        player_profiles = {
            "small": SMALL_HAND,
            "median": MEDIAN_HAND,
            "large": LARGE_HAND,
        }
        if (
            value.service_version != SERVICE_VERSION
            or value.score_input_version != SCORE_INPUT_VERSION
            or registry != dict(SCORE_FORMAT_REGISTRY)
            or value.input_suffixes != SCORE_SUFFIXES
            or value.difficulty_tiers != tuple(tier.name for tier in tiers)
            or value.profiles != tuple(player_profiles)
            or value.arrangement_styles != tuple(ARRANGEMENT_STYLES)
            or value.technique_profiles != tuple(TECHNIQUE_PROFILES)
        ):
            raise _serialization_error("capabilities.score_input")
        return {
            "service_version": value.service_version,
            "profile_registry_version": value.profile_registry_version,
            "profiles": [_profile_wire(name, player_profiles[name]) for name in value.profiles],
            "arrangement_styles": [
                {
                    "id": style.id,
                    "label": style.label,
                    "description": style.description,
                }
                for style in ARRANGEMENT_STYLES.values()
            ],
            "technique_profiles": [
                {
                    "id": technique.id,
                    "label": technique.label,
                    "description": technique.description,
                }
                for technique in TECHNIQUE_PROFILES.values()
            ],
            "difficulty_tiers": [_difficulty_tier_wire(tier) for tier in tiers],
            "inputs": {
                "score_suffixes": list(value.input_suffixes),
                "score_input": {
                    "router_version": value.score_input_version,
                    "format_importers": registry,
                },
                "tab_json": {
                    "schema_version": ORACLE_INPUT_SCHEMA_VERSION,
                    "max_bytes": MAX_TAB_JSON_BYTES,
                },
                "target_json": {
                    "schema_version": value.target_input_schema_version,
                    "max_bytes": MAX_TARGET_JSON_BYTES,
                    "max_depth": MAX_TARGET_JSON_DEPTH,
                    "max_nodes": MAX_TARGET_JSON_NODES,
                },
            },
            "render_formats": list(value.render_formats),
            "controls": {
                "arrange": {
                    "defaults": {
                        "profile": value.default_arrange_options.profile,
                        "style": value.default_arrange_options.style,
                        "difficulty_tier": value.default_arrange_options.difficulty_tier,
                        "technique_profile": value.default_arrange_options.technique_profile,
                        "n": value.default_arrange_options.n,
                        "max_iters": value.default_arrange_options.max_iters,
                        "use_critic": value.default_arrange_options.use_critic,
                        "tempo_bpm": value.default_arrange_options.tempo_bpm,
                    },
                    "n": {"min": 1, "max": MAX_AGENT_CANDIDATES},
                    "max_iters": {"min": 0, "max": MAX_AGENT_REPAIR_ITERS},
                },
                "check": {
                    "defaults": {
                        "profile": value.default_check_options.profile,
                        "tempo_bpm": value.default_check_options.tempo_bpm,
                        "beats_per_bar": value.default_check_options.beats_per_bar,
                    },
                    "tempo_bpm": {"min": MIN_TEMPO_BPM, "max": MAX_TEMPO_BPM},
                    "beats_per_bar": {"min": 1, "max": MAX_BEATS_PER_BAR},
                },
                "difficulty": {
                    "defaults": {
                        "tier": value.default_difficulty_options.tier,
                        "tempo_bpm": value.default_difficulty_options.tempo_bpm,
                        "beats_per_bar": value.default_difficulty_options.beats_per_bar,
                    },
                    "tier": {"values": list(value.difficulty_tiers)},
                    "tempo_bpm": {"min": MIN_TEMPO_BPM, "max": MAX_TEMPO_BPM},
                    "beats_per_bar": {"min": 1, "max": MAX_BEATS_PER_BAR},
                },
                "solve": {
                    "defaults": {
                        "profile": value.default_solve_options.profile,
                        "tuning": list(value.default_solve_options.tuning),
                        "capo": value.default_solve_options.capo,
                        "tempo_bpm": value.default_solve_options.tempo_bpm,
                        "beam": value.default_solve_options.beam,
                    },
                    "beam": {"min": 1, "max": MAX_SOLVER_BEAM},
                    "search_complete": False,
                    "max_solutions": 1,
                },
                "render": {
                    "defaults": {
                        "format": value.default_render_options.format,
                        "profile": value.default_render_options.profile,
                    }
                },
            },
            "stamps": stamps,
            "implemented": [
                "arrange_score_bytes",
                "style_control",
                "player_profiles",
                "technique_profiles",
                "section_regeneration",
                "left_hand_fingering_edit",
                "midi_input",
                "check_playability",
                "check_difficulty",
                "bounded_fingering_search",
                "render_ascii",
                "render_audio",
                "alphatab",
                "animated_fretboard",
                "live_ab",
                "render_guitar_pro_5",
                "render_guitar_pro_7",
                "render_midi",
                "render_musicxml_tab",
                "render_pdf_tab",
                "render_tab_text",
            ],
            "deferred": [
                "live_leaderboard",
            ],
        }
    except ApplicationError:
        raise
    except Exception:
        raise _serialization_error("capabilities") from None


def application_error_to_wire(error: ApplicationError) -> Wire:
    """Serialize only stable application-authored error fields."""

    if type(error) is not ApplicationError:
        raise _serialization_error("error")
    try:
        return {
            "service_version": SERVICE_VERSION,
            "code": error.code.value,
            "path": error.path,
            "detail": error.detail,
            "diagnostics": [
                {
                    "code": item.code,
                    "path": item.path,
                    "message": item.message,
                }
                for item in error.diagnostics
            ],
        }
    except Exception:
        raise _serialization_error("error") from None


__all__ = [
    "application_error_to_wire",
    "arrange_outcome_to_wire",
    "capabilities_to_wire",
    "check_outcome_to_wire",
    "difficulty_outcome_to_wire",
    "fingering_edit_outcome_to_wire",
    "render_outcome_to_wire",
    "section_regeneration_outcome_to_wire",
    "solve_outcome_to_wire",
]
