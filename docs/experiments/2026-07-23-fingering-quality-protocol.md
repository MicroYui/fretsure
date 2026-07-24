# Fingering-quality iteration protocol — 2026-07-23

## Purpose

Improve the general fingering quality of the bounded solver without encoding a
song, title, melody fingerprint, fixed fret cutoff, or other fixture-specific
rule. Oracle playability and pitch/rhythm preservation remain hard gates;
quality ranking only compares candidates that satisfy those gates.

`fret <= 5` for the public-domain *Frère Jacques / Two Tigers* fixture is a
regression observation, not a production feature, constraint, reward, or model
selection target.

## Frozen data roles

- GuitarSet, CC BY 4.0, Zenodo record 3371780:
  - performers `00..03`: train;
  - performer `04`: development;
  - performer `05`: internal test. Its `fingering-solver@0.3.0` baseline was
    recorded once after this split was frozen; it is sealed during feature,
    model, and hyperparameter selection and is rerun only after those choices
    are frozen.
- EGSet12, CC BY 4.0, Zenodo record 11406378:
  - all 12 performances: external audit set;
  - its first 16-note baseline windows were inspected before this protocol was
    frozen, so it is not claimed as a pristine confirmatory test for this
    iteration;
  - from this point it is sealed and is not used to choose features, weights,
    preprocessing, or stopping rules.

Every window inherits its source file's split. Source URL, license, file digest,
preprocessing configuration, profile fingerprint, and solver version are stored
with each report. Human string/fret choices are demonstrations, not assertions
that every different playable fingering is wrong.

Partial GuitarSet runs use a fixed performer-by-mode (`comp`/`solo`) round-robin
over lexically sorted filenames. This prevents a small run from accidentally
sampling only the first performer or only one playing mode. Full runs still
visit every source file exactly once.

## Dataset attribution and redistribution

- **GuitarSet** — Qingyang Xi, Rachel M. Bittner, Johan Pauwels, Xuzhou Ye,
  and Juan P. Bello; DOI `10.5281/zenodo.3371780`; [Zenodo record](https://zenodo.org/records/3371780);
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The checked-in
  experimental ranker is a derived artifact: the annotations were transformed
  into the fixed windows above and used to fit non-negative pairwise weights.
- **EGSet12** — Hegel Pedroza, Wallace Abreu, Ryan Corey, and Iran Roman;
  [Zenodo record](https://zenodo.org/records/11406378);
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). It was used only
  for the disclosed external engineering audit and did not update the weights.

No GuitarSet or EGSet12 audio, JAMS, Guitar Pro source, or performance payload
is redistributed by this repository. Only the small derived model artifact,
its provenance, aggregate reports, and reproducible evaluation code are kept.

## Versioned preprocessing

- Read only per-string `note_midi` annotations.
- Round measured GuitarSet pitch to the nearest MIDI semitone using exact
  half-up arithmetic, then derive fret from annotated string and standard
  tuning.
- Convert seconds to beats using the annotated tempo and quantize on the
  explicitly reported grid.
- Clip a note at the next attack on the same physical string.
- Never silently delete or repair an unrepresentable window. Duplicate
  onset/pitch targets, collapsed same-string attacks, oversized frames, and
  public solver preflight failures are reported as unsupported coverage.

The grid and window size are experiment configuration, not musical-quality
pass thresholds.

## Evaluation order

1. Record the deterministic `fingering-solver@0.2.0` baseline.
2. Collect every full-Oracle-GREEN finalist already admitted by the existing
   bounded final-check budget. Measure selected-versus-best-in-pool headroom.
3. If the useful human-like candidate was pruned before the pool, improve
   generic beam diversity before fitting a ranker.
4. If the pool has headroom, fit a small non-negative pairwise linear ranker on
   GuitarSet train, choose regularization on performer-grouped development, and
   evaluate internal test once the choice is frozen.
5. Re-evaluate the now-sealed EGSet12 audit only after the candidate generator,
   features, model, and report schema are frozen. A future unseen corpus or
   preregistered human trial is required for a genuinely confirmatory result.
6. Use blind human play/listening comparisons before treating higher imitation
   agreement as higher musical quality.

Reinforcement learning is deferred until supervised ranking has plateaued and
real human A/B preference labels exist. Any later policy may select only among
full-Oracle-GREEN bounded candidates; it may not emit unchecked tablature.

## Reported metrics

- representable/unsupported coverage and reason counts;
- GREEN, AMBER, and infeasible outcome counts;
- exact string, fret, and joint agreement with the demonstration;
- fret absolute error;
- maximum fret and duration-weighted fret exposure;
- shift count and minimum physical shift distance;
- runtime, pool size, candidate diversity, best-in-pool agreement, and selected
  regret when a candidate pool is available.

No single imitation metric is a correctness oracle. Iterations are compared as
continuous multi-metric results; an update must preserve the hard gates and
must not be dominated on the frozen development/test reports. No song-specific
numeric exception is permitted to rescue an otherwise regressing update.

## Production feature restrictions

Allowed features describe general physical or notational burden: continuous
position, position exposure, required hand travel, finger load, shape margin,
string travel, and local stability. Production ranking must not receive corpus
identity, source file, title, performer, genre, key, melody hash, fixture name,
or a hand-authored special fret range.

## Frozen ranker result and rollout lock

The first frozen model is the non-negative pairwise ranker with SHA-256
`b6cc57b0b55ed55f959d827e46276371e87820938c5678adf860ffa60f845315`.
Its GuitarSet performer-05 test report is byte-deterministic and has SHA-256
`fbca8f1d6f8a06e0fa63d905bdcff726fb72ed92bc8c4239ebada5a7d67b97f8`.
Across 36 windows with a full-GREEN pool, it changed joint string/fret agreement
from `26.1574%` to `43.8657%`, string distance from `734` to `554`, and fret
distance from `3398` to `2576`. Mean selected maximum fret changed from
`7.6389` to `8.1944`; therefore the unguarded model is not promoted directly.

The candidate production rollout was locked before the EGSet12 audit as follows:

1. Preserve the existing candidate generator, beam, and complete Oracle gate.
2. Find the legacy full-GREEN winner first.
3. Let the frozen ranker compare only full-GREEN finalists whose maximum fret is
   no greater than that legacy winner's maximum fret.
4. Keep the legacy winner if no eligible alternative exists.

This is a relative incumbent guard, not an absolute fret cutoff. It contains no
song identity or hand-authored fret number. On the frozen train/development
pools it preserves the incumbent mean maximum fret exactly while improving
joint agreement from `38.7931%` to `52.8736%` (train) and from `39.1667%` to
`47.5%` (development). It is explicitly a conservative first rollout, not the
final position model: a later version should replace it with a continuous
position-risk objective calibrated only on train/development and human A/B
preferences.

The guarded rule was selected after inspecting the unguarded internal-test
tradeoff, so neither a guarded rerun of performer 05 nor the previously
inspected EGSet12 set will be described as pristine confirmation. They are
engineering audits. A future preregistered human comparison or unseen corpus is
required for confirmatory evidence.

## EGSet12 audit outcome

The locked external engineering audit produced report SHA-256
`e3f4557f3f2d407662f6638081722da61f34b3a1782e4efc60eeae17304f8f8b`.
Six of twelve 24-note windows had a full-GREEN pool. The guarded model preserved
mean maximum fret (`4.3333`) but moved joint exact agreement from `65.9722%` to
`64.5833%`, string distance from `49` to `51`, and fret distance from `241` to
`251`. The guard activated on zero windows, so this is evidence against the
learned score itself rather than against the relative maximum-fret guard.

The learned selector is therefore **not** enabled in the production solver.
`fingering-solver@0.3.0` remains the production choice: it contains the generic
position/shift/open-string fixes and the hard GREEN-priority search, but not the
ranker. The model, manifest, evaluators, and audit remain as reproducible offline
experiments. A later model must improve its temporal/context features and pass a
new independent audit or blind human A/B comparison before promotion.
