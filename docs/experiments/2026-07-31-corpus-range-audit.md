# Nineteen scores the guitar cannot sound — 2026-07-31

## What it is

Guitar is notated an octave above sounding pitch. LilyPond note names *are*
sounding pitch, so a correct source needs no adjustment — but engravers routinely
type what they see and let `\clef "treble_8"` handle the display, and Mutopia
holds both conventions. `build_mutopia_lilypond_corpus.py` has no octave, clef or
transpose handling of any kind, so whichever a source used is what the corpus got.

**19 of 292 scores hold pitches no guitar can produce.** Not "a hand cannot reach
them" — the notes do not exist on the instrument.

```
  13  written pitch: fits exactly an octave down
   3  unrecorded tuning: sixth string down 2 (drop D)
   1  unrecorded tuning: sixth string down 1
   1  written pitch and sixth string down 2
   1  fits no octave or tuning -- not a guitar score
```

Nine of the thirteen have a range of exactly **52–88**: the open low E and a high
E, both an octave up. Among them is `spanish-romance`, which every guitarist plays
on the open sixth string.

## Why it matters more than nineteen pieces

Only **six** of the nineteen are reported as out of range. A score fails at
whichever problem it reaches first, so the rest are counted in buckets that were
supposed to be about the hand model and the search:

```
   9  no non-red extension within beam
   4  no feasible frame config
   6  pitch unreachable on this tuning/capo
```

So both buckets analysed this session were contaminated. The corrected picture:

```
292 scores
  19  hold pitches no guitar can sound
 149  accepted
 124  refused, of sound scores
        40  frame-config   →  17 geometry at a median of 21.3 mm, closed
        84  beam
```

The frame-config closure survives the correction — excluding the four bad scores
the median margin is unchanged at 21.3 mm, because the two contaminated geometry
frames sat at 3.4 and 67.2, one at each end.

## The evidence, and its limit

The sources for the affected scores are not vendored here, so the mechanism
cannot be confirmed from the score. It can be tested against the instrument:

```
carcassi-op60-09   as recorded  pitch 87 unreachable   an octave down  GREEN
horetzky11         as recorded  pitch 88 unreachable   an octave down  GREEN
spanish-romance    as recorded  beam death             an octave down  beam death
giuliani-op50n26   as recorded  frame-config           an octave down  beam death
```

Two of four go from impossible to certified by a pure octave shift. The other two
stay refused but **move buckets**, which is the contamination demonstrated
directly rather than argued.

That is strong evidence and not proof, so this reports rather than repairs.
Transposing a corpus on a hunch is how the first frame-config analysis came to be
retracted, and the repair differs by case: an octave for thirteen, a tuning for
five, and nothing for the transcription that is not guitar music.

## What is now permanent

`scripts/audit_corpus_range.py` classifies every score by which repair, if any,
would put it on the instrument, with five tests pinning the classification rather
than the counts — the counts change the day someone repairs the corpus and the
classification must not. The audit takes a second and had never existed; the
corpus has been rebuilt, deduplicated and re-attributed three times this month
without anyone asking whether its notes exist on a guitar.
