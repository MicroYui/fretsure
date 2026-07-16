"""Playability profiles: parameterized, semantically versioned hand models.

Everything the oracle needs about the player/instrument lives here. The
pessimistic/optimistic transforms give the three-state verdict its soundness
direction: GREEN = passes the *pessimistic* profile, RED = fails the
*optimistic* one.

PLACEHOLDER CALIBRATION — the absolute numbers are v1 placeholders to be fit
against real players (roadmap D.4). Only their ordering/direction is asserted.
"""

import hashlib
import json
import math
import re
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import cast

from fretsure.geometry import DEFAULT_STRING_LENGTH_MM

MAX_SUPPORTED_FRET = 36
"""Inclusive safety bound for the ordinary-guitar profile input domain."""

_PROFILE_CANONICAL_SCHEMA = "fretsure.profile@1"
_PROFILE_VERSION_MAX_LENGTH = 128
_PROFILE_FRACTION_COMPONENT_BITS = 256
_PROFILE_VERSION_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9._+@-]*(?:/[A-Za-z0-9][A-Za-z0-9._+@-]*)*"
)
_REAL_FIELDS = (
    "hand_span_mm",
    "reach_mm",
    "v_shift_mm_per_s",
    "r_max_hz",
    "string_length_mm",
)
MIN_PROFILE_NUMERIC_VALUE = 1e-6
# Deliberately generous ceilings for the advertised ordinary-guitar model,
# while remaining low enough that a caller cannot turn the profile into an
# arbitrary/infinite-resource certificate.
MAX_HAND_SPAN_MM = 250.0
MAX_REACH_MM = 200.0
MAX_SHIFT_MM_PER_S = 5_000.0
MAX_RIGHT_HAND_RATE_HZ = 50.0
MAX_STRING_LENGTH_MM = 1_000.0
_REAL_BOUNDS = {
    "hand_span_mm": (MIN_PROFILE_NUMERIC_VALUE, MAX_HAND_SPAN_MM),
    "reach_mm": (MIN_PROFILE_NUMERIC_VALUE, MAX_REACH_MM),
    "v_shift_mm_per_s": (MIN_PROFILE_NUMERIC_VALUE, MAX_SHIFT_MM_PER_S),
    "r_max_hz": (MIN_PROFILE_NUMERIC_VALUE, MAX_RIGHT_HAND_RATE_HZ),
    "string_length_mm": (MIN_PROFILE_NUMERIC_VALUE, MAX_STRING_LENGTH_MM),
}


@dataclass(frozen=True, slots=True)
class ProfileValidationIssue:
    """A stable, machine-readable reason that a profile is outside the input domain."""

    code: str
    path: str
    message: str


@dataclass(frozen=True)
class Profile:
    version: str
    hand_span_mm: float  # max fingertip 1..4 distance
    reach_mm: float  # one-sided longitudinal hand-centre reach radius for shift overlap
    v_shift_mm_per_s: float  # hand-shift speed ceiling
    r_max_hz: float  # single right-finger repeat-rate ceiling
    string_length_mm: float
    max_fret: int = 22

    def __post_init__(self) -> None:
        # Construction accepts only inert built-in numeric representations,
        # then stores exact plain floats.  Public revalidation can therefore
        # reject a low-level-forged float subclass instead of executing its
        # overloaded arithmetic inside a predicate.
        for field in _REAL_FIELDS:
            try:
                raw = object.__getattribute__(self, field)
            except (AttributeError, TypeError):
                continue
            if type(raw) not in (int, float, Fraction):
                continue
            try:
                if type(raw) is Fraction:
                    numerator = object.__getattribute__(raw, "_numerator")
                    denominator = object.__getattribute__(raw, "_denominator")
                    if (
                        type(numerator) is not int
                        or type(denominator) is not int
                        or numerator.bit_length() > _PROFILE_FRACTION_COMPONENT_BITS
                        or denominator.bit_length() > _PROFILE_FRACTION_COMPONENT_BITS
                        or denominator <= 0
                        or math.gcd(numerator, denominator) != 1
                    ):
                        continue
                    # Convert an internally owned exact snapshot, never the caller's
                    # potentially low-level-forged Fraction object.
                    normalized = float(Fraction(numerator, denominator))
                else:
                    normalized = float(raw)
            except (OverflowError, TypeError, ValueError):
                continue
            object.__setattr__(self, field, normalized)

        issues = validate_profile(self)
        if issues:
            raise ValueError(_format_validation_issues(issues))

    @property
    def fingerprint(self) -> str:
        """SHA-256 of the canonical, schema-tagged profile representation."""

        return profile_fingerprint(self)


def _issue(code: str, path: str, message: str) -> ProfileValidationIssue:
    return ProfileValidationIssue(code=code, path=path, message=message)


def _format_validation_issues(issues: tuple[ProfileValidationIssue, ...]) -> str:
    return "; ".join(f"{issue.code} at {issue.path}: {issue.message}" for issue in issues)


def snapshot_profile(profile: object) -> object:
    """Capture each exact Profile field once into an externally unreachable copy."""

    if type(profile) is not Profile:
        return profile
    snapshot = object.__new__(Profile)
    for field in ("version", *_REAL_FIELDS, "max_fret"):
        try:
            value = object.__getattribute__(profile, field)
        except (AttributeError, TypeError):
            continue
        object.__setattr__(snapshot, field, value)
    return snapshot


def validated_profile_snapshot(profile: object) -> Profile:
    """Return a detached valid profile or raise a bounded ValueError."""

    snapshot = snapshot_profile(profile)
    issues = validate_profile(snapshot)
    if issues:
        raise ValueError(_format_validation_issues(issues))
    return cast(Profile, snapshot)


def _read_field(
    profile: Profile, field: str
) -> tuple[object | None, ProfileValidationIssue | None]:
    try:
        return object.__getattribute__(profile, field), None
    except (AttributeError, TypeError):
        return None, _issue(
            "PROFILE_FIELD_MISSING",
            f"$.{field}",
            f"{field} is missing",
        )


def validate_profile(profile: object) -> tuple[ProfileValidationIssue, ...]:
    """Validate an untrusted profile without mutating it or raising.

    Revalidation is intentionally public: a frozen dataclass protects ordinary
    callers, but adapters at trust boundaries must also fail closed if an
    object was forged or mutated through low-level Python mechanisms.
    """

    if type(profile) is not Profile:
        return (
            _issue(
                "PROFILE_TYPE",
                "$",
                "profile must be an exact Profile instance",
            ),
        )

    issues: list[ProfileValidationIssue] = []

    version, missing = _read_field(profile, "version")
    if missing is not None:
        issues.append(missing)
    elif type(version) is not str:
        issues.append(
            _issue(
                "PROFILE_VERSION_TYPE",
                "$.version",
                "version must be a string",
            )
        )
    elif (
        len(version) > _PROFILE_VERSION_MAX_LENGTH
        or _PROFILE_VERSION_RE.fullmatch(version) is None
    ):
        issues.append(
            _issue(
                "PROFILE_VERSION_FORMAT",
                "$.version",
                (
                    "version must be 1-128 ASCII identifier characters with non-empty "
                    "slash-separated segments"
                ),
            )
        )

    for field in _REAL_FIELDS:
        value, missing = _read_field(profile, field)
        if missing is not None:
            issues.append(missing)
            continue
        if type(value) is not float:
            issues.append(
                _issue(
                    "PROFILE_NUMERIC_TYPE",
                    f"$.{field}",
                    f"{field} must be stored as a normalized plain float",
                )
            )
            continue
        numeric = value
        if not math.isfinite(numeric):
            issues.append(
                _issue(
                    "PROFILE_NUMERIC_NOT_FINITE",
                    f"$.{field}",
                    f"{field} must be finite",
                )
            )
        else:
            lower, upper = _REAL_BOUNDS[field]
            if numeric <= 0.0:
                issues.append(
                    _issue(
                        "PROFILE_NUMERIC_NOT_POSITIVE",
                        f"$.{field}",
                        f"{field} must be greater than zero",
                    )
                )
            elif numeric < lower:
                issues.append(
                    _issue(
                        "PROFILE_NUMERIC_BELOW_MIN",
                        f"$.{field}",
                        f"{field} must be at least {lower:g}",
                    )
                )
            elif numeric > upper:
                issues.append(
                    _issue(
                        "PROFILE_NUMERIC_ABOVE_MAX",
                        f"$.{field}",
                        f"{field} must not exceed {upper:g}",
                    )
                )

    max_fret, missing = _read_field(profile, "max_fret")
    if missing is not None:
        issues.append(missing)
    elif type(max_fret) is not int:
        issues.append(
            _issue(
                "PROFILE_MAX_FRET_TYPE",
                "$.max_fret",
                "max_fret must be an exact int; bool is not accepted",
            )
        )
    elif not 0 <= max_fret <= MAX_SUPPORTED_FRET:
        issues.append(
            _issue(
                "PROFILE_MAX_FRET_RANGE",
                "$.max_fret",
                f"max_fret must be between 0 and {MAX_SUPPORTED_FRET} inclusive",
            )
        )

    return tuple(issues)


def canonical_profile_bytes(profile: Profile) -> bytes:
    """Return the canonical, field-order-stable representation of ``profile``.

    Hexadecimal floats are exact and independent of locale or decimal display
    policy. The explicit schema tag and field order make future changes
    deliberate instead of silently changing reproducibility stamps.
    """

    profile = validated_profile_snapshot(profile)

    payload: dict[str, str | int] = {
        "schema": _PROFILE_CANONICAL_SCHEMA,
        "version": profile.version,
        "hand_span_mm": float(profile.hand_span_mm).hex(),
        "reach_mm": float(profile.reach_mm).hex(),
        "v_shift_mm_per_s": float(profile.v_shift_mm_per_s).hex(),
        "r_max_hz": float(profile.r_max_hz).hex(),
        "string_length_mm": float(profile.string_length_mm).hex(),
        "max_fret": profile.max_fret,
    }
    return json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii")


def profile_fingerprint(profile: Profile) -> str:
    """Return a stable lowercase hexadecimal SHA-256 profile fingerprint."""

    return hashlib.sha256(canonical_profile_bytes(profile)).hexdigest()


SMALL_HAND = Profile("small@0.1", 90.0, 45.0, 450.0, 7.0, DEFAULT_STRING_LENGTH_MM)
MEDIAN_HAND = Profile("median@0.1", 100.0, 50.0, 500.0, 8.0, DEFAULT_STRING_LENGTH_MM)
LARGE_HAND = Profile("large@0.1", 115.0, 58.0, 560.0, 9.0, DEFAULT_STRING_LENGTH_MM)

_PESS = 0.9
_OPT = 1.1


def _derived_version(version: str, suffix: str) -> str:
    candidate = f"{version}/{suffix}"
    if len(candidate) <= _PROFILE_VERSION_MAX_LENGTH:
        return candidate
    digest = hashlib.sha256(version.encode("ascii")).hexdigest()
    return f"derived-{digest}/{suffix}"


def _scaled(field: str, value: float, factor: float) -> float:
    lower, upper = _REAL_BOUNDS[field]
    return min(upper, max(lower, value * factor))


def pessimistic(p: Profile) -> Profile:
    """A stricter profile (smaller hand/reach, slower shift, lower repeat rate)."""
    p = validated_profile_snapshot(p)
    return replace(
        p,
        version=_derived_version(p.version, "pess"),
        hand_span_mm=_scaled("hand_span_mm", p.hand_span_mm, _PESS),
        reach_mm=_scaled("reach_mm", p.reach_mm, _PESS),
        v_shift_mm_per_s=_scaled("v_shift_mm_per_s", p.v_shift_mm_per_s, _PESS),
        r_max_hz=_scaled("r_max_hz", p.r_max_hz, _PESS),
    )


def optimistic(p: Profile) -> Profile:
    """A looser profile (bigger hand/reach, faster shift, higher repeat rate)."""
    p = validated_profile_snapshot(p)
    return replace(
        p,
        version=_derived_version(p.version, "opt"),
        hand_span_mm=_scaled("hand_span_mm", p.hand_span_mm, _OPT),
        reach_mm=_scaled("reach_mm", p.reach_mm, _OPT),
        v_shift_mm_per_s=_scaled("v_shift_mm_per_s", p.v_shift_mm_per_s, _OPT),
        r_max_hz=_scaled("r_max_hz", p.r_max_hz, _OPT),
    )
