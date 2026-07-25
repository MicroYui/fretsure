import type {
  ArrangementResponse,
  DifficultyCheckResponse,
  VerifiedAlternative,
} from "./types";

export const REPAIR_ABLATION_STATUS = "NOT_KEPT · benchmark v2" as const;

export interface LiveRunScorecard {
  oracle: string;
  fidelity: string;
  difficulty: string;
  verifiedAlternatives: string;
  actualModelCalls: string;
  fairComparison: string;
  productPolicy: string;
  repairAblation: typeof REPAIR_ABLATION_STATUS;
}

export function fairAlternativePair(
  result: ArrangementResponse,
): readonly [VerifiedAlternative, VerifiedAlternative] | null {
  const ordered = [...result.alternatives].sort(
    (left, right) => left.candidate_index - right.candidate_index,
  );
  for (let left = 0; left < ordered.length; left += 1) {
    for (let right = left + 1; right < ordered.length; right += 1) {
      if (ordered[left].work.model_calls === ordered[right].work.model_calls) {
        return [ordered[left], ordered[right]];
      }
    }
  }
  return null;
}

export function selectedCandidateIndex(result: ArrangementResponse): number | null {
  const selection = result.trace.steps.find((step) => step.event === "CANDIDATE_SELECTED");
  return selection?.candidate_index ?? null;
}

export function computeLiveRunScorecard(
  result: ArrangementResponse,
  difficulty: DifficultyCheckResponse | null,
): LiveRunScorecard {
  const pair = fairAlternativePair(result);
  const modelCalls = result.alternatives.reduce(
    (total, alternative) => total + alternative.work.model_calls,
    0,
  );
  const policy = result.trace.steps.find(
    (step) => step.data.policy === "incremental_v1",
  );
  return {
    oracle: result.playability?.verdict ?? "UNAVAILABLE",
    fidelity: result.faithfulness
      ? result.faithfulness.passed
        ? `PASS · ${result.faithfulness.evaluated_dimensions.length}/3`
        : `REVIEW · ${result.faithfulness.evaluated_dimensions.length}/3`
      : "UNAVAILABLE",
    difficulty: difficulty
      ? `${difficulty.options.tier.toUpperCase()} · ${difficulty.difficulty.meets ? "PASS" : "REVIEW"} · est. G${difficulty.published_grade.estimated_grade}`
      : "UNAVAILABLE",
    verifiedAlternatives: `${result.alternatives.length} / ${result.options.candidate_count}`,
    actualModelCalls: String(modelCalls),
    fairComparison: pair
      ? `READY · ${pair[0].work.model_calls} call${pair[0].work.model_calls === 1 ? "" : "s"} / output`
      : "UNAVAILABLE",
    productPolicy: policy ? "incremental_v1" : "deterministic baseline",
    repairAblation: REPAIR_ABLATION_STATUS,
  };
}
