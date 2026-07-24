from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
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


def _model_payload(*, negative_weight: bool = False) -> dict[str, object]:
    weights = {
        name: ("-1" if negative_weight and index == 0 else "1")
        for index, name in enumerate(ranker.FEATURE_NAMES)
    }
    return {
        "schema": ranker.RANKER_MODEL_SCHEMA,
        "experimental_only": True,
        "production_solver_integration": False,
        "feature_schema": {
            "id": ranker.FEATURE_SCHEMA,
            "features": [{"name": name} for name in ranker.FEATURE_NAMES],
        },
        "solver": {
            "capo": 0,
            "tuning": list(STANDARD_TUNING),
            "version": frozen_eval.FROZEN_CANDIDATE_GENERATOR_VERSION,
        },
        "profile": {
            "fingerprint": MEDIAN_HAND.fingerprint,
            "version": MEDIAN_HAND.version,
        },
        "feature_scales_train_only": {name: "1" for name in ranker.FEATURE_NAMES},
        "scaled_nonnegative_weights": weights,
        "configuration": {
            "notes_per_window": 1,
            "window_offset": 0,
            "quantize_denominator": 96,
            "beam": 16,
            "beats_per_bar": 4,
        },
    }


def _write_model(path: Path, payload: dict[str, object]) -> str:
    raw = canonical_json_bytes(payload)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _frozen_model(*, sha256: str | None = None) -> object:
    return frozen_eval.FrozenModel(
        sha256 or frozen_eval.FROZEN_MODEL_SHA256,
        {"schema": ranker.RANKER_MODEL_SCHEMA},
        (1.0,) * len(ranker.FEATURES),
        (1.0,) * len(ranker.FEATURES),
        1,
        0,
        96,
        16,
        4,
    )


def test_checked_in_default_model_is_the_frozen_artifact() -> None:
    model = frozen_eval.load_frozen_model(
        ROOT / frozen_eval.DEFAULT_MODEL,
        expected_sha256=frozen_eval.FROZEN_MODEL_SHA256,
    )

    assert model.sha256 == frozen_eval.FROZEN_MODEL_SHA256
    assert model.payload["production_solver_integration"] is False


def test_model_loader_binds_sha_schema_features_and_nonnegative_weights(
    tmp_path: Path,
) -> None:
    path = tmp_path / "model.json"
    digest = _write_model(path, _model_payload())

    model = frozen_eval.load_frozen_model(path, expected_sha256=digest)

    assert model.sha256 == digest
    assert model.weights == (1.0,) * len(ranker.FEATURES)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        frozen_eval.load_frozen_model(path, expected_sha256="0" * 64)

    bad_path = tmp_path / "negative.json"
    bad_digest = _write_model(bad_path, _model_payload(negative_weight=True))
    with pytest.raises(ValueError, match="non-negative"):
        frozen_eval.load_frozen_model(bad_path, expected_sha256=bad_digest)

    noncanonical_path = tmp_path / "noncanonical.json"
    noncanonical_raw = json.dumps(_model_payload(), indent=2).encode("utf-8")
    noncanonical_path.write_bytes(noncanonical_raw)
    with pytest.raises(ValueError, match="canonical JSON encoding"):
        frozen_eval.load_frozen_model(
            noncanonical_path,
            expected_sha256=hashlib.sha256(noncanonical_raw).hexdigest(),
        )


def test_public_evaluation_loads_path_with_frozen_digest_and_fixed_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "profile" not in inspect.signature(
        frozen_eval.evaluate_frozen_model
    ).parameters
    model = _frozen_model()
    observed: dict[str, object] = {}

    def load(path: Path, *, expected_sha256: str) -> object:
        observed["model_path"] = path
        observed["expected_sha256"] = expected_sha256
        return model

    def evaluate(value: object, *, guitarset_zip: Path) -> dict[str, object]:
        observed["model"] = value
        observed["guitarset_zip"] = guitarset_zip
        return {"schema": "fixture"}

    monkeypatch.setattr(frozen_eval, "load_frozen_model", load)
    monkeypatch.setattr(frozen_eval, "_evaluate_loaded_model_unchecked", evaluate)

    result = frozen_eval.evaluate_frozen_model(
        Path("fixture-model.json"),
        guitarset_zip=Path("fixture-annotation.zip"),
    )

    assert result == {"schema": "fixture"}
    assert observed == {
        "model_path": Path("fixture-model.json"),
        "expected_sha256": frozen_eval.FROZEN_MODEL_SHA256,
        "model": model,
        "guitarset_zip": Path("fixture-annotation.zip"),
    }


def test_archive_digest_mismatch_fails_before_corpus_iteration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(frozen_eval, "_sha256_file", lambda path: "0" * 64)
    monkeypatch.setattr(
        quality_eval,
        "iter_guitarset_documents",
        lambda *args, **kwargs: pytest.fail("corpus iterator must not run"),
    )

    with pytest.raises(ValueError, match="annotation.zip SHA-256 mismatch"):
        frozen_eval._evaluate_loaded_model_unchecked(
            _frozen_model(),
            guitarset_zip=Path("fixture.zip"),
        )


def test_member_set_digest_mismatch_fails_before_window_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = tuple(
        quality_eval.CorpusDocument(
            corpus="guitarset",
            corpus_id=quality_eval.GUITARSET_CORPUS_ID,
            source_id=f"05_fixture_{index:02d}_solo.jams",
            source_sha256=f"{index + 1:064x}",
            split="test",
            tempo_bpm=Fraction(60),
            notes=(),
        )
        for index in range(60)
    )
    monkeypatch.setattr(
        frozen_eval,
        "_sha256_file",
        lambda path: frozen_eval.GUITARSET_ANNOTATION_SHA256,
    )
    monkeypatch.setattr(
        quality_eval,
        "iter_guitarset_documents",
        lambda path, *, split: iter(documents),
    )
    monkeypatch.setattr(
        frozen_eval,
        "_guitarset_member_set_sha256",
        lambda values: "0" * 64,
    )
    monkeypatch.setattr(
        quality_eval,
        "build_window",
        lambda *args, **kwargs: pytest.fail("window construction must not run"),
    )

    with pytest.raises(ValueError, match="test member-set SHA-256 mismatch"):
        frozen_eval._evaluate_loaded_model_unchecked(
            _frozen_model(),
            guitarset_zip=Path("fixture.zip"),
        )


def test_frozen_evaluation_reads_only_test_and_is_byte_deterministic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = tuple(
        quality_eval.CorpusDocument(
            corpus="guitarset",
            corpus_id=quality_eval.GUITARSET_CORPUS_ID,
            source_id=f"05_fixture_{index:02d}_solo.jams",
            source_sha256=f"{index + 1:064x}",
            split="test",
            tempo_bpm=Fraction(60),
            notes=(),
        )
        for index in range(60)
    )
    accesses: list[str] = []
    solver_profiles: list[object] = []

    def iter_documents(path: Path, *, split: str) -> object:
        del path
        accesses.append(split)
        return iter(documents)

    def build_window(document: object, **kwargs: object) -> object:
        window_index = int(kwargs["window_index"])
        return quality_eval.EvaluationWindow(
            corpus="guitarset",
            corpus_id=quality_eval.GUITARSET_CORPUS_ID,
            source_id=document.source_id,
            source_sha256=document.source_sha256,
            split="test",
            window_index=window_index,
            tempo_bpm=Fraction(60),
            notes=(quality_eval.WindowNote(Fraction(0), Fraction(1), 64, 5, 0),),
        )

    tab = Tab(
        (TabNote(Fraction(0), Fraction(1), 5, 0, 0, "i"),),
        STANDARD_TUNING,
        0,
    )
    finalist = SimpleNamespace(tab=tab, quality=QualityCost(), stable_rank=1)
    monkeypatch.setattr(
        frozen_eval,
        "_sha256_file",
        lambda path: frozen_eval.GUITARSET_ANNOTATION_SHA256,
    )
    monkeypatch.setattr(
        frozen_eval,
        "_guitarset_member_set_sha256",
        lambda values: frozen_eval.GUITARSET_TEST_MEMBER_SET_SHA256,
    )
    monkeypatch.setattr(quality_eval, "iter_guitarset_documents", iter_documents)
    monkeypatch.setattr(quality_eval, "build_window", build_window)

    def solve(*args: object, **kwargs: object) -> object:
        del kwargs
        solver_profiles.append(args[3])
        return SimpleNamespace(result=tab, green_pool=(finalist,))

    monkeypatch.setattr(
        frozen_eval.solver_api,
        "_solve_fingering_with_green_pool",
        solve,
    )
    model = _frozen_model()

    first = frozen_eval._evaluate_loaded_model_unchecked(
        model, guitarset_zip=Path("fixture.zip")
    )
    second = frozen_eval._evaluate_loaded_model_unchecked(
        model, guitarset_zip=Path("fixture.zip")
    )

    assert accesses == ["test", "test"]
    assert solver_profiles == [MEDIAN_HAND] * 120
    assert first["data_access_assertion"]["read_splits"] == ["test"]
    assert (
        first["test_provenance"]["annotation_zip_sha256"]
        == frozen_eval.GUITARSET_ANNOTATION_SHA256
    )
    assert (
        first["test_provenance"]["member_set_sha256"]
        == frozen_eval.GUITARSET_TEST_MEMBER_SET_SHA256
    )
    assert first["coverage"]["green_pool_queries"] == 60
    assert first["selected_vs_model_vs_best"]["model"]["joint_exact_rate"] == 1.0
    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_test_pool_rejects_non_test_windows() -> None:
    window = quality_eval.EvaluationWindow(
        corpus="guitarset",
        corpus_id="fixture",
        source_id="04_dev.jams",
        source_sha256="0" * 64,
        split="dev",
        window_index=0,
        tempo_bpm=Fraction(60),
        notes=(quality_eval.WindowNote(Fraction(0), Fraction(1), 64, 5, 0),),
    )
    tab = Tab(
        (TabNote(Fraction(0), Fraction(1), 5, 0, 0, "i"),),
        STANDARD_TUNING,
        0,
    )
    with pytest.raises(ValueError, match="only GuitarSet test"):
        frozen_eval._test_query_pool(
            window,
            tab,
            (SimpleNamespace(tab=tab, quality=QualityCost(), stable_rank=1),),
            beats_per_bar=4,
        )


def test_cli_removes_sha_override_and_main_uses_safe_evaluator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit):
        frozen_eval._parser().parse_args(
            ["--expected-model-sha256", "0" * 64]
        )

    observed: dict[str, object] = {}

    def evaluate(path: Path, *, guitarset_zip: Path) -> dict[str, object]:
        observed["model_path"] = path
        observed["guitarset_zip"] = guitarset_zip
        return {"schema": "fixture"}

    monkeypatch.setattr(frozen_eval, "evaluate_frozen_model", evaluate)
    output = tmp_path / "report.json"
    result = frozen_eval.main(
        [
            "--model",
            "fixture-model.json",
            "--guitarset-zip",
            "fixture-annotation.zip",
            "--output",
            str(output),
        ]
    )

    assert result == 0
    assert observed["model_path"] == Path("fixture-model.json")
    assert observed["guitarset_zip"] == Path("fixture-annotation.zip")
    assert output.read_bytes() == canonical_json_bytes({"schema": "fixture"})
