import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { importer } from "@coderline/alphatab";
import { describe, expect, it } from "vitest";

import { loadAlphaTabScore, musicXmlToGp7 } from "../src/alphatab";

const EXAMPLE = new Uint8Array(
  readFileSync(resolve(process.cwd(), "public/examples/fretsure-etude.musicxml")),
);

describe("alphaTab interoperability", () => {
  it("loads the bundled MusicXML and writes a reopenable native GP7 file", () => {
    const score = loadAlphaTabScore(EXAMPLE);

    expect(score.tracks.length).toBeGreaterThan(0);
    const gp = musicXmlToGp7(EXAMPLE);
    expect(gp.byteLength).toBeGreaterThan(100);

    const reopened = importer.ScoreLoader.loadScoreFromBytes(gp);
    expect(reopened.tracks.length).toBe(score.tracks.length);
    expect(reopened.masterBars.length).toBe(score.masterBars.length);
  });
});
