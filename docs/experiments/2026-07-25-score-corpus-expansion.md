# Licensed score-corpus expansion — 2026-07-25

## Decision

Use published, licensed fingerings as labels. Do not require a person to
re-finger or approve every score. Human effort is limited to source-rights
review, exception handling, and release spot checks; deterministic checks bind
the source, converter, normalized output, grouping, and label counts.

## Added corpus

The new Mutopia ShareAlike artifact contains 37 movements from 35 editions,
14,461 notes, 1,776 technical annotation rows, and 1,673 explicit pressed-finger
labels. Together with the existing Public Domain artifacts, the repository now
holds 58 normalized examples, 17,944 notes, 2,483 annotation rows, and 2,362
pressed-finger labels.

The new artifact SHA-256 is
`c53ae16e24ae2d512f1f7a6f72b225322135b10756a71ba2e45493ca63d5d6bb`.
Full attribution and per-source digests are in
[`../../data/score_corpus/SOURCES.md`](../../data/score_corpus/SOURCES.md).

## Expanded-ranker audit

The frozen production builder was also run with
`--include-mutopia-sharealike`, writing a separate candidate rather than
overwriting the shipped model. The candidate SHA-256 was
`8d34f774f4d0a8c605b798eddb6eb2966970b839405a1c1564d346e88d85fdd0`.

- Coverage rose from 351 windows / 639 scored labels to 1,035 windows / 2,080
  scored labels.
- The added editions came from four composers and one typesetter; only
  Dionisio Aguado added a new composer group.
- The composer-held-out development and test groups remained Brahms and Fauré.
- The expanded candidate abstained on every scored window: development stayed
  29/60 and test stayed 30/78, so it did not retain the frozen model's small
  held-out gains.
- Oracle-status regressions remained zero and Carcassi Prelude 1 remained
  16/21 within the builder's supported windows.

The expanded candidate is therefore not promoted. The larger corpus is kept as
licensed training material, while the production model remains the previously
accepted hash
`10bd1f9c2751417c5ef3a5f360da5696f736cc24db838857b9d2dd058b6cfed0`.
This result is evidence that more labels from the same few composers/editor do
not replace edition and style diversity.

## Style evidence

The separate GuitarSet aggregate uses 24 training, 6 development, and 6 test
accompaniment performances for each of Jazz and Funk. Jazz directly selects
the Jazz rhythm phases. The public R&B control uses Funk only as an explicitly
named adjacent proxy; it is not described as R&B score supervision. The frozen
aggregate hash is
`c1a57bb1aa4599594db83f5fb9074e96b53be83a03d1e306e38ea5cae7df342d`.

No GuitarSet JAMS or audio is redistributed. The derived aggregate records the
CC BY 4.0 attribution, archive digest, preprocessing, performer-disjoint split,
and train/dev/test phase distributions.
