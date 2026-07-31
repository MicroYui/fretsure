#!/usr/bin/env python3
"""Ask whether the verifier accepts music humans demonstrably play.

Every example in the licensed published-score corpus is a piece that guitarists
have performed for a century or more.  Running the production solver over all of
them turns "is the physical model right?" into a number that moves when the
model improves and drops when it regresses.  A rejection here is a proven false
negative with a named human author, not a modelling opinion.

The report also carries the falsifier for the obvious cheat: ``sustain_retention``
is realized sounding time over notated time.  A model that buys acceptance by
quietly dropping sustain shows up as a low retention, not as a better score.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from fractions import Fraction
from pathlib import Path
from typing import Final, cast

from fretsure.geometry import STANDARD_TUNING, note_pitch
from fretsure.ir import Note, VoiceRole
from fretsure.oracle.core import CHECKER_VERSION, check_playability
from fretsure.oracle.input import ORACLE_INPUT_SCHEMA_VERSION, SolverInputError
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.solver.api import FINGERING_SOLVER_VERSION, Infeasible
from fretsure.solver.left_hand import LEFT_HAND_MODEL_VERSION
from fretsure.solver.score import (
    SCORE_SOLVER_VERSION,
    solve_fingering_score,
    solve_fingering_score_choosing_capo,
)
from fretsure.solver.sustain import SUSTAIN_RETENTION_FLOOR
from fretsure.tab import Tab

ROOT: Final = Path(__file__).resolve().parents[1]
CORPUS_DIR: Final = ROOT / "data" / "score_corpus"
# The three artifacts this gate was frozen against.  They stay named rather
# than discovered so that every later expansion can still be reported against
# the same 58 scores: a moving denominator would quietly rewrite the history of
# every measurement taken on it.
BASELINE_CORPUS: Final = (
    CORPUS_DIR / "carcassi_op59.json",
    CORPUS_DIR / "mutopia_pd_additional.json",
    CORPUS_DIR / "mutopia_cc_by_sa.json",
)
DEFAULT_CORPUS: Final = (
    *BASELINE_CORPUS,
    *sorted(
        path
        for path in CORPUS_DIR.glob("mutopia_expanded_*.json")
        if not path.name.endswith("_manifest.json")
    ),
)
RESULT_SCHEMA: Final = "fretsure-repertoire-playability@0.1.0"


def _fraction(value: object, path: str) -> Fraction:
    if (
        type(value) is not list
        or len(value) != 2
        or any(type(part) is not int for part in value)
        or value[1] <= 0
    ):
        raise ValueError(f"{path} must be [integer numerator, positive denominator]")
    return Fraction(value[0], value[1])


def _notes(example: dict[str, object], example_id: str) -> tuple[Note, ...]:
    raw = example.get("notes")
    if type(raw) is not list or not raw:
        raise ValueError(f"{example_id}: notes must be a non-empty array")
    notes: list[Note] = []
    for index, entry in enumerate(raw):
        if type(entry) is not dict:
            raise ValueError(f"{example_id}.notes[{index}] must be an object")
        pitch = entry.get("pitch")
        voice = entry.get("voice")
        if type(pitch) is not int or voice not in ("melody", "bass", "harmony"):
            raise ValueError(f"{example_id}.notes[{index}] has no exact pitch/voice")
        notes.append(
            Note(
                onset=_fraction(entry.get("onset"), f"{example_id}.notes[{index}].onset"),
                duration=_fraction(
                    entry.get("duration"), f"{example_id}.notes[{index}].duration"
                ),
                pitch=pitch,
                voice=cast(VoiceRole, voice),
            )
        )
    return tuple(sorted(notes, key=lambda note: (note.onset, note.pitch)))


def sustain_retention(target: tuple[Note, ...], tab: Tab) -> dict[str, object]:
    """Realized sounding time over notated time, overall and for melody alone.

    A tab note is matched to its target note by sounding pitch at the same
    onset, so a shortened hold is visible as a shortfall rather than hidden.
    """

    melody_keys = {(note.onset, note.pitch) for note in target if note.voice == "melody"}
    notated = sum((note.duration for note in target), Fraction(0))
    melody_notated = sum(
        (note.duration for note in target if note.voice == "melody"), Fraction(0)
    )
    realized = Fraction(0)
    melody_realized = Fraction(0)
    for note in tab.notes:
        realized += note.duration
        sounding = note_pitch(note.string, note.fret, tab.tuning, tab.capo)
        if (note.onset, sounding) in melody_keys:
            melody_realized += note.duration
    return {
        "notated_beats": str(notated),
        "realized_beats": str(realized),
        "retention": None if notated == 0 else str(realized / notated),
        "melody_notated_beats": str(melody_notated),
        "melody_realized_beats": str(melody_realized),
        "melody_retention": (
            None if melody_notated == 0 else str(melody_realized / melody_notated)
        ),
    }


def evaluate_example(
    example: dict[str, object],
    *,
    profile: Profile,
    beam: int,
    choose_capo: bool = False,
) -> dict[str, object]:
    example_id = str(example.get("id"))
    notes = _notes(example, example_id)
    raw_tuning = example.get("tuning")
    tuning = (
        STANDARD_TUNING
        if type(raw_tuning) is not list
        else tuple(int(cast(int, value)) for value in raw_tuning)
    )
    capo = int(cast(int, example.get("capo") or 0))
    raw_signature = example.get("time_signature")
    beats_per_bar = (
        4 if type(raw_signature) is not list else int(cast(int, raw_signature[0]))
    )
    tempo = float(cast(float, example.get("tempo_bpm") or 90))
    record: dict[str, object] = {
        "id": example_id,
        "composer": example.get("composer"),
        "title": example.get("title"),
        "split": example.get("split"),
        "notes": len(notes),
        "tuning": list(tuning),
        "capo": capo,
    }
    # Off by default and reported as a separate number, never replacing the
    # recorded one: choosing a capo is a real arranging liberty, and a gate that
    # silently started taking it would make every earlier measurement
    # incomparable.
    solver = solve_fingering_score_choosing_capo if choose_capo else solve_fingering_score
    try:
        solved = solver(
            notes,
            tuning,
            capo,
            profile,
            tempo_bpm=tempo,
            beats_per_bar=beats_per_bar,
            beam=beam,
        )
    except SolverInputError as error:
        record["outcome"] = "UNSUPPORTED_INPUT"
        record["diagnostics"] = [
            {"code": diagnostic.code.value, "path": diagnostic.path}
            for diagnostic in error.diagnostics[:8]
        ]
        return record
    if isinstance(solved, Infeasible):
        record["outcome"] = "INFEASIBLE"
        record["infeasible"] = {
            "code": solved.code.value,
            "reason": solved.reason,
            "onset": None if solved.onset is None else str(solved.onset),
            "pitches": list(solved.pitches),
        }
        return record
    verdict = check_playability(
        solved,
        profile,
        tempo_bpm=tempo,
        beats_per_bar=beats_per_bar,
    )
    record["outcome"] = verdict.verdict
    record["diagnostic_types"] = sorted(
        {diagnostic.violation_type for diagnostic in verdict.diagnostics}
    )
    record["sustain"] = sustain_retention(notes, solved)
    return record


def run(
    paths: tuple[Path, ...],
    *,
    profile: Profile,
    beam: int,
    choose_capo: bool = False,
) -> dict[str, object]:
    records: list[dict[str, object]] = []
    for path in paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        for example in document["examples"]:
            records.append(
                evaluate_example(
                    example, profile=profile, beam=beam, choose_capo=choose_capo
                )
            )
    outcomes = Counter(str(record["outcome"]) for record in records)
    reasons: Counter[str] = Counter()
    for record in records:
        infeasible = record.get("infeasible")
        if type(infeasible) is dict:
            reasons[str(infeasible.get("reason"))] += 1
    accepted = tuple(
        str(record["id"]) for record in records if record["outcome"] in ("GREEN", "AMBER")
    )
    return {
        "schema": RESULT_SCHEMA,
        "configuration": {
            "beam": beam,
            "profile_version": profile.version,
            "profile_fingerprint": profile.fingerprint,
            "corpus": [path.name for path in paths],
            "sustain_retention_floor": str(SUSTAIN_RETENTION_FLOOR),
            "choose_capo": choose_capo,
        },
        "versions": {
            "checker": CHECKER_VERSION,
            "fingering_solver": FINGERING_SOLVER_VERSION,
            "score_solver": SCORE_SOLVER_VERSION,
            "left_hand_model": LEFT_HAND_MODEL_VERSION,
            "input_schema": ORACLE_INPUT_SCHEMA_VERSION,
        },
        "aggregate": {
            "examples": len(records),
            "accepted": len(accepted),
            "outcome_counts": dict(sorted(outcomes.items())),
            "infeasible_reason_counts": dict(sorted(reasons.items())),
            # Reported separately and always, so that a corpus expansion cannot
            # move the number every earlier measurement was taken against.
            "baseline_subset": _baseline_summary(records),
        },
        "accepted_ids": list(accepted),
        "examples": records,
    }


def _baseline_summary(records: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """The frozen 58-score slice, however much larger the corpus has become."""

    baseline_ids: set[str] = set()
    for path in BASELINE_CORPUS:
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        baseline_ids.update(str(example["id"]) for example in payload["examples"])
    subset = [record for record in records if str(record["id"]) in baseline_ids]
    accepted = [r for r in subset if r["outcome"] in ("GREEN", "AMBER")]
    return {
        "examples": len(subset),
        "accepted": len(accepted),
        "accepted_ids": sorted(str(r["id"]) for r in accepted),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evaluate_repertoire_playability",
        description="Solve every published-score example and report the verdicts.",
    )
    parser.add_argument("--corpus", type=Path, action="append")
    parser.add_argument("--beam", type=int, default=16)
    parser.add_argument(
        "--choose-capo",
        action="store_true",
        help="let a refused score try other capo positions; reported separately",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="print the aggregate block instead of the full per-example report",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = tuple(args.corpus) if args.corpus else DEFAULT_CORPUS
    try:
        report = run(
            paths, profile=MEDIAN_HAND, beam=args.beam, choose_capo=args.choose_capo
        )
    except (OSError, ValueError, KeyError) as error:
        print(f"repertoire evaluation failed: {error}", file=sys.stderr)
        return 2
    # The summary carries what produced it, not only what came out. Every frozen
    # gate artifact before 2026-07-31 was a bare aggregate: no mode, no beam, no
    # profile, no checker version. That is how 146/292 came to be quoted for
    # months without anyone being able to tell it was the `--choose-capo` figure,
    # and how it was then "corrected" to a number measured the other way.
    payload = (
        {key: report[key] for key in ("schema", "configuration", "versions", "aggregate")}
        if args.summary_only
        else report
    )
    text = json.dumps(payload, indent=1, sort_keys=True, allow_nan=False)
    if args.output is None:
        print(text)
    else:
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
