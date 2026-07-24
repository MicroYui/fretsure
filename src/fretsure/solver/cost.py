"""Lexicographic quality model for the bounded fingering solver.

Playability remains the oracle's responsibility.  This module only ranks
already-admitted paths, using dimensionally separate fields rather than adding
physical millimetres to abstract fret/finger penalties.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction

from fretsure.geometry import press_x
from fretsure.oracle.profiles import Profile
from fretsure.solver.frames import FrameConfig

_MICROMETRES_PER_MILLIMETRE = 1_000
HandWindow = tuple[int, int]


def _to_micrometres(millimetres: float) -> int:
    """Snapshot geometry at deterministic micrometre resolution for ranking."""

    return round(millimetres * _MICROMETRES_PER_MILLIMETRE)


@dataclass(frozen=True, order=True, slots=True)
class QualityCost:
    """Fixed-width, lexicographically ordered path-quality accumulator.

    The field order is the objective order.  There are deliberately no weights
    or position thresholds: lower maximum position and lower duration-weighted
    fret exposure win first, followed by discrete shifts, physical shift
    distance, and small fingering/string-motion tiebreaks.
    """

    max_fret: int = 0
    fret_exposure: Fraction = Fraction(0)
    shift_count: int = 0
    shift_distance_um: int = 0
    finger_load: int = 0
    string_crossings: int = 0


def hand_window_for_frets(
    frets: Iterable[int],
    capo: int,
    profile: Profile,
) -> HandWindow | None:
    """Feasible hand-centre interval for the active positive-fret shape.

    Open strings impose no new left-hand constraint.  An intrinsically wider
    shape can occur on an AMBER path admitted by the optimistic oracle; for
    ranking it is represented by its midpoint rather than an inverted interval.
    The final oracle gate remains authoritative.
    """

    lower: int | None = None
    upper: int | None = None
    for fret in frets:
        if fret <= 0:
            continue
        px = press_x(capo + fret, profile.string_length_mm)
        assert px is not None  # positive effective fret
        note_lower = _to_micrometres(px - profile.reach_mm)
        note_upper = _to_micrometres(px + profile.reach_mm)
        lower = note_lower if lower is None else max(lower, note_lower)
        upper = note_upper if upper is None else min(upper, note_upper)
    if lower is None or upper is None:
        return None
    if lower <= upper:
        return (lower, upper)
    midpoint = (lower + upper) // 2
    return (midpoint, midpoint)


def config_hand_window(
    config: FrameConfig,
    capo: int,
    profile: Profile,
) -> HandWindow | None:
    """Feasible hand-centre interval for one attack configuration."""

    return hand_window_for_frets(
        (placement.fret for placement in config.placements),
        capo,
        profile,
    )


def hand_window_gap(left: HandWindow, right: HandWindow) -> int:
    """Minimum physical movement between two feasible hand intervals."""

    if left[1] < right[0]:
        return right[0] - left[1]
    if right[1] < left[0]:
        return left[0] - right[1]
    return 0


def advance_hand_window(
    previous: HandWindow | None,
    current_shape: HandWindow | None,
) -> tuple[HandWindow | None, bool, int]:
    """Advance the ergonomic hand state by one sounding shape.

    An unconstrained (all-open) shape preserves the previous position.  When
    intervals overlap, their intersection is exactly the set of positions that
    need no shift.  For disjoint intervals, the nearest boundary is the set of
    positions attaining the minimum movement paid by this transition.
    """

    if current_shape is None:
        return (previous, False, 0)
    if previous is None:
        return (current_shape, False, 0)

    lower = max(previous[0], current_shape[0])
    upper = min(previous[1], current_shape[1])
    if lower <= upper:
        return ((lower, upper), False, 0)
    gap = hand_window_gap(previous, current_shape)
    if previous[1] < current_shape[0]:
        destination = current_shape[0]
    else:
        destination = current_shape[1]
    return ((destination, destination), True, gap)


def config_fret_exposure(
    config: FrameConfig,
    durations: dict[int, Fraction],
) -> Fraction:
    """Exact, threshold-free sum of ``fret * sounding duration``."""

    return sum(
        (durations[placement.pitch] * placement.fret for placement in config.placements),
        start=Fraction(0),
    )


def config_finger_load(config: FrameConfig) -> int:
    """Number of distinct fretting fingers required by an attack frame."""

    return len(
        {
            placement.left_finger
            for placement in config.placements
            if placement.left_finger > 0
        }
    )


def string_crossing_distance(
    previous: FrameConfig | None,
    current: FrameConfig,
) -> int:
    """Discrete string travel between consecutive attack configurations.

    For polyphonic attacks the closest previous/current string pair is used;
    this is only a late tiebreak and never competes numerically with hand motion.
    """

    if previous is None or not previous.placements or not current.placements:
        return 0
    return min(
        abs(left.string - right.string)
        for left in previous.placements
        for right in current.placements
    )


def config_hand_center(config: FrameConfig, capo: int, profile: Profile) -> float | None:
    """Legacy diagnostic centre; the solver no longer ranks with this scalar."""
    xs: list[float] = []
    for p in config.placements:
        if p.fret > 0:
            px = press_x(capo + p.fret, profile.string_length_mm)
            assert px is not None  # fret > 0 => fretted
            xs.append(px)
    if not xs:
        return None
    return sum(xs) / len(xs)


def config_base_cost(config: FrameConfig) -> float:
    """Legacy scalar retained for API compatibility, not solver ranking."""
    fret_sum = float(sum(p.fret for p in config.placements))
    fingers_used = len({p.left_finger for p in config.placements if p.left_finger > 0})
    return fret_sum + 2.0 * fingers_used


def transition_cost(
    prev: FrameConfig, curr: FrameConfig, capo: int, profile: Profile
) -> float:
    """Legacy centre displacement retained for compatibility and diagnostics."""
    a = config_hand_center(prev, capo, profile)
    b = config_hand_center(curr, capo, profile)
    if a is None or b is None:
        return 0.0
    return abs(b - a)
