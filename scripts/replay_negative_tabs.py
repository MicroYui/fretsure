#!/usr/bin/env python3
"""Assert the oracle's verdict on known-unplayable tabs has not moved.

The 2026-07-17 benchmark collection produced 1,849 single-shot LLM tabs that
parse cleanly and are mostly faithful to the melody, yet none of them is
playable.  They are the best negative set this project will ever get for free:
real model output, real distribution, already judged.

This guard asserts *invariance*, not "no new acceptances".  Any movement in the
verdict multiset -- in either direction -- means the physical model changed, and
a change that was supposed to be confined to the solver has leaked into the
verifier.  The replay bundle lives outside Git, so the guard reports SKIPPED
rather than failing when it is absent.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Final

from fretsure.oracle.core import CHECKER_VERSION, check_playability
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.tab import Tab, tab_from_json

ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_BUNDLE: Final = (
    ROOT / "outputs/private/benchmark-v2-task9/attempt-004-replay-a/canonical"
)
RESULT_SCHEMA: Final = "fretsure-negative-tab-replay@0.1.0"

# Frozen on 2026-07-26 against oracle@0.3.0 / median@0.1, over the 1,718 distinct
# tabs behind 1,849 VALID_TAB raw rows.
#
# The collection itself recorded 1843 RED / 6 AMBER / 0 GREEN per row under
# oracle@0.2.0.  Today's checker accepts six of these raw model tabs outright:
# the 0.3.0 occupancy correction (docs/LEFT_HAND_SOLVER_V2.md:33), made so that
# standard open C and F chords stop reading as AMBER, also admitted them.  That
# divergence is recorded rather than smoothed over -- it is the only measured
# false-accept evidence this project has.
EXPECTED_VERDICTS: Final[dict[str, int]] = {"RED": 1651, "AMBER": 61, "GREEN": 6}


def load_raw_tabs(canonical: Path) -> tuple[Tab, ...]:
    """Rebuild every raw-baseline tab the collection actually produced."""

    wanted: set[str] = set()
    with (canonical / "rows.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("row_type") != "raw":
                continue
            outcome = row["payload"]["outcome"]
            digest = outcome.get("tab_blob_sha256")
            if outcome.get("status") == "VALID_TAB" and isinstance(digest, str):
                wanted.add(digest)
    contents: dict[str, object] = {}
    with (canonical / "blobs.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            blob = json.loads(line)
            if blob.get("kind") == "tab" and blob.get("sha256") in wanted:
                contents[blob["sha256"]] = blob["content"]
    missing = sorted(wanted - set(contents))
    if missing:
        raise ValueError(f"{len(missing)} referenced tab blobs are absent from the bundle")
    return tuple(tab_from_json(json.dumps(contents[digest])) for digest in sorted(wanted))


def replay(tabs: tuple[Tab, ...], profile: Profile) -> dict[str, object]:
    verdicts: Counter[str] = Counter()
    for tab in tabs:
        verdicts[check_playability(tab, profile).verdict] += 1
    observed = {name: verdicts.get(name, 0) for name in ("RED", "AMBER", "GREEN")}
    return {
        "schema": RESULT_SCHEMA,
        "checker_version": CHECKER_VERSION,
        "profile_version": profile.version,
        "profile_fingerprint": profile.fingerprint,
        "distinct_tabs": len(tabs),
        "expected": EXPECTED_VERDICTS,
        "observed": observed,
        "invariant_held": observed == EXPECTED_VERDICTS,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="replay_negative_tabs",
        description="Replay known-unplayable model tabs and assert the verdicts are unchanged.",
    )
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    canonical: Path = args.bundle
    if not (canonical / "rows.jsonl").is_file():
        print(
            f"SKIPPED: no replay bundle at {canonical}; this guard needs the "
            "owner-held collection artifacts",
            file=sys.stderr,
        )
        return 0
    try:
        tabs = load_raw_tabs(canonical)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"negative replay failed to load: {error}", file=sys.stderr)
        return 2
    report = replay(tabs, MEDIAN_HAND)
    print(json.dumps(report, indent=1, sort_keys=True, allow_nan=False))
    if not report["invariant_held"]:
        print(
            "VERDICT MULTISET MOVED: the oracle changed. A solver-only change "
            "must leave these judgements byte-identical.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
