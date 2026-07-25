import { describe, expect, it } from "vitest";

import {
  computeLiveRunScorecard,
  fairAlternativePair,
  selectedCandidateIndex,
} from "../src/scorecard";
import type { VerifiedAlternative } from "../src/types";
import { arrangement } from "./fixtures";

function alternative(candidateIndex: number, modelCalls: number): VerifiedAlternative {
  return {
    candidate_index: candidateIndex,
    tab: structuredClone(arrangement.tab!),
    ascii: arrangement.ascii!,
    playability: structuredClone(arrangement.playability!),
    faithfulness: structuredClone(arrangement.faithfulness!),
    work: {
      model_calls: modelCalls,
      trial_solver_calls: 1,
      proposed_additions: 2,
      accepted_additions: 1,
    },
    proposal_status: "LLM_SUCCESS",
    observed_critic: {
      status: null,
      overall: null,
      meaning: "machine_observation_not_human_musicality_evidence",
    },
  };
}

describe("live scorecard", () => {
  it("forms a fair pair only when per-output model-call counts match", () => {
    const result = structuredClone(arrangement);
    result.options.candidate_count = 3;
    result.alternatives = [alternative(2, 2), alternative(0, 1), alternative(1, 1)];

    expect(fairAlternativePair(result)?.map((item) => item.candidate_index)).toEqual([0, 1]);
    result.alternatives[0].work.model_calls = 1;
    result.alternatives[1].work.model_calls = 2;
    result.alternatives[2].work.model_calls = 3;
    expect(fairAlternativePair(result)).toBeNull();
  });

  it("recomputes unavailable and ablation evidence from the typed run", () => {
    const result = structuredClone(arrangement);
    result.options.candidate_count = 2;
    result.alternatives = [alternative(0, 1), alternative(1, 1)];
    const scorecard = computeLiveRunScorecard(result, null);

    expect(scorecard).toMatchObject({
      oracle: "GREEN",
      fidelity: "PASS · 3/3",
      difficulty: "UNAVAILABLE",
      verifiedAlternatives: "2 / 2",
      actualModelCalls: "2",
      fairComparison: "READY · 1 call / output",
      repairAblation: "NOT_KEPT · benchmark v2",
    });
    expect(selectedCandidateIndex(result)).toBe(0);
  });
});
