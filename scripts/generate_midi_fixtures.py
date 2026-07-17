"""Generate and replay the immutable real-producer MIDI corpus.

Generation is intentionally narrower than ordinary export automation.  It
requires exact producer versions, exact source bytes, and exact known output
hashes, and it only writes a brand-new directory outside the checked-in frozen
corpus.  Every generated artifact is then passed through the public strict MIDI
importer and compared with the manifest assembled from that same typed result.

Example::

    uv run python scripts/generate_midi_fixtures.py \
      --output-dir /tmp/fretsure-midi-producers

The resulting directory is review material.  This script will never overwrite
``tests/fixtures/midi/producers`` or any existing path.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import BinaryIO

from fretsure.importers import (  # type: ignore[import-untyped]
    ImportFailure,
    ImportSuccess,
    import_midi,
)

ROOT = Path(__file__).resolve().parents[1]
FROZEN_OUTPUT = ROOT / "tests" / "fixtures" / "midi" / "producers"
MANIFEST_NAME = "provenance.json"
MANIFEST_SCHEMA = "fretsure-midi-producer-fixtures@0.1.0"
CENSUS_SCHEMA = "fretsure-midi-census@0.1.0"
MIDI_IMPORTER_VERSION = "midi@0.1.0"
MUSIC21_VERSION = "10.5.0"
MUSESCORE_VERSION = "4.7.4"
MUSESCORE_IDENTITY = f"MuseScore4 {MUSESCORE_VERSION}"
SUBPROCESS_TIMEOUT_SECONDS = 60.0
SUBPROCESS_OUTPUT_LIMIT = 64 * 1024

MELODY_SOURCE = ROOT / "tests" / "fixtures" / "midi" / "sources" / "melody_only.musicxml"
HARMONY_SOURCE = ROOT / "tests" / "fixtures" / "musicxml" / "supported_basic.musicxml"
SOURCE_CONTRACTS = {
    MELODY_SOURCE: (
        "253cb68bb88db194d899a7e36f8702bb9691f33309c1692c9c57aa8c5f71893d",
        2_096,
    ),
    HARMONY_SOURCE: (
        "a57887bc0373babb8029ef0316e4f6ab91e980576bf67273dabecdd626126984",
        2_308,
    ),
}

_MUSIC21_EXPORT = """
from music21 import converter
import sys
converter.parse(sys.argv[1]).write('midi', fp=sys.argv[2])
""".strip()


@dataclass(frozen=True, slots=True)
class ArtifactSpec:
    output_file: str
    source: Path
    producer: str
    producer_class: str
    version: str
    producer_license: str
    command: tuple[str, ...]
    expected: str
    raw_sha256: str
    raw_bytes: int
    observation: str


ARTIFACT_SPECS = (
    ArtifactSpec(
        "musescore-4.7.4-melody_only.mid",
        MELODY_SOURCE,
        "MuseScore Studio",
        "notation-application",
        MUSESCORE_VERSION,
        "GPL-3.0-only (external exporter only)",
        ("mscore", "-o", "{output_file}", "{source_file}"),
        "success",
        "f28ca58259125af6f7aa11388dd26a9c2fc01dba833798843b182f59f82605b7",
        147,
        (
            "MuseScore emits the note-bearing EOT at 7 beats, dropping the source's "
            "final beat of silence, and releases each sounding note one PPQN tick "
            "early; those raw durations are authoritative."
        ),
    ),
    ArtifactSpec(
        "music21-10.5.0-melody_only.mid",
        MELODY_SOURCE,
        "music21",
        "python-library",
        MUSIC21_VERSION,
        "BSD-3-Clause (external exporter only)",
        (
            "python",
            "-c",
            "<frozen-music21-export>",
            "{source_file}",
            "{output_file}",
        ),
        "success",
        "9d6dff16ad49f7a2cb75f43b60af4a85bd86797f505d7f5e7f5efd7a06ea227c",
        116,
        (
            "music21 preserves notation-exact note durations and the note-track EOT "
            "as 8 beats; no equality with the MuseScore MusicIR is claimed."
        ),
    ),
    ArtifactSpec(
        "musescore-4.7.4-supported_basic.mid",
        HARMONY_SOURCE,
        "MuseScore Studio",
        "notation-application",
        MUSESCORE_VERSION,
        "GPL-3.0-only (external exporter only)",
        ("mscore", "-o", "{output_file}", "{source_file}"),
        "failure",
        "5906383876caa705d525e92e331107ac65b148f1b8c07ed3aabb24fd15b5cf91",
        246,
        "Realized harmony creates multiple note-bearing streams and is rejected.",
    ),
    ArtifactSpec(
        "music21-10.5.0-supported_basic.mid",
        HARMONY_SOURCE,
        "music21",
        "python-library",
        MUSIC21_VERSION,
        "BSD-3-Clause (external exporter only)",
        (
            "python",
            "-c",
            "<frozen-music21-export>",
            "{source_file}",
            "{output_file}",
        ),
        "failure",
        "6110193b34647adcf9f3968d7b3a46be192ef97a802a7ba35026764430bd88b6",
        173,
        "Realized harmony is polyphonic and fails the strict single-stream contract.",
    ),
)


@dataclass(frozen=True, slots=True)
class CapturedProcess:
    returncode: int
    stdout: bytes
    stderr: bytes


@dataclass(slots=True)
class _Capture:
    data: bytearray
    total: int = 0
    exceeded: bool = False


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _path_contract(path: Path) -> tuple[str, int]:
    raw = path.read_bytes()
    return _sha256(raw), len(raw)


def _canonical_sha256(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return _sha256(canonical)


def _drain_pipe(
    stream: BinaryIO,
    capture: _Capture,
    process: subprocess.Popen[bytes],
    output_limit: int,
) -> None:
    try:
        while chunk := stream.read(8 * 1024):
            capture.total += len(chunk)
            remaining = max(0, output_limit - len(capture.data))
            capture.data.extend(chunk[:remaining])
            if capture.total > output_limit:
                capture.exceeded = True
                try:
                    process.kill()
                except OSError:
                    pass
    finally:
        stream.close()


def _run_bounded(
    command: list[str],
    *,
    timeout_seconds: float = SUBPROCESS_TIMEOUT_SECONDS,
    output_limit: int = SUBPROCESS_OUTPUT_LIMIT,
) -> CapturedProcess:
    """Run one producer command with bounded time and captured output memory."""

    if timeout_seconds <= 0 or output_limit < 0:
        raise ValueError("subprocess bounds must be positive time and non-negative bytes")
    process = subprocess.Popen(  # noqa: S603 - frozen maintainer command only
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise RuntimeError("producer subprocess did not expose bounded pipes")
    stdout = _Capture(bytearray())
    stderr = _Capture(bytearray())
    threads = (
        threading.Thread(
            target=_drain_pipe,
            args=(process.stdout, stdout, process, output_limit),
            daemon=True,
        ),
        threading.Thread(
            target=_drain_pipe,
            args=(process.stderr, stderr, process, output_limit),
            daemon=True,
        ),
    )
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=5)
    if any(thread.is_alive() for thread in threads):
        process.kill()
        raise RuntimeError("producer subprocess pipes did not close after termination")
    if timed_out:
        raise RuntimeError(
            f"producer subprocess exceeded {timeout_seconds:g}s timeout"
        )
    if stdout.exceeded or stderr.exceeded:
        raise RuntimeError(
            f"producer subprocess exceeded {output_limit} captured output bytes"
        )
    return CapturedProcess(
        process.returncode,
        bytes(stdout.data),
        bytes(stderr.data),
    )


def _require_versions(musescore: str) -> None:
    actual_music21 = importlib.metadata.version("music21")
    if actual_music21 != MUSIC21_VERSION:
        raise RuntimeError(
            f"music21 fixture requires {MUSIC21_VERSION}, found {actual_music21}"
        )
    completed = _run_bounded([musescore, "--version"])
    observed = b"\n".join(
        value.strip()
        for value in (completed.stdout, completed.stderr)
        if value.strip()
    ).decode("utf-8", errors="replace")
    if completed.returncode != 0 or observed != MUSESCORE_IDENTITY:
        raise RuntimeError(
            "MuseScore fixture requires exact identity "
            f"{MUSESCORE_IDENTITY!r}; found returncode={completed.returncode}, "
            f"output={observed!r}"
        )


def _prepare_output(output: Path) -> Path:
    resolved = output.expanduser().resolve()
    frozen = FROZEN_OUTPUT.resolve()
    if resolved == frozen or frozen in resolved.parents:
        raise RuntimeError("refusing to write into the frozen MIDI producer corpus")
    if resolved.exists():
        raise RuntimeError(f"output directory already exists: {resolved}")
    resolved.mkdir(parents=True)
    return resolved


def _export(spec: ArtifactSpec, output: Path, musescore: str) -> Path:
    source_expected = SOURCE_CONTRACTS[spec.source]
    before = _path_contract(spec.source)
    if before != source_expected:
        raise RuntimeError(
            f"source contract changed for {spec.source.relative_to(ROOT)}: {before!r}"
        )
    destination = output / spec.output_file
    if destination.exists():
        raise RuntimeError(f"refusing to overwrite producer artifact: {destination}")
    if spec.producer == "MuseScore Studio":
        completed = _run_bounded(
            [musescore, "-o", str(destination), str(spec.source)]
        )
        # MuseScore 4.7.4 can abort during GUI-runtime teardown after writing a
        # complete deterministic file.  Exact bytes below, not exit status,
        # decide acceptance; stderr/stdout remain bounded either way.
        _ = completed.returncode
    else:
        completed = _run_bounded(
            [sys.executable, "-c", _MUSIC21_EXPORT, str(spec.source), str(destination)]
        )
        if completed.returncode != 0:
            tail = completed.stderr.decode("utf-8", errors="replace")[-2_000:]
            raise RuntimeError(
                f"music21 export failed with {completed.returncode}: {tail!r}"
            )
    after = _path_contract(spec.source)
    if after != before:
        raise RuntimeError(f"source changed while exporting: {spec.source}")
    try:
        raw_contract = _path_contract(destination)
    except OSError as exc:
        raise RuntimeError(f"producer wrote no readable artifact: {destination}") from exc
    expected_raw = (spec.raw_sha256, spec.raw_bytes)
    if raw_contract != expected_raw:
        raise RuntimeError(
            f"producer bytes changed for {spec.output_file}: expected "
            f"{expected_raw!r}, found {raw_contract!r}"
        )
    return destination


def _fraction(value: Fraction | None) -> list[int] | None:
    if value is None:
        return None
    return [value.numerator, value.denominator]


def _diagnostic(value: object) -> dict[str, object]:
    code = object.__getattribute__(value, "code")
    severity = object.__getattribute__(value, "severity")
    location = object.__getattribute__(value, "location")
    serialized_location: dict[str, object] | None = None
    if location is not None:
        serialized_location = {
            field: raw
            for field in ("element", "track_index", "channel", "tick", "event_index")
            if (raw := object.__getattribute__(location, field)) is not None
        }
    return {
        "code": code.value,
        "location": serialized_location,
        "severity": severity.value,
    }


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
                "duration": _fraction(note.duration),
                "onset": _fraction(note.onset),
                "pitch": note.pitch,
                "voice": note.voice,
            }
            for note in ir.notes
        ],
        "tempo_bpm": ir.meta.tempo_bpm,
        "time_sig": list(ir.meta.time_sig),
        "title": ir.meta.title,
        "warnings": [_diagnostic(warning) for warning in result.warnings],
    }


def _smf_header(raw: bytes) -> dict[str, int]:
    if len(raw) < 14 or raw[:8] != b"MThd\x00\x00\x00\x06":
        raise RuntimeError("producer artifact lacks the exact SMF header envelope")
    division = int.from_bytes(raw[12:14], "big")
    if division == 0 or division & 0x8000:
        raise RuntimeError("producer artifact does not use positive PPQN timing")
    return {
        "format": int.from_bytes(raw[8:10], "big"),
        "ticks_per_quarter": division,
        "tracks": int.from_bytes(raw[10:12], "big"),
    }


def _row(spec: ArtifactSpec, artifact: Path) -> dict[str, object]:
    raw = artifact.read_bytes()
    source_sha256, source_bytes = SOURCE_CONTRACTS[spec.source]
    result = import_midi(artifact)
    semantic: dict[str, object] | None
    diagnostics: list[dict[str, object]]
    if spec.expected == "success":
        if not isinstance(result, ImportSuccess):
            raise RuntimeError(
                f"expected public strict import success for {artifact.name}: "
                f"{getattr(result, 'diagnostics', None)!r}"
            )
        if result.importer_version != MIDI_IMPORTER_VERSION or result.sha256 != spec.raw_sha256:
            raise RuntimeError(f"public import identity changed for {artifact.name}")
        if result.provenance is None or (
            result.provenance.source_format != "midi"
            or result.provenance.source_filename != artifact.name
            or result.provenance.raw_sha256 != spec.raw_sha256
            or result.provenance.root_sha256 != spec.raw_sha256
            or result.provenance.root_member is not None
        ):
            raise RuntimeError(f"public import provenance changed for {artifact.name}")
        semantic = _semantic(result)
        diagnostics = []
    else:
        if not isinstance(result, ImportFailure):
            raise RuntimeError(f"expected public strict import failure for {artifact.name}")
        semantic = None
        diagnostics = [_diagnostic(item) for item in result.diagnostics]
        if not diagnostics or not any(item["severity"] == "error" for item in diagnostics):
            raise RuntimeError(f"typed negative lacks an error for {artifact.name}")
    return {
        "command": list(spec.command),
        "diagnostics": diagnostics,
        "diagnostics_sha256": None if not diagnostics else _canonical_sha256(diagnostics),
        "expected": spec.expected,
        "importer_version": MIDI_IMPORTER_VERSION,
        "observation": spec.observation,
        "output_file": spec.output_file,
        "producer": spec.producer,
        "producer_class": spec.producer_class,
        "producer_license": spec.producer_license,
        "raw_bytes": len(raw),
        "raw_sha256": _sha256(raw),
        "score_license": "CC0-1.0",
        "semantic": semantic,
        "semantic_sha256": None if semantic is None else _canonical_sha256(semantic),
        "smf": _smf_header(raw),
        "source_bytes": source_bytes,
        "source_file": spec.source.relative_to(ROOT).as_posix(),
        "source_sha256": source_sha256,
        "version": spec.version,
    }


def _manifest(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "fixtures": rows,
        "note": (
            "Artifacts are unedited exact producer output. Raw tick timelines are "
            "authoritative per artifact. MuseScore and music21 durations intentionally "
            "differ, so no cross-producer MusicIR equivalence is claimed. Producer "
            "licenses describe external exporters, not project dependencies."
        ),
        "schema": MANIFEST_SCHEMA,
    }


def _load_manifest(corpus: Path) -> dict[str, object]:
    try:
        value = json.loads((corpus / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("cannot read MIDI producer manifest") from exc
    if not isinstance(value, dict) or value.get("schema") != MANIFEST_SCHEMA:
        raise RuntimeError("unexpected MIDI producer manifest schema")
    return value


def replay_corpus(corpus: Path) -> dict[str, object]:
    """Re-import every frozen byte and fail if any manifest contract changed."""

    resolved = corpus.resolve()
    manifest = _load_manifest(resolved)
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list) or not all(isinstance(row, dict) for row in fixtures):
        raise RuntimeError("MIDI producer manifest fixtures must be object rows")
    by_name = {spec.output_file: spec for spec in ARTIFACT_SPECS}
    if len(fixtures) != len(by_name):
        raise RuntimeError("MIDI producer manifest must contain exactly four rows")
    output_names = [row.get("output_file") for row in fixtures]
    if not all(type(name) is str for name in output_names):
        raise RuntimeError("MIDI producer manifest output names must be exact strings")
    if len(output_names) != len(set(output_names)) or set(output_names) != set(by_name):
        raise RuntimeError("MIDI producer manifest output names are not a bijection")
    on_disk = {
        path.name
        for path in resolved.iterdir()
        if path.name != MANIFEST_NAME
    }
    if on_disk != set(by_name):
        raise RuntimeError("MIDI producer artifact directory does not match its manifest")
    reproduced = [
        _row(by_name[name], resolved / name)
        for name in output_names
        if isinstance(name, str)
    ]
    if reproduced != fixtures:
        raise RuntimeError("public strict replay no longer matches the MIDI manifest")
    return build_census(manifest)


def build_census(manifest: dict[str, object]) -> dict[str, object]:
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list) or not all(isinstance(row, dict) for row in fixtures):
        raise RuntimeError("cannot census malformed MIDI fixture rows")
    successes = sum(row.get("expected") == "success" for row in fixtures)
    failures = sum(row.get("expected") == "failure" for row in fixtures)
    return {
        "artifact_count": len(fixtures),
        "artifacts": [
            {
                "expected": row["expected"],
                "output_file": row["output_file"],
                "raw_bytes": row["raw_bytes"],
                "raw_sha256": row["raw_sha256"],
                "result_sha256": (
                    row["semantic_sha256"]
                    if row["expected"] == "success"
                    else row["diagnostics_sha256"]
                ),
                "smf": row["smf"],
            }
            for row in fixtures
        ],
        "corpus_schema": MANIFEST_SCHEMA,
        "cross_producer_ir_equivalence_claimed": False,
        "importer_version": MIDI_IMPORTER_VERSION,
        "note": (
            "MuseScore melody duration is 7 beats with one-tick-short releases; "
            "music21 melody duration is 8 beats. Both harmony realizations are typed "
            "negatives, and no producer-wide compatibility is inferred."
        ),
        "producer_versions": ["MuseScore Studio 4.7.4", "music21 10.5.0"],
        "raw_bytes_total": sum(int(row["raw_bytes"]) for row in fixtures),
        "schema": CENSUS_SCHEMA,
        "successes": successes,
        "typed_failures": failures,
    }


def generate(output: Path, *, musescore: str) -> dict[str, object]:
    _require_versions(musescore)
    destination = _prepare_output(output)
    rows = [
        _row(spec, _export(spec, destination, musescore))
        for spec in ARTIFACT_SPECS
    ]
    manifest = _manifest(rows)
    (destination / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    census = replay_corpus(destination)
    if (
        census["artifact_count"] != 4
        or census["successes"] != 2
        or census["typed_failures"] != 2
    ):
        raise RuntimeError("fresh MIDI corpus failed its final census")
    return census


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="new non-frozen directory for exact producer output",
    )
    parser.add_argument(
        "--musescore",
        default=shutil.which("mscore"),
        help="exact MuseScore 4.7.4 executable",
    )
    args = parser.parse_args()
    if not args.musescore:
        raise RuntimeError("MuseScore 4.7.4 is required for the MIDI corpus")
    census = generate(args.output_dir, musescore=args.musescore)
    print(json.dumps(census, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
