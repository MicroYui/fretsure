# Oracle gold set (planned human-played playability labels)

Ground-truth labels for **validating the checker itself** (see `docs/SCOPE.md`
and roadmap §14 A.8). A guitarist with a measured hand span plays each tab and
records whether it is physically playable. This is the empirical anchor for the
GREEN false-accept rate.

> **Status (2026-07-13): not collected.** `sample_labeled.jsonl` contains six
> constructed rows only; it validates serialization/statistics plumbing and cannot
> support a real-player false-accept claim or parameter calibration. Collection can
> happen in parallel with Plan 6 and other engineering; it does not block continued
> implementation. It does block a measured real-world false-accept rate, mapping
> versioned profiles/tiers to real players, and any stronger claim that a real
> guitarist can necessarily play a result certified GREEN by `oracle@0.2.0`.
> Human musicality is a separate blind-rating track; this physical-playability set
> cannot establish it.

## Current fixture schema — `*.jsonl`

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
- `human_playable` is only a fixture field in the current six-row sample. The
  present loader calls the oracle with default tempo 90 and 4 beats/bar and has no
  session/player metadata, so this schema is **not collection-ready**.

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
not make the six constructed rows human evidence or make the current schema
collection-ready.

## Required collection-schema extension

Before collecting real labels, extend the loader/schema to bind every observation
to the exact claim being tested:

- `tempo_bpm`, `beats_per_bar`, checker/profile version **and Git commit**;
- anonymous player/session id, rater id, date, measured hand-span method/value,
  relevant experience level;
- instrument type, scale length, tuning, capo and setup;
- whether the player must use the **exhibited fingering** (the oracle's claim) or
  may refinger; the primary label must test the exhibited fingering;
- attempt count, clean-play criterion, playable/unplayable/uncertain label and an
  optional reason/diagnostic;
- source/family/template/seed identifiers so near-duplicates stay in one split.

Do not start the headline collection against the current simplified loader: tempo
and session ambiguity would make the label disagree with the proposition certified
by the oracle.

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
