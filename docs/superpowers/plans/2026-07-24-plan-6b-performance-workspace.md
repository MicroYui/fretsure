# Plan 6B — performance workspace and honest live demo

> Status: IN PROGRESS. Software, production-browser and desktop-interoperability gates are
> complete. The user approved the result workspace on 2026-07-24 (“做的不错，界面挺好看的”).
> Unseen-input real-proxy and human-guitarist receipts remain open.

## Goal

Turn the verified canonical Tab into a playable, inspectable and comparable product
experience: real notation, synchronized fretboard evidence, audio playback, export
interoperability, checkpoint replay, fair A/B and a deterministic live scorecard.
Every stage element must be backed by the same application contracts used by ordinary
product flows.

## Evidence adjustments

- Benchmark v2 marked verifier-guided repair `NOT_KEPT`. Plan 6B must not restore it as
  a default product claim. The default live loop shows a proposed additive batch being
  rejected and the product retaining its last GREEN checkpoint. Legacy repair may only
  appear as an explicitly labelled research comparison.
- Best-of-N is an explicit opt-in because deployment cost is unknown. Candidate comparison
  must show its model-call count and must not imply that breadth is free.
- The critic has no human-musicality claim. Machine critic values may be shown only as
  observed metadata, never as a taste verdict.
- Human playability, difficulty and musicality claims remain open until a guitarist signs
  the corresponding acceptance record.

## In scope

1. **Notation and playback**
   - AlphaTab renders the backend's canonical MusicXML TAB.
   - Browser playback uses AlphaTab's bundled synthesizer and locally packaged SoundFont;
     playback position is synchronized with notation and the fretboard.
   - Native Guitar Pro 7+ `.gp` export is generated from the same loaded score.
2. **Fretboard evidence**
   - A responsive SVG six-string neck shows the active canonical `(string, fret, finger)`
     set at the current beat.
   - Trace trial checkpoints can be replayed; rejected NON_GREEN trials show localized
     oracle diagnostics and the retained final GREEN checkpoint remains explicit.
3. **Trace and checkpoints**
   - The existing public trace remains the source of truth.
   - Incremental trial rows include the attempted Tab checkpoint and oracle diagnostics.
   - Product spans are exported through one OpenTelemetry seam; no second agent runtime or
     duplicate trace store is introduced.
   - Users can compare available checkpoints and resume an allowed product action from a
     selected checkpoint. Hidden chain-of-thought is never exposed.
4. **Product controls**
   - Difficulty/tier is exposed through the existing Plan 5 checker/simplifier.
   - Verified best-of-N alternatives can be compared without bypassing either checker.
   - A local personal library stores canonical results and provenance only; it does not
     claim learned retrieval until a measured memory component exists.
5. **Audio and interoperability**
   - Application/API/MCP expose real synthesized audio, with deterministic MIDI as the
     source and a documented synthesizer/runtime stamp.
   - GP7, GP5, MusicXML, MIDI and PDF exports receive automated structural checks and a
     manual desktop-open receipt where automation cannot prove application compatibility.
6. **Demo Lab**
   - A fair A/B uses the same input, model identity and declared model-call budget; both
     outputs are checked by the same oracle and fidelity checker.
   - The live scorecard is recomputed from typed results and shows missing/unavailable
     values as such. It includes the relevant ablation/evidence status, not stale legacy
     headline claims.
   - Prescreened examples may provide a disclosed cached fallback, but the primary action
     runs the real product path.
7. **Money moment**
   - An unseen symbolic input can be selected with difficulty, visibly reaches a rejected
     trial or diagnostic, retains/produces a GREEN result, plays in the app, and is then
     performed by a real guitarist under a recorded acceptance receipt.

## Explicit cuts

- Accounts, cloud deployment, a remote multi-user database and social features are not
  Plan 6B requirements.
- MP3/WAV transcription remains the original best-effort v2 cut. Symbolic input is the
  complete presentation path; no fake transcription or correction UI is added.
- No second framework demo, fake streaming, hidden prerecorded result, or unearned
  `100% playable` copy.

## Implementation order and gates

1. Freeze the Plan 6B product/trace/audio contracts and add failing focused tests.
2. Add AlphaTab packaging, notation/playback primitives and GP7 export.
3. Add trial checkpoint evidence, synchronized fretboard state and audio rendering.
4. Expose difficulty, alternatives and checkpoint actions through the application seam.
5. Build the approved performance workspace and Demo Lab.
6. Pass Python/Web/static/package gates, real-browser desktop/mobile interaction and
   reduced-motion/keyboard checks.
7. Record GP7/GP5/MusicXML desktop interoperability and the human money-moment receipt.

Plan 6B closes only when every item above has direct evidence. A passing unit suite alone
does not close browser, desktop-application or human gates.
