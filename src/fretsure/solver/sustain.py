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
from dataclasses import dataclass
from fractions import Fraction
from typing import Final

from fretsure.ir import Note

SUSTAIN_MODEL_VERSION: Final = "sustain-model@0.2.0"

# A bass note may be let go halfway through what the engraver wrote, never
# sooner.  The guard is faithfulness, not physics: bass_root_accuracy asks
# whether a chord's root is still sounding at the chord's onset, and normal
# engraving puts that well inside the first half.
_BASS_MINIMUM_HOLD_FRACTION: Final = Fraction(1, 2)

# A score is not "playable" if the way it became playable was to stop holding
# it.  Releasing every eligible voice outright measures 0.729 on the published
# corpus, so this floor has a calibrated failure point rather than being a
# round number nothing ever reaches.
SUSTAIN_RETENTION_FLOOR: Final = Fraction(9, 10)


@dataclass(frozen=True, slots=True)
class HoldBounds:
    """How long a note must be held, and how long it was written for."""

    minimum: Fraction
    maximum: Fraction


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


def hold_bounds(notes: Sequence[Note]) -> tuple[HoldBounds, ...]:
    """Derive per-note hold bounds from the target alone.

    The freedom is derived here and never declared by a caller: a target that
    could assert its own minimum hold would be able to buy playability for free.

    Three rules, all deterministic:

    * every note sounds at least through the next attack, so the geometry of
      adjacent frames stays fully constrained -- this is what keeps a release
      model from degenerating into ignoring sustain;
    * a melody note has no freedom at all.  Measured twice on the published
      corpus: releasing melody buys nothing, and holding it makes melody-F1
      invariant by construction;
    * a bass note may go to half its written value, no further.
    """

    onsets = sorted({note.onset for note in notes})
    # Positional rather than keyed: two voices may share an onset and a pitch,
    # and a lookup that cannot tell them apart would hand one note the other's
    # freedom.
    bounds: list[HoldBounds] = []
    for note in notes:
        end = note.onset + note.duration
        later = [onset for onset in onsets if note.onset < onset < end]
        floor = (later[0] - note.onset) if later else note.duration
        if note.voice == "melody":
            floor = note.duration
        elif note.voice == "bass":
            floor = max(floor, note.duration * _BASS_MINIMUM_HOLD_FRACTION)
        bounds.append(
            HoldBounds(minimum=min(floor, note.duration), maximum=note.duration)
        )
    return tuple(bounds)


def sustain_relaxations(notes: Sequence[Note]) -> tuple[tuple[Note, ...], ...]:
    """The score as written, then with accompanying voices let go progressively early.

    A whole-score retry rather than a per-frame choice, because releasing a
    hold only buys anything *before* the hand needs the freedom: letting go at
    the instant a frame is refused frees the shape but not one millisecond of
    travel, since the hand was pinned to that instant.  Measured -- releasing
    reactively inside the beam reaches three fewer pieces than deciding up
    front.

    Three properties make the ladder safe to climb:

    * the score exactly as written is always the first rung, so anything that
      solves without giving something up takes the path it always took;
    * rungs are ordered by how much sustain they give up, least first, so the
      solver never spends more faithfulness than it had to;
    * a rung that would hold less than ``SUSTAIN_RETENTION_FLOOR`` of what was
      written is not offered at all.  The floor is structural here rather than
      a report the caller may ignore -- an unbounded ladder would eventually
      accept every score by simply not sustaining it.
    """

    bounds = hold_bounds(notes)
    notated = sum((note.duration for note in notes), Fraction(0))

    def relax(voices: frozenset[str], keep: Fraction) -> tuple[Note, ...]:
        return tuple(
            note
            if note.voice not in voices
            else Note(
                onset=note.onset,
                duration=max(limits.minimum, note.duration * keep),
                pitch=note.pitch,
                voice=note.voice,
            )
            for note, limits in zip(notes, bounds, strict=True)
        )

    written = tuple(notes)
    rungs: dict[tuple[Note, ...], Fraction] = {}
    for voices in (frozenset({"bass"}), frozenset({"bass", "harmony"})):
        for keep in (Fraction(3, 4), Fraction(1, 2), Fraction(0)):
            rung = relax(voices, keep)
            if rung == written or rung in rungs:
                continue
            realized = sum((note.duration for note in rung), Fraction(0))
            if not notated or realized / notated >= SUSTAIN_RETENTION_FLOOR:
                rungs[rung] = realized
    return (written, *sorted(rungs, key=lambda rung: -rungs[rung]))


__all__ = [
    "SUSTAIN_MODEL_VERSION",
    "SUSTAIN_RETENTION_FLOOR",
    "HoldBounds",
    "hold_bounds",
    "sustain_relaxations",
    "repair_repeated_pitch_holds",
]
