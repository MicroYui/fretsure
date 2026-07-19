import socketserver
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

import httpx
import pytest

from fretsure.llm._proxy_transport import (
    BoundedLoopbackTransport,
    ProxyTransportBoundaryError,
    build_proxy_http_client,
)


class _Chunks(httpx.SyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        yield from self.chunks

    def close(self) -> None:
        self.closed = True


def _client_for(
    handler: httpx.MockTransport,
    *,
    max_bytes: int = 5,
    follow_redirects: bool = False,
) -> httpx.Client:
    return httpx.Client(
        transport=BoundedLoopbackTransport(
            max_response_bytes=max_bytes,
            inner=handler,
        ),
        follow_redirects=follow_redirects,
        headers={"Accept-Encoding": "identity"},
    )


@contextmanager
def _raw_http_server(response: bytes) -> Iterator[tuple[str, list[bytes]]]:
    requests: list[bytes] = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            self.request.settimeout(2.0)
            received = bytearray()
            try:
                while b"\r\n\r\n" not in received and len(received) <= 64 * 1024:
                    chunk = self.request.recv(4096)
                    if not chunk:
                        break
                    received.extend(chunk)
            except OSError:
                pass
            requests.append(bytes(received))
            try:
                self.request.sendall(response)
            except OSError:
                pass

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    server = Server(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = server.server_address
    host = cast(str, address[0])
    port = address[1]
    try:
        yield f"http://{host}:{port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def _http_response(body: bytes) -> bytes:
    return (
        b"HTTP/1.1 200 OK\r\n"
        + f"Content-Length: {len(body)}\r\n".encode("ascii")
        + b"Connection: close\r\n\r\n"
        + body
    )


def test_transport_accepts_exact_cap_and_forces_identity_request() -> None:
    observed_encoding: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal observed_encoding
        observed_encoding = request.headers.get("accept-encoding")
        return httpx.Response(200, content=b"12345")

    with _client_for(httpx.MockTransport(handler)) as client:
        assert client.get("http://127.0.0.1:8317/v1").content == b"12345"
    assert observed_encoding == "identity"


def test_transport_accepts_explicit_identity_response_encoding() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-encoding": "identity"},
            content=b"ok",
        )

    with _client_for(httpx.MockTransport(handler)) as client:
        assert client.get("http://127.0.0.1:8317/v1").content == b"ok"


@pytest.mark.parametrize("status_code", [200, 500])
def test_transport_rejects_content_length_over_cap_before_body_read(
    status_code: int,
) -> None:
    stream = _Chunks((b"ignored",))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            headers={"content-length": "6"},
            stream=stream,
        )

    with _client_for(httpx.MockTransport(handler)) as client, pytest.raises(
        ProxyTransportBoundaryError
    ):
        client.get("http://127.0.0.1:8317/v1")
    assert stream.closed is True


def test_transport_rejects_chunked_cumulative_body_over_cap() -> None:
    stream = _Chunks((b"123", b"456"))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    with _client_for(httpx.MockTransport(handler)) as client, pytest.raises(
        ProxyTransportBoundaryError
    ):
        client.get("http://127.0.0.1:8317/v1")
    assert stream.closed is True


@pytest.mark.parametrize(
    "headers",
    [
        [("content-length", "3"), ("content-length", "3")],
        [("content-length", "3, 3")],
        [("content-length", "+3")],
        [("content-length", "three")],
        [("content-length", "9" * 21)],
    ],
)
def test_transport_rejects_ambiguous_or_noncanonical_content_length(
    headers: list[tuple[str, str]],
) -> None:
    stream = _Chunks((b"123",))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=headers, stream=stream)

    with _client_for(httpx.MockTransport(handler)) as client, pytest.raises(
        ProxyTransportBoundaryError
    ):
        client.get("http://127.0.0.1:8317/v1")
    assert stream.closed is True


def test_transport_rejects_non_bytes_stream_chunk() -> None:
    stream = _Chunks((b"ok",))
    stream.chunks = (bytearray(b"bad"),)  # type: ignore[assignment]

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    with _client_for(httpx.MockTransport(handler)) as client, pytest.raises(
        ProxyTransportBoundaryError
    ):
        client.get("http://127.0.0.1:8317/v1")
    assert stream.closed is True


@pytest.mark.parametrize("encoding", ["gzip", "deflate", "br", "identity, gzip"])
def test_transport_rejects_compressed_responses(encoding: str) -> None:
    stream = _Chunks((b"x",))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-encoding": encoding},
            stream=stream,
        )

    with _client_for(httpx.MockTransport(handler)) as client, pytest.raises(
        ProxyTransportBoundaryError
    ):
        client.get("http://127.0.0.1:8317/v1")
    assert stream.closed is True


@pytest.mark.parametrize(
    "encodings",
    [
        [("content-encoding", "identity"), ("content-encoding", "identity")],
        [("content-encoding", "identity"), ("content-encoding", "gzip")],
    ],
)
def test_transport_rejects_multiple_content_encoding_headers(
    encodings: list[tuple[str, str]],
) -> None:
    stream = _Chunks((b"x",))

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers=encodings, stream=stream)

    with _client_for(httpx.MockTransport(handler)) as client, pytest.raises(
        ProxyTransportBoundaryError
    ):
        client.get("http://127.0.0.1:8317/v1")
    assert stream.closed is True


def test_transport_rejects_non_loopback_before_inner_transport() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, content=b"ok")

    with _client_for(httpx.MockTransport(handler)) as client, pytest.raises(
        ProxyTransportBoundaryError
    ):
        client.get("https://example.com/v1")
    assert called is False


def test_redirect_is_returned_without_following_off_loopback_location() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(307, headers={"location": "https://example.com/secret"})

    with _client_for(httpx.MockTransport(handler)) as client:
        response = client.get("http://127.0.0.1:8317/v1")
    assert response.status_code == 307
    assert calls == 1


def test_transport_blocks_off_loopback_redirect_even_if_following_is_enabled() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(307, headers={"location": "https://example.com/secret"})

    with _client_for(
        httpx.MockTransport(handler),
        follow_redirects=True,
    ) as client, pytest.raises(ProxyTransportBoundaryError):
        client.get("http://127.0.0.1:8317/v1")
    assert calls == 1


def test_built_client_disables_environment_and_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    client = build_proxy_http_client(
        timeout_seconds=30.0,
        connect_timeout_seconds=5.0,
        max_response_bytes=1024,
    )
    try:
        assert client.follow_redirects is False
        assert client._trust_env is False
        assert client._mounts == {}
        assert client.headers["accept-encoding"] == "identity"
        assert client.timeout.connect == 5.0
        assert client.timeout.read == 30.0
    finally:
        client.close()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timeout_seconds", True),
        ("timeout_seconds", "30"),
        ("timeout_seconds", 0),
        ("timeout_seconds", -1),
        ("timeout_seconds", float("nan")),
        ("timeout_seconds", float("inf")),
        ("connect_timeout_seconds", True),
        ("connect_timeout_seconds", 0),
    ],
)
def test_built_client_rejects_nonexact_or_unbounded_timeouts(
    field: str,
    value: object,
) -> None:
    kwargs: dict[str, object] = {
        "timeout_seconds": 30.0,
        "connect_timeout_seconds": 5.0,
        "max_response_bytes": 1024,
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match=rf"{field} must be a positive finite number"):
        build_proxy_http_client(**kwargs)  # type: ignore[arg-type]


def test_built_client_ignores_poisoned_ambient_proxy_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with (
        _raw_http_server(_http_response(b"direct")) as (target_url, target_requests),
        _raw_http_server(_http_response(b"proxy")) as (proxy_url, proxy_requests),
    ):
        for variable in (
            "HTTP_PROXY",
            "http_proxy",
            "HTTPS_PROXY",
            "https_proxy",
            "ALL_PROXY",
            "all_proxy",
        ):
            monkeypatch.setenv(variable, proxy_url)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)

        client = build_proxy_http_client(
            timeout_seconds=2.0,
            connect_timeout_seconds=1.0,
            max_response_bytes=1024,
        )
        try:
            assert client.get(f"{target_url}/v1").content == b"direct"
        finally:
            client.close()

    assert len(target_requests) == 1
    assert proxy_requests == []


def test_real_http_parser_rejects_oversized_response_headers() -> None:
    oversized_response = (
        b"HTTP/1.1 200 OK\r\n"
        + b"X-Oversized: "
        # Exceed several network read chunks before the terminating CRLF. h11's
        # incomplete-event bound is otherwise sensitive to packet coalescing.
        + b"x" * (512 * 1024)
        + b"\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok"
    )
    with _raw_http_server(oversized_response) as (target_url, requests):
        client = build_proxy_http_client(
            timeout_seconds=2.0,
            connect_timeout_seconds=1.0,
            max_response_bytes=1024,
        )
        try:
            with pytest.raises(httpx.RemoteProtocolError):
                client.get(f"{target_url}/v1")
        finally:
            client.close()

    assert len(requests) == 1
