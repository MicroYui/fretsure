"""Fail-closed observation boundary for sequential benchmark LLM calls.

The product-facing :class:`~fretsure.llm.client.LLMClient` protocol deliberately
returns only text.  ``ObservingLLM`` preserves that small interface while emitting
bounded, immutable records around each call.  Records contain domain-separated
digests of visible text, never the text itself or an exception message.

This module is intentionally storage-agnostic.  The in-memory sink is useful for
tests and small harnesses; the artifact layer can implement ``ObservationSink`` with
a durable write-ahead journal without changing an LLM implementation.
"""

from __future__ import annotations

import hashlib
import inspect
import math
import re
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol, Self, cast

from fretsure.llm.client import (
    MAX_PROXY_OUTPUT_TOKENS,
    MAX_PROXY_REQUEST_BYTES,
    MAX_PROXY_REQUEST_FIELD_BYTES,
    MAX_PROXY_RESPONSE_BYTES,
    MAX_PROXY_TEXT_BYTES_PER_TOKEN,
    MAX_PROXY_USAGE_TOKENS,
    LLMClient,
    LLMIntegrityError,
    LLMModelIdError,
    ProxyAttemptObserver,
    ProxyCallMetadata,
    close_llm_client,
    observe_proxy_attempts,
    snapshot_llm_model_id,
    validate_llm_model_id,
)

MAX_OBSERVATION_CALLS = 100_000
MAX_OBSERVATION_INDEX = 1_000_000
MAX_OBSERVATION_IDENTIFIER_CHARS = 128
MAX_OBSERVED_ATTEMPTS = 16
MAX_MONOTONIC_NANOSECONDS = (1 << 63) - 1
MAX_ELAPSED_MICROSECONDS = 24 * 60 * 60 * 1_000_000

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+\-]*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SYSTEM_DIGEST_DOMAIN = b"fretsure:benchmark-call-system@0.1.0\0"
_USER_DIGEST_DOMAIN = b"fretsure:benchmark-call-user@0.1.0\0"
_REQUEST_DIGEST_DOMAIN = b"fretsure:benchmark-call-request@0.1.0\0"
_REPLY_DIGEST_DOMAIN = b"fretsure:benchmark-call-reply@0.1.0\0"


class ObservationInputError(LLMIntegrityError, ValueError):
    """A public observation value was outside the exact bounded contract."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid observation {field}: {detail}")


class ObservationContextError(LLMIntegrityError, RuntimeError):
    """A model call did not have one explicit benchmark call scope."""


class ObservationSinkError(LLMIntegrityError, RuntimeError):
    """A fail-closed observation sink rejected an intent or terminal record."""

    def __init__(
        self,
        phase: Literal["intent", "attempt_intent", "attempt_terminal", "terminal"],
    ) -> None:
        self.phase = phase
        super().__init__(f"observation {phase} sink failed")


class CallFailureCode(StrEnum):
    """Stable terminal failures; provider and exception text are never persisted."""

    DELEGATE_FAILED = "DELEGATE_FAILED"
    INVALID_REPLY = "INVALID_REPLY"
    CLOCK_FAILED = "CLOCK_FAILED"
    PROVIDER_METADATA_INVALID = "PROVIDER_METADATA_INVALID"
    RETURNED_MODEL_MISMATCH = "RETURNED_MODEL_MISMATCH"


class CallStage(StrEnum):
    """Frozen semantic stage for one benchmark model call."""

    PROPOSAL = "proposal"
    REPAIR = "repair"
    CRITIC = "critic"
    RAW = "raw"


class ObservedCallError(RuntimeError):
    """A redacted, typed LLM call failure exposed to the calling harness."""

    def __init__(self, code: CallFailureCode) -> None:
        self.code = code
        super().__init__(f"observed LLM call failed: {code.value}")


class ObservedCallIntegrityError(ObservedCallError, LLMIntegrityError):
    """Instrumentation corruption that formal collection must never fallback over."""


def _require_identifier(value: object, field: str) -> str:
    if type(value) is not str:
        raise ObservationInputError(field, "must be an exact string")
    identifier = value
    if (
        not 1 <= len(identifier) <= MAX_OBSERVATION_IDENTIFIER_CHARS
        or _IDENTIFIER.fullmatch(identifier) is None
    ):
        raise ObservationInputError(field, "must be bounded inert ASCII")
    return identifier


def _require_index(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_OBSERVATION_INDEX:
        raise ObservationInputError(
            field,
            f"must be an exact integer in 0..{MAX_OBSERVATION_INDEX}",
        )
    return value


def _require_sha256(value: object, field: str) -> str:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ObservationInputError(field, "must be one lowercase SHA-256 digest")
    return value


def _utf8_bytes(value: object, field: str, limit: int) -> bytes:
    if type(value) is not str:
        raise ObservationInputError(field, "must be an exact string")
    text = value
    if len(text) > limit:
        raise ObservationInputError(field, "exceeds the bounded UTF-8 limit")
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError:
        raise ObservationInputError(field, "must contain valid Unicode text") from None
    if len(encoded) > limit:
        raise ObservationInputError(field, "exceeds the bounded UTF-8 limit")
    return encoded


def _domain_digest(domain: bytes, encoded: bytes) -> str:
    return hashlib.sha256(domain + encoded).hexdigest()


def visible_text_sha256(
    kind: Literal["system", "user", "reply"],
    text: str,
    *,
    max_bytes: int = MAX_PROXY_RESPONSE_BYTES,
) -> str:
    """Hash bounded visible text with a field-specific domain separator."""

    if type(max_bytes) is not int or not 0 <= max_bytes <= MAX_PROXY_REQUEST_BYTES:
        raise ObservationInputError(
            "max_bytes",
            f"must be an exact integer in 0..{MAX_PROXY_REQUEST_BYTES}",
        )
    domains = {
        "system": _SYSTEM_DIGEST_DOMAIN,
        "user": _USER_DIGEST_DOMAIN,
        "reply": _REPLY_DIGEST_DOMAIN,
    }
    if type(kind) is not str or kind not in domains:
        raise ObservationInputError("kind", "must be system, user, or reply")
    encoded = _utf8_bytes(text, kind, max_bytes)
    return _domain_digest(domains[kind], encoded)


@dataclass(frozen=True, slots=True)
class CallContext:
    """Immutable preregistered identity and order for one logical model call."""

    run_id: str
    logical_call_id: str
    call_index: int
    item_id: str
    family_id: str
    cluster_id: str
    pair_id: str
    sample_index: int
    candidate_index: int
    stage: CallStage
    stage_ordinal: int

    def __post_init__(self) -> None:
        _validate_context(self)


def _validate_context(context: object) -> CallContext:
    if type(context) is not CallContext:
        raise ObservationInputError("context", "must be an exact CallContext")
    exact = context
    _require_identifier(exact.run_id, "run_id")
    _require_identifier(exact.logical_call_id, "logical_call_id")
    _require_index(exact.call_index, "call_index")
    _require_identifier(exact.item_id, "item_id")
    _require_identifier(exact.family_id, "family_id")
    _require_identifier(exact.cluster_id, "cluster_id")
    _require_identifier(exact.pair_id, "pair_id")
    _require_index(exact.sample_index, "sample_index")
    _require_index(exact.candidate_index, "candidate_index")
    if type(exact.stage) is not CallStage:
        raise ObservationInputError("stage", "must be an exact CallStage")
    _require_index(exact.stage_ordinal, "stage_ordinal")
    if exact.stage is not CallStage.REPAIR and exact.stage_ordinal != 0:
        raise ObservationInputError(
            "stage_ordinal",
            "proposal, critic, and raw calls require ordinal zero",
        )
    if exact.sample_index != exact.candidate_index:
        raise ObservationInputError(
            "sample_index",
            "arrangement sample index must equal candidate index",
        )
    return exact


@dataclass(frozen=True, slots=True)
class CallIntent:
    """Write-ahead record emitted before a delegate can make a network call."""

    run_id: str
    logical_call_id: str
    call_index: int
    item_id: str
    family_id: str
    cluster_id: str
    pair_id: str
    sample_index: int
    candidate_index: int
    stage: CallStage
    stage_ordinal: int
    requested_model_id: str
    system_sha256: str
    user_sha256: str
    request_sha256: str
    max_tokens: int
    temperature: float

    def __post_init__(self) -> None:
        _validate_intent(self)


def _validate_intent(intent: object) -> CallIntent:
    if type(intent) is not CallIntent:
        raise ObservationInputError("intent", "must be an exact CallIntent")
    exact = intent
    _require_identifier(exact.run_id, "run_id")
    _require_identifier(exact.logical_call_id, "logical_call_id")
    _require_index(exact.call_index, "call_index")
    _require_identifier(exact.item_id, "item_id")
    _require_identifier(exact.family_id, "family_id")
    _require_identifier(exact.cluster_id, "cluster_id")
    _require_identifier(exact.pair_id, "pair_id")
    _require_index(exact.sample_index, "sample_index")
    _require_index(exact.candidate_index, "candidate_index")
    if type(exact.stage) is not CallStage:
        raise ObservationInputError("stage", "must be an exact CallStage")
    _require_index(exact.stage_ordinal, "stage_ordinal")
    if exact.stage is not CallStage.REPAIR and exact.stage_ordinal != 0:
        raise ObservationInputError(
            "stage_ordinal",
            "proposal, critic, and raw calls require ordinal zero",
        )
    if exact.sample_index != exact.candidate_index:
        raise ObservationInputError(
            "sample_index",
            "arrangement sample index must equal candidate index",
        )
    try:
        validate_llm_model_id(exact.requested_model_id)
    except LLMModelIdError:
        raise ObservationInputError(
            "requested_model_id", "must be one bounded printable model identifier"
        ) from None
    _require_sha256(exact.system_sha256, "system_sha256")
    _require_sha256(exact.user_sha256, "user_sha256")
    _require_sha256(exact.request_sha256, "request_sha256")
    if type(exact.max_tokens) is not int or not 1 <= exact.max_tokens <= MAX_PROXY_OUTPUT_TOKENS:
        raise ObservationInputError(
            "max_tokens",
            f"must be an exact integer in 1..{MAX_PROXY_OUTPUT_TOKENS}",
        )
    if type(exact.temperature) is not float or not math.isfinite(exact.temperature):
        raise ObservationInputError("temperature", "must be one finite float in 0..1")
    if not 0.0 <= exact.temperature <= 1.0:
        raise ObservationInputError("temperature", "must be one finite float in 0..1")
    if exact.request_sha256 != _request_digest(
        exact.requested_model_id,
        exact.system_sha256,
        exact.user_sha256,
        exact.max_tokens,
        exact.temperature,
    ):
        raise ObservationInputError(
            "request_sha256",
            "does not bind the validated request fields",
        )
    return exact


@dataclass(frozen=True, slots=True)
class AttemptIntent:
    """Durable reservation emitted immediately before one network attempt."""

    run_id: str
    logical_call_id: str
    call_index: int
    attempt_id: str
    attempt_index: int
    request_sha256: str
    reserved_output_tokens: int

    def __post_init__(self) -> None:
        _validate_attempt_intent(self)


def _validate_attempt_intent(intent: object) -> AttemptIntent:
    if type(intent) is not AttemptIntent:
        raise ObservationInputError("attempt_intent", "must be an exact AttemptIntent")
    exact = intent
    _require_identifier(exact.run_id, "attempt.run_id")
    _require_identifier(exact.logical_call_id, "attempt.logical_call_id")
    _require_index(exact.call_index, "attempt.call_index")
    _require_identifier(exact.attempt_id, "attempt.attempt_id")
    if type(exact.attempt_index) is not int or not 0 <= exact.attempt_index < MAX_OBSERVED_ATTEMPTS:
        raise ObservationInputError(
            "attempt.attempt_index",
            f"must be an exact integer in 0..{MAX_OBSERVED_ATTEMPTS - 1}",
        )
    _require_sha256(exact.request_sha256, "attempt.request_sha256")
    if (
        type(exact.reserved_output_tokens) is not int
        or not 1 <= exact.reserved_output_tokens <= MAX_PROXY_OUTPUT_TOKENS
    ):
        raise ObservationInputError(
            "attempt.reserved_output_tokens",
            f"must be an exact integer in 1..{MAX_PROXY_OUTPUT_TOKENS}",
        )
    return exact


@dataclass(frozen=True, slots=True)
class AttemptResult:
    """Typed terminal record paired with one attempt reservation."""

    run_id: str
    logical_call_id: str
    call_index: int
    attempt_id: str
    attempt_index: int
    status: Literal["succeeded", "failed"]
    retryable: bool

    def __post_init__(self) -> None:
        _validate_attempt_result(self)


def _validate_attempt_result(result: object) -> AttemptResult:
    if type(result) is not AttemptResult:
        raise ObservationInputError("attempt_result", "must be an exact AttemptResult")
    exact = result
    _require_identifier(exact.run_id, "attempt.run_id")
    _require_identifier(exact.logical_call_id, "attempt.logical_call_id")
    _require_index(exact.call_index, "attempt.call_index")
    _require_identifier(exact.attempt_id, "attempt.attempt_id")
    if type(exact.attempt_index) is not int or not 0 <= exact.attempt_index < MAX_OBSERVED_ATTEMPTS:
        raise ObservationInputError(
            "attempt.attempt_index",
            f"must be an exact integer in 0..{MAX_OBSERVED_ATTEMPTS - 1}",
        )
    if type(exact.status) is not str or exact.status not in ("succeeded", "failed"):
        raise ObservationInputError("attempt.status", "must be succeeded or failed")
    if type(exact.retryable) is not bool:
        raise ObservationInputError("attempt.retryable", "must be an exact bool")
    if exact.status == "succeeded" and exact.retryable:
        raise ObservationInputError(
            "attempt.retryable",
            "a successful attempt cannot be retryable",
        )
    return exact


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    """Exact bounded provider metadata, or an explicit unavailable snapshot."""

    available: bool
    status: Literal["succeeded", "failed"] | None
    attempts: int | None
    retries: int | None
    returned_model_id: str | None
    response_id_sha256: str | None
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None

    def __post_init__(self) -> None:
        _validate_provider_observation(self)


def _validate_optional_tokens(value: object, field: str) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= MAX_PROXY_USAGE_TOKENS:
        raise ObservationInputError(
            field,
            f"must be null or an exact integer in 0..{MAX_PROXY_USAGE_TOKENS}",
        )
    return value


def _validate_provider_observation(value: object) -> ProviderObservation:
    if type(value) is not ProviderObservation:
        raise ObservationInputError("provider_observation", "must be an exact ProviderObservation")
    exact = value
    if type(exact.available) is not bool:
        raise ObservationInputError("provider.available", "must be an exact bool")
    optional_fields = (
        exact.status,
        exact.attempts,
        exact.retries,
        exact.returned_model_id,
        exact.response_id_sha256,
        exact.input_tokens,
        exact.output_tokens,
        exact.cache_creation_input_tokens,
        exact.cache_read_input_tokens,
    )
    if not exact.available:
        if any(field is not None for field in optional_fields):
            raise ObservationInputError(
                "provider", "unavailable metadata must contain only null fields"
            )
        return exact
    if type(exact.status) is not str or exact.status not in ("succeeded", "failed"):
        raise ObservationInputError("provider.status", "must be succeeded or failed")
    if type(exact.attempts) is not int or not 1 <= exact.attempts <= MAX_OBSERVED_ATTEMPTS:
        raise ObservationInputError(
            "provider.attempts",
            f"must be an exact integer in 1..{MAX_OBSERVED_ATTEMPTS}",
        )
    if type(exact.retries) is not int or exact.retries != exact.attempts - 1:
        raise ObservationInputError("provider.retries", "must equal attempts minus one")
    if exact.returned_model_id is not None:
        try:
            validate_llm_model_id(exact.returned_model_id)
        except LLMModelIdError:
            raise ObservationInputError(
                "provider.returned_model_id", "must be null or a bounded model identifier"
            ) from None
    if exact.response_id_sha256 is not None:
        _require_sha256(exact.response_id_sha256, "provider.response_id_sha256")
    _validate_optional_tokens(exact.input_tokens, "provider.input_tokens")
    _validate_optional_tokens(exact.output_tokens, "provider.output_tokens")
    _validate_optional_tokens(
        exact.cache_creation_input_tokens,
        "provider.cache_creation_input_tokens",
    )
    _validate_optional_tokens(exact.cache_read_input_tokens, "provider.cache_read_input_tokens")
    return exact


_UNAVAILABLE_PROVIDER = ProviderObservation(
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
)


@dataclass(frozen=True, slots=True)
class CallResult:
    """Exactly one terminal result for a previously recorded logical call intent."""

    logical_call_id: str
    call_index: int
    status: Literal["succeeded", "failed"]
    reply_sha256: str | None
    elapsed_microseconds: int
    failure_code: CallFailureCode | None
    provider: ProviderObservation

    def __post_init__(self) -> None:
        _validate_result(self)


def _validate_result(result: object) -> CallResult:
    if type(result) is not CallResult:
        raise ObservationInputError("result", "must be an exact CallResult")
    exact = result
    _require_identifier(exact.logical_call_id, "logical_call_id")
    _require_index(exact.call_index, "call_index")
    if type(exact.status) is not str or exact.status not in ("succeeded", "failed"):
        raise ObservationInputError("status", "must be succeeded or failed")
    if (
        type(exact.elapsed_microseconds) is not int
        or not 0 <= exact.elapsed_microseconds <= MAX_ELAPSED_MICROSECONDS
    ):
        raise ObservationInputError(
            "elapsed_microseconds",
            f"must be an exact integer in 0..{MAX_ELAPSED_MICROSECONDS}",
        )
    _validate_provider_observation(exact.provider)
    if exact.status == "succeeded":
        if exact.reply_sha256 is None:
            raise ObservationInputError("reply_sha256", "successful calls require a digest")
        _require_sha256(exact.reply_sha256, "reply_sha256")
        if exact.failure_code is not None:
            raise ObservationInputError("failure_code", "successful calls require null")
        if exact.provider.available and exact.provider.status != "succeeded":
            raise ObservationInputError(
                "provider.status", "must agree with the successful terminal status"
            )
        return exact
    if exact.reply_sha256 is not None:
        raise ObservationInputError("reply_sha256", "failed calls require null")
    if type(exact.failure_code) is not CallFailureCode:
        raise ObservationInputError("failure_code", "failed calls require a stable code")
    if (
        exact.failure_code is CallFailureCode.DELEGATE_FAILED
        and exact.provider.available
        and exact.provider.status != "failed"
    ):
        raise ObservationInputError(
            "provider.status", "must agree with the failed delegate terminal status"
        )
    return exact


class ObservationSink(Protocol):
    """Storage boundary used synchronously around one sequential LLM call."""

    def write_intent(self, intent: CallIntent) -> None: ...

    def write_attempt_intent(self, intent: AttemptIntent) -> None: ...

    def write_attempt_result(self, result: AttemptResult) -> None: ...

    def write_result(self, result: CallResult) -> None: ...


class ObservationRequestGuard(Protocol):
    """Fail-closed policy over one fully validated visible request."""

    def __call__(
        self,
        system_bytes: bytes,
        user_bytes: bytes,
        max_tokens: int,
        /,
    ) -> None: ...


class _MetadataSource(Protocol):
    @property
    def last_call_metadata(self) -> object: ...


class InMemoryObservationSink:
    """Bounded single-writer sink with strict intent/result pairing."""

    def __init__(self, *, max_calls: int = MAX_OBSERVATION_CALLS) -> None:
        if type(max_calls) is not int or not 1 <= max_calls <= MAX_OBSERVATION_CALLS:
            raise ObservationInputError(
                "max_calls",
                f"must be an exact integer in 1..{MAX_OBSERVATION_CALLS}",
            )
        self._max_calls = max_calls
        self._intents: list[CallIntent] = []
        self._results: list[CallResult] = []
        self._events: list[CallIntent | CallResult] = []
        self._journal_events: list[CallIntent | AttemptIntent | AttemptResult | CallResult] = []
        self._attempt_intents: list[AttemptIntent] = []
        self._attempt_results: list[AttemptResult] = []
        self._attempt_events: list[AttemptIntent | AttemptResult] = []
        self._logical_call_ids: set[str] = set()
        self._attempt_ids: set[str] = set()
        self._open_intent: CallIntent | None = None
        self._open_attempt: AttemptIntent | None = None
        self._next_attempt_index = 0

    def write_intent(self, intent: CallIntent) -> None:
        exact = _validate_intent(intent)
        if self._open_intent is not None:
            raise ObservationInputError(
                "intent", "the preceding sequential intent has no terminal result"
            )
        if len(self._intents) >= self._max_calls:
            raise ObservationInputError("intent", "call count exceeds the sink limit")
        if exact.logical_call_id in self._logical_call_ids:
            raise ObservationInputError("logical_call_id", "is duplicated")
        if exact.call_index != len(self._intents):
            raise ObservationInputError(
                "call_index", "must equal the next deterministic zero-based call index"
            )
        self._logical_call_ids.add(exact.logical_call_id)
        self._open_intent = exact
        self._intents.append(exact)
        self._events.append(exact)
        self._journal_events.append(exact)

    def write_attempt_intent(self, intent: AttemptIntent) -> None:
        exact = _validate_attempt_intent(intent)
        logical = self._open_intent
        if logical is None:
            raise ObservationInputError("attempt_intent", "has no open logical intent")
        if self._open_attempt is not None:
            raise ObservationInputError(
                "attempt_intent",
                "the preceding attempt has no terminal result",
            )
        if (
            exact.run_id != logical.run_id
            or exact.logical_call_id != logical.logical_call_id
            or exact.call_index != logical.call_index
            or exact.request_sha256 != logical.request_sha256
            or exact.reserved_output_tokens != logical.max_tokens
        ):
            raise ObservationInputError(
                "attempt_intent",
                "does not match the open logical intent and reservation",
            )
        if exact.attempt_id in self._attempt_ids:
            raise ObservationInputError("attempt.attempt_id", "is duplicated")
        if exact.attempt_index != self._next_attempt_index:
            raise ObservationInputError(
                "attempt.attempt_index",
                "must equal the next deterministic attempt index",
            )
        self._attempt_ids.add(exact.attempt_id)
        self._open_attempt = exact
        self._attempt_intents.append(exact)
        self._attempt_events.append(exact)
        self._journal_events.append(exact)

    def write_attempt_result(self, result: AttemptResult) -> None:
        exact = _validate_attempt_result(result)
        intent = self._open_attempt
        if intent is None:
            raise ObservationInputError("attempt_result", "has no unmatched attempt intent")
        if (
            exact.run_id != intent.run_id
            or exact.logical_call_id != intent.logical_call_id
            or exact.call_index != intent.call_index
            or exact.attempt_id != intent.attempt_id
            or exact.attempt_index != intent.attempt_index
        ):
            raise ObservationInputError("attempt_result", "does not match the open attempt")
        self._open_attempt = None
        self._next_attempt_index += 1
        self._attempt_results.append(exact)
        self._attempt_events.append(exact)
        self._journal_events.append(exact)

    def write_result(self, result: CallResult) -> None:
        exact = _validate_result(result)
        intent = self._open_intent
        if intent is None:
            raise ObservationInputError("result", "has no unmatched intent")
        if self._open_attempt is not None:
            raise ObservationInputError("result", "the open attempt has no terminal result")
        if self._next_attempt_index == 0 and not (
            exact.status == "failed" and exact.failure_code is CallFailureCode.CLOCK_FAILED
        ):
            raise ObservationInputError("result", "logical calls require at least one attempt")
        if exact.provider.available and exact.provider.attempts != self._next_attempt_index:
            raise ObservationInputError(
                "provider.attempts",
                "must equal the number of terminal attempt records",
            )
        if exact.logical_call_id != intent.logical_call_id or exact.call_index != intent.call_index:
            raise ObservationInputError("result", "does not match the open intent")
        self._open_intent = None
        self._next_attempt_index = 0
        self._results.append(exact)
        self._events.append(exact)
        self._journal_events.append(exact)

    @property
    def intents(self) -> tuple[CallIntent, ...]:
        return tuple(self._intents)

    @property
    def intent_count(self) -> int:
        """Return the logical-call count without snapshotting the journal."""

        return len(self._intents)

    def intents_since(self, start_index: int) -> tuple[CallIntent, ...]:
        """Return only the immutable logical-intent suffix at ``start_index``."""

        if type(start_index) is not int or not 0 <= start_index <= len(self._intents):
            raise ObservationInputError(
                "start_index",
                "must be an exact integer inside the logical-intent journal",
            )
        return tuple(self._intents[start_index:])

    @property
    def results(self) -> tuple[CallResult, ...]:
        return tuple(self._results)

    @property
    def result_count(self) -> int:
        """Return the terminal logical-call count without snapshotting the journal."""

        return len(self._results)

    def results_since(self, start_index: int) -> tuple[CallResult, ...]:
        """Return only the immutable logical-result suffix at ``start_index``."""

        if type(start_index) is not int or not 0 <= start_index <= len(self._results):
            raise ObservationInputError(
                "start_index",
                "must be an exact integer inside the logical-result journal",
            )
        return tuple(self._results[start_index:])

    @property
    def attempt_intents(self) -> tuple[AttemptIntent, ...]:
        return tuple(self._attempt_intents)

    @property
    def attempt_intent_count(self) -> int:
        """Return the provider-attempt count without snapshotting the journal."""

        return len(self._attempt_intents)

    def attempt_intents_since(self, start_index: int) -> tuple[AttemptIntent, ...]:
        """Return only the immutable attempt-intent suffix at ``start_index``."""

        if type(start_index) is not int or not 0 <= start_index <= len(self._attempt_intents):
            raise ObservationInputError(
                "start_index",
                "must be an exact integer inside the attempt-intent journal",
            )
        return tuple(self._attempt_intents[start_index:])

    @property
    def attempt_results(self) -> tuple[AttemptResult, ...]:
        return tuple(self._attempt_results)

    @property
    def attempt_result_count(self) -> int:
        """Return the terminal provider-attempt count without snapshotting the journal."""

        return len(self._attempt_results)

    def attempt_results_since(self, start_index: int) -> tuple[AttemptResult, ...]:
        """Return only the immutable attempt-result suffix at ``start_index``."""

        if type(start_index) is not int or not 0 <= start_index <= len(self._attempt_results):
            raise ObservationInputError(
                "start_index",
                "must be an exact integer inside the attempt-result journal",
            )
        return tuple(self._attempt_results[start_index:])

    @property
    def attempt_events(self) -> tuple[AttemptIntent | AttemptResult, ...]:
        return tuple(self._attempt_events)

    @property
    def events(self) -> tuple[CallIntent | CallResult, ...]:
        return tuple(self._events)

    @property
    def journal_events(
        self,
    ) -> tuple[CallIntent | AttemptIntent | AttemptResult | CallResult, ...]:
        return tuple(self._journal_events)

    @property
    def has_open_intent(self) -> bool:
        return self._open_intent is not None

    @property
    def has_open_attempt(self) -> bool:
        return self._open_attempt is not None


_CURRENT_CONTEXT: ContextVar[CallContext | None] = ContextVar(
    "fretsure_benchmark_call_context",
    default=None,
)


@contextmanager
def call_scope(context: CallContext) -> Iterator[CallContext]:
    """Install one call context and restore any outer context on exit."""

    exact = _validate_context(context)
    token = _CURRENT_CONTEXT.set(exact)
    try:
        yield exact
    finally:
        _CURRENT_CONTEXT.reset(token)


def current_call_context() -> CallContext | None:
    """Return the active immutable context, if a harness installed one."""

    return _CURRENT_CONTEXT.get()


class CallSequence:
    """Single-writer allocator for deterministic run-wide logical call indices."""

    def __init__(self, run_id: object, *, start_call_index: object = 0) -> None:
        self._run_id = _require_identifier(run_id, "run_id")
        self._next_call_index = _require_index(start_call_index, "start_call_index")
        self._active = False

    @property
    def next_call_index(self) -> int:
        return self._next_call_index

    def bind_candidate(
        self,
        *,
        item_id: object,
        family_id: object,
        cluster_id: object,
        pair_id: object,
    ) -> CandidateCallScopes:
        return CandidateCallScopes(
            self,
            item_id=_require_identifier(item_id, "item_id"),
            family_id=_require_identifier(family_id, "family_id"),
            cluster_id=_require_identifier(cluster_id, "cluster_id"),
            pair_id=_require_identifier(pair_id, "pair_id"),
        )

    @contextmanager
    def _scope(
        self,
        *,
        item_id: str,
        family_id: str,
        cluster_id: str,
        pair_id: str,
        candidate_index: object,
        stage: object,
        stage_ordinal: object,
    ) -> Iterator[CallContext]:
        if self._active:
            raise ObservationContextError("benchmark model calls must be sequential")
        if self._next_call_index > MAX_OBSERVATION_INDEX:
            raise ObservationInputError("call_index", "exceeds the run-wide call limit")
        if type(stage) is not str:
            raise ObservationInputError("stage", "must name one frozen call stage")
        try:
            exact_stage = CallStage(stage)
        except ValueError:
            raise ObservationInputError("stage", "must name one frozen call stage") from None
        exact_candidate_index = _require_index(candidate_index, "candidate_index")
        exact_stage_ordinal = _require_index(stage_ordinal, "stage_ordinal")
        call_index = self._next_call_index
        context = CallContext(
            run_id=self._run_id,
            logical_call_id=f"call:{call_index}",
            call_index=call_index,
            item_id=item_id,
            family_id=family_id,
            cluster_id=cluster_id,
            pair_id=pair_id,
            sample_index=exact_candidate_index,
            candidate_index=exact_candidate_index,
            stage=exact_stage,
            stage_ordinal=exact_stage_ordinal,
        )
        self._next_call_index += 1
        self._active = True
        try:
            with call_scope(context):
                yield context
        finally:
            self._active = False


class CandidateCallScopes:
    """Agent-compatible callable that binds one item's call identities."""

    def __init__(
        self,
        sequence: CallSequence,
        *,
        item_id: str,
        family_id: str,
        cluster_id: str,
        pair_id: str,
    ) -> None:
        self._sequence = sequence
        self._item_id = item_id
        self._family_id = family_id
        self._cluster_id = cluster_id
        self._pair_id = pair_id

    def __call__(
        self,
        stage: str,
        candidate_index: int,
        stage_ordinal: int,
    ) -> AbstractContextManager[object]:
        return self._sequence._scope(
            item_id=self._item_id,
            family_id=self._family_id,
            cluster_id=self._cluster_id,
            pair_id=self._pair_id,
            candidate_index=candidate_index,
            stage=stage,
            stage_ordinal=stage_ordinal,
        )


def _validate_request(
    system: object,
    user: object,
    max_tokens: object,
    temperature: object,
) -> tuple[str, str, bytes, bytes, int, float]:
    system_bytes = _utf8_bytes(system, "system", MAX_PROXY_REQUEST_FIELD_BYTES)
    user_bytes = _utf8_bytes(user, "user", MAX_PROXY_REQUEST_FIELD_BYTES)
    if len(system_bytes) + len(user_bytes) > MAX_PROXY_REQUEST_BYTES:
        raise ObservationInputError("request", "exceeds the bounded UTF-8 limit")
    if type(max_tokens) is not int or not 1 <= max_tokens <= MAX_PROXY_OUTPUT_TOKENS:
        raise ObservationInputError(
            "max_tokens",
            f"must be an exact integer in 1..{MAX_PROXY_OUTPUT_TOKENS}",
        )
    if type(temperature) not in (int, float):
        raise ObservationInputError("temperature", "must be a finite real number in 0..1")
    exact_temperature = cast(int | float, temperature)
    if not 0.0 <= exact_temperature <= 1.0:
        raise ObservationInputError("temperature", "must be a finite real number in 0..1")
    normalized_temperature = float(exact_temperature)
    if not math.isfinite(normalized_temperature) or not 0.0 <= normalized_temperature <= 1.0:
        raise ObservationInputError("temperature", "must be a finite real number in 0..1")
    return (
        cast(str, system),
        cast(str, user),
        system_bytes,
        user_bytes,
        max_tokens,
        normalized_temperature,
    )


def _request_digest(
    requested_model_id: str,
    system_sha256: str,
    user_sha256: str,
    max_tokens: int,
    temperature: float,
) -> str:
    fields = (
        requested_model_id.encode("utf-8"),
        system_sha256.encode("ascii"),
        user_sha256.encode("ascii"),
        str(max_tokens).encode("ascii"),
        temperature.hex().encode("ascii"),
    )
    payload = b"".join(len(field).to_bytes(4, "big") + field for field in fields)
    return _domain_digest(_REQUEST_DIGEST_DOMAIN, payload)


def _snapshot_provider_metadata(delegate: object) -> ProviderObservation:
    try:
        inspect.getattr_static(delegate, "last_call_metadata")
    except AttributeError:
        return _UNAVAILABLE_PROVIDER
    except Exception:
        raise ObservationInputError(
            "provider_metadata", "could not be read through the exact boundary"
        ) from None
    try:
        metadata = cast(_MetadataSource, delegate).last_call_metadata
    except Exception:
        raise ObservationInputError(
            "provider_metadata", "could not be read through the exact boundary"
        ) from None
    if metadata is None:
        return _UNAVAILABLE_PROVIDER
    if type(metadata) is not ProxyCallMetadata:
        raise ObservationInputError(
            "provider_metadata", "must be null or an exact ProxyCallMetadata"
        )
    exact = metadata
    # Revalidate every field: frozen dataclasses can still be corrupted through
    # low-level object APIs, and provider evidence is not trusted by provenance.
    if type(exact.status) is not str or exact.status not in ("succeeded", "failed"):
        raise ObservationInputError("provider.status", "must be succeeded or failed")
    if type(exact.attempts) is not int or not 1 <= exact.attempts <= MAX_OBSERVED_ATTEMPTS:
        raise ObservationInputError("provider.attempts", "exceeds the bounded attempt limit")
    if exact.returned_model_id is not None:
        try:
            validate_llm_model_id(exact.returned_model_id)
        except LLMModelIdError:
            raise ObservationInputError(
                "provider.returned_model_id", "is not an exact bounded model id"
            ) from None
    if exact.response_id_sha256 is not None:
        _require_sha256(exact.response_id_sha256, "provider.response_id_sha256")
    _validate_optional_tokens(exact.input_tokens, "provider.input_tokens")
    _validate_optional_tokens(exact.output_tokens, "provider.output_tokens")
    _validate_optional_tokens(
        exact.cache_creation_input_tokens,
        "provider.cache_creation_input_tokens",
    )
    _validate_optional_tokens(exact.cache_read_input_tokens, "provider.cache_read_input_tokens")
    return ProviderObservation(
        available=True,
        status=exact.status,
        attempts=exact.attempts,
        retries=exact.attempts - 1,
        returned_model_id=exact.returned_model_id,
        response_id_sha256=exact.response_id_sha256,
        input_tokens=exact.input_tokens,
        output_tokens=exact.output_tokens,
        cache_creation_input_tokens=exact.cache_creation_input_tokens,
        cache_read_input_tokens=exact.cache_read_input_tokens,
    )


def _read_clock(clock_ns: Callable[[], int]) -> int:
    try:
        value = clock_ns()
    except Exception:
        raise ObservedCallIntegrityError(CallFailureCode.CLOCK_FAILED) from None
    if type(value) is not int or not 0 <= value <= MAX_MONOTONIC_NANOSECONDS:
        raise ObservedCallIntegrityError(CallFailureCode.CLOCK_FAILED)
    return value


def _elapsed_microseconds(start_ns: int, end_ns: int) -> int:
    if end_ns < start_ns:
        raise ObservedCallIntegrityError(CallFailureCode.CLOCK_FAILED)
    elapsed = (end_ns - start_ns) // 1_000
    if elapsed > MAX_ELAPSED_MICROSECONDS:
        raise ObservedCallIntegrityError(CallFailureCode.CLOCK_FAILED)
    return elapsed


def _supports_attempt_observation(delegate: object) -> bool:
    try:
        marker = inspect.getattr_static(delegate, "supports_attempt_observation")
    except AttributeError:
        return False
    except Exception:
        raise ObservationInputError(
            "delegate.attempt_observation",
            "could not be inspected through the exact boundary",
        ) from None
    if type(marker) is not bool:
        raise ObservationInputError(
            "delegate.attempt_observation",
            "must be an exact bool when present",
        )
    return marker


class _AttemptJournal(ProxyAttemptObserver):
    """Pair one logical call with its ordered per-attempt reservations."""

    def __init__(self, owner: ObservingLLM, context: CallContext, intent: CallIntent) -> None:
        self._owner = owner
        self._context = context
        self._intent = intent
        self._open_index: int | None = None
        self._completed = 0

    @property
    def completed(self) -> int:
        return self._completed

    def before_attempt(self, attempt_index: int) -> None:
        if (
            type(attempt_index) is not int
            or attempt_index != self._completed
            or self._open_index is not None
        ):
            raise ObservationInputError(
                "attempt.attempt_index",
                "delegate attempts must be sequential and non-overlapping",
            )
        attempt_id = f"attempt:{self._context.call_index}:{attempt_index}"
        self._owner._write_attempt_intent(
            AttemptIntent(
                run_id=self._context.run_id,
                logical_call_id=self._context.logical_call_id,
                call_index=self._context.call_index,
                attempt_id=attempt_id,
                attempt_index=attempt_index,
                request_sha256=self._intent.request_sha256,
                reserved_output_tokens=self._intent.max_tokens,
            )
        )
        self._open_index = attempt_index

    def after_attempt(
        self,
        attempt_index: int,
        *,
        status: Literal["succeeded", "failed"],
        retryable: bool,
    ) -> None:
        if type(attempt_index) is not int or attempt_index != self._open_index:
            raise ObservationInputError(
                "attempt.attempt_index",
                "terminal attempt does not match the open reservation",
            )
        attempt_id = f"attempt:{self._context.call_index}:{attempt_index}"
        self._owner._write_attempt_result(
            AttemptResult(
                run_id=self._context.run_id,
                logical_call_id=self._context.logical_call_id,
                call_index=self._context.call_index,
                attempt_id=attempt_id,
                attempt_index=attempt_index,
                status=status,
                retryable=retryable,
            )
        )
        self._open_index = None
        self._completed += 1

    def require_terminal(self) -> None:
        if self._open_index is not None or self._completed == 0:
            raise ObservationInputError(
                "attempt",
                "delegate did not produce a complete attempt journal",
            )


class ObservingLLM:
    """An ``LLMClient`` wrapper that emits one intent and one terminal record."""

    def __init__(
        self,
        delegate: LLMClient,
        sink: ObservationSink,
        *,
        clock_ns: Callable[[], int] = time.monotonic_ns,
        request_guard: ObservationRequestGuard | None = None,
    ) -> None:
        try:
            self._model_id = snapshot_llm_model_id(delegate)
        except LLMModelIdError:
            raise ObservationInputError(
                "delegate.model_id", "could not be read as a bounded model id"
            ) from None
        if not callable(clock_ns):
            raise ObservationInputError("clock_ns", "must be callable")
        if request_guard is not None and not callable(request_guard):
            raise ObservationInputError("request_guard", "must be null or callable")
        self._delegate = delegate
        self._sink = sink
        self._clock_ns = clock_ns
        self._request_guard = request_guard
        self._closed = False

    @property
    def model_id(self) -> str:
        return self._model_id

    def close(self) -> None:
        if self._closed:
            return
        close_llm_client(self._delegate)
        self._closed = True

    def __enter__(self) -> Self:
        if self._closed:
            raise RuntimeError("observing LLM is closed")
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _write_intent(self, intent: CallIntent) -> None:
        try:
            self._sink.write_intent(intent)
        except Exception:
            raise ObservationSinkError("intent") from None

    def _write_attempt_intent(self, intent: AttemptIntent) -> None:
        try:
            self._sink.write_attempt_intent(intent)
        except Exception:
            raise ObservationSinkError("attempt_intent") from None

    def _write_attempt_result(self, result: AttemptResult) -> None:
        try:
            self._sink.write_attempt_result(result)
        except Exception:
            raise ObservationSinkError("attempt_terminal") from None

    def _write_result(self, result: CallResult) -> None:
        try:
            self._sink.write_result(result)
        except Exception:
            raise ObservationSinkError("terminal") from None

    def _failed_result(
        self,
        context: CallContext,
        code: CallFailureCode,
        elapsed_microseconds: int,
        provider: ProviderObservation = _UNAVAILABLE_PROVIDER,
    ) -> CallResult:
        return CallResult(
            logical_call_id=context.logical_call_id,
            call_index=context.call_index,
            status="failed",
            reply_sha256=None,
            elapsed_microseconds=elapsed_microseconds,
            failure_code=code,
            provider=provider,
        )

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        if self._closed:
            raise RuntimeError("observing LLM is closed")
        context = current_call_context()
        if context is None:
            raise ObservationContextError("LLM call requires an explicit call scope")
        context = _validate_context(context)
        (
            system,
            user,
            system_bytes,
            user_bytes,
            max_tokens,
            temperature,
        ) = _validate_request(system, user, max_tokens, temperature)
        if self._request_guard is not None:
            self._request_guard(system_bytes, user_bytes, max_tokens)
        system_sha256 = _domain_digest(_SYSTEM_DIGEST_DOMAIN, system_bytes)
        user_sha256 = _domain_digest(_USER_DIGEST_DOMAIN, user_bytes)
        intent = CallIntent(
            run_id=context.run_id,
            logical_call_id=context.logical_call_id,
            call_index=context.call_index,
            item_id=context.item_id,
            family_id=context.family_id,
            cluster_id=context.cluster_id,
            pair_id=context.pair_id,
            sample_index=context.sample_index,
            candidate_index=context.candidate_index,
            stage=context.stage,
            stage_ordinal=context.stage_ordinal,
            requested_model_id=self._model_id,
            system_sha256=system_sha256,
            user_sha256=user_sha256,
            request_sha256=_request_digest(
                self._model_id,
                system_sha256,
                user_sha256,
                max_tokens,
                temperature,
            ),
            max_tokens=max_tokens,
            temperature=temperature,
        )
        self._write_intent(intent)

        try:
            start_ns = _read_clock(self._clock_ns)
        except ObservedCallError as error:
            self._write_result(self._failed_result(context, error.code, 0))
            raise

        attempt_journal = _AttemptJournal(self, context, intent)
        proxy_attempts = _supports_attempt_observation(self._delegate)
        if not proxy_attempts:
            attempt_journal.before_attempt(0)

        try:
            if proxy_attempts:
                with observe_proxy_attempts(attempt_journal):
                    reply = self._delegate.complete(
                        system=system,
                        user=user,
                        max_tokens=max_tokens,
                        temperature=temperature,
                    )
            else:
                reply = self._delegate.complete(
                    system=system,
                    user=user,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
        except LLMIntegrityError:
            raise
        except Exception:
            if not proxy_attempts:
                attempt_journal.after_attempt(
                    0,
                    status="failed",
                    retryable=False,
                )
            attempt_journal.require_terminal()
            try:
                end_ns = _read_clock(self._clock_ns)
                elapsed = _elapsed_microseconds(start_ns, end_ns)
            except ObservedCallError as error:
                self._write_result(self._failed_result(context, error.code, 0))
                raise
            try:
                provider = _snapshot_provider_metadata(self._delegate)
            except ObservationInputError:
                code = CallFailureCode.PROVIDER_METADATA_INVALID
                self._write_result(self._failed_result(context, code, elapsed))
                raise ObservedCallIntegrityError(code) from None
            if provider.available and provider.status != "failed":
                code = CallFailureCode.PROVIDER_METADATA_INVALID
                self._write_result(self._failed_result(context, code, elapsed))
                raise ObservedCallIntegrityError(code) from None
            if provider.available and provider.attempts != attempt_journal.completed:
                code = CallFailureCode.PROVIDER_METADATA_INVALID
                self._write_result(self._failed_result(context, code, elapsed))
                raise ObservedCallIntegrityError(code) from None
            code = CallFailureCode.DELEGATE_FAILED
            self._write_result(self._failed_result(context, code, elapsed, provider))
            raise ObservedCallError(code) from None

        if not proxy_attempts:
            attempt_journal.after_attempt(
                0,
                status="succeeded",
                retryable=False,
            )
        attempt_journal.require_terminal()

        try:
            end_ns = _read_clock(self._clock_ns)
            elapsed = _elapsed_microseconds(start_ns, end_ns)
        except ObservedCallError as error:
            try:
                provider = _snapshot_provider_metadata(self._delegate)
            except ObservationInputError:
                provider = _UNAVAILABLE_PROVIDER
            if provider.available and (
                provider.status != "succeeded" or provider.attempts != attempt_journal.completed
            ):
                provider = _UNAVAILABLE_PROVIDER
            self._write_result(self._failed_result(context, error.code, 0, provider))
            raise
        try:
            provider = _snapshot_provider_metadata(self._delegate)
        except ObservationInputError:
            code = CallFailureCode.PROVIDER_METADATA_INVALID
            self._write_result(self._failed_result(context, code, elapsed))
            raise ObservedCallIntegrityError(code) from None
        if provider.available and provider.status != "succeeded":
            code = CallFailureCode.PROVIDER_METADATA_INVALID
            self._write_result(self._failed_result(context, code, elapsed))
            raise ObservedCallIntegrityError(code) from None
        if provider.available and provider.attempts != attempt_journal.completed:
            code = CallFailureCode.PROVIDER_METADATA_INVALID
            self._write_result(self._failed_result(context, code, elapsed))
            raise ObservedCallIntegrityError(code) from None

        reply_limit = min(MAX_PROXY_RESPONSE_BYTES, max_tokens * MAX_PROXY_TEXT_BYTES_PER_TOKEN)
        try:
            reply_sha256 = visible_text_sha256("reply", reply, max_bytes=reply_limit)
        except ObservationInputError:
            code = CallFailureCode.INVALID_REPLY
            self._write_result(self._failed_result(context, code, elapsed, provider))
            raise ObservedCallError(code) from None
        result = CallResult(
            logical_call_id=context.logical_call_id,
            call_index=context.call_index,
            status="succeeded",
            reply_sha256=reply_sha256,
            elapsed_microseconds=elapsed,
            failure_code=None,
            provider=provider,
        )
        self._write_result(result)
        return reply


__all__ = [
    "AttemptIntent",
    "AttemptResult",
    "CallContext",
    "CallFailureCode",
    "CallIntent",
    "CallResult",
    "CallSequence",
    "CallStage",
    "CandidateCallScopes",
    "InMemoryObservationSink",
    "MAX_ELAPSED_MICROSECONDS",
    "MAX_OBSERVATION_CALLS",
    "MAX_OBSERVATION_IDENTIFIER_CHARS",
    "MAX_OBSERVATION_INDEX",
    "MAX_OBSERVED_ATTEMPTS",
    "ObservationContextError",
    "ObservationInputError",
    "ObservationSink",
    "ObservationSinkError",
    "ObservedCallError",
    "ObservedCallIntegrityError",
    "ObservingLLM",
    "ProviderObservation",
    "call_scope",
    "current_call_context",
    "visible_text_sha256",
]
