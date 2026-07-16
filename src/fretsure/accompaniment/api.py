"""Accompaniment arrangement entry (T2): chords -> playable accompaniment tab."""

from collections.abc import Callable

from fretsure.accompaniment.patterns import arpeggio, strum
from fretsure.agent.arranger import ArrangeGoal
from fretsure.ir import ChordSymbol, MusicIR, Note, snapshot_music_ir
from fretsure.oracle.input import ensure_instrument_config
from fretsure.oracle.profiles import Profile
from fretsure.solver.api import Infeasible, solve_fingering
from fretsure.tab import Tab

_PATTERNS: dict[str, Callable[[ChordSymbol], tuple[Note, ...]]] = {
    "arpeggio": arpeggio,
    "strum": strum,
}


def arrange_accompaniment(
    ir: MusicIR, goal: ArrangeGoal, profile: Profile, *, style: str = "arpeggio"
) -> Tab | Infeasible:
    """Realize the lead sheet's chords into a playable accompaniment.

    Evaluates harmony/bass faithfulness + groove feasibility (T2), not melody
    carrying. Output passes the Plan 1 oracle (non-RED).
    """
    ir = snapshot_music_ir(ir)
    tuning, capo, profile, tempo_bpm = ensure_instrument_config(
        goal.tuning,
        goal.capo,
        profile,
        tempo_bpm=goal.tempo_bpm,
    )

    pattern = _PATTERNS[style]
    notes: list[Note] = []
    for chord in ir.chords:
        notes.extend(pattern(chord))
    target = tuple(sorted(notes, key=lambda n: (n.onset, n.pitch)))
    return solve_fingering(target, tuning, capo, profile, tempo_bpm=tempo_bpm)
