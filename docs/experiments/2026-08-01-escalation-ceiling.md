# The ceiling with everything already built — 2026-08-01

`solve_fingering_score_with_escalation` has been implemented, documented and
tested for two weeks, and until 2026-07-31 nothing outside tests had ever called
it. It tries the capo ladder first, then widens the beam to 32 and then 64 for
whatever nothing else saved. This is the first time it has been run over the
corpus.

## The ladder, one rung at a time

`oracle@0.8.0` / `median@0.3`, repaired corpus, 292 scores:

| | accepted | GREEN | AMBER | frame-config | beam | frozen 56 | runtime |
|---|---|---|---|---|---|---|---|
| fixed capo | 154 / 292 = 52.7% | 117 | 37 | 46 | 91 | 30 | 35 min |
| **+ capo ladder** | **178 / 292 = 61.0%** | 132 | 46 | 40 | 73 | 32 | 90 min |
| **+ beam ladder (32, 64)** | **182 / 292 = 62.3%** | 135 | 47 | 40 | **69** | 33 | 145 min |

## The asymmetry is the finding

```
capo ladder    +24 pieces
beam ladder     +4 pieces      at nearly twice the runtime again
```

Trying **every refused score at beam 32 and then at beam 64** recovers four
pieces, 5.5% of the 73 beam deaths it was aimed at. The other 69 are not
width-limited in any sense that widening reaches.

That closes the width question as firmly as this corpus can. Earlier evidence
pointed the same way and was weaker: beam 32 alone measured *worse* than 16 —
143 against 149 — while the bucket it was meant to shrink moved 93 → 94, and the
escalation docstring's own note records beam 32 recovering 7 of 80 refusals and
beam 64 recovering 5 with only 4 in common. Now the union of both, applied to
everything, is measured: four.

## What that leaves, at the ceiling

```
292 scores
 182  accepted (62.3%)
 110  refused
        69  beam        not width-limited
        40  frame-config  closed: geometry at a median margin of 21.8 mm
         1  quarantined corpus defect
```

So the only lever left in this corpus is **which** states the beam retains, not
how many of them, and not the capo. `score.py` says so in its own words —
*"cost does not predict completability"* — and the number that now backs it is
that four extra pieces is what quadrupling the search buys.

## The cost, stated plainly

145 minutes against 35 for the plain gate. The capo ladder earns its 55 extra
minutes at +24 pieces; the beam ladder spends another 55 for +4. As a product
default the capo ladder is worth having and the beam ladder is not, which is what
the escalation function's own docstring already argued from a much smaller
sample.
