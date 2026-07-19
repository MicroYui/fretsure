# Benchmark v2 preregistered budget

Date: 2026-07-18<br>
Status: operational amendment before a fresh collection attempt; estimands and schedule unchanged

The independent primary unit is one procedural family. Ten proposal slots are nested
repeated observations, not 5,000 independent families. Unknown provider usage and price
remain unavailable rather than zero.

## Operational collection amendment

- At most `4` scheduled units may be in flight; admission and canonical merge
  remain ordered by the frozen collection schedule.
- Each formal proxy attempt has a hard `300`-second whole-attempt deadline;
  pool/connect/TLS/write/read operations are capped by the remaining wall time.
  Connect inactivity also retains its separate `5`-second cap.
- Each logical call still permits three attempts with `0.5` / `1.0` second
  retry backoffs.
- Each attempt also reserves `10` seconds of recorded elapsed overhead for
  durable attempt-intent/result fsync and timeout-delivery scheduling.
- The durable recorded provider-call elapsed ceiling is `51,840,000` seconds,
  which covers the `51,539,895`-second timeout/overhead full-run envelope.
- Model, prompts, corpus, schedule, statistical decisions, output-token limits,
  and pricing contracts are unchanged by this operational amendment.

## Frozen collection shape

- Primary: 500 procedural families; secondary: 3 licensed public works.
- Ten agent candidates and ten raw-baseline calls per item, temperature `0.8`.
- Maximum eight repair calls and one conditional critic call per agent candidate.
- One deterministic pure-solver row per item makes no provider call.

## Primary procedural maximum

| Stage | Logical calls | Requested output tokens |
|---|---:|---:|
| proposal | 5,000 | 24,194,560 |
| repair | 40,000 | 40,960,000 |
| critic | 5,000 | 2,560,000 |
| raw | 5,000 | 24,194,560 |
| **Total** | **55,000** | **91,909,120** |

- Provider attempts: `165,000`.
- Attempt-reserved output tokens: `275,727,360`.
- Bounded response text: `2,941,091,840` bytes.
- Raw transport envelope: `173,015,040,000` bytes.

## Full 503-item maximum

| Stage | Logical calls | Requested output tokens |
|---|---:|---:|
| proposal | 5,030 | 24,561,920 |
| repair | 40,240 | 41,205,760 |
| critic | 5,030 | 2,575,360 |
| raw | 5,030 | 24,561,920 |
| **Total** | **55,330** | **92,904,960** |

- Maximum attempts: `165,990`.
- Attempt-reserved output tokens: `278,714,880`.
- Bounded response text: `2,972,958,720` bytes.
- Raw transport envelope: `174,053,130,240` bytes.
- Timeout-derived provider envelope: `49,879,995,000` ms.
- Timeout plus recorded attempt-overhead reservation: `51,539,895,000` ms.
- Recorded provider-call elapsed ceiling: `51,840,000` seconds.
  This sums durable call-result elapsed time; it is not host wall time and
  excludes local solver, serialization, replay, and report CPU time.

## Lossless public compact proposal contract

The version `arrangement-proposal-compact@0.1.0` uses `128 + 32 × source events`,
with a 16,384-token cap. It changes the wire representation, not the normalized
notegraph: no event is truncated, and every work remains one inferential family.
This is a visible-output request/format cap, not a billable `output_tokens` ceiling.
Provider-reported output usage includes non-visible generated tokens; the corrected
formal cost contract therefore uses the official `gpt-5.6-sol` maximum of 128,000
billable output tokens per attempt.
Long-score solving admits at most `4` bounded searches:
`12,000,000` estimated work units per admitted segment and
`48,000,000` across admitted segment searches.
Rejected oversized preflights and the final full-history oracle are control
work outside that estimate; they do not authorize another segment search.

| Public item | Events | Proposal/raw tokens |
|---|---:|---:|
| `public-classical-beethoven-op48-5` | 198 | 6,464 |
| `public-midi-bwv775` | 443 | 14,304 |
| `public-midi-bwv774` | 495 | 15,968 |

## Scheduled-unit reservation and matched controls

Before starting the next preregistered schedule unit, reserve its exact arm
envelope. The maximum single-unit envelope is the agent arm:
`10` logical calls,
`30` attempts and `24,672` requested output tokens,
plus `789,504` response-text bytes and
`31,457,280` transport bytes.
An agent+raw pair has a separate 11-call/33-attempt maximum envelope; it is
not the ArtifactStore atomic unit. Summed schedule-unit envelopes equal the
full preregistered totals.

Matched no-repair/raw prefix counts across all 503 items:

- `m=1`: 57 items
- `m=2`: 167 items
- `m=3`: 148 items
- `m=4`: 56 items
- `m=5`: 75 items

## External cost gate

`cost_contract_unavailable`: maximum spend is null until a verifiable price contract
and explicit user authorization exist. This preregistration authorizes no provider
call. Later pilot and formal configs may lower these ceilings but may not raise
them without a new preregistration.
All ceilings apply to one numbered collection attempt and are non-transferable.
After an orphan, a higher attempt needs a fresh pre-call config and cost
authorization that accounts for prior consumed spend; partial outcomes cannot
be inspected to choose whether to restart.

### Billing-contract and terminal-attempt boundary

The historical 16,384-output contract and attempt-001 gate remain immutable
audit evidence. Pricing contract v2 and formal envelope v0.2 retain the official
128,000-token billable-output maximum and the unchanged one-attempt formal
mechanical maximum of `1,167,905,640,000` micro-USD
(`$1,167,905.640000`). Attempts 001, 002, and 003 are terminal `INCOMPLETE`
and must not be resumed or overwritten. Their cumulative known/tight cost is
`$2.130022 / $804.234022`; adding one complete future formal attempt gives a
cumulative audit maximum of `$1,168,709.874022`. A fresh attempt-004 requires
a new pre-call and formal budget gate that bind this operational
preregistration's raw SHA-256.

## Excluded throughput pilot and ETA reporting

Before formal collection, an analysis-excluded throughput pilot advances through
`2 → 4 → 8` in-flight units. Each step records success rate, retry rate,
provider-latency P50/P95, and completed-unit/call throughput. One eight-unit block
per level is smoke evidence that may reject but never approve eight-way execution.
Only complete live blocks with identical execution, analysis, lock, pricing, model,
timeout, and corpus bindings may enter the comparison; stub blocks never qualify.
Freezing `8` requires at least eight complete blocks (64 units) at both `4` and `8`,
an independent confirmation, 8/4 unit throughput at least 1.35, call throughput
at least 1.25, success degradation at most two percentage points, bounded retry and
P50/P95 degradation, and no new timeout or integrity failure. Missing or boundary
evidence freezes `4`. Selecting `8` requires regenerating and rebinding this
operational preregistration before attempt-004.

Before the formal call gate opens, the pilot's observed rates must produce
optimistic, median, and conservative completion-time estimates. During collection,
the estimate is updated from actual durable completions after 30 minutes, near each
additional 5% of scheduled units, and whenever throughput changes materially.
Canonical `benchmark-progress@0.1.0` JSON lines are appended to the operator log;
they report the durable row count out of 10,563, current-process and recent
throughput, stalled state, and all three ETA estimates without entering analysis
artifacts.

## Durable checkpoint and interruption boundary

Each completed scheduled unit is durably checkpointed with its lane WAL before it
becomes resumable. A graceful interruption stops new admissions, drains already
started units, persists their completed-unit checkpoints, and resumes later from the
next schedule unit after the verified durable prefix. Resume never changes frozen
schedule order or treats network completion order as semantic.
The formal invocation runs detached from the interactive Codex session, so a UI or
client connection loss does not terminate collection. The detached process writes
its PID and append-only operator log beside the attempt artifacts.
The wrapper directly `exec`s the repository's `.venv/bin/fretsure-bench` entry
point; it does not interpose `uv run`, whose supervisor PID does not reliably
forward `SIGINT` on this host.
Formal and pilot proxy URLs use numeric loopback (`127.0.0.1` or `::1`), not
`localhost`, so name resolution cannot sit outside the attempt deadline.

This contract does not promise recovery of a provider request interrupted halfway.
An admitted unit without a verified durable completion, or a WAL with an
open attempt, fails closed for operator audit instead of guessing whether the
provider completed it.
A terminal concurrent abort writes a canonical sidecar that binds the coordinator
and every admitted lane WAL; the abort receipt reason embeds that sidecar's raw
SHA-256 so usage from out-of-order or incomplete lanes remains auditable.

## Runner alignment note

Formal live collection must derive its 300-second request timeout and frozen
admission limit from the preregistration embedded in the pre-call declaration.
The declaration and gate must be regenerated after the implementation commit;
neither artifact generation nor this preregistration grants billing authorization.
Formal hot paths must remain linear in units and provider observations; the pilot
must not run against a prefix-dependent implementation whose later units get slower.
