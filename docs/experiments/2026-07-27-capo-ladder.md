# The capo was sitting there the whole time — 2026-07-27

## What was refused, and what it cost to stop refusing it

Five attempts had been made to raise published-repertoire acceptance by changing
the hand model. Together they bought **+2 pieces**, and each one had to be
weighed against certifying tabs known to be unplayable:

| attempt | repertoire | price |
|---|---|---|
| profile-wide loosening (W6) | +1 | false certifications 6 → 19 |
| `reach` 50 → 52.5 | +7 but **5 lost** | a cliff 1% away |
| `d_max` (1,4) and (3,4) | 0, and −2 when tightened | — |
| `d_max` (1,2) 0.50 → 0.55 | **+2** | +4, all out of the AMBER band |

Every one of them is a trade between accepting repertoire and weakening the
verifier. The capo is not in that structure at all.

## The mechanism, stated correctly the second time

The first version of this reasoning was wrong and is recorded because the wrong
version is the intuitive one: *"a capo shrinks fret spacing, so the same stretch
costs fewer millimetres."* It does not. Fret wire does not move. The absolute
fret that produces a given pitch is identical with or without a capo, and so is
the geometry of pressing it.

What a capo actually does is raise every open string by N semitones, so **any
note at exactly the new open pitch stops needing a finger at all**. The left
hand has fewer things to hold, and every span constraint that involved those
notes disappears with them. The price is that anything below the new open pitch
has nowhere to go on that string — so a piece with deep bass loses options
rather than gaining them.

A trade, then, but an *internal* one, paid in the score's own range rather than
in the verifier's judgement.

## What it recovers

A cheap range check first, before spending any solver time: of the 264 refused
scores, only 184 have a lowest note that survives a capo at fret 3 at all. 57 sit
on the open low E and can take no capo whatsoever. That bound cost nothing to
compute and said the idea was worth measuring.

Solving those 184 at capos 1 through 5:

**50 recovered** on that sample. Running the whole gate with the ladder enabled
recovers **52**, because the shipped implementation filters candidates position
by position rather than demanding a piece tolerate the whole ladder — two scores
that can only take a capo at fret 1 or 2 were excluded by the probe's blanket
pre-filter and are admitted by the real thing.

**The gate moves 125 → 177 of 389** (32.1% → **45.5%**), and GREEN specifically
moves 73 → 99.

The lowest working position is spread across the ladder — 5 pieces at capo 1,
14 at 2, 11 at 3, 6 at 4, 14 at 5 — so there is no single value to hard-code;
the search is the point.

Verified through the gate's own `evaluate_example` rather than the probe that
found it, on a sample: `INFEASIBLE → GREEN` with sustain retention 1.000 for
three of four, 0.927 for the fourth, all above the floor.

## Why this costs nothing

- **The oracle is unchanged.** Not one rule, not one constant. The verdict on
  any given tab is exactly what it was.
- **Pitches are preserved exactly.** A capo is an instrument setting, not an edit
  to the music, so the fidelity gate sees the same notes.
- **False certifications cannot move**, because nothing about the judgement
  moved.
- **Regression is structurally impossible.** The requested position is always
  tried first and returned immediately on success, so a score that solves today
  solves identically and pays nothing for the ladder. Only refusals reach it.

## Three things the implementation had to get right

**The requested capo goes first.** Otherwise the 125 already-accepted scores
would be re-solved under a different setup, and every earlier measurement would
become incomparable.

**It is opt-in.** `solve_fingering_score_choosing_capo` is a separate entry
point, and the gate needs `--choose-capo`. Moving the capo is a decision the
player has to physically carry out, and a solver that quietly made it would be
answering a question nobody asked.

**The choice announces itself.** `Tab.capo` is part of the tab, so anything that
renders it shows which position it needs. This is the same defect as the
deterministic proposal fallback recorded in the skill-registry ablation, where a
caller cannot tell which proposer answered — one silent substitution per project
is enough.

**Failure reports the position that was asked for**, not the last rung tried,
so an infeasibility describes the score somebody actually submitted.

## What this does not fix

Even counting every recovery, **212 of 389 are still refused**. Of those, **130**
die with "no non-red extension within beam" — down from 168, but still the
largest bucket by far, and it is the search exploring and finding nothing rather
than a statement about how strict the model is. A further 75 have no feasible
frame configuration at any position.

The beam is already known to be **non-monotone**: a wider hand model loses pieces
it previously solved. That is a search defect, it is measurable, and fixing it
does not require touching the oracle either. See the postscript.

## Postscript: the beam, and a diagnosis that was wrong — 2026-07-28

The obvious next suspect was the beam, since acceptance is not monotone in the
hand model. Sweeping width over 80 refused scores:

| beam | recovered of 80 |
|---|---|
| 16 (baseline) | 0, by definition |
| 32 | **7** |
| 64 | **5** |

Doubling the width **loses three pieces it had solved** (`carcassi-op60-01`
movement 2, `horetzky40` movements 1 and 2) and gains one. Pure slot shortage
cannot do that — 64 slots hold anything 32 slots held.

**The explanation given for this was wrong, and reading the code disproved it.**
The claim was that the diversity grouping churns: more candidates means more
groups, so round-robin spreads thinner and evicts. But `_select_diverse_partition`
uses `limit` *only* as a stopping condition, and its addition order does not
depend on it — so for a fixed candidate pool, what a beam of 32 keeps is a strict
prefix of what 64 keeps. The selection is monotone.

The non-monotonicity is one level up. A wider beam lets more prefixes survive the
*previous* frame, so the next frame ranks a strictly larger pool, and prefixes
that are cheap but will not complete can displace ones that would have. **Cost
does not predict completability.** That is the classic beam-search failure, not
a defect in this project's grouping key.

The corrected diagnosis changes the repair. The fix first proposed — reserving
beam slots for the globally cheapest states — would have made things *worse*, by
leaning harder on exactly the ranking that is misleading. What is actually
supported is that different widths explore differently, so their union beats
either: `solve_fingering_score_with_escalation` tries the capo ladder first, then
widens.

Its harvest is modest and should not be confused with the capo's. Of the 8 pieces
width recovers, 4 were already recovered by the capo; **4 are new**. Capo is +52;
width is roughly +4 per 80 refused scores and costs far more per attempt, which
is why it goes last and only for scores nothing cheaper saved.

## A process note worth keeping

The probe that produced this number was, in its first version, a fork bomb:
`raise SystemExit(main())` at module level, under macOS `spawn`, means every
worker re-imports the module and forks again. The previous session's machine
lock-up was attributed to "too much concurrency" — that diagnosis would have
been treated with self-discipline about worker counts, and it would not have
helped, because this bug detonates with one worker. The fix is
`if __name__ == "__main__":`, and it is now a fixed check for any throwaway
multiprocessing script.
