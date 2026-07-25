import type { CanonicalTab, CanonicalTabNote } from "./types";

export interface PositionedTabNote extends CanonicalTabNote {
  onsetBeats: number;
  durationBeats: number;
  endBeats: number;
}

export function fractionBeats(token: string): number {
  const [numerator, denominator] = token.split("/").map(Number);
  return numerator / denominator;
}

export function positionTab(tab: CanonicalTab): PositionedTabNote[] {
  return tab.notes.map((note) => {
    const onsetBeats = fractionBeats(note.onset);
    const durationBeats = fractionBeats(note.duration);
    return {
      ...note,
      onsetBeats,
      durationBeats,
      endBeats: onsetBeats + durationBeats,
    };
  });
}

export function activeNotesAtBeat(
  notes: readonly PositionedTabNote[],
  beat: number,
): PositionedTabNote[] {
  return notes.filter((note) => note.onsetBeats <= beat && beat < note.endBeats);
}

export function tabDurationBeats(notes: readonly PositionedTabNote[]): number {
  return notes.reduce((latest, note) => Math.max(latest, note.endBeats), 0);
}

export function millisecondsToBeat(milliseconds: number, tempoBpm: number): number {
  return (milliseconds * tempoBpm) / 60_000;
}

export function beatToMeasure(beat: number, beatsPerMeasure: number): number {
  return Math.floor(beat / beatsPerMeasure) + 1;
}
