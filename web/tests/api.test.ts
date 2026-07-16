import { afterEach, describe, expect, it, vi } from "vitest";
import { arrangeScore, FretsureAPIError, getCapabilities } from "../src/api";
import { arrangement, capabilities, jsonResponse } from "./fixtures";

const controls = {
  engine: "offline" as const,
  profile: "median",
  n: 1,
  maxIters: 0,
  useCritic: false,
  tempoBpm: null,
};

function requestArrangement(document: unknown): ReturnType<typeof arrangeScore> {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(document)));
  return arrangeScore(new File(["x"], "x.musicxml"), controls);
}

describe("API client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("rejects incompatible success documents", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ ok: true })));
    await expect(getCapabilities()).rejects.toThrow("incompatible capabilities");
  });

  it("rejects malformed nested capability controls and identities", async () => {
    const invalidDefault = structuredClone(capabilities) as unknown as {
      controls: { arrange: { defaults: Record<string, unknown> } };
    };
    invalidDefault.controls.arrange.defaults.n = "4";

    const duplicateEngine = structuredClone(capabilities) as unknown as {
      engines: Array<Record<string, unknown>>;
    };
    duplicateEngine.engines[1].id = "offline";

    const missingProfile = structuredClone(capabilities) as unknown as {
      profiles: Array<Record<string, unknown>>;
    };
    Reflect.deleteProperty(missingProfile.profiles[0], "fingerprint");

    for (const document of [invalidDefault, duplicateEngine, missingProfile]) {
      vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(document)));
      await expect(getCapabilities()).rejects.toThrow("incompatible capabilities");
    }
  });

  it("surfaces application/problem+json without stringifying unknown server data", async () => {
    const problem = {
      type: "about:blank",
      api_version: "fretsure-api@0.1.0",
      status: 413,
      code: "BODY_LIMIT_EXCEEDED",
      title: "Request body too large",
      detail: "request body exceeds the public limit",
    };
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(problem, 413)));

    const request = arrangeScore(new File(["x"], "x.musicxml"), {
      engine: "offline",
      profile: "median",
      n: 1,
      maxIters: 0,
      useCritic: false,
      tempoBpm: null,
    });
    await expect(request).rejects.toBeInstanceOf(FretsureAPIError);
    await expect(request).rejects.toMatchObject({ problem: { code: "BODY_LIMIT_EXCEEDED" } });
  });

  it("accepts the frozen arrangement envelope", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(arrangement)));
    await expect(
      arrangeScore(new File(["x"], "x.mxl"), {
        engine: "offline",
        profile: "median",
        n: 2,
        maxIters: 3,
        useCritic: true,
        tempoBpm: 87.5,
      }),
    ).resolves.toEqual(arrangement);
  });

  it("rejects malformed nested arrangement evidence before render", async () => {
    const invalidTimeSignature = structuredClone(arrangement) as unknown as {
      score: { time_signature: Record<string, unknown> };
    };
    invalidTimeSignature.score.time_signature.denominator = "4";

    const invalidDiagnostic = structuredClone(arrangement) as unknown as {
      playability: { diagnostics: unknown[] };
    };
    invalidDiagnostic.playability.diagnostics = [
      {
        measure: 1,
        beat: "0/1",
        violation_type: "SPAN",
        offending_notes: [0],
        overage: "1.5",
        suggested_relaxations: ["drop_note"],
      },
    ];

    const invalidFaithfulness = structuredClone(arrangement) as unknown as {
      faithfulness: Record<string, unknown>;
    };
    invalidFaithfulness.faithfulness.harmony_jaccard = 1.5;

    const invalidWarning = structuredClone(arrangement) as unknown as {
      source: { warnings: unknown[] };
    };
    invalidWarning.source.warnings = [
      { code: "NOTICE", severity: "warning", message: "notice", location: { measure: 1 } },
    ];

    const missingApiVersion = structuredClone(arrangement) as unknown as Record<string, unknown>;
    Reflect.deleteProperty(missingApiVersion, "api_version");

    const invalidEngine = structuredClone(arrangement) as unknown as {
      model: Record<string, unknown>;
    };
    invalidEngine.model.engine = "remote";

    const missingStamp = structuredClone(arrangement);
    Reflect.deleteProperty(missingStamp.stamps, "model_id");

    const mismatchedStamp = structuredClone(arrangement);
    mismatchedStamp.stamps.model_id = "another-model";

    for (const document of [
      invalidTimeSignature,
      invalidDiagnostic,
      invalidFaithfulness,
      invalidWarning,
      missingApiVersion,
      invalidEngine,
      missingStamp,
      mismatchedStamp,
    ]) {
      await expect(requestArrangement(document)).rejects.toThrow("incompatible arrangement");
    }
  });

  it("enforces status and product-gate absence consistency", async () => {
    const noFingering = structuredClone(arrangement);
    noFingering.status = "no_fingering_within_budget";
    noFingering.tab = null;
    noFingering.ascii = null;
    noFingering.playability = null;
    noFingering.faithfulness = null;
    await expect(requestArrangement(noFingering)).resolves.toEqual(noFingering);

    const statusMismatch = structuredClone(arrangement);
    statusMismatch.status = "no_fingering_within_budget";
    await expect(requestArrangement(statusMismatch)).rejects.toThrow("incompatible arrangement");

    const missingGate = structuredClone(arrangement);
    missingGate.playability = null;
    await expect(requestArrangement(missingGate)).rejects.toThrow("incompatible arrangement");

    const emptyAscii = structuredClone(arrangement);
    emptyAscii.ascii = "";
    await expect(requestArrangement(emptyAscii)).rejects.toThrow("incompatible arrangement");
  });

  it("rejects malformed, non-contiguous, or schema-inconsistent trace rows", async () => {
    const skippedSequence = structuredClone(arrangement);
    skippedSequence.trace.steps[1].seq = 7;

    const wrongSchema = structuredClone(arrangement);
    wrongSchema.trace.steps[0].trace_schema_version = "agent-trace@future";

    const extraTraceField = structuredClone(arrangement) as unknown as {
      trace: { steps: Array<Record<string, unknown>> };
    };
    extraTraceField.trace.steps[0].hidden_reasoning = "must not enter the public row";

    const invalidData = structuredClone(arrangement) as unknown as {
      trace: { steps: Array<Record<string, unknown>> };
    };
    invalidData.trace.steps[0].data = [];

    const mismatchedEventKind = structuredClone(arrangement);
    mismatchedEventKind.trace.steps[0].kind = "ORACLE";

    for (const document of [
      skippedSequence,
      wrongSchema,
      extraTraceField,
      invalidData,
      mismatchedEventKind,
    ]) {
      await expect(requestArrangement(document)).rejects.toThrow("incompatible arrangement");
    }
  });

  it("keeps the capability fixture aligned", () => {
    expect(capabilities.controls.arrange.n.max).toBe(8);
  });
});
