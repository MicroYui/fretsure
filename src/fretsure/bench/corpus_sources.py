"""Pinned public-source census and bounded acquisition for benchmark corpus builds."""

from __future__ import annotations

import hashlib
import re
import unicodedata
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import NoReturn, cast
from urllib.parse import urlsplit

from fretsure.bench.contracts import (
    BENCHMARK_SOURCE_CENSUS_VERSION,
    canonical_sha256,
)

MAX_SOURCE_RECORDS = 256
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_SOURCE_BYTES = 64 * 1024 * 1024
MAX_SOURCE_TEXT = 2_048
MAX_TIMEOUT_SECONDS = 60
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+\-]*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_CACHE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_LAYERS = frozenset({"public_leadsheet", "public_classical", "public_midi"})
_FORMATS = {
    "public_leadsheet": frozenset({"musicxml", "mxl"}),
    "public_classical": frozenset({"musicxml", "mxl"}),
    "public_midi": frozenset({"midi"}),
}
_ROLES = frozenset({"melody", "bass", "harmony"})


class SourceStatus(StrEnum):
    INCLUDED = "included"
    EXCLUDED = "excluded"
    UNAVAILABLE = "unavailable"


class CorpusSourceError(ValueError):
    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid corpus source {field}: {detail}")


def _fail(field: str, detail: str) -> NoReturn:
    raise CorpusSourceError(field, detail)


def _dict(value: object, field: str, keys: frozenset[str]) -> dict[str, object]:
    if type(value) is not dict or set(cast(dict[object, object], value)) != keys:
        _fail(field, "must contain the exact frozen keys")
    return cast(dict[str, object], value)


def _list(value: object, field: str, maximum: int) -> list[object]:
    if type(value) is not list:
        _fail(field, "must be an exact array")
    result = cast(list[object], value)
    if len(result) > maximum:
        _fail(field, f"count exceeds {maximum}")
    return result


def _text(value: object, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or not value or len(value) > MAX_SOURCE_TEXT:
        _fail(field, "must be a bounded nonempty string")
    if "\x00" in value or unicodedata.normalize("NFC", value) != value:
        _fail(field, "must be NUL-free NFC text")
    return value


def _identifier(value: object, field: str, *, optional: bool = False) -> str | None:
    result = _text(value, field, optional=optional)
    if result is None:
        return None
    if _IDENTIFIER.fullmatch(result) is None:
        _fail(field, "must use the identifier grammar")
    return result


def _sha(value: object, field: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _fail(field, "must be a lowercase SHA-256")
    return value


def _bool(value: object, field: str, *, optional: bool) -> bool | None:
    if value is None and optional:
        return None
    if type(value) is not bool:
        _fail(field, "must be an exact bool")
    return value


def _int(value: object, field: str, *, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(field, f"must be an exact integer in {minimum}..{maximum}")
    return value


@dataclass(frozen=True, slots=True)
class SourceLicense:
    expression: str
    attribution: str | None
    redistribution: bool | None
    derivatives: bool | None
    provider_submission: bool | None


@dataclass(frozen=True, slots=True)
class PublicSource:
    source_id: str
    layer: str
    status: SourceStatus
    source_format: str | None
    source_url: str | None
    upstream_revision: str | None
    retrieval_date: str | None
    expected_sha256: str | None
    cache_name: str | None
    max_bytes: int
    item_id: str | None
    family_id: str | None
    cluster_id: str | None
    genre: str | None
    split: str | None
    role_map: tuple[tuple[str, str], ...]
    normalization: tuple[str, ...]
    license: SourceLicense
    exclusion_reason: str | None


@dataclass(frozen=True, slots=True)
class SourceCensus:
    allowed_hosts: tuple[str, ...]
    timeout_seconds: int
    max_total_bytes: int
    sources: tuple[PublicSource, ...]


def _license_from_dict(value: object, field: str) -> SourceLicense:
    obj = _dict(
        value,
        field,
        frozenset(
            {"expression", "attribution", "redistribution", "derivatives", "provider_submission"}
        ),
    )
    return SourceLicense(
        cast(str, _text(obj["expression"], f"{field}.expression")),
        _text(obj["attribution"], f"{field}.attribution", optional=True),
        _bool(obj["redistribution"], f"{field}.redistribution", optional=True),
        _bool(obj["derivatives"], f"{field}.derivatives", optional=True),
        _bool(obj["provider_submission"], f"{field}.provider_submission", optional=True),
    )


def _role_map(value: object, field: str) -> tuple[tuple[str, str], ...]:
    result: list[tuple[str, str]] = []
    for index, raw in enumerate(_list(value, field, 256)):
        path = f"{field}[{index}]"
        obj = _dict(raw, path, frozenset({"source", "role"}))
        source = cast(str, _text(obj["source"], f"{path}.source"))
        role = cast(str, _identifier(obj["role"], f"{path}.role"))
        if role not in _ROLES:
            _fail(f"{path}.role", "must be melody, bass, or harmony")
        result.append((source, role))
    snapshot = tuple(result)
    if snapshot != tuple(sorted(set(snapshot))):
        _fail(field, "must be uniquely and canonically ordered")
    return snapshot


def _normalization(value: object, field: str) -> tuple[str, ...]:
    result = tuple(
        cast(str, _text(raw, f"{field}[{index}]"))
        for index, raw in enumerate(_list(value, field, 256))
    )
    if result != tuple(sorted(set(result))):
        _fail(field, "must be uniquely and canonically ordered")
    return result


def _validate_url(url: str, allowed_hosts: tuple[str, ...], field: str) -> None:
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        _fail(field, "must use a valid HTTPS authority")
    if (
        parts.scheme != "https"
        or parts.hostname not in allowed_hosts
        or parts.username is not None
        or parts.password is not None
        or port not in (None, 443)
        or parts.query
        or parts.fragment
    ):
        _fail(field, "must be an HTTPS URL on the frozen host allowlist")


def _source_from_dict(
    value: object,
    index: int,
    allowed_hosts: tuple[str, ...],
) -> PublicSource:
    field = f"sources[{index}]"
    obj = _dict(
        value,
        field,
        frozenset(
            {
                "source_id",
                "layer",
                "status",
                "source_format",
                "source_url",
                "upstream_revision",
                "retrieval_date",
                "expected_sha256",
                "cache_name",
                "max_bytes",
                "item_id",
                "family_id",
                "cluster_id",
                "genre",
                "split",
                "role_map",
                "normalization",
                "license",
                "exclusion_reason",
            }
        ),
    )
    source_id = cast(str, _identifier(obj["source_id"], f"{field}.source_id"))
    layer = cast(str, _identifier(obj["layer"], f"{field}.layer"))
    if layer not in _LAYERS:
        _fail(f"{field}.layer", "is not a public benchmark layer")
    raw_status = obj["status"]
    if type(raw_status) is not str:
        _fail(f"{field}.status", "is not a supported status")
    try:
        status = SourceStatus(raw_status)
    except (TypeError, ValueError):
        _fail(f"{field}.status", "is not a supported status")
    source_format = _identifier(obj["source_format"], f"{field}.source_format", optional=True)
    source_url = _text(obj["source_url"], f"{field}.source_url", optional=True)
    revision = _text(obj["upstream_revision"], f"{field}.upstream_revision", optional=True)
    retrieval_date = _text(obj["retrieval_date"], f"{field}.retrieval_date", optional=True)
    expected_sha = _sha(obj["expected_sha256"], f"{field}.expected_sha256", optional=True)
    cache_name = _text(obj["cache_name"], f"{field}.cache_name", optional=True)
    max_bytes = _int(obj["max_bytes"], f"{field}.max_bytes", minimum=0, maximum=MAX_SOURCE_BYTES)
    item_id = _identifier(obj["item_id"], f"{field}.item_id", optional=True)
    family_id = _identifier(obj["family_id"], f"{field}.family_id", optional=True)
    cluster_id = _identifier(obj["cluster_id"], f"{field}.cluster_id", optional=True)
    genre = _text(obj["genre"], f"{field}.genre", optional=True)
    split = _identifier(obj["split"], f"{field}.split", optional=True)
    roles = _role_map(obj["role_map"], f"{field}.role_map")
    normalization = _normalization(obj["normalization"], f"{field}.normalization")
    license_value = _license_from_dict(obj["license"], f"{field}.license")
    reason = _text(obj["exclusion_reason"], f"{field}.exclusion_reason", optional=True)

    if retrieval_date is not None:
        try:
            if date.fromisoformat(retrieval_date).isoformat() != retrieval_date:
                raise ValueError
        except ValueError:
            _fail(f"{field}.retrieval_date", "must be a canonical calendar date")
    if cache_name is not None and _CACHE_NAME.fullmatch(cache_name) is None:
        _fail(f"{field}.cache_name", "must be one inert filename")
    if source_format is not None and source_format not in _FORMATS[layer]:
        _fail(f"{field}.source_format", "does not match the layer")

    if status is SourceStatus.INCLUDED:
        required = (
            source_format,
            source_url,
            revision,
            retrieval_date,
            expected_sha,
            cache_name,
            item_id,
            family_id,
            cluster_id,
            genre,
            split,
        )
        if any(value is None for value in required) or max_bytes == 0:
            _fail(field, "included sources require every pinned identity field")
        assert source_url is not None and revision is not None
        _validate_url(source_url, allowed_hosts, f"{field}.source_url")
        if not roles or not normalization:
            _fail(field, "included sources require explicit role and normalization maps")
        if (
            license_value.expression == "NOASSERTION"
            or license_value.redistribution is not True
            or license_value.derivatives is not True
            or license_value.provider_submission is not True
        ):
            _fail(f"{field}.license", "included sources require verified use permissions")
        if reason is not None:
            _fail(f"{field}.exclusion_reason", "must be null for an included source")
    elif reason is None:
        _fail(f"{field}.exclusion_reason", "is required for excluded/unavailable sources")

    return PublicSource(
        source_id,
        layer,
        status,
        source_format,
        source_url,
        revision,
        retrieval_date,
        expected_sha,
        cache_name,
        max_bytes,
        item_id,
        family_id,
        cluster_id,
        genre,
        split,
        roles,
        normalization,
        license_value,
        reason,
    )


def source_census_from_dict(value: object) -> SourceCensus:
    obj = _dict(
        value,
        "$",
        frozenset({"schema", "allowed_hosts", "timeout_seconds", "max_total_bytes", "sources"}),
    )
    if obj["schema"] != BENCHMARK_SOURCE_CENSUS_VERSION:
        _fail("schema", "has the wrong version")
    hosts = tuple(
        cast(str, _text(raw, f"allowed_hosts[{index}]"))
        for index, raw in enumerate(_list(obj["allowed_hosts"], "allowed_hosts", 32))
    )
    if not hosts or hosts != tuple(sorted(set(hosts))):
        _fail("allowed_hosts", "must be a nonempty canonical set")
    for index, host in enumerate(hosts):
        if urlsplit(f"https://{host}").hostname != host or "/" in host or ":" in host:
            _fail(f"allowed_hosts[{index}]", "must be one lowercase DNS hostname")
    timeout = _int(
        obj["timeout_seconds"],
        "timeout_seconds",
        minimum=1,
        maximum=MAX_TIMEOUT_SECONDS,
    )
    maximum_total = _int(
        obj["max_total_bytes"],
        "max_total_bytes",
        minimum=1,
        maximum=MAX_TOTAL_SOURCE_BYTES,
    )
    sources = tuple(
        _source_from_dict(raw, index, hosts)
        for index, raw in enumerate(_list(obj["sources"], "sources", MAX_SOURCE_RECORDS))
    )
    source_ids = tuple(source.source_id for source in sources)
    cache_names = tuple(
        cast(str, source.cache_name) for source in sources if source.status is SourceStatus.INCLUDED
    )
    if source_ids != tuple(sorted(set(source_ids))):
        _fail("sources", "source ids must be unique and canonically ordered")
    if len(cache_names) != len(set(cache_names)):
        _fail("sources.cache_name", "included cache names must be unique")
    if (
        sum(source.max_bytes for source in sources if source.status is SourceStatus.INCLUDED)
        > maximum_total
    ):
        _fail("max_total_bytes", "is below the declared included-source ceilings")
    return SourceCensus(hosts, timeout, maximum_total, sources)


def _license_to_dict(value: SourceLicense) -> dict[str, object]:
    return {
        "expression": value.expression,
        "attribution": value.attribution,
        "redistribution": value.redistribution,
        "derivatives": value.derivatives,
        "provider_submission": value.provider_submission,
    }


def source_census_to_dict(value: SourceCensus) -> dict[str, object]:
    if type(value) is not SourceCensus:
        _fail("census", "must be an exact SourceCensus")
    sources: list[dict[str, object]] = []
    for source in value.sources:
        sources.append(
            {
                "source_id": source.source_id,
                "layer": source.layer,
                "status": source.status.value,
                "source_format": source.source_format,
                "source_url": source.source_url,
                "upstream_revision": source.upstream_revision,
                "retrieval_date": source.retrieval_date,
                "expected_sha256": source.expected_sha256,
                "cache_name": source.cache_name,
                "max_bytes": source.max_bytes,
                "item_id": source.item_id,
                "family_id": source.family_id,
                "cluster_id": source.cluster_id,
                "genre": source.genre,
                "split": source.split,
                "role_map": [
                    {"source": selector, "role": role} for selector, role in source.role_map
                ],
                "normalization": list(source.normalization),
                "license": _license_to_dict(source.license),
                "exclusion_reason": source.exclusion_reason,
            }
        )
    wire = {
        "schema": BENCHMARK_SOURCE_CENSUS_VERSION,
        "allowed_hosts": list(value.allowed_hosts),
        "timeout_seconds": value.timeout_seconds,
        "max_total_bytes": value.max_total_bytes,
        "sources": sources,
    }
    if source_census_from_dict(wire) != value:
        _fail("census", "contains noncanonical typed values")
    return wire


def source_census_sha256(value: SourceCensus) -> str:
    return canonical_sha256(BENCHMARK_SOURCE_CENSUS_VERSION, source_census_to_dict(value))


SourceFetcher = Callable[[str, float, int], bytes]


def _download(url: str, timeout_seconds: float, max_bytes: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "fretsure-benchmark-corpus/0.1"},
        method="GET",
    )
    response = None
    try:
        response = urllib.request.urlopen(request, timeout=timeout_seconds)
        data = response.read(max_bytes + 1)
    except (OSError, urllib.error.HTTPError, urllib.error.URLError):
        _fail("source_url", "download failed")
    finally:
        if response is not None:
            response.close()
    if type(data) is not bytes or len(data) > max_bytes:
        _fail("source_url", "download exceeds the source byte limit")
    return data


def fetch_included_sources(
    census: SourceCensus,
    output_dir: Path,
    *,
    fetcher: SourceFetcher = _download,
) -> dict[str, str]:
    """Fetch pinned included sources into one fresh directory and return exact hashes."""

    if type(census) is not SourceCensus or not isinstance(output_dir, Path):
        _fail("fetch", "requires a typed census and pathlib output directory")
    source_census_from_dict(source_census_to_dict(census))
    try:
        output_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    except FileExistsError:
        _fail("output_dir", "must be fresh")
    except OSError:
        _fail("output_dir", "could not create output directory")
    written: list[Path] = []
    hashes: dict[str, str] = {}
    observed_total = 0
    try:
        for source in census.sources:
            if source.status is not SourceStatus.INCLUDED:
                continue
            assert source.source_url is not None
            assert source.cache_name is not None
            assert source.expected_sha256 is not None
            data = fetcher(
                source.source_url,
                float(census.timeout_seconds),
                source.max_bytes,
            )
            if type(data) is not bytes or len(data) > source.max_bytes:
                _fail(f"sources.{source.source_id}.response", "returned invalid bytes")
            observed_total += len(data)
            if observed_total > census.max_total_bytes:
                _fail("max_total_bytes", "observed source bytes exceed the total limit")
            digest = hashlib.sha256(data).hexdigest()
            if digest != source.expected_sha256:
                _fail(f"sources.{source.source_id}.expected_sha256", "download hash mismatch")
            destination = output_dir / source.cache_name
            written.append(destination)
            destination.write_bytes(data)
            hashes[source.source_id] = digest
        return dict(sorted(hashes.items()))
    except BaseException:
        for path in reversed(written):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        try:
            output_dir.rmdir()
        except OSError:
            pass
        raise


__all__ = [
    "BENCHMARK_SOURCE_CENSUS_VERSION",
    "CorpusSourceError",
    "PublicSource",
    "SourceCensus",
    "SourceFetcher",
    "SourceLicense",
    "SourceStatus",
    "fetch_included_sources",
    "source_census_from_dict",
    "source_census_sha256",
    "source_census_to_dict",
]
