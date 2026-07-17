import type {
  APIProblem,
  ArrangeControls,
  ArrangementResponse,
  CapabilitiesResponse,
  FaithfulnessDimension,
  ScoreFormat,
} from "./types";

export class FretsureAPIError extends Error {
  readonly problem: APIProblem;

  constructor(problem: APIProblem) {
    super(problem.detail);
    this.name = "FretsureAPIError";
    this.problem = problem;
  }
}

const CURRENT_API_VERSION = "fretsure-api@0.2.0";
const CURRENT_PACKAGE_VERSION = "0.6.0";
const CURRENT_SERVICE_VERSION = "fretsure-service@0.2.0";
const CURRENT_SCORE_INPUT_VERSION = "score-input@0.1.0";
const CURRENT_FIDELITY_VERSION = "fidelity@0.3.0";
const CURRENT_TRACE_VERSION = "agent-trace@0.2.0";
const SCORE_SUFFIXES = [".musicxml", ".xml", ".mxl", ".mid", ".midi"] as const;
const FORMAT_IMPORTERS: Readonly<Record<ScoreFormat, string>> = {
  musicxml: "musicxml@0.3.0",
  mxl: "musicxml@0.3.0",
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

function hasExactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const actual = Object.keys(value);
  return actual.length === keys.length && keys.every((key) => Object.hasOwn(value, key));
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

function isCapabilities(value: unknown): value is CapabilitiesResponse {
  if (
    !isRecord(value) ||
    value.api_version !== CURRENT_API_VERSION ||
    value.package_version !== CURRENT_PACKAGE_VERSION ||
    value.service_version !== CURRENT_SERVICE_VERSION ||
    value.trace_schema_version !== CURRENT_TRACE_VERSION ||
    !Array.isArray(value.engines) ||
    value.engines.length !== 2 ||
    !value.engines.every(isEngineCapability) ||
    !Array.isArray(value.profiles) ||
    value.profiles.length === 0 ||
    !value.profiles.every(isProfileIdentity) ||
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
    !isStringArray(value.implemented) ||
    !isStringArray(value.deferred) ||
    !isStringRecord(value.stamps)
  ) {
    return false;
  }

  const engines = value.engines as CapabilitiesResponse["engines"];
  const profiles = value.profiles as CapabilitiesResponse["profiles"];
  const engineIds = engines.map((engine) => engine.id);
  const profileNames = profiles.map((profile) => profile.name);
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
    !hasUniqueStrings(value.inputs.score_suffixes) ||
    !requiredStampMatches(stamps, "package_version", CURRENT_PACKAGE_VERSION) ||
    !requiredStampMatches(stamps, "service_version", CURRENT_SERVICE_VERSION) ||
    !requiredStampMatches(stamps, "score_input_version", CURRENT_SCORE_INPUT_VERSION) ||
    !requiredStampMatches(stamps, "fidelity_checker_version", CURRENT_FIDELITY_VERSION) ||
    !requiredStampMatches(stamps, "trace_schema_version", CURRENT_TRACE_VERSION) ||
    !isNonEmptyString(defaults.profile) ||
    !profileNames.includes(defaults.profile) ||
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
  "tab",
  "ascii",
  "playability",
  "faithfulness",
  "trace",
  "stamps",
] as const;
const MODEL_KEYS = ["model_id", "engine"] as const;
const FAITHFULNESS_KEYS = [
  "melody_f1",
  "bass_root_accuracy",
  "harmony_jaccard",
  "evaluated_dimensions",
  "unavailable_dimensions",
  "passed",
  "checker_version",
] as const;
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
  "profile_version",
  "profile_fingerprint",
  "oracle_checker_version",
  "oracle_input_schema_version",
  "fidelity_checker_version",
  "target_input_schema_version",
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
  if (
    !hasExactKeys(data, CANDIDATE_SELECTED_KEYS) ||
    !isIntegerAtLeast(data.winner_candidate_index, 0) ||
    !isIntegerAtLeast(data.candidates_considered, 1) ||
    data.winner_candidate_index >= data.candidates_considered ||
    selection.candidate_index !== data.winner_candidate_index ||
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

function isArrangement(value: unknown): value is ArrangementResponse {
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
    !isPublicTrace(value.trace) ||
    !isStringRecord(value.stamps)
  ) {
    return false;
  }

  const produced = value.status === "tab_produced";
  const productOutputsAgree = produced
    ? isRecord(value.tab) &&
      isNonEmptyString(value.ascii) &&
      isPlayability(value.playability) &&
      isFaithfulness(value.faithfulness)
    : value.tab === null &&
      value.ascii === null &&
      value.playability === null &&
      value.faithfulness === null;
  if (!productOutputsAgree) return false;

  const source = value.source as Record<string, unknown>;
  const options = value.options as Record<string, unknown>;
  const profile = options.profile as Record<string, unknown>;
  const model = value.model;
  const trace = value.trace as Record<string, unknown>;
  const publicTrace = value.trace as ArrangementResponse["trace"];
  const stamps = value.stamps;
  const sourceFormat = source.format as ScoreFormat;
  if (
    !REQUIRED_ARRANGEMENT_STAMPS.every((key) => isNonEmptyString(stamps[key])) ||
    !requiredStampMatches(stamps, "package_version", CURRENT_PACKAGE_VERSION) ||
    !requiredStampMatches(stamps, "service_version", CURRENT_SERVICE_VERSION) ||
    !requiredStampMatches(stamps, "score_input_version", CURRENT_SCORE_INPUT_VERSION) ||
    !requiredStampMatches(stamps, "fidelity_checker_version", CURRENT_FIDELITY_VERSION) ||
    !requiredStampMatches(stamps, "trace_schema_version", CURRENT_TRACE_VERSION) ||
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
      !candidateSelectionMatchesGates(publicTrace, playability, faithfulness)
    ) {
      return false;
    }
  } else if (publicTrace.steps.some((step) => step.event === "CANDIDATE_SELECTED")) {
    return false;
  }
  return true;
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
  if (payload.model.engine !== controls.engine) {
    throw new Error("Fretsure returned an incompatible arrangement document.");
  }
  return payload;
}
