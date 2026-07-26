"""How long a fretted note is actually held, as distinct from how long it is written.

A fretted note only sounds while its finger stays down, so the oracle treats a
notated duration as a physical hold.  That is right for the verifier and wrong
for the source: engravers write voice-leading, and a player lifts when the hand
must move.  This module owns the difference.

Nothing here relaxes the oracle.  It bounds what the *solver* may choose to
exhibit, and the oracle then judges the tab it actually produced.  The freedom is
derived from the target, never declared by a caller -- an input that could assert
its own minimum hold would be able to buy playability for free.
"""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction
from typing import Final

from fretsure.ir import Note

SUSTAIN_MODEL_VERSION: Final = "sustain-model@0.1.0"


def repair_repeated_pitch_holds(notes: Sequence[Note]) -> tuple[Note, ...]:
    """End a note when the same pitch is attacked again.

    Re-plucking a pitch necessarily stops the earlier instance of it, so a
    target that asks for both at once is asking for something no instrument
    does.  Clipping is safe for faithfulness by construction: the pitch is still
    sounding at the moment the overlap would have begun, because that moment is
    exactly when it was attacked again.
    """

    onsets_by_pitch: dict[int, list[Fraction]] = {}
    for note in notes:
        onsets_by_pitch.setdefault(note.pitch, []).append(note.onset)
    for onsets in onsets_by_pitch.values():
        onsets.sort()
    repaired: list[Note] = []
    for note in notes:
        end = note.onset + note.duration
        later = [
            onset for onset in onsets_by_pitch[note.pitch] if note.onset < onset < end
        ]
        if not later:
            repaired.append(note)
            continue
        repaired.append(
            Note(
                onset=note.onset,
                duration=min(later) - note.onset,
                pitch=note.pitch,
                voice=note.voice,
            )
        )
    return tuple(repaired)


__all__ = ["SUSTAIN_MODEL_VERSION", "repair_repeated_pitch_holds"]
