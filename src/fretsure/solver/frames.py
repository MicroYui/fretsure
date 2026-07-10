"""Per-frame configuration enumeration.

A frame's target pitches are placed on distinct strings (from their candidates),
the fretted subset gets a feasible left-hand fingering (Plan 1 CSP), and the
plucked notes get an ascending-rank p-i-m-a right-hand selection matching string
order. Each candidate config is then verified against the oracle in isolation
(``check_playability != RED``) so it is genuinely playable as a single frame —
this also catches behind-the-barre cases the geometric CSP alone misses.
Multiple right-hand selections are emitted so the solver can avoid repeat-rate
violations across frames.
"""

import itertools
from dataclasses import dataclass
from fractions import Fraction

from fretsure.oracle.core import passes_optimistic
from fretsure.oracle.csp import feasible_fingerings
from fretsure.oracle.profiles import Profile
from fretsure.solver.candidates import candidates
from fretsure.tab import RightFinger, Tab, TabNote

_RIGHT_ORDER: tuple[RightFinger, ...] = ("p", "i", "m", "a")


@dataclass(frozen=True)
class Placement:
    pitch: int
    string: int
    fret: int
    left_finger: int
    right_finger: RightFinger


@dataclass(frozen=True)
class FrameConfig:
    placements: tuple[Placement, ...]  # ascending string order


_ConfigKey = tuple[tuple[int, int, int, str], ...]


def _single_frame_tab(
    placements: tuple[Placement, ...], tuning: tuple[int, ...], capo: int
) -> Tab:
    notes = tuple(
        TabNote(Fraction(0), Fraction(1), p.string, p.fret, p.left_finger, p.right_finger)
        for p in placements
    )
    return Tab(notes, tuning, capo)


def frame_configs(
    pitches: tuple[int, ...],
    tuning: tuple[int, ...],
    capo: int,
    profile: Profile,
    *,
    limit: int = 48,
) -> list[FrameConfig]:
    if not pitches:
        return [FrameConfig(())]

    cand_lists = [candidates(p, tuning, capo, profile.max_fret) for p in pitches]
    if any(not cl for cl in cand_lists):
        return []

    configs: list[FrameConfig] = []
    seen: set[_ConfigKey] = set()
    for combo in itertools.product(*cand_lists):
        strings = [sf[0] for sf in combo]
        if len(set(strings)) != len(strings):
            continue  # one string, one note
        if len(combo) > 4:
            continue  # more plucks than right-hand fingers

        order = sorted(range(len(pitches)), key=lambda k: combo[k][0])
        frame_notes = tuple(
            TabNote(Fraction(0), Fraction(1), combo[k][0], combo[k][1], 0, "p")
            for k in order
        )
        fingerings = feasible_fingerings(frame_notes, profile, capo=capo)
        if not fingerings:
            continue
        fretted_order = [k for k in order if combo[k][1] > 0]

        for assign in fingerings:
            finger_of = {k: assign[i] for i, k in enumerate(fretted_order)}

            # verify the left hand + within-frame right hand once, with a canonical
            # ascending right-finger selection; barre-behind etc. are rejected here.
            base = tuple(
                Placement(
                    pitches[k], combo[k][0], combo[k][1], finger_of.get(k, 0), _RIGHT_ORDER[i]
                )
                for i, k in enumerate(order)
            )
            base_tab = _single_frame_tab(base, tuning, capo)
            if not passes_optimistic(base_tab, profile):
                continue

            for right_sel in itertools.combinations(_RIGHT_ORDER, len(order)):
                placements = tuple(
                    Placement(
                        pitches[k], combo[k][0], combo[k][1], finger_of.get(k, 0), right_sel[i]
                    )
                    for i, k in enumerate(order)
                )
                key: _ConfigKey = tuple(
                    (p.string, p.fret, p.left_finger, p.right_finger) for p in placements
                )
                if key not in seen:
                    seen.add(key)
                    configs.append(FrameConfig(placements))

    configs.sort(
        key=lambda c: (
            sum(p.fret for p in c.placements),
            len({p.left_finger for p in c.placements if p.left_finger > 0}),
            tuple((p.string, p.fret, p.left_finger, p.right_finger) for p in c.placements),
        )
    )
    return configs[:limit]
