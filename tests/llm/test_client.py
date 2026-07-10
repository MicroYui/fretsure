import os

import pytest

from fretsure.llm.client import FakeLLM, extract_json


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


@pytest.mark.integration
def test_proxy_llm_real_call() -> None:
    if not os.environ.get("ANTHROPIC_BASE_URL"):
        pytest.skip("no local LLM proxy configured (ANTHROPIC_BASE_URL unset)")
    from fretsure.llm.client import ProxyLLM

    out = ProxyLLM().complete(
        system="You are terse.", user="Reply with exactly the token PROXY_OK.", max_tokens=20
    )
    assert isinstance(out, str) and out.strip()
