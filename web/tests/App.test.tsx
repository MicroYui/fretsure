/// <reference types="node" />

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import type { VerifiedAlternative } from "../src/types";
import {
  arrangement,
  capabilities,
  jsonResponse,
  midiArrangement,
  publishedGrade,
  producerMxlArrangement,
  producerXmlArrangement,
} from "./fixtures";

describe("Fretsure product flow", () => {
  beforeEach(() => {
    Object.defineProperty(window, "scrollTo", { configurable: true, value: vi.fn() });
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads server-owned controls and submits exact raw score bytes", async () => {
    const user = userEvent.setup();
    const configuredArrangement = structuredClone(arrangement);
    configuredArrangement.options.profile = capabilities.profiles[0];
    configuredArrangement.options.style = "jazz";
    configuredArrangement.options.difficulty_tier = "advanced";
    configuredArrangement.options.technique_profile = "low_position";
    configuredArrangement.stamps.profile_version = capabilities.profiles[0].version;
    configuredArrangement.stamps.profile_fingerprint = capabilities.profiles[0].fingerprint;
    configuredArrangement.playability!.profile_version = capabilities.profiles[0].version;
    configuredArrangement.playability!.profile_fingerprint = capabilities.profiles[0].fingerprint;
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(capabilities))
      .mockResolvedValueOnce(jsonResponse(configuredArrangement));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    expect(await screen.findByText("Oracle ready")).toBeInTheDocument();
    expect(screen.getByText("Plan 7A · editable performance workspace")).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Arrangement style"), "jazz");
    await user.selectOptions(screen.getByLabelText("Difficulty target"), "advanced");
    await user.selectOptions(screen.getByLabelText("Player hand profile"), "small");
    await user.selectOptions(screen.getByLabelText("Technique preference"), "low_position");
    const file = new File(["<score-partwise />"], "song.musicxml", {
      type: "application/xml",
    });
    await user.upload(screen.getByLabelText("Choose a supported symbolic score"), file);
    expect(screen.getByText("song.musicxml")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));

    const resultHeading = await screen.findByRole("heading", { name: "Evidence Song" });
    expect(resultHeading).toHaveFocus();
    expect(window.scrollTo).toHaveBeenCalledWith({ top: 0 });
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toContain("/api/v1/arrangements?");
    expect(String(url)).toContain("filename=song.musicxml");
    expect(String(url)).toContain("engine=offline");
    expect(String(url)).toContain("profile=small");
    expect(String(url)).toContain("style=jazz");
    expect(String(url)).toContain("difficulty_tier=advanced");
    expect(String(url)).toContain("technique_profile=low_position");
    expect(String(url)).toContain("n=1");
    expect(String(url)).toContain("max_iters=0");
    expect(String(url)).toContain("use_critic=false");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(file);
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/vnd.recordare.musicxml+xml",
    );
  });

  it("explains the baseline-first proxy policy instead of exposing repair passes", async () => {
    const user = userEvent.setup();
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValueOnce(jsonResponse(capabilities)));
    render(<App />);

    expect(await screen.findByText("Deterministic baseline")).toBeInTheDocument();
    expect(screen.queryByText("Repair passes")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Engine"), "proxy");

    expect(screen.getByText("Baseline-first · 8 checks")).toBeInTheDocument();
    expect(
      screen.getByText("Agent additions survive only if the full score stays GREEN"),
    ).toBeInTheDocument();
  });

  it("carries the selected difficulty target into generation and the first check", async () => {
    const user = userEvent.setup();
    const tier = capabilities.difficulty_tiers[2];
    const advancedArrangement = structuredClone(arrangement);
    advancedArrangement.options.difficulty_tier = "advanced";
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = String(input);
      if (url === "/api/v1/capabilities") return jsonResponse(capabilities);
      if (url.startsWith("/api/v1/arrangements?")) return jsonResponse(advancedArrangement);
      if (url.startsWith("/api/v1/difficulty/check?")) {
        return jsonResponse({
          api_version: capabilities.api_version,
          service_version: capabilities.service_version,
          status: "checked",
          options: { tier: "advanced", tempo_bpm: 90, beats_per_bar: 4 },
          tab: arrangement.tab,
          tier,
          difficulty: {
            checker_version: "difficulty@0.1.0",
            meets: true,
            playable: "GREEN",
            tier_violations: [],
          },
          published_grade: publishedGrade,
          stamps: {
            difficulty_checker_version: "difficulty@0.1.0",
            published_grade_estimator_version: publishedGrade.model_version,
            published_grade_model_sha256: publishedGrade.model_sha256,
            profile_version: tier.profile.version,
            profile_fingerprint: tier.profile.fingerprint,
          },
        });
      }
      if (url.startsWith("/api/v1/exports/musicxml-tab?")) {
        return new Response("<score-partwise/>", {
          headers: { "content-type": "application/vnd.recordare.musicxml+xml" },
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await screen.findByText("Oracle ready");
    await user.selectOptions(screen.getByLabelText("Difficulty target"), "advanced");
    await user.upload(
      screen.getByLabelText("Choose a supported symbolic score"),
      new File(["score"], "difficulty.musicxml"),
    );
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));

    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes("difficulty_tier=advanced"),
      ),
    ).toBe(true);
    expect(await screen.findByRole("combobox", { name: "Difficulty tier" })).toHaveValue(
      "advanced",
    );
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([input]) =>
          String(input).startsWith("/api/v1/difficulty/check?tier=advanced&"),
        ),
      ).toBe(true),
    );
    expect(await screen.findByText("Advanced · PASS")).toBeInTheDocument();
  });

  it("submits MIDI bytes unchanged and renders unavailable fidelity as N/A", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(capabilities))
      .mockResolvedValueOnce(jsonResponse(midiArrangement));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    expect(await screen.findByText("Oracle ready")).toBeInTheDocument();
    expect(
      screen.getByText(/supported symbolic scores—MusicXML, MXL, and MIDI/),
    ).toBeInTheDocument();
    const bytes = new Uint8Array([
      0x4d, 0x54, 0x68, 0x64, 0x00, 0x00, 0x00, 0x06, 0x00, 0x00, 0x00, 0x01, 0x01, 0xe0,
      0xff, 0x00,
    ]);
    const file = new File([bytes], "MELODY.MIDI", { type: "audio/midi" });
    await user.upload(screen.getByLabelText("Choose a supported symbolic score"), file);
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));

    expect(
      await screen.findByText(
        "Arrangement evidence / playability passed · available fidelity passed (1/3)",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("PASS · 1/3 available")).toBeInTheDocument();
    expect(screen.getAllByText("N/A")).toHaveLength(2);
    expect(screen.getByLabelText("Bass root: N/A").querySelector("progress")).toBeNull();
    expect(screen.getByLabelText("Harmony: N/A").querySelector("progress")).toBeNull();
    expect(screen.getAllByRole("progressbar")).toHaveLength(1);
    expect(screen.getAllByText("midi@0.1.0").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/MIDI_HARMONY_UNPROVIDED/)).toBeInTheDocument();

    const [, init] = fetchMock.mock.calls[1];
    expect(String(fetchMock.mock.calls[1][0])).toContain("filename=MELODY.MIDI");
    expect(init?.body).toBe(file);
    expect(new Uint8Array(await (init?.body as File).arrayBuffer())).toEqual(bytes);
    expect(new Headers(init?.headers).get("content-type")).toBe("audio/midi");
  });

  it.each([
    {
      filename: "musescore-4.7.4.musicxml",
      mediaType: "application/vnd.recordare.musicxml+xml",
      path: "../../tests/fixtures/producers/musescore-4.7.4.musicxml",
      response: producerXmlArrangement,
      sha256: "8aa3f622429dee2dda26ca91c87237470d60c4c02fb996bd9171c9238cd77386",
    },
    {
      filename: "musescore-4.7.4-roundtrip-supported_basic.mxl",
      mediaType: "application/vnd.recordare.musicxml",
      path: "../../tests/fixtures/producers/musescore-4.7.4-roundtrip-supported_basic.mxl",
      response: producerMxlArrangement,
      sha256: "9fbca0cd86c4110a24a51c46a7982859a3d39e1cadfb50d5ad31a479fafe0cc1",
    },
  ])("renders loss-aware evidence for frozen $filename upload", async (producer) => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(capabilities))
      .mockResolvedValueOnce(jsonResponse(producer.response));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await screen.findByText("Oracle ready");
    const frozenBytes = readFileSync(new URL(producer.path, import.meta.url));
    expect(createHash("sha256").update(frozenBytes).digest("hex")).toBe(producer.sha256);
    const file = new File([frozenBytes], producer.filename);
    await user.upload(screen.getByLabelText("Choose a supported symbolic score"), file);
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));

    expect(
      await screen.findByText("key-signature:fifths=0;mode=unprovided"),
    ).toBeInTheDocument();
    expect(screen.getByText(/KEY_MODE_UNPROVIDED/)).toBeInTheDocument();
    expect(screen.queryByText("C major")).not.toBeInTheDocument();
    expect(screen.queryByText("A minor")).not.toBeInTheDocument();
    expect(screen.getAllByText("musicxml@0.4.0").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/fingering-solver@0\.7\.0/)).toHaveTextContent(
      "score-solver@0.4.0",
    );
    expect(screen.getByText(/fingering-solver@0\.7\.0/)).toHaveTextContent(
      "left-hand-ergonomics@0.1.0",
    );

    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toContain(`filename=${producer.filename}`);
    expect(init?.body).toBe(file);
    expect(
      createHash("sha256")
        .update(Buffer.from(await (init?.body as File).arrayBuffer()))
        .digest("hex"),
    ).toBe(producer.sha256);
    expect(new Headers(init?.headers).get("content-type")).toBe(producer.mediaType);
  });

  it("keeps hostile metadata and trace text inert", async () => {
    const user = userEvent.setup();
    const hostile = structuredClone(arrangement);
    hostile.score.title = '<img src=x onerror="globalThis.pwned=true">';
    hostile.trace.steps[1].detail = "<script>globalThis.pwned=true</script>";
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(jsonResponse(capabilities))
        .mockResolvedValueOnce(jsonResponse(hostile)),
    );
    render(<App />);
    await screen.findByText("Oracle ready");
    await user.upload(
      screen.getByLabelText("Choose a supported symbolic score"),
      new File(["score"], "hostile.musicxml"),
    );
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));

    expect(await screen.findByText(hostile.score.title)).toBeInTheDocument();
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("script")).toBeNull();
    expect((globalThis as typeof globalThis & { pwned?: boolean }).pwned).toBeUndefined();
  });

  it("replays typed trace steps without presenting hidden reasoning", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(jsonResponse(capabilities))
        .mockResolvedValueOnce(jsonResponse(arrangement)),
    );
    render(<App />);
    await screen.findByText("Oracle ready");
    await user.upload(
      screen.getByLabelText("Choose a supported symbolic score"),
      new File(["score"], "trace.mxl"),
    );
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));
    expect(await screen.findByText("What changed, and why")).toBeInTheDocument();
    expect(screen.getByText("Replay, not chain-of-thought")).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: /Oracle/ })[0]);
    expect(screen.getByText("Oracle returned GREEN with 0 diagnostics.")).toBeInTheDocument();
  });

  it("opens on the selected checkpoint even when the trace contains a rejected trial", async () => {
    const user = userEvent.setup();
    const withRejectedTrial = structuredClone(arrangement);
    withRejectedTrial.trace.steps[2].seq = 3;
    withRejectedTrial.trace.steps.splice(2, 0, {
      trace_schema_version: "agent-trace@0.3.0",
      seq: 2,
      kind: "EDIT",
      event: "EDIT",
      candidate_index: 0,
      iteration: 1,
      detail: "Rejected an unreachable addition and restored the selected checkpoint.",
      data: {
        policy: "incremental_v1",
        accepted: false,
        solver_called: true,
        reason_code: "NON_GREEN",
        verdict: "RED",
        tab_checkpoint: {
          type: "tab",
          complete: true,
          state: arrangement.tab,
        },
        diagnostics: [],
      },
    });
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(jsonResponse(capabilities))
        .mockResolvedValueOnce(jsonResponse(withRejectedTrial)),
    );
    render(<App />);
    await screen.findByText("Oracle ready");
    await user.upload(
      screen.getByLabelText("Choose a supported symbolic score"),
      new File(["score"], "checkpoint.musicxml"),
    );
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));

    expect(await screen.findByText("Trial 1 rolled back")).toBeInTheDocument();
    expect(screen.getByText("Score, audio and exports share one checkpoint")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Selected checkpoint/ })).toHaveAttribute(
      "aria-current",
      "step",
    );
    expect(screen.queryByText("Trial replay is isolated from exports")).not.toBeInTheDocument();
  });

  it("renders typed service failures and can dismiss them", async () => {
    const user = userEvent.setup();
    const problem = {
      type: "about:blank",
      api_version: "fretsure-api@0.3.0",
      status: 422,
      code: "IMPORT_REJECTED",
      title: "Request semantics rejected",
      detail: "score bytes were rejected by the importer",
      diagnostics: [{ code: "UNSAFE_XML", path: "score", message: "unsafe structure" }],
    };
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(jsonResponse(capabilities))
        .mockResolvedValueOnce(jsonResponse(problem, 422)),
    );
    render(<App />);
    await screen.findByText("Oracle ready");
    await user.upload(
      screen.getByLabelText("Choose a supported symbolic score"),
      new File(["bad"], "bad.musicxml"),
    );
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("IMPORT_REJECTED");
    expect(screen.getByRole("alert")).toHaveTextContent("UNSAFE_XML");
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("rejects unsupported files before making an arrangement request", async () => {
    const user = userEvent.setup({ applyAccept: false });
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValueOnce(jsonResponse(capabilities));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await screen.findByText("Oracle ready");
    await user.upload(
      screen.getByLabelText("Choose a supported symbolic score"),
      new File(["audio"], "song.mp3"),
    );
    expect(screen.getByText(/Choose \.musicxml/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Arrange and verify" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("loads the bundled CC0 example without substituting a fake result", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(capabilities))
      .mockResolvedValueOnce(
        new Response('<score-partwise version="4.0" />', {
          headers: { "content-type": "application/vnd.recordare.musicxml+xml" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    await screen.findByText("Oracle ready");
    await user.click(screen.getByRole("button", { name: "Or load the CC0 example" }));

    expect(await screen.findByText("fretsure-etude.musicxml")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][0]).toBe("/examples/fretsure-etude.musicxml");
    expect(screen.getByRole("button", { name: "Arrange and verify" })).toBeEnabled();
  });

  it("keeps the hidden file input out of keyboard and accessibility navigation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValueOnce(jsonResponse(capabilities)),
    );
    render(<App />);
    await screen.findByText("Oracle ready");

    const input = screen.getByLabelText("Choose a supported symbolic score");
    expect(input).toHaveAttribute("aria-hidden", "true");
    expect(input).toHaveAttribute("tabindex", "-1");
    expect(input).toHaveAttribute("accept", ".musicxml,.xml,.mxl,.mid,.midi");
    expect(screen.getByRole("button", { name: /Drop a symbolic score/ })).toBeVisible();
  });

  it("recovers from a capabilities failure through an explicit retry", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockRejectedValueOnce(new Error("local service unavailable"))
        .mockResolvedValueOnce(jsonResponse(capabilities)),
    );
    render(<App />);

    expect(await screen.findByText("Service unavailable")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("local service unavailable");
    await user.click(screen.getByRole("button", { name: "Retry connection" }));
    expect(await screen.findByText("Oracle ready")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("renders an honest no-fingering result with absent product gates", async () => {
    const user = userEvent.setup();
    const noFingering = structuredClone(arrangement);
    noFingering.status = "no_fingering_within_budget";
    noFingering.editable_target = null;
    noFingering.tab = null;
    noFingering.ascii = null;
    noFingering.playability = null;
    noFingering.faithfulness = null;
    noFingering.trace.steps = [];
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(jsonResponse(capabilities))
        .mockResolvedValueOnce(jsonResponse(noFingering)),
    );
    render(<App />);
    await screen.findByText("Oracle ready");
    await user.upload(
      screen.getByLabelText("Choose a supported symbolic score"),
      new File(["score"], "bounded.musicxml"),
    );
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));

    expect(await screen.findByText("No fingering within the bounded search.")).toBeInTheDocument();
    expect(screen.getByText("Arrangement evidence / bounded search ended")).toBeInTheDocument();
    expect(screen.getAllByText("N/A")).toHaveLength(2);
    expect(screen.getByText("No public trace steps were recorded.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download MusicXML" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download Guitar Pro" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download PDF" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download ASCII TAB" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download MIDI" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Download Tab JSON" })).not.toBeInTheDocument();
  });

  it("distinguishes an Agent-selected candidate from a deterministic baseline", async () => {
    const user = userEvent.setup();
    const agent = structuredClone(arrangement);
    agent.model = { engine: "proxy", model_id: "gpt-5.6-sol" };
    agent.stamps.model_id = "gpt-5.6-sol";
    const baseline = structuredClone(agent);
    const baselineSelection = baseline.trace.steps.find(
      (step) => step.event === "CANDIDATE_SELECTED",
    )!;
    baselineSelection.candidate_index = null;
    baselineSelection.data.winner_candidate_index = null;
    baselineSelection.detail =
      "Selected the deterministic baseline after the model candidates returned no tablature.";
    let arrangementRequest = 0;
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = String(input);
      if (url === "/api/v1/capabilities") return jsonResponse(capabilities);
      if (url.startsWith("/api/v1/arrangements?")) {
        arrangementRequest += 1;
        return jsonResponse(arrangementRequest === 1 ? agent : baseline);
      }
      if (url.startsWith("/api/v1/exports/musicxml-tab?")) {
        return new Response("<score-partwise/>", {
          headers: { "content-type": "application/vnd.recordare.musicxml+xml" },
        });
      }
      if (url === "/api/v1/difficulty/check") {
        return jsonResponse({
          api_version: capabilities.api_version,
          service_version: capabilities.service_version,
          status: "checked",
          options: { tier: "intermediate", tempo_bpm: 90, beats_per_bar: 4 },
          tab: arrangement.tab,
          tier: capabilities.difficulty_tiers[1],
          difficulty: {
            checker_version: "difficulty@0.1.0",
            meets: true,
            playable: "GREEN",
            tier_violations: [],
          },
          published_grade: publishedGrade,
          stamps: {
            difficulty_checker_version: "difficulty@0.1.0",
            published_grade_estimator_version: publishedGrade.model_version,
            published_grade_model_sha256: publishedGrade.model_sha256,
          },
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await screen.findByText("Oracle ready");
    await user.selectOptions(screen.getByRole("combobox", { name: "Engine" }), "proxy");
    await user.upload(
      screen.getByLabelText("Choose a supported symbolic score"),
      new File(["score"], "agent.musicxml"),
    );
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));

    expect(await screen.findByText("Agent candidate 1 selected")).toBeInTheDocument();
    expect(screen.getByText(/gpt-5.6-sol proposed this candidate/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Arrange another" }));
    await user.upload(
      screen.getByLabelText("Choose a supported symbolic score"),
      new File(["score"], "baseline.musicxml"),
    );
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));

    expect(await screen.findByText("No Agent candidate was used")).toBeInTheDocument();
    expect(screen.getByText(/entirely from the deterministic baseline/)).toBeInTheDocument();
  });

  it("inspects verified alternatives and opens only a budget-matched fair A/B", async () => {
    const user = userEvent.setup();
    const alternative = (candidateIndex: number): VerifiedAlternative => ({
      candidate_index: candidateIndex,
      tab: structuredClone(arrangement.tab!),
      ascii: arrangement.ascii!,
      playability: structuredClone(arrangement.playability!),
      faithfulness: structuredClone(arrangement.faithfulness!),
      work: {
        model_calls: 1,
        trial_solver_calls: candidateIndex + 1,
        proposed_additions: 2,
        accepted_additions: 1,
      },
      proposal_status: "LLM_SUCCESS",
      observed_critic: {
        status: null,
        overall: null,
        meaning: "machine_observation_not_human_musicality_evidence",
      },
    });
    const agent = structuredClone(arrangement);
    agent.model = { engine: "proxy", model_id: "gpt-5.6-sol" };
    agent.stamps.model_id = "gpt-5.6-sol";
    agent.options.candidate_count = 2;
    agent.alternatives = [alternative(0), alternative(1)];
    const tier = capabilities.difficulty_tiers[1];
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = String(input);
      if (url === "/api/v1/capabilities") return jsonResponse(capabilities);
      if (url.startsWith("/api/v1/arrangements?")) return jsonResponse(agent);
      if (url === "/api/v1/difficulty/check") {
        return jsonResponse({
          api_version: capabilities.api_version,
          service_version: capabilities.service_version,
          status: "checked",
          options: { tier: "intermediate", tempo_bpm: 90, beats_per_bar: 4 },
          tab: arrangement.tab,
          tier,
          difficulty: {
            checker_version: "difficulty@0.1.0",
            meets: true,
            playable: "GREEN",
            tier_violations: [],
          },
          published_grade: publishedGrade,
          stamps: {
            difficulty_checker_version: "difficulty@0.1.0",
            published_grade_estimator_version: publishedGrade.model_version,
            published_grade_model_sha256: publishedGrade.model_sha256,
            profile_version: tier.profile.version,
            profile_fingerprint: tier.profile.fingerprint,
          },
        });
      }
      if (url.startsWith("/api/v1/exports/musicxml-tab?")) {
        return new Response("<score-partwise/>", {
          headers: { "content-type": "application/vnd.recordare.musicxml+xml" },
        });
      }
      if (url.startsWith("/api/v1/exports/audio?")) {
        return new Response(new Uint8Array(48), {
          headers: { "content-type": "audio/wav" },
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: vi.fn(() => "blob:comparison"),
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: vi.fn(),
    });
    render(<App />);
    await screen.findByText("Oracle ready");
    await user.selectOptions(screen.getByLabelText("Engine"), "proxy");
    await user.upload(
      screen.getByLabelText("Choose a supported symbolic score"),
      new File(["score"], "alternatives.musicxml"),
    );
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));

    expect(await screen.findByText("Verified candidate pool")).toBeInTheDocument();
    expect(screen.getByText(/Breadth used 2 logical model calls/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Inspect candidate 2" }));
    expect(screen.getByText("Candidate 2 under inspection / independently checked")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Demo Lab" }));
    expect(await screen.findByText("Blind A/B audition")).toBeInTheDocument();
    expect(screen.getByText(/same source, model identity, tempo, SoundFont/i)).toBeInTheDocument();
    expect(screen.getByText("NOT_KEPT · benchmark v2")).toBeInTheDocument();
    expect(screen.getByText("READY · 1 call / output")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Choose A" }));
    expect(
      JSON.parse(window.localStorage.getItem("fretsure.feedback.v1")!).entries,
    ).toEqual([
      expect.objectContaining({
        kind: "ab_preference",
        candidate_indices: [0, 1],
      }),
    ]);
  });

  it("closes the offline revision, finger correction, and local feedback loop", async () => {
    const user = userEvent.setup();
    const editable = structuredClone(arrangement);
    editable.score.duration_beats = "16/1";
    editable.tab!.notes = [
      {
        onset: "0/1",
        duration: "1/1",
        string: 4,
        fret: 1,
        left_finger: 1,
        right_finger: "i",
      },
    ];
    const fingerEditedTab = structuredClone(editable.tab!);
    fingerEditedTab.notes[0].left_finger = 2;
    const sectionResponse = {
      api_version: capabilities.api_version,
      service_version: capabilities.service_version,
      status: "accepted" as const,
      selection: {
        start_measure: 2,
        end_measure: 4,
        locked_voices: ["melody" as const],
      },
      options: {
        profile: editable.options.profile,
        style: editable.options.style,
        difficulty_tier: editable.options.difficulty_tier,
        technique_profile: editable.options.technique_profile,
        tempo_bpm: editable.options.tempo_override_bpm,
      },
      model: editable.model,
      editable_target: editable.editable_target!,
      tab: editable.tab!,
      ascii: editable.ascii!,
      playability: editable.playability!,
      faithfulness: editable.faithfulness!,
      revision: {
        schema_version: "section-regeneration@0.1.0" as const,
        proposal_status: "CONSTANT_LLM_BYPASS",
        model_calls: 0 as const,
        reason: null,
      },
      stamps: editable.stamps,
    };
    const fingeringResponse = {
      api_version: capabilities.api_version,
      service_version: capabilities.service_version,
      status: "applied" as const,
      options: {
        profile: editable.options.profile,
        tempo_bpm: 90,
        beats_per_bar: 4,
      },
      tab: fingerEditedTab,
      ascii: editable.ascii!,
      playability: editable.playability!,
      attempted_playability: editable.playability!,
      edit: {
        note_index: 0,
        onset: "0/1",
        string: 4,
        fret: 1,
        before_finger: 1,
        requested_finger: 2,
        reason: null,
      },
      stamps: editable.stamps,
    };
    const tier = capabilities.difficulty_tiers[0];
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === "/api/v1/capabilities") return jsonResponse(capabilities);
      if (url.startsWith("/api/v1/arrangements?")) return jsonResponse(editable);
      if (url.startsWith("/api/v1/arrangements/regenerate-section?")) {
        return jsonResponse(sectionResponse);
      }
      if (url.startsWith("/api/v1/fingering/left-hand?")) {
        return jsonResponse(fingeringResponse);
      }
      if (url.startsWith("/api/v1/difficulty/check?")) {
        return jsonResponse({
          api_version: capabilities.api_version,
          service_version: capabilities.service_version,
          status: "checked",
          options: { tier: "beginner", tempo_bpm: 90, beats_per_bar: 4 },
          tab: JSON.parse(String(init?.body)),
          tier,
          difficulty: {
            checker_version: "difficulty@0.1.0",
            meets: true,
            playable: "GREEN",
            tier_violations: [],
          },
          published_grade: publishedGrade,
          stamps: {
            difficulty_checker_version: "difficulty@0.1.0",
            published_grade_estimator_version: publishedGrade.model_version,
            published_grade_model_sha256: publishedGrade.model_sha256,
            profile_version: tier.profile.version,
            profile_fingerprint: tier.profile.fingerprint,
          },
        });
      }
      if (url.startsWith("/api/v1/exports/musicxml-tab?")) {
        return new Response("<score-partwise/>", {
          headers: { "content-type": "application/vnd.recordare.musicxml+xml" },
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await screen.findByText("Oracle ready");
    await user.upload(
      screen.getByLabelText("Choose a supported symbolic score"),
      new File(["score"], "editable.musicxml"),
    );
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));

    expect(await screen.findByRole("heading", { name: "Regenerate a section" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Edit a finger number" })).toBeInTheDocument();
    expect(screen.getByText("0 model calls")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("From measure"), { target: { value: "2" } });
    expect(screen.getByLabelText("To measure")).toHaveValue(2);
    fireEvent.change(screen.getByLabelText("To measure"), { target: { value: "4" } });
    expect(screen.getByText("Drag across the score · measures 2–4")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Regenerate & verify" }));
    expect(
      await screen.findByText(
        "Accepted: the revised section is now the selected GREEN checkpoint.",
      ),
    ).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Left finger"), "2");
    await user.click(screen.getByRole("button", { name: "Apply finger & recheck" }));
    expect(await screen.findByText("Applied: note 1 now uses finger 2.")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Overall result"), "5");
    await user.click(screen.getByLabelText("natural"));
    await user.type(screen.getByPlaceholderText("What felt natural or awkward?"), "Easy reach");
    await user.click(screen.getByRole("button", { name: "Save feedback" }));
    expect(await screen.findByText("2 saved events")).toBeInTheDocument();
    expect(screen.getByText(/API calls do not retrain the model/)).toBeInTheDocument();
    expect(
      JSON.parse(window.localStorage.getItem("fretsure.feedback.v1")!).entries,
    ).toEqual([
      expect.objectContaining({ kind: "fingering_correction", outcome: "applied" }),
      expect.objectContaining({ kind: "rating", rating: 5, tags: ["natural"] }),
    ]);

    const revisionCall = fetchMock.mock.calls.find(([input]) =>
      String(input).startsWith("/api/v1/arrangements/regenerate-section?"),
    );
    expect(JSON.parse(String(revisionCall?.[1]?.body))).toMatchObject({
      selection: { start_measure: 2, end_measure: 4, locked_voices: ["melody"] },
      options: {
        style: "fingerstyle",
        difficulty_tier: "intermediate",
        technique_profile: "balanced",
      },
    });
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).startsWith(
          "/api/v1/fingering/left-hand?note_index=0&left_finger=2&profile=median",
        ),
      ),
    ).toBe(true);
  });

  it("saves, reopens, and removes canonical results in the local-only library", async () => {
    const user = userEvent.setup();
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(jsonResponse(capabilities))
        .mockResolvedValueOnce(jsonResponse(arrangement)),
    );
    render(<App />);
    await screen.findByText("Oracle ready");
    await user.upload(
      screen.getByLabelText("Choose a supported symbolic score"),
      new File(["score"], "library.musicxml"),
    );
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));
    await screen.findByRole("heading", { name: "Evidence Song" });

    await user.click(screen.getByRole("button", { name: "Save to local library" }));
    expect(screen.getByRole("status")).toHaveTextContent("saved in this browser");
    await user.click(screen.getByRole("button", { name: "Library" }));
    expect(await screen.findByRole("heading", { name: "Personal library" })).toBeInTheDocument();
    expect(screen.getByText(/does not store source bytes/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Evidence Song" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Open evidence" }));
    expect(await screen.findByRole("heading", { name: "Evidence Song" })).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Library" }));
    await user.click(screen.getByRole("button", { name: "Remove" }));
    expect(screen.getByText("No saved canonical results yet.")).toBeInTheDocument();
  });

  it("offers editable, printable, listening, and evidence export choices", async () => {
    const user = userEvent.setup();
    const midiBytes = new Uint8Array([0x4d, 0x54, 0x68, 0x64]);
    const musicxmlBytes = new TextEncoder().encode("<score-partwise/>");
    const guitarProBytes = new TextEncoder().encode("FICHIER GUITAR PRO v5.10");
    const pdfBytes = new TextEncoder().encode("%PDF-1.4");
    const guitarTab = "Six-line tablature (high e to low E):\ne|--0--|\n";
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = String(input);
      if (url === "/api/v1/capabilities") return jsonResponse(capabilities);
      if (url.startsWith("/api/v1/arrangements?")) return jsonResponse(arrangement);
      if (url === "/api/v1/difficulty/check") {
        return jsonResponse({
          api_version: capabilities.api_version,
          service_version: capabilities.service_version,
          status: "checked",
          options: { tier: "intermediate", tempo_bpm: 90, beats_per_bar: 4 },
          tab: arrangement.tab,
          tier: capabilities.difficulty_tiers[1],
          difficulty: {
            checker_version: "difficulty@0.1.0",
            meets: true,
            playable: "GREEN",
            tier_violations: [],
          },
          published_grade: publishedGrade,
          stamps: {
            difficulty_checker_version: "difficulty@0.1.0",
            published_grade_estimator_version: publishedGrade.model_version,
            published_grade_model_sha256: publishedGrade.model_sha256,
          },
        });
      }
      if (url.startsWith("/api/v1/exports/musicxml-tab?")) {
        return new Response(musicxmlBytes, {
          headers: {
            "content-disposition":
              'attachment; filename="fretsure-guitar-tablature.musicxml"',
            "content-type": "application/vnd.recordare.musicxml+xml",
          },
        });
      }
      if (url.startsWith("/api/v1/exports/guitar-pro?")) {
        return new Response(guitarProBytes, {
          headers: {
            "content-disposition": 'attachment; filename="fretsure-guitar-tab.gp5"',
            "content-type": "application/octet-stream",
          },
        });
      }
      if (url.startsWith("/api/v1/exports/pdf-tab?")) {
        return new Response(pdfBytes, {
          headers: {
            "content-disposition": 'attachment; filename="fretsure-guitar-tab.pdf"',
            "content-type": "application/pdf",
          },
        });
      }
      if (url === "/api/v1/exports/tab-text") {
        return new Response(guitarTab, {
          headers: {
            "content-disposition": 'attachment; filename="fretsure-guitar-tablature.txt"',
            "content-type": "text/plain; charset=utf-8",
          },
        });
      }
      if (url.startsWith("/api/v1/exports/midi?")) {
        return new Response(midiBytes, {
          headers: {
            "content-disposition": 'attachment; filename="fretsure-arrangement.mid"',
            "content-type": "audio/midi",
          },
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const createObjectURL = vi.fn(() => "blob:fretsure-download");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectURL,
    });
    const anchorClick = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
    render(<App />);
    await screen.findByText("Oracle ready");
    await user.upload(
      screen.getByLabelText("Choose a supported symbolic score"),
      new File(["score"], "export.musicxml"),
    );
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));

    await user.click(await screen.findByRole("button", { name: "Download Tab JSON" }));
    expect(createObjectURL).toHaveBeenCalledWith(
      expect.objectContaining({ type: "application/json" }),
    );
    expect(anchorClick).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Download MusicXML" }));
    await waitFor(() => expect(anchorClick).toHaveBeenCalledTimes(2));
    const musicXmlCalls = fetchMock.mock.calls.filter(([input]) =>
      String(input).startsWith("/api/v1/exports/musicxml-tab?"),
    );
    expect(musicXmlCalls.length).toBeGreaterThanOrEqual(2);
    expect(String(musicXmlCalls.at(-1)?.[0])).toBe(
      "/api/v1/exports/musicxml-tab?tempo_bpm=90",
    );
    expect(musicXmlCalls.at(-1)?.[1]?.body).toBe(JSON.stringify(arrangement.tab));

    await user.click(screen.getByRole("button", { name: "Download Guitar Pro 5" }));
    await waitFor(() => expect(anchorClick).toHaveBeenCalledTimes(3));
    const guitarProCall = fetchMock.mock.calls.find(([input]) =>
      String(input).startsWith("/api/v1/exports/guitar-pro?"),
    );
    expect(String(guitarProCall?.[0])).toBe("/api/v1/exports/guitar-pro?tempo_bpm=90");
    expect(guitarProCall?.[1]?.body).toBe(JSON.stringify(arrangement.tab));

    await user.click(screen.getByRole("button", { name: "Download Printable PDF" }));
    await waitFor(() => expect(anchorClick).toHaveBeenCalledTimes(4));
    expect(
      fetchMock.mock.calls.some(
        ([input]) => String(input) === "/api/v1/exports/pdf-tab?tempo_bpm=90",
      ),
    ).toBe(true);

    await user.click(screen.getByRole("button", { name: "Download ASCII TAB" }));
    await waitFor(() => expect(anchorClick).toHaveBeenCalledTimes(5));
    expect(
      fetchMock.mock.calls.some(([input]) => String(input) === "/api/v1/exports/tab-text"),
    ).toBe(true);

    await user.click(screen.getByRole("button", { name: "Download MIDI" }));
    await waitFor(() => expect(anchorClick).toHaveBeenCalledTimes(6));
    expect(
      fetchMock.mock.calls.some(
        ([input]) => String(input) === "/api/v1/exports/midi?tempo_bpm=90",
      ),
    ).toBe(true);
    expect(revokeObjectURL).toHaveBeenCalledTimes(6);
  });

  it.each(["AMBER", "RED"] as const)(
    "does not claim complete evidence when playability is %s",
    async (verdict) => {
      const user = userEvent.setup();
      const notGreen = structuredClone(arrangement);
      notGreen.playability!.verdict = verdict;
      notGreen.faithfulness!.passed = true;
      const selection = notGreen.trace.steps.find(
        (step) => step.event === "CANDIDATE_SELECTED",
      )!;
      selection.data.verdict = verdict;
      selection.data.green_certified = false;
      selection.data.playability_gate = "not_passed";
      vi.stubGlobal(
        "fetch",
        vi
          .fn<typeof fetch>()
          .mockResolvedValueOnce(jsonResponse(capabilities))
          .mockResolvedValueOnce(jsonResponse(notGreen)),
      );
      render(<App />);
      await screen.findByText("Oracle ready");
      await user.upload(
        screen.getByLabelText("Choose a supported symbolic score"),
        new File(["score"], `${verdict.toLowerCase()}.musicxml`),
      );
      await user.click(screen.getByRole("button", { name: "Arrange and verify" }));

      expect(
        await screen.findByText("Arrangement evidence / playability needs review"),
      ).toBeInTheDocument();
      expect(screen.queryByText("Arrangement evidence / both gates passed")).not.toBeInTheDocument();
    },
  );

  it("shows localized oracle diagnostics and source warnings as plain evidence", async () => {
    const user = userEvent.setup();
    const warned = structuredClone(arrangement);
    warned.playability!.verdict = "RED";
    const warnedSelection = warned.trace.steps.find(
      (step) => step.event === "CANDIDATE_SELECTED",
    )!;
    warnedSelection.data.verdict = "RED";
    warnedSelection.data.green_certified = false;
    warnedSelection.data.playability_gate = "not_passed";
    warned.playability!.diagnostics = [
      {
        measure: 2,
        beat: "3/2",
        violation_type: "SPAN_LIMIT",
        offending_notes: [1, 2],
        overage: 1.25,
        suggested_relaxations: ["move the upper note"],
      },
    ];
    warned.source.warnings = [
      {
        code: "IGNORED_NOTATION",
        severity: "warning",
        message: "lyrics were not imported",
        location: null,
      },
    ];
    vi.stubGlobal(
      "fetch",
      vi
        .fn<typeof fetch>()
        .mockResolvedValueOnce(jsonResponse(capabilities))
        .mockResolvedValueOnce(jsonResponse(warned)),
    );
    render(<App />);
    await screen.findByText("Oracle ready");
    await user.upload(
      screen.getByLabelText("Choose a supported symbolic score"),
      new File(["score"], "warned.musicxml"),
    );
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));

    expect(await screen.findByText("SPAN LIMIT")).toBeInTheDocument();
    expect(screen.getByText(/Measure 2 · beat 3\/2 · overage 1.25/)).toBeInTheDocument();
    expect(screen.getByText("move the upper note")).toBeInTheDocument();
    expect(screen.getByText(/lyrics were not imported/)).toBeInTheDocument();
  });
});
