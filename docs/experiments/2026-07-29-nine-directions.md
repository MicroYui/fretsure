# Nine directions measured, one defect fixed, one number moved by one — 2026-07-29

The question was whether 143 of 292 accepted published scores is too low. It is:
every one of the 292 is music guitarists demonstrably play, so the ceiling is
~100%. This records what was measured in answer, including the eight things that
turned out not to be the reason.

## The one real defect

`median@0.1` returned AMBER on a G major chord and `small@0.1` returned RED. The
outermost string centres sit 52.5 mm apart at one fret; `d_max` allowed the
(2, 3) and (3, 4) finger pairs 50.0 mm. Removing the single note that spans the
neck made the chord GREEN on every profile.

`oracle@0.6.0` floors `d_max` at the neck's width for every distinct-finger pair.
The justification is the instrument rather than the corpus, which is what makes
it different from the five earlier attempts in this area: those searched for a
constant that made more of the corpus pass, and bought +2 pieces between them.

**Repertoire moved 143 → 144.** That is not the case for the change and is not
presented as one. A fix chosen for the number would have been rejected, and the
verifier would still be unable to certify a G chord.

## Why nobody noticed

The guard that should have objected was scoring the other way. `replay_negative_tabs.py`
counts any drift toward GREEN over 1,718 raw-LLM tabs as the verifier weakening,
and those tabs are known-bad *by provenance* — a language model wrote them
without a solver, and no one had looked at one.

Rendering the eleven that move:

```
ten of eleven:   [3 0 x 0 x 3]   G B D G
                 fingers 2 and 3, strings 6 and 1, both at fret 3
                 along the neck 0.0 mm, across the strings 52.5 mm
the eleventh:    the same shape at the sixth fret
```

So eleven "false certifications" were G major chords, and that guard has been
vetoing improvements to this verifier for months. The expectation is now
re-derived rather than re-frozen: RED 1650 unchanged, eleven misclassifications
removed from AMBER by sight. The remaining 47 AMBER and 21 GREEN have still never
been inspected.

## What was closed

| direction | measurement | result |
|---|---|---|
| beam width | 16×, budgets lifted | 1 of 16 |
| retention quality | 40 randomised restarts each | 0 of 3 fair trials |
| sustain relaxation | accompaniment released | 4 of 15, **none above the 0.90 floor** |
| shift speed | 500 → 700 mm/s, train split | gained 0, lost 0 of 201 |
| frame-config generation | 48 → 384, ceilings lifted | 1 of 12 |
| a bigger hand span | inverted from editorial fingerings | required spans 60–259 mm; **RED degrades** |
| a lower retention floor | implied by editorial fingerings | min 0.955, **nothing near 0.90** |
| the mirror's conservatism | 4,000 generated tabs | **0 disagreements** |
| anisotropic span | on top of the floor | 0 editorial gain, **RED 1650 → 1648** |

Two of these deserve their own note.

**The retention floor.** 0.90 is a placeholder whose defence ("releasing
everything measures 0.729") argues only for "less than 1.0". The suspicion was
that we demand a literal sustain no guitarist gives. The corpus answers without
a player: a fingering is also a statement about release, since a finger asked for
elsewhere has left. Over 100 pieces carrying fingerings, the implied retention —
an upper bound — has a minimum of 0.955. **The engravers' own fingerings do not
support lowering the floor.**

**The mirror.** Its agreement tests are one-sided by design; conservatism is
licensed. Measured, the disagreement rate in the permitted direction is zero, so
the mirror is exact rather than merely safe, and this is now pinned. A first
attempt reported 30% — that was the measurement comparing against a subset of the
oracle that omitted the right-hand repeat rate, so the "disagreements" were
repeated open strings at 12 Hz against an 8 Hz limit.

## The import audit

Twelve refusals were already known to be import defects rather than hard music.
Auditing the rest, with every signature also measured on the accepted pieces so
that ordinary corpus texture does not become a finding:

| signature | refused | accepted |
|---|---|---|
| above the top fret | 15 (10.1%) | 0 |
| below the lowest open string | 5 (3.4%) | 0 |
| more sounding at once than strings | 3 (2.0%) | 0 |
| a voice overlapping itself | 134 (89.9%) | 101 (70.6%) |
| one onset far denser than the piece | 78 (52.3%) | 45 (31.5%) |

The last two are corpus texture, not defects: they are nearly as common among
pieces that solve.

The first three are exclusive to refusals. `capricho-arabe.ly` declares
`stringTunings = #guitar-drop-d-tuning` and the corpus records standard tuning;
four more pieces carry D2 or D#2, which cannot exist below a standard low E.
**Correcting the tuning does not make any of them solvable** — they fail for other
reasons afterwards — so this is a correctness fix worth making with zero
coverage attached, and it is recorded as such rather than as a win.

The fifteen above the top fret are a different thing: mostly one to three notes
of E6 in a piece, and `aguado-op03n05` reaches B6, which needs the 31st fret.
`capricho-arabe` uses `\harmonicsOn` with "harm. 7" and "harm. 12" markings, so
at least some of these are natural harmonics, which the importer does not model
and which are notated at sounding pitch.

## Where this leaves the number

144 of 292. Nine directions measured and closed, one real defect fixed, and no
cheap path off the number identified.

The strongest remaining candidate is the one the numbers keep pointing at
sideways: the negative guard still asserts provenance rather than playability.
Eleven of its tabs were chords, found by looking. Forty-seven AMBER and
twenty-one GREEN remain unlooked-at, and every measurement of "what does
loosening cost" is scored against them.
