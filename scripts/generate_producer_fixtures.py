"""Generate the immutable real-producer MusicXML/MXL compatibility corpus.

Run with the exact exporter versions recorded below and a fresh, untracked
destination::

    uv run --isolated --with defusedxml==0.7.1 \
      --with music21==10.5.0 \
      python scripts/generate_producer_fixtures.py \
      --output-dir /tmp/fretsure-producer-exports

The three pre-existing producer fixtures are immutable inputs and are copied
byte-for-byte into the fresh output after their hashes and current importer
semantics are revalidated.  Only the six MusicXML round-trips and one MXL are
re-exported.  In particular, do not regenerate or replace the dated music21
fixture: the legacy MuseScore fixture is bound to those exact source bytes.

The script refuses to write into the checked-in corpus or overwrite any
destination.  Freezing a newly reviewed round-trip export is a deliberate
maintainer action followed by review of every manifest/hash change; the fresh
directory must never be copied over the corpus without that review.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from fractions import Fraction
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any

from fretsure.importers import ImportSuccess, import_musicxml

ROOT = Path(__file__).resolve().parents[1]
FROZEN_OUTPUT = ROOT / "tests" / "fixtures" / "producers"
MUSICXML_SOURCES = ROOT / "tests" / "fixtures" / "musicxml"
MUSIC21_VERSION = "10.5.0"
MUSESCORE_VERSION = "4.7.4"
MUSESCORE_IDENTITY = f"MuseScore4 {MUSESCORE_VERSION}"
MUSESCORE_MARKER = f"MuseScore Studio {MUSESCORE_VERSION}".encode()
MUSESCORE_PRODUCER_LICENSE = "GPL-3.0-only (external exporter only)"
ROUNDTRIP_SOURCE_NAMES = (
    "metamorphic_long.musicxml",
    "metamorphic_tied.musicxml",
    "supported_basic.musicxml",
    "supported_harmonies.musicxml",
    "supported_minor.musicxml",
    "supported_tie_continue.musicxml",
)
KEY_MODE_WARNING = "KEY_MODE_UNPROVIDED"
MXL_MEDIA_TYPE_WARNING = "MXL_ROOTFILE_MEDIA_TYPE_UNPROVIDED"
LEGACY_ARTIFACT_NAMES = (
    "music21-10.5.0.musicxml",
    "musicxml-1.6.1.musicxml",
    "musescore-4.7.4.musicxml",
)


def _require_version(package: str, expected: str) -> None:
    actual = importlib.metadata.version(package)
    if actual != expected:
        raise RuntimeError(f"{package} fixture requires {expected}, found {actual}")


def _require_musescore_version(executable: str) -> None:
    completed = subprocess.run(
        [executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
    )
    observed = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    if completed.returncode != 0 or observed != MUSESCORE_IDENTITY:
        raise RuntimeError(
            "MuseScore fixture requires exact identity "
            f"{MUSESCORE_IDENTITY!r}, found returncode={completed.returncode}, "
            f"output={observed!r}"
        )


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _path_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _split_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _read_mxl_root(raw: bytes) -> tuple[str, bytes]:
    if not zipfile.is_zipfile(BytesIO(raw)):
        raise RuntimeError("MuseScore MXL output is not a complete ZIP archive")
    try:
        with zipfile.ZipFile(BytesIO(raw)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise RuntimeError("MuseScore MXL output contains duplicate members")
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            rootfiles = [
                element
                for element in container.iter()
                if _split_tag(element.tag) == "rootfile"
            ]
            if len(rootfiles) != 1:
                raise RuntimeError("MuseScore MXL output must declare exactly one rootfile")
            root_member = rootfiles[0].get("full-path")
            if not root_member:
                raise RuntimeError("MuseScore MXL rootfile has no full-path")
            member_path = PurePosixPath(root_member)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise RuntimeError("MuseScore MXL rootfile path is unsafe")
            root = archive.read(root_member)
    except (ET.ParseError, KeyError, OSError, zipfile.BadZipFile) as exc:
        raise RuntimeError("MuseScore MXL output is incomplete or malformed") from exc
    return root_member, root


def _validate_musescore_output(path: Path) -> tuple[str | None, bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimeError("MuseScore export produced no readable output") from exc
    if path.suffix.lower() == ".mxl":
        root_member, root = _read_mxl_root(raw)
    else:
        root_member, root = None, raw
    try:
        ET.fromstring(root)
    except ET.ParseError as exc:
        raise RuntimeError("MuseScore export did not produce complete MusicXML") from exc
    if MUSESCORE_MARKER not in root:
        raise RuntimeError("MuseScore output lacks the exact exporter identity")
    return root_member, root


def _shell_exit_code(returncode: int) -> int:
    """Normalize a signal return to the status convention visible in a shell."""

    return 128 - returncode if returncode < 0 else returncode


def _run_musescore(executable: str, source: Path, output: Path) -> tuple[int, str]:
    if output.exists():
        raise RuntimeError(f"refusing to overwrite output artifact: {output}")
    source_sha256 = _path_sha256(source)
    completed = subprocess.run(
        [executable, "-o", str(output), str(source)],
        check=False,
        capture_output=True,
    )
    try:
        _validate_musescore_output(output)
    except RuntimeError as exc:
        stderr = completed.stderr.decode("utf-8", errors="replace")[-2_000:]
        raise RuntimeError(
            f"MuseScore export failed with returncode={completed.returncode}; "
            f"stderr_tail={stderr!r}"
        ) from exc
    if _path_sha256(source) != source_sha256:
        raise RuntimeError(f"MuseScore source changed while it was exported: {source}")
    # MuseScore 4.7.4 on this arm64 host can abort during GUI-runtime teardown
    # after completing a valid export.  Acceptance above is intentionally
    # strict: complete XML/ZIP plus the exact exporter marker are mandatory,
    # and the normalized non-zero exit remains bound into provenance.
    return _shell_exit_code(completed.returncode), source_sha256


def _fraction(value: Fraction | None) -> list[int] | None:
    if value is None:
        return None
    return [value.numerator, value.denominator]


def _semantic(result: ImportSuccess) -> dict[str, object]:
    ir = result.ir
    return {
        "chords": [
            {
                "onset": _fraction(chord.onset),
                "pitch_classes": sorted(chord.pitch_classes),
                "root_pc": chord.root_pc,
                "symbol": chord.symbol,
            }
            for chord in ir.chords
        ],
        "duration_beats": _fraction(ir.meta.duration_beats),
        "key": ir.meta.key,
        "license": ir.meta.license,
        "notes": [
            {
                "duration": _fraction(event.duration),
                "onset": _fraction(event.onset),
                "pitch": event.pitch,
                "voice": event.voice,
            }
            for event in ir.notes
        ],
        "tempo_bpm": ir.meta.tempo_bpm,
        "time_sig": list(ir.meta.time_sig),
        "title": ir.meta.title,
        "warning_codes": [warning.code.value for warning in result.warnings],
    }


def _semantic_sha256(value: dict[str, object]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _sha256(canonical)


def _fixture_row(
    output: Path,
    *,
    producer: str,
    producer_class: str,
    version: str,
    producer_license: str,
    score_license: str,
    export_exit_code: int,
    expected_warnings: tuple[str, ...],
    source: Path | None = None,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    if (source is None) != (source_sha256 is None):
        raise RuntimeError("source path and source hash must be provided together")
    if source is not None and _path_sha256(source) != source_sha256:
        raise RuntimeError(f"source changed before manifest binding: {source}")
    raw = output.read_bytes()
    if output.suffix.lower() == ".mxl":
        format_name = "mxl"
        root_member, root = _read_mxl_root(raw)
    else:
        format_name = "musicxml"
        root_member, root = None, raw

    result = import_musicxml(output)
    if not isinstance(result, ImportSuccess):
        raise RuntimeError(
            f"frozen producer artifact must import successfully: {output.name}: "
            f"{result.diagnostics!r}"
        )
    warning_codes = tuple(warning.code.value for warning in result.warnings)
    if warning_codes != expected_warnings:
        raise RuntimeError(
            f"unexpected importer warnings for {output.name}: "
            f"expected {expected_warnings!r}, found {warning_codes!r}"
        )
    if result.ir.meta.license != score_license:
        raise RuntimeError(
            f"score license changed for {output.name}: {result.ir.meta.license!r}"
        )
    output_sha256 = _sha256(raw)
    root_sha256 = _sha256(root)
    if result.provenance is None:
        raise RuntimeError(f"import lacks structured provenance: {output.name}")
    if (
        result.provenance.source_format != format_name
        or result.provenance.raw_sha256 != output_sha256
        or result.provenance.root_member != root_member
        or result.provenance.root_sha256 != root_sha256
    ):
        raise RuntimeError(f"import provenance mismatch: {output.name}")

    semantic = _semantic(result)
    return {
        "expected": "success",
        "expected_warnings": list(expected_warnings),
        "export_exit_code": export_exit_code,
        "format": format_name,
        "output_file": output.name,
        "output_sha256": output_sha256,
        "producer": producer,
        "producer_class": producer_class,
        "producer_license": producer_license,
        "root_member": root_member,
        "root_sha256": root_sha256,
        "score_license": score_license,
        "semantic": semantic,
        "semantic_sha256": _semantic_sha256(semantic),
        "source_file": None if source is None else source.relative_to(ROOT).as_posix(),
        "source_sha256": source_sha256,
        "version": version,
    }


def _copy_and_validate_legacy_rows(output: Path) -> list[dict[str, Any]]:
    """Copy immutable legacy evidence without pretending to regenerate it."""

    try:
        manifest = json.loads(
            (FROZEN_OUTPUT / "provenance.json").read_text(encoding="utf-8")
        )
        frozen_rows = manifest["fixtures"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError("cannot read the frozen producer manifest") from exc
    if manifest.get("schema") != "fretsure-producer-fixtures@0.3.0":
        raise RuntimeError("unexpected frozen producer-manifest schema")
    if not isinstance(frozen_rows, list):
        raise RuntimeError("frozen producer manifest fixtures must be a list")
    by_name = {
        row.get("output_file"): row
        for row in frozen_rows
        if isinstance(row, dict) and isinstance(row.get("output_file"), str)
    }
    named_rows = [
        row
        for row in frozen_rows
        if isinstance(row, dict) and isinstance(row.get("output_file"), str)
    ]
    if len(by_name) != len(named_rows):
        raise RuntimeError("frozen producer manifest contains duplicate output names")
    if any(name not in by_name for name in LEGACY_ARTIFACT_NAMES):
        raise RuntimeError("frozen producer manifest lacks immutable legacy rows")

    copied: list[dict[str, Any]] = []
    for name in LEGACY_ARTIFACT_NAMES:
        frozen_row = by_name[name]
        source_path = FROZEN_OUTPUT / name
        raw = source_path.read_bytes()
        if _sha256(raw) != frozen_row.get("output_sha256"):
            raise RuntimeError(f"immutable legacy artifact hash changed: {name}")
        destination = output / name
        if destination.exists():
            raise RuntimeError(f"refusing to overwrite legacy artifact: {destination}")
        destination.write_bytes(raw)

        source_file = frozen_row.get("source_file")
        source_sha256 = frozen_row.get("source_sha256")
        source = (ROOT / source_file).resolve() if isinstance(source_file, str) else None
        if source is None:
            source_sha256 = None
        elif not source.is_relative_to(ROOT.resolve()):
            raise RuntimeError(f"legacy source path escapes the repository: {name}")
        elif not isinstance(source_sha256, str):
            raise RuntimeError(f"legacy source binding is incomplete: {name}")
        expected_warnings = frozen_row.get("expected_warnings")
        if not isinstance(expected_warnings, list) or not all(
            isinstance(code, str) for code in expected_warnings
        ):
            raise RuntimeError(f"legacy warning contract is malformed: {name}")
        export_exit_code = frozen_row.get("export_exit_code")
        if not isinstance(export_exit_code, int):
            raise RuntimeError(f"legacy export exit code is malformed: {name}")
        required_text = (
            "producer",
            "producer_class",
            "version",
            "producer_license",
            "score_license",
        )
        if not all(isinstance(frozen_row.get(field), str) for field in required_text):
            raise RuntimeError(f"legacy producer metadata is malformed: {name}")

        reproduced = _fixture_row(
            destination,
            producer=frozen_row["producer"],
            producer_class=frozen_row["producer_class"],
            version=frozen_row["version"],
            producer_license=frozen_row["producer_license"],
            score_license=frozen_row["score_license"],
            export_exit_code=export_exit_code,
            expected_warnings=tuple(expected_warnings),
            source=source,
            source_sha256=source_sha256,
        )
        if reproduced != frozen_row:
            raise RuntimeError(
                f"immutable legacy row no longer matches current contracts: {name}"
            )
        copied.append(reproduced)
    return copied


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="new, empty, untracked directory for fresh producer output comparison",
    )
    parser.add_argument(
        "--musescore",
        default=shutil.which("mscore"),
        help="exact MuseScore 4.7.4 CLI used for the notation-app corpus",
    )
    args = parser.parse_args()
    _require_version("music21", MUSIC21_VERSION)
    if not args.musescore:
        raise RuntimeError("MuseScore 4.7.4 is required for the producer corpus")
    _require_musescore_version(args.musescore)

    output = args.output_dir.expanduser().resolve()
    frozen = FROZEN_OUTPUT.resolve()
    if output == frozen or frozen in output.parents:
        raise RuntimeError("refusing to overwrite the frozen producer-fixture corpus")
    if output.exists():
        raise RuntimeError(f"output directory already exists: {output}")
    output.mkdir(parents=True)

    fixtures = _copy_and_validate_legacy_rows(output)

    for source_name in ROUNDTRIP_SOURCE_NAMES:
        source = MUSICXML_SOURCES / source_name
        destination = output / f"musescore-4.7.4-roundtrip-{source.stem}.musicxml"
        exit_code, source_sha256 = _run_musescore(args.musescore, source, destination)
        warnings = () if source_name == "supported_minor.musicxml" else (KEY_MODE_WARNING,)
        score_license = "CC-BY-4.0" if source_name == "supported_minor.musicxml" else "CC0-1.0"
        fixtures.append(
            _fixture_row(
                destination,
                producer="MuseScore Studio",
                producer_class="notation-application",
                version=MUSESCORE_VERSION,
                producer_license=MUSESCORE_PRODUCER_LICENSE,
                score_license=score_license,
                export_exit_code=exit_code,
                expected_warnings=warnings,
                source=source,
                source_sha256=source_sha256,
            )
        )

    mxl_source = MUSICXML_SOURCES / "supported_basic.musicxml"
    mxl_path = output / "musescore-4.7.4-roundtrip-supported_basic.mxl"
    mxl_exit, mxl_source_sha256 = _run_musescore(
        args.musescore, mxl_source, mxl_path
    )
    fixtures.append(
        _fixture_row(
            mxl_path,
            producer="MuseScore Studio",
            producer_class="notation-application",
            version=MUSESCORE_VERSION,
            producer_license=MUSESCORE_PRODUCER_LICENSE,
            score_license="CC0-1.0",
            export_exit_code=mxl_exit,
            expected_warnings=(MXL_MEDIA_TYPE_WARNING, KEY_MODE_WARNING),
            source=mxl_source,
            source_sha256=mxl_source_sha256,
        )
    )

    manifest = {
        "schema": "fretsure-producer-fixtures@0.3.0",
        "note": (
            "Files are unedited exporter output. Success and exact semantics are "
            "observed only for each frozen artifact/version; no broader producer or "
            "MusicXML compatibility is implied. GPL software is an external exporter, "
            "not a project dependency."
        ),
        "fixtures": fixtures,
    }
    (output / "provenance.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
