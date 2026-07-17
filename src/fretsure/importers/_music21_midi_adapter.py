"""Lazy music21 cross-check for strict, first-party MIDI preflight results."""

from __future__ import annotations

import math
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from fractions import Fraction
from importlib import import_module
from typing import Protocol, cast

from fretsure.importers._midi_preflight import MIDIPreflightMetadata


class MIDIDependencyError(Exception):
    """The exact optional MIDI parser dependency is unavailable."""


class Music21MIDIAdapterError(Exception):
    """music21 disagreed with the first-party authoritative event timeline."""


class _Tie(Protocol):
    type: str


class _Pitch(Protocol):
    midi: int | float


class _Note(Protocol):
    offset: object
    quarterLength: object
    pitch: _Pitch
    tie: _Tie | None


class _FlatStream(Protocol):
    def __iter__(self) -> Iterator[object]: ...


class _Part(Protocol):
    def flatten(self) -> _FlatStream: ...


class _Score(Protocol):
    parts: Iterable[object]


class _TranslateModule(Protocol):
    def midiStringToStream(
        self,
        raw: bytes,
        *,
        quantizePost: bool,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class _Segment:
    onset: Fraction
    duration: Fraction
    pitch: int
    tie: str | None


@dataclass(frozen=True, slots=True)
class _PendingTie:
    onset: Fraction
    duration: Fraction
    pitch: int


def _exact_fraction(value: object, *, field: str) -> Fraction:
    if type(value) is int:
        converted = Fraction(value, 1)
    elif type(value) is float:
        raw_float = value
        if not math.isfinite(raw_float):
            raise Music21MIDIAdapterError(f"music21 returned an invalid {field}")
        converted = Fraction(raw_float)
    elif type(value) is Fraction:
        raw_fraction = value
        converted = Fraction(raw_fraction.numerator, raw_fraction.denominator)
    else:
        raise Music21MIDIAdapterError(f"music21 returned a non-rational {field}")
    if (
        converted.denominator <= 0
        or converted.numerator.bit_length() > 63
        or converted.denominator.bit_length() > 63
    ):
        raise Music21MIDIAdapterError(f"music21 returned an out-of-range {field}")
    return converted


def _midi_pitch(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise Music21MIDIAdapterError("music21 returned a non-numeric MIDI pitch")
    numeric = float(value)
    if not math.isfinite(numeric) or not numeric.is_integer():
        raise Music21MIDIAdapterError("music21 returned a non-integral MIDI pitch")
    pitch = int(numeric)
    if not 0 <= pitch <= 127:
        raise Music21MIDIAdapterError("music21 returned a MIDI pitch outside 0..127")
    return pitch


def _segments(
    score: _Score,
    *,
    note_class: type[object],
    chord_class: type[object],
) -> tuple[_Segment, ...]:
    bearing: list[list[object]] = []
    for raw_part in score.parts:
        objects = list(cast(_Part, raw_part).flatten())
        if any(isinstance(item, chord_class) for item in objects):
            raise Music21MIDIAdapterError(
                "preflight/music21 disagreement: canonical monophony became a chord"
            )
        notes = [item for item in objects if isinstance(item, note_class)]
        if notes:
            bearing.append(notes)
    if len(bearing) != 1:
        raise Music21MIDIAdapterError(
            "preflight/music21 disagreement: canonical MIDI did not yield one note-bearing part"
        )

    segments: list[_Segment] = []
    for raw_note in bearing[0]:
        note = cast(_Note, raw_note)
        onset = _exact_fraction(note.offset, field="note onset")
        duration = _exact_fraction(note.quarterLength, field="note duration")
        if onset < 0 or duration <= 0:
            raise Music21MIDIAdapterError(
                "music21 returned a negative onset or non-positive note duration"
            )
        tie = None if note.tie is None else note.tie.type
        if tie not in {None, "start", "continue", "stop"}:
            raise Music21MIDIAdapterError("music21 returned an unsupported tie type")
        segments.append(_Segment(onset, duration, _midi_pitch(note.pitch.midi), tie))
    segments.sort(key=lambda item: (item.onset, item.pitch, item.duration))
    return tuple(segments)


def _merge_ties(segments: tuple[_Segment, ...]) -> tuple[tuple[Fraction, Fraction, int], ...]:
    notes: list[tuple[Fraction, Fraction, int]] = []
    pending: _PendingTie | None = None
    for segment in segments:
        if segment.tie == "start":
            if pending is not None:
                raise Music21MIDIAdapterError("music21 returned overlapping tie starts")
            pending = _PendingTie(segment.onset, segment.duration, segment.pitch)
        elif segment.tie == "continue":
            if (
                pending is None
                or pending.pitch != segment.pitch
                or pending.onset + pending.duration != segment.onset
            ):
                raise Music21MIDIAdapterError("music21 returned a malformed tie continuation")
            pending = _PendingTie(
                pending.onset,
                pending.duration + segment.duration,
                pending.pitch,
            )
        elif segment.tie == "stop":
            if (
                pending is None
                or pending.pitch != segment.pitch
                or pending.onset + pending.duration != segment.onset
            ):
                raise Music21MIDIAdapterError("music21 returned a malformed tie stop")
            notes.append(
                (pending.onset, pending.duration + segment.duration, pending.pitch)
            )
            pending = None
        else:
            if pending is not None:
                raise Music21MIDIAdapterError("music21 returned a dangling tie start")
            notes.append((segment.onset, segment.duration, segment.pitch))
    if pending is not None:
        raise Music21MIDIAdapterError("music21 returned a dangling tie at end of score")
    notes.sort(key=lambda item: (item[0], item[2], item[1]))
    return tuple(notes)


def crosscheck_music21_midi(
    canonical_midi: bytes,
    metadata: MIDIPreflightMetadata,
) -> None:
    """Require music21 10.5.0 to reproduce the exact authoritative note timeline."""

    if type(canonical_midi) is not bytes or type(metadata) is not MIDIPreflightMetadata:
        raise Music21MIDIAdapterError("invalid preflight adapter input")
    try:
        translate = cast(_TranslateModule, import_module("music21.midi.translate"))
        note_module = import_module("music21.note")
        chord_module = import_module("music21.chord")
    except ModuleNotFoundError as exc:
        if exc.name is not None and exc.name.split(".", 1)[0] == "music21":
            raise MIDIDependencyError("music21") from None
        raise
    try:
        parsed = translate.midiStringToStream(canonical_midi, quantizePost=False)
        actual = _merge_ties(
            _segments(
                cast(_Score, parsed),
                note_class=cast(type[object], note_module.Note),
                chord_class=cast(type[object], chord_module.Chord),
            )
        )
    except Music21MIDIAdapterError:
        raise
    except Exception as exc:
        raise Music21MIDIAdapterError(
            f"music21 MIDI parser failed safely: {type(exc).__name__}"
        ) from None

    expected = tuple(
        (event.onset, event.duration, event.pitch) for event in metadata.note_events
    )
    if actual != expected:
        raise Music21MIDIAdapterError(
            "preflight/music21 disagreement on note count, pitch, onset, or duration"
        )


__all__ = [
    "MIDIDependencyError",
    "Music21MIDIAdapterError",
    "crosscheck_music21_midi",
]
