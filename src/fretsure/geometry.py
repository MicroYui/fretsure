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
# Outermost string centres at one fret.  This is a property of the instrument,
# not of a hand, and it is the floor every finger pair has to clear -- see
# ``d_max``.
NECK_WIDTH_MM: float = (len(STANDARD_TUNING) - 1) * STRING_SPACING_MM


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
    is handled separately, hence 0 -- and hence exempt from the floor below.

    **Every pair reaches at least across the neck.**  The factors alone did not:
    the outermost string centres sit ``NECK_WIDTH_MM`` = 52.5 mm apart at one
    fret, while (2, 3) and (3, 4) allowed 50.0 mm on the median hand and 45.0 mm
    on the small one.  A model in which two fingers cannot touch the outer
    strings at the same fret is not a strict model of a hand; it is a model of
    something that cannot play the instrument, and it refused a G major chord::

        G major, 3-2-0-0-0-3, spelled as a thumb sweep plus i-m-a
            small@0.1   RED    FRET_SPAN over by 7.5 mm
            median@0.1  AMBER  FRET_SPAN over by 2.5 mm
        the same chord with the high-E note removed
            every profile  GREEN

    The floor is therefore not a calibration choice and does not come from
    fitting the corpus -- five earlier attempts did exactly that and bought +2
    pieces between them.  It comes from the instrument: a guitarist demonstrably
    places two fingers on the outer strings at one fret, so no hand model may
    say otherwise, whatever its span.

    This was hidden because the guard that should have caught it was scoring the
    other way.  ``replay_negative_tabs.py`` treats any drift toward GREEN over
    1,718 raw-LLM tabs as the verifier weakening, and the tabs are known-bad by
    provenance rather than by inspection.  Twelve of them turn GREEN when the
    span rule stops refusing across-neck shapes, and eleven of those twelve are
    refused on a pair whose along-neck separation is **zero** -- ordinary chords,
    counted as false certifications.
    """

    low, high = min(i, j), max(i, j)
    if low == high:
        return 0.0
    return max(_SPAN_FACTORS[(low, high)] * hand_span_mm, NECK_WIDTH_MM)


def open_pitch(string: int, tuning: tuple[int, ...], capo: int) -> int:
    return tuning[string] + capo


def note_pitch(string: int, fret: int, tuning: tuple[int, ...], capo: int) -> int:
    return open_pitch(string, tuning, capo) + fret
