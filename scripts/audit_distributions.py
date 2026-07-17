#!/usr/bin/env python3
"""Audit Fretsure release artifacts against runtime and source-evidence invariants."""

from __future__ import annotations

import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
FORBIDDEN_PARTS = {
    ".env",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "coverage",
    "node_modules",
}


def _assert_safe(names: set[str], *, artifact: str) -> None:
    for name in names:
        parts = Path(name).parts
        if any(part in FORBIDDEN_PARTS for part in parts) or name.endswith((".log", ".pyc")):
            raise ValueError(f"{artifact}: forbidden distribution entry {name}")


def _require_suffix(names: set[str], suffix: str, *, artifact: str) -> None:
    if not any(name.endswith(suffix) for name in names):
        raise ValueError(f"{artifact}: required entry ending {suffix!r} is missing")


def _audit_wheel(path: Path) -> int:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    _assert_safe(names, artifact=path.name)
    for suffix in (
        "fretsure/web_static/index.html",
        "fretsure/web_static/favicon.svg",
        "fretsure/web_static/examples/fretsure-etude.musicxml",
        "fretsure/web_static/licenses/OFL-1.1.txt",
        "fretsure/web_static/licenses/README.txt",
    ):
        _require_suffix(names, suffix, artifact=path.name)
    for extension in (".js", ".css", ".woff2"):
        if not any(
            "fretsure/web_static/assets/" in name and name.endswith(extension)
            for name in names
        ):
            raise ValueError(f"{path.name}: built web asset {extension} is missing")
    return len(names)


def _audit_sdist(path: Path) -> int:
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
    names = {member.name for member in members}
    _assert_safe(names, artifact=path.name)
    if any(member.issym() or member.islnk() for member in members):
        raise ValueError(f"{path.name}: links are not allowed in the source distribution")
    for suffix in (
        "/web/package.json",
        "/web/package-lock.json",
        "/web/public/licenses/OFL-1.1.txt",
        "/src/fretsure/web_static/index.html",
        "/docs/WEB_API_MCP.md",
        "/docs/superpowers/plans/2026-07-16-producer-driven-musicxml-ir.md",
        "/docs/PRODUCER_MUSICXML_ACCEPTANCE.md",
        "/docs/experiments/2026-07-16-producer-musicxml-census.json",
        "/scripts/generate_producer_fixtures.py",
        "/scripts/replay_producer_census.py",
        "/tests/fixtures/producers/provenance.json",
        "/tests/fixtures/producers/musescore-4.7.4.musicxml",
        "/tests/fixtures/producers/musescore-4.7.4-roundtrip-metamorphic_long.musicxml",
        "/tests/fixtures/producers/musescore-4.7.4-roundtrip-metamorphic_tied.musicxml",
        "/tests/fixtures/producers/musescore-4.7.4-roundtrip-supported_basic.musicxml",
        "/tests/fixtures/producers/musescore-4.7.4-roundtrip-supported_harmonies.musicxml",
        "/tests/fixtures/producers/musescore-4.7.4-roundtrip-supported_minor.musicxml",
        "/tests/fixtures/producers/musescore-4.7.4-roundtrip-supported_tie_continue.musicxml",
        "/tests/fixtures/producers/musescore-4.7.4-roundtrip-supported_basic.mxl",
    ):
        _require_suffix(names, suffix, artifact=path.name)
    return len(names)


def main() -> int:
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = metadata["project"]["version"]
    wheels = sorted(DIST.glob(f"fretsure_oracle-{version}-*.whl"))
    sdists = sorted(DIST.glob(f"fretsure_oracle-{version}.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        print("expected exactly one wheel and one sdist in dist/", file=sys.stderr)
        return 1
    try:
        wheel_count = _audit_wheel(wheels[0])
        sdist_count = _audit_sdist(sdists[0])
    except (OSError, tarfile.TarError, ValueError, zipfile.BadZipFile) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Distribution contents OK (wheel={wheel_count}, sdist={sdist_count})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
