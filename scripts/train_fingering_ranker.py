#!/usr/bin/env python3
"""Train an offline, Oracle-constrained fingering learning-to-rank experiment.

This script is deliberately outside the production solver.  It constructs the
same fixed GuitarSet windows as ``solver_quality_eval.py``, retains only the
solver's fully checked GREEN finalist pool, and learns non-negative burden
weights from human string/fret imitation preferences.  Training statistics and
feature scales come from the frozen train performers; regularization selection
uses only the frozen dev performer.  Test performers and EGSet12 are never read.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import math
import sys
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

import numpy as np
import solver_quality_eval as quality_eval
from scipy.optimize import minimize
from scipy.special import expit

from fretsure.bench.contracts import canonical_json_bytes
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import Note
from fretsure.oracle.core import check_playability
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.solver import api as solver_api
from fretsure.solver.api import FINGERING_SOLVER_VERSION, Infeasible, SolverInputError
from fretsure.solver.cost import QualityCost
from fretsure.tab import Tab, tab_to_json

TrainingSplit = Literal["train", "dev"]
PoolSplit = Literal["train", "dev", "test", "held-out"]

RANKER_MODEL_SCHEMA: Final = "fingering-green-pairwise-ranker@0.1.0"
RANKER_REPORT_SCHEMA: Final = "fingering-green-pairwise-training-report@0.1.0"
FEATURE_SCHEMA: Final = "fingering-generic-burden-features@0.1.0"
PAIR_PROTOCOL: Final = "strict-lexicographic-human-imitation-pairs@0.1.0"
OPTIMIZER_PROTOCOL: Final = "scipy-nonnegative-bradley-terry@0.1.0"


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    name: str
    source: str
    unit: str
    description: str


FEATURES: Final[tuple[FeatureDefinition, ...]] = (
    FeatureDefinition(
        "max_fret",
        "QualityCost.max_fret",
        "fret",
        "highest fret used by the complete candidate",
    ),
    FeatureDefinition(
        "duration_weighted_fret_exposure",
        "QualityCost.fret_exposure",
        "fret-beat",
        "exact sum of sounding duration multiplied by fret",
    ),
    FeatureDefinition(
        "shift_count",
        "QualityCost.shift_count",
        "count",
        "number of disjoint feasible-hand-window transitions",
    ),
    FeatureDefinition(
        "shift_distance_micrometres",
        "QualityCost.shift_distance_um",
        "micrometre",
        "minimum physical distance between successive feasible hand windows",
    ),
    FeatureDefinition(
        "finger_load",
        "QualityCost.finger_load",
        "finger-attacks",
        "sum of distinct fretting fingers required by attack frames",
    ),
    FeatureDefinition(
        "string_crossings",
        "QualityCost.string_crossings",
        "string-distance",
        "discrete string travel between consecutive attack configurations",
    ),
)
FEATURE_NAMES: Final[tuple[str, ...]] = tuple(feature.name for feature in FEATURES)


@dataclass(frozen=True, slots=True)
class RankerConfig:
    guitarset_zip: Path = quality_eval.DEFAULT_GUITARSET_ZIP
    train_windows: int = 2
    dev_windows: int = 1
    notes_per_window: int = 8
    window_offset: int = 0
    quantize_denominator: int = 96
    beam: int = 16
    beats_per_bar: int = 4
    l2_grid: tuple[float, ...] = (0.001, 0.01, 0.1, 1.0)
    max_iterations: int = 500

    def validate(self) -> None:
        positive_integers = {
            "train_windows": self.train_windows,
            "dev_windows": self.dev_windows,
            "notes_per_window": self.notes_per_window,
            "quantize_denominator": self.quantize_denominator,
            "beam": self.beam,
            "beats_per_bar": self.beats_per_bar,
            "max_iterations": self.max_iterations,
        }
        for name, value in positive_integers.items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if type(self.window_offset) is not int or self.window_offset < 0:
            raise ValueError("window_offset must be a non-negative integer")
        if not self.l2_grid:
            raise ValueError("l2_grid must be non-empty")
        if any(
            type(value) is not float or not math.isfinite(value) or value <= 0
            for value in self.l2_grid
        ):
            raise ValueError("l2_grid entries must be finite positive floats")
        if len(set(self.l2_grid)) != len(self.l2_grid):
            raise ValueError("l2_grid entries must be unique")


@dataclass(frozen=True, slots=True)
class ImitationStats:
    notes_compared: int
    joint_mismatch_count: int
    string_distance: int
    fret_distance: int

    @property
    def preference_key(self) -> tuple[int, int, int]:
        """Existing offline imitation key without its stable-order tiebreak."""

        return (
            self.joint_mismatch_count,
            self.string_distance,
            self.fret_distance,
        )


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    query_id: str
    candidate_id: str
    stable_rank: int
    oracle_verdict: str
    production_selected: bool
    features: tuple[float, ...]
    imitation: ImitationStats

    def validate(self) -> None:
        if self.oracle_verdict != "GREEN":
            raise ValueError("experimental ranker candidates must be full-oracle GREEN")
        if len(self.features) != len(FEATURES):
            raise ValueError("candidate feature width does not match feature schema")
        if any(not math.isfinite(value) or value < 0 for value in self.features):
            raise ValueError("candidate burden features must be finite and non-negative")
        if self.imitation.notes_compared <= 0:
            raise ValueError("candidate must contain at least one compared note")


@dataclass(frozen=True, slots=True)
class QueryPool:
    split: PoolSplit
    query_id: str
    candidates: tuple[CandidateRecord, ...]

    def __post_init__(self) -> None:
        if not self.candidates:
            raise ValueError("query pool must be non-empty")
        for candidate in self.candidates:
            candidate.validate()
            if candidate.query_id != self.query_id:
                raise ValueError("candidate query id does not match its pool")
        ids = tuple(candidate.candidate_id for candidate in self.candidates)
        if ids != tuple(sorted(ids)) or len(ids) != len(set(ids)):
            raise ValueError("query candidates must have unique canonical order")
        if sum(candidate.production_selected for candidate in self.candidates) != 1:
            raise ValueError("query pool must identify exactly one production selection")


@dataclass(frozen=True, slots=True)
class PreferencePair:
    query_id: str
    preferred_candidate_id: str
    dispreferred_candidate_id: str
    burden_delta: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class PairDataset:
    pairs: tuple[PreferencePair, ...]
    tied_candidate_pairs: int


@dataclass(frozen=True, slots=True)
class SplitCoverage:
    selected_windows: int
    construction_rejections: int
    solver_rejections: int
    infeasible_windows: int
    non_green_windows: int
    green_pool_queries: int
    raw_green_candidates: int
    canonical_green_candidates: int
    accessed_sources: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class FitResult:
    l2: float
    weights: tuple[float, ...]
    objective: float
    iterations: int
    optimizer_status: str


def _quality_features(quality: QualityCost) -> tuple[float, ...]:
    values = (
        float(quality.max_fret),
        float(quality.fret_exposure),
        float(quality.shift_count),
        float(quality.shift_distance_um),
        float(quality.finger_load),
        float(quality.string_crossings),
    )
    assert len(values) == len(FEATURES)
    return values


def _quality_payload(quality: QualityCost) -> dict[str, object]:
    return {
        "max_fret": quality.max_fret,
        "fret_exposure": (
            f"{quality.fret_exposure.numerator}/{quality.fret_exposure.denominator}"
        ),
        "shift_count": quality.shift_count,
        "shift_distance_um": quality.shift_distance_um,
        "finger_load": quality.finger_load,
        "string_crossings": quality.string_crossings,
    }


def _candidate_id(tab: Tab, quality: QualityCost) -> str:
    digest = hashlib.sha256()
    digest.update(b"fretsure:fingering-ranker-candidate@0.1.0\0")
    digest.update(tab_to_json(tab).encode("ascii"))
    digest.update(b"\0")
    digest.update(canonical_json_bytes(_quality_payload(quality)))
    return digest.hexdigest()


def _query_id(window: quality_eval.EvaluationWindow) -> str:
    payload = {
        "corpus_id": window.corpus_id,
        "source_sha256": window.source_sha256,
        "split": window.split,
        "window_index": window.window_index,
        "tempo": f"{window.tempo_bpm.numerator}/{window.tempo_bpm.denominator}",
        "notes": [
            {
                "onset": f"{note.onset.numerator}/{note.onset.denominator}",
                "duration": f"{note.duration.numerator}/{note.duration.denominator}",
                "pitch": note.pitch,
                "string": note.string,
                "fret": note.fret,
            }
            for note in window.notes
        ],
    }
    return hashlib.sha256(
        b"fretsure:fingering-ranker-query@0.1.0\0" + canonical_json_bytes(payload)
    ).hexdigest()


def _imitation_stats(
    window: quality_eval.EvaluationWindow,
    tab: Tab,
) -> ImitationStats:
    comparison = quality_eval._comparison_metrics(window, tab)
    return ImitationStats(
        notes_compared=cast(int, comparison["notes_compared"]),
        joint_mismatch_count=cast(int, comparison["joint_mismatch_count"]),
        string_distance=cast(int, comparison["string_absolute_error_sum"]),
        fret_distance=cast(int, comparison["fret_absolute_error_sum"]),
    )


def canonical_query_pool(
    split: PoolSplit,
    query_id: str,
    candidates: Iterable[CandidateRecord],
) -> QueryPool:
    """Deduplicate and content-sort one already certified GREEN pool."""

    by_id: dict[str, CandidateRecord] = {}
    for candidate in candidates:
        candidate.validate()
        if candidate.query_id != query_id:
            raise ValueError("candidate query id does not match requested pool")
        existing = by_id.get(candidate.candidate_id)
        if existing is None:
            by_id[candidate.candidate_id] = candidate
            continue
        if existing.features != candidate.features or existing.imitation != candidate.imitation:
            raise ValueError("candidate id collision has different content")
        by_id[candidate.candidate_id] = CandidateRecord(
            query_id=query_id,
            candidate_id=candidate.candidate_id,
            stable_rank=min(existing.stable_rank, candidate.stable_rank),
            oracle_verdict="GREEN",
            production_selected=(
                existing.production_selected or candidate.production_selected
            ),
            features=existing.features,
            imitation=existing.imitation,
        )
    return QueryPool(split, query_id, tuple(by_id[key] for key in sorted(by_id)))


def _query_from_green_pool(
    window: quality_eval.EvaluationWindow,
    selected: Tab,
    pool: Sequence[solver_api._GreenFinalist],
    *,
    profile: Profile,
    beats_per_bar: int,
) -> QueryPool:
    if window.split not in ("train", "dev"):
        raise ValueError("ranker may construct query pools only from train/dev")
    query_id = _query_id(window)
    records: list[CandidateRecord] = []
    for finalist in pool:
        verdict = check_playability(
            finalist.tab,
            profile,
            tempo_bpm=float(window.tempo_bpm),
            beats_per_bar=beats_per_bar,
        ).verdict
        if verdict != "GREEN":
            raise RuntimeError("private GREEN pool contained a non-GREEN candidate")
        records.append(
            CandidateRecord(
                query_id=query_id,
                candidate_id=_candidate_id(finalist.tab, finalist.quality),
                stable_rank=finalist.stable_rank,
                oracle_verdict=verdict,
                production_selected=finalist.tab == selected,
                features=_quality_features(finalist.quality),
                imitation=_imitation_stats(window, finalist.tab),
            )
        )
    return canonical_query_pool(window.split, query_id, records)


def _collect_split(
    config: RankerConfig,
    split: TrainingSplit,
    *,
    profile: Profile,
) -> tuple[tuple[QueryPool, ...], SplitCoverage]:
    requested = config.train_windows if split == "train" else config.dev_windows
    selected_windows = 0
    construction_rejections = 0
    solver_rejections = 0
    infeasible_windows = 0
    non_green_windows = 0
    raw_green_candidates = 0
    accessed_sources: list[tuple[str, str]] = []
    pools: list[QueryPool] = []

    documents = quality_eval.iter_guitarset_documents(config.guitarset_zip, split=split)
    for window_index, document in enumerate(itertools.islice(documents, requested)):
        selected_windows += 1
        accessed_sources.append((document.source_id, document.source_sha256))
        selection = quality_eval.build_window(
            document,
            notes_per_window=config.notes_per_window,
            window_offset=config.window_offset,
            quantize_denominator=config.quantize_denominator,
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
                profile,
                tempo_bpm=float(selection.tempo_bpm),
                beats_per_bar=config.beats_per_bar,
                beam=config.beam,
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
        raw_green_candidates += len(outcome.green_pool)
        pools.append(
            _query_from_green_pool(
                selection,
                outcome.result,
                outcome.green_pool,
                profile=profile,
                beats_per_bar=config.beats_per_bar,
            )
        )
    if selected_windows != requested:
        raise ValueError(
            f"split {split} produced {selected_windows} windows; requested {requested}"
        )
    pools.sort(key=lambda pool: pool.query_id)
    return (
        tuple(pools),
        SplitCoverage(
            selected_windows=selected_windows,
            construction_rejections=construction_rejections,
            solver_rejections=solver_rejections,
            infeasible_windows=infeasible_windows,
            non_green_windows=non_green_windows,
            green_pool_queries=len(pools),
            raw_green_candidates=raw_green_candidates,
            canonical_green_candidates=sum(len(pool.candidates) for pool in pools),
            accessed_sources=tuple(accessed_sources),
        ),
    )


def build_preference_pairs(queries: Sequence[QueryPool]) -> PairDataset:
    pairs: list[PreferencePair] = []
    tied = 0
    for query in sorted(queries, key=lambda item: item.query_id):
        for left, right in itertools.combinations(query.candidates, 2):
            left_key = left.imitation.preference_key
            right_key = right.imitation.preference_key
            if left_key == right_key:
                tied += 1
                continue
            preferred, dispreferred = (
                (left, right) if left_key < right_key else (right, left)
            )
            pairs.append(
                PreferencePair(
                    query_id=query.query_id,
                    preferred_candidate_id=preferred.candidate_id,
                    dispreferred_candidate_id=dispreferred.candidate_id,
                    burden_delta=tuple(
                        worse - better
                        for better, worse in zip(
                            preferred.features,
                            dispreferred.features,
                            strict=True,
                        )
                    ),
                )
            )
    pairs.sort(
        key=lambda pair: (
            pair.query_id,
            pair.preferred_candidate_id,
            pair.dispreferred_candidate_id,
        )
    )
    return PairDataset(tuple(pairs), tied)


def fit_feature_scales(train_queries: Sequence[QueryPool]) -> tuple[float, ...]:
    """RMS scales computed only from canonically ordered train candidates."""

    candidates = tuple(
        candidate
        for query in sorted(train_queries, key=lambda item: item.query_id)
        for candidate in query.candidates
    )
    scales: list[float] = []
    for feature_index in range(len(FEATURES)):
        values = tuple(candidate.features[feature_index] for candidate in candidates)
        if not values:
            scales.append(1.0)
            continue
        rms = math.sqrt(math.fsum(value * value for value in values) / len(values))
        scales.append(rms if rms > 0 else 1.0)
    return tuple(scales)


def _scaled_pair_matrix(
    dataset: PairDataset,
    scales: Sequence[float],
) -> np.ndarray:
    if len(scales) != len(FEATURES):
        raise ValueError("feature scale width does not match feature schema")
    return np.asarray(
        [
            [value / scale for value, scale in zip(pair.burden_delta, scales, strict=True)]
            for pair in dataset.pairs
        ],
        dtype=np.float64,
    ).reshape((len(dataset.pairs), len(FEATURES)))


def fit_pairwise_ranker(
    train_pairs: PairDataset,
    scales: Sequence[float],
    *,
    l2: float,
    max_iterations: int,
) -> FitResult:
    """Fit deterministic non-negative Bradley–Terry burden weights."""

    if not math.isfinite(l2) or l2 <= 0:
        raise ValueError("l2 must be finite and positive")
    matrix = _scaled_pair_matrix(train_pairs, scales)
    if not train_pairs.pairs:
        return FitResult(l2, (0.0,) * len(FEATURES), 0.0, 0, "NO_TRAIN_PAIRS")

    def objective(weights: np.ndarray) -> tuple[float, np.ndarray]:
        margins = matrix @ weights
        loss = float(np.mean(np.logaddexp(0.0, -margins)))
        loss += 0.5 * l2 * float(weights @ weights)
        gradient = np.mean(-matrix * expit(-margins)[:, np.newaxis], axis=0)
        gradient += l2 * weights
        return loss, cast(np.ndarray, gradient)

    result = minimize(
        objective,
        np.zeros(len(FEATURES), dtype=np.float64),
        method="L-BFGS-B",
        jac=True,
        bounds=[(0.0, None)] * len(FEATURES),
        options={
            "maxiter": max_iterations,
            "ftol": 1e-12,
            "gtol": 1e-9,
            "maxls": 50,
            "maxcor": 10,
        },
    )
    if not result.success:
        raise RuntimeError(f"pairwise optimizer failed: {result.message}")
    weights = tuple(max(0.0, float(value)) for value in result.x)
    return FitResult(
        l2=l2,
        weights=weights,
        objective=float(result.fun),
        iterations=int(result.nit),
        optimizer_status=str(result.message),
    )


def _pair_metrics(
    dataset: PairDataset,
    scales: Sequence[float],
    weights: Sequence[float],
) -> dict[str, object]:
    matrix = _scaled_pair_matrix(dataset, scales)
    if not dataset.pairs:
        return {
            "strict_pairs": 0,
            "tied_candidate_pairs_skipped": dataset.tied_candidate_pairs,
            "accuracy": None,
            "log_loss": None,
            "positive_margins": 0,
            "zero_margins": 0,
        }
    margins = matrix @ np.asarray(weights, dtype=np.float64)
    return {
        "strict_pairs": len(dataset.pairs),
        "tied_candidate_pairs_skipped": dataset.tied_candidate_pairs,
        "accuracy": float(np.mean(margins > 0.0)),
        "log_loss": float(np.mean(np.logaddexp(0.0, -margins))),
        "positive_margins": int(np.sum(margins > 0.0)),
        "zero_margins": int(np.sum(margins == 0.0)),
    }


def _is_dominated(candidate: CandidateRecord, pool: QueryPool) -> bool:
    return any(
        other.candidate_id != candidate.candidate_id
        and all(
            other_value <= candidate_value
            for other_value, candidate_value in zip(
                other.features, candidate.features, strict=True
            )
        )
        and any(
            other_value < candidate_value
            for other_value, candidate_value in zip(
                other.features, candidate.features, strict=True
            )
        )
        for other in pool.candidates
    )


def select_model_candidate(
    pool: QueryPool,
    scales: Sequence[float],
    weights: Sequence[float],
) -> CandidateRecord:
    """Select lowest learned burden on the Pareto frontier.

    Pareto filtering is a weight-independent safety property: a candidate that
    is no better on any generic burden and strictly worse on at least one can
    never win, including the all-zero-weight cold-start case.
    """

    frontier = tuple(
        candidate
        for candidate in pool.candidates
        if not _is_dominated(candidate, pool)
    )
    if not frontier:
        raise RuntimeError("non-empty finite candidate pool has an empty Pareto frontier")

    def key(candidate: CandidateRecord) -> tuple[float, str]:
        score = math.fsum(
            weight * value / scale
            for weight, value, scale in zip(
                weights, candidate.features, scales, strict=True
            )
        )
        return (score, candidate.candidate_id)

    return min(frontier, key=key)


def _best_imitation_candidate(pool: QueryPool) -> CandidateRecord:
    return min(
        pool.candidates,
        key=lambda candidate: (candidate.imitation.preference_key, candidate.candidate_id),
    )


def _production_candidate(pool: QueryPool) -> CandidateRecord:
    return next(candidate for candidate in pool.candidates if candidate.production_selected)


def _selector_metrics(
    queries: Sequence[QueryPool],
    selector: Callable[[QueryPool], CandidateRecord],
) -> dict[str, object]:
    chosen = tuple(selector(query) for query in queries)
    notes = sum(candidate.imitation.notes_compared for candidate in chosen)
    mismatches = sum(candidate.imitation.joint_mismatch_count for candidate in chosen)
    return {
        "queries": len(chosen),
        "notes_compared": notes,
        "joint_exact_count": notes - mismatches,
        "joint_exact_rate": (notes - mismatches) / notes if notes else None,
        "string_distance": sum(candidate.imitation.string_distance for candidate in chosen),
        "fret_distance": sum(candidate.imitation.fret_distance for candidate in chosen),
        "mean_max_fret": (
            math.fsum(candidate.features[0] for candidate in chosen) / len(chosen)
            if chosen
            else None
        ),
    }


def _selection_metrics(
    queries: Sequence[QueryPool],
    scales: Sequence[float],
    weights: Sequence[float],
) -> dict[str, object]:
    def production(pool: QueryPool) -> CandidateRecord:
        return _production_candidate(pool)

    def model(pool: QueryPool) -> CandidateRecord:
        return select_model_candidate(pool, scales, weights)

    def best(pool: QueryPool) -> CandidateRecord:
        return _best_imitation_candidate(pool)
    return {
        "production_selected": _selector_metrics(queries, production),
        "model": _selector_metrics(queries, model),
        "imitation_best_in_green_pool": _selector_metrics(queries, best),
        "model_matches_imitation_best_queries": sum(
            model(query).candidate_id == best(query).candidate_id for query in queries
        ),
        "production_matches_imitation_best_queries": sum(
            production(query).candidate_id == best(query).candidate_id for query in queries
        ),
    }


def _select_hyperparameter(
    train_pairs: PairDataset,
    dev_pairs: PairDataset,
    scales: Sequence[float],
    config: RankerConfig,
) -> tuple[FitResult, list[dict[str, object]]]:
    trials: list[tuple[FitResult, dict[str, object]]] = []
    for l2 in config.l2_grid:
        fit = fit_pairwise_ranker(
            train_pairs,
            scales,
            l2=l2,
            max_iterations=config.max_iterations,
        )
        dev_metrics = _pair_metrics(dev_pairs, scales, fit.weights)
        trials.append(
            (
                fit,
                {
                    "l2": l2,
                    "train_objective": fit.objective,
                    "train_iterations": fit.iterations,
                    "dev_pair_metrics": dev_metrics,
                },
            )
        )
    if dev_pairs.pairs:
        selected = min(
            trials,
            key=lambda item: (
                cast(float, item[1]["dev_pair_metrics"]["log_loss"]),  # type: ignore[index]
                -cast(float, item[1]["dev_pair_metrics"]["accuracy"]),  # type: ignore[index]
                item[0].l2,
            ),
        )[0]
    else:
        selected = trials[0][0]
    return selected, [trial for _, trial in trials]


def _coverage_payload(
    coverage: SplitCoverage,
    pairs: PairDataset,
) -> dict[str, object]:
    return {
        "selected_windows": coverage.selected_windows,
        "construction_rejections": coverage.construction_rejections,
        "solver_rejections": coverage.solver_rejections,
        "infeasible_windows": coverage.infeasible_windows,
        "non_green_windows": coverage.non_green_windows,
        "green_pool_queries": coverage.green_pool_queries,
        "raw_full_green_candidates": coverage.raw_green_candidates,
        "canonical_full_green_candidates": coverage.canonical_green_candidates,
        "strict_preference_pairs": len(pairs.pairs),
        "tied_candidate_pairs_skipped": pairs.tied_candidate_pairs,
    }


def _float_text(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("model values must be finite")
    if value == 0:
        return "0"
    return format(value, ".17g")


def _accessed_corpus_digest(
    train_coverage: SplitCoverage,
    dev_coverage: SplitCoverage,
) -> str:
    """Digest only JAMS bytes that the fixed train/dev window selection read."""

    digest = hashlib.sha256()
    digest.update(b"fretsure:fingering-ranker-accessed-corpus@0.1.0\0")
    digest.update(
        canonical_json_bytes(
            {
                "corpus_id": quality_eval.GUITARSET_CORPUS_ID,
                "sampling_protocol": quality_eval.GUITARSET_SAMPLING_PROTOCOL,
                "train": [
                    {"member": member, "sha256": sha256}
                    for member, sha256 in train_coverage.accessed_sources
                ],
                "dev": [
                    {"member": member, "sha256": sha256}
                    for member, sha256 in dev_coverage.accessed_sources
                ],
            }
        )
    )
    return digest.hexdigest()


def train_ranker(
    config: RankerConfig,
    *,
    profile: Profile = MEDIAN_HAND,
) -> tuple[dict[str, object], dict[str, object]]:
    """Train on GuitarSet train and select regularization on GuitarSet dev."""

    config.validate()
    if not config.guitarset_zip.is_file():
        raise FileNotFoundError(f"GuitarSet annotation archive not found: {config.guitarset_zip}")

    train_queries, train_coverage = _collect_split(config, "train", profile=profile)
    dev_queries, dev_coverage = _collect_split(config, "dev", profile=profile)
    train_pairs = build_preference_pairs(train_queries)
    dev_pairs = build_preference_pairs(dev_queries)
    scales = fit_feature_scales(train_queries)
    selected_fit, trials = _select_hyperparameter(
        train_pairs,
        dev_pairs,
        scales,
        config,
    )

    feature_payload = [
        {
            "name": feature.name,
            "source": feature.source,
            "unit": feature.unit,
            "monotonic_direction": "lower_burden_is_better",
            "description": feature.description,
        }
        for feature in FEATURES
    ]
    corpus_digest = _accessed_corpus_digest(train_coverage, dev_coverage)
    scaled_weights = dict(
        zip(FEATURE_NAMES, (_float_text(value) for value in selected_fit.weights), strict=True)
    )
    raw_weights = dict(
        zip(
            FEATURE_NAMES,
            (
                _float_text(weight / scale)
                for weight, scale in zip(selected_fit.weights, scales, strict=True)
            ),
            strict=True,
        )
    )
    scale_payload = dict(
        zip(FEATURE_NAMES, (_float_text(value) for value in scales), strict=True)
    )
    configuration = {
        "train_windows": config.train_windows,
        "dev_windows": config.dev_windows,
        "notes_per_window": config.notes_per_window,
        "window_offset": config.window_offset,
        "quantize_denominator": config.quantize_denominator,
        "beam": config.beam,
        "beats_per_bar": config.beats_per_bar,
        "l2_grid": list(config.l2_grid),
        "max_iterations": config.max_iterations,
        "optimizer_initialization": "all_zero",
        "candidate_shuffle": False,
    }
    model: dict[str, object] = {
        "schema": RANKER_MODEL_SCHEMA,
        "experimental_only": True,
        "production_solver_integration": False,
        "feature_schema": {
            "id": FEATURE_SCHEMA,
            "features": feature_payload,
            "selection_safety": "pareto_frontier_before_linear_score",
        },
        "solver": {
            "version": FINGERING_SOLVER_VERSION,
            "tuning": list(STANDARD_TUNING),
            "capo": 0,
        },
        "profile": {
            "version": profile.version,
            "fingerprint": profile.fingerprint,
        },
        "corpus": {
            "id": quality_eval.GUITARSET_CORPUS_ID,
            "accessed_train_dev_digest": corpus_digest,
            "digest_scope": "selected train/dev JAMS member bytes only",
            "train_members_read": len(train_coverage.accessed_sources),
            "dev_members_read": len(dev_coverage.accessed_sources),
            "license": quality_eval.CORPUS_LICENSE,
            "record_url": quality_eval.GUITARSET_RECORD_URL,
        },
        "data_access": {
            "fit": "guitarset/train-only",
            "feature_scales": "guitarset/train-only",
            "hyperparameter_selection": "guitarset/dev-only",
            "guitarset_test": "not-read",
            "egset12_held_out": "not-read",
        },
        "split": {
            "performers": dict(sorted(quality_eval.GUITARSET_PERFORMER_SPLITS.items())),
            "sampling_protocol": quality_eval.GUITARSET_SAMPLING_PROTOCOL,
        },
        "configuration": configuration,
        "pair_protocol": PAIR_PROTOCOL,
        "optimizer_protocol": OPTIMIZER_PROTOCOL,
        "selected_l2": _float_text(selected_fit.l2),
        "feature_scales_train_only": scale_payload,
        "scaled_nonnegative_weights": scaled_weights,
        "raw_nonnegative_weights": raw_weights,
    }
    model_bytes = canonical_json_bytes(model)
    report: dict[str, object] = {
        "schema": RANKER_REPORT_SCHEMA,
        "model_schema": RANKER_MODEL_SCHEMA,
        "model_sha256": hashlib.sha256(model_bytes).hexdigest(),
        "accessed_train_dev_corpus_digest": corpus_digest,
        "configuration": configuration,
        "data_access_assertion": {
            "read_splits": ["train", "dev"],
            "fit_rows": "train-only",
            "scale_rows": "train-only",
            "hyperparameter_rows": "dev-only",
            "forbidden": ["guitarset/test", "egset12/held-out"],
        },
        "coverage": {
            "train": _coverage_payload(train_coverage, train_pairs),
            "dev": _coverage_payload(dev_coverage, dev_pairs),
        },
        "hyperparameter_trials": trials,
        "selected_fit": {
            "l2": selected_fit.l2,
            "train_objective": selected_fit.objective,
            "iterations": selected_fit.iterations,
            "optimizer_status": selected_fit.optimizer_status,
            "all_weights_nonnegative": all(value >= 0 for value in selected_fit.weights),
        },
        "pair_metrics": {
            "train": _pair_metrics(train_pairs, scales, selected_fit.weights),
            "dev": _pair_metrics(dev_pairs, scales, selected_fit.weights),
        },
        "selected_vs_model_vs_best": {
            "train": _selection_metrics(train_queries, scales, selected_fit.weights),
            "dev": _selection_metrics(dev_queries, scales, selected_fit.weights),
        },
    }
    return model, report


def canonical_artifact_bytes(value: dict[str, object]) -> bytes:
    """Expose the exact bytes used for reproducibility checks and artifacts."""

    return canonical_json_bytes(value)


def _parse_l2_grid(text: str) -> tuple[float, ...]:
    try:
        values = tuple(float(token) for token in text.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("l2 grid must be comma-separated floats") from exc
    if not values or any(not math.isfinite(value) or value <= 0 for value in values):
        raise argparse.ArgumentTypeError("l2 grid entries must be finite and positive")
    return values


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guitarset-zip", type=Path, default=quality_eval.DEFAULT_GUITARSET_ZIP)
    parser.add_argument("--train-windows", type=int, default=2)
    parser.add_argument("--dev-windows", type=int, default=1)
    parser.add_argument("--notes-per-window", type=int, default=8)
    parser.add_argument("--window-offset", type=int, default=0)
    parser.add_argument("--quantize-denominator", type=int, default=96)
    parser.add_argument("--beam", type=int, default=16)
    parser.add_argument("--beats-per-bar", type=int, default=4)
    parser.add_argument("--l2-grid", type=_parse_l2_grid, default=(0.001, 0.01, 0.1, 1.0))
    parser.add_argument("--max-iterations", type=int, default=500)
    parser.add_argument("--model-output", type=Path)
    parser.add_argument("--report-output", type=Path)
    return parser


def _write_artifact(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_artifact_bytes(value))


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = RankerConfig(
        guitarset_zip=args.guitarset_zip,
        train_windows=args.train_windows,
        dev_windows=args.dev_windows,
        notes_per_window=args.notes_per_window,
        window_offset=args.window_offset,
        quantize_denominator=args.quantize_denominator,
        beam=args.beam,
        beats_per_bar=args.beats_per_bar,
        l2_grid=args.l2_grid,
        max_iterations=args.max_iterations,
    )
    try:
        model, report = train_ranker(config)
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
    if args.model_output is not None:
        _write_artifact(args.model_output, model)
    if args.report_output is not None:
        _write_artifact(args.report_output, report)
    if args.model_output is None and args.report_output is None:
        sys.stdout.buffer.write(
            canonical_artifact_bytes({"model": model, "report": report}) + b"\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
