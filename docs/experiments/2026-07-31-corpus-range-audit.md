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

## The mechanism, proven for the tuning case

`faure_op78_sicilienne.ly` is one of eight sources vendored locally, and it is
one of the nineteen. Its source says, in two places:

```lilypond
piece = \markup { \circle 6 = D }          % drop D tuning
%        stringTunings = #guitar-drop-d-tuning
```

The tuning is declared in a `\markup` for the reader and in a **commented-out**
`stringTunings` line. The corpus recorded `[40, 45, 50, 55, 59, 64]` — standard.

So for this class the answer is not inference: **the score says what instrument
it is for, and the converter reads only notes.** Everything on the page about the
instrument — clef, tuning markup, string indications — is discarded.

## A third case, and the one that can hide

`aguado-op03n05` is not "not a guitar score". It has **23 notes of 345** an octave
above where they belong, spread over 23 different onsets from beat 72 to beat
589, while the other 322 are ordinary. Shifting only those down twelve puts them
at 76–86, the top of the fretboard.

Partial displacement only announces itself when a stray note clears fret 22. The
same defect in a lower-lying score passes silently, with wrong notes, into every
measurement.

I tried to screen for it — a note that leaps up an octave and straight back —
and it fires on **133 of 292 scores**, because that is exactly what a guitar
arpeggio does inside one voice label: bass, melody, back down. `spanish-romance`
scores 22. A clean corpus would score the same, so the screen measures texture
rather than defect and is not used. **Finding the hidden ones needs the sources.**

Two limits are recorded in the script rather than smoothed over: a score whose
whole range also fits an octave down is reported as written pitch even if only
some notes are displaced, since only a score reaching the open low E
disambiguates itself.

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
