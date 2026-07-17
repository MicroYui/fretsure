from dataclasses import replace
from fractions import Fraction as F
from pathlib import Path

import pytest

from fretsure.bench.contracts import BenchmarkContractError
from fretsure.bench.normalizers import (
    PUBLIC_CLASSICAL_NORMALIZATION,
    PUBLIC_LEADSHEET_NORMALIZATION,
    PUBLIC_MIDI_NORMALIZATION,
    ArrangementNote,
    ArrangementSource,
    ArrangementStream,
    normalize_arrangement_source,
    normalize_public_arrangement,
    normalize_public_classical,
    normalize_public_leadsheet,
    normalize_public_midi,
)
from fretsure.importers import (
    MIDI_IMPORTER_VERSION,
    MUSICXML_IMPORTER_VERSION,
    ImportCode,
    ImportFailure,
    ImportProvenance,
    ImportSuccess,
    import_musicxml_bytes,
    import_score,
)
from fretsure.ir import MAX_IR_NOTES, ChordSymbol, Meta, MusicIR, Note

_FIXTURES = Path(__file__).parents[1] / "fixtures"

_MULTIPART_MUSICXML = b"""<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <work><work-title>Voice and Piano</work-title></work>
  <identification><rights>CC0-1.0</rights></identification>
  <part-list>
    <score-part id="P1"><part-name>Voice</part-name></score-part>
    <score-part id="P2"><part-name>Piano</part-name></score-part>
  </part-list>
  <part id="P1"><measure number="1">
    <attributes><divisions>1</divisions><key><fifths>0</fifths><mode>major</mode></key>
      <time><beats>4</beats><beat-type>4</beat-type></time></attributes>
    <direction><direction-type><metronome><beat-unit>quarter</beat-unit>
      <per-minute>96</per-minute></metronome></direction-type><sound tempo="96"/></direction>
    <note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration>
      <voice>1</voice><type>whole</type></note>
  </measure></part>
  <part id="P2"><measure number="1">
    <attributes><divisions>1</divisions><key><fifths>0</fifths><mode>major</mode></key>
      <time><beats>4</beats><beat-type>4</beat-type></time></attributes>
    <note><pitch><step>E</step><octave>5</octave></pitch><duration>4</duration>
      <voice>1</voice><type>whole</type></note>
    <backup><duration>4</duration></backup>
    <note><pitch><step>C</step><octave>3</octave></pitch><duration>4</duration>
      <voice>2</voice><type>whole</type></note>
  </measure></part>
</score-partwise>
"""


def _musicxml_success(ir: MusicIR | None = None) -> ImportSuccess:
    source_ir = ir or MusicIR(
        notes=(
            Note(F(0), F(1), 72, "melody"),
            Note(F(0), F(1), 48, "bass"),
            Note(F(0), F(1), 60, "harmony"),
        ),
        chords=(ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 0),),
        meta=Meta("C", (4, 4), 96.0, "fixture", "roles", "CC0-1.0", F(1)),
    )
    digest = "1" * 64
    return ImportSuccess(
        source_ir,
        (),
        MUSICXML_IMPORTER_VERSION,
        digest,
        ImportProvenance(
            "roles.musicxml",
            "musicxml",
            digest,
            None,
            digest,
            None,
        ),
    )


def _identity_role_map() -> tuple[tuple[str, str], ...]:
    return (
        ("voice:bass", "bass"),
        ("voice:harmony", "harmony"),
        ("voice:melody", "melody"),
    )


def _multipart_source(*, source_format: str = "musicxml") -> ArrangementSource:
    return ArrangementSource(
        streams=(
            ArrangementStream(
                "part:P1/voice:1",
                (ArrangementNote(F(0), F(4), 60),),
            ),
            ArrangementStream(
                "part:P2/voice:1",
                (ArrangementNote(F(0), F(4), 76),),
            ),
            ArrangementStream(
                "part:P2/voice:2",
                (ArrangementNote(F(0), F(4), 48),),
            ),
        ),
        chords=(),
        meta=Meta(
            "C",
            (4, 4),
            96.0,
            "OpenScore fixture",
            "Voice and Piano",
            "CC0-1.0",
            F(4),
        ),
        source_format=source_format,  # type: ignore[arg-type]
    )


def test_public_leadsheet_normalizes_imported_fixture_with_explicit_map() -> None:
    imported = import_score(_FIXTURES / "musicxml" / "supported_harmonies.musicxml")
    assert isinstance(imported, ImportSuccess)

    result = normalize_public_leadsheet(
        imported,
        (("voice:melody", "melody"),),
    )

    assert result.ir == imported.ir
    assert result.role_map == (("voice:melody", "melody"),)
    assert result.normalization == PUBLIC_LEADSHEET_NORMALIZATION
    assert len(result.ir.chords) == 4


def test_classical_and_leadsheet_paths_have_distinct_normalization_records() -> None:
    imported = _musicxml_success()

    leadsheet = normalize_public_arrangement(
        imported,
        _identity_role_map(),
        layer="public_leadsheet",
    )
    classical = normalize_public_classical(imported, _identity_role_map())

    assert leadsheet.normalization == PUBLIC_LEADSHEET_NORMALIZATION
    assert classical.normalization == PUBLIC_CLASSICAL_NORMALIZATION
    assert leadsheet.normalization != classical.normalization


def test_explicit_role_map_can_relabel_only_the_named_source_voices() -> None:
    imported = _musicxml_success()

    result = normalize_public_leadsheet(
        imported,
        (
            ("voice:bass", "harmony"),
            ("voice:harmony", "bass"),
            ("voice:melody", "melody"),
        ),
    )

    by_pitch = {note.pitch: note.voice for note in result.ir.notes}
    assert by_pitch == {48: "harmony", 60: "bass", 72: "melody"}
    assert result.ir.chords == imported.ir.chords


@pytest.mark.parametrize(
    ("role_map", "message"),
    [
        ((), "explicit role map"),
        (
            (("voice:bass", "bass"), ("voice:melody", "melody")),
            "cover every source voice",
        ),
        (
            (
                ("voice:bass", "bass"),
                ("voice:harmony", "harmony"),
                ("voice:melody", "melody"),
                ("voice:other", "harmony"),
            ),
            "cover every source voice",
        ),
        (
            (
                ("voice:bass", "bass"),
                ("voice:bass", "harmony"),
                ("voice:harmony", "harmony"),
                ("voice:melody", "melody"),
            ),
            "one target",
        ),
        (
            (
                ("voice:melody", "melody"),
                ("voice:harmony", "harmony"),
                ("voice:bass", "bass"),
            ),
            "canonically ordered",
        ),
        (
            (
                ("voice:bass", "bass"),
                ("voice:harmony", "inner"),
                ("voice:melody", "melody"),
            ),
            "target role",
        ),
    ],
)
def test_role_map_is_exact_explicit_canonical_and_complete(
    role_map: tuple[tuple[str, str], ...],
    message: str,
) -> None:
    with pytest.raises(BenchmarkContractError, match=message):
        normalize_public_leadsheet(_musicxml_success(), role_map)


def test_role_map_rejection_does_not_fall_back_to_voice_or_pitch_guessing() -> None:
    imported = _musicxml_success()

    with pytest.raises(BenchmarkContractError, match="cover every source voice"):
        normalize_public_leadsheet(
            imported,
            (("voice:melody", "melody"),),
        )


def test_real_multipart_musicxml_uses_explicit_stream_seam_not_public_importer() -> None:
    public_result = import_musicxml_bytes(_MULTIPART_MUSICXML, "voice-piano.musicxml")
    assert isinstance(public_result, ImportFailure)
    assert ImportCode.MULTIPLE_NOTE_BEARING_PARTS in {
        diagnostic.code for diagnostic in public_result.diagnostics
    }

    normalized = normalize_arrangement_source(
        _multipart_source(),
        (
            ("part:P1/voice:1", "melody"),
            ("part:P2/voice:1", "harmony"),
            ("part:P2/voice:2", "bass"),
        ),
        layer="public_classical",
    )

    # The piano upper voice is higher than the vocal line.  The checked-in map,
    # not a top-pitch heuristic, still makes P1 the melody.
    assert {note.pitch: note.voice for note in normalized.ir.notes} == {
        48: "bass",
        60: "melody",
        76: "harmony",
    }
    assert normalized.normalization == PUBLIC_CLASSICAL_NORMALIZATION


def test_multipart_stream_map_must_cover_every_part_voice() -> None:
    with pytest.raises(BenchmarkContractError, match="cover every source voice"):
        normalize_arrangement_source(
            _multipart_source(),
            (
                ("part:P1/voice:1", "melody"),
                ("part:P2/voice:2", "bass"),
            ),
            layer="public_classical",
        )


def test_mxl_adapter_seam_has_same_explicit_role_contract() -> None:
    result = normalize_arrangement_source(
        _multipart_source(source_format="mxl"),
        (
            ("part:P1/voice:1", "melody"),
            ("part:P2/voice:1", "harmony"),
            ("part:P2/voice:2", "bass"),
        ),
        layer="public_classical",
    )

    assert result.role_map[0] == ("part:P1/voice:1", "melody")


def test_arrangement_source_uses_existing_ir_note_limit_before_flattening() -> None:
    note = ArrangementNote(F(0), F(1), 60)
    source = ArrangementSource(
        (ArrangementStream("part:P1/voice:1", (note,) * (MAX_IR_NOTES + 1)),),
        (),
        Meta("C", (4, 4), 96.0, "fixture", "large", "CC0-1.0"),
        "musicxml",
    )

    with pytest.raises(BenchmarkContractError, match=f"count exceeds {MAX_IR_NOTES}"):
        normalize_arrangement_source(
            source,
            (("part:P1/voice:1", "melody"),),
            layer="public_classical",
        )


def test_arrangement_stream_order_is_canonical_not_semantically_guessed() -> None:
    source = _multipart_source()
    reversed_source = replace(source, streams=tuple(reversed(source.streams)))

    with pytest.raises(BenchmarkContractError, match="canonically ordered"):
        normalize_arrangement_source(
            reversed_source,
            (
                ("part:P1/voice:1", "melody"),
                ("part:P2/voice:1", "harmony"),
                ("part:P2/voice:2", "bass"),
            ),
            layer="public_classical",
        )


def test_invalid_relabelled_ir_is_rejected_after_mapping() -> None:
    with pytest.raises(BenchmarkContractError, match="melody_polyphony"):
        normalize_public_leadsheet(
            _musicxml_success(),
            (
                ("voice:bass", "melody"),
                ("voice:harmony", "harmony"),
                ("voice:melody", "melody"),
            ),
        )


def test_public_midi_uses_only_the_existing_strict_importer_success() -> None:
    positive = import_score(_FIXTURES / "midi" / "producers" / "music21-10.5.0-melody_only.mid")
    assert isinstance(positive, ImportSuccess)

    result = normalize_public_midi(positive, (("voice:melody", "melody"),))

    assert positive.importer_version == MIDI_IMPORTER_VERSION == "midi@0.1.0"
    assert result.normalization == PUBLIC_MIDI_NORMALIZATION
    assert {note.voice for note in result.ir.notes} == {"melody"}
    assert result.ir.chords == ()

    unsupported = import_score(
        _FIXTURES / "midi" / "producers" / "music21-10.5.0-supported_basic.mid"
    )
    assert isinstance(unsupported, ImportFailure)
    with pytest.raises(BenchmarkContractError, match="ImportSuccess"):
        normalize_public_midi(unsupported, (("voice:melody", "melody"),))


def test_public_midi_rejects_non_strict_importer_stamp() -> None:
    imported = import_score(_FIXTURES / "midi" / "producers" / "music21-10.5.0-melody_only.mid")
    assert isinstance(imported, ImportSuccess)

    with pytest.raises(BenchmarkContractError, match="midi@0.1.0"):
        normalize_public_midi(
            replace(imported, importer_version="benchmark-midi@0.1.0"),
            (("voice:melody", "melody"),),
        )


def test_midi_normalization_never_infers_harmony_from_notes() -> None:
    imported = import_score(_FIXTURES / "midi" / "producers" / "musescore-4.7.4-melody_only.mid")
    assert isinstance(imported, ImportSuccess)

    result = normalize_public_midi(imported, (("voice:melody", "melody"),))

    assert result.ir.chords == ()
    assert not any(note.voice in {"bass", "harmony"} for note in result.ir.notes)


@pytest.mark.parametrize(
    ("normalizer", "imported", "message"),
    [
        (
            normalize_public_midi,
            _musicxml_success(),
            "source format",
        ),
        (
            normalize_public_classical,
            None,
            "ImportSuccess",
        ),
    ],
)
def test_normalizer_rejects_wrong_source_contract(
    normalizer: object,
    imported: object,
    message: str,
) -> None:
    callable_normalizer = normalizer
    assert callable(callable_normalizer)
    with pytest.raises(BenchmarkContractError, match=message):
        callable_normalizer(imported, (("voice:melody", "melody"),))


def test_unified_normalizer_rejects_non_public_layer() -> None:
    with pytest.raises(BenchmarkContractError, match="public arrangement layer"):
        normalize_public_arrangement(
            _musicxml_success(),
            _identity_role_map(),
            layer="procedural",  # type: ignore[arg-type]
        )
