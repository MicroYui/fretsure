from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from fretsure.bench.corpus_sources import (
    BENCHMARK_SOURCE_CENSUS_VERSION,
    CorpusSourceError,
    SourceCensus,
    fetch_included_sources,
    source_census_from_dict,
    source_census_to_dict,
)


def _wire(data: bytes = b"public score") -> dict[str, object]:
    revision = "1" * 40
    return {
        "schema": BENCHMARK_SOURCE_CENSUS_VERSION,
        "allowed_hosts": ["raw.githubusercontent.com"],
        "timeout_seconds": 10,
        "max_total_bytes": 4096,
        "sources": [
            {
                "source_id": "openscore-example",
                "layer": "public_classical",
                "status": "included",
                "source_format": "mxl",
                "source_url": f"https://raw.githubusercontent.com/O/R/{revision}/score.mxl",
                "upstream_revision": revision,
                "retrieval_date": "2026-07-17",
                "expected_sha256": hashlib.sha256(data).hexdigest(),
                "cache_name": "openscore-example.mxl",
                "max_bytes": 1024,
                "item_id": "public-classical-000001",
                "family_id": "public-family-000001",
                "cluster_id": "public-cluster-000001",
                "genre": "lieder",
                "split": "test",
                "role_map": [{"source": "part:P1/voice:1", "role": "melody"}],
                "normalization": ["explicit-part-and-voice-selection"],
                "license": {
                    "expression": "CC0-1.0",
                    "attribution": "OpenScore Lieder Corpus",
                    "redistribution": True,
                    "derivatives": True,
                    "provider_submission": True,
                },
                "exclusion_reason": None,
            },
            {
                "source_id": "public-leadsheet-unavailable",
                "layer": "public_leadsheet",
                "status": "unavailable",
                "source_format": None,
                "source_url": None,
                "upstream_revision": None,
                "retrieval_date": "2026-07-17",
                "expected_sha256": None,
                "cache_name": None,
                "max_bytes": 0,
                "item_id": None,
                "family_id": None,
                "cluster_id": None,
                "genre": None,
                "split": None,
                "role_map": [],
                "normalization": [],
                "license": {
                    "expression": "NOASSERTION",
                    "attribution": None,
                    "redistribution": None,
                    "derivatives": None,
                    "provider_submission": None,
                },
                "exclusion_reason": "NO_LICENSE_AUDITED_SOURCE",
            },
        ],
    }


def test_source_census_strict_round_trip_and_hash_inputs() -> None:
    census = source_census_from_dict(_wire())

    assert isinstance(census, SourceCensus)
    assert source_census_from_dict(source_census_to_dict(census)) == census
    assert census.sources[0].role_map == (("part:P1/voice:1", "melody"),)


def test_included_source_requires_pinned_url_permissions_and_exact_keys() -> None:
    cases = []
    missing_permission = _wire()
    missing_permission["sources"][0]["license"]["provider_submission"] = None  # type: ignore[index]
    cases.append(missing_permission)
    moving_url = _wire()
    moving_url["sources"][0]["source_url"] = (  # type: ignore[index]
        "http://raw.githubusercontent.com/O/R/" + "1" * 40 + "/score.mxl"
    )
    cases.append(moving_url)
    unknown = _wire()
    unknown["extra"] = True
    cases.append(unknown)

    for value in cases:
        with pytest.raises(CorpusSourceError):
            source_census_from_dict(value)


def test_fetch_writes_exact_pinned_bytes(tmp_path: Path) -> None:
    data = b"public score"
    census = source_census_from_dict(_wire(data))
    url = census.sources[0].source_url
    assert url is not None
    calls: list[tuple[str, float, int]] = []

    def fetcher(source_url: str, timeout: float, max_bytes: int) -> bytes:
        calls.append((source_url, timeout, max_bytes))
        return data

    output = tmp_path / "sources"

    hashes = fetch_included_sources(
        census,
        output,
        fetcher=fetcher,
    )

    assert hashes == {"openscore-example": hashlib.sha256(data).hexdigest()}
    assert (output / "openscore-example.mxl").read_bytes() == data
    assert {path.name for path in output.iterdir()} == {"openscore-example.mxl"}
    assert calls == [(url, 10.0, 1024)]


def test_fetch_rejects_oversized_result_and_cleans_output(tmp_path: Path) -> None:
    data = b"public score"
    census = source_census_from_dict(_wire(data))
    url = census.sources[0].source_url
    assert url is not None
    output = tmp_path / "sources"

    with pytest.raises(CorpusSourceError, match="invalid bytes"):
        fetch_included_sources(
            census,
            output,
            fetcher=lambda _url, _timeout, max_bytes: b"x" * (max_bytes + 1),
        )

    assert not output.exists()


def test_fetch_hash_failure_leaves_same_path_retryable(tmp_path: Path) -> None:
    data = b"public score"
    census = source_census_from_dict(_wire(data))
    url = census.sources[0].source_url
    assert url is not None
    output = tmp_path / "sources"

    with pytest.raises(CorpusSourceError):
        fetch_included_sources(
            census,
            output,
            fetcher=lambda _url, _timeout, _max_bytes: b"changed",
        )
    assert not output.exists()

    fetch_included_sources(
        census,
        output,
        fetcher=lambda _url, _timeout, _max_bytes: data,
    )
    assert (output / "openscore-example.mxl").read_bytes() == data


def test_fetch_refuses_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "sources"
    output.mkdir()

    with pytest.raises(CorpusSourceError, match="fresh"):
        fetch_included_sources(source_census_from_dict(_wire()), output)
