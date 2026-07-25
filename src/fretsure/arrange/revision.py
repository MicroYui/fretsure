"""Measure-scoped target replacement with explicit voice locks."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import cast

from fretsure.agent.arranger import arrangement_solver_ir
from fretsure.ir import MusicIR, Note, VoiceRole, snapshot_music_ir

SECTION_REGENERATION_VERSION = "section-regeneration@0.1.0"
MAX_REGENERATION_MEASURES = 32
_VOICE_ORDER: tuple[VoiceRole, ...] = ("melody", "bass", "harmony")


@dataclass(frozen=True, slots=True)
class SectionSelection:
    start_measure: int
    end_measure: int
    locked_voices: tuple[VoiceRole, ...]

    def __post_init__(self) -> None:
        if (
            type(self.start_measure) is not int
            or type(self.end_measure) is not int
            or self.start_measure < 1
            or self.end_measure < self.start_measure
            or self.end_measure - self.start_measure + 1 > MAX_REGENERATION_MEASURES
        ):
            raise ValueError(f"measure selection must span 1..{MAX_REGENERATION_MEASURES} measures")
        if type(self.locked_voices) is not tuple or any(
            type(voice) is not str or voice not in _VOICE_ORDER for voice in self.locked_voices
        ):
            raise ValueError("locked voices must use supported voice names")
        if len(set(self.locked_voices)) != len(self.locked_voices):
            raise ValueError("locked voices must be unique")
        object.__setattr__(
            self,
            "locked_voices",
            tuple(voice for voice in _VOICE_ORDER if voice in self.locked_voices),
        )


def measure_bounds(ir: MusicIR, selection: SectionSelection) -> tuple[Fraction, Fraction]:
    source = snapshot_music_ir(ir)
    numerator, denominator = source.meta.time_sig
    bar = Fraction(numerator * 4, denominator)
    return (
        (selection.start_measure - 1) * bar,
        selection.end_measure * bar,
    )


def section_target_text(
    target: tuple[Note, ...],
    ir: MusicIR,
    selection: SectionSelection,
) -> str:
    start, end = measure_bounds(ir, selection)
    rows = (
        f"{note.voice}@{note.onset}:{note.duration}:midi{note.pitch}"
        for note in target
        if start <= note.onset < end
    )
    return "; ".join(rows) or "(empty)"


def merge_section_target(
    source_ir: MusicIR,
    baseline: tuple[Note, ...],
    proposal: tuple[Note, ...],
    selection: SectionSelection,
) -> tuple[Note, ...]:
    """Replace unlocked attack onsets in the range and preserve source melody."""

    source = arrangement_solver_ir(snapshot_music_ir(source_ir))
    start, end = measure_bounds(source, selection)
    locked = set(selection.locked_voices)
    merged: dict[tuple[Fraction, int], Note] = {
        (note.onset, note.pitch): note
        for note in baseline
        if not start <= note.onset < end or note.voice in locked
    }
    for note in proposal:
        if start <= note.onset < end and note.voice not in locked:
            merged[(note.onset, note.pitch)] = note

    # Source melody attacks are an invariant even when the melody layer is
    # unlocked to permit short fills inside genuine rests.
    for note in source.notes:
        if note.voice == "melody" and start <= note.onset < end:
            merged[(note.onset, note.pitch)] = note

    voice_rank = {voice: index for index, voice in enumerate(_VOICE_ORDER)}
    return tuple(
        sorted(
            cast(tuple[Note, ...], tuple(merged.values())),
            key=lambda note: (note.onset, note.pitch, voice_rank[note.voice]),
        )
    )


__all__ = [
    "MAX_REGENERATION_MEASURES",
    "SECTION_REGENERATION_VERSION",
    "SectionSelection",
    "measure_bounds",
    "merge_section_target",
    "section_target_text",
]
