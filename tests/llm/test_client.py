import gc
import hashlib
import os
import sys
import traceback
from types import SimpleNamespace
from typing import cast

import anthropic
import httpx
import pytest

from fretsure.llm._proxy_transport import ProxyTransportBoundaryError
from fretsure.llm.client import (
    DEFAULT_PROXY_MODEL,
    MAX_PROXY_CONTENT_BLOCKS,
    MAX_PROXY_OUTPUT_TOKENS,
    MAX_PROXY_REQUEST_FIELD_BYTES,
    MAX_PROXY_RESPONSE_ID_CHARS,
    MAX_PROXY_TEXT_BYTES_PER_TOKEN,
    PROXY_REQUEST_TIMEOUT_SECONDS,
    FakeLLM,
    LLMClientCloseError,
    LLMProxyConfigurationError,
    LLMProxyRequestError,
    ProxyCallMetadata,
    ProxyLLM,
    close_llm_client,
    extract_json,
    managed_llm_client,
    proxy_environment_configured,
)


def test_fake_llm_returns_scripted_in_order() -> None:
    llm = FakeLLM(["a", "b"])
    assert llm.complete(system="s", user="u1") == "a"
    assert llm.complete(system="s", user="u2") == "b"
    assert len(llm.calls) == 2
    assert llm.calls[0]["user"] == "u1"


def test_fake_llm_exhausted_raises() -> None:
    llm = FakeLLM(["only"])
    llm.complete(system="s", user="u")
    with pytest.raises(IndexError):
        llm.complete(system="s", user="u")


def test_extract_json_plain() -> None:
    assert extract_json('{"op": "drop_note", "x": 1}') == {"op": "drop_note", "x": 1}


def test_extract_json_fenced() -> None:
    assert extract_json("here:\n```json\n{\"a\": 2}\n```\ndone") == {"a": 2}


def test_extract_json_with_prefix_and_suffix() -> None:
    assert extract_json('Sure! {"a": 3, "b": [1, 2]} done') == {"a": 3, "b": [1, 2]}


def test_extract_json_nested_braces() -> None:
    assert extract_json('x {"a": {"b": 1}, "c": 2} y') == {"a": {"b": 1}, "c": 2}


def test_extract_json_bad_raises() -> None:
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_proxy_llm_forwards_canonical_gpt_5_6_sol_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request: dict[str, object] = {}

    class FakeMessages:
        def create(self, **kwargs: object) -> object:
            request.update(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="MODEL_OK")])

    constructor: dict[str, object] = {}

    class FakeAnthropic:
        def __init__(self, **kwargs: object) -> None:
            constructor.update(kwargs)
            self.messages = FakeMessages()
            self.http_client = kwargs["http_client"]

        def close(self) -> None:
            client = self.http_client
            assert isinstance(client, httpx.Client)
            client.close()

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8317/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    llm = ProxyLLM()

    assert llm.model_id == DEFAULT_PROXY_MODEL == "gpt-5.6-sol"
    assert llm.complete(system="s", user="u", max_tokens=20) == "MODEL_OK"
    assert request["model"] == "gpt-5.6-sol"
    http_client = constructor.pop("http_client")
    assert isinstance(http_client, httpx.Client)
    assert http_client.follow_redirects is False
    assert http_client._trust_env is False
    assert http_client.headers["accept-encoding"] == "identity"
    assert http_client.timeout.read == PROXY_REQUEST_TIMEOUT_SECONDS
    assert constructor == {
        "base_url": "http://127.0.0.1:8317/v1",
        "auth_token": "test-token",
        "max_retries": 0,
        "timeout": http_client.timeout,
    }
    assert llm.last_call_metadata == ProxyCallMetadata(
        status="succeeded",
        attempts=1,
        returned_model_id=None,
        response_id_sha256=None,
        input_tokens=None,
        output_tokens=None,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
    )
    llm.close()


def test_proxy_llm_snapshots_bounded_provider_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response_id = "msg_test_123"

    class FakeMessages:
        def create(self, **_kwargs: object) -> object:
            return SimpleNamespace(
                id=response_id,
                model="gpt-5.6-sol-20260717",
                content=[SimpleNamespace(type="text", text="MODEL_OK")],
                usage=SimpleNamespace(
                    input_tokens=11,
                    output_tokens=3,
                    cache_creation_input_tokens=2,
                    cache_read_input_tokens=5,
                ),
            )

    class FakeAnthropic:
        def __init__(self, **_kwargs: object) -> None:
            self.messages = FakeMessages()

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8317/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    llm = ProxyLLM()

    assert llm.complete(system="s", user="u", max_tokens=20) == "MODEL_OK"
    assert llm.last_call_metadata == ProxyCallMetadata(
        status="succeeded",
        attempts=1,
        returned_model_id="gpt-5.6-sol-20260717",
        response_id_sha256=hashlib.sha256(response_id.encode("utf-8")).hexdigest(),
        input_tokens=11,
        output_tokens=3,
        cache_creation_input_tokens=2,
        cache_read_input_tokens=5,
    )


@pytest.mark.parametrize(
    "message",
    [
        SimpleNamespace(
            content=[SimpleNamespace(type="text", text="x")]
            * (MAX_PROXY_CONTENT_BLOCKS + 1)
        ),
        SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text",
                    text="x" * (20 * MAX_PROXY_TEXT_BYTES_PER_TOKEN + 1),
                )
            ]
        ),
        SimpleNamespace(
            id="x" * (MAX_PROXY_RESPONSE_ID_CHARS + 1),
            content=[SimpleNamespace(type="text", text="ok")],
        ),
        SimpleNamespace(
            content=[SimpleNamespace(type="text", text="ok")],
            usage=SimpleNamespace(input_tokens=True),
        ),
        SimpleNamespace(
            content=[SimpleNamespace(type=object(), text="must not be ignored")],
        ),
    ],
)
def test_proxy_llm_rejects_unbounded_or_malformed_provider_response_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    message: object,
) -> None:
    calls = 0

    class FakeMessages:
        def create(self, **_kwargs: object) -> object:
            nonlocal calls
            calls += 1
            return message

    class FakeAnthropic:
        def __init__(self, **_kwargs: object) -> None:
            self.messages = FakeMessages()

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8317/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    llm = ProxyLLM()

    with pytest.raises(RuntimeError, match="proxy response failed validation"):
        llm.complete(system="s", user="u", max_tokens=20)

    assert calls == 1
    assert llm.last_call_metadata is not None
    assert llm.last_call_metadata.status == "failed"
    assert llm.last_call_metadata.attempts == 1


def test_declared_optional_metadata_attribute_error_is_invalid_not_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Message:
        content = [SimpleNamespace(type="text", text="ok")]

        @property
        def model(self) -> object:
            raise AttributeError("SECRET model getter")

    class FakeMessages:
        def create(self, **_kwargs: object) -> object:
            return Message()

    class FakeAnthropic:
        def __init__(self, **_kwargs: object) -> None:
            self.messages = FakeMessages()

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8317/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)

    with pytest.raises(RuntimeError) as caught:
        ProxyLLM().complete(system="s", user="u", max_tokens=20)

    assert str(caught.value) == "LLM proxy response failed validation"
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize(
    ("system", "user", "max_tokens", "temperature"),
    [
        (object(), "u", 20, 0.0),
        ("s", object(), 20, 0.0),
        ("\ud800", "u", 20, 0.0),
        ("s", "x" * (MAX_PROXY_REQUEST_FIELD_BYTES + 1), 20, 0.0),
        ("s", "u", True, 0.0),
        ("s", "u", 0, 0.0),
        ("s", "u", MAX_PROXY_OUTPUT_TOKENS + 1, 0.0),
        ("s", "u", 20, True),
        ("s", "u", 20, float("nan")),
        ("s", "u", 20, float("inf")),
        ("s", "u", 20, 10**400),
        ("s", "u", 20, -0.1),
        ("s", "u", 20, 1.1),
    ],
)
def test_proxy_llm_rejects_invalid_request_before_network(
    monkeypatch: pytest.MonkeyPatch,
    system: object,
    user: object,
    max_tokens: object,
    temperature: object,
) -> None:
    calls = 0

    class FakeMessages:
        def create(self, **_kwargs: object) -> object:
            nonlocal calls
            calls += 1
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])

    class FakeAnthropic:
        def __init__(self, **_kwargs: object) -> None:
            self.messages = FakeMessages()

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8317/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    llm = ProxyLLM()

    with pytest.raises(LLMProxyRequestError):
        llm.complete(
            system=cast(str, system),
            user=cast(str, user),
            max_tokens=cast(int, max_tokens),
            temperature=cast(float, temperature),
        )

    assert calls == 0
    assert llm.last_call_metadata is None


def test_proxy_llm_clears_previous_metadata_before_rejecting_next_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMessages:
        def create(self, **_kwargs: object) -> object:
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])

    class FakeAnthropic:
        def __init__(self, **_kwargs: object) -> None:
            self.messages = FakeMessages()

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8317/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    llm = ProxyLLM()

    assert llm.complete(system="s", user="u") == "ok"
    assert llm.last_call_metadata is not None
    with pytest.raises(LLMProxyRequestError):
        llm.complete(system="s", user="u", max_tokens=0)
    assert llm.last_call_metadata is None


@pytest.mark.parametrize(
    ("base_url", "token"),
    [
        (None, "token"),
        ("http://127.0.0.1:8317/v1", None),
        ("https://api.anthropic.com", "token"),
        ("http://localhost.evil.example/v1", "token"),
        ("http://user:secret@127.0.0.1:8317/v1", "token"),
    ],
)
def test_proxy_llm_rejects_missing_or_nonlocal_configuration_before_sdk_init(
    monkeypatch: pytest.MonkeyPatch,
    base_url: str | None,
    token: str | None,
) -> None:
    if base_url is None:
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    if token is None:
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", token)

    called = False

    class ForbiddenAnthropic:
        def __init__(self, **_kwargs: object) -> None:
            nonlocal called
            called = True

    monkeypatch.setattr("anthropic.Anthropic", ForbiddenAnthropic)
    assert proxy_environment_configured() is False
    with pytest.raises(LLMProxyConfigurationError):
        ProxyLLM()
    assert called is False


def test_proxy_llm_redacts_sdk_constructor_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingAnthropic:
        def __init__(self, **kwargs: object) -> None:
            raise RuntimeError(f"constructor leaked {kwargs['auth_token']}")

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8317/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-secret-token")
    monkeypatch.setattr("anthropic.Anthropic", FailingAnthropic)

    with pytest.raises(LLMProxyConfigurationError) as exc_info:
        ProxyLLM()

    assert str(exc_info.value) == "LLM proxy client initialization failed"
    assert "test-secret-token" not in str(exc_info.value)


def _api_status_error(
    status_code: int,
    retry_header: str | None = None,
) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "http://127.0.0.1:8317/v1/messages")
    headers = {} if retry_header is None else {"x-should-retry": retry_header}
    response = httpx.Response(status_code, headers=headers, request=request)
    return anthropic.APIStatusError(
        "SECRET provider body",
        response=response,
        body={"secret": "SECRET provider body"},
    )


@pytest.mark.parametrize(
    ("status_code", "retry_header", "expected_retry"),
    [
        (307, None, False),
        (400, None, False),
        (401, None, False),
        (403, None, False),
        (404, None, False),
        (413, None, False),
        (422, None, False),
        (408, None, True),
        (409, None, True),
        (429, None, True),
        (500, None, True),
        (503, None, True),
        (529, None, True),
        (600, None, False),
        (400, "true", True),
        (503, "false", False),
    ],
)
def test_proxy_llm_retries_only_whitelisted_status_failures(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    retry_header: str | None,
    expected_retry: bool,
) -> None:
    calls = 0
    sleeps: list[float] = []

    class FakeMessages:
        def create(self, **_kwargs: object) -> object:
            nonlocal calls
            calls += 1
            if calls < 3 or not expected_retry:
                raise _api_status_error(status_code, retry_header)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])

    class FakeAnthropic:
        def __init__(self, **_kwargs: object) -> None:
            self.messages = FakeMessages()

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8317/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    monkeypatch.setattr("fretsure.llm.client.time.sleep", sleeps.append)
    llm = ProxyLLM()

    if expected_retry:
        assert llm.complete(system="s", user="u", max_tokens=20) == "ok"
        assert calls == 3
        assert sleeps == [0.5, 1.0]
        assert llm.last_call_metadata is not None
        assert llm.last_call_metadata.status == "succeeded"
        assert llm.last_call_metadata.attempts == 3
    else:
        with pytest.raises(RuntimeError) as exc_info:
            llm.complete(system="s", user="u", max_tokens=20)
        assert str(exc_info.value) == "LLM call failed"
        assert calls == 1
        assert sleeps == []
        assert llm.last_call_metadata is not None
        assert llm.last_call_metadata.attempts == 1
        rendered = "".join(
            traceback.format_exception(
                type(exc_info.value),
                exc_info.value,
                exc_info.value.__traceback__,
            )
        )
        assert "SECRET provider body" not in rendered


def test_proxy_llm_retries_connection_failures_then_redacts_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []
    request = httpx.Request("POST", "http://127.0.0.1:8317/v1/messages")

    class FakeMessages:
        def create(self, **_kwargs: object) -> object:
            nonlocal calls
            calls += 1
            raise anthropic.APIConnectionError(message="SECRET transport", request=request)

    class FakeAnthropic:
        def __init__(self, **_kwargs: object) -> None:
            self.messages = FakeMessages()

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8317/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    monkeypatch.setattr("fretsure.llm.client.time.sleep", sleeps.append)
    llm = ProxyLLM()

    with pytest.raises(RuntimeError) as exc_info:
        llm.complete(system="s", user="u", max_tokens=20)

    assert str(exc_info.value) == "LLM call failed after bounded retries"
    assert calls == 3
    assert sleeps == [0.5, 1.0]
    assert llm.last_call_metadata is not None
    assert llm.last_call_metadata.status == "failed"
    assert llm.last_call_metadata.attempts == 3
    rendered = "".join(
        traceback.format_exception(
            type(exc_info.value),
            exc_info.value,
            exc_info.value.__traceback__,
        )
    )
    assert "SECRET transport" not in rendered


def test_proxy_llm_does_not_retry_generic_sdk_retryable_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    class FakeMessages:
        def create(self, **_kwargs: object) -> object:
            nonlocal calls
            calls += 1
            raise anthropic.RetryableError("SECRET marker")

    class FakeAnthropic:
        def __init__(self, **_kwargs: object) -> None:
            self.messages = FakeMessages()

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8317/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    llm = ProxyLLM()

    with pytest.raises(RuntimeError, match="^LLM call failed$"):
        llm.complete(system="s", user="u", max_tokens=20)
    assert calls == 1


@pytest.mark.parametrize("wrapped", [False, True])
def test_proxy_llm_never_retries_transport_boundary_failure(
    monkeypatch: pytest.MonkeyPatch,
    wrapped: bool,
) -> None:
    calls = 0
    sleeps: list[float] = []
    request = httpx.Request("POST", "http://127.0.0.1:8317/v1/messages")

    class FakeMessages:
        def create(self, **_kwargs: object) -> object:
            nonlocal calls
            calls += 1
            try:
                raise ProxyTransportBoundaryError("SECRET oversized response")
            except ProxyTransportBoundaryError as error:
                if wrapped:
                    raise anthropic.APIConnectionError(request=request) from error
                raise

    class FakeAnthropic:
        def __init__(self, **_kwargs: object) -> None:
            self.messages = FakeMessages()

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8317/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    monkeypatch.setattr("fretsure.llm.client.time.sleep", sleeps.append)
    llm = ProxyLLM()

    with pytest.raises(RuntimeError) as exc_info:
        llm.complete(system="s", user="u", max_tokens=20)

    assert str(exc_info.value) == "LLM proxy transport failed validation"
    assert calls == 1
    assert sleeps == []
    assert llm.last_call_metadata is not None
    assert llm.last_call_metadata.attempts == 1
    rendered = "".join(
        traceback.format_exception(
            type(exc_info.value),
            exc_info.value,
            exc_info.value.__traceback__,
        )
    )
    assert "SECRET oversized response" not in rendered


def test_proxy_llm_close_is_idempotent_and_blocks_future_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closes = 0

    class FakeMessages:
        def create(self, **_kwargs: object) -> object:
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])

    class FakeAnthropic:
        def __init__(self, **_kwargs: object) -> None:
            self.messages = FakeMessages()

        def close(self) -> None:
            nonlocal closes
            closes += 1

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8317/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    llm = ProxyLLM()

    llm.close()
    llm.close()
    with pytest.raises(RuntimeError, match="client is closed"):
        llm.complete(system="s", user="u")

    assert closes == 1


def test_proxy_llm_close_failure_can_be_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closes = 0

    class FakeAnthropic:
        def __init__(self, **_kwargs: object) -> None:
            self.messages = SimpleNamespace()

        def close(self) -> None:
            nonlocal closes
            closes += 1
            if closes == 1:
                raise RuntimeError("SECRET close failure")

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8317/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    llm = ProxyLLM()

    with pytest.raises(RuntimeError) as exc_info:
        llm.close()
    assert str(exc_info.value) == "LLM proxy client close failed"
    llm.close()
    llm.close()
    assert closes == 2


def test_proxy_llm_gc_finalizer_swallows_raw_transport_close_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unraisable: list[object] = []

    class BadHTTPClient:
        timeout = 1.0

        def close(self) -> None:
            raise RuntimeError("SECRET finalizer transport")

    class FakeAnthropic:
        def __init__(self, **_kwargs: object) -> None:
            self.messages = SimpleNamespace()

        def close(self) -> None:
            return None

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8317/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    monkeypatch.setattr(
        "fretsure.llm._proxy_transport.build_proxy_http_client",
        lambda **_kwargs: BadHTTPClient(),
    )
    monkeypatch.setattr(sys, "unraisablehook", unraisable.append)

    llm = ProxyLLM()
    del llm
    gc.collect()

    assert unraisable == []


def test_proxy_llm_failed_explicit_close_keeps_gc_finalizer_armed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport_closes = 0
    unraisable: list[object] = []

    class FlakyHTTPClient:
        timeout = 1.0

        def close(self) -> None:
            nonlocal transport_closes
            transport_closes += 1
            if transport_closes == 1:
                raise RuntimeError("SECRET first transport close")

    class FailingAnthropic:
        def __init__(self, **_kwargs: object) -> None:
            self.messages = SimpleNamespace()

        def close(self) -> None:
            raise RuntimeError("SECRET SDK close")

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8317/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setattr("anthropic.Anthropic", FailingAnthropic)
    monkeypatch.setattr(
        "fretsure.llm._proxy_transport.build_proxy_http_client",
        lambda **_kwargs: FlakyHTTPClient(),
    )
    monkeypatch.setattr(sys, "unraisablehook", unraisable.append)

    llm = ProxyLLM()
    with pytest.raises(RuntimeError) as caught:
        llm.close()
    assert str(caught.value) == "LLM proxy client close failed"
    assert llm._http_client_finalizer.alive is True

    del caught
    del llm
    gc.collect()

    assert transport_closes == 2
    assert unraisable == []


def test_managed_llm_client_closes_on_success_and_failure() -> None:
    class ClosableFake(FakeLLM):
        def __init__(self) -> None:
            super().__init__([])
            self.closes = 0

        def close(self) -> None:
            self.closes += 1

    succeeded = ClosableFake()
    with managed_llm_client(succeeded) as owned:
        assert owned is succeeded
    assert succeeded.closes == 1

    failed = ClosableFake()
    with pytest.raises(ValueError, match="body failed"):
        with managed_llm_client(failed):
            raise ValueError("body failed")
    assert failed.closes == 1


def test_close_llm_client_is_optional_and_redacts_bad_hooks() -> None:
    close_llm_client(FakeLLM([]))

    class BadClose(FakeLLM):
        def close(self) -> None:
            raise RuntimeError("SECRET close hook")

    with pytest.raises(LLMClientCloseError) as exc_info:
        close_llm_client(BadClose([]))
    assert str(exc_info.value) == "LLM client close failed"
    assert "SECRET" not in str(exc_info.value)

    class AttributeErrorClose(FakeLLM):
        @property
        def close(self) -> object:
            raise AttributeError("SECRET close getter")

    with pytest.raises(LLMClientCloseError) as exc_info:
        close_llm_client(AttributeErrorClose([]))
    assert str(exc_info.value) == "LLM client close hook could not be read"
    assert "SECRET" not in str(exc_info.value)


@pytest.mark.integration
def test_proxy_llm_real_call() -> None:
    if not os.environ.get("ANTHROPIC_BASE_URL"):
        pytest.skip("no local LLM proxy configured (ANTHROPIC_BASE_URL unset)")
    out = ProxyLLM().complete(
        system="You are terse.", user="Reply with exactly the token PROXY_OK.", max_tokens=20
    )
    assert out.strip() == "PROXY_OK"
