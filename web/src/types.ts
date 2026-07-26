export type Verdict = "GREEN" | "AMBER" | "RED";
export type FaithfulnessDimension = "melody" | "bass_root" | "harmony";
export type ScoreFormat = "musicxml" | "mxl" | "midi";
export type DifficultyTierName = "beginner" | "intermediate" | "advanced";
export type ArrangementStyleId = "fingerstyle" | "classical" | "jazz" | "rnb";
export type TechniqueProfileId =
  | "balanced"
  | "avoid_barres"
  | "low_position"
  | "minimize_shifts";
export type VoiceRole = "melody" | "bass" | "harmony";

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
  melody_f1: number | null;
  bass_root_accuracy: number | null;
  harmony_jaccard: number | null;
  evaluated_dimensions: FaithfulnessDimension[];
  unavailable_dimensions: FaithfulnessDimension[];
  passed: boolean;
  checker_version: string;
}

export interface SourceLocation {
  part_id: string | null;
  measure: string | null;
  voice: string | null;
  element: string | null;
  archive_member: string | null;
  track_index: number | null;
  event_index: number | null;
  channel: number | null;
  tick: number | null;
}

export interface SourceEvidence {
  filename: string;
  format: ScoreFormat;
  raw_sha256: string;
  root_member: string | null;
  root_sha256: string;
  container_version: string | null;
  importer_version: string;
  warnings: Array<{
    code: string;
    severity: "warning";
    message: string;
    location: SourceLocation | null;
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
  style: ArrangementStyleId;
  difficulty_tier: DifficultyTierName;
  technique_profile: TechniqueProfileId;
  tuning: number[];
  capo: number;
  candidate_count: number;
  max_repair_iterations: number;
  critic_enabled: boolean;
  tempo_override_bpm: number | null;
  source_tempo_bpm: number;
  effective_tempo_bpm: number;
}

export interface EditableTargetNote {
  onset: string;
  duration: string;
  pitch: number;
  voice: VoiceRole;
}

export interface EditableTarget {
  schema_version: "editable-arrangement-target@0.1.0";
  notes: EditableTargetNote[];
}

export interface CanonicalTabNote {
  onset: string;
  duration: string;
  string: number;
  fret: number;
  left_finger: number;
  right_finger: "p" | "i" | "m" | "a";
  /**
   * Notes sharing a positive group at one onset are one right-hand gesture --
   * a roll or a strum -- rather than that many independent plucks.  Absent on
   * every ordinary pluck, and on every tab produced before gestures existed.
   */
  attack_group?: number;
}

export interface CanonicalTab {
  tuning: number[];
  capo: number;
  notes: CanonicalTabNote[];
}

export interface VerifiedAlternative {
  candidate_index: number;
  tab: CanonicalTab;
  ascii: string;
  playability: PlayabilityResult;
  faithfulness: FaithfulnessResult;
  work: {
    model_calls: number;
    trial_solver_calls: number;
    proposed_additions: number;
    accepted_additions: number;
  };
  proposal_status:
    | "LLM_SUCCESS"
    | "PARSE_VALIDATION_FALLBACK"
    | "CALL_FAILURE_FALLBACK"
    | "CONSTANT_LLM_BYPASS";
  observed_critic: {
    status:
      | "LLM_SUCCESS"
      | "PARSE_VALIDATION_FALLBACK"
      | "CALL_FAILURE_FALLBACK"
      | null;
    overall: number | null;
    meaning: "machine_observation_not_human_musicality_evidence";
  };
}

export interface ArrangementResponse {
  api_version: string;
  service_version: string;
  status: "tab_produced" | "no_fingering_within_budget";
  source: SourceEvidence;
  score: ScoreSummary;
  options: ArrangementOptionsWire;
  model: { model_id: string; engine: "offline" | "proxy" };
  editable_target: EditableTarget | null;
  tab: CanonicalTab | null;
  ascii: string | null;
  playability: PlayabilityResult | null;
  faithfulness: FaithfulnessResult | null;
  alternatives: VerifiedAlternative[];
  trace: PublicTrace;
  stamps: Record<string, string>;
}

export interface EngineCapability {
  id: "offline" | "proxy";
  available: boolean;
  model_id: string;
}

export interface NamedControlCapability<Id extends string> {
  id: Id;
  label: string;
  description: string;
}

export interface DifficultyTierCapability {
  name: DifficultyTierName;
  profile: ProfileIdentity;
  constraints: {
    max_simultaneous: number;
    allow_barre: boolean;
    max_position: number;
    max_shifts_per_bar: number;
  };
}

export interface DifficultyCheckControls {
  tier: DifficultyTierName;
  tempoBpm: number;
  beatsPerBar: number;
}

export interface DifficultyCheckResponse {
  api_version: string;
  service_version: string;
  status: "checked";
  options: {
    tier: DifficultyTierName;
    tempo_bpm: number;
    beats_per_bar: number;
  };
  tab: CanonicalTab;
  tier: DifficultyTierCapability;
  difficulty: {
    checker_version: string;
    meets: boolean;
    playable: Verdict;
    tier_violations: string[];
  };
  published_grade: {
    model_version: string;
    model_sha256: string;
    grade_system: string;
    estimated_grade: number;
    likely_interval: { lower: number; upper: number };
    band: "foundational" | "intermediate" | "advanced";
    confidence: "low";
    burden_percentile: number;
    feature_percentiles: Record<string, number>;
    training_scope: string;
    meaning: "corpus_calibrated_estimate_not_a_playability_guarantee";
  };
  stamps: Record<string, string>;
}

export interface CapabilitiesResponse {
  api_version: string;
  package_version: string;
  service_version: string;
  trace_schema_version: string;
  profile_registry_version: string;
  engines: EngineCapability[];
  profiles: ProfileIdentity[];
  arrangement_styles: Array<NamedControlCapability<ArrangementStyleId>>;
  technique_profiles: Array<NamedControlCapability<TechniqueProfileId>>;
  difficulty_tiers: DifficultyTierCapability[];
  inputs: {
    score_suffixes: string[];
    score_input: {
      router_version: string;
      format_importers: Record<ScoreFormat, string>;
    };
    max_xml_bytes?: number;
    max_mxl_bytes?: number;
    [key: string]: unknown;
  };
  controls: {
    arrange: {
      defaults: {
        profile: string;
        style: ArrangementStyleId;
        difficulty_tier: DifficultyTierName;
        technique_profile: TechniqueProfileId;
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
    difficulty: {
      defaults: {
        tier: DifficultyTierName;
        tempo_bpm: number;
        beats_per_bar: number;
      };
      tier: { values: DifficultyTierName[] };
      tempo_bpm: { min: number; max: number };
      beats_per_bar: { min: number; max: number };
    };
    [key: string]: unknown;
  };
  implemented: string[];
  deferred: string[];
  audio: {
    available: boolean;
    renderer: "FluidSynth";
    runtime_version: string | null;
    export_version: string;
    sample_rate_hz: number;
    media_type: "audio/wav";
  };
  stamps: Record<string, string>;
  [key: string]: unknown;
}

export interface ArrangeControls {
  engine: "offline" | "proxy";
  profile: string;
  style: ArrangementStyleId;
  difficultyTier: DifficultyTierName;
  techniqueProfile: TechniqueProfileId;
  n: number;
  maxIters: number;
  useCritic: boolean;
  tempoBpm: number | null;
}

export interface SectionSelection {
  startMeasure: number;
  endMeasure: number;
  lockedVoices: VoiceRole[];
}

export interface SectionRegenerationResponse {
  api_version: string;
  service_version: string;
  status: "accepted" | "preserved" | "unchanged";
  selection: {
    start_measure: number;
    end_measure: number;
    locked_voices: VoiceRole[];
  };
  options: {
    profile: ProfileIdentity;
    style: ArrangementStyleId;
    difficulty_tier: DifficultyTierName;
    technique_profile: TechniqueProfileId;
    tempo_bpm: number | null;
  };
  model: { model_id: string; engine: "offline" | "proxy" };
  editable_target: EditableTarget;
  tab: CanonicalTab;
  ascii: string;
  playability: PlayabilityResult;
  faithfulness: FaithfulnessResult;
  revision: {
    schema_version: "section-regeneration@0.1.0";
    proposal_status: string | null;
    model_calls: 0 | 1;
    reason: string | null;
  };
  stamps: Record<string, string>;
}

export interface FingeringEditResponse {
  api_version: string;
  service_version: string;
  status: "applied" | "rejected" | "unchanged";
  options: {
    profile: ProfileIdentity;
    tempo_bpm: number;
    beats_per_bar: number;
  };
  tab: CanonicalTab;
  ascii: string;
  playability: PlayabilityResult;
  attempted_playability: PlayabilityResult;
  edit: {
    note_index: number;
    onset: string;
    string: number;
    fret: number;
    before_finger: number;
    requested_finger: number;
    reason: string | null;
  };
  stamps: Record<string, string>;
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
