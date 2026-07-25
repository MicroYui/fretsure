import type {
  ArrangementStyleId,
  DifficultyTierName,
  TechniqueProfileId,
} from "./types";

export const FEEDBACK_SCHEMA_VERSION = "fretsure-feedback@0.1.0";
const STORAGE_KEY = "fretsure.feedback.v1";

interface FeedbackContext {
  source_sha256: string;
  model_id: string;
  player_profile: string;
  style: ArrangementStyleId;
  difficulty_tier: DifficultyTierName;
  technique_profile: TechniqueProfileId;
}

interface FeedbackBase extends FeedbackContext {
  id: string;
  created_at: string;
}

export type FeedbackEvent =
  | (FeedbackBase & {
      kind: "rating";
      rating: number;
      tags: string[];
      note: string;
    })
  | (FeedbackBase & {
      kind: "ab_preference";
      candidate_indices: [number, number];
      preferred_candidate_index: number;
    })
  | (FeedbackBase & {
      kind: "fingering_correction";
      note_index: number;
      before_finger: number;
      requested_finger: number;
      outcome: "applied" | "rejected" | "unchanged";
    });

export interface FeedbackDocument {
  schema_version: typeof FEEDBACK_SCHEMA_VERSION;
  identity: "anonymous";
  meaning: "local_preference_evidence_not_model_training";
  entries: FeedbackEvent[];
}

type NewFeedbackEvent =
  | (FeedbackContext & Omit<Extract<FeedbackEvent, { kind: "rating" }>, keyof FeedbackBase>)
  | (FeedbackContext &
      Omit<Extract<FeedbackEvent, { kind: "ab_preference" }>, keyof FeedbackBase>)
  | (FeedbackContext &
      Omit<Extract<FeedbackEvent, { kind: "fingering_correction" }>, keyof FeedbackBase>);

function emptyDocument(): FeedbackDocument {
  return {
    schema_version: FEEDBACK_SCHEMA_VERSION,
    identity: "anonymous",
    meaning: "local_preference_evidence_not_model_training",
    entries: [],
  };
}

function isFeedbackDocument(value: unknown): value is FeedbackDocument {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const document = value as Record<string, unknown>;
  return (
    document.schema_version === FEEDBACK_SCHEMA_VERSION &&
    document.identity === "anonymous" &&
    document.meaning === "local_preference_evidence_not_model_training" &&
    Array.isArray(document.entries)
  );
}

export function readFeedback(storage: Storage = window.localStorage): FeedbackDocument {
  try {
    const encoded = storage.getItem(STORAGE_KEY);
    if (encoded === null) return emptyDocument();
    const parsed: unknown = JSON.parse(encoded);
    return isFeedbackDocument(parsed) ? parsed : emptyDocument();
  } catch {
    return emptyDocument();
  }
}

export function appendFeedback(
  event: NewFeedbackEvent,
  storage: Storage = window.localStorage,
  now: Date = new Date(),
): FeedbackDocument {
  const document = readFeedback(storage);
  const entry = {
    ...event,
    id: `${now.getTime()}-${document.entries.length}`,
    created_at: now.toISOString(),
  } as FeedbackEvent;
  const next: FeedbackDocument = {
    ...document,
    entries: [...document.entries, entry].slice(-1000),
  };
  storage.setItem(STORAGE_KEY, JSON.stringify(next));
  return next;
}

export function clearFeedback(storage: Storage = window.localStorage): FeedbackDocument {
  const document = emptyDocument();
  storage.setItem(STORAGE_KEY, JSON.stringify(document));
  return document;
}

export function feedbackDownload(document: FeedbackDocument): {
  blob: Blob;
  filename: string;
} {
  return {
    blob: new Blob([JSON.stringify(document, null, 2)], {
      type: "application/json",
    }),
    filename: "fretsure-anonymous-feedback.json",
  };
}
