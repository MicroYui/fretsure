# Beam width is exhausted; selection is not — 2026-07-30

## Why it was re-opened

`no non-red extension within beam` is 93 of 292 pieces, the largest single reason
the repertoire gate refuses. Beam width was closed earlier in the week at one
piece in sixteen, but that was measured against a profile since found to carry a
30 mm defect and a monotonic rule since found to refuse the wrist's own geometry.
Both kept the beam far less crowded than it is now, so the old answer did not
carry to the new conditions and was re-run rather than inherited.

## It is worse, and not marginally

Full corpus, `oracle@0.8.0` / `median@0.3`, same process shape:

| | accepted | GREEN | AMBER | frozen 56-slice | runtime |
|---|---|---|---|---|---|
| **beam 16** | **149 / 292** | **114** | 35 | **30 / 56** | 35 min |
| beam 32 | 143 / 292 | 111 | 32 | 27 / 56 | 65 min |

Failure reasons:

```
                                       beam 16   beam 32
no non-red extension within beam            93        94
no feasible frame config                    44        42
score-level solver segment budget exhausted  0         5
```

## The two things this says

**Doubling the width does not shrink the bucket it was supposed to shrink.**
93 → 94. Whatever kills those searches, it is not that sixteen states were too
few. That is the finding worth keeping: the deaths are about *which* states are
retained, not *how many*, and the two are separate levers.

**The cost is real and it lands somewhere else.** Five pieces fail on the
score-level segment budget at beam 32 and none at beam 16, because a wider beam
doubles per-configuration work and the aggregate work gate then exhausts
segmentation. That is a confound: those five are not search failures.

It does not rescue the result. Crediting all five as successes gives 148, still
below 149, and the beam bucket is still larger. Beam 32 loses on its own terms
before the budget is even considered.

## What it re-opens

A retention *policy* change — reordering which states survive rather than keeping
more of them — is not tested by this and is not subsumed by it. I closed that
direction once with the reasoning that a wider beam subsumes what a ranker could
keep, which is false: the beam retains by cost and diversity, and a ranker would
retain by predicted completability. This measurement is direct evidence that the
two differ, since the width lever is now measured flat.

Whether that is worth building is a separate question -- a learned ranker needs
supervision, and the honest supervision here is "did this prefix reach a certified
end", which is exactly what the gate takes 35 minutes to compute. But the
direction is open where width is closed.

## Not changed

`MAX_SCORE_SOLVER_SEGMENTS` stays at 4 and the beam stays at 16. Nothing here
argues for moving either; the segment budget only appeared as a confound of a
change that lost anyway.
