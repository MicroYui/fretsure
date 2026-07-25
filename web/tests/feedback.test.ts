import { beforeEach, describe, expect, it } from "vitest";

import {
  appendFeedback,
  clearFeedback,
  FEEDBACK_SCHEMA_VERSION,
  feedbackDownload,
  readFeedback,
} from "../src/feedback";

const context = {
  source_sha256: "a".repeat(64),
  model_id: "constant-stub",
  player_profile: "median",
  style: "fingerstyle" as const,
  difficulty_tier: "intermediate" as const,
  technique_profile: "balanced" as const,
};

describe("local anonymous feedback", () => {
  beforeEach(() => window.localStorage.clear());

  it("stores rating, A/B and fingering correction evidence without training claims", () => {
    appendFeedback(
      { ...context, kind: "rating", rating: 4, tags: ["natural"], note: "Comfortable" },
      window.localStorage,
      new Date("2026-07-25T01:00:00.000Z"),
    );
    appendFeedback({
      ...context,
      kind: "ab_preference",
      candidate_indices: [0, 1],
      preferred_candidate_index: 1,
    });
    const document = appendFeedback({
      ...context,
      kind: "fingering_correction",
      note_index: 2,
      before_finger: 3,
      requested_finger: 2,
      outcome: "applied",
    });

    expect(document.schema_version).toBe(FEEDBACK_SCHEMA_VERSION);
    expect(document.identity).toBe("anonymous");
    expect(document.meaning).toBe("local_preference_evidence_not_model_training");
    expect(document.entries.map((entry) => entry.kind)).toEqual([
      "rating",
      "ab_preference",
      "fingering_correction",
    ]);
    expect(document.entries[0]).toMatchObject({
      player_profile: "median",
      difficulty_tier: "intermediate",
    });
    expect(readFeedback()).toEqual(document);
    expect(feedbackDownload(document).filename).toBe("fretsure-anonymous-feedback.json");
  });

  it("ignores stale documents and clears explicitly", () => {
    window.localStorage.setItem("fretsure.feedback.v1", '{"schema_version":"old"}');
    expect(readFeedback().entries).toEqual([]);
    appendFeedback({ ...context, kind: "rating", rating: 3, tags: [], note: "" });
    expect(clearFeedback().entries).toEqual([]);
  });
});
