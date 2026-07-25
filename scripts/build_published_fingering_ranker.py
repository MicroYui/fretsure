#!/usr/bin/env python3
"""Fit and evaluate the frozen published-score GREEN-finalist ranker."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Final, Literal, cast

import numpy as np
from numpy.typing import NDArray

from fretsure.geometry import note_pitch
from fretsure.ir import Note, VoiceRole
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.api import Infeasible, _solve_fingering_with_green_pool
from fretsure.solver.score_supervision import (
    PUBLISHED_FINGERING_FEATURE_NAMES,
    PUBLISHED_FINGERING_FEATURE_SCHEMA,
    PUBLISHED_FINGERING_MIN_ATTACK_GEOMETRIES,
    PUBLISHED_FINGERING_MIN_ONSETS,
    PUBLISHED_FINGERING_RANKER_VERSION,
    PUBLISHED_FINGERING_SOURCE_SOLVER_VERSION,
    published_fingering_features,
)

ROOT: Final = Path(__file__).resolve().parents[1]
DEFAULT_CORPORA: Final = (
    ROOT / "data/score_corpus/carcassi_op59.json",
    ROOT / "data/score_corpus/mutopia_pd_additional.json",
)
SHAREALIKE_CORPUS: Final = ROOT / "data/score_corpus/mutopia_cc_by_sa.json"
DEFAULT_OUTPUT: Final = (
    ROOT
    / "src/fretsure/solver/models/published-fingering-ranker-v0.1.0.json"
)
SPLIT_SEED: Final = "plan7b-fingering-composer-v1"
WINDOW_ONSETS: Final = 4
RIDGE_L2: Final = 100.0
MAX_EFFORT_DELTA: Final = 6

Split = Literal["train", "dev", "test"]
FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]


@dataclass(frozen=True, slots=True)
class _Row:
    split: Split
    example_id: str
    onset_index: int
    features: FloatArray
    matches: IntArray
    annotation_count: int
    distinct_attack_geometries: int


def _fraction(value: object) -> Fraction:
    parts = cast(list[int], value)
    return Fraction(parts[0], parts[1])


def _composer_splits(composers: set[str]) -> dict[str, Split]:
    ordered = sorted(
        composers,
        key=lambda composer: (
            hashlib.sha256(f"{SPLIT_SEED}\0{composer}".encode()).digest(),
            composer,
        ),
    )
    train_end = min(round(len(ordered) * 0.70), len(ordered) - 2)
    dev_end = train_end + 1
    return {
        composer: "train" if index < train_end else "dev" if index < dev_end else "test"
        for index, composer in enumerate(ordered)
    }


def _candidate_rows(
    documents: tuple[dict[str, object], ...],
) -> tuple[list[_Row], dict[str, int], dict[str, Split]]:
    examples = [
        cast(dict[str, object], example)
        for document in documents
        for example in cast(list[object], document["examples"])
    ]
    splits = _composer_splits({cast(str, example["composer"]) for example in examples})
    unsupported = {"open_string_labels": 0, "no_green_window": 0}
    rows: list[_Row] = []

    for example in examples:
        notes = tuple(
            Note(
                _fraction(raw_note["onset"]),
                _fraction(raw_note["duration"]),
                cast(int, raw_note["pitch"]),
                cast(VoiceRole, raw_note["voice"]),
            )
            for raw_note in cast(list[dict[str, object]], example["notes"])
        )
        labels: dict[tuple[Fraction, int], frozenset[int]] = {}
        for annotation in cast(list[dict[str, object]], example["annotations"]):
            accepted = frozenset(
                finger
                for finger in cast(list[int], annotation["accepted_fingers"])
                if 1 <= finger <= 4
            )
            if not accepted:
                unsupported["open_string_labels"] += 1
                continue
            labels[(_fraction(annotation["onset"]), cast(int, annotation["pitch"]))] = accepted

        onsets = tuple(sorted({note.onset for note in notes}))
        for onset_index in range(0, len(onsets), WINDOW_ONSETS):
            window_onsets = frozenset(onsets[onset_index : onset_index + WINDOW_ONSETS])
            window_labels = {
                key: accepted for key, accepted in labels.items() if key[0] in window_onsets
            }
            if not window_labels:
                continue
            window_notes = tuple(note for note in notes if note.onset in window_onsets)
            outcome = _solve_fingering_with_green_pool(
                window_notes,
                tuple(cast(list[int], example["tuning"])),
                cast(int, example["capo"]),
                MEDIAN_HAND,
                tempo_bpm=float(cast(int | float, example["tempo_bpm"])),
                beats_per_bar=cast(list[int], example["time_signature"])[0],
                beam=16,
                _collect_full_green_pool=True,
            )
            if isinstance(outcome.result, Infeasible) or not outcome.green_pool:
                unsupported["no_green_window"] += len(window_labels)
                continue

            feature_rows: list[tuple[float, ...]] = []
            match_rows: list[int] = []
            for finalist in outcome.green_pool:
                actual = {
                    (
                        note.onset,
                        note_pitch(
                            note.string,
                            note.fret,
                            finalist.tab.tuning,
                            finalist.tab.capo,
                        ),
                    ): note.left_finger
                    for note in finalist.tab.notes
                }
                match_rows.append(
                    sum(actual.get(key) in accepted for key, accepted in window_labels.items())
                )
                feature_rows.append(
                    tuple(
                        float(value)
                        for value in published_fingering_features(
                            finalist.tab,
                            finalist.quality,
                        )
                    )
                )
            rows.append(
                _Row(
                    splits[cast(str, example["composer"])],
                    cast(str, example["id"]),
                    onset_index,
                    np.asarray(feature_rows, dtype=np.float64),
                    np.asarray(match_rows, dtype=np.int64),
                    len(window_labels),
                    len(
                        {
                            tuple(
                                sorted(
                                    (note.string, note.fret)
                                    for note in outcome.green_pool[0].tab.notes
                                    if note.onset == onset
                                )
                            )
                            for onset in {
                                note.onset for note in outcome.green_pool[0].tab.notes
                            }
                        }
                    ),
                )
            )
    return rows, unsupported, splits


def _fit(rows: list[_Row]) -> tuple[FloatArray, FloatArray]:
    centered_features = np.vstack(
        [row.features - row.features.mean(axis=0) for row in rows if row.split == "train"]
    )
    centered_matches = np.concatenate(
        [row.matches - row.matches.mean() for row in rows if row.split == "train"]
    )
    scales = np.sqrt(np.mean(centered_features * centered_features, axis=0))
    scales[scales == 0] = 1.0
    design = centered_features / scales
    weights = np.linalg.solve(
        design.T @ design + RIDGE_L2 * np.eye(design.shape[1]),
        design.T @ centered_matches,
    )
    return scales, weights


def _eligible(features: FloatArray) -> IntArray:
    baseline = features[0]
    return np.flatnonzero(
        (features[:, 9] <= baseline[9])
        & (features[:, 0] <= baseline[0])
        & (features[:, 1] <= baseline[1] + MAX_EFFORT_DELTA)
    )


def _winner(row: _Row, scales: FloatArray, weights: FloatArray) -> tuple[int, float]:
    if row.distinct_attack_geometries < PUBLISHED_FINGERING_MIN_ATTACK_GEOMETRIES:
        return 0, 0.0
    scores = (row.features / scales) @ weights
    eligible = _eligible(row.features)
    selected = int(eligible[np.argmax(scores[eligible])])
    return selected, float(scores[selected] - scores[0])


def _threshold(rows: list[_Row], scales: FloatArray, weights: FloatArray) -> float:
    prepared = [
        (row, *_winner(row, scales, weights)) for row in rows if row.split == "dev"
    ]
    margins = sorted({margin for _row, selected, margin in prepared if selected != 0})
    candidates = [-1e-12, *margins, max(margins, default=0.0) + 1.0]
    return max(
        candidates,
        key=lambda threshold: (
            sum(
                int(row.matches[selected if selected and margin >= threshold else 0])
                for row, selected, margin in prepared
            ),
            -sum(selected != 0 and margin >= threshold for _row, selected, margin in prepared),
            threshold,
        ),
    )


def _metrics(
    rows: list[_Row],
    scales: FloatArray,
    weights: FloatArray,
    threshold: float,
) -> dict[str, object]:
    result: dict[str, object] = {}
    for split in ("train", "dev", "test"):
        selected_rows = [row for row in rows if row.split == split]
        baseline_matches = 0
        model_matches = 0
        changes = 0
        labels = 0
        for row in selected_rows:
            selected, margin = _winner(row, scales, weights)
            if selected == 0 or margin < threshold:
                selected = 0
            baseline_matches += int(row.matches[0])
            model_matches += int(row.matches[selected])
            changes += selected != 0
            labels += row.annotation_count
        result[split] = {
            "windows": len(selected_rows),
            "labels": labels,
            "baseline_exact_matches": baseline_matches,
            "baseline_exact_rate": baseline_matches / labels,
            "model_exact_matches": model_matches,
            "model_exact_rate": model_matches / labels,
            "selected_changes": changes,
            "oracle_status_regressions": 0,
        }

    prelude_rows = [
        row for row in rows if row.example_id == "mutopia-carcassi-op59-prelude-01"
    ]
    prelude_baseline = sum(int(row.matches[0]) for row in prelude_rows)
    prelude_model = 0
    for row in prelude_rows:
        selected, margin = _winner(row, scales, weights)
        if selected == 0 or margin < threshold:
            selected = 0
        prelude_model += int(row.matches[selected])
    result["carcassi_prelude_1"] = {
        "labels": sum(row.annotation_count for row in prelude_rows),
        "baseline_exact_matches": prelude_baseline,
        "model_exact_matches": prelude_model,
    }
    return result


def build_model(corpus_paths: tuple[Path, ...]) -> dict[str, object]:
    raw_corpora = tuple(path.read_bytes() for path in corpus_paths)
    documents = tuple(
        cast(dict[str, object], json.loads(raw.decode("utf-8"))) for raw in raw_corpora
    )
    rows, unsupported, splits = _candidate_rows(documents)
    scales, weights = _fit(rows)
    threshold = _threshold(rows, scales, weights)
    scored_labels = sum(row.annotation_count for row in rows)
    return {
        "ranker_version": PUBLISHED_FINGERING_RANKER_VERSION,
        "deployment_status": "guarded_default_green_near_tie",
        "feature_schema": PUBLISHED_FINGERING_FEATURE_SCHEMA,
        "source_solver_version": PUBLISHED_FINGERING_SOURCE_SOLVER_VERSION,
        "training": {
            "method": "window-centred ridge regression",
            "ridge_l2": RIDGE_L2,
            "window_onsets": WINDOW_ONSETS,
            "split_policy": "composer-grouped-sha256@0.1.0",
            "split_seed": SPLIT_SEED,
            "composer_splits": splits,
            "corpora": [
                {
                    "path": str(path.resolve().relative_to(ROOT)),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
                for path, raw in zip(corpus_paths, raw_corpora, strict=True)
            ],
        },
        "coverage": {
            "scored_windows": len(rows),
            "scored_labels": scored_labels,
            "unsupported_labels": unsupported,
            "missing_annotations_are_unlabelled": True,
        },
        "guard": {
            "requires_complete_oracle_green": True,
            "minimum_onsets": PUBLISHED_FINGERING_MIN_ONSETS,
            "minimum_distinct_attack_geometries": (
                PUBLISHED_FINGERING_MIN_ATTACK_GEOMETRIES
            ),
            "max_fret_delta": 0,
            "max_awkward_fingering_event_delta": 0,
            "max_left_hand_effort_delta": MAX_EFFORT_DELTA,
        },
        "minimum_score_margin": repr(threshold),
        "features": [
            {
                "name": name,
                "scale": repr(float(scale)),
                "weight": repr(float(weight)),
            }
            for name, scale, weight in zip(
                PUBLISHED_FINGERING_FEATURE_NAMES,
                scales,
                weights,
                strict=True,
            )
        ],
        "evaluation": _metrics(rows, scales, weights, threshold),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, action="append")
    parser.add_argument(
        "--include-mutopia-sharealike",
        action="store_true",
        help="append the reviewed CC BY-SA corpus to the frozen production inputs",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.corpus and args.include_mutopia_sharealike:
        parser.error("--corpus and --include-mutopia-sharealike are mutually exclusive")
    corpus_paths = tuple(args.corpus) if args.corpus else DEFAULT_CORPORA
    if args.include_mutopia_sharealike:
        corpus_paths = (*corpus_paths, SHAREALIKE_CORPUS)
    document = build_model(corpus_paths)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
