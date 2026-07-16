import dataclasses
import re
import sys
from dataclasses import replace
from fractions import Fraction

import pytest

import fretsure.oracle.profiles as profiles_module
from fretsure.oracle.profiles import (
    LARGE_HAND,
    MAX_HAND_SPAN_MM,
    MAX_REACH_MM,
    MAX_RIGHT_HAND_RATE_HZ,
    MAX_SHIFT_MM_PER_S,
    MAX_STRING_LENGTH_MM,
    MAX_SUPPORTED_FRET,
    MEDIAN_HAND,
    SMALL_HAND,
    Profile,
    ProfileValidationIssue,
    canonical_profile_bytes,
    optimistic,
    pessimistic,
    profile_fingerprint,
    validate_profile,
)


def test_three_presets_exist_with_versions() -> None:
    for p, tag in ((SMALL_HAND, "small"), (MEDIAN_HAND, "median"), (LARGE_HAND, "large")):
        assert isinstance(p, Profile)
        assert tag in p.version


def test_preset_hand_span_ordering() -> None:
    assert SMALL_HAND.hand_span_mm < MEDIAN_HAND.hand_span_mm < LARGE_HAND.hand_span_mm


def test_pessimistic_is_stricter_everywhere() -> None:
    p = MEDIAN_HAND
    q = pessimistic(p)
    # "harder to pass": smaller hand/reach, slower shift, lower repeat rate
    assert q.hand_span_mm < p.hand_span_mm
    assert q.reach_mm < p.reach_mm
    assert q.v_shift_mm_per_s < p.v_shift_mm_per_s
    assert q.r_max_hz < p.r_max_hz


def test_optimistic_is_looser_everywhere() -> None:
    p = MEDIAN_HAND
    q = optimistic(p)
    assert q.hand_span_mm > p.hand_span_mm
    assert q.reach_mm > p.reach_mm
    assert q.v_shift_mm_per_s > p.v_shift_mm_per_s
    assert q.r_max_hz > p.r_max_hz


def test_pessimistic_carries_distinct_version() -> None:
    assert pessimistic(MEDIAN_HAND).version != MEDIAN_HAND.version
    assert optimistic(MEDIAN_HAND).version != MEDIAN_HAND.version


def test_profile_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        MEDIAN_HAND.hand_span_mm = 1.0  # type: ignore[misc]


@pytest.mark.parametrize(
    "version",
    [
        "",
        " ",
        " median@0.1",
        "median@0.1 ",
        "median hand@0.1",
        "median\n@0.1",
        "/median@0.1",
        "median@0.1//pess",
        "a" * 129,
    ],
)
def test_profile_rejects_malformed_version(version: str) -> None:
    with pytest.raises(ValueError, match="version"):
        replace(MEDIAN_HAND, version=version)


def test_profile_rejects_non_string_version() -> None:
    with pytest.raises(ValueError, match="version"):
        replace(MEDIAN_HAND, version=1)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field",
    [
        "hand_span_mm",
        "reach_mm",
        "v_shift_mm_per_s",
        "r_max_hz",
        "string_length_mm",
    ],
)
@pytest.mark.parametrize(
    "value",
    [True, False, "1", None, 1 + 0j, float("nan"), float("inf"), -float("inf"), 0, -1],
)
def test_profile_rejects_invalid_positive_finite_real_fields(
    field: str, value: object
) -> None:
    with pytest.raises(ValueError, match=field):
        replace(MEDIAN_HAND, **{field: value})  # type: ignore[arg-type]


def test_profile_accepts_and_normalizes_real_numeric_fields() -> None:
    profile = Profile(
        "numeric@1/test",
        90,
        Fraction(91, 2),
        450,
        Fraction(15, 2),
        648,
        22,
    )

    assert profile.hand_span_mm == 90.0
    assert profile.reach_mm == 45.5
    assert profile.v_shift_mm_per_s == 450.0
    assert profile.r_max_hz == 7.5
    assert profile.string_length_mm == 648.0
    assert all(
        isinstance(value, float)
        for value in (
            profile.hand_span_mm,
            profile.reach_mm,
            profile.v_shift_mm_per_s,
            profile.r_max_hz,
            profile.string_length_mm,
        )
    )


def test_profile_constructor_rejects_forged_fraction_without_running_hooks() -> None:
    class HostileInt(int):
        def __int__(self) -> int:
            raise AssertionError("hostile Fraction component was converted")

    value = Fraction(1, 2)
    object.__setattr__(value, "_numerator", HostileInt(1))

    with pytest.raises(ValueError, match="hand_span_mm"):
        Profile("hostile@1", value, 45, 450, 7, 648)


@pytest.mark.parametrize("max_fret", [True, False, -1, 22.0, 37, "22", None])
def test_profile_rejects_invalid_max_fret(max_fret: object) -> None:
    with pytest.raises(ValueError, match="max_fret"):
        replace(MEDIAN_HAND, max_fret=max_fret)  # type: ignore[arg-type]


@pytest.mark.parametrize("max_fret", [0, MAX_SUPPORTED_FRET])
def test_profile_accepts_safe_max_fret_boundaries(max_fret: int) -> None:
    assert replace(MEDIAN_HAND, max_fret=max_fret).max_fret == max_fret


def test_profile_max_fret_contract_is_ordinary_guitar_safe_bound() -> None:
    assert MAX_SUPPORTED_FRET == 36


def test_canonical_profile_serialization_has_frozen_schema_and_field_order() -> None:
    profile = Profile("profile@1/test", 1.0, 2.0, 4.0, 8.0, 16.0, 22)

    assert canonical_profile_bytes(profile) == (
        b'{"schema":"fretsure.profile@1","version":"profile@1/test",'
        b'"hand_span_mm":"0x1.0000000000000p+0",'
        b'"reach_mm":"0x1.0000000000000p+1",'
        b'"v_shift_mm_per_s":"0x1.0000000000000p+2",'
        b'"r_max_hz":"0x1.0000000000000p+3",'
        b'"string_length_mm":"0x1.0000000000000p+4","max_fret":22}'
    )


def test_profile_fingerprint_is_stable_lowercase_sha256() -> None:
    profile = Profile("profile@1/test", 1.0, 2.0, 4.0, 8.0, 16.0, 22)

    assert profile_fingerprint(profile) == (
        "e38ad7ee03ae3887356011a9e5d19755106155abd51cb20b9d0f9fa48e23c42b"
    )
    assert profile.fingerprint == profile_fingerprint(profile)
    assert re.fullmatch(r"[0-9a-f]{64}", profile.fingerprint)


def test_profile_fingerprint_binds_identity_and_every_model_parameter() -> None:
    base = MEDIAN_HAND
    variants = (
        replace(base, version="median@0.1/variant"),
        replace(base, hand_span_mm=base.hand_span_mm + 1.0),
        replace(base, reach_mm=base.reach_mm + 1.0),
        replace(base, v_shift_mm_per_s=base.v_shift_mm_per_s + 1.0),
        replace(base, r_max_hz=base.r_max_hz + 1.0),
        replace(base, string_length_mm=base.string_length_mm + 1.0),
        replace(base, max_fret=base.max_fret + 1),
    )

    assert len({base.fingerprint, *(profile.fingerprint for profile in variants)}) == 8


def test_equivalent_real_numeric_inputs_have_same_canonical_fingerprint() -> None:
    ints = Profile("equivalent@1", 90, 45, 450, 7, 648, 22)
    fractions = Profile(
        "equivalent@1",
        Fraction(90),
        Fraction(45),
        Fraction(450),
        Fraction(7),
        Fraction(648),
        22,
    )

    assert canonical_profile_bytes(ints) == canonical_profile_bytes(fractions)
    assert ints.fingerprint == fractions.fingerprint


def test_profile_transforms_remain_valid_monotone_and_reproducible() -> None:
    pess = pessimistic(MEDIAN_HAND)
    opt = optimistic(MEDIAN_HAND)

    assert pess.max_fret == MEDIAN_HAND.max_fret == opt.max_fret
    assert pess.string_length_mm == MEDIAN_HAND.string_length_mm == opt.string_length_mm
    assert pess.hand_span_mm < MEDIAN_HAND.hand_span_mm < opt.hand_span_mm
    assert pess.reach_mm < MEDIAN_HAND.reach_mm < opt.reach_mm
    assert pess.v_shift_mm_per_s < MEDIAN_HAND.v_shift_mm_per_s < opt.v_shift_mm_per_s
    assert pess.r_max_hz < MEDIAN_HAND.r_max_hz < opt.r_max_hz
    assert pessimistic(MEDIAN_HAND).fingerprint == pess.fingerprint
    assert optimistic(MEDIAN_HAND).fingerprint == opt.fingerprint


def test_validate_profile_is_public_total_and_side_effect_free() -> None:
    assert validate_profile(MEDIAN_HAND) == ()
    assert validate_profile(MEDIAN_HAND) == ()

    wrong_type = validate_profile({"version": "median@0.1"})
    assert wrong_type == (
        ProfileValidationIssue(
            code="PROFILE_TYPE",
            path="$",
            message="profile must be an exact Profile instance",
        ),
    )


def test_validate_profile_reports_stable_ordered_issues_for_corrupted_profile() -> None:
    profile = replace(MEDIAN_HAND)
    object.__setattr__(profile, "version", "bad version")
    object.__setattr__(profile, "hand_span_mm", True)
    object.__setattr__(profile, "reach_mm", float("nan"))
    object.__setattr__(profile, "v_shift_mm_per_s", 0.0)
    object.__setattr__(profile, "max_fret", 37)

    assert validate_profile(profile) == (
        ProfileValidationIssue(
            code="PROFILE_VERSION_FORMAT",
            path="$.version",
            message=(
                "version must be 1-128 ASCII identifier characters with non-empty "
                "slash-separated segments"
            ),
        ),
        ProfileValidationIssue(
            code="PROFILE_NUMERIC_TYPE",
            path="$.hand_span_mm",
            message="hand_span_mm must be stored as a normalized plain float",
        ),
        ProfileValidationIssue(
            code="PROFILE_NUMERIC_NOT_FINITE",
            path="$.reach_mm",
            message="reach_mm must be finite",
        ),
        ProfileValidationIssue(
            code="PROFILE_NUMERIC_NOT_POSITIVE",
            path="$.v_shift_mm_per_s",
            message="v_shift_mm_per_s must be greater than zero",
        ),
        ProfileValidationIssue(
            code="PROFILE_MAX_FRET_RANGE",
            path="$.max_fret",
            message="max_fret must be between 0 and 36 inclusive",
        ),
    )


def test_validate_profile_handles_deleted_field_without_raising() -> None:
    profile = replace(MEDIAN_HAND)
    object.__delattr__(profile, "reach_mm")

    assert validate_profile(profile) == (
        ProfileValidationIssue(
            code="PROFILE_FIELD_MISSING",
            path="$.reach_mm",
            message="reach_mm is missing",
        ),
    )


def test_canonicalization_revalidates_a_corrupted_profile() -> None:
    profile = replace(MEDIAN_HAND)
    object.__setattr__(profile, "r_max_hz", float("inf"))

    with pytest.raises(ValueError, match="PROFILE_NUMERIC_NOT_FINITE"):
        canonical_profile_bytes(profile)


def test_profile_identity_uses_detached_snapshot_after_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = replace(MEDIAN_HAND, version="identity-snapshot@0.1")
    expected = canonical_profile_bytes(source)
    real_validate = profiles_module.validate_profile

    def mutate_source_after_barrier(value: object) -> object:
        issues = real_validate(value)
        object.__setattr__(source, "version", "bad version with spaces")
        object.__setattr__(source, "hand_span_mm", float("nan"))
        return issues

    monkeypatch.setattr(profiles_module, "validate_profile", mutate_source_after_barrier)

    assert profiles_module.canonical_profile_bytes(source) == expected
    object.__setattr__(source, "version", "identity-snapshot@0.1")
    object.__setattr__(source, "hand_span_mm", MEDIAN_HAND.hand_span_mm)
    assert profiles_module.optimistic(source).version.startswith("identity-snapshot@0.1")


@pytest.mark.parametrize(
    ("field", "upper"),
    [
        ("hand_span_mm", MAX_HAND_SPAN_MM),
        ("reach_mm", MAX_REACH_MM),
        ("v_shift_mm_per_s", MAX_SHIFT_MM_PER_S),
        ("r_max_hz", MAX_RIGHT_HAND_RATE_HZ),
        ("string_length_mm", MAX_STRING_LENGTH_MM),
    ],
)
def test_profile_numeric_domain_has_a_finite_upper_bound(
    field: str, upper: float
) -> None:
    assert getattr(replace(MEDIAN_HAND, **{field: upper}), field) == upper
    with pytest.raises(ValueError, match="PROFILE_NUMERIC_ABOVE_MAX"):
        replace(MEDIAN_HAND, **{field: upper * 2.0})


def test_extreme_finite_scale_is_rejected_before_geometry_can_overflow() -> None:
    with pytest.raises(ValueError, match="string_length_mm"):
        replace(MEDIAN_HAND, string_length_mm=sys.float_info.max)


def test_profile_transforms_are_closed_at_version_and_numeric_boundaries() -> None:
    profile = Profile(
        "a" * 128,
        MAX_HAND_SPAN_MM,
        MAX_REACH_MM,
        MAX_SHIFT_MM_PER_S,
        MAX_RIGHT_HAND_RATE_HZ,
        MAX_STRING_LENGTH_MM,
        MAX_SUPPORTED_FRET,
    )
    for derived in (optimistic(profile), pessimistic(profile)):
        assert validate_profile(derived) == ()
        assert len(derived.version) <= 128
        assert all(
            value < float("inf")
            for value in (
                derived.hand_span_mm,
                derived.reach_mm,
                derived.v_shift_mm_per_s,
                derived.r_max_hz,
                derived.string_length_mm,
            )
        )


def test_revalidation_rejects_forged_float_subclass() -> None:
    class HostileFloat(float):
        def __mul__(self, _other: object) -> float:
            raise AssertionError("hostile arithmetic executed")

    profile = replace(MEDIAN_HAND)
    object.__setattr__(profile, "hand_span_mm", HostileFloat(100.0))
    issues = validate_profile(profile)
    assert issues[0].code == "PROFILE_NUMERIC_TYPE"
    assert issues[0].path == "$.hand_span_mm"
    with pytest.raises(ValueError, match="PROFILE_NUMERIC_TYPE"):
        profile_fingerprint(profile)


def test_revalidation_rejects_non_normalized_fraction_inserted_by_mutation() -> None:
    profile = replace(MEDIAN_HAND)
    object.__setattr__(profile, "hand_span_mm", Fraction(100))

    issues = validate_profile(profile)
    assert issues[0].code == "PROFILE_NUMERIC_TYPE"
    with pytest.raises(ValueError, match="PROFILE_NUMERIC_TYPE"):
        canonical_profile_bytes(profile)
