# Benchmark v2 Task 8 — operational pilot software readiness

> **Status (2026-07-18): SOFTWARE READY; REAL PILOT NOT RUN.** The offline pilot,
> pricing, budget, resume, and authorization boundaries are implemented. No provider,
> proxy, or network collection has started because no verified pricing contract or
> user-approved spend ceiling has been supplied.

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
and requires the billing model and output ceiling to match the frozen pilot. Creating
that declaration is not authorization: a live invocation must separately provide the
same exact maximum-spend microunit value. Missing or drifted confirmation fails before
client construction or output creation. No signature service, price discovery, Git
query, or runtime subprocess is involved.

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

## Offline evidence

- The directed Task 8 suites passed 31 tests: 13 pilot tests and 18 pricing/budget
  tests. Ruff and strict mypy passed both scripts.
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
- Task 8 changes only source-distribution scripts, tests, CI YAML, JSON, and Markdown.
  The frozen 0.6.0 runtime package, lockfile, and formal runner remain unchanged. There is no
  frontend or visual-design change.

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

After a verified pricing contract exists, build a priced declaration only from explicit
digests; this command does not inspect Git or authorize collection:

```bash
uv run python scripts/task8_pilot.py \
  --write-pre-call /secure/path/pilot-pre-call.json \
  --spec docs/experiments/2026-07-18-benchmark-v2-pilot-spec.json \
  --pricing-contract /secure/path/pricing-contract.json \
  --collection-attempt 1 \
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
  --output-dir /secure/path/pilot-attempt-001
```

The completed live receipt and root operational summary then feed the non-authorizing
formal gate:

```bash
uv run python scripts/task8_budget_gate.py \
  --prereg docs/experiments/2026-07-17-benchmark-v2-prereg.json \
  --pricing-contract /secure/path/pricing-contract.json \
  --expected-pricing-sha256 <pricing-contract-sha256> \
  --pilot-summary /secure/path/pilot-attempt-001/operational-summary.json \
  --pilot-receipt /secure/path/pilot-attempt-001/canonical/receipt.json \
  --output /secure/path/formal-budget-gate.json
```

## Remaining external gate

Before any real pilot call, supply a verifiable canonical pricing contract and explicitly
approve its mechanically computed pilot maximum spend. After the live pilot, the formal
budget gate will bind its receipt, operational summary, pricing contract, and unchanged
formal preregistration. Task 9 still requires a separate explicit authorization of the
computed formal spend and call budget. Until those inputs exist, the correct next state
is paused at the external price/authorization gate.
