#!/usr/bin/env python3
"""Repair the corpus pitches and tunings the converter never read.

`build_mutopia_lilypond_corpus.py` reads notes and nothing else -- no clef, no
`stringTunings`, no `\\transposition`. Nineteen of 292 scores therefore hold data
no guitar can produce. This applies the corrections, from an explicit table with
the evidence for each one attached, because the generator is upstream
(`m1_lilypond.py`, not vendored here) and a rebuild would otherwise put every
defect straight back. `tests/test_corpus_fits_the_instrument.py` is what fails if
that happens.

Three evidence levels, kept apart on purpose:

* **declared** -- the source says so in LilyPond's own words. `capricho-arabe.ly`
  line 493 is a live `stringTunings = #guitar-drop-d-tuning`;
  `faure_op78_sicilienne.ly` has the same line commented out beside a
  `\\markup { \\circle 6 = D }`. Both were recorded as standard tuning.
* **measured** -- the recorded range is impossible and lands exactly on the
  instrument an octave down. Of the thirteen, three go from "pitch unreachable"
  to GREEN under the shift and two more to AMBER; none is playable as recorded.
  Source metadata cannot decide this class and that was checked rather than
  assumed: `\\transposition` appears in ten scores whose pitches are fine and in
  only two of the thirteen, so it is neither necessary nor sufficient.
* **inferred** -- the range needs the sixth string lowered and the source says
  nothing. Recording the tuning is the smallest change that makes the notes
  producible, and drop D is an ordinary guitar tuning, but no declaration backs
  it.

One score is deliberately **not** repaired. `aguado-op03n05` has 23 notes of 345
an octave high, scattered across the piece; a whole-score transform is wrong for
it and shifting 23 notes chosen by their range is fitting the repair to the
symptom. It is quarantined by name in the test instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

CORPUS_DIR: Final = ROOT / "data" / "score_corpus"
DROP_D: Final = [38, 45, 50, 55, 59, 64]
DROP_E_FLAT: Final = [39, 45, 50, 55, 59, 64]

# id -> (semitone shift, replacement tuning or None, evidence)
CORRECTIONS: Final[dict[str, tuple[int, list[int] | None, str]]] = {
    # declared in the source, ignored by the converter
    "mutopia-cc-by-sa-capricho-arabe": (0, DROP_D, "declared: live stringTunings"),
    "mutopia-faure-op78-sicilienne": (0, DROP_D, "declared: commented stringTunings + markup"),
    # measured: impossible as recorded, exact an octave down
    "mutopia-bwv-1006a-1g": (-12, None, "measured: unreachable -> GREEN"),
    "mutopia-carcassi-op60-09-movement-1": (-12, None, "measured: unreachable -> GREEN"),
    "mutopia-horetzky11-movement-1": (-12, None, "measured: unreachable -> GREEN"),
    "mutopia-horetzky35-movement-1": (-12, None, "measured: refused -> AMBER"),
    "mutopia-giuliani-op50n12": (-12, None, "measured: refused -> AMBER"),
    "mutopia-giuliani-op50n25": (-12, None, "measured: range fits exactly an octave down"),
    "mutopia-giuliani-op50n26": (-12, None, "measured: range fits exactly an octave down"),
    "mutopia-giuliani-op50n32": (-12, None, "measured: range fits exactly an octave down"),
    "mutopia-spanish-romance": (-12, None, "measured: range fits exactly an octave down"),
    "mutopia-sor-op-1-5-1": (-12, None, "measured: range fits exactly an octave down"),
    "mutopia-sor-op-1-5-3": (-12, None, "measured: range fits exactly an octave down"),
    "mutopia-horetzky49-movement-1": (-12, None, "measured: range fits exactly an octave down"),
    "mutopia-knjze12monferl-movement-1": (-12, None, "measured: range fits exactly an octave down"),
    "mutopia-sor-op1-3": (-12, DROP_D, "measured: octave, then the range needs drop D"),
    # inferred: the range needs a lowered sixth string, the source is silent
    "mutopia-cc-by-sa-carcassi-op60-23": (0, DROP_D, "inferred: lowest note is D2"),
    "mutopia-aguado-op03n04": (0, DROP_E_FLAT, "inferred: lowest note is Eb2"),
}

# Not repaired, and named rather than filtered by a rule, so that the exemption
# is as visible as the corrections.
QUARANTINED: Final[dict[str, str]] = {
    "mutopia-aguado-op03n05": (
        "23 of 345 notes an octave high, scattered; a whole-score transform is "
        "wrong and selecting notes by their range fits the repair to the symptom"
    ),
}


def repair(*, apply: bool) -> dict[str, object]:
    changed: list[dict[str, object]] = []
    for path in sorted(CORPUS_DIR.glob("*.json")):
        if path.name.endswith("_manifest.json"):
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        touched = False
        for example in document["examples"]:
            correction = CORRECTIONS.get(str(example.get("id")))
            if correction is None:
                continue
            shift, tuning, evidence = correction
            before = [min(n["pitch"] for n in example["notes"]),
                      max(n["pitch"] for n in example["notes"])]
            if shift:
                for note in example["notes"]:
                    note["pitch"] += shift
                for annotation in example.get("annotations", ()):
                    if annotation.get("pitch") is not None:
                        annotation["pitch"] += shift
            if tuning is not None:
                example["tuning"] = list(tuning)
            touched = True
            changed.append({
                "id": example["id"], "corpus": path.name, "shift": shift,
                "tuning": tuning, "evidence": evidence, "range_before": before,
                "range_after": [min(n["pitch"] for n in example["notes"]),
                                max(n["pitch"] for n in example["notes"])],
            })
        if touched and apply:
            path.write_text(
                json.dumps(document, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
    return {"applied": apply, "corrections": len(changed),
            "quarantined": sorted(QUARANTINED), "changed": changed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write the corrected corpus; otherwise report only")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = repair(apply=args.apply)
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return 0
    print(f"{report['corrections']} corrections "
          f"({'applied' if args.apply else 'dry run'}), "
          f"{len(report['quarantined'])} quarantined\n")
    for row in report["changed"]:
        tuning = "" if row["tuning"] is None else f"  tuning -> {row['tuning']}"
        print(f"  {row['id'][:40]:40s} {row['shift']:+3d}  "
              f"{row['range_before']} -> {row['range_after']}{tuning}")
        print(f"  {'':40s}      {row['evidence']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
