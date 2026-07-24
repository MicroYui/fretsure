from fractions import Fraction as F

import pytest

from fretsure.geometry import STANDARD_TUNING
from fretsure.render.tab_text import (
    TAB_TEXT_EXPORT_VERSION,
    TabTextExportCode,
    TabTextExportError,
    render_tab_text,
)
from fretsure.tab import Tab, TabNote


def _tab() -> Tab:
    return Tab(
        (
            TabNote(F(1), F(1, 2), 5, 3, 3, "a"),
            TabNote(F(0), F(1), 0, 0, 0, "p"),
            TabNote(F(0), F(1), 1, 2, 2, "i"),
        ),
        STANDARD_TUNING,
        2,
    )


def test_tab_text_contains_six_lines_and_exact_fingering_rows() -> None:
    rendered = render_tab_text(_tab())

    assert f"Format: {TAB_TEXT_EXPORT_VERSION}" in rendered
    assert "direct export; not derived from MIDI" in rendered
    tab_lines = [
        line
        for line in rendered.splitlines()
        if line.startswith(("e|", "B|", "G|", "D|", "A|", "E|"))
    ]
    assert len(tab_lines) == 6
    assert tab_lines[0].startswith("e|")
    assert tab_lines[-1].startswith("E|")
    assert (
        "note_index\tonset\tduration\tguitar_string\tcanonical_string"
        "\tfret\tleft_finger\tright_finger"
    ) in rendered
    assert "0\t1/1\t1/2\t1\t5\t3\t3\ta" in rendered
    assert "1\t0/1\t1/1\t6\t0\t0\t0\tp" in rendered
    assert "2\t0/1\t1/1\t5\t1\t2\t2\ti" in rendered
    assert "Tuning MIDI (canonical strings 0..5): 40 45 50 55 59 64" in rendered
    assert "Capo fret: 2" in rendered


def test_tab_text_is_deterministic_and_rejects_invalid_tab() -> None:
    assert render_tab_text(_tab()) == render_tab_text(_tab())

    with pytest.raises(TabTextExportError) as caught:
        render_tab_text(Tab((), STANDARD_TUNING, 0))
    assert caught.value.code is TabTextExportCode.INVALID_TAB
