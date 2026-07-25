import { describe, expect, it } from "vitest";

import {
  activeNotesAtBeat,
  beatToMeasure,
  fractionBeats,
  millisecondsToBeat,
  positionTab,
  tabDurationBeats,
} from "../src/performance";
import type { CanonicalTab } from "../src/types";

const tab: CanonicalTab = {
  tuning: [40, 45, 50, 55, 59, 64],
  capo: 0,
  notes: [
    {
      onset: "0/1",
      duration: "2/1",
      string: 0,
      fret: 3,
      left_finger: 3,
      right_finger: "p",
    },
    {
      onset: "1/1",
      duration: "1/2",
      string: 4,
      fret: 1,
      left_finger: 1,
      right_finger: "i",
    },
  ],
};

describe("performance timeline", () => {
  it("maps canonical fractions to active half-open sounding intervals", () => {
    const positioned = positionTab(tab);

    expect(fractionBeats("3/2")).toBe(1.5);
    expect(activeNotesAtBeat(positioned, 1)).toHaveLength(2);
    expect(activeNotesAtBeat(positioned, 1.5)).toHaveLength(1);
    expect(activeNotesAtBeat(positioned, 2)).toHaveLength(0);
    expect(tabDurationBeats(positioned)).toBe(2);
  });

  it("maps player milliseconds onto beats and one-based measures", () => {
    expect(millisecondsToBeat(1_000, 120)).toBe(2);
    expect(beatToMeasure(0, 4)).toBe(1);
    expect(beatToMeasure(4, 4)).toBe(2);
  });
});
