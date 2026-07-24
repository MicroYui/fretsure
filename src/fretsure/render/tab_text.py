"""Deterministic plain-text export for canonical guitar Tabs."""

from __future__ import annotations

from enum import StrEnum
from fractions import Fraction

from fretsure.oracle.input import OracleInputError, ensure_oracle_input
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.render.ascii import render_ascii
from fretsure.tab import Tab

TAB_TEXT_EXPORT_VERSION = "tab-text@0.1.0"


class TabTextExportCode(StrEnum):
    """Stable reasons why a Tab cannot be exported as canonical text."""

    INVALID_TAB = "INVALID_TAB"


class TabTextExportError(ValueError):
    """A safe, typed rejection from deterministic Tab-text export."""

    def __init__(self, code: TabTextExportCode, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code.value}: {message}")


def _fraction_token(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def render_tab_text(tab: Tab) -> str:
    """Export a six-line guitar tab and its exact canonical fingering rows.

    The six-line view is rendered directly from ``Tab``.  The accompanying
    table preserves the canonical note order and records exact rational timing,
    guitar/canonical string numbers, fret, and both hand assignments.  No field
    is reconstructed from MIDI.
    """

    try:
        canonical, _, _, _ = ensure_oracle_input(
            tab,
            MEDIAN_HAND,
            tempo_bpm=90.0,
            beats_per_bar=4,
        )
    except OracleInputError:
        raise TabTextExportError(
            TabTextExportCode.INVALID_TAB,
            "Tab is outside the canonical guitar export input domain",
        ) from None

    lines = [
        "Fretsure guitar tablature export",
        f"Format: {TAB_TEXT_EXPORT_VERSION}",
        "Source: canonical Tab JSON (direct export; not derived from MIDI)",
        "Timing unit: quarter-note beats; fractions are exact",
        "String mapping: canonical 0..5 (low to high) = guitar strings 6..1",
        "Tuning MIDI (canonical strings 0..5): "
        + " ".join(str(pitch) for pitch in canonical.tuning),
        f"Capo fret: {canonical.capo}",
        "",
        "Six-line tablature (high e to low E):",
        render_ascii(canonical),
        "",
        "Canonical fingering table (original Tab note order):",
        (
            "note_index\tonset\tduration\tguitar_string\tcanonical_string"
            "\tfret\tleft_finger\tright_finger"
        ),
    ]
    lines.extend(
        "\t".join(
            (
                str(index),
                _fraction_token(note.onset),
                _fraction_token(note.duration),
                str(6 - note.string),
                str(note.string),
                str(note.fret),
                str(note.left_finger),
                note.right_finger,
            )
        )
        for index, note in enumerate(canonical.notes)
    )
    lines.extend(
        (
            "",
            "Left finger: 0=open, 1=index, 2=middle, 3=ring, 4=little",
            "Right finger: p=thumb, i=index, m=middle, a=ring",
        )
    )
    return "\n".join(lines) + "\n"


__all__ = [
    "TAB_TEXT_EXPORT_VERSION",
    "TabTextExportCode",
    "TabTextExportError",
    "render_tab_text",
]
