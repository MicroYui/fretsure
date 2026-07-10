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

from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.tab import Tab, TabNote


@dataclass(frozen=True)
class Tier:
    name: str
    profile: Profile
    max_simultaneous: int
    allow_barre: bool
    max_position: int
    max_shifts_per_bar: int


BEGINNER = Tier(
    "beginner", replace(MEDIAN_HAND, version="beginner@0.1", max_fret=5), 2, False, 5, 2
)
INTERMEDIATE = Tier(
    "intermediate", replace(MEDIAN_HAND, version="intermediate@0.1", max_fret=9), 3, True, 9, 4
)
ADVANCED = Tier(
    "advanced", replace(MEDIAN_HAND, version="advanced@0.1", max_fret=19), 4, True, 19, 99
)


def tier_violations(tab: Tab, tier: Tier, *, beats_per_bar: int = 4) -> list[str]:
    """Non-geometric tier constraints (max simultaneous notes, barres, position).

    Geometric feasibility under the tier's tightened profile is handled by the
    oracle; this is the tier-specific overlay. Deterministic (onset-sorted).
    """
    out: list[str] = []
    frames: defaultdict[Fraction, list[TabNote]] = defaultdict(list)
    for n in tab.notes:
        frames[n.onset].append(n)

    for onset in sorted(frames):
        notes = frames[onset]
        if len(notes) > tier.max_simultaneous:
            out.append(f"too_many_simultaneous@{onset}: {len(notes)}>{tier.max_simultaneous}")
        if not tier.allow_barre:
            by_finger: defaultdict[int, int] = defaultdict(int)
            for n in notes:
                if n.fret > 0 and n.left_finger > 0:
                    by_finger[n.left_finger] += 1
            if any(count > 1 for count in by_finger.values()):
                out.append(f"barre_not_allowed@{onset}")

    for n in sorted(tab.notes, key=lambda x: (x.onset, x.string)):
        if n.fret > tier.max_position:
            out.append(f"above_position@{n.onset}: fret {n.fret}>{tier.max_position}")
    return out
