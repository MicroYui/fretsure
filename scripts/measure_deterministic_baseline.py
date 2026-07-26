#!/usr/bin/env python3
"""Re-run the deterministic B2 baseline over the whole benchmark corpus.

The benchmark's headline number is 14.8% joint success, and the easy reading is
that the model is bad at arranging.  This measures the part of that number no
model touches: the same 503 items, proposed and fingered without a single
inference call.  If the deterministic path also fails most of them, the ceiling
is in the verification stack rather than in the policy, and paying to re-run the
benchmark with a stronger proposer would buy nothing.

Free by construction -- no LLM is called, so this can be re-run after any
solver or oracle change to see whether the ceiling moved.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Final

from fretsure.agent.arranger import ArrangeGoal
from fretsure.bench.baselines import PureSolverStatus, run_pure_solver_baseline
from fretsure.bench.frozen_corpus import load_frozen_benchmark_corpus
from fretsure.oracle.core import CHECKER_VERSION, check_playability
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.api import FINGERING_SOLVER_VERSION

RESULT_SCHEMA: Final = "fretsure-deterministic-baseline@0.1.0"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="stop after N items (0 = all)")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    items = load_frozen_benchmark_corpus()
    if args.limit:
        items = items[: args.limit]

    statuses: Counter[str] = Counter()
    verdicts: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    for index, item in enumerate(items):
        # Built exactly as the benchmark runner builds it, so this number is
        # comparable with the recorded collection rather than merely similar.
        goal = replace(ArrangeGoal(), tempo_bpm=item.ir.meta.tempo_bpm)
        outcome = run_pure_solver_baseline(item.ir, goal, MEDIAN_HAND)
        statuses[outcome.status.value] += 1
        if outcome.tab is not None:
            verdict = check_playability(
                outcome.tab,
                MEDIAN_HAND,
                tempo_bpm=goal.tempo_bpm,
                beats_per_bar=item.ir.meta.time_sig[0],
            ).verdict
            verdicts[verdict] += 1
        elif outcome.infeasible is not None:
            reasons[outcome.infeasible.reason] += 1
        if (index + 1) % 25 == 0:
            print(f"  {index + 1}/{len(items)}", flush=True)

    payload = {
        "schema": RESULT_SCHEMA,
        "checker_version": CHECKER_VERSION,
        "fingering_solver_version": FINGERING_SOLVER_VERSION,
        "profile": MEDIAN_HAND.version,
        "items": len(items),
        "status": dict(sorted(statuses.items())),
        "oracle_verdict_when_solved": dict(sorted(verdicts.items())),
        "infeasible_reason": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        "solved": statuses.get(PureSolverStatus.TAB.value, 0),
    }
    text = json.dumps(payload, indent=1, sort_keys=True)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
