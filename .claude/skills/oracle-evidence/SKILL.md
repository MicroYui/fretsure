---
name: oracle-evidence
description: Measure a change to Fretsure's playability oracle, solver or corpus with evidence that will survive review. Use for any claim of the form "this gains N pieces", "this is free", "this is dead code", or "loosening X is safe" — and before touching src/fretsure/oracle/ at all.
---

# Measuring a change to the verifier

This project's whole value is that its oracle can be trusted. A change that
gains repertoire by quietly certifying unplayable tabs is worse than no change,
and it will not look worse in the number you were watching. So the discipline
below is not ceremony — every rule here exists because it was violated and
caught, and the violations are recorded so you can see the shape of the mistake.

## Use the instruments that exist. Do not write your own sweep.

| question | command |
|---|---|
| how much published repertoire is accepted | `uv run --frozen python scripts/evaluate_repertoire_playability.py --summary-only` |
| did the oracle's judgement move on known-unplayable tabs | `uv run --frozen python scripts/replay_negative_tabs.py` |
| what does a candidate `d_max` table cost | `uv run --frozen python scripts/score_span_table.py --table '{"1-2": 0.55}'` |
| what does loosening a profile cost | `uv run --frozen python scripts/measure_profile_frontier.py --skip-positives` |
| where is the LLM-free benchmark ceiling | `uv run --frozen python scripts/measure_deterministic_baseline.py` |
| is the mutation suite still killing everything | `uv run --frozen pytest -q tests/validation` |

The corpus, the negative set and the guards are wired into these already. Rolling
your own is where the errors below came from.

## The rules, and the mistake each one is made of

**1. Never report a gate number measured on a subset.**
Measuring "how many of the 266 failing pieces does this gain" cannot see
regressions among the 123 that already pass. This was done, reported `+7`, and
the true figure was `+7 gained, 5 lost` — a net `+2` that violated the
no-regression guard. Always run the full corpus.

**2. Both sides, or say nothing.**
Any change touching the oracle reports the negative set's verdict multiset, with
`GREEN` counted separately from `AMBER`. A `GREEN` there is the oracle
*certifying* a tab known to be unplayable; an `AMBER` is only declining to
certify. They are not the same price and a combined "non-RED" count hides the
one that matters.

**3. Reproduce the baseline first, and say that you did.**
The negative set is `{RED 1651, AMBER 61, GREEN 6}` and the frozen 58-score
subset accepts 26. If your harness cannot reproduce those, stop — the harness is
wrong, not the model.

**4. No regression is a hard constraint, not a tiebreak.**
Every piece accepted before must still be accepted. A net gain that loses
currently-playable repertoire is not a gain.

**5. Acceptance is not monotone in the model, because the beam is not.**
A wider hand admits more per-frame configurations, so the width-16 beam retains
different prefixes and pieces trade places — measured: three pieces gained at
`reach=60` are lost again at `reach=70`. Report gains *and* losses. "Accepted at
setting X" is partly a property of the search, not only of the model.

**6. "Dead code" and "inert" are measurements, not proofs.**
An analytic argument that one constraint implies another held on random frames
and failed on the real corpus. A claim that a term never binds must be shown by
flipping it and observing no verdict moves — and "no verdict moves" means zero,
not nearly zero. One claim of "completely inert" turned out to move 1 tab in
1,718; that is a different sentence.

**7. Separate what you measured from what you inferred.**
Both are useful. Presenting the second as the first is how a review gets wasted.

**8. Every number comes with the command that reproduces it.**
Not a description of the method — the literal command line, including flags.
This is the cheapest rule here and it catches the most: the `+7` that was really
`+7 gained, 5 lost` would have been obvious the moment its command showed the
run was scoped to the failing subset. If a number cannot be attached to a
command, it is an inference (see 7), and if the command is a script you wrote
yourself rather than one from the table above, say so and explain why the
existing instrument would not answer the question.

## The four guards

Run all four for any change to `src/fretsure/oracle/`, `src/fretsure/solver/`
or `data/score_corpus/`:

1. **Oracle not weakened** — the 1,718 raw-LLM tab verdict multiset is
   *invariant*, in either direction. Movement means a solver change leaked into
   the verifier.
2. **No regression** — every previously accepted score still accepted.
3. **Sustain not quietly dropped** — `sustain_retention >= 0.90`; the ladder must
   release zero melody beats.
4. **Mutation suite unchanged** — 14 mutants, kill rate 1.0. If you add a rule,
   add a mutant that dies with it; if you cannot construct one, say why the rule
   is redundant rather than shipping an unkillable mutant.

## Version stamps move when behaviour does

`oracle@`, `tab-input@`, `fingering-solver@`, `score-solver@`, `sustain-model@`,
profile versions. The project does not do backwards compatibility — re-freeze
golden values deliberately and say in the commit what moved and why. **Needing
to bump `oracle@` for anything other than a newly representable technique is a
signal that the verifier is being weakened. Stop and re-read the measurement.**

## When the answer is "no"

Declining is a normal outcome and three changes have already been measured and
declined: a profile-wide loosening (tripled false certifications for one piece
in fifty-eight), a per-pair `d_max` table (inert on the pairs tested), and a
`reach` increase (net `+2` with 5 regressions). Each is written up with the
numbers. A correctness or clarity change with no quality effect is also a
legitimate outcome — label it as such rather than dressing it up.
