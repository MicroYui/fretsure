# Benchmark v2 — versioned evidence, paired ablations, and honest replay

> **Status (2026-07-17): FROZEN FOR IMPLEMENTATION.** The strict MIDI stage is
> closed at `46ff8ac070e97422b4aecf5c0f2a22b588a5fda4`, with local, tracking, and
> remote SHA equality. This document is the new benchmark-v2 contract required
> by the roadmap; the historical 2026-07-10 Plan 4 remains an implementation
> record and its numerical tables are not a current baseline.
>
> **Implementation progress (2026-07-18): Tasks 1–8 complete; Task 9 formal runner
> implemented, formal collection not started.** Strict
> corpus/generator contracts, observable trajectories, the shared ten-sample pool,
> baselines, registered statistics, durable artifacts, deterministic reports, and
> full/fast CLI replay have passed their directed and independent gates. The licensed
> 500-procedural + 3-public corpus, exact role-map normalization, contamination controls,
> canonical builder, datasheet, and checker-vs-judge software boundary have also
> closed. Task 7 froze the 0.6.0 runner, preregistration, budget, distribution, and
> full-size deterministic stub artifacts. Its full replay, final gates, and external
> push have closed. After the user's next instruction, Task 8 froze a separate 2×2
> operational pilot, exact pricing/budget arithmetic, WAL/resume path, and explicit
> spend-confirmation boundary. The user then selected official current-model pricing
> as the reference basis; a dated `gpt-5.6-sol` contract and a pre-network 4,096-token
> input ceiling compute a `$10.960896` per-collection-attempt pilot maximum. The user
> authorized attempt 001 and the proxy returned canonical `gpt-5.6-sol`, but collection
> stopped at 0/8 rows after a valid empty diagnostic-code list exposed a trace-validator
> inconsistency. The minimal fix passed the full offline gate and attempt 001 remains
> terminal. After disclosure of the `$11.574272` cumulative two-attempt ceiling (excluding
> Task 9), the user separately authorized attempt 002's `$10.960896` ceiling; attempt 002
> completed all 8/8 rows. The pilot pricing contract remains
> `c93229c60003905d0946bd4d66096943a337a3763839715f296ecb338148baa5`.
> A separate formal billing envelope and non-authorizing gate compute the formal worst
> case. Task 9 now enforces its runtime input guard, and the user independently
> authorized all project model billing. Runner release gates have passed; a clean pushed
> runner SHA remains before collection.
>
> **Runtime provenance correction (2026-07-17, before any model outcome):** per the
> user-approved simplicity boundary, runtime collection/replay must not spawn Git or
> inspect checkout state. Task 7's explicit external release gate verifies cleanliness
> and SHA equality once; the runtime manifest only records that accepted SHA and its
> analysis/package bindings.

**Goal:** turn the existing benchmark skeleton into a versioned evidence system
that preserves item-level outcomes, measures repair/search/critic effects on shared
samples, emits complete uncertainty and availability-aware strata, and can
deterministically regenerate every aggregate from frozen rows. Then run the current
canonical model without rewriting legacy evidence or claiming human validation that
does not exist.

**Architecture:** a strict corpus snapshot and checked-in preregistration produce an
immutable pre-call config manifest. Collection uses a single-writer write-ahead journal
and records canonical candidate, raw-baseline, deterministic-baseline, and judge rows
plus a private operational sidecar. A post-run receipt—not the pre-call manifest—binds
observed model metadata, completion status, and every finalized hash. Aggregation is a
pure deterministic rescore/reaggregate function of the config, corpus, rows, and
sanitized observations. Proposal, repair, search, and critic comparisons reuse one
ordered proposal pool per item so stochastic draws are not confounded with the
capability under test.

This plan changes no Web surface. It produces CLI/JSON/JSONL artifacts and Markdown
evidence only. Any benchmark dashboard, chart page, live A/B, leaderboard, or new
visual control is Plan 6B work and requires prior user design confirmation against
the existing “classical luthier workshop × verification instrument” visual system.

## 1. Entry receipt and evidence boundary

- Prerequisite SHA is exactly
  `46ff8ac070e97422b4aecf5c0f2a22b588a5fda4`; do not amend the pushed MIDI commit.
- Before implementation, commit and push this plan plus the stale project-status
  corrections, then verify `HEAD == @{u} == ls-remote`. That commit is the
  human-readable preregistration receipt; implementation cannot start before equality.
- Before any headline proxy collection, commit and push the finished runner, corpus,
  machine-readable preregistration, tests, and distribution. The preregistration
  freezes a rule that the pre-call config must bind the then-clean `HEAD`; that config
  records the execution SHA without editing the preregistered file. Results
  live in a later commit, avoiding a self-referential
  “result file contains its own commit SHA” fiction.
- Legacy `oracle@0.1.0`, unversioned-fidelity, `claude-opus-4-8`, and current-shape
  smoke tables remain byte-for-byte historical evidence. They are neither pooled with
  nor compared definition-for-definition to v2.
- A repeated real-model call is stochastic. “Reproducible” means identical corpus,
  schedule, configuration, scoring, and deterministic aggregation; it does not mean
  that invoking the provider again must reproduce the same completion.
- Exact numerical replay means recomputing a report from the frozen raw rows, never
  silently recalling the model.

## 2. Version decisions

The public benchmark CLI and artifact contract change materially, so the runner-ready
commit will bump package `0.5.0 → 0.6.0` and introduce:

- `benchmark-notegraph@0.1.0`
- `benchmark-corpus@0.1.0`
- `benchmark-manifest@0.1.0`
- `benchmark-row@0.1.0`
- `benchmark-observations@0.1.0`
- `benchmark-receipt@0.1.0`
- `benchmark-report@0.1.0`

Keep these semantics unchanged and stamp them in every applicable config/row/receipt/report:

- playability `oracle@0.2.0`
- public tab input `tab-input@0.2.0`
- faithfulness `fidelity@0.3.0`
- score router `score-input@0.1.0`
- trace `agent-trace@0.2.0`
- profile `median@0.1` plus canonical fingerprint
- requested proxy model `gpt-5.6-sol`

Do not change exact-onset faithfulness, its thresholds, oracle semantics, or the
profile to improve results. DTW/grid tolerance would require a separate fidelity
version and a later preregistration.

## 3. Frozen experimental unit and headline discipline

### 3.1 Units

- The independent inferential/resampling unit is `cluster_id`/`family_id`. The primary
  procedural stratum contains exactly one item per family and one family per cluster.
  Ten `sample_index` observations are nested repeated proposals inside that family;
  they never turn inferential N from the family count into ten times that count.
- For arrangement trials, `sample_index` is also the ordered candidate index: there is
  one pool of ten proposals per item, not ten pools of ten candidates.
- All ten confirmatory proposals use the identical request and fixed temperature
  `0.8`; a preregistered outcome-independent permutation defines each item’s k-prefix,
  and a preregistered interleaving schedule mitigates time/order drift. A heterogeneous
  rising-temperature pool would violate the exchangeability required by the HumanEval-
  style estimators and is not used for pass@k/pass^k.
- Each candidate begins with one frozen target proposal. The no-repair outcome is its
  iteration-zero solve/oracle result; the repair outcome is the terminal state reached
  from that exact target. This is the primary paired repair comparison.
- Best-of-k (`k = 1, 2, 4, 8`) and critic-on/off select over prefixes of that same
  ordered repaired candidate pool. They do not trigger independent proposal draws.
- The controlled `full` selection is repaired best-of-4 with critic enabled. It is the
  benchmark configuration, not a claim that the product’s rising-temperature schedule
  was repeatedly sampled; all k values remain visible instead of promoting only the
  favorable point.
- Reliability k is every integer `1..10`; search k is `1,2,4,8`. The canonical M2
  reliability curve is terminal-repaired GREEN `pass@k`/`pass^k`, while terminal
  joint-success is its mandatory faithfulness guard. Separately named initial-GREEN,
  initial-joint, terminal-joint, raw-GREEN, and raw-joint curves prevent the generic
  word “pass” from changing predicates between tables. These are candidate-policy
  estimands, not repeated full-system reliability.
- Raw-LLM uses the same item, source context, requested model, sample schedule, and
  bounded output policy, but emits unverified tab directly. Pure-solver is deterministic
  and runs once per item, then is expanded only as a deterministic baseline—not as ten
  fake independent samples.

### 3.2 Primary and secondary evidence

- Primary software/statistical suite: a design target of 500 independently seeded
  procedural families with full melody/bass/harmony evidence, `n_samples = 10`, and
  the fixed schedule above. The machine preregistration may only increase that balanced
  family count if the frozen power gate requires it; it may never reduce N after seeing
  pilot or formal outcomes. Programmatic data is the contamination-resistant headline
  stratum.
- Public lead-sheet, public-classical, and public-MIDI layers are separately reported
  secondary generalization strata. Only license/provenance-audited files enter them.
- DadaGP/GuitarSet-style tab sources are checker-validation strata, not source-to-tab
  arrangement items. Never mix their denominator into arrangement success.
- Melody-only MIDI is its own `melody` evidence signature. It may contribute a
  melody-only joint result, but it is never pooled with three-dimensional fidelity as
  one headline rate.
- Difficulty labels without expert calibration are named `synthetic_complexity` or
  `unrated`, never “human beginner/intermediate/advanced” evidence.
- `median@0.1` remains the sole confirmatory generation/scoring profile. After rows are
  frozen, every stored tab is deterministically rechecked under `small@0.1`,
  `median@0.1`, and `large@0.1` with all fingerprints. That is model-sensitivity
  analysis, not human calibration, and it never recalls the LLM.
- Every headline carries an interval and exact numerator/denominator. No mixed pooled
  single number may hide layer, family, complexity, polyphony, or evidence availability.

### 3.3 Preregistered capability decisions

- **Repair primary endpoint:** paired change in candidate-level joint success from
  iteration zero to terminal repair. Average the ten paired binary deltas within each
  family, then use the family-level sign-flip test; raw discordant counts remain visible
  but are not treated as independent families. The repair SESOI is +0.10 absolute joint
  success. KEEP requires point delta at least the SESOI, Holm-adjusted one-sided
  `p < 0.05`, a one-sided 97.5% family-bootstrap lower bound above zero, and the
  budget-matched guard below. Otherwise report the null/negative result; do not move
  the threshold.
- **No-cheap-remedy guard:** before calls, compute per item the largest prefix
  `m <= 10` of high-temperature no-repair proposals and, separately, raw-LLM proposals
  whose worst-case logical calls and requested-token ceilings fit one proposal plus
  the full eight-edit repair budget. Compare terminal-joint `pass@1` with
  initial-joint `pass@m_no_repair` and raw-joint `pass@m_raw` using paired family
  bootstrap differences. Each guard requires point margin at least +0.05,
  Holm-adjusted one-sided `p < 0.05` within the two-control guard family, and a
  one-sided 97.5% lower bound above zero. A tie, missing control, or prefix capped at
  ten before spending the matched budget yields `INCONCLUSIVE`. Actual observed-cost
  Pareto remains a separate sensitivity view; shared collection cost is never
  mistaken for deployment-arm cost.
- **Best-of-N primary endpoint:** paired joint-success change for shared-pool best-of-1
  versus best-of-4. There is one selection pair per independent family, so report
  discordant counts, exact McNemar inference, and a family-bootstrap interval. The
  causal selector comparison charges both arms the same first-four trajectory pool so
  only selection changes; a separate deployment view charges best-of-1 only prefix one
  and best-of-4 prefix four. The search SESOI is +0.05 absolute joint success. KEEP
  requires point delta at least the
  SESOI, Holm-adjusted one-sided `p < 0.05`, a one-sided 97.5% lower bound above zero,
  and a non-dominated point on the actual prefix-cost Pareto using logical calls,
  tokens, and latency. If usage/cost is unavailable, the decision is
  `PROBATION_COST_UNKNOWN`. The remaining k values are the frozen curve; GREEN change
  is diagnostic because its non-negative direction is structural.
- **Critic software endpoint:** paired change in the critic-selected score on the same
  pool plus joint/fidelity side effects and a family-cluster bootstrap interval. Its
  self-score direction is structural, so no p-value on that direction enters the
  confirmatory family or a KEEP decision. This is selection sensitivity, not external
  musicality evidence. Its decision is `HUMAN_BLOCKED/PROBATION`; it cannot graduate
  without human blind A/B.
- **Reliability:** emit the complete pass@k and pass^k curves for every valid k, never
  only pass^8. Counts insufficient for a k are N/A, not zero.
- **Multiplicity:** the confirmatory family is repair joint delta and best-of-4 joint
  delta; Holm–Bonferroni covers those frozen tests. Critic self-score, raw-LLM, public
  strata, and other k points are diagnostic/exploratory and labeled as such.
- **Missingness/ITT:** only a structural source-evidence dimension frozen in the corpus
  before calls may be excluded. Every transport/parse/fallback/no-tab/scoring event
  remains in the same ITT denominator. If deterministic fallback still returns a valid
  tab, the primary end-to-end arm scores that tab normally and marks
  `fallback_assisted`; only the separately named LLM-only sensitivity counts fallback
  as failure. An outcome without a valid scorable tab is a failure in every structurally
  applicable binary denominator. Continuous fidelity reports both a conditional mean
  among scored tabs and a failure-inclusive composite with zero assigned only for
  structurally applicable failed outcomes; no arm-specific complete-case denominator
  is allowed. An orphaned call makes a formal run incomplete rather than an outcome
  that may be dropped or silently replaced.

### 3.4 Frozen inference map and power gate

- One-outcome-per-family binary arm rates report Wilson 95% intervals as the primary
  display and Clopper–Pearson 95% as an exact small/zero-cell sensitivity interval.
  Neither is applied to the `N × 10` nested candidate rows as if they were independent.
- pass@k/pass^k U-statistics and paired risk deltas use a deterministic, generator-
  stratum-preserving family bootstrap with 10,000 resamples. Degenerate samples return
  an explicit point interval/status. Two-sided 95% intervals are descriptive; the
  preregistered one-sided 97.5% lower bounds are decision guards.
- Bootstrap input is first reduced to one equal-weight value per musical family, then
  whole clusters are resampled with replacement inside the frozen
  `(layer, evidence_signature, synthetic_complexity, polyphony)` strata. Quantiles use
  the type-7 linear rule over sorted replicates. Conditional fidelity averages only
  scored, structurally applicable values within each family and always appears beside
  the equal-family-weight failure-inclusive composite; it never enters a binary KEEP
  rule or replaces an ITT denominator.
- Repair uses an exact sign-flip test when at most 20 family deltas are nonzero and a
  frozen-seed 100,000-draw sign-flip test otherwise, with `(extreme+1)/(draws+1)`.
- The two no-cheap-remedy guard p-values use that same one-sided family-level sign-flip
  rule on their paired per-family pass-estimand differences, while their uncertainty
  intervals remain the preregistered paired cluster bootstrap. They form their own
  two-test Holm family, separate from repair plus search.
- Best-of-4 uses one-sided exact McNemar on family discordances. Report the matched
  odds ratio with a two-sided exact 95% interval; zero cells yield explicit zero/
  infinity bounds, never an unstated pseudo-count. The continuity-corrected chi-square
  remains compatibility-only.
- The deployment Pareto maximizes equal-family joint success while minimizing mean
  logical calls, complete provider token usage
  `(input + output + cache-create + cache-read)`, and summed model-call latency. Any
  nullable token component makes the formal token/cost dimension unavailable rather
  than zero. Best-of-4 is non-dominated only when no frozen k point is at least as good
  in success and no worse in every available cost dimension, with one strict advantage.
- Holm-adjusted p-values control the two confirmatory tests at family-wise alpha 0.05.
- Before any proxy outcome is inspected, the machine preregistration must demonstrate
  at least 80% power using conservative per-test alpha 0.025, repair SESOI 0.10,
  search SESOI 0.05, search discordance 0.15, repair within-family ICC 0.25, and the
  frozen ITT rules. The initial N is 500 families. If exact/simulation power is below
  80%, increase balanced N and re-freeze config/budget before collection; never select
  N from operational-pilot outcome rates. Report sensitivity to the frozen assumptions.

## 4. Artifact contracts

### 4.1 Corpus snapshot

Each strict `CorpusItem` records:

- stable `item_id`, `family_id`, `cluster_id`, ordered position, layer, genre,
  `synthetic_complexity`, polyphony stratum, and evidence signature;
- canonical notegraph and item SHA-256;
- source format, source/root SHA-256, router/importer and container versions where
  applicable;
- provenance URL or local producer identity, retrieval date, per-file license/status,
  split, and an explicit role-map/normalization record;
- generator version and full validated `GenConfig`, including the actual derived seed;
- tempo and every source-evidence availability dimension.

The ordered corpus manifest has its own SHA-256. Exact duplicate notegraphs and shared
musical families are detected before split assignment. The two frozen MIDI producer
positives represent one musical family, not two independent samples.

`notegraph_to_ir` is a strict public-artifact parser: exact field sets and types,
bounded arrays/strings/integers/fractions, canonical order, finite tempo, supported
roles, and a final `snapshot_music_ir`/`validate_ir` gate. It must never use permissive
`int(...)`, `float(...)`, or `Fraction(...)` coercion on hostile artifact fields.

### 4.2 Immutable pre-call config manifest

The config manifest exists and is hashed before the first call. It binds:

- separately named `plan_git_sha`, `prereg_file_sha256`, clean
  `execution_git_sha`, `uv_lock_sha256`, analysis-module/wheel-RECORD digest, and
  package/Python/OS/architecture (never host, user, HOME, or an absolute path);
- every semantic version/fingerprint listed above, ordered corpus SHA, and item hashes;
- requested model plus an exact allowed returned-model rule—not an observed value;
- seeds, sample/permutation/interleaving schedule, temperature, k values, repair budget,
  prompt-template hashes, max-token rules, timeout/retry/fallback policy, and arms;
- endpoints, SESOIs, alternatives, inference map, power assumptions, bootstrap/
  permutation seeds and repetitions, multiplicity family, and ITT/missingness rules;
- maximum logical calls, attempts, response bytes, output tokens, wall time, and spend
  (or a pre-call `cost_contract_unavailable` flag).

The Task 7 release gate refuses a dirty source checkout or mismatched SHA/lock before
freezing the config. Runtime collection then validates the frozen SHA/lock/corpus/config,
requested model, prompt hashes, package/analysis binding, and reserved budget for the
next complete logical candidate unit without invoking Git or inspecting ambient import
paths. Replay and live collection therefore have no runtime `.git` dependency.

All canonical artifacts use validated NFC strings, UTF-8, LF, sorted exact keys,
compact JSON separators, `ensure_ascii=False`, `allow_nan=False`, base-10 exact
integers, and reduced
`numerator/denominator` fraction strings. The pinned Python minor/runtime owns finite
float rendering. Hashes are domain-separated as
`SHA256("fretsure:<schema-version>\0" || canonical_bytes)`; no artifact contains its
own hash.

### 4.3 Write-ahead journal, rows, blobs, and private observations

The single-writer journal is an integrity boundary:

- create the output directory as mode `0700` and evidence files as `0600`; acquire an
  exclusive writer lock and refuse symlink/FIFO/non-regular targets;
- fsync a unique `CALL_INTENT` with logical-unit ID, attempt ID, request digest, and
  worst-case budget reservation before network use; fsync its `CALL_RESULT` or typed
  failure immediately after return, then fsync parent directories on create/rename;
- count every intent/attempt against budget. Resume is allowed only when every intent
  has one terminal result and the hash chain is intact. An orphan intent makes a formal
  run `INCOMPLETE` and requires a fresh run ID; it is never overwritten, silently
  replaced, or converted into a model outcome.

Canonical row variants retain enough evidence to rescore and reaggregate:

- schema/run/item/family/cluster/row-type/sample/candidate/pair IDs;
- requested/returned model IDs subject to the allowed rule;
- prompt/reply digests and bounded parse/fallback status—no hidden reasoning and no
  credential, proxy URL/token, environment value, or transport exception text;
- canonical source notegraph plus initial target, iteration-zero tab, terminal target/
  tab, and raw-baseline tab stored inline or in bounded content-addressed blobs;
  target/tab/trace digests must resolve and verify;
- initial/terminal verdicts, bounded diagnostic codes, applied/rejected/no-op edit
  counts, termination reason, nullable authoritative fidelity scores, exact evidence
  partition, faithfulness passed, GREEN, joint success, critic status/score, and
  baseline provenance;
- search/critic selections are derived from ordered candidate rows rather than
  duplicated as independently sampled evidence.

The private call sidecar records logical calls/attempts, input/output/cache tokens when
returned, elapsed monotonic duration in integer microseconds (never raw clock values),
bounded returned-model metadata, and an optional hashed response ID. Unknown usage is
`null`, not 0. Public export omits response identity and contains no hostname, username,
HOME, absolute path, proxy URL, cookie, credential, or environment dump. Per-candidate
work—not only the winning trace—contributes to operational summaries.

A separate canonical `benchmark-observations@0.1.0` artifact is produced by fail-closed
sanitization of that sidecar. It retains only logical/attempt IDs, typed status, bounded
returned model, retry count, nullable token usage, and elapsed microseconds needed to
recompute public operational summaries. The private sidecar never enters replay or Git;
the sanitized observations artifact is hash-bound and is a required full-replay input.

### 4.4 Final receipt and deterministic report

After collection stops, `benchmark-receipt@0.1.0` binds the immutable config hash,
corpus hash, finalized journal/row/blob hashes, sanitized telemetry hash, observed
returned-model set, analysis code digest, exact expected/observed counts, and completion
status. An abort receipt preserves an incomplete run but cannot enter a report; only a
fully validated `COMPLETE` receipt owns a canonical raw-table hash. The pre-call
manifest never owns run-after facts.

Task 8 attempt 001 hit an unexpected local exception after closed calls but before the
collector published an abort receipt. Its byte-preserved private pre-call, manifest, and
WAL plus public hashes establish the incomplete boundary; no abort receipt exists, and
the run remains non-resumable and ineligible for reporting. This exception does not
relax the formal runner's abort-receipt or complete-receipt requirements.

Rows are uniquely keyed and deterministically sorted before finalization. Report replay
first resolves every blob and re-runs solver/oracle/fidelity (`rescore`), then derives
all arms/statistics from those verified outcomes (`reaggregate`). A separate fast
reaggregate-only mode is labeled as such and cannot serve as the scoring audit. The
report verifies config/receipt/schema/code bindings and exact expected-key coverage;
replay timestamps or host information never enter canonical output.

## 5. Task 1 — Strict corpus and generator contracts

**Files:**

- `src/fretsure/bench/contracts.py`
- `src/fretsure/bench/corpus.py`
- `src/fretsure/bench/generator.py`
- `tests/bench/test_contracts.py`
- `tests/bench/test_corpus.py`
- `tests/bench/test_generator.py`

**TDD/work:**

- Add schema constants, strict snapshots, canonical JSON/hash helpers, and bounded
  typed errors before changing the runner.
- Reject unknown keys, invalid meter/bars/seed, non-canonical fractions/order,
  duplicate IDs/hashes, invalid licenses, impossible evidence partitions, and hostile
  mapping subclasses. The generator must not silently map an unknown key to C.
- Derive source tempo per item and prove a 96-BPM item is evaluated at 96, not the old
  global 90-BPM `ArrangeGoal()` default.
- Freeze deterministic procedural stratification, family IDs, item seeds, canaries,
  complexity/polyphony/evidence labels, datasheet, and corpus hash. Double generation
  with the same config must be byte-identical. Canaries are non-secret public test
  strings that may be sent to the provider; they are not credentials or private data.
- Add mutation/property tests for notegraph round-trip and fail-closed resource limits.

## 6. Task 2 — Observable model calls and candidate trajectories

**Files:**

- `src/fretsure/llm/client.py`
- `src/fretsure/bench/observe.py`
- `src/fretsure/agent/harness.py`
- `src/fretsure/agent/arranger.py`
- `src/fretsure/agent/repair.py`
- corresponding agent/LLM/benchmark tests

**TDD/work:**

- Add a wrapper/sink that observes visible request/reply digests, timing, retry count,
  hashed provider response ID, returned model, and token usage when available while preserving the
  existing `LLMClient.complete() -> str` contract.
- Before joining, hashing, or parsing provider content, cap blocks at 64, each text
  block at 256 KiB, and total response text at `min(1 MiB, max_tokens * 32 bytes)`;
  bound response IDs to 512 printable characters and usage fields to exact nonnegative
  integers within the manifest ceiling. Provider `max_tokens` is not trusted as a
  transport-byte limit.
- The loopback HTTP client disables ambient proxy state and redirects, requests
  identity encoding, and caps the complete raw success/error response stream at 1 MiB
  before SDK parsing. Only connection failures or status 408/409/429/5xx are retried
  (subject to the provider's explicit `x-should-retry` override); permanent failures
  and transport/schema boundary failures make one attempt.
- Keep proxy credentials and raw transport exceptions redacted. Tests use fake SDK
  response objects; no telemetry field is trusted without exact type/range bounds.
- Make proposal parse failure and deterministic fallback explicit in benchmark
  observation without changing the product’s fail-safe fallback behavior.
- Expose an immutable candidate trajectory with the shared initial target,
  iteration-zero result, terminal repair result, critic result, all candidate trace
  steps, and total work. Refactor the production harness to consume the same primitive;
  differential tests prove existing `arrange` results/traces remain unchanged.
- Preserve public trace bounds while benchmark rows account for every pool candidate.
- Tests prove execution provenance is a pure declaration of the externally accepted
  SHA and installed-wheel binding; it performs no filesystem, import-path, Git, or
  subprocess inspection at runtime.

## 7. Task 3 — Shared-pool collection and baselines

**Files:**

- `src/fretsure/bench/experiment.py`
- `src/fretsure/bench/ablation.py`
- `src/fretsure/bench/baselines.py`
- `tests/bench/test_experiment.py`
- `tests/bench/test_ablation.py`
- `tests/bench/test_paired_ablation.py`
- `tests/bench/test_baselines.py`

**TDD/work:**

- Replace headline use of independently sampled leave-one-out arms with one ordered
  candidate schedule and explicit pair IDs. Retain the legacy helper only as a clearly
  labeled compatibility API if tests/users require it.
- Build no-repair/repaired, best-of-k, and critic-on/off outcomes from the same candidate
  trajectories. Prove no comparison makes an extra proposal call or changes pool order.
- Derive deployment cost for best-of-k from only its first k candidate trajectories,
  not from the shared ten-row collection; emit logical-call/token/latency Pareto points.
  Also emit the equal-first-four-pool accounting used by the causal best-of-1/4 selector
  test so “same experimental cost” and “actual deployment cost” cannot be conflated.
  Build the preregistered call- and token-budget-matched no-repair/raw prefixes and
  expose a typed censored status when ten samples cannot spend the matched budget.
- Keep `n_samples` semantically distinct from k even though each arrangement sample is
  one ordered candidate. Require `n_samples >= max(k)` for reliability output and fail
  closed otherwise; never create a hidden second candidate dimension.
- Score all three nullable faithfulness dimensions and exact availability counts. Test
  full-evidence MusicXML/procedural and melody-only MIDI separately; never pool their
  joint denominator.
- Test that a valid deterministic fallback is scored in the primary end-to-end arm,
  visibly tagged `fallback_assisted`, and counted as failure only in the separately
  named LLM-only sensitivity; invalid/no-tab fallback remains an ITT failure.
- Run raw-LLM and pure-solver baselines on the same item schedule. Give raw-LLM the same
  source facts and bounded capacity, while retaining its intentionally unverified-tab
  design. Record parse/fallback/usage honestly.
- Record deterministic B3/B4 baseline availability as `unavailable` with exact reason
  unless a license-audited reproducible adapter exists; do not fabricate a comparison.

## 8. Task 4 — Statistics, deterministic report, and CLI artifacts

**Files:**

- `src/fretsure/bench/stats.py`
- `src/fretsure/bench/artifacts.py`
- `src/fretsure/bench/reliability.py`
- `src/fretsure/bench/report.py`
- `src/fretsure/bench/runner.py`
- `tests/bench/test_stats.py`
- `tests/bench/test_artifacts.py`
- `tests/bench/test_report.py`
- `tests/bench/test_runner.py`

**TDD/work:**

- Implement/test Wilson and exact Clopper–Pearson intervals, exact paired McNemar
  inference and matched odds-ratio interval, deterministic family-cluster bootstrap,
  paired family-level sign-flip/permutation inference, and Holm correction. Fix the current
  continuity-corrected McNemar bug (`max(|b-c|-1, 0)^2/(b+c)`).
- Connect existing fail-closed pass@k/pass^k estimators to the six explicitly named
  predicates, emit reliability k `1..10` and search k `1,2,4,8`, and bootstrap their
  per-family estimates. Never attach a row-level Wilson interval to nested candidates.
- Aggregate only from validated rows. Emit exact counts, denominators, availability,
  intervals, strata, paired discordance/deltas, baselines, usage/latency, missingness,
  and keep/cut decisions mechanically derived from the frozen rule.
- Recheck every frozen tab offline under small/median/large profile fingerprints and
  report model sensitivity separately from the median confirmatory result.
- Make stub corpus/rows/report byte-identical across two clean runs. Stub timing,
  provider response identity, and usage are explicitly unavailable/null. Real call
  observations are canonicalized and hash-bound after collection; replaying those same
  frozen observations must reproduce their operational summaries byte-for-byte, while
  recalling the provider is a new run rather than a replay.
- CLI requires an output directory, writes config/WAL/rows/blobs/private sidecar/
  sanitized observations/receipt/report with no silent overwrite, validates only
  orphan-free resumptions, and finalizes canonical artifacts atomically. Full replay
  accepts config+receipt+rows+blobs+sanitized observations, performs no LLM call, and
  rescores before aggregation; a separately named fast mode only reaggregates. Both
  must regenerate their corresponding report byte-identically.
- Bound items, samples, candidates, rows, bytes, bootstrap work, calls, tokens, and
  journal recovery before allocation or network use. Add symlink/FIFO/path-race and
  partial/corrupt-journal tests using the project’s existing safe-file patterns.

## 9. Task 5 — Corpus layers and contamination controls

**Implementation status (2026-07-17): COMPLETE.** The default 503-item build is
byte-identical across two fresh runs, all three contamination gates are clean, and
independent source/normalizer/contamination/builder reviews have zero unresolved
findings. Exact hashes and exclusions are recorded in the corpus datasheet.

**Files:**

- `src/fretsure/bench/normalizers.py`
- `src/fretsure/bench/contamination.py`
- `scripts/build_benchmark_corpus.py`
- `data/benchmark/**`
- `docs/experiments/benchmark-v2-corpus-datasheet.md`
- corpus/normalizer/contamination tests

**TDD/work:**

- Implement A public lead-sheet, B public-classical, C public-MIDI, D checker-only tab,
  and E procedural manifests as distinct typed layers. Arrangement normalizers require
  an explicit checked-in role map; no automatic track-role or harmony inference.
- Fetch only immutable, license-compatible public artifacts with recorded source URL,
  retrieval date, upstream revision where available, raw SHA, and per-file license.
  A missing/ambiguous license is an exclusion reason, not “public domain” by guess.
- Treat first acquisition as census input. Reproducible fetches require a pre-recorded
  expected SHA-256, HTTPS host allowlist, one timeout, and per-file/total byte caps.
  This local, checked-in-source workflow deliberately does not add DNS, proxy,
  redirect, cookie, credential, or exclusive-file security machinery; the frozen hash
  is the content identity and the builder writes only to a fresh output directory.
- License rows freeze SPDX expression, attribution, redistribution, derivative-work,
  and provider-submission permission separately. A file that cannot lawfully be sent
  to the configured model is excluded from proxy collection even if local evaluation
  or metadata redistribution would otherwise be allowed. Retrieval dates are fixed
  manifest values, never rebuild-time timestamps.
- Public MIDI normalization is benchmark-internal and must not broaden or weaken the
  public strict `midi@0.1.0` importer.
- Split by musical family before variants; detect exact/near duplicates, transposition/
  tempo variants, canary leakage, item overlap, and producer duplicates. Report real
  and procedural strata separately, with a denominator-free cross-stratum collision
  gate so procedural items cannot silently duplicate public controls.
- Corpus build writes only a fresh directory, refuses overwrite, validates every
  artifact, and reproduces the ordered manifest/hash from pinned inputs.

If an upstream dataset is unavailable, too large to redistribute, license-ambiguous,
or cannot be normalized without semantic guessing, retain its adapter/test and record
the layer as externally unavailable. Do not substitute a hand-authored “real” sample.

## 10. Task 6 — Checker-vs-judge software boundary

**Implementation status (2026-07-17): COMPLETE.** No judge/provider call was made;
this task froze and tested the software boundary with deterministic fakes only.

**Files:**

- `src/fretsure/bench/checker_vs_judge.py`
- `data/gold/README.md`
- `tests/bench/test_checker_vs_judge.py`

**TDD/work:**

- Freeze a formal label/row contract suitable for later human collection, including
  adversarial class, family, profile, labeler agreement/status, and provenance.
- Parse only exact `PLAYABLE` or `UNPLAYABLE`; malformed output is `INVALID`, never a
  semantic substring match. Preserve each repeated judge result, flip rate, model
  stamp, usage, and cost availability.
- Freeze `zero_shot` and versioned `rubric` prompt conditions with exact prompt hashes,
  five repetitions per item/condition/judge, outcome-independent order, and FakeLLM
  coverage of flips/invalids. This freezes the eventual experiment shape without
  pretending constructed labels are human gold.
- Emit software-fixture evidence only until real guitarist labels exist. Constructed
  examples prove machinery, not that oracle beats an LLM judge.
- Task 6 software-fixture results always keep cross-provider comparison `unavailable`.
  A later live collection may lift that status only with independently versioned
  models, exact returned-model observations, and an explicit full call budget.

**Closing evidence:** the label proposition binds canonical Tab, quarter-note time and
tempo units, exact meter, profile fingerprint, and `EXHIBITED_ONLY` fingering policy.
Both prompt conditions use frozen hashes, every request binds model/max-tokens/
temperature, every cell has five pre-scheduled repetitions, and malformed/failing
outputs remain `INVALID`/`CALL_FAILED`. Human observations, provider-returned model
metadata, and empirical comparisons remain unavailable rather than inferred from
fixtures. Directed tests and two independent reviews closed with zero findings.

## 11. Task 7 — Runner-ready preregistration and first Git gate

**Implementation status (2026-07-18): COMPLETE.** The 0.6.0 runner,
preregistration, budget, distribution, deterministic full-size stub path, and
fail-closed live boundary are implemented. Runtime collection and replay remain
independent of Git and subprocess discovery. The full replay and final verification
receipt are complete; the external commit/push and SHA-equality check are the terminal
handoff operation rather than runtime behavior.

**Files:**

- `docs/experiments/2026-07-17-benchmark-v2-prereg.json`
- `docs/experiments/2026-07-17-benchmark-v2-budget.md`
- package/version/distribution manifests

**Acceptance before any headline collection:**

- Freeze the exact ordered primary corpus (500 independent procedural families unless
  the pre-outcome power gate requires a larger balanced N), public secondary corpus,
  ten exchangeable proposal samples at fixed temperature
  `0.8`, outcome-independent per-item prefix permutations, interleaved call order,
  reliability k `1..10`, search k `1,2,4,8`,
  arm definitions, call/token/time ceilings, statistical seeds/repetitions, SESOI/
  power calculation and assumptions, primary decisions, and ITT/missingness rules.
- Run two byte-identical full-size stub collections plus replay. Stub success is a
  software/determinism gate, never model-quality evidence.
- Run offline full suite, Ruff, strict mypy, lock/link/diff checks, build, distribution
  audit, and clean-install smoke matrix.
- Add a `[benchmark]` extra for live mixed-corpus collection (`anthropic`, score
  importers and exact music21 pin). Core-wheel replay/statistics must work without
  `.git` or network. Clean-install smoke core replay and `[benchmark]` stub/live-config
  fail-closed paths. Wheel/sdist must exclude private telemetry, raw formal results,
  and corpus bytes lacking redistribution rights while retaining schemas, fetchers,
  manifests, and licensed fixtures.
- Independent scope, security/privacy/resource, statistics/reproducibility, and
  release/consumer reviews report zero unresolved blocker/important/minor findings.
- Commit and push the runner-ready tree, then verify `HEAD == @{u} == ls-remote`.
  The immutable preregistration requires the subsequently generated pre-call config to
  record that exact SHA as `execution_git_sha`; do not edit the prereg file after
  seeing the commit ID.

**Frozen implementation evidence:**

- Preregistration SHA-256:
  `ad9129edfb47634085f7bfd5557ca76f59eb8358865a1742bfcba69fa0c1362b`.
  Budget Markdown SHA-256:
  `4814206e1b749a03e458822016b66caeb1cfb480e033111e05030ffafe372b19`.
- Each full-size stub run covered 503 items and 10,060 scheduled units, producing
  10,563 rows. All seven canonical files from runs A and B were byte-identical.
- `report.json` file SHA-256:
  `73d77442426eab0100ff55a551913c7656cfdd3e795106939e4085fc17e47d32`.
  Embedded `report_sha256`:
  `131c0b9bb5baf63f03100a85546e1edc48351615ae19e9ddea7f1b5cff2fb776`.
  Full-size stub run B elapsed 4,431.39 seconds.
- These are deterministic software artifacts, not model-quality evidence. No real
  provider/network collection occurred, and no frontend surface or design changed.

**Runner-ready closure state:**

- **Full replay complete:** the full rescore/reaggregate replay completed in
  `2454.226` seconds. All seven canonical outputs were byte-identical to collection A,
  including the `73d77442...d32` report JSON and `131c0b9b...776` embedded report
  contract hash.
- **Final gates complete:** the final offline suite passed `2415` tests with `8`
  integration tests deselected; the empty-provider integration boundary exited cleanly
  with those `8` tests skipped. Ruff, strict mypy (`94` source files plus both frozen
  build scripts), lock, preregistration, and `35`-file Markdown-link checks passed.
  The web suite passed `29` tests plus typecheck/build. Rebuilt distributions passed
  the `114`-entry wheel / `307`-entry sdist audit and the full isolated install smoke
  matrix. All four independent review lenses closed at 0 blocker / 0 important /
  0 minor findings.
- **Terminal push and pause:** commit and push this runner-ready tree, verify
  local/tracking/remote SHA equality, then pause and await the user's next instruction.
  The task handoff records the non-self-referential Git receipt. Do not proceed into
  Task 8 in the same execution sequence.

## 12. Task 8 — Operational proxy pilot and explicit budget gate

**Status (2026-07-18): OPERATIONAL PILOT COMPLETE / FORMAL GATE NON-AUTHORIZING /
TASK 9 RUNNER READY, FORMAL COLLECTION NOT STARTED.** The canonical
[pilot specification](../../experiments/2026-07-18-benchmark-v2-pilot-spec.json),
scripts-only collector, exact pricing/budget gate, clean-resume tests, and explicit
spend-confirmation boundary are complete. See the
[Task 8 readiness record](../../BENCHMARK_V2_TASK8_READINESS.md). The official-reference
price contract and its pre-network input bound are verified. The user authorized
attempt 001, which made 6 logical calls/7 provider attempts and committed 0/8 rows
before the trace exception. The run cannot resume; its known cost is `$0.074775`, and
retry-aware accounting bounds it at `$0.184343`. The minimal trace fix passed the full
offline gate. After disclosure of the `$11.574272` cumulative two-attempt ceiling
(excluding Task 9), the user separately authorized attempt 002's `$10.960896` ceiling.
Attempt 002 then completed 8/8 rows with 27 logical calls, 31 provider attempts, 4
retries, 34,304 requested output tokens, 42,496 attempt-reserved output tokens,
473,726,578 µs recorded provider elapsed time, and 477,264,352 µs active host time.
Twenty-five successful attempts reported 18,781 input and 11,482 output tokens with
zero cache usage; six failed attempts had no usage metadata. Its known cost is
`$0.438365`, its retry-aware tight upper bound is `$1.095773`, and the two attempts'
cumulative known/tight-upper costs are `$0.513140` / `$1.280116`.

The pilot pricing contract SHA-256 remains
`c93229c60003905d0946bd4d66096943a337a3763839715f296ecb338148baa5`. A separate
formal billing envelope with SHA-256
`5bcd24585db7a062955b2dc3de543e8ecc7e875c4647b6d767e348ee1cb15b5d` binds that
contract, formal scope/enforcement, 272,000-token ceilings for input, cache creation,
and cache read, and a 16,384-token output ceiling. The resulting formal gate SHA-256 is
`a421e1c330b600dbd19cdc3da145967033c9740132278c0c7afa7f62711fc57e`; it computes a
`$538,865.486400` formal worst case. Pilot actual cost and pilot-informed formal
projection remain unavailable because failed-attempt usage is missing. The gate is not
authorization.

- Run a separately labeled pilot only after the runner-ready SHA is clean and pushed,
  on at most two two-bar procedural families excluded from the formal corpus and two
  samples each; preflight must prove each proposal/raw call retains the 2,048-token
  cap used by this calculation. Its hard ceilings are 44 logical calls, 132 provider
  attempts, 51,200 requested
  output tokens, and 90 minutes; the pilot manifest must also freeze a spend ceiling.
  Reserve one complete sample’s worst case (11 calls, 33 attempts, 12,800 output
  tokens) before starting it. Lower ceilings are allowed; higher ones require a new
  preregistration.
- The pilot’s sole decision purpose is operational: verify provider metadata,
  orphan-free WAL/resume, latency, and actual/available usage fields. Do not inspect it
  to move outcome thresholds, choose favorable strata, or rewrite endpoints.
- Mechanically compute the worst-case and pilot-informed remaining logical calls,
  retries, output tokens, wall time, and price if a verifiable price contract exists.
- The formal run may start only when its maximum external spend/call budget is explicit.
  If pilot or formal cost is unknown/material, pause for user authorization before that
  collection rather than silently launching calls.
- Pilot rows never enter the formal report and have a distinct manifest/run ID.
- The exact provider timeout envelope, durable recorded-elapsed ceiling, and active
  host deadline are separate fields. Pilot-informed projections use stage-level totals
  and retain the formal corpus's variable proposal/raw token base.
- Creating a priced pre-call declaration is not authorization. Live collection also
  requires a caller-supplied maximum spend equal to the mechanically recomputed pilot
  worst case; this remains subordinate to explicit user approval.

## 13. Task 9 — Current-model collection and deterministic analysis

**Status (2026-07-18): FORMAL RUNNER IMPLEMENTED / EXTERNAL CEILING DECLARED /
FORMAL COLLECTION NOT STARTED.** The private external-ceiling gate SHA-256 is
`931b5ae14d587d89511aa3b5c45c7458e96c377df54093ad6244a14948527bd9` and declares
the exact `$538,865.486400` mechanical maximum. The gate does not grant authorization;
the user's separate project-wide model-billing authorization does. The runtime guard, exact caller spend
confirmation, provider-evidence abort boundary, raw-only live finalization, and
independent double-replay workflow are implemented; the commit-bound attempt-001
pre-call is generated only after the runner-ready SHA is clean and pushed.

- Before any Task 9 provider call, the formal runner must enforce the billing envelope's
  input ceiling as UTF-8 prompt bytes plus the fixed 256-token framing allowance before
  observation, retry, or network I/O. The user independently authorized the formal
  maximum on 2026-07-18; the computed `$538,865.486400` gate remains non-authorizing.
- Run the frozen primary and secondary suites with requested `gpt-5.6-sol` from the
  runner-ready clean SHA. Resume only from a validated, orphan-free WAL with the same
  config. Any orphan marks that run incomplete and forces a fresh formal run ID.
- Abort on model mismatch, corpus/config drift, unexpected usage schema, exhausted
  budget, or incomplete expected-key coverage. Never fill missing rows with stub data.
- Finalize/sort/hash rows, then generate the report in an offline replay process.
  A second independent replay must be byte-identical.
- Publish null and negative effects unchanged. Do not move thresholds, drop failed
  items, or replace the preregistered primary stratum after seeing results.

## 14. Task 10 — Documentation, acceptance, review, and closure

**Files:**

- `docs/BENCHMARK_RESULTS.md`
- `docs/BENCHMARK_V2_ACCEPTANCE.md`
- `docs/PROJECT_STATE.md`
- `README.md`
- `CLAUDE.md`
- roadmap/spec/scope references
- sanitized config/observations/receipt/report plus compressed canonical rows/blobs
  when legally redistributable. If size/license prevents repository storage, retain
  them in a stable access-controlled artifact location with hashes and access policy;
  an integrity hash alone does not justify a public rescore/reproducibility claim.
  Private telemetry is never committed.

**Acceptance:**

- Add a v2 section; preserve legacy tables and correct stale “current
  `fidelity@0.2.0`” prose to `fidelity@0.3.0` without retroactively relabeling old
  measurements.
- Record corpus composition, exclusions, hashes, execution SHA, exact commands,
  counts/denominators/CI, paired tests, multiplicity, usage availability, component
  keep/cut outcomes, nulls/negatives, and exact replay receipt.
- Treat byte-identical replay as authoritative only on the manifest-bound package,
  Python, OS, and architecture runtime; cross-platform equality is not claimed.
- Acceptance is split into `software/statistical`, `external collection`, and `human
  empirical`. Never mark the last complete without real human evidence.
- Re-run all offline/proxy/distribution gates and independent scope, security/privacy,
  statistics, and release/product reviews with zero unresolved findings.
- Make a reviewable results/closure commit, push, and verify local/tracking/remote SHA
  equality before opening the next plan.

## 15. Human and external gates that remain OPEN

These do not excuse incomplete software, but they block the corresponding claims:

- real-player gold and agreement block empirical GREEN false-accept, AMBER bandwidth,
  oracle/profile calibration, and checker-vs-judge superiority;
- blind guitarist/listener A/B or MOS blocks external musicality and critic-value claims;
- expert rankings block human difficulty calibration and tier accuracy claims;
- a real design partner blocks real-world playability/generalization promises;
- independently authorized provider access blocks cross-provider judge comparison;
- unavailable or ambiguous upstream licenses block affected public corpus/baseline rows.

## 16. Stop lines

- Do not redo MIDI, MusicXML, the oracle, or historical Plan 4 skeletons.
- Do not publish a single pooled score across different evidence signatures.
- Do not treat unavailable fidelity as 0 or 1, deterministic baselines as repeated
  stochastic samples, or provider-missing tokens/cost as zero.
- Do not call structural GREEN/taste direction an empirical capability win.
- Do not infer public-data licenses, track roles, chords, human difficulty, or human
  playability.
- Do not weaken schemas, limits, timeouts, validation, or statistical gates to finish a
  run.
- Do not add a dashboard, chart page, live leaderboard, audio, RL, Plan 6B, or Plan 7.
- Do not open the next plan until benchmark-v2 acceptance, commit/push, and SHA equality
  are closed, or the stage is explicitly paused at a documented human/external gate.

## 17. Required gate commands

Exact CLI arguments will be copied from the frozen machine preregistration; the gate
shapes are:

```bash
uv run fretsure-bench ... --stub --output-dir <fresh-a>
uv run fretsure-bench ... --stub --output-dir <fresh-b>
diff -rq <fresh-a>/canonical <fresh-b>/canonical
uv run python scripts/build_benchmark_precall.py \
  --prereg docs/experiments/2026-07-17-benchmark-v2-prereg.json \
  --pricing-contract docs/experiments/2026-07-18-gpt-5.6-sol-pricing-contract.json \
  --expected-pricing-sha256 c93229c60003905d0946bd4d66096943a337a3763839715f296ecb338148baa5 \
  --formal-billing-envelope docs/experiments/2026-07-18-gpt-5.6-sol-formal-billing-envelope.json \
  --expected-formal-billing-envelope-sha256 5bcd24585db7a062955b2dc3de543e8ecc7e875c4647b6d767e348ee1cb15b5d \
  --formal-budget-gate <private-external-ceiling-gate> \
  --expected-formal-budget-gate-sha256 931b5ae14d587d89511aa3b5c45c7458e96c377df54093ad6244a14948527bd9 \
  --collection-attempt 1 --execution-git-sha <pushed-runner-sha> \
  --uv-lock-sha256 <uv.lock-sha256> \
  --analysis-binding-kind analysis_module_sha256 \
  --analysis-code-sha256 <report.py-sha256> --output <pre-call.json>
uv run fretsure-bench --live --pre-call-config <pre-call.json> \
  --authorized-maximum-spend-microunits 538865486400 \
  --output-dir <fresh-attempt>
uv run fretsure-bench --replay-config <config> --replay-receipt <receipt> \
  --replay-rows <rows> --replay-blobs <blobs> \
  --replay-observations <sanitized-observations> --output-dir <fresh-replay-a>
uv run fretsure-bench --replay-config <config> --replay-receipt <receipt> \
  --replay-rows <rows> --replay-blobs <blobs> \
  --replay-observations <sanitized-observations> --output-dir <fresh-replay-b>
diff -rq <fresh-replay-a>/canonical <fresh-replay-b>/canonical

uv run pytest -q -m 'not integration'
env -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_BASE_URL \
  uv run pytest -q -m integration  # provider-free skip/fail-closed boundary
uv run ruff check .
uv run mypy --strict src
uv run mypy --strict scripts/build_benchmark_corpus.py
uv run mypy --strict scripts/build_benchmark_prereg.py
uv run mypy --strict scripts/build_benchmark_precall.py
uv lock --check
uv run python scripts/build_benchmark_prereg.py --check
uv run python scripts/check_markdown_links.py
git diff --check
uv build
uv run python scripts/audit_distributions.py
uv run python scripts/smoke_distributions.py

git status --short
git rev-parse HEAD
git rev-parse '@{u}'
git ls-remote origin refs/heads/main
```

## 18. Plan-freeze review receipt

Before the preregistration commit, independent scope/method, statistics, and
security/privacy/resource/reproducibility reviews each reached 0 blocker, 0 important,
and 0 minor findings after fixes. The documentation gate passed `git diff --check`,
all 32 Markdown link targets, and the unchanged benchmark skeleton’s 120 tests. This
receipt validates the implementation contract; it is not benchmark outcome evidence.

Pre-collection implementation census amendment: the loopback proxy’s opaque response
ID is 416 printable characters, so its private-only hash input cap is 512 rather than
the initial 256. The ID remains omitted from public artifacts; no benchmark outcome was
inspected and no endpoint, threshold, corpus, or statistical rule changed.

Pre-collection security clarification: the optional proxy transport now freezes a
1-MiB raw identity-encoded response cap, disables ambient HTTP proxies and redirects,
and white-lists retryable failure classes. This closes transport allocation and
credential-routing ambiguity discovered before collection; no provider outcome was
observed and no inferential rule changed.
