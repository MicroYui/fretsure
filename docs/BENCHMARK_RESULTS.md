# Benchmark Results — benchmark v2 and legacy agent ablations

> **Current status (2026-07-23): benchmark v2 is complete.** The current-model
> result uses `gpt-5.6-sol`, `oracle@0.2.0`, `tab-input@0.2.0`, and
> `fidelity@0.3.0`; its software/statistical result is reported first below. The
> 2026-07-10–11 tables are preserved afterward as a **LEGACY / UNVERSIONED
> FIDELITY SNAPSHOT**. They used the old note-onset harmony-Jaccard semantics and
> must not be pooled with or relabeled as v2 evidence. Their historical state is
> pinned by consolidated baseline commit `bee8a1c`.
>
> The active proxy default moved to canonical `gpt-5.6-sol` on 2026-07-16.
> Current trace and aggregate benchmark JSON stamp `llm_model_id`; the legacy
> tables below remain attributed to `claude-opus-4-8` and were not rewritten.

Machine-scored, recorded numbers for the Fretsure arrangement agent. The
scorer is the deterministic oracle + faithfulness gate — **not** an LLM judge.
Every capability must face an ablation; stochastic selection effects use shared
proposal pools and paired comparisons where implemented. Components that *don't*
earn their cost are reported here too.

Two gates must not be conflated:

- **Playability:** GREEN means passing `oracle@0.2.0` under the stamped simplified
  model/profile, after `tab-input@0.2.0` validation; every result also carries the
  profile version and canonical fingerprint. It does not imply fidelity to the source.
- **Faithfulness:** the current `fidelity@0.3.0` uses exact-onset top-voice melody
  matching, bass-root evaluation at each exact chord onset using the lowest note
  sounding there (including a note sustained from an earlier onset), and
  chord-segment pitch-class Jaccard for harmony. Sustained notes count in every
  segment they cross; `Meta.duration_beats` bounds the final segment and preserves
  notated trailing rests. Its public gate also records nullable scores plus an exact
  evaluated/unavailable partition, so absent source evidence is N/A rather than 1.0.
  Melody/bass preservation used for candidate ranking remains exact-onset based.

The human-played gold set is still pending, so GREEN is model-relative evidence,
not a measured real-player false-accept guarantee. Human work does not block the
engineering roadmap, but it does block real-world false-accept claims, profile/tier
calibration, human-musicality claims, and any stronger promise that a real guitarist
can play every GREEN result.

## Benchmark v2 — current-model result

The formal run is `benchmark-v2-formal-20260717-attempt-004`. Requested and returned
model IDs were both exactly `gpt-5.6-sol`. The confirmatory profile was
`median@0.1`; package/runtime bindings were Fretsure `0.6.0`, Python `3.11.15`,
Darwin/arm64. Collection binds execution commit
`773c69deca4d2b00cdcdc5a33841369cb3016955`; deterministic analysis binds
`495ac3870a79ef394323f59cd664d551f1696ae58b52184f8e9fc351ec495281`.

The primary population is 500 independent procedural families/clusters, balanced
over a 3×3 `synthetic_complexity × polyphony` design and carrying complete
melody+bass+harmony evidence. Ten proposals are nested within each family; they do
not make inferential N equal 5,000. Secondary evidence comprises one public-classical
and two public-MIDI controls with melody+harmony evidence. These three controls are
reported separately and are not a representative real-music sample.

### Controlled-full headline and the important negative strata

The frozen `full` policy is repaired best-of-4 with critic enabled. It achieved
74/500 joint successes, `0.148`, with Wilson 95% CI
`[0.119561, 0.181806]` and exact Clopper–Pearson 95% CI
`[0.118033, 0.182200]`.

| procedural complexity / polyphony | full joint success |
|---|---:|
| high / high | 0/55 |
| high / low | 0/55 |
| high / medium | 0/55 |
| low / high | 16/56 |
| low / low | 20/56 |
| low / medium | 27/56 |
| medium / high | 0/55 |
| medium / low | 4/56 |
| medium / medium | 7/56 |

All high-complexity cells and the medium/high cell were zero. The single public
classical control was 0/1 and the two public MIDI controls were 0/2 under the same
selected-policy view; at candidate level, terminal GREEN/joint was 0/10 and 0/20
respectively. This is a material negative generalization result. The 14.8% procedural
rate is not evidence that the system handles arbitrary real pieces.

### Preregistered component decisions

| capability | paired evidence | preregistered decision |
|---|---|---|
| Repair | Δ joint `+0.0566`; family bootstrap 95% `[0.0456, 0.068205]`; 283 improved / 0 worsened nested candidate pairs; Holm-adjusted `p=9.9999e-6` | `NOT_KEPT`: positive, but below the `0.10` SESOI; the matched no-repair guard was only `+0.02531` |
| Best-of-4 search | best-of-1 40/500 → best-of-4 74/500; Δ `+0.068`, 95% `[0.048, 0.088]`; McNemar 34 improved / 0 worsened, raw `p=5.82077e-11`, Holm `p=1.16415e-10`; matched OR `∞`, exact 95% lower `8.72593` | `PROBATION_COST_UNKNOWN`: the efficacy gate passes, but provider token/cost fields are incomplete, so deployment Pareto status is unavailable |
| Critic | without/with critic 75/500 → 74/500; joint Δ `-0.002`, 95% `[-0.006, 0]`; self-score Δ `+0.00272`, 95% `[0.0015, 0.00416]` | `HUMAN_BLOCKED_PROBATION`: the self-score direction is structural, joint is slightly negative, and no blind human evidence exists |

The matched controls are guards, not extra component verdicts. The full arm's margin
over matched no-repair was `+0.02531`, 95% `[0.01276, 0.03870]`, below its `0.05`
SESOI. Its margin over matched raw LLM was `+0.0726`, 95%
`[0.0594, 0.0862]`, Holm-adjusted `p=1.99998e-5`, so that raw-baseline guard passed.
Critic's failure-inclusive fidelity side effects were melody `+0.000167`, bass-root
`-0.0005`, and harmony `-0.001167` (95% `[-0.002167, -0.000333]`). These machine
effects are not musical-taste evidence.

The release consequence follows those decisions: product entry points now default to
`n=1`, `max_iters=0`, and `use_critic=false`. Best-of-N search, verifier-guided repair,
and critic scoring remain explicit research/compatibility opt-ins. The frozen formal
`full` arm above remains the benchmark estimand; changing product defaults does not
rewrite that run or its execution SHA.

Search breadth improved the selected joint rate monotonically on this frozen shared
pool, but the real deployment cost dimension remains unknown:

| search k | selected joint | Wilson 95% CI |
|---:|---:|---:|
| 1 | 40/500 = 0.080 | [0.05930, 0.10711] |
| 2 | 52/500 = 0.104 | [0.08019, 0.13384] |
| 4 | 74/500 = 0.148 | [0.11956, 0.18181] |
| 8 | 105/500 = 0.210 | [0.17658, 0.24784] |

This table is a selected-policy curve, not `pass^8`. The canonical report retains the
complete terminal GREEN and joint `pass@k`/`pass^k` curves for every k=1..10; no
favorable k is promoted as a generic reliability number.

### Candidate availability and profile sensitivity

Across the 500 procedural families, the 5,000 nested proposal outcomes were:

| arm | GREEN | joint | LLM-only success |
|---|---:|---:|---:|
| initial | 108/5,000 | 80/5,000 | 24/5,000 |
| terminal repaired | 445/5,000 | 363/5,000 | 306/5,000 |
| raw LLM | 0/5,000 | 0/5,000 | 0/5,000 |
| pure solver | 69/500 | 44/500 | N/A |

The terminal fidelity view had 1,419 scored and 3,581 failed/unscored outcomes; missing
or invalid outcomes remain failures in structurally applicable ITT denominators. They
are not dropped as complete cases.

Deterministic profile rechecks below are model sensitivity only, not human difficulty
calibration. The candidate denominator is 5,030 terminal slots: 5,000 procedural plus
30 public.

| profile | GREEN | AMBER | RED | unavailable |
|---|---:|---:|---:|---:|
| `small@0.1` | 112 | 770 | 537 | 3,611 |
| `median@0.1` | 445 | 974 | 0 | 3,611 |
| `large@0.1` | 1,150 | 269 | 0 | 3,611 |

### Corpus exclusions, usage, and unavailable comparisons

The run produced 10,563 canonical rows and 15,887 blobs from 503 items. The corpus
domain SHA-256 is
`b4e2a1ed05eb07d82bdea18b9105cdd92b564cf864d8acedaa3c37d820848e8b`.
No license/evidence-compatible public lead-sheet entered the corpus. Mutopia BWV 773,
Bizet Op. 21 No. 10, and Abbott's *Just for Today* were excluded for explicit frozen-
normalizer contract violations. Checker-only tab fixtures remained software tests.
Optional baselines B3/B4 are unavailable with reason
`LICENSE_AUDITED_REPRODUCIBLE_ADAPTER_ABSENT`; no comparison was fabricated.

Collection recorded 45,215 logical calls and 45,700 provider attempts, including 485
extra attempts; requested output was 84,894,720 tokens and attempt-reserved output was
87,030,688 tokens. Summed recorded elapsed time was 1,108,043,068,140 µs. Complete
provider-token totals and all input/output/cache token components are `null` because
some attempts lack complete usage. Actual token cost, dollar cost, and deployment
Pareto non-dominance are therefore unavailable—not zero. The orphan-recovery supplement
is disclosed separately as `$0.219054` known / `$28.363054` tight with four
usage-unknown attempts; it is not benchmark data.

### Replay receipt and artifact access

Two fresh default FULL_RESCORE replays produced byte-identical seven-file canonical
directories and report digest
`79d1927b5100bd80db2f47b056ad5a7887739460869020c7b53a65d0d19bb3f8`.
The claim is restricted to the manifest-bound Fretsure 0.6.0, Python 3.11.15,
Darwin/arm64 runtime; no cross-platform byte-equality claim is made.

The public aggregate report, COMPLETE receipt, every raw/domain hash, exact replay
commands, access policy, and compression receipt are in
[`benchmark-v2-formal-attempt-004`](experiments/benchmark-v2-formal-attempt-004/README.md).
The full replay package remains owner-controlled because the repository lacks a data
license grant and a recorded provider-output redistribution basis. Consequently this
repository makes an integrity and authorized-replay claim, **not** a public-rescore
claim.

## Legacy Plan 4 command shape (historical)

```bash
export ANTHROPIC_BASE_URL=http://localhost:4141 ANTHROPIC_AUTH_TOKEN=<token>
uv run fretsure-bench --seed 1 --items 16 --bars 2 --paired
uv run fretsure-bench --seed 2 --items 16 --bars 2 --paired
```

These commands describe the historical Plan 4 shape; the v2 CLI/artifact contract
requires an explicit output directory and preregistration/replay inputs. Same seed
rebuilds the same procedurally novel corpus, which resists exact-item tab
memorization but is not an absolute proof against learned generator patterns or
contamination. The current LLM (`gpt-5.6-sol` via the
local proxy) is stochastic, so this reproduces the experiment shape, **not the exact
recorded counts**. More importantly, the command now scores with
`fidelity@0.3.0`; it cannot reproduce the legacy tables under the same scoring
semantics. The current CLI stamps the model id and both checker versions in its
aggregate JSON, but
does not calculate the Wilson intervals shown below or persist per-item raw rows.
Its public controls fail closed before corpus construction or LLM-factory calls:
`seed` is an exact signed 63-bit integer, `items` is 1..1000, `bars` is 1..64,
`items * bars` is at most 4096, and `paired` is an exact bool.

The legacy tables used `claude-opus-4-8` via the local proxy and accumulated in
commits `fb5b56a` (first seed), `337ed23`
(second seed), `a866aa0` (paired best-of-N), and `c587f75` (paired critic), then
were consolidated at `bee8a1c`. The exact execution commit and raw per-item rows
were not stamped into each run artifact, which remains a reproducibility gap.

## Current `fidelity@0.3.0` smoke evidence (not a benchmark baseline)

Two deterministic end-to-end smokes make the independent gates visible:

```bash
uv run fretsure-demo
uv run fretsure-arrange tests/fixtures/musicxml/supported_basic.musicxml \
  --n 1 --no-critic --trace-jsonl /tmp/fretsure-supported-basic.trace.jsonl
```

- `fretsure-demo` returns `oracle@0.2.0` GREEN and
  `fidelity@0.3.0` melody-F1 `1.00`, bass-root `1.00`, harmony `0.75`, gate PASS
  with all 3 dimensions evaluated.
- The supported MusicXML fixture imports at source/effective tempo 96 bpm and
  returns the same oracle GREEN, but harmony is about `0.29`, so the fidelity gate
  is FAIL. This is expected: playability certification and source faithfulness are
  independent gates, and GREEN must never be reported as joint success by itself.

These are smoke examples, not corpus-level effect estimates. At the strict MIDI
closure the repository collected 1849 tests: 1841 offline cases plus 8 proxy-backed
integration cases; both partitions and the full 1849-case run passed. The final Ruff,
strict mypy, lock and package/install smokes are recorded in `docs/MIDI_ACCEPTANCE.md`;
the preceding Oracle 0.2 trust-gate plan retains its own independently closed evidence.

## Legacy / unversioned fidelity snapshot (2026-07-10–11)

Every numerical ablation result below uses the historical `oracle@0.1.0` playability
stamp and unversioned note-onset harmony metric. Preserve it as a directional research
record; do not quote it as a current `oracle@0.2.0` / `fidelity@0.3.0` baseline or
compare a new v2 run to it as though the scoring definitions were unchanged.

## Headline — a large, repeatable repair effect (paired causal test still pending)

Leave-one-out over the full agent, `n=16` procedural lead sheets, seed 1,
median hand profile. In this legacy snapshot, `joint_success` = tab is GREEN under
the stamped model/profile **and** passes the then-unversioned faithfulness gate
(melody-F1 ≥ 0.9, bass-root ≥ 0.7, note-onset harmony ≥ 0.6).

| arm | joint_success | green_rate | mean melody-F1 | mean edit steps | Wilson 95% (green) |
|---|---|---|---|---|---|
| **full** | **0.81** | 0.81 | 1.00 | 3.31 | [0.57, 0.93] |
| **− repair** | 0.31 | 0.31 | 0.56 | 0.00 | [0.14, 0.56] |
| − critic | 0.81 | 0.81 | 1.00 | 2.63 | [0.57, 0.93] |
| − best-of-N | 0.94 | 0.94 | 1.00 | 0.00 | [0.72, 0.99] |

**Within the legacy snapshot, repair is the strongest load-bearing candidate.**
Removing it drops success from 0.81
to 0.31 and melody-F1 from 1.00 to 0.56. The two Wilson intervals do **not**
overlap (full lower bound 0.57 > −repair upper bound 0.56), showing a large
descriptive separation in this run. Because the stochastic leave-one-out arms are
not paired, this is not a formal paired significance test. Mechanistically, it is the
verifier-guided repair loop doing exactly its job: the raw LLM proposal is often
AMBER/RED or drops notes; the oracle's localized diagnostics steer edits back to
a GREEN, melody-faithful tab.

## Robustness — a second seed, and pooled intervals

Seed 2 (`--seed 2 --items 16`), same pipeline:

| arm | joint_success | green_rate | mean melody-F1 | Wilson 95% (green) |
|---|---|---|---|---|
| **full** | 0.94 | 1.00 | 1.00 | [0.81, 1.00] |
| **− repair** | 0.31 | 0.44 | 0.87 | [0.23, 0.67] |
| − critic | 0.88 | 1.00 | 1.00 | [0.81, 1.00] |
| − best-of-N | 0.56 | 0.56 | 0.81 | [0.33, 0.77] |

Pooling both seeds (n = 32) on `joint_success`:

| arm | joint_success (28/32 etc.) | Wilson 95% |
|---|---|---|
| **full** | **0.875** | [0.72, 0.95] |
| **− repair** | 0.312 | [0.18, 0.49] |
| − critic | 0.844 | [0.68, 0.93] |
| − best-of-N | 0.750 | [0.58, 0.87] |

**The repair association remains large when pooled** — its interval [0.18, 0.49] sits entirely
below full's [0.72, 0.95] over 32 items. Critic's marginal interval overlaps `full`,
so there is no clear descriptive separation here; overlap is not a formal difference
test. Best-of-N's *unpaired* arm is the sharpest illustration of
the unpaired-sampling caveat: it looked *negative* on seed 1 (−best-of-N 0.94 >
full 0.81) and *strongly positive* on seed 2 (full 0.94 > −best-of-N 0.56). An
effect whose **sign flips between seeds** is dominated by which independent draws
each arm happened to get — it must be measured with a paired ablation. That is
exactly the next section.

## Paired best-of-N — resolving the confound

`fretsure-bench --paired` (and `bench.paired_best_of_n`) builds **one** proposal
pool of N per item, then scores best-of-1 (the greedy temp-0 draw) vs best-of-N on
that *same* pool — so the only thing that varies is selection breadth, not the LLM
draws. Real LLM, N=2, both seeds:

| seed | best-of-1 green | best-of-N green | Δ green | best-of-1 joint | best-of-N joint | Δ joint |
|---|---|---|---|---|---|---|
| 1 | 0.69 | 0.81 | **+0.125** | 0.56 | 0.75 | +0.19 |
| 2 | 0.75 | 0.88 | **+0.125** | 0.75 | 0.88 | +0.125 |
| pooled (n=32) | 0.719 | 0.844 | **+0.125** | 0.656 | 0.812 | +0.156 |

In these paired selections, best-of-N recorded +0.125 GREEN on both seeds and
+0.13–0.19 joint. The positive **joint** deltas make a provisional modest keep
reasonable; the sign-flipping unpaired arm was not a usable estimate of the selection
effect. A formal paired test still needs per-item discordant binary outcomes. The
current aggregate result object does **not** retain those pairs, so adding McNemar
requires a runner/schema change rather than merely post-processing current counts.

Note one delta is *structural*, not empirical: because `is_green` is `_rank`'s top
key and best-of-N selects over a superset that includes the greedy draw,
**Δ green ≥ 0 always** (locked by `test_green_delta_is_never_negative_by_construction`).
The joint delta is the informative one, since `_rank` optimizes green/melody/bass —
not exactly the harmony-inclusive joint gate — so best-of-N can, in principle, trade
a hair of harmony for greenness.

## Paired critic — judged on its actual job (taste)

`bench.paired_critic` builds one pool per item (critic scored), then selects
best-of-N **with** vs **without** the critic term on that same pool. Crucially, the
critic is inspected on the metric it directly optimizes — musical **taste** (its own
0–1 score of the selection) — *not* the playability+faithfulness joint gate, which it
neither targets nor should. (An earlier draft scored it on `joint_delta`; the opus
review correctly flagged that as the wrong yardstick — `_rank` keys on `melody_recall`
while the gate keys on top-voice `melody_f1`, so the critic can even *hurt* joint by
construction.) Real LLM, N=2, both seeds:

| seed | taste off → on | Δ taste | Δ joint (side effect) | Δ green |
|---|---|---|---|---|
| 1 | 0.276 → 0.284 | +0.009 | −0.063 | 0 |
| 2 | 0.377 → 0.391 | +0.014 | 0.000 | 0 |
| pooled | 0.326 → 0.338 | **+0.012** | −0.031 | 0 |

**Verdict: the critic has not earned its cost on this corpus.** On its own self-score it
lifts the selection by ≈ **+0.01** (about 1% of scale); on the joint gate it is
neutral-to-slightly-negative. On these easy 2-bar pieces the candidates rarely differ
enough for a tie-break-level signal (the critic sits at rank index 3, below
green/melody/bass) to matter. `Δ green = 0` is structural (critic ranks below green).
The non-negative self-score direction is fully structural because enabling the term
selects for that same critic score; only the recorded magnitude is empirical, and it
is **not external musicality evidence**. The
critic must justify itself on harder/taste-sensitive corpora and a human blind A/B,
or be cut.

## Legacy scorecard (superseded by benchmark v2)

- **Repair — strongest positive evidence.** Pooled 0.31 → 0.88 with non-overlapping
  marginal intervals; retain it, while still adding a same-proposal paired repair test.
- **Best-of-N — provisional modest keep**: GREEN increased by 0.125 in both recorded
  selections, but its non-negative direction is structural; the more informative
  joint deltas were positive and await per-item discordance/McNemar evidence.
- **Critic — has NOT earned it here.** Paired selection sensitivity on its own
  self-score is ≈ +0.01 and the joint side effect is ≤ 0; no human taste result exists.
  It stays in the agent for now
  *on probation*: it must show a real taste lift on harder/real corpora, or be cut.

Keeping this scorecard public — including the component that has *not* paid its way —
is the anti-LARP discipline the project runs on. Under the historical metric, repair
has a large descriptive association, best-of-N has a provisional paired gain, and
critic is honestly flagged rather than quietly promoted. Benchmark v2 refreshed these
verdicts above; the legacy decisions were not silently carried forward.

## A "who checks the corpus" finding

The first real-LLM run showed `joint_success = 0.0` while `green_rate = 1.0` and
`melody_f1 = 1.0` — a contradiction that turned out to be a **corpus-label bug,
not an agent or metric bug**. The generator labeled chords `KEY:degN` with a
*0-indexed* degree, so `C:deg5` actually meant the vi chord (A, `root_pc=9`) but
reads to any musician as "V = G". The LLM followed the misleading label and
placed a G bass; `bass_root_accuracy` scored against the correct `root_pc=9` and
marked every arrangement wrong — a permanent, silent zero. Neither the LLM nor
the metric was wrong; the label was. Fixed by emitting real chord names
(`Am`, `Dm`, …) consistent with `root_pc`; `bass_root` then went 0.00 → 1.00 and
the gate began passing. Regression tests now assert symbol-root == `root_pc`.

This is the same discipline the oracle work applies to itself ("who checks the
checker") turned on the benchmark corpus.

## Limitations (do not over-read these numbers)

- **n = 16 per seed, 2 seeds (n = 32 pooled)**. These are effect-size
  demonstrations, not leaderboard numbers; repair shows a large descriptive
  separation, but its formal same-proposal paired test is still pending.
- The procedural corpus is deliberately *easy* (2-bar diatonic lead sheets) so the
  data flow and ablation are unambiguous. Harder inputs (longer pieces, real MIDI,
  dense harmony) are needed to learn whether the critic changes anything; no positive
  effect is assumed in advance.
- The original **leave-one-out** arms are unpaired across the stochastic LLM; repair's
  large descriptive separation repeats across seeds, while small effects are confounded.
  The dedicated
  paired sections above now isolate both best-of-N and critic over shared proposal
  pools. Their sample is still small and per-item rows were not retained. Best-of-N's
  binary GREEN/joint outcomes need discordance + McNemar; critic's continuous taste
  scores need a paired permutation/bootstrap, Wilcoxon, or another justified paired
  continuous-outcome test (and ultimately human ratings).
- The recorded `joint_success` values used exact-onset matching and the old
  note-onset harmony Jaccard. Current `fidelity@0.3.0` includes the 0.2 chord-segment
  semantics (held notes remain visible across segment/chord boundaries) and adds
  explicit evidence availability; therefore the old and new joint counts are not
  score-compatible. Grid/DTW
  tolerance for real human-timed corpora remains a later refinement.
- Benchmark v2 now supplies the current `fidelity@0.3.0` real-model baseline, paired
  rows, family bootstrap/sign-flip repair inference, and exact McNemar search inference.
  It does not retroactively make the legacy numbers score-compatible.

## Also implemented in this harness (not headline empirical results)

- **Checker vs. LLM-judge** (`bench/checker_vs_judge.py`): comparison and McNemar
  statistic machinery are implemented and unit-tested on a two-item constructed
  fixture. The planned human-labeled, repeated real-LLM experiment has not yet been
  run, so no empirical superiority claim is made from that fixture.
- **Baselines** (`bench/baselines.py`): raw-LLM-unverified and pure-solver arms are
  included in the v2 canonical report; B3/B4 remain explicitly unavailable.
- **pass@k / pass^k** estimators + Wilson primitives (`bench/reliability.py`) are
  emitted for every valid k in the v2 canonical JSON report.
