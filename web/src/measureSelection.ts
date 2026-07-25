export const MAX_SECTION_MEASURES = 32;

export type MeasureRange = {
  start: number;
  end: number;
};

export type MeasureBounds = {
  measure: number;
  x: number;
  y: number;
  width: number;
  height: number;
};

function clampMeasure(measure: number, measureCount: number): number {
  return Math.min(Math.max(Math.round(measure), 1), Math.max(1, measureCount));
}

export function measureRangeFromDrag(
  anchor: number,
  focus: number,
  measureCount: number,
  maxSpan = MAX_SECTION_MEASURES,
): MeasureRange {
  const safeAnchor = clampMeasure(anchor, measureCount);
  const safeFocus = clampMeasure(focus, measureCount);
  const safeSpan = Math.max(1, Math.round(maxSpan));

  if (safeFocus >= safeAnchor) {
    return {
      start: safeAnchor,
      end: Math.min(safeFocus, safeAnchor + safeSpan - 1),
    };
  }
  return {
    start: Math.max(safeFocus, safeAnchor - safeSpan + 1),
    end: safeAnchor,
  };
}

export function normalizeMeasureRange(
  start: number,
  end: number,
  measureCount: number,
  maxSpan = MAX_SECTION_MEASURES,
): MeasureRange {
  const safeStart = clampMeasure(start, measureCount);
  const safeEnd = clampMeasure(Math.max(end, safeStart), measureCount);
  return {
    start: safeStart,
    end: Math.min(safeEnd, safeStart + Math.max(1, Math.round(maxSpan)) - 1),
  };
}

export function measureAtPoint(
  bounds: readonly MeasureBounds[],
  x: number,
  y: number,
): number | null {
  let nearest: MeasureBounds | null = null;
  let nearestDistance = Number.POSITIVE_INFINITY;

  for (const candidate of bounds) {
    const right = candidate.x + candidate.width;
    const bottom = candidate.y + candidate.height;
    const dx = x < candidate.x ? candidate.x - x : x > right ? x - right : 0;
    const dy = y < candidate.y ? candidate.y - y : y > bottom ? y - bottom : 0;
    const distance = dx * dx + dy * dy;
    if (
      distance < nearestDistance ||
      (distance === nearestDistance &&
        (nearest === null || candidate.measure < nearest.measure))
    ) {
      nearest = candidate;
      nearestDistance = distance;
    }
  }

  return nearest?.measure ?? null;
}
