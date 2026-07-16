"""Per-frame configuration enumeration.

A frame's target pitches are placed on distinct strings (from their candidates),
the fretted subset gets a feasible left-hand fingering (Plan 1 CSP), and the
plucked notes get an ascending-rank p-i-m-a right-hand selection matching string
order. Each candidate config is then verified against the oracle in isolation
using the equivalent prevalidated single-frame predicate subset, so it is
genuinely playable as a single frame without paying the full public-oracle
boundary cost per assignment.  This also catches behind-the-barre cases the
geometric CSP alone misses.
Multiple right-hand selections are emitted so the solver can avoid repeat-rate
violations across frames.
"""

import itertools
from dataclasses import dataclass
from fractions import Fraction
from typing import TypeVar

from fretsure.oracle.csp import feasible_fingerings
from fretsure.oracle.input import MAX_SOLVER_FRAME_FINGERINGS
from fretsure.oracle.predicates import (
    check_barre,
    check_finger_count,
    check_finger_monotonic,
    check_fret_span,
    check_shift_speed,
)
from fretsure.oracle.profiles import Profile, optimistic
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
_GeometryKey = tuple[tuple[int, int], ...]
_LeftKey = tuple[int, ...]
_ConfigSortKey = tuple[int, int, _ConfigKey]
_KeyT = TypeVar("_KeyT")


@dataclass(frozen=True, slots=True)
class _ConfigCandidate:
    placements: tuple[Placement, ...]
    key: _ConfigKey
    left_key: _LeftKey
    sort_key: _ConfigSortKey


@dataclass(slots=True)
class _GeometryBucket:
    # The cheapest candidates overall supply deterministic RH/history variants.
    all_best: dict[_ConfigKey, _ConfigCandidate]
    # One cheapest candidate per LH assignment preserves fingering diversity.
    left_best: dict[_LeftKey, _ConfigCandidate]


def _single_frame_tab(placements: tuple[Placement, ...], tuning: tuple[int, ...], capo: int) -> Tab:
    notes = tuple(
        TabNote(Fraction(0), Fraction(1), p.string, p.fret, p.left_finger, p.right_finger)
        for p in placements
    )
    return Tab(notes, tuning, capo)


def _single_frame_static_passes(
    placements: tuple[Placement, ...],
    tuning: tuple[int, ...],
    capo: int,
    optimistic_profile: Profile,
) -> bool:
    """Equivalent single-frame subset without the public oracle boundary cost.

    Candidate placement, the CSP, and canonical RH ordering already establish
    range/wellformed/string/RH/sustain constraints.  These are the remaining
    static and first-shape predicates that can reject an isolated frame.  The
    profile transform is computed once by the caller, rather than once per
    fingering assignment.
    """

    frame = _single_frame_tab(placements, tuning, capo)
    return not (
        check_finger_count(frame, optimistic_profile)
        or check_finger_monotonic(frame, optimistic_profile)
        or check_barre(frame, optimistic_profile)
        or check_fret_span(frame, optimistic_profile)
        or check_shift_speed(frame, optimistic_profile)
    )


def _candidate(placements: tuple[Placement, ...]) -> _ConfigCandidate:
    key: _ConfigKey = tuple(
        (p.string, p.fret, p.left_finger, p.right_finger) for p in placements
    )
    left_key = tuple(p.left_finger for p in placements)
    return _ConfigCandidate(
        placements,
        key,
        left_key,
        (
            sum(p.fret for p in placements),
            len({p.left_finger for p in placements if p.left_finger > 0}),
            key,
        ),
    )


def _retain_bounded(
    mapping: dict[_KeyT, _ConfigCandidate],
    key: _KeyT,
    candidate: _ConfigCandidate,
    limit: int,
) -> None:
    """Keep at most ``limit`` cheapest values under distinct mapping keys."""

    prior = mapping.get(key)
    if prior is not None and prior.sort_key <= candidate.sort_key:
        return
    mapping[key] = candidate
    if len(mapping) > limit:
        worst_key = max(mapping, key=lambda item: mapping[item].sort_key)
        del mapping[worst_key]


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
    if len(pitches) > len(_RIGHT_ORDER):
        # This is an ordinary, valid-but-infeasible target, not malformed input.
        # Reject before candidate-list construction: even a 16-note attack must
        # never reach the candidate Cartesian product.
        return []
    if limit <= 0:
        return []

    cand_lists = [candidates(p, tuning, capo, profile.max_fret) for p in pitches]
    if any(not cl for cl in cand_lists):
        return []

    optimistic_profile = optimistic(profile)
    buckets: dict[_GeometryKey, _GeometryBucket] = {}
    for combo in itertools.product(*cand_lists):
        strings = [sf[0] for sf in combo]
        if len(set(strings)) != len(strings):
            continue  # one string, one note
        order = sorted(range(len(pitches)), key=lambda k: combo[k][0])
        frame_notes = tuple(
            TabNote(Fraction(0), Fraction(1), combo[k][0], combo[k][1], 0, "p") for k in order
        )
        fingerings = feasible_fingerings(
            frame_notes,
            optimistic_profile,
            capo=capo,
            limit=MAX_SOLVER_FRAME_FINGERINGS,
        )
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
            if not _single_frame_static_passes(
                base,
                tuning,
                capo,
                optimistic_profile,
            ):
                continue

            for right_sel in itertools.combinations(_RIGHT_ORDER, len(order)):
                placements = tuple(
                    Placement(
                        pitches[k], combo[k][0], combo[k][1], finger_of.get(k, 0), right_sel[i]
                    )
                    for i, k in enumerate(order)
                )
                item = _candidate(placements)
                geometry: _GeometryKey = tuple(
                    (p.string, p.fret) for p in placements
                )
                bucket = buckets.setdefault(
                    geometry,
                    _GeometryBucket({}, {}),
                )
                _retain_bounded(bucket.all_best, item.key, item, limit)
                _retain_bounded(bucket.left_best, item.left_key, item, limit)

    if not buckets:
        return []

    ordered_all = {
        geometry: tuple(
            sorted(bucket.all_best.values(), key=lambda item: item.sort_key)
        )
        for geometry, bucket in buckets.items()
    }
    ordered_left = {
        geometry: tuple(
            sorted(bucket.left_best.values(), key=lambda item: item.sort_key)
        )
        for geometry, bucket in buckets.items()
    }
    geometries = tuple(
        sorted(buckets, key=lambda geometry: ordered_all[geometry][0].sort_key)
    )

    selected: list[_ConfigCandidate] = []
    selected_keys: set[_ConfigKey] = set()
    seen_left: dict[_GeometryKey, set[_LeftKey]] = {
        geometry: set() for geometry in geometries
    }

    def add(item: _ConfigCandidate, geometry: _GeometryKey) -> None:
        selected.append(item)
        selected_keys.add(item.key)
        seen_left[geometry].add(item.left_key)

    # First retain every affordable string/fret geometry.
    for geometry in geometries:
        add(ordered_all[geometry][0], geometry)
        if len(selected) >= limit:
            return [FrameConfig(item.placements) for item in selected]

    # Then round-robin distinct left-hand assignments across geometries.  A
    # sustained finger choice can decide whether the next attack is reachable,
    # so RH variants must not crowd all alternatives for a critical placement.
    while True:
        added_left = False
        for geometry in geometries:
            left_variant = next(
                (
                    item
                    for item in ordered_left[geometry]
                    if item.key not in selected_keys
                    and item.left_key not in seen_left[geometry]
                ),
                None,
            )
            if left_variant is None:
                continue
            add(left_variant, geometry)
            added_left = True
            if len(selected) >= limit:
                return [FrameConfig(item.placements) for item in selected]
        if not added_left:
            break

    # Finally fill RH/finger-history variants fairly across placement groups.
    while len(selected) < limit:
        added_variant = False
        for geometry in geometries:
            history_variant = next(
                (
                    item
                    for item in ordered_all[geometry]
                    if item.key not in selected_keys
                ),
                None,
            )
            if history_variant is None:
                continue
            add(history_variant, geometry)
            added_variant = True
            if len(selected) >= limit:
                return [FrameConfig(item.placements) for item in selected]
        if not added_variant:
            break
    return [FrameConfig(item.placements) for item in selected]
