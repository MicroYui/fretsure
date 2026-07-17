/// <reference types="node" />

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../src/App";
import {
  arrangement,
  capabilities,
  jsonResponse,
  producerMxlArrangement,
  producerXmlArrangement,
} from "./fixtures";

describe("Fretsure product flow", () => {
  beforeEach(() => {
    Object.defineProperty(window, "scrollTo", { configurable: true, value: vi.fn() });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads server-owned controls and submits exact raw score bytes", async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(capabilities))
      .mockResolvedValueOnce(jsonResponse(arrangement));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    expect(await screen.findByText("Oracle ready")).toBeInTheDocument();
    const file = new File(["<score-partwise />"], "song.musicxml", {
      type: "application/xml",
    });
    await user.upload(screen.getByLabelText("Choose a MusicXML or MXL score"), file);
    expect(screen.getByText("song.musicxml")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));

    const resultHeading = await screen.findByRole("heading", { name: "Evidence Song" });
    expect(resultHeading).toHaveFocus();
    expect(window.scrollTo).toHaveBeenCalledWith({ top: 0 });
    const [url, init] = fetchMock.mock.calls[1];
    expect(String(url)).toContain("/api/v1/arrangements?");
    expect(String(url)).toContain("filename=song.musicxml");
    expect(String(url)).toContain("engine=offline");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(file);
    expect(new Headers(init?.headers).get("content-type")).toBe(
      "application/vnd.recordare.musicxml+xml",
    );
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
    await user.upload(screen.getByLabelText("Choose a MusicXML or MXL score"), file);
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));

    expect(
      await screen.findByText("key-signature:fifths=0;mode=unprovided"),
    ).toBeInTheDocument();
    expect(screen.getByText(/KEY_MODE_UNPROVIDED/)).toBeInTheDocument();
    expect(screen.queryByText("C major")).not.toBeInTheDocument();
    expect(screen.queryByText("A minor")).not.toBeInTheDocument();
    expect(screen.getAllByText("musicxml@0.3.0").length).toBeGreaterThanOrEqual(1);

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
      screen.getByLabelText("Choose a MusicXML or MXL score"),
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
      screen.getByLabelText("Choose a MusicXML or MXL score"),
      new File(["score"], "trace.mxl"),
    );
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));
    expect(await screen.findByText("What changed, and why")).toBeInTheDocument();
    expect(screen.getByText("Replay, not chain-of-thought")).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: /Oracle/ })[0]);
    expect(screen.getByText("Oracle returned GREEN with 0 diagnostics.")).toBeInTheDocument();
  });

  it("renders typed service failures and can dismiss them", async () => {
    const user = userEvent.setup();
    const problem = {
      type: "about:blank",
      api_version: "fretsure-api@0.1.0",
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
      screen.getByLabelText("Choose a MusicXML or MXL score"),
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
      screen.getByLabelText("Choose a MusicXML or MXL score"),
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

    const input = screen.getByLabelText("Choose a MusicXML or MXL score");
    expect(input).toHaveAttribute("aria-hidden", "true");
    expect(input).toHaveAttribute("tabindex", "-1");
    expect(input).toHaveAttribute("accept", ".musicxml,.xml,.mxl");
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
      screen.getByLabelText("Choose a MusicXML or MXL score"),
      new File(["score"], "bounded.musicxml"),
    );
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));

    expect(await screen.findByText("No fingering within the bounded search.")).toBeInTheDocument();
    expect(screen.getByText("Arrangement evidence / bounded search ended")).toBeInTheDocument();
    expect(screen.getAllByText("N/A")).toHaveLength(2);
    expect(screen.getByText("No public trace steps were recorded.")).toBeInTheDocument();
  });

  it.each(["AMBER", "RED"] as const)(
    "does not claim both gates passed when playability is %s",
    async (verdict) => {
      const user = userEvent.setup();
      const notGreen = structuredClone(arrangement);
      notGreen.playability!.verdict = verdict;
      notGreen.faithfulness!.passed = true;
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
        screen.getByLabelText("Choose a MusicXML or MXL score"),
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
        severity: "WARNING",
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
      screen.getByLabelText("Choose a MusicXML or MXL score"),
      new File(["score"], "warned.musicxml"),
    );
    await user.click(screen.getByRole("button", { name: "Arrange and verify" }));

    expect(await screen.findByText("SPAN LIMIT")).toBeInTheDocument();
    expect(screen.getByText(/Measure 2 · beat 3\/2 · overage 1.25/)).toBeInTheDocument();
    expect(screen.getByText("move the upper note")).toBeInTheDocument();
    expect(screen.getByText(/lyrics were not imported/)).toBeInTheDocument();
  });
});
