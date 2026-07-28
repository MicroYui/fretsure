# The verifier refuses fingerings that editors printed — 2026-07-29

## Five directions closed, then one opened

143 of 292 published scores are accepted. Every one of the 292 is music
guitarists demonstrably play, so the ceiling is ~100% and each of the 149
refusals is a defect somewhere. Five candidate explanations were measured, and
each was closed:

| direction | measurement | result |
|---|---|---|
| beam width (retention *quantity*) | 16× width, budgets lifted | **1 of 16** |
| retention *quality* | 40 randomised-retention restarts each | **0 of 3** fair trials |
| sustain relaxation | accompaniment released to the next attack | 4 of 15, **0 above the 0.90 floor** |
| shift speed | 500 → 700 mm/s on the train split | **gained 0, lost 0** of 201 |
| frame-config generation width | 48 → 384, work ceiling lifted | **1 of 12** |

Three of the five were closed before anything was built. The learned
completability ranker alone would have been weeks of work aimed at a bottleneck
that measurement says is not there.

## What the printed fingerings say

Forty-nine of the 149 refused pieces carry editorial fingerings — 2,653 labels
naming which left-hand finger a publisher's editor assigned to a note. That is
ground truth about what a hand does, produced by people who play, sitting in the
repository unused.

The labels give the finger only, never string or fret, so the editor's frame
cannot be rebuilt exactly. It does not need to be. Pitch and tuning admit only a
few (string, fret) choices per note, so the question is whether **any**
realisation consistent with the editor's fingers survives the oracle.

Over the 144 frames where an editor named two or more fingers:

```
editor's fingering admitted            103
editor's fingering refused              41   (28.5%)
    attributable to the sustain reading  1
    attributable to the hand model      40
```

Filtering the 40 for things that are not the oracle's fault:

```
 30  distinct fingers, pitches in range   <- a real test, and it fails
  9  the same finger on two pitches at one onset (a barre, or a label
     attached to the wrong note by the annotation extractor)
  1  a pitch below the lowest open string (scordatura the corpus records
     as standard tuning -- the same defect class as `pitch 39 unreachable`)
```

**Thirty frames across fifteen pieces admit no realisation of a fingering a human
editor put in print.**

## What this is and is not

It is the first evidence in this project that the oracle produces false
negatives, sourced from humans rather than from a parameter sweep. It is the
`§14 A.8` question — who checks the checker — answered for the first time with
data that already existed.

It is not a clean 28.5% false-negative rate, and quoting it as one would be
overclaiming twice over:

- **The label extractor has a demonstrated error rate.** Nine of the forty
  attached the same finger to two pitches at one onset, which is either a barre
  the annotation cannot express or a label on the wrong note. If that failure
  mode is 9 of 40 where it is visible, some fraction of the surviving 30 is
  likely the same thing where it is not.
- **A frame is only as honest as our reading of what sounds in it.** The test
  controls for this by re-checking with attacked notes only, and exactly one
  refusal disappeared, so the sustain reading is not the explanation here. But
  it does mean the 30 depend on the IR being right about simultaneity.

So the honest claim is: **at least 30 frames in 15 published pieces are refused
under every reading consistent with what an editor printed, and that number is
an upper bound on how many are genuine.**

## Why this outranks the parameter sweeps

Every previous attempt to move the hand model was a search for a constant that
made more of the corpus pass — five attempts, +2 pieces between them, each
costing false certifications. That is fitting a placeholder to an evaluation set,
and it is why the results were poor and why the profile still says
`placeholder_pending_human_calibration`.

The editorial fingerings are different in kind. They do not say "loosen until the
corpus passes"; they name a specific frame, a specific set of fingers, and a
specific human who committed to it in print. Each one is a falsifiable claim
about a rule. Thirty of them is a calibration target with provenance, not a
sweep.

The corpus also has a grouped train/dev/test split, which today's deduplication
made trustworthy — 86 pieces had been present twice and could land on both sides
of it. Any calibration derived from these frames can therefore be chosen on
train and reported on test, which is the difference between a number that can be
published and one that cannot.

## What has not moved

The negative set is insensitive to at least one parameter: doubling shift speed
to 1000 mm/s left the 1,718-tab multiset at RED 1650 / AMBER 58 / GREEN 10
exactly. Read correctly that is a warning, not a licence — the guard could not
object, which is not the same as approving, and those tabs are known-bad by
provenance rather than by anyone having checked them.

Non-monotonicity is now a concrete observation rather than a remembered one:
`bwv-1006a-7g` **loses** at 1000 mm/s a solution it has at 500 and 700. A looser
hand model does not monotonically accept more.

## A methodological error worth keeping

Probes 1 through 4 of this investigation analysed pieces that fail plain
`solve_fingering_score`, while the gate uses
`solve_fingering_score_with_escalation` — capo ladder, then widening. Twenty
percent of that sample (4 of 20) were pieces the shipped gate already solves.

Every conclusion was recomputed on the true refusals and every one survived, most
of them strengthened: wide-beam recovery fell from 2/18 to **1/16**, the
transition-versus-static split went from 12:2 to **10:1**. But the error is the
same class as the skill-registry ablation — measuring at the wrong layer, so the
thing being explained is not the thing that happens — and it was caught only
because a piece that "failed" turned out to solve.
