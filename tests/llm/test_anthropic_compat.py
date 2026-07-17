"""Retry-contract regression run against the declared Anthropic minimum in CI."""

import anthropic
import httpx
import pytest

from fretsure.llm.client import _is_retryable_proxy_exception


def _status_error(
    status_code: int,
    retry_header: str | None = None,
) -> anthropic.APIStatusError:
    request = httpx.Request("POST", "http://127.0.0.1:8317/v1/messages")
    headers = {} if retry_header is None else {"x-should-retry": retry_header}
    response = httpx.Response(status_code, headers=headers, request=request)
    return anthropic.APIStatusError("redacted fixture", response=response, body=None)


@pytest.mark.parametrize(
    ("status_code", "retry_header", "expected"),
    [
        (408, None, True),
        (409, None, True),
        (429, None, True),
        (500, None, True),
        (599, None, True),
        (600, None, False),
        (400, "true", True),
        (503, "false", False),
    ],
)
def test_retry_classifier_uses_anthropic_0_40_status_surface(
    status_code: int,
    retry_header: str | None,
    expected: bool,
) -> None:
    assert _is_retryable_proxy_exception(_status_error(status_code, retry_header)) is expected


def test_retry_classifier_uses_anthropic_0_40_connection_surface() -> None:
    request = httpx.Request("POST", "http://127.0.0.1:8317/v1/messages")
    assert _is_retryable_proxy_exception(anthropic.APIConnectionError(request=request)) is True
    assert _is_retryable_proxy_exception(RuntimeError("generic SDK marker")) is False
