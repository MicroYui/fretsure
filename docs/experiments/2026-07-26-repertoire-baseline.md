# Repertoire playability baseline — 2026-07-26


> **Corrected 2026-07-28.** Every `/389` figure below was divided by a corpus
> that counted 86 pieces twice and carried 11 that were never parsed correctly;
> the corrected size is 292. The *rates* survive nearly unchanged (45.9% deduped
> against 45.5% before), because the duplicates split between accepted and
> refused almost exactly at the base rate — what does not survive is the corpus
> size, the grouped split, and any per-piece attribution. See
> [`2026-07-28-corpus-defects.md`](2026-07-28-corpus-defects.md). The numbers
> below are left as measured rather than rewritten.

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

### W2 — the solver's mirror stops guessing: 15 → 18

`check_shift_speed` propagates a reachable hand-centre interval through every
release and attack. The solver's incremental admission state summarized the hand
as a single centre and only compared it when the fretted sets were disjoint, so
it admitted paths the complete oracle later rejected. Sixteen beam slots filled
with doomed states and the final gate rejected all of them.

Rather than write a fourth copy of the rules, the propagation was extracted from
the predicate into shared helpers (`travel_reachable`, `admit_attack_shape`,
`hand_shape`, `fretted_interval`) and both callers now use them. The extraction
was verified behaviour-neutral first: the 1,718 negative tabs kept every verdict
and the gate stayed at 15 before the mirror was rewired.

The same reconciliation closed the barre divergence recorded at baseline.
`csp.assignment_valid` gained the missing rule, and the pruned DFS now defers a
completed candidate to that shared definition instead of approximating it
pairwise — the N-version differential caught the half-done version immediately,
which is what it is for.

| `Infeasible.reason` | before | after |
|---|---|---|
| no candidate passes the final full-oracle gate | 10 | **0** |
| no non-red extension within beam | 15 | 19 |
| score-level solver segment budget is exhausted | 8 | 10 |
| no feasible frame config | 7 | 8 |
| frame has more attacks than fingers | 3 | 3 |

The whole final-gate bucket is gone. Pieces that used to die there now either
solve or die inside the beam, and two more hit the segment budget — the exact
replay costs more work per extension, which is W5's problem.

GREEN 8 → 12, AMBER 7 → 6, accepted 15 → 18. Nothing lost. Worst sustain
retention 0.921, melody 0.984. Frozen Carcassi reference still 17/21 AMBER.
The oracle's verdicts on the negative set are unchanged: no rule moved, only the
solver's ability to see them.

One frozen expectation was re-frozen knowingly:
`test_decimal_runtime_reproduces_frozen_development_selection` pinned a specific
index into the GREEN finalist pool, and the pool is whatever the search kept.
The selection is still a certified GREEN finalist with zero awkward events.

### W5 — the segment budget was arbitrary: 18 → 21

`MAX_SCORE_SOLVER_SEGMENTS = 4` bounded how many independent bounded searches one
score may consume. It rejected ten of the fifty-eight pieces for running out of
splits — a statement about the search budget, not about anything a hand cannot
do. W2 made it worse (8 → 10) because an exact mirror costs more per extension.

Measured before choosing:

| segments | accepted | budget-exhausted failures |
|---|---|---|
| 4 | 18 | 10 |
| **8 (shipped)** | **21** | **2** |
| 16 | 22 | 1 |

Eight clears the bucket; sixteen buys one further piece for four times the
advertised aggregate work, which is not a trade worth advertising. Two pieces
still exhaust it and are reported as such rather than hidden.

`score-solver@0.4.0` → `0.5.0`, and `MAX_SCORE_SOLVER_AGGREGATE_WORK_UNITS`
doubles with it. Nothing lost, retention unchanged (0.921 / 0.984), negative-tab
verdicts unchanged.

### W5b — compute cannot buy this: 21 → 23

With cost explicitly removed as a constraint, every bounded-search limit was
swept against the thirty-seven pieces still failing. Almost all of them buy
nothing:

| knob | additional pieces solved (of 37) |
|---|---|
| beam 16 → 32 | 0 |
| beam 16 → 64 | 0 (and pushes 16 pieces into segment exhaustion) |
| frame configurations 48 → 192 | 0 |
| frame fingerings 64 → 256 | 0 |
| final full checks 16 → 64 | 0 |
| per-search work units 4× | **+2** |
| score segments 8 → 32 | **+2** |

The last two win the *same* two pieces (`carcassi-op26-04`, `sorf-op35-no24`)
and do not stack: the whole compute ceiling is two pieces. Raising the segment
count was chosen over the per-search budget because the latter is the public
`solve_fingering` contract that the interactive path also runs under.

**This is the useful result: the search bounds are not what limits quality.**
Twenty-four pieces fail with "no non-red extension within beam" — the search
explores and finds nothing the physical model accepts. That is not a budget
problem, and no amount of compute fixes it.

Accepted 23/58, and the segment-budget bucket is now empty. What remains is
fully attributed:

| remaining failure | count | owner |
|---|---|---|
| no non-red extension within beam | 24 | the sustain model (W3) and profile calibration (W6) |
| no feasible frame config | 8 | profile calibration (W6) |
| frame has more attacks than fingers | 3 | rolled chords (W4) |

### W3 — sustain has to be let go before the hand needs it: 23 → 26

A fretted note only sounds while its finger stays down, so the oracle reads a
notated duration as a physical hold. That is right for the verifier and wrong
for the source: engravers write voice-leading, and a player lifts when the hand
must move. Twenty-one of the twenty-four beam deaths are pieces asking a hand to
hold a bass pedal it was never expected to hold.

The obvious design is to let go inside the beam — try holding, and on refusal
compute the smallest set of releases that admits the frame. It was built, and it
is worth recording why it does not work. Instrumented on `brahms-op39-no9`, the
solver found 304 release opportunities across the whole search and the oracle
refused every one:

| release opportunities | hold admitted | release admitted |
|---|---|---|
| 304 | 0 | 0 |

Releasing at the frame that fails frees the *shape* but not one millisecond of
travel, because the hand was pinned to that instant by the very note being let
go. Freedom has to be taken before it is needed, and a beam that discovers the
need three frames later cannot reach back. Measured directly: releasing inside
the beam bought **0** pieces once the ladder below existed, so the machinery —
release variants, a reserved beam budget, a `released_sustain_beats` cost field,
the diversity key extension — was deleted rather than kept for its story.

What ships is a ladder of whole-score attempts. The score exactly as written is
always the first rung, so anything that solved before takes the path it always
took. Later rungs let the accompanying voices go progressively early, ordered by
how much sustain they give up, least first:

| rung | melody | bass / harmony |
|---|---|---|
| as written | full | full |
| ↓ | full | ¾ of written, floored at the next attack |
| ↓ | full | ½ |
| ↓ | full | the derived minimum |

Three rules bound the freedom, and all three are derived from the target rather
than declared by a caller — an input that could assert its own minimum hold
would be able to buy playability for free:

* every note sounds at least through the next attack, so adjacent-frame geometry
  stays fully constrained and the model cannot degenerate into ignoring sustain;
* a melody note has no freedom at all. Measured twice: releasing melody buys
  nothing, and holding it keeps melody-F1 invariant by construction;
* a bass note may go to half its written value and no further, which is what
  keeps a chord's root sounding when `bass_root_accuracy` asks for it.

**The retention floor is structural here, not a report.** A rung holding less
than 0.90 of what was written is never offered, because an unbounded ladder
would eventually accept every score by simply not sustaining it. The floor bites
immediately: `carcassi-op59-prelude-16` solves at 0.742 retention and is
therefore *refused*, staying in the failure column rather than being counted as
a win. That is the whole point of having the floor.

Accepted 23 → **26** (GREEN 13, AMBER 13). Nothing lost. Worst retention 0.921,
and the only two pieces below 1.000 on melody (0.984, 0.995) are W1's
repeated-pitch repair, not release — the ladder gave up zero melody beats.
Negative-tab verdicts unchanged, mutation suite unchanged, frozen Carcassi
reference still 17/21. `score-solver@0.5.0` → `0.6.0`, `sustain-model@0.1.0` →
`0.2.0`; `oracle@0.3.0` untouched.

Remaining, fully attributed:

| remaining failure | count | owner |
|---|---|---|
| no non-red extension within beam | 21 | profile calibration (W6) |
| no feasible frame config | 8 | profile calibration (W6) |
| frame has more attacks than fingers | 3 | rolled chords (W4) |

### W4 — a strummed chord was not representable at all: 26 → 26

The right hand admitted at most four simultaneous plucks, and a five- or
six-note chord is exactly that many. So the verifier's answer for an open E
minor — six strings, the first chord in every beginner's method book — was RED,
and there was no way to write it that would not be. That is not a calibration
problem. The model had no vocabulary for the motion.

`TabNote` gains an optional `attack_group`. Notes sharing a positive group at
one onset are **one gesture**: a finger sweeping a run of adjacent strings,
costing one right-hand event and one repetition rather than one per string.
Zero, the default, is an ordinary pluck, so a tab that never mentions the field
is judged exactly as it was before the field existed — which is what keeps the
negative-tab guard meaningful rather than merely re-passing.

| chord | verdict |
|---|---|
| open Em, thumb sweeps strings 0–2, i-m-a on 3–5 | **GREEN** |
| the same six notes as six independent plucks | RED |
| a sweep skipping a string | RED |
| two fingers sharing one group label | RED |
| a "group" of one note | RED |
| the same shape with an impossible left hand | RED |

Grouping names a motion; it does not create fingers. Five gestures need five
fingers just as five plucks do, so the four-event cap still has no mutant that
can be killed — the same redundancy already recorded for the pluck cap. What is
load-bearing, and does have a mutant, is that a sweep is one finger crossing
strings with no gap in the run.

**On the repertoire gate this bought nothing: 26 → 26.** Of the three pieces
failing for too many attacks, only one was ever a technique gap; the other two
demand 7 and 11 notes at a single onset, and a six-string instrument plays
neither however the hand is labelled. So the honest change to that bucket is
what it is now called:

| bucket | before | after |
|---|---|---|
| frame has more attacks than fingers | 3 | — |
| frame has more attacks than the instrument has strings | — | **1** |
| no non-red extension within beam | 21 | 23 |

The two freed pieces now die later, inside the beam, for a physical reason
rather than a vocabulary one. That is W6's bucket.

`oracle@0.3.0` → `0.4.0` and `tab-input@0.2.0` → `0.3.0`, the only planned
oracle bump in this milestone, and it *adds* a representable technique rather
than loosening a rule: every tab that was RED for a reason other than the pluck
cap is still RED, and the 1,849 negative tabs are verdict-for-verdict
unchanged. `fingering-solver@0.6.0` → `0.7.0`. The field round-trips through
Tab JSON (written only when non-zero), the agent trace, the HTTP wire, the web
client and the text export.

### W6 — the hand model is a placeholder, but loosening it is a bad trade: 26 → 26

`median@0.1` was never fitted to anything; its own docstring says the numbers
are v1 placeholders and only their ordering was ever asserted. Fitting them to
the repertoire is the obvious next step, and it is also exactly how a verifier
becomes a rubber stamp, so every move was priced on both sides at once:
the 58 published scores against the 1,718 raw-LLM tabs plus the mutation
triggers. A GREEN on that negative set is a false *certification*, not merely a
loss of caution, so it is counted separately from AMBER throughout.

| move | repertoire | false GREEN of 1,718 |
|---|---|---|
| baseline `median@0.1` | 26 | 6 (0.35%) |
| `v_shift` + `r_max` ×1.1 | 26 | 6 |
| `hand_span` + `reach` ×1.1 | **27** | **19 (1.11%)** |
| all four ×1.05 | **25** | 7 |
| `hand_span` ×1.25 | **25** | — |

No single coordinate buys anything. The one piece that is available
(`aguado-op11n01`) comes entirely from hand span and reach, and it costs
thirteen additional false certifications — a 3.2× increase in the measured
false-accept rate to gain one piece in fifty-eight. **That is not a trade this
project should make**, so no loosened profile ships and `median@0.1` remains
the default. What ships instead is `scripts/measure_profile_frontier.py`, which
re-runs the whole measurement and reports the curve rather than a number.

Three findings matter more than the exchange rate.

**A scalar hand width cannot separate the two sets.** Thirteen negatives have
fret span as their *only* defect, with overages of 9.3, 12.1, 12.1, 12.1, 12.1,
13.6, 18.8, 22.1, 22.1, 22.1, 40.0, 45.2 and 49.7 mm past what the median hand
allows. The repertoire frames that a 10% wider hand unlocks need +12.7 to
+14.7 mm. Six of the thirteen lie inside that same band: published Aguado and
raw model output overlap *in the margin*. Widening the hand until the repertoire
fits necessarily admits them too. That is a statement about the rule's shape --
`d_max = factor(gap) x span` over Euclidean fingertip distance -- and no
constant fixes it.

**Acceptance is not monotone in the profile.** `hand_span` ×1.25 loses
`prelude-16` and `carcassi-op26-05`; all four at ×1.05 loses one. A bigger hand
reorders the beam and discards paths that currently work, so a calibration
reporting only "accepted went up" would partly be measuring its own noise.

**"No measured cost" is not "no cost."** `SHIFT_SPEED` is the most common
violation in the negative set -- 1,629 of 1,640 RED tabs, and the sole reason
for 161 of them -- yet loosening it by 3× moves nothing. The model's shift
violations are gross, never marginal, so that set holds no near-boundary
evidence about shift speed and cannot validate a ten-percent change to it.
Reporting `v_shift` as "free" would be reporting the absence of a measurement.

Two limits were also measured rather than assumed:

* an **impossible hand** (250 mm span, 200 mm reach, 5 m/s shift, 50 Hz) solves
  13 of the 23 beam deaths and still fails the other 10. So roughly half that
  bucket is hand-model-limited in principle, at values no hand has, and half is
  not hand-limited at all;
* the deferred **drop-D metadata** fix was re-tested now that W3 and W5b exist.
  All three D2 pieces still fail under drop-D — `carcassi-op60-23` merely
  changes which reason it fails for. The deferral was correct and is now closed
  as measured rather than pending.

### W7 — what the benchmark says, measured for free

The milestone's last question was whether it is worth paying to re-run
benchmark v2 with a stronger proposer. That can be answered without spending
anything: run the deterministic B2 path over the same 503 items — no inference
call at all — before and after, with identical measurement code.

| | solved | GREEN | AMBER | final-gate failures | beam deaths |
|---|---|---|---|---|---|
| before (`ad24de8`) | 215/503 | 120 | 95 | **37** | 180 |
| after (`20cc429`) | **220/503** | **124** | 96 | **0** | 212 |

W2's reconciliation shows up here as well: the bucket where the incremental
mirror admitted paths the full oracle then rejected is gone, 37 → 0.

But the headline moved by +5 items, one percentage point, against a repertoire
gate that doubled. That gap is the result. The deterministic path used to solve
42.7% of the procedural corpus and 22.4% of published repertoire; it now solves
43.7% and 44.8%. **The synthetic corpus was not exercising what real music
exercises** — which is why 503 generated items never surfaced these failures and
58 published scores surfaced all of them in a day.

So: **do not pay to re-run the benchmark yet.** It would mostly re-measure a
corpus that does not probe the stack where it is weak. Fixing the corpus comes
first. That is a judgement from these numbers, not a measurement of a re-run.

Full receipt: [`../REPERTOIRE_MILESTONE_ACCEPTANCE.md`](../REPERTOIRE_MILESTONE_ACCEPTANCE.md).
