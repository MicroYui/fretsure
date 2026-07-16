"""Compatibility/provenance gate over unedited real-exporter output."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fretsure.importers import ImportCode, ImportFailure, ImportSuccess, import_musicxml

FIXTURES = Path(__file__).parents[1] / "fixtures" / "producers"


def _manifest() -> dict[str, object]:
    return json.loads((FIXTURES / "provenance.json").read_text(encoding="utf-8"))


def test_fixture_set_separates_library_success_from_notation_app_evidence() -> None:
    fixtures = _manifest()["fixtures"]
    assert isinstance(fixtures, list)
    positive = [row for row in fixtures if row["expected"] == "success"]
    assert {
        (row["producer"], row["version"], row["producer_class"])
        for row in positive
    } == {
        ("music21", "10.5.0", "symbolic-music-toolkit"),
        ("musicxml", "1.6.1", "object-model-library"),
    }
    notation_apps = [
        row for row in fixtures if row["producer_class"] == "notation-application"
    ]
    assert [(row["producer"], row["version"], row["expected"]) for row in notation_apps] == [
        ("MuseScore Studio", "4.7.4", "UNSUPPORTED_KEY")
    ]


def test_producer_hash_license_and_expected_result_are_frozen() -> None:
    fixtures = _manifest()["fixtures"]
    assert isinstance(fixtures, list)
    for row in fixtures:
        path = FIXTURES / row["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == row["sha256"]
        assert row["score_license"] == "CC0-1.0"
        first = import_musicxml(path)
        second = import_musicxml(path)
        assert first == second
        if row["expected"] == "success":
            assert isinstance(first, ImportSuccess), getattr(first, "diagnostics", None)
            assert first.ir.meta.license == "CC0-1.0"
        else:
            assert isinstance(first, ImportFailure)
            assert {diagnostic.code for diagnostic in first.diagnostics} == {
                ImportCode(row["expected"])
            }


def test_exporter_identity_is_present_in_each_raw_file() -> None:
    expected_markers = {
        "music21-10.5.0.musicxml": b"music21 v.10.5.0",
        "musicxml-1.6.1.musicxml": b"musicxml 1.6.1",
        "musescore-4.7.4.musicxml": b"MuseScore Studio 4.7.4",
    }
    for filename, marker in expected_markers.items():
        assert marker in (FIXTURES / filename).read_bytes()
