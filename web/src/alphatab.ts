import { exporter, importer, type model } from "@coderline/alphatab";

/** Load one notation file through alphaTab's public importer registry. */
export function loadAlphaTabScore(data: Uint8Array): model.Score {
  return importer.ScoreLoader.loadScoreFromBytes(data);
}

/** Export a loaded score as a native Guitar Pro 7+ `.gp` archive. */
export function exportGp7Score(score: model.Score): Uint8Array {
  return new exporter.Gp7Exporter().export(score);
}

/** Convert the backend's canonical MusicXML TAB into Guitar Pro 7+ bytes. */
export function musicXmlToGp7(data: Uint8Array): Uint8Array {
  return exportGp7Score(loadAlphaTabScore(data));
}

export async function musicXmlBlobToGp7(blob: Blob): Promise<Blob> {
  const source = new Uint8Array(await blob.arrayBuffer());
  const gp = musicXmlToGp7(source);
  const owned = new Uint8Array(gp.byteLength);
  owned.set(gp);
  return new Blob([owned.buffer], { type: "application/octet-stream" });
}
