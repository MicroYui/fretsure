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
