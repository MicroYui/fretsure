#!/usr/bin/env python3
"""Compare the production solver with a public-domain fingering reference."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from fractions import Fraction
from pathlib import Path
from typing import Final, cast

from fretsure.bench.contracts import canonical_json_bytes
from fretsure.geometry import STANDARD_TUNING, note_pitch
from fretsure.ir import Note, VoiceRole
from fretsure.oracle.core import CHECKER_VERSION, check_playability
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.api import FINGERING_SOLVER_VERSION, Infeasible
from fretsure.solver.left_hand import LEFT_HAND_MODEL_VERSION
from fretsure.solver.score import SCORE_SOLVER_VERSION, solve_fingering_score

ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE: Final = (
    ROOT / "tests/fixtures/fingering_reference/carcassi_op59_prelude_1.json"
)
RESULT_SCHEMA: Final = "fretsure-left-hand-reference-result@0.1.0"


def _fraction(value: object, path: str) -> Fraction:
    if (
        type(value) is not list
        or len(value) != 2
        or any(type(part) is not int for part in value)
        or value[1] <= 0
    ):
        raise ValueError(f"{path} must be [integer numerator, positive denominator]")
    return Fraction(value[0], value[1])


def _fraction_wire(value: Fraction) -> list[int]:
    return [value.numerator, value.denominator]


def evaluate_fixture(path: Path, *, beam: int = 16) -> dict[str, object]:
    raw = path.read_bytes()
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("reference fixture must be UTF-8 JSON") from exc
    if type(document) is not dict:
        raise ValueError("reference fixture root must be an object")
    if document.get("schema") != "fretsure-left-hand-reference@0.1.0":
        raise ValueError("unsupported reference fixture schema")

    raw_notes = document.get("notes")
    raw_annotations = document.get("annotations")
    threshold = document.get("minimum_exact_matches")
    source = document.get("source")
    if (
        type(raw_notes) is not list
        or type(raw_annotations) is not list
        or type(threshold) is not int
        or type(source) is not dict
    ):
        raise ValueError("reference fixture has malformed top-level fields")

    notes: list[Note] = []
    for index, value in enumerate(raw_notes):
        if type(value) is not dict:
            raise ValueError(f"notes[{index}] must be an object")
        pitch = value.get("pitch")
        voice = value.get("voice")
        if type(pitch) is not int or voice not in ("melody", "bass", "harmony"):
            raise ValueError(f"notes[{index}] has an invalid pitch or voice")
        notes.append(
            Note(
                _fraction(value.get("onset"), f"notes[{index}].onset"),
                _fraction(value.get("duration"), f"notes[{index}].duration"),
                pitch,
                cast(VoiceRole, voice),
            )
        )

    result = solve_fingering_score(
        tuple(notes),
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
        beam=beam,
    )
    common: dict[str, object] = {
        "schema": RESULT_SCHEMA,
        "reference_id": document.get("id"),
        "reference_sha256": hashlib.sha256(raw).hexdigest(),
        "source": source,
        "difficulty_independent": True,
        "versions": {
            "fingering_solver": FINGERING_SOLVER_VERSION,
            "score_solver": SCORE_SOLVER_VERSION,
            "left_hand_model": LEFT_HAND_MODEL_VERSION,
            "oracle": CHECKER_VERSION,
            "profile": MEDIAN_HAND.version,
            "profile_fingerprint": MEDIAN_HAND.fingerprint,
        },
        "beam": beam,
    }
    if isinstance(result, Infeasible):
        return {
            **common,
            "status": "infeasible",
            "passed": False,
            "reason": result.reason,
        }

    actual = {
        (
            note.onset,
            note_pitch(note.string, note.fret, result.tuning, result.capo),
        ): note.left_finger
        for note in result.notes
    }
    mismatches: list[dict[str, object]] = []
    matches = 0
    for index, value in enumerate(raw_annotations):
        if type(value) is not dict:
            raise ValueError(f"annotations[{index}] must be an object")
        onset = _fraction(value.get("onset"), f"annotations[{index}].onset")
        pitch = value.get("pitch")
        expected = value.get("left_finger")
        if type(pitch) is not int or type(expected) is not int:
            raise ValueError(f"annotations[{index}] has invalid pitch/finger")
        observed = actual.get((onset, pitch))
        if observed == expected:
            matches += 1
        else:
            mismatches.append(
                {
                    "onset": _fraction_wire(onset),
                    "pitch": pitch,
                    "expected_left_finger": expected,
                    "actual_left_finger": observed,
                }
            )

    oracle = check_playability(result, MEDIAN_HAND)
    return {
        **common,
        "status": "evaluated",
        "passed": matches >= threshold and oracle.verdict != "RED",
        "oracle_verdict": oracle.verdict,
        "exact_matches": matches,
        "annotation_count": len(raw_annotations),
        "minimum_exact_matches": threshold,
        "mismatches": mismatches,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--beam", type=int, default=16)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = evaluate_fixture(args.fixture, beam=args.beam)
    except (OSError, ValueError) as exc:
        print(f"reference evaluation rejected: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(canonical_json_bytes(report))
    return 0 if report["passed"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
