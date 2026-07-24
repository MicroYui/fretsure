"""Deterministic Guitar Pro 5 export for canonical guitar Tabs.

The result is an actual Guitar Pro 5.1 binary file, not renamed text or MIDI.
GP5 is the stable open-writer target used here; current automated compatibility
evidence is a PyGuitarPro round-trip. The newer ``.gp`` container is not covered
by a stable open Python writer.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from io import BytesIO

import guitarpro as gp  # type: ignore[import-untyped]

from fretsure.oracle.input import OracleInputError, ensure_oracle_input
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.render.contracts import GUITAR_PRO_EXPORT_VERSION
from fretsure.tab import Tab, TabNote

GUITAR_PRO_FILE_VERSION = (5, 1, 0)

_BEATS_PER_MEASURE = 4
_MAX_TITLE_BYTES = 255
_MAX_DURATION_UNITS = 65_536
_MAX_MEASURES = 10_000
_MAX_EXPORTED_BEATS = 200_000
_GP_ENCODING = "cp1252"
_TRACK_NAME = "Fretsure Guitar"


class GuitarProExportCode(StrEnum):
    """Stable reasons why a Tab cannot be represented by the GP5 profile."""

    INVALID_TAB = "INVALID_TAB"
    TITLE_UNREPRESENTABLE = "TITLE_UNREPRESENTABLE"
    TIMING_UNREPRESENTABLE = "TIMING_UNREPRESENTABLE"
    STRING_OVERLAP = "STRING_OVERLAP"
    OUTPUT_LIMIT_EXCEEDED = "OUTPUT_LIMIT_EXCEEDED"
    SERIALIZATION_FAILED = "SERIALIZATION_FAILED"


class GuitarProExportError(ValueError):
    """A safe, typed rejection from deterministic Guitar Pro export."""

    def __init__(self, code: GuitarProExportCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code.value}: {message}")


@dataclass(frozen=True, slots=True)
class _DurationChoice:
    length: Fraction
    value: int
    dotted: bool
    tuplet_enters: int
    tuplet_times: int

    @property
    def simplicity(self) -> tuple[int, int, int]:
        return (
            int((self.tuplet_enters, self.tuplet_times) != (1, 1)),
            int(self.dotted),
            self.value,
        )

    def model(self) -> object:
        return gp.Duration(
            value=self.value,
            isDotted=self.dotted,
            tuplet=gp.Tuplet(self.tuplet_enters, self.tuplet_times),
        )


def _duration_choices() -> tuple[_DurationChoice, ...]:
    by_length: dict[Fraction, _DurationChoice] = {}
    for value in (1, 2, 4, 8, 16, 32, 64):
        for dotted in (False, True):
            for enters, times in gp.Tuplet.supportedTuplets:
                length = Fraction(4, value) * Fraction(3 if dotted else 2, 2)
                length *= Fraction(times, enters)
                if not 0 < length <= _BEATS_PER_MEASURE:
                    continue
                choice = _DurationChoice(length, value, dotted, enters, times)
                current = by_length.get(length)
                if current is None or choice.simplicity < current.simplicity:
                    by_length[length] = choice
    return tuple(
        sorted(
            by_length.values(),
            key=lambda choice: (-choice.length, choice.simplicity),
        )
    )


_DURATION_CHOICES = _duration_choices()


def _decompose_duration(length: Fraction) -> tuple[_DurationChoice, ...]:
    """Find a deterministic, minimum-beat exact GP5 duration decomposition."""

    direct = next((choice for choice in _DURATION_CHOICES if choice.length == length), None)
    if direct is not None:
        return (direct,)

    amount = length.numerator
    if amount > _MAX_DURATION_UNITS:
        raise GuitarProExportError(
            GuitarProExportCode.TIMING_UNREPRESENTABLE,
            "a rhythmic interval needs more exact units than the GP5 export limit",
        )

    scaled: list[tuple[int, _DurationChoice]] = []
    for choice in _DURATION_CHOICES:
        units = choice.length * length.denominator
        if units.denominator == 1 and 0 < units.numerator <= amount:
            scaled.append((units.numerator, choice))
    if not scaled:
        raise GuitarProExportError(
            GuitarProExportCode.TIMING_UNREPRESENTABLE,
            f"the exact interval {length} quarter-note beats has no GP5 duration spelling",
        )

    unreachable = amount + 1
    counts = [unreachable] * (amount + 1)
    previous = [-1] * (amount + 1)
    picked = [-1] * (amount + 1)
    counts[0] = 0
    for current in range(1, amount + 1):
        for index, (scaled_units, _choice) in enumerate(scaled):
            if scaled_units > current or counts[current - scaled_units] == unreachable:
                continue
            candidate = counts[current - scaled_units] + 1
            if candidate < counts[current]:
                counts[current] = candidate
                previous[current] = current - scaled_units
                picked[current] = index

    if counts[amount] == unreachable:
        raise GuitarProExportError(
            GuitarProExportCode.TIMING_UNREPRESENTABLE,
            f"the exact interval {length} quarter-note beats has no GP5 duration spelling",
        )

    result: list[_DurationChoice] = []
    current = amount
    while current:
        index = picked[current]
        result.append(scaled[index][1])
        current = previous[current]
    return tuple(result)


def _gp5_tempo(tempo_bpm: float) -> int:
    # GP5 stores a whole-number song tempo. Canonical tempo is positive, so
    # floor(x + 0.5) gives deterministic nearest-integer, half-up rounding.
    return math.floor(tempo_bpm + 0.5)


def _validated_title(title: str) -> str:
    if type(title) is not str:
        raise GuitarProExportError(
            GuitarProExportCode.TITLE_UNREPRESENTABLE,
            "title must be a string",
        )
    try:
        encoded = title.encode(_GP_ENCODING)
    except UnicodeEncodeError:
        raise GuitarProExportError(
            GuitarProExportCode.TITLE_UNREPRESENTABLE,
            "GP5 titles must be representable in the format's CP1252 text encoding",
        ) from None
    if len(encoded) > _MAX_TITLE_BYTES:
        raise GuitarProExportError(
            GuitarProExportCode.TITLE_UNREPRESENTABLE,
            "GP5 titles are limited to 255 encoded bytes",
        )
    return title


def _assert_strings_do_not_overlap(notes: tuple[TabNote, ...]) -> None:
    by_string: dict[int, list[TabNote]] = defaultdict(list)
    for note in notes:
        by_string[note.string].append(note)
    for string, string_notes in by_string.items():
        ordered = sorted(string_notes, key=lambda note: (note.onset, note.duration, note.fret))
        previous_end = Fraction(-1)
        for note in ordered:
            if note.onset < previous_end:
                raise GuitarProExportError(
                    GuitarProExportCode.STRING_OVERLAP,
                    f"canonical string {string} has overlapping notes that GP5 cannot spell",
                )
            previous_end = note.onset + note.duration


def _left_fingering(note: TabNote) -> object:
    if note.left_finger == 0:
        return gp.Fingering.open
    return {
        1: gp.Fingering.index,
        2: gp.Fingering.middle,
        3: gp.Fingering.annular,
        4: gp.Fingering.little,
    }[note.left_finger]


def _right_fingering(note: TabNote) -> object:
    return {
        "p": gp.Fingering.thumb,
        "i": gp.Fingering.index,
        "m": gp.Fingering.middle,
        "a": gp.Fingering.annular,
    }[note.right_finger]


def _model_note(beat: object, note: TabNote, *, tied: bool) -> object:
    effect = gp.NoteEffect()
    if not tied:
        effect.leftHandFinger = _left_fingering(note)
        effect.rightHandFinger = _right_fingering(note)
    return gp.Note(
        beat=beat,
        value=note.fret,
        string=6 - note.string,
        effect=effect,
        type=gp.NoteType.tie if tied else gp.NoteType.normal,
    )


def _build_song(tab: Tab, *, tempo: int, title: str) -> object:
    last_end = max(note.onset + note.duration for note in tab.notes)
    measure_count = max(
        1,
        (last_end.numerator + 4 * last_end.denominator - 1)
        // (4 * last_end.denominator),
    )
    if measure_count > _MAX_MEASURES:
        raise GuitarProExportError(
            GuitarProExportCode.OUTPUT_LIMIT_EXCEEDED,
            f"GP5 export is limited to {_MAX_MEASURES} measures",
        )

    quarter_time = gp.Duration.quarterTime
    headers = [
        gp.MeasureHeader(
            number=index + 1,
            start=quarter_time + index * _BEATS_PER_MEASURE * quarter_time,
        )
        for index in range(measure_count)
    ]
    song = gp.Song(
        versionTuple=GUITAR_PRO_FILE_VERSION,
        title=title,
        tab="Fretsure",
        notice=[
            "Generated deterministically from canonical Fretsure Tab "
            f"with {GUITAR_PRO_EXPORT_VERSION}."
        ],
        tempo=tempo,
        measureHeaders=headers,
        tracks=[],
    )
    strings = [gp.GuitarString(6 - index, pitch) for index, pitch in enumerate(tab.tuning)]
    strings.sort(key=lambda string: string.number)
    track = gp.Track(
        song=song,
        number=1,
        fretCount=max(24, max(note.fret for note in tab.notes)),
        offset=tab.capo,
        indicateTuning=True,
        name=_TRACK_NAME,
        measures=[],
        strings=strings,
    )
    track.settings.tablature = True
    track.settings.notation = True
    track.settings.showRhythm = True
    track.measures = [gp.Measure(track, header) for header in headers]
    song.tracks = [track]

    starts: dict[Fraction, list[TabNote]] = defaultdict(list)
    ends: dict[Fraction, list[TabNote]] = defaultdict(list)
    boundaries_by_measure: dict[int, set[Fraction]] = defaultdict(set)
    for note in tab.notes:
        end = note.onset + note.duration
        starts[note.onset].append(note)
        ends[end].append(note)
        start_measure = int(note.onset // _BEATS_PER_MEASURE)
        end_measure = (
            end.numerator + _BEATS_PER_MEASURE * end.denominator - 1
        ) // (_BEATS_PER_MEASURE * end.denominator) - 1
        for measure_index in range(start_measure, end_measure + 1):
            measure_start = Fraction(measure_index * _BEATS_PER_MEASURE)
            measure_end = measure_start + _BEATS_PER_MEASURE
            if measure_start < note.onset < measure_end:
                boundaries_by_measure[measure_index].add(note.onset)
            if measure_start < end < measure_end:
                boundaries_by_measure[measure_index].add(end)

    active: dict[int, TabNote] = {}
    exported_beats = 0
    for measure_index, measure in enumerate(track.measures):
        measure_start = Fraction(measure_index * _BEATS_PER_MEASURE)
        measure_end = measure_start + _BEATS_PER_MEASURE
        boundaries = sorted(
            {measure_start, measure_end, *boundaries_by_measure.get(measure_index, set())}
        )
        voice = measure.voices[0]
        for boundary, next_boundary in zip(boundaries, boundaries[1:], strict=False):
            for note in ends.get(boundary, ()):
                active.pop(note.string, None)
            for note in sorted(starts.get(boundary, ()), key=lambda item: item.string):
                active[note.string] = note

            cursor = boundary
            for duration in _decompose_duration(next_boundary - boundary):
                exported_beats += 1
                if exported_beats > _MAX_EXPORTED_BEATS:
                    raise GuitarProExportError(
                        GuitarProExportCode.OUTPUT_LIMIT_EXCEEDED,
                        f"GP5 export is limited to {_MAX_EXPORTED_BEATS} written beats",
                    )
                beat = gp.Beat(
                    voice=voice,
                    duration=duration.model(),
                    start=quarter_time + cursor * quarter_time,
                    status=gp.BeatStatus.normal if active else gp.BeatStatus.rest,
                )
                beat.notes = [
                    _model_note(beat, note, tied=cursor != note.onset)
                    for note in sorted(active.values(), key=lambda item: item.string)
                ]
                voice.beats.append(beat)
                cursor += duration.length
        for note in ends.get(measure_end, ()):
            active.pop(note.string, None)

    return song


def render_guitar_pro(
    tab: Tab,
    *,
    tempo_bpm: float = 90.0,
    title: str = "Fretsure Guitar Arrangement",
) -> bytes:
    """Return a deterministic, genuine Guitar Pro 5.1 binary file.

    Timing is written with exact GP rhythmic values and ties, including ties
    across barlines. String, fret, capo, tuning and attack fingerings come
    directly from the canonical :class:`~fretsure.tab.Tab`; tempo is rounded
    to GP5's nearest whole BPM, with exact halves rounded upward.
    """

    try:
        canonical, _, normalized_tempo, _ = ensure_oracle_input(
            tab,
            MEDIAN_HAND,
            tempo_bpm=tempo_bpm,
            beats_per_bar=_BEATS_PER_MEASURE,
        )
    except OracleInputError:
        raise GuitarProExportError(
            GuitarProExportCode.INVALID_TAB,
            "Tab or tempo is outside the canonical export input domain",
        ) from None

    tempo = _gp5_tempo(normalized_tempo)
    safe_title = _validated_title(title)
    _assert_strings_do_not_overlap(canonical.notes)
    song = _build_song(canonical, tempo=tempo, title=safe_title)

    stream = BytesIO()
    try:
        gp.write(song, stream, version=GUITAR_PRO_FILE_VERSION, encoding=_GP_ENCODING)
    except (AttributeError, KeyError, OSError, TypeError, ValueError, gp.GPException) as exc:
        raise GuitarProExportError(
            GuitarProExportCode.SERIALIZATION_FAILED,
            "the validated score could not be serialized as GP5",
        ) from exc
    return stream.getvalue()


__all__ = [
    "GUITAR_PRO_EXPORT_VERSION",
    "GUITAR_PRO_FILE_VERSION",
    "GuitarProExportCode",
    "GuitarProExportError",
    "render_guitar_pro",
]
