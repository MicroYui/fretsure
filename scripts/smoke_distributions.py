#!/usr/bin/env python3
"""Clean-install smoke matrix for the current release wheel and optional extras."""

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
                "assert importlib.util.find_spec('mcp') is None; "
                "assert importlib.util.find_spec('music21') is None; "
                "assert importlib.util.find_spec('defusedxml') is None; "
                "assert importlib.util.find_spec('anthropic') is None"
            ),
        )
        _environment(
            root,
            "musicxml",
            wheel,
            "musicxml",
            (
                "from pathlib import Path; "
                "from fretsure.importers import IMPORTER_VERSION, ImportSuccess, "
                "import_musicxml; "
                "score = Path('tests/fixtures/producers/musescore-4.7.4.musicxml'); "
                "result = import_musicxml(score); "
                "assert isinstance(result, ImportSuccess); "
                "assert result.importer_version == IMPORTER_VERSION; "
                "assert result.ir.meta.key == "
                "'key-signature:fifths=0;mode=unprovided'"
            ),
        )
        _environment(
            root,
            "service",
            wheel,
            "service,musicxml,agent",
            (
                "from pathlib import Path; "
                "from fretsure.application import ArrangeOptions, arrange_outcome_to_wire, "
                "arrange_score_bytes; "
                "from fretsure.importers import IMPORTER_VERSION; "
                "from fretsure.llm.client import ConstantLLM; "
                "score = Path('tests/fixtures/producers/musescore-4.7.4.musicxml'); "
                "outcome = arrange_score_bytes(score.read_bytes(), filename=score.name, "
                "options=ArrangeOptions(n=1, max_iters=0, use_critic=False), "
                "llm=ConstantLLM('noop')); "
                "wire = arrange_outcome_to_wire(outcome); "
                "assert outcome.status == 'tab_produced'; "
                "assert wire['source']['importer_version'] == IMPORTER_VERSION; "
                "assert wire['stamps']['importer_version'] == IMPORTER_VERSION"
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
