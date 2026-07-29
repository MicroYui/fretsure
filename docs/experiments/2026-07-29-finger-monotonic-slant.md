# The monotonic rule refuses the hand's slant — 2026-07-29

## Confirmed, not fixed

`check_finger_monotonic` requires finger order to follow fret order globally:

```python
if na.fret < nb.fret and na.left_finger > nb.left_finger:   # refused
```

The string is not consulted. On a guitar the wrist angles across the neck, so a
finger reaching toward the treble strings lands slightly nearer the nut — and
that shape is refused.

The clearest case has only two notes and only one sensible fingering. `G#2` with
finger 2, `D4` with finger 3, from the editor's own printed fingering:

```
s6f4g2  s2f3g3     RED  ['FINGER_MONOTONIC']   <- the natural fingering
s6f4g2  s3f7g3     RED  ['FRET_SPAN']
s6f4g2  s4f12g3    RED  ['FRET_SPAN', 'SHIFT_SPEED']
s6f4g2  s5f17g3    RED  ['FRET_SPAN', 'SHIFT_SPEED']
```

`G#2` exists at exactly one place in standard tuning, the sixth string at fret 4.
Every other realisation of `D4` puts it 7 to 17 frets away, so the span refuses
those on their own merits. The one shape a guitarist would use — middle finger on
the bass string, ring finger a fret back on the second string — is refused for
being out of finger order.

## The direction is real, and measured

Across every frame where an editor printed two or more fingers, taking the
lowest-position realisation of each, 63 pairs have a higher-numbered finger
nearer the nut. Split by direction:

| | pairs | setback, frets |
|---|---|---|
| **the hand's slant** — higher finger toward the trebles | **50 (79%)** | median 1–4, max 5 |
| the opposite cross — higher finger toward the bass | 13 (21%) | median 1–2, max 4 |

Four fifths of what editors write in inverted order is the slant. It does not
scale cleanly with how many strings are crossed, which rules out the tidy model
where the setback is proportional to the angle.

`FINGER_MONOTONIC` appears in **37 of the 39** refused editorial frames and is
the sole refusing rule in 9 of them. After the neck-width floor removed the span
defect, this is what is left holding the frame-level false-negative rate at
16.3%.

## Why it is not fixed here

Exempting the slant — refusing an inversion only when the higher finger is *not*
toward the trebles — was prototyped and measured on both sides:

| displacement | shipped | slant exempt | bounded to 2 frets |
|---|---|---|---|
| 0 frets (printed fingerings) | 16.3% refused | **12.2%** | 14.2% |
| 3 frets | 83.9% | 67.8% | 76.6% |
| **6 frets (185 mm)** | **99.6%** | **87.6%** | **90.0%** |
| 12 frets | 100% | 100% | 100% |

It buys ten editorial frames and costs a tenth of the far field: shapes stretched
185 mm apart go from almost always refused to admitted one time in ten. Bounding
the exemption to the setback editors actually write recovers only part of that,
and does not restore it.

On the negative tabs, RED goes 1650 → 1649 while **GREEN stays at 21**. That
distinction matters and I first got it wrong by treating any RED movement as
disqualifying: the tab in question moves to AMBER, so nothing new is certified.
Rendered, it is a diagonal — little finger on the first string at fret 6, ring
finger on the fifth at fret 7 — and it is RED for `SHIFT_SPEED` as well, which
the change does not touch.

So the disqualifier is not the negative set. It is the trade: **far-field
discrimination for zero-displacement accuracy**, and there is no argument that
the trade is worth making. Shipping it would be choosing the number this week
happens to be looking at.

## What would settle it

The exemption needs a bound with a source, and the corpus does not supply one —
the setback distribution is flat across string distances, so there is no angle to
read off. Either the geometry gets a real model of the hand's plane, in which
case the setback follows from it rather than being fitted, or this stays a named
defect.

It is worth naming precisely because the frame-level rate is what compounds: a
piece passes only if all ~150 of its frames do, so 0.49 = x^150 puts the required
per-frame accuracy at 99.5%. Two systematic per-frame defects have now been
found by asking what human fingerings the verifier refuses. One was fixable from
the instrument alone; this one is not.

## Postscript: four bounds, one exchange rate — 2026-07-29

The defect survived `oracle@0.7.0`. Of the 36 editorial frames still refused,
**35 involve `FINGER_MONOTONIC`** and 10 are refused by it alone, so it is
essentially the whole of the remaining 13.3%.

Four different ways of bounding the exemption were measured, on both span
metrics:

| bound | 0 frets | 6 frets |
|---|---|---|
| shipped `oracle@0.7.0` | 13.3% | 99.4% |
| exempt the slant entirely (Euclidean span) | 16.3 → 12.2 | 99.6 → 87.6 |
| exempt, bounded to 2 frets | → 14.2 | → 90.0 |
| exempt entirely (along-neck span) | 13.3 → 9.2 | 99.4 → 83.3 |
| exempt within a fingertip width, 25 mm | 13.3 → 12.2 | 99.4 → 94.3 |

Four unrelated bounds, and the exchange rate is the same each time. That
consistency is the finding: **the monotonic rule is not redundant with the span
rule.** It carries information the span rule cannot measure -- fingers cannot
pass through each other, which is not a distance -- so relaxing it loses
discrimination at a fixed rate no matter where the relaxation is drawn.

### A measurement I got wrong, and the correction

An earlier pass concluded the slant setbacks are large (median 54.5 mm, none
within a fingertip width) and used that to rule out the finger-width
explanation. That measurement chose the *lowest-position* realisation of each
frame, where a fret is 35 mm. The realisations actually admitted are often high
on the neck, where a fret is 14 mm:

```
fingers (2, 4)   sixth string fret 18 and fourth string fret 17
along the neck 14.0 mm       d_max(2,4) = 90.0 mm       span has no objection
```

Finger 4 one fret nearer the nut than finger 2, at the eighteenth fret, is an
inversion narrower than a fingertip. So the finger-width idea was right to test
and was tested wrongly the first time -- but the sweep above shows it does not
pay either, because a bound loose enough to admit these also admits the shapes
that should be refused.

### What would unblock it

Not another bound on the monotonic rule. The two rules need to become one
constraint on hand configuration, which is the hand-plane model measured in
`2026-07-29-hand-plane-viability.md` -- viable at 30 degrees of rotation and
about 16 mm of finger play, and requiring two parameters the corpus cannot
supply, because the setback distribution has no angle to read off.

The evidence base is also smaller than the frame count suggests. 249 editorial
frames reduce to **182 distinct shapes** corpus-wide; repeat expansion copies a
typed fingering into every pass. And the LilyPond sources hold no fingerings the
conversion dropped -- the corpus carries 1,439 against 550 typed, the difference
being those same repeats -- so there is nothing left to recover from the sources
already vendored.

More evidence therefore means more *editions*: other publishers, other editors,
fingering the same repertoire differently. That is a licensing and parsing
project rather than a modelling one, and it is the concrete unblock.
