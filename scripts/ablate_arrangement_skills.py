#!/usr/bin/env python3
"""Does the arrangement skill registry earn its place, or just sound like it should?

Paired ablation. Both arms see the same benchmark items in the same order at the
same temperature; the only difference is whether `skill_guidance()` reaches the
proposal prompt. Pairing is the point — arrangement outcomes vary enough between
items that an unpaired comparison of two independent samples would mostly measure
which items each arm happened to get.

Scored by the checker, never by a model: the oracle's verdict on the produced
tab, and the independent fidelity gate. `joint` means both, which is the
benchmark's own definition of success and the only number that has ever been
allowed to decide whether an agent mechanism ships here.

Pre-registered before running, so the result cannot be reinterpreted afterwards:

* **primary outcome** — paired difference in joint success rate.
* **ship** if the difference is positive and its 95% interval excludes zero.
* **decline** otherwise, including when it is positive but the interval does not
  exclude zero. Repair was declined at +0.0566 on 500 items; a smaller sample
  here cannot license a weaker standard, only a wider interval.
* **secondary outcome, declared before running and not a ship criterion** —
  whether the proposed target yields any tab at all. Joint success at `n=1` with
  no repair is rare enough that a few hundred items cannot resolve it, and a
  primary outcome nobody can move is a way of guaranteeing a null. This
  secondary has a much higher base rate and is what the skills actually target:
  they are about writing a target this oracle can accept. It can support a
  *mechanism* claim, never a ship decision.

The sample is a fixed prefix of the frozen 503-item corpus rather than a random
draw, so that a re-run compares the same items.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Final

import fretsure.agent.arranger as arranger_module
from fretsure.agent import skills as skills_module
from fretsure.agent.arranger import ArrangeGoal
from fretsure.agent.harness import arrange
from fretsure.agent.skills import ARRANGEMENT_SKILL_REGISTRY_VERSION, skill_guidance
from fretsure.bench.corpus import CorpusItem
from fretsure.bench.frozen_corpus import load_frozen_benchmark_corpus
from fretsure.llm.client import ProxyLLM, proxy_environment_configured
from fretsure.metrics.fidelity import FIDELITY_CHECKER_VERSION, faithfulness
from fretsure.oracle.core import CHECKER_VERSION

RESULT_SCHEMA: Final = "fretsure-skill-ablation@0.1.0"


def _wilson(successes: int, total: int) -> tuple[float, float]:
    if total == 0:
        return (0.0, 0.0)
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return (max(0.0, centre - spread), min(1.0, centre + spread))


def _paired_interval(improved: int, worsened: int) -> tuple[float, float, float]:
    """Difference in rate and its interval, from the discordant pairs alone.

    Concordant pairs carry no information about a difference, which is exactly
    why a paired design is worth the trouble.
    """

    discordant = improved + worsened
    if discordant == 0:
        return (0.0, 0.0, 0.0)
    point = (improved - worsened) / discordant
    low, high = _wilson(improved, discordant)
    return (point, 2 * low - 1, 2 * high - 1)


def run_arm(
    items: list[CorpusItem],
    *,
    with_skills: bool,
    model_id: str,
    temperature: float,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """One arm. Skills are removed by emptying the guidance, not by editing code."""

    original = skills_module.skill_guidance
    if not with_skills:
        arranger_module.skill_guidance = lambda: ""  # type: ignore[attr-defined]
    outcomes: list[dict[str, object]] = []
    try:
        for index, item in enumerate(items):
            goal = replace(ArrangeGoal(), tempo_bpm=item.ir.meta.tempo_bpm)
            llm = ProxyLLM(model_id)
            result = arrange(
                item.ir,
                goal,
                llm,
                n=1,
                max_iters=0,
                use_critic=False,
                temperature=temperature,
            )
            verdict = None if result.oracle is None else result.oracle.verdict
            # ArrangeResult carries the raw fidelity scores; joint success is the
            # gate over them, which is how report.py defines it.
            gate = None if result.tab is None else faithfulness(item.ir, result.tab)
            fidelity_passed = None if gate is None else gate.passed
            outcomes.append(
                {
                    "item_id": item.item_id,
                    "verdict": verdict,
                    "fidelity_passed": fidelity_passed,
                    "joint": bool(verdict == "GREEN" and fidelity_passed),
                    "solved": result.tab is not None,
                }
            )
            if (index + 1) % 10 == 0:
                print(
                    f"  {'skills' if with_skills else 'control'} {index + 1}/{len(items)}",
                    file=sys.stderr,
                    flush=True,
                )
    finally:
        arranger_module.skill_guidance = original  # type: ignore[attr-defined]
    return outcomes, {
        "with_skills": with_skills,
        "joint": sum(1 for o in outcomes if o["joint"]),
        "solved": sum(1 for o in outcomes if o["solved"]),
        "green": sum(1 for o in outcomes if o["verdict"] == "GREEN"),
        "verdicts": dict(Counter(str(o["verdict"]) for o in outcomes)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", type=int, default=60)
    parser.add_argument("--model", type=str, required=True)
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if not proxy_environment_configured():
        raise SystemExit("proxy environment is not configured")

    corpus = list(load_frozen_benchmark_corpus())[: args.items]
    control_outcomes, control = run_arm(
        corpus, with_skills=False, model_id=args.model, temperature=args.temperature
    )
    treated_outcomes, treated = run_arm(
        corpus, with_skills=True, model_id=args.model, temperature=args.temperature
    )

    improved = worsened = 0
    solved_improved = solved_worsened = 0
    for a, b in zip(control_outcomes, treated_outcomes, strict=True):
        if b["joint"] and not a["joint"]:
            improved += 1
        elif a["joint"] and not b["joint"]:
            worsened += 1
        if b["solved"] and not a["solved"]:
            solved_improved += 1
        elif a["solved"] and not b["solved"]:
            solved_worsened += 1
    point, low, high = _paired_interval(improved, worsened)
    s_point, s_low, s_high = _paired_interval(solved_improved, solved_worsened)
    decision = "SHIP" if point > 0 and low > 0 else "DECLINE"

    payload = {
        "schema": RESULT_SCHEMA,
        "skill_registry": ARRANGEMENT_SKILL_REGISTRY_VERSION,
        "skill_block_chars": len(skill_guidance()),
        "checker": CHECKER_VERSION,
        "fidelity_checker": FIDELITY_CHECKER_VERSION,
        "model": args.model,
        "temperature": args.temperature,
        "items": len(corpus),
        "control": control,
        "treated": treated,
        "paired": {
            "improved": improved,
            "worsened": worsened,
            "discordant": improved + worsened,
            "point": point,
            "ci95": [low, high],
        },
        "paired_solved": {
            "improved": solved_improved,
            "worsened": solved_worsened,
            "discordant": solved_improved + solved_worsened,
            "point": s_point,
            "ci95": [s_low, s_high],
        },
        "decision": decision,
        "per_item": [
            {
                "item_id": a["item_id"],
                "control": a["joint"],
                "treated": b["joint"],
                "control_solved": a["solved"],
                "treated_solved": b["solved"],
            }
            for a, b in zip(control_outcomes, treated_outcomes, strict=True)
        ],
    }
    text = json.dumps(payload, indent=1, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
