# Measuring a prompt change through a solver that refuses most things — 2026-07-27


> **Corrected 2026-07-28.** Every `/389` figure below was divided by a corpus
> that counted 86 pieces twice and carried 11 that were never parsed correctly;
> the corrected size is 292. The *rates* survive nearly unchanged (45.9% deduped
> against 45.5% before), because the duplicates split between accepted and
> refused almost exactly at the base rate — what does not survive is the corpus
> size, the grouped split, and any per-piece attribution. See
> [`2026-07-28-corpus-defects.md`](2026-07-28-corpus-defects.md). The numbers
> below are left as measured rather than rewritten.

## The mistake this file exists to record

The arrangement skill registry was ablated, came back at a clean zero, and was
cut. That conclusion was wrong, and the way it was wrong is worth more than the
registry.

The first measurement scored each arm by what came out of `arrange()`: did the
proposed target solve, and was the result jointly successful. Both arms landed on
**exactly the same 108 items solved of 250, zero discordant pairs**. That looked
like a decisive null.

It was a ceiling. The solver accepts 123 of 389 published scores and 220 of 503
benchmark items *with no model involved at all*. A bottleneck that severe masks
any improvement upstream of it, so "the guidance changed nothing" and "the solver
refused everything either way" produce the same number. The measurement could not
tell them apart, and it was read as though it could.

## The second mistake, caught in code rather than in data

The obvious repair is to measure the *target* instead of what the solver made of
it. The first attempt read `ArrangeResult.target` — but the harness returns

```python
return ArrangeResult(None, None, None, None, trace, k)   # no candidate solved
```

so the target is discarded on exactly the items that fail. Reading target quality
from that field conditions the measurement on the bottleneck it was meant to see
past. Verified before trusting it: 2 targets captured of 4.

The fix is to call `propose_arrangement` directly and run the solver afterwards,
keeping the target either way. Target capture went to 4 of 4.

## What the corrected measurement says

Paired, 250 items, same items both arms, temperature 0.7, checker-scored.

| | control (no guidance) | treated (7 skills) |
|---|---|---|
| joint success | 33 | **36** |
| produced any tab | 84 | **98** |

| outcome | improved | worsened | point | 95% interval |
|---|---|---|---|---|
| joint (primary) | 7 | 4 | `+0.273` | `[-0.292, 0.697]` |
| solvable (secondary) | 19 | 5 | **`+0.583`** | **`[0.191, 0.815]`** |

And the targets themselves moved in the direction the skills argue for, with the
solver out of the path entirely:

| target property | control | treated | the skill that asks for it |
|---|---|---|---|
| mean pitch span per frame | 7.90 | **6.81** | along-neck distance is what costs |
| same-pitch re-attacks | 3.52 | **3.10** | do not re-pluck instead of holding |
| notes per target | 27.9 | 26.1 | — |
| frames over six notes | 0 | 0 | never violated either way |
| melody duration kept | 1.000 | 1.000 | already perfect either way |

**The mechanism works.** The model does what the guidance says, and more of what
it writes is solvable.

## The decision is still DECLINE, and that is not a formality

The pre-registered primary was joint success, and its interval spans zero. The
secondary was declared before running as *never a ship criterion*, precisely so
that a good-looking secondary could not be promoted after the fact. It looks good
now. Promoting it would be choosing the standard after seeing the data, which is
the failure mode the pre-registration existed to prevent.

So: **the registry stays, unshipped-as-proven.** It is kept rather than cut
because the mechanism is demonstrated and one of its lines corrects a rule that
was actively false. Whether it improves joint success is unresolved and needs a
larger sample, not a re-reading of this one.

## A larger finding, found while checking the first one

**The LLM proposal fell back to the deterministic proposer on 11 of 20 items —
55% — in this environment.** `propose_arrangement` catches `ValueError`,
`KeyError`, `TypeError`, `RuntimeError` and `ZeroDivisionError` and silently
substitutes `propose_style`.

The reason matters and was checked rather than assumed. It is
`RuntimeError: LLM call failed after bounded retries` from `llm/client.py:654` —
a **transport failure against the local proxy**, not the model producing output
that fails validation. Those would mean very different things, and only the
second would be a statement about the product's arranging ability.

Two consequences, stated at the strength the evidence supports:

* Any A/B on the prompt is diluted, because on a fallback both arms receive the
  *identical* deterministic target. This is what produced the first run's zero
  discordant pairs. The `+0.583` above is measured over the items where the model
  actually answered, so it is a floor rather than a ceiling.
* The **silent** substitution is a product property worth surfacing regardless of
  why it triggered: a caller cannot currently tell whether an arrangement came
  from the model or from the deterministic fallback. The 55% rate itself is not a
  product claim — it is one local proxy under load, measured here, and says
  nothing about a healthy deployment.

Both are separate from the skills question and neither was investigated further.

## What to keep from this

A measurement taken downstream of a severe bottleneck cannot support a claim
about anything upstream of it. This project already knew that in one direction —
the repertoire milestone concluded the ceiling is in verification rather than in
the policy's taste — and then made the mirror-image error immediately afterwards
by testing a policy change through that same verifier.
