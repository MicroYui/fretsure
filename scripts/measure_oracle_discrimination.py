#!/usr/bin/env python3
"""Where does the verifier's boundary sit, relative to what guitarists write?

Every judgement of a change to this oracle has been scored against 1,718 raw-LLM
tabs presumed unplayable because a language model produced them without a
solver. That presumption is not a physical property, and it was wrong: eleven of
those tabs are G major chords, so the guard spent months vetoing the fix for a
defect that made an open G uncertifiable.

Both sides can come from the published repertoire instead, and then no
presumption is involved.

**Positives** are frames where a publisher's editor printed which finger plays
which note. The labels give the finger only, so the test is whether *any*
(string, fret) assignment consistent with them survives -- a frame where none
does is the verifier refusing, under every possible reading, a fingering a human
committed to in print.

**Negatives** are those same frames with one note displaced along the neck by a
growing number of frets. At twelve frets the fingers are a fifth of a metre
apart and no hand of any size makes the shape, so a verifier that still admits
it is not discriminating at all.

What matters is the *curve*, not either endpoint. A well-placed boundary admits
nearly everything at zero displacement and refuses nearly everything far out,
turning over somewhere in between. A boundary that already refuses at zero is
cutting into real music; one that still admits at twelve frets is a rubber
stamp. Reporting a single threshold would hide which of those is happening.

Nothing here needs a player: the positives are published fingerings and the
negatives are the same music pulled apart by a measured distance.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import Counter
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fretsure.geometry import press_x  # noqa: E402
from fretsure.ir import Note  # noqa: E402
from fretsure.oracle.core import CHECKER_VERSION, check_playability  # noqa: E402
from fretsure.oracle.profiles import MEDIAN_HAND, Profile  # noqa: E402
from fretsure.solver.candidates import candidates  # noqa: E402
from fretsure.tab import Tab, TabNote  # noqa: E402

CORPUS_DIR: Final = ROOT / "data" / "score_corpus"
DISPLACEMENTS: Final = (0, 1, 2, 3, 6, 12)
RESULT_SCHEMA: Final = "fretsure-oracle-discrimination@0.1.0"
# Enough to enumerate a dense frame exhaustively; frames beyond it are counted
# separately rather than being silently judged on a truncated candidate list,
# which is a confound this project has already paid for once.
MAX_REALISATIONS: Final = 20_000


def _editorial_frames(splits: frozenset[str]) -> list[dict[str, object]]:
    """Every frame where an editor named two or more fingers."""

    frames: list[dict[str, object]] = []
    for path in sorted(CORPUS_DIR.glob("*.json")):
        if path.name.endswith("_manifest.json"):
            continue
        for example in json.loads(path.read_text(encoding="utf-8"))["examples"]:
            if splits and example["split"] not in splits:
                continue
            wanted: dict[Fraction, dict[int, frozenset[int]]] = {}
            for annotation in example["annotations"]:
                fingers = frozenset(f for f in annotation["accepted_fingers"] if 1 <= f <= 4)
                if not fingers:
                    continue
                onset = Fraction(*annotation["onset"])
                wanted.setdefault(onset, {})[annotation["pitch"]] = fingers
            if not wanted:
                continue
            notes = tuple(
                Note(Fraction(*n["onset"]), Fraction(*n["duration"]), n["pitch"], n["voice"])
                for n in example["notes"]
            )
            for onset, fingers in wanted.items():
                if len(fingers) < 2:
                    continue
                sounding = tuple(sorted(
                    n.pitch for n in notes if n.onset <= onset < n.onset + n.duration))
                if len(sounding) > len(example["tuning"]):
                    continue
                frames.append({
                    "id": example["id"], "split": example["split"],
                    "onset": onset, "sounding": sounding,
                    "fingers": fingers, "tuning": tuple(example["tuning"]),
                    "capo": example["capo"],
                    "tempo_bpm": float(example.get("tempo_bpm") or 90),
                })
    return frames


def _frame_tab(
    placements: list[tuple[int, int, int]], tuning: tuple[int, ...], capo: int
) -> Tab:
    """One frame, spelled the way the oracle requires a chord to be played.

    Right-hand assignment is not incidental here. Writing every note as a thumb
    pluck makes `check_right_hand` refuse the frame whatever the left hand is
    doing, and the first version of this script did exactly that -- reporting
    that the verifier refuses 100% of printed fingerings, which measured the
    spelling rather than the geometry. Five and six note chords are one gesture:
    the thumb sweeps a run of adjacent low strings, i-m-a take the top three.
    """

    ordered = sorted(placements)
    sweep = len(ordered) - 3 if len(ordered) > 4 else 0
    notes = []
    for index, (string, fret, finger) in enumerate(ordered):
        if index < sweep:
            right, group = "p", 1
        else:
            rank = index - sweep + (1 if sweep else 0)
            right, group = ("p", "i", "m", "a")[min(rank, 3)], 0
        notes.append(TabNote(Fraction(0), Fraction(1), string, fret, finger, right, group))
    return Tab(tuple(notes), tuning, capo)


def _admitted_realisation(
    frame: dict[str, object], profile: Profile
) -> list[tuple[int, int, int]] | None:
    """The realisation of the editor's fingering the verifier accepts, if any."""

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
    for combo in itertools.product(*per_pitch):
        strings = [c[0] for c in combo]
        if len(set(strings)) != len(strings):
            continue
        if check_playability(_frame_tab(list(combo), tuning, capo), profile).verdict != "RED":
            return list(combo)
    return []


def _displaced_admits(
    frame: dict[str, object], profile: Profile,
    realisation: list[tuple[int, int, int]], displacement: int,
) -> bool | None:
    """Judge *that* fingering with one note moved -- never a re-enumeration.

    Re-enumerating is what the first version did, asking "is there any
    realisation whose displaced form still passes". With dozens of realisations
    per frame some always survive, so a note pulled a third of a metre away
    looked admissible half the time. That measured the enumerator, not the
    verifier. The negative has to be the same hand shape, pulled apart.
    """

    fretted = [i for i, (_, f, lf) in enumerate(realisation) if f > 0 and lf > 0]
    if len(fretted) < 2:
        return None
    target = fretted[0]
    string, fret, finger = realisation[target]
    if fret + displacement > profile.max_fret:
        return None
    moved = list(realisation)
    moved[target] = (string, fret + displacement, finger)
    tab = _frame_tab(moved, frame["tuning"], frame["capo"])
    return check_playability(tab, profile).verdict != "RED"


def _admits(frame: dict[str, object], profile: Profile, displacement: int) -> bool | None:
    """Does any realisation of the editor's fingering survive, once displaced?

    ``None`` means the frame could not be judged -- too many realisations to
    enumerate, or the displacement runs off the fretboard -- and those are
    reported rather than folded into either side.
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

    judged = False
    for combo in itertools.product(*per_pitch):
        strings = [c[0] for c in combo]
        if len(set(strings)) != len(strings):
            continue
        placements = list(combo)
        if displacement:
            fretted = [i for i, (_, f, lf) in enumerate(placements) if f > 0 and lf > 0]
            if len(fretted) < 2:
                continue
            target = fretted[0]
            string, fret, finger = placements[target]
            if fret + displacement > profile.max_fret:
                continue
            placements[target] = (string, fret + displacement, finger)
        judged = True
        tab = _frame_tab(placements, tuning, capo)
        if check_playability(tab, profile).verdict != "RED":
            return True
    return False if judged else None


def measure(profile: Profile, splits: frozenset[str]) -> dict[str, object]:
    frames = _editorial_frames(splits)
    rows = []
    baseline = {id(frame): _admitted_realisation(frame, profile) for frame in frames}
    for displacement in DISPLACEMENTS:
        tally: Counter[str] = Counter()
        for frame in frames:
            realisation = baseline[id(frame)]
            if realisation is None:
                verdict = None
            elif not realisation:
                verdict = False           # nothing passes even undisplaced
            elif displacement == 0:
                verdict = True
            else:
                verdict = _displaced_admits(frame, profile, realisation, displacement)
            if verdict is None:
                tally["unjudged"] += 1
            elif verdict:
                tally["admitted"] += 1
            else:
                tally["refused"] += 1
        judged = tally["admitted"] + tally["refused"]
        millimetres = (
            press_x(1 + displacement, profile.string_length_mm)
            - press_x(1, profile.string_length_mm)
        ) if displacement else 0.0
        rows.append({
            "displacement_frets": displacement,
            "displacement_mm_at_the_nut": round(millimetres, 1),
            "admitted": tally["admitted"],
            "refused": tally["refused"],
            "unjudged": tally["unjudged"],
            "refused_rate": (tally["refused"] / judged) if judged else None,
        })
    return {
        "schema": RESULT_SCHEMA,
        "checker_version": CHECKER_VERSION,
        "profile": {"version": profile.version, "hand_span_mm": profile.hand_span_mm,
                    "fingerprint": profile.fingerprint},
        "splits": sorted(splits) if splits else ["all"],
        "editorial_frames": len(frames),
        "curve": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", action="append", default=None,
                        help="restrict to train/dev/test; repeatable")
    parser.add_argument("--hand-span", type=float, default=None,
                        help="override hand_span_mm to sweep the boundary")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    profile = MEDIAN_HAND
    if args.hand_span is not None:
        profile = replace(MEDIAN_HAND, hand_span_mm=args.hand_span)
    report = measure(profile, frozenset(args.split or ()))

    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True, allow_nan=False))
        return 0

    print(f"{report['checker_version']}  {report['profile']['version']} "
          f"hand_span {report['profile']['hand_span_mm']} mm  "
          f"splits {'+'.join(report['splits'])}")
    print(f"{report['editorial_frames']} frames where an editor named two or more fingers\n")
    print(f"{'displaced':>10s} {'mm':>7s}  {'admitted':>9s} {'refused':>8s} "
          f"{'unjudged':>9s}   refused")
    for row in report["curve"]:
        rate = row["refused_rate"]
        print(f"{row['displacement_frets']:7d} fr {row['displacement_mm_at_the_nut']:7.1f}  "
              f"{row['admitted']:9d} {row['refused']:8d} {row['unjudged']:9d}   "
              f"{'n/a' if rate is None else f'{100*rate:5.1f}%'}")
    zero = report["curve"][0]["refused_rate"]
    far = report["curve"][-1]["refused_rate"]
    if zero is not None and far is not None:
        print(f"\n  at zero displacement the verifier refuses {100*zero:.1f}% of fingerings "
              f"a human printed")
        print(f"  at {DISPLACEMENTS[-1]} frets it refuses {100*far:.1f}%")
        print(f"  separation: {100*(far - zero):.1f} points")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
