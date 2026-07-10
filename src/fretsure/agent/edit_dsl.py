"""The edit DSL the repair loop applies to an arrangement.

Operators mutate the *target note set* (the LLM's musical intent); the solver then
re-derives a Tab. The melody is inviolable: any edit that would drop, shift, or
revoice a ``melody`` note raises :class:`MelodyProtected` for the loop to skip.
"""

from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Any, Literal

from fretsure.ir import Note

EditOp = Literal["drop_note", "octave_shift", "revoice", "drop_inner"]
_VALID_OPS: tuple[EditOp, ...] = ("drop_note", "octave_shift", "revoice", "drop_inner")


class MelodyProtected(Exception):
    """Raised when an edit targets a melody note (never allowed)."""


@dataclass(frozen=True)
class Edit:
    op: EditOp
    target_onset: Fraction
    target_pitch: int
    arg: int = 0  # octave_shift: +/-12 semitones; revoice: the new pitch


def apply_edit(notes: tuple[Note, ...], edit: Edit) -> tuple[Note, ...]:
    """Return a new sorted target with ``edit`` applied to the first matching note.

    A non-matching edit is a no-op. Editing a melody note raises MelodyProtected.
    """
    result: list[Note] = []
    applied = False
    for n in notes:
        if not applied and n.onset == edit.target_onset and n.pitch == edit.target_pitch:
            applied = True
            if n.voice == "melody":
                raise MelodyProtected(f"cannot {edit.op} the melody note {n.pitch}@{n.onset}")
            if edit.op in ("drop_note", "drop_inner"):
                continue  # remove
            if edit.op == "octave_shift":
                result.append(replace(n, pitch=n.pitch + edit.arg))
            elif edit.op == "revoice":
                result.append(replace(n, pitch=edit.arg))
        else:
            result.append(n)
    return tuple(sorted(result, key=lambda x: (x.onset, x.pitch)))


def parse_edit(obj: dict[str, Any]) -> Edit:
    """Parse an LLM-emitted edit object. Raises ValueError on anything malformed."""
    op = obj.get("op")
    if op not in _VALID_OPS:
        raise ValueError(f"invalid edit op: {op!r}")
    try:
        onset = Fraction(str(obj["target_onset"]))
        pitch = int(obj["target_pitch"])
        arg = int(obj.get("arg", 0))
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f"malformed edit {obj!r}: {exc}") from exc
    return Edit(op, onset, pitch, arg)
