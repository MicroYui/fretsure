"""Immutable provenance and strict replay gate for real MIDI exporters."""

from __future__ import annotations

import hashlib
import json
import runpy
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from fretsure.importers import (  # type: ignore[import-untyped]
    ImportFailure,
    ImportSuccess,
    import_midi,
)

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "midi" / "producers"
MANIFEST = FIXTURES / "provenance.json"
CENSUS = ROOT / "docs" / "experiments" / "2026-07-17-midi-census.json"
GENERATOR = runpy.run_path(str(ROOT / "scripts" / "generate_midi_fixtures.py"))
REPLAY = cast(Callable[[Path], dict[str, object]], GENERATOR["replay_corpus"])
BUILD_CENSUS = cast(
    Callable[[dict[str, object]], dict[str, object]],
    GENERATOR["build_census"],
)
PREPARE_OUTPUT = cast(Callable[[Path], Path], GENERATOR["_prepare_output"])
RUN_BOUNDED = cast(Callable[..., object], GENERATOR["_run_bounded"])

RAW_CONTRACTS = {
    "musescore-4.7.4-melody_only.mid": (
        "f28ca58259125af6f7aa11388dd26a9c2fc01dba833798843b182f59f82605b7",
        147,
    ),
    "music21-10.5.0-melody_only.mid": (
        "9d6dff16ad49f7a2cb75f43b60af4a85bd86797f505d7f5e7f5efd7a06ea227c",
        116,
    ),
    "musescore-4.7.4-supported_basic.mid": (
        "5906383876caa705d525e92e331107ac65b148f1b8c07ed3aabb24fd15b5cf91",
        246,
    ),
    "music21-10.5.0-supported_basic.mid": (
        "6110193b34647adcf9f3968d7b3a46be192ef97a802a7ba35026764430bd88b6",
        173,
    ),
}
SOURCE_CONTRACTS = {
    "tests/fixtures/midi/sources/melody_only.musicxml": (
        "253cb68bb88db194d899a7e36f8702bb9691f33309c1692c9c57aa8c5f71893d",
        2_096,
    ),
    "tests/fixtures/musicxml/supported_basic.musicxml": (
        "a57887bc0373babb8029ef0316e4f6ab91e980576bf67273dabecdd626126984",
        2_308,
    ),
}
ROW_FIELDS = {
    "command",
    "diagnostics",
    "diagnostics_sha256",
    "expected",
    "importer_version",
    "observation",
    "output_file",
    "producer",
    "producer_class",
    "producer_license",
    "raw_bytes",
    "raw_sha256",
    "score_license",
    "semantic",
    "semantic_sha256",
    "smf",
    "source_bytes",
    "source_file",
    "source_sha256",
    "version",
}
SEMANTIC_FIELDS = {
    "chords",
    "duration_beats",
    "key",
    "license",
    "notes",
    "tempo_bpm",
    "time_sig",
    "title",
    "warnings",
}
DIAGNOSTIC_FIELDS = {"code", "location", "severity"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest() -> dict[str, object]:
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _rows() -> list[dict[str, object]]:
    fixtures = _manifest()["fixtures"]
    assert isinstance(fixtures, list)
    assert all(isinstance(row, dict) for row in fixtures)
    return fixtures


def _by_name() -> dict[str, dict[str, object]]:
    rows = _rows()
    assert all(type(row["output_file"]) is str for row in rows)
    return {cast(str, row["output_file"]): row for row in rows}


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and set(value) <= frozenset("0123456789abcdef")
    )


def _assert_fraction(value: object) -> None:
    assert type(value) is list and len(value) == 2
    assert all(type(item) is int for item in value)
    assert value[1] > 0


def _assert_diagnostic(value: object) -> None:
    assert isinstance(value, dict)
    assert set(value) == DIAGNOSTIC_FIELDS
    assert type(value["code"]) is str and value["code"]
    assert value["severity"] in {"error", "warning"}
    location = value["location"]
    assert location is None or isinstance(location, dict)
    if isinstance(location, dict):
        assert set(location) <= {
            "channel",
            "element",
            "event_index",
            "tick",
            "track_index",
        }
        for field in ("channel", "event_index", "tick", "track_index"):
            if field in location:
                assert type(location[field]) is int and location[field] >= 0
        if "channel" in location:
            assert 1 <= location["channel"] <= 16


def _assert_semantic(value: object) -> None:
    assert isinstance(value, dict)
    assert set(value) == SEMANTIC_FIELDS
    _assert_fraction(value["duration_beats"])
    assert value["key"] == "C"
    assert value["license"] == "unprovided"
    assert value["tempo_bpm"] == 96.0
    assert value["time_sig"] == [4, 4]
    assert value["title"] == "Melody"
    assert value["chords"] == []
    notes = value["notes"]
    assert isinstance(notes, list) and len(notes) == 4
    for note in notes:
        assert isinstance(note, dict)
        assert set(note) == {"duration", "onset", "pitch", "voice"}
        _assert_fraction(note["duration"])
        _assert_fraction(note["onset"])
        assert type(note["pitch"]) is int and 0 <= note["pitch"] <= 127
        assert note["voice"] == "melody"
    warnings = value["warnings"]
    assert isinstance(warnings, list) and len(warnings) == 3
    for warning in warnings:
        _assert_diagnostic(warning)
        assert warning["severity"] == "warning"


def test_manifest_schema_and_rows_are_exact() -> None:
    manifest = _manifest()
    assert set(manifest) == {"fixtures", "note", "schema"}
    assert manifest["schema"] == "fretsure-midi-producer-fixtures@0.1.0"
    assert type(manifest["note"]) is str
    assert "no cross-producer MusicIR equivalence" in manifest["note"]

    rows = _rows()
    assert len(rows) == 4
    for row in rows:
        assert set(row) == ROW_FIELDS
        assert row["expected"] in {"success", "failure"}
        assert row["importer_version"] == "midi@0.1.0"
        for field in (
            "observation",
            "output_file",
            "producer",
            "producer_class",
            "producer_license",
            "score_license",
            "source_file",
            "version",
        ):
            assert type(row[field]) is str and row[field]
        assert row["score_license"] == "CC0-1.0"
        assert _is_sha256(row["raw_sha256"])
        assert _is_sha256(row["source_sha256"])
        assert type(row["raw_bytes"]) is int and row["raw_bytes"] > 0
        assert type(row["source_bytes"]) is int and row["source_bytes"] > 0
        command = row["command"]
        assert isinstance(command, list) and command
        assert all(type(part) is str and part for part in command)
        smf = row["smf"]
        assert isinstance(smf, dict)
        assert set(smf) == {"format", "ticks_per_quarter", "tracks"}
        assert smf["format"] == 1
        assert smf["ticks_per_quarter"] in {480, 10_080}
        assert smf["tracks"] in {1, 2}
        diagnostics = row["diagnostics"]
        assert isinstance(diagnostics, list)
        for diagnostic in diagnostics:
            _assert_diagnostic(diagnostic)
        if row["expected"] == "success":
            assert diagnostics == []
            assert row["diagnostics_sha256"] is None
            _assert_semantic(row["semantic"])
            assert _is_sha256(row["semantic_sha256"])
        else:
            assert diagnostics
            assert any(item["severity"] == "error" for item in diagnostics)
            assert _is_sha256(row["diagnostics_sha256"])
            assert row["semantic"] is None
            assert row["semantic_sha256"] is None


def test_artifacts_and_sources_match_their_frozen_hashes_and_sizes() -> None:
    assert {
        path.name: (_sha256(path.read_bytes()), path.stat().st_size)
        for path in FIXTURES.glob("*.mid")
    } == RAW_CONTRACTS
    assert {
        name: (_sha256((ROOT / name).read_bytes()), (ROOT / name).stat().st_size)
        for name in SOURCE_CONTRACTS
    } == SOURCE_CONTRACTS

    rows = _by_name()
    assert set(rows) == set(RAW_CONTRACTS)
    for name, (raw_sha256, raw_bytes) in RAW_CONTRACTS.items():
        assert rows[name]["raw_sha256"] == raw_sha256
        assert rows[name]["raw_bytes"] == raw_bytes
        source_file = cast(str, rows[name]["source_file"])
        source_sha256, source_bytes = SOURCE_CONTRACTS[source_file]
        assert rows[name]["source_sha256"] == source_sha256
        assert rows[name]["source_bytes"] == source_bytes


def test_artifact_directory_and_manifest_are_a_bidirectional_bijection() -> None:
    manifest_names = [cast(str, row["output_file"]) for row in _rows()]
    assert len(manifest_names) == len(set(manifest_names))
    assert all(Path(name).name == name for name in manifest_names)
    on_disk = {
        path.name
        for path in FIXTURES.iterdir()
        if path.is_file() and path.name != MANIFEST.name
    }
    assert set(manifest_names) == on_disk


def test_positive_producers_bind_exact_but_non_equivalent_raw_timing() -> None:
    rows = _by_name()
    musescore = cast(
        dict[str, object],
        rows["musescore-4.7.4-melody_only.mid"]["semantic"],
    )
    music21 = cast(
        dict[str, object],
        rows["music21-10.5.0-melody_only.mid"]["semantic"],
    )
    assert musescore["duration_beats"] == [7, 1]
    assert [note["duration"] for note in cast(list[dict[str, object]], musescore["notes"])] == [
        [479, 480],
        [1439, 480],
        [479, 480],
        [479, 480],
    ]
    assert music21["duration_beats"] == [8, 1]
    assert [note["duration"] for note in cast(list[dict[str, object]], music21["notes"])] == [
        [1, 1],
        [3, 1],
        [1, 1],
        [1, 1],
    ]
    assert musescore != music21
    assert "one PPQN tick early" in cast(
        str,
        rows["musescore-4.7.4-melody_only.mid"]["observation"],
    )
    assert "no equality" in cast(
        str,
        rows["music21-10.5.0-melody_only.mid"]["observation"],
    )


def test_harmony_realizations_are_exact_typed_negatives() -> None:
    rows = _by_name()
    musescore = cast(
        list[dict[str, object]],
        rows["musescore-4.7.4-supported_basic.mid"]["diagnostics"],
    )
    assert musescore == [
        {
            "code": "MULTIPLE_NOTE_BEARING_STREAMS",
            "location": {
                "channel": 2,
                "element": "note-on",
                "event_index": 30,
                "tick": 0,
                "track_index": 0,
            },
            "severity": "error",
        }
    ]

    music21 = cast(
        list[dict[str, object]],
        rows["music21-10.5.0-supported_basic.mid"]["diagnostics"],
    )
    error_codes = {
        item["code"] for item in music21 if item["severity"] == "error"
    }
    assert error_codes == {"MIDI_NOTE_PAIRING_ERROR", "MIDI_POLYPHONY_UNSUPPORTED"}
    assert all(item["location"] is not None for item in music21)


def test_public_strict_replay_is_deterministic_for_all_four_artifacts() -> None:
    for row in _rows():
        name = cast(str, row["output_file"])
        path = FIXTURES / name
        first = import_midi(path)
        second = import_midi(path)
        assert first == second
        if row["expected"] == "success":
            assert isinstance(first, ImportSuccess)
            assert first.sha256 == row["raw_sha256"]
            assert first.importer_version == "midi@0.1.0"
        else:
            assert isinstance(first, ImportFailure)


def test_frozen_and_fresh_directory_replay_match_the_checked_census(
    tmp_path: Path,
) -> None:
    checked_census = json.loads(CENSUS.read_text(encoding="utf-8"))
    assert REPLAY(FIXTURES) == checked_census

    fresh = tmp_path / "fresh-midi-corpus"
    shutil.copytree(FIXTURES, fresh)
    assert REPLAY(fresh) == checked_census


def test_census_is_exactly_derived_and_disclaims_cross_producer_equivalence() -> None:
    census = json.loads(CENSUS.read_text(encoding="utf-8"))
    assert census == BUILD_CENSUS(_manifest())
    assert census["schema"] == "fretsure-midi-census@0.1.0"
    assert census["corpus_schema"] == "fretsure-midi-producer-fixtures@0.1.0"
    assert census["artifact_count"] == 4
    assert census["successes"] == 2
    assert census["typed_failures"] == 2
    assert census["raw_bytes_total"] == 682
    assert census["cross_producer_ir_equivalence_claimed"] is False
    assert "7 beats" in census["note"]
    assert "8 beats" in census["note"]


def test_generator_refuses_frozen_or_existing_output(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="frozen MIDI producer corpus"):
        PREPARE_OUTPUT(FIXTURES)
    with pytest.raises(RuntimeError, match="already exists"):
        PREPARE_OUTPUT(tmp_path)

    fresh = tmp_path / "new" / "corpus"
    assert PREPARE_OUTPUT(fresh) == fresh.resolve()
    assert fresh.is_dir()


def test_generator_subprocess_capture_enforces_output_and_timeout_bounds() -> None:
    with pytest.raises(RuntimeError, match="captured output bytes"):
        RUN_BOUNDED(
            [sys.executable, "-c", "print('x' * 10000)"],
            timeout_seconds=5,
            output_limit=128,
        )
    with pytest.raises(RuntimeError, match="timeout"):
        RUN_BOUNDED(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            timeout_seconds=0.05,
            output_limit=128,
        )
