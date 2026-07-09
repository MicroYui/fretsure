# Fretsure Oracle — Scope of Certification (honest claims)

The oracle makes a **narrow, bounded** claim. Read this before trusting a GREEN.

## What GREEN means

> Any tab Fretsure certifies **GREEN** is, under the published profile P (hand
> span H, reach R, shift speed v, right-hand rate r), the specified
> instrument/tuning/scale-length, and our documented **static hand-geometry
> model M**, playable at the notated tempo — with a **finger assignment that we
> exhibit** (not necessarily the most idiomatic one).

Two claims are kept separate:

- **Mathematical claim** — the decision procedure is *sound* on model M: if it
  says GREEN, a feasible fingering exists under M. Verified by property /
  metamorphic / mutation / N-version self-checks.
- **Empirical claim** — M is calibrated to real players. Verified by a
  human-played gold set; reported as the GREEN false-accept rate with a
  Clopper–Pearson upper bound + confusion matrix + Cohen's κ.

## Soundness direction

GREEN is the strictest verdict (passes the *pessimistic* profile), RED the
loosest (fails the *optimistic* profile), AMBER absorbs uncertainty in between.
**We never relax the GREEN threshold to reduce AMBER.** The trust metric is the
GREEN false-accept rate, reported with a one-sided confidence bound.

## In scope

- Left-hand geometry: millimetre fret spacing, pairwise fingertip reach (a CSP),
  finger–fret monotonicity, barre feasibility, string/tuning range.
- Right hand: p-i-m-a assignment, one-finger-per-string, ≤4 simultaneous
  plucks, single-finger repeat rate.
- Temporal: hand-shift speed (with guide-finger relaxation), sustain conflicts.
- Full parameterization by profile (hand span, reach, shift speed, repeat rate,
  scale length, capo, tuning, tier max fret).

## Out of scope (explicitly)

- **Only static geometry.** We model reach and shift kinematics, **not** tendon
  coupling, fatigue, or endurance. Fatigue is flagged, never certified.
- **Only the notated tempo.** No rubato/expressive-timing modeling.
- **Not "idiomatic".** We certify that *a* feasible fingering exists, not that it
  is the most natural one. Musicality is a separate (LLM critic) axis.
- **Profile-relative.** Claims hold only for players matching the published
  profile; users pick a hand size.
- **Audio transcription** is out of the guaranteed path (best-effort, v2).

## Techniques marked IN / OUT

Advanced techniques that change the geometry are **not silently GREEN**. Thumb-
over, tapping, bends, hybrid picking, and partial barres are marked AMBER or
unsupported until modeled — never certified GREEN by omission.

## Version stamping

Every verdict carries `checker_version` + `profile_version`. A claim is only
reproducible against the exact versions it was made under.
