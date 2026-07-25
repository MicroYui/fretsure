"""Versioned player technique preferences for GREEN fingering selection.

These preferences rank only candidates that already passed the complete
playability Oracle.  They do not weaken physical limits and are independent of
the beginner/intermediate/advanced score-difficulty checker.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from fretsure.solver.cost import QualityCost

TECHNIQUE_PROFILE_REGISTRY_VERSION = "technique-profile-registry@0.1.0"
DEFAULT_TECHNIQUE_PROFILE = "balanced"


@dataclass(frozen=True, slots=True)
class TechniqueProfile:
    id: str
    label: str
    description: str
    prompt_guidance: str


_PROFILES: tuple[TechniqueProfile, ...] = (
    TechniqueProfile(
        "balanced",
        "Balanced",
        "Use the solver's general-purpose ergonomic ordering.",
        "Prefer natural, stable fingering without a special technique bias.",
    ),
    TechniqueProfile(
        "avoid_barres",
        "Avoid barres",
        "Prefer GREEN solutions with less barre burden.",
        "Keep chord textures economical so the fingering can avoid or shorten barres.",
    ),
    TechniqueProfile(
        "low_position",
        "Low position",
        "Prefer lower frets when several GREEN solutions exist.",
        "Prefer voicings that can remain in the lower positions of the neck.",
    ),
    TechniqueProfile(
        "minimize_shifts",
        "Fewer shifts",
        "Prefer GREEN solutions with fewer and shorter position changes.",
        "Favor connected voicings and avoid unnecessary position changes.",
    ),
)

TECHNIQUE_PROFILES: Mapping[str, TechniqueProfile] = MappingProxyType(
    {profile.id: profile for profile in _PROFILES}
)
TECHNIQUE_PROFILE_NAMES = tuple(TECHNIQUE_PROFILES)


def technique_profile(name: object) -> TechniqueProfile:
    if type(name) is not str or name not in TECHNIQUE_PROFILES:
        raise ValueError(
            "technique_profile must be one of " + ", ".join(TECHNIQUE_PROFILE_NAMES)
        )
    return TECHNIQUE_PROFILES[name]


def technique_quality_key(
    quality: QualityCost,
    profile_name: str,
) -> tuple[object, ...]:
    """Return a deterministic preference key for a certified GREEN finalist."""

    technique_profile(profile_name)
    if profile_name == "avoid_barres":
        return (
            quality.barre_burden,
            quality.awkward_fingering_events,
            quality.left_hand_effort,
            quality,
        )
    if profile_name == "low_position":
        return (quality.max_fret, quality.fret_exposure, quality)
    if profile_name == "minimize_shifts":
        return (
            quality.position_shift_count,
            quality.position_shift_distance,
            quality.shift_count,
            quality.shift_distance_um,
            quality,
        )
    return (quality,)


__all__ = [
    "DEFAULT_TECHNIQUE_PROFILE",
    "TECHNIQUE_PROFILE_NAMES",
    "TECHNIQUE_PROFILE_REGISTRY_VERSION",
    "TECHNIQUE_PROFILES",
    "TechniqueProfile",
    "technique_profile",
    "technique_quality_key",
]
