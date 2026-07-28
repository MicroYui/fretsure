# The corpus was counting the same music twice — 2026-07-28

## How this started

A direct question: the corpus is three hundred published classical guitar
pieces, so why does the failure analysis say thirteen of them are *physically
impossible*?

It was the right question. A piece guitarists have played for two hundred years
is not impossible, and calling it that was a statement about the corpus dressed
up as a statement about the music. Three defects came out of following it, all
of them upstream of every measurement taken on this corpus.

## 1. 86 of 389 pieces were the same music twice

389 ids collapse to 303 distinct note streams.

The cause is a LilyPond convention. Mutopia sources conventionally end with two
`\score` blocks — one wrapping `\layout` for engraving, one wrapping `\midi` for
playback — around the same music. 92 of 240 vendored sources have that shape.
The converter reports each block as a movement, and the manifest builder
deduplicated on `root_sha256`, the digest of the intermediate MusicXML. The two
blocks serialise to different bytes, so the guard saw two movements where a
musician sees one piece, and they shipped as `-movement-1` and `-movement-2`.

Two things made this invisible:

- **A duplicate guard existed and was watching the wrong thing.** Byte identity
  of an intermediate artifact is not musical identity. `content_sha256` — a
  digest over notes, annotations and instrument setup — was already computed for
  every movement and written into the manifest. It just was not what the guard
  compared.
- **Id uniqueness held throughout.** The duplicates had distinct ids, so every
  test that checked ids passed straight through the defect.

The fix moves the identity into `fretsure.score_corpus.musical_identity`, shared
by the builder and by consumers, and verified byte-identical against all 368
movements the shipped manifests already pin — so no existing binding moved.

One duplicate was cross-artifact and no per-build check could have caught it:
`twominorpreludes` shipped in an expansion as a second copy of two Carcassi
op.59 preludes already in the frozen baseline. The builder now seeds its seen
set from the shipped corpus rather than only from its own run.

**Deduplication was resolved in the baseline's favour.** All 86 drops fall in
the two expanded artifacts; the 58 baseline scores are untouched, so every
measurement previously taken against them stays comparable.

## 2. `bass` did not mean bass

```python
if any(member.voice_id != primary_voice for member in members):
    voice = "bass"
```

Every note outside the engraver's primary voice was filed as bass. Classical
guitar is written `<< {upper} \\ {lower} >>`, often with three or four voices, so
inner parts were labelled as the bass line. Across the corpus that put chords in
a monophonic role at **2,867 onsets in 267 of 389 pieces** — one onset carried
seven simultaneous "bass" notes with six different written durations.

It cost twice, though one of the two costs is smaller than it first looks:

- `bass_preserved` is `_voice_recall(ir, tab, "bass")`, so it scored recall over
  a set that was not the bass line. `bass_root_accuracy` is **not** affected —
  it reads the lowest *sounding* pitch in the tab at each chord onset and never
  touches `note.voice` — and on this corpus it is vacuous anyway, since the score
  corpus carries no chord annotations at all.
- The sustain model gives `bass` a floor of half its written value that
  `harmony` does not get, so inner voices were held tighter than they needed to
  be. That floor exists so a chord's root is still sounding when the chord
  arrives, which is sound reasoning that silently depended on the labelled bass
  actually being the lowest note. Loosening it for inner voices is a plausible
  source of *additional accepted pieces*, not merely a correctness fix.

The correction is deliberately local: among the notes outside the primary voice
at one onset, the lowest is the bass and the rest are inner voices.

### The rule that was tempting and wrong

Deriving both outer voices from pitch — highest is melody, lowest is bass — is
the obvious move and it fails. Measured over the corpus it relabels 22% of all
notes, and two numbers say why:

```
bass -> melody                12,674 notes
onsets carrying no melody     12,018 -> 0
```

Guitar melody notes are held across onsets where only the accompaniment
attacks; those onsets legitimately have no melody, which `ir.py` already says
in as many words. Calling the highest note there "melody" invents a line the
engraver did not write, then pins it to its full written value in the sustain
model and scores it in melody-F1. The rule was rejected on that evidence and the
rejection is pinned by a test.

The existing corpus tests used a two-voice fixture, where "not the primary
voice" and "the bass line" coincide. They passed through the defect for as long
as it existed. The new tests use three voices.

## 3. The impossible onsets are misparsed, not impossible

Eleven pieces demand more simultaneous attacks than the guitar has strings, up
to eleven notes at one instant. Three lines of evidence say the import is wrong rather than the music:

- **Position.** They sit at the very first or very last onset of the piece.
- **Content.** They contain semitone clashes — `G3` with `G♯3`, a chromatic run
  `C5-C♯5-D5-E5` at one instant. Neither is a chord, and neither is a rolled
  chord either, since a roll still holds every note down.
- **Texture.** `carcassi-op60-06` carries exactly two notes at every onset in the
  piece and then eight at the last one.

That last piece pins it. Its source ends `<c, aes f>2` over `<f, c f,>2`, then
`<e c>2` over `<g e c>2` — at most six pitches, then five. The parse produced two
and then eight, so the second-to-last bar's chord landed on the final onset.

**The mechanism is not established, and two hypotheses were falsified:**

| hypothesis | test | result |
|---|---|---|
| dropped spacer rests (`s`) shift a voice | do affected sources use `s`? | 6 of the 8 with a matched source, against a base rate of 53% — no signal |
| onsets drift late | parsed length vs engraved barlines | swamped by repeat expansion; the worst overshoots are unaffected pieces |

The most likely site is `score_corpus.py`'s heuristic repair for a missing
`<backup>`, which only fires when the preceding voice filled its bar exactly —
so a bar the previous voice underfills would append the next voice rather than
placing it underneath. That is a hypothesis, not a finding: confirming it needs
the intermediate MusicXML, which requires the pinned external converter and
`python-ly`, neither available here.

Until it is settled, `parse_score_corpus_source` refuses a parse that asks for
more simultaneous attacks than the instrument has strings. Scoring a solver
against a score no instrument can play measures nothing, and keeping such a row
silently inflates the refusal count with failures that belong to the importer.

## What this invalidates

Less than it looked like it would, and the measurement says so.

Re-running the repertoire gate at each stage, `median@0.1` with the capo ladder:

| corpus | pieces | accepted | rate | GREEN |
|---|---|---|---|---|
| 389 rows (with duplicates) | 389 | 177 | 45.5% | 99 |
| deduplicated | 303 | 139 | 45.9% | 81 |
| **+ role split + quarantine** | **292** | **143** | **49.0%** | 83 |

Attributing the three steps separately:

- **Deduplication moved the rate 0.4 points.** Of the 86 duplicates, 38 were
  accepted and 48 refused — against a base rate of 45.5%, almost exactly
  representative. The inflated denominator was **not** inflating the headline
  number, and any suggestion that the published rates were badly wrong is not
  supported.
- **Quarantine moved it 1.7 points, all denominator.** The eleven removed rows
  ask for more simultaneous attacks than there are strings, so no assignment of
  distinct strings exists and every one of them was already a refusal. Removing
  known-misparsed refusals raises a rate without improving anything.
- **The role split accepted 4 more pieces**, 139 to 143 over the same 292. That
  one is a real gain: inner voices previously carried the bass floor of half
  their written value and now carry `harmony`'s freedom, so the sustain ladder
  has somewhere to go. **It cost nothing in the verifier** — not one oracle rule
  or constant moved — which puts it in the same category as the capo ladder
  rather than in the trade-offs that bought +2 across five attempts.

Beam deaths fall 106 → 99 and no-feasible-frame 52 → 46, both partly from the
removed rows and partly from the four newly solved.

What the defects did invalidate is narrower and still real:

- **The size of the corpus.** 292 pieces, not 389. Any claim about breadth of
  coverage counted 86 pieces twice and 11 that were never parsed correctly.
- **The grouped split.** Identical music could land in train and in test, which
  is a leakage path for anything trained on this corpus.
- **Per-piece attribution.** Every count of the form "N pieces fail for reason
  R" double-counted whichever of them were duplicated.

The increments survive as counts, since a duplicate appears on both sides of a
before/after comparison: the capo ladder's +52, the per-pair `d_max` +2 and the
beam's +4 are unaffected in kind, though each would now be quoted over a
smaller corpus.

**The baseline is 56, not 58.** No duplicate was removed from it, but two of the
quarantined rows were baseline pieces. Both were refused in every historical
run, so the accepted count is unchanged at 26; it is the denominator that was
two larger than the music justified.

Specifically retracted: the conclusion in
`2026-07-28-no-feasible-frame-config.md` that "13 are physically impossible" and
that the bucket is therefore genuine with no cheap win in it. Eleven rows are
import defects, and the bucket has not been re-attributed on a clean corpus yet.

Untouched: the oracle's verdicts, which do not depend on the corpus, and the
1,718 raw-LLM tab invariance guard.

## What the beam measurement was really saying

Running alongside this, a pre-registered gating test asked whether a learned
completability ranker for beam retention was worth building: if retention is the
bottleneck, a very wide beam should recover most of the 40 beam-death pieces.

Beam 128 recovered 1. Beam 256 recovered 0 — but **25% of that sample hit the
score-level segment budget instead of failing the search**, so a quarter of it
never got a fair trial and the measurement does not support its conclusion
either way. It has to be redone with the budget raised, on the corrected corpus,
and it is not evidence for anything until then.
