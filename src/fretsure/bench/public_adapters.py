"""Pinned-source adapters that expose explicit streams to Task 5 normalizers.

These adapters are benchmark-only.  They use the exact optional ``music21`` pin,
perform no role or harmony inference, and do not change the public MusicXML/MIDI
importer contracts.
"""

from __future__ import annotations

import math
import unicodedata
from fractions import Fraction
from importlib import import_module
from typing import Any, NoReturn, cast

from fretsure.bench.normalizers import (
    ArrangementNote,
    ArrangementSource,
    ArrangementSourceFormat,
    ArrangementStream,
    snapshot_arrangement_source,
)
from fretsure.importers._mxl_container import MXLContainerPayload, read_mxl_container
from fretsure.importers.contracts import DEFAULT_LIMITS, ImportFailure, ImportLimits
from fretsure.ir import ChordSymbol, Meta

MAX_PUBLIC_ADAPTER_BYTES = 16 * 1024 * 1024
MAX_PUBLIC_ADAPTER_PARTS = 256
MAX_PUBLIC_ADAPTER_TEXT = 2_048
BENCHMARK_PUBLIC_ADAPTER_VERSION = "benchmark-public-adapter@0.1.0"
BENCHMARK_PUBLIC_ROUTER_VERSION = "benchmark-public-router@0.1.0"
PUBLIC_MIDI_ADAPTER_NORMALIZATION = (
    "music21-10.5.0-no-quantize-explicit-tie-coalescing-part-name-selection"
)
PUBLIC_MUSICXML_ADAPTER_NORMALIZATION = "music21-10.5.0-explicit-tie-coalescing-part-selection"


class PublicAdapterError(ValueError):
    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid public adapter {field}: {detail}")


def _fail(field: str, detail: str) -> NoReturn:
    raise PublicAdapterError(field, detail)


def _text(value: object, field: str) -> str:
    if type(value) is not str or not value or len(value) > MAX_PUBLIC_ADAPTER_TEXT:
        _fail(field, "must be bounded nonempty text")
    if "\x00" in value or unicodedata.normalize("NFC", value) != value:
        _fail(field, "must be NUL-free NFC text")
    return value


def _fraction(value: object, field: str, *, positive: bool) -> Fraction:
    result: Fraction
    if type(value) is int:
        result = Fraction(value, 1)
    elif type(value) is float and math.isfinite(value):
        result = Fraction(str(value))
    elif isinstance(value, Fraction):
        result = Fraction(value.numerator, value.denominator)
    else:
        numerator = getattr(value, "numerator", None)
        denominator = getattr(value, "denominator", None)
        if type(numerator) is not int or type(denominator) is not int or denominator <= 0:
            _fail(field, "music21 returned a non-rational value")
        result = Fraction(numerator, denominator)
    if (positive and result <= 0) or (not positive and result < 0):
        _fail(field, "music21 returned an out-of-range rational value")
    if result.numerator.bit_length() > 256 or result.denominator.bit_length() > 256:
        _fail(field, "rational value exceeds the 256-bit envelope")
    return result


def _midi_pitch(value: object, field: str) -> int:
    if type(value) is int:
        numeric = float(value)
    elif type(value) is float and math.isfinite(value):
        numeric = value
    else:
        _fail(field, "music21 returned a non-numeric pitch")
    result = round(numeric)
    if numeric != float(result) or not 0 <= result <= 127:
        _fail(field, "music21 returned a non-integral MIDI pitch")
    return result


def _root_xml(data: bytes, source_format: ArrangementSourceFormat, limits: ImportLimits) -> bytes:
    if source_format != "mxl":
        return data
    payload = read_mxl_container(data, limits)
    if isinstance(payload, ImportFailure):
        code = payload.diagnostics[0].code.value if payload.diagnostics else "INVALID_MXL"
        _fail("data", f"MXL container was rejected ({code})")
    assert isinstance(payload, MXLContainerPayload)
    return payload.root_bytes


def _midi_duration(midi_file: Any) -> Fraction:
    ticks_per_quarter = getattr(midi_file, "ticksPerQuarterNote", None)
    if type(ticks_per_quarter) is not int or ticks_per_quarter <= 0:
        _fail("midi.duration", "music21 returned invalid ticks-per-quarter")
    tracks = tuple(getattr(midi_file, "tracks", ()))
    if not tracks:
        _fail("midi.duration", "music21 returned no MIDI tracks")
    track_ends: list[int] = []
    for track_index, track in enumerate(tracks):
        tick = 0
        for event_index, event in enumerate(tuple(getattr(track, "events", ()))):
            is_delta_time = getattr(event, "isDeltaTime", None)
            if callable(is_delta_time) and is_delta_time():
                delta = getattr(event, "time", None)
                if type(delta) is not int or delta < 0:
                    _fail(
                        f"midi.tracks[{track_index}].events[{event_index}]",
                        "music21 returned an invalid delta time",
                    )
                tick += delta
        track_ends.append(tick)
    return _fraction(Fraction(max(track_ends), ticks_per_quarter), "midi.duration", positive=True)


def _load_score(
    data: bytes,
    source_format: ArrangementSourceFormat,
    limits: ImportLimits,
) -> tuple[Any, Fraction | None]:
    if type(limits) is not ImportLimits:
        _fail("limits", "must be an exact ImportLimits")
    format_limit = {
        "musicxml": limits.max_bytes,
        "mxl": limits.max_mxl_archive_bytes,
        "midi": limits.max_midi_bytes,
    }[source_format]
    if (
        type(data) is not bytes
        or not data
        or len(data) > min(MAX_PUBLIC_ADAPTER_BYTES, format_limit)
    ):
        _fail("data", "must be nonempty exact bytes within the adapter limit")
    try:
        if source_format == "midi":
            midi_module = import_module("music21.midi")
            translate = import_module("music21.midi.translate")
        else:
            converter = import_module("music21.converter")
    except ModuleNotFoundError:
        _fail("dependency", "music21 is required by the benchmark adapter")
    try:
        if source_format in {"musicxml", "mxl"}:
            root = _root_xml(data, source_format, limits)
            return converter.parseData(root.decode("utf-8"), format="musicxml"), None
        midi_file = midi_module.MidiFile()
        midi_file.readstr(data)
        return (
            translate.midiFileToStream(midi_file, quantizePost=False),
            _midi_duration(midi_file),
        )
    except PublicAdapterError:
        raise
    except Exception as error:
        _fail("data", f"music21 rejected the pinned source ({type(error).__name__})")


def _part_selector(part: Any, source_format: ArrangementSourceFormat, index: int) -> str:
    if source_format == "midi":
        name = getattr(part, "partName", None)
        if type(name) is not str or not name:
            _fail(f"parts[{index}]", "MIDI part lacks an explicit track name")
        return _text(f"part-name:{name}", f"parts[{index}].selector")
    identifier = getattr(part, "id", None)
    if type(identifier) is not str or not identifier:
        _fail(f"parts[{index}]", "MusicXML part lacks a stable parser identity")
    return _text(f"part:{identifier}", f"parts[{index}].selector")


def _part_notes(part: Any, index: int) -> tuple[ArrangementNote, ...]:
    note_module = import_module("music21.note")
    chord_module = import_module("music21.chord")
    harmony_module = import_module("music21.harmony")
    note_class = cast(type[object], note_module.Note)
    chord_class = cast(type[object], chord_module.Chord)
    harmony_class = cast(type[object], harmony_module.Harmony)
    strip_ties = getattr(part, "stripTies", None)
    if not callable(strip_ties):
        _fail(f"parts[{index}]", "music21 part cannot merge explicit ties")
    part_without_ties = strip_ties(inPlace=False, matchByPitch=False)
    flatten = getattr(part_without_ties, "flatten", None)
    if not callable(flatten):
        _fail(f"parts[{index}]", "music21 part cannot be flattened")
    stream = flatten()
    raw_notes = getattr(stream, "notes", None)
    if raw_notes is None:
        _fail(f"parts[{index}]", "music21 part has no note collection")
    notes: list[ArrangementNote] = []
    for event_index, event in enumerate(raw_notes):
        if isinstance(event, harmony_class):
            continue
        onset = _fraction(
            getattr(event, "offset", None),
            f"parts[{index}].notes[{event_index}].onset",
            positive=False,
        )
        duration_object = getattr(event, "duration", None)
        duration = _fraction(
            getattr(duration_object, "quarterLength", None),
            f"parts[{index}].notes[{event_index}].duration",
            positive=True,
        )
        if isinstance(event, note_class):
            pitch = getattr(getattr(event, "pitch", None), "midi", None)
            notes.append(
                ArrangementNote(
                    onset,
                    duration,
                    _midi_pitch(pitch, f"parts[{index}].notes[{event_index}].pitch"),
                )
            )
        elif isinstance(event, chord_class):
            pitches = tuple(getattr(event, "pitches", ()))
            if not pitches:
                _fail(f"parts[{index}].notes[{event_index}]", "empty chord event")
            for pitch_index, pitch in enumerate(pitches):
                notes.append(
                    ArrangementNote(
                        onset,
                        duration,
                        _midi_pitch(
                            getattr(pitch, "midi", None),
                            f"parts[{index}].notes[{event_index}].pitches[{pitch_index}]",
                        ),
                    )
                )
        else:
            _fail(f"parts[{index}].notes[{event_index}]", "unsupported music21 note event")
    if not notes:
        _fail(f"parts[{index}]", "part contains no arrangement notes")
    return tuple(sorted(notes, key=lambda note: (note.onset, note.pitch, note.duration)))


def _score_offset(value: Any, score: Any, field: str) -> Fraction:
    get_offset = getattr(value, "getOffsetInHierarchy", None)
    if not callable(get_offset):
        _fail(field, "music21 element has no score-relative offset")
    return _fraction(get_offset(score), field, positive=False)


def _time_signature(score: Any) -> tuple[int, int]:
    meter_module = import_module("music21.meter")
    time_class = cast(type[object], meter_module.TimeSignature)
    values: set[tuple[Fraction, int, int]] = set()
    for value in score.recurse().getElementsByClass(time_class):
        values.add(
            (
                _score_offset(value, score, "time_signature.offset"),
                cast(int, getattr(value, "numerator", None)),
                cast(int, getattr(value, "denominator", None)),
            )
        )
    signatures = {
        (numerator, denominator) for offset, numerator, denominator in values if offset == 0
    }
    if len(signatures) != 1 or any(offset != 0 for offset, _n, _d in values):
        _fail("time_signature", "source must have one unchanged explicit time signature")
    numerator, denominator = next(iter(signatures))
    if type(numerator) is not int or type(denominator) is not int:
        _fail("time_signature", "music21 returned non-integer meter values")
    return numerator, denominator


def _tempo(score: Any) -> float:
    tempo_module = import_module("music21.tempo")
    tempo_class = cast(type[object], tempo_module.MetronomeMark)
    values: set[tuple[Fraction, float]] = set()
    for value in score.recurse().getElementsByClass(tempo_class):
        get_quarter = getattr(value, "getQuarterBPM", None)
        bpm = None if not callable(get_quarter) else get_quarter()
        if type(bpm) is int:
            exact_bpm: int | float = bpm
        elif type(bpm) is float and math.isfinite(bpm):
            exact_bpm = bpm
        else:
            _fail("tempo", "source lacks an exact numeric sounding tempo")
        values.add(
            (
                _score_offset(value, score, "tempo.offset"),
                float(exact_bpm),
            )
        )
    tempos = {bpm for offset, bpm in values if offset == 0}
    if len(tempos) != 1 or any(offset != 0 for offset, _bpm in values):
        _fail("tempo", "source must have one unchanged explicit tempo")
    return next(iter(tempos))


def _key(score: Any) -> str:
    key_module = import_module("music21.key")
    key_class = cast(type[object], key_module.Key)
    signature_class = cast(type[object], key_module.KeySignature)
    values: set[tuple[Fraction, str]] = set()
    for value in score.recurse().getElementsByClass(signature_class):
        offset = _score_offset(value, score, "key.offset")
        if isinstance(value, key_class):
            tonic = getattr(getattr(value, "tonic", None), "name", None)
            mode = getattr(value, "mode", None)
            if type(tonic) is not str or mode not in {"major", "minor"}:
                _fail("key", "music21 returned an unsupported key")
            label = tonic.replace("-", "b") + ("m" if mode == "minor" else "")
        else:
            sharps = getattr(value, "sharps", None)
            if type(sharps) is not int or not -7 <= sharps <= 7:
                _fail("key", "music21 returned an unsupported key signature")
            label = f"key-signature:{sharps:+d}"
        values.add((offset, label))
    keys = {label for offset, label in values if offset == 0}
    if len(keys) != 1 or any(offset != 0 for offset, _label in values):
        _fail("key", "source must have one unchanged explicit key signature")
    return next(iter(keys))


def _chord_symbols(score: Any) -> tuple[ChordSymbol, ...]:
    harmony_module = import_module("music21.harmony")
    chord_symbol_class = cast(type[object], harmony_module.ChordSymbol)
    chords: list[ChordSymbol] = []
    for index, value in enumerate(score.recurse().getElementsByClass(chord_symbol_class)):
        root_method = getattr(value, "root", None)
        root = None if not callable(root_method) else root_method()
        root_pc = getattr(root, "pitchClass", None)
        pitches = tuple(getattr(value, "pitches", ()))
        figure = getattr(value, "figure", None)
        if type(root_pc) is not int or type(figure) is not str or not pitches:
            _fail(f"chords[{index}]", "music21 returned an incomplete explicit chord symbol")
        chords.append(
            ChordSymbol(
                _score_offset(value, score, f"chords[{index}].onset"),
                _text(figure, f"chords[{index}].symbol"),
                frozenset(
                    _midi_pitch(getattr(pitch, "midi", None), f"chords[{index}].pitch") % 12
                    for pitch in pitches
                ),
                root_pc,
            )
        )
    return tuple(sorted(chords, key=lambda value: (value.onset, value.root_pc, value.symbol)))


def arrangement_source_from_pinned_bytes(
    data: bytes,
    *,
    source_format: ArrangementSourceFormat,
    source_identity: str,
    license_expression: str,
    limits: ImportLimits = DEFAULT_LIMITS,
) -> ArrangementSource:
    """Expose every note-bearing parser part as a stable, unmapped stream."""

    if source_format not in {"musicxml", "mxl", "midi"}:
        _fail("source_format", "must be musicxml, mxl, or midi")
    identity = _text(source_identity, "source_identity")
    license_value = _text(license_expression, "license_expression")
    score, midi_duration = _load_score(data, source_format, limits)
    parts = tuple(getattr(score, "parts", ()))
    if not parts or len(parts) > MAX_PUBLIC_ADAPTER_PARTS:
        _fail("parts", "source has no parts or exceeds the part limit")
    streams = tuple(
        sorted(
            (
                ArrangementStream(
                    _part_selector(part, source_format, index),
                    _part_notes(part, index),
                )
                for index, part in enumerate(parts)
            ),
            key=lambda value: value.selector,
        )
    )
    if len({stream.selector for stream in streams}) != len(streams):
        _fail("parts", "parser part selectors are not unique")
    if midi_duration is None:
        score_duration = getattr(getattr(score, "duration", None), "quarterLength", None)
        duration = _fraction(score_duration, "duration", positive=True)
    else:
        duration = midi_duration
    metadata = getattr(score, "metadata", None)
    raw_title = None if metadata is None else getattr(metadata, "title", None)
    title = identity if type(raw_title) is not str or not raw_title else _text(raw_title, "title")
    return snapshot_arrangement_source(
        ArrangementSource(
            streams,
            _chord_symbols(score),
            Meta(
                _key(score),
                _time_signature(score),
                _tempo(score),
                identity,
                title,
                license_value,
                duration_beats=duration,
            ),
            source_format,
        )
    )


__all__ = [
    "BENCHMARK_PUBLIC_ADAPTER_VERSION",
    "BENCHMARK_PUBLIC_ROUTER_VERSION",
    "MAX_PUBLIC_ADAPTER_BYTES",
    "PUBLIC_MIDI_ADAPTER_NORMALIZATION",
    "PUBLIC_MUSICXML_ADAPTER_NORMALIZATION",
    "PublicAdapterError",
    "arrangement_source_from_pinned_bytes",
]
