import { beforeEach, describe, expect, it } from "vitest";

import {
  LOCAL_LIBRARY_SCHEMA_VERSION,
  readLocalLibrary,
  removeLocalResult,
  saveLocalResult,
} from "../src/library";
import { arrangement } from "./fixtures";

describe("local canonical result library", () => {
  beforeEach(() => window.localStorage.clear());

  it("stores only the typed result envelope and replaces the same checkpoint", () => {
    const first = saveLocalResult(
      arrangement,
      window.localStorage,
      new Date("2026-07-24T01:00:00.000Z"),
    );
    const second = saveLocalResult(
      arrangement,
      window.localStorage,
      new Date("2026-07-24T02:00:00.000Z"),
    );

    expect(first).toHaveLength(1);
    expect(second).toHaveLength(1);
    expect(second[0].saved_at).toBe("2026-07-24T02:00:00.000Z");
    expect(second[0].result).toEqual(arrangement);
    const stored = JSON.parse(window.localStorage.getItem("fretsure.local-library.v2")!);
    expect(stored.schema_version).toBe(LOCAL_LIBRARY_SCHEMA_VERSION);
    expect(stored.entries[0]).not.toHaveProperty("source_bytes");
  });

  it("ignores incompatible local documents and removes entries explicitly", () => {
    window.localStorage.setItem(
      "fretsure.local-library.v2",
      JSON.stringify({ schema_version: "old", entries: [{ hidden: true }] }),
    );
    expect(readLocalLibrary()).toEqual([]);

    const [entry] = saveLocalResult(arrangement);
    expect(removeLocalResult(entry.id)).toEqual([]);
    expect(readLocalLibrary()).toEqual([]);
  });
});
