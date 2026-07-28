# The longitudinal hand limit is written twice — 2026-07-27


> **Corrected 2026-07-28.** Every `/389` figure below was divided by a corpus
> that counted 86 pieces twice and carried 11 that were never parsed correctly;
> the corrected size is 292. The *rates* survive nearly unchanged (45.9% deduped
> against 45.5% before), because the duplicates split between accepted and
> refused almost exactly at the base rate — what does not survive is the corpus
> size, the grouped split, and any per-piece attribution. See
> [`2026-07-28-corpus-defects.md`](2026-07-28-corpus-defects.md). The numbers
> below are left as measured rather than rewritten.

## Question

The oracle constrains how far apart the left hand's fingers can be along the
neck in two places:

- `d_max(i, j, hand_span_mm)` — finger-aware, over the **Euclidean** fingertip
  distance of a pair. For (1,4): `1.0 × 100 = 100 mm` median.
- `fretted_interval` / `hand_shape` in `check_shift_speed` — finger-**agnostic**,
  requiring every held fretted note to fit a hand-centre window of width
  `2 × reach_mm = 100 mm` median.

Same physical fact, written twice, at identical values, one carrying the
across-string component and one not. Nothing tied them together. Did the
duplication cost anything?

## Answer: yes, but not in the direction assumed, and fixing it buys nothing

**The redundancy is one-directional and provable.** Over all 266 failing frames
in the 389-score corpus:

| frame verdict | count |
|---|---|
| admissible in isolation (refusal is temporal) | 136 (51%) |
| both refuse | 82 (31%) |
| structural (>4 frets / barre / monotonicity) | 28 (11%) |
| **`d_max` refuses what the reach window would admit** | **16 (6%)** |
| **reach refuses what `d_max` would admit** | **0** |

Zero is forced, not luck. Take a frame's lowest- and highest-fret notes. Same
finger ⇒ the barre rule forces the same fret ⇒ span 0. Different fingers ⇒
`euclid ≤ d_max ≤ 100 = 2 × reach_mm`, and longitudinal `dx ≤ euclid`. So while
`hand_span_mm == 2 × reach_mm`, **`d_max`-feasible implies reach-feasible at
every frame**: the intrinsic-span half of the reach check is dead, and `d_max`
is strictly the tighter of the two because it carries the across-string term
the window drops.

This is the opposite of the earlier guess, which was that reach bound first and
therefore blocked any `d_max` refinement.

**What the reach window does contribute is temporal**, not intrinsic: the
hand-centre interval propagated across time, through travel and sustained holds,
which `d_max` does not model at all. Every repertoire gain from opening `reach`
comes from that half.

## The reach frontier, measured on both sides

| `reach_mm` | gate (of 389) | negatives `{RED, AMBER, GREEN}` |
|---|---|---|
| **50.0 (shipped)** | **123** | `{1651, 61, 6}` |
| 52.5 | **125** | `{1651, 61, 6}` — bit-identical |
| **53.0** | — | `{1644, 62, 12}` — false certifications **double** |
| 55.0 | — | `{1644, 62, 12}` |
| 70.0 | — | `{1537, 167, 14}` |

**Corrected 2026-07-27**: this table first placed the cliff at 55.0, because 53.0
was never sampled. It is at or below **53.0** — the "free" setting at 52.5 sits
**0.5 mm, one percent**, below a doubling of the only false-accept evidence this
project has. Found by an agent re-checking the claim (which put it at 53.75, also
too high) and pinned by bisection. A frontier is only as trustworthy as its
sampling grid, and a coarse grid flatters whatever point you were hoping for.

`reach = 52.5` is free on the negative set. It is still **declined**, because
the `+2` net is `7 gained and 5 lost`: `carcassi-op59-prelude-10`,
`giuliani-op50n07`, `giuliani-op50n08`, `horetzky42-movement-1` and
`horetzky42-movement-2` stop being accepted. Losing five published scores that
currently work violates the no-regression guard, and the gain is not a better
model — it is beam churn. A wider window admits more per-frame configurations,
so the width-16 beam retains different, cheaper prefixes that later dead-end.

**Acceptance is not monotone in the model**, and that is the durable finding
here: three pieces accepted at `reach = 60` are lost again at `reach = 70`.
"Accepted at setting X" is partly a property of the search.

A methodology note worth keeping: the first measurement of this frontier
reported `+7` at 52.5 and a gate of 130. It was run over the 266 *failing*
pieces only, so it structurally could not observe the five regressions among the
123 that already passed. Independent re-measurement over the full corpus gave
125. Gains measured on a failing subset are not gate numbers.

## A correction to the earlier `d_max` result

The per-pair `d_max` work concluded that loosening `d_max` was inert. That holds
on the 58-score corpus and **not** on 389: removing `d_max` entirely buys about
7% of the failing pieces, and 38% need both limits raised together.

More usefully, at the 16 frames where `d_max` alone refuses, the binding finger
pair is **adjacent (gap 1, factor 0.5)** in 13 of 16 cases, and gap 3 in only 3.
The tight term is the adjacent-finger allowance — which is exactly why raising
(1,4) to 1.10 or even 1.50 moved nothing. The per-pair idea was not wrong; the
wrong pairs were tested.

That path is not free either: the negative set binds on adjacent pairs with
margins as small as 0.54 mm. It is left open, with the corpus now 6.7× larger
than when it was first attempted.

## What did ship

Nothing that changes a verdict. Two coherence repairs:

**`large@0.1` → `large@0.2`.** It shipped `hand_span_mm = 115` against
`reach_mm = 58`, i.e. `2 × reach = 116` — describing a hand a millimetre wider
than the one it claimed. `small` and `median` are exact (90/90, 100/100).
Measured to move **0 of 1,718** verdicts, because `d_max` is the tighter limit
either way, so this is a coherence repair with no behavioural claim.
`reach_mm` moved rather than `hand_span_mm` because tightening is the safe
direction for a verifier.

**`span_reach_inconsistency()` plus a test** holding every shipped profile and
both its transforms to `2 × reach_mm == hand_span_mm`. Deliberately *not* wired
into `validate_profile`: the mutation suite earns its keep by constructing
profiles that are incoherent on purpose, each neutralising one constraint so a
predicate can be shown to be load-bearing. Refusing to construct those would
trade a real test for a tidier invariant.

The dead intrinsic-span half of the reach check was **not** deleted. Its
deadness is conditional on other predicates holding — same-finger notes are kept
at one fret by the barre rule, not by `d_max` — so removing it from a verifier
would trade a corner case for a few lines. It is documented instead.

## Postscript: the adjacent pair, which is the one that paid — 2026-07-27

The correction above said the tight term is the adjacent-finger allowance, not
the 1–4 span that had been tried. Sweeping it on the full 389-score corpus, both
sides:

| table | repertoire | gained | lost | negatives `{RED, AMBER, GREEN}` |
|---|---|---|---|---|
| baseline | 123 | — | — | `{1651, 61, 6}` |
| **`(1,2)` → 0.55** | **125** | 2 | **0** | `{1650, 58, 10}` |
| `(1,2)` → 0.60 | 125 | 2 | 0 | `{1649, 59, 10}` |
| `(1,2)`+`(2,3)` → 0.55 | 124 | 2 | 1 | `{1650, 58, 10}` |
| `(1,2)`+`(2,3)` → 0.60 | 124 | 2 | 1 | `{1649, 49, 20}` |
| `(1,2)`+`(2,3)` → 0.70 | 126 | 5 | 2 | `{1642, 54, 22}` |

Touching `(2,3)` always costs a published score. `(1,2)` alone does not.

**The number that decided it was not the GREEN count.** Every earlier candidate
in this area was reported as "false certifications rose from 6 to N" and none of
them was ever traced. Tracing this one:

```
AMBER -> GREEN  4
RED   -> AMBER  1
RED   -> GREEN  0
```

Nothing the oracle had confidently refused became certified. The four extra
certifications came out of the band where it had already declined to commit,
which is a different act from overturning a refusal — and it is the distinction
that separates this from the profile-wide loosening declined in W6, where the
same summary statistic hid an unexamined mix.

Shipped as `oracle@0.4.0 → 0.5.0`: `d_max` becomes a per-pair table, with `(1,2)`
at 0.55 and every other pair unchanged. Repertoire 123 → **125** of 389, the
frozen 58-score subset unmoved at 26, mutation suite 14/14, 2,783 tests passing.

That is the entire harvest of this direction, and it is +2 of 264 remaining
failures. The ceiling is not in these constants.
