# The frame-config bucket, closed under the corrected oracle — 2026-07-31

## Why a third time

`no feasible frame config` is the one refusal that can be attributed: a beam
death is a search that wandered off, this fails at a single instant and the
refusing rule can be named. Both real oracle defects found this week were found
that way.

It was judged **genuine** on 2026-07-29 — geometry at a median margin of 26 mm,
which no defensible constant reaches. That verdict was measured against
`median@0.1`. Since then `hand_span_mm` went 100 → 130, `reach_mm` 50 → 65, and
the monotonic rule stopped refusing the wrist's slant. A 26 mm median against a
30 mm widening is exactly where an old verdict stops being safe to inherit.

## The answer

`oracle@0.8.0` / `median@0.3`, fixed capo, 149/292 accepted, 143 refused:

```
44 frames with no feasible configuration

    19  geometry                                        margin 3.4 / 21.3 / 67.2 mm
    18  no assignment puts them on distinct strings     an instrument fact
     4  the frame alone is fine — the refusal is history
     3  a barre crosses a string stopped behind it
```

The week's widenings closed nine of the twenty-eight geometry frames and moved
the median margin from 26 mm to **21.3 mm**. The minimum is 3.4 mm — one frame.

For scale: the neck-width floor, the largest justified constant change of the
week that was *not* a technique decision, moved a limit by 2.5 mm. The span
change that moved 30 mm was justified by naming the technique it models, not by
reaching for a margin. Nothing defensible reaches a median of 21 mm.

**So the bucket is closed, and now under an oracle that no longer carries the two
defects the previous verdict was measured with.** Twenty-five of the forty-four
are not hand-model questions at any margin.

## Corrected the same day: four of the forty-four are not real

A corpus audit written an hour later found **19 of 292 scores hold pitches no
guitar can sound** — thirteen entered at written pitch, an octave high, and five
in a tuning the corpus did not record. The gate reports only six of them as out
of range; the rest fail at whichever bucket they reach first, and four of them
land here.

Excluding those four, geometry is **17 frames, median still 21.3 mm**, min 3.4,
max 66.5. The two contaminated geometry frames sat at 3.4 and 67.2 — one at each
end, so the closure is unaffected. The other two were "no assignment puts them on
distinct strings", which is what an octave-high score looks like.

## What that leaves

```
292 scores
  19  hold pitches no guitar can sound          corpus defect, see the range audit
 149  accepted
 124  refused, of sound scores
        40  frame-config   →  17 geometry at a median of 21.3 mm, closed
        84  beam
```

Beam *width* is measured flat — 32 is worse than 16 and the bucket moves 93 → 94.
So the remaining headroom in this corpus is retention **policy**, or nothing.

## Three bugs in this instrument, in ten minutes

Every one was caught by the same question — *if this were correct, what would it
print?* — and every one made the physical model look like it had more headroom
than it has:

1. **Skipped frames with more than four fretted notes.** The rule limits distinct
   *frets*; a barre lets every note at one fret share a finger. This excluded
   exactly the barre shapes and reported fourteen of forty-four as needing a
   fifth finger, emptying the line the analysis exists to fill.
2. **Reported a zero-millimetre margin as geometry.** Zero past the span limit
   means the span is not what refused it — what is left in `assignment_valid` is
   the barre-occupancy clause, which has no millimetre margin at all. Three
   frames. Left as written, a reader concludes the limit is one adjustment from
   admitting them.
3. Inherited from the retracted first analysis and avoided here: counting frames
   that need more strings than the instrument has as physically impossible
   *music* rather than as an import or arrangement fact.

Both live ones are now regression tests. The instrument count for the week stands
at four defects in the discrimination measure and two here — which is the actual
argument for writing these as scripts with tests rather than as one-off analyses.

## Artifacts

`2026-07-31-gate-fixed-capo.json` is the first gate artifact that records its own
mode, beam, profile fingerprint and checker version. The three before it —
07-26, 07-27, 07-28 — are bare aggregates and cannot be attributed to a
configuration at all, which is how a capo-mode figure came to be quoted for a
fixed-capo one.
