"""Explicit-role normalizers for public benchmark arrangement sources.

The public score importers keep their deliberately narrow contracts.  Benchmark
adapters for broader, audited sources expose notes as named
:class:`ArrangementStream` objects; this module only applies a checked-in map
from those stable selectors to ``melody``/``bass``/``harmony``.  It never picks
a part by pitch, order, name, or density and never derives chord symbols.

The convenience wrappers for :class:`~fretsure.importers.contracts.ImportSuccess`
remain useful for sources inside the public importer subset.  In particular, the
MIDI wrapper accepts only ``midi@0.1.0`` output and therefore cannot broaden the
public MIDI entry point.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal, NoReturn, cast

from fretsure.bench.contracts import BenchmarkContractError
from fretsure.bench.corpus import MAX_ROLE_MAP_ENTRIES, MAX_SHORT_TEXT_CHARS
from fretsure.importers.contracts import ImportProvenance, ImportSuccess
from fretsure.importers.midi import MIDI_IMPORTER_VERSION
from fretsure.importers.musicxml import MUSICXML_IMPORTER_VERSION
from fretsure.ir import (
    MAX_IR_NOTES,
    ChordSymbol,
    IRInputError,
    Meta,
    MusicIR,
    Note,
    VoiceRole,
    snapshot_music_ir,
    validate_ir,
)

PublicArrangementLayer = Literal[
    "public_leadsheet",
    "public_classical",
    "public_midi",
]
ArrangementSourceFormat = Literal["musicxml", "mxl", "midi"]

PUBLIC_LEADSHEET_NORMALIZATION = ("public-leadsheet-explicit-role-map@0.1.0",)
PUBLIC_CLASSICAL_NORMALIZATION = ("public-classical-explicit-role-map@0.1.0",)
PUBLIC_MIDI_NORMALIZATION = ("public-midi-explicit-role-map@0.1.0",)

_ROLES = frozenset({"melody", "bass", "harmony"})
_NORMALIZATION_BY_LAYER: dict[PublicArrangementLayer, tuple[str, ...]] = {
    "public_leadsheet": PUBLIC_LEADSHEET_NORMALIZATION,
    "public_classical": PUBLIC_CLASSICAL_NORMALIZATION,
    "public_midi": PUBLIC_MIDI_NORMALIZATION,
}
_FORMATS_BY_LAYER: dict[PublicArrangementLayer, frozenset[str]] = {
    "public_leadsheet": frozenset({"musicxml", "mxl"}),
    "public_classical": frozenset({"musicxml", "mxl"}),
    "public_midi": frozenset({"midi"}),
}
_IMPORTER_BY_LAYER: dict[PublicArrangementLayer, str] = {
    "public_leadsheet": MUSICXML_IMPORTER_VERSION,
    "public_classical": MUSICXML_IMPORTER_VERSION,
    "public_midi": MIDI_IMPORTER_VERSION,
}


@dataclass(frozen=True, slots=True)
class ArrangementNote:
    """One unassigned source note inside an explicitly named stream."""

    onset: Fraction
    duration: Fraction
    pitch: int


@dataclass(frozen=True, slots=True)
class ArrangementStream:
    """A source part/voice/track whose selector is frozen in the corpus data."""

    selector: str
    notes: tuple[ArrangementNote, ...]


@dataclass(frozen=True, slots=True)
class ArrangementSource:
    """Strict adapter seam between an audited parser and role normalization.

    A MusicXML adapter should use selectors such as ``part:P1/voice:1``; a MIDI
    adapter should retain an explicit track/channel identity.  The normalizer
    treats selector text as identity only and assigns no semantics to it.
    """

    streams: tuple[ArrangementStream, ...]
    chords: tuple[ChordSymbol, ...]
    meta: Meta
    source_format: ArrangementSourceFormat


@dataclass(frozen=True, slots=True)
class NormalizedArrangement:
    """Normalized IR plus the exact provenance fields stored in a corpus item."""

    ir: MusicIR
    role_map: tuple[tuple[str, str], ...]
    normalization: tuple[str, ...]


def _fail(field: str, detail: str) -> NoReturn:
    raise BenchmarkContractError(field, detail)


def _snapshot_layer(value: object) -> PublicArrangementLayer:
    if type(value) is not str or value not in _NORMALIZATION_BY_LAYER:
        _fail("normalizer.layer", "must be a supported public arrangement layer")
    return value


def _selector(value: object, field: str) -> str:
    if type(value) is not str or not value:
        _fail(field, "must be a non-empty exact string")
    text = value
    if len(text) > MAX_SHORT_TEXT_CHARS:
        _fail(field, f"length exceeds {MAX_SHORT_TEXT_CHARS}")
    if unicodedata.normalize("NFC", text) != text:
        _fail(field, "must be NFC-normalized")
    return text


def snapshot_arrangement_source(value: object) -> ArrangementSource:
    """Validate and detach one adapter-produced multi-stream source."""

    if type(value) is not ArrangementSource:
        _fail("normalizer.source", "must be an exact ArrangementSource")
    source = value
    if type(source.source_format) is not str or source.source_format not in {
        "musicxml",
        "mxl",
        "midi",
    }:
        _fail("normalizer.source.source_format", "must be musicxml, mxl, or midi")
    source_format = source.source_format
    if type(source.streams) is not tuple:
        _fail("normalizer.source.streams", "must be an exact tuple")
    if not source.streams:
        _fail("normalizer.source.streams", "must contain at least one note stream")
    if len(source.streams) > MAX_ROLE_MAP_ENTRIES:
        _fail(
            "normalizer.source.streams",
            f"count exceeds {MAX_ROLE_MAP_ENTRIES}",
        )

    raw_notes: list[Note] = []
    stream_descriptors: list[tuple[str, int]] = []
    for stream_index, raw_stream in enumerate(source.streams):
        field = f"normalizer.source.streams[{stream_index}]"
        if type(raw_stream) is not ArrangementStream:
            _fail(field, "must be an exact ArrangementStream")
        stream = raw_stream
        selector = _selector(stream.selector, f"{field}.selector")
        if type(stream.notes) is not tuple:
            _fail(f"{field}.notes", "must be an exact tuple")
        if not stream.notes:
            _fail(f"{field}.notes", "must contain at least one note")
        if len(raw_notes) + len(stream.notes) > MAX_IR_NOTES:
            _fail(
                "normalizer.source.notes",
                f"count exceeds {MAX_IR_NOTES}",
            )
        for note_index, raw_note in enumerate(stream.notes):
            note_field = f"{field}.notes[{note_index}]"
            if type(raw_note) is not ArrangementNote:
                _fail(note_field, "must be an exact ArrangementNote")
            note = raw_note
            # snapshot_music_ir below applies the existing exact Fraction,
            # pitch, metadata, chord, and aggregate note-count contracts.
            raw_notes.append(Note(note.onset, note.duration, note.pitch, "harmony"))
        stream_descriptors.append((selector, len(stream.notes)))

    selectors = tuple(selector for selector, _count in stream_descriptors)
    if selectors != tuple(sorted(selectors)):
        _fail("normalizer.source.streams", "must be canonically ordered by selector")
    if len(selectors) != len(set(selectors)):
        _fail("normalizer.source.streams", "selectors must be unique")

    try:
        detached = snapshot_music_ir(MusicIR(tuple(raw_notes), source.chords, source.meta))
    except IRInputError as error:
        _fail(f"normalizer.source.{error.field}", error.detail)

    streams: list[ArrangementStream] = []
    offset = 0
    for selector, count in stream_descriptors:
        notes = tuple(
            ArrangementNote(note.onset, note.duration, note.pitch)
            for note in detached.notes[offset : offset + count]
        )
        streams.append(ArrangementStream(selector, notes))
        offset += count
    return ArrangementSource(
        tuple(streams),
        detached.chords,
        detached.meta,
        source_format,
    )


def _snapshot_imported(
    value: object,
    *,
    layer: PublicArrangementLayer,
) -> ImportSuccess:
    if type(value) is not ImportSuccess:
        _fail("normalizer.imported", "must be an exact ImportSuccess")
    imported = value
    provenance = imported.provenance
    if type(provenance) is not ImportProvenance:
        _fail(
            "normalizer.imported.provenance",
            "is required to bind the public source format",
        )
    if provenance.source_format not in _FORMATS_BY_LAYER[layer]:
        _fail(
            "normalizer.imported.provenance.source_format",
            f"source format is not valid for {layer}",
        )
    expected_importer = _IMPORTER_BY_LAYER[layer]
    if imported.importer_version != expected_importer:
        _fail(
            "normalizer.imported.importer_version",
            f"must equal {expected_importer}",
        )
    try:
        ir = snapshot_music_ir(imported.ir)
    except IRInputError as error:
        _fail(f"normalizer.imported.ir.{error.field}", error.detail)
    violations = validate_ir(ir)
    if violations:
        first = violations[0]
        _fail("normalizer.imported.ir", f"{first.kind}: {first.detail}")
    if layer == "public_midi" and (ir.chords or any(note.voice != "melody" for note in ir.notes)):
        _fail(
            "normalizer.imported.ir",
            "does not match the melody-only midi@0.1.0 contract",
        )
    return ImportSuccess(
        ir,
        imported.warnings,
        imported.importer_version,
        imported.sha256,
        provenance,
    )


def arrangement_source_from_import(
    imported: object,
    *,
    layer: PublicArrangementLayer,
) -> ArrangementSource:
    """Adapt one narrow public-importer success into named voice streams."""

    layer_snapshot = _snapshot_layer(layer)
    snapshot = _snapshot_imported(imported, layer=layer_snapshot)
    assert snapshot.provenance is not None
    notes_by_voice: dict[VoiceRole, list[ArrangementNote]] = {
        "melody": [],
        "bass": [],
        "harmony": [],
    }
    for note in snapshot.ir.notes:
        notes_by_voice[note.voice].append(ArrangementNote(note.onset, note.duration, note.pitch))
    streams = tuple(
        ArrangementStream(f"voice:{role}", tuple(notes_by_voice[role]))
        for role in ("bass", "harmony", "melody")
        if notes_by_voice[role]
    )
    return snapshot_arrangement_source(
        ArrangementSource(
            streams,
            snapshot.ir.chords,
            snapshot.ir.meta,
            snapshot.provenance.source_format,
        )
    )


def _snapshot_role_map(
    value: object,
    *,
    source: ArrangementSource,
) -> tuple[tuple[str, VoiceRole], ...]:
    if type(value) is not tuple:
        _fail("normalizer.role_map", "must be an exact tuple")
    raw = cast(tuple[object, ...], value)
    if not raw:
        _fail("normalizer.role_map", "an explicit role map is required")
    if len(raw) > MAX_ROLE_MAP_ENTRIES:
        _fail(
            "normalizer.role_map",
            f"count exceeds {MAX_ROLE_MAP_ENTRIES}",
        )

    role_map: list[tuple[str, VoiceRole]] = []
    for index, raw_pair in enumerate(raw):
        field = f"normalizer.role_map[{index}]"
        if type(raw_pair) is not tuple or len(raw_pair) != 2:
            _fail(field, "must be an exact (source selector, target role) tuple")
        pair = cast(tuple[object, object], raw_pair)
        selector = _selector(pair[0], f"{field}.source")
        target = pair[1]
        if type(target) is not str or target not in _ROLES:
            _fail(
                f"{field}.target",
                "target role must be melody, bass, or harmony",
            )
        role_map.append((selector, cast(VoiceRole, target)))

    snapshot = tuple(role_map)
    if snapshot != tuple(sorted(snapshot)):
        _fail("normalizer.role_map", "must be canonically ordered")
    selectors = tuple(selector for selector, _target in snapshot)
    if len(selectors) != len(set(selectors)):
        _fail("normalizer.role_map", "each source selector must map to one target")

    present = {stream.selector for stream in source.streams}
    if set(selectors) != present:
        _fail(
            "normalizer.role_map",
            "must cover every source voice exactly and contain no unused selector",
        )
    return snapshot


def _apply_role_map(
    source: ArrangementSource,
    role_map: tuple[tuple[str, VoiceRole], ...],
) -> MusicIR:
    by_selector = dict(role_map)
    notes = tuple(
        sorted(
            (
                Note(note.onset, note.duration, note.pitch, by_selector[stream.selector])
                for stream in source.streams
                for note in stream.notes
            ),
            key=lambda note: (
                note.onset,
                note.pitch,
                note.duration,
                note.voice,
            ),
        )
    )
    chords = tuple(
        sorted(
            source.chords,
            key=lambda chord: (
                chord.onset,
                chord.root_pc,
                chord.symbol,
                tuple(sorted(chord.pitch_classes)),
            ),
        )
    )
    try:
        result = snapshot_music_ir(MusicIR(notes, chords, source.meta))
    except IRInputError as error:
        _fail(f"normalizer.output.{error.field}", error.detail)
    violations = validate_ir(result)
    if violations:
        first = violations[0]
        _fail("normalizer.output", f"{first.kind}: {first.detail}")
    return result


def normalize_arrangement_source(
    source: object,
    role_map: object,
    *,
    layer: PublicArrangementLayer,
) -> NormalizedArrangement:
    """Normalize adapter-produced streams using a complete explicit role map."""

    layer_snapshot = _snapshot_layer(layer)
    source_snapshot = snapshot_arrangement_source(source)
    if source_snapshot.source_format not in _FORMATS_BY_LAYER[layer_snapshot]:
        _fail(
            "normalizer.source.source_format",
            f"source format is not valid for {layer_snapshot}",
        )
    role_map_snapshot = _snapshot_role_map(role_map, source=source_snapshot)
    result = _apply_role_map(source_snapshot, role_map_snapshot)
    return NormalizedArrangement(
        result,
        tuple((selector, target) for selector, target in role_map_snapshot),
        _NORMALIZATION_BY_LAYER[layer_snapshot],
    )


def normalize_public_arrangement(
    imported: object,
    role_map: object,
    *,
    layer: PublicArrangementLayer,
) -> NormalizedArrangement:
    """Normalize one public-importer success with no role inference."""

    layer_snapshot = _snapshot_layer(layer)
    source = arrangement_source_from_import(imported, layer=layer_snapshot)
    return normalize_arrangement_source(source, role_map, layer=layer_snapshot)


def normalize_public_leadsheet(
    imported: object,
    role_map: object,
) -> NormalizedArrangement:
    """Normalize a public lead-sheet import using its checked-in role map."""

    return normalize_public_arrangement(
        imported,
        role_map,
        layer="public_leadsheet",
    )


def normalize_public_classical(
    imported: object,
    role_map: object,
) -> NormalizedArrangement:
    """Normalize a narrow public-classical import using its checked-in role map."""

    return normalize_public_arrangement(
        imported,
        role_map,
        layer="public_classical",
    )


def normalize_public_midi(
    imported: object,
    role_map: object,
) -> NormalizedArrangement:
    """Normalize a strict MIDI import without expanding public MIDI support."""

    return normalize_public_arrangement(
        imported,
        role_map,
        layer="public_midi",
    )


__all__ = [
    "PUBLIC_CLASSICAL_NORMALIZATION",
    "PUBLIC_LEADSHEET_NORMALIZATION",
    "PUBLIC_MIDI_NORMALIZATION",
    "ArrangementNote",
    "ArrangementSource",
    "ArrangementSourceFormat",
    "ArrangementStream",
    "NormalizedArrangement",
    "PublicArrangementLayer",
    "arrangement_source_from_import",
    "normalize_arrangement_source",
    "normalize_public_arrangement",
    "normalize_public_classical",
    "normalize_public_leadsheet",
    "normalize_public_midi",
    "snapshot_arrangement_source",
]
