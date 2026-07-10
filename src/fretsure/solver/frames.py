"""Per-frame configuration enumeration.

A frame's target pitches are placed on distinct strings (from their candidates),
the fretted subset gets a feasible left-hand fingering (reusing the Plan 1 CSP),
and the plucked notes get p-i-m-a right fingers in ascending-string order. Each
resulting :class:`FrameConfig` is, by construction, playable in isolation.
"""

import itertools
from dataclasses import dataclass
from fractions import Fraction

from fretsure.oracle.csp import feasible_fingerings
from fretsure.oracle.profiles import Profile
from fretsure.solver.candidates import candidates
from fretsure.tab import RightFinger, TabNote

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


def frame_configs(
    pitches: tuple[int, ...],
    tuning: tuple[int, ...],
    capo: int,
    profile: Profile,
    *,
    limit: int = 64,
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

        right_of = {k: _RIGHT_ORDER[i] for i, k in enumerate(order)}
        fretted_order = [k for k in order if combo[k][1] > 0]
        for assign in fingerings:
            finger_of = {k: assign[i] for i, k in enumerate(fretted_order)}
            placements = tuple(
                Placement(
                    pitch=pitches[k],
                    string=combo[k][0],
                    fret=combo[k][1],
                    left_finger=finger_of.get(k, 0),
                    right_finger=right_of[k],
                )
                for k in order
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
            tuple((p.string, p.fret, p.left_finger) for p in c.placements),
        )
    )
    return configs[:limit]
