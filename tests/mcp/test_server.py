from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import cast

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult, TextResourceContents
from pydantic import AnyUrl

import fretsure.mcp.server as server_module
from fretsure.application import (
    CheckOptions,
    RenderOptions,
    SolveOptions,
    check_outcome_to_wire,
    check_tab_json,
    render_outcome_to_wire,
    render_tab_json,
    solve_outcome_to_wire,
    solve_target_json,
)
from fretsure.application.target import MAX_TARGET_JSON_BYTES
from fretsure.geometry import STANDARD_TUNING
from fretsure.mcp.server import (
    CAPABILITIES_URI,
    MCP_VERSION,
    create_server,
)
from fretsure.tab import MAX_TAB_JSON_BYTES

_TARGET_JSON = (
    '{"notes":['
    '{"onset":"0/1","duration":"1/1","pitch":60,"voice":"melody"},'
    '{"onset":"1/1","duration":"1/1","pitch":62,"voice":"melody"}'
    "]}"
)
_SOLVE_ARGUMENTS: dict[str, object] = {
    "target_json": _TARGET_JSON,
    "profile": "median",
    "tuning": list(STANDARD_TUNING),
    "capo": 0,
    "tempo_bpm": 90,
    "beam": 4,
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _structured(result: CallToolResult) -> dict[str, object]:
    assert result.isError is False
    assert type(result.structuredContent) is dict
    return cast(dict[str, object], result.structuredContent)


def _error_text(result: CallToolResult) -> str:
    assert result.isError is True
    assert result.structuredContent is None
    assert len(result.content) == 1
    content = result.content[0]
    assert content.type == "text"
    return content.text


def _tab_json(wire: dict[str, object]) -> str:
    tab = wire["tab"]
    assert type(tab) is dict
    return json.dumps(tab, ensure_ascii=True, separators=(",", ":"))


@pytest.mark.anyio
async def test_initialize_lists_only_the_three_honest_tools() -> None:
    async with create_connected_server_and_client_session(create_server()) as session:
        assert session.get_server_capabilities() is not None
        result = await session.list_tools()

    tools = {tool.name: tool for tool in result.tools}
    assert set(tools) == {
        "check_playability",
        "feasible_fingerings",
        "render_notation",
    }
    assert "render_audio" not in tools
    assert "bounded" in (tools["feasible_fingerings"].description or "").lower()
    assert "not a guarantee" in (tools["check_playability"].description or "").lower()
    assert all(tool.outputSchema is not None for tool in tools.values())
    assert all(tool.inputSchema["additionalProperties"] is False for tool in tools.values())
    assert tools["feasible_fingerings"].inputSchema["properties"]["tuning"] == {
        "items": {"type": "integer"},
        "maxItems": 6,
        "minItems": 6,
        "title": "Tuning",
        "type": "array",
    }


@pytest.mark.anyio
async def test_in_memory_tools_match_the_shared_service_exactly() -> None:
    async with create_connected_server_and_client_session(create_server()) as session:
        solve_result = await session.call_tool(
            "feasible_fingerings",
            dict(_SOLVE_ARGUMENTS),
        )
        solve_wire = _structured(solve_result)
        tab_json = _tab_json(solve_wire)
        check_wire = _structured(
            await session.call_tool(
                "check_playability",
                {
                    "tab_json": tab_json,
                    "profile": "median",
                    "tempo_bpm": 90,
                    "beats_per_bar": 4,
                },
            )
        )
        render_wire = _structured(
            await session.call_tool(
                "render_notation",
                {"tab_json": tab_json},
            )
        )

    direct_solve = solve_outcome_to_wire(
        solve_target_json(
            _TARGET_JSON,
            options=SolveOptions(
                tuning=STANDARD_TUNING,
                capo=0,
                tempo_bpm=90,
                beam=4,
            ),
        )
    )
    direct_check = check_outcome_to_wire(
        check_tab_json(tab_json, options=CheckOptions(tempo_bpm=90, beats_per_bar=4))
    )
    direct_render = render_outcome_to_wire(
        render_tab_json(tab_json, options=RenderOptions())
    )

    assert solve_wire.pop("mcp_version") == MCP_VERSION
    assert check_wire.pop("mcp_version") == MCP_VERSION
    assert render_wire.pop("mcp_version") == MCP_VERSION
    assert solve_wire == direct_solve
    assert check_wire == direct_check
    assert render_wire == direct_render


@pytest.mark.anyio
async def test_bounded_search_response_never_claims_completeness() -> None:
    async with create_connected_server_and_client_session(create_server()) as session:
        found = _structured(
            await session.call_tool("feasible_fingerings", dict(_SOLVE_ARGUMENTS))
        )
        not_found = _structured(
            await session.call_tool(
                "feasible_fingerings",
                {
                    **_SOLVE_ARGUMENTS,
                    "target_json": '{"notes":[]}',
                },
            )
        )

    assert found["status"] == "found"
    assert found["search_complete"] is False
    assert found["max_solutions"] == 1
    assert not_found["status"] == "not_found_within_budget"
    assert not_found["search_complete"] is False
    infeasible = not_found["infeasible"]
    assert type(infeasible) is dict
    assert infeasible["claim"] == "bounded_search_result_not_an_unsatisfiability_proof"


@pytest.mark.anyio
async def test_capability_resource_is_shared_versioned_and_marks_audio_deferred() -> None:
    async with create_connected_server_and_client_session(create_server()) as session:
        listed = await session.list_resources()
        assert [str(resource.uri) for resource in listed.resources] == [CAPABILITIES_URI]
        result = await session.read_resource(AnyUrl(CAPABILITIES_URI))

    assert len(result.contents) == 1
    content = result.contents[0]
    assert isinstance(content, TextResourceContents)
    assert content.mimeType == "application/json"
    wire = cast(dict[str, object], json.loads(content.text))
    assert wire["mcp_version"] == MCP_VERSION
    assert wire["service_version"] == "fretsure-service@0.2.0"
    assert wire["render_formats"] == ["ascii"]
    assert "render_audio" in cast(list[str], wire["deferred"])
    assert "render_audio" not in cast(list[str], wire["implemented"])
    mcp_wire = cast(dict[str, object], wire["mcp"])
    assert mcp_wire["default_transport"] == "stdio"
    assert mcp_wire["tools"] == [
        "check_playability",
        "feasible_fingerings",
        "render_notation",
    ]


@pytest.mark.parametrize(
    ("name", "arguments", "code"),
    [
        (
            "feasible_fingerings",
            {**_SOLVE_ARGUMENTS, "beam": "4"},
            "INVALID_OPTIONS",
        ),
        (
            "feasible_fingerings",
            {**_SOLVE_ARGUMENTS, "tuning": "[40,45,50,55,59,64]"},
            "INVALID_OPTIONS",
        ),
        (
            "feasible_fingerings",
            {
                **_SOLVE_ARGUMENTS,
                "target_json": '{"notes":[],"notes":[]}',
            },
            "DUPLICATE_KEY",
        ),
        (
            "render_notation",
            {"tab_json": "{}", "format": "html"},
            "UNSUPPORTED_RENDER_FORMAT",
        ),
        (
            "check_playability",
            {
                "tab_json": {},
                "profile": "median",
                "tempo_bpm": 90,
                "beats_per_bar": 4,
            },
            "TAB_INPUT_REJECTED",
        ),
        (
            "check_playability",
            {
                "tab_json": "{}",
                "profile": "median",
                "tempo_bpm": 90,
                "beats_per_bar": 4,
                "unknown": "secret-value",
            },
            "MCP_UNKNOWN_ARGUMENT",
        ),
        (
            "check_playability",
            {"tab_json": "{}"},
            "MCP_MISSING_ARGUMENT",
        ),
        (
            "check_playability",
            {
                "tab_json": "x" * (MAX_TAB_JSON_BYTES + 1),
                "profile": "median",
                "tempo_bpm": 90,
                "beats_per_bar": 4,
            },
            "TAB_INPUT_REJECTED",
        ),
        (
            "feasible_fingerings",
            {
                **_SOLVE_ARGUMENTS,
                "target_json": "x" * (MAX_TARGET_JSON_BYTES + 1),
            },
            "TARGET_INPUT_REJECTED",
        ),
    ],
)
@pytest.mark.anyio
async def test_invalid_inputs_are_safe_tool_errors_and_server_survives(
    name: str,
    arguments: dict[str, object],
    code: str,
) -> None:
    async with create_connected_server_and_client_session(create_server()) as session:
        invalid = await session.call_tool(name, arguments)
        text = _error_text(invalid)
        survivor = _structured(
            await session.call_tool(
                "feasible_fingerings",
                {
                    **_SOLVE_ARGUMENTS,
                    "target_json": '{"notes":[]}',
                },
            )
        )

    assert code in text
    assert "Traceback" not in text
    assert "pydantic.dev" not in text
    assert "secret-value" not in text
    assert survivor["status"] == "not_found_within_budget"


@pytest.mark.anyio
async def test_unexpected_internal_exception_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "raw traceback /private/tmp/key=secret"

    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError(secret)

    monkeypatch.setattr(server_module, "check_tab_json", fail)
    async with create_connected_server_and_client_session(create_server()) as session:
        result = await session.call_tool(
            "check_playability",
            {
                "tab_json": "{}",
                "profile": "median",
                "tempo_bpm": 90,
                "beats_per_bar": 4,
            },
        )

    text = _error_text(result)
    assert "MCP_INTERNAL_ERROR" in text
    assert secret not in text
    assert "RuntimeError" not in text


@pytest.mark.anyio
async def test_real_stdio_subprocess_handshake_three_tools_and_survival() -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "fretsure.mcp.cli"],
        cwd=str(Path.cwd()),
        env={"PYTHONUNBUFFERED": "1"},
    )
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr:
        with anyio.fail_after(20):
            async with stdio_client(parameters, errlog=stderr) as (read, write):
                async with ClientSession(read, write) as session:
                    initialized = await session.initialize()
                    assert initialized.serverInfo.name == "Fretsure"
                    assert initialized.serverInfo.version == MCP_VERSION
                    tools = await session.list_tools()
                    assert [tool.name for tool in tools.tools] == [
                        "check_playability",
                        "feasible_fingerings",
                        "render_notation",
                    ]

                    invalid = await session.call_tool(
                        "feasible_fingerings",
                        {**_SOLVE_ARGUMENTS, "beam": "4"},
                    )
                    assert "INVALID_OPTIONS" in _error_text(invalid)

                    solve_wire = _structured(
                        await session.call_tool(
                            "feasible_fingerings",
                            dict(_SOLVE_ARGUMENTS),
                        )
                    )
                    tab_json = _tab_json(solve_wire)
                    _structured(
                        await session.call_tool(
                            "check_playability",
                            {
                                "tab_json": tab_json,
                                "profile": "median",
                                "tempo_bpm": 90,
                                "beats_per_bar": 4,
                            },
                        )
                    )
                    rendered = _structured(
                        await session.call_tool(
                            "render_notation",
                            {"tab_json": tab_json, "format": "ascii"},
                        )
                    )
                    assert rendered["format"] == "ascii"

        stderr.seek(0)
        stderr_text = stderr.read()
    # The official stdio client parsed every stdout line as a protocol frame;
    # any banner/debug text would have failed initialization or a subsequent call.
    assert "Traceback" not in stderr_text
    assert "secret" not in stderr_text


def test_cli_with_closed_stdin_exits_cleanly_without_stdout() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "fretsure.mcp.cli"],
        input=b"",
        capture_output=True,
        check=False,
        timeout=10,
        cwd=Path.cwd(),
    )
    assert completed.returncode == 0
    assert completed.stdout == b""
    assert completed.stderr == b""
