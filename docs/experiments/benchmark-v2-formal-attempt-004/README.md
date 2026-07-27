# Benchmark v2 formal artifact index

This directory publishes the aggregate, payload-free evidence for
`benchmark-v2-formal-20260717-attempt-004`:

- `receipt.json` — the COMPLETE collection receipt;
- `report.json` — the deterministic aggregate report;
- `report.md` — the short generated report; and
- `artifact-index.json` — hashes, runtime binding, storage, access, licensing,
  privacy exclusions, and replay instructions for the complete seven-file package.

The public repository does **not** contain the replay inputs. The generated benchmark
license identifier has no accompanying grant, and the repository does not record a
provider-output redistribution basis. Until both are resolved, `config.json`, sanitized
`observations.json`, `rows.jsonl.zst`, and `blobs.jsonl.zst` remain in the immutable
owner-controlled object named in `artifact-index.json`. This is an access-controlled
replay package, not a public-rescore claim.

Access is limited to the repository owner and explicitly approved auditors. Requests
must go through the repository owner; onward redistribution is prohibited until a later
version of the index records the applicable data license and provider-rights basis.
The owner-controlled copy has no automatic expiry and must be retained until an index
update records its migration and replacement hash. No remote-durability claim is made.

Never add private observations, journals/WAL, staging data, lane or unit state,
operator logs, PID/lock files, abort/audit records, or recovery telemetry to this
directory or to a release asset.

## Authorized replay

After authorized retrieval, verify every stored hash in `artifact-index.json`,
decompress the two JSONL files, then verify their raw hashes before running:

```bash
zstd -d -q -c rows.jsonl.zst > rows.jsonl
zstd -d -q -c blobs.jsonl.zst > blobs.jsonl

uv run fretsure-bench \
  --replay-config config.json \
  --replay-receipt receipt.json \
  --replay-rows rows.jsonl \
  --replay-blobs blobs.jsonl \
  --replay-observations observations.json \
  --output-dir replay-a

uv run fretsure-bench \
  --replay-config config.json \
  --replay-receipt receipt.json \
  --replay-rows rows.jsonl \
  --replay-blobs blobs.jsonl \
  --replay-observations observations.json \
  --output-dir replay-b

diff -rq replay-a/canonical replay-b/canonical
```

Omitting `--fast-reaggregate` is intentional: the authoritative mode is the default
FULL_RESCORE. Byte equality is asserted only for the runtime recorded in the index;
cross-platform byte equality is not claimed.

## Byte-exact artifacts removed, 2026-07-27

`report.json`, `artifact-index.json` and `receipt.json` were deleted. Every
version they were stamped against has since moved (`oracle@0.2.0` is now
`@0.4.0`, and the collection machinery they indexed was itself removed in the
2026-07-25 over-design cleanup), so nothing in the repository could verify
them. A hash chain no code can check is not evidence.

What they supported is kept: the measured results and the honest limits, in
`report.md`, `../../BENCHMARK_RESULTS.md` and `../../BENCHMARK_V2_ACCEPTANCE.md`.
The negative findings in particular — repair below its SESOI, critic at a
slight joint loss, search with unknown cost — are the reason this project does
not re-run those experiments, and they stay.
