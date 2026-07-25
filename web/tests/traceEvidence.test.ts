import { describe, expect, it } from "vitest";

import { incrementalTrials, summarizeTrace } from "../src/traceEvidence";
import type { PublicTrace } from "../src/types";

const attemptedTab = {
  tuning: [40, 45, 50, 55, 59, 64],
  capo: 0,
  notes: [
    {
      onset: "0/1",
      duration: "1/1",
      string: 5,
      fret: 15,
      left_finger: 4,
      right_finger: "i",
    },
  ],
};

const trace: PublicTrace = {
  schema_version: "agent-trace@0.3.0",
  steps: [
    {
      trace_schema_version: "agent-trace@0.3.0",
      seq: 0,
      kind: "PROPOSE",
      event: "PROPOSE",
      candidate_index: 0,
      iteration: null,
      detail: "proposal",
      data: {
        policy: "incremental_v1",
        proposed_addition_count: 3,
      },
    },
    {
      trace_schema_version: "agent-trace@0.3.0",
      seq: 1,
      kind: "EDIT",
      event: "EDIT",
      candidate_index: 0,
      iteration: 1,
      detail: "rejected",
      data: {
        policy: "incremental_v1",
        accepted: false,
        solver_called: true,
        reason_code: "NON_GREEN",
        verdict: "RED",
        tab_checkpoint: { type: "tab", complete: true, state: attemptedTab },
        diagnostics: [
          {
            code: "FRET_SPAN_EXCEEDED",
            measure: 1,
            beat: "0/1",
            offending_note_indices: [0],
            overage: 12,
            suggested_relaxations: ["reduce span"],
          },
        ],
      },
    },
    {
      trace_schema_version: "agent-trace@0.3.0",
      seq: 2,
      kind: "EDIT",
      event: "EDIT",
      candidate_index: 0,
      iteration: 2,
      detail: "accepted",
      data: {
        policy: "incremental_v1",
        accepted: true,
        solver_called: true,
        reason_code: null,
        verdict: "GREEN",
      },
    },
    {
      trace_schema_version: "agent-trace@0.3.0",
      seq: 3,
      kind: "SOLVE",
      event: "SOLVE",
      candidate_index: 0,
      iteration: null,
      detail: "finished",
      data: {
        policy: "incremental_v1",
        accepted_addition_count: 2,
      },
    },
  ],
};

describe("incremental trace evidence", () => {
  it("extracts an attempted red Tab and localized diagnostics", () => {
    const trials = incrementalTrials(trace);

    expect(trials).toHaveLength(2);
    expect(trials[0]).toMatchObject({
      accepted: false,
      reasonCode: "NON_GREEN",
      verdict: "RED",
      attemptedTab,
    });
    expect(trials[0].diagnostics[0].violation_type).toBe("FRET_SPAN_EXCEEDED");
  });

  it("summarizes live product work without inventing missing values", () => {
    expect(summarizeTrace(trace)).toEqual({
      acceptedTrials: 1,
      rejectedTrials: 1,
      solverTrials: 2,
      proposedAdditions: 3,
      acceptedAdditions: 2,
    });
  });
});
