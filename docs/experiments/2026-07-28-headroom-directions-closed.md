# Four directions closed, and a population that was the wrong one — 2026-07-28

## The question

143 of 292 published scores accepted. Every one of the 292 is music guitarists
demonstrably play, so the ceiling is ~100% and each of the 149 refusals is a
defect somewhere. Where?

## What was closed

### 1. Beam retention, and the learned ranker built on it

Sixteen times the beam width with both budget knobs lifted past any possibility
of binding recovered **2 of 18** pieces that got a fair trial.

The budget lift is the point. A previous run of this measurement was invalid
because 25% of its sample aborted on the score-level segment budget rather than
on the search, and a budget abort counted as "the wide search found nothing".
This run reports the failure reason of every trial; budget aborts were **zero**,
so the trials were fair.

Beyond the rate, the speed is the tell. Six of the fourteen that still failed
died in under 21 seconds at beam 256 — one of them in 0.4 seconds. A frame that
refuses in 0.4 s at sixteen times the width is not an eviction.

**A reasoning error to record**, because it briefly closed the door on stronger
grounds than the evidence supports: "a wider beam subsumes what a ranker could
keep" is false. The beam retains by cost and diversity, so a completability
ranker could keep a state ranked 500th that beam 256 never holds. The width
experiment shows *more of the same kind of state* does not help. It does not
show *better-chosen states* would not.

### 2. Sustain relaxation

Of 18 pieces no amount of search reached, **4** solve once the accompaniment is
released at the next attack — and **0 of those 4** clear the shipped 0.90
retention floor. The lowest sits at **0.416**. Reaching them means letting go of
more than half the written note durations, which is not making a score playable;
it is writing a different score.

The other 14 fail even in a diagnostic arm that releases *every* note including
the melody — an illegal configuration used only as a ceiling. No sustain model
reaches them.

### 3. "Hand geometry" — a label mistaken for a finding

The sustain probe filed those 14 as `hand-geometry`, and that was reported as
"the wall is the profile". It was a **residual category** — "not fixed by
releasing sustain" — not a positive identification, and taking it as one was
wrong.

Attributing them properly, by asking whether the frame at the death onset has
any configuration at all:

| | pieces |
|---|---|
| **transition** — the frame is fine, the step into it is not | **12** |
| static — the frame cannot be fingered at all | 2 |

Death frames are tiny: six pieces die at a **2-note** frame, and several of those
frames offer 48 fingerings. The refusal was never the geometry of the frame.

### 4. Shift speed

This one looked like the find of the day. Dissecting the 12 transition deaths in
isolation named `SHIFT_SPEED` as the binding constraint on 4 of them, needing
533, 566, 568 and 680 mm/s against the profile's placeholder **500**. Three of
four within 14%. Published études, demanding a shift the model calls impossible.

The false-certification cost measured as **zero**: doubling the parameter to
1000 mm/s left the 1,718-tab negative multiset at RED 1650 / AMBER 58 / GREEN 10,
unmoved. Read the right way round, that says the guard is *insensitive* to this
parameter — it cannot object, which is not the same as approving.

Then the benefit was measured properly, on the train split, through the gate's
own escalation path:

```
v_shift 500   97/201 accepted
v_shift 600   97/201    gained 0   lost 0
v_shift 700   97/201    gained 0   lost 0
```

**Forty percent faster buys nothing.** The direction is dead, and this
measurement is the clean one in this document: correct solve path, proper split,
both directions counted.

## The error underneath

The four blocked pieces were then solved directly, and the result exposed a
defect in the whole chain above:

```
piece                      split      v=500     v=700    v=1000
air-varie-movement-2       train        @4        @4        @4
bwv-1006a-7g               train    SOLVED    SOLVED      @1/2
cc-by-sa-aguado-op11n09    train       @14       @14       @14
horetzky53-movement-1      train   TIMEOUT   TIMEOUT   TIMEOUT
```

`bwv-1006a-7g` **solves at baseline**. Probes 1 through 4 used plain
`solve_fingering_score` at the requested capo; the gate uses
`solve_fingering_score_with_escalation`, which tries a capo ladder and then
widens. So the population under analysis was "fails the plain solver" — a
superset of the gate's refusals containing pieces the shipped gate already
solves.

That is the same class of error the skill-registry ablation hit: measuring at
the wrong layer, so the thing being explained is not the thing that happens.

Two pieces also die at the *same onset* at 500, 700 and 1000 mm/s, which means
shift speed is not what blocks them in the real solve either. The `SHIFT_SPEED`
attribution was most likely an artifact of the two-frame probe, which set both
frames' durations to the gap between them and so changed the sustain structure
that the reachable-interval propagation depends on.

**What survives:** the shift-speed sweep (sound), and the non-monotonicity
evidence — `bwv-1006a-7g` *loses* at 1000 mm/s a solution it has at 500 and 700.
A looser hand model dropping a piece it used to solve is now a concrete
observation rather than a remembered one.

**What has to be redone:** the failure decomposition, against the gate's actual
refusals.

## Where this leaves the number

Four directions are closed and one analysis is invalidated, so 49% stands with
no cheap path off it identified. Three of the four were closed *before* anything
was built, which is the point of measuring first — the ranker alone would have
been weeks.

The standing constraint has not moved: five attempts to loosen the hand model
bought +2 pieces between them, every change that left the verifier alone was
free, and the profile those attempts were arguing about is still a placeholder
whose `calibration_status` says so. The repertoire is evidence about human hands,
but indirect evidence: that a guitarist plays a study does not establish what the
hypothetical median hand can do. That boundary still needs a human, and nothing
here changed it.
