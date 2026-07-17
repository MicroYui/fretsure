"""Compatibility/provenance gate over unedited real-exporter output."""

from __future__ import annotations

import hashlib
import json
import runpy
import xml.etree.ElementTree as ET
import zipfile
from fractions import Fraction
from io import BytesIO
from pathlib import Path

from fretsure.importers import ImportSuccess, import_musicxml

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "producers"
MANIFEST = FIXTURES / "provenance.json"
GENERATOR = runpy.run_path(str(ROOT / "scripts" / "generate_producer_fixtures.py"))
REPLAY = runpy.run_path(str(ROOT / "scripts" / "replay_producer_census.py"))["replay"]
LEGACY_ARTIFACT_NAMES = GENERATOR["LEGACY_ARTIFACT_NAMES"]
COPY_AND_VALIDATE_LEGACY_ROWS = GENERATOR["_copy_and_validate_legacy_rows"]
HEX_DIGITS = frozenset("0123456789abcdef")
ROW_FIELDS = {
    "expected",
    "expected_warnings",
    "export_exit_code",
    "format",
    "output_file",
    "output_sha256",
    "producer",
    "producer_class",
    "producer_license",
    "root_member",
    "root_sha256",
    "score_license",
    "semantic",
    "semantic_sha256",
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
    "warning_codes",
}
MUSESCORE_ROUNDTRIP_SOURCES = {
    "tests/fixtures/musicxml/metamorphic_long.musicxml",
    "tests/fixtures/musicxml/metamorphic_tied.musicxml",
    "tests/fixtures/musicxml/supported_basic.musicxml",
    "tests/fixtures/musicxml/supported_harmonies.musicxml",
    "tests/fixtures/musicxml/supported_minor.musicxml",
    "tests/fixtures/musicxml/supported_tie_continue.musicxml",
}
ORIGINAL_ARTIFACT_HASHES = {
    "musescore-4.7.4.musicxml": (
        "8aa3f622429dee2dda26ca91c87237470d60c4c02fb996bd9171c9238cd77386"
    ),
    "music21-10.5.0.musicxml": (
        "115f2c2f5353680d34bd49f02c12519511181741cbc1b51e9f1e539335291c70"
    ),
    "musicxml-1.6.1.musicxml": (
        "c814281aa7bf4784421a9c8c53ebeffbe5abf9e897b2f254a1c31560d85100f8"
    ),
}


def _manifest() -> dict[str, object]:
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _rows() -> list[dict[str, object]]:
    fixtures = _manifest()["fixtures"]
    assert isinstance(fixtures, list)
    assert all(isinstance(row, dict) for row in fixtures)
    return fixtures


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and set(value) <= HEX_DIGITS
    )


def _fraction(value: Fraction | None) -> list[int] | None:
    if value is None:
        return None
    return [value.numerator, value.denominator]


def _semantic(result: ImportSuccess) -> dict[str, object]:
    """Return the provenance-free semantic payload covered by the manifest hash."""

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


def _root_bytes(raw: bytes, row: dict[str, object]) -> bytes:
    if row["format"] == "musicxml":
        assert row["root_member"] is None
        return raw
    root_member = row["root_member"]
    assert type(root_member) is str
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        assert len(archive.namelist()) == len(set(archive.namelist()))
        return archive.read(root_member)


def _assert_fraction_json(value: object, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    assert type(value) is list and len(value) == 2
    assert all(type(component) is int for component in value)
    assert value[1] > 0


def _assert_semantic_schema(value: object) -> None:
    assert isinstance(value, dict)
    assert set(value) == SEMANTIC_FIELDS
    for field in ("key", "license", "title"):
        assert type(value[field]) is str and value[field]
    assert type(value["tempo_bpm"]) is float
    assert 1.0 <= value["tempo_bpm"] <= 1_000.0
    assert type(value["time_sig"]) is list and len(value["time_sig"]) == 2
    assert all(type(component) is int for component in value["time_sig"])
    _assert_fraction_json(value["duration_beats"], nullable=True)

    notes = value["notes"]
    assert type(notes) is list
    for event in notes:
        assert isinstance(event, dict)
        assert set(event) == {"duration", "onset", "pitch", "voice"}
        _assert_fraction_json(event["duration"])
        _assert_fraction_json(event["onset"])
        assert type(event["pitch"]) is int and 0 <= event["pitch"] <= 127
        assert event["voice"] in {"melody", "bass", "harmony"}

    chords = value["chords"]
    assert type(chords) is list
    for chord in chords:
        assert isinstance(chord, dict)
        assert set(chord) == {"onset", "pitch_classes", "root_pc", "symbol"}
        _assert_fraction_json(chord["onset"])
        assert type(chord["symbol"]) is str and chord["symbol"]
        assert type(chord["root_pc"]) is int and 0 <= chord["root_pc"] <= 11
        assert type(chord["pitch_classes"]) is list
        assert chord["pitch_classes"] == sorted(set(chord["pitch_classes"]))
        assert all(
            type(pitch_class) is int and 0 <= pitch_class <= 11
            for pitch_class in chord["pitch_classes"]
        )

    warning_codes = value["warning_codes"]
    assert type(warning_codes) is list
    assert all(type(code) is str and code for code in warning_codes)


def test_manifest_schema_fields_and_types_are_exact() -> None:
    manifest = _manifest()
    assert set(manifest) == {"fixtures", "note", "schema"}
    assert manifest["schema"] == "fretsure-producer-fixtures@0.3.0"
    assert type(manifest["note"]) is str and manifest["note"]

    rows = _rows()
    assert len(rows) == 10
    for row in rows:
        assert set(row) == ROW_FIELDS
        for field in (
            "output_file",
            "producer",
            "producer_class",
            "producer_license",
            "score_license",
            "version",
        ):
            assert type(row[field]) is str and row[field]
        assert row["format"] in {"musicxml", "mxl"}
        assert row["expected"] == "success"
        assert type(row["export_exit_code"]) is int
        assert 0 <= row["export_exit_code"] <= 255
        assert _is_sha256(row["output_sha256"])
        assert _is_sha256(row["root_sha256"])
        assert _is_sha256(row["semantic_sha256"])
        _assert_semantic_schema(row["semantic"])
        assert type(row["expected_warnings"]) is list
        assert all(type(code) is str and code for code in row["expected_warnings"])
        assert len(row["expected_warnings"]) == len(set(row["expected_warnings"]))

        source_file = row["source_file"]
        source_sha256 = row["source_sha256"]
        assert (source_file is None) == (source_sha256 is None)
        if source_file is not None:
            assert type(source_file) is str and source_file
            assert _is_sha256(source_sha256)
        if row["format"] == "musicxml":
            assert str(row["output_file"]).endswith(".musicxml")
            assert row["root_member"] is None
            assert row["root_sha256"] == row["output_sha256"]
        else:
            assert str(row["output_file"]).endswith(".mxl")
            assert type(row["root_member"]) is str and row["root_member"]


def test_manifest_and_artifact_directory_are_a_bidirectional_bijection() -> None:
    output_files = [row["output_file"] for row in _rows()]
    output_hashes = [row["output_sha256"] for row in _rows()]
    assert len(output_files) == len(set(output_files))
    assert len(output_hashes) == len(set(output_hashes))
    assert all(type(name) is str and Path(name).name == name for name in output_files)

    on_disk = {
        path.name
        for path in FIXTURES.iterdir()
        if path.is_file() and path.name != MANIFEST.name
    }
    assert set(output_files) == on_disk


def test_frozen_output_source_and_root_hashes_are_independently_bound() -> None:
    for row in _rows():
        output_file = row["output_file"]
        assert type(output_file) is str
        output_path = FIXTURES / output_file
        raw = output_path.read_bytes()
        assert _sha256(raw) == row["output_sha256"]

        source_file = row["source_file"]
        if source_file is not None:
            assert type(source_file) is str
            source_path = (ROOT / source_file).resolve()
            assert source_path.is_relative_to(ROOT.resolve())
            assert source_path.is_file()
            assert _sha256(source_path.read_bytes()) == row["source_sha256"]

        root = _root_bytes(raw, row)
        assert _sha256(root) == row["root_sha256"]


def test_original_producer_artifact_bytes_remain_immutable() -> None:
    assert {
        name: _sha256((FIXTURES / name).read_bytes())
        for name in ORIGINAL_ARTIFACT_HASHES
    } == ORIGINAL_ARTIFACT_HASHES


def test_generator_treats_original_artifacts_as_immutable_inputs(
    tmp_path: Path,
) -> None:
    rows = COPY_AND_VALIDATE_LEGACY_ROWS(tmp_path)
    assert [row["output_file"] for row in rows] == list(LEGACY_ARTIFACT_NAMES)
    assert {
        name: _sha256((tmp_path / name).read_bytes())
        for name in LEGACY_ARTIFACT_NAMES
    } == ORIGINAL_ARTIFACT_HASHES
    assert rows[2]["source_file"] == (
        "tests/fixtures/producers/music21-10.5.0.musicxml"
    )
    assert rows[2]["source_sha256"] == ORIGINAL_ARTIFACT_HASHES[
        "music21-10.5.0.musicxml"
    ]


def test_checked_in_census_replay_matches_the_current_manifest() -> None:
    replay = REPLAY(FIXTURES)
    assert replay["artifact_count"] == replay["successes"] == 10
    assert replay["failures"] == 0
    assert replay["package_version"] == "0.5.0"
    assert replay["importer_version"] == "musicxml@0.3.0"
    assert replay["manifest_schema"] == "fretsure-producer-fixtures@0.3.0"


def test_fixture_imports_are_deterministic_and_match_exact_semantic_digests() -> None:
    for row in _rows():
        output_file = row["output_file"]
        assert type(output_file) is str
        path = FIXTURES / output_file
        first = import_musicxml(path)
        second = import_musicxml(path)
        assert first == second
        assert isinstance(first, ImportSuccess), getattr(first, "diagnostics", None)
        assert first.sha256 == row["output_sha256"]
        assert first.provenance is not None
        assert first.provenance.source_filename == output_file
        assert first.provenance.source_format == row["format"]
        assert first.provenance.raw_sha256 == row["output_sha256"]
        assert first.provenance.root_member == row["root_member"]
        assert first.provenance.root_sha256 == row["root_sha256"]

        semantic = _semantic(first)
        assert semantic == row["semantic"]
        assert semantic["license"] == row["score_license"]
        assert semantic["warning_codes"] == row["expected_warnings"]
        assert _semantic_sha256(semantic) == row["semantic_sha256"]


def test_musescore_roundtrips_change_only_loss_aware_key_context_and_warnings() -> None:
    for row in _rows():
        source_file = row["source_file"]
        if source_file is None:
            continue
        assert type(source_file) is str
        output_file = row["output_file"]
        assert type(output_file) is str
        source_result = import_musicxml(ROOT / source_file)
        output_result = import_musicxml(FIXTURES / output_file)
        assert isinstance(source_result, ImportSuccess), getattr(
            source_result, "diagnostics", None
        )
        assert isinstance(output_result, ImportSuccess), getattr(
            output_result, "diagnostics", None
        )

        source_semantic = _semantic(source_result)
        output_semantic = _semantic(output_result)
        if source_file.endswith("supported_minor.musicxml"):
            # MuseScore preserves the one explicit minor mode in this census.
            assert output_semantic == source_semantic
            continue

        expected = dict(source_semantic)
        expected["key"] = "key-signature:fifths=0;mode=unprovided"
        expected["warning_codes"] = row["expected_warnings"]
        assert output_semantic == expected


def test_musescore_roundtrip_census_is_exactly_six_xml_and_one_real_mxl() -> None:
    roundtrips = [
        row
        for row in _rows()
        if row["producer"] == "MuseScore Studio"
        and row["source_file"] in MUSESCORE_ROUNDTRIP_SOURCES
    ]
    assert {row["source_file"] for row in roundtrips} == MUSESCORE_ROUNDTRIP_SOURCES
    assert sum(row["format"] == "musicxml" for row in roundtrips) == 6
    assert sum(row["format"] == "mxl" for row in roundtrips) == 1
    assert all(row["version"] == "4.7.4" for row in roundtrips)
    assert all(row["producer_class"] == "notation-application" for row in roundtrips)


def test_real_musescore_mxl_binds_raw_root_and_importer_provenance() -> None:
    mxl_rows = [row for row in _rows() if row["format"] == "mxl"]
    assert len(mxl_rows) == 1
    row = mxl_rows[0]
    output_file = row["output_file"]
    assert type(output_file) is str
    path = FIXTURES / output_file
    assert zipfile.is_zipfile(path)
    assert row["root_member"] == "score.xml"

    raw = path.read_bytes()
    root = _root_bytes(raw, row)
    assert b"MuseScore Studio 4.7.4" in root
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        assert archive.namelist() == ["META-INF/container.xml", "score.xml"]
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfiles = [
            element
            for element in container.iter()
            if element.tag.rsplit("}", 1)[-1] == "rootfile"
        ]
        assert len(rootfiles) == 1
        assert rootfiles[0].get("full-path") == row["root_member"]
        assert rootfiles[0].get("media-type") is None
    plain_row = next(
        candidate
        for candidate in _rows()
        if candidate["output_file"]
        == "musescore-4.7.4-roundtrip-supported_basic.musicxml"
    )
    assert root == (FIXTURES / str(plain_row["output_file"])).read_bytes()
    assert row["root_sha256"] == plain_row["root_sha256"]
    result = import_musicxml(path)
    assert isinstance(result, ImportSuccess), getattr(result, "diagnostics", None)
    assert result.provenance is not None
    assert result.provenance.source_filename == output_file
    assert result.provenance.source_format == "mxl"
    assert result.provenance.raw_sha256 == row["output_sha256"]
    assert result.provenance.root_member == row["root_member"]
    assert result.provenance.root_sha256 == row["root_sha256"]
    assert result.provenance.container_version == "mxl-container@0.1.0"


def test_exporter_identity_is_present_in_every_raw_or_container_root() -> None:
    expected_markers = {
        ("music21", "10.5.0"): b"music21 v.10.5.0",
        ("musicxml", "1.6.1"): b"musicxml 1.6.1",
        ("MuseScore Studio", "4.7.4"): b"MuseScore Studio 4.7.4",
    }
    for row in _rows():
        output_file = row["output_file"]
        assert type(output_file) is str
        root = _root_bytes((FIXTURES / output_file).read_bytes(), row)
        marker = expected_markers[(row["producer"], row["version"])]
        assert marker in root
