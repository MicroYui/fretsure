#!/usr/bin/env python3
"""Does injected arranging knowledge earn its place, or just sound like it should?

Kept after the registry it was written for was cut. It measured that registry at
a clean zero -- 250 paired items, no change at all in which targets are solvable
-- and the next mechanism that claims to make this agent smarter should have to
face the same instrument rather than a fresh one built to suit it.

Point it at whatever the harness injects by exposing `skill_guidance` on
`fretsure.agent.arranger`; with nothing injected both arms are identical and the
run is a no-op, which is the correct answer to "does nothing help".

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
from fractions import Fraction
from multiprocessing import Pool
from pathlib import Path
from typing import Final, cast

import fretsure.agent.arranger as arranger_module
from fretsure.agent import skills as skills_module
from fretsure.agent.arranger import ArrangeGoal, propose_arrangement
from fretsure.bench.corpus import CorpusItem
from fretsure.bench.frozen_corpus import load_frozen_benchmark_corpus
from fretsure.ir import MusicIR, Note
from fretsure.llm.client import ProxyLLM, proxy_environment_configured
from fretsure.metrics.fidelity import FIDELITY_CHECKER_VERSION, faithfulness
from fretsure.oracle.core import CHECKER_VERSION, check_playability
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.score import solve_fingering_score
from fretsure.tab import Tab

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


_WORK: dict[str, object] = {}


def _init_worker(model_id: str, temperature: float, items_wanted: int) -> None:
    _WORK["items"] = list(load_frozen_benchmark_corpus())[:items_wanted]
    _WORK["model_id"] = model_id
    _WORK["temperature"] = temperature


def target_properties(source: MusicIR, target: tuple[Note, ...]) -> dict[str, object]:
    """What the skills claim about a target, measured on the target itself.

    The downstream outcome cannot separate "the guidance did nothing" from "the
    solver refused everything anyway": it accepts 123 of 389 published scores and
    220 of 503 benchmark items *without any model involved*, so a ceiling that
    low can mask any improvement in what is fed to it. These properties are read
    straight off the proposed notes, with the solver out of the path, so they
    answer the mechanistic question -- did the model do what it was told -- even
    when nothing downstream moves.
    """

    by_onset: dict[Fraction, list[Note]] = {}
    for note in target:
        by_onset.setdefault(note.onset, []).append(note)
    frame_sizes = [len(v) for v in by_onset.values()]

    # "six strings is the hard ceiling" and "five-note chords need a sweep"
    over_six = sum(1 for n in frame_sizes if n > 6)
    dense = sum(1 for n in frame_sizes if 5 <= n <= 6)

    # "do not re-pluck instead of holding": the same pitch attacked again while
    # the previous one would still have been sounding.
    onsets_by_pitch: dict[int, list[tuple[Fraction, Fraction]]] = {}
    for note in target:
        onsets_by_pitch.setdefault(note.pitch, []).append((note.onset, note.duration))
    re_attacks = 0
    for spans in onsets_by_pitch.values():
        spans.sort()
        for (onset, duration), (next_onset, _d) in zip(spans, spans[1:], strict=False):
            if next_onset < onset + duration + Fraction(1, 2):
                re_attacks += 1

    # "never shorten a melody note": how much of the source melody's written
    # duration survives into the target.
    source_melody = sum(
        (n.duration for n in source.notes if n.voice == "melody"), Fraction(0)
    )
    target_melody = sum((n.duration for n in target if n.voice == "melody"), Fraction(0))
    kept = float(target_melody / source_melody) if source_melody else None

    # "along-neck distance is what costs": the widest pitch span asked for at one
    # onset, which is the thing a hand has to stretch across.
    spans_semitones = [
        max(n.pitch for n in v) - min(n.pitch for n in v) for v in by_onset.values()
    ]
    return {
        "notes": len(target),
        "frames": len(by_onset),
        "max_frame": max(frame_sizes, default=0),
        "frames_over_six": over_six,
        "frames_five_or_six": dense,
        "re_attacks": re_attacks,
        "melody_duration_kept": kept,
        "max_frame_pitch_span": max(spans_semitones, default=0),
        "mean_frame_pitch_span": (
            sum(spans_semitones) / len(spans_semitones) if spans_semitones else 0.0
        ),
    }


def _run_one(task: tuple[int, bool]) -> dict[str, object]:
    """One item in one arm. Both arms run the same item, which is the pairing.

    The proposal is called directly rather than through `arrange`, because
    `ArrangeResult` discards the target whenever no candidate solves -- and that
    is most items. Reading target quality only from solved items would condition
    the measurement on the very bottleneck it is meant to see past.
    """

    index, with_skills = task
    items = cast(list[CorpusItem], _WORK["items"])
    item = items[index]
    temperature = cast(float, _WORK["temperature"])
    original = skills_module.skill_guidance
    if not with_skills:
        arranger_module.skill_guidance = lambda: ""  # type: ignore[attr-defined]
    try:
        goal = replace(ArrangeGoal(), tempo_bpm=item.ir.meta.tempo_bpm)
        target = propose_arrangement(
            item.ir,
            goal,
            ProxyLLM(cast(str, _WORK["model_id"])),
            temperature=temperature,
        )
    finally:
        arranger_module.skill_guidance = original  # type: ignore[attr-defined]

    # Reproduce what the harness does for n=1, max_iters=0, use_critic=False,
    # but keep the target regardless of whether any of it succeeds.
    solved = solve_fingering_score(
        target,
        goal.tuning,
        goal.capo,
        MEDIAN_HAND,
        tempo_bpm=goal.tempo_bpm,
        beats_per_bar=item.ir.meta.time_sig[0],
    )
    tab = solved if isinstance(solved, Tab) else None
    verdict = (
        None
        if tab is None
        else check_playability(
            tab,
            MEDIAN_HAND,
            tempo_bpm=goal.tempo_bpm,
            beats_per_bar=item.ir.meta.time_sig[0],
        ).verdict
    )
    gate = None if tab is None else faithfulness(item.ir, tab)
    fidelity_passed = None if gate is None else gate.passed
    return {
        "index": index,
        "with_skills": with_skills,
        "item_id": item.item_id,
        "verdict": verdict,
        "fidelity_passed": fidelity_passed,
        "joint": bool(verdict == "GREEN" and fidelity_passed),
        "solved": tab is not None,
        "target": target_properties(item.ir, target),
    }


def summarise(
    outcomes: list[dict[str, object]], *, with_skills: bool
) -> tuple[list[dict[str, object]], dict[str, object]]:
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
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if not proxy_environment_configured():
        raise SystemExit("proxy environment is not configured")

    corpus = list(load_frozen_benchmark_corpus())[: args.items]
    tasks = [(i, arm) for i in range(len(corpus)) for arm in (False, True)]
    done: list[dict[str, object]] = []
    with Pool(
        processes=args.workers,
        initializer=_init_worker,
        initargs=(args.model, args.temperature, len(corpus)),
    ) as pool:
        for finished, row in enumerate(pool.imap_unordered(_run_one, tasks), start=1):
            done.append(row)
            if finished % 20 == 0:
                print(f"  {finished}/{len(tasks)}", file=sys.stderr, flush=True)

    by_arm: dict[bool, list[dict[str, object]]] = {False: [], True: []}
    for row in sorted(done, key=lambda r: (r["with_skills"], r["index"])):
        by_arm[cast(bool, row["with_skills"])].append(row)
    control_outcomes, control = summarise(by_arm[False], with_skills=False)
    treated_outcomes, treated = summarise(by_arm[True], with_skills=True)

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
        "skill_registry": getattr(
            arranger_module, "ARRANGEMENT_SKILL_REGISTRY_VERSION", "none"
        ),
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
        "targets": {
            "control": [o["target"] for o in control_outcomes],
            "treated": [o["target"] for o in treated_outcomes],
        },
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
