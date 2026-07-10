"""Procedural functional-harmony lead-sheet generator — the crown test set.

Samples a chord progression from a T->S->D->T functional grammar and a melody
constrained to chord tones / diatonic passing tones, with a bass on the chord
root. These pieces never existed before (seeded), so no LLM could have memorized
their tabs, and the melody/bass/harmony ground truth is exact — the
contamination-proof layer the benchmark leans its headline results on.
"""

import random
from dataclasses import dataclass
from fractions import Fraction

from fretsure.ir import ChordSymbol, Meta, MusicIR, Note

_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)  # semitones from tonic
_NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_FUNCTIONS: dict[str, tuple[int, ...]] = {
    "T": (0, 5, 2),  # I, vi, iii  (0-indexed scale degrees)
    "S": (3, 1),  # IV, ii
    "D": (4, 6),  # V, vii
}
_CYCLE = ("T", "S", "D", "T")
_KEY_TONIC = {"C": 0, "G": 7, "D": 2, "A": 9, "E": 4, "F": 5, "Bb": 10, "B": 11}


def _chord_name(root_pc: int, pcs: frozenset[int]) -> str:
    """An unambiguous chord name (root note + quality) consistent with ``root_pc``.

    The label the LLM reads must name the same root the bass-root metric scores;
    a mismatched label makes a correct arranger look wrong (and vice-versa).
    """
    intervals = tuple(sorted((p - root_pc) % 12 for p in pcs))
    quality: dict[tuple[int, ...], str] = {(0, 3, 6): "dim", (0, 3, 7): "m", (0, 4, 8): "aug"}
    return _NOTE_NAMES[root_pc] + quality.get(intervals, "")


@dataclass(frozen=True)
class GenConfig:
    key: str = "C"
    meter: tuple[int, int] = (4, 4)
    bars: int = 4
    seed: int = 0


def _triad(tonic_pc: int, degree: int) -> tuple[frozenset[int], int]:
    pcs = frozenset(
        (tonic_pc + _MAJOR_SCALE[(degree + step) % 7]) % 12 for step in (0, 2, 4)
    )
    root_pc = (tonic_pc + _MAJOR_SCALE[degree % 7]) % 12
    return pcs, root_pc


def generate_leadsheet(cfg: GenConfig) -> MusicIR:
    rng = random.Random(cfg.seed)
    tonic_pc = _KEY_TONIC.get(cfg.key, 0)
    beats_per_bar = cfg.meter[0]
    notes: list[Note] = []
    chords: list[ChordSymbol] = []

    for bar in range(cfg.bars):
        function = _CYCLE[bar % len(_CYCLE)]
        degree = rng.choice(_FUNCTIONS[function])
        chord_pcs, root_pc = _triad(tonic_pc, degree)
        bar_onset = Fraction(bar * beats_per_bar)
        chords.append(ChordSymbol(bar_onset, _chord_name(root_pc, chord_pcs), chord_pcs, root_pc))
        notes.append(Note(bar_onset, Fraction(beats_per_bar), 48 + root_pc, "bass"))

        for beat in range(beats_per_bar):
            onset = Fraction(bar * beats_per_bar + beat)
            if beat % 2 == 0:
                pitch_class = rng.choice(sorted(chord_pcs))
            else:
                pitch_class = (tonic_pc + rng.choice(_MAJOR_SCALE)) % 12
            midi = 60 + pitch_class  # melody in octave 5 (C4..B4), above the bass
            notes.append(Note(onset, Fraction(1), midi, "melody"))

    meta = Meta(
        key=cfg.key,
        time_sig=cfg.meter,
        tempo_bpm=90.0,
        source=f"procedural:seed{cfg.seed}",
        title=f"gen-{cfg.key}-{cfg.seed}",
        license="generated",
    )
    return MusicIR(tuple(sorted(notes, key=lambda n: (n.onset, n.pitch))), tuple(chords), meta)
