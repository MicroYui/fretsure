from __future__ import annotations

import importlib.util
import inspect
import sys
from fractions import Fraction
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from fretsure.bench.contracts import canonical_json_bytes
from fretsure.geometry import STANDARD_TUNING
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.cost import QualityCost
from fretsure.tab import Tab, TabNote

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
frozen_eval = _load_script("evaluate_fingering_ranker", "evaluate_fingering_ranker.py")
audit = _load_script(
    "audit_egset12_fingering_ranker",
    "audit_egset12_fingering_ranker.py",
)


def _model(*, notes_per_window: int = 24) -> object:
    return frozen_eval.FrozenModel(
        audit.FROZEN_MODEL_SHA256,
        {"schema": ranker.RANKER_MODEL_SCHEMA},
        (1.0,) * len(ranker.FEATURES),
        (1.0,) * len(ranker.FEATURES),
        notes_per_window,
        0,
        96,
        16,
        4,
    )


def _documents(*, split: str = "held-out") -> tuple[object, ...]:
    return tuple(
        quality_eval.CorpusDocument(
            corpus="egset12",
            corpus_id=quality_eval.EGSET12_CORPUS_ID,
            source_id=f"{index:02d}.jams",
            source_sha256=f"{index:064x}",
            split=split,
            tempo_bpm=Fraction(60),
            notes=(),
        )
        for index in range(1, 13)
    )


def _tab(string: int, fret: int) -> Tab:
    return Tab(
        (
            TabNote(
                Fraction(0),
                Fraction(1),
                string,
                fret,
                0 if fret == 0 else 1,
                "i",
            ),
        ),
        STANDARD_TUNING,
        0,
    )


def test_public_entry_binds_model_sha_and_exposes_no_training_or_guitarset_knobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    model = _model()

    def load(path: Path, *, expected_sha256: str) -> object:
        observed["path"] = path
        observed["sha"] = expected_sha256
        return model

    def evaluate(value: object, *, egset12_dir: Path) -> dict[str, object]:
        observed["model"] = value
        observed["egset12_dir"] = egset12_dir
        return {"schema": "fixture"}

    monkeypatch.setattr(frozen_eval, "load_frozen_model", load)
    monkeypatch.setattr(audit, "_evaluate_loaded_model_unchecked", evaluate)

    report = audit.evaluate_external_audit(
        Path("model.json"),
        egset12_dir=Path("egset12"),
    )

    assert report == {"schema": "fixture"}
    assert observed == {
        "path": Path("model.json"),
        "sha": audit.FROZEN_MODEL_SHA256,
        "model": model,
        "egset12_dir": Path("egset12"),
    }
    assert "profile" not in inspect.signature(audit.evaluate_external_audit).parameters
    args = audit._parser().parse_args([])
    assert not hasattr(args, "guitarset_zip")
    assert not hasattr(args, "train_windows")
    assert not hasattr(args, "expected_model_sha256")


def test_fixed_model_protocol_is_checked_before_external_data_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        quality_eval,
        "iter_egset12_documents",
        lambda path: pytest.fail(f"must not read external data: {path}"),
    )

    with pytest.raises(ValueError, match="configuration does not match"):
        audit._evaluate_loaded_model_unchecked(
            _model(notes_per_window=23),
            egset12_dir=Path("fixture"),
        )


def test_external_audit_is_deterministic_green_only_and_guarded_per_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = _documents()
    corpus_accesses: list[Path] = []
    solver_calls: list[tuple[object, ...]] = []
    oracle_checks: list[Tab] = []
    production_selector_calls: list[tuple[object, ...]] = []

    def iterate(path: Path) -> object:
        corpus_accesses.append(path)
        return iter(documents)

    monkeypatch.setattr(quality_eval, "iter_egset12_documents", iterate)
    monkeypatch.setattr(
        quality_eval,
        "iter_guitarset_documents",
        lambda *args, **kwargs: pytest.fail("GuitarSet must never be read"),
    )

    def build_window(document: object, **kwargs: object) -> object:
        assert kwargs == {
            "notes_per_window": 24,
            "window_offset": 0,
            "quantize_denominator": 96,
            "window_index": int(document.source_id[:2]) - 1,
        }
        return quality_eval.EvaluationWindow(
            corpus="egset12",
            corpus_id=quality_eval.EGSET12_CORPUS_ID,
            source_id=document.source_id,
            source_sha256=document.source_sha256,
            split="held-out",
            window_index=kwargs["window_index"],
            tempo_bpm=Fraction(60),
            notes=(
                quality_eval.WindowNote(
                    Fraction(0), Fraction(1), 64, 5, 0
                ),
            ),
        )

    monkeypatch.setattr(quality_eval, "build_window", build_window)

    legacy = _tab(4, 5)
    unguarded = _tab(3, 9)
    guarded = _tab(5, 0)
    finalists = (
        SimpleNamespace(
            tab=legacy,
            quality=QualityCost(max_fret=5, fret_exposure=Fraction(100)),
            stable_rank=1,
        ),
        SimpleNamespace(
            tab=unguarded,
            quality=QualityCost(max_fret=9, fret_exposure=Fraction(0)),
            stable_rank=2,
        ),
        SimpleNamespace(
            tab=guarded,
            quality=QualityCost(max_fret=0, fret_exposure=Fraction(90)),
            stable_rank=3,
        ),
    )

    def solve(*args: object, **kwargs: object) -> object:
        solver_calls.append(args + (kwargs,))
        assert args[1:] == (STANDARD_TUNING, 0, MEDIAN_HAND)
        assert kwargs == {
            "tempo_bpm": 60.0,
            "beats_per_bar": 4,
            "beam": 16,
            "_collect_full_green_pool": True,
        }
        # Deliberately differ from pool[0]: a v0.4 public result may already be
        # guarded/model-selected, but the audit baseline must stay historical.
        return SimpleNamespace(result=guarded, green_pool=finalists)

    monkeypatch.setattr(
        audit.solver_api,
        "_solve_fingering_with_green_pool",
        solve,
    )

    def check(tab: Tab, *args: object, **kwargs: object) -> object:
        oracle_checks.append(tab)
        assert args == (MEDIAN_HAND,)
        assert kwargs == {"tempo_bpm": 60.0, "beats_per_bar": 4}
        return SimpleNamespace(verdict="GREEN")

    monkeypatch.setattr(audit, "check_playability", check)

    def select_guarded(
        qualities: tuple[QualityCost, ...],
        stable_ranks: tuple[int, ...],
        *,
        legacy_index: int,
    ) -> int:
        production_selector_calls.append((qualities, stable_ranks, legacy_index))
        assert qualities == tuple(finalist.quality for finalist in finalists)
        assert stable_ranks == (1, 2, 3)
        assert legacy_index == 0
        return 2

    monkeypatch.setattr(
        audit.production_ranker,
        "select_guarded_green_index",
        select_guarded,
    )

    first = audit._evaluate_loaded_model_unchecked(
        _model(), egset12_dir=Path("fixture-egset12")
    )
    second = audit._evaluate_loaded_model_unchecked(
        _model(), egset12_dir=Path("fixture-egset12")
    )

    assert corpus_accesses == [Path("fixture-egset12"), Path("fixture-egset12")]
    assert len(solver_calls) == 24
    assert len(oracle_checks) == 72
    # Guard selection is evaluated once for per-query evidence and once for
    # aggregate metrics on each run: every call crosses the production API.
    assert len(production_selector_calls) == 48
    assert first["audit_classification"] == {
        "purpose": "external-exploratory-audit",
        "confirmatory": False,
        "guard_frozen_before_data_access": True,
        "training_or_weight_updates_allowed": False,
        "production_integration": False,
    }
    assert first["data_access_assertion"]["read"] == ["egset12/held-out"]
    assert first["model"]["artifact_candidate_generator_version"] == (
        "fingering-solver@0.3.0"
    )
    assert first["model"]["audit_runtime_solver_version"] == (
        audit.FINGERING_SOLVER_VERSION
    )
    assert first["model"]["guarded_selector"]["implementation"] == (
        "fretsure.solver.ranker.select_guarded_green_index"
    )
    assert first["coverage"] == {
        "selected_documents": 12,
        "selected_windows": 12,
        "construction_rejections": 0,
        "solver_rejections": 0,
        "infeasible_windows": 0,
        "non_green_windows": 0,
        "green_pool_queries": 12,
        "green_pool_query_rate": 1.0,
        "raw_full_green_candidates": 36,
        "canonical_full_green_candidates": 36,
    }

    metrics = first["selector_metrics"]
    assert metrics["legacy_production"]["joint_exact_rate"] == 0.0
    assert metrics["legacy_production"]["string_distance"] == 12
    assert metrics["legacy_production"]["fret_distance"] == 60
    assert metrics["legacy_production"]["mean_max_fret"] == 5.0
    assert metrics["frozen_model_unguarded"]["mean_max_fret"] == 9.0
    assert metrics["frozen_model_relative_max_fret_guarded"][
        "joint_exact_rate"
    ] == 1.0
    assert metrics["frozen_model_relative_max_fret_guarded"][
        "string_exact_rate"
    ] == 1.0
    assert metrics["frozen_model_relative_max_fret_guarded"][
        "fret_exact_rate"
    ] == 1.0
    assert metrics["frozen_model_relative_max_fret_guarded"][
        "mean_max_fret"
    ] == 0.0

    guard = first["relative_max_fret_guard_check"]
    assert guard["protocol_sha256"] == audit.GUARD_PROTOCOL_SHA256
    assert guard["queries_checked"] == 12
    assert guard["activated_queries"] == 12
    assert guard["violating_queries"] == 0
    assert guard["all_queries_guarded_max_fret_not_above_legacy"] is True
    assert all(
        item["guarded_model_max_fret"] <= item["legacy_max_fret"]
        for item in guard["per_query"]
    )
    assert [member["source_id"] for member in first["provenance"]["members"]] == [
        f"{index:02d}.jams" for index in range(1, 13)
    ]
    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_wrong_member_or_split_fails_before_window_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong_members = list(_documents())
    wrong_members[-1] = quality_eval.CorpusDocument(
        corpus="egset12",
        corpus_id=quality_eval.EGSET12_CORPUS_ID,
        source_id="13.jams",
        source_sha256="f" * 64,
        split="held-out",
        tempo_bpm=Fraction(60),
        notes=(),
    )
    monkeypatch.setattr(
        quality_eval,
        "iter_egset12_documents",
        lambda path: iter(wrong_members),
    )
    monkeypatch.setattr(
        quality_eval,
        "build_window",
        lambda *args, **kwargs: pytest.fail("invalid members must fail first"),
    )

    with pytest.raises(ValueError, match="01.jams through 12.jams"):
        audit._evaluate_loaded_model_unchecked(_model(), egset12_dir=Path("fixture"))

    monkeypatch.setattr(
        quality_eval,
        "iter_egset12_documents",
        lambda path: iter(_documents(split="test")),
    )
    with pytest.raises(ValueError, match="held-out EGSet12"):
        audit._evaluate_loaded_model_unchecked(_model(), egset12_dir=Path("fixture"))
