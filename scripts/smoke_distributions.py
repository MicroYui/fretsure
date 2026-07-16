#!/usr/bin/env python3
"""Clean-install smoke matrix for the built Plan 6A wheel and optional extras."""

from __future__ import annotations

import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(*arguments: str, cwd: Path = ROOT) -> None:
    subprocess.run(arguments, cwd=cwd, check=True)  # noqa: S603


def _environment(root: Path, name: str, wheel: Path, extras: str, code: str) -> None:
    environment = root / name
    _run("uv", "venv", str(environment), "--python", "3.11", "--quiet")
    python = environment / "bin" / "python"
    requirement = f"{wheel}[{extras}]" if extras else str(wheel)
    _run("uv", "pip", "install", "--quiet", "--python", str(python), requirement)
    _run(str(python), "-c", code)


def main() -> int:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    wheels = list((ROOT / "dist").glob(f"fretsure_oracle-{version}-*.whl"))
    if len(wheels) != 1:
        raise SystemExit("build exactly one current wheel before running smoke tests")
    wheel = wheels[0].resolve()
    with tempfile.TemporaryDirectory(prefix="fretsure-wheel-smoke-") as temporary:
        root = Path(temporary)
        _environment(
            root,
            "core",
            wheel,
            "",
            (
                "import importlib.util, fretsure; "
                f"assert fretsure.__version__ == '{version}'; "
                "assert importlib.util.find_spec('fastapi') is None; "
                "assert importlib.util.find_spec('mcp') is None"
            ),
        )
        _environment(
            root,
            "musicxml",
            wheel,
            "musicxml",
            (
                "from pathlib import Path; "
                "from fretsure.importers import ImportSuccess, import_musicxml; "
                "assert isinstance(import_musicxml(Path('tests/fixtures/musicxml/"
                "supported_basic.musicxml')), ImportSuccess)"
            ),
        )
        _environment(
            root,
            "service",
            wheel,
            "service,musicxml,agent",
            (
                "from fretsure.api import create_app; "
                "from fretsure.llm.client import CONSTANT_LLM_MODEL_ID; "
                "assert create_app().state.fretsure_config.offline_model_id == "
                "CONSTANT_LLM_MODEL_ID"
            ),
        )
        _environment(
            root,
            "mcp",
            wheel,
            "mcp",
            (
                "from fretsure.mcp.server import MCP_VERSION, create_server; "
                "assert create_server()._mcp_server.version == MCP_VERSION"
            ),
        )
    print("Clean wheel install matrix OK (core, musicxml, service, mcp)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
