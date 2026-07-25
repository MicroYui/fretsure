import type {
  CanonicalTab,
  PlayabilityDiagnostic,
  PublicTrace,
  TraceStep,
  Verdict,
} from "./types";

export interface IncrementalTrialEvidence {
  step: TraceStep;
  accepted: boolean;
  reasonCode: string | null;
  verdict: Verdict | "INFEASIBLE" | null;
  attemptedTab: CanonicalTab | null;
  diagnostics: PlayabilityDiagnostic[];
}

export interface TraceSummary {
  acceptedTrials: number;
  rejectedTrials: number;
  solverTrials: number;
  proposedAdditions: number;
  acceptedAdditions: number;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isCanonicalTab(value: unknown): value is CanonicalTab {
  return (
    isRecord(value) &&
    Array.isArray(value.tuning) &&
    value.tuning.length === 6 &&
    value.tuning.every((pitch) => Number.isInteger(pitch)) &&
    Number.isInteger(value.capo) &&
    Array.isArray(value.notes) &&
    value.notes.every(
      (note) =>
        isRecord(note) &&
        typeof note.onset === "string" &&
        typeof note.duration === "string" &&
        Number.isInteger(note.string) &&
        Number.isInteger(note.fret) &&
        Number.isInteger(note.left_finger) &&
        (note.right_finger === "p" ||
          note.right_finger === "i" ||
          note.right_finger === "m" ||
          note.right_finger === "a"),
    )
  );
}

function checkpointTab(value: unknown): CanonicalTab | null {
  if (!isRecord(value) || value.type !== "tab" || value.complete !== true) return null;
  return isCanonicalTab(value.state) ? value.state : null;
}

function diagnostics(value: unknown): PlayabilityDiagnostic[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (
      !isRecord(item) ||
      typeof item.code !== "string" ||
      !Number.isInteger(item.measure) ||
      typeof item.beat !== "string" ||
      !Array.isArray(item.offending_note_indices) ||
      !item.offending_note_indices.every((index) => Number.isInteger(index)) ||
      typeof item.overage !== "number" ||
      !Array.isArray(item.suggested_relaxations) ||
      !item.suggested_relaxations.every((text) => typeof text === "string")
    ) {
      return [];
    }
    return [
      {
        measure: item.measure as number,
        beat: item.beat,
        violation_type: item.code,
        offending_notes: item.offending_note_indices as number[],
        overage: item.overage,
        suggested_relaxations: item.suggested_relaxations as string[],
      },
    ];
  });
}

export function incrementalTrials(trace: PublicTrace): IncrementalTrialEvidence[] {
  return trace.steps.flatMap((step) => {
    const data = step.data;
    if (
      step.event !== "EDIT" ||
      data.policy !== "incremental_v1" ||
      typeof data.accepted !== "boolean"
    ) {
      return [];
    }
    const verdict =
      data.verdict === "GREEN" ||
      data.verdict === "AMBER" ||
      data.verdict === "RED" ||
      data.verdict === "INFEASIBLE"
        ? data.verdict
        : null;
    return [
      {
        step,
        accepted: data.accepted,
        reasonCode: typeof data.reason_code === "string" ? data.reason_code : null,
        verdict,
        attemptedTab: checkpointTab(data.tab_checkpoint),
        diagnostics: diagnostics(data.diagnostics),
      },
    ];
  });
}

export function summarizeTrace(trace: PublicTrace): TraceSummary {
  const trials = incrementalTrials(trace);
  const proposal = trace.steps.find(
    (step) => step.event === "PROPOSE" && step.data.policy === "incremental_v1",
  );
  const terminal = [...trace.steps]
    .reverse()
    .find((step) => step.event === "SOLVE" && step.data.policy === "incremental_v1");
  return {
    acceptedTrials: trials.filter((trial) => trial.accepted).length,
    rejectedTrials: trials.filter((trial) => !trial.accepted).length,
    solverTrials: trials.filter((trial) => trial.step.data.solver_called === true).length,
    proposedAdditions:
      typeof proposal?.data.proposed_addition_count === "number"
        ? proposal.data.proposed_addition_count
        : 0,
    acceptedAdditions:
      typeof terminal?.data.accepted_addition_count === "number"
        ? terminal.data.accepted_addition_count
        : 0,
  };
}
