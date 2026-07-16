import os
from types import SimpleNamespace

import pytest

from fretsure.llm.client import (
    DEFAULT_PROXY_MODEL,
    PROXY_REQUEST_TIMEOUT_SECONDS,
    FakeLLM,
    LLMProxyConfigurationError,
    ProxyLLM,
    extract_json,
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

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8317/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    llm = ProxyLLM()

    assert llm.model_id == DEFAULT_PROXY_MODEL == "gpt-5.6-sol"
    assert llm.complete(system="s", user="u", max_tokens=20) == "MODEL_OK"
    assert request["model"] == "gpt-5.6-sol"
    assert constructor == {
        "base_url": "http://127.0.0.1:8317/v1",
        "auth_token": "test-token",
        "max_retries": 0,
        "timeout": PROXY_REQUEST_TIMEOUT_SECONDS,
    }


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


@pytest.mark.integration
def test_proxy_llm_real_call() -> None:
    if not os.environ.get("ANTHROPIC_BASE_URL"):
        pytest.skip("no local LLM proxy configured (ANTHROPIC_BASE_URL unset)")
    out = ProxyLLM().complete(
        system="You are terse.", user="Reply with exactly the token PROXY_OK.", max_tokens=20
    )
    assert out.strip() == "PROXY_OK"
