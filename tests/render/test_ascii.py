from fractions import Fraction as F

from fretsure.geometry import STANDARD_TUNING
from fretsure.render.ascii import render_ascii
from fretsure.tab import Tab, TabNote


def test_render_ascii_six_lines_high_e_on_top() -> None:
    t = Tab(
        (TabNote(F(0), F(1), 0, 3, 3, "p"), TabNote(F(0), F(1), 5, 0, 0, "a")),
        STANDARD_TUNING,
        0,
    )
    lines = render_ascii(t).split("\n")
    assert len(lines) == 6
    assert lines[0].startswith("e")  # high e on top
    assert lines[5].startswith("E")  # low E on bottom
    assert "3" in lines[5]  # low-E string fret 3
    assert "0" in lines[0]  # high-e string open


def test_render_ascii_columns_track_onsets() -> None:
    t = Tab(
        (TabNote(F(0), F(1), 0, 3, 1, "p"), TabNote(F(1), F(1), 0, 5, 1, "p")),
        STANDARD_TUNING,
        0,
    )
    low = render_ascii(t).split("\n")[5]
    assert "3" in low and "5" in low


def test_render_ascii_two_digit_frets_align() -> None:
    t = Tab((TabNote(F(0), F(1), 0, 12, 1, "p"),), STANDARD_TUNING, 0)
    assert "12" in render_ascii(t).split("\n")[5]


def test_render_ascii_deterministic() -> None:
    t = Tab((TabNote(F(0), F(1), 0, 3, 1, "p"),), STANDARD_TUNING, 0)
    assert render_ascii(t) == render_ascii(t)
