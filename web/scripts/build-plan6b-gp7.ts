import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { loadAlphaTabScore, musicXmlToGp7 } from "../src/alphatab";

const root = resolve(process.cwd(), "..");
const directory = resolve(root, "artifacts/plan6b-interoperability");
const source = resolve(directory, "fretsure-acceptance.musicxml");
const destination = resolve(directory, "fretsure-acceptance.gp");
const manifestPath = resolve(directory, "manifest.json");
const gp = musicXmlToGp7(new Uint8Array(readFileSync(source)));
writeFileSync(destination, gp);
const reopened = loadAlphaTabScore(gp);
if (reopened.tracks.length === 0 || reopened.masterBars.length === 0) {
  throw new Error("generated GP7 archive did not reopen through alphaTab");
}
const manifest = JSON.parse(readFileSync(manifestPath, "utf8")) as {
  files: Record<string, unknown>;
};
manifest.files.gp7 = {
  filename: "fretsure-acceptance.gp",
  bytes: gp.byteLength,
  sha256: createHash("sha256").update(gp).digest("hex"),
  structural_check: "PASS",
};
writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`);
console.log(destination);
