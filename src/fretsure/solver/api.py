"""Beam-search fingering solver.

Builds the tab frame by frame; every candidate extension is verified against the
*actual* oracle (``check_playability`` on the partial tab), so the solver never
returns a RED tab — sustain conflicts, right-hand repeat-rate, and open-frame
shift carry-forward are all caught by the oracle itself rather than reimplemented
here. A comfort/shift cost orders the beam. If no non-RED extension exists for a
frame within the beam, it returns Infeasible.

Contract: the returned Tab is always non-RED under the given profile. The dual
"finds a solution whenever one exists" is best-effort — the bounded beam plus the
``frame_configs`` / ``feasible_fingerings`` caps can drop a config a later frame
needs — and biases to a safe Infeasible rather than a RED output.
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from fretsure.ir import Note
from fretsure.oracle.core import passes_optimistic
from fretsure.oracle.profiles import Profile
from fretsure.solver.candidates import candidates
from fretsure.solver.cost import config_base_cost, transition_cost
from fretsure.solver.frames import FrameConfig
from fretsure.solver.frames import frame_configs as _frame_configs
from fretsure.tab import Tab, TabNote


@dataclass(frozen=True)
class Infeasible:
    onset: Fraction
    reason: str
    pitches: tuple[int, ...]


_State = tuple[float, tuple[TabNote, ...], "FrameConfig | None"]


def _notes_key(notes: tuple[TabNote, ...]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (n.onset, n.string, n.fret, n.left_finger, n.right_finger) for n in notes
    )


def solve_fingering(
    notes: Sequence[Note],
    tuning: tuple[int, ...],
    capo: int,
    profile: Profile,
    *,
    tempo_bpm: float = 90.0,
    beam: int = 16,
) -> Tab | Infeasible:
    beam = max(1, beam)  # a non-positive beam would empty the search
    by_onset: dict[Fraction, list[Note]] = defaultdict(list)
    for n in notes:
        by_onset[n.onset].append(n)
    onsets = sorted(by_onset)
    if not onsets:
        return Tab((), tuning, capo)

    states: list[_State] = [(0.0, (), None)]
    for onset in onsets:
        fnotes = by_onset[onset]
        pitches = tuple(sorted(fn.pitch for fn in fnotes))
        durs = {fn.pitch: fn.duration for fn in fnotes}
        for pitch in pitches:
            if not candidates(pitch, tuning, capo, profile.max_fret):
                return Infeasible(
                    onset, f"pitch {pitch} unreachable on this tuning/capo", (pitch,)
                )
        cfgs = _frame_configs(pitches, tuning, capo, profile)
        if not cfgs:
            return Infeasible(onset, "no feasible frame config", pitches)

        extended: list[_State] = []
        for cost, snotes, last_cfg in states:
            for cfg in cfgs:
                added = tuple(
                    TabNote(onset, durs[p.pitch], p.string, p.fret, p.left_finger, p.right_finger)
                    for p in cfg.placements
                )
                cand = snotes + added
                if not passes_optimistic(Tab(cand, tuning, capo), profile, tempo_bpm=tempo_bpm):
                    continue
                step = 0.0 if last_cfg is None else transition_cost(last_cfg, cfg, capo, profile)
                extended.append((cost + step + config_base_cost(cfg), cand, cfg))

        if not extended:
            return Infeasible(onset, "no non-red extension within beam", pitches)
        extended.sort(key=lambda s: (s[0], _notes_key(s[1])))
        states = extended[:beam]

    best = min(states, key=lambda s: (s[0], _notes_key(s[1])))
    return Tab(best[1], tuning, capo)
