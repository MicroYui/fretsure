"""Lazy music21 adapter for the frozen MusicXML subset."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from fractions import Fraction
from importlib import import_module
from typing import Protocol, cast

from fretsure.importers._musicxml_preflight import (
    PreflightHarmonyEvent,
    PreflightMetadata,
    PreflightNoteEvent,
)
from fretsure.ir import ChordSymbol, Meta, MusicIR, Note


class MusicXMLDependencyError(Exception):
    def __init__(self, package: str) -> None:
        self.package = package
        super().__init__(package)


class Music21AdapterError(Exception):
    pass


class _M21Tie(Protocol):
    type: str


class _M21Pitch(Protocol):
    midi: int | float
    pitchClass: int
    name: str


class _M21Note(Protocol):
    offset: object
    quarterLength: object
    pitch: _M21Pitch
    tie: _M21Tie | None


class _M21Chord(Protocol):
    offset: object
    pitches: tuple[_M21Pitch, ...]
    chordKind: str

    def root(self) -> _M21Pitch | None: ...


class _M21FlatStream(Protocol):
    def __iter__(self) -> Iterator[object]: ...


class _M21Part(Protocol):
    def flatten(self) -> _M21FlatStream: ...


class _M21Score(Protocol):
    parts: Iterable[object]


@dataclass(frozen=True, slots=True)
class _PendingTie:
    onset: Fraction
    duration: Fraction
    pitch: int


_CHORD_SUFFIX = {
    "major": "",
    "minor": "m",
    "augmented": "+",
    "diminished": "dim",
    "dominant-seventh": "7",
    "major-seventh": "maj7",
    "minor-seventh": "m7",
    "diminished-seventh": "dim7",
    "augmented-seventh": "+7",
    "half-diminished": "m7b5",
    "half-diminished-seventh": "m7b5",
    "major-minor": "m(maj7)",
    "minor-major-seventh": "m(maj7)",
    "major-sixth": "6",
    "minor-sixth": "m6",
    "dominant-ninth": "9",
    "major-ninth": "maj9",
    "minor-ninth": "m9",
    "dominant-11th": "11",
    "major-11th": "maj11",
    "minor-11th": "m11",
    "dominant-13th": "13",
    "major-13th": "maj13",
    "minor-13th": "m13",
    "suspended-second": "sus2",
    "suspended-fourth": "sus4",
    "power": "5",
}


def _midi_number(pitch: _M21Pitch) -> int:
    midi = pitch.midi
    if isinstance(midi, bool) or not isinstance(midi, (int, float)):
        raise Music21AdapterError("music21 returned a non-numeric MIDI pitch")
    rounded = round(midi)
    if float(midi) != float(rounded) or not 0 <= rounded <= 127:
        raise Music21AdapterError(f"music21 returned unsupported MIDI pitch {midi!r}")
    return rounded


def _iter_objects(flat_stream: _M21FlatStream) -> list[object]:
    return list(flat_stream)


def _select_note_part(score: _M21Score, note_class: type[object]) -> _M21Part:
    parts = list(score.parts)
    bearing: list[_M21Part] = []
    for raw_part in parts:
        part = cast(_M21Part, raw_part)
        if any(isinstance(element, note_class) for element in _iter_objects(part.flatten())):
            bearing.append(part)
    if len(bearing) != 1:
        raise Music21AdapterError(
            f"preflight/music21 disagreement: expected one note-bearing part, got {len(bearing)}"
        )
    return bearing[0]


def _adapt_notes(
    objects: list[object],
    note_class: type[object],
    exact_events: tuple[PreflightNoteEvent, ...],
) -> tuple[Note, ...]:
    parsed = [cast(_M21Note, element) for element in objects if isinstance(element, note_class)]
    if len(parsed) != len(exact_events):
        raise Music21AdapterError(
            "preflight/music21 note-count disagreement: "
            f"expected {len(exact_events)}, got {len(parsed)}"
        )

    segments: list[tuple[Fraction, Fraction, int, str | None]] = []
    for index, (note, exact) in enumerate(zip(parsed, exact_events, strict=True)):
        pitch = _midi_number(note.pitch)
        tie_type = note.tie.type if note.tie is not None else None
        if pitch != exact.pitch or tie_type != exact.tie_type:
            raise Music21AdapterError(
                "preflight/music21 note disagreement at event "
                f"{index}: expected pitch/tie {exact.pitch}/{exact.tie_type}, "
                f"got {pitch}/{tie_type}"
            )
        segments.append((exact.onset, exact.duration, pitch, tie_type))

    notes: list[Note] = []
    pending: _PendingTie | None = None
    for onset, duration, pitch, tie_type in segments:
        if duration <= 0:
            raise Music21AdapterError("music21 returned a non-positive note duration")
        if tie_type == "start":
            if pending is not None:
                raise Music21AdapterError("music21 returned overlapping tie starts")
            pending = _PendingTie(onset, duration, pitch)
        elif tie_type == "continue":
            if (
                pending is None
                or pending.pitch != pitch
                or pending.onset + pending.duration != onset
            ):
                raise Music21AdapterError("music21 returned a malformed tie continuation")
            pending = _PendingTie(pending.onset, pending.duration + duration, pitch)
        elif tie_type == "stop":
            if (
                pending is None
                or pending.pitch != pitch
                or pending.onset + pending.duration != onset
            ):
                raise Music21AdapterError("music21 returned a malformed tie stop")
            notes.append(Note(pending.onset, pending.duration + duration, pitch, "melody"))
            pending = None
        elif tie_type is None:
            if pending is not None:
                raise Music21AdapterError("music21 returned a dangling tie start")
            notes.append(Note(onset, duration, pitch, "melody"))
        else:
            raise Music21AdapterError(f"music21 returned unsupported tie type {tie_type!r}")
    if pending is not None:
        raise Music21AdapterError("music21 returned a dangling tie at end of score")
    return tuple(sorted(notes, key=lambda note: (note.onset, note.pitch, note.duration)))


def _adapt_chords(
    objects: list[object],
    chord_class: type[object],
    exact_events: tuple[PreflightHarmonyEvent, ...],
) -> tuple[ChordSymbol, ...]:
    parsed = [cast(_M21Chord, element) for element in objects if isinstance(element, chord_class)]
    if len(parsed) != len(exact_events):
        raise Music21AdapterError(
            "preflight/music21 harmony-count disagreement: "
            f"expected {len(exact_events)}, got {len(parsed)}"
        )

    chords: list[ChordSymbol] = []
    for index, (chord, exact) in enumerate(zip(parsed, exact_events, strict=True)):
        root = chord.root()
        if root is None:
            raise Music21AdapterError("music21 harmony has no root")
        suffix = _CHORD_SUFFIX.get(chord.chordKind)
        if suffix is None:
            raise Music21AdapterError(
                f"preflight/music21 disagreement on harmony kind {chord.chordKind!r}"
            )
        root_name = root.name.replace("-", "b")
        root_pc = int(root.pitchClass)
        symbol = f"{root_name}{suffix}"
        if symbol != exact.symbol or root_pc != exact.root_pc:
            raise Music21AdapterError(
                "preflight/music21 harmony disagreement at event "
                f"{index}: expected {exact.symbol}/{exact.root_pc}, "
                f"got {symbol}/{root_pc}"
            )
        pitch_classes = frozenset(int(pitch.pitchClass) for pitch in chord.pitches)
        if root_pc not in pitch_classes:
            raise Music21AdapterError("music21 harmony pitch classes omit their root")
        chords.append(ChordSymbol(exact.onset, symbol, pitch_classes, root_pc))
    chords.sort(key=lambda chord: (chord.onset, chord.symbol, chord.root_pc))
    if len({chord.onset for chord in chords}) != len(chords):
        raise Music21AdapterError("music21 returned duplicate harmony at one onset")
    return tuple(chords)


def music21_to_ir(
    canonical_xml: bytes,
    *,
    metadata: PreflightMetadata,
    source_filename: str,
    sha256: str,
    importer_version: str,
) -> MusicIR:
    """Parse canonical, DTD-free XML and convert only the frozen typed subset."""

    try:
        music21 = import_module("music21")
        note_module = import_module("music21.note")
        harmony_module = import_module("music21.harmony")
    except ModuleNotFoundError as exc:
        if exc.name is not None and exc.name.split(".", 1)[0] == "music21":
            raise MusicXMLDependencyError("music21") from exc
        raise

    converter = getattr(music21, "converter", None)
    if converter is None:
        raise Music21AdapterError("music21.converter is unavailable")
    try:
        score = cast(
            _M21Score,
            converter.parseData(canonical_xml.decode("utf-8"), format="musicxml"),
        )
    except Exception as exc:
        raise Music21AdapterError(
            f"music21 rejected preflight-approved canonical XML: {type(exc).__name__}: {exc}"
        ) from exc

    note_class = cast(type[object], note_module.Note)
    chord_class = cast(type[object], harmony_module.ChordSymbol)
    part = _select_note_part(score, note_class)
    objects = _iter_objects(part.flatten())
    notes = _adapt_notes(objects, note_class, metadata.note_events)
    chords = _adapt_chords(objects, chord_class, metadata.harmony_events)
    provenance = (
        f"filename={source_filename};sha256={sha256};importer={importer_version}"
    )
    return MusicIR(
        notes,
        chords,
        Meta(
            metadata.key,
            metadata.time_sig,
            metadata.tempo_bpm,
            provenance,
            metadata.title,
            metadata.rights,
            duration_beats=metadata.duration_beats,
        ),
    )
