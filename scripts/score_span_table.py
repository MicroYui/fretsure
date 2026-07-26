#!/usr/bin/env python3
"""Score a candidate left-hand span table against both sides at once.

Changing `d_max` changes the oracle, which is the highest-stakes edit in this
project.  A candidate table is only interesting if it moves both sides in the
right direction, so this refuses to report one without the other: repertoire
accepted, and every negative that would newly be *certified*.

The table is supplied as JSON so candidates can be scored without editing
source and without a rebuild:

    uv run --frozen python scripts/score_span_table.py --table '{"1-2": 0.62, ...}'

Keys are "i-j" with i <= j over fingers 1..4; any pair omitted keeps its
current value.  With no --table this reports the current model, which is the
baseline every candidate has to beat.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Final

import fretsure.geometry as geometry_module
from fretsure.ir import Note
from fretsure.oracle.core import check_playability
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.oracle.validation.mutation import MUTANTS, run_mutation_suite
from fretsure.solver.score import solve_fingering_score
from fretsure.tab import Tab

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from replay_negative_tabs import DEFAULT_BUNDLE, load_raw_tabs  # noqa: E402

RESULT_SCHEMA: Final = "fretsure-span-table-score@0.1.0"
CORPUS_FILES: Final = (
    "carcassi_op59.json",
    "mutopia_pd_additional.json",
    "mutopia_cc_by_sa.json",
)


def current_factors() -> dict[str, float]:
    """Read today's model back out as a per-pair table, whatever its shape."""

    return {
        f"{i}-{j}": geometry_module.d_max(i, j, 1.0)
        for i in range(1, 5)
        for j in range(i, 5)
    }


def install(factors: dict[str, float]) -> None:
    """Rebind d_max everywhere it was imported, not only where it is defined."""

    def patched(i: int, j: int, hand_span_mm: float) -> float:
        key = f"{min(i, j)}-{max(i, j)}"
        return factors[key] * hand_span_mm

    geometry_module.d_max = patched
    for name in ("fretsure.oracle.predicates", "fretsure.oracle.csp"):
        module = sys.modules.get(name)
        if module is not None and hasattr(module, "d_max"):
            module.d_max = patched  # type: ignore[attr-defined]


def corpus() -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for name in CORPUS_FILES:
        payload = json.loads((ROOT / "data/score_corpus" / name).read_text(encoding="utf-8"))
        out.extend(payload["examples"])
    return out


def solves(example: dict[str, object]) -> bool:
    rows, tuning = example["notes"], example["tuning"]
    signature = example.get("time_signature") or [4, 4]
    capo, tempo = example["capo"], example.get("tempo_bpm") or 90
    assert isinstance(rows, list) and isinstance(tuning, list) and isinstance(signature, list)
    assert isinstance(capo, int) and isinstance(tempo, int | float)
    result = solve_fingering_score(
        tuple(
            Note(Fraction(*row["onset"]), Fraction(*row["duration"]), row["pitch"], row["voice"])
            for row in rows
        ),
        tuple(tuning),
        capo,
        MEDIAN_HAND,
        tempo_bpm=float(tempo),
        beats_per_bar=int(signature[0]),
        beam=16,
    )
    return isinstance(result, Tab)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", type=str, default=None, help='JSON, e.g. {"3-4": 0.35}')
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--skip-repertoire", action="store_true")
    args = parser.parse_args()

    factors = current_factors()
    if args.table:
        override = json.loads(args.table)
        unknown = set(override) - set(factors)
        if unknown:
            raise SystemExit(f"unknown finger pairs: {sorted(unknown)}")
        factors.update({key: float(value) for key, value in override.items()})
        install(factors)

    negatives = load_raw_tabs(args.bundle)
    verdicts = Counter(check_playability(tab, MEDIAN_HAND).verdict for tab in negatives)
    triggers = tuple(tab for _n, _r, _m, tabs in MUTANTS for tab in tabs)
    trigger_leaks = sum(
        1 for tab in triggers if check_playability(tab, MEDIAN_HAND).verdict != "RED"
    )
    mutation = run_mutation_suite()

    payload: dict[str, object] = {
        "schema": RESULT_SCHEMA,
        "factors": factors,
        "negatives": len(negatives),
        # A GREEN here is the oracle certifying a tab known to be unplayable.
        "negative_verdicts": {key: verdicts[key] for key in ("RED", "AMBER", "GREEN")},
        "mutation_trigger_leaks": trigger_leaks,
        "mutation_survivors": list(mutation.survived),
    }
    if not args.skip_repertoire:
        examples = corpus()
        accepted = sorted(str(e["id"]) for e in examples if solves(e))
        payload["repertoire_accepted"] = len(accepted)
        payload["repertoire_total"] = len(examples)
        payload["repertoire_ids"] = accepted
    print(json.dumps(payload, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
