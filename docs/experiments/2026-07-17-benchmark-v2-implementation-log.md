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

## 2026-07-18 — Task 8 attempt 002 completion and formal budget gate (historical accounting)

The cost ceilings in this chronological checkpoint are superseded by the billable-output
correction below; the attempt and artifact facts remain immutable history.

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

## 2026-07-18 — Task 9 formal runner and externally declared ceiling (pre-attempt checkpoint)

This section records the state before the first formal provider call. Its cost ceiling
and schema references are superseded by the later correction section.

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

## 2026-07-18 — Billable-output correction and Task 9 attempt 001 terminal state

The first formal run invalidated one cost-model assumption without changing the frozen
experiment. A critic call requested at most `512` visible output tokens, but the proxy
reported `704` billable `output_tokens` for exact model `gpt-5.6-sol`. Historical Task 8
attempt 002 had likewise already returned `839` billable output tokens for a critic with
the same visible request limit. Official token-counting guidance states that reported
output includes non-visible generated tokens, and the official model page gives a
128,000-token maximum. A visible request limit therefore cannot serve as the billable
usage ceiling for this proxy.

The old pricing contract, formal envelope, external-ceiling gate, and Task 9 attempt-001
pre-call remain immutable historical evidence. In particular, the old authorized gate
SHA-256 is
`931b5ae14d587d89511aa3b5c45c7458e96c377df54093ad6244a14948527bd9`, and the
attempt-001 pre-call SHA-256 is
`3d9995e7f4561059077b1c9bc3b956a79b6db632f6252a24653e65cd811f3450`.
They must not be rewritten or reused for another call.

The corrected official price-source v2 SHA-256 is
`b16339b98c7ad7a269dc6d9d07416f8071a7b14f4ee4afeccea84940230c2062`; pricing
contract v2 SHA-256 is
`7b5ae715a08bb4e1cc7cca32e77db6ffc7e5f000133150194cf70a4b8f62c9b2`; and formal
envelope v0.2 SHA-256 is
`a1969546babcdcbcbf281c682260c38551b2fd12ef382014eb34a79e85df5544`. The envelope
binds the official 128,000-token maximum to billable `output_tokens`, including
non-visible tokens. Under this contract:

- the Task 8 pilot mechanical maximum is `$513.232896`;
- Task 8 attempt 001 is `$0.074775..$3.962903`, attempt 002 is
  `$0.438365..$23.767133`, and combined known/tight cost is
  `$0.513140..$27.730036`;
- the formal one-attempt mechanical maximum is `1,167,905,640,000` micro-USD
  (`$1,167,905.640000`).

These are official-contract audit maxima, not claims that the local proxy enforces a
pre-consumption spend hard stop. The user's project-wide billing authorization remains
separate from the artifacts.

The corrected `benchmark-formal-budget-gate@0.3.0` uses the neutral binding field
`pricing_contract_raw_sha256`, was regenerated byte-identically, and passed its
`--check` path. Its SHA-256 is
`9b50fd8a271a78705e728de8f8cbb24a09e08b24eb2db9122df6a943bdd958f6`. The corrected
fresh-run declaration schema is `benchmark-pre-call-config@0.3.0`; it does not mutate the
historical attempt-001 declaration.

Formal run `benchmark-v2-formal-20260717-attempt-001` completed all 503 pure-solver
units and committed the first agent unit. Logical call 13 was the critic call described
above. The old validator incorrectly compared billable usage with the visible request
limit, so the durable sink emitted terminal `INCOMPLETE` with reason code
`provider_integrity_failure`. The run's known cost is `$0.188415`; four usage-missing
attempts produce a corrected tight upper bound of `$28.332415` under the official model
maximum. No private prompt or response content was inspected or admitted to
documentation. Attempt-001 must never be resumed or overwritten. At this checkpoint,
the next permitted collection was fresh attempt-002 after the corrected runner,
pricing/envelope, and gate artifacts were pushed and rebound, with exact CLI confirmation
`--authorized-maximum-spend-microunits 1167905640000`.

## 2026-07-18 — Corrected billing release closeout

The corrected runner and billing artifacts passed the complete provider-free release
matrix. The offline suite finished with `2481 passed, 8 deselected`; the explicitly
proxy-cleared integration selection finished with `8 skipped, 2481 deselected`. Ruff,
strict mypy over 94 source files and five release scripts, lock/preregistration/pilot
generation, the corrected formal gate `--check`, 36-file Markdown links, and whitespace
checks all passed. The unchanged frontend passed 29 tests, typecheck, production build,
and `npm audit` with zero vulnerabilities. Rebuilt distributions passed the 114-wheel /
320-sdist content audit and all seven clean-install smoke groups. No provider call was
made by any command in this corrected-billing closeout.

## 2026-07-18 — Task 9 attempt 002 terminal state and post-edit validation correction

Fresh formal attempt-002 used pre-call SHA-256
`48796200a05af2cbc9ae83d80f06a89ff437841810241954a8b7fe3f794be6eb`, bound to the
clean pushed execution commit `1feeef622d96a95b187c473a40e273852cdf6a45`. It committed
`524/10,563` scheduled rows before the durable collector emitted terminal `INCOMPLETE`
with reason code `unexpected_unowned_observation`. Attempt-002 made 91 logical calls and
131 provider attempts. Of those attempts, 72 succeeded with complete usage and 59 have
missing usage; 19 logical calls ended `DELEGATE_FAILED`. Missing usage was not converted
to zero.

The structural cause was local, not a change in provider/model behavior or the frozen
experiment. A legal edit was applied and produced a duplicate onset/pitch. When the
target checkpoint was constructed, local validation raised instead of mapping the
post-edit invalid state into the existing recheck control path. That exception reached
the collector outside the owning logical-call observation boundary. No private prompt
or response content was inspected, admitted to documentation or canonical artifacts,
or used to diagnose or change the experiment.

Attempt-002's known cost is `$0.986494`. Charging every usage-missing attempt at the
official billable ceiling yields a tight upper bound of `$416.110494`. Combined with
attempt-001, the two terminal runs have a cumulative tight upper bound of
`$444.442909`; adding one complete formal attempt's `$1,167,905.640000` mechanical
maximum yields a cumulative upper bound of `$1,168,350.082909`. These remain
official-contract audit bounds, not a claim that the local proxy implements a
pre-consumption hard stop.

The 72 complete-usage attempts report 51,264 input, 21,171 output, 15,207 cache-creation,
and zero cache-read tokens. Each of the 59 usage-missing attempts contributes the frozen
`$7.036000` per-attempt ceiling to the tight upper bound. The run binds pricing v2
SHA-256 `7b5ae715a08bb4e1cc7cca32e77db6ffc7e5f000133150194cf70a4b8f62c9b2` and
billing-envelope SHA-256
`a1969546babcdcbcbf281c682260c38551b2fd12ef382014eb34a79e85df5544`. The abort
receipt SHA-256 is `14d7487a1a5e22dea93a919e0ae1e5a9257be316f6d1d6b060a6279d6282d730`;
the raw WAL and receipt-bound journal SHA-256 values are respectively
`cb29b2dd19ff354eb78630b3c73bd11af7d7baca2ae4ac6c8a41cf5aa4dedadd` and
`93cb62375c54d32f2483bd9ffcfbe38b104499a19bfc78552e80b7b0148477a1`.
All 444 WAL events passed sequence, hash-chain, intent/result, and attempt-pairing
validation, with no open call or attempt. The terminal receipt has no finalized hashes,
canonical directory, or private-observations artifact; its empty returned-model summary
is an abort-receipt construction detail, while the WAL contains 72 exact
`gpt-5.6-sol` success records.

Attempt-002's pre-call, WAL, and terminal receipt are immutable; it must never be
resumed or overwritten. The narrow software correction maps post-edit pitch-bound
violations and onset/pitch collisions to the existing `MODEL_EDIT_INVALID` → `RECHECK`
path. It changes no prompt, model, corpus, schedule, or trace schema. After directed and
full release gates pass and the correction is pushed, collection continues only as a
fresh attempt-003 with a new pre-call and output directory. No frontend surface or
visual design changed; any future frontend design work still requires confirmation
against the accepted visual baseline.

### Post-edit correction release gate

The final corrected tree passed `2,498` offline tests with `8` integration tests
deselected. With proxy variables explicitly removed, the integration selection skipped
all `8` provider-dependent tests and deselected the same `2,498` offline tests; no
provider call was made. Ruff, strict mypy over 94 source files and the five release
scripts, the offline 80-package lock check, machine preregistration, pilot spec, formal
budget gate, all 36 Markdown link sets, and `git diff --check` passed. An independent
source/trace review reported zero findings after 122 directed tests.

The unchanged frontend passed 29 tests, TypeScript checking, production build, and
`npm audit` with zero vulnerabilities and produced no tracked static diff. Rebuilt
distributions retained the 114-wheel / 320-sdist content audit and passed all seven
clean-install groups. These gates changed no frozen experimental input or schema and
made no model call. Attempt-003 must bind the subsequent clean pushed execution commit,
not either terminal predecessor.

## 2026-07-18 — Task 9 attempt 003 throughput stop and operational amendment

Fresh attempt-003 used pre-call SHA-256
`fc3091ba8684b8d08304a3752f0662c9c82e951ee62db40131ed772b1ee65bad`, bound to clean
pushed execution commit `4dd7be9880dcccf2744d05e3617d6411d60ab4de`. After 503 local
pure-solver rows, the live model segment showed an operational defect: the client used
a hard 30-second request timeout, while long raw/proposal generations regularly needed
more time. A non-content WAL snapshot showed every fully failed call taking about 91.5
seconds, exactly matching three 30-second attempts plus the frozen 0.5/1.0-second
backoff. Repair calls were 48/48 successful in that snapshot, while raw calls were 0/8;
continuing would both take roughly 13 days at the observed rate and confound model
outcomes with infrastructure timeout failures.

The run was therefore deliberately interrupted rather than allowed to spend days
collecting systematically timed-out rows. It is terminal `INCOMPLETE` with reason
`interrupted_with_unowned_observation`, `523/10,563` committed rows, 78 logical calls,
and 113 provider attempts. Sixty-two calls succeeded with complete usage and exact
returned model `gpt-5.6-sol`; 16 calls ended `DELEGATE_FAILED`. The 113 attempts comprise
62 successes and 51 failures with unavailable usage. Reported usage is 33,068 input,
21,062 output, 25,085 cache-creation, and 2,263 cache-read tokens. Known cost is
`$0.955113`; charging every usage-missing attempt at the formal `$7.036000` ceiling gives
a tight upper bound of `$359.791113`.

Attempts 001–003 now have cumulative known/tight cost
`$2.130022 / $804.234022`. Adding one further complete formal attempt's unchanged
`$1,167,905.640000` price maximum gives a cumulative audit upper bound of
`$1,168,709.874022`. The amount remains an audit bound, not a proxy pre-consumption
gate.

Attempt-003's abort receipt, raw WAL, receipt-bound WAL, and config SHA-256 values are,
respectively:

- `fee645785388b9455b30c5ac8b3c84d560bca230a9933cd8d7e47239cebc2799`;
- `ac370c1bc9539ff82d372cd32fa0e4ff9566c6ba5af1c4ad9a97c395c7292535`;
- `fb94d3e00d886dd52b41fa8b76ad3f78b28ef6510510f7e0c4602071af311c71`;
- `b01f16850154eacd0a96db326a710df63cc0a36b2cd36282a8eb6efc97bee4d7`.

All 382 WAL events passed canonical encoding, sequence, hash-chain, call pairing, and
attempt pairing checks, with no open intent or attempt. No canonical directory or
private-observations artifact exists. No prompt, response, note, or staging content was
inspected or admitted to documentation. Attempt-003 must never be resumed or
overwritten.

The corrective path preserves all 10,563 rows, ten samples per arm, prompts, model,
`xhigh` reasoning quality, repair/critic behavior, schedule, seeds, pricing, and
statistical estimands. A versioned operational amendment instead raises the formal
request timeout to 300 seconds. An analysis-excluded throughput pilot advances through
`2 → 4 → 8` in-flight units and records success rate, retry rate, provider-latency
P50/P95, and completed-unit/call throughput. One eight-unit block per level is smoke
evidence that may reject but never approve eight-way execution. Freezing `8` requires at
least eight complete blocks (64 units) at both `4` and `8`, an independent confirmation,
8/4 unit throughput at least 1.35, call throughput at least 1.25, tightly bounded
reliability/tail-latency degradation, and no new timeout or integrity failure; missing or
boundary evidence freezes `4`. The current four-unit preregistration must be regenerated
and rebound if the confirmation selects eight.

The separate `task9_throughput_pilot.py` runner uses the same per-lane durable WAL and
completed-unit boundary, a 300-second timeout, and one distinct agent/raw client pair per
worker. Stub summaries and summaries with different execution Git, analysis, lock,
pricing, model, timeout, or corpus bindings cannot enter a comparison; every successful
live call must carry exact provider/model evidence. A graceful SIGINT during setup or
active work leaves a clean resumable root, while a hard kill with an in-flight request is
explicitly fail-closed. Each live block still requires the exact `513,232,896`
micro-USD mechanical spend confirmation; this is an audit ceiling, not expected spend.

Before the formal gate opens, observed pilot rates will yield optimistic, median, and
conservative completion-time estimates. During collection, the estimate will be updated
from actual durable completions after 30 minutes, near each additional 5% of scheduled
units, and whenever throughput changes materially.
The append-only operator log uses canonical `benchmark-progress@0.1.0` JSON lines and
reports the durable row count out of 10,563, current-process/recent throughput, stalled
state, and all three ETA estimates; these records never enter analysis artifacts.

Each unit retains a sequential proposal→repair→critic path and its own durable pre-call
WAL; admission and canonical merge remain in frozen schedule order, while network
completion order is explicitly non-semantic. A completed unit is durably checkpointed
before it becomes resumable. Graceful interruption stops new admissions, drains already
started units, persists their checkpoints, and resumes from the next unit after the
verified durable prefix. No recovery is promised for a provider request interrupted
halfway: an admitted unit without durable completion, or a WAL with an open attempt,
fails closed for operator audit rather than guessing whether the request completed.
The formal invocation runs detached from the interactive Codex session and writes its
PID plus append-only operator log beside the attempt artifacts, so a UI or client
connection loss does not terminate collection.
On terminal concurrent abort, a canonical sidecar binds the coordinator plus every
admitted lane WAL; its raw SHA-256 is embedded in the abort receipt reason so usage from
out-of-order or incomplete lanes remains auditable.

Before the throughput pilot, four prefix-dependent hot paths were linearized: WAL count
checks no longer copy the full history, unit commits validate only the new call suffix,
scheduled-unit membership is constant-time, and final row materialization indexes calls
once. Operational pre-call scalar bindings are parsed once rather than repeatedly
decoding the approximately 4 MB declaration. A local 1,000-append measurement averaged
about 0.022 ms per `fsync`, so the remaining linear duplicate-WAL sync cost is small
relative to provider latency and was not replaced with a more complex batching protocol.
Offline shared-view cost accounting also groups calls by item once instead of rescanning
the full ledger for every item, removing the last observed `O(items × calls)` report path.

The historical 30-second Task 8 and Task 9 artifacts remain byte-immutable. All
timeout/concurrency pilot rows are excluded from formal analysis. Attempts 001–003 are
terminal and must never be resumed or overwritten; the next collection is fresh
attempt-004. Only after the amended runner, preregistration, budget bindings,
crash/resume tests, throughput pilot, and full release gates are pushed may it start.

## 2026-07-19 — Full-rescore linearization and detached recovery gate

Profiling the original full-rescore path on 16 representative items took 206.43 seconds
and showed 336 solver calls and 336 NoteGraph parses: 21 repeated executions per item for
the same stub target. The final report implementation memoizes only pure deterministic
computations within one item: source/target/tab parsing, solver results, all three profile
oracles, checkpoint scores, fidelity, raw request bindings, and pure targets. Every row
still independently validates blob ownership plus stored solver, infeasible, diagnostics,
verdict, score, and ranking fields. The cache resets when the canonical row order advances
to the next item, so peak parsed-tab retention is bounded by one item's rows rather than
the full 10,563-row run. FAST_REAGGREGATE is regression-locked against solver, proposal,
oracle, faithfulness, and fidelity execution. Report fixture wire bytes remain unchanged.

The ordinary full-scale stub gate then ran twice through an external detached `screen`
supervisor using the direct `.venv/bin/fretsure-bench` entry point. Run A received one
`SIGINT` at 167 durable scheduled units, stopped admission, drained to 212, exited, and
resumed from the same output directory. It completed all 10,563 rows in 30:05 including
the interruption and resume. Run B ran continuously and completed in 27:00. Exact file
timestamps put collection/finalization at 25:30 / 4:35 for A and 22:40 / 4:20 for B.
Both receipts are `COMPLETE` with 15,090 observed calls. `diff -rq` reported no difference
across the seven canonical files; their SHA-256 values are:

- blobs: `8f245bec0b8af39d7b6c87e64de07a457d0f2054270f28c5cc03c095f98e5610`;
- config: `0c4ff35269e73aa9f79ab948f05fb0c869e06283d077685b9e685d04622f5414`;
- observations: `8dbcf25e87b6745cb397d1e6db69aadd9ef8cfbc9a374d330aa1641ba583c14e`;
- receipt: `598ee4d364b718081a8c8cae1b7c71acc76a40927d4aa0c64ca675af0a2464ac`;
- report JSON: `98553b148528095ee75dbba7bd7fd5179ea86dd653fa85992faf531967048716`;
- report Markdown: `412d744f1454da567fb81d371279891b7002ce8045a023e92eca70039894501c`;
- rows: `cff6de86e2acfe5dfdfa196ced2b2e4e14cae2233babee1c2611a361852f658f`.

The operator command now explicitly records the PID of the direct benchmark process.
An `exec uv run ...` smoke proved unsuitable on this host because the recorded `uv`
supervisor PID did not forward `SIGINT` to its benchmark child. Recovery-related suites
passed 24/24, the legacy resume test 1/1, and execution-provenance tests 10/10. No runtime
Git or subprocess call exists. The full stub gate made no provider call, inspected no
private prompt/response or staging payload, and changed no frontend surface.

### Subsequent wall-reservation and hard-deadline amendment

Final review found that the original unit reservation equalled only three timeout values
plus retry backoff. Recorded call elapsed time also contains durable WAL and timeout-delivery
overhead, so a three-timeout result could exceed its lane ceiling after its terminal record.
The operational preregistration now binds `10.0` seconds of recorded overhead per attempt.
Its timeout-only full envelope remains `49,879,995,000` milliseconds; the combined
reservation is `51,539,895` seconds, below the unchanged `51,840,000`-second global ceiling.
Legacy preregistration and budget bytes remain unchanged.

HTTPX inactivity timeouts were also not a whole-request bound: a response could keep each
read alive while trickling beyond 300 seconds. The loopback transport now carries one
absolute deadline across pool wait, connect, TLS, write, read, and response streaming. A
daemon watchdog closes only the active expired socket; three retries and attempt WAL hooks
remain unchanged, and the same client can open a healthy connection afterward. Offline
loopback tests covered slow chunks, slow writes, pool waiting, stalled TLS, healthy HTTPS,
and 200 deadline timers without thread leakage. The direct `httpcore` dependency is pinned
to `>=1.0.9,<1.1`. Formal/pilot launch uses numeric loopback rather than `localhost` so
name resolution cannot sit outside the bound.

These changes produced operational preregistration SHA-256
`df7eeee61155c35e5344a61f896e1646ef38afa992cf1d3a2734a843c57cc40a` and operational
budget SHA-256 `0892feb6d4a4b6ed24b916f9bc140867f81f3de34cb4207a3dac4d7643852fd5`.
They do not change model, prompt, corpus, schedule, statistical estimands, or spend. A later
gate audit also established that the preceding `fretsure-bench --stub --prereg` command uses
the legacy sequential stub path. Its hashes remain full-rescore, ordinary-resume,
performance, and byte-determinism evidence, but not evidence for the four-lane coordinator.
The new provider-free `task9_operational_stub_gate.py` keeps the context in stub mode while
driving the production coordinator across the complete schedule; it cannot construct a proxy
client. Final acceptance therefore has two explicit A/B gates: ordinary full-stub report
determinism and operational WAL/READY/admission-drain recovery.

The first operational-gate dry run then exposed one more gate-only nondeterminism: concurrent
stub observations inherited the live monotonic clock, so elapsed microseconds could perturb
the canonical receipt. Operational workers now use the existing zero clock only when the
context remains `stub=True`; formal live observations continue to record real elapsed time.
A two-run coordinator regression locks byte-identical canonical artifacts, and the new gate
script is an exact required sdist entry with an omission regression.

The same audit found a late-SIGINT edge: a signal arriving after schedule completion could
allow canonical publication and then turn the successful CLI exit into status 130. Normal
completion now remains authoritative once terminal publication finishes; signals observed
during setup or collection are still consumed at the existing durable boundaries. Operator
instructions check for a terminal `COMPLETE` receipt before deciding to resume. Formal and
pilot default client creation also mechanically rejects `localhost`; only numeric loopback
can enter the whole-attempt deadline path.

### Final amended ordinary and operational gates

The final ordinary full-stub pair used the amended preregistration and each survived one
clean interruption plus in-place resume. A completed in 28:22 wall time and B in 35:59,
including B's longer operator pause. Both receipts are `COMPLETE` with 10,563 rows and
15,090 observed calls. All seven canonical files are byte-identical; the internal report
SHA-256 is `9fccefa8f62403f23f1518c1bc316af332bacac682a00afd69b351a99044c5fd`.

The final provider-free operational pair then exercised the production four-lane
coordinator over all 10,060 scheduled units. Run A received its only `SIGINT` at 284
admitted units with one apparent in-flight unit. Admission stopped, the unit drained in
under one second, and the boundary stabilized at 284 lane artifacts, 284 READY units, 568
coordinator records, no temporary artifact, and no open handle. The same directory resumed
25 seconds later and finished in 30:12 total wall time. Uninterrupted B finished in 27:24.
Both are `COMPLETE` with 10,563 rows, 15,090 calls, 10,060 READY lane artifacts, 20,120
coordinator records, no reason code, and byte-identical five-file canonical directories.

The final shared SHA-256 values are:

- blobs: `8f245bec0b8af39d7b6c87e64de07a457d0f2054270f28c5cc03c095f98e5610`;
- config: `2cdb96b17eff0f41673dc3189427c4d2b6be4b47264d847e704bb42012f4078d`;
- observations: `8dbcf25e87b6745cb397d1e6db69aadd9ef8cfbc9a374d330aa1641ba583c14e`;
- receipt: `223c9f07593a75f15a5df1b1d457cb20ff7bae1624ec6a76b3c8c9fb6658a39e`;
- rows: `cff6de86e2acfe5dfdfa196ced2b2e4e14cae2233babee1c2611a361852f658f`.

The ordinary report-only additions are report JSON
`8c9e55ae829f84a603a733d2b92347d281ffab5ab22316dfbb38e9af18a4eee8` and report
Markdown `0787de0645789a2ba72d36e7a21adb6dfd8034b400fffb389af55e3f5db6566f`.
No provider call was possible, no private payload or staging content was inspected, and no
frontend surface changed.

The first post-gate strict-mypy pass caught that a compact conditional expression would pass
`None` as the live observation clock even though only the stub branch had been exercised by
the full-scale gate. The final implementation branches explicitly: operational stub workers
pass the zero clock, while live workers omit the argument and retain `ObservingLLM`'s original
monotonic default. Strict mypy and a live-like out-of-order coordinator regression pass; the
stub behavior and therefore the completed gate bytes are unchanged.

### Final provider-free release gates

The final offline suite passed `2599` tests with `8` integration tests deselected and the
existing Starlette/httpx2 migration warning in 1393.94 seconds. A separate environment with
the proxy URL and token removed selected the integration boundary: all `8` tests skipped as
designed, with `2599` deselected and no provider call. Ruff, strict mypy over all 96 source
files and the five release scripts, lock/preregistration generation checks, all 37 Markdown
link targets, and whitespace integrity passed.

The rebuilt 0.6.0 wheel and sdist passed the exact content audit with `116` wheel entries and
`331` sdist entries. The isolated clean-wheel matrix passed core replay, benchmark,
MusicXML, MIDI, score, service, and MCP groups. These gates made no provider call, inspected
no private prompt/response or staging payload, and changed no frontend surface.

### Replicated throughput pilot and four-lane freeze

The analysis-excluded pilot bound execution commit
`08f456d2a21b63dc01e2586fc842e9e8cb64c34a`, analysis SHA-256
`495ac3870a79ef394323f59cd664d551f1696ae58b52184f8e9fc351ec495281`, the unchanged lock,
pricing, model, timeout, and Task 8 pilot corpus. One two-lane smoke block and eight complete
blocks each at four and eight lanes produced 17/17 terminal summaries. Across all summaries,
408 logical calls succeeded, none failed, and nine provider retries were retained. Recorded
known/tight cost was `$11.680634 / $46.673786`; missing usage from failed retry attempts was
not treated as zero.

Four lanes completed 64 units and 191 calls at 225.824948 units/hour and 673.946328
calls/hour, with zero retry and P50/P95 latency 9.552/70.288 seconds. Eight lanes completed
64 units and 193 calls at 221.193397 units/hour and 667.036338 calls/hour, with nine retries
and P50/P95 9.687/77.732 seconds. The 8/4 unit and call ratios were `0.979490526` and
`0.989746973`, below the frozen `1.35` and `1.25` requirements. An independent content-free
audit confirmed all bindings, summary hashes, usage coverage, and the conclusion that formal
collection must retain four lanes. The comparison SHA-256 is
`452d31be314bd66a6fe73548bb8d12078c38a132c968c3b95f92b212c9901d6d`.

For 10,060 network units, the observed four-lane blocks imply an optimistic 35:29, median
41:14, pooled-rate 44:33, and conservative 67:22 completion estimate. These exclude operator
pauses and later offline replay/finalization; canonical progress will replace the pilot
estimate with actual durable throughput after 30 minutes and near each additional 5%.
No private prompt, response, or checkpoint payload was inspected, and the pilot changed no
formal analysis artifact or frontend surface.

### Attempt-004 live 5% checkpoint

Fresh formal attempt-004 started detached with four lanes. Its immutable bindings are
execution commit `773c69deca4d2b00cdcdc5a33841369cb3016955`, pre-call SHA-256
`facafd058cba2eb5223bde1ddf6f6da802cdbc6ab439eb40e749171a8ec9b76b`, and formal
budget-gate SHA-256 `26260e11ebbbdffc05f9770957075f2a88b8925888887cb5564789e5f9f8c5d7`.
The process is supervised outside the runtime by detached `screen`; the collector itself
still invokes neither Git nor a subprocess.

Canonical operator sequence 24 recorded the exact 5% network checkpoint after
16,980.321718 active seconds: 503/10,060 network units, 1,006/10,563 total rows, and 2,451
completed calls. Overall and recent-15-minute throughput were 106.641089 and 112.0
units/hour, respectively. The record was not stalled and estimated 307,190 optimistic and
322,627 median/conservative seconds remaining. Its canonical line SHA-256 is
`eda86df690b9b588f825b6a57d0f52ee4f71a39c6508c5006235f40221673bdb`.

This is operational progress evidence, not a benchmark result. No terminal receipt or
complete usage/cost summary exists yet; retry attempts with missing usage remain unavailable
rather than zero. No prompt, response, unit artifact, lane payload, or private observation
was inspected to produce this checkpoint, and no frontend surface changed.

### Attempt-004 live 10% and 15% checkpoints

The same detached process and immutable execution binding crossed two further durable
milestones without a stall. Canonical operator sequence 29 recorded exactly 10% after
19,688.674576 active seconds: 1,006/10,060 network units, 1,509/10,563 total rows, and
3,446 completed calls. Overall/recent-15-minute unit throughput was
183.943311/1,780.0 per hour, and its optimistic/median/conservative remaining estimates
were 18,312/177,199/177,199 seconds. The exact line SHA-256 is
`d69ca7d4e31baa53482c6206ae09526e1e09d0cfd11120888353b30f30ff9a48`.

Sequence 30 recorded exactly 15% after 19,798.344881 active seconds: 1,509 units,
2,012 rows, and 4,187 completed calls. Overall/recent unit throughput was
274.386573/3,740.0 per hour, and its optimistic/median/conservative remaining estimates
were 8,231/112,191/112,191 seconds. The exact line SHA-256 is
`d154bc8525501152dca55468c4fb5a76af7ecfef10f226e0b5ec62f37d065a6e`.

Those recent-window rates reflect a durable-emission burst and are not treated as a stable
completion forecast. The subsequent sequence-33 snapshot reported 1,899 units, 2,402 rows,
and 4,958 calls (18.876740%), with overall/recent rates of 313.749257/96.0 units/hour and
819.151561/480.0 calls/hour. Its remaining range widened to 93,641–306,038 seconds. At the
same read-only check, coordinator event types were 1,907 `UNIT_ADMITTED` and 1,903
`UNIT_READY`; lane event types were 5,225/5,221 attempt intent/result and 4,982/4,978 call
intent/result. Thus the content-free extra-attempt count was 243. PID and detached `screen`
were healthy, and no terminal or abort marker existed.

Only operator progress fields and aggregate event-type counts were inspected. No prompt,
response, unit artifact, lane payload, or private observation was read, and the live process,
execution SHA, configuration, and frontend remained unchanged.

### Attempt-004 live 20% checkpoint

Canonical operator sequence 39 recorded exactly 20% after 25,622.819133 active seconds:
2,012/10,060 network units, 2,515/10,563 total rows, and 5,479 completed calls. Overall and
recent-15-minute rates were 282.685522/124.0 units/hour and 769.798198/624.0 calls/hour.
The record was not stalled and reported 102,492 optimistic/median and 233,652 conservative
seconds remaining, or approximately 1 day 4 hours 28 minutes through 2 days 16 hours
54 minutes. Its exact canonical line SHA-256 is
`0975c7b1499fc4c7d5df7b86dd668d0702ec36db6956cee8b71481d2a49d6c4a`.

At the same read-only snapshot, coordinator event types were 2,017 `UNIT_ADMITTED` and
2,013 `UNIT_READY`; lane event types were 5,745/5,741 attempt intent/result and
5,492/5,488 call intent/result. The content-free extra-attempt count was therefore 253.
PID 54154 and detached screen 54151 remained healthy, and no terminal or abort marker
existed. Only progress metadata and aggregate event types were inspected; the live process,
execution binding, configuration, private payloads, and frontend were untouched.

### Attempt-004 live 25% checkpoint

Canonical operator sequence 62 recorded exactly 25% after 41,149.404322 active seconds:
2,515/10,060 network units, 3,018/10,563 total rows, and 7,887 completed calls. Overall and
recent-15-minute rates were 220.027486/112.0 units/hour and 690.002698/608.0 calls/hour.
The record was not stalled and reported 123,449 optimistic/median and 242,518 conservative
seconds remaining, approximately 1 day 10 hours 17 minutes through 2 days 19 hours
22 minutes. Its exact canonical line SHA-256 is
`ce9aa6843a774eb3a290dc55ec422eebdbf578939a5e8562ec445faf5c037d9e`.

The subsequent sequence-66 snapshot reported 2,601 units, 3,104 rows, and 8,350 calls
(25.854871%), with overall/recent rates of 214.450061/120.0 units/hour and a remaining range
of 125,216–223,770 seconds. At the same read-only check, coordinator event types were
2,610 `UNIT_ADMITTED` and 2,606 `UNIT_READY`; lane event types were 8,673/8,669 attempt
intent/result and 8,386/8,382 call intent/result. The content-free extra-attempt count was
287. PID and detached `screen` were healthy, and no terminal or abort marker existed.

Only operator progress metadata and aggregate event types were inspected. The process,
immutable execution SHA, configuration, private payloads, and frontend remained untouched.

### Attempt-004 abrupt disconnect and orphan-lane recovery amendment

The host later went offline and the detached process disappeared. The last operator record
reported 2,621 network units, 3,124 rows, and 8,444 calls (26.053678%). The subsequent local
resume reconstruction established a main durable prefix of 2,622 network units plus 503
controls: 3,125 rows and 8,445 calls. It found four admitted but non-READY lanes, indices
2622 through 2625, each with one open call and attempt boundary. The original frozen resume
contract therefore failed closed and emitted an `INCOMPLETE` abort receipt with SHA-256
`2abbeb9f609acc6e01fdb5bb7461eda884dfbe5563f9bdfbbf89f45d996c4efe`.

The operator explicitly overrode the whole-attempt fail-closed policy for current and future
machine disconnects. The amendment retains every durable and READY unit, quarantines each
active lane as a complete byte-exact WAL, creates an empty WAL at the same active index, and
retries at most four complete units. It never truncates an event chain or pretends an open
attempt did not happen. The previous abort receipt and its concurrent audit are quarantined
byte-exact as the recovery transaction's final commit gate.

`scripts/task9_recover_orphan_lanes.py` implements this as a provider-free external operator
tool, leaving `src/fretsure/**` and the bound `773c69de…` collector unchanged. It acquires the
existing writer lock, rejects canonical output or binding drift, emits a mutation-free plan,
requires the exact plan SHA for apply, fsyncs every move and empty replacement, can roll
forward an interrupted apply, and emits a canonical applied receipt. Directed synthetic
tests verify that coordinator/main-journal bytes remain unchanged, active artifacts are also
quarantined, the original coordinator resume accepts the recovered state, apply is
idempotent, wrong hashes make no mutation, and receipt bytes contain no request/response
payload.

The exact real plan SHA-256 is
`bf662a673a380c18365384c04f107652ae1941a226bd9923b5d9e50fce41d90c`
(tool SHA-256 `d0d8eac7262b46029a43b2d4e335e4ab0f996dfcfa76f280f453dfcfe634dd6e`).
It binds the original execution/pre-call/budget gate, abort receipt/audit, config, main
journal, coordinator, and all four active lane hashes. The quarantined supplement contains
nine attempt intents, five terminal complete-usage records, and four open usage-unknown
attempts. Known tokens are 15 input, 5,757 output, 7,403 cache-creation, and zero cache-read.
Under pricing v2 this is `$0.219054` known and `$28.363054` tight. It will be added to the
eventual COMPLETE canonical cost instead of being counted as zero or as benchmark outcome
data.

This is a disclosed post-hoc recovery amendment, not a claim that the original fail-closed
preregistration was unchanged. No prompt, response, unit payload, or private observation was
inspected, and no frontend surface changed.

The operator-only amendment was pushed as commit `12cf262`. Applying the exact plan produced
APPLIED receipt SHA-256
`c53c1d8a05709a7f72ca71d9ed36660ad0b9fef960dab29fc4e81f1ad6ded31c`.
The post-check verified four empty active replacements, byte-exact quarantined WALs, and
unchanged main config/journal plus concurrent config/coordinator hashes. The original runner
then accepted same-directory `--resume` with 2,622 completed units, 3,125 rows, and 8,445
calls. All four retried units subsequently became READY and collection continued beyond them;
the first stable post-recovery snapshot had 2,627 READY / 2,631 admitted, approximately 3,130
rows and 8,458 completed calls (26.11%), with no abort marker.

### Attempt-004 live 30% checkpoint after recovery

Canonical post-recovery operator sequence 17 recorded exactly 30% after 12,349.713427 active
segment seconds: 3,018/10,060 network units, 3,521/10,563 total rows, and 10,451 completed
calls. The resume prefix was 2,622 units and 8,445 calls, so this segment had durably added
396 units and 2,006 calls at the checkpoint. Overall/recent-15-minute segment throughput was
115.435877/108.0 units/hour and 584.758508/616.0 calls/hour. The record was not stalled and
reported 219,613 optimistic/median and 234,734 conservative seconds remaining, approximately
2 days 13 hours through 2 days 17 hours 12 minutes. Its exact canonical line SHA-256 is
`82b8c541e06f5451e4976fce83557b64f02dfa337c58be72745da212dbb38e13`.

The subsequent read-only snapshot had 3,095 `UNIT_ADMITTED` and 3,091 `UNIT_READY` events;
lane event types were 11,200/11,197 attempt intent/result and 10,796/10,793 call
intent/result. The content-free extra-attempt count was therefore 404. PID and detached
`screen` were healthy, and no terminal or abort marker existed. Only progress metadata and
aggregate event types were inspected; the execution binding, private payloads, and frontend
remained untouched.

### Attempt-004 live 35% checkpoint after recovery

Canonical post-recovery operator sequence 42 recorded exactly 35% after 28,807.810163 active
segment seconds: 3,521 network units, 4,024 total rows, and 12,928 completed calls. Segment
overall/recent-15-minute throughput was 112.344534/128.0 units/hour and
560.223075/608.0 calls/hour. The record was not stalled and reported 183,910 optimistic and
209,538 median/conservative seconds remaining, approximately 2 days 3 hours 5 minutes through
2 days 10 hours 12 minutes. Its exact canonical line SHA-256 is
`3dd048c77e0ed21f85ed438acf479a0cd2561a68f10fc5cbc0ccfc22b0104f35`.

The subsequent read-only snapshot had 3,544 `UNIT_ADMITTED` and 3,542 `UNIT_READY` events;
lane event types were 13,449/13,447 attempt intent/result and 13,023/13,021 call
intent/result. The content-free extra-attempt count was 426. PID and detached `screen`
remained healthy, and no terminal or abort marker existed. Only progress metadata and
aggregate event types were inspected; the execution binding, private payloads, and frontend
remained untouched.

### Attempt-004 operator-requested clean pause after 35%

Before leaving, the operator requested an explicit pause until further instruction. Exactly
one `SIGINT` was sent. New admission stopped, the final in-flight unit drained normally, and
the collector plus detached `screen` exited without an abort or canonical marker. The clean
pause state has 3,705 `UNIT_ADMITTED` = 3,705 `UNIT_READY`, corresponding to 4,208 total rows
and 13,840 completed calls (36.83%). Lane event-type totals are 14,279/14,279 attempt
intent/result and 13,840/13,840 call intent/result, so the content-free extra-attempt count is
439 and every active boundary is closed. The hourly automation was set to `PAUSED`; no
same-directory resume is permitted until the user explicitly requests it. No private payload
or frontend was inspected or changed.

### Attempt-004 explicit resume and deferred concurrency retest

The user subsequently instructed the collector to continue. Same-directory `--resume`
started under PID 38427 and detached `screen`; its new operator sequence 0 accepted the clean
3,705-unit, 4,208-row, 13,840-call prefix without an abort. The immutable attempt continues
with its bound four-lane execution contract.

The user also reported that the network environment has improved. The initial instruction
was misunderstood as a post-COMPLETE action; the user clarified that the one-time controlled
four-versus-eight-lane pilot should begin at the next hourly check. To avoid contaminating
both measurements through shared proxy/network load, the operator will first send one
`SIGINT` to drain attempt-004 cleanly, then run eight complete four-lane blocks and eight
complete eight-lane blocks in alternating serial order under a fresh root. The retest holds
the model, proxy, pilot corpus, timeout, pricing, and measurement method constant and compares
durable unit/call throughput, P50/P95 latency, retries, and cost. Once its terminal comparison
exists, attempt-004 resumes in the same directory. The pilot will not rewrite attempt-004 and
must not be launched again after the terminal comparison exists.

### Immediate network retest result

At the next hourly check, attempt-004 received one `SIGINT` and drained cleanly at 3,789
durable units and 14,232 completed calls. The independent retest then completed all sixteen
blocks in alternating serial order without overlapping formal collection. Its terminal
comparison SHA-256 is
`1fcfb8a383c4f1f484f761093faba2f89b00dd6143a6adc697a853c0da2322d3`.

The eight four-lane blocks completed 64 units and 197 calls at 226.126226 units/hour and
696.044790 calls/hour, with zero retries and P50/P95 latency of 10.439/81.895 seconds. The
eight eight-lane blocks completed 64 units and 209 calls at 228.123987 units/hour and
744.967394 calls/hour, also with zero retries and P50/P95 latency of 8.453/80.586 seconds.
The 8/4 unit/call throughput ratios were `1.008834716058` and `1.070286574518`; P95 ratio was
`0.984021689103`, and success-rate delta was zero. The improved network eliminated the old
eight-lane retries and improved latency, but unit throughput rose only 0.88% and call
throughput 7.03%, still below the frozen `1.35 / 1.25` thresholds. The conclusion therefore
remains four lanes. Recorded known and tight cost were equal: `$5.497270` at four lanes and
`$5.801575` at eight, `$11.298845` total.

The batch then automatically resumed attempt-004 in its original directory. New operator
sequence 0 accepted the 3,789-unit, 4,292-row, 14,232-call prefix. The subsequent read-only
snapshot had 3,802 admitted / 3,798 READY, 4,301 rows, and 14,267 completed calls (37.75%),
with no abort marker. The retest terminal comparison is a one-time sentinel and must not be
repeated. Only aggregate throughput, latency, retry, usage/cost, progress, and event-type
metadata were inspected; no private payload or frontend was read or changed.

### Attempt-004 live 40% checkpoint after the network retest

Canonical post-retest operator sequence 8 recorded exactly 40% after 7,397.262962 active
segment seconds: 4,024/10,060 network units, 4,527/10,563 total rows, and 15,430 completed
calls. The 3,789-unit resume prefix means this segment had durably added 235 units and 1,198
calls. Overall/recent-15-minute segment throughput was 114.366625/112.0 units/hour and
583.026455/532.0 calls/hour. The record was not stalled and reported 190,000
optimistic/median and 194,015 conservative seconds remaining, approximately 2 days 4 hours
47 minutes through 2 days 5 hours 54 minutes. Its exact canonical line SHA-256 is
`76f21402594c0acc73affb078e5cd04767df76652074a2837ac152b651cabe69`.

The subsequent read-only snapshot had 4,039 `UNIT_ADMITTED` and 4,035 `UNIT_READY` events,
corresponding to 4,538 rows and 15,496 completed calls (40.11%). Lane event types were
15,939/15,935 attempt intent/result and 15,500/15,496 call intent/result; the content-free
extra-attempt count therefore remained 439, with four currently active boundaries. PID and
detached `screen` were healthy, and no terminal, abort, or canonical marker existed. Only
progress metadata and aggregate event types were inspected; the execution binding, private
payloads, and frontend remained untouched.

### Attempt-004 live 45% checkpoint after the network retest

Canonical post-retest operator sequence 33 recorded exactly 45% after 23,772.847399 active
segment seconds: 4,527/10,060 network units, 5,030/10,563 total rows, and 17,884 completed
calls. The 3,789-unit resume prefix means this segment had durably added 738 units and 3,652
calls. Overall/recent-15-minute segment throughput was 111.757753/124.0 units/hour and
553.034299/608.0 calls/hour. The record was not stalled and reported 160,636 optimistic and
178,232 median/conservative seconds remaining, approximately 1 day 20 hours 37 minutes
through 2 days 1 hour 31 minutes. Its exact canonical line SHA-256 is
`282f78c2e841095f9719ee5fb3fc51d5e01cd60f32f02be65996969f70b19558`.

The subsequent read-only snapshot had 4,593 `UNIT_ADMITTED` and 4,589 `UNIT_READY` events,
corresponding to 5,092 rows and 18,251 completed calls (45.62%). Lane event types were
18,697/18,693 attempt intent/result and 18,255/18,251 call intent/result; the content-free
extra-attempt count was 442, with four currently active boundaries. PID and detached
`screen` were healthy, and no terminal, abort, or canonical marker existed. Only progress
metadata and aggregate event types were inspected; the execution binding, private payloads,
and frontend remained untouched.

### Attempt-004 live 65% checkpoint after the network retest

Canonical post-retest operator sequence 132 recorded exactly 65% after 90,774.274008 active
segment seconds: 6,539/10,060 network units, 7,042/10,563 total rows, and 27,856 completed
calls. The 3,789-unit resume prefix means this segment had durably added 2,750 units and
13,624 calls. Overall/recent-15-minute segment throughput was 109.061737/104.0 units/hour and
540.311675/456.0 calls/hour. The record was not stalled and reported 116,225
optimistic/median and 121,881 conservative seconds remaining, approximately 1 day 8 hours
17 minutes through 1 day 9 hours 51 minutes. Its exact canonical line SHA-256 is
`003eb7025bf3b44fc21cd2dd1cc2f72d9f7a93f822eb691f81900ffac2d734b5`.

The subsequent read-only snapshot had 6,554 `UNIT_ADMITTED` and 6,553 `UNIT_READY` events,
corresponding to 7,056 rows and 27,910 completed calls (65.14%). Lane event types were
28,377/28,376 attempt intent/result and 27,911/27,910 call intent/result; the content-free
extra-attempt count was 466, with one currently active boundary. PID and detached `screen`
were healthy, and no terminal, abort, or canonical marker existed. Only progress metadata
and aggregate event types were inspected; the execution binding, private payloads, and
frontend remained untouched.

### Attempt-004 live 60% checkpoint after the network retest

Canonical post-retest operator sequence 112 recorded exactly 60% after 76,103.297075 active
segment seconds: 6,036/10,060 network units, 6,539/10,563 total rows, and 25,360 completed
calls. The 3,789-unit resume prefix means this segment had durably added 2,247 units and
11,128 calls. Overall/recent-15-minute segment throughput was 106.292372/140.0 units/hour and
526.400321/756.0 calls/hour. The record was not stalled and reported 103,475 optimistic and
136,289 median/conservative seconds remaining, approximately 1 day 4 hours 45 minutes
through 1 day 13 hours 51 minutes. Its exact canonical line SHA-256 is
`6ba204b779a72de4fe86ba108304fe5e13694ab3b1cb5ef486e6b91e7d3440da`.

The subsequent read-only snapshot had 6,060 `UNIT_ADMITTED` and 6,056 `UNIT_READY` events,
corresponding to 6,559 rows and 25,451 completed calls (60.20%). Lane event types were
25,918/25,914 attempt intent/result and 25,455/25,451 call intent/result; the content-free
extra-attempt count was 463, with four currently active boundaries. PID and detached
`screen` were healthy, and no terminal, abort, or canonical marker existed. Only progress
metadata and aggregate event types were inspected; the execution binding, private payloads,
and frontend remained untouched.

### Attempt-004 live 55% checkpoint after the network retest

Canonical post-retest operator sequence 84 recorded exactly 55% after 57,861.903058 active
segment seconds: 5,533/10,060 network units, 6,036/10,563 total rows, and 22,768 completed
calls. The 3,789-unit resume prefix means this segment had durably added 1,744 units and
8,536 calls. Overall/recent-15-minute segment throughput was 108.506628/108.0 units/hour and
531.085194/460.0 calls/hour. The record was not stalled and reported 150,196
optimistic/median and 150,900 conservative seconds remaining, approximately 1 day 17 hours
43 minutes through 1 day 17 hours 55 minutes. Its exact canonical line SHA-256 is
`4b7407d61c355647350ffb90254b6de0ccfbd004b6184f560382227fd5c8c202`.

The subsequent read-only snapshot had 5,551 `UNIT_ADMITTED` and 5,547 `UNIT_READY` events,
corresponding to 6,050 rows and 22,850 completed calls (55.14%). Lane event types were
23,303/23,299 attempt intent/result and 22,854/22,850 call intent/result; the content-free
extra-attempt count was 449, with four currently active boundaries. PID and detached
`screen` were healthy, and no terminal, abort, or canonical marker existed. Only progress
metadata and aggregate event types were inspected; the execution binding, private payloads,
and frontend remained untouched.

### Attempt-004 live 50% checkpoint after the network retest

Canonical post-retest operator sequence 56 recorded exactly 50% after 39,912.844159 active
segment seconds: 5,030/10,060 network units, 5,533/10,563 total rows, and 20,411 completed
calls. The 3,789-unit resume prefix means this segment had durably added 1,241 units and
6,179 calls. Overall/recent-15-minute segment throughput was 111.933892/108.0 units/hour and
557.324352/336.0 calls/hour. The record was not stalled and reported 161,775
optimistic/median and 167,667 conservative seconds remaining, approximately 1 day 20 hours
56 minutes through 1 day 22 hours 34 minutes. Its exact canonical line SHA-256 is
`843a877e793bcde8f32b97a87c7cb4873f025365ebfb27df63f0f212667f971c`.

The subsequent read-only snapshot had 5,046 `UNIT_ADMITTED` and 5,042 `UNIT_READY` events,
corresponding to 5,545 rows and 20,488 completed calls (50.12%). Lane event types were
20,936/20,932 attempt intent/result and 20,492/20,488 call intent/result; the content-free
extra-attempt count was 444, with four currently active boundaries. PID and detached
`screen` were healthy, and no terminal, abort, or canonical marker existed. Only progress
metadata and aggregate event types were inspected; the execution binding, private payloads,
and frontend remained untouched.
