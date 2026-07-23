# Benchmark v2 acceptance

Date: 2026-07-23
Status: **TASK 10 COMPLETE; HUMAN AND PUBLIC-REDISTRIBUTION GATES OPEN**

This receipt separates what the completed benchmark establishes from what remains
external or human work. A closed software/statistical gate does not turn the oracle
into human playability evidence, and a completed provider collection does not make the
full replay package publicly redistributable.

| acceptance partition | state | claim boundary |
|---|---|---|
| Software/statistical | COMPLETE | versioned machine outcomes and preregistered inference only |
| Current-model external collection | COMPLETE | one `gpt-5.6-sol` formal run; no cross-provider claim |
| Artifact integrity / authorized replay | COMPLETE | owner-controlled package; no public rescore or remote-durability claim |
| Artifact public redistribution | OPEN | data-license terms and provider-output redistribution basis are not recorded |
| Human empirical | OPEN | no real-player gold, calibration, blind musicality, or human difficulty evidence |

## 1. Formal run and immutable bindings

- Run ID: `benchmark-v2-formal-20260717-attempt-004`
- Requested/returned model: exactly `gpt-5.6-sol`
- Collection execution SHA:
  `773c69deca4d2b00cdcdc5a33841369cb3016955`
- Provider-free finalizer amendment SHA:
  `33577c8d41198c6d604e5a4323239e74a309945c`
- Task 9 closure SHA:
  `6683183e0d09eff3208be70e425db4ae6714ee3d`
- Analysis SHA-256:
  `495ac3870a79ef394323f59cd664d551f1696ae58b52184f8e9fc351ec495281`
- Pre-call SHA-256:
  `facafd058cba2eb5223bde1ddf6f6da802cdbc6ab439eb40e749171a8ec9b76b`
- Budget-gate SHA-256:
  `26260e11ebbbdffc05f9770957075f2a88b8925888887cb5564789e5f9f8c5d7`
- Package/runtime: Fretsure `0.6.0`, Python `3.11.15`, Darwin/arm64,
  `uv.lock` SHA-256
  `5a8b43a396b5854057c554c4c0d0c6629ffbb58cae5edc9d6bc49bc5a6df2f8e`
- Checker contracts: `oracle@0.2.0`, `tab-input@0.2.0`,
  `fidelity@0.3.0`, confirmatory `median@0.1` fingerprint
  `fcefa5394cba876b94881fc77886e6db130d8be10406d46538ad6c83c40b7b62`

## 2. Corpus, exclusions, and denominators

The primary population contains 500 independently seeded procedural
items/families/clusters with complete melody+bass+harmony evidence, balanced across
nine complexity/polyphony strata. Ten proposal observations are nested within each
family and never inflate inferential N beyond 500.

Secondary evidence is one public-classical and two public-MIDI controls with
melody+harmony evidence. They remain separate from the procedural headline. The
licensed included sources are CC0-1.0 and CC-PDDC. The following exclusions remain
visible:

- no public lead-sheet candidate met the permission and evidence contract;
- Mutopia BWV 773 was excluded for polyphony outside the frozen normalizer;
- Bizet Op. 21 No. 10 was excluded for time-signature changes;
- Abbott's *Just for Today* was excluded for a polyphonic vocal stream; and
- checker-only tab fixtures were software checks, not formal proxy items.

The completed package contains 503 corpus items, 10,563 rows, 15,887 blobs, and these
domain-separated bindings:

| binding | SHA-256 |
|---|---|
| corpus | `b4e2a1ed05eb07d82bdea18b9105cdd92b564cf864d8acedaa3c37d820848e8b` |
| config/manifest | `61f5ce16de31ce11c55b750a8d5d39a3824cb93c6696be2d9f07b16bd49297a8` |
| observations | `f58b29df41cfe06456b44f2c2a6a15d44a8e73d43c5ee6f95aeae237ca175899` |
| receipt | `d64fa9c6435c1f1e0771f836768b80e3dbfce23dea017e6bdd185998a96bb174` |
| rows | `a1260b6d7751b94ef9524aacdc7168bb61e9bf1aca82dfdcd80595b35c8fa7d2` |
| blobs | `819fbc163d751fc799e2e0baaed1ac73d35578bc5282359a8e44353a1abd7276` |
| journal | `ca34117badc09ef469eed9132ba11026a9518c0564ad8252144e736c014adc9b` |

Contamination checks were clean within the real and procedural partitions and across
them. The three public controls are too few for an external-validity or genre claim.

## 3. Software/statistical acceptance

The machine preregistration froze the estimands, 500-family power gate, shared
ten-proposal pool, schedule/permutation, ITT missingness, family bootstrap, sign-flip
tests, exact McNemar test, SESOIs, and two separate Holm families before formal model
outcomes.

- Repair: `+0.0566` joint, 95% bootstrap `[0.0456, 0.068205]`, adjusted
  `p=9.9999e-6`; `NOT_KEPT` because it misses the `0.10` SESOI and matched-budget
  guard.
- Best-of-4: `+0.068`, 95% `[0.048, 0.088]`, McNemar 34/0,
  adjusted `p=1.16415e-10`; `PROBATION_COST_UNKNOWN` because complete provider-token
  cost is unavailable.
- Critic: self-score `+0.00272`, joint `-0.002`; `HUMAN_BLOCKED_PROBATION`.
- Product consequence: CLI, demo, application/pipeline, HTTP capabilities/defaults,
  OpenAPI, and Web controls use `n=1`, `max_iters=0`, `use_critic=false`. The three
  evaluated components remain available only through explicit opt-in; the frozen
  formal `full` arm and Task 9 execution SHA are unchanged.
- Full selected policy: 74/500 = 0.148, Wilson 95%
  `[0.119561, 0.181806]`; every high-complexity stratum and all three public controls
  recorded zero selected joint successes.
- Raw and pure-solver baselines are present. Optional B3/B4 remain `unavailable` with
  `LICENSE_AUDITED_REPRODUCIBLE_ADAPTER_ABSENT`; missing comparisons were not invented.
- Every structurally applicable transport/parse/no-tab/scoring failure remains in its
  ITT denominator. Missing usage and unavailable fidelity dimensions are null/N/A,
  never zero or one.
- The complete k=1..10 terminal GREEN/joint pass@k and pass^k curves, conditional and
  failure-inclusive fidelity, all nine procedural strata, and three separately named
  profiles remain in the canonical report. No favorable k or pooled evidence signature
  is promoted as a single reliability claim.

The full numerical scorecard, including negative effects and exact intervals, is in
[`BENCHMARK_RESULTS.md`](BENCHMARK_RESULTS.md). The aggregate canonical JSON is
[`report.json`](experiments/benchmark-v2-formal-attempt-004/report.json).

## 4. Current-model collection acceptance

Attempt-004 completed 10,060/10,060 network units and 10,563/10,563 rows with 45,215
logical calls and 45,700 provider attempts. All 10,060 coordinator admissions reached
READY and no active lane remained. The provider returned only the exact requested model.

Collection used the pre-call-bound command below; resumes reused the same output
directory and arguments with `--resume`. Authentication values are intentionally not
recorded.

```bash
.venv/bin/fretsure-bench \
  --live \
  --pre-call-config outputs/private/benchmark-v2-task9/pre-call-attempt-004.json \
  --authorized-maximum-spend-microunits 1167905640000 \
  --output-dir outputs/private/benchmark-v2-task9/attempt-004
```

The old generic finalizer failed after all provider work because the valid observation
envelope exceeded a generic node limit. The approved provider-free amendment preserved
the per-call schema and bytes, kept the generic guard unchanged, used fail-fast provider
factories, and published exactly five raw canonical files with zero provider-factory
calls. The zero-active-lane recovery plan SHA-256 was
`fe612a6b42e5d511a9b7e044c941b6617cf41392df42b0c13e53b9d58108a726`;
its APPLIED receipt was
`0c9e71516a2d9e152f71698a450b43b2bedb27ba57a149f4aca0514f1c22028e`.

Usage remains availability-aware:

- logical requested output tokens: 84,894,720;
- attempt-reserved output tokens: 87,030,688;
- summed recorded elapsed: 1,108,043,068,140 µs;
- complete provider tokens: null; and
- input/output/cache-create/cache-read totals: all null.

Therefore actual token cost and best-of-4 deployment Pareto status are unavailable,
not zero. The orphan-lane recovery supplement remains a separate `$0.219054` known /
`$28.363054` tight addendum with four usage-unknown attempts; it is not included as
benchmark data.

Attempts 001–003 remain immutable `INCOMPLETE` operational evidence and are not pooled
with attempt-004. No provider call is required for Task 10 closure.

## 5. Deterministic replay and artifact policy

Two fresh default FULL_RESCORE processes produced byte-identical seven-file canonical
directories. Each replay's five raw files were also byte-identical to the source
publication. Report digest:
`79d1927b5100bd80db2f47b056ad5a7887739460869020c7b53a65d0d19bb3f8`.

This byte-equality claim is authoritative only for the manifest-bound Fretsure 0.6.0,
Python 3.11.15, Darwin/arm64 environment. A different platform may reproduce the
domain result without producing byte-identical files; cross-platform equality is not
claimed.

The public repository contains only a payload-free README/index, the COMPLETE receipt,
and the aggregate report in JSON/Markdown; it contains no replay inputs. The
owner-controlled replay package contains config, sanitized observations, receipt,
report, and independently compressed rows/blobs. Its seven stored files total
33,682,168 bytes. The exact stored/raw hashes, zstd 1.5.7 command, logical locator,
retention/access policy, and exact replay commands are recorded in the
[`artifact index`](experiments/benchmark-v2-formal-attempt-004/artifact-index.json).

Public redistribution of the full package remains OPEN because
`LicenseRef-FretSure-Generated-Benchmark-v2` has no accompanying terms and no
provider-output redistribution basis is recorded. Access is owner-approved, onward
redistribution is prohibited, and the public repository makes no public-rescore or
remote-durability claim.

Private observations, prompt/response payloads, journal/WAL, staging, lane/unit state,
operator logs, PID/lock files, abort/audit records, and recovery telemetry are neither
in the package nor permitted in a future public release.

## 6. Human and external gates that remain OPEN

- Real-player gold, repeated ratings, and agreement: blocks empirical GREEN
  false-accept, AMBER bandwidth, oracle/profile calibration, and checker-vs-judge
  superiority.
- Blind guitarist/listener A/B or MOS: blocks human musicality and critic-value claims.
- Expert rankings: blocks human difficulty/tier calibration.
- A real design partner: blocks real-world playability/generalization promises.
- An independently authorized second provider: blocks cross-provider judge comparison.
- Unavailable or ambiguous upstream licenses: block the affected public corpus and
  baseline rows.
- A project data license and provider-output rights record: block public release of the
  complete replay package.

Constructed labels and model/profile sensitivity are software evidence only. Human
empirical acceptance remains OPEN until real humans are collected; Task 10 must never
change that state merely to close engineering work.

## 7. Fresh Task 10 validation receipt

Fresh validation ran on the Task 10 review tree; no Task 9 result was reused:

- `uv sync --frozen --extra dev`: locked dependency sync passed.
- `uv run ruff check`: passed.
- `uv run mypy --strict src`: 96 source files passed. Strict mypy also passed
  separately for the three corpus/prereg/pre-call builders, both Task 8 scripts, and
  all three Task 9 operational/throughput/recovery scripts.
- `uv run pytest -q -m 'not integration'`: `2615 passed, 8 deselected` in
  `1508.30s` (`0:25:08`). The only warning was the known third-party
  Starlette/httpx deprecation notice.
- Empty-provider integration boundary: `8 skipped, 2615 deselected`; no provider
  call was made.
- Isolated minimum `anthropic==0.40.0` compatibility: `9 passed`.
- `uv lock --check`, frozen preregistration check, Task 8 pilot-spec check,
  Markdown links (`43 files`), and `git diff --check`: passed.
- Frontend: clean `npm ci`, `30 passed`, TypeScript typecheck, deterministic
  production build, tracked-plus-untracked generated-asset guard, and
  `npm audit --audit-level=high` with zero vulnerabilities all passed.
- Distribution: wheel/sdist build passed; the allowlist audit reported
  `wheel=116, sdist=342`; clean installs passed for core replay, benchmark,
  MusicXML, MIDI, score, service, and MCP combinations.

The automatic CI remains intentionally lightweight; the complete benchmark,
integration, artifact, and distribution matrix stays in the manual `Full validation`
workflow.

## 8. Independent review receipt

Independent scope, security/privacy, statistics, artifact-distribution,
release/product, version-boundary, and stale-claim reviews completed with **zero
unresolved findings**. Reviewers separately checked the aggregate numerical results,
public/private artifact boundary, exact hashes and bundle byte total, product defaults,
explicit opt-ins, legacy compatibility, generated Web bundle, and historical/current
claim separation. They did not inspect private observations or prompt/response,
lane/unit payloads.

## 9. Git closure receipt

The reviewable content/validation commit is
`d64cace8ac34d19a299c5b20fefdd7c4ad9bc985`, based on Task 9/main closure
`6683183e0d09eff3208be70e425db4ae6714ee3d`. The follow-up closure commit contains this
receipt and is fast-forwarded through `codex/benchmark-v2-task10` to `main`. Its own SHA
cannot be embedded in its content, so the final handoff records the containing SHA and
the verified equality of local `HEAD`, local `main`, tracking `origin/main`, and remote
`main`.
