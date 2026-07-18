# Benchmark v2 Task 8 — operational pilot software readiness

> **Status (2026-07-18): ATTEMPT 001 INCOMPLETE; TRACE FIX VERIFIED; ATTEMPT 002 NOT
> AUTHORIZED.** The user authorized the exact `$10.960896` attempt-001 ceiling and the
> live proxy returned canonical `gpt-5.6-sol`. Collection stopped before its first row
> committed when a valid empty diagnostic-code list met an inconsistent non-empty trace
> constraint. The run is retained as private evidence and cannot be resumed. Task 9 has
> not started.

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

[`task8_budget_gate.py`](../scripts/task8_budget_gate.py) accepts only a canonical,
evidence-bound pricing contract with exact integer microunit rates, billing model and
provider, currency, fixed per-attempt cost, four billable token ceilings, rounding
semantics, evidence timestamp/reference, and evidence SHA-256. It computes both pilot
and formal worst cases without floating-point arithmetic. Aggregate billing rounds the
combined token amount once; per-attempt/per-component billing is used only when the
contract explicitly selects it. Missing usage and retry attempts not covered by usage
metadata remain unavailable rather than becoming zero.

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
no usage metadata, so exact billed cost is unavailable. Applying all three 4,096-token
input-bucket ceilings to each of the 7 attempts, plus their stage-specific 9,216
reserved output tokens, gives a conservative attempt-001 upper bound of `$0.613376`.

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

Attempt 002 requires a new execution commit, `collection_attempt=2` pre-call, fresh
directory, and a new exact `$10.960896` authorization. Adding the conservative
attempt-001 upper bound makes the disclosed two-collection-attempt mechanical upper
bound `$11.574272`. The partial outcome was not used to change the model, prompts,
corpus, schedule, or acceptance criteria.

## Offline evidence

- The directed Task 8 suites passed 34 tests: 15 pilot tests and 19 pricing/budget
  tests. Ruff and strict mypy passed both scripts.
- The full offline suite passed 2,451 tests with 8 integration tests deselected. The
  empty-provider integration boundary skipped all 8 integration tests without making
  a call. The rebuilt distribution audit passed with 114 wheel and 315 sdist entries;
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

Build a priced declaration from the checked-in contract and explicit execution digests;
this command does not inspect Git or authorize collection:

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

Only after the user approves the exact computed value may a live invocation supply the
second, matching confirmation:

```bash
uv run python scripts/task8_pilot.py \
  --live \
  --pre-call-config /secure/path/pilot-pre-call.json \
  --authorized-maximum-spend-microunits <exact-pilot-maximum> \
  --output-dir /secure/path/pilot-attempt-002
```

The completed live receipt and root operational summary then feed the non-authorizing
formal gate:

```bash
uv run python scripts/task8_budget_gate.py \
  --prereg docs/experiments/2026-07-17-benchmark-v2-prereg.json \
  --pricing-contract /secure/path/pricing-contract.json \
  --expected-pricing-sha256 <pricing-contract-sha256> \
  --pilot-summary /secure/path/pilot-attempt-002/operational-summary.json \
  --pilot-receipt /secure/path/pilot-attempt-002/canonical/receipt.json \
  --output /secure/path/formal-budget-gate.json
```

## Remaining external gate

Before attempt 002, explicitly approve its mechanically computed `$10.960896` maximum
after seeing the cumulative `$11.574272` two-collection-attempt upper bound. Then the new live run
must use the captured official pricing basis, new pre-call, and new output directory.
After a complete pilot, the formal budget gate will bind its receipt, operational
summary, pricing contract, and unchanged formal preregistration. Task 9 still requires a
separate explicit authorization of the computed formal spend and call budget. Until
then, the correct state is paused at the fresh-attempt spend gate.
