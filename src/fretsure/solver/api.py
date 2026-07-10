"""Viterbi fingering solver.

Groups notes into frames, enumerates each frame's feasible configs, and runs a
frame-level DP that minimizes hand-shift displacement while *hard*-rejecting any
transition whose shift speed exceeds the profile ceiling (with guide-finger
relaxation, matching the oracle). The result therefore passes the oracle's
shift-speed gate; per-frame feasibility is already guaranteed by frame_configs.
Returns Infeasible for the first frame with no reachable config.
"""

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction

from fretsure.ir import Note
from fretsure.oracle.profiles import Profile
from fretsure.solver.cost import config_base_cost, config_hand_center
from fretsure.solver.frames import FrameConfig, frame_configs
from fretsure.tab import Tab, TabNote


@dataclass(frozen=True)
class Infeasible:
    onset: Fraction
    reason: str
    pitches: tuple[int, ...]


def _shift_feasible(
    prev: FrameConfig,
    curr: FrameConfig,
    dt_seconds: float,
    capo: int,
    profile: Profile,
) -> bool:
    # guide finger: a shared (string, fret, finger) anchors the hand
    keys_prev = {(p.string, p.fret, p.left_finger) for p in prev.placements if p.fret > 0}
    keys_curr = {(p.string, p.fret, p.left_finger) for p in curr.placements if p.fret > 0}
    if keys_prev & keys_curr:
        return True
    a = config_hand_center(prev, capo, profile)
    b = config_hand_center(curr, capo, profile)
    if a is None or b is None:
        return True  # open-only frame: hand unconstrained
    if dt_seconds <= 0:
        return abs(b - a) == 0.0
    return abs(b - a) / dt_seconds <= profile.v_shift_mm_per_s


def solve_fingering(
    notes: Sequence[Note],
    tuning: tuple[int, ...],
    capo: int,
    profile: Profile,
    *,
    tempo_bpm: float = 90.0,
) -> Tab | Infeasible:
    by_onset: dict[Fraction, list[Note]] = defaultdict(list)
    for n in notes:
        by_onset[n.onset].append(n)
    onsets = sorted(by_onset)
    if not onsets:
        return Tab((), tuning, capo)

    frame_cfgs: list[list[FrameConfig]] = []
    frame_durs: list[dict[int, Fraction]] = []
    frame_pitches: list[tuple[int, ...]] = []
    for onset in onsets:
        fnotes = by_onset[onset]
        pitches = tuple(sorted(fn.pitch for fn in fnotes))
        cfgs = frame_configs(pitches, tuning, capo, profile)
        if not cfgs:
            return Infeasible(onset, "no feasible frame config", pitches)
        frame_cfgs.append(cfgs)
        frame_durs.append({fn.pitch: fn.duration for fn in fnotes})
        frame_pitches.append(pitches)

    num = len(onsets)
    prev_cost = [config_base_cost(c) for c in frame_cfgs[0]]
    back: list[list[int]] = [[-1] * len(frame_cfgs[0])]
    for i in range(1, num):
        dt = float(onsets[i] - onsets[i - 1]) * 60.0 / tempo_bpm
        cur = frame_cfgs[i]
        cur_cost = [math.inf] * len(cur)
        cur_back = [-1] * len(cur)
        for j, cfg in enumerate(cur):
            best = math.inf
            best_prev = -1
            for pj, pcfg in enumerate(frame_cfgs[i - 1]):
                if math.isinf(prev_cost[pj]):
                    continue
                if not _shift_feasible(pcfg, cfg, dt, capo, profile):
                    continue
                c = prev_cost[pj] + abs(_delta(pcfg, cfg, capo, profile))
                if c < best:
                    best = c
                    best_prev = pj
            if not math.isinf(best):
                cur_cost[j] = best + config_base_cost(cfg)
                cur_back[j] = best_prev
        if all(math.isinf(c) for c in cur_cost):
            return Infeasible(onsets[i], "shift too fast from previous frame", frame_pitches[i])
        prev_cost = cur_cost
        back.append(cur_back)

    best_j = min(range(len(prev_cost)), key=lambda j: prev_cost[j])
    path = [0] * num
    path[num - 1] = best_j
    for i in range(num - 1, 0, -1):
        path[i - 1] = back[i][path[i]]

    tab_notes: list[TabNote] = []
    for i, onset in enumerate(onsets):
        cfg = frame_cfgs[i][path[i]]
        durs = frame_durs[i]
        for p in cfg.placements:
            tab_notes.append(
                TabNote(onset, durs[p.pitch], p.string, p.fret, p.left_finger, p.right_finger)
            )
    return Tab(tuple(tab_notes), tuning, capo)


def _delta(prev: FrameConfig, curr: FrameConfig, capo: int, profile: Profile) -> float:
    a = config_hand_center(prev, capo, profile)
    b = config_hand_center(curr, capo, profile)
    if a is None or b is None:
        return 0.0
    return b - a
