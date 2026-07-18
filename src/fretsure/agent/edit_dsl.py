"""The edit DSL the repair loop applies to an arrangement.

Operators mutate the *target note set* (the LLM's musical intent); the solver then
re-derives a Tab. The melody is inviolable: any edit that would drop, shift, or
revoice a ``melody`` note raises :class:`MelodyProtected` for the loop to skip.
"""

from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Any, Literal

from fretsure.ir import MAX_IR_FRACTION_COMPONENT_BITS, Note

EditOp = Literal["drop_note", "octave_shift", "revoice", "drop_inner"]
_VALID_OPS: tuple[EditOp, ...] = ("drop_note", "octave_shift", "revoice", "drop_inner")


class MelodyProtected(Exception):
    """Raised when an edit targets a melody note (never allowed)."""


class InvalidEditTarget(ValueError):
    """Raised when an edit would leave the solver target outside its domain."""


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
    ordered = tuple(sorted(result, key=lambda x: (x.onset, x.pitch)))
    identities: set[tuple[Fraction, int]] = set()
    for note in ordered:
        if not 0 <= note.pitch <= 127:
            raise InvalidEditTarget("edit would move a note outside the MIDI domain")
        identity = (note.onset, note.pitch)
        if identity in identities:
            raise InvalidEditTarget("edit would create an ambiguous onset/pitch target")
        identities.add(identity)
    return ordered


def parse_edit(obj: dict[str, Any]) -> Edit:
    """Parse an LLM-emitted edit object. Raises ValueError on anything malformed."""
    op = obj.get("op")
    if op not in _VALID_OPS:
        raise ValueError(f"invalid edit op: {op!r}")
    pitch_value = obj.get("target_pitch")
    arg_value = obj.get("arg", 0)
    if type(pitch_value) is not int or type(arg_value) is not int:
        raise ValueError("edit pitch and arg values must be JSON integers")
    try:
        onset = Fraction(str(obj["target_onset"]))
        pitch = pitch_value
        arg = arg_value
    except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
        raise ValueError(f"malformed edit {obj!r}: {exc}") from exc
    if (
        onset < 0
        or onset.numerator.bit_length() > MAX_IR_FRACTION_COMPONENT_BITS
        or onset.denominator.bit_length() > MAX_IR_FRACTION_COMPONENT_BITS
        or not 0 <= pitch <= 127
        or (op in ("drop_note", "drop_inner") and arg != 0)
        or (op == "octave_shift" and arg not in (-12, 12))
        or (op == "revoice" and not 0 <= arg <= 127)
    ):
        raise ValueError("edit values are outside the target/edit domain")
    return Edit(op, onset, pitch, arg)
