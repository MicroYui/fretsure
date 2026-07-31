#!/usr/bin/env python3
"""Why does the solver find no configuration at all for a frame?

`no non-red extension within beam` is a search that wandered off and cannot be
attributed. `no feasible frame config` fails at one instant, so the refusing rule
can be named — which is how both real defects found this week were found.

The bucket was analysed on 2026-07-29 and judged genuine: ten frames impossible
on the instrument, two needing a fifth finger, the rest geometry at a median
margin of 26 mm that no defensible constant reaches. That verdict was measured
against `median@0.1`. Since then the span has gone 100 → 130 mm, `reach_mm` has
gone 50 → 65, and the monotonic rule has stopped refusing the wrist's slant. A
26 mm median margin against a 30 mm widening is exactly the situation where an
old "genuine" verdict stops being safe to inherit.

Two things this gets right because earlier versions got them wrong:

* **Fingers are not enumerated independently.** The monotonic and barre rules
  constrain them jointly, and a span measured on an assignment those rules refuse
  can be smaller than any legal one — which made the margins look like 5–8 mm
  when they were 11–26.
* **Frames too large to enumerate are reported, not absorbed.** Judging them on a
  truncated candidate list is a confound this project has already paid for.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fretsure.geometry import d_max, fingertip_xy  # noqa: E402
from fretsure.oracle.csp import assignment_valid  # noqa: E402
from fretsure.oracle.profiles import MEDIAN_HAND, Profile, optimistic  # noqa: E402
from fretsure.solver.candidates import candidates  # noqa: E402
from fretsure.tab import TabNote  # noqa: E402

RESULT_SCHEMA: Final = "fretsure-frame-config-attribution@0.1.0"
NO_CONFIG: Final = "no feasible frame config"
# Bounded like the rest of the instruments here: past this a piece is reported as
# unjudged rather than judged on part of its candidate space.
MAX_TRIALS: Final = 400_000


def _placements(
    pitches: list[int], tuning: tuple[int, ...], capo: int, profile: Profile
) -> list[list[tuple[int, int]]]:
    per_pitch = [
        list(candidates(pitch, tuning, capo, profile.max_fret)) for pitch in pitches
    ]
    if any(not options for options in per_pitch):
        return []
    out = []
    for combo in itertools.product(*per_pitch):
        strings = [string for string, _fret in combo]
        if len(set(strings)) == len(strings):
            out.append(list(combo))
    return out


def _monotone_and_barre_ok(
    fretted: list[TabNote], assignment: tuple[int, ...]
) -> bool:
    """The clauses that constrain fingers jointly, stated once.

    Kept separate from the span clause so a margin can be measured on assignments
    these accept, which is the only kind the solver would ever build.
    """

    for i, na in enumerate(fretted):
        for j, nb in enumerate(fretted):
            if i == j:
                continue
            fa, fb = assignment[i], assignment[j]
            if na.fret < nb.fret and fa > fb and na.string <= nb.string:
                return False
            if fa == fb and na.fret != nb.fret:
                return False
    return True


def _excess(
    fretted: list[TabNote], assignment: tuple[int, ...], profile: Profile, capo: int
) -> float:
    """Millimetres past `d_max` for the pair that binds, or 0.0 if none does."""

    worst = 0.0
    for i in range(len(fretted)):
        for j in range(i + 1, len(fretted)):
            if assignment[i] == assignment[j]:
                continue
            pa = fingertip_xy(fretted[i].string, capo + fretted[i].fret,
                              profile.string_length_mm)
            pb = fingertip_xy(fretted[j].string, capo + fretted[j].fret,
                              profile.string_length_mm)
            assert pa is not None and pb is not None
            over = abs(pa[0] - pb[0]) - d_max(assignment[i], assignment[j],
                                              profile.hand_span_mm)
            worst = max(worst, over)
    return worst


def classify_frame(
    pitches: list[int], tuning: tuple[int, ...], capo: int, profile: Profile
) -> dict[str, object]:
    combos = _placements(pitches, tuning, capo, profile)
    if not combos:
        return {"verdict": "no assignment puts them on distinct strings"}

    trials = 0
    best: float | None = None
    monotone_possible = False
    for combo in combos:
        fretted = [
            TabNote(0, 1, string, fret, 0, "p")  # type: ignore[arg-type]
            for string, fret in combo
            if fret > 0
        ]
        if not fretted:
            return {"verdict": "the frame alone is fine -- the refusal is the history"}
        # `check_finger_count` limits distinct *frets*, not fretted notes: a barre
        # lets every note at one fret share a finger. Skipping on note count
        # instead excluded exactly the barre shapes and reported them as needing a
        # fifth finger, which inflated that line and emptied the geometry one.
        if len({note.fret for note in fretted}) > 4:
            continue
        for assignment in itertools.product(range(1, 5), repeat=len(fretted)):
            trials += 1
            if trials > MAX_TRIALS:
                return {"verdict": "unjudged: enumeration exceeded the bound"}
            if not _monotone_and_barre_ok(fretted, assignment):
                continue
            monotone_possible = True
            if assignment_valid(fretted, assignment, profile, capo=capo):
                return {
                    "verdict": "the frame alone is fine -- the refusal is the history"
                }
            over = _excess(fretted, assignment, profile, capo)
            best = over if best is None else min(best, over)

    if not monotone_possible:
        if all(len({f for _s, f in combo if f > 0}) > 4 for combo in combos):
            return {"verdict": "more distinct frets than fingers"}
        return {"verdict": "no assignment satisfies the monotonic/barre rules"}
    # A margin of zero means the closest legal assignment sits inside `d_max` and
    # was still refused, so the span is not what refused it. What is left in
    # `assignment_valid` is the barre-occupancy clause: a barre presses every
    # string it crosses, so nothing between its ends may be stopped nearer the
    # nut. Reporting that as "geometry, 0.0 mm" would put a rule with no
    # millimetre margin into a table of millimetre margins.
    if best is None or best <= 1e-9:
        return {"verdict": "a barre crosses a string stopped behind it"}
    return {"verdict": "geometry", "margin_mm": round(best, 1)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="a full (not --summary-only) gate report")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = json.loads(args.report.read_text(encoding="utf-8"))
    profile = optimistic(MEDIAN_HAND)  # what frame_configs itself uses

    rows = []
    for record in report["examples"]:
        infeasible = record.get("infeasible")
        if not isinstance(infeasible, dict) or infeasible.get("reason") != NO_CONFIG:
            continue
        outcome = classify_frame(
            list(infeasible["pitches"]),
            tuple(record["tuning"]),
            int(record["capo"]),
            profile,
        )
        rows.append({"id": record["id"], "onset": infeasible.get("onset"),
                     "pitches": list(infeasible["pitches"]), **outcome})

    tally = Counter(str(row["verdict"]) for row in rows)
    margins = sorted(float(row["margin_mm"]) for row in rows if "margin_mm" in row)
    summary = {
        "schema": RESULT_SCHEMA,
        "configuration": report.get("configuration"),
        "versions": report.get("versions"),
        "frames": len(rows),
        "verdicts": dict(tally.most_common()),
        "margin_mm": None if not margins else {
            "min": margins[0],
            "median": margins[len(margins) // 2],
            "max": margins[-1],
        },
    }
    if args.json:
        print(json.dumps({**summary, "rows": rows}, indent=1, sort_keys=True))
        return 0
    print(f"{summary['frames']} frames with no feasible configuration\n")
    for verdict, count in tally.most_common():
        print(f"  {count:4d}  {verdict}")
    if margins:
        print(f"\n  geometry margin past d_max, mm: min {margins[0]:.1f}  "
              f"median {margins[len(margins) // 2]:.1f}  max {margins[-1]:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
