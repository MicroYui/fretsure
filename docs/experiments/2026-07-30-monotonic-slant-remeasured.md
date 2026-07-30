# The slant exemption, re-measured — 2026-07-30

## Why it is measured again

On 2026-07-29 exempting the hand's slant from `check_finger_monotonic` was
prototyped, measured, and **declined**: it bought 4.1 points of printed-fingering
accuracy and appeared to cost a tenth of the far field, shapes stretched 185 mm
apart going from almost always refused to admitted one time in ten.

Both instruments behind that number were later found defective, on the same day:

* the **hand-centre window** was 30 mm tighter than the span rule it duplicates,
  so part of what looked like the monotonic rule's contribution to
  discrimination was the window's over-refusal (`2026-07-30-reach-window.md`);
* the **negative construction** pushed whichever fretted note came first up the
  neck, so shapes whose lowest note moved were *collapsed* rather than pulled
  apart — four six-fret "negatives" ended with two fingers on one fret.

Neither is a reason to expect the answer to flip. Both are reasons the old answer
does not stand on its own, so it was measured again rather than inherited.

## What it measures now

All splits, 249 editorial frames, corrected profile and corrected instrument.
These four rows were measured against each other before the instrument was fixed
a fourth time (below), so read them as a comparison of variants rather than as
the shipped numbers, which are lower still:

| | 0 fr | 1 fr | 2 fr | 3 fr | 6 fr | 12 fr | refused frames |
|---|---|---|---|---|---|---|---|
| shipped | 9.8% | 47.9% | 57.0% | 71.9% | 97.9% | 100% | 23 |
| **slant exempt** | **5.3%** | 35.4% | 45.7% | 62.1% | **96.7%** | **100%** | **12** |
| slant, ≤2 frets | 6.9% | 36.6% | 49.4% | 73.3% | 97.9% | 100% | 16 |
| slant, ≤1 fret | 8.1% | 39.5% | 58.0% | 72.4% | 97.9% | 100% | 19 |

Held-out test split, 30 frames:

| | 0 fr | 6 fr | 12 fr |
|---|---|---|---|
| shipped | 3.3% | 96.7% | 100% |
| **slant exempt** | **0.0%** | **96.7%** | **100%** |

The far-field cost recorded on 07-29 was 12 points. It is **1.2** points on the
ambiguous six-fret band, **zero** on the twelve-fret band, and **zero** on the
test split at either. The 0.0% is thirty frames, so it means "nothing in the
held-out split is refused" and not much more, but the direction is unambiguous.

`FINGER_MONOTONIC` now blocks **no** editorial frame on its own, down from 11.
The remaining twelve are four held by `FRET_SPAN` alone and eight needing more
than one rule dropped.

## Why the exemption carries no fret bound

The bounded variants score worse on the positives and identically on the clean
far field, which is the empirical answer. The design answer matters more:
**`check_fret_span` already bounds how far back the slant may sit**, and it bounds
it positionally, which is the shape of the real constraint since frets narrow up
the neck.

```
lowest fret :  largest setback d_max(1, 4) still permits
     1      :  4 frets          9      :  7 frets
     3      :  5 frets         12      :  9 frets
     5      :  5 frets         15      :  7 frets
     7      :  6 frets         19      :  3 frets
```

Editors write setbacks of one to five frets. A fret-count bound inside the
monotonic rule would be a **third** statement of the same longitudinal limit —
and this codebase spent the morning removing the second one.

## The negative tabs, looked at

Ten move, all one shape family, rendered before the expected multiset was touched:

```
s2f5g3 s5f3g4    ring on the A string, little on the B a fret back
s4f4g3 s6f3g4    little on the top string, one fret back
s1f2g1 s5f1g2    index on the bass, middle toward the trebles
s2f7g3 s6f6g4    the one that reaches GREEN
```

`RED 1449 → 1439, AMBER 188 → 197, GREEN 81 → 82.`

## What it costs elsewhere

Relaxing the monotonic bound relaxes the CSP's pruning with it — the DFS may no
longer bound a finger from below using a lower-fret note that sits toward the
trebles, or the solver would be unable to find what the verifier now accepts. On
the deliberately pathological stress frame the internals roughly triple, 1,160
static checks to 3,328 and 4,640 candidates to 13,312.

The envelope does not move. `feasible_fingerings` is capped at
`MAX_SOLVER_FRAME_FINGERINGS = 64` per frame and the input contract already
prices every frame at that cap, so what changed is occupancy of a pre-paid bound.
The public result is still seven configurations.

## The mid-field

1 to 3 frets loosens by 10 to 12 points, and that is the one number here worth
being uneasy about. It is not a clean negative band — a three-fret displacement
moves 42 to 89 mm, inside what a hand spans — but it is not nothing either, and
the exemption is the largest single relaxation of the left-hand model so far.
What holds it in place is that the twelve-fret field is untouched and the span
rule still bounds every shape the exemption admits.

## Then the instrument turned out to be wrong a fourth time

The twelve frames left after the exemption were classified, and five shared one
minimal blocking set: `BARRE_INFEASIBLE + FINGER_MONOTONIC + SUSTAIN_CONFLICT`,
four of them on **two-note** frames. Five independent hard cases do not look like
that. Reading the annotations:

```
capricho-arabe   @ 75     sounding [53, 70]   editor's fingers {53: [4], 70: [4]}
sorf-op35-no3    @ 52     sounding [57, 60]   editor's fingers {57: [1], 60: [1]}
faure-sicilienne @ 75/2   sounding [48, 70]   editor's fingers {48: [3], 70: [3]}
```

**The same finger on both notes.** One finger cannot hold two different frets, so
the editor is not claiming these are held together — the mark on the bass note
describes the hand when it was *attacked*, and by the time the melody note
arrives the finger has been lifted and reused. `_editorial_frames` builds a frame
from everything sounding at an onset, which assumes every notated duration is
held to its end, so it manufactures a frame the editor never played.

A perfect verifier refuses those too, which is the standing check for this class
of error. They are now reported as unjudgeable. Guarded by test, and the open
string (finger 0) and shared fret (a barre) both correctly escape the filter.

## Where it actually lands

```
oracle@0.8.0 / median@0.3, all splits, 249 editorial frames

  0 fr   3.3% refused (8 of 241 judged)        12 fr   100.0%
  separation 96.7 points
  refused frames 7: FRET_SPAN alone 4, FRET_SPAN+SHIFT_SPEED 3
```

`FINGER_MONOTONIC` blocks nothing. Every remaining refusal is the span rule.

## And the span rule's remainder is mostly the same artifact

Asking what `hand_span_mm` each would need:

| needs | frames | as a first-position stretch |
|---|---|---|
| 140–150 mm (1.08–1.15×) | 3 | frets 1–6 — a real stretch, plausibly a large hand |
| 212–216 mm (1.63×) | 3 | frets 1–9 — no hand |
| nothing up to 250 mm | 2 | not a span question at all |

So at most three of the eight are about the hand model. The other five are the
full-sustain assumption again, in the form the same-finger filter cannot see: the
frame is geometrically impossible because a notated bass note is required to stay
down. Catching those needs the release model the solver already has and the
instrument does not.

**The frame-level instrument has largely bottomed out.** It found the neck-width
floor, the along-neck metric, the stretch span, the reach window and the slant;
what it has left to say is bounded by an assumption it makes about sustain rather
than by the oracle. The next gains are in the search, not the physical model.

## The repertoire gate went down, and that is the interesting part

| | accepted | GREEN | AMBER | INFEASIBLE | frozen 56-slice |
|---|---|---|---|---|---|
| reach fix only | 152 / 292 | 118 | 34 | 140 | 30 / 56 |
| **+ slant exempt** | **149 / 292** | 114 | 35 | 143 | 30 / 56 |

Three pieces and four certifications lost, while the verifier became markedly
*more* correct per frame. The infeasible reasons say exactly where they went:

```
no feasible frame config           44 -> 44     unchanged
no non-red extension within beam   90 -> 93     all of the loss
```

Nothing became physically unplayable. More configurations now survive each frame,
so a beam of 16 is under more pressure, and prefixes that used to reach the end
are displaced by cheaper ones that dead-end. This is the beam non-monotonicity
already documented in `score.py` and already observed once, when unconditional
sustain release made a solvable piece unsolvable by reordering the beam.

It is recorded rather than traded away. The oracle's job is to be right about
whether an exhibited fingering is playable, and on the only evidence that speaks
to that — fingerings human editors committed to in print — it went from refusing
9.8% to refusing 3.3% with the twelve-fret field untouched. A search that cannot
yet exploit a more permissive model is a search problem.

That said, it does mean **the corpus gate is not currently a clean read on the
physical model**, because a model improvement can move it either way through the
beam. The two numbers have to be reported together.

## What this reopens

Beam width was closed as a direction earlier in the week at one piece in sixteen.
That was measured against a profile since found to carry a 30 mm defect and a
monotonic rule since found to refuse the wrist's own geometry — both of which
kept the beam far less crowded than it now is. The beam bucket is now 93 of 292,
the largest single reason for refusal by a wide margin, and the old answer does
not carry to the new conditions.
