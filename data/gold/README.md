# Oracle gold set (planned human-played playability labels)

Ground-truth labels for **validating the checker itself** (see `docs/SCOPE.md`
and roadmap §14 A.8). A guitarist with a measured hand span plays each tab and
records whether it is physically playable. This is the empirical anchor for the
GREEN false-accept rate.

> **Status (2026-07-17): formal contract frozen; human observations = 0; real
> provider calls = 0.** Task 6 can emit software-fixture evidence only. The formal
> label/proposition boundary is ready for a later approved collection, but no
> constructed row is human gold and no checker-vs-judge superiority claim is
> available. Human musicality remains a separate blind-rating track.

## Legacy software fixture — `sample_labeled.jsonl`

```json
{
  "tab": {
    "tuning": [40, 45, 50, 55, 59, 64],
    "capo": 0,
    "notes": [
      {"onset": "0/1", "duration": "1/1", "string": 0, "fret": 2,
       "left_finger": 1, "right_finger": "p"}
    ]
  },
  "human_playable": true,
  "note": "free-text provenance / why"
}
```

- `tab` is the exact JSON emitted by `fretsure.tab.tab_to_json`. `string` 0 =
  lowest-pitched (6th) string. `right_finger` ∈ {p, i, m, a}.
- `human_playable` is only a legacy fixture field. The six constructed rows validate
  serialization and statistics plumbing with default tempo 90 and 4 beats/bar; they
  are not Task 6 formal rows and are **not collection-ready**.

## Files

- `sample_labeled.jsonl` — a tiny hand-authored **sample** (not the real gold
  set): constructed easy, impossible, and borderline first-position-stretch cases
  to exercise the stats code. `green_unplayable` on this sample is 0 by construction.

## Zero-GREEN semantics (resolved software gate)

A dataset with no GREEN predictions has denominator zero and therefore supplies
**no evidence** about GREEN false accepts. The canonical result now returns
`status="no_green"`, preserves `green_total=0`, and sets both the observed rate and
one-sided Clopper–Pearson upper bound to `None`; low-level interval entry points also
reject the zero-denominator misuse. Reports must keep that explicit representation
and must never coerce it to a perfect `0.0` result.

The label loader/statistics boundary is also fail-closed and resource-bounded for
both JSONL files and in-memory rows: exact built-in JSON containers only; deep
snapshotting against mutation; physical-line provenance tied to canonical content;
64 MiB cumulative bytes/scalars; 200,000 notes; 2,000,000 checker-work units;
1,000,000 physical lines; and 1,000,000 cumulative JSON nodes, plus per-row
depth/node/token limits. These protections make the plumbing safe to expose; they do
not make the six constructed rows human evidence.

## Formal human-label contract

One future collection record must bind the label to the exact proposition being
tested. The field names and states mirror the Task 6 `JudgeItem`, `ReferenceLabel`,
`LabelProvenance`, and agreement contracts:

```json
{
  "item_id": "human-item-001",
  "family_id": "human-family-001",
  "adversarial_class": "stretch-near-miss",
  "tab": {
    "tuning": [40, 45, 50, 55, 59, 64],
    "capo": 0,
    "notes": [
      {"onset": "0/1", "duration": "1/1", "string": 0, "fret": 2,
       "left_finger": 1, "right_finger": "p"}
    ]
  },
  "tab_time_unit": "quarter_note",
  "tempo_bpm": 90.0,
  "tempo_unit": "quarter_notes_per_minute",
  "meter": [4, 4],
  "bar_duration_quarter_notes": "4/1",
  "fingering_policy": "EXHIBITED_ONLY",
  "profile_version": "median@0.1",
  "profile_fingerprint": "<lowercase-sha256>",
  "profile_assignment_protocol_version": "<frozen-before-collection>",
  "checker_version": "oracle@0.2.0",
  "proposition_sha256": "<lowercase-sha256>",
  "execution_git_sha": "<task-7-release-sha>",
  "provenance": {
    "source": "human",
    "dataset_id": "<dataset-id>",
    "protocol_version": "<protocol-version>",
    "record_sha256": "<lowercase-sha256>"
  },
  "observations": [
    {
      "player_id": "anon-player-001",
      "session_id": "anon-session-001",
      "rater_id": "anon-rater-001",
      "date": "2026-08-01",
      "measured_finger_1_to_4_span_mm": 100.0,
      "hand_span_method": "finger-1-to-4-max-span@0.1.0",
      "experience_level": "advanced",
      "instrument_type": "classical-guitar",
      "scale_length_mm": 648.0,
      "assigned_profile_version": "median@0.1",
      "assigned_profile_fingerprint": "<same-as-proposition>",
      "attempt_count": 3,
      "label": "PLAYABLE",
      "reason": null
    },
    {
      "player_id": "anon-player-002",
      "session_id": "anon-session-002",
      "rater_id": "anon-rater-002",
      "date": "2026-08-01",
      "measured_finger_1_to_4_span_mm": 100.0,
      "hand_span_method": "finger-1-to-4-max-span@0.1.0",
      "experience_level": "advanced",
      "instrument_type": "classical-guitar",
      "scale_length_mm": 648.0,
      "assigned_profile_version": "median@0.1",
      "assigned_profile_fingerprint": "<same-as-proposition>",
      "attempt_count": 3,
      "label": "PLAYABLE",
      "reason": null
    }
  ],
  "agreement_status": "agreed",
  "labeler_count": 2,
  "verdict": "PLAYABLE",
  "adjudication": null
}
```

- `tab` is the exact exhibited fingering; refingering answers a different question.
  Tab onset/duration values are quarter-note units, and `tempo_bpm` is quarter notes
  per minute. `bar_duration_quarter_notes` is derived exactly as
  `meter_numerator * 4 / meter_denominator`; for example, 6/8 is `3/1`.
  The tempo, exact meter, profile fingerprint, canonical tab, fixed units, and fixed
  `EXHIBITED_ONLY` policy determine `proposition_sha256`; `profile_version` is stamped
  separately.
  `adversarial_class` follows the collection protocol's frozen taxonomy; it is not a
  human difficulty label.
- Player, session, and rater identifiers are anonymous. `Profile.hand_span_mm` means
  maximum finger-1-to-4 distance, not thumb-to-pinky span; instrument scale length must
  match the assigned profile. A versioned profile-assignment protocol must be frozen
  before collection. Every accepted observation carries the same assigned profile
  version/fingerprint as the proposition; observations assigned to different profiles
  are never aggregated. The collection protocol also freezes date, experience level,
  attempts, clean-play criterion, and optional reason before collection.
- Each observation label is exactly `PLAYABLE`, `UNPLAYABLE`, or `UNCERTAIN`.
  `UNCERTAIN` is preserved as an observation but can never become the final binary
  `verdict`.
- Human agreement states are `pending`, `single_label`, `uncertain`, `agreed`,
  `disagreed`, and `adjudicated`. Only an `agreed` or `adjudicated` row with final
  binary verdict `PLAYABLE` or `UNPLAYABLE` may enter a future confirmatory
  denominator. All other states remain visible and follow the preregistered
  missingness rule.
- The future collection loader derives the aggregate from accepted observations bound
  to the same proposition; it does not trust a caller-supplied summary. `labeler_count`
  equals the number of those observations. Zero observations gives `pending`; one
  binary label gives `single_label`; one or more all-`UNCERTAIN` labels gives
  `uncertain`; at least two identical binary labels with no uncertainty gives `agreed`;
  every other multi-observation combination gives `disagreed` with a null verdict.
  `adjudicated` is allowed only after `disagreed` and requires a separate versioned
  adjudication record with anonymous adjudicator, date, binary verdict, and reason;
  the adjudicator is not added to `labeler_count`.
- `execution_git_sha` is supplied by the external Task 7 publication workflow. Task 6
  runtime does not inspect `.git`, invoke Git, or launch a subprocess to discover it.

Do not collect formal labels through the legacy `human_playable` loader.

## Evidence and open gates

- Task 6 output is exactly `SOFTWARE_FIXTURE_ONLY`. A software fixture has constructed
  binary truth, `agreement_status="software_fixture"`, and `labeler_count=0`; it proves
  parsing, prompt/schedule, repetition, flip/invalid, and accounting machinery only.
- **Human empirical: OPEN.** Real guitarist observations and agreement/adjudication
  remain required for empirical GREEN false-accept, AMBER bandwidth, player/profile
  calibration, and checker-vs-judge superiority claims. No profile-assignment
  protocol has yet been approved, so formal collection remains closed.
- **Cross-provider comparison: UNAVAILABLE.** It remains unavailable until at least
  two independently versioned provider models have exact model bindings and an
  explicit versioned call budget covering the complete frozen schedule. Repeated
  prompts, aliases, or fake clients do not satisfy this gate.

## Planned collection protocol

1. Run a small pilot first to validate instructions, timing, label repeatability and
   the number of GREEN examples needed for a useful false-accept upper bound; do not
   promise that ~300 rows fit in a fixed 2–3 hour session before the pilot.
2. Draw the powered sample stratified by difficulty/texture and verdict, including
   adversarial near-misses just inside/outside the boundary.
3. A guitarist with a measured hand span attempts the **exact displayed fingering at
   the notated tempo** and records the defined outcome. A second rater or repeated
   subset is required if reporting inter-rater/retest κ.
4. Split train/dev/test by source family/template/seed, not merely by row, so related
   variants cannot cross splits. Calibrate only on train/dev; freeze test before use.
5. Report the GREEN false-accept denominator and Clopper–Pearson upper bound,
   confusion matrix, uncertainty/AMBER rows and rater agreement separately. A
   zero-GREEN split must be reported as no evidence, never as `0.0` error.
6. AMBER width is currently fixed by the 0.9/1.1 profile transforms. Learning it from
   human disagreement is future work and must not be claimed until code and a
   multi-rater protocol actually implement that fit.

Difficulty-tier calibration needs its own task/label protocol, and musicality needs
a blinded preference/rating study. They may share recruiting/session infrastructure
with this gold-set effort, but neither should be inferred from the binary physical
playability label.

**Do not** feed the held-out test labels to any arranger/agent — they exist only to
validate the oracle. Train/dev may calibrate model parameters, but the test split
must remain isolated from both calibration and agent selection.
