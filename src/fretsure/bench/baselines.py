"""Typed benchmark baselines with honest execution provenance.

B1 asks the model for tablature directly and deliberately does not repair or
playability-gate a schema/domain-valid result.  B2 runs the deterministic proposal
and solver exactly once per item.  B3/B4 remain explicit unavailable records until a
license-audited reproducible adapter exists.
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

from fretsure.agent.arranger import (
    MAX_OUTPUT_TOKENS,
    MIN_OUTPUT_TOKENS,
    ArrangeGoal,
    arrangement_source_context,
    arrangement_source_context_sha256,
    proposal_output_token_budget,
)
from fretsure.arrange.propose import propose_fingerstyle
from fretsure.bench.observe import (
    CallContext,
    CallFailureCode,
    CallStage,
    ObservedCallError,
    current_call_context,
)
from fretsure.ir import MusicIR, snapshot_music_ir
from fretsure.llm.client import LLMClient, LLMIntegrityError, extract_json
from fretsure.oracle.input import (
    MAX_AGENT_CANDIDATES,
    MAX_BEATS_PER_BAR,
    MAX_TEMPO_BPM,
    MIN_TEMPO_BPM,
    OracleInputError,
    ensure_instrument_config,
    ensure_solver_domain,
)
from fretsure.oracle.profiles import Profile
from fretsure.solver.api import Infeasible, solve_fingering
from fretsure.tab import Tab, TabSchemaError, validated_tab_from_json

RAW_BASELINE_TEMPERATURE = 0.8
PURE_SOLVER_BASELINE_SLOTS = 10
LICENSE_AUDITED_REPRODUCIBLE_ADAPTER_ABSENT = "LICENSE_AUDITED_REPRODUCIBLE_ADAPTER_ABSENT"

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_LEGACY_RAW_SYSTEM = (
    "You are a guitar tablature writer. Output ONLY a JSON tab in this exact schema: "
    '{"tuning": [40,45,50,55,59,64], "capo": 0, "notes": [{"onset": "<fraction>", '
    '"duration": "<fraction>", "string": <0-5>, "fret": <int>, "left_finger": <0-4>, '
    '"right_finger": "p|i|m|a"}, ...]}. string 0 = lowest-pitched.'
)
_RAW_SYSTEM = (
    "You are a guitar tablature writer. Output ONLY one JSON tab in this exact schema: "
    '{"tuning": [<six requested open-string MIDI integers>], "capo": <requested capo>, '
    '"notes": [{"onset": "<fraction>", "duration": "<fraction>", "string": <0-5>, '
    '"fret": <int>, "left_finger": <0-4>, "right_finger": "p|i|m|a"}, ...]}. '
    "string 0 = lowest-pitched. Do not include prose or Markdown."
)


class RawStatus(StrEnum):
    """Intent-to-treat outcome of one scheduled raw-baseline call."""

    VALID_TAB = "VALID_TAB"
    PARSE_FAILED = "PARSE_FAILED"
    CALL_FAILED = "CALL_FAILED"


class RawParseCode(StrEnum):
    """Stable failure phase after a raw model call itself succeeded."""

    NO_JSON_OBJECT = "NO_JSON_OBJECT"
    TAB_SCHEMA_INVALID = "TAB_SCHEMA_INVALID"
    TAB_DOMAIN_INVALID = "TAB_DOMAIN_INVALID"
    INSTRUMENT_MISMATCH = "INSTRUMENT_MISMATCH"


class PureSolverStatus(StrEnum):
    """The exhaustive type of one deterministic B2 result."""

    TAB = "TAB"
    INFEASIBLE = "INFEASIBLE"


class BaselineId(StrEnum):
    """Optional comparison adapters whose absence must remain explicit."""

    B3 = "B3"
    B4 = "B4"


class BaselineAvailabilityStatus(StrEnum):
    """Whether a reproducible adapter may contribute comparison rows."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class BaselineAvailability:
    """One non-row availability record for an optional external baseline."""

    baseline_id: BaselineId
    status: BaselineAvailabilityStatus
    reason: str | None

    def __post_init__(self) -> None:
        if type(self.baseline_id) is not BaselineId:
            raise ValueError("baseline_id must be a BaselineId")
        if type(self.status) is not BaselineAvailabilityStatus:
            raise ValueError("status must be a BaselineAvailabilityStatus")
        if self.status is BaselineAvailabilityStatus.UNAVAILABLE:
            if self.reason != LICENSE_AUDITED_REPRODUCIBLE_ADAPTER_ABSENT:
                raise ValueError("unavailable optional baselines require the frozen reason")
        elif self.reason is not None:
            raise ValueError("available optional baselines cannot carry an unavailable reason")


B3_AVAILABILITY = BaselineAvailability(
    BaselineId.B3,
    BaselineAvailabilityStatus.UNAVAILABLE,
    LICENSE_AUDITED_REPRODUCIBLE_ADAPTER_ABSENT,
)
B4_AVAILABILITY = BaselineAvailability(
    BaselineId.B4,
    BaselineAvailabilityStatus.UNAVAILABLE,
    LICENSE_AUDITED_REPRODUCIBLE_ADAPTER_ABSENT,
)
OPTIONAL_BASELINE_AVAILABILITY = (B3_AVAILABILITY, B4_AVAILABILITY)


@dataclass(frozen=True, slots=True)
class RawObservationKey:
    """Logical observation identity only; usage remains in observation records."""

    run_id: str
    logical_call_id: str
    call_index: int

    def __post_init__(self) -> None:
        if type(self.run_id) is not str or not self.run_id:
            raise ValueError("run_id must be a non-empty exact string")
        if type(self.logical_call_id) is not str or not self.logical_call_id:
            raise ValueError("logical_call_id must be a non-empty exact string")
        if type(self.call_index) is not int or self.call_index < 0:
            raise ValueError("call_index must be an exact non-negative integer")


@dataclass(frozen=True, slots=True)
class RawBaselineRequest:
    """Frozen visible request and validation contract for one raw sample."""

    system: str
    user: str
    max_tokens: int
    temperature: float
    tuning: tuple[int, ...]
    capo: int
    tempo_bpm: float
    beats_per_bar: int
    source_context_sha256: str
    profile_fingerprint: str

    def __post_init__(self) -> None:
        if type(self.system) is not str or not self.system:
            raise ValueError("system must be a non-empty exact string")
        if type(self.user) is not str or not self.user:
            raise ValueError("user must be a non-empty exact string")
        if (
            type(self.max_tokens) is not int
            or not MIN_OUTPUT_TOKENS <= self.max_tokens <= MAX_OUTPUT_TOKENS
        ):
            raise ValueError("max_tokens must use the bounded proposal policy")
        if type(self.temperature) is not float or self.temperature != RAW_BASELINE_TEMPERATURE:
            raise ValueError("raw baseline temperature must be exactly 0.8")
        if (
            type(self.tuning) is not tuple
            or len(self.tuning) != 6
            or any(type(pitch) is not int for pitch in self.tuning)
        ):
            raise ValueError("tuning must be an exact six-integer tuple")
        if type(self.capo) is not int or self.capo < 0:
            raise ValueError("capo must be an exact non-negative integer")
        if (
            type(self.tempo_bpm) is not float
            or not math.isfinite(self.tempo_bpm)
            or not MIN_TEMPO_BPM <= self.tempo_bpm <= MAX_TEMPO_BPM
        ):
            raise ValueError("tempo_bpm must be a finite supported float")
        if type(self.beats_per_bar) is not int or not 1 <= self.beats_per_bar <= MAX_BEATS_PER_BAR:
            raise ValueError("beats_per_bar must be an exact supported integer")
        for field, value in (
            ("source_context_sha256", self.source_context_sha256),
            ("profile_fingerprint", self.profile_fingerprint),
        ):
            if type(value) is not str or _SHA256.fullmatch(value) is None:
                raise ValueError(f"{field} must be one lowercase SHA-256 digest")


@dataclass(frozen=True, slots=True)
class RawLLMOutcome:
    """One raw ITT outcome without copied provider usage or fallback output."""

    sample_index: int
    status: RawStatus
    tab: Tab | None
    parse_code: RawParseCode | None
    call_failure_code: CallFailureCode | None
    llm_calls: int
    source_context_sha256: str
    observation_key: RawObservationKey

    def __post_init__(self) -> None:
        if type(self.sample_index) is not int or not 0 <= self.sample_index < MAX_AGENT_CANDIDATES:
            raise ValueError("sample_index must be an exact bounded integer")
        if type(self.status) is not RawStatus:
            raise ValueError("status must be a RawStatus")
        if type(self.llm_calls) is not int or self.llm_calls != 1:
            raise ValueError("a raw outcome must contain exactly one logical call")
        if (
            type(self.source_context_sha256) is not str
            or _SHA256.fullmatch(self.source_context_sha256) is None
        ):
            raise ValueError("source_context_sha256 must be one lowercase SHA-256 digest")
        if type(self.observation_key) is not RawObservationKey:
            raise ValueError("observation_key must be a RawObservationKey")
        if self.status is RawStatus.VALID_TAB:
            if type(self.tab) is not Tab or self.parse_code is not None:
                raise ValueError("VALID_TAB requires exactly one validated Tab")
            if self.call_failure_code is not None:
                raise ValueError("VALID_TAB cannot carry a call failure")
        elif self.status is RawStatus.PARSE_FAILED:
            if self.tab is not None or type(self.parse_code) is not RawParseCode:
                raise ValueError("PARSE_FAILED requires exactly one RawParseCode")
            if self.call_failure_code is not None:
                raise ValueError("PARSE_FAILED cannot carry a call failure")
        elif (
            self.tab is not None
            or self.parse_code is not None
            or type(self.call_failure_code) is not CallFailureCode
        ):
            raise ValueError("CALL_FAILED requires exactly one CallFailureCode")

    @property
    def fallback_assisted(self) -> bool:
        """Raw collection never substitutes an output or resamples a failure."""

        return False


@dataclass(frozen=True, slots=True)
class PureSolverOutcome:
    """One actual B2 solve, reusable by reference for deterministic comparisons."""

    status: PureSolverStatus
    tab: Tab | None
    infeasible: Infeasible | None
    solver_calls: int = 1
    llm_calls: int = 0

    def __post_init__(self) -> None:
        if type(self.status) is not PureSolverStatus:
            raise ValueError("status must be a PureSolverStatus")
        if (self.tab is None) == (self.infeasible is None):
            raise ValueError("pure solver outcome requires exactly one solver result")
        if self.status is PureSolverStatus.TAB:
            if type(self.tab) is not Tab or self.infeasible is not None:
                raise ValueError("TAB status requires exactly one Tab")
        elif self.tab is not None or not isinstance(self.infeasible, Infeasible):
            raise ValueError("INFEASIBLE status requires exactly one Infeasible result")
        if type(self.solver_calls) is not int or self.solver_calls != 1:
            raise ValueError("pure solver baseline must run exactly once")
        if type(self.llm_calls) is not int or self.llm_calls != 0:
            raise ValueError("pure solver baseline cannot make LLM calls")


class RawCallScopeFactory(Protocol):
    """Build the mandatory RAW observation scope for one scheduled sample."""

    def __call__(
        self,
        stage: Literal["raw"],
        candidate_index: int,
        stage_ordinal: int,
    ) -> AbstractContextManager[object]: ...


class RawCallScopeError(LLMIntegrityError):
    """The formal raw observation scope violated its fail-closed contract."""


def build_raw_baseline_request(
    ir: MusicIR,
    goal: ArrangeGoal,
    profile: Profile,
) -> RawBaselineRequest:
    """Build the preregistered raw request from the same facts/policy as proposal."""

    ir = snapshot_music_ir(ir)
    notes, tuning, capo, profile, tempo_bpm = ensure_solver_domain(
        ir.notes,
        goal.tuning,
        goal.capo,
        profile,
        tempo_bpm=goal.tempo_bpm,
    )
    ir = MusicIR(notes, tuple(ir.chords), ir.meta)
    context = arrangement_source_context(ir)
    low = min(tuning) + capo
    high = max(tuning) + capo + 22
    tuning_text = ",".join(str(pitch) for pitch in tuning)
    user = (
        f"{context}\n"
        f"Effective arrangement tempo: {tempo_bpm} BPM.\n\n"
        f"Requested tuning (open-string MIDI, low to high): [{tuning_text}]. "
        f"Requested capo: {capo}. "
        f"Playable range on this tuning: MIDI {low}-{high}. "
        "Keep at most 4 notes sounding at the same onset. "
        f"Goal: {goal.style}, {goal.tier} difficulty. "
        "Write the fingerstyle tab directly now."
    )
    return RawBaselineRequest(
        system=_RAW_SYSTEM,
        user=user,
        max_tokens=proposal_output_token_budget(ir),
        temperature=RAW_BASELINE_TEMPERATURE,
        tuning=tuning,
        capo=capo,
        tempo_bpm=tempo_bpm,
        beats_per_bar=ir.meta.time_sig[0],
        source_context_sha256=arrangement_source_context_sha256(ir),
        profile_fingerprint=profile.fingerprint,
    )


def _scope_exit(
    exit_scope: object,
    exc_type: type[BaseException] | None,
    exc: BaseException | None,
    traceback: object,
) -> None:
    if not callable(exit_scope):
        raise RawCallScopeError("raw model-call scope has no callable exit boundary")
    try:
        suppressed = exit_scope(exc_type, exc, traceback)
        if suppressed:
            raise RawCallScopeError("raw model-call scopes may not suppress call failures")
    except LLMIntegrityError:
        raise
    except Exception:
        raise RawCallScopeError("raw model-call scope exit failed") from None


@contextmanager
def _raw_call_scope(
    factory: RawCallScopeFactory,
    *,
    sample_index: int,
) -> Iterator[CallContext]:
    if type(sample_index) is not int or not 0 <= sample_index < MAX_AGENT_CANDIDATES:
        raise RawCallScopeError("raw sample index is outside the bounded schedule")
    if factory is None:
        raise RawCallScopeError("formal raw collection requires an observation scope")
    if current_call_context() is not None:
        raise RawCallScopeError("raw model-call scopes cannot inherit an outer identity")
    try:
        manager = factory("raw", sample_index, 0)
        enter_scope = manager.__enter__
        exit_scope = manager.__exit__
        enter_scope()
    except LLMIntegrityError:
        raise
    except Exception:
        raise RawCallScopeError("raw model-call scope entry failed") from None

    try:
        context = current_call_context()
        if (
            type(context) is not CallContext
            or context.stage is not CallStage.RAW
            or context.stage_ordinal != 0
            or context.sample_index != sample_index
            or context.candidate_index != sample_index
        ):
            raise RawCallScopeError("raw model-call scope installed the wrong identity")
    except BaseException:
        exc_type, exc, traceback = sys.exc_info()
        _scope_exit(exit_scope, exc_type, exc, traceback)
        raise

    try:
        yield context
    except BaseException:
        exc_type, exc, traceback = sys.exc_info()
        _scope_exit(exit_scope, exc_type, exc, traceback)
        raise
    else:
        _scope_exit(exit_scope, None, None, None)


def _observation_key(context: CallContext) -> RawObservationKey:
    return RawObservationKey(
        run_id=context.run_id,
        logical_call_id=context.logical_call_id,
        call_index=context.call_index,
    )


def _raw_outcome(
    request: RawBaselineRequest,
    context: CallContext,
    sample_index: int,
    status: RawStatus,
    *,
    tab: Tab | None = None,
    parse_code: RawParseCode | None = None,
    call_failure_code: CallFailureCode | None = None,
) -> RawLLMOutcome:
    return RawLLMOutcome(
        sample_index=sample_index,
        status=status,
        tab=tab,
        parse_code=parse_code,
        call_failure_code=call_failure_code,
        llm_calls=1,
        source_context_sha256=request.source_context_sha256,
        observation_key=_observation_key(context),
    )


def _balanced_json_object_text(reply: object) -> str:
    """Return the first balanced object slice without decoding away duplicate keys."""

    if type(reply) is not str:
        raise TypeError("raw model reply must be an exact string")
    text = reply
    start = text.find("{")
    if start < 0:
        raise ValueError("raw model reply has no JSON object")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise ValueError("raw model reply has no balanced JSON object")


def collect_raw_llm_baseline(
    request: RawBaselineRequest,
    llm: LLMClient,
    profile: Profile,
    *,
    sample_index: int,
    call_scope_factory: RawCallScopeFactory,
) -> RawLLMOutcome:
    """Collect one raw sample with no fallback, repair, verifier gate, or resampling."""

    if type(request) is not RawBaselineRequest:
        raise ValueError("request must be an exact RawBaselineRequest")
    tuning, capo, profile, tempo_bpm = ensure_instrument_config(
        request.tuning,
        request.capo,
        profile,
        tempo_bpm=request.tempo_bpm,
    )
    if profile.fingerprint != request.profile_fingerprint:
        raise ValueError("raw request profile does not match the validation profile")

    with _raw_call_scope(call_scope_factory, sample_index=sample_index) as context:
        try:
            reply = llm.complete(
                system=request.system,
                user=request.user,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
            )
        except LLMIntegrityError:
            raise
        except Exception as exc:  # noqa: BLE001 - outcome retains only a stable code
            failure_code = (
                exc.code if isinstance(exc, ObservedCallError) else CallFailureCode.DELEGATE_FAILED
            )
            return _raw_outcome(
                request,
                context,
                sample_index,
                RawStatus.CALL_FAILED,
                call_failure_code=failure_code,
            )

    try:
        object_text = _balanced_json_object_text(reply)
    except (TypeError, ValueError):
        return _raw_outcome(
            request,
            context,
            sample_index,
            RawStatus.PARSE_FAILED,
            parse_code=RawParseCode.NO_JSON_OBJECT,
        )
    try:
        tab = validated_tab_from_json(
            object_text,
            profile=profile,
            tempo_bpm=tempo_bpm,
            beats_per_bar=request.beats_per_bar,
        )
    except TabSchemaError:
        return _raw_outcome(
            request,
            context,
            sample_index,
            RawStatus.PARSE_FAILED,
            parse_code=RawParseCode.TAB_SCHEMA_INVALID,
        )
    except OracleInputError:
        return _raw_outcome(
            request,
            context,
            sample_index,
            RawStatus.PARSE_FAILED,
            parse_code=RawParseCode.TAB_DOMAIN_INVALID,
        )
    if tab.tuning != tuning or tab.capo != capo:
        return _raw_outcome(
            request,
            context,
            sample_index,
            RawStatus.PARSE_FAILED,
            parse_code=RawParseCode.INSTRUMENT_MISMATCH,
        )
    return _raw_outcome(
        request,
        context,
        sample_index,
        RawStatus.VALID_TAB,
        tab=tab,
    )


def run_pure_solver_baseline(
    ir: MusicIR,
    goal: ArrangeGoal,
    profile: Profile,
) -> PureSolverOutcome:
    """Run the deterministic B2 proposal and solver once for one item."""

    target = propose_fingerstyle(
        ir,
        goal.tuning,
        goal.capo,
        profile=profile,
        tempo_bpm=goal.tempo_bpm,
    )
    solved = solve_fingering(
        target,
        goal.tuning,
        goal.capo,
        profile,
        tempo_bpm=goal.tempo_bpm,
        beats_per_bar=ir.meta.time_sig[0],
    )
    if isinstance(solved, Tab):
        return PureSolverOutcome(PureSolverStatus.TAB, solved, None)
    return PureSolverOutcome(PureSolverStatus.INFEASIBLE, None, solved)


def repeat_pure_solver_outcome(
    outcome: PureSolverOutcome,
    slots: int = PURE_SOLVER_BASELINE_SLOTS,
) -> tuple[PureSolverOutcome, ...]:
    """Expose deterministic comparison slots as references to the one actual solve."""

    if type(outcome) is not PureSolverOutcome:
        raise ValueError("outcome must be a PureSolverOutcome")
    if type(slots) is not int or not 1 <= slots <= MAX_AGENT_CANDIDATES:
        raise ValueError("slots must be an exact bounded positive integer")
    return (outcome,) * slots


def baseline_raw_llm(
    ir: MusicIR,
    goal: ArrangeGoal,
    llm: LLMClient,
    profile: Profile,
) -> Tab | None:
    """Compatibility wrapper retaining the historical unobserved B1 API."""

    melody = "; ".join(
        f"onset={note.onset} pitch={note.pitch}" for note in ir.notes if note.voice == "melody"
    )
    try:
        reply = llm.complete(
            system=_LEGACY_RAW_SYSTEM,
            user=f"Melody: {melody}\nWrite a fingerstyle tab.",
            max_tokens=2048,
        )
        return validated_tab_from_json(
            json.dumps(extract_json(reply)),
            profile=profile,
            tempo_bpm=goal.tempo_bpm,
        )
    except LLMIntegrityError:
        raise
    except (ValueError, KeyError, TypeError, RuntimeError):
        return None


def baseline_pure_solver(
    ir: MusicIR,
    goal: ArrangeGoal,
    profile: Profile,
) -> Tab | Infeasible:
    """Compatibility wrapper retaining the historical B2 result union."""

    outcome = run_pure_solver_baseline(ir, goal, profile)
    if outcome.tab is not None:
        return outcome.tab
    assert outcome.infeasible is not None
    return outcome.infeasible


__all__ = [
    "B3_AVAILABILITY",
    "B4_AVAILABILITY",
    "LICENSE_AUDITED_REPRODUCIBLE_ADAPTER_ABSENT",
    "OPTIONAL_BASELINE_AVAILABILITY",
    "PURE_SOLVER_BASELINE_SLOTS",
    "RAW_BASELINE_TEMPERATURE",
    "BaselineAvailability",
    "BaselineAvailabilityStatus",
    "BaselineId",
    "PureSolverOutcome",
    "PureSolverStatus",
    "RawBaselineRequest",
    "RawCallScopeError",
    "RawCallScopeFactory",
    "RawLLMOutcome",
    "RawObservationKey",
    "RawParseCode",
    "RawStatus",
    "baseline_pure_solver",
    "baseline_raw_llm",
    "build_raw_baseline_request",
    "collect_raw_llm_baseline",
    "repeat_pure_solver_outcome",
    "run_pure_solver_baseline",
]
