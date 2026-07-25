# Plan 7A — product controls and editing loop

Date: 2026-07-25  
Decision: complete the functional surface first; tune subjective effect quality
after real use.

## Scope

1. Versioned Fingerstyle, Classical, Jazz, and R&B arrangement intent.
2. Score difficulty carried into generation and checked independently; player
   hand profile and technique preference remain separate, and a fixed score's
   finger assignment never reads difficulty.
3. Measure-scoped regeneration with melody/bass/harmony locks and transactional
   GREEN + faithfulness rollback.
4. Manual left-hand finger-number correction with a complete Oracle recheck.
5. Local anonymous rating, A/B, and correction evidence with JSON export and no
   training claim.
6. Selected-checkpoint-first result UI even when a later trial was rejected.

## Non-goals

- No remote-model fine-tuning or claim that API feedback learns automatically.
- No representative human calibration of style, difficulty, hand profiles, or
  ergonomic weights.
- No new repair loop, cloud account, database, leaderboard, or partial MCP tool.
- No weakening of the current playability or faithfulness gates.

## Acceptance rule

Each control must be carried by the typed API and provenance stamps, exposed in
the existing Performance Workspace, exercised by an end-to-end test, and safe to
use without a proxy call. Subjective quality can remain provisional, but a
successful result must remain a fully checked checkpoint and a failed edit must
leave the previous checkpoint unchanged.

The evidence receipt is [`PLAN7A_ACCEPTANCE.md`](../../PLAN7A_ACCEPTANCE.md).
