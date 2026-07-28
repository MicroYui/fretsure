# The frames with no fingering are mostly real — 2026-07-28


> **Corrected 2026-07-28.** Every `/389` figure below was divided by a corpus
> that counted 86 pieces twice and carried 11 that were never parsed correctly;
> the corrected size is 292. The *rates* survive nearly unchanged (45.9% deduped
> against 45.5% before), because the duplicates split between accepted and
> refused almost exactly at the base rate — what does not survive is the corpus
> size, the grouped split, and any per-piece attribution. See
> [`2026-07-28-corpus-defects.md`](2026-07-28-corpus-defects.md). The numbers
> below are left as measured rather than rewritten.
>
> **This document's conclusion is retracted, not merely re-based.** It found 13
> scores "physically impossible" and concluded from that, among other things,
> that the bucket is genuine with no cheap win in it. Those 13 are published
> studies guitarists play; it is the import that is wrong, and calling them
> impossible was a statement about the corpus dressed as a statement about the
> music. The bucket falls 52 → 46 on the corrected corpus and **has not been
> re-attributed**, so nothing below should be relied on for what refuses what.

## Why this bucket looked promising

After the capo ladder, 212 of 389 published scores are still refused. The largest
bucket is 130 beam deaths, but "no feasible frame config" is the more tractable
one: it fails at a *single frame*, so the refusing rule can be named exactly
rather than inferred from a search that wandered off.

At capo 0 there are 89 such scores, and their frame sizes pointed somewhere
specific:

| notes in the failing frame | scores |
|---|---|
| 2 | 9 |
| 3 | 4 |
| 4 | 22 |
| **5** | **33** |
| **6** | **21** |

Fifty-four at five or six notes is exactly where `oracle@0.4.0`'s attack-group
gestures apply — and that model offers **one** shape: the thumb sweeps the lowest
`size - 3` strings, contiguously, and i-m-a take the top three. A real hand has
more options, so the obvious hypothesis was that the shipped gesture vocabulary
is too small.

## The hypothesis was wrong

Attributing all 89:

| what refuses | scores |
|---|---|
| **left hand: no legal fingering for any placement** | **73** |
| pitches cannot be placed on distinct strings | 13 |
| both hands admit something, but `frame_configs` is still empty | 3 |
| **right hand: no gesture shape fits** | **0** |

Zero. The gesture vocabulary blocks nothing. Measured before implementing
anything, which is the only reason it cost an afternoon rather than a feature.

## What actually refuses

Drilling into the left hand, over 76 frames, taking for each the rule that
refuses the *fewest* assignments — the binding one:

| binding constraint | frames |
|---|---|
| **span (`d_max`)** | **58** |
| finger order against fret order | 10 |
| same finger, different fret | 3 |
| a note under the barre | 2 |
| would have been accepted | 3 |

Span dominates. That looks at first like it contradicts the finding that
loosening `d_max` buys almost nothing — the whole per-pair line was +2 — but it
does not, because the margins are wide:

```
span overage past the limit:  min 2.9 mm   median 13.8 mm   max 243 mm
                              within 5 mm:  7 of 32
                              within 10 mm: 14 of 32
```

The (1,2) change that shipped moved the allowance by 0.05 of the hand span — 5 mm
— and already pulled four certifications up out of the AMBER band. Reaching a
13.8 mm median would be far past the point where the negative set starts
certifying tabs it had confidently refused.

A separate 44 scores demand more than four fretted notes in a frame. A barre
answers that in principle, and the CSP does enumerate barres — `itertools.product`
over fingers includes repeats, so the enumeration is not the gap. For 22 of them
a barre is geometrically available and the CSP still refuses; for 35 there is no
shared fret to barre at all.

## The three that looked like a bug

Three scores had the left hand admitting a placement, the right hand admitting a
placement, and `frame_configs` still returning nothing. That reads like two
copies of one rule set disagreeing, which this project has paid for before.

It is not. Checking whether *one* placement satisfies both:

```
giuliani-op50n29        notes=5   both=0   lh_only=1   rh_only=1
horetzky19-movement-1   notes=5   both=0   lh_only=2   rh_only=18
```

Both hands are satisfiable, never simultaneously. The categorisation that made it
look like a bug checked the two constraints *independently* and assumed a
conjunction — the same error that produced four other wrong readings in this
stretch of work, and the reason each of them was checked before being built on.

## Conclusion

**This bucket is genuine.** Of 89: 13 are physically impossible, 35 need a fifth
finger nobody has, 32 overreach by a median 13.8 mm, and 3 need two things at
once that cannot both be had. There is no cheap win here, and no modelling gap
that a defensible constant would close.

That is worth knowing precisely because it was the tractable-looking bucket. The
remaining headroom is not in single-frame feasibility.
