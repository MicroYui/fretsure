import type {
  APIProblem,
  ArrangeControls,
  ArrangementStyleId,
  ArrangementResponse,
  CanonicalTab,
  CapabilitiesResponse,
  DifficultyCheckControls,
  DifficultyCheckResponse,
  DifficultyTierCapability,
  DifficultyTierName,
  EditableTarget,
  FaithfulnessDimension,
  FingeringEditResponse,
  ScoreFormat,
  SectionRegenerationResponse,
  SectionSelection,
  TechniqueProfileId,
  VerifiedAlternative,
} from "./types";

export class FretsureAPIError extends Error {
  readonly problem: APIProblem;

  constructor(problem: APIProblem) {
    super(problem.detail);
    this.name = "FretsureAPIError";
    this.problem = problem;
  }
}

const CURRENT_API_VERSION = "fretsure-api@0.3.0";
const CURRENT_PACKAGE_VERSION = "0.6.0";
const CURRENT_SERVICE_VERSION = "fretsure-service@0.3.0";
const CURRENT_SCORE_INPUT_VERSION = "score-input@0.1.0";
const CURRENT_FIDELITY_VERSION = "fidelity@0.3.0";
const CURRENT_DIFFICULTY_VERSION = "difficulty@0.1.0";
const CURRENT_TRACE_VERSION = "agent-trace@0.3.0";
const CURRENT_ORACLE_VERSION = "oracle@0.8.0";
const CURRENT_FINGERING_SOLVER_VERSION = "fingering-solver@0.7.0";
const CURRENT_SCORE_SOLVER_VERSION = "score-solver@0.4.0";
const CURRENT_LEFT_HAND_MODEL_VERSION = "left-hand-ergonomics@0.1.0";
const CURRENT_PUBLISHED_FINGERING_RANKER_VERSION = "published-fingering-ranker@0.1.0";
const CURRENT_PUBLISHED_FINGERING_FEATURE_SCHEMA = "published-fingering-features@0.1.0";
const CURRENT_PUBLISHED_FINGERING_MODEL_SHA256 =
  "10bd1f9c2751417c5ef3a5f360da5696f736cc24db838857b9d2dd058b6cfed0";
const CURRENT_PUBLISHED_GRADE_VERSION = "published-grade-estimator@0.1.0";
const CURRENT_PUBLISHED_GRADE_MODEL_SHA256 =
  "a3bb39aaf5f881513ed0141d20b3e3776c8b38357dd11351681c38701dddf16a";
const CURRENT_PROFILE_REGISTRY_VERSION = "profile-registry@0.2.0";
const CURRENT_STYLE_REGISTRY_VERSION = "arrangement-style-registry@0.2.0";
const CURRENT_STYLE_PROFILE_VERSION = "guitarset-style-profiles@0.1.0";
const CURRENT_STYLE_PROFILE_SHA256 =
  "c1a57bb1aa4599594db83f5fb9074e96b53be83a03d1e306e38ea5cae7df342d";
const CURRENT_TECHNIQUE_REGISTRY_VERSION = "technique-profile-registry@0.1.0";
const CURRENT_EDITABLE_TARGET_VERSION = "editable-arrangement-target@0.1.0";
const CURRENT_SECTION_REGENERATION_VERSION = "section-regeneration@0.1.0";
const DIFFICULTY_TIERS = ["beginner", "intermediate", "advanced"] as const;
const ARRANGEMENT_STYLES = ["fingerstyle", "classical", "jazz", "rnb"] as const;
const TECHNIQUE_PROFILES = [
  "balanced",
  "avoid_barres",
  "low_position",
  "minimize_shifts",
] as const;
const SCORE_SUFFIXES = [".musicxml", ".xml", ".mxl", ".mid", ".midi"] as const;
const FORMAT_IMPORTERS: Readonly<Record<ScoreFormat, string>> = {
  musicxml: "musicxml@0.4.0",
  mxl: "musicxml@0.4.0",
  midi: "midi@0.1.0",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isNonEmptyString(value: unknown): value is string {
  return isString(value) && value.length > 0;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isIntegerAtLeast(value: unknown, minimum: number): value is number {
  return isFiniteNumber(value) && Number.isInteger(value) && value >= minimum;
}

function isPositiveNumber(value: unknown): value is number {
  return isFiniteNumber(value) && value > 0;
}

function isUnitInterval(value: unknown): value is number {
  return isFiniteNumber(value) && value >= 0 && value <= 1;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || isString(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(isString);
}

function isStringRecord(value: unknown): value is Record<string, string> {
  return isRecord(value) && Object.values(value).every(isString);
}

function isPercentileRecord(value: unknown): value is Record<string, number> {
  return (
    isRecord(value) &&
    Object.values(value).every(
      (item) => isFiniteNumber(item) && item >= 0 && item <= 100,
    )
  );
}

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value);
  return actual.length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

function hasKeys(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[],
): boolean {
  const actual = Object.keys(value);
  return (
    actual.every((key) => required.includes(key) || optional.includes(key)) &&
    required.every((key) => Object.hasOwn(value, key))
  );
}

function hasUniqueStrings(values: readonly string[]): boolean {
  return new Set(values).size === values.length;
}

function arraysEqual<T>(left: readonly T[], right: readonly T[]): boolean {
  return left.length === right.length && left.every((item, index) => item === right[index]);
}

function isScoreInputCapability(
  value: unknown,
): value is CapabilitiesResponse["inputs"]["score_input"] {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["router_version", "format_importers"]) ||
    value.router_version !== CURRENT_SCORE_INPUT_VERSION
  ) {
    return false;
  }
  const registry = value.format_importers;
  if (!isRecord(registry) || !hasExactKeys(registry, Object.keys(FORMAT_IMPORTERS))) {
    return false;
  }
  return Object.entries(FORMAT_IMPORTERS).every(
    ([format, importer]) => registry[format] === importer,
  );
}

function isProfileIdentity(
  value: unknown,
): value is CapabilitiesResponse["profiles"][number] {
  return (
    isRecord(value) &&
    isNonEmptyString(value.name) &&
    isNonEmptyString(value.version) &&
    isNonEmptyString(value.fingerprint) &&
    isNonEmptyString(value.calibration_status)
  );
}

function isEngineCapability(
  value: unknown,
): value is CapabilitiesResponse["engines"][number] {
  return (
    isRecord(value) &&
    (value.id === "offline" || value.id === "proxy") &&
    typeof value.available === "boolean" &&
    isNonEmptyString(value.model_id)
  );
}

function isIntegerRange(
  value: unknown,
  minimum: number,
): value is { min: number; max: number } {
  return (
    isRecord(value) &&
    isIntegerAtLeast(value.min, minimum) &&
    isIntegerAtLeast(value.max, minimum) &&
    value.min <= value.max
  );
}

function isPositiveNumberRange(
  value: unknown,
): value is { min: number; max: number; nullable: true } {
  return (
    isRecord(value) &&
    isPositiveNumber(value.min) &&
    isPositiveNumber(value.max) &&
    value.min <= value.max &&
    value.nullable === true
  );
}

function isAudioCapability(
  value: unknown,
): value is CapabilitiesResponse["audio"] {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      "available",
      "renderer",
      "runtime_version",
      "export_version",
      "sample_rate_hz",
      "media_type",
    ]) &&
    typeof value.available === "boolean" &&
    value.renderer === "FluidSynth" &&
    (value.runtime_version === null || isNonEmptyString(value.runtime_version)) &&
    isNonEmptyString(value.export_version) &&
    isIntegerAtLeast(value.sample_rate_hz, 1) &&
    value.media_type === "audio/wav"
  );
}

function isDifficultyTierName(value: unknown): value is DifficultyTierName {
  return value === "beginner" || value === "intermediate" || value === "advanced";
}

function isArrangementStyle(value: unknown): value is ArrangementStyleId {
  return ARRANGEMENT_STYLES.some((style) => style === value);
}

function isTechniqueProfile(value: unknown): value is TechniqueProfileId {
  return TECHNIQUE_PROFILES.some((profile) => profile === value);
}

function isNamedControlCapability(
  value: unknown,
  idGuard: (id: unknown) => boolean,
): boolean {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["id", "label", "description"]) &&
    idGuard(value.id) &&
    isNonEmptyString(value.label) &&
    isNonEmptyString(value.description)
  );
}

function isDifficultyTierCapability(value: unknown): value is DifficultyTierCapability {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["name", "profile", "constraints"]) &&
    isDifficultyTierName(value.name) &&
    isProfileIdentity(value.profile) &&
    value.profile.name === value.name &&
    isRecord(value.constraints) &&
    hasExactKeys(value.constraints, [
      "max_simultaneous",
      "allow_barre",
      "max_position",
      "max_shifts_per_bar",
    ]) &&
    isIntegerAtLeast(value.constraints.max_simultaneous, 1) &&
    value.constraints.max_simultaneous <= 6 &&
    typeof value.constraints.allow_barre === "boolean" &&
    isIntegerAtLeast(value.constraints.max_position, 0) &&
    isIntegerAtLeast(value.constraints.max_shifts_per_bar, 0)
  );
}

function isDifficultyControls(
  value: unknown,
): value is CapabilitiesResponse["controls"]["difficulty"] {
  return (
    isRecord(value) &&
    isRecord(value.defaults) &&
    isDifficultyTierName(value.defaults.tier) &&
    isPositiveNumber(value.defaults.tempo_bpm) &&
    isIntegerAtLeast(value.defaults.beats_per_bar, 1) &&
    isRecord(value.tier) &&
    isStringArray(value.tier.values) &&
    arraysEqual(value.tier.values, DIFFICULTY_TIERS) &&
    isRecord(value.tempo_bpm) &&
    isPositiveNumber(value.tempo_bpm.min) &&
    isPositiveNumber(value.tempo_bpm.max) &&
    value.tempo_bpm.min <= value.tempo_bpm.max &&
    isIntegerRange(value.beats_per_bar, 1)
  );
}

function isCapabilities(value: unknown): value is CapabilitiesResponse {
  if (
    !isRecord(value) ||
    value.api_version !== CURRENT_API_VERSION ||
    value.package_version !== CURRENT_PACKAGE_VERSION ||
    value.service_version !== CURRENT_SERVICE_VERSION ||
    value.trace_schema_version !== CURRENT_TRACE_VERSION ||
    value.profile_registry_version !== CURRENT_PROFILE_REGISTRY_VERSION ||
    !Array.isArray(value.engines) ||
    value.engines.length !== 2 ||
    !value.engines.every(isEngineCapability) ||
    !Array.isArray(value.profiles) ||
    value.profiles.length === 0 ||
    !value.profiles.every(isProfileIdentity) ||
    !Array.isArray(value.arrangement_styles) ||
    !value.arrangement_styles.every((item) =>
      isNamedControlCapability(item, isArrangementStyle),
    ) ||
    !Array.isArray(value.technique_profiles) ||
    !value.technique_profiles.every((item) =>
      isNamedControlCapability(item, isTechniqueProfile),
    ) ||
    !Array.isArray(value.difficulty_tiers) ||
    value.difficulty_tiers.length !== DIFFICULTY_TIERS.length ||
    !value.difficulty_tiers.every(isDifficultyTierCapability) ||
    !isRecord(value.inputs) ||
    !isStringArray(value.inputs.score_suffixes) ||
    !arraysEqual(value.inputs.score_suffixes, SCORE_SUFFIXES) ||
    !isScoreInputCapability(value.inputs.score_input) ||
    !isRecord(value.controls) ||
    !isRecord(value.controls.arrange) ||
    !isRecord(value.controls.arrange.defaults) ||
    !isIntegerRange(value.controls.arrange.n, 1) ||
    !isIntegerRange(value.controls.arrange.max_iters, 0) ||
    !isPositiveNumberRange(value.controls.arrange.tempo_bpm) ||
    !isDifficultyControls(value.controls.difficulty) ||
    !isStringArray(value.implemented) ||
    !isStringArray(value.deferred) ||
    !isAudioCapability(value.audio) ||
    !isStringRecord(value.stamps)
  ) {
    return false;
  }

  const engines = value.engines as CapabilitiesResponse["engines"];
  const profiles = value.profiles as CapabilitiesResponse["profiles"];
  const engineIds = engines.map((engine) => engine.id);
  const profileNames = profiles.map((profile) => profile.name);
  const styleIds = value.arrangement_styles.map((style) => style.id);
  const techniqueIds = value.technique_profiles.map((profile) => profile.id);
  const tierNames = value.difficulty_tiers.map((tier) => tier.name);
  const defaults = value.controls.arrange.defaults;
  const stamps = value.stamps;
  const candidateRange = value.controls.arrange.n as { min: number; max: number };
  const repairRange = value.controls.arrange.max_iters as { min: number; max: number };
  const tempoRange = value.controls.arrange.tempo_bpm as { min: number; max: number };
  if (
    !hasUniqueStrings(engineIds) ||
    !engineIds.includes("offline") ||
    !engineIds.includes("proxy") ||
    !hasUniqueStrings(profileNames) ||
    !arraysEqual(styleIds, ARRANGEMENT_STYLES) ||
    !arraysEqual(techniqueIds, TECHNIQUE_PROFILES) ||
    !arraysEqual(tierNames, DIFFICULTY_TIERS) ||
    !hasUniqueStrings(value.inputs.score_suffixes) ||
    !requiredStampMatches(stamps, "package_version", CURRENT_PACKAGE_VERSION) ||
    !requiredStampMatches(stamps, "service_version", CURRENT_SERVICE_VERSION) ||
    !requiredStampMatches(
      stamps,
      "profile_registry_version",
      CURRENT_PROFILE_REGISTRY_VERSION,
    ) ||
    !requiredStampMatches(
      stamps,
      "arrangement_style_registry_version",
      CURRENT_STYLE_REGISTRY_VERSION,
    ) ||
    !requiredStampMatches(
      stamps,
      "arrangement_style_profile_version",
      CURRENT_STYLE_PROFILE_VERSION,
    ) ||
    !requiredStampMatches(
      stamps,
      "arrangement_style_profile_sha256",
      CURRENT_STYLE_PROFILE_SHA256,
    ) ||
    !requiredStampMatches(
      stamps,
      "technique_profile_registry_version",
      CURRENT_TECHNIQUE_REGISTRY_VERSION,
    ) ||
    !requiredStampMatches(stamps, "score_input_version", CURRENT_SCORE_INPUT_VERSION) ||
    !requiredStampMatches(stamps, "oracle_checker_version", CURRENT_ORACLE_VERSION) ||
    !requiredStampMatches(stamps, "fidelity_checker_version", CURRENT_FIDELITY_VERSION) ||
    !requiredStampMatches(stamps, "difficulty_checker_version", CURRENT_DIFFICULTY_VERSION) ||
    !requiredStampMatches(
      stamps,
      "fingering_solver_version",
      CURRENT_FINGERING_SOLVER_VERSION,
    ) ||
    !requiredStampMatches(stamps, "score_solver_version", CURRENT_SCORE_SOLVER_VERSION) ||
    !requiredStampMatches(stamps, "left_hand_model_version", CURRENT_LEFT_HAND_MODEL_VERSION) ||
    !requiredStampMatches(
      stamps,
      "published_fingering_ranker_version",
      CURRENT_PUBLISHED_FINGERING_RANKER_VERSION,
    ) ||
    !requiredStampMatches(
      stamps,
      "published_fingering_model_sha256",
      CURRENT_PUBLISHED_FINGERING_MODEL_SHA256,
    ) ||
    !requiredStampMatches(
      stamps,
      "published_fingering_feature_schema",
      CURRENT_PUBLISHED_FINGERING_FEATURE_SCHEMA,
    ) ||
    !requiredStampMatches(stamps, "trace_schema_version", CURRENT_TRACE_VERSION) ||
    !isNonEmptyString(defaults.profile) ||
    !profileNames.includes(defaults.profile) ||
    !isArrangementStyle(defaults.style) ||
    !styleIds.includes(defaults.style) ||
    !isDifficultyTierName(defaults.difficulty_tier) ||
    !tierNames.includes(defaults.difficulty_tier) ||
    !isTechniqueProfile(defaults.technique_profile) ||
    !techniqueIds.includes(defaults.technique_profile) ||
    !isIntegerAtLeast(defaults.n, 1) ||
    defaults.n < candidateRange.min ||
    defaults.n > candidateRange.max ||
    !isIntegerAtLeast(defaults.max_iters, 0) ||
    defaults.max_iters < repairRange.min ||
    defaults.max_iters > repairRange.max ||
    typeof defaults.use_critic !== "boolean" ||
    !(
      defaults.tempo_bpm === null ||
      (isPositiveNumber(defaults.tempo_bpm) &&
        defaults.tempo_bpm >= tempoRange.min &&
        defaults.tempo_bpm <= tempoRange.max)
    ) ||
    !(
      defaults.engine === undefined ||
      defaults.engine === "offline" ||
      defaults.engine === "proxy"
    )
  ) {
    return false;
  }
  return true;
}

const SHA256 = /^[0-9a-f]{64}$/i;
const FRACTION = /^(?:0|[1-9][0-9]*)\/[1-9][0-9]*$/;
const TRACE_KINDS = new Set([
  "PLAN",
  "PROPOSE",
  "SOLVE",
  "ORACLE",
  "REASON",
  "EDIT",
  "RECHECK",
  "SELECT",
]);
const TRACE_EVENT_KINDS: Readonly<Record<string, string>> = {
  PLAN: "PLAN",
  PROPOSE: "PROPOSE",
  SOLVE: "SOLVE",
  ORACLE: "ORACLE",
  REASON: "REASON",
  EDIT: "EDIT",
  RECHECK: "RECHECK",
  SELECT: "SELECT",
  PIPELINE_CONFIGURED: "PLAN",
  CANDIDATE_PROPOSED: "PROPOSE",
  CANDIDATE_FINISHED: "SOLVE",
  SOLVER_RETURNED_TAB: "SOLVE",
  SOLVER_RETURNED_NO_TAB: "SOLVE",
  PLAYABILITY_CHECKED: "ORACLE",
  TIER_CHECKED: "ORACLE",
  REPAIR_EDIT_PROPOSED: "REASON",
  MODEL_CALL_FAILED: "REASON",
  EDIT_APPLIED: "EDIT",
  EDIT_REJECTED: "EDIT",
  MODEL_EDIT_INVALID: "EDIT",
  RECHECK_STARTED: "RECHECK",
  CANDIDATE_SELECTED: "SELECT",
  NO_CANDIDATE_SELECTED: "SELECT",
};
const TRACE_KEYS = [
  "trace_schema_version",
  "seq",
  "kind",
  "event",
  "candidate_index",
  "iteration",
  "detail",
  "data",
] as const;
const ARRANGEMENT_KEYS = [
  "api_version",
  "service_version",
  "status",
  "source",
  "score",
  "options",
  "model",
  "editable_target",
  "tab",
  "ascii",
  "playability",
  "faithfulness",
  "alternatives",
  "trace",
  "stamps",
] as const;
const DIFFICULTY_RESPONSE_KEYS = [
  "api_version",
  "service_version",
  "status",
  "options",
  "tab",
  "tier",
  "difficulty",
  "published_grade",
  "stamps",
] as const;
const DIFFICULTY_OPTIONS_KEYS = ["tier", "tempo_bpm", "beats_per_bar"] as const;
const DIFFICULTY_RESULT_KEYS = [
  "checker_version",
  "meets",
  "playable",
  "tier_violations",
] as const;
const PUBLISHED_GRADE_KEYS = [
  "model_version",
  "model_sha256",
  "grade_system",
  "estimated_grade",
  "likely_interval",
  "band",
  "confidence",
  "burden_percentile",
  "feature_percentiles",
  "training_scope",
  "meaning",
] as const;
const MODEL_KEYS = ["model_id", "engine"] as const;
const TAB_KEYS = ["tuning", "capo", "notes"] as const;
const TAB_NOTE_KEYS = [
  "onset",
  "duration",
  "string",
  "fret",
  "left_finger",
  "right_finger",
] as const;
// Written only when a note belongs to a gesture, so an ungrouped tab carries
// exactly the keys it always did.
const OPTIONAL_TAB_NOTE_KEYS = ["attack_group"] as const;
const FAITHFULNESS_KEYS = [
  "melody_f1",
  "bass_root_accuracy",
  "harmony_jaccard",
  "evaluated_dimensions",
  "unavailable_dimensions",
  "passed",
  "checker_version",
] as const;
const ALTERNATIVE_KEYS = [
  "candidate_index",
  "tab",
  "ascii",
  "playability",
  "faithfulness",
  "work",
  "proposal_status",
  "observed_critic",
] as const;
const ALTERNATIVE_WORK_KEYS = [
  "model_calls",
  "trial_solver_calls",
  "proposed_additions",
  "accepted_additions",
] as const;
const OBSERVED_CRITIC_KEYS = ["status", "overall", "meaning"] as const;
const CANDIDATE_SELECTED_KEYS = [
  "winner_candidate_index",
  "candidates_considered",
  "verdict",
  "green_certified",
  "playability_gate",
  "faithfulness_passed",
  "ranking_melody_recall",
  "ranking_bass_preserved",
  "ranking_harmony_jaccard",
  "melody_f1",
  "bass_root_accuracy",
  "harmony_jaccard",
  "evaluated_dimensions",
  "unavailable_dimensions",
  "critic_status",
  "critic_overall",
] as const;
const FAITHFULNESS_DIMENSIONS = ["melody", "bass_root", "harmony"] as const;
const FAITHFULNESS_THRESHOLDS: Readonly<Record<FaithfulnessDimension, number>> = {
  melody: 0.9,
  bass_root: 0.7,
  harmony: 0.6,
};
const SOURCE_KEYS = [
  "filename",
  "format",
  "raw_sha256",
  "root_member",
  "root_sha256",
  "container_version",
  "importer_version",
  "warnings",
] as const;
const WARNING_KEYS = ["code", "severity", "message", "location"] as const;
const IMPORT_LOCATION_KEYS = [
  "part_id",
  "measure",
  "voice",
  "element",
  "archive_member",
  "track_index",
  "event_index",
  "channel",
  "tick",
] as const;
const REQUIRED_ARRANGEMENT_STAMPS = [
  "package_version",
  "service_version",
  "score_input_version",
  "profile_registry_version",
  "arrangement_style_registry_version",
  "arrangement_style_profile_version",
  "arrangement_style_profile_sha256",
  "technique_profile_registry_version",
  "profile_version",
  "profile_fingerprint",
  "oracle_checker_version",
  "oracle_input_schema_version",
  "fidelity_checker_version",
  "fingering_solver_version",
  "score_solver_version",
  "left_hand_model_version",
  "published_fingering_ranker_version",
  "published_fingering_model_sha256",
  "published_fingering_feature_schema",
  "target_input_schema_version",
  "editable_target_schema_version",
  "section_regeneration_version",
  "trace_schema_version",
  "importer_version",
  "model_id",
] as const;

function isImportLocation(value: unknown): boolean {
  if (value === null) return true;
  return (
    isRecord(value) &&
    hasExactKeys(value, IMPORT_LOCATION_KEYS) &&
    isNullableString(value.part_id) &&
    isNullableString(value.measure) &&
    isNullableString(value.voice) &&
    isNullableString(value.element) &&
    isNullableString(value.archive_member) &&
    (value.track_index === null || isIntegerAtLeast(value.track_index, 0)) &&
    (value.event_index === null || isIntegerAtLeast(value.event_index, 0)) &&
    (value.channel === null ||
      (isIntegerAtLeast(value.channel, 1) && value.channel <= 16)) &&
    (value.tick === null || isIntegerAtLeast(value.tick, 0))
  );
}

function isSourceEvidence(value: unknown): boolean {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, SOURCE_KEYS) ||
    !isNonEmptyString(value.filename) ||
    !(value.format === "musicxml" || value.format === "mxl" || value.format === "midi") ||
    !isString(value.raw_sha256) ||
    !SHA256.test(value.raw_sha256) ||
    !isNullableString(value.root_member) ||
    !isString(value.root_sha256) ||
    !SHA256.test(value.root_sha256) ||
    !isNullableString(value.container_version) ||
    !isNonEmptyString(value.importer_version) ||
    !Array.isArray(value.warnings) ||
    !value.warnings.every(
      (warning) =>
        isRecord(warning) &&
        hasExactKeys(warning, WARNING_KEYS) &&
        isNonEmptyString(warning.code) &&
        warning.severity === "warning" &&
        isString(warning.message) &&
        isImportLocation(warning.location),
    )
  ) {
    return false;
  }
  if (value.format === "mxl") {
    return isNonEmptyString(value.root_member) && isNonEmptyString(value.container_version);
  }
  if (value.root_member !== null || value.container_version !== null) return false;
  return value.format !== "midi" || value.raw_sha256 === value.root_sha256;
}

function isScoreSummary(value: unknown): boolean {
  return (
    isRecord(value) &&
    isString(value.title) &&
    isString(value.key) &&
    isRecord(value.time_signature) &&
    isIntegerAtLeast(value.time_signature.numerator, 1) &&
    isIntegerAtLeast(value.time_signature.denominator, 1) &&
    isPositiveNumber(value.source_tempo_bpm) &&
    (value.duration_beats === null ||
      (isString(value.duration_beats) && FRACTION.test(value.duration_beats))) &&
    isIntegerAtLeast(value.note_count, 0) &&
    isRecord(value.voice_counts) &&
    isIntegerAtLeast(value.voice_counts.melody, 0) &&
    isIntegerAtLeast(value.voice_counts.bass, 0) &&
    isIntegerAtLeast(value.voice_counts.harmony, 0) &&
    isIntegerAtLeast(value.chord_count, 0) &&
    isString(value.source_description) &&
    isString(value.rights_or_license)
  );
}

function isArrangementOptions(value: unknown): boolean {
  return (
    isRecord(value) &&
    isProfileIdentity(value.profile) &&
    isArrangementStyle(value.style) &&
    isDifficultyTierName(value.difficulty_tier) &&
    isTechniqueProfile(value.technique_profile) &&
    Array.isArray(value.tuning) &&
    value.tuning.length > 0 &&
    value.tuning.every((pitch) => isIntegerAtLeast(pitch, 0)) &&
    isIntegerAtLeast(value.capo, 0) &&
    isIntegerAtLeast(value.candidate_count, 1) &&
    isIntegerAtLeast(value.max_repair_iterations, 0) &&
    typeof value.critic_enabled === "boolean" &&
    (value.tempo_override_bpm === null || isPositiveNumber(value.tempo_override_bpm)) &&
    isPositiveNumber(value.source_tempo_bpm) &&
    isPositiveNumber(value.effective_tempo_bpm)
  );
}

function isEditableTarget(value: unknown): value is EditableTarget {
  return (
    isRecord(value) &&
    hasExactKeys(value, ["schema_version", "notes"]) &&
    value.schema_version === CURRENT_EDITABLE_TARGET_VERSION &&
    Array.isArray(value.notes) &&
    value.notes.every(
      (note) =>
        isRecord(note) &&
        hasExactKeys(note, ["onset", "duration", "pitch", "voice"]) &&
        isString(note.onset) &&
        FRACTION.test(note.onset) &&
        isString(note.duration) &&
        FRACTION.test(note.duration) &&
        note.duration !== "0/1" &&
        isIntegerAtLeast(note.pitch, 0) &&
        note.pitch <= 127 &&
        (note.voice === "melody" || note.voice === "bass" || note.voice === "harmony"),
    )
  );
}

function isCanonicalTab(value: unknown): value is CanonicalTab {
  return (
    isRecord(value) &&
    hasExactKeys(value, TAB_KEYS) &&
    Array.isArray(value.tuning) &&
    value.tuning.length === 6 &&
    value.tuning.every((pitch) => isIntegerAtLeast(pitch, 0)) &&
    isIntegerAtLeast(value.capo, 0) &&
    Array.isArray(value.notes) &&
    value.notes.every(
      (note) =>
        isRecord(note) &&
        hasKeys(note, TAB_NOTE_KEYS, OPTIONAL_TAB_NOTE_KEYS) &&
        isString(note.onset) &&
        FRACTION.test(note.onset) &&
        isString(note.duration) &&
        FRACTION.test(note.duration) &&
        note.duration !== "0/1" &&
        isIntegerAtLeast(note.string, 0) &&
        note.string <= 5 &&
        isIntegerAtLeast(note.fret, 0) &&
        isIntegerAtLeast(note.left_finger, 0) &&
        note.left_finger <= 4 &&
        (note.right_finger === "p" ||
          note.right_finger === "i" ||
          note.right_finger === "m" ||
          note.right_finger === "a") &&
        (!Object.hasOwn(note, "attack_group") || isIntegerAtLeast(note.attack_group, 0)),
    )
  );
}

function isPlayabilityDiagnostic(value: unknown): boolean {
  return (
    isRecord(value) &&
    isIntegerAtLeast(value.measure, 1) &&
    isString(value.beat) &&
    FRACTION.test(value.beat) &&
    isNonEmptyString(value.violation_type) &&
    Array.isArray(value.offending_notes) &&
    value.offending_notes.every((index) => isIntegerAtLeast(index, 0)) &&
    isFiniteNumber(value.overage) &&
    value.overage >= 0 &&
    isStringArray(value.suggested_relaxations)
  );
}

function isPlayability(value: unknown): boolean {
  return (
    isRecord(value) &&
    (value.verdict === "GREEN" || value.verdict === "AMBER" || value.verdict === "RED") &&
    isNonEmptyString(value.meaning) &&
    Array.isArray(value.diagnostics) &&
    value.diagnostics.every(isPlayabilityDiagnostic) &&
    isNonEmptyString(value.checker_version) &&
    isNonEmptyString(value.profile_version) &&
    isNonEmptyString(value.profile_fingerprint) &&
    isNonEmptyString(value.input_schema_version)
  );
}

function isFaithfulness(value: unknown): boolean {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, FAITHFULNESS_KEYS) ||
    !(value.melody_f1 === null || isUnitInterval(value.melody_f1)) ||
    !(value.bass_root_accuracy === null || isUnitInterval(value.bass_root_accuracy)) ||
    !(value.harmony_jaccard === null || isUnitInterval(value.harmony_jaccard)) ||
    !isStringArray(value.evaluated_dimensions) ||
    !isStringArray(value.unavailable_dimensions) ||
    typeof value.passed !== "boolean" ||
    value.checker_version !== CURRENT_FIDELITY_VERSION
  ) {
    return false;
  }

  const scores: Readonly<Record<FaithfulnessDimension, number | null>> = {
    melody: value.melody_f1,
    bass_root: value.bass_root_accuracy,
    harmony: value.harmony_jaccard,
  };
  const evaluated = FAITHFULNESS_DIMENSIONS.filter((dimension) => scores[dimension] !== null);
  const unavailable = FAITHFULNESS_DIMENSIONS.filter((dimension) => scores[dimension] === null);
  const expectedPassed =
    evaluated.length > 0 &&
    evaluated.every((dimension) => {
      const score = scores[dimension];
      return score !== null && score >= FAITHFULNESS_THRESHOLDS[dimension];
    });
  return (
    arraysEqual(value.evaluated_dimensions, evaluated) &&
    arraysEqual(value.unavailable_dimensions, unavailable) &&
    value.passed === expectedPassed
  );
}

export function isVerifiedAlternative(value: unknown): value is VerifiedAlternative {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ALTERNATIVE_KEYS) ||
    !isIntegerAtLeast(value.candidate_index, 0) ||
    !isCanonicalTab(value.tab) ||
    !isNonEmptyString(value.ascii) ||
    !isPlayability(value.playability) ||
    (value.playability as Record<string, unknown>).verdict !== "GREEN" ||
    !isFaithfulness(value.faithfulness)
  ) {
    return false;
  }
  const work = value.work;
  if (
    !isRecord(work) ||
    !hasExactKeys(work, ALTERNATIVE_WORK_KEYS) ||
    !isIntegerAtLeast(work.model_calls, 0) ||
    !isIntegerAtLeast(work.trial_solver_calls, 0) ||
    !isIntegerAtLeast(work.proposed_additions, 0) ||
    !isIntegerAtLeast(work.accepted_additions, 0) ||
    work.accepted_additions > work.proposed_additions ||
    !(
      value.proposal_status === "LLM_SUCCESS" ||
      value.proposal_status === "PARSE_VALIDATION_FALLBACK" ||
      value.proposal_status === "CALL_FAILURE_FALLBACK" ||
      value.proposal_status === "CONSTANT_LLM_BYPASS"
    )
  ) {
    return false;
  }
  const critic = value.observed_critic;
  if (
    !isRecord(critic) ||
    !hasExactKeys(critic, OBSERVED_CRITIC_KEYS) ||
    critic.meaning !== "machine_observation_not_human_musicality_evidence"
  ) {
    return false;
  }
  if (critic.status === null) return critic.overall === null;
  return (
    (critic.status === "LLM_SUCCESS" ||
      critic.status === "PARSE_VALIDATION_FALLBACK" ||
      critic.status === "CALL_FAILURE_FALLBACK") &&
    isUnitInterval(critic.overall)
  );
}

function isPublicTrace(value: unknown): boolean {
  if (
    !isRecord(value) ||
    value.schema_version !== CURRENT_TRACE_VERSION ||
    !Array.isArray(value.steps)
  ) {
    return false;
  }
  return value.steps.every(
    (step, index) =>
      isRecord(step) &&
      hasExactKeys(step, TRACE_KEYS) &&
      step.trace_schema_version === value.schema_version &&
      step.seq === index &&
      isString(step.kind) &&
      TRACE_KINDS.has(step.kind) &&
      isNonEmptyString(step.event) &&
      TRACE_EVENT_KINDS[step.event] === step.kind &&
      (step.candidate_index === null || isIntegerAtLeast(step.candidate_index, 0)) &&
      (step.iteration === null || isIntegerAtLeast(step.iteration, 0)) &&
      isString(step.detail) &&
      isRecord(step.data),
  );
}

function candidateSelectionMatchesGates(
  trace: ArrangementResponse["trace"],
  playability: Record<string, unknown>,
  faithfulness: Record<string, unknown>,
): boolean {
  const selections = trace.steps.filter((step) => step.event === "CANDIDATE_SELECTED");
  if (selections.length !== 1) return false;

  const selection = selections[0];
  const data = selection.data;
  const winner = data.winner_candidate_index;
  if (
    !hasExactKeys(data, CANDIDATE_SELECTED_KEYS) ||
    (winner !== null && !isIntegerAtLeast(winner, 0)) ||
    !isIntegerAtLeast(data.candidates_considered, 1) ||
    (winner !== null && winner >= data.candidates_considered) ||
    selection.candidate_index !== winner ||
    selection.iteration !== null ||
    (data.verdict !== "GREEN" && data.verdict !== "AMBER" && data.verdict !== "RED") ||
    data.verdict !== playability.verdict ||
    typeof data.green_certified !== "boolean" ||
    data.green_certified !== (data.verdict === "GREEN") ||
    (data.playability_gate !== "passed" && data.playability_gate !== "not_passed") ||
    (data.playability_gate === "passed") !== (data.verdict === "GREEN") ||
    typeof data.faithfulness_passed !== "boolean" ||
    data.faithfulness_passed !== faithfulness.passed ||
    !isUnitInterval(data.ranking_melody_recall) ||
    !isUnitInterval(data.ranking_bass_preserved) ||
    !isUnitInterval(data.ranking_harmony_jaccard) ||
    !Object.is(data.melody_f1, faithfulness.melody_f1) ||
    !Object.is(data.bass_root_accuracy, faithfulness.bass_root_accuracy) ||
    !Object.is(data.harmony_jaccard, faithfulness.harmony_jaccard) ||
    !isStringArray(data.evaluated_dimensions) ||
    !isStringArray(data.unavailable_dimensions) ||
    !isStringArray(faithfulness.evaluated_dimensions) ||
    !isStringArray(faithfulness.unavailable_dimensions) ||
    !arraysEqual(data.evaluated_dimensions, faithfulness.evaluated_dimensions) ||
    !arraysEqual(data.unavailable_dimensions, faithfulness.unavailable_dimensions) ||
    (data.critic_status !== "SCORED" && data.critic_status !== "NOT_RUN") ||
    (data.critic_status === "SCORED"
      ? !isUnitInterval(data.critic_overall)
      : data.critic_overall !== null)
  ) {
    return false;
  }
  return true;
}

function requiredStampMatches(
  stamps: Record<string, string>,
  key: string,
  expected: string,
): boolean {
  return isNonEmptyString(stamps[key]) && stamps[key] === expected;
}

export function isArrangement(value: unknown): value is ArrangementResponse {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ARRANGEMENT_KEYS) ||
    value.api_version !== CURRENT_API_VERSION ||
    (value.status !== "tab_produced" && value.status !== "no_fingering_within_budget") ||
    value.service_version !== CURRENT_SERVICE_VERSION ||
    !isSourceEvidence(value.source) ||
    !isScoreSummary(value.score) ||
    !isArrangementOptions(value.options) ||
    !isRecord(value.model) ||
    !hasExactKeys(value.model, MODEL_KEYS) ||
    !isNonEmptyString(value.model.model_id) ||
    (value.model.engine !== "offline" && value.model.engine !== "proxy") ||
    !Array.isArray(value.alternatives) ||
    !value.alternatives.every(isVerifiedAlternative) ||
    !isPublicTrace(value.trace) ||
    !isStringRecord(value.stamps)
  ) {
    return false;
  }

  const produced = value.status === "tab_produced";
  const productOutputsAgree = produced
    ? isEditableTarget(value.editable_target) &&
      isCanonicalTab(value.tab) &&
      isNonEmptyString(value.ascii) &&
      isPlayability(value.playability) &&
      isFaithfulness(value.faithfulness)
    : value.editable_target === null &&
      value.tab === null &&
      value.ascii === null &&
      value.playability === null &&
      value.faithfulness === null;
  if (!productOutputsAgree) return false;

  const candidateIndices = value.alternatives.map((item) => item.candidate_index);
  if (
    value.alternatives.length >
      ((value.options as Record<string, unknown>).candidate_count as number) ||
    new Set(candidateIndices).size !== candidateIndices.length ||
    (!produced && value.alternatives.length > 0)
  ) {
    return false;
  }

  const source = value.source as Record<string, unknown>;
  const options = value.options as Record<string, unknown>;
  const profile = options.profile as Record<string, unknown>;
  const model = value.model;
  const trace = value.trace as Record<string, unknown>;
  const publicTrace = value.trace as ArrangementResponse["trace"];
  const stamps = value.stamps;
  const locallyRevised =
    stamps.local_checkpoint_origin === CURRENT_SECTION_REGENERATION_VERSION ||
    stamps.local_checkpoint_origin === "left-hand-fingering-edit@0.1.0";
  const sourceFormat = source.format as ScoreFormat;
  if (
    !REQUIRED_ARRANGEMENT_STAMPS.every((key) => isNonEmptyString(stamps[key])) ||
    !requiredStampMatches(stamps, "package_version", CURRENT_PACKAGE_VERSION) ||
    !requiredStampMatches(stamps, "service_version", CURRENT_SERVICE_VERSION) ||
    !requiredStampMatches(
      stamps,
      "profile_registry_version",
      CURRENT_PROFILE_REGISTRY_VERSION,
    ) ||
    !requiredStampMatches(
      stamps,
      "arrangement_style_registry_version",
      CURRENT_STYLE_REGISTRY_VERSION,
    ) ||
    !requiredStampMatches(
      stamps,
      "arrangement_style_profile_version",
      CURRENT_STYLE_PROFILE_VERSION,
    ) ||
    !requiredStampMatches(
      stamps,
      "arrangement_style_profile_sha256",
      CURRENT_STYLE_PROFILE_SHA256,
    ) ||
    !requiredStampMatches(
      stamps,
      "technique_profile_registry_version",
      CURRENT_TECHNIQUE_REGISTRY_VERSION,
    ) ||
    !requiredStampMatches(stamps, "score_input_version", CURRENT_SCORE_INPUT_VERSION) ||
    !requiredStampMatches(stamps, "oracle_checker_version", CURRENT_ORACLE_VERSION) ||
    !requiredStampMatches(stamps, "fidelity_checker_version", CURRENT_FIDELITY_VERSION) ||
    !requiredStampMatches(
      stamps,
      "fingering_solver_version",
      CURRENT_FINGERING_SOLVER_VERSION,
    ) ||
    !requiredStampMatches(stamps, "score_solver_version", CURRENT_SCORE_SOLVER_VERSION) ||
    !requiredStampMatches(stamps, "left_hand_model_version", CURRENT_LEFT_HAND_MODEL_VERSION) ||
    !requiredStampMatches(
      stamps,
      "published_fingering_ranker_version",
      CURRENT_PUBLISHED_FINGERING_RANKER_VERSION,
    ) ||
    !requiredStampMatches(
      stamps,
      "published_fingering_model_sha256",
      CURRENT_PUBLISHED_FINGERING_MODEL_SHA256,
    ) ||
    !requiredStampMatches(
      stamps,
      "published_fingering_feature_schema",
      CURRENT_PUBLISHED_FINGERING_FEATURE_SCHEMA,
    ) ||
    !requiredStampMatches(stamps, "trace_schema_version", CURRENT_TRACE_VERSION) ||
    !requiredStampMatches(
      stamps,
      "editable_target_schema_version",
      CURRENT_EDITABLE_TARGET_VERSION,
    ) ||
    !requiredStampMatches(
      stamps,
      "section_regeneration_version",
      CURRENT_SECTION_REGENERATION_VERSION,
    ) ||
    trace.schema_version !== CURRENT_TRACE_VERSION ||
    !requiredStampMatches(stamps, "importer_version", source.importer_version as string) ||
    source.importer_version !== FORMAT_IMPORTERS[sourceFormat] ||
    !requiredStampMatches(stamps, "model_id", model.model_id as string) ||
    !requiredStampMatches(stamps, "profile_version", profile.version as string) ||
    !requiredStampMatches(stamps, "profile_fingerprint", profile.fingerprint as string)
  ) {
    return false;
  }
  if (produced) {
    const playability = value.playability as Record<string, unknown>;
    const faithfulness = value.faithfulness as Record<string, unknown>;
    if (
      playability.profile_version !== profile.version ||
      playability.profile_fingerprint !== profile.fingerprint ||
      playability.checker_version !== stamps.oracle_checker_version ||
      playability.input_schema_version !== stamps.oracle_input_schema_version ||
      faithfulness.checker_version !== stamps.fidelity_checker_version ||
      (!locallyRevised && !candidateSelectionMatchesGates(publicTrace, playability, faithfulness))
    ) {
      return false;
    }
  } else if (publicTrace.steps.some((step) => step.event === "CANDIDATE_SELECTED")) {
    return false;
  }
  return true;
}

function isDifficultyCheck(value: unknown): value is DifficultyCheckResponse {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, DIFFICULTY_RESPONSE_KEYS) ||
    value.api_version !== CURRENT_API_VERSION ||
    value.service_version !== CURRENT_SERVICE_VERSION ||
    value.status !== "checked" ||
    !isRecord(value.options) ||
    !hasExactKeys(value.options, DIFFICULTY_OPTIONS_KEYS) ||
    !isDifficultyTierName(value.options.tier) ||
    !isPositiveNumber(value.options.tempo_bpm) ||
    !isIntegerAtLeast(value.options.beats_per_bar, 1) ||
    !isCanonicalTab(value.tab) ||
    !isDifficultyTierCapability(value.tier) ||
    !isRecord(value.difficulty) ||
    !hasExactKeys(value.difficulty, DIFFICULTY_RESULT_KEYS) ||
    value.difficulty.checker_version !== CURRENT_DIFFICULTY_VERSION ||
    typeof value.difficulty.meets !== "boolean" ||
    (value.difficulty.playable !== "GREEN" &&
      value.difficulty.playable !== "AMBER" &&
      value.difficulty.playable !== "RED") ||
    !isStringArray(value.difficulty.tier_violations) ||
    !isRecord(value.published_grade) ||
    !hasExactKeys(value.published_grade, PUBLISHED_GRADE_KEYS) ||
    value.published_grade.model_version !== CURRENT_PUBLISHED_GRADE_VERSION ||
    value.published_grade.model_sha256 !== CURRENT_PUBLISHED_GRADE_MODEL_SHA256 ||
    !isNonEmptyString(value.published_grade.grade_system) ||
    !isIntegerAtLeast(value.published_grade.estimated_grade, 1) ||
    value.published_grade.estimated_grade > 10 ||
    !isRecord(value.published_grade.likely_interval) ||
    !hasExactKeys(value.published_grade.likely_interval, ["lower", "upper"]) ||
    !isIntegerAtLeast(value.published_grade.likely_interval.lower, 1) ||
    value.published_grade.likely_interval.lower > value.published_grade.estimated_grade ||
    !isIntegerAtLeast(value.published_grade.likely_interval.upper, 1) ||
    value.published_grade.likely_interval.upper < value.published_grade.estimated_grade ||
    value.published_grade.likely_interval.upper > 10 ||
    (value.published_grade.band !== "foundational" &&
      value.published_grade.band !== "intermediate" &&
      value.published_grade.band !== "advanced") ||
    value.published_grade.confidence !== "low" ||
    !isFiniteNumber(value.published_grade.burden_percentile) ||
    value.published_grade.burden_percentile < 0 ||
    value.published_grade.burden_percentile > 100 ||
    !isPercentileRecord(value.published_grade.feature_percentiles) ||
    !isNonEmptyString(value.published_grade.training_scope) ||
    value.published_grade.meaning !==
      "corpus_calibrated_estimate_not_a_playability_guarantee" ||
    !isStringRecord(value.stamps)
  ) {
    return false;
  }

  return (
    value.options.tier === value.tier.name &&
    requiredStampMatches(
      value.stamps,
      "difficulty_checker_version",
      CURRENT_DIFFICULTY_VERSION,
    ) &&
    requiredStampMatches(value.stamps, "profile_version", value.tier.profile.version) &&
    requiredStampMatches(
      value.stamps,
      "profile_fingerprint",
      value.tier.profile.fingerprint,
    ) &&
    requiredStampMatches(
      value.stamps,
      "published_grade_estimator_version",
      CURRENT_PUBLISHED_GRADE_VERSION,
    ) &&
    requiredStampMatches(
      value.stamps,
      "published_grade_model_sha256",
      CURRENT_PUBLISHED_GRADE_MODEL_SHA256,
    )
  );
}

function isSectionRegeneration(
  value: unknown,
): value is SectionRegenerationResponse {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "api_version",
      "service_version",
      "status",
      "selection",
      "options",
      "model",
      "editable_target",
      "tab",
      "ascii",
      "playability",
      "faithfulness",
      "revision",
      "stamps",
    ]) ||
    value.api_version !== CURRENT_API_VERSION ||
    value.service_version !== CURRENT_SERVICE_VERSION ||
    (value.status !== "accepted" &&
      value.status !== "preserved" &&
      value.status !== "unchanged") ||
    !isRecord(value.selection) ||
    !hasExactKeys(value.selection, ["start_measure", "end_measure", "locked_voices"]) ||
    !isIntegerAtLeast(value.selection.start_measure, 1) ||
    !isIntegerAtLeast(value.selection.end_measure, value.selection.start_measure) ||
    !Array.isArray(value.selection.locked_voices) ||
    !value.selection.locked_voices.every(
      (voice) => voice === "melody" || voice === "bass" || voice === "harmony",
    ) ||
    !isRecord(value.options) ||
    !hasExactKeys(value.options, [
      "profile",
      "style",
      "difficulty_tier",
      "technique_profile",
      "tempo_bpm",
    ]) ||
    !isProfileIdentity(value.options.profile) ||
    !isArrangementStyle(value.options.style) ||
    !isDifficultyTierName(value.options.difficulty_tier) ||
    !isTechniqueProfile(value.options.technique_profile) ||
    !(value.options.tempo_bpm === null || isPositiveNumber(value.options.tempo_bpm)) ||
    !isRecord(value.model) ||
    !hasExactKeys(value.model, MODEL_KEYS) ||
    !isNonEmptyString(value.model.model_id) ||
    (value.model.engine !== "offline" && value.model.engine !== "proxy") ||
    !isEditableTarget(value.editable_target) ||
    !isCanonicalTab(value.tab) ||
    !isNonEmptyString(value.ascii) ||
    !isPlayability(value.playability) ||
    !isFaithfulness(value.faithfulness) ||
    !isRecord(value.revision) ||
    !hasExactKeys(value.revision, [
      "schema_version",
      "proposal_status",
      "model_calls",
      "reason",
    ]) ||
    value.revision.schema_version !== CURRENT_SECTION_REGENERATION_VERSION ||
    !(value.revision.proposal_status === null || isNonEmptyString(value.revision.proposal_status)) ||
    !(value.revision.model_calls === 0 || value.revision.model_calls === 1) ||
    !isNullableString(value.revision.reason) ||
    !isStringRecord(value.stamps)
  ) {
    return false;
  }
  return (
    value.status !== "accepted" ||
    (value.playability as Record<string, unknown>).verdict === "GREEN"
  );
}

function isFingeringEdit(value: unknown): value is FingeringEditResponse {
  return (
    isRecord(value) &&
    hasExactKeys(value, [
      "api_version",
      "service_version",
      "status",
      "options",
      "tab",
      "ascii",
      "playability",
      "attempted_playability",
      "edit",
      "stamps",
    ]) &&
    value.api_version === CURRENT_API_VERSION &&
    value.service_version === CURRENT_SERVICE_VERSION &&
    (value.status === "applied" || value.status === "rejected" || value.status === "unchanged") &&
    isRecord(value.options) &&
    hasExactKeys(value.options, ["profile", "tempo_bpm", "beats_per_bar"]) &&
    isProfileIdentity(value.options.profile) &&
    isPositiveNumber(value.options.tempo_bpm) &&
    isIntegerAtLeast(value.options.beats_per_bar, 1) &&
    isCanonicalTab(value.tab) &&
    isNonEmptyString(value.ascii) &&
    isPlayability(value.playability) &&
    isPlayability(value.attempted_playability) &&
    isRecord(value.edit) &&
    hasExactKeys(value.edit, [
      "note_index",
      "onset",
      "string",
      "fret",
      "before_finger",
      "requested_finger",
      "reason",
    ]) &&
    isIntegerAtLeast(value.edit.note_index, 0) &&
    isString(value.edit.onset) &&
    FRACTION.test(value.edit.onset) &&
    isIntegerAtLeast(value.edit.string, 0) &&
    isIntegerAtLeast(value.edit.fret, 1) &&
    isIntegerAtLeast(value.edit.before_finger, 1) &&
    value.edit.before_finger <= 4 &&
    isIntegerAtLeast(value.edit.requested_finger, 1) &&
    value.edit.requested_finger <= 4 &&
    isNullableString(value.edit.reason) &&
    isStringRecord(value.stamps)
  );
}

function isProblem(value: unknown): value is APIProblem {
  return (
    isRecord(value) &&
    typeof value.type === "string" &&
    value.api_version === CURRENT_API_VERSION &&
    typeof value.status === "number" &&
    typeof value.code === "string" &&
    typeof value.title === "string" &&
    typeof value.detail === "string"
  );
}

async function decodeJSON(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().includes("json")) {
    throw new Error("Fretsure returned a non-JSON response.");
  }
  return response.json() as Promise<unknown>;
}

async function requestJSON(input: RequestInfo | URL, init?: RequestInit): Promise<unknown> {
  const response = await fetch(input, init);
  const payload = await decodeJSON(response);
  if (!response.ok) {
    if (isProblem(payload)) {
      throw new FretsureAPIError(payload);
    }
    throw new Error(`Fretsure request failed with HTTP ${response.status}.`);
  }
  return payload;
}

function assertCapabilities(value: unknown): asserts value is CapabilitiesResponse {
  if (!isCapabilities(value)) {
    throw new Error("Fretsure returned an incompatible capabilities document.");
  }
}

function assertArrangement(value: unknown): asserts value is ArrangementResponse {
  if (!isArrangement(value)) {
    throw new Error("Fretsure returned an incompatible arrangement document.");
  }
}

function assertDifficultyCheck(value: unknown): asserts value is DifficultyCheckResponse {
  if (!isDifficultyCheck(value)) {
    throw new Error("Fretsure returned an incompatible difficulty document.");
  }
}

function assertSectionRegeneration(
  value: unknown,
): asserts value is SectionRegenerationResponse {
  if (!isSectionRegeneration(value)) {
    throw new Error("Fretsure returned an incompatible section revision document.");
  }
}

function assertFingeringEdit(value: unknown): asserts value is FingeringEditResponse {
  if (!isFingeringEdit(value)) {
    throw new Error("Fretsure returned an incompatible fingering edit document.");
  }
}

export async function getCapabilities(signal?: AbortSignal): Promise<CapabilitiesResponse> {
  const payload = await requestJSON("/api/v1/capabilities", { signal });
  assertCapabilities(payload);
  return payload;
}

function mediaTypeFor(file: File): string {
  const suffix = file.name.toLowerCase();
  if (suffix.endsWith(".mid") || suffix.endsWith(".midi")) return "audio/midi";
  if (suffix.endsWith(".mxl")) return "application/vnd.recordare.musicxml";
  return "application/vnd.recordare.musicxml+xml";
}

export async function arrangeScore(
  file: File,
  controls: ArrangeControls,
  signal?: AbortSignal,
): Promise<ArrangementResponse> {
  const query = new URLSearchParams({
    filename: file.name,
    engine: controls.engine,
    profile: controls.profile,
    style: controls.style,
    difficulty_tier: controls.difficultyTier,
    technique_profile: controls.techniqueProfile,
    n: String(controls.n),
    max_iters: String(controls.maxIters),
    use_critic: controls.useCritic ? "true" : "false",
  });
  if (controls.tempoBpm !== null) {
    query.set("tempo_bpm", String(controls.tempoBpm));
  }
  const payload = await requestJSON(`/api/v1/arrangements?${query.toString()}`, {
    method: "POST",
    headers: { "Content-Type": mediaTypeFor(file) },
    body: file,
    signal,
  });
  assertArrangement(payload);
  if (
    payload.model.engine !== controls.engine ||
    payload.options.profile.name !== controls.profile ||
    payload.options.style !== controls.style ||
    payload.options.difficulty_tier !== controls.difficultyTier ||
    payload.options.technique_profile !== controls.techniqueProfile
  ) {
    throw new Error("Fretsure returned an incompatible arrangement document.");
  }
  return payload;
}

async function fileBase64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  const chunks: string[] = [];
  const chunkSize = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    chunks.push(String.fromCharCode(...bytes.subarray(offset, offset + chunkSize)));
  }
  return btoa(chunks.join(""));
}

export async function regenerateSection(
  file: File,
  result: ArrangementResponse,
  selection: SectionSelection,
  signal?: AbortSignal,
): Promise<SectionRegenerationResponse> {
  if (!result.editable_target || !result.tab) {
    throw new Error("The selected checkpoint cannot be revised.");
  }
  if (signal?.aborted) throw new DOMException("The request was aborted.", "AbortError");
  const document = {
    source: { filename: file.name, base64: await fileBase64(file) },
    baseline: { editable_target: result.editable_target, tab: result.tab },
    selection: {
      start_measure: selection.startMeasure,
      end_measure: selection.endMeasure,
      locked_voices: selection.lockedVoices,
    },
    options: {
      profile: result.options.profile.name,
      style: result.options.style,
      difficulty_tier: result.options.difficulty_tier,
      technique_profile: result.options.technique_profile,
      tempo_bpm: result.options.tempo_override_bpm,
    },
  };
  const query = new URLSearchParams({ engine: result.model.engine });
  const payload = await requestJSON(
    `/api/v1/arrangements/regenerate-section?${query.toString()}`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(document),
      signal,
    },
  );
  assertSectionRegeneration(payload);
  return payload;
}

export async function editLeftFinger(
  tab: CanonicalTab,
  noteIndex: number,
  leftFinger: number,
  profile: string,
  tempoBpm: number,
  beatsPerBar: number,
  signal?: AbortSignal,
): Promise<FingeringEditResponse> {
  const query = new URLSearchParams({
    note_index: String(noteIndex),
    left_finger: String(leftFinger),
    profile,
    tempo_bpm: String(tempoBpm),
    beats_per_bar: String(beatsPerBar),
  });
  const payload = await requestJSON(`/api/v1/fingering/left-hand?${query.toString()}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: canonicalTabJSON(tab),
    signal,
  });
  assertFingeringEdit(payload);
  return payload;
}

export async function checkDifficulty(
  tab: CanonicalTab,
  controls: DifficultyCheckControls,
  signal?: AbortSignal,
): Promise<DifficultyCheckResponse> {
  const query = new URLSearchParams({
    tier: controls.tier,
    tempo_bpm: String(controls.tempoBpm),
    beats_per_bar: String(controls.beatsPerBar),
  });
  const payload = await requestJSON(`/api/v1/difficulty/check?${query.toString()}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: canonicalTabJSON(tab),
    signal,
  });
  assertDifficultyCheck(payload);
  return payload;
}

export interface DownloadAsset {
  blob: Blob;
  filename: string;
}

export function canonicalTabJSON(tab: CanonicalTab): string {
  return JSON.stringify({
    tuning: tab.tuning,
    capo: tab.capo,
    notes: tab.notes.map((note) => ({
      onset: note.onset,
      duration: note.duration,
      string: note.string,
      fret: note.fret,
      left_finger: note.left_finger,
      right_finger: note.right_finger,
      ...(note.attack_group === undefined ? {} : { attack_group: note.attack_group }),
    })),
  });
}

function attachmentFilename(header: string | null, fallback: string): string {
  const match = header?.match(/(?:^|;)\s*filename="([A-Za-z0-9][A-Za-z0-9._-]{0,127})"/i);
  return match?.[1] ?? fallback;
}

export async function exportMidi(
  tab: CanonicalTab,
  tempoBpm: number,
  signal?: AbortSignal,
): Promise<DownloadAsset> {
  const query = new URLSearchParams({ tempo_bpm: String(tempoBpm) });
  const response = await fetch(`/api/v1/exports/midi?${query.toString()}`, {
    method: "POST",
    headers: {
      Accept: "audio/midi",
      "Content-Type": "application/json",
    },
    body: canonicalTabJSON(tab),
    signal,
  });
  if (!response.ok) {
    const payload = await decodeJSON(response);
    if (isProblem(payload)) throw new FretsureAPIError(payload);
    throw new Error(`Fretsure MIDI export failed with HTTP ${response.status}.`);
  }
  const contentType = response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (contentType !== "audio/midi") {
    throw new Error("Fretsure returned an incompatible MIDI export.");
  }
  const blob = await response.blob();
  if (blob.size === 0) {
    throw new Error("Fretsure returned an empty MIDI export.");
  }
  return {
    blob,
    filename: attachmentFilename(
      response.headers.get("content-disposition"),
      "fretsure-arrangement.mid",
    ),
  };
}

export async function exportAudio(
  tab: CanonicalTab,
  tempoBpm: number,
  signal?: AbortSignal,
): Promise<DownloadAsset> {
  const query = new URLSearchParams({ tempo_bpm: String(tempoBpm) });
  const response = await fetch(`/api/v1/exports/audio?${query.toString()}`, {
    method: "POST",
    headers: {
      Accept: "audio/wav",
      "Content-Type": "application/json",
    },
    body: canonicalTabJSON(tab),
    signal,
  });
  if (!response.ok) {
    const payload = await decodeJSON(response);
    if (isProblem(payload)) throw new FretsureAPIError(payload);
    throw new Error(`Fretsure audio export failed with HTTP ${response.status}.`);
  }
  const contentType = response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (contentType !== "audio/wav") {
    throw new Error("Fretsure returned an incompatible audio export.");
  }
  const blob = await response.blob();
  if (blob.size < 44) {
    throw new Error("Fretsure returned an invalid audio export.");
  }
  return {
    blob,
    filename: attachmentFilename(
      response.headers.get("content-disposition"),
      "fretsure-arrangement.wav",
    ),
  };
}

export async function exportTabText(
  tab: CanonicalTab,
  signal?: AbortSignal,
): Promise<DownloadAsset> {
  const response = await fetch("/api/v1/exports/tab-text", {
    method: "POST",
    headers: {
      Accept: "text/plain",
      "Content-Type": "application/json",
    },
    body: canonicalTabJSON(tab),
    signal,
  });
  if (!response.ok) {
    const payload = await decodeJSON(response);
    if (isProblem(payload)) throw new FretsureAPIError(payload);
    throw new Error(`Fretsure guitar TAB export failed with HTTP ${response.status}.`);
  }
  const contentType = response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (contentType !== "text/plain") {
    throw new Error("Fretsure returned an incompatible guitar TAB export.");
  }
  const blob = await response.blob();
  if (blob.size === 0) {
    throw new Error("Fretsure returned an empty guitar TAB export.");
  }
  return {
    blob,
    filename: attachmentFilename(
      response.headers.get("content-disposition"),
      "fretsure-guitar-tablature.txt",
    ),
  };
}

async function exportProfessionalTab(
  tab: CanonicalTab,
  tempoBpm: number,
  options: {
    path: string;
    accept: string;
    expectedContentType: string;
    label: string;
    fallbackFilename: string;
  },
  signal?: AbortSignal,
): Promise<DownloadAsset> {
  const query = new URLSearchParams({ tempo_bpm: String(tempoBpm) });
  const response = await fetch(`${options.path}?${query.toString()}`, {
    method: "POST",
    headers: {
      Accept: options.accept,
      "Content-Type": "application/json",
    },
    body: canonicalTabJSON(tab),
    signal,
  });
  if (!response.ok) {
    const payload = await decodeJSON(response);
    if (isProblem(payload)) throw new FretsureAPIError(payload);
    throw new Error(`Fretsure ${options.label} export failed with HTTP ${response.status}.`);
  }
  const contentType = response.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase();
  if (contentType !== options.expectedContentType) {
    throw new Error(`Fretsure returned an incompatible ${options.label} export.`);
  }
  const blob = await response.blob();
  if (blob.size === 0) {
    throw new Error(`Fretsure returned an empty ${options.label} export.`);
  }
  return {
    blob,
    filename: attachmentFilename(
      response.headers.get("content-disposition"),
      options.fallbackFilename,
    ),
  };
}

export function exportMusicXMLTab(
  tab: CanonicalTab,
  tempoBpm: number,
  signal?: AbortSignal,
): Promise<DownloadAsset> {
  return exportProfessionalTab(
    tab,
    tempoBpm,
    {
      path: "/api/v1/exports/musicxml-tab",
      accept: "application/vnd.recordare.musicxml+xml",
      expectedContentType: "application/vnd.recordare.musicxml+xml",
      label: "MusicXML TAB",
      fallbackFilename: "fretsure-guitar-tablature.musicxml",
    },
    signal,
  );
}

export function exportGuitarPro(
  tab: CanonicalTab,
  tempoBpm: number,
  signal?: AbortSignal,
): Promise<DownloadAsset> {
  return exportProfessionalTab(
    tab,
    tempoBpm,
    {
      path: "/api/v1/exports/guitar-pro",
      accept: "application/octet-stream",
      expectedContentType: "application/octet-stream",
      label: "Guitar Pro",
      fallbackFilename: "fretsure-guitar-tab.gp5",
    },
    signal,
  );
}

export async function exportGuitarPro7(
  tab: CanonicalTab,
  tempoBpm: number,
  signal?: AbortSignal,
): Promise<DownloadAsset> {
  const musicXml = await exportMusicXMLTab(tab, tempoBpm, signal);
  const { musicXmlBlobToGp7 } = await import("./alphatab");
  return {
    blob: await musicXmlBlobToGp7(musicXml.blob),
    filename: "fretsure-guitar-tab.gp",
  };
}

export function exportPdfTab(
  tab: CanonicalTab,
  tempoBpm: number,
  signal?: AbortSignal,
): Promise<DownloadAsset> {
  return exportProfessionalTab(
    tab,
    tempoBpm,
    {
      path: "/api/v1/exports/pdf-tab",
      accept: "application/pdf",
      expectedContentType: "application/pdf",
      label: "PDF TAB",
      fallbackFilename: "fretsure-guitar-tab.pdf",
    },
    signal,
  );
}
