#!/usr/bin/env python3
"""Build the benchmark-v2 corpus from the checked-in pinned source cache.

This command is deliberately offline: it reads one source census and the exact
cache files named by that census, combines normalized public items with the
procedural headline stratum, audits contamination, and writes one fresh output
directory of canonical JSON artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

from fretsure.bench.contamination import (
    NEAR_DUPLICATE_SIMILARITY,
    CanaryDocument,
    ContaminationFinding,
    ContaminationReport,
    StratumContaminationReport,
    audit_contamination,
)
from fretsure.bench.contracts import canonical_json_bytes
from fretsure.bench.corpus import (
    CorpusItem,
    CorpusProvenance,
    EvidenceAvailability,
    LicenseProvenance,
    ProceduralCorpusConfig,
    build_primary_procedural_corpus,
    corpus_from_dict,
    corpus_sha256,
    corpus_to_dict,
    datasheet,
    snapshot_corpus,
)
from fretsure.bench.corpus_sources import (
    PublicSource,
    SourceCensus,
    SourceStatus,
    source_census_from_dict,
    source_census_sha256,
    source_census_to_dict,
)
from fretsure.bench.normalizers import (
    ArrangementSourceFormat,
    PublicArrangementLayer,
    normalize_arrangement_source,
)
from fretsure.bench.public_adapters import (
    BENCHMARK_PUBLIC_ADAPTER_VERSION,
    BENCHMARK_PUBLIC_ROUTER_VERSION,
    PUBLIC_MIDI_ADAPTER_NORMALIZATION,
    PUBLIC_MUSICXML_ADAPTER_NORMALIZATION,
    arrangement_source_from_pinned_bytes,
)
from fretsure.importers._mxl_container import (
    MXL_CONTAINER_VERSION,
    MXLContainerPayload,
    read_mxl_container,
)
from fretsure.importers.contracts import (
    DEFAULT_LIMITS,
    ImportFailure,
)
from fretsure.ir import MusicIR

CORPUS_BUILD_RECEIPT_VERSION = "benchmark-corpus-build-receipt@0.1.0"
CONTAMINATION_REPORT_VERSION = "benchmark-contamination@0.1.0"
_OUTPUT_NAMES = (
    "contamination.json",
    "corpus.json",
    "datasheet.json",
    "source-census.json",
    "receipt.json",
)
_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CENSUS = _ROOT / "data" / "benchmark" / "source-census.json"
DEFAULT_SOURCE_CACHE = _ROOT / "data" / "benchmark" / "sources"


class CorpusBuildError(ValueError):
    """One local corpus-build input or output failed its frozen contract."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid benchmark corpus build {field}: {detail}")


@dataclass(frozen=True, slots=True)
class CorpusBuildResult:
    output_dir: Path
    corpus_sha256: str
    source_census_sha256: str
    item_count: int
    real_item_count: int
    procedural_item_count: int


def _fail(field: str, detail: str) -> NoReturn:
    raise CorpusBuildError(field, detail)


def _read_json(path: Path, field: str) -> object:
    try:
        data = path.read_bytes()
    except OSError:
        _fail(field, "could not read the local JSON file")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail(field, "must contain UTF-8 JSON")


def _read_census(path: Path) -> SourceCensus:
    try:
        return source_census_from_dict(_read_json(path, "census"))
    except CorpusBuildError:
        raise
    except ValueError as error:
        _fail("census", str(error))


def _read_pinned_sources(
    census: SourceCensus,
    cache_dir: Path,
) -> tuple[dict[str, bytes], dict[str, str]]:
    payloads: dict[str, bytes] = {}
    hashes: dict[str, str] = {}
    observed_total = 0
    for source in census.sources:
        if source.status is not SourceStatus.INCLUDED:
            continue
        assert source.cache_name is not None
        assert source.expected_sha256 is not None
        path = cache_dir / source.cache_name
        try:
            data = path.read_bytes()
        except OSError:
            _fail(f"sources.{source.source_id}", "could not read the pinned cache file")
        if not data or len(data) > source.max_bytes:
            _fail(f"sources.{source.source_id}", "is empty or exceeds its byte ceiling")
        observed_total += len(data)
        if observed_total > census.max_total_bytes:
            _fail("sources", "total pinned cache bytes exceed the census ceiling")
        digest = hashlib.sha256(data).hexdigest()
        if digest != source.expected_sha256:
            _fail(f"sources.{source.source_id}", "does not match expected_sha256")
        payloads[source.source_id] = data
        hashes[source.source_id] = digest
    return payloads, dict(sorted(hashes.items()))


def _root_sha256(source_format: str, data: bytes, source_id: str) -> str:
    if source_format != "mxl":
        return hashlib.sha256(data).hexdigest()
    payload = read_mxl_container(data, DEFAULT_LIMITS)
    if isinstance(payload, ImportFailure):
        _fail(f"sources.{source_id}", "MXL container could not expose its verified root")
    assert isinstance(payload, MXLContainerPayload)
    return hashlib.sha256(payload.root_bytes).hexdigest()


def _evidence(ir: MusicIR) -> EvidenceAvailability:
    return EvidenceAvailability(
        melody=any(note.voice == "melody" for note in ir.notes),
        bass=bool(ir.chords),
        harmony=bool(ir.chords) or any(note.voice in {"bass", "harmony"} for note in ir.notes),
    )


def _polyphony(ir: MusicIR) -> str:
    events = sorted(
        (
            (time, delta)
            for note in ir.notes
            for time, delta in ((note.onset, 1), (note.onset + note.duration, -1))
        ),
        key=lambda event: (event[0], event[1]),
    )
    sounding = 0
    maximum = 0
    for _time, delta in events:
        sounding += delta
        maximum = max(maximum, sounding)
    return "monophonic" if maximum <= 1 else "polyphonic"


def _normalizer_versions(source: PublicSource) -> tuple[str, str | None]:
    return (
        BENCHMARK_PUBLIC_ADAPTER_VERSION,
        MXL_CONTAINER_VERSION if source.source_format == "mxl" else None,
    )


def _public_item(
    source: PublicSource,
    data: bytes,
    *,
    position: int,
) -> CorpusItem:
    required = (
        source.source_format,
        source.source_url,
        source.expected_sha256,
        source.item_id,
        source.family_id,
        source.cluster_id,
        source.genre,
        source.split,
        source.retrieval_date,
    )
    if source.status is not SourceStatus.INCLUDED or any(value is None for value in required):
        _fail(f"sources.{source.source_id}", "is not a complete included source")
    source_format = cast(ArrangementSourceFormat, source.source_format)
    layer = cast(PublicArrangementLayer, source.layer)
    adapter_source = arrangement_source_from_pinned_bytes(
        data,
        source_format=source_format,
        source_identity=source.source_id,
        license_expression=source.license.expression,
    )
    normalized = normalize_arrangement_source(
        adapter_source,
        source.role_map,
        layer=layer,
    )
    if normalized.role_map != source.role_map:
        _fail(f"sources.{source.source_id}.role_map", "normalizer changed the pinned map")
    adapter_step = (
        PUBLIC_MIDI_ADAPTER_NORMALIZATION
        if source_format == "midi"
        else PUBLIC_MUSICXML_ADAPTER_NORMALIZATION
    )
    container_steps = (f"{MXL_CONTAINER_VERSION}-verified-root",) if source_format == "mxl" else ()
    expected_normalization = tuple(
        sorted((*normalized.normalization, adapter_step, *container_steps))
    )
    if source.normalization != expected_normalization:
        _fail(
            f"sources.{source.source_id}.normalization",
            "does not equal the executed adapter/container/normalizer pipeline",
        )
    importer_version, container_version = _normalizer_versions(source)
    canary_material = f"{source.source_id}\0{source.expected_sha256}\0{source.item_id}".encode(
        "ascii"
    )
    canary_suffix = hashlib.sha256(canary_material).hexdigest()[:24]
    return CorpusItem(
        ir=normalized.ir,
        layer=source.layer,
        genre=cast(str, source.genre),
        difficulty=0,
        item_id=cast(str, source.item_id),
        family_id=cast(str, source.family_id),
        cluster_id=cast(str, source.cluster_id),
        position=position,
        provenance=CorpusProvenance(
            source_format=source_format,
            source_sha256=cast(str, source.expected_sha256),
            root_sha256=_root_sha256(source_format, data, source.source_id),
            router_version=BENCHMARK_PUBLIC_ROUTER_VERSION,
            importer_version=importer_version,
            container_version=container_version,
            source_url=cast(str, source.source_url),
            producer=None,
            retrieval_date=cast(str, source.retrieval_date),
            license=LicenseProvenance(
                expression=source.license.expression,
                status="verified",
                redistribution=source.license.redistribution,
                derivatives=source.license.derivatives,
                provider_submission=source.license.provider_submission,
            ),
            split=cast(str, source.split),
            role_map=normalized.role_map,
            normalization=source.normalization,
            generator=None,
        ),
        evidence=_evidence(normalized.ir),
        synthetic_complexity="unrated",
        polyphony=_polyphony(normalized.ir),
        canary=f"fretsure-benchmark-v2-canary-public-{canary_suffix}",
    )


def _finding_to_dict(finding: ContaminationFinding) -> dict[str, object]:
    return {
        "kind": finding.kind.value,
        "stratum": finding.stratum.value,
        "item_ids": list(finding.item_ids),
        "family_ids": list(finding.family_ids),
        "splits": list(finding.splits),
        "references": list(finding.references),
        "evidence": finding.evidence,
        "is_violation": finding.is_violation,
    }


def _stratum_to_dict(report: StratumContaminationReport) -> dict[str, object]:
    return {
        "stratum": report.stratum.value,
        "item_count": report.item_count,
        "split_counts": {name: count for name, count in report.split_counts},
        "clean": report.clean,
        "violation_count": len(report.violations),
        "findings": [_finding_to_dict(finding) for finding in report.findings],
    }


def _contamination_to_dict(report: ContaminationReport) -> dict[str, object]:
    return {
        "schema": CONTAMINATION_REPORT_VERSION,
        "near_duplicate_similarity": NEAR_DUPLICATE_SIMILARITY,
        "cross_stratum_gate": {
            "clean": report.cross_stratum_clean,
            "violation_count": len(report.cross_stratum_findings),
            "findings": [_finding_to_dict(finding) for finding in report.cross_stratum_findings],
        },
        "strata": {
            "real": _stratum_to_dict(report.real),
            "procedural": _stratum_to_dict(report.procedural),
        },
    }


def _artifact_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_fresh(output_dir: Path, artifacts: dict[str, bytes]) -> None:
    try:
        output_dir.mkdir(mode=0o755, parents=False, exist_ok=False)
    except FileExistsError:
        _fail("output_dir", "must be fresh")
    except OSError:
        _fail("output_dir", "could not create the output directory")
    written: list[Path] = []
    try:
        for name in _OUTPUT_NAMES:
            destination = output_dir / name
            written.append(destination)
            destination.write_bytes(artifacts[name])
    except BaseException as error:
        for path in reversed(written):
            try:
                path.unlink()
            except OSError:
                pass
        try:
            output_dir.rmdir()
        except OSError:
            pass
        if isinstance(error, OSError):
            _fail("output_dir", "could not write the canonical artifacts")
        raise


def build_benchmark_corpus(
    *,
    census_path: Path = DEFAULT_CENSUS,
    source_cache_dir: Path = DEFAULT_SOURCE_CACHE,
    output_dir: Path,
    procedural_config: ProceduralCorpusConfig | None = None,
) -> CorpusBuildResult:
    """Build and publish one deterministic corpus directory from local inputs only."""

    if not all(isinstance(path, Path) for path in (census_path, source_cache_dir, output_dir)):
        _fail("paths", "must use pathlib.Path")
    if output_dir.exists():
        _fail("output_dir", "must be fresh")
    config = ProceduralCorpusConfig() if procedural_config is None else procedural_config

    try:
        census = _read_census(census_path)
        payloads, source_hashes = _read_pinned_sources(census, source_cache_dir)
        procedural = build_primary_procedural_corpus(config)
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
        canary_documents = (
            CanaryDocument(
                "source-census",
                canonical_json_bytes(source_census_to_dict(census)).decode("utf-8"),
            ),
            *(
                CanaryDocument(
                    f"source:{hashlib.sha256(source_id.encode('ascii')).hexdigest()[:24]}",
                    data.decode("latin-1"),
                )
                for source_id, data in sorted(payloads.items())
            ),
        )
        contamination = audit_contamination(
            items,
            canary_documents=canary_documents,
        )
        if not contamination.clean:
            _fail("contamination", "one or more strata failed the clean gate")

        census_wire = source_census_to_dict(census)
        corpus_wire = corpus_to_dict(items)
        if corpus_from_dict(corpus_wire) != items:
            _fail("corpus", "canonical corpus round-trip changed an item")
        artifact_bytes: dict[str, bytes] = {
            "source-census.json": canonical_json_bytes(census_wire),
            "corpus.json": canonical_json_bytes(corpus_wire),
            "datasheet.json": canonical_json_bytes(datasheet(items)),
            "contamination.json": canonical_json_bytes(_contamination_to_dict(contamination)),
        }
        corpus_digest = corpus_sha256(items)
        census_digest = source_census_sha256(census)
        receipt: dict[str, Any] = {
            "schema": CORPUS_BUILD_RECEIPT_VERSION,
            "status": "COMPLETE",
            "corpus_sha256": corpus_digest,
            "source_census_sha256": census_digest,
            "procedural_config": {
                "family_count": config.family_count,
                "base_seed": config.base_seed,
                "bars": config.bars,
                "split": config.split,
            },
            "item_counts": {
                "total": len(items),
                "real": contamination.real.item_count,
                "procedural": contamination.procedural.item_count,
            },
            "contamination_clean": {
                "cross_stratum": contamination.cross_stratum_clean,
                "real": contamination.real.clean,
                "procedural": contamination.procedural.clean,
            },
            "included_source_sha256": source_hashes,
            "artifact_sha256": {
                name: _artifact_sha256(data) for name, data in sorted(artifact_bytes.items())
            },
        }
        artifact_bytes["receipt.json"] = canonical_json_bytes(receipt)
    except CorpusBuildError:
        raise
    except (OSError, ValueError) as error:
        _fail("build", str(error))

    _write_fresh(output_dir, artifact_bytes)
    return CorpusBuildResult(
        output_dir=output_dir,
        corpus_sha256=corpus_digest,
        source_census_sha256=census_digest,
        item_count=len(items),
        real_item_count=contamination.real.item_count,
        procedural_item_count=contamination.procedural.item_count,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--census", type=Path, default=DEFAULT_CENSUS)
    parser.add_argument("--source-cache", type=Path, default=DEFAULT_SOURCE_CACHE)
    parser.add_argument(
        "--procedural-family-count",
        type=int,
        default=ProceduralCorpusConfig().family_count,
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = build_benchmark_corpus(
            census_path=args.census,
            source_cache_dir=args.source_cache,
            output_dir=args.output_dir,
            procedural_config=ProceduralCorpusConfig(
                family_count=args.procedural_family_count,
            ),
        )
    except (CorpusBuildError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "corpus_sha256": result.corpus_sha256,
                "item_count": result.item_count,
                "output_dir": str(result.output_dir),
                "procedural_item_count": result.procedural_item_count,
                "real_item_count": result.real_item_count,
                "source_census_sha256": result.source_census_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
