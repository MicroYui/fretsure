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

## 2026-07-18 — Task 7

Task 7 completed the runner-ready software implementation and froze the pre-call
experiment contract without making a real provider call:

- Package and distribution metadata are versioned as `0.6.0`. The core distribution
  retains offline replay/statistics without a checkout, while the `[benchmark]` extra
  supplies the live mixed-corpus dependencies and pins `music21==10.5.0`.
- The machine preregistration binds the ordered 503-item corpus, ten proposal samples,
  all 10,060 scheduled units, arm and ITT definitions, statistical seeds,
  power assumptions, decision rules, per-unit reservations, and full-run ceilings.
  Its SHA-256 is
  `ad9129edfb47634085f7bfd5557ca76f59eb8358865a1742bfcba69fa0c1362b`.
  The accompanying budget document SHA-256 is
  `4814206e1b749a03e458822016b66caeb1cfb480e033111e05030ffafe372b19`.
- Attempt-local pre-call declarations use a formal experiment ID plus a monotonically
  numbered collection attempt. Cost-unavailable live execution fails before creating
  an output directory or client; an orphaned attempt is excluded in full and requires
  a fresh declaration, directory, and authorization. Stub collection refuses injected
  client factories before output creation.
- The compact proposal parser rejects duplicate keys and targets outside the strict
  solver domain before accepting an LLM result. Exact-onset/pitch unisons are coalesced
  only in the solver target, leaving the source prompt, corpus identity, and fidelity
  evidence unchanged. The score solver admits at most four deterministic segments,
  with a 12,000,000-state per-segment bound and a 48,000,000-state aggregate bound.
- Report generation and full rescore use the same solver IR, including the public
  Beethoven unisons. Public controls that are genuinely infeasible remain typed
  infeasible outcomes rather than being weakened, dropped, or rewritten as successes.
- Fresh wheel/sdist audits, isolated no-`.git` core replay, `[benchmark]` stub smoke,
  and both live fail-closed consumer paths passed offline. Runtime collection/replay
  contains no Git or subprocess checkout discovery.
- The preregistration/budget generation check and offline lock check passed. The
  directed distribution, clean-install, runner, and pre-call group passed 52 tests in
  280.21 seconds; the fresh 0.6.0 distribution audit covered 114 wheel entries and 307
  sdist entries. These directed results do not replace the final full-suite TODO below.

Full-size stub evidence:

- Each complete run contained 503 items, 10,060 scheduled units, and 10,563 canonical
  rows. The seven canonical files from the two runs were byte-identical.
- The `report.json` file SHA-256 is
  `73d77442426eab0100ff55a551913c7656cfdd3e795106939e4085fc17e47d32`;
  its embedded domain-separated `report_sha256` is
  `131c0b9bb5baf63f03100a85546e1edc48351615ae19e9ddea7f1b5cff2fb776`.
- The measured elapsed time for full-size stub run B was 4,431.39 seconds. Stub timing,
  usage, and response identity remain unavailable in canonical model observations and
  are not presented as provider evidence.

Independent scope, statistics/reproducibility, security/privacy/resource, and
release/consumer reviews closed at zero blocker, zero important, and zero minor
findings after the consumer documentation was synchronized. The remaining
runner-ready closure steps are deliberately not inferred from the stub comparison:

- **Full replay complete:** the full rescore/reaggregate replay completed in
  `2454.226` seconds. Its seven canonical files were byte-identical to collection A:
  config `f92810d4fc43388fae55503093e7ca9bb0ca859dc79f9514eff93f47fce3ea1d`,
  receipt `90f9155c569d5c35e7fe0012e4a4e63f998267123451259bd0f2aae8dd891ba3`,
  rows `cff6de86e2acfe5dfdfa196ced2b2e4e14cae2233babee1c2611a361852f658f`,
  blobs `8f245bec0b8af39d7b6c87e64de07a457d0f2054270f28c5cc03c095f98e5610`,
  observations `8dbcf25e87b6745cb397d1e6db69aadd9ef8cfbc9a374d330aa1641ba583c14e`,
  report JSON `73d77442426eab0100ff55a551913c7656cfdd3e795106939e4085fc17e47d32`,
  and report Markdown
  `a68ac69b1bce151f0dcaf310f11f486d7f870d46c1ef370f64be8ae149be7599`.
- **Final gates complete:** the final offline suite passed `2415` tests with `8`
  integration tests deselected; the no-provider integration boundary exited cleanly
  with those `8` tests skipped. Ruff passed, strict mypy passed for `94` source files
  and both frozen build scripts, the lock and generated preregistration checks passed,
  and all local links across `35` Markdown files resolved. The web suite passed `29`
  tests plus typecheck and production build. The rebuilt 0.6.0 distributions again
  passed the `114`-entry wheel / `307`-entry sdist audit and the isolated install smoke
  matrix for core replay, benchmark, MusicXML, MIDI, score, service, and MCP.
- **External Git gate:** this runner-ready tree is closed by the terminal commit/push
  and local/tracking/remote SHA-equality check recorded in the task handoff. Per the
  user's instruction, work pauses immediately after that push; Task 8 does not begin
  automatically.

No real provider or model call was made, and no network collection was performed. No
frontend surface or frontend design changed.

## 2026-07-18 — Task 8 offline readiness

After the user resumed work, Task 8 completed every non-provider software gate while
leaving the real operational pilot unrun:

- A canonical 2-family × 2-sample pilot specification binds the Task 7 preregistration
  by SHA without duplicating its 503-item payload. Its own corpus is two bars per family
  and is disjoint from the formal corpus by all frozen identity and content digests.
- The separate scripts-only collector reuses the durable ArtifactStore/WAL boundary but
  has its own manifest schema, run ID, schedule, and five-file canonical bundle. It
  reserves one complete agent/raw pair before each agent row, cleanly resumes after
  agent or raw rows, rejects orphaned calls, and never invokes the formal report path.
- Pilot time accounting now distinguishes the exact 4,026-second provider timeout
  envelope from the 5,400-second durable recorded ceiling and invocation-local host
  deadline. Formal accounting likewise distinguishes 5,062,695 seconds from the
  runner's 5,184,000-second recorded ceiling.
- The pricing contract uses exact integer microunit arithmetic and canonical evidence
  bindings. Pilot pre-call parsing embeds and recomputes the complete contract and
  mechanical worst case. Live collection additionally requires the caller to repeat
  that exact maximum spend; declaration generation alone is not authorization.
- The user selected current official model pricing as the reference basis. A dated
  OpenAI `gpt-5.6-sol` source snapshot and canonical contract now bind standard
  short-context input/cache-write/cache-read/output rates. A Task 8-only guard checks
  UTF-8 prompt bytes plus 256 framing tokens against all declared input buckets before
  observation, retry, or network I/O, turning the former 4,096-token fixture value into
  an enforced live ceiling without changing the package.
- Stage-aware pilot projections preserve the formal 503-item proposal/raw token base.
  Missing provider usage and uncovered retry usage remain unavailable rather than
  becoming zero. Pilot rows cannot enter or alter the formal report.

The canonical pilot-spec SHA-256 is
`e455a608d4b186f24a2739e009b8f9fe604036fd3a4f34d0ef97d2afb3ab7ad3`. The directed
Task 8 suites passed 34 tests; Ruff and strict mypy passed both scripts. One-shot stub
collection and a clean resume after one row produced byte-identical config, receipt,
rows, blobs, and observations. Full hashes and resource arithmetic are recorded in
[`BENCHMARK_V2_TASK8_READINESS.md`](../BENCHMARK_V2_TASK8_READINESS.md).

No `src/`, package metadata, dependency lock, runtime Git behavior, or frontend surface
changed. No real proxy, network, or provider call was used. The official-reference
contract computes a conservative pilot maximum of `$10.960896`; the next step is
explicit pilot-spend authorization plus a matching configured proxy. Task 9 has not
started. Final follow-up verification passed 2,449 offline tests (8 integration tests
deselected), the empty-provider integration boundary skipped all 8 tests, and the
distribution audit reported 114 wheel / 315 sdist entries. The wheel SHA-256 remained
`615025e1d3f0fdc34119880ac79231b9388e3a2d0b513abc1ad7d15ef99b87fb`.

## 2026-07-18 — Task 8 live attempt 001 interruption and trace fix

The user explicitly authorized the priced pilot's exact `$10.960896` maximum and the
configured loopback proxy returned `gpt-5.6-sol`. Attempt 001 stopped before committing
its first row after 6 logical calls and 7 provider attempts. The WAL is closed and
hash-valid, but its terminal calls are not owned by a complete staged unit, so the
existing recovery contract correctly refuses `--resume` before any new provider request
or network attempt.

No raw prompt or response text was inspected or used to change the experiment; diagnosis
used only the typed exception, aggregate WAL metadata, and offline deterministic
reproduction.

The reported successful usage was 4,515 input and 1,740 output tokens with no cache
usage, which is `$0.074775` under the checked-in reference contract. One failed retry
has no usage metadata, so exact billed cost is unavailable; applying all contractual
input ceilings to the seven attempts plus their 9,216 stage-specific reserved output
tokens gives a conservative `$0.613376` upper bound. The private
pre-call, manifest, and WAL were preserved outside Git under ignored `outputs/private/`;
their public audit hashes are recorded in
[`BENCHMARK_V2_TASK8_READINESS.md`](../BENCHMARK_V2_TASK8_READINESS.md).

The exception exposed one narrow trace inconsistency. AMBER can legitimately have zero
median-profile diagnostics because its verdict also considers optimistic/pessimistic
profiles. Repair preserved that fact as an empty diagnostic-code list, while the trace
validator incorrectly required a non-empty list. The validator now accepts an empty
list while retaining its exact-list, bounded-length, unique-code, and stable-code
checks. Trace-level and repair-level regressions cover the state. The directed repair,
trace, pipeline, and Task 8 pilot suite passed 147 tests; the full offline suite passed
2,451 tests with 8 integration tests deselected; Ruff, strict mypy, the empty-provider
integration boundary, lock/prereg/spec checks, Markdown links, and diff integrity all
passed. The rebuilt 0.6.0 distributions passed the 114-wheel/315-sdist audit and clean
install matrix; wheel SHA-256 is
`f24e510a56219d1c7673d03ec5870736b523c195adea73f82a1765a71738372d`.
No frontend, model, prompt, corpus, schedule, pricing, runtime Git, or subprocess behavior
changed.

Attempt 001 is terminal. Attempt 002 needs a new commit-bound pre-call, a fresh output
directory, and a new explicit `$10.960896` authorization. Including attempt 001's
conservative bound, the disclosed two-collection-attempt cumulative mechanical upper
bound is `$11.574272`.

Task 9 has not started.

## 2026-07-18 — Task 8 attempt 002 completion and formal budget gate

Attempt 001 remains terminal and its trace-validator repair remains unchanged. A later
retry-aware accounting pass tightened that attempt's bound from the original coarse
`$0.613376` ceiling to `$0.184343` while preserving its `$0.074775` known cost. No raw
prompt or response content entered this calculation.

After the user saw the `$11.574272` cumulative two-attempt ceiling, explicitly excluding
any Task 9 spend, they separately authorized attempt 002's exact `$10.960896` ceiling.
The fresh commit-bound pre-call and output directory completed all 8/8 pilot rows. The
run made 27 logical calls and 31 provider attempts, including 4 retries; it requested
34,304 output tokens and reserved 42,496 across attempts. Recorded provider elapsed time
was 473,726,578 microseconds and active host time was 477,264,352 microseconds.

Twenty-five successful attempts reported 18,781 input tokens, 11,482 output tokens, and
zero cache-creation/cache-read tokens. Six failed attempts had no usage metadata, so
exact actual cost and the pilot-informed formal projection remain unavailable rather
than treating those attempts as free. The reported usage prices to a known `$0.438365`;
stage-specific retry accounting gives attempt 002 a tight `$1.095773` upper bound. Across
attempts 001 and 002, known cost is `$0.513140` and the tight upper bound is `$1.280116`.

The original pilot pricing contract remains byte-identical at SHA-256
`c93229c60003905d0946bd4d66096943a337a3763839715f296ecb338148baa5`. Formal pricing
uses the same rate evidence through a separate billing envelope rather than widening the
pilot contract. The envelope SHA-256 is
`5bcd24585db7a062955b2dc3de543e8ecc7e875c4647b6d767e348ee1cb15b5d`; it binds the
pilot pricing SHA, formal-only scope and enforcement, 272,000-token ceilings for each of
input, cache creation, and cache read, and a 16,384-token output ceiling. Its mechanical
formal worst case is `$538,865.486400`. That artifact is explicitly non-authorizing.

Independent review then found that the pilot-informed projection independently rounded
critic calls and critic token totals. For attempt 002, 3,773 projected critic calls at
512 tokens each require 1,931,776 requested/reserved tokens; the first artifact was 256
tokens low in each field. The fix rounds calls and retries first, then multiplies by the
frozen per-call ceiling. Corrected projection totals are 71,658,496 requested and
96,220,416 attempt-reserved output tokens. The formal worst case and authorization
status are unchanged. The final gate SHA-256 is
`a421e1c330b600dbd19cdc3da145967033c9740132278c0c7afa7f62711fc57e`.

Task 9 has not started. Before it can start, the formal runtime must enforce the billing
envelope before every provider attempt by checking UTF-8 prompt bytes plus the fixed
256-token framing allowance, and the user must independently authorize the formal spend.

Final closeout reran the provider-free CI boundary: 2,456 tests passed with all 8
integration tests deselected, then the same 8 integration tests skipped with both proxy
environment variables explicitly empty. Ruff, the CI strict-mypy set (94 source files
and four frozen scripts), lock/preregistration/pilot-spec checks, Markdown links, and
whitespace integrity passed. The rebuilt distributions passed the 114-wheel/316-sdist
entry audit and the clean-install matrix for core replay, benchmark, MusicXML, MIDI,
score, service, and MCP. The unchanged frontend passed 29 tests, TypeScript checking,
production build byte verification, and an audit with zero vulnerabilities.

One initial closeout command accidentally omitted the integration marker while the local
proxy environment was still configured. It completed the existing arrangement and
critic integration smoke tests and was interrupted while a third end-to-end integration
test awaited a response. These calls were outside both benchmark collection attempts and
Task 9; no response content was inspected or admitted to any benchmark artifact. Their
usage was not durably captured and is therefore unknown, not zero. All authoritative
closeout results above come from the corrected provider-free commands.

## 2026-07-18 — Task 9 formal runner and externally declared ceiling

The user resumed the previously paused plan and separately authorized all model billing
in scope. A private, byte-reproducible `external_ceiling_declared` gate records only the
mechanical ceiling and explicitly does not grant that authorization. It preserves the formal envelope and
pricing contract unchanged, declares the exact mechanical maximum of
`538,865,486,400` micro-USD (`$538,865.486400`), and has SHA-256
`931b5ae14d587d89511aa3b5c45c7458e96c377df54093ad6244a14948527bd9`.

The formal runner now closes the remaining pre-call boundaries:

- pre-call schema `benchmark-pre-call-config@0.2.0` embeds the formal billing envelope
  and hash-binds the pricing contract, external-ceiling gate, execution declaration, runtime,
  model, prompts, schemas, attempt number, and full resource budget;
- live API/CLI callers must repeat the exact maximum spend before client or output
  creation;
- every request is checked as strict UTF-8 system bytes + user bytes + the frozen 256
  framing allowance, plus its output ceiling, before any digest, observation, WAL event,
  retry, delegate, or network operation;
- malformed provider response/usage schemas, missing or mismatched successful model
  evidence, reported usage above the envelope, budget exhaustion, and coverage/integrity
  failures become terminal aborts rather than ITT fallback or stub substitution;
- retrying or missing any usage field keeps exact provider-token/cost coverage
  unavailable while preserving reported known components;
- live collection atomically publishes only config, rows, blobs, sanitized observations,
  and receipt. Report generation is restricted to two fresh, independent default full
  replays whose seven canonical files must be byte-identical.

The deterministic pre-call builder accepts all Git/lock/analysis digests as explicit
release inputs; runtime code never invokes Git or a subprocess. The current directed
evidence is 39 runner tests, 9 pre-call/builder tests, 142 provider/artifact/report/client tests,
and 82 observation/experiment tests, all passing. Ruff, strict mypy for the changed
source and new builder, and whitespace checks pass.

The first full-repository closeout exposed six Task 8 compatibility regressions: the
generic artifact sink had applied Task 9 provider-evidence semantics to the older pilot
pre-call and stub contracts. The fix makes successful-provider evidence an explicit
sink policy enabled by formal pre-call schema 0.2, while keeping model mismatch
fail-closed for every caller. Usage ceilings remain independently enforced and are now
rechecked symmetrically on fresh writes and resume. The full Task 8 pilot file passed
15 tests, the formal/compatibility boundary passed seven directed cases, and an
independent release re-review returned zero findings.

Final runner-ready gates passed without a provider call: `2478 passed, 8 deselected`
for the complete offline suite, then `8 skipped, 2478 deselected` for the explicitly
proxy-cleared integration boundary. Ruff, strict mypy over 94 source files and five
scripts, lock/preregistration/pilot-spec generation checks, 36-file Markdown links,
and whitespace integrity passed. The unchanged frontend passed 29 tests, typecheck,
production build, and `npm audit` with zero vulnerabilities. Rebuilt distributions
passed the 114-wheel/318-sdist content audit and the clean-install matrix for core
replay, benchmark, MusicXML, MIDI, score, service, and MCP. The private external-ceiling
gate also passed byte-for-byte regeneration with SHA-256
`931b5ae14d587d89511aa3b5c45c7458e96c377df54093ad6244a14948527bd9`.
No Task 9 provider call or formal collection artifact has been created yet, and no
frontend surface changed.
