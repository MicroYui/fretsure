#!/usr/bin/env python3
"""Generate or verify the frozen benchmark-v2 preregistration artifacts."""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Sequence
from pathlib import Path

from build_benchmark_corpus import (
    DEFAULT_CENSUS,
    DEFAULT_SOURCE_CACHE,
    _public_item,
    _read_census,
    _read_pinned_sources,
)

from fretsure.bench.contracts import canonical_json_bytes
from fretsure.bench.corpus import (
    ProceduralCorpusConfig,
    build_primary_procedural_corpus,
    corpus_to_dict,
    datasheet,
    snapshot_corpus,
)
from fretsure.bench.corpus_sources import (
    SourceStatus,
    source_census_sha256,
    source_census_to_dict,
)
from fretsure.bench.preregistration import (
    TASK5_CORPUS_FILE_SHA256,
    TASK5_DATASHEET_FILE_SHA256,
    TASK5_SOURCE_CENSUS_FILE_SHA256,
    TASK5_SOURCE_CENSUS_SHA256,
    BenchmarkPreregistration,
    budget_markdown,
    build_legacy_preregistration,
    build_preregistration,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREREG = ROOT / "docs" / "experiments" / "2026-07-17-benchmark-v2-prereg.json"
DEFAULT_BUDGET = ROOT / "docs" / "experiments" / "2026-07-17-benchmark-v2-budget.md"
DEFAULT_OPERATIONAL_PREREG = (
    ROOT / "docs" / "experiments" / "2026-07-18-benchmark-v2-operational-prereg.json"
)
DEFAULT_OPERATIONAL_BUDGET = (
    ROOT / "docs" / "experiments" / "2026-07-18-benchmark-v2-operational-budget.md"
)
def _operational_budget_markdown(preregistration: BenchmarkPreregistration) -> str:
    """Render the operational budget from its single library source of truth."""
    return budget_markdown(preregistration)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _artifacts(
    census_path: Path,
    source_cache: Path,
) -> tuple[bytes, bytes, bytes, bytes]:
    census = _read_census(census_path)
    if source_census_sha256(census) != TASK5_SOURCE_CENSUS_SHA256:
        raise ValueError("source census domain digest differs from the Task 5 receipt")
    census_bytes = canonical_json_bytes(source_census_to_dict(census))
    if _sha256(census_bytes) != TASK5_SOURCE_CENSUS_FILE_SHA256:
        raise ValueError("canonical source census bytes differ from the Task 5 receipt")
    payloads, _source_hashes = _read_pinned_sources(census, source_cache)
    procedural = build_primary_procedural_corpus(ProceduralCorpusConfig())
    public = tuple(
        _public_item(
            source,
            payloads[source.source_id],
            position=len(procedural) + index,
        )
        for index, source in enumerate(
            source for source in census.sources if source.status is SourceStatus.INCLUDED
        )
    )
    items = snapshot_corpus(procedural + public)
    if _sha256(canonical_json_bytes(corpus_to_dict(items))) != TASK5_CORPUS_FILE_SHA256:
        raise ValueError("canonical corpus bytes differ from the Task 5 receipt")
    if _sha256(canonical_json_bytes(datasheet(items))) != TASK5_DATASHEET_FILE_SHA256:
        raise ValueError("canonical datasheet bytes differ from the Task 5 receipt")
    legacy = build_legacy_preregistration(items)
    operational = build_preregistration(items)
    return (
        legacy.wire_json,
        budget_markdown(legacy).encode("utf-8"),
        operational.wire_json,
        _operational_budget_markdown(operational).encode("utf-8"),
    )


def _check(path: Path, expected: bytes) -> None:
    try:
        observed = path.read_bytes()
    except OSError as error:
        raise ValueError(f"required generated artifact is unreadable: {path}") from error
    if observed != expected:
        raise ValueError(f"generated artifact differs byte-for-byte: {path}")


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--census", type=Path, default=DEFAULT_CENSUS)
    parser.add_argument("--source-cache", type=Path, default=DEFAULT_SOURCE_CACHE)
    parser.add_argument("--output-prereg", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--output-budget", type=Path, default=DEFAULT_BUDGET)
    parser.add_argument(
        "--output-operational-prereg",
        type=Path,
        default=DEFAULT_OPERATIONAL_PREREG,
    )
    parser.add_argument(
        "--output-operational-budget",
        type=Path,
        default=DEFAULT_OPERATIONAL_BUDGET,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        legacy_preregistration, legacy_budget, preregistration, budget = _artifacts(
            args.census,
            args.source_cache,
        )
        if args.check:
            _check(args.output_prereg, legacy_preregistration)
            _check(args.output_budget, legacy_budget)
            _check(args.output_operational_prereg, preregistration)
            _check(args.output_operational_budget, budget)
        else:
            _write(args.output_prereg, legacy_preregistration)
            _write(args.output_budget, legacy_budget)
            _write(args.output_operational_prereg, preregistration)
            _write(args.output_operational_budget, budget)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(
        "benchmark preregistration OK "
        f"(legacy_json_sha256={_sha256(legacy_preregistration)}, "
        f"legacy_budget_sha256={_sha256(legacy_budget)}, "
        f"operational_json_sha256={_sha256(preregistration)}, "
        f"operational_budget_sha256={_sha256(budget)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
