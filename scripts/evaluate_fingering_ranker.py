#!/usr/bin/env python3
"""Evaluate one frozen fingering ranker on GuitarSet performer 05 only.

This command never fits features, scales, weights, or hyperparameters.  It
strictly binds a model digest, reads only the frozen GuitarSet ``test`` split,
and lets the model choose only among full-Oracle-GREEN finalists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, cast

import solver_quality_eval as quality_eval
import train_fingering_ranker as ranker

from fretsure.bench.contracts import canonical_json_bytes
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import Note
from fretsure.oracle.core import check_playability
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver import api as solver_api
from fretsure.solver.api import Infeasible, SolverInputError
from fretsure.tab import Tab

EVALUATION_SCHEMA: Final = "fingering-frozen-ranker-test-eval@0.1.0"
FROZEN_CANDIDATE_GENERATOR_VERSION: Final = "fingering-solver@0.3.0"
FROZEN_MODEL_SHA256: Final = (
    "b6cc57b0b55ed55f959d827e46276371e87820938c5678adf860ffa60f845315"
)
GUITARSET_ANNOTATION_SHA256: Final = (
    "8daa02e6417ccca1685feb44b135e95928ad7037e5032ecb326b5791856fda99"
)
GUITARSET_TEST_MEMBER_SET_SHA256: Final = (
    "fb0a0c9a8c0408f7309d4895b795a09b06a75da87fd9826a90e29e31131acefe"
)
DEFAULT_MODEL: Final = Path(
    "docs/experiments/artifacts/"
    "fingering-green-pairwise-ranker-v0.1.0-b6cc57b0.json"
)
DEFAULT_OUTPUT: Final = Path(
    "outputs/fingering-ranker-guitarset-train60-dev30-24notes-20260723/"
    "test-report.json"
)


@dataclass(frozen=True, slots=True)
class FrozenModel:
    sha256: str
    payload: dict[str, object]
    scales: tuple[float, ...]
    weights: tuple[float, ...]
    notes_per_window: int
    window_offset: int
    quantize_denominator: int
    beam: int
    beats_per_bar: int


def _object(value: object, path: str) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError(f"{path} must be an object")
    return cast(dict[str, object], value)


def _integer(value: object, path: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{path} must be an integer >= {minimum}")
    return value


def _finite_decimal_text(value: object, path: str, *, positive: bool) -> float:
    if type(value) is not str:
        raise ValueError(f"{path} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{path} must be a finite decimal string") from exc
    if not parsed.is_finite() or (parsed <= 0 if positive else parsed < 0):
        relation = "positive" if positive else "non-negative"
        raise ValueError(f"{path} must be finite and {relation}")
    result = float(parsed)
    if not math.isfinite(result):
        raise ValueError(f"{path} is outside finite float range")
    return result


def load_frozen_model(path: Path, *, expected_sha256: str) -> FrozenModel:
    raw = path.read_bytes()
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"model SHA-256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )
    try:
        root = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("model must be canonical UTF-8 JSON") from exc
    payload = _object(root, "$model")
    if raw != canonical_json_bytes(payload):
        raise ValueError("model must use canonical JSON encoding")
    if payload.get("schema") != ranker.RANKER_MODEL_SCHEMA:
        raise ValueError("model schema does not match the frozen ranker")
    if payload.get("experimental_only") is not True:
        raise ValueError("test evaluator accepts only the frozen experimental model")
    solver = _object(payload.get("solver"), "$model.solver")
    if solver != {
        "capo": 0,
        "tuning": list(STANDARD_TUNING),
        "version": FROZEN_CANDIDATE_GENERATOR_VERSION,
    }:
        raise ValueError("model solver binding does not match this candidate generator")
    profile = _object(payload.get("profile"), "$model.profile")
    if profile != {
        "fingerprint": MEDIAN_HAND.fingerprint,
        "version": MEDIAN_HAND.version,
    }:
        raise ValueError("model profile binding does not match median-hand evaluation")

    feature_schema = _object(payload.get("feature_schema"), "$model.feature_schema")
    if feature_schema.get("id") != ranker.FEATURE_SCHEMA:
        raise ValueError("model feature schema id does not match")
    raw_definitions = feature_schema.get("features")
    if type(raw_definitions) is not list:
        raise ValueError("$model.feature_schema.features must be an array")
    names = tuple(
        _object(item, f"$model.feature_schema.features[{index}]").get("name")
        for index, item in enumerate(raw_definitions)
    )
    if names != ranker.FEATURE_NAMES:
        raise ValueError("model feature order does not match the evaluator")

    raw_scales = _object(
        payload.get("feature_scales_train_only"),
        "$model.feature_scales_train_only",
    )
    raw_weights = _object(
        payload.get("scaled_nonnegative_weights"),
        "$model.scaled_nonnegative_weights",
    )
    if set(raw_scales) != set(ranker.FEATURE_NAMES) or set(raw_weights) != set(
        ranker.FEATURE_NAMES
    ):
        raise ValueError("model scale/weight feature names do not match")
    scales = tuple(
        _finite_decimal_text(raw_scales[name], f"scale.{name}", positive=True)
        for name in ranker.FEATURE_NAMES
    )
    weights = tuple(
        _finite_decimal_text(raw_weights[name], f"weight.{name}", positive=False)
        for name in ranker.FEATURE_NAMES
    )
    if not any(weight > 0 for weight in weights):
        raise ValueError("frozen model must contain at least one positive weight")

    configuration = _object(payload.get("configuration"), "$model.configuration")
    return FrozenModel(
        actual_sha256,
        payload,
        scales,
        weights,
        _integer(configuration.get("notes_per_window"), "notes_per_window", minimum=1),
        _integer(configuration.get("window_offset"), "window_offset"),
        _integer(
            configuration.get("quantize_denominator"),
            "quantize_denominator",
            minimum=1,
        ),
        _integer(configuration.get("beam"), "beam", minimum=1),
        _integer(configuration.get("beats_per_bar"), "beats_per_bar", minimum=1),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _guitarset_member_set_sha256(
    documents: Sequence[quality_eval.CorpusDocument],
) -> str:
    digest = hashlib.sha256()
    for document in documents:
        digest.update(document.source_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(document.source_sha256))
    return digest.hexdigest()


def _test_query_pool(
    window: quality_eval.EvaluationWindow,
    selected: Tab,
    pool: Sequence[solver_api._GreenFinalist],
    *,
    beats_per_bar: int,
) -> ranker.QueryPool:
    if window.split != "test":
        raise ValueError("frozen evaluation accepts only GuitarSet test windows")
    query_id = ranker._query_id(window)
    records: list[ranker.CandidateRecord] = []
    for finalist in pool:
        verdict = check_playability(
            finalist.tab,
            MEDIAN_HAND,
            tempo_bpm=float(window.tempo_bpm),
            beats_per_bar=beats_per_bar,
        ).verdict
        if verdict != "GREEN":
            raise RuntimeError("candidate pool contains a non-GREEN finalist")
        records.append(
            ranker.CandidateRecord(
                query_id=query_id,
                candidate_id=ranker._candidate_id(finalist.tab, finalist.quality),
                stable_rank=finalist.stable_rank,
                oracle_verdict=verdict,
                production_selected=finalist.tab == selected,
                features=ranker._quality_features(finalist.quality),
                imitation=ranker._imitation_stats(window, finalist.tab),
            )
        )
    return ranker.canonical_query_pool("test", query_id, records)


def _evaluate_loaded_model_unchecked(
    model: FrozenModel,
    *,
    guitarset_zip: Path = quality_eval.DEFAULT_GUITARSET_ZIP,
) -> dict[str, object]:
    annotation_sha256 = _sha256_file(guitarset_zip)
    if annotation_sha256 != GUITARSET_ANNOTATION_SHA256:
        raise ValueError(
            "GuitarSet annotation.zip SHA-256 mismatch: "
            f"expected {GUITARSET_ANNOTATION_SHA256}, got {annotation_sha256}"
        )
    documents = tuple(
        quality_eval.iter_guitarset_documents(guitarset_zip, split="test")
    )
    if len(documents) != 60:
        raise ValueError(
            f"frozen GuitarSet test split must contain 60 files, got {len(documents)}"
        )
    member_set_sha256 = _guitarset_member_set_sha256(documents)
    if member_set_sha256 != GUITARSET_TEST_MEMBER_SET_SHA256:
        raise ValueError(
            "GuitarSet test member-set SHA-256 mismatch: "
            f"expected {GUITARSET_TEST_MEMBER_SET_SHA256}, got {member_set_sha256}"
        )

    construction_rejections = 0
    solver_rejections = 0
    infeasible_windows = 0
    non_green_windows = 0
    pools: list[ranker.QueryPool] = []
    provenance: list[dict[str, str]] = []
    for window_index, document in enumerate(documents):
        provenance.append(
            {"source_id": document.source_id, "sha256": document.source_sha256}
        )
        selection = quality_eval.build_window(
            document,
            notes_per_window=model.notes_per_window,
            window_offset=model.window_offset,
            quantize_denominator=model.quantize_denominator,
            window_index=window_index,
        )
        if isinstance(selection, quality_eval.WindowConstructionRejection):
            construction_rejections += 1
            continue
        target = tuple(
            Note(note.onset, note.duration, note.pitch, "melody")
            for note in selection.notes
        )
        try:
            outcome = solver_api._solve_fingering_with_green_pool(
                target,
                STANDARD_TUNING,
                0,
                MEDIAN_HAND,
                tempo_bpm=float(selection.tempo_bpm),
                beats_per_bar=model.beats_per_bar,
                beam=model.beam,
                _collect_full_green_pool=True,
            )
        except SolverInputError:
            solver_rejections += 1
            continue
        if isinstance(outcome.result, Infeasible):
            infeasible_windows += 1
            continue
        if not outcome.green_pool:
            non_green_windows += 1
            continue
        pools.append(
            _test_query_pool(
                selection,
                # The frozen comparison is explicitly bound to the 0.3
                # baseline.  Experimental rankers may inspect the complete
                # GREEN pool, but pool[0] remains that baseline selection.
                outcome.green_pool[0].tab,
                outcome.green_pool,
                beats_per_bar=model.beats_per_bar,
            )
        )

    metrics = ranker._selection_metrics(pools, model.scales, model.weights)
    return {
        "schema": EVALUATION_SCHEMA,
        "model": {
            "sha256": model.sha256,
            "schema": model.payload["schema"],
            "feature_schema": ranker.FEATURE_SCHEMA,
            "candidate_generator_version": FROZEN_CANDIDATE_GENERATOR_VERSION,
        },
        "configuration": {
            "split": "test",
            "performer": "05",
            "files": 60,
            "notes_per_window": model.notes_per_window,
            "window_offset": model.window_offset,
            "quantize_denominator": model.quantize_denominator,
            "beam": model.beam,
            "beats_per_bar": model.beats_per_bar,
            "sampling_protocol": quality_eval.GUITARSET_SAMPLING_PROTOCOL,
        },
        "data_access_assertion": {
            "read_splits": ["test"],
            "forbidden": ["guitarset/train", "guitarset/dev", "egset12/held-out"],
            "training_or_weight_updates": False,
        },
        "test_provenance": {
            "corpus_id": quality_eval.GUITARSET_CORPUS_ID,
            "license": quality_eval.CORPUS_LICENSE,
            "record_url": quality_eval.GUITARSET_RECORD_URL,
            "annotation_zip_sha256": annotation_sha256,
            "member_set_sha256": member_set_sha256,
            "members": provenance,
        },
        "coverage": {
            "selected_windows": len(documents),
            "construction_rejections": construction_rejections,
            "solver_rejections": solver_rejections,
            "infeasible_windows": infeasible_windows,
            "non_green_windows": non_green_windows,
            "green_pool_queries": len(pools),
            "full_green_candidates": sum(len(pool.candidates) for pool in pools),
        },
        "selected_vs_model_vs_best": metrics,
    }


def evaluate_frozen_model(
    model_path: Path = DEFAULT_MODEL,
    *,
    guitarset_zip: Path = quality_eval.DEFAULT_GUITARSET_ZIP,
) -> dict[str, object]:
    model = load_frozen_model(
        model_path,
        expected_sha256=FROZEN_MODEL_SHA256,
    )
    return _evaluate_loaded_model_unchecked(model, guitarset_zip=guitarset_zip)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--guitarset-zip", type=Path, default=quality_eval.DEFAULT_GUITARSET_ZIP)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = evaluate_frozen_model(
            args.model,
            guitarset_zip=args.guitarset_zip,
        )
    except (
        FileNotFoundError,
        RuntimeError,
        SolverInputError,
        ValueError,
        zipfile.BadZipFile,
        quality_eval.CorpusDataError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
