"""Millimetre neck geometry.

Span feasibility is a *physical* fact, so it is modelled in millimetres, never
in fret counts: ``fret_x(f) = L * (1 - 2**(-f/12))``. A 4-fret stretch near the
nut is far wider than the same 4-fret stretch high up the neck, and a
fret-count model would wrongly treat them as equal.

The absolute constants (string spacing, ``d_max`` scaling) are v1 placeholders.

CALIBRATION: fit ``d_max`` / ``STRING_SPACING_MM`` against real players — see
roadmap D.4 (design partner). Correctness *direction* is guarded by the
property/metamorphic/mutation suites regardless of the absolute numbers.
"""

import math
from typing import Final

STANDARD_TUNING: tuple[int, ...] = (40, 45, 50, 55, 59, 64)  # E A D G B E, low -> high
STRING_SPACING_MM: float = 10.5  # adjacent-string centre distance (v1 constant)
DEFAULT_STRING_LENGTH_MM: float = 648.0  # classical scale length


def fret_x(f: int, length_mm: float = DEFAULT_STRING_LENGTH_MM) -> float:
    """Distance from the nut to fret wire ``f`` (mm). ``fret_x(0) == 0``."""
    return length_mm * (1.0 - math.pow(2.0, -f / 12.0))


def press_x(f: int, length_mm: float = DEFAULT_STRING_LENGTH_MM) -> float | None:
    """Fingertip press position for a fretted note (mm), or ``None`` if open.

    For ``f >= 1`` the fingertip sits between wire ``f-1`` and wire ``f``.
    """
    if f <= 0:
        return None
    return (fret_x(f - 1, length_mm) + fret_x(f, length_mm)) / 2.0


def string_y(string: int) -> float:
    """Lateral position of a string centre (mm)."""
    return string * STRING_SPACING_MM


def fingertip_xy(
    string: int, fret: int, length_mm: float = DEFAULT_STRING_LENGTH_MM
) -> tuple[float, float] | None:
    """(x, y) of the fingertip for a fretted note, or ``None`` for an open string.

    ``fret`` here is the **absolute** fret measured from the nut. Fret wire
    positions do not move when a capo is fitted, so a capo-aware caller must
    pass ``capo + capo_relative_fret`` — never the bare capo-relative fret.
    """
    px = press_x(fret, length_mm)
    if px is None:
        return None
    return (px, string_y(string))


def euclid(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


# Per unordered finger pair, as a fraction of the profile's 1--4 span.
#
# This was a function of the printed finger-number gap alone until 2026-07-27,
# which gave index-middle and ring-little identical allowance -- and the gap is
# not what a hand cares about.  Exact enumeration over the failing frames of 389
# published scores put 13 of the 16 span-only refusals on an *adjacent* pair,
# and only 3 on the 1--4 pair that earlier work had tried widening; raising 1--4
# by half changed nothing at all.
#
# Only (1, 2) moved, from 0.50 to 0.55, and the measurement behind that number is
# unusually specific: +2 published scores accepted, zero previously-accepted
# scores lost, and on the 1,718 known-unplayable tabs **zero RED verdicts became
# GREEN** -- the four extra certifications all came from AMBER, the band where
# the oracle had already declined to commit.  Widening (2, 3) as well cost a
# published score, so it did not move.
_SPAN_FACTORS: Final[dict[tuple[int, int], float]] = {
    (1, 1): 0.0, (2, 2): 0.0, (3, 3): 0.0, (4, 4): 0.0,
    (1, 2): 0.55,
    (2, 3): 0.5,
    (3, 4): 0.5,
    (1, 3): 0.9,
    (2, 4): 0.9,
    (1, 4): 1.0,
}


def d_max(i: int, j: int, hand_span_mm: float) -> float:
    """Max fingertip distance allowed between left-hand fingers ``i`` and ``j``.

    ``hand_span_mm`` is the 1--4 fingertip span by definition, so that pair is
    1.0 and every other is a fraction of it.  Same-finger geometry is a barre and
    is handled separately, hence 0.

    The factors are not a curve fitted to the corpus: each is a landmark that
    admits canonical open-position shapes for the median profile, and only one
    of them has been moved since, against a two-sided measurement.
    """

    return _SPAN_FACTORS[(min(i, j), max(i, j))] * hand_span_mm


def open_pitch(string: int, tuning: tuple[int, ...], capo: int) -> int:
    return tuning[string] + capo


def note_pitch(string: int, fret: int, tuning: tuple[int, ...], capo: int) -> int:
    return open_pitch(string, tuning, capo) + fret
