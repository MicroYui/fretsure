export type Verdict = "GREEN" | "AMBER" | "RED";

export interface TraceStep {
  trace_schema_version: string;
  seq: number;
  kind:
    | "PLAN"
    | "PROPOSE"
    | "SOLVE"
    | "ORACLE"
    | "REASON"
    | "EDIT"
    | "RECHECK"
    | "SELECT";
  event: string;
  candidate_index: number | null;
  iteration: number | null;
  detail: string;
  data: Record<string, unknown>;
}

export interface PublicTrace {
  schema_version: string;
  steps: TraceStep[];
}

export interface ProfileIdentity {
  name: string;
  version: string;
  fingerprint: string;
  calibration_status: string;
}

export interface PlayabilityDiagnostic {
  measure: number;
  beat: string;
  violation_type: string;
  offending_notes: number[];
  overage: number;
  suggested_relaxations: string[];
}

export interface PlayabilityResult {
  verdict: Verdict;
  meaning: "versioned_model_relative_not_a_real_player_guarantee" | string;
  diagnostics: PlayabilityDiagnostic[];
  checker_version: string;
  profile_version: string;
  profile_fingerprint: string;
  input_schema_version: string;
}

export interface FaithfulnessResult {
  melody_f1: number;
  bass_root_accuracy: number;
  harmony_jaccard: number;
  passed: boolean;
  checker_version: string;
}

export interface SourceEvidence {
  filename: string | null;
  format: string | null;
  raw_sha256: string;
  root_member: string | null;
  root_sha256: string;
  container_version: string | null;
  importer_version: string;
  warnings: Array<{
    code: string;
    severity: string;
    message: string;
    location: Record<string, string | null> | null;
  }>;
}

export interface ScoreSummary {
  title: string;
  key: string;
  time_signature: { numerator: number; denominator: number };
  source_tempo_bpm: number;
  duration_beats: string | null;
  note_count: number;
  voice_counts: Record<"melody" | "bass" | "harmony", number>;
  chord_count: number;
  source_description: string;
  rights_or_license: string;
}

export interface ArrangementOptionsWire {
  profile: ProfileIdentity;
  tuning: number[];
  capo: number;
  candidate_count: number;
  max_repair_iterations: number;
  critic_enabled: boolean;
  tempo_override_bpm: number | null;
  source_tempo_bpm: number;
  effective_tempo_bpm: number;
}

export interface ArrangementResponse {
  api_version: string;
  service_version: string;
  status: "tab_produced" | "no_fingering_within_budget";
  source: SourceEvidence;
  score: ScoreSummary;
  options: ArrangementOptionsWire;
  model: { model_id: string; engine: "offline" | "proxy" };
  tab: Record<string, unknown> | null;
  ascii: string | null;
  playability: PlayabilityResult | null;
  faithfulness: FaithfulnessResult | null;
  trace: PublicTrace;
  stamps: Record<string, string>;
}

export interface EngineCapability {
  id: "offline" | "proxy";
  available: boolean;
  model_id: string;
}

export interface CapabilitiesResponse {
  api_version: string;
  package_version: string;
  service_version: string;
  engines: EngineCapability[];
  profiles: ProfileIdentity[];
  inputs: {
    score_suffixes: string[];
    max_xml_bytes?: number;
    max_mxl_bytes?: number;
    [key: string]: unknown;
  };
  controls: {
    arrange: {
      defaults: {
        profile: string;
        n: number;
        max_iters: number;
        use_critic: boolean;
        tempo_bpm: number | null;
        engine?: "offline" | "proxy";
      };
      n: { min: number; max: number };
      max_iters: { min: number; max: number };
      tempo_bpm: { min: number; max: number; nullable: true };
    };
    [key: string]: unknown;
  };
  implemented: string[];
  deferred: string[];
  stamps: Record<string, string>;
  [key: string]: unknown;
}

export interface ArrangeControls {
  engine: "offline" | "proxy";
  profile: string;
  n: number;
  maxIters: number;
  useCritic: boolean;
  tempoBpm: number | null;
}

export interface APIProblem {
  type: string;
  api_version: string;
  status: number;
  code: string;
  title: string;
  detail: string;
  diagnostics?: Array<{ code: string; path?: string; message?: string }>;
}
