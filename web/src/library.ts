import { isArrangement } from "./api";
import type { ArrangementResponse } from "./types";

export const LOCAL_LIBRARY_SCHEMA_VERSION = "fretsure-local-library@0.2.0";
const STORAGE_KEY = "fretsure.local-library.v2";

export interface LocalLibraryEntry {
  id: string;
  saved_at: string;
  result: ArrangementResponse;
}

interface LocalLibraryDocument {
  schema_version: typeof LOCAL_LIBRARY_SCHEMA_VERSION;
  entries: LocalLibraryEntry[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function entryId(result: ArrangementResponse): string {
  const checkpoint = JSON.stringify(result.tab);
  let hash = 0x811c9dc5;
  for (let index = 0; index < checkpoint.length; index += 1) {
    hash = Math.imul(hash ^ checkpoint.charCodeAt(index), 0x01000193);
  }
  return `${result.source.raw_sha256.slice(0, 16)}-${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

export function readLocalLibrary(storage: Storage = window.localStorage): LocalLibraryEntry[] {
  try {
    const encoded = storage.getItem(STORAGE_KEY);
    if (encoded === null) return [];
    const document: unknown = JSON.parse(encoded);
    if (
      !isRecord(document) ||
      document.schema_version !== LOCAL_LIBRARY_SCHEMA_VERSION ||
      !Array.isArray(document.entries)
    ) {
      return [];
    }
    return document.entries.filter(
      (entry): entry is LocalLibraryEntry =>
        isRecord(entry) &&
        typeof entry.id === "string" &&
        entry.id.length > 0 &&
        typeof entry.saved_at === "string" &&
        !Number.isNaN(Date.parse(entry.saved_at)) &&
        isArrangement(entry.result),
    );
  } catch {
    return [];
  }
}

export function saveLocalResult(
  result: ArrangementResponse,
  storage: Storage = window.localStorage,
  now: Date = new Date(),
): LocalLibraryEntry[] {
  const id = entryId(result);
  const entry: LocalLibraryEntry = {
    id,
    saved_at: now.toISOString(),
    result,
  };
  const entries = [entry, ...readLocalLibrary(storage).filter((item) => item.id !== id)];
  const document: LocalLibraryDocument = {
    schema_version: LOCAL_LIBRARY_SCHEMA_VERSION,
    entries,
  };
  storage.setItem(STORAGE_KEY, JSON.stringify(document));
  return entries;
}

export function removeLocalResult(
  id: string,
  storage: Storage = window.localStorage,
): LocalLibraryEntry[] {
  const entries = readLocalLibrary(storage).filter((entry) => entry.id !== id);
  const document: LocalLibraryDocument = {
    schema_version: LOCAL_LIBRARY_SCHEMA_VERSION,
    entries,
  };
  storage.setItem(STORAGE_KEY, JSON.stringify(document));
  return entries;
}
