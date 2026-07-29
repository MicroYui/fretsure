# The frame-config bucket, re-attributed — 2026-07-29

## Why it needed doing again

42 of the 146 refusals fail with "no feasible frame config". The bucket was
analysed once under `oracle@0.5.0` and that analysis is **retracted**: it
concluded 13 pieces were physically impossible, and they were import defects.
Since then the corpus was deduplicated, its voice roles rebuilt, eleven misparsed
scores quarantined, `d_max` floored at the neck's width, and the span rule moved
from a straight line to the neck axis. None of that had been re-measured here.

It is the locatable bucket. A beam death is a search that wandered off; this
fails at one instant, so the refusing rule can be named rather than inferred —
which is how both real defects found this week were found.

## What refuses them

The gate records the failing onset and its pitches, so nothing needs re-solving.
Enumerating every (string, fret, finger) placement of that frame and asking which
rule refuses *all* of them:

| | pieces |
|---|---|
| enumeration too large, excluded | 17 |
| several rules, none alone | 14 |
| no placement puts them on distinct strings | 9 |
| the frame alone is fine — the refusal is the history | 1 |
| BARRE | 1 |

Nine are genuinely impossible: the pitches cannot occupy distinct strings, which
is an instrument fact and not a hand model. Seventeen are excluded rather than
counted — five and six note frames with four finger choices each exceed the
enumeration bound, and judging them on a truncated list is the confound that cost
a measurement earlier this week.

## The margins are much tighter than they were

For the fourteen, how far the closest placement still sits from the limit:

```
horetzky27, horetzky32   along-neck 57.9   limit 52.5   over  5.4   fingers (2,3)
g-major-allegro          along-neck 61.2   limit 55.0   over  6.2   fingers (1,2)
aguado-op11n03           along-neck 63.1   limit 55.0   over  8.1   fingers (1,2)
giuliani-op50n32         along-neck 111.0  limit 100.0  over 11.0   fingers (1,4)
sorf-op45n05             along-neck 148.1  limit 100.0  over 48.1   fingers (1,4)
```

Several are 5 to 8 mm out, against a median of 20.7 mm when this was last
measured under the Euclidean metric. Moving the span rule to the neck axis did
not only change which frames pass; it changed how close the failures are.

`d_max(2, 3)` is 52.5 mm — the neck-width floor, above its own factor of
0.5 × 100 = 50 mm. Fifty millimetres along the neck is about 1.4 frets at the
nut, so the model allows middle and ring barely more than one fret apart
longitudinally, and guitarists routinely place them two.

## A candidate that did not survive the split

There is a real argument for re-deriving the factors: they were calibrated as
*straight-line* limits, and a straight-line limit has to accommodate the
across-string component, so the same number describes a different quantity once
the metric is longitudinal.

Sweeping (2, 3) and (3, 4) on **train**:

| factor | 0 frets | 2 frets | 6 frets | 12 frets |
|---|---|---|---|---|
| 0.50 (shipped) | 14.7% | 67.5% | 99.5% | 100% |
| 0.60 | **13.2%** | 61.7% | 99.5% | 100% |
| 0.70 | 13.2% | 59.7% | 99.5% | 100% |

The far field does not move at all, which is different from every earlier
loosening — those collapsed at six frets. So this looked shippable.

On **test** it does nothing: 6.7 / 66.7 / 100 / 100 at 0.60, identical to 0.50.
The negative tabs are unchanged too, at RED 1648 / AMBER 45 / GREEN 25, so there
is no cost — but there is no held-out benefit either.

**Not shipped.** A gain that appears only on the split it was chosen on is
fitting, not calibration. The test split is 30 frames and may simply lack the
power to see a real 1.5-point effect, which is worth saying — but "might be real"
is not evidence, and this is the fifth constant this week whose only support was
the number being looked at.

## Where the bucket stands

Of 42: nine impossible on the instrument, one a history problem rather than a
frame problem, one a barre, fourteen refused by several rules at once with
margins now in single-digit millimetres, and seventeen not judged at all because
the enumeration is too large.

That last number is the actionable one. Seventeen unjudged is 40% of the bucket,
and the shipped solver enumerates at 48 configurations per frame — the same bound
that made these too large to analyse exhaustively. Whether the solver is refusing
them for the same reason this analysis could not judge them is a question the
generation-width measurement touched (1 of 12 recovered at eight times the bound)
but did not answer for this bucket specifically.

## Postscript: all 42 judged, and the bucket is genuine — 2026-07-29

The seventeen "too large to enumerate" frames were a self-inflicted bound.
`check_finger_monotonic` requires finger order to follow fret order, so once the
(string, fret) assignment is chosen the fingers are **determined** -- sort the
distinct frets and number them, notes sharing a fret sharing a finger, which is
what a barre is. Enumerating fingers independently multiplies the space by 4^n
for nothing. Collapsing it takes `horetzky3` from sixteen million placements to
about four thousand, and every frame in the bucket becomes enumerable.

Before that, the generation hypothesis was tested directly and killed:
`frame_configs` returns **zero** configurations for all seventeen dense frames at
48, 200, 1,000, 5,000 and 20,000 -- four hundred times the shipped bound. Their
emptiness is not the search failing to look. And none of the 42 needs more than
four distinct frets, so it is not the finger count either.

All 42, with fingers collapsed:

| | pieces |
|---|---|
| FRET_SPAN alone | 14 |
| several rules, none alone | 14 |
| no assignment puts them on distinct strings | 10 |
| the frame alone is fine — the refusal is the history | 2 |
| FINGER_COUNT | 2 |

How far the closest admissible-shaped placement still sits from the limit:

```
FRET_SPAN alone            11.0 / 25.8 /  93.1 mm   (min / median / max)
several rules, none alone  11.0 / 39.7 / 110.2 mm
```

**Eleven millimetres at best, twenty-six at the median.** For comparison, the
neck-width floor that made a G major certifiable moved a limit by 2.5 mm, and the
adjacent-finger factor sweep rejected above gains 1.5 points on train and nothing
on test. Nothing in that range reaches a median of 26 mm.

### A number of mine that this corrects

The partial attribution earlier in this document reported margins of 5 to 8 mm.
That version enumerated fingers independently, including assignments the
monotonic rule then refuses, and a span measured on an illegal fingering can be
smaller than any legal one. The collapsed figures are the right ones, because the
solver can only build monotone assignments in the first place.

### Where the bucket stands

Ten are impossible on the instrument -- the pitches cannot occupy distinct
strings, which is not a hand model at all. Two need a fifth finger. Two are not
frame problems. The remaining twenty-eight are geometry, at margins no defensible
constant reaches.

So this bucket is genuine, and unlike the retracted version that claim now rests
on a corrected corpus, a corrected oracle, and **all 42 judged rather than 25**.
The remaining headroom in this corpus is not here.
