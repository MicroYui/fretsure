import type { ArrangementResponse, CapabilitiesResponse } from "../src/types";

export const capabilities: CapabilitiesResponse = {
  api_version: "fretsure-api@0.1.0",
  package_version: "0.4.0",
  service_version: "fretsure-service@0.1.0",
  engines: [
    { id: "offline", available: true, model_id: "constant-stub" },
    { id: "proxy", available: true, model_id: "gpt-5.6-sol" },
  ],
  profiles: [
    {
      name: "median",
      version: "median@0.1",
      fingerprint: "abc123",
      calibration_status: "placeholder_pending_human_calibration",
    },
  ],
  inputs: {
    score_suffixes: [".musicxml", ".xml", ".mxl"],
    max_xml_bytes: 10 * 1024 * 1024,
    max_mxl_bytes: 20 * 1024 * 1024,
  },
  controls: {
    arrange: {
      defaults: {
        profile: "median",
        n: 4,
        max_iters: 8,
        use_critic: true,
        tempo_bpm: null,
        engine: "offline",
      },
      n: { min: 1, max: 8 },
      max_iters: { min: 0, max: 16 },
      tempo_bpm: { min: 1, max: 1000, nullable: true },
    },
  },
  implemented: ["arrange_score_bytes"],
  deferred: ["render_audio"],
  stamps: {
    package_version: "0.4.0",
    service_version: "fretsure-service@0.1.0",
    importer_version: "musicxml@0.3.0",
    trace_schema_version: "agent-trace@0.1.0",
  },
};

export const arrangement: ArrangementResponse = {
  api_version: "fretsure-api@0.1.0",
  service_version: "fretsure-service@0.1.0",
  status: "tab_produced",
  source: {
    filename: "example.musicxml",
    format: "musicxml",
    raw_sha256: "a".repeat(64),
    root_member: null,
    root_sha256: "a".repeat(64),
    container_version: null,
    importer_version: "musicxml@0.3.0",
    warnings: [],
  },
  score: {
    title: "Evidence Song",
    key: "C major",
    time_signature: { numerator: 4, denominator: 4 },
    source_tempo_bpm: 90,
    duration_beats: "4/1",
    note_count: 4,
    voice_counts: { melody: 2, bass: 2, harmony: 0 },
    chord_count: 1,
    source_description: "fixture",
    rights_or_license: "CC0",
  },
  options: {
    profile: capabilities.profiles[0],
    tuning: [40, 45, 50, 55, 59, 64],
    capo: 0,
    candidate_count: 4,
    max_repair_iterations: 8,
    critic_enabled: true,
    tempo_override_bpm: null,
    source_tempo_bpm: 90,
    effective_tempo_bpm: 90,
  },
  model: { model_id: "constant-stub", engine: "offline" },
  tab: { tuning: [40, 45, 50, 55, 59, 64], capo: 0, notes: [] },
  ascii: "e|--0--|\nB|--1--|\nG|--0--|\nD|--2--|\nA|--3--|\nE|-----|",
  playability: {
    verdict: "GREEN",
    meaning: "versioned_model_relative_not_a_real_player_guarantee",
    diagnostics: [],
    checker_version: "oracle@0.2.0",
    profile_version: "median@0.1",
    profile_fingerprint: "abc123",
    input_schema_version: "tab-input@0.2.0",
  },
  faithfulness: {
    melody_f1: 1,
    bass_root_accuracy: 0.75,
    harmony_jaccard: 0.5,
    passed: true,
    checker_version: "fidelity@0.2.0",
  },
  trace: {
    schema_version: "agent-trace@0.1.0",
    steps: [
      {
        trace_schema_version: "agent-trace@0.1.0",
        seq: 0,
        kind: "PLAN",
        event: "PIPELINE_CONFIGURED",
        candidate_index: null,
        iteration: null,
        detail: "Configured a bounded pipeline.",
        data: { checker_version: "oracle@0.2.0" },
      },
      {
        trace_schema_version: "agent-trace@0.1.0",
        seq: 1,
        kind: "EDIT",
        event: "EDIT_APPLIED",
        candidate_index: 0,
        iteration: 1,
        detail: "The targeted edit was applied to the repair state.",
        data: {
          outcome: "APPLIED",
          edit: { op: "drop_note", target_pitch: 55 },
        },
      },
      {
        trace_schema_version: "agent-trace@0.1.0",
        seq: 2,
        kind: "ORACLE",
        event: "PLAYABILITY_CHECKED",
        candidate_index: 0,
        iteration: 1,
        detail: "Oracle returned GREEN with 0 diagnostics.",
        data: { verdict: "GREEN" },
      },
    ],
  },
  stamps: {
    package_version: "0.4.0",
    service_version: "fretsure-service@0.1.0",
    profile_registry_version: "profile-registry@0.1.0",
    profile_version: "median@0.1",
    profile_fingerprint: "abc123",
    oracle_checker_version: "oracle@0.2.0",
    oracle_input_schema_version: "tab-input@0.2.0",
    fidelity_checker_version: "fidelity@0.2.0",
    target_input_schema_version: "target-input@0.1.0",
    trace_schema_version: "agent-trace@0.1.0",
    importer_version: "musicxml@0.3.0",
    model_id: "constant-stub",
  },
};

const unprovidedKey = "key-signature:fifths=0;mode=unprovided";
const keyModeUnprovidedWarning = {
  code: "KEY_MODE_UNPROVIDED",
  severity: "warning",
  message: "traditional key signature supplied no mode; no mode was inferred",
  location: {
    part_id: "P1",
    measure: "1",
    voice: null,
    element: "key",
    archive_member: null,
  },
};

export const producerXmlArrangement: ArrangementResponse = {
  ...arrangement,
  source: {
    filename: "musescore-4.7.4.musicxml",
    format: "musicxml",
    raw_sha256: "8aa3f622429dee2dda26ca91c87237470d60c4c02fb996bd9171c9238cd77386",
    root_member: null,
    root_sha256: "8aa3f622429dee2dda26ca91c87237470d60c4c02fb996bd9171c9238cd77386",
    container_version: null,
    importer_version: "musicxml@0.3.0",
    warnings: [keyModeUnprovidedWarning],
  },
  score: {
    title: "Fretsure Producer Etude",
    key: unprovidedKey,
    time_signature: { numerator: 4, denominator: 4 },
    source_tempo_bpm: 96,
    duration_beats: "4/1",
    note_count: 4,
    voice_counts: { melody: 4, bass: 0, harmony: 0 },
    chord_count: 1,
    source_description:
      "filename=musescore-4.7.4.musicxml;format=musicxml;sha256=8aa3f622429dee2dda26ca91c87237470d60c4c02fb996bd9171c9238cd77386;root_sha256=8aa3f622429dee2dda26ca91c87237470d60c4c02fb996bd9171c9238cd77386;importer=musicxml@0.3.0",
    rights_or_license: "CC0-1.0",
  },
  options: {
    ...arrangement.options,
    candidate_count: 1,
    max_repair_iterations: 0,
    critic_enabled: false,
    source_tempo_bpm: 96,
    effective_tempo_bpm: 96,
  },
};

export const producerMxlArrangement: ArrangementResponse = {
  ...arrangement,
  source: {
    filename: "musescore-4.7.4-roundtrip-supported_basic.mxl",
    format: "mxl",
    raw_sha256: "9fbca0cd86c4110a24a51c46a7982859a3d39e1cadfb50d5ad31a479fafe0cc1",
    root_member: "score.xml",
    root_sha256: "e6af1f610580baa3c6a588d738a7aeb690cfd19f684a1dbaa6cc1d91c681a39f",
    container_version: "mxl-container@0.1.0",
    importer_version: "musicxml@0.3.0",
    warnings: [
      {
        code: "MXL_ROOTFILE_MEDIA_TYPE_UNPROVIDED",
        severity: "warning",
        message: "container rootfile omits media-type; accepted by the extension/path contract",
        location: {
          part_id: null,
          measure: null,
          voice: null,
          element: "rootfile",
          archive_member: "META-INF/container.xml",
        },
      },
      keyModeUnprovidedWarning,
    ],
  },
  score: {
    title: "Importer Etude",
    key: unprovidedKey,
    time_signature: { numerator: 4, denominator: 4 },
    source_tempo_bpm: 96,
    duration_beats: "8/1",
    note_count: 4,
    voice_counts: { melody: 4, bass: 0, harmony: 0 },
    chord_count: 2,
    source_description:
      "filename=musescore-4.7.4-roundtrip-supported_basic.mxl;format=mxl;sha256=9fbca0cd86c4110a24a51c46a7982859a3d39e1cadfb50d5ad31a479fafe0cc1;root_sha256=e6af1f610580baa3c6a588d738a7aeb690cfd19f684a1dbaa6cc1d91c681a39f;root_member=score.xml;importer=musicxml@0.3.0;container=mxl-container@0.1.0",
    rights_or_license: "CC0-1.0",
  },
  options: {
    ...arrangement.options,
    candidate_count: 1,
    max_repair_iterations: 0,
    critic_enabled: false,
    source_tempo_bpm: 96,
    effective_tempo_bpm: 96,
  },
};

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}
