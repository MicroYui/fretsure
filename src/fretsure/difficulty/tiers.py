"""Difficulty tiers.

A tier is a tightened :class:`Profile` (position/reach limits) plus non-geometric
hard constraints (max simultaneous notes, whether barres are allowed, the highest
playable position). Layering these onto the oracle is what makes difficulty
*verifiable* — an output only claims a tier if it passes that tier's checker.

CALIBRATION: tier thresholds are v1 placeholders — fit against real learners
(roadmap D.4 design partner).
"""

from collections import defaultdict
from dataclasses import dataclass, replace
from fractions import Fraction

from fretsure.geometry import press_x
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.tab import Tab, TabNote

_SHIFT_MM = 25.0  # CALIBRATION: hand-centre move (mm) counted as a position shift


@dataclass(frozen=True)
class Tier:
    name: str
    profile: Profile
    max_simultaneous: int
    allow_barre: bool
    max_position: int
    max_shifts_per_bar: int


BEGINNER = Tier(
    "beginner",
    replace(MEDIAN_HAND, version="beginner@0.1", max_fret=5, hand_span_mm=90.0,
            v_shift_mm_per_s=400.0, r_max_hz=6.0),
    max_simultaneous=2, allow_barre=False, max_position=5, max_shifts_per_bar=2,
)
INTERMEDIATE = Tier(
    "intermediate",
    replace(MEDIAN_HAND, version="intermediate@0.1", max_fret=9, hand_span_mm=100.0,
            v_shift_mm_per_s=500.0, r_max_hz=8.0),
    max_simultaneous=3, allow_barre=True, max_position=9, max_shifts_per_bar=4,
)
ADVANCED = Tier(
    "advanced",
    replace(MEDIAN_HAND, version="advanced@0.1", max_fret=19, hand_span_mm=115.0,
            v_shift_mm_per_s=560.0, r_max_hz=10.0),
    max_simultaneous=4, allow_barre=True, max_position=19, max_shifts_per_bar=99,
)


def tier_violations(tab: Tab, tier: Tier, *, beats_per_bar: int = 4) -> list[str]:
    """Non-geometric tier constraints (max simultaneous, barres, position, shifts).

    Geometric feasibility under the tier's tightened profile is handled by the
    oracle; this is the tier-specific overlay. Deterministic (onset-sorted).
    """
    out: list[str] = []
    frames: defaultdict[Fraction, list[TabNote]] = defaultdict(list)
    for n in tab.notes:
        frames[n.onset].append(n)

    for onset in sorted(frames):
        if len(frames[onset]) > tier.max_simultaneous:
            out.append(
                f"too_many_simultaneous@{onset}: {len(frames[onset])}>{tier.max_simultaneous}"
            )

    # Barre = one finger holding the same fret on >1 string at overlapping times.
    # Checking time overlap (not just one frame) also catches held/arpeggiated barres.
    if not tier.allow_barre:
        fretted = sorted(
            (n for n in tab.notes if n.fret > 0 and n.left_finger > 0),
            key=lambda n: (n.onset, n.string),
        )
        for i, a in enumerate(fretted):
            for b in fretted[i + 1 :]:
                if a.left_finger != b.left_finger or a.fret != b.fret or a.string == b.string:
                    continue
                overlap = a.onset < b.onset + b.duration and b.onset < a.onset + a.duration
                if overlap:
                    out.append(f"barre_not_allowed@{min(a.onset, b.onset)}")
                    break

    for n in sorted(tab.notes, key=lambda x: (x.onset, x.string)):
        if n.fret > tier.max_position:
            out.append(f"above_position@{n.onset}: fret {n.fret}>{tier.max_position}")

    # Hand-position shifts per bar (uses beats_per_bar).
    centers: dict[Fraction, float] = {}
    for onset in sorted(frames):
        xs = [
            px
            for n in frames[onset]
            if n.fret > 0 and (px := press_x(tab.capo + n.fret, tier.profile.string_length_mm))
        ]
        if xs:
            centers[onset] = sum(xs) / len(xs)
    shifts_per_bar: defaultdict[int, int] = defaultdict(int)
    prev: float | None = None
    for onset in sorted(centers):
        if prev is not None and abs(centers[onset] - prev) > _SHIFT_MM:
            shifts_per_bar[int(onset // beats_per_bar)] += 1
        prev = centers[onset]
    for bar in sorted(shifts_per_bar):
        if shifts_per_bar[bar] > tier.max_shifts_per_bar:
            out.append(
                f"too_many_shifts@bar{bar}: {shifts_per_bar[bar]}>{tier.max_shifts_per_bar}"
            )

    return out
