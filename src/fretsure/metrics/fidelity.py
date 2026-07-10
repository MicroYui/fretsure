"""Faithfulness metrics (STUB).

M0-grade preservation checks: whether the arrangement kept the input melody,
bass, and harmony. STUB: Plan 4 replaces these with the DTW-aligned Melody-F1 /
bass-root-accuracy / harmony-Jaccard of the roadmap (§B.5). Directions match.
"""

from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction

from fretsure.geometry import note_pitch
from fretsure.ir import MusicIR
from fretsure.tab import Tab


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


def harmony_jaccard(ir: MusicIR, tab: Tab) -> float:
    """Mean per-onset pitch-class-set Jaccard between the input and the tab."""
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
        a = ir_pc[onset]
        b = tab_pc.get(onset, set())
        union = a | b
        total += len(a & b) / len(union) if union else 1.0
    return total / len(onsets)


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
    """Fraction of chord onsets where the tab's lowest sounding pitch class equals
    the chord root."""
    if not ir.chords:
        return 1.0
    low_pc: dict[Fraction, int] = {}
    for tn in tab.notes:
        pitch = note_pitch(tn.string, tn.fret, tab.tuning, tab.capo)
        if tn.onset not in low_pc or pitch < low_pc[tn.onset]:
            low_pc[tn.onset] = pitch
    hits = sum(
        1
        for c in ir.chords
        if c.onset in low_pc and low_pc[c.onset] % 12 == c.root_pc
    )
    return hits / len(ir.chords)


@dataclass(frozen=True)
class FaithfulnessGate:
    melody_f1: float
    bass_root: float
    harmony: float
    passed: bool


def faithfulness(
    ir: MusicIR, tab: Tab, *, tau_m: float = 0.9, tau_b: float = 0.7, tau_h: float = 0.6
) -> FaithfulnessGate:
    mf = melody_f1(ir, tab)
    br = bass_root_accuracy(ir, tab)
    hj = harmony_jaccard(ir, tab)
    return FaithfulnessGate(mf, br, hj, mf >= tau_m and br >= tau_b and hj >= tau_h)
