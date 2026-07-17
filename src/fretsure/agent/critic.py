"""Musicality critic — the one genuine second agent.

The oracle cannot judge whether an arrangement is *good*; this LLM critic rates
voice leading, bass motion, and texture. It judges TASTE only, never playability
(that stays with the deterministic oracle). Malformed output degrades to a
neutral 0.5 so it can never block the pipeline.
"""

import math
from dataclasses import dataclass
from enum import StrEnum

from fretsure.agent.model_calls import ModelCallScopeFactory, model_call_scope
from fretsure.ir import MusicIR, snapshot_music_ir
from fretsure.llm.client import LLMClient, LLMIntegrityError, extract_json
from fretsure.render.ascii import render_ascii
from fretsure.tab import Tab

_CRITIC_SYSTEM = (
    "You are a guitar-arrangement critic. Judge ONLY musicality — voice leading, bass "
    "motion, texture — NOT playability (a separate deterministic oracle handles that). "
    'Reply with ONLY JSON: {"overall": 0..1, "voice_leading": 0..1, "bass_motion": 0..1, '
    '"texture": 0..1, "notes": "<one sentence>"}.'
)

MAX_CRITIC_SCORE_INTEGER_BITS = 64
CRITIC_MAX_TOKENS = 512


@dataclass(frozen=True)
class CriticScore:
    overall: float
    voice_leading: float
    bass_motion: float
    texture: float
    notes: str


class CriticStatus(StrEnum):
    """Stable provenance for a critic score used by product selection."""

    LLM_SUCCESS = "LLM_SUCCESS"
    PARSE_VALIDATION_FALLBACK = "PARSE_VALIDATION_FALLBACK"
    CALL_FAILURE_FALLBACK = "CALL_FAILURE_FALLBACK"


@dataclass(frozen=True, slots=True)
class CriticOutcome:
    """One critic score plus explicit success/fallback provenance."""

    score: CriticScore
    status: CriticStatus
    llm_calls: int

    def __post_init__(self) -> None:
        if type(self.score) is not CriticScore:
            raise ValueError("score must be an exact CriticScore")
        if type(self.status) is not CriticStatus:
            raise ValueError("status must be a CriticStatus")
        if type(self.llm_calls) is not int or self.llm_calls != 1:
            raise ValueError("a critic outcome must contain exactly one logical LLM call")

    @property
    def fallback_assisted(self) -> bool:
        return self.status is not CriticStatus.LLM_SUCCESS


def _clamp01(value: object) -> float:
    """Validate one exact finite numeric score, then preserve historical clamping."""

    if type(value) is int:
        integer = value
        if integer.bit_length() > MAX_CRITIC_SCORE_INTEGER_BITS:
            raise ValueError("critic score integer exceeds the bounded numeric envelope")
        if integer <= 0:
            return 0.0
        if integer >= 1:
            return 1.0
        return float(integer)
    if type(value) is float:
        number = value
        if not math.isfinite(number):
            raise ValueError("critic score must be finite")
        return max(0.0, min(1.0, number))
    raise ValueError("critic score must be an exact integer or float")


def _neutral_outcome(status: CriticStatus) -> CriticOutcome:
    return CriticOutcome(
        CriticScore(0.5, 0.5, 0.5, 0.5, "unparsed critic output; neutral score"),
        status,
        1,
    )


def critique_outcome(
    ir: MusicIR,
    tab: Tab,
    llm: LLMClient,
    *,
    call_scope_factory: ModelCallScopeFactory | None = None,
    candidate_index: int | None = None,
) -> CriticOutcome:
    """Return the product score and lossless benchmark-visible call status."""

    ir = snapshot_music_ir(ir)
    user = f"Key {ir.meta.key}. Arrangement tab:\n{render_ascii(tab)}\n\nRate its musicality."
    try:
        with model_call_scope(
            call_scope_factory,
            stage="critic",
            stage_ordinal=0,
            candidate_index=candidate_index,
        ):
            reply = llm.complete(
                system=_CRITIC_SYSTEM,
                user=user,
                max_tokens=CRITIC_MAX_TOKENS,
            )
    except LLMIntegrityError:
        raise
    except (ValueError, KeyError, TypeError, RuntimeError):
        return _neutral_outcome(CriticStatus.CALL_FAILURE_FALLBACK)

    try:
        obj = extract_json(reply)
        overall = _clamp01(obj["overall"])
        return CriticOutcome(
            CriticScore(
                overall=overall,
                voice_leading=_clamp01(obj.get("voice_leading", overall)),
                bass_motion=_clamp01(obj.get("bass_motion", overall)),
                texture=_clamp01(obj.get("texture", overall)),
                notes=str(obj.get("notes", "")),
            ),
            CriticStatus.LLM_SUCCESS,
            1,
        )
    except (ValueError, KeyError, TypeError, RuntimeError, OverflowError):
        return _neutral_outcome(CriticStatus.PARSE_VALIDATION_FALLBACK)


def critique(
    ir: MusicIR,
    tab: Tab,
    llm: LLMClient,
    *,
    call_scope_factory: ModelCallScopeFactory | None = None,
    candidate_index: int | None = None,
) -> CriticScore:
    """Compatibility wrapper retaining the historical score-only API."""

    return critique_outcome(
        ir,
        tab,
        llm,
        call_scope_factory=call_scope_factory,
        candidate_index=candidate_index,
    ).score
