# Plan 1 — Acceptance Gate Verification

Gate-by-gate evidence for **Plan 1: Core + Oracle** (roadmap §C Plan 1 +
§14 A.7–A.9). This is a historical acceptance record, updated here to make the
remaining external-validity gate explicit. After replaying the Oracle 0.2 trust gate
on the independently closed MusicXML-first tree, the current repository collects
1248 tests: 1242 offline tests and 6 integration tests requiring the local LLM
proxy. The smaller 91/103/264/445 counts mentioned in
review history were snapshots at the time of those reviews. Ruff and strict mypy are green. CI
(`.github/workflows/ci.yml`) runs `ruff check` + `mypy src` + `pytest -q` on
Python 3.11; no remote-run evidence is recorded here.

| # | Gate | Status | Evidence |
|---|------|--------|----------|
| 1 | `fretsure-oracle` pip-installable, `import fretsure.oracle` works, quality workflow present | ✅ current full-tree local package gate | The current wheel builds and installs in clean Python 3.11 venvs. Without the `musicxml` extra, core imports succeed and the importer returns typed `MISSING_DEPENDENCY`; installing `wheel[musicxml]` makes the packaged `fretsure-arrange` CLI run deterministically on the supported fixture. `.github/workflows/ci.yml` exists. No remote CI run is claimed here. |
| 2 | Deterministic; verdict stamped with checker/profile/input versions + profile fingerprint | ✅ | `tests/oracle/test_core.py::test_deterministic`, `::test_version_stamp`; current stamps are `oracle@0.2.0`, `tab-input@0.2.0`, and `median@0.1` plus its canonical SHA-256 fingerprint. |
| 3 | Span uses **mm**, not fret count; a test proves same fret-count span differs by position | ✅ | `test_csp.py::test_same_fret_span_different_position_differs`; `test_metamorphic.py::test_span_overage_eases_up_the_neck`. `geometry.fret_x(12)=L/2`. |
| 4 | monotone-in-resources property, ≥1000 random tabs, no reversal | ✅ | `test_property_monotone.py::test_monotone_in_resources` (1000 examples) + `::test_monotone_under_uniform_scale_up` (200). RED→AMBER→GREEN only. |
| 5 | Mutation kill-rate ≥ 0.9; N-version differential no divergence | ✅ | `test_mutation.py` (`12/12` mutants killed, kill_rate **1.0**); `test_csp.py::test_nversion_fast_matches_bruteforce` (500 random frames, fast DFS ≡ exhaustive). |
| 6 | GREEN false-accept Clopper–Pearson upper bound + confusion matrix (+ Wilson CI) | ⚠️ software/statistics gate complete; empirical gate OPEN | `stats.py`: typed/resource-bounded JSONL and in-memory rows, `confusion_from_labeled`, canonical GREEN false-accept result, Clopper–Pearson, κ and Wilson. Zero GREEN now returns `status="no_green"` with rate/bound `None`. `sample_labeled.jsonl` still has only 6 constructed rows; under MEDIAN it yields 3 GREEN/0 false accepts and a vacuous 97.5% upper bound ≈0.708. It is not human evidence. The real human TEST set is not collected or scheduled. |
| 7 | 3-preset (hand-size) sensitivity scan, judged monotone | ✅ | `test_stats.py::test_preset_sensitivity_green_count_monotone` (SMALL ≤ MEDIAN ≤ LARGE GREEN count). |
| 8 | Honest scope declaration (§14 A.9): simplified geometry + limited timing, techniques IN/OUT | ✅ | `docs/SCOPE.md`. |

## Remaining open gates

Gate 6's **real human gold test set** (sample size to be powered after a pilot,
including adversarial near-miss tabs) requires recruiting one or more real players;
it is not yet scheduled. It can run in parallel and does not block continued
software engineering. It does block a measured real-world GREEN false-accept rate,
profile/tier calibration, human-musicality evidence, and any stronger claim that a
real guitarist can necessarily play a GREEN result. Until then:

- The scoring primitives and loader boundary are unit-tested and resource-bounded.
  A sample with zero GREEN predictions has denominator zero and therefore provides
  **no evidence**; the canonical result now preserves that as `no_green`/`None`.
  What remains is extending the collection schema with player/session/instrument/
  tempo metadata and then collecting human labels—not another zero-GREEN software fix.
- The **soundness direction** invariant (more resources cannot make a verdict worse)
  is exercised over 1200 monotonic-property cases (1000 + 200) plus 500 separate
  metamorphic cases (250 + 250). As the
  independent review demonstrated, this invariant alone cannot rule out a
  model/implementation false GREEN and says nothing about agreement with real players.
- The absolute calibration numbers (`d_max`, `v_shift`, `r_max`, string spacing)
  are v1 placeholders, marked `CALIBRATION` in `geometry.py` / `profiles.py`.

## Current public boundary

The code/model acceptance now includes arbitrary public `Tab`, solver target and
`Profile` inputs. `tab-input@0.2.0` rejects invalid strings, non-positive durations,
malformed tuning/capo/profile, non-finite or non-built-in tempo, hostile duck types,
oversized JSON/Fractions/notes/frames, and over-budget solver searches before a
verdict or expensive search. Valid profile-relative out-of-range frets remain RED;
malformed inputs are typed errors. See `docs/SCOPE.md`.

`oracle@0.2.0` certifies only playability under the stamped model/profile.
`fidelity@0.2.0` is a separate source-faithfulness checker; an oracle GREEN may still
fail fidelity, and joint success requires both. Neither checker version substitutes
for the pending human calibration gate.

## Independent review

- Foundation layer (IR/Tab/geometry/Profile): reviewed by an independent Opus
  reviewer — SPEC ✅ / quality Approved; findings fixed (commit "fix: address
  foundation review").
- **Whole-branch final review (Opus, read-only): found 1 Critical + 2 Important
  soundness bugs — now fixed and regression-tested:**
  1. *Critical false GREEN* — a fretted note carrying `left_finger == 0` slipped
     past every finger-filtered left-hand predicate, certifying an impossible
     fret-1 + fret-20 double-stop as GREEN. Fixed by `check_wellformed`
     (`fret > 0` iff `left_finger > 0`) → `MALFORMED_FINGERING` → RED.
  2. *Capo range* — `check_range` bounded the capo-relative fret; a capo could
     push the absolute position off the neck end and still certify GREEN. Fixed
     to bound `capo + fret ≤ max_fret`.
  3. *Shift bridged by an open frame* — an open-only frame reset the hand
     position, hiding a too-fast shift. Fixed to carry the hand position across
     open frames.
  Regression tests: `test_core::test_malformed_fingering_is_red`,
  `::test_capo_past_neck_end_is_red`,
  `test_predicates_lh::test_range_absolute_position_with_capo_flagged` +
  `test_wellformed_*`, `test_predicates_temporal::test_shift_bridged_by_open_frame_still_charged`.
  The property/metamorphic generators now emit well-formed fingerings.
- **Re-review (after the fixes) found one residual of the same class — now
  fixed:** `check_wellformed` enforced the fret↔finger biconditional but not the
  finger *domain*, so `left_finger == 5` inflated `d_max` (`|1-5|/3·H > H`) and
  certified an unreachable fret-1↔fret-4 span GREEN. Extended `check_wellformed`
  to also reject `left_finger ∉ 0..4` and `right_finger ∉ {p,i,m,a}`, and made
  `check_right_hand` crash-safe on out-of-domain right fingers. Regression:
  `test_core::test_out_of_domain_finger_is_red`,
  `::test_invalid_right_finger_is_red_without_crashing`,
  `test_predicates_lh::test_wellformed_left_finger_out_of_domain_flagged`,
  `::test_wellformed_invalid_right_finger_flagged`.
- **Final code/model verdict: Ready; empirical calibration gate still open.** The reviewer's updated verdict was
  "Ready-with-minor-fixes" contingent on the finger-domain guard; that guard is
  now in place, re-verified (the finger-5 input reproduces as RED), 103 tests
  green **at that review snapshot**. The reviewer's "Nothing else … produces a
  false GREEN" assessment applied only to the normalized-input code/model scope
  reviewed at that historical time. Oracle 0.2 subsequently closed the arbitrary
  public-input software boundary; neither review establishes real-player validity.
- **Oracle 0.2 trust-gate re-review:** all sounding notes now participate in left-hand
  geometry; same-string duration overlap is typed; shift propagates a reachable
  hand-centre interval over fretted attack/release events with half-open intervals,
  release-before-attack ordering, no guide reset, and actual `reach_mm` consumption.
  Profile transforms/limits/fingerprints, exact built-in tempo, hostile gold rows,
  zero-GREEN semantics, and the bounded/non-complete solver were separately red-teamed.
  The former 39 s high-branching solver input is now rejected in preflight while the
  500-frame four-note pressure path remains admitted. No remaining software blocker
  was reported; positive compatibility evidence for a mainstream notation
  application and human empirical validation remain open.
- **Lesson (worth stating):** the monotone-in-resources property passed *over*
  the Critical bug (a false GREEN that is monotone in resources stays hidden).
  Monotonicity certifies the soundness *direction*, not end-to-end soundness —
  the human gold set, adversarial cases, public-input hardening, and independent
  review are all needed to close the remaining gap. This is exactly why the
  independent pass ran and why the empirical gate remains open.

## Reproduce

```bash
uv sync --extra dev
uv run pytest -q -m "not integration"   # 1242 passed, 6 deselected
uv run pytest --collect-only -q          # 1248 collected
uv run ruff check .                        # clean
uv run mypy src                            # clean (strict)
uv build
uv venv --python 3.11 /tmp/f
uv pip install --python /tmp/f/bin/python 'dist/fretsure_oracle-0.2.0-py3-none-any.whl[musicxml]'
```
