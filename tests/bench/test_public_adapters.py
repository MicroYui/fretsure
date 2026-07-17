from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import cast

import pytest

from fretsure.bench.corpus_sources import (
    SourceStatus,
    source_census_from_dict,
)
from fretsure.bench.normalizers import (
    ArrangementSourceFormat,
    PublicArrangementLayer,
    normalize_arrangement_source,
)
from fretsure.bench.public_adapters import arrangement_source_from_pinned_bytes
from fretsure.importers.contracts import DEFAULT_LIMITS, ImportCode, ImportFailure
from fretsure.importers.midi import import_midi_bytes

_ROOT = Path(__file__).resolve().parents[2]
_CENSUS_PATH = _ROOT / "data" / "benchmark" / "source-census.json"
_SOURCES_DIR = _ROOT / "data" / "benchmark" / "sources"

_EXPECTED = {
    "mutopia-bach-bwv774": {
        "streams": (("part-name:one", 247), ("part-name:two", 248)),
        "roles": {"melody": 247, "bass": 248},
        "key": "D",
        "meter": (3, 8),
        "tempo": 80.0,
        "duration": Fraction(89),
        "notes": 495,
    },
    "mutopia-bach-bwv775": {
        "streams": (("part-name:one", 243), ("part-name:two", 200)),
        "roles": {"melody": 243, "bass": 200},
        "key": "Dm",
        "meter": (3, 8),
        "tempo": 90.0,
        "duration": Fraction(78),
        "notes": 443,
    },
    "openscore-lieder-beethoven-op48-5": {
        "streams": (
            ("part:P2-Staff1", 88),
            ("part:P2-Staff2", 80),
            ("part:Singstimme Voice", 30),
        ),
        "roles": {"harmony": 88, "bass": 80, "melody": 30},
        "key": "key-signature:+0",
        "meter": (2, 2),
        "tempo": 140.0,
        "duration": Fraction(72),
        "notes": 198,
    },
}


def _census():
    return source_census_from_dict(json.loads(_CENSUS_PATH.read_text(encoding="utf-8")))


@pytest.mark.parametrize("source_id", tuple(_EXPECTED))
def test_pinned_public_source_adapter_and_explicit_role_map(source_id: str) -> None:
    census = _census()
    source = next(value for value in census.sources if value.source_id == source_id)
    assert source.status is SourceStatus.INCLUDED
    assert source.cache_name is not None
    assert source.expected_sha256 is not None
    assert source.source_format is not None
    data = (_SOURCES_DIR / source.cache_name).read_bytes()
    assert hashlib.sha256(data).hexdigest() == source.expected_sha256

    adapted = arrangement_source_from_pinned_bytes(
        data,
        source_format=cast(ArrangementSourceFormat, source.source_format),
        source_identity=source.source_id,
        license_expression=source.license.expression,
    )
    expected = _EXPECTED[source_id]
    assert (
        tuple((stream.selector, len(stream.notes)) for stream in adapted.streams)
        == (expected["streams"])
    )
    assert adapted.meta.key == expected["key"]
    assert adapted.meta.time_sig == expected["meter"]
    assert adapted.meta.tempo_bpm == expected["tempo"]
    assert adapted.meta.duration_beats == expected["duration"]

    normalized = normalize_arrangement_source(
        adapted,
        source.role_map,
        layer=cast(PublicArrangementLayer, source.layer),
    )
    assert normalized.role_map == source.role_map
    assert len(normalized.ir.notes) == expected["notes"]
    assert Counter(note.voice for note in normalized.ir.notes) == expected["roles"]
    assert normalized.ir.chords == ()


def test_source_cache_contains_only_the_three_included_pins() -> None:
    census = _census()
    expected_names = {
        source.cache_name for source in census.sources if source.status is SourceStatus.INCLUDED
    }

    assert {path.name for path in _SOURCES_DIR.iterdir()} == expected_names
    assert None not in expected_names


@pytest.mark.parametrize(
    "filename",
    ("mutopia-bach-bwv774.mid", "mutopia-bach-bwv775.mid"),
)
def test_benchmark_midi_adapter_does_not_widen_the_public_importer(filename: str) -> None:
    result = import_midi_bytes((_SOURCES_DIR / filename).read_bytes(), filename)

    assert isinstance(result, ImportFailure)
    assert ImportCode.MULTIPLE_NOTE_BEARING_STREAMS in {
        diagnostic.code for diagnostic in result.diagnostics
    }


def test_explicit_chord_symbols_are_not_reclassified_as_sounding_notes() -> None:
    data = (_ROOT / "tests" / "fixtures" / "musicxml" / "supported_harmonies.musicxml").read_bytes()

    adapted = arrangement_source_from_pinned_bytes(
        data,
        source_format="musicxml",
        source_identity="supported-harmonies",
        license_expression="CC0-1.0",
    )

    assert [(stream.selector, len(stream.notes)) for stream in adapted.streams] == [
        ("part:Melody", 4)
    ]
    assert len(adapted.chords) == 4
    assert [chord.symbol for chord in adapted.chords] == ["C", "Am", "G7", "Fmaj7"]


def test_midi_adapter_preserves_exact_ticks_and_merges_parser_ties() -> None:
    data = (
        _ROOT / "tests" / "fixtures" / "midi" / "producers" / "musescore-4.7.4-melody_only.mid"
    ).read_bytes()

    adapted = arrangement_source_from_pinned_bytes(
        data,
        source_format="midi",
        source_identity="musescore-exact-ticks",
        license_expression="CC0-1.0",
    )

    assert len(adapted.streams) == 1
    assert adapted.streams[0].selector == "part-name:Melody"
    assert [(note.onset, note.duration, note.pitch) for note in adapted.streams[0].notes] == [
        (Fraction(0), Fraction(479, 480), 60),
        (Fraction(2), Fraction(1439, 480), 62),
        (Fraction(5), Fraction(479, 480), 63),
        (Fraction(6), Fraction(479, 480), 66),
    ]
    assert adapted.meta.duration_beats == Fraction(7)

    with pytest.raises(ValueError, match="adapter limit"):
        arrangement_source_from_pinned_bytes(
            data,
            source_format="midi",
            source_identity="musescore-exact-ticks",
            license_expression="CC0-1.0",
            limits=replace(DEFAULT_LIMITS, max_midi_bytes=len(data) - 1),
        )
