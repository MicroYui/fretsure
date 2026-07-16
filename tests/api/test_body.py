from __future__ import annotations

import asyncio
from collections.abc import Iterable

import pytest
from starlette.requests import Request

from fretsure.api.body import parse_query, read_bounded_body, require_media_type
from fretsure.api.problems import APIProblem


def _request(
    *,
    headers: Iterable[tuple[bytes, bytes]] = (),
    query: bytes = b"",
    chunks: Iterable[bytes] = (),
) -> tuple[Request, list[int]]:
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": True,
        }
        for chunk in chunks
    ]
    messages.append({"type": "http.request", "body": b"", "more_body": False})
    calls: list[int] = []

    async def receive() -> dict[str, object]:
        calls.append(len(calls))
        return messages.pop(0)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": query,
        "headers": list(headers),
        "client": ("127.0.0.1", 1234),
        "server": ("127.0.0.1", 80),
    }
    return Request(scope, receive), calls


def _body_problem(request: Request, *, limit: int) -> APIProblem:
    with pytest.raises(APIProblem) as caught:
        asyncio.run(read_bounded_body(request, limit=limit))
    return caught.value


def test_streamed_body_preserves_chunks_and_declared_length() -> None:
    request, calls = _request(
        headers=((b"content-length", b"6"),),
        chunks=(b"ab", b"cdef"),
    )
    assert asyncio.run(read_bounded_body(request, limit=6)) == b"abcdef"
    assert len(calls) == 3


def test_declared_oversize_rejects_before_receiving_body() -> None:
    request, calls = _request(headers=((b"content-length", b"11"),), chunks=(b"secret",))
    problem = _body_problem(request, limit=10)
    assert problem.status == 413
    assert problem.code == "BODY_TOO_LARGE"
    assert calls == []


def test_chunked_oversize_stops_on_first_excess_chunk() -> None:
    request, calls = _request(
        headers=((b"transfer-encoding", b"chunked"),),
        chunks=(b"12345", b"678901", b"must-not-be-read"),
    )
    problem = _body_problem(request, limit=10)
    assert problem.status == 413
    assert problem.code == "BODY_TOO_LARGE"
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("headers", "code"),
    [
        (
            ((b"content-length", b"1"), (b"content-length", b"1")),
            "DUPLICATE_HEADER",
        ),
        (((b"content-length", b"x"),), "INVALID_CONTENT_LENGTH"),
        (((b"content-length", b"-1"),), "INVALID_CONTENT_LENGTH"),
        (
            ((b"content-length", b"1"), (b"transfer-encoding", b"chunked")),
            "AMBIGUOUS_BODY_FRAMING",
        ),
        (((b"transfer-encoding", b"gzip"),), "INVALID_TRANSFER_ENCODING"),
    ],
)
def test_invalid_or_ambiguous_framing_is_typed(
    headers: tuple[tuple[bytes, bytes], ...], code: str
) -> None:
    request, _calls = _request(headers=headers, chunks=(b"x",))
    problem = _body_problem(request, limit=10)
    assert problem.status == 400
    assert problem.code == code


@pytest.mark.parametrize(
    ("declared", "chunks"),
    [(b"2", (b"x",)), (b"1", (b"xx",))],
)
def test_content_length_mismatch_fails_closed(
    declared: bytes, chunks: tuple[bytes, ...]
) -> None:
    request, _calls = _request(
        headers=((b"content-length", declared),),
        chunks=chunks,
    )
    problem = _body_problem(request, limit=10)
    assert problem.status == 400
    assert problem.code == "CONTENT_LENGTH_MISMATCH"


def test_media_type_parser_accepts_only_one_explicit_utf8_parameter() -> None:
    request, _calls = _request(
        headers=((b"content-type", b"application/json; charset=utf-8"),)
    )
    assert require_media_type(
        request,
        {"application/json"},
        allow_utf8_charset=True,
    ) == "application/json"


@pytest.mark.parametrize(
    ("headers", "status", "code"),
    [
        (
            ((b"content-type", b"application/json"), (b"content-type", b"application/json")),
            400,
            "DUPLICATE_HEADER",
        ),
        (
            ((b"content-type", b"application/json; charset=latin-1"),),
            415,
            "UNSUPPORTED_MEDIA_TYPE_PARAMETER",
        ),
        (
            ((b"content-type", b"application/json"), (b"content-encoding", b"gzip")),
            415,
            "CONTENT_ENCODING_UNSUPPORTED",
        ),
    ],
)
def test_media_type_and_encoding_ambiguity_is_typed(
    headers: tuple[tuple[bytes, bytes], ...], status: int, code: str
) -> None:
    request, _calls = _request(headers=headers)
    with pytest.raises(APIProblem) as caught:
        require_media_type(request, {"application/json"}, allow_utf8_charset=True)
    assert caught.value.status == status
    assert caught.value.code == code


def test_query_parser_decodes_utf8_and_plus_without_coercion() -> None:
    request, _calls = _request(query=b"filename=%E4%BD%9C%E5%93%81.xml&engine=offline+a")
    assert parse_query(
        request,
        allowed={"filename", "engine"},
        required={"filename"},
    ) == {"filename": "作品.xml", "engine": "offline a"}


@pytest.mark.parametrize(
    ("query", "code"),
    [
        (b"filename=a.xml&filename=b.xml", "DUPLICATE_QUERY_FIELD"),
        (b"filename=a.xml&other=x", "UNKNOWN_QUERY_FIELD"),
        (b"filename=%ZZ", "MALFORMED_QUERY"),
        (b"filename=%ff", "MALFORMED_QUERY"),
        (b"filename", "MALFORMED_QUERY"),
        (b"filename=a=b", "MALFORMED_QUERY"),
        (b"", "MISSING_QUERY_FIELD"),
    ],
)
def test_query_parser_rejects_ambiguous_grammar(query: bytes, code: str) -> None:
    request, _calls = _request(query=query)
    with pytest.raises(APIProblem) as caught:
        parse_query(request, allowed={"filename"}, required={"filename"})
    assert caught.value.status == 400
    assert caught.value.code == code
