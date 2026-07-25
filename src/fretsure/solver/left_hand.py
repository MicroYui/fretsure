"""Contextual left-hand ergonomics for the fingering solver.

The physical Oracle answers whether a fully specified fingering is possible.
This module answers the separate, musical question of which possible fingering
is idiomatic.  In particular, it carries a discrete guitar position (the fret
normally occupied by finger 1), distinguishes a one-fret extension from a real
position shift, discourages accidental barres, and preserves a repeated note's
finger when the surrounding geometry has not changed.

Score difficulty is deliberately absent.  A fixed score receives the same
default fingering regardless of any beginner/intermediate/advanced arrangement
target.  Player geometry remains the Oracle profile's responsibility.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Protocol

LEFT_HAND_MODEL_VERSION: Final = "left-hand-ergonomics@0.1.0"

# These integer points compare like ergonomic concepts only; they never mix
# millimetres from the physical Oracle with fret or finger counts.
_POSITION_DEVIATION_POINTS: Final = 6
_POSITION_SHIFT_EVENT_POINTS: Final = 5
_POSITION_SHIFT_FRET_POINTS: Final = 4
_REFINGERING_POINTS: Final = 18
_FRET_HEIGHT_POINTS: Final = 2
_FINGER_CROSSOVER_POINTS: Final = 4


class _FrettedLike(Protocol):
    @property
    def string(self) -> int: ...

    @property
    def fret(self) -> int: ...

    @property
    def left_finger(self) -> int: ...


class _FrameLike(Protocol):
    @property
    def placements(self) -> tuple[_FrettedLike, ...]: ...


@dataclass(frozen=True, slots=True)
class LeftHandTransition:
    """One deterministic update of the musical left-hand state."""

    position: int | None
    awkward_events: int
    effort: int
    refingerings: int
    barre_burden: int
    finger_crossover_burden: int
    fret_height_burden: int
    position_deviation: int
    position_shift_count: int
    position_shift_distance: int


def _fretted(items: Iterable[_FrettedLike]) -> tuple[_FrettedLike, ...]:
    return tuple(item for item in items if item.fret > 0 and 1 <= item.left_finger <= 4)


def _barre_metrics(items: Iterable[_FrettedLike]) -> tuple[int, int]:
    """Return ``(awkward_events, burden_points)`` for represented barres.

    A barre is inferred only when one finger presses the same fret on multiple
    strings.  Span matters because the finger must cover every intervening
    string even when only the endpoints are sounded.  Index-finger barres are
    normal guitar technique; long barres with fingers 2--4 remain possible but
    receive one explicit awkward-event marker.
    """

    groups: dict[tuple[int, int], set[int]] = defaultdict(set)
    for item in _fretted(items):
        groups[(item.left_finger, item.fret)].add(item.string)

    awkward = 0
    burden = 0
    for (finger, _fret), strings in groups.items():
        if len(strings) < 2:
            continue
        span = max(strings) - min(strings) + 1
        burden += 4 + 3 * span + 2 * (finger - 1) * max(1, span - 1)
        if finger > 1 and span >= 3:
            awkward += 1
    return awkward, burden


def _position_candidates(
    fretted: tuple[_FrettedLike, ...],
    previous_position: int | None,
) -> tuple[int, ...]:
    ideals = tuple(max(1, item.fret - item.left_finger + 1) for item in fretted)
    candidates = set(ideals)

    # Squared deviation has its optimum near the mean, which need not be one of
    # the individual ideal positions for a contracted or extended chord shape.
    total = sum(ideals)
    lower_mean = max(1, total // len(ideals))
    candidates.add(lower_mean)
    candidates.add(max(1, lower_mean + (total % len(ideals) != 0)))
    if previous_position is not None:
        candidates.add(previous_position)
    return tuple(sorted(candidates))


def _finger_crossover_burden(items: Iterable[_FrettedLike]) -> int:
    """Measure reversed distinct fingers across strings at one fret."""

    by_fret: dict[int, list[_FrettedLike]] = defaultdict(list)
    for item in _fretted(items):
        by_fret[item.fret].append(item)
    burden = 0
    for group in by_fret.values():
        ordered = sorted(group, key=lambda item: item.string)
        for left_index, left in enumerate(ordered):
            for right in ordered[left_index + 1 :]:
                if left.left_finger > right.left_finger:
                    burden += (left.left_finger - right.left_finger) * (right.string - left.string)
    return burden


def _position_metrics(
    fretted: tuple[_FrettedLike, ...],
    position: int,
) -> tuple[int, int, int]:
    """Return severe deviation, total deviation and deviation effort."""

    deviations = tuple(abs(item.fret - (position + item.left_finger - 1)) for item in fretted)
    awkward = sum(max(0, deviation - 1) for deviation in deviations)
    total = sum(deviations)
    effort = _POSITION_DEVIATION_POINTS * sum(deviation * deviation for deviation in deviations)
    return awkward, total, effort


def _refingering_count(previous: _FrameLike | None, current: _FrameLike) -> int:
    if previous is None:
        return 0
    previous_fingers = {
        (item.string, item.fret): item.left_finger for item in _fretted(previous.placements)
    }
    return sum(
        1
        for item in _fretted(current.placements)
        if (prior := previous_fingers.get((item.string, item.fret))) is not None
        and prior != item.left_finger
    )


def advance_left_hand(
    previous_position: int | None,
    active_items: Iterable[_FrettedLike],
    previous_frame: _FrameLike | None,
    current_frame: _FrameLike,
) -> LeftHandTransition:
    """Choose and score the next contextual guitar position.

    Open-only sounding frames preserve the last position.  For a fretted shape,
    the candidate set is bounded by its four finger-implied positions, their
    mean, and the prior position.  This keeps runtime fixed while retaining the
    musically important choice between a small extension and a real shift.
    """

    fretted = _fretted(active_items)
    refingerings = _refingering_count(previous_frame, current_frame)
    barre_awkward, barre_burden = _barre_metrics(fretted)
    finger_crossover_burden = _finger_crossover_burden(fretted)
    fret_height_burden = sum(item.fret for item in _fretted(current_frame.placements))
    fixed_effort = (
        barre_burden
        + refingerings * _REFINGERING_POINTS
        + fret_height_burden * _FRET_HEIGHT_POINTS
        + finger_crossover_burden * _FINGER_CROSSOVER_POINTS
    )

    if not fretted:
        return LeftHandTransition(
            previous_position,
            barre_awkward,
            fixed_effort,
            refingerings,
            barre_burden,
            finger_crossover_burden,
            fret_height_burden,
            0,
            0,
            0,
        )

    ranked: list[tuple[tuple[int, int, int, int, int, int], LeftHandTransition]] = []
    for position in _position_candidates(fretted, previous_position):
        awkward, deviation, deviation_effort = _position_metrics(
            fretted,
            position,
        )
        shift_distance = 0 if previous_position is None else abs(position - previous_position)
        shift_count = int(shift_distance > 0)
        shift_effort = (
            shift_count * _POSITION_SHIFT_EVENT_POINTS
            + shift_distance * _POSITION_SHIFT_FRET_POINTS
        )
        effort = deviation_effort + shift_effort + fixed_effort
        transition = LeftHandTransition(
            position,
            awkward + barre_awkward,
            effort,
            refingerings,
            barre_burden,
            finger_crossover_burden,
            fret_height_burden,
            deviation,
            shift_count,
            shift_distance,
        )
        ranked.append(
            (
                (
                    transition.awkward_events,
                    transition.effort,
                    transition.position_shift_count,
                    transition.position_shift_distance,
                    transition.position_deviation,
                    position,
                ),
                transition,
            )
        )
    return min(ranked, key=lambda item: item[0])[1]


def static_assignment_sort_key(
    items: Iterable[_FrettedLike],
) -> tuple[int, int, int, int, int]:
    """Cheap context-free ordering used only inside bounded frame retention.

    Runtime search still performs the contextual comparison.  This preliminary
    key merely ensures that the first retained assignment is a natural hand
    shape instead of an accidental one-finger barre.
    """

    fretted = _fretted(items)
    if not fretted:
        return (0, 0, 0, 0, 0)
    barre_awkward, barre_burden = _barre_metrics(fretted)
    finger_crossover_burden = _finger_crossover_burden(fretted)
    best_position = min(
        (
            _position_metrics(fretted, position) + (position,)
            for position in _position_candidates(fretted, None)
        ),
        key=lambda item: (item[0], item[2], item[1], item[3]),
    )
    position_awkward, deviation, deviation_effort, _position = best_position
    return (
        position_awkward + barre_awkward,
        deviation_effort + barre_burden + finger_crossover_burden * _FINGER_CROSSOVER_POINTS,
        barre_burden,
        finger_crossover_burden,
        deviation,
    )


__all__ = [
    "LEFT_HAND_MODEL_VERSION",
    "LeftHandTransition",
    "advance_left_hand",
    "static_assignment_sort_key",
]
