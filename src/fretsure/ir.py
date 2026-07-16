"""Music Intermediate Representation (IR).

A unified, versionable representation that all downstream components work on.
Every :class:`Note` carries a ``voice`` role that fixes repair priority:
``melody`` must be kept, ``bass`` kept where possible, ``inner`` is editable.
"""

from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

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
