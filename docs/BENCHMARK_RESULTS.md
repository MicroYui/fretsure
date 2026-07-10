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

## The honest negatives (ablated components we keep public)

- **Critic buys nothing here.** −critic ties `full` exactly (0.81). The critic
  only re-ranks among already-GREEN candidates; on this corpus the top candidate
  was already the faithful one, so taste never changed the outcome. It is *not*
  earning its cost on this test set.
- **Best-of-N does not help — and nominally hurts.** −best-of-N (i.e. N=1) scored
  *higher* (0.94) than `full` (0.81). Caveat: each arm draws *independently* from
  the stochastic LLM (the arms are not paired on a shared proposal pool), so this
  is a between-samples comparison and the gap is within the overlapping Wilson
  intervals — the honest reading is "no measurable benefit on this corpus," not
  "N=1 is better." A paired best-of-N ablation (fix the proposals, vary only the
  selection) is the correct next experiment.

Reporting these keeps the project honest: only **repair** has so far earned its
existence on the procedural corpus. Critic and best-of-N remain in the agent but
are on notice — they must earn their keep on the harder (real-corpus) test sets.

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

- **n = 16, one seed** for the headline table (a second seed is used only as a
  robustness check). These are effect-size demonstrations, not leaderboard
  numbers.
- The procedural corpus is deliberately *easy* (2-bar diatonic lead sheets) so the
  data flow and ablation are unambiguous. Harder inputs (longer pieces, real MIDI,
  dense harmony) are the D-layer corpus work and are expected to move critic and
  best-of-N off zero.
- Ablation arms are unpaired across the stochastic LLM; large effects (repair)
  survive this, small ones (best-of-N) are confounded by it — hence the paired
  follow-up noted above.
- `joint_success` uses exact-onset matching for melody/bass; grid/DTW tolerance
  for real (human-timed) corpora is a later refinement (see `metrics/fidelity.py`).

## Also validated by this harness

- **Checker vs. LLM-judge** (`bench/checker_vs_judge.py`): the LLM judge
  false-accepts unplayable tabs the oracle rejects; McNemar confirms the oracle
  is the sounder gate. This is *why* the benchmark scores with the checker.
- **Baselines** (`bench/baselines.py`): raw-LLM-unverified and pure-solver arms
  bracket the agent.
- **pass@k / pass^k** unbiased estimators + Wilson (`bench/reliability.py`).
