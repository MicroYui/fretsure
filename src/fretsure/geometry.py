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


def d_max(i: int, j: int, hand_span_mm: float) -> float:
    """Max fingertip distance allowed between left-hand fingers ``i`` and ``j``.

    Fingers 1..4 span 3 gaps that together cover the full hand span, so
    ``d_max = (|i-j|/3) * hand_span``. Same finger -> 0 (barre handled elsewhere).
    """
    return (abs(i - j) / 3.0) * hand_span_mm


def open_pitch(string: int, tuning: tuple[int, ...], capo: int) -> int:
    return tuning[string] + capo


def note_pitch(string: int, fret: int, tuning: tuple[int, ...], capo: int) -> int:
    return open_pitch(string, tuning, capo) + fret
