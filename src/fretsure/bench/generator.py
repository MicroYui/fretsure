"""Bounded procedural lead-sheet generation for benchmark evidence.

The legacy :func:`generate_leadsheet` surface remains deterministic and keeps its
default output stable.  Benchmark-v2 uses :func:`generate_procedural_variant` to
cover an explicit low/medium/high complexity x polyphony grid without changing
the legacy generator's musical distribution.
"""

import math
import random
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal, cast

from fretsure.ir import (
    ChordSymbol,
    Meta,
    MusicIR,
    Note,
    snapshot_music_ir,
    validate_ir,
)

GENERATOR_VERSION = "procedural-generator@0.1.0"
MAX_GENERATOR_BARS = 64
MAX_GENERATOR_BEATS_PER_BAR = 32
MAX_GENERATOR_NOTE_EVENTS = 16_384
MAX_GENERATOR_SEED = (1 << 63) - 1
MIN_GENERATOR_TEMPO_BPM = 1.0
MAX_GENERATOR_TEMPO_BPM = 1_000.0

_SUPPORTED_DENOMINATORS = frozenset({1, 2, 4, 8, 16, 32, 64})
_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)  # semitones from tonic
_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_FUNCTIONS: dict[str, tuple[int, ...]] = {
    "T": (0, 5, 2),  # I, vi, iii  (0-indexed scale degrees)
    "S": (3, 1),  # IV, ii
    "D": (4, 6),  # V, vii
}
_CYCLE = ("T", "S", "D", "T")
_KEY_TONIC = {"C": 0, "G": 7, "D": 2, "A": 9, "E": 4, "F": 5, "Bb": 10, "B": 11}

ProceduralLevel = Literal["low", "medium", "high"]
PROCEDURAL_LEVELS: tuple[ProceduralLevel, ...] = ("low", "medium", "high")


class GeneratorInputError(ValueError):
    """Typed fail-closed rejection for procedural generator controls."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid generator {field}: {detail}")


def _validated_config_values(
    key: object,
    meter: object,
    bars: object,
    seed: object,
    tempo_bpm: object,
) -> tuple[str, tuple[int, int], int, int, float]:
    if type(key) is not str or key not in _KEY_TONIC:
        raise GeneratorInputError(
            "key",
            "must be an exact supported key: C, G, D, A, E, F, Bb, or B",
        )
    if type(meter) is not tuple or len(meter) != 2:
        raise GeneratorInputError("meter", "must be an exact two-item tuple")
    numerator, denominator = meter
    if type(numerator) is not int or not 1 <= numerator <= MAX_GENERATOR_BEATS_PER_BAR:
        raise GeneratorInputError(
            "meter",
            f"numerator must be an exact integer in 1..{MAX_GENERATOR_BEATS_PER_BAR}",
        )
    if type(denominator) is not int or denominator not in _SUPPORTED_DENOMINATORS:
        raise GeneratorInputError(
            "meter",
            "denominator must be an exact power of two in 1,2,4,8,16,32,64",
        )
    if type(bars) is not int or not 1 <= bars <= MAX_GENERATOR_BARS:
        raise GeneratorInputError(
            "bars",
            f"must be an exact integer in 1..{MAX_GENERATOR_BARS}",
        )
    if type(seed) is not int or not -MAX_GENERATOR_SEED <= seed <= MAX_GENERATOR_SEED:
        raise GeneratorInputError("seed", "must be an exact signed 63-bit integer")
    if type(tempo_bpm) not in (int, float):
        raise GeneratorInputError(
            "tempo_bpm",
            "must be an exact built-in int or float; bool and subclasses are rejected",
        )
    exact_tempo = cast(int | float, tempo_bpm)
    if not MIN_GENERATOR_TEMPO_BPM <= exact_tempo <= MAX_GENERATOR_TEMPO_BPM:
        raise GeneratorInputError(
            "tempo_bpm",
            f"must be finite and within {MIN_GENERATOR_TEMPO_BPM:g}..{MAX_GENERATOR_TEMPO_BPM:g}",
        )
    normalized_tempo = float(exact_tempo)
    if (
        not math.isfinite(normalized_tempo)
        or not MIN_GENERATOR_TEMPO_BPM <= normalized_tempo <= MAX_GENERATOR_TEMPO_BPM
    ):
        raise GeneratorInputError(
            "tempo_bpm",
            f"must be finite and within {MIN_GENERATOR_TEMPO_BPM:g}..{MAX_GENERATOR_TEMPO_BPM:g}",
        )

    # The densest public variant has two melody attacks and two harmony notes per
    # metrical beat, plus one bass per bar.  Reject before allocating any lists.
    densest_note_count = bars + bars * numerator * 2 * 3
    if densest_note_count > MAX_GENERATOR_NOTE_EVENTS:
        raise GeneratorInputError(
            "resources",
            f"requested meter/bars exceed the {MAX_GENERATOR_NOTE_EVENTS}-note limit",
        )
    return key, (numerator, denominator), bars, seed, normalized_tempo


@dataclass(frozen=True)
class GenConfig:
    key: str = "C"
    meter: tuple[int, int] = (4, 4)
    bars: int = 4
    seed: int = 0
    tempo_bpm: float = 90.0

    def __post_init__(self) -> None:
        key, meter, bars, seed, tempo = _validated_config_values(
            self.key,
            self.meter,
            self.bars,
            self.seed,
            self.tempo_bpm,
        )
        # Normalize the one intentionally accepted numeric union.  All other
        # fields already have a single exact canonical runtime type.
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "meter", meter)
        object.__setattr__(self, "bars", bars)
        object.__setattr__(self, "seed", seed)
        object.__setattr__(self, "tempo_bpm", tempo)


def snapshot_gen_config(value: object) -> GenConfig:
    """Validate, normalize, and detach one exact :class:`GenConfig`."""

    if type(value) is not GenConfig:
        raise GeneratorInputError("$", "must be an exact GenConfig")
    try:
        raw = tuple(
            object.__getattribute__(value, field)
            for field in ("key", "meter", "bars", "seed", "tempo_bpm")
        )
    except (AttributeError, TypeError):
        raise GeneratorInputError("$", "required GenConfig field is missing") from None
    key, meter, bars, seed, tempo = _validated_config_values(*raw)
    return GenConfig(key=key, meter=meter, bars=bars, seed=seed, tempo_bpm=tempo)


@dataclass(frozen=True, slots=True)
class ProceduralVariation:
    """The two explicit, non-human-calibrated procedural stratification axes."""

    synthetic_complexity: ProceduralLevel
    polyphony: ProceduralLevel

    def __post_init__(self) -> None:
        if (
            type(self.synthetic_complexity) is not str
            or self.synthetic_complexity not in PROCEDURAL_LEVELS
        ):
            raise GeneratorInputError(
                "synthetic_complexity",
                "must be the exact string low, medium, or high",
            )
        if type(self.polyphony) is not str or self.polyphony not in PROCEDURAL_LEVELS:
            raise GeneratorInputError(
                "polyphony",
                "must be the exact string low, medium, or high",
            )


def _snapshot_variation(value: object) -> ProceduralVariation:
    if type(value) is not ProceduralVariation:
        raise GeneratorInputError("variation", "must be an exact ProceduralVariation")
    try:
        complexity = object.__getattribute__(value, "synthetic_complexity")
        polyphony = object.__getattribute__(value, "polyphony")
    except (AttributeError, TypeError):
        raise GeneratorInputError("variation", "required variation field is missing") from None
    if type(complexity) is not str or complexity not in PROCEDURAL_LEVELS:
        raise GeneratorInputError(
            "synthetic_complexity",
            "must be the exact string low, medium, or high",
        )
    if type(polyphony) is not str or polyphony not in PROCEDURAL_LEVELS:
        raise GeneratorInputError(
            "polyphony",
            "must be the exact string low, medium, or high",
        )
    return ProceduralVariation(complexity, polyphony)


def procedural_variations() -> tuple[ProceduralVariation, ...]:
    """Return the canonical balanced 3 x 3 procedural variation grid."""

    return tuple(
        ProceduralVariation(complexity, polyphony)
        for complexity in PROCEDURAL_LEVELS
        for polyphony in PROCEDURAL_LEVELS
    )


def _chord_name(root_pc: int, pcs: frozenset[int]) -> str:
    """Return an unambiguous chord name consistent with ``root_pc``."""

    intervals = tuple(sorted((p - root_pc) % 12 for p in pcs))
    quality: dict[tuple[int, ...], str] = {(0, 3, 6): "dim", (0, 3, 7): "m", (0, 4, 8): "aug"}
    return _NOTE_NAMES[root_pc] + quality.get(intervals, "")


def _triad(tonic_pc: int, degree: int) -> tuple[frozenset[int], int]:
    pcs = frozenset(
        (tonic_pc + _MAJOR_SCALE[(degree + step) % 7]) % 12 for step in (0, 2, 4)
    )
    root_pc = (tonic_pc + _MAJOR_SCALE[degree % 7]) % 12
    return pcs, root_pc


def _checked_ir(ir: MusicIR) -> MusicIR:
    snapshot = snapshot_music_ir(ir)
    if validate_ir(snapshot):
        raise RuntimeError("procedural generator produced an invalid MusicIR")
    return snapshot


def generate_leadsheet(cfg: GenConfig) -> MusicIR:
    """Generate the legacy deterministic lead sheet after strict preflight."""

    cfg = snapshot_gen_config(cfg)
    rng = random.Random(cfg.seed)
    tonic_pc = _KEY_TONIC[cfg.key]
    beats_per_bar = cfg.meter[0]
    beat_duration = Fraction(4, cfg.meter[1])
    bar_duration = beats_per_bar * beat_duration
    notes: list[Note] = []
    chords: list[ChordSymbol] = []

    for bar in range(cfg.bars):
        function = _CYCLE[bar % len(_CYCLE)]
        degree = rng.choice(_FUNCTIONS[function])
        chord_pcs, root_pc = _triad(tonic_pc, degree)
        bar_onset = bar * bar_duration
        chords.append(
            ChordSymbol(bar_onset, _chord_name(root_pc, chord_pcs), chord_pcs, root_pc)
        )
        notes.append(Note(bar_onset, bar_duration, 48 + root_pc, "bass"))

        for beat in range(beats_per_bar):
            onset = bar_onset + beat * beat_duration
            if beat % 2 == 0:
                pitch_class = rng.choice(sorted(chord_pcs))
            else:
                pitch_class = (tonic_pc + rng.choice(_MAJOR_SCALE)) % 12
            midi = 60 + pitch_class  # melody above the legacy bass register
            notes.append(Note(onset, beat_duration, midi, "melody"))

    meta = Meta(
        key=cfg.key,
        time_sig=cfg.meter,
        tempo_bpm=cfg.tempo_bpm,
        source=f"procedural:seed{cfg.seed}",
        title=f"gen-{cfg.key}-{cfg.seed}",
        license="generated",
    )
    return _checked_ir(
        MusicIR(tuple(sorted(notes, key=lambda n: (n.onset, n.pitch))), tuple(chords), meta)
    )


def _neighboring_diatonic_pitch(
    pitch: int,
    tonic_pc: int,
    *,
    event_index: int,
    seed: int,
) -> int:
    relative_pc = (pitch - tonic_pc) % 12
    degree = _MAJOR_SCALE.index(relative_pc)
    step = 1 if (seed + event_index) % 2 == 0 else -1
    return 60 + (tonic_pc + _MAJOR_SCALE[(degree + step) % 7]) % 12


def _variant_melody(base: MusicIR, cfg: GenConfig, level: ProceduralLevel) -> list[Note]:
    base_melody = {note.onset: note for note in base.notes if note.voice == "melody"}
    beat_duration = Fraction(4, cfg.meter[1])
    bar_duration = cfg.meter[0] * beat_duration
    melody: list[Note] = []
    event_index = 0
    tonic_pc = _KEY_TONIC[cfg.key]
    for bar in range(cfg.bars):
        bar_onset = bar * bar_duration
        for beat in range(cfg.meter[0]):
            onset = bar_onset + beat * beat_duration
            source_note = base_melody[onset]
            if level == "low":
                if beat % 2:
                    continue
                duration = min(2 * beat_duration, bar_onset + bar_duration - onset)
                melody.append(Note(onset, duration, source_note.pitch, "melody"))
            elif level == "medium":
                melody.append(Note(onset, beat_duration, source_note.pitch, "melody"))
            else:
                half_beat = beat_duration / 2
                melody.append(Note(onset, half_beat, source_note.pitch, "melody"))
                melody.append(
                    Note(
                        onset + half_beat,
                        half_beat,
                        _neighboring_diatonic_pitch(
                            source_note.pitch,
                            tonic_pc,
                            event_index=event_index,
                            seed=cfg.seed,
                        ),
                        "melody",
                    )
                )
            event_index += 1
    return melody


def _variant_bass(chords: tuple[ChordSymbol, ...], bar_duration: Fraction) -> list[Note]:
    return [
        Note(chord.onset, bar_duration, 40 + (chord.root_pc - 4) % 12, "bass")
        for chord in chords
    ]


def _variant_harmony(
    melody: list[Note],
    bass: list[Note],
    chords: tuple[ChordSymbol, ...],
    bar_duration: Fraction,
    count: int,
) -> list[Note]:
    if count == 0:
        return []
    harmony: list[Note] = []
    for melody_note in melody:
        bar = min(melody_note.onset // bar_duration, len(chords) - 1)
        bass_pitch = bass[bar].pitch
        chord = chords[bar]
        candidates = [
            pitch
            for pitch in range(bass_pitch + 1, melody_note.pitch)
            if pitch % 12 in chord.pitch_classes
        ]
        if len(candidates) < count:
            raise RuntimeError("procedural variant lacks an inner-harmony register")
        harmony.extend(
            Note(melody_note.onset, melody_note.duration, pitch, "harmony")
            for pitch in candidates[-count:]
        )
    return harmony


def generate_procedural_variant(
    cfg: GenConfig,
    variation: ProceduralVariation,
) -> MusicIR:
    """Generate one deterministic, full-evidence v2 procedural stratum item.

    ``synthetic_complexity`` controls melody attack density (half, one, or two
    attacks per metrical beat). ``polyphony`` controls maximum sounding notes
    (two, three, or four).  Chord annotations keep all three authoritative
    faithfulness dimensions available even in the low-polyphony stratum.
    """

    cfg = snapshot_gen_config(cfg)
    variation = _snapshot_variation(variation)
    base = generate_leadsheet(cfg)
    beat_duration = Fraction(4, cfg.meter[1])
    bar_duration = cfg.meter[0] * beat_duration
    melody = _variant_melody(base, cfg, variation.synthetic_complexity)
    bass = _variant_bass(base.chords, bar_duration)
    harmony_count = {"low": 0, "medium": 1, "high": 2}[variation.polyphony]
    harmony = _variant_harmony(melody, bass, base.chords, bar_duration, harmony_count)
    notes = tuple(sorted((*bass, *melody, *harmony), key=lambda note: (note.onset, note.pitch)))
    if len(notes) > MAX_GENERATOR_NOTE_EVENTS:
        raise GeneratorInputError(
            "resources",
            f"generated note count exceeds {MAX_GENERATOR_NOTE_EVENTS}",
        )
    source = (
        f"procedural:{GENERATOR_VERSION}:seed{cfg.seed}:"
        f"complexity={variation.synthetic_complexity}:polyphony={variation.polyphony}"
    )
    meta = Meta(
        key=cfg.key,
        time_sig=cfg.meter,
        tempo_bpm=cfg.tempo_bpm,
        source=source,
        title=(
            f"gen-v2-{cfg.key}-{cfg.seed}-"
            f"{variation.synthetic_complexity}-{variation.polyphony}"
        ),
        license="generated",
        duration_beats=cfg.bars * bar_duration,
    )
    return _checked_ir(MusicIR(notes, base.chords, meta))
