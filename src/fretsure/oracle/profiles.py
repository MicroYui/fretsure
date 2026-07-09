"""Playability profiles: parameterized, semantically versioned hand models.

Everything the oracle needs about the player/instrument lives here. The
pessimistic/optimistic transforms give the three-state verdict its soundness
direction: GREEN = passes the *pessimistic* profile, RED = fails the
*optimistic* one.

PLACEHOLDER CALIBRATION — the absolute numbers are v1 placeholders to be fit
against real players (roadmap D.4). Only their ordering/direction is asserted.
"""

from dataclasses import dataclass, replace

from fretsure.geometry import DEFAULT_STRING_LENGTH_MM


@dataclass(frozen=True)
class Profile:
    version: str
    hand_span_mm: float  # max fingertip 1..4 distance
    reach_mm: float  # RESERVED for future position-dependent reach compression;
    # not yet consumed by any predicate (v1 folds reach into d_max via hand_span).
    v_shift_mm_per_s: float  # hand-shift speed ceiling
    r_max_hz: float  # single right-finger repeat-rate ceiling
    string_length_mm: float
    max_fret: int = 22


SMALL_HAND = Profile("small@0.1", 90.0, 45.0, 450.0, 7.0, DEFAULT_STRING_LENGTH_MM)
MEDIAN_HAND = Profile("median@0.1", 100.0, 50.0, 500.0, 8.0, DEFAULT_STRING_LENGTH_MM)
LARGE_HAND = Profile("large@0.1", 115.0, 58.0, 560.0, 9.0, DEFAULT_STRING_LENGTH_MM)

_PESS = 0.9
_OPT = 1.1


def pessimistic(p: Profile) -> Profile:
    """A stricter profile (smaller hand/reach, slower shift, lower repeat rate)."""
    return replace(
        p,
        version=f"{p.version}/pess",
        hand_span_mm=p.hand_span_mm * _PESS,
        reach_mm=p.reach_mm * _PESS,
        v_shift_mm_per_s=p.v_shift_mm_per_s * _PESS,
        r_max_hz=p.r_max_hz * _PESS,
    )


def optimistic(p: Profile) -> Profile:
    """A looser profile (bigger hand/reach, faster shift, higher repeat rate)."""
    return replace(
        p,
        version=f"{p.version}/opt",
        hand_span_mm=p.hand_span_mm * _OPT,
        reach_mm=p.reach_mm * _OPT,
        v_shift_mm_per_s=p.v_shift_mm_per_s * _OPT,
        r_max_hz=p.r_max_hz * _OPT,
    )
