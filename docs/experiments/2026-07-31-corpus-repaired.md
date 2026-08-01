# The corpus, repaired — 2026-07-31

The question was why a corpus of licensed classical guitar repertoire holds
pitches a guitar cannot sound. The repertoire was never the problem.
`build_mutopia_lilypond_corpus.py` reads notes and nothing else: no clef, no
`stringTunings`, no `\transposition`. Everything the page says about the
instrument is discarded.

All 281 sources are vendored under `data/score_corpus/sources/`, so the whole
question was answerable locally.

## What the sources say

**Two scores declare a tuning that was thrown away.** `capricho-arabe.ly` line
493 carries a live

```lilypond
stringTunings = #guitar-drop-d-tuning
```

and `faure_op78_sicilienne.ly` has the same line commented out beside a
`\markup { \circle 6 = D }` and the comment `% drop D tuning`. Both were recorded
as `[40, 45, 50, 55, 59, 64]`. Sweeping all 281 sources, those are the **only
two** that mention a non-standard tuning at all.

**The octave class cannot be decided from source metadata, and that was measured
rather than assumed.** `carcassi-op60-09.ly` says, in its own words:

```lilypond
\transposition c  % guitar music actually sounds an
                  % octave lower than written.
```

which looks conclusive until you count. `\transposition` affects LilyPond's MIDI
output, not its note names, and across the corpus:

```
  2  declares \transposition | pitches above the fretboard
 10  declares \transposition | pitches fit
 12  declares nothing        | pitches above the fretboard
122  declares nothing        | pitches fit
```

Neither necessary nor sufficient. Acting on it would have transposed ten correct
scores. The same is true of the octave clef: 124 of 125 scores carrying
`treble_8` or `G_8` are fine, but so are 122 that carry neither.

## The check that does come from the source

Twenty-seven scores carry printed **string numbers**, and a string number pins a
pitch to a 22-fret window. The numbering convention is not documented, so it was
calibrated rather than assumed — and it is **1 = lowest string**, the opposite of
the usual ①=high-E convention:

```
1 = highest string   consistent 383 / 476 = 80.5%
1 = lowest string    consistent 471 / 476 = 98.9%
```

Under the correct reading, `capricho-arabe` is **197/204 consistent as recorded**
and only 149/204 an octave down. So its pitches are right and its tuning is
wrong, confirmed from the score's own string marks and not from its range.

Five annotations across three scores still disagree, and the source shows why —
`carcassi-op60-10.ly` line 59:

```lilypond
<a cis,>8 <cis-4 e,-3\3> <cis e,\3> |
```

The `\3` belongs to `e,`, and the corpus attached it to `cis`. That is a chord
attachment bug in `python_ly_string_numbers.py`, not a pitch error.

## What was measured on the instrument

All thirteen octave candidates, as recorded and twelve semitones down:

```
bwv-1006a-1g            pitch 88 unreachable   ->  GREEN
carcassi-op60-09        pitch 87 unreachable   ->  GREEN
horetzky11              pitch 88 unreachable   ->  GREEN
horetzky35              beam death             ->  AMBER
giuliani-op50n12        beam death             ->  AMBER
(eight others stay refused, most changing bucket)
```

Zero of thirteen playable as recorded, five after the shift, three certified
outright. The corpus-wide acceptance rate is 51%, so 5/13 is the same order — and
these are the scores that were broken, not a random sample.

## The repair

`scripts/repair_corpus_pitches.py` holds an explicit table with the evidence
level attached to each entry, because the generator (`m1_lilypond.py`) is
upstream and not vendored: a rebuild would put every defect straight back.

```
 2  declared   the source says the tuning in LilyPond's own words
14  measured   impossible as recorded, exact an octave down
 2  inferred   the range needs a lowered sixth string, the source is silent
 1  quarantined
```

`aguado-op03n05` is **not** repaired. It has 23 notes of 345 an octave high,
scattered across the piece; a whole-score transform is wrong for it and selecting
notes by their range fits the repair to the symptom. It is named in the
quarantine list rather than filtered by a rule, and a test asserts it still fails
so the exemption cannot quietly become permanent.

The range audit goes **19 → 1**.

## The guard that never existed

`tests/test_corpus_fits_the_instrument.py` asserts producibility — do these notes
exist on this instrument — for every score, with the quarantine named in the
test. The corpus was rebuilt, deduplicated and re-attributed three times in July
and nobody had asked.

Two frozen corpus digests were re-frozen with the reason recorded.

## The gate, before and after

Same mode (fixed capo), same profile, `oracle@0.8.0` / `median@0.3`:

| | accepted | GREEN | AMBER | frame-config | beam | out of range |
|---|---|---|---|---|---|---|
| before the repair | 149 / 292 | 114 | 35 | 44 | 93 | 6 |
| **after** | **154 / 292 = 52.7%** | **117** | 37 | 46 | 91 | **1** |

Five pieces and three certifications, which is exactly the five that became
playable when shifted piece by piece — the whole-corpus number and the per-piece
experiment agree. "Out of range" falls to the one quarantined score.

Frame-config rises 44 → 46 while the total falls, because repaired pieces that
still fail now fail *inside* the model rather than off the end of the fretboard.
Re-attributed on the repaired corpus the bucket is unchanged in character: 20
geometry at a median margin of 21.8 mm, 18 that cannot occupy distinct strings, 5
history, 3 barre occupancy. The closure holds.

The frame-level metric moved the other way, 3.3% → 4.1% refused and separation
96.7 → 95.9 points, because three more editorial frames are now judged and the
scores under them are no longer an octave out. **The number got worse because it
is now measured on correct data**, which is the only direction that means
anything.

## What is still not known

Partial displacement that stays under fret 22 is invisible to the range audit,
and the obvious screen for it — an octave leap up and straight back — fires on
133 of 292 scores because that is what a guitar arpeggio does inside one voice
label. A clean corpus would score the same.

**The note-by-note comparison was attempted and abandoned.** python-ly is not in
this runtime, so a second extraction would have been independent by construction,
which is exactly what makes it worth having. A hand-written LilyPond subset
parser comparing *sets* of distinct pitches — robust to order, repeats and voice
interleaving — reached:

```
first version    18 / 271 scores identical
after two fixes  44 / 271 scores identical
```

Two real bugs were found and fixed along the way: octave resolution in
`elative` goes by **letter distance, not semitones** (`c`→`fis` and `c`→`ges`
are both a fourth on the staff but not in semitones), and `\key a \major` holds a
note name that is not a note. Sixteen percent agreement after that is still a
broken parser, not a corpus finding, and the diagnostic that settled it was the
tool contradicting itself: the report listed pitches in the low twenties for a
score whose blocks, printed one by one, contained none.

So it was deleted rather than shipped. Debugging a parser toward a finding is
unbounded work whose failure mode is a confident wrong answer, which is the one
this project has spent the week retracting. **The sound checks remain what they
were**: the range audit, which is arithmetic, and the printed string numbers,
which verify 27 scores at 98.9%.

The full escalation ceiling (capo ladder plus beam ladder) was started and killed
at 1.5 hours without completing; it would need re-running on the repaired corpus
regardless.
