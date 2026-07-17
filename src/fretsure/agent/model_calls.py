"""Stage-scoped hooks for observable agent model calls.

The production agent does not depend on benchmark storage.  Instead, callers may
inject a small context-manager factory that is entered immediately around each
logical model call.  The default is a true no-op, preserving the existing product
surface while giving the benchmark an explicit proposal/repair/critic boundary.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Literal, Protocol, cast

from fretsure.llm.client import LLMIntegrityError
from fretsure.oracle.input import MAX_AGENT_CANDIDATES, MAX_AGENT_REPAIR_ITERS

ModelCallStage = Literal["proposal", "repair", "critic"]


class ModelCallScopeFactory(Protocol):
    """Build one non-suppressing scope for a logical agent model call."""

    def __call__(
        self,
        stage: ModelCallStage,
        candidate_index: int,
        stage_ordinal: int,
    ) -> AbstractContextManager[object]: ...


class ModelCallScopeError(LLMIntegrityError):
    """The injected observation scope failed its fail-closed contract."""


def _validate_scope_identity(
    stage: object,
    stage_ordinal: object,
    candidate_index: object,
) -> tuple[ModelCallStage, int, int]:
    if type(stage) is not str or stage not in {"proposal", "repair", "critic"}:
        raise ModelCallScopeError("model-call stage must be proposal, repair, or critic")
    if (
        type(stage_ordinal) is not int
        or not 0 <= stage_ordinal <= MAX_AGENT_REPAIR_ITERS
        or (stage != "repair" and stage_ordinal != 0)
    ):
        raise ModelCallScopeError("model-call stage ordinal is outside the bounded schedule")
    if (
        type(candidate_index) is not int
        or not 0 <= candidate_index < MAX_AGENT_CANDIDATES
    ):
        raise ModelCallScopeError("model-call candidate index is outside the bounded schedule")
    return cast(ModelCallStage, stage), stage_ordinal, candidate_index


def _scope_exit(
    exit_scope: object,
    exc_type: type[BaseException] | None,
    exc: BaseException | None,
    traceback: object,
) -> None:
    if not callable(exit_scope):
        raise ModelCallScopeError("model-call scope has no callable exit boundary")
    try:
        suppressed = exit_scope(exc_type, exc, traceback)
        if suppressed:
            raise ModelCallScopeError("model-call scopes may not suppress call failures")
    except LLMIntegrityError:
        raise
    except Exception:
        raise ModelCallScopeError("model-call scope exit failed") from None


@contextmanager
def model_call_scope(
    factory: ModelCallScopeFactory | None,
    *,
    stage: ModelCallStage,
    stage_ordinal: int,
    candidate_index: int | None,
) -> Iterator[None]:
    """Enter one injected scope without converting scope failures into fallbacks."""

    if factory is None:
        yield
        return

    exact_stage, exact_ordinal, exact_candidate = _validate_scope_identity(
        stage,
        stage_ordinal,
        candidate_index,
    )
    try:
        manager = factory(exact_stage, exact_candidate, exact_ordinal)
        enter_scope = manager.__enter__
        exit_scope = manager.__exit__
        enter_scope()
    except LLMIntegrityError:
        raise
    except Exception:
        raise ModelCallScopeError("model-call scope entry failed") from None

    try:
        yield
    except BaseException:
        exc_type, exc, traceback = sys.exc_info()
        _scope_exit(exit_scope, exc_type, exc, traceback)
        raise
    else:
        _scope_exit(exit_scope, None, None, None)


__all__ = [
    "ModelCallScopeError",
    "ModelCallScopeFactory",
    "ModelCallStage",
    "model_call_scope",
]
