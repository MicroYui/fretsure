"""Deterministic, printable PDF rendering for canonical guitar Tabs."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from importlib import import_module
from typing import Any

from fretsure.oracle.input import OracleInputError, ensure_oracle_input
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.render.contracts import PDF_TAB_EXPORT_VERSION
from fretsure.tab import Tab, TabNote

_PAGE_WIDTH = 595.2755905511812  # ISO A4, points.
_PAGE_HEIGHT = 841.8897637795277
_LEFT_MARGIN = 48.0
_RIGHT_MARGIN = 48.0
_SYSTEM_LEFT = 65.0
_SYSTEM_RIGHT = _PAGE_WIDTH - _RIGHT_MARGIN
_SYSTEM_WIDTH = _SYSTEM_RIGHT - _SYSTEM_LEFT
_SYSTEM_HEIGHT = 108.0
_FIRST_SYSTEM_TOP = _PAGE_HEIGHT - 137.0
_LATER_SYSTEM_TOP = _PAGE_HEIGHT - 74.0
_MIN_SYSTEM_TOP = 180.0
_STRING_SPACING = 10.5
_MIN_MEASURE_WIDTH = 103.0
_MAX_MEASURES_PER_SYSTEM = 4
_MIN_ATTACK_GAP = 13.0
_MAX_PDF_BARS = 4_096

_PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


class PdfTabExportCode(StrEnum):
    """Stable reasons why a canonical Tab cannot be rendered as PDF."""

    INVALID_TAB = "INVALID_TAB"
    INVALID_TITLE = "INVALID_TITLE"
    LAYOUT_UNREPRESENTABLE = "LAYOUT_UNREPRESENTABLE"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"


class PdfTabExportError(ValueError):
    """A safe, typed rejection from deterministic PDF-tab export."""

    def __init__(self, code: PdfTabExportCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code.value}: {message}")


@dataclass(frozen=True, slots=True)
class _Measure:
    index: int
    notes: tuple[TabNote, ...]
    onsets: tuple[Fraction, ...]
    required_width: float


@dataclass(frozen=True, slots=True)
class _System:
    measures: tuple[_Measure, ...]
    widths: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class _PdfRuntime:
    canvas: Any
    colors: Any


def _load_pdf_runtime() -> _PdfRuntime:
    try:
        canvas_module = import_module("reportlab.pdfgen.canvas")
        colors = import_module("reportlab.lib.colors")
    except ModuleNotFoundError:
        raise PdfTabExportError(
            PdfTabExportCode.DEPENDENCY_UNAVAILABLE,
            "PDF export requires the optional reportlab package",
        ) from None
    return _PdfRuntime(canvas=canvas_module, colors=colors)


def _ceil_fraction(value: Fraction) -> int:
    return -(-value.numerator // value.denominator)


def _pitch_name(pitch: int) -> str:
    return f"{_PITCH_NAMES[pitch % 12]}{pitch // 12 - 1}"


def _fraction_token(value: Fraction) -> str:
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def _rhythm_token(duration: Fraction) -> str:
    names = {
        Fraction(4): "w",
        Fraction(3): "h.",
        Fraction(2): "h",
        Fraction(3, 2): "q.",
        Fraction(1): "q",
        Fraction(3, 4): "e.",
        Fraction(1, 2): "e",
        Fraction(3, 8): "s.",
        Fraction(1, 4): "s",
    }
    return names.get(duration, _fraction_token(duration))


def _measure_width(onsets: tuple[Fraction, ...]) -> float:
    required = 20.0 + max(0, len(onsets) - 1) * _MIN_ATTACK_GAP
    return max(_MIN_MEASURE_WIDTH, required)


def _make_measures(tab: Tab, beats_per_bar: int) -> tuple[_Measure, ...]:
    bar_length = Fraction(beats_per_bar)
    end = max(note.onset + note.duration for note in tab.notes)
    bar_count = max(1, _ceil_fraction(end / bar_length))
    if bar_count > _MAX_PDF_BARS:
        raise PdfTabExportError(
            PdfTabExportCode.LAYOUT_UNREPRESENTABLE,
            f"the score spans {bar_count} bars; PDF export supports at most {_MAX_PDF_BARS}",
        )

    notes_by_bar: list[list[TabNote]] = [[] for _ in range(bar_count)]
    for note in tab.notes:
        bar_index = int(note.onset // bar_length)
        if bar_index >= bar_count:
            bar_index = bar_count - 1
        notes_by_bar[bar_index].append(note)

    measures: list[_Measure] = []
    for index, notes in enumerate(notes_by_bar):
        ordered = tuple(
            sorted(
                notes,
                key=lambda note: (note.onset, -note.string, note.fret, note.left_finger),
            )
        )
        onsets = tuple(sorted({note.onset for note in ordered}))
        required_width = _measure_width(onsets)
        if required_width > _SYSTEM_WIDTH:
            raise PdfTabExportError(
                PdfTabExportCode.LAYOUT_UNREPRESENTABLE,
                f"bar {index + 1} is too dense for a legible printable system",
            )
        measures.append(_Measure(index, ordered, onsets, required_width))
    return tuple(measures)


def _make_systems(measures: tuple[_Measure, ...]) -> tuple[_System, ...]:
    systems: list[_System] = []
    pending: list[_Measure] = []
    pending_width = 0.0

    def finish() -> None:
        nonlocal pending, pending_width
        if not pending:
            return
        extra = (_SYSTEM_WIDTH - pending_width) / len(pending)
        widths = tuple(measure.required_width + extra for measure in pending)
        systems.append(_System(tuple(pending), widths))
        pending = []
        pending_width = 0.0

    for measure in measures:
        would_overflow = pending and pending_width + measure.required_width > _SYSTEM_WIDTH
        if would_overflow or len(pending) == _MAX_MEASURES_PER_SYSTEM:
            finish()
        pending.append(measure)
        pending_width += measure.required_width
    finish()
    return tuple(systems)


def _page_chunks(systems: tuple[_System, ...]) -> tuple[tuple[_System, ...], ...]:
    first_capacity = int((_FIRST_SYSTEM_TOP - _MIN_SYSTEM_TOP) // _SYSTEM_HEIGHT) + 1
    later_capacity = int((_LATER_SYSTEM_TOP - _MIN_SYSTEM_TOP) // _SYSTEM_HEIGHT) + 1
    pages: list[tuple[_System, ...]] = []
    offset = 0
    capacity = first_capacity
    while offset < len(systems):
        pages.append(systems[offset : offset + capacity])
        offset += capacity
        capacity = later_capacity
    return tuple(pages)


def _attack_positions(
    measure: _Measure,
    *,
    x: float,
    width: float,
    beats_per_bar: int,
) -> dict[Fraction, float]:
    if not measure.onsets:
        return {}
    bar_start = Fraction(measure.index * beats_per_bar)
    left = x + 10.0
    right = x + width - 10.0
    span = right - left
    raw = [
        left + float((onset - bar_start) / beats_per_bar) * span
        for onset in measure.onsets
    ]
    positions = raw[:]
    for index in range(1, len(positions)):
        positions[index] = max(positions[index], positions[index - 1] + _MIN_ATTACK_GAP)
    if positions[-1] > right:
        positions[-1] = right
        for index in range(len(positions) - 2, -1, -1):
            positions[index] = min(positions[index], positions[index + 1] - _MIN_ATTACK_GAP)
    if positions[0] < left - 0.01:
        raise PdfTabExportError(
            PdfTabExportCode.LAYOUT_UNREPRESENTABLE,
            f"bar {measure.index + 1} cannot preserve legible note spacing",
        )
    return dict(zip(measure.onsets, positions, strict=True))


def _set_font(canvas: Any, name: str, size: float, color: Any) -> None:
    canvas.setFont(name, size)
    canvas.setFillColor(color)


def _draw_first_header(
    canvas: Any,
    colors: Any,
    *,
    title: str,
    tempo_bpm: float,
    beats_per_bar: int,
    tab: Tab,
) -> None:
    ink = colors.HexColor("#172033")
    muted = colors.HexColor("#596579")
    accent = colors.HexColor("#177C73")

    _set_font(canvas, "Helvetica-Bold", 20.0, ink)
    canvas.drawString(_LEFT_MARGIN, _PAGE_HEIGHT - 52.0, title)
    _set_font(canvas, "Helvetica", 8.5, accent)
    canvas.drawRightString(
        _PAGE_WIDTH - _RIGHT_MARGIN,
        _PAGE_HEIGHT - 50.0,
        "CANONICAL GUITAR TAB",
    )
    _set_font(canvas, "Helvetica", 9.0, muted)
    canvas.drawString(
        _LEFT_MARGIN,
        _PAGE_HEIGHT - 74.0,
        f"Tempo: {tempo_bpm:g} BPM  |  Meter: {beats_per_bar}/4  |  Capo: {tab.capo}",
    )
    tuning = "  ".join(
        f"{6 - string}:{_pitch_name(tab.tuning[string])}" for string in range(6)
    )
    canvas.drawString(_LEFT_MARGIN, _PAGE_HEIGHT - 92.0, f"Tuning (string:pitch): {tuning}")
    _set_font(canvas, "Helvetica", 7.5, muted)
    canvas.drawString(
        _LEFT_MARGIN,
        _PAGE_HEIGHT - 108.0,
        "Directly rendered from canonical Tab fingering; no MIDI reconstruction.",
    )
    canvas.setStrokeColor(colors.HexColor("#B9C2CE"))
    canvas.setLineWidth(0.7)
    canvas.line(
        _LEFT_MARGIN,
        _PAGE_HEIGHT - 119.0,
        _PAGE_WIDTH - _RIGHT_MARGIN,
        _PAGE_HEIGHT - 119.0,
    )


def _draw_later_header(canvas: Any, colors: Any, *, title: str) -> None:
    ink = colors.HexColor("#172033")
    muted = colors.HexColor("#596579")
    _set_font(canvas, "Helvetica-Bold", 10.0, ink)
    canvas.drawString(_LEFT_MARGIN, _PAGE_HEIGHT - 42.0, title)
    _set_font(canvas, "Helvetica", 7.5, muted)
    canvas.drawRightString(
        _PAGE_WIDTH - _RIGHT_MARGIN,
        _PAGE_HEIGHT - 42.0,
        "GUITAR TAB - CONTINUED",
    )
    canvas.setStrokeColor(colors.HexColor("#CDD3DC"))
    canvas.setLineWidth(0.6)
    canvas.line(
        _LEFT_MARGIN,
        _PAGE_HEIGHT - 54.0,
        _PAGE_WIDTH - _RIGHT_MARGIN,
        _PAGE_HEIGHT - 54.0,
    )


def _draw_legend(canvas: Any, colors: Any) -> None:
    x = _LEFT_MARGIN
    y = 38.0
    width = _PAGE_WIDTH - _LEFT_MARGIN - _RIGHT_MARGIN
    canvas.setFillColor(colors.HexColor("#F3F6F8"))
    canvas.setStrokeColor(colors.HexColor("#D3DAE2"))
    canvas.setLineWidth(0.6)
    canvas.roundRect(x, y, width, 38.0, 4.0, fill=1, stroke=1)
    _set_font(canvas, "Helvetica-Bold", 7.2, colors.HexColor("#344054"))
    canvas.drawString(x + 9.0, y + 25.0, "FINGERING AND RHYTHM LEGEND")
    _set_font(canvas, "Helvetica", 6.8, colors.HexColor("#596579"))
    canvas.drawString(
        x + 9.0,
        y + 14.0,
        (
            "Fret = large center; LH finger = small upper-right "
            "(0 open, 1 index, 2 middle, 3 ring, 4 little)."
        ),
    )
    canvas.drawString(
        x + 9.0,
        y + 5.0,
        (
            "RH finger = small lower-left (p thumb, i index, m middle, a ring). "
            "Rhythm: w h q e s = whole to sixteenth."
        ),
    )


def _draw_footer(canvas: Any, colors: Any, *, page: int, page_count: int) -> None:
    _set_font(canvas, "Helvetica", 6.8, colors.HexColor("#7A8596"))
    canvas.drawString(_LEFT_MARGIN, 20.0, f"Generated by Fretsure | {PDF_TAB_EXPORT_VERSION}")
    canvas.drawRightString(
        _PAGE_WIDTH - _RIGHT_MARGIN,
        20.0,
        f"Page {page} of {page_count}",
    )


def _draw_note_token(
    canvas: Any,
    colors: Any,
    *,
    x: float,
    y: float,
    notes: tuple[TabNote, ...],
) -> None:
    fret = "/".join(str(note.fret) for note in notes)
    left = "/".join(str(note.left_finger) for note in notes)
    right = "/".join(note.right_finger for note in notes)
    width = canvas.stringWidth(fret, "Helvetica-Bold", 8.0) + 4.0
    canvas.setFillColor(colors.white)
    canvas.rect(x - width / 2, y - 4.2, width, 8.4, fill=1, stroke=0)
    _set_font(canvas, "Helvetica-Bold", 8.0, colors.HexColor("#111827"))
    canvas.drawCentredString(x, y - 2.8, fret)
    _set_font(canvas, "Helvetica-Bold", 5.2, colors.HexColor("#177C73"))
    canvas.drawString(x + width / 2 - 0.4, y + 3.3, left)
    _set_font(canvas, "Helvetica-Oblique", 5.2, colors.HexColor("#A24A2A"))
    canvas.drawRightString(x - width / 2 + 0.4, y - 7.7, right)


def _draw_measure(
    canvas: Any,
    colors: Any,
    measure: _Measure,
    *,
    x: float,
    width: float,
    top_string_y: float,
    beats_per_bar: int,
    tuning: tuple[int, ...],
    is_last: bool,
    show_string_labels: bool,
) -> None:
    ink = colors.HexColor("#263244")
    muted = colors.HexColor("#697586")
    grid = colors.HexColor("#D7DDE5")
    accent = colors.HexColor("#177C73")
    bottom_string_y = top_string_y - 5 * _STRING_SPACING

    if measure.index % 2 == 1:
        canvas.setFillColor(colors.HexColor("#FAFBFC"))
        canvas.rect(x, bottom_string_y - 4.0, width, 5 * _STRING_SPACING + 8.0, fill=1, stroke=0)

    _set_font(canvas, "Helvetica-Bold", 6.7, accent)
    canvas.drawString(x + 4.0, top_string_y + 32.0, f"BAR {measure.index + 1}")

    canvas.setStrokeColor(grid)
    canvas.setLineWidth(0.35)
    canvas.setDash(1.0, 2.0)
    for beat in range(beats_per_bar):
        beat_x = x + width * beat / beats_per_bar
        canvas.line(beat_x, top_string_y + 16.0, beat_x, bottom_string_y)
        _set_font(canvas, "Helvetica", 5.6, muted)
        canvas.drawCentredString(beat_x + 4.0, top_string_y + 20.0, str(beat + 1))
    canvas.setDash()

    canvas.setStrokeColor(ink)
    canvas.setLineWidth(0.55)
    for display_index, string in enumerate(range(5, -1, -1)):
        string_y = top_string_y - display_index * _STRING_SPACING
        canvas.line(x, string_y, x + width, string_y)
        if show_string_labels:
            _set_font(canvas, "Helvetica", 6.3, muted)
            canvas.drawRightString(
                x - 5.0,
                string_y - 2.0,
                _pitch_name(tuning[string]),
            )

    canvas.setStrokeColor(ink)
    canvas.setLineWidth(1.0)
    canvas.line(x, top_string_y, x, bottom_string_y)
    canvas.setLineWidth(1.6 if is_last else 0.8)
    canvas.line(x + width, top_string_y, x + width, bottom_string_y)

    positions = _attack_positions(
        measure,
        x=x,
        width=width,
        beats_per_bar=beats_per_bar,
    )
    for onset in measure.onsets:
        attack_notes = tuple(note for note in measure.notes if note.onset == onset)
        durations = tuple(sorted({note.duration for note in attack_notes}, reverse=True))
        rhythm = "/".join(_rhythm_token(duration) for duration in durations)
        attack_x = positions[onset]
        _set_font(canvas, "Helvetica", 5.8, muted)
        canvas.drawCentredString(attack_x, top_string_y + 8.2, rhythm)
        for string in range(5, -1, -1):
            string_notes = tuple(note for note in attack_notes if note.string == string)
            if not string_notes:
                continue
            string_y = top_string_y - (5 - string) * _STRING_SPACING
            _draw_note_token(canvas, colors, x=attack_x, y=string_y, notes=string_notes)


def _draw_system(
    canvas: Any,
    colors: Any,
    system: _System,
    *,
    system_top: float,
    beats_per_bar: int,
    tuning: tuple[int, ...],
) -> None:
    top_string_y = system_top - 32.0
    x = _SYSTEM_LEFT
    for index, (measure, width) in enumerate(
        zip(system.measures, system.widths, strict=True)
    ):
        _draw_measure(
            canvas,
            colors,
            measure,
            x=x,
            width=width,
            top_string_y=top_string_y,
            beats_per_bar=beats_per_bar,
            tuning=tuning,
            is_last=index == len(system.measures) - 1,
            show_string_labels=index == 0,
        )
        x += width


def render_tab_pdf(
    tab: Tab,
    *,
    title: str = "Fretsure Guitar Tablature",
    tempo_bpm: float = 90.0,
    beats_per_bar: int = 4,
) -> bytes:
    """Render a canonical, fully fingered Tab as a deterministic A4 PDF.

    The score uses independent vector geometry rather than embedding the ASCII
    renderer.  Each attack carries its exact fret, left-hand finger, right-hand
    finger and rhythm token; beat guides and bar boundaries make the horizontal
    timeline readable, while systems and pages are laid out deterministically.
    """

    if type(title) is not str or not title.strip() or len(title) > 120:
        raise PdfTabExportError(
            PdfTabExportCode.INVALID_TITLE,
            "title must be a non-empty string of at most 120 characters",
        )
    clean_title = " ".join(title.strip().split())
    try:
        canonical, _, normalized_tempo, normalized_beats = ensure_oracle_input(
            tab,
            MEDIAN_HAND,
            tempo_bpm=tempo_bpm,
            beats_per_bar=beats_per_bar,
        )
    except OracleInputError:
        raise PdfTabExportError(
            PdfTabExportCode.INVALID_TAB,
            "Tab, tempo or meter is outside the canonical PDF export input domain",
        ) from None
    if not math.isfinite(normalized_tempo):
        raise PdfTabExportError(
            PdfTabExportCode.INVALID_TAB,
            "tempo must be finite",
        )

    measures = _make_measures(canonical, normalized_beats)
    systems = _make_systems(measures)
    pages = _page_chunks(systems)
    runtime = _load_pdf_runtime()
    buffer = io.BytesIO()
    canvas = runtime.canvas.Canvas(
        buffer,
        pagesize=(_PAGE_WIDTH, _PAGE_HEIGHT),
        pageCompression=1,
        invariant=1,
        pdfVersion=(1, 4),
    )
    canvas.setTitle(clean_title)
    canvas.setAuthor("Fretsure")
    canvas.setSubject("Canonical guitar tablature with exact verified fingering")
    canvas.setCreator(f"Fretsure {PDF_TAB_EXPORT_VERSION}")

    for page_index, page_systems in enumerate(pages):
        if page_index == 0:
            _draw_first_header(
                canvas,
                runtime.colors,
                title=clean_title,
                tempo_bpm=normalized_tempo,
                beats_per_bar=normalized_beats,
                tab=canonical,
            )
            system_top = _FIRST_SYSTEM_TOP
        else:
            _draw_later_header(canvas, runtime.colors, title=clean_title)
            system_top = _LATER_SYSTEM_TOP
        for system in page_systems:
            _draw_system(
                canvas,
                runtime.colors,
                system,
                system_top=system_top,
                beats_per_bar=normalized_beats,
                tuning=canonical.tuning,
            )
            system_top -= _SYSTEM_HEIGHT
        _draw_legend(canvas, runtime.colors)
        _draw_footer(
            canvas,
            runtime.colors,
            page=page_index + 1,
            page_count=len(pages),
        )
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


__all__ = [
    "PDF_TAB_EXPORT_VERSION",
    "PdfTabExportCode",
    "PdfTabExportError",
    "render_tab_pdf",
]
