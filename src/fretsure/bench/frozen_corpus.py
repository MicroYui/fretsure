"""Rebuild the frozen benchmark-v2 corpus from packaged, pinned sources.

The 503-item corpus is 500 procedurally generated families plus three licensed
public controls.  Nothing about it is stored as a snapshot: the census, the
three source files and the generator are the corpus, and rebuilding them takes
about three seconds — less than parsing a serialized copy would.  The rebuild
ends by comparing the result against one frozen identity digest, so a changed
generator, census or source file fails closed instead of silently producing a
different benchmark.
"""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files
from pathlib import Path
from typing import NoReturn, cast

from fretsure.bench.corpus import (
    CorpusItem,
    CorpusProvenance,
    EvidenceAvailability,
    LicenseProvenance,
    ProceduralCorpusConfig,
    build_primary_procedural_corpus,
    corpus_sha256,
    snapshot_corpus,
)
from fretsure.bench.corpus_sources import (
    PublicSource,
    SourceCensus,
    SourceStatus,
    source_census_from_dict,
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
from fretsure.importers.contracts import DEFAULT_LIMITS, ImportFailure
from fretsure.ir import MusicIR

FROZEN_CORPUS_SHA256 = "b4e2a1ed05eb07d82bdea18b9105cdd92b564cf864d8acedaa3c37d820848e8b"
FROZEN_CORPUS_ITEM_COUNT = 503
FROZEN_PRIMARY_FAMILY_COUNT = 500


class FrozenCorpusError(ValueError):
    """A pinned source, the census, or the rebuilt corpus failed its contract."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid frozen benchmark corpus {field}: {detail}")


def _fail(field: str, detail: str) -> NoReturn:
    raise FrozenCorpusError(field, detail)


def read_source_census(data: bytes) -> SourceCensus:
    """Parse one census from exact bytes."""

    if type(data) is not bytes:
        _fail("census", "must be exact bytes")
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("census", "must contain UTF-8 JSON")
    try:
        return source_census_from_dict(parsed)
    except ValueError as error:
        _fail("census", str(error))


def read_pinned_sources(
    census: SourceCensus,
    payloads_by_name: dict[str, bytes],
) -> tuple[dict[str, bytes], dict[str, str]]:
    """Verify every included source against its pinned size and digest."""

    payloads: dict[str, bytes] = {}
    hashes: dict[str, str] = {}
    observed_total = 0
    for source in census.sources:
        if source.status is not SourceStatus.INCLUDED:
            continue
        assert source.cache_name is not None
        assert source.expected_sha256 is not None
        data = payloads_by_name.get(source.cache_name)
        if data is None:
            _fail(f"sources.{source.source_id}", "has no pinned cache file")
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


def root_sha256(source_format: str, data: bytes, source_id: str) -> str:
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


def public_item(source: PublicSource, data: bytes, *, position: int) -> CorpusItem:
    """Normalize one pinned public source into its frozen corpus item."""

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
            root_sha256=root_sha256(source_format, data, source.source_id),
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


def build_frozen_corpus(
    census: SourceCensus,
    payloads: dict[str, bytes],
    *,
    procedural_config: ProceduralCorpusConfig | None = None,
) -> tuple[CorpusItem, ...]:
    """Combine the procedural stratum with the verified public controls."""

    config = ProceduralCorpusConfig() if procedural_config is None else procedural_config
    procedural = build_primary_procedural_corpus(config)
    public = tuple(
        public_item(source, payloads[source.source_id], position=len(procedural) + index)
        for index, source in enumerate(
            source for source in census.sources if source.status is SourceStatus.INCLUDED
        )
    )
    return snapshot_corpus(procedural + public)


def _packaged_bytes(*parts: str) -> bytes:
    resource = files("fretsure.bench").joinpath("data")
    for part in parts:
        resource = resource.joinpath(part)
    return resource.read_bytes()


def load_frozen_benchmark_corpus(
    *,
    census_path: Path | None = None,
    source_cache_dir: Path | None = None,
) -> tuple[CorpusItem, ...]:
    """Return the frozen 503-item corpus, verified against its identity digest.

    Without arguments this reads the census and the three pinned sources that
    ship inside the wheel, so a clean install can rebuild the benchmark corpus
    with no repository checkout.
    """

    if census_path is None:
        census = read_source_census(_packaged_bytes("source-census.json"))
    else:
        census = read_source_census(census_path.read_bytes())
    names = tuple(
        source.cache_name
        for source in census.sources
        if source.status is SourceStatus.INCLUDED and source.cache_name is not None
    )
    if source_cache_dir is None:
        payloads_by_name = {name: _packaged_bytes("sources", name) for name in names}
    else:
        payloads_by_name = {}
        for name in names:
            try:
                payloads_by_name[name] = (source_cache_dir / name).read_bytes()
            except OSError:
                _fail(f"sources.{name}", "could not read the pinned cache file")
    payloads, _hashes = read_pinned_sources(census, payloads_by_name)
    items = build_frozen_corpus(census, payloads)
    if len(items) != FROZEN_CORPUS_ITEM_COUNT:
        _fail("corpus", f"must contain exactly {FROZEN_CORPUS_ITEM_COUNT} items")
    if corpus_sha256(items) != FROZEN_CORPUS_SHA256:
        _fail("corpus", "rebuilt corpus differs from the frozen identity digest")
    return items


__all__ = [
    "FROZEN_CORPUS_ITEM_COUNT",
    "FROZEN_CORPUS_SHA256",
    "FROZEN_PRIMARY_FAMILY_COUNT",
    "FrozenCorpusError",
    "build_frozen_corpus",
    "load_frozen_benchmark_corpus",
    "public_item",
    "read_pinned_sources",
    "read_source_census",
    "root_sha256",
]
