# Plan 7B acceptance — published-score supervision

Status on 2026-07-25: **CLOSED — SOFTWARE AND OFFLINE EVIDENCE GATES
PASSED**.

Plan 7B uses licensed, editor-prepared scores as the primary supervision
source. It does not require a person to re-label every score. Human playing is
reserved for release spot-checks and for claims that symbolic scores cannot
establish, such as population-wide comfort or style authenticity.

## Accepted data contract

- The deterministic MusicXML/MXL corpus reader retains work, edition, source,
  licence, content digests, composer grouping, split, notes, and optional
  fingering annotations. Missing fingering is unlabelled, never finger 0 and
  never an error.
- The checked Public Domain corpus contains 21 works, 3,483 notes, and 707
  explicit left-hand annotations. The two canonical artifacts are:
  - `carcassi_op59.json`: 16 works / 451 annotations, SHA-256
    `4f906864d68ed9272a4ed74be11743b080d2e5e8519e1125ffae9c8fd9363908`;
  - `mutopia_pd_additional.json`: 5 works / 256 annotations, SHA-256
    `4bf36f7633693f4b02f19bccb1f8ccf704de47915bec2e4bab3f25fad7997e37`.
- Work, edition, and composer groups cannot cross splits. Corpus builds and
  split assignments are byte-deterministic. Full source and licence records
  are in [`../data/score_corpus/SOURCES.md`](../data/score_corpus/SOURCES.md).

## Published-fingering selector

`published-fingering-ranker@0.1.0` resolves near-ties only inside the complete
Oracle GREEN finalist pool used by balanced fingering. Its model SHA-256 is
`10bd1f9c2751417c5ef3a5f360da5696f736cc24db838857b9d2dd058b6cfed0`.

- The 43 inputs are generic solver-cost and finger/fret-count features. Title,
  corpus, composer, edition, style, grade, key, pitch sequence, and melody
  identity are excluded.
- The selector abstains below four attack onsets or two distinct attack
  geometries. It cannot raise max fret, add awkward-fingering events, or add
  more than six left-hand-effort points. Special technique profiles still
  take precedence.
- On held-out composer groups, exact annotated-finger agreement improved from
  29/60 to 30/60 on development and from 30/78 to 32/78 on test, with zero
  Oracle-status regressions. Training improved from 224/501 to 236/501.
- Coverage is explicit: 351 windows and 639 pressed-finger labels were scored;
  18 open-string labels and 50 labels without a GREEN window were unsupported.
- The separately reviewed whole Carcassi Prelude 1 fixture remains 17/21 exact
  and Oracle AMBER. It did not regress and is not promoted to a human guarantee.

The production solver stamps are `fingering-solver@0.6.0` and
`score-solver@0.4.0`. Pitch, rhythm, tuning, and the complete Oracle remain the
hard contract; the learned selector cannot make an infeasible candidate valid.

## Published-grade estimate

`published-grade-estimator@0.1.0` is separate from both fingering and the hard
beginner/intermediate/advanced tier gate. Its model SHA-256 is
`a3bb39aaf5f881513ed0141d20b3e3776c8b38357dd11351681c38701dddf16a`.

- The reference is the Delcamp/Eric Crouch 1–10 system: 427 attributed
  classical-guitar scores and 1,108 extracted feature rows. Raw scores and the
  feature snapshot are not redistributed.
- The split is composer-grouped: 319 train / 82 development / 26 untouched
  test pieces across 87 / 19 / 18 composers.
- Test results are 50.0% exact, 88.46% within one grade, 0.615 mean absolute
  error, and 65.38% three-band accuracy. This passes the preregistered 70%
  within-one threshold without dummy or model-generated labels.
- API and Web show the grading system, estimated grade, likely ±1 interval,
  band, low-confidence marker, model version/hash, burden percentile, and
  training scope. The existing deterministic tier result remains visible and
  authoritative for the requested product gate.

The reference set has one curator and classical/composer confounding, so this
is a corpus-calibrated estimate rather than a universal player difficulty
claim.

## Product integration and validation

The service serializer, OpenAPI contract, strict Web decoder, scorecard, and
workspace all expose the two new model stamps and the separate grade estimate.
Fingerstyle/Classical/Jazz/R&B generation and player/technique controls remain
independent: difficulty shapes the generated score; a fixed score's finger
numbers depend on that score, tuning, capo, physical profile, context, and
technique preference—not its grade label.

Final focused evidence:

- 380 relevant Python tests passed; the only warning is the known third-party
  Starlette/httpx2 deprecation.
- 70 Web tests passed; TypeScript and the production build passed.
- The full frozen Carcassi evaluation passed at 17/21 with no regression.
- Both model builders reproduce byte-identical artifacts; Ruff, strict mypy,
  and `git diff --check` passed.
- No real proxy-model call, reinforcement learning, remote fine-tuning,
  database, crawler framework, or general LilyPond runtime was introduced.

Reproduce the core evidence with:

```bash
uv run python scripts/build_published_fingering_ranker.py
uv run python scripts/evaluate_left_hand_reference.py
uv run pytest -q tests/test_score_corpus.py tests/test_score_corpus_data.py \
  tests/difficulty tests/solver
cd web && npm test && npm run build
```

The generic corpus builder takes `MANIFEST OUTPUT` arguments. Rebuilding the
grade artifact additionally takes the owner-held licensed feature snapshot:
`uv run python scripts/build_published_grade_model.py FEATURES.csv`. Its input
digest and upstream commit are frozen in the model artifact.

Agreement with one published edition is evidence of an expert-supported
choice, not proof that it is the only valid fingering. Broader styles,
publishers, and grading systems should be added as licensed grouped corpora;
they do not require per-score manual relabelling.

## Post-closure licensed expansion

Later on 2026-07-25, 37 movements from 35 Mutopia CC BY-SA editions were added
without changing this historical acceptance receipt. The combined normalized
corpus now has 58 examples, 17,944 notes, 2,483 technical annotation rows, and
2,362 pressed-finger labels. An expanded candidate reached 1,035 windows and
2,080 scored labels but did not retain the frozen held-out gains, so the
accepted production model above remains unchanged. See
[`experiments/2026-07-25-score-corpus-expansion.md`](experiments/2026-07-25-score-corpus-expansion.md).
