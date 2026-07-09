"""Oracle diagnostics: verdicts, violation types, and localized reports.

A :class:`Diagnostic` is the environment signal the repair agent reads to make
*targeted* edits (not blind search): it pins where and by how much a constraint
is violated, plus suggested relaxations.
"""

from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

Verdict = Literal["GREEN", "RED", "AMBER"]

ViolationType = Literal[
    "MALFORMED_FINGERING",
    "RANGE",
    "ONE_STRING_ONE_NOTE",
    "FINGER_COUNT",
    "FINGER_MONOTONIC",
    "FRET_SPAN",
    "BARRE_INFEASIBLE",
    "SHIFT_SPEED",
    "RIGHT_HAND",
    "SUSTAIN_CONFLICT",
]


@dataclass(frozen=True)
class Diagnostic:
    measure: int  # 1-indexed
    beat: Fraction  # 1-indexed within the bar
    violation_type: ViolationType
    offending_notes: tuple[int, ...]  # indices into Tab.notes
    overage: float  # how far past the limit (mm, mm/s, or count)
    suggested_relaxations: tuple[str, ...]
