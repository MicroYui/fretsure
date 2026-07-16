# Benchmark Results — Agent Ablation (Plan 4)

> **Current status (2026-07-16): the numerical tables in this document are a
> LEGACY / UNVERSIONED FIDELITY SNAPSHOT.** They were recorded on 2026-07-10–11
> before the fidelity checker was version-stamped, using the old note-onset
> harmony-Jaccard semantics. They are **not** results under the current
> `fidelity@0.2.0`. The historical code/document state is pinned by the
> consolidated baseline commit `bee8a1c`; a real-LLM benchmark v2 rerun is still
> required before publishing a current headline number.
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
- **Faithfulness:** the current `fidelity@0.2.0` uses exact-onset top-voice melody
  matching, bass-root evaluation at each exact chord onset using the lowest note
  sounding there (including a note sustained from an earlier onset), and
  chord-segment pitch-class Jaccard for harmony. Sustained notes count in every
  segment they cross; `Meta.duration_beats` bounds the final segment and preserves
  notated trailing rests. Melody/bass preservation used for candidate ranking remains
  exact-onset based.

The human-played gold set is still pending, so GREEN is model-relative evidence,
not a measured real-player false-accept guarantee. Human work does not block the
engineering roadmap, but it does block real-world false-accept claims, profile/tier
calibration, human-musicality claims, and any stronger promise that a real guitarist
can play every GREEN result.

## How to run the current benchmark shape

```bash
export ANTHROPIC_BASE_URL=http://localhost:4141 ANTHROPIC_AUTH_TOKEN=<token>
uv run fretsure-bench --seed 1 --items 16 --bars 2 --paired
uv run fretsure-bench --seed 2 --items 16 --bars 2 --paired
```

Same seed rebuilds the same procedurally novel corpus, which resists exact-item tab
memorization but is not an absolute proof against learned generator patterns or
contamination. The current LLM (`gpt-5.6-sol` via the
local proxy) is stochastic, so this reproduces the experiment shape, **not the exact
recorded counts**. More importantly, the command now scores with
`fidelity@0.2.0`; it cannot reproduce the legacy tables under the same scoring
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

## Current `fidelity@0.2.0` smoke evidence (not a benchmark baseline)

Two deterministic end-to-end smokes make the independent gates visible:

```bash
uv run fretsure-demo
uv run fretsure-arrange tests/fixtures/musicxml/supported_basic.musicxml \
  --n 1 --no-critic --trace-jsonl /tmp/fretsure-supported-basic.trace.jsonl
```

- `fretsure-demo` returns `oracle@0.2.0` GREEN and
  `fidelity@0.2.0` melody-F1 `1.00`, bass-root `1.00`, harmony `0.75`, gate PASS.
- The supported MusicXML fixture imports at source/effective tempo 96 bpm and
  returns the same oracle GREEN, but harmony is about `0.29`, so the fidelity gate
  is FAIL. This is expected: playability certification and source faithfulness are
  independent gates, and GREEN must never be reported as joint success by itself.

These are smoke examples, not corpus-level effect estimates. The current repository
collects 1248 tests: 1242 offline cases plus 6 proxy-backed integration cases; the
full local-proxy run passes all 1248. The final ruff, strict mypy, lock and
package/install smokes are recorded in
`docs/PROJECT_STATE.md` and the safe `.mxl` container plan; the preceding Oracle
0.2 trust-gate plan retains its own independently closed evidence.

## Legacy / unversioned fidelity snapshot (2026-07-10–11)

Every numerical ablation result below uses the historical `oracle@0.1.0` playability
stamp and unversioned note-onset harmony metric. Preserve it as a directional research
record; do not quote it as a current `oracle@0.2.0` / `fidelity@0.2.0` baseline or
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

## Legacy scorecard (provisional until the v2 rerun)

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
critic is honestly flagged rather than quietly promoted. These component verdicts
must be refreshed, not silently carried forward, when benchmark v2 is run under
`fidelity@0.2.0`.

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
  note-onset harmony Jaccard. Current `fidelity@0.2.0` changes harmony to active
  chord segments and makes held notes visible across segment/chord boundaries;
  therefore the old and new joint counts are not score-compatible. Grid/DTW
  tolerance for real human-timed corpora remains a later refinement.
- There is no current real-LLM benchmark baseline under `fidelity@0.2.0` yet.
  Benchmark v2 must retain paired per-item rows, rerun the baselines/ablations,
  and use McNemar for binary paired outcomes plus a justified paired test for
  continuous critic/taste scores.

## Also implemented in this harness (not headline empirical results)

- **Checker vs. LLM-judge** (`bench/checker_vs_judge.py`): comparison and McNemar
  statistic machinery are implemented and unit-tested on a two-item constructed
  fixture. The planned human-labeled, repeated real-LLM experiment has not yet been
  run, so no empirical superiority claim is made from that fixture.
- **Baselines** (`bench/baselines.py`): raw-LLM-unverified and pure-solver arms are
  implemented; a full published baseline table on the frozen real/hard corpus is pending.
- **pass@k / pass^k** estimators + Wilson primitives (`bench/reliability.py`) are
  unit-tested but not currently emitted by `fretsure-bench`'s JSON report.
