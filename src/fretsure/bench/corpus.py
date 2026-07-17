"""Strict, versioned benchmark notegraphs and corpus snapshots.

The v2 writer always emits an explicit schema.  The reader retains one narrowly
defined compatibility path for the historical unversioned notegraph, but never
coerces artifact values through ``int(value)``, ``float(value)``, or
``Fraction(value)``.  Corpus snapshots detach the MusicIR, bind provenance and
evidence availability, and reject duplicate or split-leaking musical families.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Any, NoReturn, cast

from fretsure.bench.contracts import (
    BENCHMARK_CORPUS_VERSION,
    BENCHMARK_NOTEGRAPH_VERSION,
    BenchmarkContractError,
    canonical_sha256,
)
from fretsure.bench.generator import (
    GENERATOR_VERSION,
    GenConfig,
    GeneratorInputError,
    ProceduralVariation,
    generate_procedural_variant,
    procedural_variations,
    snapshot_gen_config,
)
from fretsure.ir import (
    MAX_IR_CHORDS,
    MAX_IR_FRACTION_COMPONENT_BITS,
    MAX_IR_NOTES,
    MAX_IR_TEXT_CHARS,
    ChordSymbol,
    IRInputError,
    Meta,
    MusicIR,
    Note,
    VoiceRole,
    snapshot_music_ir,
    validate_ir,
)

MAX_CORPUS_ITEMS = 10_000
MAX_CORPUS_TEXT_CHARS = 1_000_000
# A note expands to a multi-field JSON object, so these aggregate limits stay
# comfortably below the shared one-million-node / 64-MiB artifact ceilings.
MAX_CORPUS_TOTAL_NOTES = 80_000
MAX_CORPUS_TOTAL_CHORDS = 20_000
MAX_IDENTIFIER_CHARS = 128
MAX_SHORT_TEXT_CHARS = 2_048
MAX_ROLE_MAP_ENTRIES = 256
MAX_NORMALIZATION_STEPS = 256
MAX_SIGNED_SEED = 2**63 - 1
PRIMARY_PROCEDURAL_FAMILY_TARGET = 500
PRIMARY_PROCEDURAL_BASE_SEED = 20_260_717
PRIMARY_PROCEDURAL_KEYS = ("C", "G", "D", "A", "E", "F", "Bb", "B")
PRIMARY_PROCEDURAL_METERS = ((4, 4), (3, 4), (6, 8))
PRIMARY_PROCEDURAL_TEMPOS = (72.0, 84.0, 96.0, 108.0, 120.0)

_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+\-]*\Z")
_FRACTION_RE = re.compile(r"(0|-?[1-9][0-9]*)(?:/([1-9][0-9]*))?\Z")
_LAYERS = frozenset(
    {
        "procedural",
        "public_leadsheet",
        "public_classical",
        "public_midi",
        "checker_tab",
    }
)
_VOICES = frozenset({"melody", "bass", "harmony"})
_PROCEDURAL_LEVELS = frozenset({"low", "medium", "high"})
_PUBLIC_POLYPHONY_LEVELS = frozenset({"monophonic", "polyphonic"})
_LICENSE_STATUSES = frozenset({"verified", "generated", "excluded", "unavailable"})
_SOURCE_FORMATS_BY_LAYER = {
    "procedural": frozenset({"procedural"}),
    "public_leadsheet": frozenset({"musicxml", "mxl"}),
    "public_classical": frozenset({"musicxml", "mxl"}),
    "public_midi": frozenset({"midi"}),
    "checker_tab": frozenset({"tab"}),
}


def _fail(field: str, detail: str) -> NoReturn:
    raise BenchmarkContractError(field, detail)


def _exact_dict(value: object, field: str, keys: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict:
        _fail(field, "must be an exact object")
    result = cast(dict[object, object], value)
    if dict.__len__(result) != len(keys):
        if dict.__len__(result) <= len(keys) + 8:
            raw_keys = tuple(dict.keys(result))
            if all(type(key) is str for key in raw_keys):
                actual = frozenset(cast(str, key) for key in raw_keys)
                missing = sorted(keys - actual)
                extra_count = len(actual - keys)
                _fail(
                    field,
                    f"key set mismatch (missing={missing}, extra_count={extra_count})",
                )
        _fail(field, "key set does not match the frozen schema")
    try:
        items = tuple(dict.items(result))
    except RuntimeError:
        _fail(field, "object changed while it was being read")
    if any(type(key) is not str for key, _child in items):
        _fail(field, "keys must be exact strings")
    actual = frozenset(cast(str, key) for key, _child in items)
    if actual != keys:
        missing = sorted(keys - actual)
        extra_count = len(actual - keys)
        _fail(
            field,
            f"key set mismatch (missing={missing}, extra_count={extra_count})",
        )
    return {cast(str, key): child for key, child in items}


def _exact_list(value: object, field: str, *, maximum: int) -> list[object]:
    if type(value) is not list:
        _fail(field, "must be an exact array")
    result = cast(list[object], value)
    snapshot = result[: maximum + 1]
    if len(snapshot) > maximum:
        _fail(field, f"count exceeds {maximum}")
    return snapshot


def _nfc_text(value: object, field: str, *, maximum: int, empty: bool = True) -> str:
    if type(value) is not str:
        _fail(field, "must be an exact string")
    text = value
    if not empty and not text:
        _fail(field, "must not be empty")
    if len(text) > maximum:
        _fail(field, f"length exceeds {maximum}")
    if "\x00" in text:
        _fail(field, "must not contain NUL")
    if unicodedata.normalize("NFC", text) != text:
        _fail(field, "must be NFC-normalized")
    return text


def _identifier(value: object, field: str) -> str:
    text = _nfc_text(value, field, maximum=MAX_IDENTIFIER_CHARS, empty=False)
    if _IDENTIFIER_RE.fullmatch(text) is None:
        _fail(field, "must use the canonical identifier grammar")
    return text


def _optional_text(value: object, field: str, *, maximum: int = MAX_SHORT_TEXT_CHARS) -> str | None:
    if value is None:
        return None
    return _nfc_text(value, field, maximum=maximum, empty=False)


def _optional_identifier(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _identifier(value, field)


def _sha256(value: object, field: str, *, optional: bool) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str:
        _fail(field, "must be a lowercase 64-character SHA-256")
    if _SHA256_RE.fullmatch(value) is None:
        _fail(field, "must be a lowercase 64-character SHA-256")
    return value


def _exact_int(value: object, field: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int:
        _fail(field, "must be an exact integer (bool is not accepted)")
    result = value
    if not minimum <= result <= maximum:
        _fail(field, f"must be within {minimum}..{maximum}")
    return result


def _ascii_uint(token: str, field: str) -> int:
    value = 0
    for char in token:
        value = value * 10 + (ord(char) - ord("0"))
        if value.bit_length() > MAX_IR_FRACTION_COMPONENT_BITS:
            _fail(field, "fraction component exceeds the 256-bit limit")
    return value


def _fraction_token(value: object, field: str, *, positive: bool) -> Fraction:
    if type(value) is not str:
        _fail(field, "must be a canonical fraction string")
    token = value
    if len(token) > 160:
        _fail(field, "fraction token is too long")
    match = _FRACTION_RE.fullmatch(token)
    if match is None:
        _fail(field, "must be a reduced canonical fraction string")
    numerator_token = match.group(1)
    negative = numerator_token.startswith("-")
    magnitude_token = numerator_token[1:] if negative else numerator_token
    numerator = _ascii_uint(magnitude_token, field)
    if negative:
        numerator = -numerator
    denominator_token = match.group(2)
    denominator = 1 if denominator_token is None else _ascii_uint(denominator_token, field)
    if denominator_token is not None and denominator == 1:
        _fail(field, "denominator 1 must be omitted")
    if math.gcd(numerator, denominator) != 1:
        _fail(field, "fraction must be reduced")
    if positive and numerator <= 0:
        _fail(field, "must be positive")
    if not positive and numerator < 0:
        _fail(field, "must be non-negative")
    return Fraction(numerator, denominator)


def _validated_ir(value: object, field: str = "ir") -> MusicIR:
    try:
        snapshot = snapshot_music_ir(value)
    except IRInputError as error:
        _fail(f"{field}.{error.field}", error.detail)
    violations = validate_ir(snapshot)
    if violations:
        first = violations[0]
        _fail(field, f"{first.kind}: {first.detail}")
    notes = tuple(
        sorted(snapshot.notes, key=lambda note: (note.onset, note.pitch, note.duration, note.voice))
    )
    chords = tuple(
        sorted(
            snapshot.chords,
            key=lambda chord: (
                chord.onset,
                chord.root_pc,
                chord.symbol,
                tuple(sorted(chord.pitch_classes)),
            ),
        )
    )
    return MusicIR(notes, chords, snapshot.meta)


def _meta_wire(ir: MusicIR) -> dict[str, object]:
    meta = ir.meta
    for name, value in (
        ("key", meta.key),
        ("source", meta.source),
        ("title", meta.title),
        ("license", meta.license),
    ):
        _nfc_text(value, f"meta.{name}", maximum=MAX_IR_TEXT_CHARS)
    return {
        "key": meta.key,
        "time_sig": [meta.time_sig[0], meta.time_sig[1]],
        "tempo_bpm": meta.tempo_bpm,
        "source": meta.source,
        "title": meta.title,
        "license": meta.license,
        "duration_beats": str(meta.duration_beats) if meta.duration_beats is not None else None,
    }


def ir_to_notegraph(ir: MusicIR) -> dict[str, Any]:
    """Return the canonical versioned notegraph for a valid MusicIR."""

    snapshot = _validated_ir(ir)
    notes: list[dict[str, object]] = []
    for note in snapshot.notes:
        notes.append(
            {
                "onset": str(note.onset),
                "duration": str(note.duration),
                "midi": note.pitch,
                "voice": note.voice,
            }
        )
    chords: list[dict[str, object]] = []
    for chord in snapshot.chords:
        _nfc_text(chord.symbol, "chords.symbol", maximum=MAX_IR_TEXT_CHARS)
        chords.append(
            {
                "onset": str(chord.onset),
                "symbol": chord.symbol,
                "pitch_classes": sorted(chord.pitch_classes),
                "root_pc": chord.root_pc,
            }
        )
    return {
        "schema": BENCHMARK_NOTEGRAPH_VERSION,
        "meta": _meta_wire(snapshot),
        "notes": notes,
        "chords": chords,
    }


def _parse_note(raw: object, index: int) -> Note:
    path = f"notes[{index}]"
    obj = _exact_dict(raw, path, frozenset({"onset", "duration", "midi", "voice"}))
    pitch = _exact_int(obj["midi"], f"{path}.midi", minimum=0, maximum=127)
    voice = _nfc_text(obj["voice"], f"{path}.voice", maximum=16, empty=False)
    if voice not in _VOICES:
        _fail(f"{path}.voice", "must be melody, bass, or harmony")
    return Note(
        _fraction_token(obj["onset"], f"{path}.onset", positive=False),
        _fraction_token(obj["duration"], f"{path}.duration", positive=True),
        pitch,
        cast(VoiceRole, voice),
    )


def _parse_chord(raw: object, index: int) -> ChordSymbol:
    path = f"chords[{index}]"
    obj = _exact_dict(
        raw,
        path,
        frozenset({"onset", "symbol", "pitch_classes", "root_pc"}),
    )
    raw_pitch_classes = _exact_list(
        obj["pitch_classes"], f"{path}.pitch_classes", maximum=12
    )
    pitch_classes = tuple(
        _exact_int(value, f"{path}.pitch_classes[{pc_index}]", minimum=0, maximum=11)
        for pc_index, value in enumerate(raw_pitch_classes)
    )
    if pitch_classes != tuple(sorted(set(pitch_classes))):
        _fail(f"{path}.pitch_classes", "must be unique and canonically ordered")
    return ChordSymbol(
        _fraction_token(obj["onset"], f"{path}.onset", positive=False),
        _nfc_text(obj["symbol"], f"{path}.symbol", maximum=MAX_IR_TEXT_CHARS),
        frozenset(pitch_classes),
        _exact_int(obj["root_pc"], f"{path}.root_pc", minimum=0, maximum=11),
    )


def _parse_meta(raw: object, *, legacy: bool) -> Meta:
    required = {"key", "time_sig", "tempo_bpm", "source", "title", "license"}
    keys = frozenset(required if legacy else {*required, "duration_beats"})
    if legacy and type(raw) is dict and "duration_beats" in cast(dict[object, object], raw):
        keys = frozenset({*required, "duration_beats"})
    obj = _exact_dict(raw, "meta", keys)
    time_sig = _exact_list(obj["time_sig"], "meta.time_sig", maximum=2)
    if len(time_sig) != 2:
        _fail("meta.time_sig", "must contain exactly two integers")
    numerator = _exact_int(time_sig[0], "meta.time_sig[0]", minimum=1, maximum=32)
    denominator = _exact_int(time_sig[1], "meta.time_sig[1]", minimum=1, maximum=64)
    tempo = obj["tempo_bpm"]
    allowed_tempo_types = (int, float) if legacy else (float,)
    if type(tempo) not in allowed_tempo_types:
        _fail("meta.tempo_bpm", "must be an exact finite float")
    exact_tempo = cast(int | float, tempo)
    if not math.isfinite(exact_tempo) or not 1 <= exact_tempo <= 1_000:
        _fail("meta.tempo_bpm", "must be finite and within 1..1000 BPM")
    normalized_tempo = exact_tempo + 0.0
    raw_duration = obj.get("duration_beats")
    duration = (
        None
        if raw_duration is None
        else _fraction_token(raw_duration, "meta.duration_beats", positive=False)
    )
    return Meta(
        _nfc_text(obj["key"], "meta.key", maximum=MAX_IR_TEXT_CHARS),
        (numerator, denominator),
        normalized_tempo,
        _nfc_text(obj["source"], "meta.source", maximum=MAX_IR_TEXT_CHARS),
        _nfc_text(obj["title"], "meta.title", maximum=MAX_IR_TEXT_CHARS),
        _nfc_text(obj["license"], "meta.license", maximum=MAX_IR_TEXT_CHARS),
        duration,
    )


def notegraph_to_ir(obj: object, *, allow_legacy: bool = True) -> MusicIR:
    """Parse a strict v2 notegraph or the single historical unversioned shape."""

    if type(allow_legacy) is not bool:
        _fail("allow_legacy", "must be an exact bool")
    if type(obj) is not dict:
        _fail("$", "must be an exact object")
    raw = cast(dict[object, object], obj)
    legacy = "schema" not in raw
    if legacy:
        if not allow_legacy:
            _fail("schema", "legacy notegraphs are disabled")
        data = _exact_dict(obj, "$", frozenset({"meta", "notes", "chords"}))
    else:
        data = _exact_dict(obj, "$", frozenset({"schema", "meta", "notes", "chords"}))
        if data["schema"] != BENCHMARK_NOTEGRAPH_VERSION:
            _fail("schema", f"must equal {BENCHMARK_NOTEGRAPH_VERSION}")
    raw_notes = _exact_list(data["notes"], "notes", maximum=MAX_IR_NOTES)
    raw_chords = _exact_list(data["chords"], "chords", maximum=MAX_IR_CHORDS)
    ir = MusicIR(
        tuple(_parse_note(note, index) for index, note in enumerate(raw_notes)),
        tuple(_parse_chord(chord, index) for index, chord in enumerate(raw_chords)),
        _parse_meta(data["meta"], legacy=legacy),
    )
    snapshot = _validated_ir(ir)
    if not legacy:
        canonical = ir_to_notegraph(snapshot)
        if canonical != data:
            _fail("$", "v2 notegraph values and order must be canonical")
    return snapshot


def notegraph_sha256(value: MusicIR | object) -> str:
    ir = _validated_ir(value) if type(value) is MusicIR else notegraph_to_ir(value)
    return canonical_sha256(BENCHMARK_NOTEGRAPH_VERSION, ir_to_notegraph(ir))


@dataclass(frozen=True, slots=True)
class EvidenceAvailability:
    melody: bool
    bass: bool
    harmony: bool

    @property
    def signature(self) -> str:
        names = tuple(
            name
            for name, available in (
                ("melody", self.melody),
                ("bass", self.bass),
                ("harmony", self.harmony),
            )
            if available
        )
        return "+".join(names) if names else "none"


@dataclass(frozen=True, slots=True)
class LicenseProvenance:
    expression: str
    status: str
    redistribution: bool | None
    derivatives: bool | None
    provider_submission: bool | None


@dataclass(frozen=True, slots=True)
class GeneratorProvenance:
    version: str
    key: str
    meter: tuple[int, int]
    bars: int
    seed: int
    tempo_bpm: float


@dataclass(frozen=True, slots=True)
class CorpusProvenance:
    source_format: str
    source_sha256: str | None
    root_sha256: str | None
    router_version: str | None
    importer_version: str | None
    container_version: str | None
    source_url: str | None
    producer: str | None
    retrieval_date: str | None
    license: LicenseProvenance
    split: str
    role_map: tuple[tuple[str, str], ...]
    normalization: tuple[str, ...]
    generator: GeneratorProvenance | None


@dataclass(frozen=True)
class CorpusItem:
    ir: MusicIR
    layer: str
    genre: str
    difficulty: int
    item_id: str
    family_id: str | None = None
    cluster_id: str | None = None
    position: int | None = None
    provenance: CorpusProvenance | None = None
    evidence: EvidenceAvailability | None = None
    synthetic_complexity: str = "unrated"
    polyphony: str = "unrated"
    canary: str | None = None


@dataclass(frozen=True, slots=True)
class ProceduralCorpusConfig:
    """Frozen controls for the contamination-resistant primary corpus."""

    family_count: int = PRIMARY_PROCEDURAL_FAMILY_TARGET
    base_seed: int = PRIMARY_PROCEDURAL_BASE_SEED
    bars: int = 4
    split: str = "test"


def snapshot_procedural_corpus_config(value: object) -> ProceduralCorpusConfig:
    if type(value) is not ProceduralCorpusConfig:
        _fail("procedural_config", "must be an exact ProceduralCorpusConfig")
    config = value
    family_count = _exact_int(
        config.family_count,
        "procedural_config.family_count",
        minimum=1,
        maximum=MAX_CORPUS_ITEMS,
    )
    split = _identifier(config.split, "procedural_config.split")
    try:
        generator_config = snapshot_gen_config(
            GenConfig(seed=config.base_seed, bars=config.bars)
        )
    except GeneratorInputError as error:
        _fail(f"procedural_config.{error.field}", error.detail)
    return ProceduralCorpusConfig(
        family_count=family_count,
        base_seed=generator_config.seed,
        bars=generator_config.bars,
        split=split,
    )


def _procedural_source_descriptor(
    config: GenConfig,
    variation: ProceduralVariation,
    position: int,
) -> dict[str, object]:
    return {
        "generator_version": GENERATOR_VERSION,
        "index": position,
        "config": {
            "key": config.key,
            "meter": [config.meter[0], config.meter[1]],
            "bars": config.bars,
            "seed": config.seed,
            "tempo_bpm": config.tempo_bpm,
        },
        "variation": {
            "synthetic_complexity": variation.synthetic_complexity,
            "polyphony": variation.polyphony,
        },
    }


def procedural_source_sha256(
    config: object,
    variation: object,
    position: object,
) -> str:
    """Bind one generated item to its exact config, stratum, and ordered position."""

    try:
        generator = snapshot_gen_config(config)
    except GeneratorInputError as error:
        _fail(f"procedural_source.{error.field}", error.detail)
    if type(variation) is not ProceduralVariation:
        _fail("procedural_source.variation", "must be an exact ProceduralVariation")
    try:
        variation_snapshot = ProceduralVariation(
            variation.synthetic_complexity,
            variation.polyphony,
        )
    except GeneratorInputError as error:
        _fail(f"procedural_source.{error.field}", error.detail)
    position_snapshot = _exact_int(
        position,
        "procedural_source.position",
        minimum=0,
        maximum=MAX_CORPUS_ITEMS - 1,
    )
    return canonical_sha256(
        BENCHMARK_CORPUS_VERSION,
        _procedural_source_descriptor(
            generator,
            variation_snapshot,
            position_snapshot,
        ),
    )


def _snapshot_bool(value: object, field: str, *, optional: bool) -> bool | None:
    if value is None and optional:
        return None
    if type(value) is not bool:
        _fail(field, "must be an exact bool")
    return value


def _snapshot_license(
    value: object,
    field: str = "provenance.license",
    *,
    complete: bool,
) -> LicenseProvenance:
    if type(value) is not LicenseProvenance:
        _fail(field, "must be an exact LicenseProvenance")
    license_value = value
    expression = _nfc_text(
        license_value.expression, f"{field}.expression", maximum=MAX_SHORT_TEXT_CHARS, empty=False
    )
    status = _identifier(license_value.status, f"{field}.status")
    if status not in _LICENSE_STATUSES:
        _fail(f"{field}.status", "is not a supported license status")
    snapshot = LicenseProvenance(
        expression,
        status,
        _snapshot_bool(license_value.redistribution, f"{field}.redistribution", optional=True),
        _snapshot_bool(license_value.derivatives, f"{field}.derivatives", optional=True),
        _snapshot_bool(
            license_value.provider_submission, f"{field}.provider_submission", optional=True
        ),
    )
    permissions = (
        snapshot.redistribution,
        snapshot.derivatives,
        snapshot.provider_submission,
    )
    if complete and any(permission is None for permission in permissions):
        _fail(field, "strict provenance requires all three license permissions")
    if complete and status in {"verified", "generated"} and expression == "NOASSERTION":
        _fail(f"{field}.expression", "must be explicit for a verified/generated license")
    return snapshot


def _snapshot_generator(value: object, field: str = "provenance.generator") -> GeneratorProvenance:
    if type(value) is not GeneratorProvenance:
        _fail(field, "must be an exact GeneratorProvenance")
    generator = value
    version = _identifier(generator.version, f"{field}.version")
    if version != GENERATOR_VERSION:
        _fail(f"{field}.version", f"must equal {GENERATOR_VERSION}")
    try:
        config = snapshot_gen_config(
            GenConfig(
                key=generator.key,
                meter=generator.meter,
                bars=generator.bars,
                seed=generator.seed,
                tempo_bpm=generator.tempo_bpm,
            )
        )
    except GeneratorInputError as error:
        _fail(f"{field}.{error.field}", error.detail)
    return GeneratorProvenance(
        version,
        config.key,
        config.meter,
        config.bars,
        config.seed,
        config.tempo_bpm,
    )


def _snapshot_evidence(
    value: object,
    ir: MusicIR,
    field: str = "evidence",
    *,
    layer: str,
) -> EvidenceAvailability:
    if type(value) is not EvidenceAvailability:
        _fail(field, "must be an exact EvidenceAvailability")
    evidence = value
    result = EvidenceAvailability(
        cast(bool, _snapshot_bool(evidence.melody, f"{field}.melody", optional=False)),
        cast(bool, _snapshot_bool(evidence.bass, f"{field}.bass", optional=False)),
        cast(bool, _snapshot_bool(evidence.harmony, f"{field}.harmony", optional=False)),
    )
    expected = (
        EvidenceAvailability(False, False, False)
        if layer == "checker_tab"
        else EvidenceAvailability(
            melody=any(note.voice == "melody" for note in ir.notes),
            bass=bool(ir.chords),
            harmony=bool(ir.chords)
            or any(note.voice in {"bass", "harmony"} for note in ir.notes),
        )
    )
    if result != expected:
        _fail(field, "does not match the authoritative source evidence in the notegraph")
    return result


def _snapshot_provenance(
    value: object,
    *,
    layer: str,
    complete: bool,
    field: str = "provenance",
) -> CorpusProvenance:
    if type(value) is not CorpusProvenance:
        _fail(field, "must be an exact CorpusProvenance")
    provenance = value
    source_format = _identifier(provenance.source_format, f"{field}.source_format")
    allowed_formats = _SOURCE_FORMATS_BY_LAYER[layer]
    if complete and source_format not in allowed_formats:
        _fail(
            f"{field}.source_format",
            f"must be one of {sorted(allowed_formats)} for layer {layer}",
        )
    source_sha = _sha256(provenance.source_sha256, f"{field}.source_sha256", optional=True)
    root_sha = _sha256(provenance.root_sha256, f"{field}.root_sha256", optional=True)
    retrieval_date = _optional_text(
        provenance.retrieval_date,
        f"{field}.retrieval_date",
        maximum=10,
    )
    if retrieval_date is not None and _DATE_RE.fullmatch(retrieval_date) is None:
        _fail(f"{field}.retrieval_date", "must use YYYY-MM-DD")
    if retrieval_date is not None:
        try:
            date.fromisoformat(retrieval_date)
        except ValueError:
            _fail(f"{field}.retrieval_date", "must be a real calendar date")
    if type(provenance.role_map) is not tuple:
        _fail(f"{field}.role_map", "must be an exact tuple")
    if len(provenance.role_map) > MAX_ROLE_MAP_ENTRIES:
        _fail(f"{field}.role_map", f"count exceeds {MAX_ROLE_MAP_ENTRIES}")
    role_map: list[tuple[str, str]] = []
    for index, pair in enumerate(provenance.role_map):
        pair_field = f"{field}.role_map[{index}]"
        if type(pair) is not tuple or len(pair) != 2:
            _fail(pair_field, "must be an exact (source, role) tuple")
        source = _nfc_text(
            pair[0],
            f"{pair_field}.source",
            maximum=MAX_SHORT_TEXT_CHARS,
            empty=False,
        )
        role = _nfc_text(pair[1], f"{pair_field}.role", maximum=16, empty=False)
        if role not in _VOICES:
            _fail(f"{pair_field}.role", "must be melody, bass, or harmony")
        role_map.append((source, role))
    if tuple(role_map) != tuple(sorted(set(role_map))):
        _fail(f"{field}.role_map", "must be unique and canonically ordered")
    if complete and layer.startswith("public_") and not role_map:
        _fail(f"{field}.role_map", "is required for a public arrangement source")
    if type(provenance.normalization) is not tuple:
        _fail(f"{field}.normalization", "must be an exact tuple")
    if len(provenance.normalization) > MAX_NORMALIZATION_STEPS:
        _fail(f"{field}.normalization", f"count exceeds {MAX_NORMALIZATION_STEPS}")
    normalization = tuple(
        _nfc_text(
            step,
            f"{field}.normalization[{index}]",
            maximum=MAX_SHORT_TEXT_CHARS,
            empty=False,
        )
        for index, step in enumerate(provenance.normalization)
    )
    if normalization != tuple(sorted(set(normalization))):
        _fail(f"{field}.normalization", "must be unique and canonically ordered")
    if complete and layer != "procedural" and not normalization:
        _fail(f"{field}.normalization", "must record the explicit normalization path")
    generator = None
    if provenance.generator is not None:
        generator = _snapshot_generator(
            provenance.generator,
            f"{field}.generator",
        )
    if complete and layer == "procedural" and generator is None:
        _fail(f"{field}.generator", "is required for a procedural corpus item")
    if complete and layer == "procedural" and source_sha is None:
        _fail(f"{field}.source_sha256", "is required for a procedural corpus item")
    if complete and layer == "procedural" and source_format != "procedural":
        _fail(f"{field}.source_format", "must be procedural for a procedural item")
    if complete and layer != "procedural" and generator is not None:
        _fail(f"{field}.generator", "must be null for a non-procedural corpus item")
    if complete and layer != "procedural" and source_sha is None:
        _fail(f"{field}.source_sha256", "is required for a non-procedural corpus item")
    source_url = _optional_text(provenance.source_url, f"{field}.source_url")
    producer = _optional_text(provenance.producer, f"{field}.producer")
    if complete and source_url is None and producer is None:
        _fail(field, "strict provenance requires source_url or local producer identity")
    if complete and layer != "procedural" and retrieval_date is None:
        _fail(f"{field}.retrieval_date", "is required for a non-procedural corpus item")
    router_version = _optional_identifier(
        provenance.router_version,
        f"{field}.router_version",
    )
    importer_version = _optional_identifier(
        provenance.importer_version,
        f"{field}.importer_version",
    )
    container_version = _optional_identifier(
        provenance.container_version,
        f"{field}.container_version",
    )
    if complete and source_format in {"musicxml", "mxl", "midi"}:
        if router_version is None:
            _fail(f"{field}.router_version", "is required for a score-imported item")
        if importer_version is None:
            _fail(f"{field}.importer_version", "is required for a score-imported item")
        if root_sha is None:
            _fail(f"{field}.root_sha256", "is required for a score-imported item")
    if complete and source_format == "mxl" and container_version is None:
        _fail(f"{field}.container_version", "is required for an MXL item")
    license_snapshot = _snapshot_license(
        provenance.license,
        f"{field}.license",
        complete=complete,
    )
    if complete and layer == "procedural":
        if license_snapshot.status != "generated":
            _fail(
                f"{field}.license.status",
                "must be generated for a procedural corpus item",
            )
        if (
            license_snapshot.redistribution is not True
            or license_snapshot.derivatives is not True
            or license_snapshot.provider_submission is not True
        ):
            _fail(
                f"{field}.license",
                "generated corpus evidence must explicitly permit all recorded uses",
            )
    if complete and layer != "procedural" and license_snapshot.status == "generated":
        _fail(
            f"{field}.license.status",
            "must not be generated for a public-source corpus item",
        )
    if complete and layer != "procedural":
        if license_snapshot.status != "verified":
            _fail(
                f"{field}.license.status",
                "included public/checker evidence must have verified license status",
            )
        if (
            license_snapshot.derivatives is not True
            or license_snapshot.provider_submission is not True
        ):
            _fail(
                f"{field}.license",
                "included public/checker evidence must permit derivatives and provider submission",
            )
    return CorpusProvenance(
        source_format,
        source_sha,
        root_sha,
        router_version,
        importer_version,
        container_version,
        source_url,
        producer,
        retrieval_date,
        license_snapshot,
        _identifier(provenance.split, f"{field}.split"),
        tuple(role_map),
        normalization,
        generator,
    )


def _legacy_provenance(item: CorpusItem, ir: MusicIR) -> CorpusProvenance:
    generated = item.layer == "procedural"
    return CorpusProvenance(
        source_format="procedural" if generated else "legacy",
        source_sha256=None,
        root_sha256=None,
        router_version=None,
        importer_version=None,
        container_version=None,
        source_url=None,
        producer=ir.meta.source or None,
        retrieval_date=None,
        license=LicenseProvenance(
            expression=ir.meta.license or "NOASSERTION",
            status="generated" if generated else "unavailable",
            redistribution=None,
            derivatives=None,
            provider_submission=None,
        ),
        split="test",
        role_map=(),
        normalization=(),
        generator=None,
    )


def _source_polyphony(ir: MusicIR) -> str:
    if not ir.notes:
        return "unrated"
    events = sorted(
        (
            (time, delta)
            for note in ir.notes
            for time, delta in ((note.onset, 1), (note.onset + note.duration, -1))
        ),
        key=lambda event: (event[0], event[1]),
    )
    sounding = 0
    maximum = 0
    for _time, delta in events:
        sounding += delta
        maximum = max(maximum, sounding)
    return "monophonic" if maximum <= 1 else "polyphonic"


def snapshot_corpus_item(
    value: object,
    *,
    allow_legacy: bool = False,
    legacy_position: int | None = None,
) -> CorpusItem:
    if type(allow_legacy) is not bool:
        _fail("allow_legacy", "must be an exact bool")
    if type(value) is not CorpusItem:
        _fail("item", "must be an exact CorpusItem")
    item = value
    ir = _validated_ir(item.ir, "item.ir")
    layer = _identifier(item.layer, "item.layer")
    if layer not in _LAYERS:
        _fail("item.layer", "is not a supported corpus layer")
    genre = _nfc_text(item.genre, "item.genre", maximum=MAX_SHORT_TEXT_CHARS, empty=False)
    difficulty = _exact_int(item.difficulty, "item.difficulty", minimum=0, maximum=10)
    item_id = _identifier(item.item_id, "item.item_id")
    v2_fields = (
        item.family_id,
        item.cluster_id,
        item.position,
        item.provenance,
        item.evidence,
        item.canary,
    )
    exact_legacy_shape = (
        all(field is None for field in v2_fields)
        and item.synthetic_complexity == "unrated"
        and item.polyphony == "unrated"
    )
    complete_v2_shape = all(field is not None for field in v2_fields)
    if not exact_legacy_shape and not complete_v2_shape:
        _fail("item", "partial legacy/v2 hybrid CorpusItem is not accepted")
    is_legacy = exact_legacy_shape
    if is_legacy and not allow_legacy:
        _fail("item", "legacy five-argument CorpusItem requires allow_legacy=True")
    if not is_legacy and difficulty != 0:
        _fail(
            "item.difficulty",
            "strict v2 stores human difficulty as the unrated sentinel 0",
        )
    family_id = (
        item_id
        if item.family_id is None
        else _identifier(item.family_id, "item.family_id")
    )
    cluster_id = (
        family_id
        if item.cluster_id is None
        else _identifier(item.cluster_id, "item.cluster_id")
    )
    if item.position is None:
        if legacy_position is None:
            _fail("item.position", "legacy position is unavailable")
        position = _exact_int(
            legacy_position,
            "item.position",
            minimum=0,
            maximum=MAX_CORPUS_ITEMS - 1,
        )
    else:
        position = _exact_int(
            item.position,
            "item.position",
            minimum=0,
            maximum=MAX_CORPUS_ITEMS - 1,
        )
    provenance_value = _legacy_provenance(item, ir) if item.provenance is None else item.provenance
    provenance = _snapshot_provenance(
        provenance_value,
        layer=layer,
        complete=not is_legacy,
        field="item.provenance",
    )
    evidence_value = (
        EvidenceAvailability(
            melody=any(note.voice == "melody" for note in ir.notes),
            bass=bool(ir.chords),
            harmony=bool(ir.chords) or any(note.voice in {"bass", "harmony"} for note in ir.notes),
        )
        if item.evidence is None
        else item.evidence
    )
    evidence = _snapshot_evidence(
        evidence_value,
        ir,
        "item.evidence",
        layer=layer,
    )
    complexity = _identifier(item.synthetic_complexity, "item.synthetic_complexity")
    polyphony = _identifier(item.polyphony, "item.polyphony")
    if is_legacy:
        if complexity != "unrated" or polyphony != "unrated":
            _fail("item", "legacy CorpusItem strata must both be unrated")
        canary = f"legacy-canary:{item_id}"
    elif layer == "procedural":
        if complexity not in _PROCEDURAL_LEVELS:
            _fail(
                "item.synthetic_complexity",
                "must be low, medium, or high for a procedural item",
            )
        if polyphony not in _PROCEDURAL_LEVELS:
            _fail(
                "item.polyphony",
                "must be low, medium, or high for a procedural item",
            )
        canary = _identifier(item.canary, "item.canary")
    elif layer == "checker_tab":
        if complexity != "unrated" or polyphony != "unrated":
            _fail(
                "item.synthetic_complexity",
                "checker-only items must keep complexity and polyphony unrated",
            )
        if evidence != EvidenceAvailability(False, False, False):
            _fail("item.evidence", "checker-only items cannot enter arrangement evidence")
        canary = _identifier(item.canary, "item.canary")
    else:
        if complexity != "unrated":
            _fail(
                "item.synthetic_complexity",
                "public sources must not receive a synthetic-complexity label",
            )
        expected_polyphony = _source_polyphony(ir)
        if (
            polyphony not in _PUBLIC_POLYPHONY_LEVELS
            or polyphony != expected_polyphony
        ):
            _fail(
                "item.polyphony",
                "must match the source notegraph's monophonic/polyphonic stratum",
            )
        if evidence == EvidenceAvailability(False, False, False):
            _fail("item.evidence", "public arrangement items require source evidence")
        canary = _identifier(item.canary, "item.canary")
    if layer == "procedural" and not is_legacy:
        assert provenance.generator is not None
        generator = provenance.generator
        if generator.key != ir.meta.key:
            _fail("item.provenance.generator.key", "does not match MusicIR metadata")
        if generator.meter != ir.meta.time_sig:
            _fail("item.provenance.generator.meter", "does not match MusicIR metadata")
        if generator.tempo_bpm != ir.meta.tempo_bpm:
            _fail("item.provenance.generator.tempo_bpm", "does not match MusicIR metadata")
        expected_duration = Fraction(
            generator.bars * generator.meter[0] * 4,
            generator.meter[1],
        )
        if ir.meta.duration_beats != expected_duration:
            _fail(
                "item.provenance.generator.bars",
                "does not match the canonical MusicIR piece duration",
            )
        generator_config = GenConfig(
            key=generator.key,
            meter=generator.meter,
            bars=generator.bars,
            seed=generator.seed,
            tempo_bpm=generator.tempo_bpm,
        )
        variation = ProceduralVariation(
            cast(Any, complexity),
            cast(Any, polyphony),
        )
        regenerated = generate_procedural_variant(generator_config, variation)
        if ir != regenerated:
            _fail(
                "item.ir",
                "does not match the frozen procedural generator output",
            )
        expected_source_sha = procedural_source_sha256(
            generator_config,
            variation,
            position,
        )
        if provenance.source_sha256 != expected_source_sha:
            _fail(
                "item.provenance.source_sha256",
                "does not bind the frozen generator config and position",
            )
    return CorpusItem(
        ir,
        layer,
        genre,
        difficulty,
        item_id,
        family_id,
        cluster_id,
        position,
        provenance,
        evidence,
        complexity,
        polyphony,
        canary,
    )


def _preflight_corpus_events(raw_items: list[object] | tuple[object, ...]) -> None:
    """Bound aggregate IR allocation before per-item detached snapshots are built."""

    total_notes = 0
    total_chords = 0
    for index, value in enumerate(raw_items):
        if type(value) is not CorpusItem:
            continue
        try:
            ir = object.__getattribute__(value, "ir")
        except (AttributeError, TypeError):
            continue
        if type(ir) is not MusicIR:
            continue
        try:
            notes = object.__getattribute__(ir, "notes")
            chords = object.__getattribute__(ir, "chords")
        except (AttributeError, TypeError):
            continue
        if type(notes) is tuple:
            total_notes += len(notes)
            if total_notes > MAX_CORPUS_TOTAL_NOTES:
                _fail(
                    f"items[{index}].ir.notes",
                    f"cumulative note count exceeds {MAX_CORPUS_TOTAL_NOTES}",
                )
        if type(chords) is tuple:
            total_chords += len(chords)
            if total_chords > MAX_CORPUS_TOTAL_CHORDS:
                _fail(
                    f"items[{index}].ir.chords",
                    f"cumulative chord count exceeds {MAX_CORPUS_TOTAL_CHORDS}",
                )


def _item_text_chars(item: CorpusItem) -> int:
    assert item.family_id is not None
    assert item.cluster_id is not None
    assert item.provenance is not None
    assert item.canary is not None
    provenance = item.provenance
    values = [
        item.item_id,
        item.family_id,
        item.cluster_id,
        item.layer,
        item.genre,
        item.synthetic_complexity,
        item.polyphony,
        item.canary,
        item.ir.meta.key,
        item.ir.meta.source,
        item.ir.meta.title,
        item.ir.meta.license,
        provenance.source_format,
        provenance.license.expression,
        provenance.license.status,
        provenance.split,
        *(chord.symbol for chord in item.ir.chords),
        *(value for value in (provenance.source_url, provenance.producer) if value is not None),
        *(source for source, _role in provenance.role_map),
        *(role for _source, role in provenance.role_map),
        *provenance.normalization,
    ]
    if provenance.generator is not None:
        values.extend((provenance.generator.version, provenance.generator.key))
    return sum(len(value) for value in values)


def snapshot_corpus(items: object, *, allow_legacy: bool = False) -> tuple[CorpusItem, ...]:
    if type(allow_legacy) is not bool:
        _fail("allow_legacy", "must be an exact bool")
    if type(items) not in (list, tuple):
        _fail("items", "must be an exact list or tuple")
    raw_items = cast(list[object] | tuple[object, ...], items)
    if len(raw_items) > MAX_CORPUS_ITEMS:
        _fail("items", f"count exceeds {MAX_CORPUS_ITEMS}")
    raw_snapshot = tuple(raw_items[: MAX_CORPUS_ITEMS + 1])
    if len(raw_snapshot) > MAX_CORPUS_ITEMS:
        _fail("items", f"count exceeds {MAX_CORPUS_ITEMS}")
    _preflight_corpus_events(raw_snapshot)
    snapshots = tuple(
        snapshot_corpus_item(item, allow_legacy=allow_legacy, legacy_position=index)
        for index, item in enumerate(raw_snapshot)
    )
    seen_ids: set[str] = set()
    seen_canaries: set[str] = set()
    seen_notegraphs: dict[str, str] = {}
    family_bindings: dict[str, tuple[str, str]] = {}
    cluster_splits: dict[str, str] = {}
    text_chars = sum(_item_text_chars(item) for item in snapshots)
    if text_chars > MAX_CORPUS_TEXT_CHARS:
        _fail("items", f"cumulative corpus metadata exceeds {MAX_CORPUS_TEXT_CHARS} chars")
    for index, item in enumerate(snapshots):
        if item.position != index:
            _fail(f"items[{index}].position", "must equal its zero-based ordered position")
        if item.item_id in seen_ids:
            _fail(f"items[{index}].item_id", f"duplicate item_id {item.item_id!r}")
        seen_ids.add(item.item_id)
        assert item.canary is not None
        if item.canary in seen_canaries:
            _fail(f"items[{index}].canary", f"duplicate canary {item.canary!r}")
        seen_canaries.add(item.canary)
        digest = notegraph_sha256(item.ir)
        previous = seen_notegraphs.get(digest)
        if previous is not None:
            _fail(f"items[{index}].notegraph", f"duplicate notegraph already used by {previous!r}")
        seen_notegraphs[digest] = item.item_id
        assert item.family_id is not None
        assert item.cluster_id is not None
        assert item.provenance is not None
        binding = (item.cluster_id, item.provenance.split)
        previous_binding = family_bindings.get(item.family_id)
        if previous_binding is not None and previous_binding != binding:
            dimension = "cluster" if previous_binding[0] != binding[0] else "split"
            _fail(
                f"items[{index}].family_id",
                f"family {item.family_id!r} crosses {dimension} boundaries",
            )
        family_bindings[item.family_id] = binding
        previous_split = cluster_splits.get(item.cluster_id)
        if previous_split is not None and previous_split != item.provenance.split:
            _fail(
                f"items[{index}].cluster_id",
                f"cluster {item.cluster_id!r} crosses split boundaries",
            )
        cluster_splits[item.cluster_id] = item.provenance.split
    return snapshots


def _license_to_dict(value: LicenseProvenance) -> dict[str, object]:
    return {
        "expression": value.expression,
        "status": value.status,
        "redistribution": value.redistribution,
        "derivatives": value.derivatives,
        "provider_submission": value.provider_submission,
    }


def _generator_to_dict(value: GeneratorProvenance | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "version": value.version,
        "config": {
            "key": value.key,
            "meter": [value.meter[0], value.meter[1]],
            "bars": value.bars,
            "seed": value.seed,
            "tempo_bpm": value.tempo_bpm,
        },
    }


def _provenance_to_dict(value: CorpusProvenance) -> dict[str, object]:
    return {
        "source_format": value.source_format,
        "source_sha256": value.source_sha256,
        "root_sha256": value.root_sha256,
        "router_version": value.router_version,
        "importer_version": value.importer_version,
        "container_version": value.container_version,
        "source_url": value.source_url,
        "producer": value.producer,
        "retrieval_date": value.retrieval_date,
        "license": _license_to_dict(value.license),
        "split": value.split,
        "role_map": [{"source": source, "role": role} for source, role in value.role_map],
        "normalization": list(value.normalization),
        "generator": _generator_to_dict(value.generator),
    }


def corpus_item_to_dict(value: object) -> dict[str, Any]:
    item = snapshot_corpus_item(value)
    assert item.family_id is not None
    assert item.cluster_id is not None
    assert item.position is not None
    assert item.provenance is not None
    assert item.evidence is not None
    assert item.canary is not None
    notegraph = ir_to_notegraph(item.ir)
    return {
        "schema": BENCHMARK_CORPUS_VERSION,
        "item_id": item.item_id,
        "family_id": item.family_id,
        "cluster_id": item.cluster_id,
        "position": item.position,
        "layer": item.layer,
        "genre": item.genre,
        "synthetic_complexity": item.synthetic_complexity,
        "polyphony": item.polyphony,
        "canary": item.canary,
        "evidence": {
            "melody": item.evidence.melody,
            "bass": item.evidence.bass,
            "harmony": item.evidence.harmony,
        },
        "provenance": _provenance_to_dict(item.provenance),
        "notegraph": notegraph,
        "notegraph_sha256": notegraph_sha256(notegraph),
    }


def corpus_item_sha256(value: object) -> str:
    return canonical_sha256(BENCHMARK_CORPUS_VERSION, corpus_item_to_dict(value))


def _license_from_dict(value: object, field: str) -> LicenseProvenance:
    obj = _exact_dict(
        value,
        field,
        frozenset(
            {"expression", "status", "redistribution", "derivatives", "provider_submission"}
        ),
    )
    return LicenseProvenance(
        _nfc_text(
            obj["expression"],
            f"{field}.expression",
            maximum=MAX_SHORT_TEXT_CHARS,
            empty=False,
        ),
        _identifier(obj["status"], f"{field}.status"),
        _snapshot_bool(obj["redistribution"], f"{field}.redistribution", optional=True),
        _snapshot_bool(obj["derivatives"], f"{field}.derivatives", optional=True),
        _snapshot_bool(obj["provider_submission"], f"{field}.provider_submission", optional=True),
    )


def _generator_from_dict(value: object, field: str) -> GeneratorProvenance | None:
    if value is None:
        return None
    obj = _exact_dict(value, field, frozenset({"version", "config"}))
    config = _exact_dict(
        obj["config"],
        f"{field}.config",
        frozenset({"key", "meter", "bars", "seed", "tempo_bpm"}),
    )
    meter = _exact_list(config["meter"], f"{field}.config.meter", maximum=2)
    if len(meter) != 2:
        _fail(f"{field}.config.meter", "must contain exactly two integers")
    tempo = config["tempo_bpm"]
    if type(tempo) is not float:
        _fail(f"{field}.config.tempo_bpm", "must be an exact finite float")
    return GeneratorProvenance(
        _identifier(obj["version"], f"{field}.version"),
        _nfc_text(config["key"], f"{field}.config.key", maximum=64, empty=False),
        (
            _exact_int(meter[0], f"{field}.config.meter[0]", minimum=1, maximum=32),
            _exact_int(meter[1], f"{field}.config.meter[1]", minimum=1, maximum=64),
        ),
        _exact_int(config["bars"], f"{field}.config.bars", minimum=1, maximum=64),
        _exact_int(
            config["seed"],
            f"{field}.config.seed",
            minimum=-MAX_SIGNED_SEED - 1,
            maximum=MAX_SIGNED_SEED,
        ),
        tempo,
    )


def _provenance_from_dict(value: object, field: str) -> CorpusProvenance:
    obj = _exact_dict(
        value,
        field,
        frozenset(
            {
                "source_format",
                "source_sha256",
                "root_sha256",
                "router_version",
                "importer_version",
                "container_version",
                "source_url",
                "producer",
                "retrieval_date",
                "license",
                "split",
                "role_map",
                "normalization",
                "generator",
            }
        ),
    )
    raw_role_map = _exact_list(obj["role_map"], f"{field}.role_map", maximum=MAX_ROLE_MAP_ENTRIES)
    role_map: list[tuple[str, str]] = []
    for index, raw_pair in enumerate(raw_role_map):
        pair_field = f"{field}.role_map[{index}]"
        pair = _exact_dict(raw_pair, pair_field, frozenset({"source", "role"}))
        role_map.append(
            (
                _nfc_text(
                    pair["source"],
                    f"{pair_field}.source",
                    maximum=MAX_SHORT_TEXT_CHARS,
                    empty=False,
                ),
                _nfc_text(pair["role"], f"{pair_field}.role", maximum=16, empty=False),
            )
        )
    raw_normalization = _exact_list(
        obj["normalization"], f"{field}.normalization", maximum=MAX_NORMALIZATION_STEPS
    )
    return CorpusProvenance(
        _identifier(obj["source_format"], f"{field}.source_format"),
        _sha256(obj["source_sha256"], f"{field}.source_sha256", optional=True),
        _sha256(obj["root_sha256"], f"{field}.root_sha256", optional=True),
        _optional_identifier(obj["router_version"], f"{field}.router_version"),
        _optional_identifier(obj["importer_version"], f"{field}.importer_version"),
        _optional_identifier(obj["container_version"], f"{field}.container_version"),
        _optional_text(obj["source_url"], f"{field}.source_url"),
        _optional_text(obj["producer"], f"{field}.producer"),
        _optional_text(obj["retrieval_date"], f"{field}.retrieval_date", maximum=10),
        _license_from_dict(obj["license"], f"{field}.license"),
        _identifier(obj["split"], f"{field}.split"),
        tuple(role_map),
        tuple(
            _nfc_text(
                step,
                f"{field}.normalization[{index}]",
                maximum=MAX_SHORT_TEXT_CHARS,
                empty=False,
            )
            for index, step in enumerate(raw_normalization)
        ),
        _generator_from_dict(obj["generator"], f"{field}.generator"),
    )


def corpus_item_from_dict(value: object) -> CorpusItem:
    obj = _exact_dict(
        value,
        "item",
        frozenset(
            {
                "schema",
                "item_id",
                "family_id",
                "cluster_id",
                "position",
                "layer",
                "genre",
                "synthetic_complexity",
                "polyphony",
                "canary",
                "evidence",
                "provenance",
                "notegraph",
                "notegraph_sha256",
            }
        ),
    )
    if obj["schema"] != BENCHMARK_CORPUS_VERSION:
        _fail("item.schema", f"must equal {BENCHMARK_CORPUS_VERSION}")
    ir = notegraph_to_ir(obj["notegraph"], allow_legacy=False)
    expected_notegraph_sha = notegraph_sha256(obj["notegraph"])
    supplied_notegraph_sha = _sha256(
        obj["notegraph_sha256"], "item.notegraph_sha256", optional=False
    )
    if supplied_notegraph_sha != expected_notegraph_sha:
        _fail("item.notegraph_sha256", "does not match the canonical notegraph")
    evidence_obj = _exact_dict(
        obj["evidence"], "item.evidence", frozenset({"melody", "bass", "harmony"})
    )
    item = CorpusItem(
        ir,
        _identifier(obj["layer"], "item.layer"),
        _nfc_text(obj["genre"], "item.genre", maximum=MAX_SHORT_TEXT_CHARS, empty=False),
        0,
        _identifier(obj["item_id"], "item.item_id"),
        _identifier(obj["family_id"], "item.family_id"),
        _identifier(obj["cluster_id"], "item.cluster_id"),
        _exact_int(obj["position"], "item.position", minimum=0, maximum=MAX_CORPUS_ITEMS - 1),
        _provenance_from_dict(obj["provenance"], "item.provenance"),
        EvidenceAvailability(
            cast(
                bool,
                _snapshot_bool(
                    evidence_obj["melody"],
                    "item.evidence.melody",
                    optional=False,
                ),
            ),
            cast(bool, _snapshot_bool(evidence_obj["bass"], "item.evidence.bass", optional=False)),
            cast(
                bool,
                _snapshot_bool(
                    evidence_obj["harmony"],
                    "item.evidence.harmony",
                    optional=False,
                ),
            ),
        ),
        _identifier(obj["synthetic_complexity"], "item.synthetic_complexity"),
        _identifier(obj["polyphony"], "item.polyphony"),
        _identifier(obj["canary"], "item.canary"),
    )
    return snapshot_corpus_item(item)


def corpus_to_dict(items: object) -> dict[str, Any]:
    snapshots = snapshot_corpus(items)
    entries: list[dict[str, object]] = []
    for item in snapshots:
        wire = corpus_item_to_dict(item)
        entries.append({"item": wire, "item_sha256": corpus_item_sha256(item)})
    return {"schema": BENCHMARK_CORPUS_VERSION, "items": entries}


def _wire_text_chars(value: object, *, field: str) -> int:
    stack = [value]
    seen: set[int] = set()
    total = 0
    nodes = 0
    while stack:
        current = stack.pop()
        nodes += 1
        if nodes > 1_000_000:
            _fail(field, "wire value count exceeds the corpus preflight limit")
        current_type = type(current)
        if current is None or current_type in (bool, int, float):
            continue
        if current_type is str:
            total += len(cast(str, current))
            if total > MAX_CORPUS_TEXT_CHARS:
                _fail(field, f"cumulative corpus text exceeds {MAX_CORPUS_TEXT_CHARS} chars")
            continue
        if current_type is list:
            identity = id(current)
            if identity in seen:
                _fail(field, "cyclic/shared wire containers are not accepted")
            seen.add(identity)
            sequence = cast(list[object], current)
            if list.__len__(sequence) > MAX_IR_NOTES:
                _fail(field, "wire array exceeds the corpus preflight limit")
            stack.extend(sequence)
            continue
        if current_type is dict:
            identity = id(current)
            if identity in seen:
                _fail(field, "cyclic/shared wire containers are not accepted")
            seen.add(identity)
            mapping = cast(dict[object, object], current)
            if dict.__len__(mapping) > 64:
                _fail(field, "wire object exceeds the corpus preflight key limit")
            stack.extend(dict.values(mapping))
            continue
        _fail(field, "wire values must use exact JSON builtins")
    return total


def _preflight_wire_corpus(raw_items: list[object]) -> None:
    total_notes = 0
    total_chords = 0
    total_text = 0
    item_keys = frozenset(
        {
            "schema",
            "item_id",
            "family_id",
            "cluster_id",
            "position",
            "layer",
            "genre",
            "synthetic_complexity",
            "polyphony",
            "canary",
            "evidence",
            "provenance",
            "notegraph",
            "notegraph_sha256",
        }
    )
    for index, raw_entry in enumerate(raw_items):
        entry = _exact_dict(
            raw_entry,
            f"items[{index}]",
            frozenset({"item", "item_sha256"}),
        )
        item = _exact_dict(entry["item"], f"items[{index}].item", item_keys)
        notegraph = _exact_dict(
            item["notegraph"],
            f"items[{index}].item.notegraph",
            frozenset({"schema", "meta", "notes", "chords"}),
        )
        notes = _exact_list(
            notegraph["notes"],
            f"items[{index}].item.notegraph.notes",
            maximum=MAX_IR_NOTES,
        )
        chords = _exact_list(
            notegraph["chords"],
            f"items[{index}].item.notegraph.chords",
            maximum=MAX_IR_CHORDS,
        )
        total_notes += len(notes)
        total_chords += len(chords)
        if total_notes > MAX_CORPUS_TOTAL_NOTES:
            _fail("items", f"cumulative note count exceeds {MAX_CORPUS_TOTAL_NOTES}")
        if total_chords > MAX_CORPUS_TOTAL_CHORDS:
            _fail("items", f"cumulative chord count exceeds {MAX_CORPUS_TOTAL_CHORDS}")
        total_text += _wire_text_chars(entry, field=f"items[{index}]")
        if total_text > MAX_CORPUS_TEXT_CHARS:
            _fail("items", f"cumulative corpus text exceeds {MAX_CORPUS_TEXT_CHARS} chars")


def corpus_from_dict(value: object) -> tuple[CorpusItem, ...]:
    obj = _exact_dict(value, "$", frozenset({"schema", "items"}))
    if obj["schema"] != BENCHMARK_CORPUS_VERSION:
        _fail("schema", f"must equal {BENCHMARK_CORPUS_VERSION}")
    raw_items = _exact_list(obj["items"], "items", maximum=MAX_CORPUS_ITEMS)
    _preflight_wire_corpus(raw_items)
    items: list[CorpusItem] = []
    for index, raw_entry in enumerate(raw_items):
        entry = _exact_dict(
            raw_entry, f"items[{index}]", frozenset({"item", "item_sha256"})
        )
        item = corpus_item_from_dict(entry["item"])
        supplied = _sha256(entry["item_sha256"], f"items[{index}].item_sha256", optional=False)
        expected = corpus_item_sha256(item)
        if supplied != expected:
            _fail(f"items[{index}].item_sha256", "does not match the canonical item")
        items.append(item)
    return snapshot_corpus(tuple(items))


def corpus_sha256(items: object) -> str:
    return canonical_sha256(BENCHMARK_CORPUS_VERSION, corpus_to_dict(items))


def datasheet(items: object) -> dict[str, Any]:
    """Return deterministic corpus strata; legacy internal items are upgraded safely."""

    snapshots = snapshot_corpus(items, allow_legacy=True)

    def counts(values: list[str]) -> dict[str, int]:
        return dict(sorted(Counter(values).items()))

    return {
        "schema": BENCHMARK_CORPUS_VERSION,
        "count": len(snapshots),
        "difficulty_status": "HUMAN_BLOCKED_UNRATED",
        "families": len({item.family_id for item in snapshots}),
        "clusters": len({item.cluster_id for item in snapshots}),
        "by_layer": counts([item.layer for item in snapshots]),
        "by_genre": counts([item.genre for item in snapshots]),
        "by_synthetic_complexity": counts(
            [item.synthetic_complexity for item in snapshots]
        ),
        "by_polyphony": counts([item.polyphony for item in snapshots]),
        "by_evidence": counts(
            [
                cast(EvidenceAvailability, item.evidence).signature
                for item in snapshots
            ]
        ),
        "by_split": counts(
            [cast(CorpusProvenance, item.provenance).split for item in snapshots]
        ),
    }


def _procedural_schedule(
    config: ProceduralCorpusConfig,
    index: int,
) -> tuple[GenConfig, ProceduralVariation]:
    variations = procedural_variations()
    variation = variations[index % len(variations)]
    cycle = index // len(variations)
    key = PRIMARY_PROCEDURAL_KEYS[(index * 5 + cycle) % len(PRIMARY_PROCEDURAL_KEYS)]
    meter = PRIMARY_PROCEDURAL_METERS[(index + cycle) % len(PRIMARY_PROCEDURAL_METERS)]
    tempo = PRIMARY_PROCEDURAL_TEMPOS[
        (index * 2 + cycle) % len(PRIMARY_PROCEDURAL_TEMPOS)
    ]
    seed_material = (
        f"{GENERATOR_VERSION}\0primary-v2\0{config.base_seed}\0{index}".encode("ascii")
    )
    seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big") & MAX_SIGNED_SEED
    return (
        GenConfig(
            key=key,
            meter=meter,
            bars=config.bars,
            seed=seed,
            tempo_bpm=tempo,
        ),
        variation,
    )


def _preflight_primary_procedural(config: ProceduralCorpusConfig) -> None:
    if config.family_count * 1024 > MAX_CORPUS_TEXT_CHARS:
        _fail(
            "procedural_config.family_count",
            "estimated corpus metadata exceeds the aggregate text limit",
        )
    total_notes = 0
    total_chords = 0
    for index in range(config.family_count):
        generator, variation = _procedural_schedule(config, index)
        beats = generator.meter[0]
        melody_per_bar = {
            "low": (beats + 1) // 2,
            "medium": beats,
            "high": 2 * beats,
        }[variation.synthetic_complexity]
        harmony_count = {"low": 0, "medium": 1, "high": 2}[variation.polyphony]
        total_notes += generator.bars + generator.bars * melody_per_bar * (1 + harmony_count)
        total_chords += generator.bars
        if total_notes > MAX_CORPUS_TOTAL_NOTES:
            _fail(
                "procedural_config.family_count",
                f"generated note count would exceed {MAX_CORPUS_TOTAL_NOTES}",
            )
        if total_chords > MAX_CORPUS_TOTAL_CHORDS:
            _fail(
                "procedural_config.family_count",
                f"generated chord count would exceed {MAX_CORPUS_TOTAL_CHORDS}",
            )


def build_primary_procedural_corpus(
    config: ProceduralCorpusConfig | None = None,
) -> tuple[CorpusItem, ...]:
    """Build the ordered v2 primary corpus with stable IDs, seeds, and canaries."""

    if config is None:
        config = ProceduralCorpusConfig()
    config = snapshot_procedural_corpus_config(config)
    _preflight_primary_procedural(config)
    items: list[CorpusItem] = []
    seeds: set[int] = set()
    for index in range(config.family_count):
        generator, variation = _procedural_schedule(config, index)
        if generator.seed in seeds:
            _fail("procedural_config.base_seed", "derived generator seed collision")
        seeds.add(generator.seed)
        ir = generate_procedural_variant(generator, variation)
        source_sha = procedural_source_sha256(generator, variation, index)
        suffix = f"{index:06d}"
        family_id = f"proc-family-v2-{suffix}"
        items.append(
            CorpusItem(
                ir=ir,
                layer="procedural",
                genre="generated",
                difficulty=0,
                item_id=f"proc-v2-{suffix}",
                family_id=family_id,
                cluster_id=f"proc-cluster-v2-{suffix}",
                position=index,
                provenance=CorpusProvenance(
                    source_format="procedural",
                    source_sha256=source_sha,
                    root_sha256=None,
                    router_version=None,
                    importer_version=None,
                    container_version=None,
                    source_url=None,
                    producer="fretsure",
                    retrieval_date=None,
                    license=LicenseProvenance(
                        expression="LicenseRef-FretSure-Generated-Benchmark-v2",
                        status="generated",
                        redistribution=True,
                        derivatives=True,
                        provider_submission=True,
                    ),
                    split=config.split,
                    role_map=(
                        ("generated:bass", "bass"),
                        ("generated:harmony", "harmony"),
                        ("generated:melody", "melody"),
                    ),
                    normalization=("procedural-generator-direct",),
                    generator=GeneratorProvenance(
                        version=GENERATOR_VERSION,
                        key=generator.key,
                        meter=generator.meter,
                        bars=generator.bars,
                        seed=generator.seed,
                        tempo_bpm=generator.tempo_bpm,
                    ),
                ),
                evidence=EvidenceAvailability(melody=True, bass=True, harmony=True),
                synthetic_complexity=variation.synthetic_complexity,
                polyphony=variation.polyphony,
                canary=f"fretsure-benchmark-v2-canary-{suffix}-{source_sha[:12]}",
            )
        )
    return snapshot_corpus(tuple(items))
