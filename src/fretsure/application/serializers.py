"""Deterministic wire serializers for application-service contracts."""

from __future__ import annotations

import json
import math
from fractions import Fraction
from typing import cast

import fretsure
import fretsure.agent.trace as trace_module
from fretsure.application.contracts import (
    PROFILE_REGISTRY_VERSION,
    SERVICE_VERSION,
    ApplicationCode,
    ApplicationError,
    ArrangeOptions,
    ArrangeOutcome,
    CheckOptions,
    CheckOutcome,
    RenderOptions,
    RenderOutcome,
    ServiceCapabilities,
    SolveOptions,
    SolveOutcome,
)
from fretsure.application.target import (
    MAX_TARGET_JSON_BYTES,
    MAX_TARGET_JSON_DEPTH,
    MAX_TARGET_JSON_NODES,
    TARGET_INPUT_SCHEMA_VERSION,
)
from fretsure.geometry import STANDARD_TUNING
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
from fretsure.oracle.profiles import MEDIAN_HAND, Profile, validated_profile_snapshot
from fretsure.render.ascii import render_ascii
from fretsure.solver.api import Infeasible, InfeasibleCode
from fretsure.tab import MAX_TAB_JSON_BYTES, Tab, tab_to_json

Wire = dict[str, object]

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
    return {
        "part_id": location.part_id,
        "measure": location.measure,
        "voice": location.voice,
        "element": location.element,
        "archive_member": location.archive_member,
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
    return {
        "filename": None if provenance is None else provenance.source_filename,
        "format": None if provenance is None else provenance.source_format,
        "raw_sha256": (
            imported.sha256 if provenance is None else provenance.raw_sha256
        ),
        "root_member": None if provenance is None else provenance.root_member,
        "root_sha256": imported.sha256 if provenance is None else provenance.root_sha256,
        "container_version": (
            None if provenance is None else provenance.container_version
        ),
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
    values = (gate.melody_f1, gate.bass_root, gate.harmony)
    if any(not math.isfinite(value) for value in values):
        raise _serialization_error("faithfulness")
    return {
        "melody_f1": gate.melody_f1,
        "bass_root_accuracy": gate.bass_root,
        "harmony_jaccard": gate.harmony,
        "passed": gate.passed,
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
    if wire.get("schema_version") != _trace_schema_version() or type(
        wire.get("steps")
    ) is not list:
        raise _serialization_error("trace")
    return wire


def _trace_schema_version() -> str:
    value = getattr(trace_module, "TRACE_SCHEMA_VERSION", "agent-trace@0.1.0")
    if type(value) is not str:
        raise _serialization_error("trace.schema_version")
    return value


def _base_stamps(profile: Profile) -> Wire:
    snapshot = validated_profile_snapshot(profile)
    return {
        "package_version": fretsure.__version__,
        "service_version": SERVICE_VERSION,
        "profile_registry_version": PROFILE_REGISTRY_VERSION,
        "profile_version": snapshot.version,
        "profile_fingerprint": snapshot.fingerprint,
        "oracle_checker_version": CHECKER_VERSION,
        "oracle_input_schema_version": ORACLE_INPUT_SCHEMA_VERSION,
        "fidelity_checker_version": FIDELITY_CHECKER_VERSION,
        "target_input_schema_version": TARGET_INPUT_SCHEMA_VERSION,
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


def arrange_outcome_to_wire(outcome: ArrangeOutcome) -> Wire:
    """Serialize a full arrangement with independent product gates."""

    if type(outcome) is not ArrangeOutcome:
        raise _serialization_error("outcome")
    try:
        stamps = _base_stamps(outcome.profile)
        stamps.update(
            {
                "importer_version": outcome.imported.importer_version,
                "model_id": outcome.model_id,
            }
        )
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
            "tab": _tab_wire(outcome.tab),
            "ascii": outcome.ascii,
            "playability": _playability_wire(outcome.oracle),
            "faithfulness": _faithfulness_wire(outcome.faithfulness),
            "trace": _trace_wire(outcome.trace_document_json),
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
        return {
            "service_version": value.service_version,
            "profile_registry_version": value.profile_registry_version,
            "profiles": [_profile_wire("median", profile)],
            "inputs": {
                "score_suffixes": list(value.input_suffixes),
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
            "stamps": _base_stamps(profile),
            "implemented": [
                "arrange_score_bytes",
                "check_playability",
                "bounded_fingering_search",
                "render_ascii",
            ],
            "deferred": [
                "render_audio",
                "midi_input",
                "alphatab",
                "animated_fretboard",
                "live_ab",
                "live_leaderboard",
                "export_interoperability",
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
    "render_outcome_to_wire",
    "solve_outcome_to_wire",
]
