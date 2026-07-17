from __future__ import annotations

import runpy
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

_AUDIT_NAMESPACE = runpy.run_path(
    "scripts/audit_distributions.py",
    run_name="fretsure_distribution_audit_test",
)
_audit_wheel = cast(Callable[..., int], _AUDIT_NAMESPACE["_audit_wheel"])
_workspace_runtime_files = cast(
    Callable[[], dict[str, Path]],
    _AUDIT_NAMESPACE["_workspace_runtime_files"],
)


def _write_test_wheel(path: Path, *, omitted: str | None = None) -> None:
    with zipfile.ZipFile(path, mode="w") as archive:
        for name, source in _workspace_runtime_files().items():
            if name != omitted:
                archive.write(source, name)
        archive.writestr(
            "fretsure_oracle-0.5.0.dist-info/METADATA",
            "Name: fretsure-oracle\nVersion: 0.5.0\n",
        )


def test_wheel_audit_requires_exact_runtime_set_and_bytes(tmp_path: Path) -> None:
    complete = tmp_path / "fretsure_oracle-0.5.0-py3-none-any.whl"
    _write_test_wheel(complete)
    assert _audit_wheel(complete, expected_version="0.5.0") > 0

    incomplete = tmp_path / "missing-runtime.whl"
    _write_test_wheel(incomplete, omitted="fretsure/importers/score.py")
    with pytest.raises(ValueError, match="runtime entry set differs"):
        _audit_wheel(incomplete, expected_version="0.5.0")
