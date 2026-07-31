# The gate had two modes and one name — 2026-07-31

## What I got wrong

On 2026-07-30 I recorded, in a commit message, in `CLAUDE.md` and in
`PROJECT_STATE.md`, that the documented repertoire gate of 146–147/292 **did not
reproduce** and was being *corrected* to 107/292. That was wrong. It reproduces
perfectly well in the mode it was measured in, and the mode was never written
down next to the number.

`scripts/evaluate_repertoire_playability.py` has a `--choose-capo` flag. With it,
a refused score may try other capo positions before being called infeasible;
without it, the capo stays where the corpus put it. The historical figures are
the first; everything I measured on 07-30 is the second.

## How it was settled

Not by argument — the two measurements agree exactly where the mechanism cannot
act, and differ exactly where it can.

```
                              frozen 56-slice        whole corpus (292)
2026-07-28 frozen artifact         26 / 56            143   GREEN 83
my run at d753751, plain           26 / 56            110   GREEN 69
                          identical, id for id          differ by 33
```

The frozen slice is the original Carcassi/Sor set and a capo buys almost nothing
there — measured separately, 1 recovery out of 31 refusals. The expanded corpus
is where it pays: on `mutopia_expanded_permissive.json`, 32 pieces, the capo
ladder takes 18/32 to 24/32, recovering **6 of 14 refusals, 43%**.

So the historical measurement and mine agree on the 56 pieces where the flag is
inert and diverge by 33 on the 236 where it is not. Two supporting signs point
the same way: the artifact reports *more* `pitch unreachable on this tuning/capo`
failures than the plain run (4 against 3), which is what moving the capo does to
notes below the new open pitch, and *fewer* beam deaths (99 against 129), which
is what recovering pieces does to every infeasible bucket.

## Why I concluded it was a wrong number

I tested the capo hypothesis on 07-30 and dismissed it: `--choose-capo` recovered
one piece in fifty-six, so it could not account for forty. The subset I tested it
on was the frozen slice — **the one part of the corpus where the capo does
nothing** — while the claim I was testing it against
(`solve_fingering_score_choosing_capo`'s own docstring, "recovers 50 of the 184
refused scores") is about all 292.

Testing a claim about one population on a different one. That is the same failure
this project has now catalogued five times, and it is worth noting that on this
occasion it produced a *confident correction of someone else's number* rather
than a merely wrong measurement — the most expensive form it has taken.

## Both numbers, labelled

| | fixed capo | capo ladder |
|---|---|---|
| `d753751` — span 100, before the week's fixes | 110 / 292, GREEN 69 | 146 / 292, GREEN 89 *(recorded)* |
| `oracle@0.8.0` / `median@0.3` — current | 149 / 292, GREEN 114 | **173 / 292, GREEN 129** |

Everything measured on 2026-07-30 — the reach fix at 152, the slant exemption at
149, beam 32 at 143 — is in the **fixed-capo** column and is internally
consistent. None of it is comparable with the historical figures, and the claim
that those figures were erroneous is withdrawn.

Read down the column that history was written in, the week moves the gate from
**146 to 173 of 292**, 50.0% to 59.2%, and certifications from 89 to 129. That is
the apples-to-apples number and it is a much better one than the fixed-capo
column suggested. The two fixes it comes from are the reach window and the slant
exemption, both of which stopped the verifier refusing shapes editors print.

At the current oracle the ladder itself is worth **+24 pieces**, 149 to 173, and
it thins every infeasible bucket rather than one:

```
                                    fixed capo   capo ladder
no feasible frame config                    44            38
no non-red extension within beam            93            75
frozen 56-slice accepted                    30            32
```

## The product finding underneath

The capo ladder is implemented, tested, and recovers roughly forty percent of
refusals on the expanded corpus. It is not on by default, and it is not in the
acceptance command in `REPERTOIRE_MILESTONE_ACCEPTANCE.md`. The default is a
defensible product decision — `solve_fingering_score_choosing_capo` argues that
moving the capo is an instrument choice the caller is entitled to make — but the
*gate* reporting only one mode, under a name that does not say which, is what
produced this whole confusion.

The gate should report both, and say which is which.
