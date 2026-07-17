# Benchmark v2 preregistered budget

Date: 2026-07-17<br>
Status: frozen before proxy outcomes; not collection authorization

The independent primary unit is one procedural family. Ten proposal slots are nested
repeated observations, not 5,000 independent families. Unknown provider usage and price
remain unavailable rather than zero.

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
- Timeout-derived provider envelope: `5,062,695,000` ms.
- Recorded provider-call elapsed ceiling: `5,184,000` seconds.
  This sums durable call-result elapsed time; it is not host wall time and
  excludes local solver, serialization, replay, and report CPU time.

## Lossless public compact proposal contract

The version `arrangement-proposal-compact@0.1.0` uses `128 + 32 × source events`,
with a 16,384-token cap. It changes the wire representation, not the normalized
notegraph: no event is truncated, and every work remains one inferential family.
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

## Runner alignment note

The frozen command shapes use `--prereg` for stub collection and `--pre-call-config`
for live collection. Runner work must implement or deliberately map those exact
flags before either gate is claimed.
