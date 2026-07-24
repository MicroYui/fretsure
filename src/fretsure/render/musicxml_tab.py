"""Deterministic MusicXML 4.0 export for canonical guitar tablature."""

from __future__ import annotations

import heapq
import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from typing import cast

from fretsure.oracle.input import OracleInputError, ensure_oracle_input
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.render.contracts import MUSICXML_TAB_EXPORT_VERSION
from fretsure.tab import Tab, TabNote

# MusicXML permits decimal divisions, but integer divisions are understood by
# the widest range of score editors.  The 4.0 specification recommends staying
# at or below 16,383 for Standard MIDI File compatibility.
MAX_MUSICXML_DIVISIONS = 16_383
MAX_MUSICXML_MEASURES = 10_000
MAX_MUSICXML_NOTE_SEGMENTS = 100_000

_DOCTYPE = (
    b'<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
    b'"http://www.musicxml.org/dtds/partwise.dtd">\n'
)
_XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8"?>\n'

_PITCH_NAMES = (
    ("C", None),
    ("C", 1),
    ("D", None),
    ("D", 1),
    ("E", None),
    ("F", None),
    ("F", 1),
    ("G", None),
    ("G", 1),
    ("A", None),
    ("A", 1),
    ("B", None),
)
_NOTE_TYPES = (
    (Fraction(16), "long"),
    (Fraction(8), "breve"),
    (Fraction(4), "whole"),
    (Fraction(2), "half"),
    (Fraction(1), "quarter"),
    (Fraction(1, 2), "eighth"),
    (Fraction(1, 4), "16th"),
    (Fraction(1, 8), "32nd"),
    (Fraction(1, 16), "64th"),
    (Fraction(1, 32), "128th"),
    (Fraction(1, 64), "256th"),
)


class MusicXMLTabExportCode(StrEnum):
    """Stable reasons why a canonical Tab cannot be exported losslessly."""

    INVALID_TAB = "INVALID_TAB"
    RHYTHM_UNREPRESENTABLE = "RHYTHM_UNREPRESENTABLE"
    TIMELINE_UNREPRESENTABLE = "TIMELINE_UNREPRESENTABLE"


class MusicXMLTabExportError(ValueError):
    """A typed, safe rejection from MusicXML guitar-TAB export."""

    def __init__(self, code: MusicXMLTabExportCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code.value}: {message}")


@dataclass(frozen=True, slots=True)
class _ChordEvent:
    onset: Fraction
    duration: Fraction
    notes: tuple[TabNote, ...]
    voice: int

    @property
    def end(self) -> Fraction:
        return self.onset + self.duration


@dataclass(frozen=True, slots=True)
class _Segment:
    onset: Fraction
    duration: Fraction
    notes: tuple[TabNote, ...]
    voice: int
    tie_stop: bool
    tie_start: bool

    @property
    def end(self) -> Fraction:
        return self.onset + self.duration


def _ceil_fraction(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def _pitch_components(midi_pitch: int) -> tuple[str, int | None, int]:
    step, alter = _PITCH_NAMES[midi_pitch % 12]
    return step, alter, midi_pitch // 12 - 1


def _append_pitch(parent: ET.Element, midi_pitch: int, *, tuning: bool = False) -> None:
    step, alter, octave = _pitch_components(midi_pitch)
    prefix = "tuning-" if tuning else ""
    ET.SubElement(parent, f"{prefix}step").text = step
    if alter is not None:
        ET.SubElement(parent, f"{prefix}alter").text = str(alter)
    ET.SubElement(parent, f"{prefix}octave").text = str(octave)


def _duration_type(duration: Fraction) -> tuple[str, int] | None:
    for base, name in _NOTE_TYPES:
        for dots in range(4):
            multiplier = Fraction(2 ** (dots + 1) - 1, 2**dots)
            if duration == base * multiplier:
                return name, dots
    return None


def _duration_units(duration: Fraction, divisions: int) -> int:
    units = duration * divisions
    if units.denominator != 1 or units.numerator <= 0:
        raise MusicXMLTabExportError(
            MusicXMLTabExportCode.RHYTHM_UNREPRESENTABLE,
            "a note or rest duration cannot be represented by the selected divisions",
        )
    return units.numerator


def _divisions_for(tab: Tab) -> int:
    divisions = 1
    for note in tab.notes:
        for value in (note.onset, note.duration):
            divisions = math.lcm(divisions, value.denominator)
            if divisions > MAX_MUSICXML_DIVISIONS:
                raise MusicXMLTabExportError(
                    MusicXMLTabExportCode.RHYTHM_UNREPRESENTABLE,
                    (
                        "exact rational timing requires more than "
                        f"{MAX_MUSICXML_DIVISIONS} integer divisions per quarter note"
                    ),
                )
    return divisions


def _note_sort_key(note: TabNote, tab: Tab) -> tuple[int, int, int, int, str]:
    pitch = tab.tuning[note.string] + tab.capo + note.fret
    return pitch, note.string, note.fret, note.left_finger, note.right_finger


def _voice_events(tab: Tab) -> tuple[_ChordEvent, ...]:
    grouped: dict[tuple[Fraction, Fraction], list[TabNote]] = defaultdict(list)
    for note in tab.notes:
        grouped[(note.onset, note.duration)].append(note)

    ordered = sorted(
        (
            onset,
            duration,
            tuple(sorted(notes, key=lambda note: _note_sort_key(note, tab))),
        )
        for (onset, duration), notes in grouped.items()
    )

    active: list[tuple[Fraction, int]] = []
    available: list[int] = []
    next_voice = 1
    events: list[_ChordEvent] = []
    for onset, duration, notes in ordered:
        while active and active[0][0] <= onset:
            _, voice = heapq.heappop(active)
            heapq.heappush(available, voice)
        if available:
            voice = heapq.heappop(available)
        else:
            voice = next_voice
            next_voice += 1
        event = _ChordEvent(onset, duration, notes, voice)
        events.append(event)
        heapq.heappush(active, (event.end, voice))
    return tuple(events)


def _measure_segments(
    tab: Tab,
    *,
    beats_per_bar: int,
) -> tuple[int, dict[int, dict[int, list[_Segment]]]]:
    bar = Fraction(beats_per_bar)
    latest_end = max(note.onset + note.duration for note in tab.notes)
    measure_count = max(1, _ceil_fraction(latest_end / bar))
    if measure_count > MAX_MUSICXML_MEASURES:
        raise MusicXMLTabExportError(
            MusicXMLTabExportCode.TIMELINE_UNREPRESENTABLE,
            f"the score requires {measure_count} measures; limit is {MAX_MUSICXML_MEASURES}",
        )

    by_measure: dict[int, dict[int, list[_Segment]]] = defaultdict(
        lambda: defaultdict(list)
    )
    segment_notes = 0
    for event in _voice_events(tab):
        first_measure = event.onset // bar
        last_measure = _ceil_fraction(event.end / bar) - 1
        spans = last_measure - first_measure + 1
        segment_notes += spans * len(event.notes)
        if segment_notes > MAX_MUSICXML_NOTE_SEGMENTS:
            raise MusicXMLTabExportError(
                MusicXMLTabExportCode.TIMELINE_UNREPRESENTABLE,
                (
                    "measure-boundary ties would require more than "
                    f"{MAX_MUSICXML_NOTE_SEGMENTS} MusicXML note segments"
                ),
            )
        for measure_index in range(first_measure, last_measure + 1):
            measure_start = bar * measure_index
            measure_end = measure_start + bar
            segment_start = max(event.onset, measure_start)
            segment_end = min(event.end, measure_end)
            by_measure[measure_index][event.voice].append(
                _Segment(
                    onset=segment_start,
                    duration=segment_end - segment_start,
                    notes=event.notes,
                    voice=event.voice,
                    tie_stop=segment_start > event.onset,
                    tie_start=segment_end < event.end,
                )
            )
    return measure_count, by_measure


def _append_rhythm(parent: ET.Element, duration: Fraction) -> None:
    notation = _duration_type(duration)
    if notation is None:
        return
    name, dots = notation
    ET.SubElement(parent, "type").text = name
    for _ in range(dots):
        ET.SubElement(parent, "dot")


def _append_rest(parent: ET.Element, duration: Fraction, *, voice: int, divisions: int) -> None:
    note = ET.SubElement(parent, "note")
    ET.SubElement(note, "rest")
    ET.SubElement(note, "duration").text = str(_duration_units(duration, divisions))
    ET.SubElement(note, "voice").text = str(voice)
    _append_rhythm(note, duration)


def _append_tab_note(
    parent: ET.Element,
    tab: Tab,
    tab_note: TabNote,
    segment: _Segment,
    *,
    divisions: int,
    chord: bool,
) -> None:
    note = ET.SubElement(parent, "note")
    if chord:
        ET.SubElement(note, "chord")
    pitch = ET.SubElement(note, "pitch")
    _append_pitch(
        pitch,
        tab.tuning[tab_note.string] + tab.capo + tab_note.fret,
    )
    ET.SubElement(note, "duration").text = str(
        _duration_units(segment.duration, divisions)
    )
    if segment.tie_stop:
        ET.SubElement(note, "tie", {"type": "stop"})
    if segment.tie_start:
        ET.SubElement(note, "tie", {"type": "start"})
    ET.SubElement(note, "voice").text = str(segment.voice)
    _append_rhythm(note, segment.duration)

    notations = ET.SubElement(note, "notations")
    if segment.tie_stop:
        ET.SubElement(notations, "tied", {"type": "stop"})
    if segment.tie_start:
        ET.SubElement(notations, "tied", {"type": "start"})
    technical = ET.SubElement(notations, "technical")
    if tab_note.left_finger == 0:
        ET.SubElement(technical, "open-string")
    else:
        ET.SubElement(technical, "fingering").text = str(tab_note.left_finger)
    ET.SubElement(technical, "pluck").text = tab_note.right_finger
    ET.SubElement(technical, "string").text = str(6 - tab_note.string)
    ET.SubElement(technical, "fret").text = str(tab_note.fret)


def _append_first_measure_metadata(
    measure: ET.Element,
    tab: Tab,
    *,
    divisions: int,
    tempo_bpm: float,
    beats_per_bar: int,
) -> None:
    attributes = ET.SubElement(measure, "attributes")
    ET.SubElement(attributes, "divisions").text = str(divisions)
    time = ET.SubElement(attributes, "time")
    ET.SubElement(time, "beats").text = str(beats_per_bar)
    ET.SubElement(time, "beat-type").text = "4"
    clef = ET.SubElement(attributes, "clef")
    ET.SubElement(clef, "sign").text = "TAB"
    ET.SubElement(clef, "line").text = "5"
    staff_details = ET.SubElement(attributes, "staff-details", {"show-frets": "numbers"})
    ET.SubElement(staff_details, "staff-lines").text = "6"
    for line, midi_pitch in enumerate(tab.tuning, start=1):
        tuning = ET.SubElement(staff_details, "staff-tuning", {"line": str(line)})
        _append_pitch(tuning, midi_pitch, tuning=True)
    ET.SubElement(staff_details, "capo").text = str(tab.capo)

    tempo_text = str(int(tempo_bpm)) if tempo_bpm.is_integer() else repr(tempo_bpm)
    direction = ET.SubElement(measure, "direction", {"placement": "above"})
    direction_type = ET.SubElement(direction, "direction-type")
    metronome = ET.SubElement(direction_type, "metronome", {"parentheses": "no"})
    ET.SubElement(metronome, "beat-unit").text = "quarter"
    ET.SubElement(metronome, "per-minute").text = tempo_text
    ET.SubElement(direction, "sound", {"tempo": tempo_text})


def _score_root() -> tuple[ET.Element, ET.Element]:
    root = ET.Element("score-partwise", {"version": "4.0"})
    work = ET.SubElement(root, "work")
    ET.SubElement(work, "work-title").text = "Fretsure Guitar Arrangement"
    identification = ET.SubElement(root, "identification")
    encoding = ET.SubElement(identification, "encoding")
    ET.SubElement(encoding, "software").text = (
        f"Fretsure {MUSICXML_TAB_EXPORT_VERSION}"
    )
    part_list = ET.SubElement(root, "part-list")
    score_part = ET.SubElement(part_list, "score-part", {"id": "P1"})
    ET.SubElement(score_part, "part-name").text = "Guitar"
    ET.SubElement(score_part, "part-abbreviation").text = "Gtr."
    score_instrument = ET.SubElement(score_part, "score-instrument", {"id": "P1-I1"})
    ET.SubElement(score_instrument, "instrument-name").text = "Acoustic Guitar (nylon)"
    midi_instrument = ET.SubElement(score_part, "midi-instrument", {"id": "P1-I1"})
    ET.SubElement(midi_instrument, "midi-channel").text = "1"
    ET.SubElement(midi_instrument, "midi-program").text = "25"
    return root, ET.SubElement(root, "part", {"id": "P1"})


def render_musicxml_tab(
    tab: Tab,
    *,
    tempo_bpm: float = 90.0,
    beats_per_bar: int = 4,
) -> bytes:
    """Return a lossless, deterministic MusicXML 4.0 guitar-TAB document.

    Quarter-note beats are converted to exact integer MusicXML divisions.
    Simultaneous equal-duration attacks become chords, overlapping material is
    assigned deterministic voices, gaps become rests, and notes crossing bar
    lines are split into tied segments.  Sounding pitch, capo-relative fret,
    string, and both hands' representable fingering are written directly from
    the canonical :class:`~fretsure.tab.Tab`.
    """

    try:
        canonical, _, normalized_tempo, normalized_meter = ensure_oracle_input(
            tab,
            MEDIAN_HAND,
            tempo_bpm=tempo_bpm,
            beats_per_bar=beats_per_bar,
        )
    except OracleInputError:
        raise MusicXMLTabExportError(
            MusicXMLTabExportCode.INVALID_TAB,
            "Tab, tempo, or meter is outside the canonical guitar export domain",
        ) from None

    divisions = _divisions_for(canonical)
    measure_count, by_measure = _measure_segments(
        canonical,
        beats_per_bar=normalized_meter,
    )
    root, part = _score_root()
    bar_duration = Fraction(normalized_meter)
    bar_units = _duration_units(bar_duration, divisions)

    for measure_index in range(measure_count):
        measure = ET.SubElement(part, "measure", {"number": str(measure_index + 1)})
        if measure_index == 0:
            _append_first_measure_metadata(
                measure,
                canonical,
                divisions=divisions,
                tempo_bpm=normalized_tempo,
                beats_per_bar=normalized_meter,
            )

        measure_start = bar_duration * measure_index
        voices = by_measure.get(measure_index)
        if not voices:
            _append_rest(measure, bar_duration, voice=1, divisions=divisions)
            continue

        for voice_position, voice in enumerate(sorted(voices)):
            if voice_position:
                backup = ET.SubElement(measure, "backup")
                ET.SubElement(backup, "duration").text = str(bar_units)
            cursor = measure_start
            for segment in sorted(
                voices[voice],
                key=lambda item: (item.onset, item.duration, item.notes),
            ):
                if segment.onset > cursor:
                    _append_rest(
                        measure,
                        segment.onset - cursor,
                        voice=voice,
                        divisions=divisions,
                    )
                for index, tab_note in enumerate(segment.notes):
                    _append_tab_note(
                        measure,
                        canonical,
                        tab_note,
                        segment,
                        divisions=divisions,
                        chord=index > 0,
                    )
                cursor = segment.end
            measure_end = measure_start + bar_duration
            if cursor < measure_end:
                _append_rest(
                    measure,
                    measure_end - cursor,
                    voice=voice,
                    divisions=divisions,
                )

    ET.indent(root, space="  ")
    body = cast(bytes, ET.tostring(root, encoding="utf-8", short_empty_elements=True))
    return _XML_DECLARATION + _DOCTYPE + body + b"\n"


__all__ = [
    "MAX_MUSICXML_DIVISIONS",
    "MAX_MUSICXML_MEASURES",
    "MAX_MUSICXML_NOTE_SEGMENTS",
    "MUSICXML_TAB_EXPORT_VERSION",
    "MusicXMLTabExportCode",
    "MusicXMLTabExportError",
    "render_musicxml_tab",
]
