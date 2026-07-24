"""Deterministic Standard MIDI File export for canonical guitar Tabs."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction

from fretsure.oracle.input import OracleInputError, ensure_oracle_input
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.tab import Tab

MIDI_TICKS_PER_QUARTER = 480
MIDI_EXPORT_VERSION = "tab-midi@0.1.0"

_MAX_VARIABLE_LENGTH_VALUE = 0x0FFFFFFF
_MAX_TEMPO_MICROSECONDS = 0xFFFFFF
_MICROSECONDS_PER_MINUTE = 60_000_000
_GUITAR_CHANNEL = 0
_ACOUSTIC_GUITAR_NYLON_PROGRAM = 24  # General MIDI program 25, zero-based on wire.
_NOTE_VELOCITY = 80


class MidiExportCode(StrEnum):
    """Stable reasons why a Tab cannot be represented by this MIDI profile."""

    INVALID_TAB = "INVALID_TAB"
    TEMPO_UNREPRESENTABLE = "TEMPO_UNREPRESENTABLE"
    TIMELINE_UNREPRESENTABLE = "TIMELINE_UNREPRESENTABLE"


class MidiExportError(ValueError):
    """A safe, typed rejection from deterministic MIDI export."""

    def __init__(self, code: MidiExportCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code.value}: {message}")


@dataclass(frozen=True, slots=True)
class _NoteEvent:
    tick: int
    kind: int  # 0 = note-off, 1 = note-on; off must sort first at one tick.
    pitch: int
    string: int


def _round_fraction(value: Fraction) -> int:
    """Round one non-negative fraction to nearest integer, ties upward."""

    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _beat_to_tick(beat: Fraction, *, timeline_scale: int) -> int:
    return _round_fraction(beat * MIDI_TICKS_PER_QUARTER * timeline_scale)


def _variable_length(value: int) -> bytes:
    if not 0 <= value <= _MAX_VARIABLE_LENGTH_VALUE:
        raise MidiExportError(
            MidiExportCode.TIMELINE_UNREPRESENTABLE,
            "a MIDI event delta exceeds the four-byte variable-length limit",
        )
    encoded = bytearray((value & 0x7F,))
    value >>= 7
    while value:
        encoded.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(encoded)


def _tempo_encoding(tempo_bpm: float) -> tuple[int, int]:
    # ``tempo_bpm`` is already an owned, finite float from ensure_oracle_input.
    # Its shortest decimal spelling gives a stable rational for half-up rounding.
    tempo = Fraction(str(tempo_bpm))
    requested_micros = Fraction(_MICROSECONDS_PER_MINUTE, 1) / tempo
    # SMF stores tempo in only 24 bits.  For very slow but otherwise supported
    # product tempos, expand the musical timeline by a small integer and encode
    # the correspondingly faster quarter-note tempo.  Playback duration remains
    # the requested duration while every exported result stays representable.
    timeline_scale = max(
        1,
        (requested_micros.numerator + _MAX_TEMPO_MICROSECONDS * requested_micros.denominator - 1)
        // (_MAX_TEMPO_MICROSECONDS * requested_micros.denominator),
    )
    micros = _round_fraction(requested_micros / timeline_scale)
    if not 1 <= micros <= _MAX_TEMPO_MICROSECONDS:
        raise MidiExportError(
            MidiExportCode.TEMPO_UNREPRESENTABLE,
            "tempo cannot be encoded in the Standard MIDI File 24-bit tempo field",
        )
    return micros, timeline_scale


def render_midi(tab: Tab, *, tempo_bpm: float = 90.0) -> bytes:
    """Return one canonical format-0 SMF representing ``tab``.

    The file has one track, one channel, and one General MIDI nylon-guitar
    program.  Every Tab note becomes a note-on/note-off pair; notes sharing an
    onset retain the same tick.  At a shared tick note-offs sort before note-ons,
    which preserves clean re-articulation of repeated pitches.
    """

    try:
        canonical, _, normalized_tempo, _ = ensure_oracle_input(
            tab,
            MEDIAN_HAND,
            tempo_bpm=tempo_bpm,
            beats_per_bar=4,
        )
    except OracleInputError:
        raise MidiExportError(
            MidiExportCode.INVALID_TAB,
            "Tab or tempo is outside the canonical export input domain",
        ) from None

    if not math.isfinite(normalized_tempo):  # defensive; the validator rejects this.
        raise MidiExportError(
            MidiExportCode.TEMPO_UNREPRESENTABLE,
            "tempo must be finite",
        )
    tempo_microseconds, timeline_scale = _tempo_encoding(normalized_tempo)

    events: list[_NoteEvent] = []
    for note in canonical.notes:
        onset_tick = _beat_to_tick(note.onset, timeline_scale=timeline_scale)
        end_tick = _beat_to_tick(note.onset + note.duration, timeline_scale=timeline_scale)
        if end_tick <= onset_tick:
            end_tick = onset_tick + 1
        if end_tick > _MAX_VARIABLE_LENGTH_VALUE:
            raise MidiExportError(
                MidiExportCode.TIMELINE_UNREPRESENTABLE,
                "the Tab timeline exceeds the canonical MIDI export range",
            )
        pitch = canonical.tuning[note.string] + canonical.capo + note.fret
        events.append(_NoteEvent(onset_tick, 1, pitch, note.string))
        events.append(_NoteEvent(end_tick, 0, pitch, note.string))

    events.sort(key=lambda event: (event.tick, event.kind, event.pitch, event.string))

    track = bytearray()
    track.extend(b"\x00\xff\x51\x03")
    track.extend(tempo_microseconds.to_bytes(3, "big"))
    track.extend((0, 0xC0 | _GUITAR_CHANNEL, _ACOUSTIC_GUITAR_NYLON_PROGRAM))

    previous_tick = 0
    for event in events:
        track.extend(_variable_length(event.tick - previous_tick))
        status = (0x80 if event.kind == 0 else 0x90) | _GUITAR_CHANNEL
        velocity = 0 if event.kind == 0 else _NOTE_VELOCITY
        track.extend((status, event.pitch, velocity))
        previous_tick = event.tick

    track.extend(b"\x00\xff\x2f\x00")
    header = b"MThd" + (6).to_bytes(4, "big") + (0).to_bytes(2, "big")
    header += (1).to_bytes(2, "big") + MIDI_TICKS_PER_QUARTER.to_bytes(2, "big")
    return header + b"MTrk" + len(track).to_bytes(4, "big") + bytes(track)


__all__ = [
    "MIDI_EXPORT_VERSION",
    "MIDI_TICKS_PER_QUARTER",
    "MidiExportCode",
    "MidiExportError",
    "render_midi",
]
