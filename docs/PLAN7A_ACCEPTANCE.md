# Plan 7A acceptance — product controls and editing loop

Status on 2026-07-25: **CLOSED — SOFTWARE, LIVE HTTP, AND PRODUCTION CHROME
VISUAL/INTERACTION GATES PASSED**.

Historical note: Plan 7B subsequently advanced the production fingering/score
solver stamps to `0.6.0` / `0.4.0`; the versions below are this receipt's frozen
Plan 7A state.

Plan 7A completes the controls and editing loop while freezing the current
playability behavior as the regression baseline. It does not claim that style
likeness, human feel, ergonomic weights, difficulty tiers, or player presets
have been calibrated by a representative population of guitarists.

## Accepted software surface

- Four versioned arrangement intents are public: Fingerstyle, Classical, Jazz,
  and R&B (`arrangement-style-registry@0.1.0`). The deterministic offline path
  gives them distinct sparse rhythms; the proxy prompt receives the same
  versioned intent. Every produced Tab still passes the unchanged Oracle gate.
- Small, median, and large player profiles select a physical model. Balanced,
  avoid-barres, low-position, and fewer-shifts preferences rank only candidates
  that already passed the full Oracle (`technique-profile-registry@0.1.0`).
  These controls are independent of beginner/intermediate/advanced score
  difficulty.
- Beginner/intermediate/advanced is now a typed arrangement input, not only a
  result-page filter. The offline proposer changes optional texture density,
  the proxy prompt receives the same target, and the response/trace bind that
  value. The selected checkpoint is still checked independently, while manual
  finger-number edits do not receive or consult the difficulty target.
- A selected range of at most 32 measures can be regenerated with melody,
  bass, and harmony voice locks. Notes outside the range and locked voices are
  preserved; source-melody attacks are restored even when melody is unlocked.
  The offline path performs zero model calls, the proxy path at most one, and
  any non-GREEN or failed-faithfulness proposal preserves the prior checkpoint.
- A fretted note's left-hand finger can be changed manually without changing
  its string or fret. The complete Tab is rechecked transactionally; GREEN is
  applied, AMBER/RED is rejected, and difficulty tier is not consulted.
- Ratings, blind A/B preferences, and fingering corrections are stored as
  anonymous local evidence under `fretsure-feedback@0.1.0` and can be exported
  as JSON. New events bind the source/model plus style, difficulty, player, and
  technique controls. API calls and local storage are not described as model
  training.
- The result workspace now opens on the selected checkpoint. A rejected trial
  remains visible as evidence but is never the default score, playback, or
  export source.

## Versioned contracts

- HTTP API: `fretsure-api@0.3.0`
- application service: `fretsure-service@0.3.0`
- Web: `fretsure-web@0.3.0`
- public trace: `agent-trace@0.3.0`
- profile registry: `profile-registry@0.2.0`
- fingering / score solvers: `fingering-solver@0.5.0` /
  `score-solver@0.3.0`
- editable target / section revision: `editable-arrangement-target@0.1.0` /
  `section-regeneration@0.1.0`

MCP remains `fretsure-mcp@0.2.0`; Plan 7A does not register partial MCP editing
tools.

## Evidence collected

- Impacted Python contract, service, solver, API, serializer, trace, and
  compatibility suite: 350 tests passed before the final style-pitch regression
  adjustment; the final directly intersecting gate then passed 297 tests.
- Final style/revision/technique focused gate: 6 tests passed; Ruff and mypy
  passed. The only Python warning is the known third-party Starlette/httpx2
  deprecation.
- Web TypeScript check and production build passed; 67 Web tests passed.
- The added generation-difficulty seam passed 177 directly intersecting Python
  tests; its focused proxy-prompt and offline-density tests use no real model.
- The final completion audit found and fixed one fallback-only omission: when
  every proxy candidate returned no Tab, the zero-call deterministic baseline
  now explicitly inherits the requested difficulty tier instead of using the
  intermediate default. Its existing fallback test plus a new
  beginner-versus-advanced regression passed (2 tests); focused Ruff and strict
  mypy also passed.
- Live `127.0.0.1:8001` HTTP run, with no proxy calls:
  - all four styles produced a GREEN Tab for the bundled CC0 example;
  - Jazz + low-position returned a stamped editable target;
  - all three voices locked returned `unchanged`,
    `ALL_VOICES_LOCKED`, and `model_calls=0`;
  - an invalid manual finger attempt returned `rejected`, preserved the GREEN
    checkpoint, and exposed the attempted RED verdict.
  - Jazz at beginner/intermediate/advanced bound the requested tier into the
    response and trace, produced 0/4/6 optional harmony notes, and remained
    GREEN in all three cases.
- Production Chrome visual/interaction run against the built Web application,
  with no proxy calls:
  - the existing user tab connected successfully and showed `Oracle ready`,
    the four arrangement styles, three difficulty tiers, three hand profiles,
    four technique preferences, candidate breadth, and explicit zero-call
    offline provenance;
  - the bundled CC0 MusicXML loaded through the visible UI, and Jazz + Advanced
    + small hand + low-position produced the selected GREEN checkpoint with an
    Advanced PASS, 3/3 available fidelity, 100% melody, 100% bass-root, and 71%
    harmony evidence;
  - all three voice locks returned an unchanged section revision while keeping
    the selected checkpoint; a manual 4→3 finger attempt was rejected as AMBER
    and rolled back, while a later 2→1 finger edit was accepted after a full
    Oracle recheck and remained GREEN;
  - the rejected correction, accepted correction, and an explicit 5/5 rating
    with `natural`, `easy to play`, and `style fit` tags produced three local
    anonymous events and enabled JSON export;
  - desktop and 375 px responsive views were visually inspected, the responsive
    document had no horizontal overflow, and the browser console reported no
    warnings or errors. The final GREEN result tab was left open for review.

## Deferred effect calibration

- Tune style resemblance, density, pocket, and voice-leading after collecting
  useful listening/playing examples.
- Tune player-profile and ergonomic preference weights against real hands and
  corrections; do not infer those mappings from local preference counts alone.
- Use exported corrections and A/B choices for analysis, evaluation sets, or a
  later explicitly designed reranker. They do not update the remote API model.
- Keep the current GREEN baseline and transactional rollback gates while these
  subjective objectives change.
