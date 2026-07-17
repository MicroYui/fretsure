"""Fail-closed HTTP transport for the optional loopback LLM proxy.

This module imports ``httpx`` and is therefore loaded lazily only when the optional
``agent`` dependencies are installed and :class:`ProxyLLM` is constructed.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterator

import httpx

MAX_CONTENT_LENGTH_DIGITS = 20


class ProxyTransportBoundaryError(httpx.TransportError):
    """A request or response crossed the frozen local transport envelope."""


def _close_stream(stream: httpx.SyncByteStream) -> None:
    try:
        stream.close()
    except Exception:
        pass


class _BoundedResponseStream(httpx.SyncByteStream):
    def __init__(
        self,
        stream: httpx.SyncByteStream,
        *,
        max_bytes: int,
        content_lengths: tuple[str, ...],
        content_encodings: tuple[str, ...],
    ) -> None:
        self._stream = stream
        self._max_bytes = max_bytes
        self._content_lengths = content_lengths
        self._content_encodings = content_encodings
        self._closed = False

    def _reject(self) -> None:
        self.close()
        raise ProxyTransportBoundaryError("LLM proxy transport response was rejected")

    def _validate_headers(self) -> None:
        if self._content_encodings and self._content_encodings != ("identity",):
            self._reject()
        if not self._content_lengths:
            return
        if len(self._content_lengths) != 1:
            self._reject()
        raw = self._content_lengths[0]
        if (
            not 1 <= len(raw) <= MAX_CONTENT_LENGTH_DIGITS
            or not raw.isascii()
            or not raw.isdigit()
            or int(raw) > self._max_bytes
        ):
            self._reject()

    def __iter__(self) -> Iterator[bytes]:
        self._validate_headers()
        total = 0
        for chunk in self._stream:
            if type(chunk) is not bytes:
                self._reject()
            total += len(chunk)
            if total > self._max_bytes:
                self._reject()
            yield chunk

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            _close_stream(self._stream)


def _require_loopback_url(url: httpx.URL) -> None:
    if url.scheme not in {"http", "https"} or url.username or url.password:
        raise ProxyTransportBoundaryError("LLM proxy request URL was rejected")
    host = url.host
    if host == "localhost":
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ProxyTransportBoundaryError("LLM proxy request URL was rejected") from None
    if not address.is_loopback:
        raise ProxyTransportBoundaryError("LLM proxy request URL was rejected")


class BoundedLoopbackTransport(httpx.BaseTransport):
    """Restrict requests to loopback and cap identity-encoded response bytes."""

    def __init__(
        self,
        *,
        max_response_bytes: int,
        inner: httpx.BaseTransport | None = None,
        limits: httpx.Limits | None = None,
    ) -> None:
        if type(max_response_bytes) is not int or not 1 <= max_response_bytes <= 64 << 20:
            raise ValueError("max_response_bytes must be an exact integer in 1..67108864")
        self._max_response_bytes = max_response_bytes
        self._inner = inner or httpx.HTTPTransport(
            trust_env=False,
            retries=0,
            limits=limits or httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        _require_loopback_url(request.url)
        response = self._inner.handle_request(request)
        stream = response.stream
        if not isinstance(stream, httpx.SyncByteStream):
            try:
                response.close()
            except Exception:
                pass
            raise ProxyTransportBoundaryError("LLM proxy response stream was rejected")
        bounded_stream = _BoundedResponseStream(
            stream,
            max_bytes=self._max_response_bytes,
            content_lengths=tuple(response.headers.get_list("content-length")),
            content_encodings=tuple(
                value.strip().lower()
                for value in response.headers.get_list("content-encoding")
                if value.strip()
            ),
        )
        return httpx.Response(
            response.status_code,
            headers=response.headers,
            stream=bounded_stream,
            extensions=response.extensions,
        )

    def close(self) -> None:
        self._inner.close()


def build_proxy_http_client(
    *,
    timeout_seconds: float,
    connect_timeout_seconds: float,
    max_response_bytes: int,
) -> httpx.Client:
    """Build the one supported proxy client without ambient proxy/redirect state."""

    timeout = httpx.Timeout(timeout_seconds, connect=connect_timeout_seconds)
    limits = httpx.Limits(
        max_connections=1,
        max_keepalive_connections=1,
        keepalive_expiry=30.0,
    )
    transport = BoundedLoopbackTransport(
        max_response_bytes=max_response_bytes,
        limits=limits,
    )
    return httpx.Client(
        trust_env=False,
        follow_redirects=False,
        timeout=timeout,
        limits=limits,
        headers={"Accept-Encoding": "identity"},
        transport=transport,
    )


__all__ = [
    "BoundedLoopbackTransport",
    "ProxyTransportBoundaryError",
    "build_proxy_http_client",
]
