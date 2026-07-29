# A hand with an angle: viable, and a bigger change than it looks — 2026-07-29

## Why the question came up

`check_fret_span` compares fingertips pairwise; `check_finger_monotonic` compares
finger numbers to fret numbers. Neither knows the fingers belong to one hand with
an orientation, and that omission is exactly what refuses the wrist's slant — the
defect that holds the frame-level false-negative rate at 16.3% and that no fitted
constant fixed without costing far-field discrimination.

A hand plane would represent it: an origin and an angle, fingers nominally spaced
along the hand's axis, each reaching within some radius of where the hand puts
it. The setback then *follows from the angle* instead of being a number chosen to
make refusals go away, which is the property every rejected fix this week lacked.

## First parameterisation: fits everything, so constrains nothing

Letting the angle, the finger spacing and the origin all float per frame
reproduced the editorial fingerings to a median residual of **0.0 mm**, with
angles filling the whole ±60° sweep and spacings using every value offered from
18 to 38 mm.

That is not the model being right. Three free parameters interpolate, and a
verifier built on it would refuse nothing. Recorded because the result *looks*
like a success and reporting it as one would have been the most expensive
mistake available.

## Second: the freedom a wrist actually has

Spacing pinned at `hand_span_mm / 3` = 33.3 mm, since the 1-to-4 span is three
gaps by definition. Only the origin free, and the angle bounded:

| wrist rotation allowed | residual median | 75% | 90% | max |
|---|---|---|---|---|
| 10° | 7.8 mm | 14.4 | 21.4 | 75.7 |
| 20° | 4.4 | 12.3 | 18.0 | 68.8 |
| **30°** | **1.2** | **9.8** | **15.8** | **60.4** |
| 45° | 0.4 | 3.5 | 9.2 | 45.3 |

So a hand rotated up to 30°, with fingers reaching about **16 mm** from where the
hand puts them, places 90% of what editors write. The last 10% need 60 mm, which
is either genuine extremes or the label noise this corpus is known to carry —
nine of forty refusals examined earlier were labels attached to the wrong note.

The model is therefore viable and it is not vacuous: 16 mm of play against 33 mm
of spacing leaves neighbouring fingers distinguishable.

## What building it would mean

This is where it stops being a rule change. The geometry is what the project's
central claim rests on, so replacing it moves:

- `d_max`, `_SPAN_FACTORS`, `check_fret_span`, `check_finger_monotonic` — the last
  two collapse into one constraint rather than being tuned separately
- the contract: `oracle@0.6.0` → `0.7.0`
- every frozen artifact: the negative-tab multiset, benchmark bundles, golden
  traces, the web and API stamps
- two new parameters, which must be chosen on the train split and reported on
  test, or they are the same fitted constants this week has rejected four times

And it needs the same two-sided evidence as everything else: the discrimination
curve must improve at zero displacement *without* the far field softening, which
is precisely the trade that disqualified the slant exemption.

## Status

Measured, viable, not started. The next step is not more measurement — it is a
decision about whether to rebuild the oracle's geometry, which is a different
kind of commitment from the fixes made this week.
