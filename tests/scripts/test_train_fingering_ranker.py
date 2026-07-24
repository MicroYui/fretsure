from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str, filename: str) -> ModuleType:
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


quality_eval = _load_script("solver_quality_eval", "solver_quality_eval.py")
ranker = _load_script("train_fingering_ranker", "train_fingering_ranker.py")


def _candidate(
    candidate_id: str,
    features: tuple[float, ...],
    preference: tuple[int, int, int],
    *,
    query_id: str = "query",
    selected: bool = False,
    verdict: str = "GREEN",
) -> Any:
    return ranker.CandidateRecord(
        query_id=query_id,
        candidate_id=candidate_id,
        stable_rank=ord(candidate_id[0]),
        oracle_verdict=verdict,
        production_selected=selected,
        features=features,
        imitation=ranker.ImitationStats(
            notes_compared=8,
            joint_mismatch_count=preference[0],
            string_distance=preference[1],
            fret_distance=preference[2],
        ),
    )


def _pool(
    split: str,
    query_id: str,
    candidates: tuple[Any, ...],
) -> Any:
    return ranker.canonical_query_pool(split, query_id, candidates)


def _coverage(queries: tuple[Any, ...]) -> Any:
    candidate_count = sum(
        (len(query.candidates) for query in queries),
        start=0,
    )
    return ranker.SplitCoverage(
        selected_windows=len(queries),
        construction_rejections=0,
        solver_rejections=0,
        infeasible_windows=0,
        non_green_windows=0,
        green_pool_queries=len(queries),
        raw_green_candidates=candidate_count,
        canonical_green_candidates=candidate_count,
        accessed_sources=((f"{queries[0].split}-fixture.jams", "0" * 64),),
    )


def _training_fixture() -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    train = (
        _pool(
            "train",
            "train-query",
            (
                _candidate(
                    "b",
                    (4.0, 12.0, 2.0, 2_000.0, 5.0, 4.0),
                    (2, 2, 5),
                    query_id="train-query",
                    selected=True,
                ),
                _candidate(
                    "a",
                    (2.0, 8.0, 1.0, 1_000.0, 4.0, 2.0),
                    (0, 0, 0),
                    query_id="train-query",
                ),
            ),
        ),
    )
    dev = (
        _pool(
            "dev",
            "dev-query",
            (
                _candidate(
                    "d",
                    (5.0, 15.0, 3.0, 3_000.0, 6.0, 5.0),
                    (3, 3, 7),
                    query_id="dev-query",
                    selected=True,
                ),
                _candidate(
                    "c",
                    (1.0, 4.0, 0.0, 0.0, 2.0, 1.0),
                    (0, 0, 0),
                    query_id="dev-query",
                ),
            ),
        ),
    )
    return train, dev


def test_strict_imitation_pairs_drop_stable_tie_and_are_order_invariant() -> None:
    candidates = (
        _candidate("c", (3, 3, 3, 3, 3, 3), (0, 1, 0)),
        _candidate("a", (1, 1, 1, 1, 1, 1), (0, 0, 0), selected=True),
        _candidate("b", (2, 2, 2, 2, 2, 2), (0, 1, 0)),
    )
    forward = _pool("train", "query", candidates)
    reverse = _pool("train", "query", tuple(reversed(candidates)))

    forward_pairs = ranker.build_preference_pairs((forward,))
    reverse_pairs = ranker.build_preference_pairs((reverse,))

    assert [candidate.candidate_id for candidate in forward.candidates] == ["a", "b", "c"]
    assert forward.candidates == reverse.candidates
    assert forward_pairs == reverse_pairs
    assert forward_pairs.tied_candidate_pairs == 1
    assert {
        (pair.preferred_candidate_id, pair.dispreferred_candidate_id)
        for pair in forward_pairs.pairs
    } == {("a", "b"), ("a", "c")}


def test_non_green_candidate_is_rejected_before_pair_construction() -> None:
    candidate = _candidate(
        "a",
        (1, 1, 1, 1, 1, 1),
        (0, 0, 0),
        selected=True,
        verdict="AMBER",
    )

    with pytest.raises(ValueError, match="full-oracle GREEN"):
        _pool("train", "query", (candidate,))


def test_feature_scales_are_fit_from_train_candidates_only() -> None:
    train, dev = _training_fixture()

    scales_before = ranker.fit_feature_scales(train)
    scales_after = ranker.fit_feature_scales(train)

    assert scales_before == scales_after
    assert all(scale > 0 for scale in scales_before)
    assert max(candidate.features[0] for query in dev for candidate in query.candidates) == 5
    assert scales_before[0] < 5


def test_pairwise_fit_is_deterministic_and_all_weights_are_nonnegative() -> None:
    train, _ = _training_fixture()
    pairs = ranker.build_preference_pairs(train)
    scales = ranker.fit_feature_scales(train)

    first = ranker.fit_pairwise_ranker(pairs, scales, l2=0.01, max_iterations=200)
    second = ranker.fit_pairwise_ranker(pairs, scales, l2=0.01, max_iterations=200)

    assert first == second
    assert all(weight >= 0 for weight in first.weights)
    assert ranker.canonical_artifact_bytes({"weights": list(first.weights)}) == (
        ranker.canonical_artifact_bytes({"weights": list(second.weights)})
    )


def test_pareto_dominated_candidate_cannot_win_even_with_zero_weights() -> None:
    # The dominated candidate sorts first by id, so an ordinary zero-score tie
    # would choose it.  Pareto safety must instead retain the generic dominator.
    pool = _pool(
        "dev",
        "query",
        (
            _candidate("a", (2, 2, 2, 2, 2, 2), (0, 0, 0), selected=True),
            _candidate("z", (1, 1, 1, 1, 1, 1), (1, 1, 1)),
        ),
    )

    winner = ranker.select_model_candidate(
        pool,
        (1.0,) * len(ranker.FEATURES),
        (0.0,) * len(ranker.FEATURES),
    )

    assert winner.candidate_id == "z"


def test_full_training_is_canonical_and_accesses_only_train_and_dev(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    train, dev = _training_fixture()
    accessed: list[str] = []

    def fake_collect(config: object, split: str, *, profile: object) -> object:
        del config, profile
        assert split in ("train", "dev")
        accessed.append(split)
        queries = train if split == "train" else dev
        return queries, _coverage(queries)

    monkeypatch.setattr(ranker, "_collect_split", fake_collect)
    archive = tmp_path / "annotation.zip"
    archive.write_bytes(b"offline-fixture")

    def forbid_whole_archive_read(path: Path) -> bytes:
        if path == archive:
            raise AssertionError("training must not hash/read the whole train/dev/test ZIP")
        return original_read_bytes(path)

    original_read_bytes = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", forbid_whole_archive_read)
    config = ranker.RankerConfig(
        guitarset_zip=archive,
        train_windows=1,
        dev_windows=1,
        notes_per_window=8,
        l2_grid=(0.01, 0.1),
        max_iterations=200,
    )

    first_model, first_report = ranker.train_ranker(config)
    second_model, second_report = ranker.train_ranker(config)

    assert accessed == ["train", "dev", "train", "dev"]
    assert ranker.canonical_artifact_bytes(first_model) == ranker.canonical_artifact_bytes(
        second_model
    )
    assert ranker.canonical_artifact_bytes(first_report) == ranker.canonical_artifact_bytes(
        second_report
    )
    assert first_model["data_access"] == {
        "fit": "guitarset/train-only",
        "feature_scales": "guitarset/train-only",
        "hyperparameter_selection": "guitarset/dev-only",
        "guitarset_test": "not-read",
        "egset12_held_out": "not-read",
    }
    assert first_report["data_access_assertion"]["read_splits"] == ["train", "dev"]
    assert first_report["coverage"]["train"]["strict_preference_pairs"] == 1
    assert first_report["coverage"]["dev"]["strict_preference_pairs"] == 1
    assert first_report["selected_vs_model_vs_best"]["dev"]["model"][
        "joint_exact_rate"
    ] == 1.0


def test_feature_schema_contains_only_generic_continuous_burdens() -> None:
    assert [feature.name for feature in ranker.FEATURES] == [
        "max_fret",
        "duration_weighted_fret_exposure",
        "shift_count",
        "shift_distance_micrometres",
        "finger_load",
        "string_crossings",
    ]
    serialized = " ".join(
        f"{feature.name} {feature.source} {feature.description}" for feature in ranker.FEATURES
    ).lower()
    for forbidden in ("song", "source_id", "performer", "style", "key_signature", "fret_band"):
        assert forbidden not in serialized


def test_cli_defaults_are_small_train_dev_smoke_only() -> None:
    args = ranker._parser().parse_args([])

    assert (args.train_windows, args.dev_windows, args.notes_per_window) == (2, 1, 8)
    assert not hasattr(args, "test_windows")
    assert not hasattr(args, "egset12_dir")
