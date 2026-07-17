#!/usr/bin/env python3
"""Audit Fretsure release artifacts against runtime and source-evidence invariants."""

from __future__ import annotations

import sys
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
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


def _workspace_runtime_files() -> dict[str, Path]:
    package_root = ROOT / "src" / "fretsure"
    result: dict[str, Path] = {}
    for path in package_root.rglob("*"):
        if (
            not path.is_file()
            or "__pycache__" in path.parts
            or path.suffix == ".pyc"
            or path.name == ".DS_Store"
        ):
            continue
        result[path.relative_to(ROOT / "src").as_posix()] = path
    return result


def _audit_wheel(path: Path, *, expected_version: str) -> int:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = {info.filename for info in infos}
        runtime_infos = [
            info
            for info in infos
            if not info.is_dir() and info.filename.startswith("fretsure/")
        ]
        wheel_runtime = {info.filename: info for info in runtime_infos}
        workspace_runtime = _workspace_runtime_files()
        if set(wheel_runtime) != set(workspace_runtime):
            missing = sorted(set(workspace_runtime) - set(wheel_runtime))
            extra = sorted(set(wheel_runtime) - set(workspace_runtime))
            raise ValueError(
                f"{path.name}: runtime entry set differs; missing={missing!r}, extra={extra!r}"
            )
        for name, workspace in workspace_runtime.items():
            if archive.read(wheel_runtime[name]) != workspace.read_bytes():
                raise ValueError(
                    f"{path.name}: runtime bytes differ for {name!r}"
                )
        metadata_entries = [
            info
            for info in infos
            if not info.is_dir() and info.filename.endswith(".dist-info/METADATA")
        ]
        if len(metadata_entries) != 1:
            raise ValueError(f"{path.name}: expected exactly one wheel METADATA file")
        metadata = BytesParser().parsebytes(archive.read(metadata_entries[0]))
        if metadata["Name"] != "fretsure-oracle" or metadata["Version"] != expected_version:
            raise ValueError(f"{path.name}: wheel name/version metadata is inconsistent")
    _assert_safe(names, artifact=path.name)
    if any(part in name.split("/") for name in names for part in ("docs", "tests", "scripts")):
        raise ValueError(f"{path.name}: source-only evidence leaked into the runtime wheel")
    for suffix in (
        "fretsure/web_static/index.html",
        "fretsure/web_static/favicon.svg",
        "fretsure/web_static/examples/fretsure-etude.musicxml",
        "fretsure/web_static/licenses/OFL-1.1.txt",
        "fretsure/web_static/licenses/README.txt",
        "fretsure/web_static/licenses/THIRD_PARTY_NOTICES.txt",
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
        "/web/public/licenses/THIRD_PARTY_NOTICES.txt",
        "/src/fretsure/web_static/index.html",
        "/src/fretsure/web_static/licenses/THIRD_PARTY_NOTICES.txt",
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
        "/docs/superpowers/plans/2026-07-17-midi-input.md",
        "/docs/MIDI_ACCEPTANCE.md",
        "/docs/experiments/2026-07-17-midi-census.json",
        "/scripts/generate_midi_fixtures.py",
        "/tests/fixtures/midi/sources/melody_only.musicxml",
        "/tests/fixtures/midi/producers/provenance.json",
        "/tests/fixtures/midi/producers/musescore-4.7.4-melody_only.mid",
        "/tests/fixtures/midi/producers/music21-10.5.0-melody_only.mid",
        "/tests/fixtures/midi/producers/musescore-4.7.4-supported_basic.mid",
        "/tests/fixtures/midi/producers/music21-10.5.0-supported_basic.mid",
    ):
        _require_suffix(names, suffix, artifact=path.name)
    critical = (
        "docs/superpowers/plans/2026-07-17-midi-input.md",
        "docs/MIDI_ACCEPTANCE.md",
        "docs/experiments/2026-07-17-midi-census.json",
        "scripts/generate_midi_fixtures.py",
        "tests/fixtures/midi/sources/melody_only.musicxml",
        "tests/fixtures/midi/producers/provenance.json",
        "tests/fixtures/midi/producers/musescore-4.7.4-melody_only.mid",
        "tests/fixtures/midi/producers/music21-10.5.0-melody_only.mid",
        "tests/fixtures/midi/producers/musescore-4.7.4-supported_basic.mid",
        "tests/fixtures/midi/producers/music21-10.5.0-supported_basic.mid",
        "web/public/licenses/THIRD_PARTY_NOTICES.txt",
        "src/fretsure/web_static/licenses/THIRD_PARTY_NOTICES.txt",
    )
    with tarfile.open(path, mode="r:gz") as archive:
        for relative in critical:
            matching = [
                member
                for member in archive.getmembers()
                if member.name.endswith(f"/{relative}")
            ]
            if len(matching) != 1:
                raise ValueError(
                    f"{path.name}: expected one source entry for {relative!r}"
                )
            stream = archive.extractfile(matching[0])
            if stream is None or stream.read() != (ROOT / relative).read_bytes():
                raise ValueError(
                    f"{path.name}: source evidence bytes differ for {relative!r}"
                )
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
        wheel_count = _audit_wheel(wheels[0], expected_version=version)
        sdist_count = _audit_sdist(sdists[0])
    except (OSError, tarfile.TarError, ValueError, zipfile.BadZipFile) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Distribution contents OK (wheel={wheel_count}, sdist={sdist_count})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
