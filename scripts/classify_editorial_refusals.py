#!/usr/bin/env python3
"""Which rule is actually holding each refused editorial frame?

`measure_oracle_discrimination.py` reports *how many* printed fingerings the
verifier refuses. This reports *why*, which is the only form of the number that
tells you what to work on next.

The attribution has to be about the frame, not about one realisation of it. A
frame is refused when **every** reading of the editor's fingering is RED, so a
violation type appearing in some realisation proves nothing -- another reading
may avoid it. The question that has an actionable answer is: *if this one rule
were dropped, would some realisation survive?* That is exactly "does any
realisation violate nothing but this rule", and it is what the script counts.

Frames that no single rule unblocks are reported separately with their minimal
blocking sets, because they cost more than one change and should not be added to
any rule's tally.

If the verifier were perfect this script would print zero refused frames.
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
sys.path.insert(0, str(ROOT / "scripts"))

from measure_oracle_discrimination import (  # noqa: E402
    MAX_REALISATIONS,
    _admitted_realisation,
    _editorial_frames,
    _frame_tab,
)

from fretsure.oracle.core import CHECKER_VERSION, check_playability  # noqa: E402
from fretsure.oracle.profiles import MEDIAN_HAND, Profile  # noqa: E402
from fretsure.solver.candidates import candidates  # noqa: E402

RESULT_SCHEMA: Final = "fretsure-editorial-refusal-attribution@0.1.0"


def _violated_sets(
    frame: dict[str, object], profile: Profile
) -> list[frozenset[str]] | None:
    """The violation types of every realisation of the editor's fingering.

    Enumerated the same way `_admitted_realisation` does, so the population here
    is the population that produced the refusal -- including the rule that the
    finger labels are the editor's and only open strings may take finger 0.
    """

    tuning = frame["tuning"]
    capo = frame["capo"]
    wanted = frame["fingers"]
    per_pitch = []
    for pitch in frame["sounding"]:
        options = []
        for string, fret in candidates(pitch, tuning, capo, profile.max_fret):
            if fret == 0:
                options.append((string, fret, 0))
                continue
            allowed = wanted.get(pitch)
            for finger in (sorted(allowed) if allowed else (1, 2, 3, 4)):
                options.append((string, fret, finger))
        if not options:
            return None
        per_pitch.append(options)
    total = 1
    for options in per_pitch:
        total *= len(options)
    if total > MAX_REALISATIONS:
        return None

    out: list[frozenset[str]] = []
    for combo in itertools.product(*per_pitch):
        strings = [c[0] for c in combo]
        if len(set(strings)) != len(strings):
            continue
        result = check_playability(_frame_tab(list(combo), tuning, capo), profile)
        out.append(frozenset(str(d.violation_type) for d in result.diagnostics))
    return out


def _minimal(sets: list[frozenset[str]]) -> list[frozenset[str]]:
    """Blocking sets no smaller blocking set is contained in."""

    unique = {s for s in sets if s}
    return sorted(
        (s for s in unique if not any(t < s for t in unique)),
        key=lambda s: (len(s), sorted(s)),
    )


def classify(profile: Profile, splits: frozenset[str]) -> dict[str, object]:
    frames = _editorial_frames(splits)
    refused: list[dict[str, object]] = []
    admitted = 0
    unjudged = 0
    for frame in frames:
        realisation = _admitted_realisation(frame, profile)
        if realisation is None:
            unjudged += 1
            continue
        if realisation:
            admitted += 1
            continue
        sets = _violated_sets(frame, profile)
        if sets is None or not sets:
            unjudged += 1
            continue
        minimal = _minimal(sets)
        refused.append({
            "id": frame["id"],
            "split": frame["split"],
            "onset": str(frame["onset"]),
            "sounding": list(frame["sounding"]),
            "realisations": len(sets),
            "minimal_blocking_sets": [sorted(s) for s in minimal],
            "unblocked_by_one_rule": sorted(next(iter(s)) for s in minimal if len(s) == 1),
        })

    alone: Counter[str] = Counter()
    for row in refused:
        for rule in row["unblocked_by_one_rule"]:
            alone[rule] += 1
    appears: Counter[str] = Counter()
    for row in refused:
        for rule in {r for s in row["minimal_blocking_sets"] for r in s}:
            appears[rule] += 1
    needs_two = sum(1 for row in refused if not row["unblocked_by_one_rule"])

    return {
        "schema": RESULT_SCHEMA,
        "checker_version": CHECKER_VERSION,
        "profile": {
            "version": profile.version,
            "hand_span_mm": profile.hand_span_mm,
            "fingerprint": profile.fingerprint,
        },
        "splits": sorted(splits) if splits else ["all"],
        "editorial_frames": len(frames),
        "admitted": admitted,
        "unjudged": unjudged,
        "refused": len(refused),
        "unblocked_by_dropping_one_rule": dict(alone.most_common()),
        "appears_in_some_minimal_set": dict(appears.most_common()),
        "needs_more_than_one_rule": needs_two,
        "frames": refused,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", action="append", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = classify(MEDIAN_HAND, frozenset(args.split or ()))
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True, allow_nan=False))
        return 0

    print(f"{report['checker_version']}  {report['profile']['version']}  "
          f"splits {'+'.join(report['splits'])}")
    print(f"{report['editorial_frames']} editorial frames: "
          f"{report['admitted']} admitted, {report['refused']} refused, "
          f"{report['unjudged']} unjudged\n")
    if not report["refused"]:
        print("  nothing refused")
        return 0
    print("  dropping this one rule would admit the editor's fingering:")
    for rule, count in report["unblocked_by_dropping_one_rule"].items():
        print(f"    {rule:24s} {count:4d} frames")
    print(f"\n  {report['needs_more_than_one_rule']} frames need more than one rule dropped")
    for row in report["frames"]:
        if row["unblocked_by_one_rule"]:
            continue
        sets = " | ".join("+".join(s) for s in row["minimal_blocking_sets"][:3])
        print(f"    {row['id'][:34]:34s} @{row['onset']:>8s}  {sets}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
