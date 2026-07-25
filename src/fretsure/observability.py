"""One optional OpenTelemetry seam for product execution spans.

The public replay trace remains authoritative product evidence. This module emits
operational spans only and never creates a second local trace store.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Protocol, cast

from fretsure.agent.model_calls import ModelCallStage

OTEL_TRACER_NAME = "fretsure.product"
OTEL_INSTRUMENTATION_VERSION = "fretsure-otel@0.1.0"


class _Tracer(Protocol):
    def start_as_current_span(
        self,
        name: str,
        *,
        attributes: dict[str, object],
    ) -> AbstractContextManager[object]: ...


class ProductTelemetry:
    """Small adapter shared by the arrangement span and model-call hooks."""

    def __init__(self, tracer: _Tracer | None) -> None:
        self._tracer = tracer

    def span(
        self,
        name: str,
        *,
        attributes: dict[str, object],
    ) -> AbstractContextManager[object]:
        if self._tracer is None:
            return nullcontext()
        return self._tracer.start_as_current_span(name, attributes=attributes)

    def model_call_scope(
        self,
        stage: ModelCallStage,
        candidate_index: int,
        stage_ordinal: int,
    ) -> AbstractContextManager[object]:
        return self.span(
            "fretsure.model_call",
            attributes={
                "fretsure.model.stage": stage,
                "fretsure.model.candidate_index": candidate_index,
                "fretsure.model.stage_ordinal": stage_ordinal,
            },
        )


def product_telemetry() -> ProductTelemetry:
    """Use the process OpenTelemetry provider, or a true no-op when absent."""

    try:
        from opentelemetry import trace
    except ImportError:
        return ProductTelemetry(None)
    tracer = trace.get_tracer(OTEL_TRACER_NAME, OTEL_INSTRUMENTATION_VERSION)
    return ProductTelemetry(cast(_Tracer, tracer))


__all__ = [
    "OTEL_INSTRUMENTATION_VERSION",
    "OTEL_TRACER_NAME",
    "ProductTelemetry",
    "product_telemetry",
]
