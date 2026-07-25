"""Versioned solo-guitar arrangement style controls.

Styles describe musical intent only.  They never choose strings, frets, or
fingers; the deterministic solver remains authoritative for those decisions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

ARRANGEMENT_STYLE_REGISTRY_VERSION = "arrangement-style-registry@0.2.0"
DEFAULT_ARRANGEMENT_STYLE = "fingerstyle"


@dataclass(frozen=True, slots=True)
class ArrangementStyle:
    id: str
    label: str
    description: str
    prompt_guidance: str


_STYLES: tuple[ArrangementStyle, ...] = (
    ArrangementStyle(
        "fingerstyle",
        "Fingerstyle",
        "Balanced melody, sustained bass roots, and sparse inner motion.",
        "Use a balanced solo-fingerstyle texture: clear melody, stable chord-root bass, "
        "and occasional inner motion. Leave space rather than filling every subdivision.",
    ),
    ArrangementStyle(
        "classical",
        "Classical",
        "Independent voices, smooth voice leading, and measured arpeggiation.",
        "Use classical-guitar voice leading: sustain structural bass notes, connect inner "
        "voices by small intervals, and prefer even arpeggiation over syncopated stabs.",
    ),
    ArrangementStyle(
        "jazz",
        "Jazz",
        "Corpus-calibrated offbeat answers, shell colors, and economical voicings.",
        "Use an economical jazz texture informed by the licensed GuitarSet Jazz profile: "
        "keep the root foundation, emphasize available thirds and sevenths, and use sparse "
        "offbeat chord answers.",
    ),
    ArrangementStyle(
        "rnb",
        "R&B",
        "A provisional Funk-informed pocket with short colors and deliberate space.",
        "Use a provisional R&B pocket informed by the licensed GuitarSet Funk profile: "
        "grounded bass, short syncopated chord-color answers, and deliberate rests. This is "
        "adjacent evidence, not a claim of direct R&B score supervision.",
    ),
)

ARRANGEMENT_STYLES: Mapping[str, ArrangementStyle] = MappingProxyType(
    {style.id: style for style in _STYLES}
)
ARRANGEMENT_STYLE_NAMES = tuple(ARRANGEMENT_STYLES)


def arrangement_style(name: object) -> ArrangementStyle:
    """Return one public style or reject an unknown control value."""

    if type(name) is not str or name not in ARRANGEMENT_STYLES:
        raise ValueError(
            "style must be one of " + ", ".join(ARRANGEMENT_STYLE_NAMES)
        )
    return ARRANGEMENT_STYLES[name]


__all__ = [
    "ARRANGEMENT_STYLE_NAMES",
    "ARRANGEMENT_STYLE_REGISTRY_VERSION",
    "ARRANGEMENT_STYLES",
    "DEFAULT_ARRANGEMENT_STYLE",
    "ArrangementStyle",
    "arrangement_style",
]
