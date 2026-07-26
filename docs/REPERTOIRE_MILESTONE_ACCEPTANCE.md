# Repertoire acceptance milestone — acceptance receipt

**Date:** 2026-07-26 · **Branch:** `main` · **Scope:** make the verification stack
accept real guitar repertoire without making it accept anything else.

## The problem this milestone attacked

The deterministic pipeline rejected **45 of 58** published classical guitar
scores — Carcassi, Sor, Aguado, Tárrega, Mertz, 17,944 notes, music humans have
played for two centuries. The same stack, *with no LLM anywhere in it*, failed
288 of 503 benchmark items (measured below). So the benchmark's 14.8% was never
mainly a statement about arranging taste, and a stronger proposer could not have
moved it. The ceiling was in verification.

## Result

**13 → 26 of 58**, doubled, with the oracle's judgement on known-unplayable
input **unchanged throughout** and every step separately measured.

| step | commit | change | gate |
|---|---|---|---|
| W0 | `ad24de8` | gate, four guards, baseline frozen | 13/58 |
| W1 | `cbfd2df` | repeated-pitch hold repair | 15/58 |
| W2 | `d8cb3e0` | exact incremental mirror, three rule copies reconciled | 18/58 |
| W5 | `2ae0f2b` | segment budget 4 → 8 | 21/58 |
| W5b | `da70199` | every other search bound measured; only segments 8 → 32 paid | 23/58 |
| W3 | `3dab291` | whole-score sustain relaxation ladder | **26/58** |
| W4 | `fdc84aa` | rolled/strummed chord gestures | 26/58 |
| W6 | `20cc429` | profile frontier measured; **loosening declined** | 26/58 |

## The four guards, at the end

| guard | requirement | result |
|---|---|---|
| 1 — oracle not weakened | 1,718 raw-LLM tab verdicts invariant | `{RED: 1651, AMBER: 61, GREEN: 6}`, unmoved |
| 2 — no regression | every baseline-accepted piece still accepted | nothing lost at any step |
| 3 — sustain not quietly dropped | overall retention ≥ 0.90 | worst 0.921; ladder released **zero** melody beats |
| 4 — mutation suite | unchanged, plus mutants for anything new | 14 mutants, kill rate 1.0 |

Guard 3's floor is enforced *structurally*, not merely reported:
`carcassi-op59-prelude-16` solves at 0.742 retention and is therefore **refused**.
An unbounded ladder would accept every score by simply not holding it.

## What is left, and what would actually fix it

| remaining failure | count | owner |
|---|---|---|
| no non-red extension within beam | 23 | the geometry model's *shape* |
| no feasible frame config | 8 | 6 the same; 2 impossible in standard tuning |
| more attacks than the instrument has strings | 1 | corpus defect (11 notes at one onset) |

Three pieces are corpus defects rather than model failures: two demand two
pitches that both need the sixth string (E2 with G2), one demands eleven notes
at a single onset. No hand model will ever accept them, and they are reported
rather than dropped from the denominator.

For the rest, the milestone's most useful output is a **negative** result, and
it is specific. Widening the hand until the repertoire fits necessarily admits
raw model output, because the two overlap in the same margin: the thirteen
negatives whose only defect is fret span overshoot by 9.3–49.7 mm, and the
repertoire frames a wider hand unlocks need 12.7–14.7 mm — six of the thirteen
sit inside that band. `d_max = factor(gap) x span` over Euclidean fingertip
distance cannot separate Aguado from garbage at *any* constant. The next real
gain is a better-shaped left-hand model (finger independence, position-dependent
span), or human calibration — not another parameter sweep.

An impossible hand (250 mm span, 5 m/s shift) solves 13 of the 23 beam deaths
and still fails 10, which bounds how much of that bucket geometry could ever
own.

## Verification

Every step ran, and passed:

```
uv run --frozen ruff check && uv run --frozen mypy --strict src
uv run --frozen pytest -q -m 'not integration'          # 2758 passed
uv run --frozen python scripts/evaluate_repertoire_playability.py
uv run --frozen python scripts/replay_negative_tabs.py
uv run --frozen python scripts/evaluate_left_hand_reference.py   # 17/21, unmoved
```

New permanent instruments, all re-runnable after any future change:

- `scripts/evaluate_repertoire_playability.py` — the gate itself, with per-piece
  outcome, failure reason and sustain retention;
- `scripts/replay_negative_tabs.py` — verdict-multiset invariance on the
  negative set;
- `scripts/measure_profile_frontier.py` — the two-sided profile frontier and the
  boundary evidence behind it;
- `scripts/measure_deterministic_baseline.py` — the LLM-free benchmark ceiling.

## Contracts that moved

`oracle@0.3.0 → 0.4.0` (adds representable rolled/strummed gestures; does not
loosen a rule), `tab-input@0.2.0 → 0.3.0`, `fingering-solver@0.6.0 → 0.7.0`,
`score-solver@0.4.0 → 0.6.0`, `sustain-model@0.1.0 → 0.2.0`.

`median@0.1`, `small@0.1` and `large@0.1` are **unchanged**, fingerprints
included, and `median@0.1` remains the product default.

## The benchmark ceiling, re-measured for free

The plan asked whether it is worth paying to re-run benchmark v2 with a
stronger proposer. The way to answer that without spending anything is to run
the deterministic B2 path — the same 503 items, proposed and fingered with no
inference call at all — before and after the milestone, with identical
measurement code (`scripts/measure_deterministic_baseline.py`, run at `ad24de8`
and at `20cc429`).

| | solved | GREEN | AMBER | final-gate failures | beam deaths |
|---|---|---|---|---|---|
| before (`ad24de8`) | 215/503 | 120 | 95 | **37** | 180 |
| after (`20cc429`) | **220/503** | **124** | 96 | **0** | 212 |

Two things this says.

**W2's reconciliation was a correctness win, and it shows here too.** The
"no candidate passes the final full-oracle gate" bucket — the incremental mirror
admitting paths the full oracle then rejected — is *gone*, 37 → 0. Those items
now either solve or fail honestly inside the beam.

**But the headline moved barely at all: +5 items, +1.0 percentage point.** Set
against the repertoire gate doubling, that gap is the finding. Before this
milestone the deterministic path solved 42.7% of the procedural corpus and only
22.4% of published repertoire; it now solves 43.7% and 44.8%. **The synthetic
corpus was never exercising the failures real music triggers** — which is
precisely why 503 benchmark items never revealed them and 58 published scores
did within a day.

So the answer to the plan's question is **no, not yet**: a paid re-run would
largely re-measure a corpus that does not probe the stack where it is weak. The
corpus is the thing to fix first. That recommendation is a judgement from these
numbers, not a measurement of a re-run.

## Honest limits

- This is machine certification under a versioned simplified geometry, not a
  human guarantee. Six raw-LLM tabs are certified GREEN by the current model and
  are the only measured false-accept evidence the project has; that number did
  not move in this milestone, but it did not improve either.
- 58 scores is a small positive set, single genre, single instrument
  configuration.
- The frozen benchmark results in `docs/BENCHMARK_RESULTS.md` are **not**
  rewritten. The deterministic re-measurement above is published under a new
  identity, in `docs/experiments/2026-07-26-deterministic-baseline{,-before}.json`.
- B2's GREEN count is an upper bound on its joint success, not a joint success
  rate: joint success also requires the independent fidelity gate, which this
  measurement does not run. It is not comparable with the recorded 74/500.
