#!/usr/bin/env python3
"""Run a frozen, exploratory EGSet12 audit of GREEN-pool selectors.

This command is intentionally separate from training and production.  It reads
only the twelve EGSet12 JAMS members, never fits or updates a parameter, and
compares three selectors over the exact same fully rechecked Oracle-GREEN pool:
the legacy production choice, the frozen unguarded ranker, and that same ranker
behind a predeclared per-query relative maximum-fret guard.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

import evaluate_fingering_ranker as frozen_eval
import solver_quality_eval as quality_eval
import train_fingering_ranker as ranker

from fretsure.bench.contracts import canonical_json_bytes
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import Note
from fretsure.oracle.core import check_playability
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver import api as solver_api
from fretsure.solver import ranker as production_ranker
from fretsure.solver.api import FINGERING_SOLVER_VERSION, Infeasible, SolverInputError
from fretsure.solver.cost import QualityCost
from fretsure.tab import Tab

AUDIT_SCHEMA: Final = "fingering-egset12-external-ranker-audit@0.1.0"
FROZEN_MODEL_SHA256: Final = (
    "b6cc57b0b55ed55f959d827e46276371e87820938c5678adf860ffa60f845315"
)
EXPECTED_MEMBER_NAMES: Final = tuple(f"{index:02d}.jams" for index in range(1, 13))
NOTES_PER_WINDOW: Final = 24
WINDOW_OFFSET: Final = 0
QUANTIZE_DENOMINATOR: Final = 96
BEAM: Final = 16
BEATS_PER_BAR: Final = 4
GUARD_PROTOCOL: Final[dict[str, object]] = {
    "id": "relative-legacy-max-fret-ceiling@0.1.0",
    "reference_selector": "legacy-production-selected",
    "candidate_pool": "full-oracle-green-only",
    "eligibility": "candidate.max_fret <= legacy.max_fret",
    "secondary_selector": "production-decimal-ranker-pareto-frontier-then-score",
    "absolute_fret_threshold": None,
}
GUARD_PROTOCOL_SHA256: Final = hashlib.sha256(
    b"fretsure:relative-max-fret-guard@0.1.0\0"
    + canonical_json_bytes(GUARD_PROTOCOL)
).hexdigest()
DEFAULT_MODEL: Final = frozen_eval.DEFAULT_MODEL
DEFAULT_EGSET12_DIR: Final = quality_eval.DEFAULT_EGSET12_DIR
DEFAULT_OUTPUT: Final = Path(
    "outputs/fingering-ranker-guitarset-train60-dev30-24notes-20260723/"
    "egset12-external-audit.json"
)


@dataclass(frozen=True, slots=True)
class ExactImitationStats:
    notes_compared: int
    joint_exact_count: int
    string_exact_count: int
    fret_exact_count: int
    string_distance: int
    fret_distance: int


@dataclass(frozen=True, slots=True)
class ProductionCandidateInput:
    candidate_id: str
    quality: QualityCost
    stable_rank: int


@dataclass(frozen=True, slots=True)
class ExternalQueryPool:
    pool: ranker.QueryPool
    exact_stats: tuple[tuple[str, ExactImitationStats], ...]
    production_order: tuple[ProductionCandidateInput, ...]

    def stats_for(self, candidate: ranker.CandidateRecord) -> ExactImitationStats:
        for candidate_id, stats in self.exact_stats:
            if candidate_id == candidate.candidate_id:
                return stats
        raise RuntimeError("audit candidate has no exact-comparison record")


def _member_set_sha256(
    documents: Sequence[quality_eval.CorpusDocument],
) -> str:
    digest = hashlib.sha256()
    digest.update(b"fretsure:egset12-member-set@0.1.0\0")
    for document in documents:
        digest.update(document.source_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(document.source_sha256))
    return digest.hexdigest()


def _validate_model_protocol(model: frozen_eval.FrozenModel) -> None:
    if model.sha256 != FROZEN_MODEL_SHA256:
        raise ValueError("external audit model does not match the frozen model SHA-256")
    actual = (
        model.notes_per_window,
        model.window_offset,
        model.quantize_denominator,
        model.beam,
        model.beats_per_bar,
    )
    expected = (
        NOTES_PER_WINDOW,
        WINDOW_OFFSET,
        QUANTIZE_DENOMINATOR,
        BEAM,
        BEATS_PER_BAR,
    )
    if actual != expected:
        raise ValueError("frozen model configuration does not match the EGSet12 protocol")


def _validate_documents(
    documents: Sequence[quality_eval.CorpusDocument],
) -> None:
    member_names = tuple(document.source_id for document in documents)
    if member_names != EXPECTED_MEMBER_NAMES:
        raise ValueError(
            "EGSet12 audit requires exactly the sorted members 01.jams through 12.jams"
        )
    for document in documents:
        if (
            document.corpus != "egset12"
            or document.corpus_id != quality_eval.EGSET12_CORPUS_ID
            or document.split != "held-out"
        ):
            raise ValueError("EGSet12 audit accepts only the held-out EGSet12 split")
        if len(document.source_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in document.source_sha256
        ):
            raise ValueError("EGSet12 member SHA-256 must be lowercase hexadecimal")


def _exact_stats(comparison: dict[str, object]) -> ExactImitationStats:
    return ExactImitationStats(
        notes_compared=cast(int, comparison["notes_compared"]),
        joint_exact_count=cast(int, comparison["string_fret_exact_count"]),
        string_exact_count=cast(int, comparison["string_exact_count"]),
        fret_exact_count=cast(int, comparison["fret_exact_count"]),
        string_distance=cast(int, comparison["string_absolute_error_sum"]),
        fret_distance=cast(int, comparison["fret_absolute_error_sum"]),
    )


def _held_out_query_pool(
    window: quality_eval.EvaluationWindow,
    selected: Tab,
    finalists: Sequence[solver_api._GreenFinalist],
) -> ExternalQueryPool:
    if window.corpus != "egset12" or window.split != "held-out":
        raise ValueError("external audit query must come from held-out EGSet12")
    query_id = ranker._query_id(window)
    records: list[ranker.CandidateRecord] = []
    stats_by_id: dict[str, ExactImitationStats] = {}
    production_order: list[ProductionCandidateInput] = []
    for finalist in finalists:
        verdict = check_playability(
            finalist.tab,
            MEDIAN_HAND,
            tempo_bpm=float(window.tempo_bpm),
            beats_per_bar=BEATS_PER_BAR,
        ).verdict
        if verdict != "GREEN":
            raise RuntimeError("external audit pool contains a non-GREEN finalist")
        candidate_id = ranker._candidate_id(finalist.tab, finalist.quality)
        comparison = quality_eval._comparison_metrics(window, finalist.tab)
        exact = _exact_stats(comparison)
        previous = stats_by_id.setdefault(candidate_id, exact)
        if previous != exact:
            raise RuntimeError("duplicate audit candidate has inconsistent comparison")
        production_order.append(
            ProductionCandidateInput(
                candidate_id,
                finalist.quality,
                finalist.stable_rank,
            )
        )
        records.append(
            ranker.CandidateRecord(
                query_id=query_id,
                candidate_id=candidate_id,
                stable_rank=finalist.stable_rank,
                oracle_verdict=verdict,
                production_selected=finalist.tab == selected,
                features=ranker._quality_features(finalist.quality),
                imitation=ranker._imitation_stats(window, finalist.tab),
            )
        )
    pool = ranker.canonical_query_pool("held-out", query_id, records)
    if set(stats_by_id) != {candidate.candidate_id for candidate in pool.candidates}:
        raise RuntimeError("canonical audit pool and comparison records disagree")
    if not production_order or not next(
        candidate
        for candidate in pool.candidates
        if candidate.candidate_id == production_order[0].candidate_id
    ).production_selected:
        raise RuntimeError("first production-order candidate is not the legacy winner")
    return ExternalQueryPool(
        pool,
        tuple(sorted(stats_by_id.items())),
        tuple(production_order),
    )


def _select_relative_max_fret_guarded(
    query: ExternalQueryPool,
) -> ranker.CandidateRecord:
    """Call the production Decimal selector on original qualities and order."""

    winner_index = production_ranker.select_guarded_green_index(
        tuple(candidate.quality for candidate in query.production_order),
        tuple(candidate.stable_rank for candidate in query.production_order),
        legacy_index=0,
    )
    winner_id = query.production_order[winner_index].candidate_id
    return next(
        candidate
        for candidate in query.pool.candidates
        if candidate.candidate_id == winner_id
    )


def _selector_metrics(
    queries: Sequence[ExternalQueryPool],
    selector: Callable[[ranker.QueryPool], ranker.CandidateRecord],
) -> dict[str, object]:
    chosen = tuple(
        (selector(query.pool), query)
        for query in queries
    )
    notes = sum(query.stats_for(candidate).notes_compared for candidate, query in chosen)
    joint = sum(query.stats_for(candidate).joint_exact_count for candidate, query in chosen)
    string = sum(query.stats_for(candidate).string_exact_count for candidate, query in chosen)
    fret = sum(query.stats_for(candidate).fret_exact_count for candidate, query in chosen)

    def rate(count: int) -> float | None:
        return count / notes if notes else None

    return {
        "queries": len(chosen),
        "notes_compared": notes,
        "joint_exact_count": joint,
        "joint_exact_rate": rate(joint),
        "string_exact_count": string,
        "string_exact_rate": rate(string),
        "fret_exact_count": fret,
        "fret_exact_rate": rate(fret),
        "string_distance": sum(
            query.stats_for(candidate).string_distance for candidate, query in chosen
        ),
        "fret_distance": sum(
            query.stats_for(candidate).fret_distance for candidate, query in chosen
        ),
        "mean_max_fret": (
            math.fsum(candidate.features[0] for candidate, _query in chosen) / len(chosen)
            if chosen
            else None
        ),
    }


def _selection_comparison(
    queries: Sequence[ExternalQueryPool],
    model: frozen_eval.FrozenModel,
) -> tuple[dict[str, object], dict[str, object]]:
    def legacy(pool: ranker.QueryPool) -> ranker.CandidateRecord:
        return ranker._production_candidate(pool)

    def unguarded(pool: ranker.QueryPool) -> ranker.CandidateRecord:
        return ranker.select_model_candidate(pool, model.scales, model.weights)

    query_by_id = {query.pool.query_id: query for query in queries}

    def guarded(pool: ranker.QueryPool) -> ranker.CandidateRecord:
        return _select_relative_max_fret_guarded(query_by_id[pool.query_id])

    max_fret_index = ranker.FEATURE_NAMES.index("max_fret")
    query_checks: list[dict[str, object]] = []
    for query in queries:
        legacy_candidate = legacy(query.pool)
        unguarded_candidate = unguarded(query.pool)
        guarded_candidate = guarded(query.pool)
        legacy_max = legacy_candidate.features[max_fret_index]
        unguarded_max = unguarded_candidate.features[max_fret_index]
        guarded_max = guarded_candidate.features[max_fret_index]
        query_checks.append(
            {
                "query_id": query.pool.query_id,
                "legacy_max_fret": legacy_max,
                "unguarded_model_max_fret": unguarded_max,
                "guarded_model_max_fret": guarded_max,
                "guard_activated": unguarded_max > legacy_max,
                "guarded_not_above_legacy": guarded_max <= legacy_max,
            }
        )
    violating = sum(
        not cast(bool, check["guarded_not_above_legacy"])
        for check in query_checks
    )
    metrics: dict[str, object] = {
        "legacy_production": _selector_metrics(queries, legacy),
        "frozen_model_unguarded": _selector_metrics(queries, unguarded),
        "frozen_model_relative_max_fret_guarded": _selector_metrics(queries, guarded),
    }
    guard_check: dict[str, object] = {
        "protocol": GUARD_PROTOCOL,
        "protocol_sha256": GUARD_PROTOCOL_SHA256,
        "queries_checked": len(query_checks),
        "activated_queries": sum(
            cast(bool, check["guard_activated"]) for check in query_checks
        ),
        "violating_queries": violating,
        "all_queries_guarded_max_fret_not_above_legacy": violating == 0,
        "per_query": query_checks,
    }
    return metrics, guard_check


def _evaluate_loaded_model_unchecked(
    model: frozen_eval.FrozenModel,
    *,
    egset12_dir: Path = DEFAULT_EGSET12_DIR,
) -> dict[str, object]:
    _validate_model_protocol(model)
    documents = tuple(quality_eval.iter_egset12_documents(egset12_dir))
    _validate_documents(documents)

    construction_rejections = 0
    solver_rejections = 0
    infeasible_windows = 0
    non_green_windows = 0
    raw_green_candidates = 0
    queries: list[ExternalQueryPool] = []
    for window_index, document in enumerate(documents):
        selection = quality_eval.build_window(
            document,
            notes_per_window=NOTES_PER_WINDOW,
            window_offset=WINDOW_OFFSET,
            quantize_denominator=QUANTIZE_DENOMINATOR,
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
                beats_per_bar=BEATS_PER_BAR,
                beam=BEAM,
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
        # The private pool remains in historical v0.3 production order even
        # when a newer public solver applies a post-pool selector.  The audit's
        # legacy baseline is therefore the first pool member, never the newer
        # ``outcome.result``.
        legacy_selected = outcome.green_pool[0].tab
        queries.append(
            _held_out_query_pool(selection, legacy_selected, outcome.green_pool)
        )

    selection_comparison, guard_check = _selection_comparison(queries, model)
    return {
        "schema": AUDIT_SCHEMA,
        "audit_classification": {
            "purpose": "external-exploratory-audit",
            "confirmatory": False,
            "guard_frozen_before_data_access": True,
            "training_or_weight_updates_allowed": False,
            "production_integration": False,
        },
        "model": {
            "sha256": model.sha256,
            "schema": model.payload["schema"],
            "feature_schema": ranker.FEATURE_SCHEMA,
            "artifact_candidate_generator_version": (
                frozen_eval.FROZEN_CANDIDATE_GENERATOR_VERSION
            ),
            "audit_runtime_solver_version": FINGERING_SOLVER_VERSION,
            "pool_generation_compatibility_assumption": (
                "v0.4 changes only post-pool selection; full GREEN pool order "
                "is the historical v0.3 legacy order"
            ),
            "guarded_selector": {
                "implementation": "fretsure.solver.ranker.select_guarded_green_index",
                "ranker_version": production_ranker.FINGERING_RANKER_VERSION,
                "model_sha256": production_ranker.FINGERING_RANKER_MODEL_SHA256,
                "numeric_semantics": "Decimal-precision-50",
            },
        },
        "configuration": {
            "split": "held-out",
            "documents": 12,
            "windows_per_document": 1,
            "notes_per_window": NOTES_PER_WINDOW,
            "window_offset": WINDOW_OFFSET,
            "quantize_denominator": QUANTIZE_DENOMINATOR,
            "beam": BEAM,
            "beats_per_bar": BEATS_PER_BAR,
            "profile": {
                "fingerprint": MEDIAN_HAND.fingerprint,
                "version": MEDIAN_HAND.version,
            },
        },
        "data_access_assertion": {
            "read": ["egset12/held-out"],
            "forbidden": [
                "guitarset/train",
                "guitarset/dev",
                "guitarset/test",
            ],
            "training_or_weight_updates": False,
        },
        "provenance": {
            "corpus_id": quality_eval.EGSET12_CORPUS_ID,
            "license": quality_eval.CORPUS_LICENSE,
            "record_url": quality_eval.EGSET12_RECORD_URL,
            "member_set_sha256": _member_set_sha256(documents),
            "members": [
                {
                    "source_id": document.source_id,
                    "sha256": document.source_sha256,
                    "split": document.split,
                }
                for document in documents
            ],
        },
        "coverage": {
            "selected_documents": len(documents),
            "selected_windows": len(documents),
            "construction_rejections": construction_rejections,
            "solver_rejections": solver_rejections,
            "infeasible_windows": infeasible_windows,
            "non_green_windows": non_green_windows,
            "green_pool_queries": len(queries),
            "green_pool_query_rate": len(queries) / len(documents),
            "raw_full_green_candidates": raw_green_candidates,
            "canonical_full_green_candidates": sum(
                len(query.pool.candidates) for query in queries
            ),
        },
        "selector_metrics": selection_comparison,
        "relative_max_fret_guard_check": guard_check,
    }


def evaluate_external_audit(
    model_path: Path = DEFAULT_MODEL,
    *,
    egset12_dir: Path = DEFAULT_EGSET12_DIR,
) -> dict[str, object]:
    model = frozen_eval.load_frozen_model(
        model_path,
        expected_sha256=FROZEN_MODEL_SHA256,
    )
    return _evaluate_loaded_model_unchecked(model, egset12_dir=egset12_dir)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--egset12-dir", type=Path, default=DEFAULT_EGSET12_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = evaluate_external_audit(
            args.model,
            egset12_dir=args.egset12_dir,
        )
    except (
        FileNotFoundError,
        RuntimeError,
        SolverInputError,
        ValueError,
        quality_eval.CorpusDataError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(canonical_json_bytes(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
