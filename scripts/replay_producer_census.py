#!/usr/bin/env python3
"""Replay the frozen producer corpus with whichever Fretsure is on sys.path."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import fretsure
from fretsure.importers import (
    IMPORTER_VERSION,
    ImportDiagnostic,
    ImportFailure,
    ImportSuccess,
    import_musicxml,
)


def _code(value: ImportDiagnostic) -> str:
    return value.code.value


def replay(fixtures: Path) -> dict[str, object]:
    manifest_path = fixtures / "provenance.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    rows = manifest.get("fixtures")
    if not isinstance(rows, list):
        raise ValueError("producer manifest fixtures must be a list")

    outcomes: list[dict[str, object]] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("output_file"), str):
            raise ValueError("producer manifest row lacks output_file")
        filename = row["output_file"]
        result = import_musicxml(fixtures / filename)
        if isinstance(result, ImportSuccess):
            outcomes.append(
                {
                    "file": filename,
                    "key": result.ir.meta.key,
                    "outcome": "success",
                    "warnings": [_code(item) for item in result.warnings],
                }
            )
        elif isinstance(result, ImportFailure):
            outcomes.append(
                {
                    "diagnostics": [_code(item) for item in result.diagnostics],
                    "file": filename,
                    "outcome": "failure",
                }
            )
        else:
            raise TypeError(f"unexpected importer result for {filename}: {type(result)!r}")

    successes = sum(row["outcome"] == "success" for row in outcomes)
    return {
        "artifact_count": len(outcomes),
        "failures": len(outcomes) - successes,
        "fixtures": outcomes,
        "importer_version": IMPORTER_VERSION,
        "manifest_schema": manifest.get("schema"),
        "package_version": fretsure.__version__,
        "successes": successes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path("tests/fixtures/producers"),
        help="directory containing provenance.json and the frozen producer artifacts",
    )
    args = parser.parse_args()
    fixtures = args.fixtures.expanduser().resolve()
    print(json.dumps(replay(fixtures), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
