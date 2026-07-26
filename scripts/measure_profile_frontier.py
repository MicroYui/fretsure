#!/usr/bin/env python3
"""Measure what loosening the hand model buys, and what it costs.

`median@0.1` was never fitted to anything -- its own docstring says the numbers
are v1 placeholders and only their ordering was ever asserted.  The obvious
next step is to fit them to the published repertoire.  The obvious next step is
also how a verifier becomes a rubber stamp, because any hand large enough
accepts everything.  So this measures both sides of every move:

* **positives** -- the 58 published classical guitar scores in
  ``data/score_corpus``, played by humans for two centuries.  A model that
  refuses them is wrong about hands.
* **negatives** -- the 1,718 distinct raw-LLM tabs from the 2026-07-17
  benchmark collection, plus the mutation suite's trigger tabs.  A model that
  starts *certifying* these is wrong in the other direction, and a GREEN here
  is a false certification, not merely a loss of caution.

The output is a frontier, not a fitted point.  There is no setting that gains
repertoire while leaving the negatives alone, so the honest artifact is the
exchange rate at each step, together with which coordinates the negative set is
actually in a position to constrain: a coordinate whose violations are all
gross rather than marginal cannot validate a ten-percent change to itself, and
reporting "no measured cost" there would be reporting the absence of a
measurement.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Final

from fretsure.ir import Note
from fretsure.oracle.core import CHECKER_VERSION, check_playability
from fretsure.oracle.profiles import MEDIAN_HAND, Profile, validated_profile_snapshot
from fretsure.oracle.validation.mutation import MUTANTS
from fretsure.solver.score import solve_fingering_score
from fretsure.tab import Tab

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from replay_negative_tabs import DEFAULT_BUNDLE, load_raw_tabs  # noqa: E402

RESULT_SCHEMA: Final = "fretsure-profile-frontier@0.1.0"
CORPUS_FILES: Final = (
    "carcassi_op59.json",
    "mutopia_pd_additional.json",
    "mutopia_cc_by_sa.json",
)
# string_length_mm and max_fret describe the instrument, not the player.
SUBSETS: Final[dict[str, tuple[str, ...]]] = {
    "span": ("hand_span_mm",),
    "reach": ("reach_mm",),
    "shift": ("v_shift_mm_per_s",),
    "rate": ("r_max_hz",),
    "span+reach": ("hand_span_mm", "reach_mm"),
    "shift+rate": ("v_shift_mm_per_s", "r_max_hz"),
    "all": ("hand_span_mm", "reach_mm", "v_shift_mm_per_s", "r_max_hz"),
}


def corpus() -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for name in CORPUS_FILES:
        payload = json.loads((ROOT / "data/score_corpus" / name).read_text(encoding="utf-8"))
        out.extend(payload["examples"])
    return out


def solves(example: dict[str, object], profile: Profile) -> bool:
    rows = example["notes"]
    tuning = example["tuning"]
    signature = example.get("time_signature") or [4, 4]
    capo = example["capo"]
    tempo = example.get("tempo_bpm") or 90
    assert isinstance(rows, list) and isinstance(tuning, list) and isinstance(signature, list)
    assert isinstance(capo, int) and isinstance(tempo, int | float)
    result = solve_fingering_score(
        tuple(
            Note(Fraction(*row["onset"]), Fraction(*row["duration"]), row["pitch"], row["voice"])
            for row in rows
        ),
        tuple(tuning),
        capo,
        profile,
        tempo_bpm=float(tempo),
        beats_per_bar=int(signature[0]),
        beam=16,
    )
    return isinstance(result, Tab)


def loosened(subset: str, scale: float) -> Profile:
    """Scale one coordinate subset, clamped to the public profile domain."""

    fields = SUBSETS[subset]
    probe = replace(
        MEDIAN_HAND,
        version=f"frontier/{subset}/{scale}",
        **{field: getattr(MEDIAN_HAND, field) * scale for field in fields},
    )
    return validated_profile_snapshot(probe)


def negative_side(tabs: Sequence[Tab], triggers: Sequence[Tab], profile: Profile) -> dict[str, int]:
    counts = Counter(check_playability(tab, profile).verdict for tab in tabs)
    return {
        "red": counts["RED"],
        "amber": counts["AMBER"],
        # A GREEN here is the oracle certifying a tab known to be unplayable.
        "green": counts["GREEN"],
        "triggers_not_red": sum(
            1 for tab in triggers if check_playability(tab, profile).verdict != "RED"
        ),
    }


def boundary_distance(tabs: Sequence[Tab], profile: Profile) -> dict[str, int]:
    """How near the negatives sit to each rule, not merely which rules they break.

    A coordinate broken only by wide margins cannot validate a small change to
    itself: nothing in the set would move either way.
    """

    sole: Counter[str] = Counter()
    for tab in tabs:
        result = check_playability(tab, profile)
        if result.verdict != "RED":
            continue
        kinds = {diagnostic.violation_type for diagnostic in result.diagnostics}
        if len(kinds) == 1:
            sole.update(kinds)
    return dict(sole)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--scales", type=float, nargs="+", default=[1.05, 1.1, 1.25])
    parser.add_argument(
        "--subsets", nargs="+", default=sorted(SUBSETS), choices=sorted(SUBSETS)
    )
    parser.add_argument(
        "--skip-positives",
        action="store_true",
        help="measure only the negative side, which is fast",
    )
    args = parser.parse_args()

    negatives = load_raw_tabs(args.bundle)
    if not negatives:
        print(json.dumps({"schema": RESULT_SCHEMA, "status": "SKIPPED_NO_BUNDLE"}, indent=1))
        return 0
    triggers = tuple(tab for _n, _r, _m, tabs in MUTANTS for tab in tabs)
    examples = corpus()

    base_profile = validated_profile_snapshot(MEDIAN_HAND)
    baseline: dict[str, object] = {
        "profile": base_profile.version,
        **negative_side(negatives, triggers, base_profile),
        "sole_violation": boundary_distance(negatives, base_profile),
    }
    if not args.skip_positives:
        baseline["accepted"] = sum(1 for example in examples if solves(example, base_profile))

    points: list[dict[str, object]] = []
    for subset in args.subsets:
        for scale in args.scales:
            profile = loosened(subset, scale)
            point: dict[str, object] = {
                "subset": subset,
                "scale": scale,
                **negative_side(negatives, triggers, profile),
            }
            if not args.skip_positives:
                accepted = [
                    str(example["id"]) for example in examples if solves(example, profile)
                ]
                point["accepted"] = len(accepted)
            points.append(point)
            print(json.dumps(point, sort_keys=True), file=sys.stderr, flush=True)

    print(
        json.dumps(
            {
                "schema": RESULT_SCHEMA,
                "checker_version": CHECKER_VERSION,
                "examples": len(examples),
                "negatives": len(negatives),
                "trigger_tabs": len(triggers),
                "baseline": baseline,
                "frontier": points,
            },
            indent=1,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
