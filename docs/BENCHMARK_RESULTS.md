# Benchmark Results — Agent Ablation (Plan 4)

Machine-scored, reproducible numbers for the Fretsure arrangement agent. The
scorer is the deterministic oracle + faithfulness gate — **not** an LLM judge.
Every capability earns its place by leave-one-out ablation; the components that
*don't* earn it are reported here too.

## How to reproduce

```bash
export ANTHROPIC_BASE_URL=http://localhost:4141 ANTHROPIC_AUTH_TOKEN=<token>
uv run fretsure-bench --seed 1 --items 16 --bars 2
```

Same seed rebuilds the same procedural, contamination-proof corpus (no tab for
these pieces ever existed to be memorized). The LLM (`claude-opus-4-8` via the
local proxy) is stochastic, so exact counts vary run to run; the effect sizes
below are stable and reported with 95% Wilson intervals.

## Headline — repair earns its existence

Leave-one-out over the full agent, `n=16` procedural lead sheets, seed 1,
median hand profile. `joint_success` = tab is GREEN (provably playable) **and**
passes the faithfulness gate (melody-F1 ≥ 0.9, bass-root ≥ 0.7, harmony ≥ 0.6).

| arm | joint_success | green_rate | mean melody-F1 | mean edit steps | Wilson 95% (green) |
|---|---|---|---|---|---|
| **full** | **0.81** | 0.81 | 1.00 | 3.31 | [0.57, 0.93] |
| **− repair** | 0.31 | 0.31 | 0.56 | 0.00 | [0.14, 0.56] |
| − critic | 0.81 | 0.81 | 1.00 | 2.63 | [0.57, 0.93] |
| − best-of-N | 0.94 | 0.94 | 1.00 | 0.00 | [0.72, 0.99] |

**Repair is the load-bearing capability.** Removing it drops success from 0.81
to 0.31 and melody-F1 from 1.00 to 0.56. The two Wilson intervals do **not**
overlap (full lower bound 0.57 > −repair upper bound 0.56), so at n=16 the
benefit is already statistically distinguishable, not noise. This is the
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

**Repair survives pooling decisively** — its interval [0.18, 0.49] sits entirely
below full's [0.72, 0.95] over 32 items. Critic's interval overlaps `full` (no
distinguishable effect). Best-of-N's *unpaired* arm is the sharpest illustration of
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

Measured paired, best-of-N is a **consistent, positive** gain: +0.125 GREEN on
*both* seeds and +0.13–0.19 joint. The sign no longer flips — so best-of-N *does*
earn a modest keep; the unpaired "no effect / negative" reading was a sampling
artifact, not the truth. Two independent seeds replicating the same +0.125 is
itself the evidence (a proper paired significance test is McNemar on the discordant
item pairs — a small future refinement; the code already exposes the per-arm
counts needed to add it).

Note one delta is *structural*, not empirical: because `is_green` is `_rank`'s top
key and best-of-N selects over a superset that includes the greedy draw,
**Δ green ≥ 0 always** (locked by `test_green_delta_is_never_negative_by_construction`).
The joint delta is the informative one, since `_rank` optimizes green/melody/bass —
not exactly the harmony-inclusive joint gate — so best-of-N can, in principle, trade
a hair of harmony for greenness.

## Paired critic — judged on its actual job (taste)

`bench.paired_critic` builds one pool per item (critic scored), then selects
best-of-N **with** vs **without** the critic term on that same pool. Crucially, the
critic is judged on the metric it actually optimizes — musical **taste** (its own
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

**Verdict: the critic barely earns anything on this corpus.** Judged on taste it
lifts the selection by ≈ **+0.01** (about 1% of scale); on the joint gate it is
neutral-to-slightly-negative. On these easy 2-bar pieces the candidates rarely differ
enough for a tie-break-level signal (the critic sits at rank index 3, below
green/melody/bass) to matter. `Δ green = 0` is structural (critic ranks below green).
So the critic has **not** paid its way here — it must justify itself on harder /
taste-sensitive corpora (where candidates diverge and taste is the point), or be cut.

## The honest scorecard (what each component earns)

- **Repair — earns it decisively.** Pooled 0.31 → 0.88; non-overlapping intervals.
- **Best-of-N — earns a modest keep**, once measured paired: +0.125 GREEN on both
  seeds. The unpaired arm's sign-flip was the confound, not the verdict.
- **Critic — has NOT earned it here.** Measured paired on its own objective, taste
  lift is ≈ +0.01 and the joint side effect is ≤ 0. It stays in the agent for now
  *on probation*: it must show a real taste lift on harder/real corpora, or be cut.

Keeping this scorecard public — including the component that has *not* paid its way —
is the anti-LARP discipline the project runs on. Two of three capabilities earn their
keep on the procedural corpus; the third is honestly flagged, not quietly retained.

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
  demonstrations, not leaderboard numbers; only the *large* effect (repair)
  clears the noise floor at this scale.
- The procedural corpus is deliberately *easy* (2-bar diatonic lead sheets) so the
  data flow and ablation are unambiguous. Harder inputs (longer pieces, real MIDI,
  dense harmony) are the D-layer corpus work and are expected to move critic off
  zero too.
- The **leave-one-out** arms are unpaired across the stochastic LLM; large effects
  (repair) survive this, small ones (best-of-N) are confounded by it — which is why
  best-of-N is measured with the paired ablation above. Critic still lacks a paired
  measurement; giving it one is the obvious next step.
- `joint_success` uses exact-onset matching for melody/bass; grid/DTW tolerance
  for real (human-timed) corpora is a later refinement (see `metrics/fidelity.py`).

## Also validated by this harness

- **Checker vs. LLM-judge** (`bench/checker_vs_judge.py`): the LLM judge
  false-accepts unplayable tabs the oracle rejects; McNemar confirms the oracle
  is the sounder gate. This is *why* the benchmark scores with the checker.
- **Baselines** (`bench/baselines.py`): raw-LLM-unverified and pure-solver arms
  bracket the agent.
- **pass@k / pass^k** unbiased estimators + Wilson (`bench/reliability.py`).
