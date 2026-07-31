#!/usr/bin/env python3
"""Does every corpus score fit on the instrument it claims to be written for?

Guitar music is conventionally notated an octave above sounding pitch. LilyPond
note names are sounding pitch, but engravers routinely type what they see and let
`\\clef "treble_8"` handle the display, so a Mutopia source may hold either. The
converter never consults the clef -- `build_mutopia_lilypond_corpus.py` has no
octave, clef or transpose handling at all -- so scores entered at written pitch
come out twelve semitones high.

Only the ones that then exceed the fretboard announce themselves. The rest are
solved, scored and reported against music a fifth of the corpus above where it
belongs, and the failures land in whichever bucket they reach first.

The signature is a range that no guitar can play and that lands exactly on the
guitar when shifted down an octave. That is evidence, not proof: the sources for
the affected scores are not vendored here, so this reports rather than repairs.
Transposing a corpus on a hunch is how the first frame-config analysis came to be
retracted.

If the corpus were clean this would print nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fretsure.geometry import STANDARD_TUNING  # noqa: E402
from fretsure.oracle.profiles import MEDIAN_HAND  # noqa: E402

CORPUS_DIR: Final = ROOT / "data" / "score_corpus"
RESULT_SCHEMA: Final = "fretsure-corpus-range-audit@0.1.0"
OCTAVE: Final = 12


def instrument_range(tuning: tuple[int, ...], capo: int, max_fret: int) -> tuple[int, int]:
    """Lowest and highest pitch the instrument can produce, ignoring the hand."""

    return tuning[0] + capo, max(tuning) + capo + max_fret


def audit_example(example: dict[str, object], max_fret: int) -> dict[str, object] | None:
    raw_tuning = example.get("tuning")
    tuning = (
        STANDARD_TUNING
        if type(raw_tuning) is not list
        else tuple(int(value) for value in raw_tuning)
    )
    capo = int(example.get("capo") or 0)
    pitches = [int(note["pitch"]) for note in example["notes"]]  # type: ignore[index]
    low, high = min(pitches), max(pitches)
    reach_low, reach_high = instrument_range(tuning, capo, max_fret)
    if reach_low <= low and high <= reach_high:
        return None

    below = sum(1 for pitch in pitches if pitch < reach_low)
    above = sum(1 for pitch in pitches if pitch > reach_high)

    # Three different repairs, so three different verdicts. Lowering the sixth
    # string is what a guitarist does for a piece that dips below the open E --
    # Capricho Arabe is in drop D and always has been -- and it is a fact about
    # the tuning the corpus failed to record, not about the notes. An octave is a
    # fact about how the source was typed. A score needing both needs both, and a
    # score needing neither is a transcription for something that is not a guitar.
    def fits(shift: int, drop: int) -> bool:
        lowered = (tuning[0] - drop, *tuning[1:])
        floor, ceiling = instrument_range(lowered, capo, max_fret)
        return floor <= low + shift and high + shift <= ceiling

    fits_down = fits(-OCTAVE, 0)
    drop = next((d for d in (1, 2) if fits(0, d)), None)
    drop_and_octave = next((d for d in (1, 2) if fits(-OCTAVE, d)), None)
    return {
        "id": str(example.get("id")),
        "split": example.get("split"),
        "notes": len(pitches),
        "range": [low, high],
        "instrument_range": [reach_low, reach_high],
        "below": below,
        "above": above,
        # A score that fits exactly one octave down is the written-pitch
        # signature. One that fits neither way is a different problem -- a
        # transcription for another instrument, or a tuning the corpus did not
        # record, and the two must not be reported as one number.
        "verdict": (
            "written pitch: fits exactly an octave down"
            if fits_down
            else f"unrecorded tuning: fits with the sixth string down {drop}"
            if drop is not None
            else f"written pitch and sixth string down {drop_and_octave}"
            if drop_and_octave is not None
            else "fits no octave or sixth-string tuning -- not a guitar score"
        ),
    }


def audit(max_fret: int) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    examples = 0
    for path in sorted(CORPUS_DIR.glob("*.json")):
        if path.name.endswith("_manifest.json"):
            continue
        for example in json.loads(path.read_text(encoding="utf-8"))["examples"]:
            examples += 1
            row = audit_example(example, max_fret)
            if row is not None:
                rows.append({**row, "corpus": path.name})
    return {
        "schema": RESULT_SCHEMA,
        "max_fret": max_fret,
        "examples": examples,
        "unplayable": len(rows),
        "verdicts": dict(Counter(str(row["verdict"]) for row in rows).most_common()),
        "scores": sorted(rows, key=lambda row: -int(row["notes"])),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-fret", type=int, default=MEDIAN_HAND.max_fret)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = audit(args.max_fret)
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return 0
    print(f"{report['examples']} scores, {report['unplayable']} outside the instrument "
          f"(max fret {report['max_fret']})\n")
    for verdict, count in report["verdicts"].items():
        print(f"  {count:4d}  {verdict}")
    if report["scores"]:
        print()
        for row in report["scores"]:
            shifted = f"{row['range'][0] - OCTAVE}-{row['range'][1] - OCTAVE}"
            print(f"    {row['id'][:42]:42s} {row['notes']:5d} notes  "
                  f"{row['range'][0]}-{row['range'][1]}  ->  {shifted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
