"""Frozen, corpus-derived rhythm profiles for arrangement styles."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal, cast

STYLE_PROFILE_SCHEMA: Final = "fretsure-guitarset-style-profiles@0.1.0"
STYLE_PROFILE_VERSION: Final = "guitarset-style-profiles@0.1.0"
STYLE_PROFILE_SHA256: Final = (
    "c1a57bb1aa4599594db83f5fb9074e96b53be83a03d1e306e38ea5cae7df342d"
)

_PROFILE_PATH: Final = (
    Path(__file__).with_name("models") / "guitarset-style-profiles-v0.1.0.json"
)
_SUPPORTED_STYLES: Final = ("jazz", "rnb")


@dataclass(frozen=True, slots=True)
class StyleRhythmProfile:
    style: Literal["jazz", "rnb"]
    evidence_role: str
    source_family: str
    intermediate_phases: tuple[Fraction, ...]
    advanced_phases: tuple[Fraction, ...]
    answer_duration: Fraction


def _fraction(value: object, field: str) -> Fraction:
    if (
        type(value) is not list
        or len(value) != 2
        or any(type(item) is not int for item in value)
    ):
        raise RuntimeError(f"style profile {field} must be an integer fraction")
    numerator, denominator = cast(list[int], value)
    if denominator <= 0:
        raise RuntimeError(f"style profile {field} denominator must be positive")
    return Fraction(numerator, denominator)


def _phases(value: object, field: str) -> tuple[Fraction, ...]:
    if type(value) is not list:
        raise RuntimeError(f"style profile {field} must be an array")
    phases = tuple(
        _fraction(item, f"{field}[{index}]")
        for index, item in enumerate(cast(list[object], value))
    )
    if not phases or len(phases) != len(set(phases)):
        raise RuntimeError(f"style profile {field} must contain unique phases")
    if any(phase <= 0 or phase >= 4 for phase in phases):
        raise RuntimeError(f"style profile {field} phases must lie inside a 4/4 bar")
    return phases


@lru_cache(maxsize=1)
def _profiles() -> dict[str, StyleRhythmProfile]:
    raw = _PROFILE_PATH.read_bytes()
    if hashlib.sha256(raw).hexdigest() != STYLE_PROFILE_SHA256:
        raise RuntimeError("arrangement style profile digest does not match runtime")
    document = cast(dict[str, object], json.loads(raw))
    if document.get("schema") != STYLE_PROFILE_SCHEMA:
        raise RuntimeError("arrangement style profile schema does not match runtime")
    if document.get("profile_version") != STYLE_PROFILE_VERSION:
        raise RuntimeError("arrangement style profile version does not match runtime")
    raw_profiles = document.get("profiles")
    if type(raw_profiles) is not dict or set(raw_profiles) != set(_SUPPORTED_STYLES):
        raise RuntimeError("arrangement style profile set does not match runtime")

    result: dict[str, StyleRhythmProfile] = {}
    for style in _SUPPORTED_STYLES:
        raw_profile = cast(dict[str, object], raw_profiles[style])
        evidence_role = raw_profile.get("evidence_role")
        source_family = raw_profile.get("source_family")
        if type(evidence_role) is not str or not evidence_role:
            raise RuntimeError("arrangement style evidence role is invalid")
        if type(source_family) is not str or not source_family:
            raise RuntimeError("arrangement style source family is invalid")
        intermediate = _phases(
            raw_profile.get("intermediate_answer_phases"),
            f"profiles.{style}.intermediate_answer_phases",
        )
        advanced = _phases(
            raw_profile.get("advanced_answer_phases"),
            f"profiles.{style}.advanced_answer_phases",
        )
        if advanced[: len(intermediate)] != intermediate:
            raise RuntimeError("advanced style phases must extend intermediate phases")
        duration = _fraction(
            raw_profile.get("answer_duration_beats"),
            f"profiles.{style}.answer_duration_beats",
        )
        if duration <= 0 or duration > 1:
            raise RuntimeError("arrangement style answer duration is outside scope")
        result[style] = StyleRhythmProfile(
            style,
            evidence_role,
            source_family,
            intermediate,
            advanced,
            duration,
        )
    return result


def style_rhythm_profile(style: str) -> StyleRhythmProfile:
    """Return the frozen Jazz or provisional R&B rhythm profile."""

    profile = _profiles().get(style)
    if profile is None:
        raise ValueError("corpus rhythm profile exists only for jazz and rnb")
    return profile


def corpus_style_harmony_offsets(
    style: str,
    difficulty_tier: str,
) -> tuple[tuple[Fraction, Fraction], ...]:
    """Return corpus-derived phase/duration pairs for one difficulty tier."""

    if difficulty_tier == "beginner":
        return ()
    profile = style_rhythm_profile(style)
    if difficulty_tier == "intermediate":
        phases = profile.intermediate_phases
    elif difficulty_tier == "advanced":
        phases = profile.advanced_phases
    else:
        raise ValueError("difficulty tier must be beginner, intermediate, or advanced")
    return tuple((phase, profile.answer_duration) for phase in phases)


__all__ = [
    "STYLE_PROFILE_SCHEMA",
    "STYLE_PROFILE_SHA256",
    "STYLE_PROFILE_VERSION",
    "StyleRhythmProfile",
    "corpus_style_harmony_offsets",
    "style_rhythm_profile",
]
