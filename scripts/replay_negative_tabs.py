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
# Re-frozen 2026-07-27 for the per-pair d_max table, deliberately and with the
# transitions recorded, because this guard exists to make exactly this kind of
# movement impossible to do quietly:
#
#     AMBER -> GREEN  4
#     RED   -> AMBER  1
#     RED   -> GREEN  0
#
# The last line is the one that mattered. Nothing the oracle had confidently
# refused became certified; the extra certifications came out of the band where
# it had already declined to commit. Every earlier candidate in this area was
# reported only as "false certifications rose from 6 to N" without anyone
# checking where the N came from.
#
# 2026-07-29: the numbers below moved, and the reason is that this guard was
# scoring in the wrong direction. It calls any drift toward GREEN a weakening,
# but these tabs are known-bad by *provenance* -- a language model wrote them
# without a solver -- and not one had ever been examined. Eleven of them are
# ordinary open-position chords.
#
# `oracle@0.6.0` floors `d_max` at the width of the neck, because the outermost
# string centres sit 52.5 mm apart at one fret while the (2, 3) and (3, 4)
# finger pairs allowed 50.0 mm. That refused a G major chord. Rendering the
# eleven tabs that move shows ten of them refused on the identical frame:
#
#     [3 0 x 0 x 3]  G B D G   fingers 2+3, strings 6 and 1, both at fret 3
#     along the neck 0.0 mm, across the strings 52.5 mm, old limit 50.0 mm
#
# The eleventh is the same shape at the sixth fret. So the new expectation is
# not a re-freeze of whatever the code now emits; it is the old expectation with
# eleven misclassifications removed, each one identified by sight.
#
#     RED   1650 -> 1650   nothing confidently refused was certified
#     AMBER   58 ->   47
#     GREEN   10 ->   21
#
# RED is unchanged, which is the property that actually protects the verifier.
# The remaining 47 AMBER and 21 GREEN have still never been inspected, so this
# guard continues to assert provenance rather than playability, and a future
# change that moves them needs the same treatment: look at them.
# 2026-07-29, again, and for the same reason as before it. `oracle@0.7.0`
# measures the span along the neck instead of in a straight line, because the
# fingers belong to one hand laid across the strings and spreading across them
# is what the hand is shaped to do. Four tabs move AMBER -> GREEN and two move
# RED -> AMBER; all six were rendered before this number was touched.
#
# The four newly certified are ordinary open-position textures. The one with the
# tightest margin has finger 3 on the first string at fret 3 and finger 2 on the
# fifth at fret 2 -- four strings apart, one fret apart, a shape inside any G or
# D voicing. It measured 53.6 mm in a straight line against a 52.5 mm limit and
# was refused by 1.1 mm; along the neck it is 33.4 mm.
#
# The two leaving RED are the same shape stretched: finger 2 on the sixth string
# and finger 4 on the first, three frets apart. They land in AMBER, so nothing
# confidently refused became certified -- the verifier declines rather than
# commits.
#
#     RED   1650 -> 1648
#     AMBER   47 ->   45
#     GREEN   21 ->   25
#
# What justifies this is not the counts but the discrimination curve, which
# improved at both ends on both splits: fewer printed fingerings refused, and
# stretched shapes caught *more* often, because the across-string component had
# been diluting the limit. See scripts/measure_oracle_discrimination.py.
# 2026-07-29, third movement this week, and the tabs behind it were rendered
# before the number was touched -- twice already that turned out to be the guard
# misclassifying ordinary playing rather than the verifier weakening.
#
# `oracle@0.7.0` raises the median span from 100.2 mm, which is one finger per
# fret in first position, to 129.9 mm, which is the first-position five-fret
# stretch. That is a technique decision, not a tuned constant: the old value was
# the beginner posture and refused two consecutive fingers two frets apart.
#
# All 24 tabs leaving RED are that shape and nothing else:
#
#     fingers (3,4) two frets apart   9
#     fingers (2,3) two frets apart   8
#     fingers (1,2) two frets apart   7
#
# None of the 24 reaches GREEN; two others move AMBER -> GREEN. Unlike the G
# major chords, these are genuine stretches -- a trained guitarist does them
# routinely, a beginner does not -- so this is a change to what the median
# profile *means*, and it is recorded as one rather than as a correction.
#
#     RED   1648 -> 1624
#     AMBER   45 ->   67
#     GREEN   25 ->   27
#
# reach_mm deliberately did not move with the span. An earlier attempt scaled it
# too, on the assumption that reach is half the span by identity, and that
# certified 27 further tabs by widening the shift-speed window -- a rule this
# change was never argued from. Reach is where the hand sits; span is how far it
# stretches, and a hand may cover less than it spans.
EXPECTED_VERDICTS: Final[dict[str, int]] = {"RED": 1624, "AMBER": 67, "GREEN": 27}


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
