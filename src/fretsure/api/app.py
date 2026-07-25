"""FastAPI adapter for the versioned Fretsure application service."""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware.base import RequestResponseEndpoint

import fretsure
from fretsure.agent.trace import TRACE_SCHEMA_VERSION
from fretsure.api.body import parse_query, read_bounded_body, require_media_type, single_header
from fretsure.api.problems import (
    API_VERSION,
    APIProblem,
    application_problem,
    not_found_problem,
    problem_response,
)
from fretsure.application import (
    DIFFICULTY_TIER_NAMES,
    PROFILE_NAMES,
    ApplicationError,
    ArrangeOptions,
    CheckOptions,
    DifficultyOptions,
    arrange_outcome_to_wire,
    arrange_score_bytes,
    capabilities,
    capabilities_to_wire,
    check_outcome_to_wire,
    check_tab_difficulty_json,
    check_tab_json,
    difficulty_outcome_to_wire,
    edit_left_finger_json,
    fingering_edit_outcome_to_wire,
    regenerate_section_bytes,
    section_regeneration_outcome_to_wire,
)
from fretsure.application.editable_target import (
    EDITABLE_TARGET_SCHEMA_VERSION,
    editable_target_from_wire,
)
from fretsure.arrange.revision import SectionSelection
from fretsure.arrange.styles import ARRANGEMENT_STYLE_NAMES
from fretsure.audio import (
    AUDIO_EXPORT_VERSION,
    AUDIO_SAMPLE_RATE,
    AudioExportCode,
    AudioExportError,
    fluidsynth_available,
    fluidsynth_version,
    render_wav,
)
from fretsure.importers import (
    DEFAULT_LIMITS,
    SCORE_FORMAT_REGISTRY,
    SCORE_INPUT_VERSION,
    SCORE_SUFFIXES,
    ImportCode,
    ImportFailure,
    validate_score_filename,
)
from fretsure.ir import Note, VoiceRole
from fretsure.llm.client import (
    CONSTANT_LLM_MODEL_ID,
    DEFAULT_PROXY_MODEL,
    ConstantLLM,
    LLMClient,
    ProxyLLM,
    close_llm_client,
    proxy_environment_configured,
    snapshot_llm_model_id,
    validate_llm_model_id,
)
from fretsure.metrics.fidelity import FIDELITY_CHECKER_VERSION
from fretsure.oracle.input import MAX_BEATS_PER_BAR, MAX_TEMPO_BPM, MIN_TEMPO_BPM
from fretsure.render.guitar_pro import GuitarProExportError, render_guitar_pro
from fretsure.render.midi import MidiExportError, render_midi
from fretsure.render.musicxml_tab import MusicXMLTabExportError, render_musicxml_tab
from fretsure.render.pdf_tab import PdfTabExportError, render_tab_pdf
from fretsure.render.tab_text import TabTextExportError, render_tab_text
from fretsure.solver.technique import TECHNIQUE_PROFILE_NAMES
from fretsure.tab import MAX_TAB_JSON_BYTES, Tab, tab_from_json

MAX_API_CANDIDATES = 8
MAX_API_REPAIR_ITERS = 16
MAX_SECTION_REGENERATION_JSON_BYTES = 48 * 1024 * 1024
API_PROXY_REQUEST_TIMEOUT_SECONDS = 300.0

_XML_MEDIA_TYPES = frozenset(
    {
        "application/vnd.recordare.musicxml+xml",
        "application/xml",
        "text/xml",
    }
)
_MXL_MEDIA_TYPES = frozenset({"application/vnd.recordare.musicxml"})
_MIDI_MEDIA_TYPES = frozenset({"audio/midi"})
_JSON_MEDIA_TYPES = frozenset({"application/json"})
_STATIC_MUSICXML_MEDIA_TYPE = "application/vnd.recordare.musicxml+xml"
_DECIMAL = re.compile(r"0|[1-9][0-9]*\Z")
_POSITIVE_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_HASHED_ASSET_PATH = re.compile(r"/assets/[^/]+-[A-Za-z0-9_-]{8}\.[A-Za-z0-9]+\Z")
# AlphaTab 1.8.4 emits two deterministic style blocks for its SVG surface and
# bundled Bravura font. CSP hashes admit those blocks without admitting others.
_ALPHATAB_STYLE_HASHES = (
    "'sha256-EIR5s3Qp1PxPxW4Koopu9nVN+I2chNMT0ImH3VG/s+c='",
    "'sha256-2dgVYmA3nzp4Wj5m/tX4Izy5mFRftjUgM13wuNbiAak='",
)

LLMFactory = Callable[[], LLMClient]


@dataclass(frozen=True, slots=True)
class _AppConfig:
    allow_proxy: bool
    offline_factory: LLMFactory
    offline_model_id: str
    proxy_factory: LLMFactory
    proxy_model_id: str
    proxy_runtime_available: bool
    static_root: Path


class _LazyConfiguredLLM:
    """Initialize a startup-authorized engine only after score import succeeds."""

    def __init__(self, factory: LLMFactory, expected_model_id: str) -> None:
        self._factory = factory
        self._expected_model_id = expected_model_id
        self._delegate: LLMClient | None = None

    def _get(self) -> LLMClient:
        if self._delegate is None:
            delegate = self._factory()
            try:
                actual_model_id = snapshot_llm_model_id(delegate)
                if actual_model_id != self._expected_model_id:
                    raise ValueError("engine model did not match startup configuration")
            except Exception:
                close_llm_client(delegate)
                raise
            self._delegate = delegate
        return self._delegate

    @property
    def model_id(self) -> str:
        self._get()
        return self._expected_model_id

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        return self._get().complete(
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def close(self) -> None:
        if self._delegate is not None:
            close_llm_client(self._delegate)
            self._delegate = None


def _api_problem(status: int, code: str, title: str, detail: str) -> APIProblem:
    return APIProblem(status=status, code=code, title=title, detail=detail)


def _integer_control(
    query: dict[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    token = query.get(name)
    if token is None:
        return default
    if len(token) > 20:
        raise _api_problem(
            400,
            "QUERY_VALUE_OUT_OF_RANGE",
            "Query value out of range",
            f"{name} must be in {minimum}..{maximum}",
        )
    if _DECIMAL.fullmatch(token) is None:
        raise _api_problem(
            400,
            "INVALID_QUERY_VALUE",
            "Invalid query value",
            f"{name} must use canonical unsigned decimal syntax",
        )
    value = int(token)
    if not minimum <= value <= maximum:
        raise _api_problem(
            400,
            "QUERY_VALUE_OUT_OF_RANGE",
            "Query value out of range",
            f"{name} must be in {minimum}..{maximum}",
        )
    return value


def _float_control(query: dict[str, str], name: str, default: float | None) -> float | None:
    token = query.get(name)
    if token is None:
        return default
    if len(token) > 64:
        raise _api_problem(
            400,
            "QUERY_VALUE_OUT_OF_RANGE",
            "Query value out of range",
            f"{name} must be in {MIN_TEMPO_BPM:g}..{MAX_TEMPO_BPM:g}",
        )
    if _POSITIVE_DECIMAL.fullmatch(token) is None:
        raise _api_problem(
            400,
            "INVALID_QUERY_VALUE",
            "Invalid query value",
            f"{name} must use canonical non-negative decimal syntax",
        )
    value = float(token)
    if not math.isfinite(value) or not MIN_TEMPO_BPM <= value <= MAX_TEMPO_BPM:
        raise _api_problem(
            400,
            "QUERY_VALUE_OUT_OF_RANGE",
            "Query value out of range",
            f"{name} must be in {MIN_TEMPO_BPM:g}..{MAX_TEMPO_BPM:g}",
        )
    return value


def _boolean_control(query: dict[str, str], name: str, default: bool) -> bool:
    token = query.get(name)
    if token is None:
        return default
    if token == "true":
        return True
    if token == "false":
        return False
    raise _api_problem(
        400,
        "INVALID_QUERY_VALUE",
        "Invalid query value",
        f"{name} must be exactly true or false",
    )


def _score_envelope(filename: str) -> tuple[frozenset[str], int]:
    suffix = validate_score_filename(filename)
    if isinstance(suffix, ImportFailure):
        too_large = any(
            diagnostic.code is ImportCode.INPUT_LIMIT_EXCEEDED for diagnostic in suffix.diagnostics
        )
        unsupported = any(
            diagnostic.code is ImportCode.UNSUPPORTED_FILE_TYPE for diagnostic in suffix.diagnostics
        )
        raise _api_problem(
            413 if too_large else 415 if unsupported else 422,
            (
                "FILENAME_TOO_LARGE"
                if too_large
                else "UNSUPPORTED_SCORE_SUFFIX"
                if unsupported
                else "INVALID_FILENAME"
            ),
            "Score filename rejected",
            "filename must be an inert basename ending in a supported score suffix",
        )
    if suffix == ".mxl":
        return _MXL_MEDIA_TYPES, DEFAULT_LIMITS.max_mxl_archive_bytes
    if suffix in {".musicxml", ".xml"}:
        return _XML_MEDIA_TYPES, DEFAULT_LIMITS.max_bytes
    if suffix in {".mid", ".midi"}:
        return _MIDI_MEDIA_TYPES, DEFAULT_LIMITS.max_midi_bytes
    raise RuntimeError("validated score suffix was not recognized")


def _loopback_authority(value: str) -> tuple[str, int | None] | None:
    if (
        not value
        or len(value) > 255
        or any(
            character.isspace() or ord(character) < 0x20 or ord(character) == 0x7F
            for character in value
        )
    ):
        return None
    host: str
    port_token: str | None
    if value.startswith("["):
        close = value.find("]")
        if close < 0:
            return None
        host = value[1:close].lower()
        remainder = value[close + 1 :]
        if not remainder:
            port_token = None
        elif remainder.startswith(":"):
            port_token = remainder[1:]
        else:
            return None
    else:
        if value.count(":") > 1:
            return None
        host, separator, partition_port = value.partition(":")
        host = host.lower()
        port_token = partition_port if separator else None
    if host not in {"localhost", "127.0.0.1", "::1"}:
        return None
    if port_token is None:
        return host, None
    if (
        not port_token
        or not port_token.isascii()
        or not port_token.isdecimal()
        or len(port_token) > 5
    ):
        return None
    port_number = int(port_token)
    if not 1 <= port_number <= 65535 or str(port_number) != port_token:
        return None
    return host, port_number


def _validate_local_browser_envelope(request: Request) -> None:
    host_value = single_header(request, "host")
    host = None if host_value is None else _loopback_authority(host_value)
    if host is None:
        raise _api_problem(
            400,
            "UNTRUSTED_HOST",
            "Host rejected",
            "the local service accepts only a loopback host",
        )
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    origin_value = single_header(request, "origin")
    if origin_value is None:
        return
    try:
        origin = urlsplit(origin_value)
        origin_port = origin.port
    except ValueError:
        origin = None
        origin_port = None
    if (
        origin is None
        or origin.scheme not in {"http", "https"}
        or origin.username is not None
        or origin.password is not None
        or origin.path not in {"", "/"}
        or origin.query
        or origin.fragment
        or origin.hostname is None
    ):
        raise _api_problem(
            403,
            "ORIGIN_REJECTED",
            "Origin rejected",
            "unsafe requests must originate from this loopback service",
        )
    origin_authority = (origin.hostname.lower(), origin_port)
    expected_port = host[1]
    if origin_authority != (host[0], expected_port):
        raise _api_problem(
            403,
            "ORIGIN_REJECTED",
            "Origin rejected",
            "unsafe requests must originate from this loopback service",
        )


def _configured_llm(config: _AppConfig, engine: str) -> LLMClient:
    if engine == "offline":
        factory = config.offline_factory
        expected_model_id = config.offline_model_id
    elif engine == "proxy":
        if not config.allow_proxy:
            raise _api_problem(
                403,
                "PROXY_DISABLED",
                "Proxy engine disabled",
                "the server was not started with proxy access enabled",
            )
        factory = config.proxy_factory
        expected_model_id = config.proxy_model_id
    else:
        raise _api_problem(
            400,
            "UNKNOWN_ENGINE",
            "Unknown arrangement engine",
            "engine must be exactly offline or proxy",
        )
    return _LazyConfiguredLLM(factory, expected_model_id)


def _api_wire(value: dict[str, object]) -> dict[str, object]:
    return {"api_version": API_VERSION, **value}


def _capabilities_wire(config: _AppConfig) -> dict[str, object]:
    wire = _api_wire(capabilities_to_wire(capabilities()))
    wire["package_version"] = fretsure.__version__
    wire["trace_schema_version"] = TRACE_SCHEMA_VERSION
    wire["score_inputs"] = {
        "musicxml": {
            "suffixes": [".musicxml", ".xml"],
            "media_types": sorted(_XML_MEDIA_TYPES),
            "max_body_bytes": DEFAULT_LIMITS.max_bytes,
        },
        "mxl": {
            "suffixes": [".mxl"],
            "media_types": sorted(_MXL_MEDIA_TYPES),
            "max_body_bytes": DEFAULT_LIMITS.max_mxl_archive_bytes,
        },
        "midi": {
            "suffixes": [".mid", ".midi"],
            "media_types": sorted(_MIDI_MEDIA_TYPES),
            "max_body_bytes": DEFAULT_LIMITS.max_midi_bytes,
        },
    }
    wire["engines"] = [
        {
            "id": "offline",
            "available": True,
            "model_id": config.offline_model_id,
            "requires_startup_permission": False,
        },
        {
            "id": "proxy",
            "available": config.allow_proxy and config.proxy_runtime_available,
            "enabled": config.allow_proxy,
            "model_id": config.proxy_model_id,
            "requires_startup_permission": True,
        },
    ]
    controls = wire.get("controls")
    if type(controls) is dict:
        arrange = cast(dict[str, object], controls).get("arrange")
        if type(arrange) is dict:
            arrange_wire = cast(dict[str, object], arrange)
            defaults = arrange_wire.get("defaults")
            if type(defaults) is dict:
                cast(dict[str, object], defaults)["engine"] = "offline"
            arrange_wire["n"] = {"min": 1, "max": MAX_API_CANDIDATES}
            arrange_wire["max_iters"] = {"min": 0, "max": MAX_API_REPAIR_ITERS}
            arrange_wire["tempo_bpm"] = {
                "min": MIN_TEMPO_BPM,
                "max": MAX_TEMPO_BPM,
                "nullable": True,
            }
            arrange_wire["engine"] = {"default": "offline", "values": ["offline", "proxy"]}
    wire["http"] = {
        "arrangements": "/api/v1/arrangements",
        "regenerate_section": "/api/v1/arrangements/regenerate-section",
        "oracle_check": "/api/v1/oracle/check",
        "left_hand_fingering_edit": "/api/v1/fingering/left-hand",
        "difficulty_check": "/api/v1/difficulty/check",
        "midi_export": "/api/v1/exports/midi",
        "audio_export": "/api/v1/exports/audio",
        "tab_text_export": "/api/v1/exports/tab-text",
        "musicxml_tab_export": "/api/v1/exports/musicxml-tab",
        "guitar_pro_export": "/api/v1/exports/guitar-pro",
        "pdf_tab_export": "/api/v1/exports/pdf-tab",
        "health": "/healthz",
        "raw_request_bodies": True,
        "multipart_uploads": False,
    }
    wire["audio"] = {
        "available": fluidsynth_available(),
        "renderer": "FluidSynth",
        "runtime_version": fluidsynth_version(),
        "export_version": AUDIO_EXPORT_VERSION,
        "sample_rate_hz": AUDIO_SAMPLE_RATE,
        "media_type": "audio/wav",
    }
    return wire


def _json_text(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise _api_problem(
            422,
            "BODY_NOT_UTF8",
            "Request body rejected",
            "JSON request body must be valid UTF-8",
        ) from None


def _section_regeneration_request(
    data: bytes,
) -> tuple[bytes, str, tuple[Note, ...], Tab, SectionSelection, ArrangeOptions]:
    try:
        raw = json.loads(_json_text(data))
    except (json.JSONDecodeError, TypeError, ValueError):
        raise _api_problem(
            422,
            "INVALID_REVISION_DOCUMENT",
            "Revision document rejected",
            "section regeneration requires one valid JSON document",
        ) from None
    try:
        if type(raw) is not dict or set(raw) != {
            "source",
            "baseline",
            "selection",
            "options",
        }:
            raise ValueError
        source = raw["source"]
        baseline = raw["baseline"]
        selection = raw["selection"]
        options = raw["options"]
        if (
            type(source) is not dict
            or set(source) != {"filename", "base64"}
            or type(source["filename"]) is not str
            or type(source["base64"]) is not str
            or type(baseline) is not dict
            or set(baseline) != {"editable_target", "tab"}
            or type(selection) is not dict
            or set(selection) != {"start_measure", "end_measure", "locked_voices"}
            or type(selection["locked_voices"]) is not list
            or type(options) is not dict
            or set(options)
            != {
                "profile",
                "style",
                "difficulty_tier",
                "technique_profile",
                "tempo_bpm",
            }
        ):
            raise ValueError
        locked = tuple(selection["locked_voices"])
        if any(voice not in {"melody", "bass", "harmony"} for voice in locked):
            raise ValueError
        section = SectionSelection(
            selection["start_measure"],
            selection["end_measure"],
            cast(tuple[VoiceRole, ...], locked),
        )
        target = editable_target_from_wire(baseline["editable_target"])
        tab = tab_from_json(
            json.dumps(
                baseline["tab"],
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            )
        )
        encoded_source = source["base64"].encode("ascii")
        source_bytes = base64.b64decode(encoded_source, validate=True)
        filename = source["filename"]
        _media_types, source_limit = _score_envelope(filename)
        if len(source_bytes) > source_limit:
            raise ValueError
        arrange_options = ArrangeOptions(
            profile=cast(str, options["profile"]),
            style=cast(str, options["style"]),
            difficulty_tier=cast(str, options["difficulty_tier"]),
            technique_profile=cast(str, options["technique_profile"]),
            n=1,
            max_iters=0,
            use_critic=False,
            tempo_bpm=cast(float | None, options["tempo_bpm"]),
        )
    except (ValueError, TypeError, UnicodeEncodeError, binascii.Error):
        raise _api_problem(
            422,
            "INVALID_REVISION_DOCUMENT",
            "Revision document rejected",
            "section regeneration fields do not match the public revision contract",
        ) from None
    return source_bytes, filename, target, tab, section, arrange_options


def _default_static_root() -> Path:
    return Path(__file__).resolve().parents[1] / "web_static"


def _default_proxy_factory(model_id: str) -> LLMFactory:
    def build() -> LLMClient:
        return ProxyLLM(
            model=model_id,
            request_timeout_seconds=API_PROXY_REQUEST_TIMEOUT_SECONDS,
            max_attempts=1,
        )

    return build


def _default_offline_factory() -> LLMClient:
    return ConstantLLM("noop")


_TRACE_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "steps"],
    "properties": {
        "schema_version": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "trace_schema_version",
                    "seq",
                    "kind",
                    "event",
                    "candidate_index",
                    "iteration",
                    "detail",
                    "data",
                ],
                "properties": {
                    "trace_schema_version": {"type": "string"},
                    "seq": {"type": "integer", "minimum": 0},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "PLAN",
                            "PROPOSE",
                            "SOLVE",
                            "ORACLE",
                            "REASON",
                            "EDIT",
                            "RECHECK",
                            "SELECT",
                        ],
                    },
                    "event": {"type": "string"},
                    "candidate_index": {"type": ["integer", "null"], "minimum": 0},
                    "iteration": {"type": ["integer", "null"], "minimum": 0},
                    "detail": {"type": "string"},
                    "data": {"type": "object"},
                },
            },
        },
    },
}

_FIDELITY_DIMENSIONS = ["melody", "bass_root", "harmony"]
_CANONICAL_DIMENSION_ARRAYS = [
    [],
    ["melody"],
    ["bass_root"],
    ["harmony"],
    ["melody", "bass_root"],
    ["melody", "harmony"],
    ["bass_root", "harmony"],
    ["melody", "bass_root", "harmony"],
]
_DIMENSION_ARRAY_SCHEMA = {
    "type": "array",
    "items": {"type": "string", "enum": _FIDELITY_DIMENSIONS},
    "minItems": 0,
    "maxItems": 3,
    "uniqueItems": True,
    "oneOf": [{"const": value} for value in _CANONICAL_DIMENSION_ARRAYS],
}
_FAITHFULNESS_RESPONSE_SCHEMA = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": [
        "melody_f1",
        "bass_root_accuracy",
        "harmony_jaccard",
        "evaluated_dimensions",
        "unavailable_dimensions",
        "passed",
        "checker_version",
    ],
    "properties": {
        "melody_f1": {"type": ["number", "null"], "minimum": 0.0, "maximum": 1.0},
        "bass_root_accuracy": {
            "type": ["number", "null"],
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "harmony_jaccard": {
            "type": ["number", "null"],
            "minimum": 0.0,
            "maximum": 1.0,
        },
        "evaluated_dimensions": _DIMENSION_ARRAY_SCHEMA,
        "unavailable_dimensions": _DIMENSION_ARRAY_SCHEMA,
        "passed": {"type": "boolean"},
        "checker_version": {"type": "string", "const": FIDELITY_CHECKER_VERSION},
    },
}
_SOURCE_LOCATION_SCHEMA = {
    "type": ["object", "null"],
    "additionalProperties": False,
    "required": [
        "part_id",
        "measure",
        "voice",
        "element",
        "archive_member",
        "track_index",
        "event_index",
        "channel",
        "tick",
    ],
    "properties": {
        "part_id": {"type": ["string", "null"]},
        "measure": {"type": ["string", "null"]},
        "voice": {"type": ["string", "null"]},
        "element": {"type": ["string", "null"]},
        "archive_member": {"type": ["string", "null"]},
        "track_index": {"type": ["integer", "null"], "minimum": 0},
        "event_index": {"type": ["integer", "null"], "minimum": 0},
        "channel": {
            "type": ["integer", "null"],
            "minimum": 1,
            "maximum": 16,
        },
        "tick": {"type": ["integer", "null"], "minimum": 0},
    },
}
_SOURCE_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "filename",
        "format",
        "raw_sha256",
        "root_member",
        "root_sha256",
        "container_version",
        "importer_version",
        "warnings",
    ],
    "properties": {
        "filename": {"type": "string"},
        "format": {"type": "string", "enum": ["musicxml", "mxl", "midi"]},
        "raw_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "root_member": {"type": ["string", "null"]},
        "root_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "container_version": {"type": ["string", "null"]},
        "importer_version": {"type": "string"},
        "warnings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["code", "severity", "message", "location"],
                "properties": {
                    "code": {"type": "string"},
                    "severity": {"type": "string", "enum": ["error", "warning"]},
                    "message": {"type": "string"},
                    "location": _SOURCE_LOCATION_SCHEMA,
                },
            },
        },
    },
}

_ALTERNATIVE_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "candidate_index",
        "tab",
        "ascii",
        "playability",
        "faithfulness",
        "work",
        "proposal_status",
        "observed_critic",
    ],
    "properties": {
        "candidate_index": {"type": "integer", "minimum": 0},
        "tab": {"type": "object"},
        "ascii": {"type": "string"},
        "playability": {"type": "object"},
        "faithfulness": {**_FAITHFULNESS_RESPONSE_SCHEMA, "type": "object"},
        "work": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "model_calls",
                "trial_solver_calls",
                "proposed_additions",
                "accepted_additions",
            ],
            "properties": {
                "model_calls": {"type": "integer", "minimum": 0},
                "trial_solver_calls": {"type": "integer", "minimum": 0},
                "proposed_additions": {"type": "integer", "minimum": 0},
                "accepted_additions": {"type": "integer", "minimum": 0},
            },
        },
        "proposal_status": {"type": "string"},
        "observed_critic": {
            "type": "object",
            "additionalProperties": False,
            "required": ["status", "overall", "meaning"],
            "properties": {
                "status": {"type": ["string", "null"]},
                "overall": {
                    "type": ["number", "null"],
                    "minimum": 0.0,
                    "maximum": 1.0,
                },
                "meaning": {
                    "type": "string",
                    "const": "machine_observation_not_human_musicality_evidence",
                },
            },
        },
    },
}

_ARRANGE_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "api_version",
        "service_version",
        "status",
        "source",
        "score",
        "options",
        "model",
        "editable_target",
        "tab",
        "ascii",
        "playability",
        "faithfulness",
        "alternatives",
        "trace",
        "stamps",
    ],
    "properties": {
        "api_version": {"type": "string"},
        "service_version": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["tab_produced", "no_fingering_within_budget"],
        },
        "source": _SOURCE_RESPONSE_SCHEMA,
        "score": {"type": "object"},
        "options": {"type": "object"},
        "model": {
            "type": "object",
            "required": ["model_id", "engine"],
            "properties": {
                "model_id": {"type": "string"},
                "engine": {"type": "string", "enum": ["offline", "proxy"]},
            },
        },
        "editable_target": {
            "oneOf": [
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["schema_version", "notes"],
                    "properties": {
                        "schema_version": {
                            "type": "string",
                            "const": EDITABLE_TARGET_SCHEMA_VERSION,
                        },
                        "notes": {"type": "array", "items": {"type": "object"}},
                    },
                },
                {"type": "null"},
            ]
        },
        "tab": {"type": ["object", "null"]},
        "ascii": {"type": ["string", "null"]},
        "playability": {"type": ["object", "null"]},
        "faithfulness": _FAITHFULNESS_RESPONSE_SCHEMA,
        "alternatives": {
            "type": "array",
            "items": _ALTERNATIVE_RESPONSE_SCHEMA,
        },
        "trace": _TRACE_RESPONSE_SCHEMA,
        "stamps": {"type": "object", "additionalProperties": {"type": "string"}},
    },
}

_CHECK_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "api_version",
        "service_version",
        "status",
        "options",
        "tab",
        "playability",
        "stamps",
    ],
    "properties": {
        "api_version": {"type": "string"},
        "service_version": {"type": "string"},
        "status": {"type": "string", "const": "checked"},
        "options": {"type": "object"},
        "tab": {"type": "object"},
        "playability": {"type": "object"},
        "stamps": {"type": "object", "additionalProperties": {"type": "string"}},
    },
}

_DIFFICULTY_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "api_version",
        "service_version",
        "status",
        "options",
        "tab",
        "tier",
        "difficulty",
        "published_grade",
        "stamps",
    ],
    "properties": {
        "api_version": {"type": "string"},
        "service_version": {"type": "string"},
        "status": {"type": "string", "const": "checked"},
        "options": {"type": "object"},
        "tab": {"type": "object"},
        "tier": {"type": "object"},
        "difficulty": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "checker_version",
                "meets",
                "playable",
                "tier_violations",
            ],
            "properties": {
                "checker_version": {"type": "string", "const": "difficulty@0.1.0"},
                "meets": {"type": "boolean"},
                "playable": {"type": "string", "enum": ["GREEN", "AMBER", "RED"]},
                "tier_violations": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        "published_grade": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "model_version",
                "model_sha256",
                "grade_system",
                "estimated_grade",
                "likely_interval",
                "band",
                "confidence",
                "burden_percentile",
                "feature_percentiles",
                "training_scope",
                "meaning",
            ],
            "properties": {
                "model_version": {
                    "type": "string",
                    "const": "published-grade-estimator@0.1.0",
                },
                "model_sha256": {"type": "string"},
                "grade_system": {"type": "string"},
                "estimated_grade": {"type": "integer", "minimum": 1, "maximum": 10},
                "likely_interval": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["lower", "upper"],
                    "properties": {
                        "lower": {"type": "integer", "minimum": 1, "maximum": 10},
                        "upper": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                },
                "band": {
                    "type": "string",
                    "enum": ["foundational", "intermediate", "advanced"],
                },
                "confidence": {"type": "string", "const": "low"},
                "burden_percentile": {"type": "number", "minimum": 0, "maximum": 100},
                "feature_percentiles": {
                    "type": "object",
                    "additionalProperties": {"type": "number"},
                },
                "training_scope": {"type": "string"},
                "meaning": {
                    "type": "string",
                    "const": "corpus_calibrated_estimate_not_a_playability_guarantee",
                },
            },
        },
        "stamps": {"type": "object", "additionalProperties": {"type": "string"}},
    },
}


def _score_body_capability_schema(
    suffixes: list[str],
    media_types: list[str],
    max_body_bytes: int,
) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["suffixes", "media_types", "max_body_bytes"],
        "properties": {
            "suffixes": {"type": "array", "const": suffixes},
            "media_types": {"type": "array", "const": media_types},
            "max_body_bytes": {
                "type": "integer",
                "const": max_body_bytes,
                "minimum": 0,
            },
        },
    }


_FORMAT_IMPORTER_PROPERTIES = {
    format_name: {"type": "string", "const": importer}
    for format_name, importer in SCORE_FORMAT_REGISTRY.items()
}
_CAPABILITIES_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "api_version",
        "service_version",
        "package_version",
        "trace_schema_version",
        "profile_registry_version",
        "profiles",
        "arrangement_styles",
        "technique_profiles",
        "difficulty_tiers",
        "inputs",
        "score_inputs",
        "engines",
        "controls",
        "render_formats",
        "implemented",
        "deferred",
        "http",
        "audio",
        "stamps",
    ],
    "properties": {
        "api_version": {"type": "string", "const": API_VERSION},
        "service_version": {"type": "string"},
        "package_version": {"type": "string"},
        "trace_schema_version": {"type": "string", "const": TRACE_SCHEMA_VERSION},
        "profile_registry_version": {"type": "string"},
        "profiles": {"type": "array", "items": {"type": "object"}},
        "arrangement_styles": {"type": "array", "items": {"type": "object"}},
        "technique_profiles": {"type": "array", "items": {"type": "object"}},
        "difficulty_tiers": {"type": "array", "items": {"type": "object"}},
        "inputs": {
            "type": "object",
            "additionalProperties": False,
            "required": ["tab_json", "target_json", "score_suffixes", "score_input"],
            "properties": {
                "tab_json": {"type": "object"},
                "target_json": {"type": "object"},
                "score_suffixes": {"type": "array", "const": list(SCORE_SUFFIXES)},
                "score_input": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["router_version", "format_importers"],
                    "properties": {
                        "router_version": {
                            "type": "string",
                            "const": SCORE_INPUT_VERSION,
                        },
                        "format_importers": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": list(SCORE_FORMAT_REGISTRY),
                            "properties": _FORMAT_IMPORTER_PROPERTIES,
                        },
                    },
                },
            },
        },
        "score_inputs": {
            "type": "object",
            "additionalProperties": False,
            "required": ["musicxml", "mxl", "midi"],
            "properties": {
                "musicxml": _score_body_capability_schema(
                    [".musicxml", ".xml"],
                    sorted(_XML_MEDIA_TYPES),
                    DEFAULT_LIMITS.max_bytes,
                ),
                "mxl": _score_body_capability_schema(
                    [".mxl"],
                    sorted(_MXL_MEDIA_TYPES),
                    DEFAULT_LIMITS.max_mxl_archive_bytes,
                ),
                "midi": _score_body_capability_schema(
                    [".mid", ".midi"],
                    sorted(_MIDI_MEDIA_TYPES),
                    DEFAULT_LIMITS.max_midi_bytes,
                ),
            },
        },
        "engines": {"type": "array", "items": {"type": "object"}},
        "controls": {"type": "object"},
        "render_formats": {"type": "array", "items": {"type": "string"}},
        "implemented": {"type": "array", "items": {"type": "string"}},
        "deferred": {"type": "array", "items": {"type": "string"}},
        "http": {"type": "object"},
        "audio": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "available",
                "renderer",
                "runtime_version",
                "export_version",
                "sample_rate_hz",
                "media_type",
            ],
            "properties": {
                "available": {"type": "boolean"},
                "renderer": {"type": "string", "const": "FluidSynth"},
                "runtime_version": {"type": ["string", "null"]},
                "export_version": {
                    "type": "string",
                    "const": AUDIO_EXPORT_VERSION,
                },
                "sample_rate_hz": {
                    "type": "integer",
                    "const": AUDIO_SAMPLE_RATE,
                },
                "media_type": {"type": "string", "const": "audio/wav"},
            },
        },
        "stamps": {"type": "object", "additionalProperties": {"type": "string"}},
    },
}

_CAPABILITIES_OPENAPI = {
    "responses": {
        "200": {
            "description": "Versioned service capabilities",
            "content": {"application/json": {"schema": _CAPABILITIES_RESPONSE_SCHEMA}},
        }
    }
}

_ARRANGE_OPENAPI = {
    "parameters": [
        {"name": "filename", "in": "query", "required": True, "schema": {"type": "string"}},
        {
            "name": "engine",
            "in": "query",
            "schema": {"type": "string", "enum": ["offline", "proxy"], "default": "offline"},
        },
        {
            "name": "profile",
            "in": "query",
            "schema": {
                "type": "string",
                "enum": list(PROFILE_NAMES),
                "default": "median",
            },
        },
        {
            "name": "style",
            "in": "query",
            "schema": {
                "type": "string",
                "enum": list(ARRANGEMENT_STYLE_NAMES),
                "default": "fingerstyle",
            },
        },
        {
            "name": "technique_profile",
            "in": "query",
            "schema": {
                "type": "string",
                "enum": list(TECHNIQUE_PROFILE_NAMES),
                "default": "balanced",
            },
        },
        {
            "name": "difficulty_tier",
            "in": "query",
            "schema": {
                "type": "string",
                "enum": list(DIFFICULTY_TIER_NAMES),
                "default": "intermediate",
            },
        },
        {
            "name": "n",
            "in": "query",
            "schema": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_API_CANDIDATES,
                "default": 1,
            },
        },
        {
            "name": "max_iters",
            "in": "query",
            "schema": {
                "type": "integer",
                "minimum": 0,
                "maximum": MAX_API_REPAIR_ITERS,
                "default": 0,
            },
        },
        {
            "name": "use_critic",
            "in": "query",
            "schema": {"type": "boolean", "default": False},
        },
        {"name": "tempo_bpm", "in": "query", "schema": {"type": "number"}},
    ],
    "requestBody": {
        "required": True,
        "content": {
            media_type: {"schema": {"type": "string", "format": "binary"}}
            for media_type in sorted(_XML_MEDIA_TYPES | _MXL_MEDIA_TYPES | _MIDI_MEDIA_TYPES)
        },
    },
    "responses": {
        "200": {
            "description": "Versioned arrangement evidence",
            "content": {"application/json": {"schema": _ARRANGE_RESPONSE_SCHEMA}},
        }
    },
}

_SECTION_REGENERATION_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "api_version",
        "service_version",
        "status",
        "selection",
        "options",
        "model",
        "editable_target",
        "tab",
        "ascii",
        "playability",
        "faithfulness",
        "revision",
        "stamps",
    ],
    "properties": {
        "api_version": {"type": "string", "const": API_VERSION},
        "service_version": {"type": "string"},
        "status": {
            "type": "string",
            "enum": ["accepted", "preserved", "unchanged"],
        },
        "selection": {"type": "object"},
        "options": {"type": "object"},
        "model": {
            "type": "object",
            "additionalProperties": False,
            "required": ["model_id", "engine"],
            "properties": {
                "model_id": {"type": "string"},
                "engine": {"type": "string", "enum": ["offline", "proxy"]},
            },
        },
        "editable_target": {"type": "object"},
        "tab": {"type": "object"},
        "ascii": {"type": "string"},
        "playability": {"type": "object"},
        "faithfulness": {"type": "object"},
        "revision": {"type": "object"},
        "stamps": {"type": "object", "additionalProperties": {"type": "string"}},
    },
}

_SECTION_REGENERATION_OPENAPI = {
    "parameters": [
        {
            "name": "engine",
            "in": "query",
            "schema": {
                "type": "string",
                "enum": ["offline", "proxy"],
                "default": "offline",
            },
        }
    ],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {"type": "object"}}},
    },
    "responses": {
        "200": {
            "description": "Transactional measure-scoped arrangement revision",
            "content": {"application/json": {"schema": _SECTION_REGENERATION_RESPONSE_SCHEMA}},
        }
    },
}

_FINGERING_EDIT_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "api_version",
        "service_version",
        "status",
        "options",
        "tab",
        "ascii",
        "playability",
        "attempted_playability",
        "edit",
        "stamps",
    ],
    "properties": {
        "api_version": {"type": "string", "const": API_VERSION},
        "service_version": {"type": "string"},
        "status": {"type": "string", "enum": ["applied", "rejected", "unchanged"]},
        "options": {"type": "object"},
        "tab": {"type": "object"},
        "ascii": {"type": "string"},
        "playability": {"type": "object"},
        "attempted_playability": {"type": "object"},
        "edit": {"type": "object"},
        "stamps": {"type": "object", "additionalProperties": {"type": "string"}},
    },
}

_FINGERING_EDIT_OPENAPI = {
    "parameters": [
        {
            "name": "note_index",
            "in": "query",
            "required": True,
            "schema": {"type": "integer", "minimum": 0},
        },
        {
            "name": "left_finger",
            "in": "query",
            "required": True,
            "schema": {"type": "integer", "minimum": 1, "maximum": 4},
        },
        {
            "name": "profile",
            "in": "query",
            "schema": {"type": "string", "enum": list(PROFILE_NAMES), "default": "median"},
        },
        {
            "name": "tempo_bpm",
            "in": "query",
            "schema": {"type": "number", "default": 90.0},
        },
        {
            "name": "beats_per_bar",
            "in": "query",
            "schema": {"type": "integer", "minimum": 1, "maximum": MAX_BEATS_PER_BAR},
        },
    ],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {"type": "object"}}},
    },
    "responses": {
        "200": {
            "description": "Oracle-checked left-hand finger-number edit",
            "content": {"application/json": {"schema": _FINGERING_EDIT_RESPONSE_SCHEMA}},
        }
    },
}

_CHECK_OPENAPI = {
    "parameters": [
        {"name": "profile", "in": "query", "schema": {"type": "string", "default": "median"}},
        {"name": "tempo_bpm", "in": "query", "schema": {"type": "number", "default": 90.0}},
        {
            "name": "beats_per_bar",
            "in": "query",
            "schema": {"type": "integer", "minimum": 1, "maximum": MAX_BEATS_PER_BAR},
        },
    ],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {"type": "object"}}},
    },
    "responses": {
        "200": {
            "description": "Versioned playability evidence",
            "content": {"application/json": {"schema": _CHECK_RESPONSE_SCHEMA}},
        }
    },
}

_DIFFICULTY_CHECK_OPENAPI = {
    "parameters": [
        {
            "name": "tier",
            "in": "query",
            "schema": {
                "type": "string",
                "enum": list(DIFFICULTY_TIER_NAMES),
                "default": "beginner",
            },
        },
        {
            "name": "tempo_bpm",
            "in": "query",
            "schema": {"type": "number", "default": 90.0},
        },
        {
            "name": "beats_per_bar",
            "in": "query",
            "schema": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_BEATS_PER_BAR,
                "default": 4,
            },
        },
    ],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {"type": "object"}}},
    },
    "responses": {
        "200": {
            "description": "Versioned difficulty-tier evidence",
            "content": {"application/json": {"schema": _DIFFICULTY_RESPONSE_SCHEMA}},
        }
    },
}

_MIDI_EXPORT_OPENAPI = {
    "parameters": [
        {
            "name": "tempo_bpm",
            "in": "query",
            "schema": {"type": "number", "default": 90.0},
        },
    ],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {"type": "object"}}},
    },
    "responses": {
        "200": {
            "description": "One-track General MIDI guitar performance",
            "content": {"audio/midi": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
}

_AUDIO_EXPORT_OPENAPI = {
    "parameters": [
        {
            "name": "tempo_bpm",
            "in": "query",
            "schema": {"type": "number", "default": 90.0},
        },
    ],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {"type": "object"}}},
    },
    "responses": {
        "200": {
            "description": "FluidSynth-rendered guitar performance",
            "content": {"audio/wav": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
}

_TAB_TEXT_EXPORT_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {"type": "object"}}},
    },
    "responses": {
        "200": {
            "description": (
                "Six-line guitar tablature with exact canonical timing and fingering rows"
            ),
            "content": {"text/plain": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
}

_MUSICXML_TAB_EXPORT_OPENAPI = {
    "parameters": [
        {
            "name": "tempo_bpm",
            "in": "query",
            "schema": {"type": "number", "default": 90.0},
        },
    ],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {"type": "object"}}},
    },
    "responses": {
        "200": {
            "description": "Editable MusicXML 4.0 guitar tablature",
            "content": {
                "application/vnd.recordare.musicxml+xml": {
                    "schema": {"type": "string", "format": "binary"}
                }
            },
        }
    },
}

_GUITAR_PRO_EXPORT_OPENAPI = {
    "parameters": [
        {
            "name": "tempo_bpm",
            "in": "query",
            "schema": {"type": "number", "default": 90.0},
        },
    ],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {"type": "object"}}},
    },
    "responses": {
        "200": {
            "description": "Genuine Guitar Pro 5.1 tablature",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
}

_PDF_TAB_EXPORT_OPENAPI = {
    "parameters": [
        {
            "name": "tempo_bpm",
            "in": "query",
            "schema": {"type": "number", "default": 90.0},
        },
    ],
    "requestBody": {
        "required": True,
        "content": {"application/json": {"schema": {"type": "object"}}},
    },
    "responses": {
        "200": {
            "description": "Printable A4 guitar tablature with exact fingering",
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
}


def create_app(
    *,
    allow_proxy: bool = False,
    offline_factory: LLMFactory | None = None,
    offline_model_id: str = CONSTANT_LLM_MODEL_ID,
    proxy_factory: LLMFactory | None = None,
    proxy_model_id: str = DEFAULT_PROXY_MODEL,
    static_root: Path | None = None,
) -> FastAPI:
    """Build one app with immutable startup engine permission and factories."""

    if type(allow_proxy) is not bool:
        raise ValueError("allow_proxy must be an exact bool")
    offline_model_id = validate_llm_model_id(offline_model_id)
    proxy_model_id = validate_llm_model_id(proxy_model_id)
    if offline_factory is None:
        offline_factory = _default_offline_factory
    elif not callable(offline_factory):
        raise ValueError("offline_factory must be callable")
    proxy_runtime_available = True
    if proxy_factory is None:
        proxy_runtime_available = (
            find_spec("anthropic") is not None and proxy_environment_configured()
        )
        proxy_factory = _default_proxy_factory(proxy_model_id)
    elif not callable(proxy_factory):
        raise ValueError("proxy_factory must be callable")
    if static_root is None:
        static_root = _default_static_root()
    if not isinstance(static_root, Path):
        raise ValueError("static_root must be a Path")
    static_root = Path(static_root)
    config = _AppConfig(
        allow_proxy,
        offline_factory,
        offline_model_id,
        proxy_factory,
        proxy_model_id,
        proxy_runtime_available,
        static_root,
    )

    app = FastAPI(
        title="Fretsure API",
        version=API_VERSION,
        docs_url=None,
        redoc_url=None,
    )
    app.state.fretsure_config = config

    @app.exception_handler(APIProblem)
    async def handle_api_problem(_request: Request, exc: APIProblem) -> JSONResponse:
        return problem_response(exc)

    @app.exception_handler(ApplicationError)
    async def handle_application_error(_request: Request, exc: ApplicationError) -> JSONResponse:
        return problem_response(application_problem(exc))

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(
        _request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        return problem_response(
            _api_problem(
                400,
                "REQUEST_VALIDATION_FAILED",
                "Request validation failed",
                "request envelope did not match the HTTP contract",
            )
        )

    @app.exception_handler(HTTPException)
    async def handle_http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code == 404:
            return problem_response(not_found_problem())
        if exc.status_code == 405:
            return problem_response(
                _api_problem(
                    405,
                    "METHOD_NOT_ALLOWED",
                    "Method not allowed",
                    "the HTTP method is not allowed for this resource",
                )
            )
        return problem_response(
            _api_problem(
                exc.status_code,
                "HTTP_REQUEST_REJECTED",
                "HTTP request rejected",
                "the HTTP request could not be accepted",
            )
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_request: Request, _exc: Exception) -> JSONResponse:
        return problem_response(
            _api_problem(
                500,
                "INTERNAL_ERROR",
                "Internal error",
                "the request could not be completed",
            )
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            _validate_local_browser_envelope(request)
        except APIProblem as exc:
            response: Response = problem_response(exc)
        else:
            response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        alpha_tab_style_hashes = " ".join(_ALPHATAB_STYLE_HASHES)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
            f"script-src 'self'; style-src 'self'; style-src-elem 'self' "
            f"{alpha_tab_style_hashes}; "
            "style-src-attr 'unsafe-inline'; img-src 'self' data:; connect-src 'self'"
        )
        if request.url.path.startswith("/api/") or request.url.path == "/healthz":
            response.headers["Cache-Control"] = "no-store"
        elif response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        elif response.status_code == 200 and _HASHED_ASSET_PATH.fullmatch(request.url.path):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    @app.get("/healthz", tags=["process"])
    async def healthz() -> dict[str, object]:
        return {
            "api_version": API_VERSION,
            "status": "alive",
            "scope": "process_liveness_only",
        }

    @app.get(
        "/api/v1/capabilities",
        tags=["capabilities"],
        openapi_extra=_CAPABILITIES_OPENAPI,
    )
    async def api_capabilities() -> dict[str, object]:
        return _capabilities_wire(config)

    @app.post(
        "/api/v1/arrangements",
        tags=["arrangements"],
        openapi_extra=_ARRANGE_OPENAPI,
    )
    async def arrangements(request: Request) -> JSONResponse:
        query = parse_query(
            request,
            allowed={
                "filename",
                "engine",
                "profile",
                "style",
                "difficulty_tier",
                "technique_profile",
                "n",
                "max_iters",
                "use_critic",
                "tempo_bpm",
            },
            required={"filename"},
        )
        filename = query["filename"]
        media_types, body_limit = _score_envelope(filename)
        require_media_type(request, media_types, allow_utf8_charset=media_types is _XML_MEDIA_TYPES)
        engine = query.get("engine", "offline")
        if engine == "proxy" and not config.allow_proxy:
            raise _api_problem(
                403,
                "PROXY_DISABLED",
                "Proxy engine disabled",
                "the server was not started with proxy access enabled",
            )
        if engine == "proxy" and not config.proxy_runtime_available:
            raise _api_problem(
                503,
                "PROXY_UNAVAILABLE",
                "Proxy engine unavailable",
                "the explicitly configured local proxy runtime is unavailable",
            )
        if engine not in {"offline", "proxy"}:
            raise _api_problem(
                400,
                "UNKNOWN_ENGINE",
                "Unknown arrangement engine",
                "engine must be exactly offline or proxy",
            )
        options = ArrangeOptions(
            profile=query.get("profile", "median"),
            style=query.get("style", "fingerstyle"),
            difficulty_tier=query.get("difficulty_tier", "intermediate"),
            technique_profile=query.get("technique_profile", "balanced"),
            n=_integer_control(
                query,
                "n",
                1,
                minimum=1,
                maximum=MAX_API_CANDIDATES,
            ),
            max_iters=_integer_control(
                query,
                "max_iters",
                0,
                minimum=0,
                maximum=MAX_API_REPAIR_ITERS,
            ),
            use_critic=_boolean_control(query, "use_critic", False),
            tempo_bpm=_float_control(query, "tempo_bpm", None),
        )
        data = await read_bounded_body(request, limit=body_limit)

        def execute() -> dict[str, object]:
            llm = _configured_llm(config, engine)
            try:
                outcome = arrange_score_bytes(
                    data,
                    filename=filename,
                    options=options,
                    llm=llm,
                )
                wire = _api_wire(arrange_outcome_to_wire(outcome))
                model = wire.get("model")
                if type(model) is dict:
                    cast(dict[str, object], model)["engine"] = engine
                return wire
            finally:
                close_llm_client(llm)

        return JSONResponse(await run_in_threadpool(execute))

    @app.post(
        "/api/v1/arrangements/regenerate-section",
        tags=["arrangements"],
        openapi_extra=_SECTION_REGENERATION_OPENAPI,
    )
    async def regenerate_arrangement_section(request: Request) -> JSONResponse:
        query = parse_query(request, allowed={"engine"})
        engine = query.get("engine", "offline")
        if engine == "proxy" and not config.proxy_runtime_available:
            raise _api_problem(
                503,
                "PROXY_UNAVAILABLE",
                "Proxy engine unavailable",
                "the explicitly configured local proxy runtime is unavailable",
            )
        require_media_type(request, _JSON_MEDIA_TYPES, allow_utf8_charset=True)
        data = await read_bounded_body(
            request,
            limit=MAX_SECTION_REGENERATION_JSON_BYTES,
        )
        source, filename, target, tab, selection, options = _section_regeneration_request(data)

        def execute() -> dict[str, object]:
            llm = _configured_llm(config, engine)
            try:
                outcome = regenerate_section_bytes(
                    source,
                    filename=filename,
                    baseline_target=target,
                    baseline_tab=tab,
                    selection=selection,
                    options=options,
                    llm=llm,
                )
                wire = _api_wire(section_regeneration_outcome_to_wire(outcome))
                model = wire.get("model")
                if type(model) is dict:
                    cast(dict[str, object], model)["engine"] = engine
                return wire
            finally:
                close_llm_client(llm)

        return JSONResponse(await run_in_threadpool(execute))

    @app.post(
        "/api/v1/oracle/check",
        tags=["oracle"],
        openapi_extra=_CHECK_OPENAPI,
    )
    async def oracle_check(request: Request) -> JSONResponse:
        query = parse_query(
            request,
            allowed={"profile", "tempo_bpm", "beats_per_bar"},
        )
        require_media_type(request, _JSON_MEDIA_TYPES, allow_utf8_charset=True)
        options = CheckOptions(
            profile=query.get("profile", "median"),
            tempo_bpm=cast(float, _float_control(query, "tempo_bpm", 90.0)),
            beats_per_bar=_integer_control(
                query,
                "beats_per_bar",
                4,
                minimum=1,
                maximum=MAX_BEATS_PER_BAR,
            ),
        )
        data = await read_bounded_body(request, limit=MAX_TAB_JSON_BYTES)
        tab_json = _json_text(data)

        def execute() -> dict[str, object]:
            return _api_wire(check_outcome_to_wire(check_tab_json(tab_json, options=options)))

        return JSONResponse(await run_in_threadpool(execute))

    @app.post(
        "/api/v1/fingering/left-hand",
        tags=["fingering"],
        openapi_extra=_FINGERING_EDIT_OPENAPI,
    )
    async def edit_left_hand_fingering(request: Request) -> JSONResponse:
        query = parse_query(
            request,
            allowed={
                "note_index",
                "left_finger",
                "profile",
                "tempo_bpm",
                "beats_per_bar",
            },
            required={"note_index", "left_finger"},
        )
        require_media_type(request, _JSON_MEDIA_TYPES, allow_utf8_charset=True)
        note_index = _integer_control(
            query,
            "note_index",
            0,
            minimum=0,
            maximum=20_000 - 1,
        )
        left_finger = _integer_control(
            query,
            "left_finger",
            1,
            minimum=1,
            maximum=4,
        )
        options = CheckOptions(
            profile=query.get("profile", "median"),
            tempo_bpm=cast(float, _float_control(query, "tempo_bpm", 90.0)),
            beats_per_bar=_integer_control(
                query,
                "beats_per_bar",
                4,
                minimum=1,
                maximum=MAX_BEATS_PER_BAR,
            ),
        )
        data = await read_bounded_body(request, limit=MAX_TAB_JSON_BYTES)
        tab_json = _json_text(data)

        def execute() -> dict[str, object]:
            return _api_wire(
                fingering_edit_outcome_to_wire(
                    edit_left_finger_json(
                        tab_json,
                        note_index=note_index,
                        left_finger=left_finger,
                        options=options,
                    )
                )
            )

        return JSONResponse(await run_in_threadpool(execute))

    @app.post(
        "/api/v1/difficulty/check",
        tags=["difficulty"],
        openapi_extra=_DIFFICULTY_CHECK_OPENAPI,
    )
    async def difficulty_check(request: Request) -> JSONResponse:
        query = parse_query(
            request,
            allowed={"tier", "tempo_bpm", "beats_per_bar"},
        )
        require_media_type(request, _JSON_MEDIA_TYPES, allow_utf8_charset=True)
        options = DifficultyOptions(
            tier=query.get("tier", "beginner"),
            tempo_bpm=cast(float, _float_control(query, "tempo_bpm", 90.0)),
            beats_per_bar=_integer_control(
                query,
                "beats_per_bar",
                4,
                minimum=1,
                maximum=MAX_BEATS_PER_BAR,
            ),
        )
        data = await read_bounded_body(request, limit=MAX_TAB_JSON_BYTES)
        tab_json = _json_text(data)

        def execute() -> dict[str, object]:
            outcome = check_tab_difficulty_json(tab_json, options=options)
            return _api_wire(difficulty_outcome_to_wire(outcome))

        return JSONResponse(await run_in_threadpool(execute))

    @app.post(
        "/api/v1/exports/midi",
        tags=["exports"],
        openapi_extra=_MIDI_EXPORT_OPENAPI,
    )
    async def midi_export(request: Request) -> Response:
        query = parse_query(request, allowed={"tempo_bpm"})
        require_media_type(request, _JSON_MEDIA_TYPES, allow_utf8_charset=True)
        tempo_bpm = cast(float, _float_control(query, "tempo_bpm", 90.0))
        data = await read_bounded_body(request, limit=MAX_TAB_JSON_BYTES)
        tab_json = _json_text(data)

        def execute() -> bytes:
            checked = check_tab_json(
                tab_json,
                options=CheckOptions(tempo_bpm=tempo_bpm),
            )
            try:
                return render_midi(checked.tab, tempo_bpm=tempo_bpm)
            except MidiExportError:
                raise _api_problem(
                    422,
                    "MIDI_EXPORT_REJECTED",
                    "MIDI export rejected",
                    "the validated Tab cannot be represented by the canonical MIDI profile",
                ) from None

        content = await run_in_threadpool(execute)
        return Response(
            content=content,
            media_type="audio/midi",
            headers={"Content-Disposition": 'attachment; filename="fretsure-arrangement.mid"'},
        )

    @app.post(
        "/api/v1/exports/audio",
        tags=["exports"],
        openapi_extra=_AUDIO_EXPORT_OPENAPI,
    )
    async def audio_export(request: Request) -> Response:
        query = parse_query(request, allowed={"tempo_bpm"})
        require_media_type(request, _JSON_MEDIA_TYPES, allow_utf8_charset=True)
        tempo_bpm = cast(float, _float_control(query, "tempo_bpm", 90.0))
        data = await read_bounded_body(request, limit=MAX_TAB_JSON_BYTES)
        tab_json = _json_text(data)

        def execute() -> bytes:
            checked = check_tab_json(
                tab_json,
                options=CheckOptions(tempo_bpm=tempo_bpm),
            )
            try:
                return render_wav(checked.tab, tempo_bpm=tempo_bpm)
            except AudioExportError as error:
                if error.code is AudioExportCode.SYNTHESIZER_UNAVAILABLE:
                    raise _api_problem(
                        503,
                        "AUDIO_RENDERER_UNAVAILABLE",
                        "Audio renderer unavailable",
                        "FluidSynth is not available in this service runtime",
                    ) from None
                if error.code is AudioExportCode.INVALID_TAB:
                    raise _api_problem(
                        422,
                        "AUDIO_EXPORT_REJECTED",
                        "Audio export rejected",
                        "the validated Tab cannot be represented by the audio profile",
                    ) from None
                raise _api_problem(
                    503,
                    "AUDIO_RENDER_FAILED",
                    "Audio render failed",
                    "FluidSynth could not render the canonical MIDI performance",
                ) from None

        content = await run_in_threadpool(execute)
        return Response(
            content=content,
            media_type="audio/wav",
            headers={"Content-Disposition": 'attachment; filename="fretsure-arrangement.wav"'},
        )

    @app.post(
        "/api/v1/exports/tab-text",
        tags=["exports"],
        openapi_extra=_TAB_TEXT_EXPORT_OPENAPI,
    )
    async def tab_text_export(request: Request) -> Response:
        parse_query(request, allowed=set())
        require_media_type(request, _JSON_MEDIA_TYPES, allow_utf8_charset=True)
        data = await read_bounded_body(request, limit=MAX_TAB_JSON_BYTES)
        tab_json = _json_text(data)

        def execute() -> str:
            checked = check_tab_json(tab_json, options=CheckOptions())
            try:
                return render_tab_text(checked.tab)
            except TabTextExportError:
                raise _api_problem(
                    422,
                    "TAB_TEXT_EXPORT_REJECTED",
                    "Tab text export rejected",
                    (
                        "the validated Tab cannot be represented by the canonical "
                        "guitar tablature text profile"
                    ),
                ) from None

        content = await run_in_threadpool(execute)
        return Response(
            content=content.encode("utf-8"),
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": ('attachment; filename="fretsure-guitar-tablature.txt"')
            },
        )

    @app.post(
        "/api/v1/exports/musicxml-tab",
        tags=["exports"],
        openapi_extra=_MUSICXML_TAB_EXPORT_OPENAPI,
    )
    async def musicxml_tab_export(request: Request) -> Response:
        query = parse_query(request, allowed={"tempo_bpm"})
        require_media_type(request, _JSON_MEDIA_TYPES, allow_utf8_charset=True)
        tempo_bpm = cast(float, _float_control(query, "tempo_bpm", 90.0))
        data = await read_bounded_body(request, limit=MAX_TAB_JSON_BYTES)
        tab_json = _json_text(data)

        def execute() -> bytes:
            checked = check_tab_json(
                tab_json,
                options=CheckOptions(tempo_bpm=tempo_bpm),
            )
            try:
                return render_musicxml_tab(checked.tab, tempo_bpm=tempo_bpm)
            except MusicXMLTabExportError:
                raise _api_problem(
                    422,
                    "MUSICXML_TAB_EXPORT_REJECTED",
                    "MusicXML TAB export rejected",
                    (
                        "the validated Tab cannot be represented by the "
                        "canonical MusicXML 4.0 guitar tablature profile"
                    ),
                ) from None

        content = await run_in_threadpool(execute)
        return Response(
            content=content,
            media_type="application/vnd.recordare.musicxml+xml",
            headers={
                "Content-Disposition": ('attachment; filename="fretsure-guitar-tablature.musicxml"')
            },
        )

    @app.post(
        "/api/v1/exports/guitar-pro",
        tags=["exports"],
        openapi_extra=_GUITAR_PRO_EXPORT_OPENAPI,
    )
    async def guitar_pro_export(request: Request) -> Response:
        query = parse_query(request, allowed={"tempo_bpm"})
        require_media_type(request, _JSON_MEDIA_TYPES, allow_utf8_charset=True)
        tempo_bpm = cast(float, _float_control(query, "tempo_bpm", 90.0))
        data = await read_bounded_body(request, limit=MAX_TAB_JSON_BYTES)
        tab_json = _json_text(data)

        def execute() -> bytes:
            checked = check_tab_json(
                tab_json,
                options=CheckOptions(tempo_bpm=tempo_bpm),
            )
            try:
                return render_guitar_pro(checked.tab, tempo_bpm=tempo_bpm)
            except GuitarProExportError:
                raise _api_problem(
                    422,
                    "GUITAR_PRO_EXPORT_REJECTED",
                    "Guitar Pro export rejected",
                    (
                        "the validated Tab cannot be represented losslessly by the "
                        "canonical Guitar Pro 5.1 profile"
                    ),
                ) from None

        content = await run_in_threadpool(execute)
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": 'attachment; filename="fretsure-guitar-tab.gp5"'},
        )

    @app.post(
        "/api/v1/exports/pdf-tab",
        tags=["exports"],
        openapi_extra=_PDF_TAB_EXPORT_OPENAPI,
    )
    async def pdf_tab_export(request: Request) -> Response:
        query = parse_query(request, allowed={"tempo_bpm"})
        require_media_type(request, _JSON_MEDIA_TYPES, allow_utf8_charset=True)
        tempo_bpm = cast(float, _float_control(query, "tempo_bpm", 90.0))
        data = await read_bounded_body(request, limit=MAX_TAB_JSON_BYTES)
        tab_json = _json_text(data)

        def execute() -> bytes:
            checked = check_tab_json(
                tab_json,
                options=CheckOptions(tempo_bpm=tempo_bpm),
            )
            try:
                return render_tab_pdf(checked.tab, tempo_bpm=tempo_bpm)
            except PdfTabExportError:
                raise _api_problem(
                    422,
                    "PDF_TAB_EXPORT_REJECTED",
                    "PDF TAB export rejected",
                    (
                        "the validated Tab cannot be rendered by the canonical "
                        "printable guitar tablature profile"
                    ),
                ) from None

        content = await run_in_threadpool(execute)
        return Response(
            content=content,
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="fretsure-guitar-tab.pdf"'},
        )

    async def static_or_spa(path: str) -> Response:
        first = path.split("/", 1)[0]
        if first in {"api", "mcp", "healthz", "openapi.json"} or any(
            part.startswith(".") for part in path.split("/")
        ):
            raise not_found_problem()
        try:
            root = config.static_root.resolve(strict=False)
            candidate = (root / path).resolve(strict=False)
            if not candidate.is_relative_to(root):
                raise not_found_problem()
            if candidate.is_file():
                media_type = (
                    _STATIC_MUSICXML_MEDIA_TYPE if candidate.suffix.lower() == ".musicxml" else None
                )
                return FileResponse(candidate, media_type=media_type)
            if first == "assets" or any("." in part for part in path.split("/")):
                raise not_found_problem()
            index = (root / "index.html").resolve(strict=False)
            if not index.is_relative_to(root) or not index.is_file():
                raise _api_problem(
                    404,
                    "WEB_BUILD_UNAVAILABLE",
                    "Web interface unavailable",
                    "the packaged web build is unavailable",
                )
            return FileResponse(index, media_type="text/html")
        except APIProblem:
            raise
        except OSError:
            raise not_found_problem() from None

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa_root() -> Response:
        return await static_or_spa("")

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa_fallback(path: str) -> Response:
        return await static_or_spa(path)

    return app


__all__ = [
    "API_VERSION",
    "MAX_API_CANDIDATES",
    "MAX_API_REPAIR_ITERS",
    "create_app",
]
