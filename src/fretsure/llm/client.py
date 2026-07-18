"""LLM client protocol, a real proxy implementation, and a deterministic fake."""

import hashlib
import inspect
import json
import math
import os
import time
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal, Protocol, Self, cast
from urllib.parse import urlsplit

DEFAULT_PROXY_MODEL = "gpt-5.6-sol"
FAKE_LLM_MODEL_ID = "fake-scripted"
CONSTANT_LLM_MODEL_ID = "constant-stub"
MAX_LLM_MODEL_ID_CHARS = 128
PROXY_REQUEST_TIMEOUT_SECONDS = 30.0
PROXY_CONNECT_TIMEOUT_SECONDS = 5.0
MAX_PROXY_CONTENT_BLOCKS = 64
MAX_PROXY_OUTPUT_TOKENS = 16_384
MAX_PROXY_REQUEST_FIELD_BYTES = 1024 * 1024
MAX_PROXY_REQUEST_BYTES = 2 * 1024 * 1024
MAX_PROXY_TEXT_BLOCK_BYTES = 256 * 1024
MAX_PROXY_RESPONSE_BYTES = 1024 * 1024
MAX_PROXY_TRANSPORT_RESPONSE_BYTES = 1024 * 1024
MAX_PROXY_TEXT_BYTES_PER_TOKEN = 32
MAX_PROXY_RESPONSE_ID_CHARS = 512
MAX_PROXY_USAGE_TOKENS = 1_000_000_000


@dataclass(frozen=True, slots=True)
class ProxyCallMetadata:
    """Bounded provider metadata for the most recent proxy call.

    Response identifiers are one-way digests; missing proxy fields remain ``None``
    rather than being fabricated as zero. The benchmark copies this snapshot into its
    private observation boundary after every sequential call.
    """

    status: Literal["succeeded", "failed"]
    attempts: int
    returned_model_id: str | None
    response_id_sha256: str | None
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None


class _ProxyResponseValidationError(ValueError):
    """Internal marker for a non-retriable malformed provider response."""


class LLMProxyRequestError(ValueError):
    """The direct proxy request exceeded the bounded public client envelope."""


class LLMClientCloseError(RuntimeError):
    """A client with an explicit lifecycle could not be closed safely."""


class LLMIntegrityError(Exception):
    """A fail-closed harness boundary failed and must not become model fallback."""


class LLMProxyResponseIntegrityError(LLMIntegrityError):
    """The provider returned a response that cannot be trusted as model output."""


def _best_effort_close(close: Callable[[], object]) -> None:
    """Run a GC-only close callback without leaking raw exceptions."""

    try:
        close()
    except BaseException:
        pass


class ProxyAttemptObserver(Protocol):
    """Fail-closed hook invoked immediately around each proxy network attempt."""

    def before_attempt(self, attempt_index: int) -> None: ...

    def after_attempt(
        self,
        attempt_index: int,
        *,
        status: Literal["succeeded", "failed"],
        retryable: bool,
    ) -> None: ...


_CURRENT_PROXY_ATTEMPT_OBSERVER: ContextVar[ProxyAttemptObserver | None] = ContextVar(
    "fretsure_proxy_attempt_observer",
    default=None,
)


@contextmanager
def observe_proxy_attempts(observer: ProxyAttemptObserver) -> Iterator[None]:
    """Install one synchronous observer for the current proxy call."""

    token = _CURRENT_PROXY_ATTEMPT_OBSERVER.set(observer)
    try:
        yield
    finally:
        _CURRENT_PROXY_ATTEMPT_OBSERVER.reset(token)


def _before_proxy_attempt(attempt_index: int) -> None:
    observer = _CURRENT_PROXY_ATTEMPT_OBSERVER.get()
    if observer is None:
        return
    try:
        observer.before_attempt(attempt_index)
    except LLMIntegrityError:
        raise
    except Exception:
        raise LLMIntegrityError("LLM attempt observer failed") from None


def _after_proxy_attempt(
    attempt_index: int,
    *,
    status: Literal["succeeded", "failed"],
    retryable: bool,
) -> None:
    observer = _CURRENT_PROXY_ATTEMPT_OBSERVER.get()
    if observer is None:
        return
    try:
        observer.after_attempt(
            attempt_index,
            status=status,
            retryable=retryable,
        )
    except LLMIntegrityError:
        raise
    except Exception:
        raise LLMIntegrityError("LLM attempt observer failed") from None


def _validate_proxy_request(
    system: object,
    user: object,
    max_tokens: object,
    temperature: object,
) -> tuple[str, str, int, float]:
    if type(system) is not str or type(user) is not str:
        raise LLMProxyRequestError("system and user must be exact strings")
    if (
        len(system) > MAX_PROXY_REQUEST_FIELD_BYTES
        or len(user) > MAX_PROXY_REQUEST_FIELD_BYTES
        or len(system) + len(user) > MAX_PROXY_REQUEST_BYTES
    ):
        raise LLMProxyRequestError("proxy request text exceeds the bounded byte limit")
    try:
        system_bytes = system.encode("utf-8")
        user_bytes = user.encode("utf-8")
    except UnicodeEncodeError:
        raise LLMProxyRequestError("system and user must be valid UTF-8 text") from None
    if (
        len(system_bytes) > MAX_PROXY_REQUEST_FIELD_BYTES
        or len(user_bytes) > MAX_PROXY_REQUEST_FIELD_BYTES
        or len(system_bytes) + len(user_bytes) > MAX_PROXY_REQUEST_BYTES
    ):
        raise LLMProxyRequestError("proxy request text exceeds the bounded byte limit")
    if type(max_tokens) is not int or not 1 <= max_tokens <= MAX_PROXY_OUTPUT_TOKENS:
        raise LLMProxyRequestError(
            f"max_tokens must be an exact integer in 1..{MAX_PROXY_OUTPUT_TOKENS}"
        )
    if type(temperature) not in (int, float):
        raise LLMProxyRequestError("temperature must be a finite real number in 0..1")
    exact_temperature = cast(int | float, temperature)
    if not 0.0 <= exact_temperature <= 1.0:
        raise LLMProxyRequestError("temperature must be a finite real number in 0..1")
    normalized_temperature = float(exact_temperature)
    if not math.isfinite(normalized_temperature) or not 0.0 <= normalized_temperature <= 1.0:
        raise LLMProxyRequestError("temperature must be a finite real number in 0..1")
    return system, user, max_tokens, normalized_temperature


def _failed_proxy_metadata(attempts: int) -> ProxyCallMetadata:
    return ProxyCallMetadata("failed", attempts, None, None, None, None, None, None)


def _contains_proxy_transport_boundary(error: BaseException) -> bool:
    from fretsure.llm._proxy_transport import ProxyTransportBoundaryError

    current: BaseException | None = error
    seen: set[int] = set()
    for _ in range(8):
        if current is None or id(current) in seen:
            return False
        if isinstance(current, ProxyTransportBoundaryError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _is_retryable_proxy_exception(error: Exception) -> bool:
    if _contains_proxy_transport_boundary(error):
        return False
    try:
        import anthropic
    except Exception:
        return False
    if isinstance(error, anthropic.APIConnectionError):
        return True
    if not isinstance(error, anthropic.APIStatusError):
        return False
    retry_header = error.response.headers.get("x-should-retry")
    if retry_header == "true":
        return True
    if retry_header == "false":
        return False
    status_code = error.status_code
    return type(status_code) is int and (
        status_code in {408, 409, 429} or 500 <= status_code <= 599
    )


def _optional_response_field(value: object, name: str) -> object | None:
    try:
        inspect.getattr_static(value, name)
    except AttributeError:
        return None
    except Exception:
        raise _ProxyResponseValidationError from None
    try:
        return cast(object, getattr(value, name))
    except Exception:
        raise _ProxyResponseValidationError from None


def _optional_usage_count(usage: object | None, name: str) -> int | None:
    if usage is None:
        return None
    raw = _optional_response_field(usage, name)
    if raw is None:
        return None
    if type(raw) is not int or not 0 <= raw <= MAX_PROXY_USAGE_TOKENS:
        raise _ProxyResponseValidationError
    return raw


def _snapshot_proxy_response(
    message: object,
    *,
    attempts: int,
    max_tokens: int,
) -> tuple[str, ProxyCallMetadata]:
    if type(max_tokens) is not int or not 1 <= max_tokens <= MAX_PROXY_OUTPUT_TOKENS:
        raise _ProxyResponseValidationError
    content = _optional_response_field(message, "content")
    if type(content) is not list or len(content) > MAX_PROXY_CONTENT_BLOCKS:
        raise _ProxyResponseValidationError
    total_limit = min(MAX_PROXY_RESPONSE_BYTES, max_tokens * MAX_PROXY_TEXT_BYTES_PER_TOKEN)
    total_bytes = 0
    texts: list[str] = []
    for block in content:
        block_type = _optional_response_field(block, "type")
        if type(block_type) is not str:
            raise _ProxyResponseValidationError
        if block_type != "text":
            continue
        text = _optional_response_field(block, "text")
        if type(text) is not str:
            raise _ProxyResponseValidationError
        if len(text) > MAX_PROXY_TEXT_BLOCK_BYTES:
            raise _ProxyResponseValidationError
        try:
            encoded = text.encode("utf-8")
        except UnicodeEncodeError:
            raise _ProxyResponseValidationError from None
        if len(encoded) > MAX_PROXY_TEXT_BLOCK_BYTES:
            raise _ProxyResponseValidationError
        total_bytes += len(encoded)
        if total_bytes > total_limit:
            raise _ProxyResponseValidationError
        texts.append(text)

    raw_model = _optional_response_field(message, "model")
    if raw_model is None:
        returned_model_id = None
    else:
        try:
            returned_model_id = validate_llm_model_id(raw_model)
        except LLMModelIdError:
            raise _ProxyResponseValidationError from None

    raw_response_id = _optional_response_field(message, "id")
    if raw_response_id is None:
        response_id_sha256 = None
    else:
        if (
            type(raw_response_id) is not str
            or not 1 <= len(raw_response_id) <= MAX_PROXY_RESPONSE_ID_CHARS
            or not raw_response_id.isprintable()
        ):
            raise _ProxyResponseValidationError
        response_id_sha256 = hashlib.sha256(raw_response_id.encode("utf-8")).hexdigest()

    usage = _optional_response_field(message, "usage")
    if usage is not None:
        for required_usage_field in ("input_tokens", "output_tokens"):
            try:
                inspect.getattr_static(usage, required_usage_field)
            except Exception:
                raise _ProxyResponseValidationError from None
    metadata = ProxyCallMetadata(
        status="succeeded",
        attempts=attempts,
        returned_model_id=returned_model_id,
        response_id_sha256=response_id_sha256,
        input_tokens=_optional_usage_count(usage, "input_tokens"),
        output_tokens=_optional_usage_count(usage, "output_tokens"),
        cache_creation_input_tokens=_optional_usage_count(
            usage, "cache_creation_input_tokens"
        ),
        cache_read_input_tokens=_optional_usage_count(usage, "cache_read_input_tokens"),
    )
    return "".join(texts), metadata


class LLMProxyConfigurationError(ValueError):
    """The local credential-backed proxy was not configured fail-closed."""


class LLMModelIdError(ValueError):
    """The LLM implementation did not expose a safe, stable model identifier."""


def _proxy_environment() -> tuple[str, str]:
    base_url = os.environ.get("ANTHROPIC_BASE_URL")
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN")
    if type(base_url) is not str or not base_url.strip():
        raise LLMProxyConfigurationError("local proxy base URL is not configured")
    if type(auth_token) is not str or not auth_token.strip():
        raise LLMProxyConfigurationError("local proxy authentication is not configured")
    try:
        parsed = urlsplit(base_url)
        port = parsed.port
    except ValueError:
        raise LLMProxyConfigurationError("local proxy base URL is invalid") from None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port == 0
    ):
        raise LLMProxyConfigurationError("local proxy base URL is invalid")
    return base_url, auth_token


def proxy_environment_configured() -> bool:
    """Return whether the explicit local proxy URL and token are both valid."""

    try:
        _proxy_environment()
    except LLMProxyConfigurationError:
        return False
    return True


class LLMClient(Protocol):
    @property
    def model_id(self) -> str: ...

    def complete(
        self, *, system: str, user: str, max_tokens: int = 1024, temperature: float = 0.0
    ) -> str: ...


def close_llm_client(llm: LLMClient) -> None:
    """Close an optional client lifecycle without requiring fakes to implement it."""

    try:
        inspect.getattr_static(llm, "close")
    except AttributeError:
        return
    except Exception:
        raise LLMClientCloseError("LLM client close hook could not be read") from None
    try:
        close = object.__getattribute__(llm, "close")
    except Exception:
        raise LLMClientCloseError("LLM client close hook could not be read") from None
    if not callable(close):
        raise LLMClientCloseError("LLM client close hook is not callable")
    try:
        close()
    except Exception:
        raise LLMClientCloseError("LLM client close failed") from None


@contextmanager
def managed_llm_client(llm: LLMClient) -> Iterator[LLMClient]:
    """Own one optional LLM lifecycle across success and failure paths."""

    try:
        yield llm
    finally:
        close_llm_client(llm)


def validate_llm_model_id(value: object) -> str:
    """Return one bounded exact model id, or fail without rendering hostile input."""
    if (
        type(value) is not str
        or not 1 <= len(value) <= MAX_LLM_MODEL_ID_CHARS
        or not value.isprintable()
    ):
        raise LLMModelIdError(
            f"model_id must be a printable exact string of 1..{MAX_LLM_MODEL_ID_CHARS} characters"
        )
    return value


def snapshot_llm_model_id(llm: LLMClient) -> str:
    """Read and validate model provenance before the implementation is invoked."""
    try:
        value = llm.model_id
    except Exception:
        raise LLMModelIdError("model_id could not be read") from None
    return validate_llm_model_id(value)


class FakeLLM:
    """Deterministic LLM stub: returns scripted replies in order, records calls."""

    def __init__(self, scripted: list[str]) -> None:
        self._scripted = list(scripted)
        self._i = 0
        self._calls: list[dict[str, Any]] = []

    @property
    def model_id(self) -> str:
        return FAKE_LLM_MODEL_ID

    def complete(
        self, *, system: str, user: str, max_tokens: int = 1024, temperature: float = 0.0
    ) -> str:
        self._calls.append(
            {"system": system, "user": user, "max_tokens": max_tokens, "temperature": temperature}
        )
        reply = self._scripted[self._i]  # IndexError when the script is exhausted
        self._i += 1
        return reply

    @property
    def calls(self) -> list[dict[str, Any]]:
        return self._calls


class ConstantLLM:
    """Always returns the same reply (never exhausts). For reproducible bench stubs."""

    def __init__(self, reply: str = "{}") -> None:
        self._reply = reply

    @property
    def model_id(self) -> str:
        return CONSTANT_LLM_MODEL_ID

    def complete(
        self, *, system: str, user: str, max_tokens: int = 1024, temperature: float = 0.0
    ) -> str:
        return self._reply


class ProxyLLM:
    """Anthropic-messages client pointed at the local proxy via env vars.

    Reads ANTHROPIC_BASE_URL and ANTHROPIC_AUTH_TOKEN (Bearer). ``anthropic`` is
    imported lazily so the core package does not require it.
    """

    supports_attempt_observation = True

    def __init__(self, model: str = DEFAULT_PROXY_MODEL) -> None:
        model = validate_llm_model_id(model)
        base_url, auth_token = _proxy_environment()
        http_client: Any | None = None
        try:
            import anthropic

            from fretsure.llm._proxy_transport import build_proxy_http_client

            http_client = build_proxy_http_client(
                timeout_seconds=PROXY_REQUEST_TIMEOUT_SECONDS,
                connect_timeout_seconds=PROXY_CONNECT_TIMEOUT_SECONDS,
                max_response_bytes=MAX_PROXY_TRANSPORT_RESPONSE_BYTES,
            )
            self._client = anthropic.Anthropic(
                base_url=base_url,
                auth_token=auth_token,
                max_retries=0,
                timeout=http_client.timeout,
                http_client=http_client,
            )
        except Exception:
            if http_client is not None:
                try:
                    http_client.close()
                except Exception:
                    pass
            raise LLMProxyConfigurationError(
                "LLM proxy client initialization failed"
            ) from None
        assert http_client is not None
        self._http_client_close = http_client.close
        self._http_client_finalizer = weakref.finalize(
            self,
            _best_effort_close,
            self._http_client_close,
        )
        self._model = model
        self._last_call_metadata: ProxyCallMetadata | None = None
        self._closed = False

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def last_call_metadata(self) -> ProxyCallMetadata | None:
        return self._last_call_metadata

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._client.close()
        except Exception:
            _best_effort_close(self._http_client_close)
            raise RuntimeError("LLM proxy client close failed") from None
        try:
            self._http_client_close()
        except Exception:
            raise RuntimeError("LLM proxy client close failed") from None
        self._http_client_finalizer.detach()
        self._closed = True

    def __enter__(self) -> Self:
        if self._closed:
            raise RuntimeError("LLM proxy client is closed")
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def complete(
        self, *, system: str, user: str, max_tokens: int = 1024, temperature: float = 0.0
    ) -> str:
        self._last_call_metadata = None
        if self._closed:
            raise RuntimeError("LLM proxy client is closed")
        system, user, max_tokens, temperature = _validate_proxy_request(
            system,
            user,
            max_tokens,
            temperature,
        )
        for attempt in range(3):  # transient proxy/network errors -> back off and retry
            _before_proxy_attempt(attempt)
            try:
                message = self._client.messages.create(
                    model=self._model,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                text, metadata = _snapshot_proxy_response(
                    message,
                    attempts=attempt + 1,
                    max_tokens=max_tokens,
                )
                self._last_call_metadata = metadata
                _after_proxy_attempt(
                    attempt,
                    status="succeeded",
                    retryable=False,
                )
                return text
            except LLMIntegrityError:
                raise
            except _ProxyResponseValidationError:
                self._last_call_metadata = _failed_proxy_metadata(attempt + 1)
                _after_proxy_attempt(
                    attempt,
                    status="failed",
                    retryable=False,
                )
                raise LLMProxyResponseIntegrityError(
                    "LLM proxy response failed validation"
                ) from None
            except Exception as exc:  # noqa: BLE001 - classified, bounded, and redacted
                self._last_call_metadata = _failed_proxy_metadata(attempt + 1)
                transport_boundary = _contains_proxy_transport_boundary(exc)
                retryable = False if transport_boundary else _is_retryable_proxy_exception(exc)
                _after_proxy_attempt(
                    attempt,
                    status="failed",
                    retryable=retryable,
                )
                if transport_boundary:
                    raise RuntimeError("LLM proxy transport failed validation") from None
                if not retryable:
                    raise RuntimeError("LLM call failed") from None
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
        self._last_call_metadata = _failed_proxy_metadata(3)
        raise RuntimeError("LLM call failed after bounded retries") from None


def extract_json(text: str) -> dict[str, Any]:
    """Extract the first balanced JSON object from ``text`` (tolerates ```json
    fences and surrounding prose). Raises ValueError if none parses."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
            elif ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break  # not valid; try the next '{'
                    if isinstance(obj, dict):
                        return obj
                    break
        start = text.find("{", start + 1)
    raise ValueError(f"no JSON object found in: {text[:80]!r}")
