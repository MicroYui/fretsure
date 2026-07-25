import { describe, expect, it } from "vitest";

import {
  measureAtPoint,
  measureRangeFromDrag,
  normalizeMeasureRange,
  type MeasureBounds,
} from "../src/measureSelection";

describe("measure selection", () => {
  it("turns forward and reverse drags into an ordered measure range", () => {
    expect(measureRangeFromDrag(3, 7, 12)).toEqual({ start: 3, end: 7 });
    expect(measureRangeFromDrag(7, 3, 12)).toEqual({ start: 3, end: 7 });
  });

  it("clamps a drag to the score and the 32-measure endpoint limit", () => {
    expect(measureRangeFromDrag(2, 80, 80)).toEqual({ start: 2, end: 33 });
    expect(measureRangeFromDrag(50, 1, 80)).toEqual({ start: 19, end: 50 });
    expect(normalizeMeasureRange(79, 90, 80)).toEqual({ start: 79, end: 80 });
  });

  it("finds the nearest rendered measure across wrapped staff systems", () => {
    const bounds: MeasureBounds[] = [
      { measure: 1, x: 10, y: 20, width: 100, height: 80 },
      { measure: 2, x: 110, y: 20, width: 100, height: 80 },
      { measure: 3, x: 10, y: 130, width: 100, height: 80 },
      { measure: 4, x: 110, y: 130, width: 100, height: 80 },
    ];

    expect(measureAtPoint(bounds, 175, 60)).toBe(2);
    expect(measureAtPoint(bounds, 55, 175)).toBe(3);
    expect(measureAtPoint(bounds, 108, 175)).toBe(3);
  });
});
