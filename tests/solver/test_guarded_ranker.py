from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from pathlib import Path

import fretsure.solver.ranker as ranker
from fretsure.solver.cost import QualityCost


def test_experimental_model_constants_match_audited_manifest() -> None:
    manifest_path = Path(ranker.__file__).parent / ranker.FINGERING_RANKER_MANIFEST
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))

    assert ranker.FINGERING_RANKER_VERSION == "fingering-green-ranker@0.1.0"
    assert ranker.FINGERING_RANKER_MODEL_SHA256 == (
        "b6cc57b0b55ed55f959d827e46276371e87820938c5678adf860ffa60f845315"
    )
    assert ranker.FINGERING_RANKER_SOURCE_SOLVER_VERSION == "fingering-solver@0.3.0"
    assert hashlib.sha256(manifest_bytes).hexdigest() == (
        ranker.FINGERING_RANKER_MANIFEST_SHA256
    )
    assert manifest["ranker_version"] == ranker.FINGERING_RANKER_VERSION
    assert manifest["deployment_status"] == "experimental_offline_only"
    assert manifest["feature_schema"] == ranker.FINGERING_RANKER_FEATURE_SCHEMA
    assert manifest["source_artifact"] == {
        "schema": "fingering-green-pairwise-ranker@0.1.0",
        "sha256": ranker.FINGERING_RANKER_MODEL_SHA256,
        "solver_version": ranker.FINGERING_RANKER_SOURCE_SOLVER_VERSION,
    }
    assert manifest["training_data"]["creators"] == [
        "Qingyang Xi",
        "Rachel M. Bittner",
        "Johan Pauwels",
        "Xuzhou Ye",
        "Juan P. Bello",
    ]
    assert manifest["training_data"]["license_url"] == (
        "https://creativecommons.org/licenses/by/4.0/"
    )
    assert tuple(feature["name"] for feature in manifest["features"]) == (
        ranker.FINGERING_RANKER_FEATURE_NAMES
    )
    assert tuple(feature["scale"] for feature in manifest["features"]) == (
        ranker.FINGERING_RANKER_FEATURE_SCALES_TEXT
    )
    assert tuple(
        feature["scaled_nonnegative_weight"] for feature in manifest["features"]
    ) == ranker.FINGERING_RANKER_SCALED_WEIGHTS_TEXT


def test_relative_incumbent_guard_rejects_model_preferred_higher_fret() -> None:
    legacy = QualityCost(5, Fraction(10_000), 100, 1_000_000, 100, 100)
    higher_but_otherwise_light = QualityCost(6, Fraction(0), 0, 0, 0, 0)
    assert ranker._model_score(higher_but_otherwise_light) < ranker._model_score(legacy)

    selected = ranker.select_guarded_green_index(
        (legacy, higher_but_otherwise_light),
        (10, 11),
    )

    assert selected == 0


def test_pareto_dominated_candidate_cannot_win_with_zero_weight_tie() -> None:
    frontier = QualityCost(5, Fraction(10), 1, 100, 5, 1)
    dominated_only_on_zero_weight_feature = QualityCost(
        5,
        Fraction(10),
        1,
        100,
        5,
        2,
    )
    assert ranker._model_score(frontier) == ranker._model_score(
        dominated_only_on_zero_weight_feature
    )

    selected = ranker.select_guarded_green_index(
        (dominated_only_on_zero_weight_feature, frontier),
        (0, 1),
    )

    assert selected == 1


def test_guarded_ranker_is_stable_across_repeated_selections() -> None:
    qualities = (
        QualityCost(5, Fraction(10), 2, 100, 8, 1),
        QualityCost(5, Fraction(8), 3, 120, 7, 1),
        QualityCost(5, Fraction(9), 2, 90, 9, 2),
    )

    selections = {
        ranker.select_guarded_green_index(qualities, (7, 8, 9))
        for _ in range(100)
    }

    assert len(selections) == 1
