# Repertoire playability baseline — 2026-07-26

## Why this exists

The benchmark's headline is 74/500 = 14.8% joint success, and the natural reading
is that the arranging model is weak. That reading is wrong, and this file is the
measurement that shows it.

`data/score_corpus/` holds 58 licensed, editor-prepared classical guitar scores —
Carcassi, Sor, Aguado, Tárrega, Mertz — 17,944 notes of music that guitarists have
performed for a century or more. Their playability is not a modelling opinion; it
is established by two hundred years of people playing them.

Run the production deterministic pipeline over all 58:

```bash
uv run python scripts/evaluate_repertoire_playability.py --summary-only
```

## Baseline (`oracle@0.3.0`, `median@0.1`, beam 16)

| outcome | count |
|---|---|
| GREEN | 7 |
| AMBER | 6 |
| INFEASIBLE | 45 |
| **accepted (non-RED)** | **13 / 58** |

**The verifier rejects 78% of real guitar repertoire.** No LLM is involved
anywhere in this measurement.

### Why the 45 fail

| `Infeasible.reason` | count | what it actually is |
|---|---|---|
| no non-red extension within beam | 16 | search died under the physical model |
| no candidate passes the final full-oracle gate | 11 | the solver's incremental mirror admitted paths the real oracle then rejected |
| score-level solver segment budget is exhausted | 8 | `MAX_SCORE_SOLVER_SEGMENTS = 4` — **nothing to do with playability** |
| no feasible frame config | 7 | one chord has no valid shape under `median@0.1` |
| frame has more attacks than available right-hand fingers | 3 | rolled chords flattened onto a single onset by the importer |

Classifying the failing frames independently: **21** fail on a frame holding a
single note (so the rejection comes from context, not the frame), **15** on a
multi-note frame whose minimal achievable fret span is ≤ 5 frets — shapes a
guitarist plays — **3** on genuinely wide stretches, and **1** on a frame with no
possible string assignment at all (E2 and G2 simultaneously, a corpus artifact).

So roughly 90% of the failures are on material that is either playable in
principle or rejected by context modelling, not by an impossible demand.

## Corroborating measurements

- The deterministic B2 baseline — **no LLM at all** — fails 296/503 items of the
  benchmark corpus, 72 of them because a demanded chord has no shape.
- The raw single-shot LLM baseline is 0/5030 GREEN across every stratum. Models
  do not produce playable tablature unaided; that premise is confirmed, and it is
  not what limits the current number.
- `docs/LEFT_HAND_SOLVER_V2.md:65` already recorded that whole Carcassi Prelude 1
  is AMBER "mainly from arpeggio spans over a sustained bass". This measurement
  shows that observation generalizes.

## Two findings recorded while building the gate

**The four-simultaneous-plucks cap is redundant.** `check_right_hand` rejects any
frame with more than four attacks, but `right_finger` has exactly four possible
values, so a fifth simultaneous note must reuse a finger and trips the
one-finger-one-string rule anyway. Deleting the cap changes the diagnostic text,
never a verdict. Lifting it alone would *not* make a six-string chord
representable — the binding rule is one finger per string.

**Two copies of the barre rule disagree.** `csp.assignment_valid` implements
monotonicity and same-finger-same-fret, but not `check_barre`'s rule that nothing
lower may be fretted inside a barre's string span (`predicates.py:296-297`). The
solver therefore enumerates fingerings the oracle rejects, and with
`MAX_SOLVER_FRAME_FINGERINGS = 64` a legal fingering can be crowded out by
invalid ones. Recorded as a strict `xfail` in
`tests/oracle/test_incremental_mirror_agreement.py`.

## Guards that come with the baseline

- `scripts/replay_negative_tabs.py` — 1,718 distinct raw-LLM tabs from the frozen
  collection, replayed through `check_playability`. Their verdict multiset must
  stay exactly `{RED: 1651, AMBER: 61, GREEN: 6}`. Any movement means a change
  meant for the solver reached the verifier.
  Note the divergence from the collection's own record: under `oracle@0.2.0` these
  were 1843 RED / 6 AMBER / **0 GREEN** per row. Today six raw model tabs are
  accepted outright — the `oracle@0.3.0` occupancy correction that stopped open C
  and F chords reading as AMBER also admitted them. That is the only measured
  false-accept evidence this project has, and it is recorded rather than smoothed.
- `sustain_retention` is reported per piece: realized sounding time over notated
  time. Blanket release of every sustain scores 0.729, so a model that buys
  acceptance by dropping sustain is visible rather than flattering.

## What this baseline does not say

The corpus is four composers and one typesetter of 19th-century classical guitar.
A false-negative rate measured here is a rate *for that idiom*. It says nothing
about the false-accept rate on machine-generated arrangements, because published
scores contain almost no unplayable examples — that direction needs a human
playing Fretsure's own output, and the one such data point on record is `PARTIAL`.

---

## Progress against this baseline

Each entry is one commit, measured on the same gate with all four guards green.

### W1 — repeated-pitch hold repair: 13 → 15

A pitch attacked again has necessarily stopped sounding, so a target asking to
hold both at once asks for something no instrument does.
`solver/sustain.py::repair_repeated_pitch_holds` ends the earlier instance at the
later attack, applied after `ensure_solver_input` so validation stays a pure gate.

The clip cannot cost faithfulness: the pitch is still sounding at the moment the
overlap would have begun, because that moment is exactly when it was re-attacked.

Gained: `mutopia-cc-by-sa-aguado-op11n02`, `mutopia-cc-by-sa-carcassi-op60-22`.
Lost: none. Worst overall sustain retention 0.952, worst melody retention 0.997.

Three neighbouring repairs were measured and **rejected** rather than shipped:

| variant | solved | target retention |
|---|---|---|
| repeated pitch only (shipped) | 15 | 0.991 |
| + clip melody at the next melody attack | 15 | 0.990 |
| + clip harmony at the next harmony attack | 15 | 0.989 |
| + clip bass at the next bass attack | 18 | 0.952 |

Clipping melody or harmony buys nothing. Clipping every held bass buys three
pieces but is not a bug fix: `bass` in this corpus means "not in the primary
voice", and two low voices ringing together is ordinary guitar writing. Those
three pieces are W3's to earn, by releasing only where the hand actually needs
it and paying for it in the objective.

### Deferred, with reasons

- **Three pieces need drop-D** (`capricho-arabe`, `faure-op78-sicilienne`,
  `carcassi-op60-23` all reach D2) but the corpus records standard tuning for
  every example. Solving them in drop D was measured: it changes no outcome,
  because they fail for other reasons. Fixing the metadata means rebuilding
  through an external `python-ly` checkout under `/private/tmp` and invalidating
  the frozen corpus digests, for zero measured gain. Recorded, not done.
- **Two Sor pieces carry 7- and 11-note simultaneities** (`sorf-op35-no21`,
  `sorf-op45n01`) — rolled chords flattened onto one onset by the converter.
  They become representable once W4 lands.
