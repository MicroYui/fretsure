# Benchmark v2 Task 8 — operational pilot software readiness

> **Status (2026-07-18): TASK 8 PILOT COMPLETE; BILLING CONTRACT CORRECTED; TASK 9
> ATTEMPTS 001–003 TERMINAL INCOMPLETE; FRESH ATTEMPT-004 NEXT.** Task 8 attempt 001
> remains an immutable incomplete run. After the trace fix and a separate explicit historical `$10.960896`
> authorization, fresh Task 8 attempt 002 completed all `8/8` scheduled rows. Subsequent
> live evidence showed that provider `output_tokens` includes non-visible tokens and can
> exceed a request's visible-output limit. The official 128,000-token model contract now
> supplies the billable ceiling: the corrected Task 8 combined known/tight interval is
> `$0.513140..$27.730036`, and the official-contract pilot mechanical maximum is
> `$513.232896`. Task 9 attempts 001, 002, and 003 are preserved as terminal `INCOMPLETE`;
> the next live run may only be a fresh attempt-004 after the operational amendment,
> throughput pilot, and release gates are pushed.

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

The historical checked-in [official price source snapshot](experiments/2026-07-18-gpt-5.6-sol-pricing-source.json)
has SHA-256
`6293e6c59908b53335e4725f3a36434966ee2e8a083cd79513b2f46746144b0f`. It records the
OpenAI standard short-context `gpt-5.6-sol` rates per million tokens: input
`$5.00`, cache write `$6.25`, cache read `$0.50`, and output `$30.00`. The canonical
[pricing contract](experiments/2026-07-18-gpt-5.6-sol-pricing-contract.json) has SHA-256
`c93229c60003905d0946bd4d66096943a337a3763839715f296ecb338148baa5`, freezes a
4,096-token ceiling for each input billing bucket and 2,048 output tokens per attempt,
and computes a deliberately conservative full-pilot maximum of `10,960,896` micro-USD
(`$10.960896`). That value was the exact amount declared and authorized for the two
historical Task 8 invocations, but it incorrectly treated the 2,048 visible-output
request limit as the maximum billable `output_tokens` usage. It remains immutable audit
history rather than the current cost ceiling.

The corrected [official price source v2](experiments/2026-07-18-gpt-5.6-sol-pricing-source-v2.json)
has SHA-256
`b16339b98c7ad7a269dc6d9d07416f8071a7b14f4ee4afeccea84940230c2062`; its
[pricing contract v2](experiments/2026-07-18-gpt-5.6-sol-pricing-contract-v2.json) has
SHA-256
`7b5ae715a08bb4e1cc7cca32e77db6ffc7e5f000133150194cf70a4b8f62c9b2`.
It binds the official `gpt-5.6-sol` 128,000-token maximum to the billable
`output_tokens` field, which includes non-visible generated tokens. Under that contract,
the pilot's mechanical maximum is `513,232,896` micro-USD (`$513.232896`). This is an
official-contract audit maximum, not a claim that the local proxy implements a
pre-consumption hard stop.

The historical v0.1 formal-envelope bytes had SHA-256
`5bcd24585db7a062955b2dc3de543e8ecc7e875c4647b6d767e348ee1cb15b5d`; the historical
gates and Task 9 attempt-001 pre-call preserve that binding unchanged. The current
[formal billing envelope v0.2](experiments/2026-07-18-gpt-5.6-sol-formal-billing-envelope.json)
has SHA-256
`a1969546babcdcbcbf281c682260c38551b2fd12ef382014eb34a79e85df5544`. It binds pricing
contract v2 and declares per-attempt ceilings of `272,000` for each input/cache billing
bucket and `128,000` billable output tokens. The formal output templates retain their
item-specific visible request limits—the largest is `15,968`—while `92,904,960` is the
full-run requested-output total, not a billable per-attempt cap. Every formal prompt must
still enforce the envelope's `UTF-8 bytes + 256` input upper bound before observation,
retry, or network I/O.

The preregistered formal workload remains independent of the pilot:

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
that one attempt's input and the official 128,000-token billable-output ceiling gives
the corrected tight reference interval `$0.074775..$3.962903`.

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
The known usage prices to `$0.438365`; applying the official billable-output ceiling to
the six usage-missing attempts gives the corrected tight reference interval
`$0.438365..$23.767133`. Because usage does
not cover every attempt, both the formal gate's pilot actual cost and pilot-informed
projection correctly remain `incomplete_attempt_usage` rather than treating missing
tokens as zero.

Across attempts 001 and 002, known reported usage costs `$0.513140`; the corrected
combined tight upper bound is `$27.730036`. These are audit intervals, not replacements
for provider billing records and not authorization for another collection.

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

## Formal budget gate correction and Task 9 boundary

The historical non-authorizing formal gate binds the pilot pricing contract SHA
`c93229c60003905d0946bd4d66096943a337a3763839715f296ecb338148baa5`, formal envelope
SHA `5bcd24585db7a062955b2dc3de543e8ecc7e875c4647b6d767e348ee1cb15b5d`, completed
attempt-002 receipt/summary, and unchanged formal preregistration. Its raw-file SHA-256
is `a421e1c330b600dbd19cdc3da145967033c9740132278c0c7afa7f62711fc57e`.

It declared `538,865,486,400` micro-USD (`$538,865.486400`) by treating the envelope's
visible request ceiling as the billable-output ceiling. The later authorized historical
gate has SHA-256
`931b5ae14d587d89511aa3b5c45c7458e96c377df54093ad6244a14948527bd9`.
Both artifacts remain immutable evidence and must not be rewritten, but neither may be
used for another provider call.

Under pricing contract v2 and the official 128,000-token billable-output maximum, the
formal workload's corrected mechanical maximum is `1,167,905,640,000` micro-USD
(`$1,167,905.640000`). It is an official-contract upper bound, not a local proxy
pre-consumption hard gate. The user has separately authorized all project model billing;
artifact generation still does not itself grant permission. The corrected
`benchmark-formal-budget-gate@0.3.0` binds pricing contract v2 and envelope v0.2; it was
regenerated byte-identically and passed `--check`. Its SHA-256 is
`9b50fd8a271a78705e728de8f8cbb24a09e08b24eb2db9122df6a943bdd958f6`.

The corrected pilot-informed resource projection is 33,953 logical calls, 38,983
attempts, 71,658,496 requested output tokens, and 96,220,416 attempt-reserved output
tokens. It remains a non-authorizing projection, not a reduction of the formal worst
case.

## Task 9 attempt-001 terminal interruption

The historical pre-call SHA-256 is
`3d9995e7f4561059077b1c9bc3b956a79b6db632f6252a24653e65cd811f3450`; it binds run ID
`benchmark-v2-formal-20260717-attempt-001`, execution commit
`7ba371fe39c5ac41a1bcdc5dbe8563fd2fcfd498`, and the historical authorized gate above.
These bytes and the attempt directory are immutable and must never be resumed or
overwritten.

Attempt-001 completed all 503 pure-solver units and committed the first agent unit. Its
thirteenth logical call was a critic request with visible limit `512`; the provider
returned exact model `gpt-5.6-sol` and reported `704` billable output tokens. The former
validator incorrectly required billable output usage to be no greater than the visible
request limit, so the durable sink terminated the run with reason code
`provider_integrity_failure`. The terminal state is `INCOMPLETE`. No private prompt or
response content was inspected, copied into documentation, or used to change the
experiment.

Reported usage prices to a known `$0.188415`. Four attempts lack usage; applying the
official 128,000-token billable-output maximum produces a corrected tight upper bound of
`$28.332415`. Its successor was the fresh attempt-002 recorded below; attempt-001 itself
remains immutable.

## Task 9 attempt-002 terminal interruption

Attempt-002 used pre-call SHA-256
`48796200a05af2cbc9ae83d80f06a89ff437841810241954a8b7fe3f794be6eb`, bound to
execution commit `1feeef622d96a95b187c473a40e273852cdf6a45`. It durably committed
`524/10,563` scheduled rows before terminal interruption. The run made 91 logical calls
and 131 provider attempts: 72 attempts succeeded with complete usage, 59 attempts have
missing usage, and 19 logical calls ended `DELEGATE_FAILED`. Missing usage remains
unavailable rather than being recorded as zero.

The structural failure began after a legal edit was applied and created a duplicate
onset/pitch. Local validation of the target checkpoint raised instead of returning the
existing invalid-edit control result; the collector then terminated the run as
`INCOMPLETE` with reason code `unexpected_unowned_observation`. No private prompt or
response was inspected or admitted to documentation or canonical artifacts.
Attempt-002's known cost is `$0.986494`; applying
the official per-attempt ceiling to usage-missing attempts gives a tight upper bound of
`$416.110494`. Attempt-002 must never be resumed or overwritten.

At this historical checkpoint, the combined tight upper bound for Task 9 attempts 001 and
002 was `$444.442909`. Adding one complete fresh formal attempt's `$1,167,905.640000`
mechanical maximum gave a cumulative upper bound of `$1,168,350.082909`. These remain
historical audit bounds, not claims of a local proxy pre-consumption hard gate.

The narrow correction maps post-edit pitch-bound violations and onset/pitch collisions
to the existing `MODEL_EDIT_INVALID` → `RECHECK` path. It does not change the prompt,
model, corpus, schedule, or trace schema. This was the gate for the subsequently executed
attempt-003 and is no longer the current next-step instruction.

## Task 9 attempt-003 terminal state and operational amendment

Fresh attempt-003 used pre-call SHA-256
`fc3091ba8684b8d08304a3752f0662c9c82e951ee62db40131ed772b1ee65bad`, bound to execution
commit `4dd7be9880dcccf2744d05e3617d6411d60ab4de`. After all 503 local pure-solver rows, the
live segment exposed a systemic 30-second request timeout: long raw/proposal generations
repeatedly exhausted three 30-second attempts plus the fixed retry backoffs. The run was
stopped at `523/10,563` rows, 78 logical calls / 113 provider attempts and is terminal
`INCOMPLETE` with reason `interrupted_with_unowned_observation`. Its known/tight cost is
`$0.955113 / $359.791113`; it must never be resumed or overwritten.

Attempts 001–003 now total `$2.130022 / $804.234022` known/tight cost. Adding one complete
formal attempt's unchanged `$1,167,905.640000` maximum gives cumulative audit maximum
`$1,168,709.874022`. The next collection must be fresh attempt-004 with a new pre-call,
formal budget gate, and output directory. The amended request timeout is 300 seconds and
the current formal candidate remains four in-flight units. The 300-second value is now a
whole-attempt deadline across pool/connect/TLS/write/read and slow chunking, with a separate
10-second recorded-overhead reservation per attempt. An analysis-excluded pilot runs
`2 → 4 → 8`; selecting `8` requires at least eight complete blocks at both `4` and `8`
(64 units per level) plus independent confirmation, otherwise `4` remains frozen. Formal
collection runs detached, resumes only at verified durable-unit boundaries, emits progress
JSONL only to the append-only operator log, and invokes neither Git nor subprocesses at runtime.

## Offline evidence

- The final amended gates split report determinism from operational recovery. The ordinary
  full-stub pair both completed 10,563 rows / 15,090 calls after one clean interruption and
  resume, and all seven canonical files were byte-identical. In the production-coordinator
  gate, run A received its only `SIGINT` at 284 admitted scheduled units; one apparent
  in-flight unit drained in under one second, the clean 284-unit prefix resumed in place,
  and A completed in 30:12. Uninterrupted B completed in 27:24. Both receipts are `COMPLETE`,
  all 10,060 lane artifacts are READY, and the five canonical files are byte-identical.
  Their SHA-256 values are blobs `8f245bec0b8af39d7b6c87e64de07a457d0f2054270f28c5cc03c095f98e5610`,
  config `2cdb96b17eff0f41673dc3189427c4d2b6be4b47264d847e704bb42012f4078d`,
  observations `8dbcf25e87b6745cb397d1e6db69aadd9ef8cfbc9a374d330aa1641ba583c14e`,
  receipt `223c9f07593a75f15a5df1b1d457cb20ff7bae1624ec6a76b3c8c9fb6658a39e`,
  and rows `cff6de86e2acfe5dfdfa196ced2b2e4e14cae2233babee1c2611a361852f658f`.
- Before the final wall-reservation preregistration amendment, the ordinary full-scale
  stub gate completed all `10,563` rows twice. Run A received
  one `SIGINT` at 167 durable units, drained to 212, resumed in the same output directory,
  and completed in 30:05. Uninterrupted run B completed in 27:00. Both receipts were
  `COMPLETE` with 15,090 observed calls, and `diff -rq` found no difference across all
  seven canonical files. The last durable unit to canonical publication took 4:35 in A
  and 4:20 in B after the full-rescore memoization change. This command used the legacy
  sequential stub path, so it is report-performance, ordinary-resume, and byte-determinism
  evidence rather than a four-lane coordinator gate. The provider-free
  `task9_operational_stub_gate.py` separately drives the production coordinator over the
  complete schedule while remaining unable to construct a proxy client.
- The final 2026-07-19 provider-free operational-amendment release gate passed
  `2599` offline tests with `8` integration tests deselected. With the proxy URL and token
  removed, the integration boundary skipped all `8` selected tests without a provider call.
  The rebuilt distributions passed the 116-wheel/331-sdist content audit and all seven
  isolated clean-install smoke groups.
- The directed Task 8 suites passed 39 tests: 15 pilot tests and 24 pricing/budget
  tests. Ruff and strict mypy passed both scripts.
- At the Task 8 closeout, the full offline suite passed 2,456 tests with 8 integration
  tests deselected. The
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

The completed receipt and operational summary feed the corrected formal gate. The exact
official-contract maximum is repeated explicitly; artifact generation remains separate
from the user's project-wide billing authorization.

```bash
uv run python scripts/task8_budget_gate.py \
  --prereg docs/experiments/2026-07-17-benchmark-v2-prereg.json \
  --pricing-contract docs/experiments/2026-07-18-gpt-5.6-sol-pricing-contract-v2.json \
  --expected-pricing-sha256 \
    7b5ae715a08bb4e1cc7cca32e77db6ffc7e5f000133150194cf70a4b8f62c9b2 \
  --formal-billing-envelope \
    docs/experiments/2026-07-18-gpt-5.6-sol-formal-billing-envelope.json \
  --expected-formal-billing-envelope-sha256 \
    a1969546babcdcbcbf281c682260c38551b2fd12ef382014eb34a79e85df5544 \
  --pilot-summary /secure/path/pilot-attempt-002/operational-summary.json \
  --pilot-receipt /secure/path/pilot-attempt-002/canonical/receipt.json \
  --formal-maximum-spend-microunits 1167905640000 \
  --output outputs/private/benchmark-v2-task9/formal-budget-gate-v3-authorized.json
```

## Subsequent Task 9 authorization history

Task 8 is complete; its attempt 002 must not be rerun. The Task 9 collector now enforces
the formal envelope's `UTF-8 bytes + 256` input bound before observation, retry, or
network I/O. On 2026-07-18 the user independently authorized all project model billing.
The corrected official-contract mechanical maximum is `$1,167,905.640000`; this is not
a local proxy pre-consumption hard gate. This paragraph records the authorization state
before attempt-003: at that time attempts 001 and 002 were terminal `INCOMPLETE`, and the
next permitted run was fresh attempt-003 with exact CLI confirmation
`--authorized-maximum-spend-microunits 1167905640000`. Attempt-003 has since become
terminal; the current next-step is fresh attempt-004 under the operational amendment above.
The historical corrected gate SHA-256 is
`9b50fd8a271a78705e728de8f8cbb24a09e08b24eb2db9122df6a943bdd958f6`; the gate itself
still grants no authorization and must not be reused for attempt-004.
