"""Stable MCP interoperability adapter over the Fretsure application service."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any, NoReturn, cast

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ContentBlock, ToolAnnotations
from mcp.types import Tool as MCPTool
from pydantic import PlainValidator, WithJsonSchema

from fretsure.application import (
    ApplicationCode,
    ApplicationDiagnostic,
    ApplicationError,
    CheckOptions,
    RenderOptions,
    SolveOptions,
    application_error_to_wire,
    capabilities,
    capabilities_to_wire,
    check_outcome_to_wire,
    check_tab_json,
    render_outcome_to_wire,
    render_tab_json,
    solve_outcome_to_wire,
    solve_target_json,
)

MCP_VERSION = "fretsure-mcp@0.1.0"
CAPABILITIES_URI = "fretsure://capabilities"

_TOOL_NAMES = (
    "check_playability",
    "feasible_fingerings",
    "render_notation",
)


def _preserve_json_type(value: object) -> object:
    """Bypass FastMCP/Pydantic coercion while retaining an accurate wire schema.

    FastMCP also pre-parses JSON-looking strings whenever a field's base
    annotation is not ``str``.  Keeping ``str`` as the annotation and using a
    plain identity validator preserves exact incoming JSON types, including the
    canonical Tab/target JSON strings themselves.  The application service then
    owns all semantic validation and stable diagnostics.
    """

    return value


_RawString = Annotated[
    str,
    PlainValidator(_preserve_json_type),
    WithJsonSchema({"type": "string"}),
]
_RawInteger = Annotated[
    str,
    PlainValidator(_preserve_json_type),
    WithJsonSchema({"type": "integer"}),
]
_RawNumber = Annotated[
    str,
    PlainValidator(_preserve_json_type),
    WithJsonSchema({"type": "number"}),
]
_RawTuning = Annotated[
    str,
    PlainValidator(_preserve_json_type),
    WithJsonSchema(
        {
            "type": "array",
            "items": {"type": "integer"},
            "minItems": 6,
            "maxItems": 6,
        }
    ),
]


@dataclass(frozen=True, slots=True)
class _ArgumentContract:
    required: frozenset[str]
    optional: frozenset[str] = frozenset()


_ARGUMENT_CONTRACTS = {
    "check_playability": _ArgumentContract(
        frozenset({"tab_json", "profile", "tempo_bpm", "beats_per_bar"})
    ),
    "feasible_fingerings": _ArgumentContract(
        frozenset(
            {
                "target_json",
                "profile",
                "tuning",
                "capo",
                "tempo_bpm",
                "beam",
            }
        )
    ),
    "render_notation": _ArgumentContract(
        frozenset({"tab_json"}),
        frozenset({"format"}),
    ),
}


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _mcp_wire(value: dict[str, object]) -> dict[str, object]:
    return {"mcp_version": MCP_VERSION, **value}


def _raise_application_error(error: ApplicationError) -> NoReturn:
    raise ToolError(
        _canonical_json(
            {
                "mcp_version": MCP_VERSION,
                "error": application_error_to_wire(error),
            }
        )
    ) from None


def _raise_internal_error() -> NoReturn:
    raise ToolError(
        _canonical_json(
            {
                "mcp_version": MCP_VERSION,
                "error": {
                    "code": "MCP_INTERNAL_ERROR",
                    "detail": "the tool could not complete the request",
                },
            }
        )
    ) from None


def _input_error(path: str, detail: str) -> ApplicationError:
    return ApplicationError(
        ApplicationCode.INVALID_OPTIONS,
        path,
        detail,
        (ApplicationDiagnostic("INVALID_TYPE", path, detail),),
    )


class FretsureFastMCP(FastMCP[None]):
    """FastMCP with exact argument-key rejection before Pydantic dispatch."""

    async def list_tools(self) -> list[MCPTool]:
        tools = await super().list_tools()
        return [
            tool.model_copy(
                update={
                    "inputSchema": {
                        **tool.inputSchema,
                        "additionalProperties": False,
                    }
                }
            )
            if tool.name in _ARGUMENT_CONTRACTS
            else tool
            for tool in tools
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        contract = _ARGUMENT_CONTRACTS.get(name)
        if contract is not None:
            keys = frozenset(arguments)
            allowed = contract.required | contract.optional
            if not keys <= allowed:
                raise ToolError(
                    _canonical_json(
                        {
                            "mcp_version": MCP_VERSION,
                            "error": {
                                "code": "MCP_UNKNOWN_ARGUMENT",
                                "detail": "tool arguments contain unknown fields",
                            },
                        }
                    )
                )
            if not contract.required <= keys:
                raise ToolError(
                    _canonical_json(
                        {
                            "mcp_version": MCP_VERSION,
                            "error": {
                                "code": "MCP_MISSING_ARGUMENT",
                                "detail": "tool arguments are missing required fields",
                            },
                        }
                    )
                )
        return await super().call_tool(name, arguments)


_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def create_server() -> FretsureFastMCP:
    """Build a fresh stdio-first server with no network or model capability."""

    server = FretsureFastMCP(
        name="Fretsure",
        instructions=(
            "Deterministic, versioned guitar-playability tools. GREEN is relative "
            "to the published model/profile and is not a real-player guarantee. "
            "Fingering search is bounded and never proves unsatisfiability."
        ),
        log_level="ERROR",
    )
    # FastMCP v1.28.1 does not forward an implementation version in its public
    # constructor. Pin the owned low-level server so initialize identifies
    # Fretsure rather than the dependency release.
    server._mcp_server.version = MCP_VERSION

    @server.tool(
        name="check_playability",
        description=(
            "Check strict canonical Tab JSON with the deterministic versioned oracle. "
            "GREEN is model-relative, not a guarantee for every real player."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def check_playability_tool(
        tab_json: _RawString,
        profile: _RawString,
        tempo_bpm: _RawNumber,
        beats_per_bar: _RawInteger,
    ) -> dict[str, object]:
        try:
            outcome = check_tab_json(
                tab_json,
                options=CheckOptions(
                    profile=profile,
                    tempo_bpm=cast(float, tempo_bpm),
                    beats_per_bar=cast(int, beats_per_bar),
                ),
            )
            return _mcp_wire(check_outcome_to_wire(outcome))
        except ApplicationError as error:
            _raise_application_error(error)
        except Exception:
            _raise_internal_error()

    @server.tool(
        name="feasible_fingerings",
        description=(
            "Return at most one checked non-RED fingering from a bounded search over "
            "strict target-input@0.1.0 JSON. search_complete is always false; a "
            "not_found_within_budget result may be a false negative and is not an "
            "unsatisfiability proof."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def feasible_fingerings_tool(
        target_json: _RawString,
        profile: _RawString,
        tuning: _RawTuning,
        capo: _RawInteger,
        tempo_bpm: _RawNumber,
        beam: _RawInteger,
    ) -> dict[str, object]:
        try:
            raw_tuning = cast(object, tuning)
            if (
                type(raw_tuning) is not list
                or len(raw_tuning) != 6
                or any(type(pitch) is not int for pitch in raw_tuning)
            ):
                raise _input_error(
                    "options.tuning",
                    "tuning must be an exact six-item JSON integer array",
                )
            tuning_snapshot = cast(tuple[int, ...], tuple(raw_tuning))
            outcome = solve_target_json(
                target_json,
                options=SolveOptions(
                    profile=profile,
                    tuning=tuning_snapshot,
                    capo=cast(int, capo),
                    tempo_bpm=cast(float, tempo_bpm),
                    beam=cast(int, beam),
                ),
            )
            return _mcp_wire(solve_outcome_to_wire(outcome))
        except ApplicationError as error:
            _raise_application_error(error)
        except Exception:
            _raise_internal_error()

    @server.tool(
        name="render_notation",
        description=(
            "Render strict canonical Tab JSON as deterministic ASCII notation. "
            "Plan 6A does not provide audio rendering."
        ),
        annotations=_READ_ONLY,
        structured_output=True,
    )
    def render_notation_tool(
        tab_json: _RawString,
        format: _RawString = "ascii",
    ) -> dict[str, object]:
        try:
            outcome = render_tab_json(
                tab_json,
                options=RenderOptions(format=format),
            )
            return _mcp_wire(render_outcome_to_wire(outcome))
        except ApplicationError as error:
            _raise_application_error(error)
        except Exception:
            _raise_internal_error()

    @server.resource(
        CAPABILITIES_URI,
        name="fretsure_capabilities",
        title="Fretsure capabilities",
        description=(
            "Versioned service contracts, hard limits, implemented tools, and "
            "explicitly deferred capabilities."
        ),
        mime_type="application/json",
    )
    def capability_resource() -> str:
        try:
            value = _mcp_wire(capabilities_to_wire(capabilities()))
            value["mcp"] = {
                "default_transport": "stdio",
                "tools": list(_TOOL_NAMES),
                "capability_resource": CAPABILITIES_URI,
            }
            return _canonical_json(value)
        except Exception:
            return _canonical_json(
                {
                    "mcp_version": MCP_VERSION,
                    "error": {
                        "code": "MCP_INTERNAL_ERROR",
                        "detail": "capabilities could not be serialized",
                    },
                }
            )

    return server


mcp = create_server()


__all__ = [
    "CAPABILITIES_URI",
    "MCP_VERSION",
    "FretsureFastMCP",
    "create_server",
    "mcp",
]
