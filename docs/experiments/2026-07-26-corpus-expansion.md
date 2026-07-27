# Expanding the published score corpus — 2026-07-26

## Why

The repertoire milestone was measured against 58 published scores. That set was
never chosen; it is whatever the corpus pipeline happened to admit. Before
trusting `26/58` as a statement about real guitar repertoire, it is worth asking
what the pipeline was leaving out.

It was leaving out most of it. Mutopia's guitar catalogue holds **331
recognised solo scores** and the corpus used **39** of them.

## What shipped

| | before | after |
|---|---|---|
| examples | 58 | **389** |
| notes | 17,944 | 108,689 |
| fingering annotations | 2,483 | 5,443 |
| composers | 4 | 20+ |
| distinct engravers | **1** | 238 |

Three artifacts, split by licence family so the Apache-2.0 repository keeps
ShareAlike material identifiable:

| artifact | sources | examples | licence |
|---|---|---|---|
| `mutopia_expanded_public_domain.json` | 91 | 176 | Public Domain |
| `mutopia_expanded_share_alike.json` | 116 | 122 | CC-BY-SA 2.0/2.5/3.0/4.0 |
| `mutopia_expanded_permissive.json` | 33 | 33 | CC-BY-3.0 |

Attribution is generated from the manifests into `SOURCES_EXPANDED.md` rather
than maintained by hand, so the credit cannot drift from what was shipped.

## The result that matters

**Acceptance on the full corpus is 123/389 = 31.6%. On the frozen 58 it is
26/58 = 44.8%.**

The frozen subset is reported separately and permanently, so every measurement
taken during the milestone remains comparable. But the gap between the two
numbers is the finding: **the original 58 were an easier-than-average slice**,
and the honest acceptance rate on published guitar repertoire is thirteen
percentage points lower than the milestone measured.

The corpus expansion corrected an over-optimistic estimate on its first run.
That is worth more than the 331 additional scores.

## Three defects found on the way, two of which caused the bias

**A header-key assumption was excluding 192 scores.** LilyPond engravings from
the 2.x era write `copyright = "..."` where later ones write `license = "..."`.
`build_mutopia_lilypond_corpus.py` read only the newer name, so every older
engraving failed its licence cross-check and was dropped. Conversion success
went from 48 to 240 when this was fixed.

This is the origin of a bias the project had already noticed but attributed to
chance: the shipped ShareAlike corpus is 35 editions by a *single* typesetter.
That was not a sampling decision. It was this bug.

**A positive-count requirement was excluding unfingered scores.** The builder
demanded `annotation_count > 0`, so any score engraved without editorial
fingering could not enter the corpus at all — biasing it toward heavily edited
editions in exactly the way a repertoire corpus must not be biased. Zero is a
legitimate expected count and is now accepted.

**The converted-root digest is not reproducible across environments.** The
manifest pins `root_sha256` over the intermediate MusicXML bytes, and those
depend on the libxml2 build that lxml links against — not only on the pinned
`lxml==5.3.0` and `python-ly==0.9.10`. The shipped `mutopia_cc_by_sa.json` does
not rebuild here.

This was verified to predate any change in this work, by running the unmodified
builder from `HEAD`. It was also verified to be **cosmetic**: converting
`aguado-op11n01.ly` today and parsing it gives a note stream identical to the
shipped artifact note-for-note (332/332), with identical annotation and pressed
counts (19/19). Only the XML serialisation differs.

So the check is tighter than the property it protects. **Fixed on 2026-07-27**:
the manifest now binds on `content_sha256`, a digest over the notes, printed
fingering annotations, tuning, capo, time signature and tempo — everything a
rebuild must preserve, and nothing about how the XML happened to serialise.
`root_sha256` is gone from the manifest schema entirely (`@0.1.0` → `@0.2.0`):
for this corpus it was a digest of a *derived intermediate*, not provenance, and
nothing ever read it back. The previously-unvalidated `note_count` is now
checked too.

Result: all four artifacts (368 examples) rebuild and verify here, where
`mutopia_cc_by_sa.json` previously failed outright. Byte-identity across
environments remains unreachable because `ScoreCorpusExample` embeds the build
fingerprint, and that field is load-bearing provenance elsewhere in the project
(contamination detection uses it for imported sources). Content identity is the
guarantee, and it now holds anywhere.

## What did not convert

52 of 292 sources produced no usable example and are reported rather than
dropped quietly:

| reason | count |
|---|---|
| no movement converted and parsed | 50 |
| converter disagreed with the header licence | 1 |
| importer rejected the movement | 1 |

Three further sources were excluded during discovery because their licence
declaration could not be read exactly: two state nothing at all, one states
`Creative Commons \texttt{http://creativecommons.org}` with no version. These
are excluded rather than guessed. A fourth, spelling its licence `Creative
Commons BY-SA 2.5` instead of the usual wording, is admitted by exact match on
that second spelling — this widens the recognised set by one string, not by
fuzzy matching.

## Honest limits

- Still one source project, one genre, one instrument configuration. Mutopia is
  now nearly exhausted for solo guitar; further growth needs a different source.
- 31.6% is measured under `oracle@0.4.0` and `median@0.1`, which remains a
  placeholder hand model — see the milestone receipt for why loosening it was
  declined.
- The expansion adds positives only. The negative set is unchanged at 1,718
  raw-LLM tabs, so nothing here improves the measured false-accept rate.
