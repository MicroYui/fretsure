import math

from fretsure.geometry import (
    STANDARD_TUNING,
    d_max,
    euclid,
    fingertip_xy,
    fret_x,
    note_pitch,
    open_pitch,
    press_x,
    string_y,
)


def test_fret_x_octave_is_half_scale() -> None:
    assert math.isclose(fret_x(12, 648.0), 324.0, rel_tol=1e-9)  # 12th fret = L/2
    assert fret_x(0, 648.0) == 0.0


def test_fret_x_monotonic_increasing() -> None:
    xs = [fret_x(f) for f in range(0, 13)]
    assert all(b > a for a, b in zip(xs, xs[1:], strict=False))


def test_fret_spacing_compresses_up_the_neck() -> None:
    # press-position gaps shrink at higher positions (mm geometry, not fret count)
    low_gap = press_x(2) - press_x(1)  # type: ignore[operator]
    high_gap = press_x(12) - press_x(11)  # type: ignore[operator]
    assert low_gap > high_gap


def test_press_x_open_is_none() -> None:
    assert press_x(0) is None
    assert press_x(1) is not None


def test_fingertip_open_none_fretted_xy() -> None:
    assert fingertip_xy(0, 0) is None
    assert fingertip_xy(3, 5) is not None


def test_d_max_monotonic_in_span_and_gap() -> None:
    assert d_max(1, 4, 100.0) > d_max(1, 2, 100.0)  # bigger gap -> bigger bound
    assert d_max(1, 4, 120.0) > d_max(1, 4, 100.0)  # bigger hand -> bigger bound
    assert math.isclose(d_max(1, 4, 120.0), 120.0)  # finger 1..4 spans full hand
    assert d_max(2, 2, 100.0) == 0.0  # same finger, no distance


def test_open_pitch_and_note_pitch() -> None:
    assert open_pitch(0, STANDARD_TUNING, 0) == 40
    assert open_pitch(0, STANDARD_TUNING, 2) == 42  # capo 2
    assert note_pitch(0, 3, STANDARD_TUNING, 0) == 43


def test_euclid() -> None:
    assert math.isclose(euclid((0.0, 0.0), (3.0, 4.0)), 5.0)


def test_string_y_spacing() -> None:
    assert string_y(0) == 0.0
    assert string_y(2) == 2 * string_y(1)
