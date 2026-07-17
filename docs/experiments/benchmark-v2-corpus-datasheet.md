# Benchmark v2 corpus datasheet

Date: 2026-07-17  
Status: Task 5 software corpus; no model outcomes have been collected

## Composition and intended use

The headline stratum is 500 independently seeded procedural families. The public
secondary stratum contains three pinned works: one OpenScore classical score and two
Mutopia MIDI files. Public and procedural results must be reported separately; the
three public works are controls, not a representative music benchmark.

The checked-in [source census](../../data/benchmark/source-census.json) is the source
of truth for URLs, retrieval date, upstream revisions, hashes, licenses, role maps,
normalization steps, inclusion decisions, and exclusions. `scripts/build_benchmark_corpus.py`
reads only that census and the local pinned cache. It does not fetch data.

| Layer | Included | Excluded or unavailable |
|---|---:|---:|
| A — public lead sheet | 0 | 1 unavailable layer record |
| B — public classical | 1 | 2 source records |
| C — public MIDI | 2 | 1 source record |
| D — checker-only tab | 0 formal proxy items | software fixtures continue in Task 6 |
| E — procedural | 500 default families | 0 |

## Public-source census

| Census record | Decision | License | Raw bytes | SHA-256 | Reason or explicit role map |
|---|---|---|---:|---|---|
| Mutopia Bach BWV 773, [piece 58](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=58) | Excluded | `CC-PDDC` | 5,366 | `b61e1e4d4a42ca70efd0eb8a65de3ed0ca0ee9c31556354608f6caa360e78e98` | Melody stream is polyphonic outside the frozen normalizer contract. |
| Mutopia Bach BWV 774, [piece 70](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=70) | Included | `CC-PDDC` | 4,231 | `602579a39b5b0e13ab4740dcff36342302553dcbd3619066e678f0074890c268` | `part-name:one → melody`; `part-name:two → bass`. |
| Mutopia Bach BWV 775, [piece 67](https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=67) | Included | `CC-PDDC` | 3,793 | `5ecc06eb04d56ea40bd4db64bfd49ac9bff523e46d134912fa5bd07aea1db1c7` | `part-name:one → melody`; `part-name:two → bass`. |
| OpenScore Lieder, Beethoven Op. 48 No. 5 | Included | `CC0-1.0` | 3,630 | `de665a004f7c55d30bf648b2b5ad17ab3afff16713a960f9441dce8ddce5b0ad` | `part:P2-Staff1 → harmony`; `part:P2-Staff2 → bass`; `part:Singstimme Voice → melody`. |
| OpenScore Lieder, Bizet Op. 21 No. 10 | Excluded | `CC0-1.0` | 7,912 | `5de9ea7c0572a5a0371c98e2ad778ac0cb01ed0fdcf821830de668dc296c0308` | Time-signature changes are outside the frozen normalizer contract. |
| OpenScore Lieder, Abbott, *Just for Today* | Excluded | `CC0-1.0` | 7,829 | `79a19736fcce71c0acf4b39feff63723d2ff75e29285ea2ab594f9b2b6a835ff` | Vocal stream is polyphonic outside the frozen normalizer contract. |
| Public lead-sheet layer | Unavailable | `NOASSERTION` | — | — | No audited candidate met the format and permission requirements without guessing. |

OpenScore evidence is frozen to repository commit
[`6b2dc542ce2e8aa4b78c8ee62103b210efc07015`](https://github.com/OpenScore/Lieder/commit/6b2dc542ce2e8aa4b78c8ee62103b210efc07015),
its [CC0 license](https://github.com/OpenScore/Lieder/blob/6b2dc542ce2e8aa4b78c8ee62103b210efc07015/LICENSE.txt),
and the [corpus license statement](https://github.com/OpenScore/Lieder/blob/6b2dc542ce2e8aa4b78c8ee62103b210efc07015/README.md#license-and-acknowledgement).
Mutopia's [public-domain terms](https://www.mutopiaproject.org/legal.html#publicdomain)
use the public-domain dedication/certification identified by
[`CC-PDDC`](https://spdx.org/licenses/CC-PDDC.html). The Allen Garvin attribution
strings retain source credit for traceability; they are not stated as license
conditions.

The lead-sheet exclusion considered OpenEWLD, Nottingham, and The Session. OpenEWLD's
[pinned license note](https://github.com/00sapo/OpenEWLD/blob/ec03cbd809ca5296ee708591b970d0423dcbe31c/README.md#licenses)
states an intention that the MusicXML content be public-domain but does not give the
required per-file grant; its [Zenodo record](https://zenodo.org/records/4332855) is
`other-open`. Nottingham's [official collection page](https://abc.sourceforge.net/NMD/)
notes third-party rights without a collection-wide redistribution license. The Session's
[terms](https://thesession.org/help/terms) do not grant the needed content redistribution,
and its [API](https://thesession.org/api) supplies melody ABC without the required
explicit harmony evidence. No hand-authored substitute was promoted as real data.

## Normalization contract

- Every source stream is exposed under a stable parser selector and the checked-in map
  must cover every selector exactly once. No part is chosen by pitch, order, density, or
  name semantics, and no chord is inferred from notes.
- MIDI parsing uses pinned `music21==10.5.0` with post-parse quantization disabled. Its
  explicit ties are coalesced, while total duration comes from the raw MIDI end-of-track
  tick span. The original PPQN timing is retained.
- MusicXML harmony annotations remain explicit chord symbols and are never expanded into
  sounding notes. MXL root bytes are selected through `mxl-container@0.1.0`.
- The explicit census dispatch and benchmark-only adapter are
  `benchmark-public-router@0.1.0` and `benchmark-public-adapter@0.1.0`. They do not widen
  `midi@0.1.0` or `musicxml@0.3.0`; both Mutopia files remain typed failures at the
  product MIDI importer because they contain multiple note-bearing streams.

## Licenses and provider use

An included public row requires explicit redistribution, derivative-work, and provider-
submission permission. The two included license expressions are `CC0-1.0` and
`CC-PDDC`; all three permission fields are `true` in the census. Ambiguous permission is
an exclusion, not an inferred public-domain claim. The procedural corpus uses
`LicenseRef-FretSure-Generated-Benchmark-v2` with the same three permissions explicitly
recorded.

Acquisition intentionally stays small: fixed HTTPS URLs, one timeout, per-file and total
byte caps, expected SHA-256 values, and a fresh output directory. It does not add runtime
Git checks or DNS, proxy, redirect, credential, or exclusive-file security machinery.

## Splits, contamination, and reporting

Each item has a unique family and cluster before assignment to the frozen `test` split.
The audit checks family split leakage, exact and 0.9-Dice near duplicates, transposition
and tempo variants, canary leakage, item overlap, and repeated producer/root bytes.
Findings and denominators are emitted separately for `real` and `procedural`; a separate
cross-stratum collision gate rejects musical overlap without creating a pooled score or
denominator. A non-clean report aborts publication.

The builder emits canonical `corpus.json`, `datasheet.json`, `source-census.json`,
`contamination.json`, and `receipt.json`. The receipt binds the corpus, census, included
source hashes, composition, and every non-receipt artifact hash. Two builds from the same
inputs must be byte-identical.

## Task 5 deterministic acceptance

Two default builds produced byte-identical artifact directories with 503 items: 500
procedural families and three public controls. The real, procedural, and cross-stratum
collision gates were all clean with zero findings. No provider or model call occurred.

- Domain-separated corpus SHA-256:
  `b4e2a1ed05eb07d82bdea18b9105cdd92b564cf864d8acedaa3c37d820848e8b`
- Domain-separated source-census SHA-256:
  `aa10f8d60b35d1c687806c0426bf50a2d30488d84b1f23317f72fc7dcceee372`
- Canonical `corpus.json` file SHA-256:
  `be32ceaf3abd0ad027667eb2dc78f08511f4f63bd78ac0e40f9d718dfead1f4c`
- Canonical `datasheet.json` file SHA-256:
  `88a3863c6c382b3348adbfc08bf23a9a8678e2be5a1a4584d021a4cd36990be8`
- Canonical `source-census.json` file SHA-256:
  `2c29a3ce7d4d528fecb854e585de44096531bff0c83cd8e7f7ca546fe6efd263`
- Canonical `contamination.json` file SHA-256:
  `64bcda562f72a0c7867b49521c2430e6be4ea15ab67fef39baba99ba913c75f5`

Task 7 will bind these inputs into the machine preregistration and runner-ready release
gate. This Task 5 receipt is a software/data acceptance result, not model-quality
evidence.

## Limitations

- Three public works cannot support a broad external-validity claim, genre comparison,
  or single pooled capability score.
- No public lead-sheet row met the license and evidence contract.
- Difficulty values are not human calibration, and no guitarist label is implied.
- Checker-only tab fixtures validate software behavior only; Task 6 freezes the later
  human-label contract.
- No model output, pilot result, or human outcome was used to select these sources,
  roles, exclusions, thresholds, or contamination rules.
