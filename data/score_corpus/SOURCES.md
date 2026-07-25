# Score supervision sources

## Carcassi Op. 59 preludes

- Upstream: Mutopia Project,
  `ftp/CarcassiM/O59/CarcassiMethodPreludes/CarcassiMethodPreludes.ly`
- Upstream revision observed: Mutopia footer `2017/12/30-2209`
- Retrieved: 2026-07-25
- Source SHA-256:
  `b1f42476923d13fd7f849c2275fddd55f1af2833815f7f712719dda2b0175cce`
- Edition: G. C. Santisteban, Oliver Ditson Company, 1906
- Rights: Public Domain, as declared in the source header
- Local source: `sources/CarcassiMethodPreludes.ly`
- Normalized artifact: `carcassi_op59.json`
- Artifact SHA-256:
  `4f906864d68ed9272a4ed74be11743b080d2e5e8519e1125ffae9c8fd9363908`

The 16 printed preludes were converted once to MusicXML with `python-ly
0.9.10`, using the reviewed conversion workarounds from `HugoFara/graded-guitar`
at commit `6e0fbf4` (MIT). The seventeenth LilyPond score block is a combined MIDI
render and is intentionally excluded. The generated MusicXML is only an
intermediate normalization step; the checked-in artifact contains the exact
notes, explicit technical annotations, input-source digest, and per-movement
MusicXML root digest.

`python-ly` is not a Fretsure dependency and no LilyPond parser is shipped in
the product. Its GPL-licensed implementation is not copied into this
repository. The normalized musical source itself is Public Domain.

Corpus facts:

- 16 pieces
- 1,745 unique onset/pitch notes
- 451 explicit left-hand fingering annotations
- deterministic piece/edition groups: 11 train, 2 dev, 3 test
- Prelude 1's 21 labels exactly match the separately reviewed reference fixture

An omitted fingering is unlabelled. It is never interpreted as a negative
example or as approval of the solver's choice.

## Additional Public Domain Mutopia editions

`mutopia_pd_additional.json` adds five independently authored or transcribed
guitar editions. Each local LilyPond source declares `Public Domain`; each
normalized row retains the matching local-source SHA-256 and its own generated
MusicXML root digest.

| Work | Edition/typesetter | Source SHA-256 | Labels |
| --- | --- | --- | ---: |
| Brahms, Vals no. 3, Op. 39 | P. Bozzo, 2014 | `c78568fe5307043167c2bc18540dc5914d2c232c6ef44ce7dfab65b9e800c379` | 33 |
| Brahms, Vals no. 9, Op. 39 | P. Bozzo, 2014 | `ccda75a08824320aa1ef0fad466fd4a9d2766ae503b4df3ffcb01774abd0b63a` | 44 |
| Fauré, Sicilienne, Op. 78 | L. A. Morin / F. Bruni | `7de57964bd270daf5f3861cbe9f636a8dbadac5308dc37240b286f8b87e33960` | 100 |
| Mertz, Etude in A minor | Emre Akbas | `9a913b7b91c8c4e6a03cbf256042edd8f501a5b37b9c86c68e35aee53a5cd6f0` | 12 |
| Tárrega, Claro de Luna | F. Tárrega / Jeffrey Olson | `3ec506744202fa2571d1e72b7c8cd238b7cf45cbc88af4235b9e793d24c5386c` | 67 |

The artifact contains 5 pieces, 1,738 notes, and 256 explicit left-hand
annotations. Its SHA-256 is
`4bf36f7633693f4b02f19bccb1f8ccf704de47915bec2e4bab3f25fad7997e37`.
Only the first printed score block is used; duplicate MIDI/layout score blocks
are excluded.

## Mutopia Creative Commons Attribution-ShareAlike editions

`mutopia_cc_by_sa.json` adds 37 selected movements from 35 guitar editions
typeset by Glen Larsen. The source files declare either
[CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) or
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); every artifact
row retains its exact source URL, declaration, local source SHA-256, edition,
composer, generated MusicXML root SHA-256, and applicable SPDX identifier.

- Reviewed manifest: `mutopia_cc_by_sa_manifest.json`, SHA-256
  `cf9158fc4cb27856c25d692616c87287ffc2aa1cc7ff104f40c22fc73767623a`
- Local sources: `sources/mutopia_cc_by_sa/`
- Canonical artifact: `mutopia_cc_by_sa.json`, SHA-256
  `c53ae16e24ae2d512f1f7a6f72b225322135b10756a71ba2e45493ca63d5d6bb`
- Scope: 37 movements, 14,461 notes, 1,776 technical annotation rows, and
  1,673 rows containing an explicit pressed-finger label 1–4
- Editions: 27 source files under CC BY-SA 4.0 and 8 under CC BY-SA 3.0

The normalized rows are adaptations and retain the source row's ShareAlike
license. The repository does not relicense them as Public Domain. Missing
finger numbers remain unlabelled. No person or model supplied replacement
fingerings.

The sources were converted with `python-ly 0.9.10` and `lxml 5.3.0` through
the reviewed `HugoFara/graded-guitar` wrapper at commit `6e0fbf4` (MIT). The
manifest freezes the converter script digest and the expected root, note, and
annotation counts. The offline builder rejects source, licence, converter,
movement, or normalized-output drift. Duplicate print/MIDI score blocks are
excluded deterministically.
