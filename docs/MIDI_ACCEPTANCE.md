# MIDI input acceptance record

> **Status (2026-07-17): SOFTWARE ACCEPTANCE COMPLETE.** Implementation, frozen
> producer replay, repository-wide tests, real-proxy tests, Web/static checks,
> independent reviews, and distribution smokes are complete. The exact Git SHA is
> intentionally an external receipt: a file cannot contain the hash of its own
> containing commit without changing that hash. The post-push local/tracking/remote
> equality receipt is reported alongside this record.

## Frozen contract

- Package: `0.5.0`.
- Score router: `score-input@0.1.0` with source-format registry
  `musicxml/mxl -> musicxml@0.3.0`, `midi -> midi@0.1.0`.
- Faithfulness: `fidelity@0.3.0`.
- Public product contracts: `agent-trace@0.2.0`,
  `fretsure-service@0.2.0`, `fretsure-api@0.2.0`,
  `fretsure-mcp@0.2.0`, and `fretsure-web@0.2.0`.
- Unchanged contracts: `oracle@0.2.0`, `tab-input@0.2.0`,
  `target-input@0.1.0`, `mxl-container@0.1.0`, and `median@0.1`.
- Optional MIDI cross-check runtime: exact `music21==10.5.0`.

The accepted MIDI domain is intentionally narrower than “MIDI support.” It accepts
raw `.mid`/`.midi` Standard MIDI Files in format 0 or 1 with PPQN timing, exactly one
non-percussion monophonic note-bearing `(track, channel)`, a fixed tick-zero tempo,
fixed 4/4 meter, an optional fixed traditional major/minor key, and the frozen
metadata/controller loss allowlist. Notes become exact-Fraction `voice="melody"`;
`chords=()` is authoritative. It does not infer track roles, bass, chords, key,
quantization, or notation duration.

## Reviewable implementation facts

- A first-party SMF parser validates header/chunk counts and lengths, exact EOF,
  canonical 1–4-byte VLQs, running status, final EOT, event data bytes, resource
  budgets, note pairing, monophony, and the semantic allowlist before `music21`, the
  arrangement pipeline, or an LLM can run.
- The raw tick timeline is authoritative. Only a zero-error parse is rebuilt as a
  minimal canonical SMF and cross-checked by music21 10.5.0 with
  `quantizePost=False`; music21 never receives the raw untrusted file.
- Limits are 10 MiB raw bytes, 64 tracks, 250,000 events, 20,000 resolved notes,
  absolute tick `0..2**31-1`, PPQN `1..32767`, a 4096-quarter-note maximum
  note-track EOT span, four VLQ bytes, 1 KiB per text/meta payload, 64 KiB cumulative
  text/meta, and 256 retained diagnostics plus one overflow sentinel. The EOT-span
  gate runs before music21 and bounds leading rest, note duration, and trailing
  silence together.
- Path and bytes entry points use the generic score router. Provenance keeps the raw
  SHA-256; MIDI raw/root hashes are equal and root member/container version are
  `None`. MIDI diagnostic locations expose zero-based track/event indices, one-based
  channel, and absolute tick.
- CLI, application, HTTP, Web responses, and capabilities carry the actual
  `midi@0.1.0` importer plus `score-input@0.1.0`. The model prompt receives the same
  canonical MusicIR but no raw source identity; the public trace separately binds its
  authoritative nullable fidelity fields to the response. HTTP accepts only canonical
  `audio/midi` for `.mid/.midi`, with suffix/media validation before body reading and
  engine initialization.
- The existing upload card and evidence-card visual system are reused. No track
  mapper, playback timeline, new page, drop-D/capo control, or new visual language was
  added, so this phase has no new human visual gate.

## Faithfulness availability

`fidelity@0.3.0` distinguishes missing evidence from a perfect score. Public scores
are nullable and are accompanied by canonically ordered, complete complementary
`evaluated_dimensions` and `unavailable_dimensions`. A score is `None` exactly when
its dimension is unavailable, and `passed` is recomputed over at least one evaluated
dimension using the frozen thresholds.

For a successful melody-only MIDI import, melody is evaluated while bass-root and
harmony are `N/A`; neither is rendered as `1.0` or described as fully evidenced.
MusicXML sources that contain melody/chord/harmonic evidence retain their prior
numeric values under the new availability-aware wire contract.

## Exact producer evidence: two positives, two typed negatives

| Census point | Exact public result |
| --- | --- |
| Before — precondition `0fa6af7` | No public MIDI importer or score router existed, so 0/4 exact `.mid` artifacts were accepted through a public MIDI entry point. This is absence of capability, not four typed importer failures. |
| After — current working tree | `midi@0.1.0` replays the same four exact files (682 raw bytes total) as 2 successes and 2 intentional typed domain rejections. |

Neither row makes a producer-wide compatibility claim.

The corpus and per-artifact hashes are frozen in
[`tests/fixtures/midi/producers/provenance.json`](../tests/fixtures/midi/producers/provenance.json)
and summarized in the
[`2026-07-17 MIDI census`](experiments/2026-07-17-midi-census.json). These rows prove
only the exact bytes listed there, not producer-wide or general MIDI compatibility.

- **Positive — MuseScore Studio 4.7.4:** format 1, PPQN 480, one track. The imported
  performance lasts exactly 7 beats; every sounding note is released one PPQN tick
  early. Those raw durations are retained rather than repaired back to notation.
- **Positive — music21 10.5.0:** format 1, PPQN 10080, two tracks. It retains exact
  notation note durations and an 8-beat note-track EOT.
- **Negative — MuseScore Studio 4.7.4 harmony realization:** the frozen
  `supported_basic` export creates multiple note-bearing streams and is rejected with
  a located `MULTIPLE_NOTE_BEARING_STREAMS` diagnostic.
- **Negative — music21 10.5.0 harmony realization:** the frozen `supported_basic`
  export is rejected for note-pairing/polyphony violations. The importer does not
  select a melody by heuristic or reconstruct source chord roles.

No cross-producer MusicIR equality is claimed: pitch/onset observations may align,
but 7-beat/one-tick-gap MuseScore timing and 8-beat music21 timing are distinct,
authoritative source performances.

## Honest limitations

- No SMF format 2, SMPTE timing, percussion, polyphony, multiple note-bearing
  streams, sustain/sostenuto gating, non-centre pitch bend, tuning changes, SysEx,
  unknown system/meta events, changing tempo/meter/key, or general controller input.
- No MIDI chord/role/key inference, arbitrary text interpretation, notation
  reconstruction, audio/performance-fidelity claim, live MIDI hardware, or DAW-wide
  compatibility claim.
- No MIDI/GP/MusicXML export, playback, FluidSynth, AlphaTab, or animated fretboard.
- Human listening and player calibration remain necessary before audible-quality or
  real-player claims, but they do not block this narrow file-input software phase.

## Final closure gates — complete

- [x] Full offline Python suite: `uv run pytest -q -m 'not integration'` — 1841
      passed, 8 deselected. The sole warning is the pre-existing Starlette/httpx
      deprecation notice.
- [x] Full real-proxy suite: `uv run pytest -q -m integration` — 8 passed, 1841
      deselected, including the exact frozen music21 MIDI artifact with melody-only
      N/A evidence and dynamic importer/router stamps.
- [x] Static/repository gates: `uv run ruff check .`; `uv run mypy --strict src`
      (78 files); strict mypy for the three release/producer scripts; `uv lock
      --check` (80 packages); `git diff --check`; and Markdown links (31 files) all
      passed.
- [x] Web gates: clean `npm ci` installed/audited 121 packages with zero
      vulnerabilities; 29 tests passed; typecheck and production build passed; a
      second build reproduced the full generated-static digest exactly. Desktop and
      390 px in-app-browser checks preserved the existing visual system and emitted
      no console warnings/errors.
- [x] Producer replay: exact MuseScore Studio 4.7.4 and music21 10.5.0 regenerated
      four artifacts (682 bytes total, 2 successes + 2 typed failures); `diff -rq`
      against the frozen corpus was empty and all raw/result hashes matched the
      manifest/census.
- [x] Distribution: `uv build` plus `scripts/audit_distributions.py` passed with 93
      wheel and 262 sdist entries. The wheel runtime set equals `src/fretsure`
      bidirectionally, every runtime byte and Name/Version metadata match, and the
      sdist evidence bytes match. Clean installs passed for core, `[musicxml]`,
      `[midi]`, `[score]`, `[service,score,agent]`, and `[mcp]`.
- [x] Independent scope/consumer review (`final_scope_review`): 0 blocker, 0
      important, 0 minor after response/trace and Web contract fixes.
- [x] Independent security review (`final_security_review`): 0 blocker, 0 important,
      0 minor after the 4096-quarter sparse-timeline gate and complete key/path
      regressions; 125 MIDI/dispatcher tests passed.
- [x] Independent release/product review (`final_release_review`): 0 blocker, 0
      important, 0 minor after license, OpenAPI, locale, and bidirectional wheel
      audit hardening. Its independent all-Python run passed 1849 tests.

## Git closure receipt

- Target branch: `origin/codex/sequential-plans`.
- Closure commit: `46ff8ac070e97422b4aecf5c0f2a22b588a5fda4`
  (`feat: add strict MIDI score input`), without an AI coauthor trailer.
- Identity check passed on 2026-07-17: `git rev-parse HEAD`, `git rev-parse @{u}`, and
  `git ls-remote origin refs/heads/codex/sequential-plans` all returned exactly
  `46ff8ac070e97422b4aecf5c0f2a22b588a5fda4`.

That external receipt closes the MIDI stage and unlocks benchmark v2.
