from fretsure.oracle.profiles import (
    LARGE_HAND,
    MEDIAN_HAND,
    SMALL_HAND,
    Profile,
    optimistic,
    pessimistic,
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
    import dataclasses

    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        MEDIAN_HAND.hand_span_mm = 1.0  # type: ignore[misc]
