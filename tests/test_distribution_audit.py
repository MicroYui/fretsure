from __future__ import annotations

import io
import runpy
import tarfile
import tomllib
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_PROJECT = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
_VERSION = cast(str, _PROJECT["project"]["version"])

_AUDIT_NAMESPACE = runpy.run_path(
    "scripts/audit_distributions.py",
    run_name="fretsure_distribution_audit_test",
)
_audit_sdist = cast(Callable[[Path], int], _AUDIT_NAMESPACE["_audit_sdist"])
_audit_wheel = cast(Callable[..., int], _AUDIT_NAMESPACE["_audit_wheel"])
_licensed_source_files = cast(
    Callable[[], dict[str, Path]], _AUDIT_NAMESPACE["_licensed_source_files"]
)
_validate_project_metadata = cast(
    Callable[[object], str], _AUDIT_NAMESPACE["_validate_project_metadata"]
)
_workspace_runtime_files = cast(
    Callable[[], dict[str, Path]], _AUDIT_NAMESPACE["_workspace_runtime_files"]
)
_BENCHMARK_WHEEL_REQUIREMENTS = cast(
    frozenset[str], _AUDIT_NAMESPACE["BENCHMARK_WHEEL_REQUIREMENTS"]
)
_SDIST_EXACT_FILES = cast(tuple[str, ...], _AUDIT_NAMESPACE["SDIST_EXACT_FILES"])
_SDIST_REQUIRED_FILES = cast(tuple[str, ...], _AUDIT_NAMESPACE["SDIST_REQUIRED_FILES"])


def _wheel_metadata(*, omitted_requirement: str | None = None, version: str = _VERSION) -> str:
    lines = [
        "Metadata-Version: 2.4",
        "Name: fretsure-oracle",
        f"Version: {version}",
        "Provides-Extra: benchmark",
    ]
    lines.extend(
        f"Requires-Dist: {requirement}"
        for requirement in sorted(_BENCHMARK_WHEEL_REQUIREMENTS)
        if requirement != omitted_requirement
    )
    return "\n".join((*lines, "", ""))


def _write_test_wheel(
    path: Path,
    *,
    omitted: str | None = None,
    metadata: str | None = None,
    extra_name: str | None = None,
    changed_name: str | None = None,
) -> None:
    with zipfile.ZipFile(path, mode="w") as archive:
        for name, source in _workspace_runtime_files().items():
            if name == omitted:
                continue
            data = source.read_bytes()
            archive.writestr(name, b"changed" if name == changed_name else data)
        if extra_name is not None:
            archive.writestr(extra_name, b"not release evidence")
        archive.writestr(
            f"fretsure_oracle-{_VERSION}.dist-info/METADATA",
            _wheel_metadata() if metadata is None else metadata,
        )


def _write_test_sdist(
    path: Path,
    *,
    omitted: str | None = None,
    extra_name: str | None = None,
) -> None:
    licensed = tuple(f"data/benchmark/sources/{name}" for name in _licensed_source_files())
    relatives = tuple(dict.fromkeys((*_SDIST_REQUIRED_FILES, *_SDIST_EXACT_FILES, *licensed)))
    prefix = f"fretsure_oracle-{_VERSION}"
    with tarfile.open(path, mode="w:gz") as archive:
        for relative in relatives:
            if relative != omitted:
                archive.add(_ROOT / relative, arcname=f"{prefix}/{relative}", recursive=False)
        if extra_name is not None:
            info = tarfile.TarInfo(f"{prefix}/{extra_name}")
            data = b"not release evidence"
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def test_task7_project_version_and_benchmark_extra_are_frozen() -> None:
    assert _VERSION == "0.6.0"
    assert _validate_project_metadata(_PROJECT) == _VERSION
    assert _PROJECT["project"]["optional-dependencies"]["benchmark"] == [
        "anthropic>=0.40",
        "httpx>=0.28,<0.29",
        "defusedxml>=0.7.1,<1",
        "music21==10.5.0",
    ]


def test_wheel_audit_requires_exact_runtime_set_bytes_and_metadata(tmp_path: Path) -> None:
    complete = tmp_path / f"fretsure_oracle-{_VERSION}-py3-none-any.whl"
    _write_test_wheel(complete)
    assert _audit_wheel(complete, expected_version=_VERSION) > 0

    incomplete = tmp_path / "missing-runtime.whl"
    _write_test_wheel(incomplete, omitted="fretsure/importers/score.py")
    with pytest.raises(ValueError, match="runtime entry set differs"):
        _audit_wheel(incomplete, expected_version=_VERSION)

    changed = tmp_path / "changed-evidence.whl"
    _write_test_wheel(changed, changed_name="fretsure/bench/data/source-census.json")
    with pytest.raises(ValueError, match="runtime bytes differ"):
        _audit_wheel(changed, expected_version=_VERSION)


def test_wheel_audit_rejects_incomplete_benchmark_extra(tmp_path: Path) -> None:
    wheel = tmp_path / "bad-extra.whl"
    omitted = next(iter(_BENCHMARK_WHEEL_REQUIREMENTS))
    _write_test_wheel(wheel, metadata=_wheel_metadata(omitted_requirement=omitted))

    with pytest.raises(ValueError, match="benchmark extra metadata"):
        _audit_wheel(wheel, expected_version=_VERSION)


@pytest.mark.parametrize(
    "filename",
    (
        "private-observations.json",
        "journal.jsonl",
        "wal.jsonl",
        "rows.jsonl",
        "blobs.jsonl",
    ),
)
def test_wheel_audit_rejects_private_or_formal_run_artifacts(
    tmp_path: Path, filename: str
) -> None:
    wheel = tmp_path / f"forbidden-{filename}.whl"
    _write_test_wheel(wheel, extra_name=f"fretsure/bench/data/{filename}")

    with pytest.raises(ValueError, match="forbidden distribution entry"):
        _audit_wheel(wheel, expected_version=_VERSION)


def test_sdist_audit_requires_task7_task8_and_task9_evidence_and_exact_sources(
    tmp_path: Path,
) -> None:
    complete = tmp_path / f"fretsure_oracle-{_VERSION}.tar.gz"
    _write_test_sdist(complete)
    assert _audit_sdist(complete) > 0

    missing_prereg = tmp_path / "missing-prereg.tar.gz"
    _write_test_sdist(
        missing_prereg,
        omitted="docs/experiments/2026-07-17-benchmark-v2-prereg.json",
    )
    with pytest.raises(ValueError, match="benchmark-v2-prereg"):
        _audit_sdist(missing_prereg)

    missing_pilot = tmp_path / "missing-task8-pilot.tar.gz"
    _write_test_sdist(
        missing_pilot,
        omitted="docs/experiments/2026-07-18-benchmark-v2-pilot-spec.json",
    )
    with pytest.raises(ValueError, match="benchmark-v2-pilot-spec"):
        _audit_sdist(missing_pilot)

    for label, relative in (
        (
            "pricing-source",
            "docs/experiments/2026-07-18-gpt-5.6-sol-pricing-source.json",
        ),
        (
            "pricing-contract",
            "docs/experiments/2026-07-18-gpt-5.6-sol-pricing-contract.json",
        ),
        (
            "formal-billing-envelope",
            (
                "docs/experiments/"
                "2026-07-18-gpt-5.6-sol-formal-billing-envelope.json"
            ),
        ),
    ):
        missing_price = tmp_path / f"missing-task8-{label}.tar.gz"
        _write_test_sdist(missing_price, omitted=relative)
        with pytest.raises(ValueError, match=label):
            _audit_sdist(missing_price)

    missing_budget_gate = tmp_path / "missing-task8-budget-gate.tar.gz"
    _write_test_sdist(
        missing_budget_gate,
        omitted="scripts/task8_budget_gate.py",
    )
    with pytest.raises(ValueError, match="task8_budget_gate"):
        _audit_sdist(missing_budget_gate)

    missing_pre_call_builder = tmp_path / "missing-task9-precall-builder.tar.gz"
    _write_test_sdist(
        missing_pre_call_builder,
        omitted="scripts/build_benchmark_precall.py",
    )
    with pytest.raises(ValueError, match="build_benchmark_precall"):
        _audit_sdist(missing_pre_call_builder)

    source_name = next(iter(_licensed_source_files()))
    missing_source = tmp_path / "missing-source.tar.gz"
    _write_test_sdist(missing_source, omitted=f"data/benchmark/sources/{source_name}")
    with pytest.raises(ValueError, match="expected one source entry"):
        _audit_sdist(missing_source)

    extra_source = tmp_path / "extra-source.tar.gz"
    _write_test_sdist(
        extra_source,
        extra_name="data/benchmark/sources/not-in-license-census.mid",
    )
    with pytest.raises(ValueError, match="licensed census"):
        _audit_sdist(extra_source)


@pytest.mark.parametrize(
    "filename",
    (
        "private-observations.json",
        "journal.jsonl",
        "wal.jsonl",
        "rows.jsonl",
        "blobs.jsonl",
    ),
)
def test_sdist_audit_rejects_private_or_formal_run_artifacts(
    tmp_path: Path, filename: str
) -> None:
    sdist = tmp_path / f"forbidden-{filename}.tar.gz"
    _write_test_sdist(sdist, extra_name=f"published-run/{filename}")

    with pytest.raises(ValueError, match="forbidden distribution entry"):
        _audit_sdist(sdist)
