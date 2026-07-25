# Plan 7B — score-supervised fingering and difficulty calibration

Date: 2026-07-25  
Status: **CLOSED — software and offline evidence gates passed**. See
[`../../PLAN7B_ACCEPTANCE.md`](../../PLAN7B_ACCEPTANCE.md).

Decision: use licensed, editor-prepared scores as the primary supervision source;
human players are a release spot-check, not a per-score annotation workforce.

## Scope

1. A deterministic corpus contract for score identity, source digest, licence,
   edition, grouping, style, grade system, notes, and optional left-hand
   fingering annotations.
2. MusicXML/MXL extraction of explicit left-hand finger, string, fret, barre,
   tempo, and score-level metadata without treating missing annotations as
   negative labels.
3. Piece/composer/edition-grouped train, development, and test splits. Notes or
   windows from one work may never cross splits.
4. A score-supervised left-hand selector trained only on generic ergonomic and
   temporal features. It may choose only a complete-Oracle non-RED finalist and
   may not use title, corpus, composer, grade, style, key, or melody identity.
5. A separate ordinal difficulty estimator calibrated on one named grading
   system at a time. It supplements the existing deterministic tier gate; it
   does not change fixed-score fingering or turn a statistical estimate into a
   playability guarantee.
6. Versioned model artefacts, reproducible offline evaluation, production
   provenance stamps, API evidence, and a compact workspace presentation.

## Initial evidence sources

- Mutopia's public-domain Carcassi Op. 59 edition supplies explicit left-hand
  fingers. Each edition is a demonstrated expert solution, not the only valid
  solution.
- Guitar Loot scores labelled on the Delcamp 1–10 scale supply difficulty
  supervision under their recorded attribution terms. Raw scores are fetched
  locally and are not redistributed.
- GuitarSet and EGSet12 remain string/fret engineering audits. Because they do
  not contain left-hand finger numbers, they are not left-finger training data.

Post-closure note: a performer-disjoint, versioned GuitarSet aggregate now
calibrates rhythm phases only. Jazz is direct Jazz evidence; the public R&B
control is explicitly a Funk-adjacent proxy. Raw JAMS/audio still do not enter
agent prompts or targets, and this use does not turn GuitarSet into left-finger
supervision.

## Acceptance rules

- Every admitted corpus row has a stable group id, content digest, direct
  source, and non-empty licence statement.
- Corpus builds and split assignments are byte-deterministic.
- Evaluation reports coverage and unsupported reasons; absent fingering labels
  are never scored as finger 0 or as an error.
- The left-hand selector must improve held-out annotated-note agreement over
  the current solver without lowering Oracle status, losing pitches/rhythm, or
  regressing the existing public-domain Carcassi fixture.
- Difficulty evaluation is composer-grouped and reports exact grade, within-one
  grade, mean absolute error, and three-band accuracy. Promotion requires at
  least 70% within-one grade on the untouched test split and no use of dummy or
  model-generated labels.
- Production continues to expose the hard beginner/intermediate/advanced gate.
  The calibrated grade is labelled as an estimate with its system, model
  version, and training scope.
- No reinforcement learning, remote fine-tuning, database, account system,
  crawler framework, or general LilyPond implementation is introduced in this
  plan.

## Delivery order

1. Corpus schema, MusicXML/MXL reader, manifest loader, split, and tests.
2. Frozen Carcassi reference corpus and baseline report.
3. Left-hand finalist features, fit/evaluate script, frozen model, and guarded
   runtime selection.
4. Delcamp feature snapshot, grouped ordinal fit/evaluate script, frozen model,
   and runtime estimator.
5. Typed service/API/Web evidence, focused regression, offline receipts, and
   project-state update.
