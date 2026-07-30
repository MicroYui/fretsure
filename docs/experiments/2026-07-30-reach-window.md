# The window was the span rule, and it had the wrong number — 2026-07-30

> **Numbers superseded the same day.** The frame-level figures below (9.8%
> refused, 23 refused frames) were correct for this change in isolation. The
> monotonic slant exemption and a fourth instrument fix followed; the current
> state is 3.3% and 7 frames. See `2026-07-30-monotonic-slant-remeasured.md`.
> The reasoning here — one limit stated in two places, the smaller silently in
> force — is unchanged.

Two questions were open at the start of the day. Both are answered, and the
answer to the second turned out to be the reason the first mattered.

## The cheap question: what is still refused, under the current profile?

`scripts/classify_editorial_refusals.py` (new) asks, for every frame where an
editor printed two or more fingers and the verifier admits no reading of it,
*which single rule, if dropped, would admit it*. A violation type present in some
realisation proves nothing — another reading may avoid it — so the attribution is
"does any realisation violate nothing but this rule", which is the form with an
actionable answer. Frames no single rule unblocks are reported separately with
their minimal blocking sets rather than added to any rule's tally.

Under `median@0.2` it found a bucket that had not been there before:

```
249 editorial frames: 216 admitted, 29 refused, 4 unjudged
  FINGER_MONOTONIC   13 frames      SHIFT_SPEED   4 frames      FRET_SPAN   3 frames
```

`SHIFT_SPEED` on a frame is not possible as stated. Every note in a frame attacks
at the same instant, no time elapses, and `v_shift_mm_per_s` is never consulted.

## What `check_shift_speed` is actually made of

The predicate gives every fretted note the hand-centre interval
`[press_x - reach_mm, press_x + reach_mm]` and intersects them. That intersection
is non-empty exactly when the extreme pair lies within `2 * reach_mm` along the
neck. So the rule has two separable parts:

* a **per-frame span constraint** at `2 * reach_mm`, finger-blind;
* a **travel constraint** at `v_shift_mm_per_s`, which is the only part about time.

The first is the same physical claim `check_fret_span` makes with `d_max`, stated
once for all fingers instead of once per pair. Two constants for one limit means
the smaller is in force and the larger is decoration:

```
                     widest d_max     2 * reach     in force
pessimistic (GREEN)      117.0 mm      90.0 mm       90.0
optimistic  (RED)        143.0 mm     110.0 mm      110.0
```

The effective allowance for the (1, 4) finger pair was 110 mm, not 143. Four
editorial frames are unblocked by dropping this rule alone; three of them, with
the geometry read off the diagnostics rather than inferred:

```
aguado-op11n10  @  41/4   fingers 2-4   116.5 mm   d_max 128.7 ok   window 110.0 NO
sorf-op35-no18  @    62   fingers 2-4   115.8 mm   d_max 128.7 ok   window 110.0 NO
sorf-op35-no20  @ 389/3   fingers 1-4   116.5 mm   d_max 143.0 ok   window 110.0 NO
```

This gap was opened on 2026-07-29 by the change that raised the span to admit
ordinary stretch technique. `reach_mm` was deliberately held at 50 on the
reasoning that reach is where the hand sits while span is how far it stretches.
That reasoning is wrong: the window is not a resting-position rule, it is the
same stretch rule with the fingers rubbed out. **The stretch decision reached the
documentation without reaching the verifier** — it still refused frets 3 to 7
held by fingers 1 and 4, which is that stretch, one position up the neck.

## The other question: do `v_shift_mm_per_s` and `r_max_hz` need a source?

Not yet. Both are on plateaus. Verdict multiset over the 1,718 raw-model tabs:

| `v_shift_mm_per_s` | 50 | 100 | **200 – 2500** |
|---|---|---|---|
| RED | 1633 | 1625 | **1624, unchanged throughout** |

| `r_max_hz` | 2 | 3 | **4 – 40** |
|---|---|---|---|
| RED | 1635 | 1624 | **1624, one tab moves above 12** |

The shipped 500 and 8 sit in the middle of a 12× and a 10× range over which no
answer changes. Sourcing them would be sourcing constants that do no work, and
the reason `SHIFT_SPEED` *looked* load-bearing is that one diagnostic name covers
two constants and the other one was mis-set. That is now recorded in the
`profiles.py` docstring so the next reader does not go looking either.

`v_shift` is not inert in the predicate — 354 of the 5,734 shift diagnostics
remaining after this change are genuinely faster than it allows. It is inert in
*outcome*: those all land on tabs refused for other reasons too.

## The instrument had the same class of defect

Widening the window first appeared to cost far-field discrimination, 99.2% → 97.9%
refused at six frets. Looking at the nine frames that moved:

```
sorf-op35-no1   [(0, 13, 1), (2, 13, 4)]   extreme pair 0.0 mm
brahms-op39-no9 [(0, 14, 2), (2, 14, 4)]   extreme pair 0.0 mm
```

Two fingers on one fret. `_displaced_admits` moved whichever fretted note came
first in the realisation and always up the neck, so when that note was the
*lowest* of the shape the "negative" was the shape collapsed rather than pulled
apart. Fixed to push an extreme note outward — highest up, or lowest toward the
nut if that runs off the board — with a regression test. All the curves below are
measured with the corrected instrument, which is also stricter in the mid-field
than the old one, because its negatives are now genuinely wider.

`2026-07-29-oracle-discrimination.json` was produced by the old construction and
its far-field numbers are not comparable with
`2026-07-30-oracle-discrimination.json`; it is kept as the record of what was
measured that day, not as a baseline.

## The change, and what it costs

`reach_mm` becomes half `hand_span_mm` in all three profiles, and
`span_reach_inconsistency` requires equality again — with the reason stated this
time, and enumerated as a property over every single frame in
`test_the_hand_window_never_refuses_a_frame_the_exact_rules_admit`.

Published-fingering curve, all splits, 249 frames:

| displacement | before | after |
|---|---|---|
| **0 fr (what editors printed)** | 12.2% refused | **9.8%** |
| 1 fr | 47.9% | 47.9% |
| 2 fr | 64.9% | 57.0% |
| 3 fr | 77.7% | 71.9% |
| 6 fr | 99.2% | 97.9% |
| **12 fr (no hand of any size)** | 100.0% | **100.0%** |

Refused editorial frames 29 → 23; the `SHIFT_SPEED` bucket is gone and
`FINGER_MONOTONIC` remains the dominant single-rule blocker at 11 frames.

Negative-tab guard, re-frozen: **RED 1624 → 1449**, AMBER 67 → 188, GREEN 27 → 81.
218 tabs move, and for once the shapes did not have to be judged by eye — every
dropped refusal was checked against the rule that owns the same limit:

* **495** named two or more fretted notes; **all 495** lie inside the pairwise
  allowance for their own finger pair, so none was a violation the window caught
  on its own merits;
* **100** named one, so they came from the interval carried across time — and in
  **all 100** the distance was inside what `v_shift` permits in the elapsed time,
  several by an order of magnitude (35.3 mm needed, 366.7 mm available). Not one
  was a shift too fast; every one was an interval too narrow to hold a hand.

**The honest cost:** GREEN goes 27 → 81. Eighty-one of 1,718 raw model tabs now
certify, and unlike the G-major movement earlier this week these have not been
inspected one by one. The guard asserts provenance, not playability, and this is
the largest movement it has ever recorded.

## The repertoire gate, and a number that does not reproduce

Both arms measured in one process, same corpus files, only `reach_mm` differing:

| | accepted | GREEN | AMBER | INFEASIBLE | frozen 56-slice |
|---|---|---|---|---|---|
| before, reach 50 | 107 / 292 | 67 | 40 | 185 | 25 / 56 |
| **after, reach 65** | **152 / 292 = 52.1%** | **118** | 34 | 140 | **30 / 56** |

GREEN rises by more than acceptance does, so part of the gain is pieces moving
from accepted to certified rather than from refused to accepted.

The *before* arm is the problem. `CLAUDE.md`, `docs/PROJECT_STATE.md` and the
message of commit `7c6bd05` all record **146–147 / 292 with GREEN 89–93** for
this configuration, and it measures 107 / 292 with GREEN 67 — twice, from a
standalone run and from the A/B arm above, at that commit with a clean tree. The
one flag that could plausibly account for forty pieces does not: `--choose-capo`
recovers exactly one piece in fifty-six on the frozen slice, which scales to
about five across the corpus.

I cannot reconstruct where 146 came from, so it is corrected rather than
explained. The lesson is the cheap one: a headline number recorded from a run
nobody can re-issue is not a baseline, and the gate is expensive enough (27
minutes an arm) that it had been quoted from memory across sessions instead of
re-measured. Both arms above were run today, in one process, for that reason.

## What this leaves open

`FINGER_MONOTONIC` refusing the wrist's natural slant, unchanged and still
blocked on other editors' fingerings of the same repertoire. Everything else in
the frame-level bucket is now either that or a two-rule interaction.
