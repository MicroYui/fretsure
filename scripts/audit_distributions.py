#!/usr/bin/env python3
"""Audit release artifacts against runtime, evidence, and privacy invariants."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tarfile
import tomllib
import zipfile
from email.message import Message
from email.parser import BytesParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PACKAGE_INIT = ROOT / "src" / "fretsure" / "__init__.py"
SOURCE_CENSUS = ROOT / "data" / "benchmark" / "source-census.json"
SOURCE_CACHE = ROOT / "data" / "benchmark" / "sources"
PREREGISTRATION = ROOT / "docs" / "experiments" / "2026-07-17-benchmark-v2-prereg.json"
BUDGET = ROOT / "docs" / "experiments" / "2026-07-17-benchmark-v2-budget.md"

BENCHMARK_REQUIREMENTS = (
    "anthropic>=0.40",
    "httpx>=0.28,<0.29",
    "defusedxml>=0.7.1,<1",
    "music21==10.5.0",
)
BENCHMARK_WHEEL_REQUIREMENTS = frozenset(
    {
        "anthropic>=0.40; extra == 'benchmark'",
        "defusedxml<1,>=0.7.1; extra == 'benchmark'",
        "httpx<0.29,>=0.28; extra == 'benchmark'",
        "music21==10.5.0; extra == 'benchmark'",
    }
)
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
FORBIDDEN_RELEASE_FILENAMES = {
    "blobs.jsonl",
    "journal.jsonl",
    "private-observations.json",
    "rows.jsonl",
    "wal.jsonl",
}

SDIST_REQUIRED_FILES = (
    "web/package.json",
    "web/package-lock.json",
    "web/public/licenses/OFL-1.1.txt",
    "web/public/licenses/THIRD_PARTY_NOTICES.txt",
    "src/fretsure/web_static/index.html",
    "src/fretsure/web_static/licenses/THIRD_PARTY_NOTICES.txt",
    "docs/WEB_API_MCP.md",
    "docs/superpowers/plans/2026-07-16-producer-driven-musicxml-ir.md",
    "docs/PRODUCER_MUSICXML_ACCEPTANCE.md",
    "docs/experiments/2026-07-16-producer-musicxml-census.json",
    "scripts/generate_producer_fixtures.py",
    "scripts/replay_producer_census.py",
    "tests/fixtures/producers/provenance.json",
    "tests/fixtures/producers/musescore-4.7.4.musicxml",
    "tests/fixtures/producers/musescore-4.7.4-roundtrip-metamorphic_long.musicxml",
    "tests/fixtures/producers/musescore-4.7.4-roundtrip-metamorphic_tied.musicxml",
    "tests/fixtures/producers/musescore-4.7.4-roundtrip-supported_basic.musicxml",
    "tests/fixtures/producers/musescore-4.7.4-roundtrip-supported_harmonies.musicxml",
    "tests/fixtures/producers/musescore-4.7.4-roundtrip-supported_minor.musicxml",
    "tests/fixtures/producers/musescore-4.7.4-roundtrip-supported_tie_continue.musicxml",
    "tests/fixtures/producers/musescore-4.7.4-roundtrip-supported_basic.mxl",
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
)
SDIST_EXACT_FILES = (
    "pyproject.toml",
    "uv.lock",
    "src/fretsure/__init__.py",
    "data/benchmark/source-census.json",
    "docs/experiments/2026-07-17-benchmark-v2-prereg.json",
    "docs/experiments/2026-07-17-benchmark-v2-budget.md",
    "docs/experiments/2026-07-18-benchmark-v2-pilot-spec.json",
    "docs/experiments/2026-07-18-gpt-5.6-sol-pricing-source.json",
    "docs/experiments/2026-07-18-gpt-5.6-sol-pricing-contract.json",
    "docs/experiments/2026-07-18-gpt-5.6-sol-pricing-source-v2.json",
    "docs/experiments/2026-07-18-gpt-5.6-sol-pricing-contract-v2.json",
    "docs/experiments/2026-07-18-gpt-5.6-sol-formal-billing-envelope.json",
    "docs/BENCHMARK_V2_TASK8_READINESS.md",
    "scripts/build_benchmark_corpus.py",
    "scripts/build_benchmark_precall.py",
    "scripts/task8_budget_gate.py",
    "scripts/task8_pilot.py",
    "scripts/audit_distributions.py",
    "scripts/smoke_distributions.py",
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


def _package_version() -> str:
    text = PACKAGE_INIT.read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$', text, re.MULTILINE)
    if match is None:
        raise ValueError("src/fretsure/__init__.py has no canonical package version")
    return match.group(1)


def _validate_project_metadata(metadata: object) -> str:
    if type(metadata) is not dict:
        raise ValueError("pyproject metadata must be an object")
    try:
        project = metadata["project"]
        optional = project["optional-dependencies"]
        version = project["version"]
        benchmark = optional["benchmark"]
    except (KeyError, TypeError):
        raise ValueError("pyproject project metadata is incomplete") from None
    if type(version) is not str or version != _package_version():
        raise ValueError("pyproject and fretsure.__version__ are inconsistent")
    if benchmark != list(BENCHMARK_REQUIREMENTS):
        raise ValueError("pyproject benchmark extra is not the frozen four-dependency set")
    return version


def _licensed_source_files() -> dict[str, Path]:
    try:
        census = json.loads(SOURCE_CENSUS.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("benchmark source census is unreadable") from error
    if type(census) is not dict or type(census.get("sources")) is not list:
        raise ValueError("benchmark source census has no source list")
    result: dict[str, Path] = {}
    for index, record in enumerate(census["sources"]):
        if type(record) is not dict or type(record.get("status")) is not str:
            raise ValueError(f"benchmark source census row {index} is invalid")
        if record["status"] != "included":
            continue
        cache_name = record.get("cache_name")
        expected_sha256 = record.get("expected_sha256")
        license_value = record.get("license")
        if (
            type(cache_name) is not str
            or not cache_name
            or Path(cache_name).name != cache_name
            or type(expected_sha256) is not str
            or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
            or type(license_value) is not dict
            or license_value.get("redistribution") is not True
        ):
            raise ValueError(f"included benchmark source row {index} lacks release permission")
        path = SOURCE_CACHE / cache_name
        try:
            data = path.read_bytes()
        except OSError as error:
            raise ValueError(f"licensed benchmark source {cache_name!r} is unreadable") from error
        if hashlib.sha256(data).hexdigest() != expected_sha256:
            raise ValueError(f"licensed benchmark source {cache_name!r} has the wrong bytes")
        if cache_name in result:
            raise ValueError("benchmark source census repeats a cache filename")
        result[cache_name] = path
    if len(result) != 3:
        raise ValueError("release requires exactly three licensed benchmark source files")
    try:
        actual_entries = tuple(SOURCE_CACHE.iterdir())
    except OSError as error:
        raise ValueError("benchmark source cache is unreadable") from error
    if any(not path.is_file() for path in actual_entries) or {
        path.name for path in actual_entries
    } != set(result):
        raise ValueError("benchmark source cache differs from the licensed census set")
    return dict(sorted(result.items()))


def _wheel_evidence_files() -> dict[str, Path]:
    result = {
        "fretsure/bench/data/source-census.json": SOURCE_CENSUS,
        "fretsure/bench/data/benchmark-v2-prereg.json": PREREGISTRATION,
    }
    for name, path in _licensed_source_files().items():
        result[f"fretsure/bench/data/sources/{name}"] = path
    for name, path in result.items():
        if not path.is_file():
            raise ValueError(f"wheel evidence source for {name!r} is missing")
    return result


def _assert_safe(names: set[str], *, artifact: str) -> None:
    for name in names:
        parts = Path(name).parts
        if (
            any(part in FORBIDDEN_PARTS for part in parts)
            or Path(name).name in FORBIDDEN_RELEASE_FILENAMES
            or "/benchmarks/results/" in f"/{name.strip('/')}/"
            or "/outputs/" in f"/{name.strip('/')}/"
            or name.endswith((".log", ".pyc"))
        ):
            raise ValueError(f"{artifact}: forbidden distribution entry {name}")


def _require_suffix(names: set[str], suffix: str, *, artifact: str) -> None:
    if not any(name.endswith(f"/{suffix}") or name == suffix for name in names):
        raise ValueError(f"{artifact}: required entry {suffix!r} is missing")


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
    for name, path in _wheel_evidence_files().items():
        if name in result:
            raise ValueError(f"wheel evidence destination {name!r} collides with package source")
        result[name] = path
    return result


def _audit_benchmark_metadata(metadata: Message, *, artifact: str) -> None:
    extras = metadata.get_all("Provides-Extra", [])
    if extras.count("benchmark") != 1:
        raise ValueError(f"{artifact}: wheel must provide exactly one benchmark extra")
    requirements = {
        requirement
        for requirement in metadata.get_all("Requires-Dist", [])
        if requirement.endswith("; extra == 'benchmark'")
    }
    if requirements != BENCHMARK_WHEEL_REQUIREMENTS:
        raise ValueError(f"{artifact}: benchmark extra metadata is inconsistent")


def _audit_wheel(path: Path, *, expected_version: str) -> int:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = {info.filename for info in infos}
        _assert_safe(names, artifact=path.name)
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
                raise ValueError(f"{path.name}: runtime bytes differ for {name!r}")
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
        _audit_benchmark_metadata(metadata, artifact=path.name)
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


def _matching_members(
    members: list[tarfile.TarInfo], relative: str
) -> list[tarfile.TarInfo]:
    return [
        member
        for member in members
        if member.name == relative or member.name.endswith(f"/{relative}")
    ]


def _audit_sdist(path: Path) -> int:
    licensed = _licensed_source_files()
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        names = {member.name for member in members}
        _assert_safe(names, artifact=path.name)
        if any(member.issym() or member.islnk() for member in members):
            raise ValueError(f"{path.name}: links are not allowed in the source distribution")
        for relative in SDIST_REQUIRED_FILES:
            _require_suffix(names, relative, artifact=path.name)
        exact_files = (*SDIST_EXACT_FILES, *(f"data/benchmark/sources/{name}" for name in licensed))
        for relative in exact_files:
            matching = _matching_members(members, relative)
            if len(matching) != 1:
                raise ValueError(f"{path.name}: expected one source entry for {relative!r}")
            stream = archive.extractfile(matching[0])
            if stream is None or stream.read() != (ROOT / relative).read_bytes():
                raise ValueError(f"{path.name}: source evidence bytes differ for {relative!r}")
        marker = "/data/benchmark/sources/"
        archived_sources = {
            member.name.rsplit("/", 1)[-1]
            for member in members
            if member.isfile() and marker in f"/{member.name}"
        }
        if archived_sources != set(licensed):
            raise ValueError(f"{path.name}: packaged benchmark sources differ from licensed census")
    return len(names)


def main() -> int:
    try:
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        version = _validate_project_metadata(metadata)
        wheels = sorted(DIST.glob(f"fretsure_oracle-{version}-*.whl"))
        sdists = sorted(DIST.glob(f"fretsure_oracle-{version}.tar.gz"))
        if len(wheels) != 1 or len(sdists) != 1:
            raise ValueError("expected exactly one wheel and one sdist in dist/")
        wheel_count = _audit_wheel(wheels[0], expected_version=version)
        sdist_count = _audit_sdist(sdists[0])
    except (OSError, tarfile.TarError, ValueError, zipfile.BadZipFile) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Distribution contents OK (wheel={wheel_count}, sdist={sdist_count})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
