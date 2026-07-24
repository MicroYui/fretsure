from __future__ import annotations

import io
from collections import defaultdict
from fractions import Fraction as F

import pdfplumber
import pytest
from pypdf import PdfReader

from fretsure.geometry import STANDARD_TUNING
from fretsure.render.pdf_tab import (
    PDF_TAB_EXPORT_VERSION,
    PdfTabExportCode,
    PdfTabExportError,
    render_tab_pdf,
)
from fretsure.tab import RightFinger, Tab, TabNote


def _note(
    onset: F,
    duration: F,
    string: int,
    fret: int,
    *,
    left: int | None = None,
    right: RightFinger = "i",
) -> TabNote:
    finger = (0 if fret == 0 else 1) if left is None else left
    return TabNote(onset, duration, string, fret, finger, right)


def _short_tab() -> Tab:
    return Tab(
        (
            _note(F(0), F(1), 0, 3, left=2, right="p"),
            _note(F(0), F(1), 1, 2, left=1, right="i"),
            _note(F(1), F(1, 2), 2, 0, right="m"),
            _note(F(3, 2), F(1, 2), 3, 2, left=1, right="a"),
            _note(F(2), F(2), 5, 3, left=3, right="m"),
        ),
        STANDARD_TUNING,
        2,
    )


def _long_tab(*, bars: int = 32) -> Tab:
    notes: list[TabNote] = []
    right_hands: tuple[RightFinger, ...] = ("p", "i", "m", "a")
    for bar in range(bars):
        start = F(bar * 4)
        fret = bar % 5
        notes.append(
            _note(
                start,
                F(1),
                bar % 6,
                fret,
                right=right_hands[bar % len(right_hands)],
            )
        )
        notes.append(
            _note(
                start + 2,
                F(1, 2),
                (bar + 2) % 6,
                (bar + 2) % 5,
                right=right_hands[(bar + 1) % len(right_hands)],
            )
        )
    return Tab(tuple(notes), STANDARD_TUNING, 0)


def _reader(data: bytes) -> PdfReader:
    return PdfReader(io.BytesIO(data))


def test_pdf_is_deterministic_a4_with_stable_metadata_and_score_text() -> None:
    first = render_tab_pdf(
        _short_tab(),
        title="Two Tigers - Guitar Arrangement",
        tempo_bpm=96.0,
    )
    second = render_tab_pdf(
        _short_tab(),
        title="Two Tigers - Guitar Arrangement",
        tempo_bpm=96.0,
    )

    assert first == second
    assert first.startswith(b"%PDF-1.4")
    reader = _reader(first)
    assert len(reader.pages) == 1
    assert reader.metadata is not None
    assert reader.metadata.title == "Two Tigers - Guitar Arrangement"
    assert reader.metadata.author == "Fretsure"
    assert reader.metadata.subject == "Canonical guitar tablature with exact verified fingering"
    assert reader.metadata.creator == f"Fretsure {PDF_TAB_EXPORT_VERSION}"
    page = reader.pages[0]
    assert float(page.mediabox.width) == pytest.approx(595.2756, abs=0.01)
    assert float(page.mediabox.height) == pytest.approx(841.8898, abs=0.01)
    text = page.extract_text()
    assert "Two Tigers - Guitar Arrangement" in text
    assert "Tempo: 96 BPM  |  Meter: 4/4  |  Capo: 2" in text
    assert "Tuning (string:pitch): 6:E2  5:A2  4:D3  3:G3  2:B3  1:E4" in text
    assert "BAR 1" in text
    assert "FINGERING AND RHYTHM LEGEND" in text
    assert "Page 1 of 1" in text


def test_pdf_paginates_and_each_page_has_vector_six_line_systems() -> None:
    data = render_tab_pdf(_long_tab(), title="Thirty-two Bar Geometry Test", tempo_bpm=120.0)
    reader = _reader(data)

    assert len(reader.pages) == 2
    first_text = reader.pages[0].extract_text()
    second_text = reader.pages[1].extract_text()
    assert "Page 1 of 2" in first_text
    assert "Page 2 of 2" in second_text
    assert "BAR 1" in first_text
    assert "BAR 32" in second_text
    assert "GUITAR TAB - CONTINUED" in second_text

    with pdfplumber.open(io.BytesIO(data)) as document:
        for page in document.pages:
            horizontal_length_by_y: dict[float, float] = defaultdict(float)
            vertical_barlines = 0
            for line in page.lines:
                x0 = float(line["x0"])
                x1 = float(line["x1"])
                y0 = float(line["y0"])
                y1 = float(line["y1"])
                if abs(y1 - y0) < 0.1:
                    horizontal_length_by_y[round(y0, 1)] += abs(x1 - x0)
                if abs(x1 - x0) < 0.1 and abs(y1 - y0) >= 50.0:
                    vertical_barlines += 1
            complete_string_rows = sum(
                length >= 0.95 * 482.0 for length in horizontal_length_by_y.values()
            )
            assert complete_string_rows >= 6
            assert vertical_barlines >= 2


def test_pdf_rejects_invalid_tab_and_title_with_typed_errors() -> None:
    with pytest.raises(PdfTabExportError) as invalid_tab:
        render_tab_pdf(Tab((), STANDARD_TUNING, 0))
    assert invalid_tab.value.code is PdfTabExportCode.INVALID_TAB

    with pytest.raises(PdfTabExportError) as invalid_title:
        render_tab_pdf(_short_tab(), title="   ")
    assert invalid_title.value.code is PdfTabExportCode.INVALID_TITLE
