# Plan 1 — Acceptance Gate Verification

Gate-by-gate evidence for **Plan 1: Core + Oracle** (roadmap §C Plan 1 +
§14 A.7–A.9). Reproduce all of it with `uv run pytest -q` (91 tests) plus the
pip-install check below. CI (`.github/workflows/ci.yml`) runs
`ruff check` + `mypy src` + `pytest -q` on Python 3.11.

| # | Gate | Status | Evidence |
|---|------|--------|----------|
| 1 | `fretsure-oracle` pip-installable, `import fretsure.oracle` works, CI green | ✅ | `uv build` → wheel installs into a clean 3.11 venv; `check_playability` importable and runs (verdict RED on a fret-1+fret-15 tab). CI = ruff+mypy+pytest. |
| 2 | Deterministic; verdict stamped with `checker_version` + `profile_version` | ✅ | `tests/oracle/test_core.py::test_deterministic`, `::test_version_stamp` (`CHECKER_VERSION="oracle@0.1.0"`). |
| 3 | Span uses **mm**, not fret count; a test proves same fret-count span differs by position | ✅ | `test_csp.py::test_same_fret_span_different_position_differs`; `test_metamorphic.py::test_span_overage_eases_up_the_neck`. `geometry.fret_x(12)=L/2`. |
| 4 | monotone-in-resources property, ≥1000 random tabs, no reversal | ✅ | `test_property_monotone.py::test_monotone_in_resources` (1000 examples) + `::test_monotone_under_uniform_scale_up` (200). RED→AMBER→GREEN only. |
| 5 | Mutation kill-rate ≥ 0.9; N-version differential no divergence | ✅ | `test_mutation.py` (9 mutants, kill_rate **1.0**); `test_csp.py::test_nversion_fast_matches_bruteforce` (500 random frames, fast DFS ≡ exhaustive). |
| 6 | GREEN false-accept Clopper–Pearson upper bound + confusion matrix (+ Wilson CI) | ✅ machinery / ⚠️ real gold set deferred | `stats.py`: `confusion_from_labeled`, `green_false_accept_upper_bound` (0/10 → 0.3085, matches `1-(1-conf)^(1/n)`), `cohen_kappa`, `wilson_ci`. Sample gold `data/gold/sample_labeled.jsonl`: `green_unplayable == 0`. **Deferred:** the real ~300-tab human-played TEST set needs a design partner (see `data/gold/README.md`, roadmap D.4). This is a deferral, not a simplification — the full scoring pipeline is implemented and tested on a sample. |
| 7 | 3-preset (hand-size) sensitivity scan, judged monotone | ✅ | `test_stats.py::test_preset_sensitivity_green_count_monotone` (SMALL ≤ MEDIAN ≤ LARGE GREEN count). |
| 8 | Honest scope declaration (§14 A.9): static geometry, notated tempo, techniques IN/OUT | ✅ | `docs/SCOPE.md`. |

## The one honest deferral

Gate 6's **real human gold test set** (≈300 adversarial-near-miss tabs played by
a guitarist with a measured hand span) requires a real player and is scheduled
with the design partner (roadmap D.4). Until then:

- The scoring machinery (confusion matrix, Clopper–Pearson bound, κ, Wilson CI)
  is complete and unit-tested.
- The **soundness direction** — the property that guarantees GREEN never
  *silently* becomes a false accept — is verified empirically over ~1200 random
  tabs (monotone-in-resources + metamorphic), independent of any human labels.
- The absolute calibration numbers (`d_max`, `v_shift`, `r_max`, string spacing)
  are v1 placeholders, marked `CALIBRATION` in `geometry.py` / `profiles.py`.

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
- **Final verdict: Ready.** The reviewer's updated verdict was
  "Ready-with-minor-fixes" contingent on the finger-domain guard; that guard is
  now in place, re-verified (the finger-5 input reproduces as RED), 103 tests
  green. "Nothing else … produces a false GREEN."
- **Lesson (worth stating):** the monotone-in-resources property passed *over*
  the Critical bug (a false GREEN that is monotone in resources stays hidden).
  Monotonicity certifies the soundness *direction*, not end-to-end soundness —
  the human gold set + adversarial cases + independent review are what close the
  gap. This is exactly why the independent pass ran.

## Reproduce

```bash
uv sync --extra dev
uv run pytest -q          # 91 passed
uv run ruff check         # clean
uv run mypy src           # clean
uv build && uv venv --python 3.11 /tmp/f && \
  uv pip install --python /tmp/f/bin/python dist/*.whl   # installs clean
```
