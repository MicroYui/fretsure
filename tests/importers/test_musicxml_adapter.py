from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from fretsure.bench.corpus import ir_to_notegraph, notegraph_to_ir
from fretsure.importers import (
    IMPORTER_VERSION,
    ImportCode,
    ImportFailure,
    ImportSuccess,
    import_musicxml,
)
from fretsure.ir import ChordSymbol, Meta, MusicIR, Note, validate_ir

FIXTURES = Path(__file__).parents[1] / "fixtures" / "musicxml"
BASIC = FIXTURES / "supported_basic.musicxml"


def _success(path: Path) -> ImportSuccess:
    result = import_musicxml(path)
    assert isinstance(result, ImportSuccess), getattr(result, "diagnostics", None)
    return result


def _without_provenance(ir: MusicIR) -> MusicIR:
    return replace(ir, meta=replace(ir.meta, source=""))


def test_basic_score_maps_exactly_to_music_ir() -> None:
    result = _success(BASIC)
    source_hash = hashlib.sha256(BASIC.read_bytes()).hexdigest()
    expected_notes = (
        Note(Fraction(0), Fraction(1), 60, "melody"),
        Note(Fraction(2), Fraction(3), 62, "melody"),
        Note(Fraction(5), Fraction(1), 63, "melody"),
        Note(Fraction(6), Fraction(1), 66, "melody"),
    )
    expected_chords = (
        ChordSymbol(Fraction(0), "C", frozenset({0, 4, 7}), 0),
        ChordSymbol(Fraction(4), "G7", frozenset({2, 5, 7, 11}), 7),
    )
    assert result.ir.notes == expected_notes
    assert result.ir.chords == expected_chords
    assert result.ir.meta == Meta(
        "C",
        (4, 4),
        96.0,
        (
            f"filename={BASIC.name};format=musicxml;sha256={source_hash};"
            f"root_sha256={source_hash};importer={IMPORTER_VERSION}"
        ),
        "Importer Etude",
        "CC0-1.0",
        duration_beats=Fraction(8),
    )
    assert result.ir.meta.duration_beats == Fraction(8)
    assert result.importer_version == IMPORTER_VERSION == "musicxml@0.2.0"
    assert max(note.onset + note.duration for note in result.ir.notes) == Fraction(7)
    assert validate_ir(result.ir) == []
    assert {note.voice for note in result.ir.notes} == {"melody"}


def test_major_minor_keys_accidentals_and_sound_only_tempo() -> None:
    result = _success(FIXTURES / "supported_minor.musicxml")
    assert result.ir.meta.key == "Cm"
    assert result.ir.meta.tempo_bpm == 80.0
    assert [note.pitch for note in result.ir.notes] == [60, 61, 63, 67]
    assert result.ir.chords == (ChordSymbol(Fraction(0), "Cm", frozenset({0, 3, 7}), 0),)


def test_common_harmony_kinds_are_symbols_not_notes() -> None:
    result = _success(FIXTURES / "supported_harmonies.musicxml")
    assert [
        (chord.onset, chord.symbol, chord.pitch_classes, chord.root_pc)
        for chord in result.ir.chords
    ] == [
        (Fraction(0), "C", frozenset({0, 4, 7}), 0),
        (Fraction(4), "Am", frozenset({0, 4, 9}), 9),
        (Fraction(8), "G7", frozenset({2, 5, 7, 11}), 7),
        (Fraction(12), "Fmaj7", frozenset({0, 4, 5, 9}), 5),
    ]
    assert all(note.voice == "melody" for note in result.ir.notes)


@pytest.mark.parametrize(
    ("kind", "suffix"),
    [
        ("major", ""),
        ("minor", "m"),
        ("augmented", "+"),
        ("diminished", "dim"),
        ("dominant", "7"),
        ("major-seventh", "maj7"),
        ("minor-seventh", "m7"),
        ("diminished-seventh", "dim7"),
        ("augmented-seventh", "+7"),
        ("half-diminished", "m7b5"),
        ("major-minor", "m(maj7)"),
        ("major-sixth", "6"),
        ("minor-sixth", "m6"),
        ("dominant-ninth", "9"),
        ("major-ninth", "maj9"),
        ("minor-ninth", "m9"),
        ("dominant-11th", "11"),
        ("major-11th", "maj11"),
        ("minor-11th", "m11"),
        ("dominant-13th", "13"),
        ("major-13th", "maj13"),
        ("minor-13th", "m13"),
        ("suspended-second", "sus2"),
        ("suspended-fourth", "sus4"),
        ("power", "5"),
    ],
)
def test_every_whitelisted_harmony_kind_roundtrips(tmp_path: Path, kind: str, suffix: str) -> None:
    raw = BASIC.read_text(encoding="utf-8")
    rewritten = raw.replace("<kind>major</kind>", f"<kind>{kind}</kind>").replace(
        "<kind>dominant</kind>", f"<kind>{kind}</kind>"
    )
    path = tmp_path / f"{kind}.musicxml"
    path.write_text(rewritten, encoding="utf-8")
    result = _success(path)
    assert [chord.symbol for chord in result.ir.chords] == [f"C{suffix}", f"G{suffix}"]
    assert validate_ir(result.ir) == []


def test_tie_start_continue_stop_becomes_one_exact_note() -> None:
    result = _success(FIXTURES / "supported_tie_continue.musicxml")
    assert result.ir.notes == (Note(Fraction(0), Fraction(12), 60, "melody"),)
    assert result.ir.meta.duration_beats == Fraction(12)


def test_divisions_scaling_does_not_change_musical_ir(tmp_path: Path) -> None:
    root = ET.fromstring(BASIC.read_bytes())
    divisions = root.find(".//divisions")
    assert divisions is not None
    divisions.text = str(int(divisions.text or "0") * 2)
    for duration in root.findall(".//duration"):
        duration.text = str(int(duration.text or "0") * 2)
    scaled = tmp_path / "scaled.musicxml"
    ET.ElementTree(root).write(scaled, encoding="utf-8", xml_declaration=True)
    assert _without_provenance(_success(scaled).ir) == _without_provenance(_success(BASIC).ir)


def test_fractional_decimal_timeline_remains_exact_after_music21(tmp_path: Path) -> None:
    raw = BASIC.read_text(encoding="utf-8")
    raw = raw.replace("<divisions>4</divisions>", "<divisions>1.0</divisions>")
    raw = raw.replace("<duration>4</duration>", "<duration>0.123456789</duration>", 1)
    raw = raw.replace("<duration>4</duration>", "<duration>0.876543211</duration>", 1)
    raw = raw.replace("<duration>8</duration>", "<duration>3.0</duration>", 1)
    raw = raw.replace("<duration>4</duration>", "<duration>1.0</duration>")
    raw = raw.replace(
        "</note>\n      <note><rest/>",
        "</note>\n      <harmony><root><root-step>F</root-step></root>"
        "<kind>major</kind></harmony>\n      <note><rest/>",
        1,
    )
    path = tmp_path / "fractional-decimal.musicxml"
    path.write_text(raw, encoding="utf-8")

    result = _success(path)

    exact = Fraction(123456789, 1_000_000_000)
    assert result.ir.notes[0] == Note(Fraction(0), exact, 60, "melody")
    assert result.ir.chords[1].onset == exact
    assert result.ir.chords[1].symbol == "F"


def test_long_note_and_equivalent_tied_fragments_are_identical() -> None:
    long_ir = _success(FIXTURES / "metamorphic_long.musicxml").ir
    tied_ir = _success(FIXTURES / "metamorphic_tied.musicxml").ir
    assert _without_provenance(long_ir) == _without_provenance(tied_ir)
    assert long_ir.meta.duration_beats == tied_ir.meta.duration_beats == Fraction(4)


def test_layout_and_visual_notation_do_not_change_ir(tmp_path: Path) -> None:
    raw = BASIC.read_text(encoding="utf-8")
    with_layout = raw.replace(
        '<measure number="1">',
        '<measure number="1" width="480"><print new-system="yes"><system-layout/></print>',
    ).replace(
        "<type>quarter</type>",
        "<type>quarter</type><stem>up</stem><notehead>normal</notehead>"
        '<beam number="1">begin</beam>',
        1,
    )
    path = tmp_path / "layout.musicxml"
    path.write_text(with_layout, encoding="utf-8")
    assert _without_provenance(_success(path).ir) == _without_provenance(_success(BASIC).ir)


def test_official_namespace_and_musicxml_31_are_supported(tmp_path: Path) -> None:
    raw = BASIC.read_text(encoding="utf-8")
    namespaced = raw.replace(
        '<score-partwise version="4.0">',
        '<score-partwise xmlns="http://www.musicxml.org/ns/musicxml" version="3.1">',
    )
    path = tmp_path / "namespaced.xml"
    path.write_text(namespaced, encoding="utf-8")
    assert _without_provenance(_success(path).ir) == _without_provenance(_success(BASIC).ir)


def test_repeated_parse_is_frozen_and_notegraph_json_roundtrips() -> None:
    first = _success(BASIC)
    second = _success(BASIC)
    assert first == second
    restored = notegraph_to_ir(json.loads(json.dumps(ir_to_notegraph(first.ir))))
    assert restored == first.ir


def test_music21_semantic_disagreement_is_typed_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fretsure.importers import _music21_adapter

    real_midi_number = _music21_adapter._midi_number

    def disagree(pitch: object) -> int:
        return real_midi_number(pitch) + 1  # type: ignore[arg-type]

    monkeypatch.setattr(_music21_adapter, "_midi_number", disagree)

    result = import_musicxml(BASIC)

    assert isinstance(result, ImportFailure)
    assert [diagnostic.code for diagnostic in result.diagnostics] == [
        ImportCode.ADAPTER_ERROR
    ]
    assert not hasattr(result, "ir")


def test_warnings_never_contain_errors_on_success() -> None:
    result = _success(BASIC)
    assert all(
        warning.code not in {ImportCode.IR_INVALID, ImportCode.ADAPTER_ERROR}
        for warning in result.warnings
    )
