# Benchmark v2 Task 8 — operational pilot software readiness

> **Status (2026-07-18): TASK 8 PILOT COMPLETE; FORMAL BUDGET GATE GENERATED; TASK 9
> NOT AUTHORIZED.** Attempt 001 remains an immutable incomplete run. After the trace fix
> and a separate explicit `$10.960896` authorization, fresh attempt 002 completed all
> `8/8` scheduled rows. Its six failed provider attempts do not expose usage, so actual
> and projected cost remain `incomplete_attempt_usage`. The formal gate is explicitly
> non-authorizing; no Task 9 model call has started.

## Frozen operational pilot

The canonical [pilot specification](experiments/2026-07-18-benchmark-v2-pilot-spec.json)
has SHA-256
`e455a608d4b186f24a2739e009b8f9fe604036fd3a4f34d0ef97d2afb3ab7ad3` and binds the
Task 7 preregistration SHA
`ad9129edfb47634085f7bfd5557ca76f59eb8358865a1742bfcba69fa0c1362b` without copying
its 503-item payload. Its two two-bar procedural families are disjoint from the formal
corpus by item, family, cluster, source, and NoteGraph digests. Each family has two
paired agent/raw samples, and every proposal/raw request retains the 2,048-token cap.

The pilot ceilings are:

| Resource | Full pilot | One complete pair |
|---|---:|---:|
| Logical calls | 44 | 11 |
| Provider attempts | 132 | 33 |
| Maximum retries | 88 | 22 |
| Requested output tokens | 51,200 | 12,800 |
| Attempt-reserved output tokens | 153,600 | 38,400 |
| Provider timeout envelope | 4,026,000,000 µs | 1,006,500,000 µs |
| Durable recorded-elapsed ceiling | 5,400,000,000 µs | — |
| Active host deadline per invocation | 5,400,000,000 µs | — |

The durable provider elapsed total and the invocation-local host deadline are separate
quantities. Resume restores the former from the WAL and starts a fresh monotonic host
deadline. Before each agent row the collector reserves the complete agent/raw pair;
before the following raw row it reserves the remaining raw unit.

## Price and authorization gate

[`task8_budget_gate.py`](../scripts/task8_budget_gate.py) accepts an immutable canonical
pilot pricing contract plus a separately hashed formal billing envelope. The pricing
contract binds exact integer microunit rates, model/provider, currency, fixed
per-attempt cost, rounding semantics, evidence, and the pilot ceilings used by both live
pre-calls. The formal envelope binds only the wider formal ceilings, its enforcement
scope, and the exact pilot-pricing SHA. Rate terms are never silently replaced. All
cost arithmetic is integer-only; missing usage remains unavailable rather than becoming
zero.

[`task8_pilot.py`](../scripts/task8_pilot.py) embeds the complete canonical pricing
contract in a pre-call declaration, recomputes its raw hash and mechanical pilot cost,
and requires the billing model and output ceiling to match the frozen pilot. Every live
prompt is also bounded before observation, retry, or network I/O: its visible UTF-8 byte
length plus a fixed 256-token two-message framing allowance must fit each declared input
billing bucket. Creating the declaration is not authorization: a live invocation must
separately provide the same exact maximum-spend microunit value. Missing or drifted
confirmation fails before client construction or output creation. No signature service,
runtime price discovery, Git query, or runtime subprocess is involved.

The checked-in [official price source snapshot](experiments/2026-07-18-gpt-5.6-sol-pricing-source.json)
has SHA-256
`6293e6c59908b53335e4725f3a36434966ee2e8a083cd79513b2f46746144b0f`. It records the
OpenAI standard short-context `gpt-5.6-sol` rates per million tokens: input
`$5.00`, cache write `$6.25`, cache read `$0.50`, and output `$30.00`. The canonical
[pricing contract](experiments/2026-07-18-gpt-5.6-sol-pricing-contract.json) has SHA-256
`c93229c60003905d0946bd4d66096943a337a3763839715f296ecb338148baa5`, freezes a
4,096-token ceiling for each input billing bucket and 2,048 output tokens per attempt,
and computes a deliberately conservative full-pilot maximum of `10,960,896` micro-USD
(`$10.960896`). This is an official direct-price reference; a live proxy must use the
same billing basis rather than add an undisclosed surcharge.

The checked-in
[formal billing envelope](experiments/2026-07-18-gpt-5.6-sol-formal-billing-envelope.json)
has SHA-256
`5bcd24585db7a062955b2dc3de543e8ecc7e875c4647b6d767e348ee1cb15b5d`. It is bound to
the pricing-contract SHA above and declares per-attempt ceilings of `272,000` for each
input/cache billing bucket and `16,384` output tokens. The formal output templates retain
their item-specific request limits—the largest is `15,968`—while `92,904,960` is the
full-run requested-output total, not a per-call limit. Before Task 9 can start, every
formal prompt must enforce the envelope's `UTF-8 bytes + 256` input upper bound before
observation, retry, or network I/O.

The still-unrun formal workload remains independent of the pilot:

| Resource | Formal worst case |
|---|---:|
| Paired samples | 5,030 |
| Logical calls | 55,330 |
| Provider attempts | 165,990 |
| Maximum retries | 110,660 |
| Requested output tokens | 92,904,960 |
| Attempt-reserved output tokens | 278,714,880 |
| Provider timeout envelope | 5,062,695,000,000 µs |
| Durable recorded-elapsed ceiling | 5,184,000,000,000 µs |

Pilot-informed estimates use stage-level proposal/raw, repair, and critic totals. The
formal proposal/raw token base comes from the 503-item preregistration, so it is never
underestimated by scaling the pilot's fixed 2,048-token calls. Estimates are labeled
non-authorizing, and the pilot is never subtracted from the formal workload.

## Live attempt 001 interruption

The user explicitly authorized the exact `10,960,896` micro-USD maximum before the live
command ran. Attempt 001 then produced 26 closed WAL events: 6 logical calls, 7 provider
attempts, one retry, 6 successful call results, and no open intent. All returned model
IDs were exactly `gpt-5.6-sol`. The reported successful usage totals were 4,515 input
tokens, 1,740 output tokens, and zero cache-creation/cache-read tokens; those reported
values price to `$0.074775` under the reference contract. The failed retry attempt had
no usage metadata, so exact billed cost is unavailable. Combining the known usage with
that one attempt's input and stage-specific output ceilings gives the tight reference
interval `$0.074775..$0.184343`.

No row was committed before the exception. Six terminal calls therefore have no owning
staged unit, so the existing fail-closed resume contract rejects this directory before
any new provider request or network attempt. The run must not be resumed or
reconstructed from response content.

The private pre-call, manifest, and WAL were copied byte-identically into gitignored
`outputs/private/benchmark-v2-task8/`; their SHA-256 values are respectively
`26b89f0545e2e789fd375a64e4faa247016d79cc74b1537c36d9cda324cc5c1d`,
`8e5b51d0801de6212205432a58bf9b176b503a36c66baabb552bb03a0eac3d19`, and
`5e81f59b49e7e25116af57b12f9963f1e2ad4cdcafb4eaf02f0a846ea9747f07`.
No canonical receipt, rows, blobs, observations, or operational summary exists.

The terminal exception reported a non-empty-list trace violation after the final closed
call, and an offline deterministic regression reproduced it. AMBER compares optimistic
and pessimistic profiles while its public diagnostics localize the median profile, so
`AMBER` with zero median diagnostics is valid. The repair path correctly supplied an
empty `based_on_diagnostic_codes` list, but the trace validator incorrectly required
that list to be non-empty. The fix only permits the existing field to be empty; list
type, maximum length, unique codes, and stable-code validation remain unchanged. It does
not fabricate a diagnosis or change repair policy. The WAL alone establishes the call
boundary and incomplete unit, not the exception's cause.

The partial outcome was not used to change the model, prompts, corpus, schedule, or
acceptance criteria. Attempt 002 used a new execution commit, `collection_attempt=2`
pre-call, fresh directory, and separate authorization; attempt 001 was never resumed.

## Live attempt 002 completion

The user independently authorized attempt 002's exact `10,960,896` micro-USD ceiling
after reviewing attempt 001. The fresh run completed all `8/8` scheduled rows and
finalized its canonical receipt, rows, blobs, observations, and operational summary.

| Operational measure | Attempt 002 |
|---|---:|
| Logical calls | 27 |
| Provider attempts | 31 |
| Retries | 4 |
| Requested output tokens | 34,304 |
| Attempt-reserved output tokens | 42,496 |
| Recorded provider elapsed | 473,726,578 µs |
| Active host elapsed | 477,264,352 µs |
| Committed rows | 8/8 |

The active-host value is from the live command's completion output; the canonical
operational-summary artifact intentionally persists provider elapsed time, not host wall
time.

Twenty-five successful attempts reported 18,781 input tokens, 11,482 output tokens,
and zero cache-creation/cache-read tokens. Six failed attempts did not report usage.
The known usage prices to `$0.438365`; applying only the missing attempts' applicable
ceilings gives the tight reference interval `$0.438365..$1.095773`. Because usage does
not cover every attempt, both the formal gate's pilot actual cost and pilot-informed
projection correctly remain `incomplete_attempt_usage` rather than treating missing
tokens as zero.

Across attempts 001 and 002, known reported usage costs `$0.513140`; the combined tight
upper bound is `$1.280116`. These are audit intervals, not replacements for provider
billing records and not authorization for another collection.

The following are SHA-256 hashes of the exact retained file bytes; the underlying files
remain private and gitignored:

| Attempt-002 artifact | Raw-file SHA-256 |
|---|---|
| Config | `99064424dedf4087c4299adaf6836790277d7011bde8f6202188c0d1deb00dbc` |
| WAL journal | `3be360890e7f090fb48ad7836e471f1876ccc12895d035c9011db85bb7b2992a` |
| Operational summary | `d299678367dccc9b9fb1b2f0c386daf961522b937cf04b0017a9aa2f1f5e041c` |
| Canonical receipt | `10802ea02d4b6188122338fb99b4327efcf175e55f59c138e1194805b204a5da` |
| Canonical rows | `61a84ad1329df420ef16d7ce37a417ccc8f1bb172356f204f6d35ca94d810d75` |
| Canonical blobs | `fd74a399ba30d084371606a0fd685d6cb0d1365ff1181983f8730b9ea5137f37` |
| Canonical observations | `bae49ce5e3081ddce80f99cd55f09981c159611e86b91b657af83601ae582f98` |

These raw-file hashes intentionally differ from any domain-separated hashes embedded
inside the receipt. A receipt binding authenticates its defined canonical domain; it is
not specified as the ordinary SHA-256 of the enclosing file bytes.

## Formal budget gate and Task 9 boundary

The non-authorizing formal gate binds the pilot pricing contract SHA
`c93229c60003905d0946bd4d66096943a337a3763839715f296ecb338148baa5`, formal envelope
SHA `5bcd24585db7a062955b2dc3de543e8ecc7e875c4647b6d767e348ee1cb15b5d`, completed
attempt-002 receipt/summary, and unchanged formal preregistration. Its raw-file SHA-256
is `a421e1c330b600dbd19cdc3da145967033c9740132278c0c7afa7f62711fc57e`.

The mechanical formal worst case is `538,865,486,400` micro-USD
(`$538,865.486400`). The gate leaves the external ceiling null with status
`authorization_required`; its actual and projected costs remain
`incomplete_attempt_usage`. Generating this artifact does not authorize the formal run.
Task 9 has not started and requires both the pre-network formal input guard and a new,
independent user authorization for the exact formal spend and call envelope.

The corrected pilot-informed resource projection is 33,953 logical calls, 38,983
attempts, 71,658,496 requested output tokens, and 96,220,416 attempt-reserved output
tokens. It remains a non-authorizing projection, not a reduction of the formal worst
case.

## Offline evidence

- The directed Task 8 suites passed 39 tests: 15 pilot tests and 24 pricing/budget
  tests. Ruff and strict mypy passed both scripts.
- The full offline suite passed 2,456 tests with 8 integration tests deselected. The
  empty-provider integration boundary skipped all 8 integration tests without making
  a call. The rebuilt distribution audit passed with 114 wheel and 316 sdist entries;
  the clean install matrix passed for core replay, benchmark, MusicXML, MIDI, score,
  service, and MCP.
- One-shot stub collection and a clean resume after one committed agent row produced
  byte-identical five-file canonical bundles. Their SHA-256 values were: config
  `5d56b53a77d79489cfa32c7fa39d3f87cf3b406c62ac231b930faf97492f7d31`, receipt
  `0c19a0b48c55f5671b537c37759d925de3162f11505e38480dd5804debdcb71b`, rows
  `801f20a6e184d5b74046e65a97ccbf0444ab6ae4f92055975eadda9ac7cc9a03`, blobs
  `6d62d0ff27fd8489c606d1ba841e8366e21794cb2617863a1dbbf55f61860613`, and observations
  `24970df37eb827b6a0ef17c2b0d9132c02a1710298b863a1be66060d382ad8c6`.
- The stub made 12 logical calls with no repairs. Stub latency, returned-model metadata,
  and usage remained unavailable, and no budget-usage summary was written.
- Pilot artifacts have a distinct run ID and manifest schema. The formal runner rejects
  them, and no pilot callback can generate or alter a formal report.
- The live finding added one minimal trace-validator relaxation plus two regression
  tests. The formal preregistration, schedule, prompts, corpus, runner script, package
  version, lockfile, frontend, and visual design remain unchanged; the trace-validation
  runtime and execution/wheel digests changed. The repaired wheel SHA-256 is
  `f24e510a56219d1c7673d03ec5870736b523c195adea73f82a1765a71738372d`.
- Attempt 002 satisfied the operational acceptance purpose: a fresh authorized run
  completed every scheduled row, finalized all canonical pilot artifacts, and retained
  missing failed-attempt usage as unavailable. The formal envelope and budget gate bind
  the completed receipt without moving any outcome threshold or starting Task 9.

## Commands

The checked-in pilot spec and the offline stub are reproducible without a proxy:

```bash
uv run python scripts/task8_pilot.py \
  --check-spec docs/experiments/2026-07-18-benchmark-v2-pilot-spec.json
uv run python scripts/task8_pilot.py \
  --stub \
  --spec docs/experiments/2026-07-18-benchmark-v2-pilot-spec.json \
  --output-dir /tmp/fretsure-task8-stub
```

The following declaration/live sequence is retained only as the completed attempt-002
procedure. It must not be rerun or resumed:

```bash
uv run python scripts/task8_pilot.py \
  --write-pre-call /secure/path/pilot-pre-call.json \
  --spec docs/experiments/2026-07-18-benchmark-v2-pilot-spec.json \
  --pricing-contract docs/experiments/2026-07-18-gpt-5.6-sol-pricing-contract.json \
  --collection-attempt 2 \
  --execution-git-sha <task8-commit-sha> \
  --analysis-code-sha256 <pilot-analysis-sha256> \
  --uv-lock-sha256 <uv-lock-sha256>
```

```bash
uv run python scripts/task8_pilot.py \
  --live \
  --pre-call-config /secure/path/pilot-pre-call.json \
  --authorized-maximum-spend-microunits <exact-pilot-maximum> \
  --output-dir /secure/path/pilot-attempt-002
```

The completed receipt and operational summary feed the non-authorizing formal gate. The
absence of `--formal-maximum-spend-microunits` is intentional: this command records that
authorization is still required.

```bash
uv run python scripts/task8_budget_gate.py \
  --prereg docs/experiments/2026-07-17-benchmark-v2-prereg.json \
  --pricing-contract docs/experiments/2026-07-18-gpt-5.6-sol-pricing-contract.json \
  --expected-pricing-sha256 \
    c93229c60003905d0946bd4d66096943a337a3763839715f296ecb338148baa5 \
  --formal-billing-envelope \
    docs/experiments/2026-07-18-gpt-5.6-sol-formal-billing-envelope.json \
  --expected-formal-billing-envelope-sha256 \
    5bcd24585db7a062955b2dc3de543e8ecc7e875c4647b6d767e348ee1cb15b5d \
  --pilot-summary /secure/path/pilot-attempt-002/operational-summary.json \
  --pilot-receipt /secure/path/pilot-attempt-002/canonical/receipt.json \
  --output /secure/path/formal-budget-gate.json
```

## Subsequent Task 9 authorization

Task 8 is complete; attempt 002 must not be rerun. The Task 9 collector now enforces the
formal envelope's `UTF-8 bytes + 256` input bound before observation, retry, or network
I/O. On 2026-07-18 the user independently authorized all project model billing,
including the exact `$538,865.486400` mechanical formal ceiling and frozen call
envelope. The generated gate itself still grants no such authorization.
