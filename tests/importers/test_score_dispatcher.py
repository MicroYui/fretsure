from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from fretsure.importers import (
    DEFAULT_LIMITS,
    SCORE_FORMAT_REGISTRY,
    SCORE_INPUT_VERSION,
    ImportCode,
    ImportFailure,
    ImportSuccess,
    import_score,
    import_score_bytes,
    validate_score_filename,
)
from fretsure.importers._midi_preflight import (
    MIDINoteEvent,
    MIDIPreflightMetadata,
    build_canonical_midi,
)
from fretsure.metrics.fidelity import faithfulness_dimensions


def _midi() -> bytes:
    metadata = MIDIPreflightMetadata(
        format_type=0,
        ticks_per_quarter=480,
        tempo_microseconds_per_quarter=500_000,
        tempo_bpm=120.0,
        time_sig=(4, 4),
        key="C",
        key_fifths=0,
        key_mode=0,
        title="",
        rights="unprovided",
        duration_ticks=480,
        duration_beats=Fraction(1),
        note_track=0,
        note_channel=0,
        note_events=(
            MIDINoteEvent(Fraction(0), Fraction(1), 60, 0, 480, 0, 0),
        ),
    )
    return build_canonical_midi(metadata)


def _supported_basic_melody_midi() -> bytes:
    metadata = MIDIPreflightMetadata(
        format_type=0,
        ticks_per_quarter=480,
        tempo_microseconds_per_quarter=625_000,
        tempo_bpm=96.0,
        time_sig=(4, 4),
        key="C",
        key_fifths=0,
        key_mode=0,
        title="",
        rights="unprovided",
        duration_ticks=3_840,
        duration_beats=Fraction(8),
        note_track=0,
        note_channel=0,
        note_events=(
            MIDINoteEvent(Fraction(0), Fraction(1), 60, 0, 480, 0, 0),
            MIDINoteEvent(Fraction(2), Fraction(3), 62, 960, 1_440, 0, 0),
            MIDINoteEvent(Fraction(5), Fraction(1), 63, 2_400, 480, 0, 0),
            MIDINoteEvent(Fraction(6), Fraction(1), 66, 2_880, 480, 0, 0),
        ),
    )
    return build_canonical_midi(metadata)


def test_score_registry_is_exact_versioned_and_immutable() -> None:
    assert SCORE_INPUT_VERSION == "score-input@0.1.0"
    assert dict(SCORE_FORMAT_REGISTRY) == {
        "musicxml": "musicxml@0.3.0",
        "mxl": "musicxml@0.3.0",
        "midi": "midi@0.1.0",
    }

    with pytest.raises(TypeError):
        SCORE_FORMAT_REGISTRY["midi"] = "forged"  # type: ignore[index]


@pytest.mark.parametrize(
    ("filename", "suffix"),
    [
        ("score.musicxml", ".musicxml"),
        ("score.XML", ".xml"),
        ("score.MXL", ".mxl"),
        ("score.mid", ".mid"),
        ("score.MIDI", ".midi"),
    ],
)
def test_validate_score_filename_supports_every_registered_suffix(
    filename: str,
    suffix: str,
) -> None:
    assert validate_score_filename(filename) == suffix


def test_validate_score_filename_reports_the_complete_suffix_set() -> None:
    result = validate_score_filename("score.pdf")

    assert isinstance(result, ImportFailure)
    assert result.diagnostics[0].code is ImportCode.UNSUPPORTED_FILE_TYPE
    assert ".musicxml, .xml, .mxl, .mid, or .midi" in result.diagnostics[0].message


def test_score_bytes_dispatches_midi_and_preserves_actual_importer_stamp() -> None:
    result = import_score_bytes(_midi(), "score.mid")

    assert isinstance(result, ImportSuccess)
    assert result.importer_version == SCORE_FORMAT_REGISTRY["midi"]
    assert result.provenance is not None
    assert result.provenance.source_format == "midi"


def test_score_path_dispatch_matches_bytes(tmp_path: Path) -> None:
    path = tmp_path / "score.mid"
    raw = _midi()
    path.write_bytes(raw)

    assert import_score(path) == import_score_bytes(raw, path.name)


def test_score_dispatch_rejects_wrong_bytes_before_any_format_guess() -> None:
    result = import_score_bytes(b"not midi", "score.mid", limits=DEFAULT_LIMITS)

    assert isinstance(result, ImportFailure)
    assert result.diagnostics[0].code is ImportCode.MALFORMED_MIDI


def test_cross_format_same_melody_differs_only_in_public_source_evidence() -> None:
    musicxml_path = Path("tests/fixtures/musicxml/supported_basic.musicxml")
    musicxml = import_score(musicxml_path)
    midi = import_score_bytes(_supported_basic_melody_midi(), "supported_basic.mid")

    assert isinstance(musicxml, ImportSuccess)
    assert isinstance(midi, ImportSuccess)
    musicxml_melody = tuple(
        (note.onset, note.duration, note.pitch, note.voice)
        for note in musicxml.ir.notes
        if note.voice == "melody"
    )
    midi_melody = tuple(
        (note.onset, note.duration, note.pitch, note.voice)
        for note in midi.ir.notes
    )
    assert midi_melody == musicxml_melody
    assert midi.ir.meta.key == musicxml.ir.meta.key == "C"
    assert midi.ir.meta.time_sig == musicxml.ir.meta.time_sig == (4, 4)
    assert midi.ir.meta.tempo_bpm == musicxml.ir.meta.tempo_bpm == 96.0
    assert midi.ir.meta.duration_beats == musicxml.ir.meta.duration_beats == Fraction(8)

    assert musicxml.ir.chords and midi.ir.chords == ()
    assert faithfulness_dimensions(musicxml.ir) == ("melody", "bass_root", "harmony")
    assert faithfulness_dimensions(midi.ir) == ("melody",)
    assert musicxml.provenance is not None and midi.provenance is not None
    assert musicxml.provenance.source_format == "musicxml"
    assert midi.provenance.source_format == "midi"
    assert musicxml.importer_version == "musicxml@0.3.0"
    assert midi.importer_version == "midi@0.1.0"
    assert musicxml.ir.meta.license == "CC0-1.0"
    assert midi.ir.meta.license == "unprovided"
