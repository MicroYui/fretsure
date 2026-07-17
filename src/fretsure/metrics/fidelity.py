"""Deterministic arrangement-faithfulness metrics.

The public ``fidelity`` score retains the M0 voice-preservation measures used
by candidate ranking.  ``faithfulness`` is the independent benchmark gate:
exact-onset top-voice Melody-F1, bass-root accuracy, and chord-segment harmony
Jaccard.  Real-corpus alignment tolerance remains a later refinement.
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from fretsure.geometry import note_pitch
from fretsure.ir import MusicIR
from fretsure.tab import Tab

FIDELITY_CHECKER_VERSION = "fidelity@0.3.0"

FaithfulnessDimension = Literal["melody", "bass_root", "harmony"]
FAITHFULNESS_DIMENSIONS: tuple[FaithfulnessDimension, ...] = (
    "melody",
    "bass_root",
    "harmony",
)
MELODY_F1_THRESHOLD = 0.9
BASS_ROOT_THRESHOLD = 0.7
HARMONY_THRESHOLD = 0.6
_FAITHFULNESS_THRESHOLDS: dict[FaithfulnessDimension, float] = {
    "melody": MELODY_F1_THRESHOLD,
    "bass_root": BASS_ROOT_THRESHOLD,
    "harmony": HARMONY_THRESHOLD,
}


def _scores_pass(
    scores: dict[FaithfulnessDimension, float | None],
    evaluated: tuple[FaithfulnessDimension, ...],
) -> bool:
    if not evaluated:
        return False
    for dimension in evaluated:
        score = scores[dimension]
        if score is None or score < _FAITHFULNESS_THRESHOLDS[dimension]:
            return False
    return True


def _tab_onset_pitches(tab: Tab) -> set[tuple[Fraction, int]]:
    return {
        (n.onset, note_pitch(n.string, n.fret, tab.tuning, tab.capo)) for n in tab.notes
    }


def _voice_recall(ir: MusicIR, tab: Tab, voice: str) -> float:
    wanted = [n for n in ir.notes if n.voice == voice]
    if not wanted:
        return 1.0
    present = _tab_onset_pitches(tab)
    hits = sum(1 for n in wanted if (n.onset, n.pitch) in present)
    return hits / len(wanted)


def melody_recall(ir: MusicIR, tab: Tab) -> float:
    return _voice_recall(ir, tab, "melody")


def bass_preserved(ir: MusicIR, tab: Tab) -> float:
    return _voice_recall(ir, tab, "bass")


def _jaccard(expected: set[int], actual: set[int]) -> float:
    union = expected | actual
    return len(expected & actual) / len(union) if union else 1.0


def _note_onset_harmony_jaccard(ir: MusicIR, tab: Tab) -> float:
    """Legacy fallback when the source carries no chord annotations."""
    ir_pc: defaultdict[Fraction, set[int]] = defaultdict(set)
    tab_pc: defaultdict[Fraction, set[int]] = defaultdict(set)
    for n in ir.notes:
        ir_pc[n.onset].add(n.pitch % 12)
    for tn in tab.notes:
        tab_pc[tn.onset].add(note_pitch(tn.string, tn.fret, tab.tuning, tab.capo) % 12)
    onsets = set(ir_pc)
    if not onsets:
        return 1.0
    total = 0.0
    for onset in onsets:
        total += _jaccard(ir_pc[onset], tab_pc.get(onset, set()))
    return total / len(onsets)


def _chord_segment_harmony_jaccard(ir: MusicIR, tab: Tab) -> float:
    """Mean chord-PC Jaccard over each annotated chord's active segment.

    A chord is active from its onset to the next *later* chord onset.  The last
    segment ends at the source IR's musical end, never at an output-only event.
    Tab notes count in every segment whose half-open interval they actually
    sound in, so a sustained note crossing a chord boundary affects both
    harmonies.  Multiple annotations at one onset share the same segment and
    are scored independently before the unweighted mean.
    """
    chord_onsets = sorted({chord.onset for chord in ir.chords})
    next_onset = dict(zip(chord_onsets, chord_onsets[1:], strict=False))
    inferred_source_end = max(
        (note.onset + note.duration for note in ir.notes),
        default=chord_onsets[-1],
    )
    source_end = (
        ir.meta.duration_beats
        if ir.meta.duration_beats is not None
        else inferred_source_end
    )

    scores: list[float] = []
    for chord in ir.chords:
        segment_end = min(next_onset.get(chord.onset, source_end), source_end)
        actual = {
            note_pitch(note.string, note.fret, tab.tuning, tab.capo) % 12
            for note in tab.notes
            if note.onset < segment_end and note.onset + note.duration > chord.onset
        }
        expected = {pitch_class % 12 for pitch_class in chord.pitch_classes}
        scores.append(_jaccard(expected, actual))
    return sum(scores) / len(scores)


def harmony_jaccard(ir: MusicIR, tab: Tab) -> float:
    """Measure harmonic preservation with an explicit source-of-truth rule.

    When chord annotations exist, their pitch classes define harmony and the
    score is computed over their active chord segments.  When they do not, the
    stable fallback is the legacy mean pitch-class Jaccard at every source-note
    onset (tab-only onsets are ignored).  An entirely empty source scores 1.0.
    """
    if ir.chords:
        return _chord_segment_harmony_jaccard(ir, tab)
    return _note_onset_harmony_jaccard(ir, tab)


@dataclass(frozen=True)
class Fidelity:
    melody_recall: float
    bass_preserved: float
    harmony_jaccard: float


def fidelity(ir: MusicIR, tab: Tab) -> Fidelity:
    return Fidelity(
        melody_recall=melody_recall(ir, tab),
        bass_preserved=bass_preserved(ir, tab),
        harmony_jaccard=harmony_jaccard(ir, tab),
    )


# --- Authoritative faithfulness (roadmap B.5): top-voice-aligned Melody-F1,
# bass-root-accuracy, and a published gate. (Exact-onset matching here; grid /
# DTW tolerance for real-corpus data is a later refinement.) ---


def _top_voice(tab: Tab) -> dict[Fraction, int]:
    top: dict[Fraction, int] = {}
    for tn in tab.notes:
        pitch = note_pitch(tn.string, tn.fret, tab.tuning, tab.capo)
        if tn.onset not in top or pitch > top[tn.onset]:
            top[tn.onset] = pitch
    return top


def melody_f1(ir: MusicIR, tab: Tab) -> float:
    """F1 between the input melody and the tab's top voice (highest per onset)."""
    inputs = {(n.onset, n.pitch) for n in ir.notes if n.voice == "melody"}
    if not inputs:
        return 1.0
    top = _top_voice(tab)
    top_set = set(top.items())
    matched = inputs & top_set
    recall = len(matched) / len(inputs)
    melody_onsets = {onset for onset, _ in inputs}
    top_at_melody = {(o, p) for o, p in top.items() if o in melody_onsets}
    precision = len(matched) / len(top_at_melody) if top_at_melody else 1.0
    if recall + precision == 0:
        return 0.0
    return 2 * recall * precision / (recall + precision)


def bass_root_accuracy(ir: MusicIR, tab: Tab) -> float:
    """Score the lowest *sounding* pitch at every annotated chord onset.

    A note that began before the chord but is still held at its onset participates
    in the comparison.  This matters for tied or otherwise sustained bass notes;
    restricting the lookup to notes attacked at the exact chord onset would turn
    a faithful held root into a false miss.
    """
    if not ir.chords:
        return 1.0
    hits = 0
    for chord in ir.chords:
        sounding = (
            note_pitch(note.string, note.fret, tab.tuning, tab.capo)
            for note in tab.notes
            if note.onset <= chord.onset < note.onset + note.duration
        )
        lowest = min(sounding, default=None)
        if lowest is not None and lowest % 12 == chord.root_pc:
            hits += 1
    return hits / len(ir.chords)


@dataclass(frozen=True, slots=True)
class FaithfulnessGate:
    melody_f1: float | None
    bass_root: float | None
    harmony: float | None
    passed: bool
    evaluated_dimensions: tuple[FaithfulnessDimension, ...]
    unavailable_dimensions: tuple[FaithfulnessDimension, ...]

    def __post_init__(self) -> None:
        if type(self.evaluated_dimensions) is not tuple:
            raise ValueError("evaluated_dimensions must be an exact tuple")
        if type(self.unavailable_dimensions) is not tuple:
            raise ValueError("unavailable_dimensions must be an exact tuple")
        evaluated = self.evaluated_dimensions
        unavailable = self.unavailable_dimensions
        if any(type(dimension) is not str for dimension in (*evaluated, *unavailable)):
            raise ValueError("faithfulness dimension names must be exact strings")
        if evaluated != tuple(
            dimension for dimension in FAITHFULNESS_DIMENSIONS if dimension in evaluated
        ):
            raise ValueError("evaluated_dimensions must be unique and canonically ordered")
        if unavailable != tuple(
            dimension for dimension in FAITHFULNESS_DIMENSIONS if dimension in unavailable
        ):
            raise ValueError("unavailable_dimensions must be unique and canonically ordered")
        if set(evaluated).isdisjoint(unavailable) is False or set(evaluated) | set(
            unavailable
        ) != set(FAITHFULNESS_DIMENSIONS):
            raise ValueError("faithfulness dimensions must form a complete partition")

        scores: dict[FaithfulnessDimension, float | None] = {
            "melody": self.melody_f1,
            "bass_root": self.bass_root,
            "harmony": self.harmony,
        }
        for dimension, score in scores.items():
            if dimension in evaluated:
                if type(score) is not float or not math.isfinite(score) or not 0.0 <= score <= 1.0:
                    raise ValueError(f"{dimension} score must be an exact finite float in 0..1")
            elif score is not None:
                raise ValueError(f"{dimension} score must be None when unavailable")

        if type(self.passed) is not bool:
            raise ValueError("passed must be an exact bool")
        expected_passed = _scores_pass(scores, evaluated)
        if self.passed is not expected_passed:
            raise ValueError("passed disagrees with the evaluated scores and frozen thresholds")


def faithfulness_dimensions(ir: MusicIR) -> tuple[FaithfulnessDimension, ...]:
    """Return the source-evidenced authoritative dimensions in canonical order."""

    has_melody = any(note.voice == "melody" for note in ir.notes)
    has_chords = bool(ir.chords)
    has_harmonic_notes = any(note.voice in {"bass", "harmony"} for note in ir.notes)
    availability: tuple[tuple[FaithfulnessDimension, bool], ...] = (
        ("melody", has_melody),
        ("bass_root", has_chords),
        ("harmony", has_chords or has_harmonic_notes),
    )
    return tuple(
        dimension
        for dimension, available in availability
        if available
    )


def faithfulness(ir: MusicIR, tab: Tab) -> FaithfulnessGate:
    evaluated = faithfulness_dimensions(ir)
    unavailable = tuple(
        dimension for dimension in FAITHFULNESS_DIMENSIONS if dimension not in evaluated
    )
    mf = melody_f1(ir, tab) if "melody" in evaluated else None
    br = bass_root_accuracy(ir, tab) if "bass_root" in evaluated else None
    hj = harmony_jaccard(ir, tab) if "harmony" in evaluated else None
    scores: dict[FaithfulnessDimension, float | None] = {
        "melody": mf,
        "bass_root": br,
        "harmony": hj,
    }
    passed = _scores_pass(scores, evaluated)
    return FaithfulnessGate(mf, br, hj, passed, evaluated, unavailable)
