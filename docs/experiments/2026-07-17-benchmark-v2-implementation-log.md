# Benchmark v2 implementation log

## 2026-07-17 — Tasks 1–4

- Entry receipts remain the pushed MIDI SHA `46ff8ac070e97422b4aecf5c0f2a22b588a5fda4`
  and the pushed human-readable benchmark plan SHA
  `44927517958ecd3b9868bafb7bfe6133be25cc8e`.
- Task 1 froze strict notegraph/corpus/generator contracts and the deterministic
  procedural family schedule.
- Task 2 froze observable logical calls/attempts, bounded provider observations,
  candidate trajectories, and exact resume source bindings.
- Task 3 froze the shared ten-sample pool, paired repair/search/critic derivations,
  matched-budget controls, raw/pure baselines, and causal versus deployment cost views.
- Task 4 implemented the preregistered intervals/tests/bootstrap/Holm rules, durable
  WAL and complete-unit resume, canonical rows/blobs/observations/receipt, deterministic
  report generation, and full-rescore versus explicit fast-reaggregate CLI replay.

Task 4 closing evidence:

- `445 passed` for the current complete `tests/bench` suite after the provenance
  simplification below.
- `318 passed` for the affected agent, LLM, and solver suites.
- Ruff, strict mypy over all 87 source files, lock check, and Markdown-link check passed.
- Two clean stub collections produced byte-identical seven-file canonical directories.
  Full replay reproduced report JSON and Markdown byte-for-byte; two fast replays were
  also byte-identical.
- The statistics, runner lifecycle, artifact/report seam, and final Task 4 acceptance
  reviews all closed with zero unresolved findings.

No real model or provider network call was made. No frontend surface changed. The work
remains uncommitted by design until the Task 7 runner-ready gate; Task 5 is next.

### Provenance simplification

Before Task 5 implementation continued, the runtime checkout bootstrap was removed at
the user's request. It had issued three short-lived, read-only Git queries and its tests
created temporary Git repositories. Runtime provenance is now a pure typed declaration
of the execution SHA already accepted by the Task 7 external release gate; it performs
no Git, subprocess, filesystem, network, or import-path inspection. The replacement has
10 directed tests and passed Ruff plus strict mypy.

The same user-directed boundary applies to Task 5 acquisition: fixed HTTPS URLs,
expected SHA-256 values, byte caps, and a fresh output directory are retained for
reproducibility; DNS/public-IP, proxy, redirect, and exclusive-write security layers
were removed before any model outcome was collected.

## 2026-07-17 — Task 5

Task 5 closed the corpus layers and contamination boundary without a model/provider
call:

- The license census records source URL, retrieval date, upstream revision, raw hash,
  SPDX expression, attribution, three separate use permissions, explicit role map,
  normalization, and typed inclusion/exclusion reason. No lead-sheet candidate met the
  license/evidence contract; it remains unavailable rather than being replaced by a
  hand-authored sample.
- The public secondary corpus contains one CC0 OpenScore Beethoven MXL and two CC-PDDC
  Mutopia Bach MIDI files. The benchmark-only router/adapter versions are
  `benchmark-public-router@0.1.0` and `benchmark-public-adapter@0.1.0`. Role assignment
  is entirely census-driven; no pitch/order/name/density or harmony inference exists.
- MIDI parsing disables music21 post-quantization, coalesces explicit ties, and binds
  duration to raw end-of-track ticks. Explicit MusicXML chord symbols are never expanded
  as sounding notes. The strict product `midi@0.1.0` importer continues to reject the
  two multi-stream public MIDI files.
- Contamination reports preserve independent real/procedural findings and denominators.
  A separate denominator-free cross-stratum gate rejects exact/near, transposition,
  tempo, item-ID, and producer/root collisions without producing a pooled score.
- The offline builder writes canonical corpus/datasheet/census/contamination/receipt
  artifacts to a fresh directory. It requires the census normalization tuple to equal
  the executed adapter/container/normalizer pipeline and cleans a simulated partial
  write so the same path is retryable.

Task 5 closing evidence:

- Two default builds were byte-identical at all five canonical artifacts and contained
  503 items: 500 procedural families plus three public controls.
- Corpus SHA: `b4e2a1ed05eb07d82bdea18b9105cdd92b564cf864d8acedaa3c37d820848e8b`.
- Source-census SHA: `aa10f8d60b35d1c687806c0426bf50a2d30488d84b1f23317f72fc7dcceee372`.
- Real, procedural, and cross-stratum contamination gates were clean with zero findings.
- The complete benchmark suite passed `504` tests. Ruff, strict mypy over 92 source
  files, lock check, and the Markdown link check over 34 files passed.
- Independent source/license, adapter/normalizer, contamination, and builder reviews
  closed with zero unresolved blocker, important, or minor findings.

No frontend surface changed. Task 6 is next; the tree remains intentionally uncommitted
until the Task 7 runner-ready gate.

## 2026-07-17 — Task 6

Task 6 closed the checker-vs-judge software boundary without a real model/provider
call:

- The formal label contract records family, adversarial class, profile fingerprint,
  agreement state, provenance, and an exact proposition hash. That proposition binds
  canonical Tab, quarter-note Tab/tempo units, exact meter, profile, and the fixed
  `EXHIBITED_ONLY` fingering policy. Human aggregate states include explicit
  `UNCERTAIN`, but only agreed/adjudicated binary labels may later enter a confirmatory
  denominator.
- Exact parsing accepts only raw `PLAYABLE` or `UNPLAYABLE`; every other returned
  string is `INVALID`, and call failures remain separate. Each item/condition/judge
  cell is scheduled before execution with five repetitions, and every repeated result
  contributes to the declared pairwise flip-rate calculation.
- The frozen zero-shot and rubric prompt hashes are
  `f634b851f8a7a115402363c93547877edf9945ec7e7893ac8ba2c09d46c7f89e` and
  `361c34103c4e16ac87c9cc62b0c7822623778ab3ea5c21994a18fec367acccbc`.
  Request hashes additionally bind the requested model, eight-token ceiling, and
  temperature `0.8`; rows retain tab hash, tempo, meter, prompt/model stamps,
  usage/cost availability, and the complete repeated result.
- Software results are labeled exactly `SOFTWARE_FIXTURE_ONLY`. Even two authorized
  fake clients with distinct model strings keep cross-provider status `UNAVAILABLE`;
  fixtures cannot manufacture a provider comparison or checker-superiority result.
- `data/gold/README.md` now separates the six legacy constructed rows from the formal
  future human record. It freezes same-profile observation aggregation, exact time and
  fingering semantics, disagreement/adjudication rules, and external Task 7 execution
  SHA injection. The profile-assignment protocol and real observations remain open.

Task 6 closing evidence:

- `19 passed` for the directed checker-vs-judge suite, `519 passed` for the complete
  `tests/bench` suite, and `2361 passed` for the final repository-wide offline suite.
- Ruff, strict mypy over 91 source files, lock check, and the Markdown-link check over
  34 files passed.
- Independent experiment-semantics and human-contract reviews closed with zero
  blocker, important, or minor findings. A separate lean-runtime audit also found no
  Task 5/6 Git, subprocess, import-path, or excess download-security machinery.
- Provider-returned model/cache usage joins remain a Task 7 integration item through
  the already frozen Task 2 observation contract; Task 6 does not fabricate them.

No frontend surface changed. No Git, network, or provider command was run. Task 7 is
active, and the tree remains intentionally uncommitted until its runner-ready gate.
