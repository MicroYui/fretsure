#!/usr/bin/env python3
"""Rebuild the descriptive published-grade model from graded-guitar features."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Final, Literal, cast

ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT: Final = (
    ROOT / "src/fretsure/difficulty/models/published-grade-v0.1.0.json"
)
MODEL_VERSION: Final = "published-grade-estimator@0.1.0"
GRADE_SYSTEM: Final = "Delcamp/Eric Crouch 1–10"
GRADE_SOURCE: Final = "delcamp-eric-crouch"
SPLIT_SEED: Final = "plan7b-difficulty-composer-v1"
FEATURES: Final = (
    "midi_max",
    "max_chord_stack",
    "voice_count_max",
    "polyphonic_measure_ratio",
    "measure_count",
)
GRADE_BANDS: Final = (
    (20.0, 3),
    (35.0, 5),
    (55.0, 6),
    (80.0, 7),
    (101.0, 8),
)
Split = Literal["train", "dev", "test"]


def _knots(rows: list[dict[str, str]], feature: str) -> list[list[float]]:
    values = sorted(float(row[feature]) for row in rows if row[feature])
    result: list[list[float]] = []
    index = 0
    while index < len(values):
        end = index + 1
        while end < len(values) and values[end] == values[index]:
            end += 1
        average_zero_based_rank = (index + end - 1) / 2
        percentile = 100.0 * average_zero_based_rank / (len(values) - 1)
        result.append([values[index], percentile])
        index = end
    return result


def _percentile(value: float, knots: list[list[float]]) -> float:
    if value <= knots[0][0]:
        return knots[0][1]
    if value >= knots[-1][0]:
        return knots[-1][1]
    for left, right in zip(knots, knots[1:], strict=True):
        if value > right[0]:
            continue
        if right[0] == left[0]:
            return right[1]
        ratio = (value - left[0]) / (right[0] - left[0])
        return left[1] + ratio * (right[1] - left[1])
    raise AssertionError("percentile lookup did not terminate")


def _predict(row: dict[str, str], knots: dict[str, list[list[float]]]) -> int:
    burden = sum(_percentile(float(row[name]), knots[name]) for name in FEATURES) / len(
        FEATURES
    )
    return next(grade for upper, grade in GRADE_BANDS if burden < upper)


def _composer_splits(rows: list[dict[str, str]]) -> dict[str, Split]:
    composers = sorted(
        {row["composer_normalized"] for row in rows},
        key=lambda composer: (
            hashlib.sha256(f"{SPLIT_SEED}\0{composer}".encode()).digest(),
            composer,
        ),
    )
    train_end = round(len(composers) * 0.70)
    dev_end = train_end + round(len(composers) * 0.15)
    return {
        composer: "train" if index < train_end else "dev" if index < dev_end else "test"
        for index, composer in enumerate(composers)
    }


def _difficulty_band(grade: int) -> str:
    return "foundational" if grade <= 5 else "intermediate" if grade <= 7 else "advanced"


def _split_metrics(
    rows: list[dict[str, str]],
    knots: dict[str, list[list[float]]],
    composer_splits: dict[str, Split],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for split in ("train", "dev", "test"):
        selected = [
            row for row in rows if composer_splits[row["composer_normalized"]] == split
        ]
        truth = [int(row["grade"]) for row in selected]
        predicted = [_predict(row, knots) for row in selected]
        count = len(selected)
        result[split] = {
            "pieces": count,
            "composers": len({row["composer_normalized"] for row in selected}),
            "exact": sum(left == right for left, right in zip(truth, predicted, strict=True))
            / count,
            "within_one": sum(
                abs(left - right) <= 1 for left, right in zip(truth, predicted, strict=True)
            )
            / count,
            "mae": sum(
                abs(left - right) for left, right in zip(truth, predicted, strict=True)
            )
            / count,
            "three_band_accuracy": sum(
                _difficulty_band(left) == _difficulty_band(right)
                for left, right in zip(truth, predicted, strict=True)
            )
            / count,
            "truth_distribution": dict(sorted(Counter(map(str, truth)).items())),
            "prediction_distribution": dict(
                sorted(Counter(map(str, predicted)).items())
            ),
        }
    return result


def build_model(features_path: Path) -> dict[str, object]:
    raw = features_path.read_bytes()
    rows = list(csv.DictReader(raw.decode("utf-8").splitlines()))
    knots = {feature: _knots(rows, feature) for feature in FEATURES}
    graded = [
        row for row in rows if row["grade_source"] == GRADE_SOURCE and row["grade"]
    ]
    splits = _composer_splits(graded)
    return {
        "model_version": MODEL_VERSION,
        "deployment_status": "descriptive_low_confidence",
        "grade_system": GRADE_SYSTEM,
        "features": list(FEATURES),
        "percentile_knots": knots,
        "grade_bands": [
            {"upper_percentile": int(upper), "grade": grade}
            for upper, grade in GRADE_BANDS
        ],
        "training": {
            "method": "equal-weight empirical feature percentiles with fixed grade bands",
            "feature_rows": len(rows),
            "input_sha256": hashlib.sha256(raw).hexdigest(),
            "upstream_repository": "HugoFara/graded-guitar",
            "upstream_commit": "6e0fbf4855a11d766531b27991bf581a7a9ad3a3",
            "redistribution": "raw scores and feature CSV are not redistributed by Fretsure",
        },
        "evaluation": {
            "schema": "composer-grouped-grade-eval@0.2.0",
            "grade_source": GRADE_SOURCE,
            "labelled_pieces": len(graded),
            "split_policy": "composer-grouped-sha256@0.1.0",
            "split_seed": SPLIT_SEED,
            "composer_confounded": True,
            "rights": (
                "You may freely use or adapt this arrangement provided you acknowledge "
                "Eric Crouch as its source."
            ),
            "splits": _split_metrics(graded, knots, splits),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("features", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    document = build_model(cast(Path, args.features))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
