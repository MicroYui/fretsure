import { afterEach, describe, expect, it, vi } from "vitest";
import {
  arrangeScore,
  canonicalTabJSON,
  exportGuitarPro,
  exportMidi,
  exportMusicXMLTab,
  exportPdfTab,
  exportTabText,
  FretsureAPIError,
  getCapabilities,
} from "../src/api";
import {
  arrangement,
  capabilities,
  jsonResponse,
  midiArrangement,
  producerMxlArrangement,
  producerXmlArrangement,
} from "./fixtures";

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

  it("accepts the evidence-backed arrangement defaults", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(capabilities)));
    await expect(getCapabilities()).resolves.toMatchObject({
      controls: {
        arrange: {
          defaults: { n: 1, max_iters: 0, use_critic: false },
        },
      },
    });
  });

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

    const stalePackageStamp = structuredClone(capabilities);
    stalePackageStamp.stamps.package_version = "0.3.0";

    const missingRouterStamp = structuredClone(capabilities);
    Reflect.deleteProperty(missingRouterStamp.stamps, "score_input_version");

    const staleMidiRegistry = structuredClone(capabilities);
    staleMidiRegistry.inputs.score_input.format_importers.midi = "midi@future";

    const missingMidiSuffix = structuredClone(capabilities);
    missingMidiSuffix.inputs.score_suffixes = missingMidiSuffix.inputs.score_suffixes.filter(
      (suffix) => suffix !== ".mid",
    );

    for (const document of [
      invalidDefault,
      duplicateEngine,
      missingProfile,
      stalePackageStamp,
      missingRouterStamp,
      staleMidiRegistry,
      missingMidiSuffix,
    ]) {
      vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(document)));
      await expect(getCapabilities()).rejects.toThrow("incompatible capabilities");
    }
  });

  it("surfaces application/problem+json without stringifying unknown server data", async () => {
    const problem = {
      type: "about:blank",
      api_version: "fretsure-api@0.2.0",
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

  it.each([
    ["musescore-4.7.4.musicxml", producerXmlArrangement],
    ["musescore-4.7.4-roundtrip-supported_basic.mxl", producerMxlArrangement],
    ["melody.mid", midiArrangement],
  ])("accepts loss-aware producer evidence for %s", async (filename, document) => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(document)));

    await expect(
      arrangeScore(new File(["producer"], filename), controls),
    ).resolves.toEqual(document);
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
      {
        code: "NOTICE",
        severity: "warning",
        message: "notice",
        location: {
          part_id: null,
          measure: null,
          voice: null,
          element: null,
          archive_member: null,
          track_index: null,
          event_index: null,
          channel: null,
          tick: -1,
        },
      },
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

    const staleImporter = structuredClone(arrangement);
    staleImporter.source.importer_version = "musicxml@0.2.0";
    staleImporter.stamps.importer_version = "musicxml@0.2.0";

    const missingFilename = structuredClone(arrangement) as unknown as {
      source: { filename: string | null };
    };
    missingFilename.source.filename = null;

    const invalidTab = structuredClone(arrangement) as unknown as {
      tab: { notes: unknown[] };
    };
    invalidTab.tab.notes = [
      {
        onset: "0/1",
        duration: "1/1",
        string: 0,
        fret: 0,
        left_finger: 0,
        right_finger: "thumb",
      },
    ];

    for (const document of [
      invalidTimeSignature,
      invalidDiagnostic,
      invalidFaithfulness,
      invalidWarning,
      missingApiVersion,
      invalidEngine,
      missingStamp,
      mismatchedStamp,
      staleImporter,
      missingFilename,
      invalidTab,
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
    noFingering.trace.steps = noFingering.trace.steps.filter(
      (step) => step.event !== "CANDIDATE_SELECTED",
    );
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

  it("rejects forged fidelity availability partitions and pass claims", async () => {
    const nullScoreMarkedEvaluated = structuredClone(midiArrangement);
    nullScoreMarkedEvaluated.faithfulness!.evaluated_dimensions = ["melody", "bass_root"];
    nullScoreMarkedEvaluated.faithfulness!.unavailable_dimensions = ["harmony"];

    const wrongDimensionOrder = structuredClone(midiArrangement);
    wrongDimensionOrder.faithfulness!.unavailable_dimensions = ["harmony", "bass_root"];

    const forgedPass = structuredClone(midiArrangement);
    forgedPass.faithfulness!.melody_f1 = 0.89;

    const unavailableClaimedAsPerfect = structuredClone(midiArrangement);
    unavailableClaimedAsPerfect.faithfulness!.bass_root_accuracy = 1;

    for (const document of [
      nullScoreMarkedEvaluated,
      wrongDimensionOrder,
      forgedPass,
      unavailableClaimedAsPerfect,
    ]) {
      await expect(requestArrangement(document)).rejects.toThrow("incompatible arrangement");
    }
  });

  it("binds the selected trace row to the authoritative product gates", async () => {
    const traceScoreMismatch = structuredClone(midiArrangement);
    const mismatchedSelection = traceScoreMismatch.trace.steps.find(
      (step) => step.event === "CANDIDATE_SELECTED",
    )!;
    mismatchedSelection.data.melody_f1 = 0.95;

    const traceAvailabilityMismatch = structuredClone(midiArrangement);
    const mismatchedAvailability = traceAvailabilityMismatch.trace.steps.find(
      (step) => step.event === "CANDIDATE_SELECTED",
    )!;
    mismatchedAvailability.data.evaluated_dimensions = ["melody", "bass_root"];
    mismatchedAvailability.data.unavailable_dimensions = ["harmony"];

    const missingSelection = structuredClone(midiArrangement);
    missingSelection.trace.steps = missingSelection.trace.steps.filter(
      (step) => step.event !== "CANDIDATE_SELECTED",
    );

    const extraSelectionField = structuredClone(midiArrangement);
    const extendedSelection = extraSelectionField.trace.steps.find(
      (step) => step.event === "CANDIDATE_SELECTED",
    )!;
    extendedSelection.data.router_version = "score-input@0.1.0";

    for (const document of [
      traceScoreMismatch,
      traceAvailabilityMismatch,
      missingSelection,
      extraSelectionField,
    ]) {
      await expect(requestArrangement(document)).rejects.toThrow("incompatible arrangement");
    }
  });

  it("accepts a deterministic baseline selection without a model candidate index", async () => {
    const baseline = structuredClone(midiArrangement);
    const selection = baseline.trace.steps.find(
      (step) => step.event === "CANDIDATE_SELECTED",
    )!;
    selection.candidate_index = null;
    selection.detail =
      "Selected the deterministic baseline after the model candidates returned no tablature.";
    selection.data.winner_candidate_index = null;

    await expect(requestArrangement(baseline)).resolves.toEqual(baseline);
  });

  it("exports the canonical Tab JSON as MIDI at the effective tempo", async () => {
    const midiBytes = new Uint8Array([0x4d, 0x54, 0x68, 0x64, 0x00, 0x00, 0x00, 0x06]);
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(midiBytes, {
        headers: {
          "content-disposition": 'attachment; filename="fretsure-arrangement.mid"',
          "content-type": "audio/midi",
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const asset = await exportMidi(arrangement.tab!, 87.5);

    expect(asset.filename).toBe("fretsure-arrangement.mid");
    expect(new Uint8Array(await asset.blob.arrayBuffer())).toEqual(midiBytes);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("/api/v1/exports/midi?tempo_bpm=87.5");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("accept")).toBe("audio/midi");
    expect(new Headers(init?.headers).get("content-type")).toBe("application/json");
    expect(init?.body).toBe(canonicalTabJSON(arrangement.tab!));
  });

  it("exports canonical Tab as a fingered six-line text score", async () => {
    const body = "Six-line tablature (high e to low E):\ne|--0--|\n";
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(body, {
        headers: {
          "content-disposition": 'attachment; filename="fretsure-guitar-tablature.txt"',
          "content-type": "text/plain; charset=utf-8",
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const asset = await exportTabText(arrangement.tab!);

    expect(asset.filename).toBe("fretsure-guitar-tablature.txt");
    expect(await asset.blob.text()).toBe(body);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("/api/v1/exports/tab-text");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("accept")).toBe("text/plain");
    expect(init?.body).toBe(canonicalTabJSON(arrangement.tab!));
  });

  it.each([
    {
      label: "MusicXML TAB",
      run: exportMusicXMLTab,
      path: "/api/v1/exports/musicxml-tab?tempo_bpm=96",
      contentType: "application/vnd.recordare.musicxml+xml",
      filename: "fretsure-guitar-tablature.musicxml",
      bytes: new TextEncoder().encode("<?xml version=\"1.0\"?><score-partwise/>"),
    },
    {
      label: "Guitar Pro",
      run: exportGuitarPro,
      path: "/api/v1/exports/guitar-pro?tempo_bpm=96",
      contentType: "application/octet-stream",
      filename: "fretsure-guitar-tab.gp5",
      bytes: new TextEncoder().encode("FICHIER GUITAR PRO v5.10"),
    },
    {
      label: "PDF TAB",
      run: exportPdfTab,
      path: "/api/v1/exports/pdf-tab?tempo_bpm=96",
      contentType: "application/pdf",
      filename: "fretsure-guitar-tab.pdf",
      bytes: new TextEncoder().encode("%PDF-1.4"),
    },
  ])("exports canonical Tab as $label", async ({
    run,
    path,
    contentType,
    filename,
    bytes,
  }) => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(bytes, {
        headers: {
          "content-disposition": `attachment; filename="${filename}"`,
          "content-type": contentType,
        },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const asset = await run(arrangement.tab!, 96);

    expect(asset.filename).toBe(filename);
    expect(Array.from(new Uint8Array(await asset.blob.arrayBuffer()))).toEqual(
      Array.from(bytes),
    );
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toBe(path);
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("accept")).toBe(contentType);
    expect(init?.body).toBe(canonicalTabJSON(arrangement.tab!));
  });

  it("serializes downloaded Tab JSON in the canonical field order", () => {
    expect(
      canonicalTabJSON({
        tuning: [40, 45, 50, 55, 59, 64],
        capo: 0,
        notes: [
          {
            onset: "0/1",
            duration: "1/2",
            string: 4,
            fret: 1,
            left_finger: 1,
            right_finger: "i",
          },
        ],
      }),
    ).toBe(
      '{"tuning":[40,45,50,55,59,64],"capo":0,"notes":[{"onset":"0/1","duration":"1/2","string":4,"fret":1,"left_finger":1,"right_finger":"i"}]}',
    );
  });

  it("rejects a non-MIDI success response from the export endpoint", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response("not midi", { headers: { "content-type": "text/plain" } }),
      ),
    );

    await expect(exportMidi(arrangement.tab!, 90)).rejects.toThrow(
      "incompatible MIDI export",
    );
  });

  it("rejects a non-text success response from the guitar TAB endpoint", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response("not tab text", { headers: { "content-type": "application/json" } }),
      ),
    );

    await expect(exportTabText(arrangement.tab!)).rejects.toThrow(
      "incompatible guitar TAB export",
    );
  });

  it("rejects a format-inconsistent professional export response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response("not pdf", { headers: { "content-type": "text/plain" } }),
      ),
    );

    await expect(exportPdfTab(arrangement.tab!, 90)).rejects.toThrow(
      "incompatible PDF TAB export",
    );
  });

  it("rejects format-inconsistent source provenance and MIDI channel locations", async () => {
    const mismatchedMidiRoot = structuredClone(midiArrangement);
    mismatchedMidiRoot.source.root_sha256 = "c".repeat(64);

    const wrappedMidi = structuredClone(midiArrangement);
    wrappedMidi.source.container_version = "wrapper@0.1.0";

    const wrappedPlainXml = structuredClone(arrangement);
    wrappedPlainXml.source.root_member = "score.xml";

    const unboundMxlRoot = structuredClone(producerMxlArrangement);
    unboundMxlRoot.source.root_member = null;

    const zeroBasedChannel = structuredClone(midiArrangement);
    zeroBasedChannel.source.warnings[0].location!.channel = 0;

    for (const document of [
      mismatchedMidiRoot,
      wrappedMidi,
      wrappedPlainXml,
      unboundMxlRoot,
      zeroBasedChannel,
    ]) {
      await expect(requestArrangement(document)).rejects.toThrow("incompatible arrangement");
    }
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
    expect(capabilities.inputs.score_suffixes).toEqual([
      ".musicxml",
      ".xml",
      ".mxl",
      ".mid",
      ".midi",
    ]);
  });
});
