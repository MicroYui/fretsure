# The engravers said which string, and the corpus threw it away — 2026-07-29

## The wall, named precisely

Every geometric conclusion drawn from the editorial fingerings this week has
needed the string assignment **inferred**, because the corpus annotations carry a
finger and nothing else:

```
corpus annotations carrying a finger : 4,531
corpus annotations carrying a string : 0
corpus annotations carrying a fret   : 0
```

A finger alone does not locate a note. `D4` with finger 3 could be the second
string at fret 3, the third at fret 7, the fourth at 12 or the fifth at 17, and
the geometry — the thing being tested — is completely different in each. So every
measurement had to pick one, and picking is where three of this week's wrong
results came from:

- the slant setback measured at 54.5 mm median, from lowest-position
  realisations where a fret is 35 mm, when the realisations actually admitted sit
  high on the neck where a fret is 14 mm;
- a per-finger ordering measured through `_admitted_realisation`, which applies
  the very rule under test, so no inversion could appear by construction;
- displaced negatives re-enumerated rather than pulled apart, three separate
  times, each making an impossible stretch look playable.

## The information exists and is being dropped

LilyPond states the string. `<g-1\2>` is *G, first finger, second string* — a
complete placement with nothing left to infer.

```
sources stating a string number   : 31 of 281
string indications typed          : 619
notes carrying BOTH finger and string : 169, across 16 sources
```

| source | complete placements |
|---|---|
| capricho-arabe.ly | 69 |
| faure_op78_sicilienne.ly | 17 |
| sorf_op35_no22.ly | 16 |
| tarrega_claro_de_luna.ly | 16 |
| aminor-study.ly | 12 |

None of it reaches the corpus. `_technical_values` in `score_corpus.py` does read
`<string>` from MusicXML and map it, so the loss is upstream of this repository —
in the pinned LilyPond-to-MusicXML converter, which cannot be run here because
`python-ly` is not installed and the converter itself lives outside the tree.

## Why 169 is worth more than its size suggests

It is not a larger sample; it is a *clean* one. One hundred and sixty-nine notes
whose position a human wrote down removes the inference step from the geometric
questions entirely — and the inference step is what has been generating wrong
answers, not the sample size.

The specific question waiting on it: the monotonic rule refuses the wrist's
slant, four bounds on the exemption all trade at the same rate, and the hand-plane
model does not dominate. Each of those conclusions rests on realisations this
repository chose. With the engraver's own string assignments, they can be checked
rather than argued.

## Two ways to get it, both real work

**Fix the converter path.** Install `python-ly`, obtain the pinned converter,
confirm whether it emits `<string>`, and if not, patch or replace it. This is the
right fix because it recovers the indications for every future import as well.

**Parse the sources directly.** Extract `<pitch-finger\string>` from the
LilyPond, which needs relative-octave resolution to turn a pitch into a fret.
Bypasses the converter and yields the 169 immediately, but is a second parser to
keep correct — and this project already carries three copies of one rule set and
has been bitten by them.

Either way this is the concrete unblock, and it is a data-recovery job rather
than a modelling one. That is a more comfortable place to be than the last four
attempts, which each needed a constant nobody could source.
