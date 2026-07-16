"""Music Intermediate Representation (IR).

A unified, versionable representation that all downstream components work on.
Every :class:`Note` carries a ``voice`` role that fixes repair priority:
``melody`` must be kept, ``bass`` kept where possible, ``inner`` is editable.
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal, cast

MAX_IR_NOTES = 20_000
MAX_IR_CHORDS = 20_000
MAX_IR_TEXT_CHARS = 10 * 1024 * 1024
MAX_IR_FRACTION_COMPONENT_BITS = 256

VoiceRole = Literal["melody", "bass", "harmony"]


@dataclass(frozen=True)
class Note:
    """A single note. ``onset``/``duration`` are in beats."""

    onset: Fraction
    duration: Fraction
    pitch: int  # MIDI number
    voice: VoiceRole


@dataclass(frozen=True)
class ChordSymbol:
    """A chord annotation active from ``onset``."""

    onset: Fraction
    symbol: str
    pitch_classes: frozenset[int]  # 0..11
    root_pc: int  # 0..11, used by bass-root-accuracy


@dataclass(frozen=True)
class Meta:
    key: str
    time_sig: tuple[int, int]
    tempo_bpm: float
    source: str  # provenance
    title: str
    license: str
    duration_beats: Fraction | None = None


@dataclass(frozen=True)
class MusicIR:
    notes: tuple[Note, ...]
    chords: tuple[ChordSymbol, ...]
    meta: Meta


@dataclass(frozen=True)
class IRViolation:
    kind: str
    detail: str
    onset: Fraction | None


class IRInputError(ValueError):
    """Typed rejection for a malformed or out-of-resource MusicIR."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid MusicIR {field}: {detail}")


def _field(value: object, name: str, path: str) -> object:
    try:
        return object.__getattribute__(value, name)
    except (AttributeError, TypeError):
        raise IRInputError(path, "required field is missing") from None


def _fraction_snapshot(value: object, path: str, *, positive: bool) -> Fraction:
    if type(value) is not Fraction:
        raise IRInputError(path, "must be an exact Fraction")
    try:
        numerator = object.__getattribute__(value, "_numerator")
        denominator = object.__getattribute__(value, "_denominator")
    except (AttributeError, TypeError):
        raise IRInputError(path, "Fraction components are missing") from None
    if type(numerator) is not int or type(denominator) is not int:
        raise IRInputError(path, "Fraction components must be exact integers")
    if (
        numerator.bit_length() > MAX_IR_FRACTION_COMPONENT_BITS
        or denominator.bit_length() > MAX_IR_FRACTION_COMPONENT_BITS
    ):
        raise IRInputError(path, "Fraction components exceed the 256-bit limit")
    if denominator <= 0 or math.gcd(numerator, denominator) != 1:
        raise IRInputError(path, "Fraction must be reduced with a positive denominator")
    if (positive and numerator <= 0) or (not positive and numerator < 0):
        relation = "positive" if positive else "non-negative"
        raise IRInputError(path, f"must be {relation}")
    return Fraction(numerator, denominator)


def snapshot_music_ir(value: object) -> MusicIR:
    """Validate structure/resources and return a deeply detached MusicIR."""

    if type(value) is not MusicIR:
        raise IRInputError("$", "must be an exact MusicIR")
    raw_notes = _field(value, "notes", "notes")
    raw_chords = _field(value, "chords", "chords")
    raw_meta = _field(value, "meta", "meta")
    if type(raw_notes) is not tuple:
        raise IRInputError("notes", "must be an exact tuple")
    if len(raw_notes) > MAX_IR_NOTES:
        raise IRInputError("notes", f"count exceeds {MAX_IR_NOTES}")
    if type(raw_chords) is not tuple:
        raise IRInputError("chords", "must be an exact tuple")
    if len(raw_chords) > MAX_IR_CHORDS:
        raise IRInputError("chords", f"count exceeds {MAX_IR_CHORDS}")
    if type(raw_meta) is not Meta:
        raise IRInputError("meta", "must be an exact Meta")

    notes: list[Note] = []
    for index, raw in enumerate(raw_notes):
        path = f"notes[{index}]"
        if type(raw) is not Note:
            raise IRInputError(path, "must be an exact Note")
        pitch = _field(raw, "pitch", f"{path}.pitch")
        voice = _field(raw, "voice", f"{path}.voice")
        if type(pitch) is not int or not 0 <= pitch <= 127:
            raise IRInputError(f"{path}.pitch", "must be an exact MIDI integer in 0..127")
        if type(voice) is not str or voice not in ("melody", "bass", "harmony"):
            raise IRInputError(f"{path}.voice", "must be melody, bass, or harmony")
        notes.append(
            Note(
                _fraction_snapshot(
                    _field(raw, "onset", f"{path}.onset"),
                    f"{path}.onset",
                    positive=False,
                ),
                _fraction_snapshot(
                    _field(raw, "duration", f"{path}.duration"),
                    f"{path}.duration",
                    positive=True,
                ),
                pitch,
                cast(VoiceRole, voice),
            )
        )

    text_chars = 0
    chords: list[ChordSymbol] = []
    for index, raw in enumerate(raw_chords):
        path = f"chords[{index}]"
        if type(raw) is not ChordSymbol:
            raise IRInputError(path, "must be an exact ChordSymbol")
        symbol = _field(raw, "symbol", f"{path}.symbol")
        pitch_classes = _field(raw, "pitch_classes", f"{path}.pitch_classes")
        root_pc = _field(raw, "root_pc", f"{path}.root_pc")
        if type(symbol) is not str:
            raise IRInputError(f"{path}.symbol", "must be an exact string")
        text_chars += len(symbol)
        if text_chars > MAX_IR_TEXT_CHARS:
            raise IRInputError("text", f"cumulative text exceeds {MAX_IR_TEXT_CHARS} chars")
        if type(pitch_classes) is not frozenset or any(
            type(pc) is not int or not 0 <= pc <= 11 for pc in pitch_classes
        ):
            raise IRInputError(
                f"{path}.pitch_classes",
                "must be an exact frozenset of pitch classes in 0..11",
            )
        if type(root_pc) is not int or not 0 <= root_pc <= 11:
            raise IRInputError(f"{path}.root_pc", "must be an exact integer in 0..11")
        chords.append(
            ChordSymbol(
                _fraction_snapshot(
                    _field(raw, "onset", f"{path}.onset"),
                    f"{path}.onset",
                    positive=False,
                ),
                symbol,
                frozenset(pitch_classes),
                root_pc,
            )
        )

    meta_values = {
        name: _field(raw_meta, name, f"meta.{name}")
        for name in (
            "key",
            "time_sig",
            "tempo_bpm",
            "source",
            "title",
            "license",
            "duration_beats",
        )
    }
    for name in ("key", "source", "title", "license"):
        text = meta_values[name]
        if type(text) is not str:
            raise IRInputError(f"meta.{name}", "must be an exact string")
        text_chars += len(text)
        if text_chars > MAX_IR_TEXT_CHARS:
            raise IRInputError("text", f"cumulative text exceeds {MAX_IR_TEXT_CHARS} chars")
    time_sig = meta_values["time_sig"]
    if (
        type(time_sig) is not tuple
        or len(time_sig) != 2
        or type(time_sig[0]) is not int
        or not 1 <= time_sig[0] <= 32
        or type(time_sig[1]) is not int
        or not 1 <= time_sig[1] <= 64
    ):
        raise IRInputError(
            "meta.time_sig",
            "must be exact numerator 1..32 and denominator 1..64",
        )
    tempo = meta_values["tempo_bpm"]
    if type(tempo) not in (int, float):
        raise IRInputError("meta.tempo_bpm", "must be an exact built-in int or float")
    try:
        normalized_tempo = float(cast(int | float, tempo))
    except OverflowError:
        normalized_tempo = math.inf
    if not math.isfinite(normalized_tempo) or not 1.0 <= normalized_tempo <= 1_000.0:
        raise IRInputError("meta.tempo_bpm", "must be finite and within 1..1000 BPM")
    duration = meta_values["duration_beats"]
    duration_snapshot = (
        None
        if duration is None
        else _fraction_snapshot(duration, "meta.duration_beats", positive=False)
    )
    return MusicIR(
        notes=tuple(notes),
        chords=tuple(chords),
        meta=Meta(
            key=cast(str, meta_values["key"]),
            time_sig=cast(tuple[int, int], time_sig),
            tempo_bpm=normalized_tempo,
            source=cast(str, meta_values["source"]),
            title=cast(str, meta_values["title"]),
            license=cast(str, meta_values["license"]),
            duration_beats=duration_snapshot,
        ),
    )


def validate_ir(ir: MusicIR) -> list[IRViolation]:
    """Return the list of invariant violations (empty == valid).

    Deterministic: violations are emitted in a stable order (metadata, notes in
    input order, onset-sorted structural checks, then chords in input order).
    """
    violations: list[IRViolation] = []
    piece_end = ir.meta.duration_beats
    piece_end_is_valid = True
    if piece_end is not None:
        if piece_end < 0:
            violations.append(
                IRViolation(
                    "negative_piece_duration", f"duration {piece_end}", None
                )
            )
            piece_end_is_valid = False
        elif piece_end == 0 and (ir.notes or ir.chords):
            violations.append(
                IRViolation(
                    "nonpositive_piece_duration",
                    "duration 0 with musical events",
                    None,
                )
            )
            piece_end_is_valid = False

    # Per-note checks (input order).
    for n in ir.notes:
        if n.onset < 0:
            violations.append(IRViolation("negative_onset", f"pitch {n.pitch}", n.onset))
        if n.duration <= 0:
            violations.append(
                IRViolation("nonpositive_duration", f"pitch {n.pitch}", n.onset)
            )
        if not 0 <= n.pitch <= 127:
            violations.append(IRViolation("pitch_range", f"pitch {n.pitch}", n.onset))
        if (
            piece_end is not None
            and piece_end_is_valid
            and n.onset + n.duration > piece_end
        ):
            violations.append(
                IRViolation(
                    "note_beyond_piece_end",
                    f"pitch {n.pitch} ends at {n.onset + n.duration}, piece ends at {piece_end}",
                    n.onset,
                )
            )

    # Structural checks.
    melody_pitches: defaultdict[Fraction, set[int]] = defaultdict(set)
    for n in ir.notes:
        if n.voice == "melody":
            melody_pitches[n.onset].add(n.pitch)

    for onset in sorted(melody_pitches):
        pitches = melody_pitches[onset]
        if len(pitches) > 1:
            violations.append(
                IRViolation("melody_polyphony", f"{sorted(pitches)}", onset)
            )

    # A piece with notes must carry a melody voice (the top voice that must be
    # retained). Per-onset melody presence is deliberately NOT required: in
    # fingerstyle the melody sustains across accompaniment onsets (Travis
    # picking, alternating bass), so those onsets legitimately have no melody
    # onset of their own.
    if ir.notes and not melody_pitches:
        violations.append(
            IRViolation("missing_melody", "no melody voice in piece", None)
        )

    # Chord checks (input order).
    for c in ir.chords:
        if c.onset < 0:
            violations.append(IRViolation("negative_onset", f"chord {c.symbol}", c.onset))
        if not 0 <= c.root_pc <= 11 or c.root_pc not in c.pitch_classes:
            violations.append(
                IRViolation("bad_chord_root", f"{c.symbol} root {c.root_pc}", c.onset)
            )
        if (
            piece_end is not None
            and piece_end_is_valid
            and c.onset >= piece_end
        ):
            violations.append(
                IRViolation(
                    "chord_outside_piece",
                    f"{c.symbol} starts at {c.onset}, piece ends at {piece_end}",
                    c.onset,
                )
            )

    return violations
