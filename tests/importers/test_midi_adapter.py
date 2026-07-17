from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from importlib import import_module as real_import_module

import pytest

import fretsure.importers._music21_midi_adapter as adapter
from fretsure.importers._midi_preflight import (
    MIDINoteEvent,
    MIDIPreflightMetadata,
    build_canonical_midi,
)


def _metadata() -> MIDIPreflightMetadata:
    return MIDIPreflightMetadata(
        format_type=1,
        ticks_per_quarter=480,
        tempo_microseconds_per_quarter=625_000,
        tempo_bpm=96.0,
        time_sig=(4, 4),
        key="C",
        key_fifths=0,
        key_mode=0,
        title="",
        rights="unprovided",
        duration_ticks=3_360,
        duration_beats=Fraction(7),
        note_track=0,
        note_channel=0,
        note_events=(
            MIDINoteEvent(Fraction(0), Fraction(479, 480), 60, 0, 479, 0, 0),
            # music21 splits this exact cross-bar note into tied segments.  The
            # adapter must merge those segments before comparing the raw event.
            MIDINoteEvent(Fraction(2), Fraction(1_439, 480), 62, 960, 1_439, 0, 0),
            MIDINoteEvent(Fraction(5), Fraction(479, 480), 63, 2_400, 479, 0, 0),
            MIDINoteEvent(Fraction(6), Fraction(479, 480), 66, 2_880, 479, 0, 0),
        ),
    )


def test_music21_crosscheck_preserves_exact_raw_note_timeline() -> None:
    metadata = _metadata()

    adapter.crosscheck_music21_midi(build_canonical_midi(metadata), metadata)


def test_music21_crosscheck_disagreement_fails_closed() -> None:
    metadata = _metadata()
    expected = replace(
        metadata,
        note_events=(
            replace(metadata.note_events[0], pitch=61),
            *metadata.note_events[1:],
        ),
    )

    with pytest.raises(
        adapter.Music21MIDIAdapterError,
        match="disagreement on note count, pitch, onset, or duration",
    ):
        adapter.crosscheck_music21_midi(build_canonical_midi(metadata), expected)


def test_music21_crosscheck_explicitly_disables_post_quantization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = _metadata()
    seen: list[bool] = []
    real_translate = real_import_module("music21.midi.translate")

    class RecordingTranslate:
        @staticmethod
        def midiStringToStream(raw: bytes, *, quantizePost: bool) -> object:
            seen.append(quantizePost)
            return real_translate.midiStringToStream(raw, quantizePost=quantizePost)

    def importing(name: str) -> object:
        if name == "music21.midi.translate":
            return RecordingTranslate()
        return real_import_module(name)

    monkeypatch.setattr(adapter, "import_module", importing)

    adapter.crosscheck_music21_midi(build_canonical_midi(metadata), metadata)

    assert seen == [False]


def test_music21_crosscheck_reports_missing_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = _metadata()

    def missing(_name: str) -> object:
        error = ModuleNotFoundError("No module named 'music21'")
        error.name = "music21"
        raise error

    monkeypatch.setattr(adapter, "import_module", missing)

    with pytest.raises(adapter.MIDIDependencyError):
        adapter.crosscheck_music21_midi(build_canonical_midi(metadata), metadata)
