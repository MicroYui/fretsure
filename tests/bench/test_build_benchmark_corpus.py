from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import Any, cast

import pytest

from fretsure.bench.contamination import (  # type: ignore[import-untyped]
    ContaminationFinding,
    ContaminationKind,
    ContaminationReport,
    CorpusStratum,
    StratumContaminationReport,
)
from fretsure.bench.contracts import canonical_json_bytes  # type: ignore[import-untyped]
from fretsure.bench.corpus import (  # type: ignore[import-untyped]
    ProceduralCorpusConfig,
    corpus_from_dict,
    corpus_sha256,
)
from fretsure.bench.corpus_sources import (  # type: ignore[import-untyped]
    source_census_from_dict,
    source_census_sha256,
)
from fretsure.bench.public_adapters import (  # type: ignore[import-untyped]
    BENCHMARK_PUBLIC_ADAPTER_VERSION,
    BENCHMARK_PUBLIC_ROUTER_VERSION,
)

_ROOT = Path(__file__).resolve().parents[2]
_CENSUS = _ROOT / "data" / "benchmark" / "source-census.json"
_SOURCES = _ROOT / "data" / "benchmark" / "sources"
_SPEC = importlib.util.spec_from_file_location(
    "fretsure_test_build_benchmark_corpus",
    _ROOT / "scripts" / "build_benchmark_corpus.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)
_BUILDER = cast(Any, _MODULE)
CONTAMINATION_REPORT_VERSION = cast(str, _BUILDER.CONTAMINATION_REPORT_VERSION)
CORPUS_BUILD_RECEIPT_VERSION = cast(str, _BUILDER.CORPUS_BUILD_RECEIPT_VERSION)
CorpusBuildError = cast(type[ValueError], _BUILDER.CorpusBuildError)
build_benchmark_corpus = _BUILDER.build_benchmark_corpus
main = _BUILDER.main
_contamination_to_dict = _BUILDER._contamination_to_dict
_OUTPUT_NAMES = {
    "contamination.json",
    "corpus.json",
    "datasheet.json",
    "receipt.json",
    "source-census.json",
}


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_bytes())
    assert type(value) is dict
    return value


def _assert_canonical_json(path: Path) -> None:
    data = path.read_bytes()
    assert canonical_json_bytes(json.loads(data)) == data


def test_build_uses_local_pins_and_two_fresh_runs_are_byte_identical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_network(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("corpus build attempted network access")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden_network)
    config = ProceduralCorpusConfig(family_count=3)
    first = tmp_path / "first"
    second = tmp_path / "second"

    first_result = build_benchmark_corpus(
        census_path=_CENSUS,
        source_cache_dir=_SOURCES,
        output_dir=first,
        procedural_config=config,
    )
    second_result = build_benchmark_corpus(
        census_path=_CENSUS,
        source_cache_dir=_SOURCES,
        output_dir=second,
        procedural_config=config,
    )

    assert {path.name for path in first.iterdir()} == _OUTPUT_NAMES
    assert {path.name for path in second.iterdir()} == _OUTPUT_NAMES
    for name in _OUTPUT_NAMES:
        assert (first / name).read_bytes() == (second / name).read_bytes()
        _assert_canonical_json(first / name)
    assert first_result.corpus_sha256 == second_result.corpus_sha256
    assert first_result.source_census_sha256 == second_result.source_census_sha256
    assert first_result.item_count == 6
    assert first_result.real_item_count == 3
    assert first_result.procedural_item_count == 3


def test_outputs_round_trip_and_receipt_binds_every_nonreceipt_artifact(
    tmp_path: Path,
) -> None:
    output = tmp_path / "corpus-build"
    result = build_benchmark_corpus(
        census_path=_CENSUS,
        source_cache_dir=_SOURCES,
        output_dir=output,
        procedural_config=ProceduralCorpusConfig(family_count=2),
    )

    corpus_wire = _json(output / "corpus.json")
    items = corpus_from_dict(corpus_wire)
    sheet = _json(output / "datasheet.json")
    census = source_census_from_dict(_json(output / "source-census.json"))
    contamination = _json(output / "contamination.json")
    receipt = _json(output / "receipt.json")

    assert len(items) == 5
    assert tuple(item.position for item in items) == tuple(range(5))
    assert [item.layer for item in items[:2]] == ["procedural", "procedural"]
    assert {item.layer for item in items[2:]} == {"public_midi", "public_classical"}
    assert all(item.provenance is not None for item in items)
    assert corpus_sha256(items) == result.corpus_sha256
    assert sheet["count"] == 5
    assert sheet["by_layer"] == {
        "procedural": 2,
        "public_classical": 1,
        "public_midi": 2,
    }

    assert contamination["schema"] == CONTAMINATION_REPORT_VERSION
    assert contamination["cross_stratum_gate"] == {
        "clean": True,
        "findings": [],
        "violation_count": 0,
    }
    strata = contamination["strata"]
    assert type(strata) is dict
    assert strata["real"] == {
        "clean": True,
        "findings": [],
        "item_count": 3,
        "split_counts": {"test": 3},
        "stratum": "real",
        "violation_count": 0,
    }
    assert strata["procedural"] == {
        "clean": True,
        "findings": [],
        "item_count": 2,
        "split_counts": {"test": 2},
        "stratum": "procedural",
        "violation_count": 0,
    }

    assert receipt["schema"] == CORPUS_BUILD_RECEIPT_VERSION
    assert receipt["status"] == "COMPLETE"
    assert receipt["corpus_sha256"] == result.corpus_sha256
    assert receipt["source_census_sha256"] == source_census_sha256(census)
    assert receipt["item_counts"] == {"procedural": 2, "real": 3, "total": 5}
    artifact_hashes = receipt["artifact_sha256"]
    assert type(artifact_hashes) is dict
    assert artifact_hashes == {
        name: hashlib.sha256((output / name).read_bytes()).hexdigest()
        for name in sorted(_OUTPUT_NAMES - {"receipt.json"})
    }


def test_nonviolating_detection_remains_visible_in_serialized_stratum() -> None:
    finding = ContaminationFinding(
        kind=ContaminationKind.TRANSPOSITION_VARIANT,
        stratum=CorpusStratum.REAL,
        item_ids=("variant-a", "variant-b"),
        family_ids=("shared-family",),
        splits=("test",),
        references=(),
        evidence="fixture-signature",
        is_violation=False,
    )
    report = ContaminationReport(
        real=StratumContaminationReport(
            stratum=CorpusStratum.REAL,
            item_count=2,
            split_counts=(("test", 2),),
            findings=(finding,),
        ),
        procedural=StratumContaminationReport(
            stratum=CorpusStratum.PROCEDURAL,
            item_count=0,
            split_counts=(),
            findings=(),
        ),
        cross_stratum_findings=(),
    )

    wire = _contamination_to_dict(report)
    strata = cast(dict[str, dict[str, object]], wire["strata"])
    real = strata["real"]

    assert real["clean"] is True
    assert real["violation_count"] == 0
    findings = cast(list[dict[str, object]], real["findings"])
    assert findings[0]["is_violation"] is False


def test_public_rows_bind_checked_in_roles_normalizers_and_source_hashes(
    tmp_path: Path,
) -> None:
    output = tmp_path / "public-bindings"
    build_benchmark_corpus(
        census_path=_CENSUS,
        source_cache_dir=_SOURCES,
        output_dir=output,
        procedural_config=ProceduralCorpusConfig(family_count=1),
    )
    items = corpus_from_dict(_json(output / "corpus.json"))
    census = source_census_from_dict(_json(output / "source-census.json"))
    included = {source.item_id: source for source in census.sources if source.item_id is not None}

    for item in items[1:]:
        source = included[item.item_id]
        assert item.provenance is not None
        assert item.provenance.source_sha256 == source.expected_sha256
        assert item.provenance.router_version == BENCHMARK_PUBLIC_ROUTER_VERSION
        assert item.provenance.importer_version == BENCHMARK_PUBLIC_ADAPTER_VERSION
        assert item.provenance.role_map == source.role_map
        assert item.provenance.normalization == source.normalization
        assert item.provenance.source_url == source.source_url
        assert item.provenance.retrieval_date == source.retrieval_date
        assert item.provenance.license.expression == source.license.expression
        assert item.provenance.license.provider_submission is True
        assert item.canary is not None
        assert item.canary.startswith("fretsure-benchmark-v2-canary-public-")


def test_census_cannot_claim_an_unexecuted_normalization_step(tmp_path: Path) -> None:
    census = _json(_CENSUS)
    sources = cast(list[dict[str, object]], census["sources"])
    included = next(source for source in sources if source["status"] == "included")
    normalization = cast(list[str], included["normalization"])
    included["normalization"] = sorted((*normalization, "unexecuted-step"))
    changed_census = tmp_path / "changed-census.json"
    changed_census.write_bytes(canonical_json_bytes(census))
    output = tmp_path / "must-not-exist"

    with pytest.raises(CorpusBuildError, match="executed adapter/container/normalizer"):
        build_benchmark_corpus(
            census_path=changed_census,
            source_cache_dir=_SOURCES,
            output_dir=output,
            procedural_config=ProceduralCorpusConfig(family_count=1),
        )

    assert not output.exists()


def test_existing_output_is_refused_without_touching_it(tmp_path: Path) -> None:
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "owned.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(CorpusBuildError, match="fresh"):
        build_benchmark_corpus(
            census_path=_CENSUS,
            source_cache_dir=_SOURCES,
            output_dir=output,
            procedural_config=ProceduralCorpusConfig(family_count=1),
        )

    assert marker.read_text(encoding="utf-8") == "keep"
    assert {path.name for path in output.iterdir()} == {"owned.txt"}


def test_partial_artifact_write_is_cleaned_for_a_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "partial-output"
    original_write_bytes = Path.write_bytes

    def fail_after_partial_write(path: Path, data: bytes) -> int:
        written = original_write_bytes(path, data[:8])
        if path.name == "corpus.json":
            raise OSError("simulated partial write")
        return written

    monkeypatch.setattr(Path, "write_bytes", fail_after_partial_write)
    with pytest.raises(CorpusBuildError, match="could not write"):
        build_benchmark_corpus(
            census_path=_CENSUS,
            source_cache_dir=_SOURCES,
            output_dir=output,
            procedural_config=ProceduralCorpusConfig(family_count=1),
        )

    assert not output.exists()


def test_default_build_freezes_five_hundred_plus_three_composition(tmp_path: Path) -> None:
    output = tmp_path / "default-corpus"

    result = build_benchmark_corpus(output_dir=output)
    receipt = _json(output / "receipt.json")
    contamination = _json(output / "contamination.json")

    assert result.item_count == 503
    assert result.procedural_item_count == 500
    assert result.real_item_count == 3
    assert receipt["procedural_config"] == {
        "family_count": 500,
        "base_seed": 20_260_717,
        "bars": 4,
        "split": "test",
    }
    assert receipt["item_counts"] == {"procedural": 500, "real": 3, "total": 503}
    assert receipt["contamination_clean"] == {
        "cross_stratum": True,
        "procedural": True,
        "real": True,
    }
    strata = cast(dict[str, dict[str, object]], contamination["strata"])
    assert strata["procedural"]["clean"] is True
    assert strata["real"]["clean"] is True
    assert cast(dict[str, object], contamination["cross_stratum_gate"])["clean"] is True


def test_changed_or_missing_local_pin_fails_before_output(tmp_path: Path) -> None:
    cache = tmp_path / "changed-cache"
    shutil.copytree(_SOURCES, cache)
    changed = cache / "mutopia-bach-bwv774.mid"
    changed.write_bytes(b"changed")
    output = tmp_path / "must-not-exist"

    with pytest.raises(CorpusBuildError, match="expected_sha256"):
        build_benchmark_corpus(
            census_path=_CENSUS,
            source_cache_dir=cache,
            output_dir=output,
            procedural_config=ProceduralCorpusConfig(family_count=1),
        )
    assert not output.exists()

    changed.unlink()
    with pytest.raises(CorpusBuildError, match="could not read"):
        build_benchmark_corpus(
            census_path=_CENSUS,
            source_cache_dir=cache,
            output_dir=output,
            procedural_config=ProceduralCorpusConfig(family_count=1),
        )
    assert not output.exists()


def test_cli_uses_explicit_small_family_count_and_prints_machine_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "cli"

    status = main(
        [
            "--census",
            str(_CENSUS),
            "--source-cache",
            str(_SOURCES),
            "--output-dir",
            str(output),
            "--procedural-family-count",
            "1",
        ]
    )

    assert status == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["item_count"] == 4
    assert summary["real_item_count"] == 3
    assert summary["procedural_item_count"] == 1
    assert summary["output_dir"] == str(output)
    assert {path.name for path in output.iterdir()} == _OUTPUT_NAMES
