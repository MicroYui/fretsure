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


class TierInputError(ValueError):
    """Typed rejection for a malformed public difficulty-tier model."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid tier.{field}: {detail}")


def snapshot_tier(tier: object, *, profile: Profile | None = None) -> Tier:
    """Validate and detach the non-geometric tier controls.

    The profile itself is validated and detached by the oracle/solver input
    boundary.  Capturing these remaining fields before that barrier prevents a
    low-level mutation from relaxing the overlay after the geometry was checked.
    """

    if type(tier) is not Tier:
        raise TierInputError("$", "must be an exact Tier")
    fields: dict[str, object] = {}
    for field in (
        "name",
        "profile",
        "max_simultaneous",
        "allow_barre",
        "max_position",
        "max_shifts_per_bar",
    ):
        try:
            fields[field] = object.__getattribute__(tier, field)
        except (AttributeError, TypeError):
            raise TierInputError(field, "required field is missing") from None

    name = fields["name"]
    if type(name) is not str or not name or len(name) > 128:
        raise TierInputError("name", "must be a non-empty exact string of at most 128 chars")
    max_simultaneous = fields["max_simultaneous"]
    if type(max_simultaneous) is not int or not 1 <= max_simultaneous <= 6:
        raise TierInputError("max_simultaneous", "must be an exact integer in 1..6")
    allow_barre = fields["allow_barre"]
    if type(allow_barre) is not bool:
        raise TierInputError("allow_barre", "must be an exact bool")
    max_position = fields["max_position"]
    if type(max_position) is not int or not 0 <= max_position <= 36:
        raise TierInputError("max_position", "must be an exact integer in 0..36")
    max_shifts = fields["max_shifts_per_bar"]
    if type(max_shifts) is not int or not 0 <= max_shifts <= 10_000:
        raise TierInputError(
            "max_shifts_per_bar",
            "must be an exact integer in 0..10000",
        )
    selected_profile = fields["profile"] if profile is None else profile
    if type(selected_profile) is not Profile:
        raise TierInputError("profile", "must be an exact Profile")
    return Tier(
        name=name,
        profile=selected_profile,
        max_simultaneous=max_simultaneous,
        allow_barre=allow_barre,
        max_position=max_position,
        max_shifts_per_bar=max_shifts,
    )


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
    tier = snapshot_tier(tier)
    # Local import keeps the representation modules acyclic.
    from fretsure.oracle.input import ensure_oracle_input

    tab, profile, _, beats_per_bar = ensure_oracle_input(
        tab,
        tier.profile,
        beats_per_bar=beats_per_bar,
    )
    tier = snapshot_tier(tier, profile=profile)
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
        groups: defaultdict[tuple[int, int], list[tuple[int, TabNote]]] = defaultdict(list)
        for index, note in enumerate(fretted):
            groups[(note.left_finger, note.fret)].append((index, note))
        flagged = [False] * len(fretted)
        for group in groups.values():
            minimum_later_onset: list[Fraction | None] = [None] * 6
            for index, note in reversed(group):
                earliest_other = min(
                    (
                        onset
                        for string, onset in enumerate(minimum_later_onset)
                        if string != note.string and onset is not None
                    ),
                    default=None,
                )
                if (
                    earliest_other is not None
                    and earliest_other < note.onset + note.duration
                ):
                    flagged[index] = True
                current = minimum_later_onset[note.string]
                if current is None or note.onset < current:
                    minimum_later_onset[note.string] = note.onset
        out.extend(
            f"barre_not_allowed@{note.onset}"
            for index, note in enumerate(fretted)
            if flagged[index]
        )

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
