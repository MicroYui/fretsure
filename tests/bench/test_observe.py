import hashlib
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from typing import cast

import anthropic
import httpx
import pytest

from fretsure.bench.observe import (
    MAX_ELAPSED_MICROSECONDS,
    MAX_OBSERVATION_IDENTIFIER_CHARS,
    CallContext,
    CallFailureCode,
    CallIntent,
    CallResult,
    CallSequence,
    CallStage,
    InMemoryObservationSink,
    ObservationContextError,
    ObservationInputError,
    ObservationRequestGuard,
    ObservationSinkError,
    ObservedCallError,
    ObservingLLM,
    ProviderObservation,
    call_scope,
    current_call_context,
    visible_text_sha256,
)
from fretsure.llm.client import (
    MAX_PROXY_REQUEST_FIELD_BYTES,
    MAX_PROXY_USAGE_TOKENS,
    ConstantLLM,
    FakeLLM,
    LLMClient,
    LLMIntegrityError,
    ProxyCallMetadata,
    ProxyLLM,
)


def _context(
    logical_call_id: str = "item-1:0:proposal",
    call_index: int = 0,
    *,
    stage: CallStage = CallStage.PROPOSAL,
    stage_ordinal: int = 0,
) -> CallContext:
    return CallContext(
        run_id="run-1",
        logical_call_id=logical_call_id,
        call_index=call_index,
        item_id="item-1",
        family_id="family-1",
        cluster_id="cluster-1",
        pair_id="pair-1",
        sample_index=0,
        candidate_index=0,
        stage=stage,
        stage_ordinal=stage_ordinal,
    )


class _Clock:
    def __init__(self, *values: object) -> None:
        self._values = iter(values)

    def __call__(self) -> int:
        value = next(self._values)
        if isinstance(value, BaseException):
            raise value
        return cast(int, value)


class _Delegate:
    model_id = "test-model"

    def __init__(self, reply: object = "reply-secret") -> None:
        self.reply = reply
        self.calls = 0

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        del system, user, max_tokens, temperature
        self.calls += 1
        return cast(str, self.reply)


class _FailingDelegate(_Delegate):
    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        del system, user, max_tokens, temperature
        self.calls += 1
        raise RuntimeError("credential=delegate-secret")


class _MetadataDelegate(_Delegate):
    def __init__(self, reply: object, metadata: object) -> None:
        super().__init__(reply)
        self._metadata = metadata

    @property
    def last_call_metadata(self) -> object:
        return self._metadata


class _IntentCheckingDelegate(_Delegate):
    def __init__(self, sink: InMemoryObservationSink) -> None:
        super().__init__("ok")
        self._sink = sink

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        assert len(self._sink.intents) == 1
        assert self._sink.results == ()
        return super().complete(
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
        )


def test_success_records_only_domain_separated_digests_and_deterministic_time() -> None:
    system_secret = "system-secret-DO-NOT-PERSIST"
    user_secret = "user-secret-DO-NOT-PERSIST"
    reply_secret = "reply-secret-DO-NOT-PERSIST"
    delegate = _Delegate(reply_secret)
    sink = InMemoryObservationSink()
    observed = ObservingLLM(delegate, sink, clock_ns=_Clock(1_000_000, 1_004_999))

    with call_scope(_context()):
        reply = observed.complete(
            system=system_secret,
            user=user_secret,
            max_tokens=100,
            temperature=0.8,
        )

    assert reply == reply_secret
    assert delegate.calls == 1
    assert len(sink.events) == 2
    intent = sink.intents[0]
    result = sink.results[0]
    assert (intent.run_id, intent.cluster_id, intent.pair_id) == (
        "run-1",
        "cluster-1",
        "pair-1",
    )
    assert intent.system_sha256 == hashlib.sha256(
        b"fretsure:benchmark-call-system@0.1.0\0" + system_secret.encode()
    ).hexdigest()
    assert intent.user_sha256 == visible_text_sha256(
        "user", user_secret, max_bytes=len(user_secret)
    )
    assert result.reply_sha256 == visible_text_sha256(
        "reply", reply_secret, max_bytes=len(reply_secret)
    )
    assert result.elapsed_microseconds == 4
    assert result.status == "succeeded"
    assert result.provider.available is False
    assert len(sink.attempt_intents) == len(sink.attempt_results) == 1
    assert sink.attempt_intents[0].request_sha256 == intent.request_sha256
    assert sink.attempt_intents[0].reserved_output_tokens == 100
    assert sink.attempt_results[0].status == "succeeded"
    assert sink.attempt_results[0].retryable is False
    rendered = repr(sink.events)
    assert system_secret not in rendered
    assert user_secret not in rendered
    assert reply_secret not in rendered
    assert visible_text_sha256("system", "same") != visible_text_sha256("reply", "same")


def test_intent_is_written_before_delegate_invocation() -> None:
    sink = InMemoryObservationSink()
    delegate = _IntentCheckingDelegate(sink)
    observed = ObservingLLM(delegate, sink, clock_ns=_Clock(0, 0))

    with call_scope(_context()):
        assert observed.complete(system="s", user="u") == "ok"


def test_request_guard_uses_exact_utf8_bytes_and_precedes_all_call_activity() -> None:
    class RequestCeilingExceeded(LLMIntegrityError):
        pass

    ceiling = len("é界".encode())
    rejected = RequestCeilingExceeded("request exceeds the test ceiling")
    guarded_requests: list[tuple[bytes, bytes, int]] = []

    def guard(system_bytes: bytes, user_bytes: bytes, max_tokens: int, /) -> None:
        guarded_requests.append((system_bytes, user_bytes, max_tokens))
        if len(system_bytes) + len(user_bytes) > ceiling:
            raise rejected

    accepted_delegate = _Delegate("ok")
    accepted_sink = InMemoryObservationSink()
    accepted = ObservingLLM(
        accepted_delegate,
        accepted_sink,
        clock_ns=_Clock(0, 0),
        request_guard=guard,
    )
    with call_scope(_context()):
        assert accepted.complete(system="é", user="界", max_tokens=17) == "ok"

    assert guarded_requests == [("é".encode(), "界".encode(), 17)]
    assert accepted_delegate.calls == 1
    assert len(accepted_sink.intents) == len(accepted_sink.attempt_intents) == 1

    rejected_delegate = _Delegate("must-not-run")
    rejected_sink = InMemoryObservationSink()

    def unexpected_clock() -> int:
        raise AssertionError("request guard must run before the observation clock")

    guarded = ObservingLLM(
        rejected_delegate,
        rejected_sink,
        clock_ns=unexpected_clock,
        request_guard=guard,
    )
    with call_scope(_context()):
        with pytest.raises(RequestCeilingExceeded) as caught:
            guarded.complete(system="é", user="界x", max_tokens=17)

    assert caught.value is rejected
    assert guarded_requests[-1] == ("é".encode(), "界x".encode(), 17)
    assert rejected_delegate.calls == 0
    assert rejected_sink.journal_events == ()


def test_request_guard_must_be_null_or_callable() -> None:
    with pytest.raises(ObservationInputError) as caught:
        ObservingLLM(
            _Delegate("ok"),
            InMemoryObservationSink(),
            request_guard=cast(ObservationRequestGuard, object()),
        )

    assert caught.value.field == "request_guard"


def test_request_digest_binds_the_requested_model() -> None:
    class OtherModel(_Delegate):
        model_id = "other-model"

    first_sink = InMemoryObservationSink()
    second_sink = InMemoryObservationSink()
    first = ObservingLLM(_Delegate("ok"), first_sink, clock_ns=_Clock(0, 0))
    second = ObservingLLM(OtherModel("ok"), second_sink, clock_ns=_Clock(0, 0))
    with call_scope(_context()):
        first.complete(system="s", user="u")
    with call_scope(_context()):
        second.complete(system="s", user="u")
    assert first_sink.intents[0].request_sha256 != second_sink.intents[0].request_sha256


def test_request_digest_accepts_a_valid_unicode_model_id() -> None:
    class UnicodeModel(_Delegate):
        model_id = "modèle"

    sink = InMemoryObservationSink()
    observed = ObservingLLM(UnicodeModel("ok"), sink, clock_ns=_Clock(0, 0))

    with call_scope(_context()):
        assert observed.complete(system="s", user="u") == "ok"

    assert sink.intents[0].requested_model_id == "modèle"


def test_call_intent_rejects_a_digest_not_bound_to_its_request_fields() -> None:
    sink = InMemoryObservationSink()
    observed = ObservingLLM(_Delegate("ok"), sink, clock_ns=_Clock(0, 0))
    with call_scope(_context()):
        observed.complete(system="s", user="u", max_tokens=11, temperature=0.8)

    with pytest.raises(ObservationInputError) as caught:
        replace(sink.intents[0], request_sha256="0" * 64)

    assert caught.value.field == "request_sha256"


def test_records_exact_proxy_usage_and_private_response_digest() -> None:
    response_digest = "a" * 64
    metadata = ProxyCallMetadata(
        status="succeeded",
        attempts=1,
        returned_model_id="gpt-5.6-sol",
        response_id_sha256=response_digest,
        input_tokens=101,
        output_tokens=23,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=7,
    )
    sink = InMemoryObservationSink()
    observed = ObservingLLM(
        _MetadataDelegate("ok", metadata),
        sink,
        clock_ns=_Clock(10, 2_010),
    )

    with call_scope(_context()):
        observed.complete(system="s", user="u")

    provider = sink.results[0].provider
    assert provider.available is True
    assert provider.attempts == 1
    assert provider.retries == 0
    assert provider.returned_model_id == "gpt-5.6-sol"
    assert provider.response_id_sha256 == response_digest
    assert provider.input_tokens == 101
    assert provider.output_tokens == 23
    assert provider.cache_creation_input_tokens is None
    assert provider.cache_read_input_tokens == 7


def test_proxy_attempts_are_reserved_and_terminated_before_each_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = InMemoryObservationSink()
    calls = 0
    sleeps: list[float] = []
    request = httpx.Request("POST", "http://127.0.0.1:8317/v1/messages")

    class FakeMessages:
        def create(self, **_kwargs: object) -> object:
            nonlocal calls
            assert len(sink.attempt_intents) == calls + 1
            assert len(sink.attempt_results) == calls
            calls += 1
            if calls < 3:
                raise anthropic.APIConnectionError(request=request)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])

    class FakeAnthropic:
        def __init__(self, **kwargs: object) -> None:
            self.messages = FakeMessages()
            self.http_client = cast(httpx.Client, kwargs["http_client"])

        def close(self) -> None:
            self.http_client.close()

    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:8317/v1")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setattr("anthropic.Anthropic", FakeAnthropic)
    monkeypatch.setattr("fretsure.llm.client.time.sleep", sleeps.append)
    observed = ObservingLLM(ProxyLLM(), sink, clock_ns=_Clock(0, 1_000))

    with call_scope(_context()):
        assert observed.complete(system="s", user="u", max_tokens=20) == "ok"

    assert calls == 3
    assert sleeps == [0.5, 1.0]
    assert [intent.attempt_index for intent in sink.attempt_intents] == [0, 1, 2]
    assert [intent.reserved_output_tokens for intent in sink.attempt_intents] == [20, 20, 20]
    assert [result.status for result in sink.attempt_results] == [
        "failed",
        "failed",
        "succeeded",
    ]
    assert [result.retryable for result in sink.attempt_results] == [True, True, False]
    assert sink.results[0].provider.attempts == 3
    assert sink.results[0].provider.retries == 2
    assert [type(event).__name__ for event in sink.journal_events] == [
        "CallIntent",
        "AttemptIntent",
        "AttemptResult",
        "AttemptIntent",
        "AttemptResult",
        "AttemptIntent",
        "AttemptResult",
        "CallResult",
    ]
    observed.close()


@pytest.mark.parametrize("delegate", [_Delegate("ok"), FakeLLM(["ok"]), ConstantLLM("ok")])
def test_generic_llms_record_provider_metadata_as_unavailable(delegate: object) -> None:
    sink = InMemoryObservationSink()
    observed = ObservingLLM(
        cast(LLMClient, delegate),
        sink,
        clock_ns=_Clock(0, 0),
    )

    with call_scope(_context()):
        observed.complete(system="s", user="u")

    assert sink.results[0].provider.available is False
    assert sink.results[0].provider.attempts is None
    assert sink.results[0].provider.input_tokens is None


def test_delegate_failure_is_redacted_and_has_one_terminal_record() -> None:
    delegate = _FailingDelegate()
    sink = InMemoryObservationSink()
    observed = ObservingLLM(delegate, sink, clock_ns=_Clock(0, 9_999))

    with call_scope(_context()):
        with pytest.raises(ObservedCallError) as caught:
            observed.complete(system="secret-system", user="secret-user")

    assert caught.value.code is CallFailureCode.DELEGATE_FAILED
    assert not isinstance(caught.value, LLMIntegrityError)
    assert "delegate-secret" not in str(caught.value)
    assert delegate.calls == 1
    assert sink.has_open_intent is False
    assert sink.results[0].status == "failed"
    assert sink.results[0].failure_code is CallFailureCode.DELEGATE_FAILED
    assert sink.results[0].reply_sha256 is None
    assert "delegate-secret" not in repr(sink.events)


def test_nested_scopes_restore_outer_context_and_keep_explicit_order() -> None:
    sink = InMemoryObservationSink()
    observed = ObservingLLM(_Delegate("ok"), sink, clock_ns=_Clock(0, 0, 0, 0))
    outer = _context("outer", 1, stage=CallStage.REPAIR, stage_ordinal=1)
    inner = _context("inner", 0, stage=CallStage.PROPOSAL)

    assert current_call_context() is None
    with call_scope(outer):
        assert current_call_context() == outer
        with call_scope(inner):
            assert current_call_context() == inner
            observed.complete(system="s", user="u")
        assert current_call_context() == outer
        observed.complete(system="s", user="u")
    assert current_call_context() is None
    assert [intent.logical_call_id for intent in sink.intents] == ["inner", "outer"]
    assert [intent.call_index for intent in sink.intents] == [0, 1]


def test_missing_context_fails_before_delegate_or_sink() -> None:
    delegate = _Delegate("ok")
    sink = InMemoryObservationSink()
    observed = ObservingLLM(delegate, sink, clock_ns=_Clock(0, 0))

    with pytest.raises(ObservationContextError):
        observed.complete(system="s", user="u")

    assert delegate.calls == 0
    assert sink.events == ()


def test_duplicate_logical_call_fails_closed_before_second_delegate_call() -> None:
    delegate = _Delegate("ok")
    sink = InMemoryObservationSink()
    observed = ObservingLLM(delegate, sink, clock_ns=_Clock(0, 0))
    context = _context()

    with call_scope(context):
        observed.complete(system="s", user="u")
    with call_scope(context):
        with pytest.raises(ObservationSinkError) as caught:
            observed.complete(system="s", user="u")

    assert caught.value.phase == "intent"
    assert delegate.calls == 1
    assert len(sink.results) == 1


def test_nonsequential_call_index_fails_closed_before_delegate() -> None:
    delegate = _Delegate("ok")
    sink = InMemoryObservationSink()
    observed = ObservingLLM(delegate, sink, clock_ns=_Clock(0, 0))

    with call_scope(_context("gap", 1)):
        with pytest.raises(ObservationSinkError) as caught:
            observed.complete(system="s", user="u")

    assert caught.value.phase == "intent"
    assert delegate.calls == 0


class _FailingSink:
    def __init__(self, phase: str) -> None:
        self.phase = phase
        self.intents = 0

    def write_intent(self, intent: CallIntent) -> None:
        del intent
        self.intents += 1
        if self.phase == "intent":
            raise RuntimeError("sink-secret")

    def write_attempt_intent(self, intent: object) -> None:
        del intent
        if self.phase == "attempt_intent":
            raise RuntimeError("sink-secret")

    def write_attempt_result(self, result: object) -> None:
        del result
        if self.phase == "attempt_terminal":
            raise RuntimeError("sink-secret")

    def write_result(self, result: CallResult) -> None:
        del result
        if self.phase == "terminal":
            raise RuntimeError("sink-secret")


def test_intent_sink_failure_makes_zero_delegate_calls_and_is_redacted() -> None:
    delegate = _Delegate("ok")
    observed = ObservingLLM(
        delegate,
        _FailingSink("intent"),
        clock_ns=_Clock(0, 0),
    )

    with call_scope(_context()):
        with pytest.raises(ObservationSinkError) as caught:
            observed.complete(system="s", user="u")

    assert caught.value.phase == "intent"
    assert "sink-secret" not in str(caught.value)
    assert delegate.calls == 0


def test_terminal_sink_failure_after_network_is_fail_closed_and_redacted() -> None:
    delegate = _Delegate("ok")
    observed = ObservingLLM(
        delegate,
        _FailingSink("terminal"),
        clock_ns=_Clock(0, 0),
    )

    with call_scope(_context()):
        with pytest.raises(ObservationSinkError) as caught:
            observed.complete(system="s", user="u")

    assert caught.value.phase == "terminal"
    assert "sink-secret" not in str(caught.value)
    assert delegate.calls == 1


def test_attempt_intent_sink_failure_happens_before_delegate_and_is_redacted() -> None:
    delegate = _Delegate("ok")
    observed = ObservingLLM(
        delegate,
        _FailingSink("attempt_intent"),
        clock_ns=_Clock(0),
    )

    with call_scope(_context()):
        with pytest.raises(ObservationSinkError) as caught:
            observed.complete(system="s", user="u")

    assert caught.value.phase == "attempt_intent"
    assert "sink-secret" not in str(caught.value)
    assert delegate.calls == 0


def test_attempt_terminal_sink_failure_aborts_after_one_delegate_call() -> None:
    delegate = _Delegate("ok")
    observed = ObservingLLM(
        delegate,
        _FailingSink("attempt_terminal"),
        clock_ns=_Clock(0),
    )

    with call_scope(_context()):
        with pytest.raises(ObservationSinkError) as caught:
            observed.complete(system="s", user="u")

    assert caught.value.phase == "attempt_terminal"
    assert "sink-secret" not in str(caught.value)
    assert delegate.calls == 1


@pytest.mark.parametrize(
    "reply",
    [cast(str, 7), "x" * 33],
)
def test_malicious_or_oversized_delegate_reply_becomes_typed_failure(reply: str) -> None:
    delegate = _Delegate(reply)
    sink = InMemoryObservationSink()
    observed = ObservingLLM(delegate, sink, clock_ns=_Clock(0, 0))

    with call_scope(_context()):
        with pytest.raises(ObservedCallError) as caught:
            observed.complete(system="s", user="u", max_tokens=1)

    assert caught.value.code is CallFailureCode.INVALID_REPLY
    assert sink.results[0].failure_code is CallFailureCode.INVALID_REPLY
    assert sink.results[0].reply_sha256 is None
    assert sink.has_open_intent is False


@pytest.mark.parametrize(
    "metadata",
    [
        object(),
        ProxyCallMetadata(
            status="failed",
            attempts=1,
            returned_model_id=None,
            response_id_sha256=None,
            input_tokens=None,
            output_tokens=None,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
        ),
    ],
)
def test_malicious_or_inconsistent_provider_metadata_fails_closed(metadata: object) -> None:
    sink = InMemoryObservationSink()
    observed = ObservingLLM(
        _MetadataDelegate("ok", metadata),
        sink,
        clock_ns=_Clock(0, 0),
    )

    with call_scope(_context()):
        with pytest.raises(ObservedCallError) as caught:
            observed.complete(system="s", user="u")

    assert caught.value.code is CallFailureCode.PROVIDER_METADATA_INVALID
    assert isinstance(caught.value, LLMIntegrityError)
    assert sink.results[0].failure_code is CallFailureCode.PROVIDER_METADATA_INVALID
    assert sink.results[0].provider.available is False
    assert sink.has_open_intent is False


class _HostileMetadataDelegate(_Delegate):
    @property
    def last_call_metadata(self) -> object:
        raise RuntimeError("metadata-secret")


class _AttributeErrorMetadataDelegate(_Delegate):
    @property
    def last_call_metadata(self) -> object:
        raise AttributeError("metadata-secret")


def test_provider_metadata_getter_exception_is_redacted() -> None:
    sink = InMemoryObservationSink()
    observed = ObservingLLM(
        _HostileMetadataDelegate("ok"),
        sink,
        clock_ns=_Clock(0, 0),
    )

    with call_scope(_context()):
        with pytest.raises(ObservedCallError) as caught:
            observed.complete(system="s", user="u")

    assert caught.value.code is CallFailureCode.PROVIDER_METADATA_INVALID
    assert "metadata-secret" not in str(caught.value)
    assert "metadata-secret" not in repr(sink.events)


def test_provider_metadata_attribute_error_is_not_misreported_as_unavailable() -> None:
    sink = InMemoryObservationSink()
    observed = ObservingLLM(
        _AttributeErrorMetadataDelegate("ok"),
        sink,
        clock_ns=_Clock(0, 0),
    )

    with call_scope(_context()):
        with pytest.raises(ObservedCallError) as caught:
            observed.complete(system="s", user="u")

    assert caught.value.code is CallFailureCode.PROVIDER_METADATA_INVALID
    assert sink.results[0].failure_code is CallFailureCode.PROVIDER_METADATA_INVALID
    assert "metadata-secret" not in str(caught.value)


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"system": cast(str, 1), "user": "u"}, "system"),
        ({"system": "s", "user": cast(str, object())}, "user"),
        ({"system": "s", "user": "u", "max_tokens": cast(int, True)}, "max_tokens"),
        ({"system": "s", "user": "u", "temperature": float("nan")}, "temperature"),
        ({"system": "s", "user": "u", "temperature": 10**400}, "temperature"),
        ({"system": "s", "user": "u", "temperature": 1.1}, "temperature"),
        ({"system": "x" * (MAX_PROXY_REQUEST_FIELD_BYTES + 1), "user": "u"}, "system"),
    ],
)
def test_invalid_requests_fail_before_intent_and_delegate(
    kwargs: dict[str, object],
    field: str,
) -> None:
    delegate = _Delegate("ok")
    sink = InMemoryObservationSink()
    observed = ObservingLLM(delegate, sink, clock_ns=_Clock(0, 0))

    with call_scope(_context()):
        with pytest.raises(ObservationInputError) as caught:
            observed.complete(**kwargs)  # type: ignore[arg-type]

    assert caught.value.field == field
    assert delegate.calls == 0
    assert sink.events == ()


def test_invalid_initial_clock_writes_failure_but_makes_zero_delegate_calls() -> None:
    delegate = _Delegate("ok")
    sink = InMemoryObservationSink()
    observed = ObservingLLM(delegate, sink, clock_ns=_Clock(-1))

    with call_scope(_context()):
        with pytest.raises(ObservedCallError) as caught:
            observed.complete(system="s", user="u")

    assert caught.value.code is CallFailureCode.CLOCK_FAILED
    assert isinstance(caught.value, LLMIntegrityError)
    assert delegate.calls == 0
    assert sink.results[0].failure_code is CallFailureCode.CLOCK_FAILED
    assert sink.results[0].elapsed_microseconds == 0
    assert sink.has_open_intent is False


@pytest.mark.parametrize(
    "clock",
    [
        _Clock(2, 1),
        _Clock(0, (MAX_ELAPSED_MICROSECONDS + 1) * 1_000),
        _Clock(0, RuntimeError("clock-secret")),
    ],
)
def test_invalid_terminal_clock_after_delegate_is_typed_and_bounded(clock: _Clock) -> None:
    delegate = _Delegate("ok")
    sink = InMemoryObservationSink()
    observed = ObservingLLM(delegate, sink, clock_ns=clock)

    with call_scope(_context()):
        with pytest.raises(ObservedCallError) as caught:
            observed.complete(system="s", user="u")

    assert caught.value.code is CallFailureCode.CLOCK_FAILED
    assert "clock-secret" not in str(caught.value)
    assert delegate.calls == 1
    assert sink.results[0].failure_code is CallFailureCode.CLOCK_FAILED
    assert 0 <= sink.results[0].elapsed_microseconds <= MAX_ELAPSED_MICROSECONDS


def test_terminal_clock_failure_retains_valid_provider_metadata() -> None:
    metadata = ProxyCallMetadata(
        status="succeeded",
        attempts=1,
        returned_model_id="gpt-5.6-sol",
        response_id_sha256=None,
        input_tokens=7,
        output_tokens=3,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
    )
    sink = InMemoryObservationSink()
    observed = ObservingLLM(
        _MetadataDelegate("ok", metadata),
        sink,
        clock_ns=_Clock(2, 1),
    )

    with call_scope(_context()):
        with pytest.raises(ObservedCallError) as caught:
            observed.complete(system="s", user="u")

    assert caught.value.code is CallFailureCode.CLOCK_FAILED
    assert sink.results[0].provider.available is True
    assert sink.results[0].provider.input_tokens == 7
    assert sink.results[0].provider.output_tokens == 3


def test_context_and_records_are_frozen_and_exactly_validated() -> None:
    context = _context()
    with pytest.raises(FrozenInstanceError):
        context.call_index = 2  # type: ignore[misc]
    with pytest.raises(ObservationInputError) as caught:
        CallContext(
            run_id="run",
            logical_call_id="x" * (MAX_OBSERVATION_IDENTIFIER_CHARS + 1),
            call_index=0,
            item_id="item",
            family_id="family",
            cluster_id="cluster",
            pair_id="pair",
            sample_index=0,
            candidate_index=0,
            stage=CallStage.PROPOSAL,
            stage_ordinal=0,
        )
    assert caught.value.field == "logical_call_id"
    with pytest.raises(ObservationInputError) as caught:
        _context(call_index=cast(int, True))
    assert caught.value.field == "call_index"


def test_context_rejects_untyped_stage_bad_ordinal_and_sample_drift() -> None:
    with pytest.raises(ObservationInputError) as caught:
        _context(stage=cast(CallStage, "proposal"))
    assert caught.value.field == "stage"

    with pytest.raises(ObservationInputError) as caught:
        _context(stage_ordinal=1)
    assert caught.value.field == "stage_ordinal"

    with pytest.raises(ObservationInputError) as caught:
        replace(_context(), sample_index=1)
    assert caught.value.field == "sample_index"


def test_call_sequence_binds_typed_stage_ordinals_and_global_order() -> None:
    sequence = CallSequence("run-1")
    scopes = sequence.bind_candidate(
        item_id="item-1",
        family_id="family-1",
        cluster_id="cluster-1",
        pair_id="pair-1",
    )
    sink = InMemoryObservationSink()
    observed = ObservingLLM(_Delegate("ok"), sink, clock_ns=_Clock(0, 0, 0, 0))

    with scopes("proposal", 0, 0):
        observed.complete(system="s", user="u")
    with scopes("repair", 0, 1):
        observed.complete(system="s", user="u")

    assert sequence.next_call_index == 2
    assert [intent.logical_call_id for intent in sink.intents] == ["call:0", "call:1"]
    assert [intent.stage for intent in sink.intents] == [
        CallStage.PROPOSAL,
        CallStage.REPAIR,
    ]
    assert [intent.stage_ordinal for intent in sink.intents] == [0, 1]


def test_in_memory_sink_enforces_call_limit_and_terminal_pairing() -> None:
    sink = InMemoryObservationSink(max_calls=1)
    observed = ObservingLLM(_Delegate("ok"), sink, clock_ns=_Clock(0, 0))
    with call_scope(_context()):
        observed.complete(system="s", user="u")

    with call_scope(_context("second", 1)):
        with pytest.raises(ObservationSinkError):
            observed.complete(system="s", user="u")
    assert len(sink.intents) == 1
    assert len(sink.results) == 1


def test_observing_llm_owns_optional_delegate_lifecycle() -> None:
    class ClosableDelegate(_Delegate):
        def __init__(self) -> None:
            super().__init__("ok")
            self.closes = 0

        def close(self) -> None:
            self.closes += 1

    delegate = ClosableDelegate()
    observed = ObservingLLM(delegate, InMemoryObservationSink(), clock_ns=_Clock(0, 0))
    observed.close()
    observed.close()
    assert delegate.closes == 1
    with pytest.raises(RuntimeError, match="observing LLM is closed"):
        observed.complete(system="s", user="u")


def test_corrupted_exact_provider_usage_is_rejected_without_rendering_value() -> None:
    metadata = ProxyCallMetadata(
        status="succeeded",
        attempts=1,
        returned_model_id=None,
        response_id_sha256=None,
        input_tokens=0,
        output_tokens=0,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
    )
    object.__setattr__(metadata, "input_tokens", MAX_PROXY_USAGE_TOKENS + 1)
    sink = InMemoryObservationSink()
    observed = ObservingLLM(
        _MetadataDelegate("ok", metadata),
        sink,
        clock_ns=_Clock(0, 0),
    )

    with call_scope(_context()):
        with pytest.raises(ObservedCallError) as caught:
            observed.complete(system="s", user="u")

    assert caught.value.code is CallFailureCode.PROVIDER_METADATA_INVALID
    assert sink.results[0].failure_code is CallFailureCode.PROVIDER_METADATA_INVALID


_HOSTILE_HOOKS: list[str] = []


class _HostileField:
    def __eq__(self, other: object) -> bool:
        del other
        _HOSTILE_HOOKS.append("eq")
        raise AssertionError("hostile equality hook must not run")

    def __repr__(self) -> str:
        _HOSTILE_HOOKS.append("repr")
        raise AssertionError("hostile repr hook must not run")

    def __str__(self) -> str:
        _HOSTILE_HOOKS.append("str")
        raise AssertionError("hostile str hook must not run")


def test_corrupted_provider_status_rejects_hostile_field_without_hooks() -> None:
    metadata = ProxyCallMetadata(
        status="succeeded",
        attempts=1,
        returned_model_id=None,
        response_id_sha256=None,
        input_tokens=None,
        output_tokens=None,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
    )
    object.__setattr__(metadata, "status", _HostileField())
    _HOSTILE_HOOKS.clear()
    sink = InMemoryObservationSink()
    observed = ObservingLLM(
        _MetadataDelegate("ok", metadata),
        sink,
        clock_ns=_Clock(0, 0),
    )

    with call_scope(_context()):
        with pytest.raises(ObservedCallError) as caught:
            observed.complete(system="s", user="u")

    assert caught.value.code is CallFailureCode.PROVIDER_METADATA_INVALID
    assert _HOSTILE_HOOKS == []


def test_result_status_rejects_hostile_field_without_hooks() -> None:
    _HOSTILE_HOOKS.clear()
    with pytest.raises(ObservationInputError) as caught:
        CallResult(
            logical_call_id="call",
            call_index=0,
            status=cast("str", _HostileField()),  # type: ignore[arg-type]
            reply_sha256=None,
            elapsed_microseconds=0,
            failure_code=CallFailureCode.DELEGATE_FAILED,
            provider=ProviderObservation(
                available=False,
                status=None,
                attempts=None,
                retries=None,
                returned_model_id=None,
                response_id_sha256=None,
                input_tokens=None,
                output_tokens=None,
                cache_creation_input_tokens=None,
                cache_read_input_tokens=None,
            ),
        )

    assert caught.value.field == "status"
    assert _HOSTILE_HOOKS == []


def test_current_scope_is_restored_even_when_body_raises() -> None:
    context = _context()

    with pytest.raises(RuntimeError, match="body failed"):
        with call_scope(context):
            assert current_call_context() == context
            raise RuntimeError("body failed")

    assert current_call_context() is None
