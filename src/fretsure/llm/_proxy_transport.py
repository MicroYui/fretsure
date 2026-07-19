"""Fail-closed HTTP transport for the optional loopback LLM proxy.

This module imports ``httpx`` and is therefore loaded lazily only when the optional
``agent`` dependencies are installed and :class:`ProxyLLM` is constructed.
"""

from __future__ import annotations

import ipaddress
import math
import socket
import ssl
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TypeVar, cast

import httpcore
import httpx

MAX_CONTENT_LENGTH_DIGITS = 20
_DEADLINE_ERROR_MESSAGE = "LLM proxy attempt deadline exceeded"
_T = TypeVar("_T")
_SocketOption = (
    tuple[int, int, int]
    | tuple[int, int, bytes | bytearray]
    | tuple[int, int, None, int]
)


class ProxyTransportBoundaryError(httpx.TransportError):
    """A request or response crossed the frozen local transport envelope."""


class ProxyAttemptDeadlineError(TimeoutError):
    """One complete proxy attempt exceeded its wall-clock reservation."""


def _positive_finite_timeout(value: object, *, name: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be a positive finite number")
    exact = float(cast(int | float, value))
    if not math.isfinite(exact) or exact <= 0.0:
        raise ValueError(f"{name} must be a positive finite number")
    return exact


class _AttemptDeadline:
    """Shared wall-clock deadline plus a bounded socket-abort watchdog."""

    def __init__(self, timeout_seconds: float) -> None:
        self._expires_at = time.monotonic() + timeout_seconds
        self._lock = threading.Lock()
        self._active_streams: set[_DeadlineNetworkStream] = set()
        self._expired = False
        self._finished = False
        self._timer = threading.Timer(timeout_seconds, self._expire)
        # A crashed caller must never be kept alive by the watchdog. The timer is
        # also cancelled synchronously when the attempt leaves its context.
        self._timer.daemon = True
        self._timer.start()

    def _expired_locked(self) -> bool:
        if not self._expired and time.monotonic() >= self._expires_at:
            self._expired = True
        return self._expired

    def remaining_seconds(self) -> float:
        with self._lock:
            remaining = self._expires_at - time.monotonic()
            if remaining <= 0.0:
                self._expired = True
                raise ProxyAttemptDeadlineError(_DEADLINE_ERROR_MESSAGE)
            return remaining

    def cap_timeout(
        self,
        timeout: float | None,
        error_type: type[Exception],
    ) -> float:
        try:
            remaining = self.remaining_seconds()
        except ProxyAttemptDeadlineError:
            raise error_type(_DEADLINE_ERROR_MESSAGE) from None
        return remaining if timeout is None else min(timeout, remaining)

    def register(
        self,
        stream: _DeadlineNetworkStream,
        error_type: type[Exception],
    ) -> None:
        with self._lock:
            expired = self._expired_locked()
            if not expired:
                self._active_streams.add(stream)
        if expired:
            stream.abort()
            raise error_type(_DEADLINE_ERROR_MESSAGE)

    def unregister(self, stream: _DeadlineNetworkStream) -> None:
        with self._lock:
            self._active_streams.discard(stream)

    def raise_if_expired(
        self,
        error_type: type[Exception] = ProxyAttemptDeadlineError,
        *,
        stream: _DeadlineNetworkStream | None = None,
    ) -> None:
        with self._lock:
            expired = self._expired_locked()
        if expired:
            if stream is not None:
                stream.abort()
            raise error_type(_DEADLINE_ERROR_MESSAGE)

    def _expire(self) -> None:
        with self._lock:
            if self._finished:
                return
            self._expired = True
            active_streams = tuple(self._active_streams)
        for stream in active_streams:
            stream.abort()

    def finish(self) -> None:
        with self._lock:
            if self._finished:
                return
            self._finished = True
            self._active_streams.clear()
        self._timer.cancel()


_CURRENT_ATTEMPT_DEADLINE: ContextVar[_AttemptDeadline | None] = ContextVar(
    "fretsure_proxy_attempt_deadline",
    default=None,
)


@contextmanager
def proxy_attempt_deadline(timeout_seconds: float) -> Iterator[None]:
    """Bound one complete synchronous SDK attempt by elapsed wall-clock time."""

    exact_timeout = _positive_finite_timeout(
        timeout_seconds,
        name="timeout_seconds",
    )
    deadline = _AttemptDeadline(exact_timeout)
    token = _CURRENT_ATTEMPT_DEADLINE.set(deadline)
    try:
        yield
    except Exception:
        # Closing a socket from the watchdog can surface as EOF, protocol, or
        # decode failure depending on where HTTPX/SDK parsing was interrupted.
        # Once the wall clock expired, normalize all of those to one retryable
        # deadline result without retaining their potentially sensitive detail.
        deadline.raise_if_expired()
        raise
    else:
        deadline.raise_if_expired()
    finally:
        _CURRENT_ATTEMPT_DEADLINE.reset(token)
        deadline.finish()


class _DeadlineNetworkStream(httpcore.NetworkStream):
    """Apply the active attempt's remaining budget to every socket operation."""

    def __init__(self, inner: httpcore.NetworkStream) -> None:
        self._inner = inner
        self._close_lock = threading.Lock()
        self._closed = False

    def _run(
        self,
        operation: Callable[[float | None], _T],
        *,
        timeout: float | None,
        error_type: type[Exception],
    ) -> _T:
        deadline = _CURRENT_ATTEMPT_DEADLINE.get()
        if deadline is None:
            return operation(timeout)
        deadline.register(self, error_type)
        try:
            capped_timeout = deadline.cap_timeout(timeout, error_type)
            try:
                result = operation(capped_timeout)
            except Exception:
                deadline.raise_if_expired(error_type, stream=self)
                raise
            deadline.raise_if_expired(error_type, stream=self)
            return result
        finally:
            deadline.unregister(self)

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._run(
            lambda capped: self._inner.read(max_bytes, timeout=capped),
            timeout=timeout,
            error_type=httpcore.ReadTimeout,
        )

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._run(
            lambda capped: self._inner.write(buffer, timeout=capped),
            timeout=timeout,
            error_type=httpcore.WriteTimeout,
        )

    def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.NetworkStream:
        stream = self._run(
            lambda capped: self._inner.start_tls(
                ssl_context,
                server_hostname=server_hostname,
                timeout=capped,
            ),
            timeout=timeout,
            error_type=httpcore.ConnectTimeout,
        )
        return _DeadlineNetworkStream(stream)

    def abort(self) -> None:
        """Interrupt an active blocking operation and permanently poison its socket."""

        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            try:
                raw_socket = self._inner.get_extra_info("socket")
            except Exception:
                raw_socket = None
            try:
                raw_socket.shutdown(socket.SHUT_RDWR)
            except (AttributeError, OSError):
                pass
            try:
                self._inner.close()
            except Exception:
                pass

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            self._inner.close()

    def get_extra_info(self, info: str) -> object:
        return self._inner.get_extra_info(info)


class _DeadlineNetworkBackend(httpcore.NetworkBackend):
    def __init__(self) -> None:
        self._inner = httpcore.SyncBackend()

    def _connect(
        self,
        operation: Callable[[float | None], httpcore.NetworkStream],
        *,
        timeout: float | None,
    ) -> httpcore.NetworkStream:
        deadline = _CURRENT_ATTEMPT_DEADLINE.get()
        if deadline is None:
            return _DeadlineNetworkStream(operation(timeout))
        capped_timeout = deadline.cap_timeout(timeout, httpcore.ConnectTimeout)
        try:
            stream = _DeadlineNetworkStream(operation(capped_timeout))
        except Exception:
            deadline.raise_if_expired(httpcore.ConnectTimeout)
            raise
        deadline.raise_if_expired(httpcore.ConnectTimeout, stream=stream)
        return stream

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[_SocketOption] | None = None,
    ) -> httpcore.NetworkStream:
        return self._connect(
            lambda capped: self._inner.connect_tcp(
                host,
                port,
                timeout=capped,
                local_address=local_address,
                socket_options=socket_options,
            ),
            timeout=timeout,
        )

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[_SocketOption] | None = None,
    ) -> httpcore.NetworkStream:
        return self._connect(
            lambda capped: self._inner.connect_unix_socket(
                path,
                timeout=capped,
                socket_options=socket_options,
            ),
            timeout=timeout,
        )

    def sleep(self, seconds: float) -> None:
        deadline = _CURRENT_ATTEMPT_DEADLINE.get()
        if deadline is None:
            self._inner.sleep(seconds)
            return
        capped = deadline.cap_timeout(seconds, httpcore.ConnectTimeout)
        self._inner.sleep(capped)
        deadline.raise_if_expired(httpcore.ConnectTimeout)


class _DeadlineHTTPTransport(httpx.HTTPTransport):
    """HTTPX transport whose socket backend observes one whole-attempt deadline."""

    def __init__(self, *, limits: httpx.Limits) -> None:
        super().__init__(trust_env=False, retries=0, limits=limits)
        # HTTPX 0.28 has no public synchronous network-backend injection point.
        # The project pins that minor line; focused tests freeze this httpcore 1.0
        # pool replacement until HTTPX exposes an equivalent public hook.
        original_pool = self._pool
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            retries=0,
            network_backend=_DeadlineNetworkBackend(),
        )
        original_pool.close()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        deadline = _CURRENT_ATTEMPT_DEADLINE.get()
        if deadline is None:
            return super().handle_request(request)
        remaining = deadline.remaining_seconds()
        timeouts = dict(request.extensions.get("timeout", {}))
        pool_timeout = timeouts.get("pool")
        timeouts["pool"] = (
            remaining if pool_timeout is None else min(pool_timeout, remaining)
        )
        request.extensions["timeout"] = timeouts
        return super().handle_request(request)


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
        exact_limits = limits or httpx.Limits(max_connections=1, max_keepalive_connections=1)
        self._inner = inner or _DeadlineHTTPTransport(
            limits=exact_limits,
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

    exact_timeout = _positive_finite_timeout(
        timeout_seconds,
        name="timeout_seconds",
    )
    exact_connect_timeout = _positive_finite_timeout(
        connect_timeout_seconds,
        name="connect_timeout_seconds",
    )
    timeout = httpx.Timeout(exact_timeout, connect=exact_connect_timeout)
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
    "ProxyAttemptDeadlineError",
    "ProxyTransportBoundaryError",
    "build_proxy_http_client",
    "proxy_attempt_deadline",
]
