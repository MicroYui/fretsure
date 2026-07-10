"""Explainable execution trace: the sequence of plan/oracle/edit/re-check steps.

Consumed by the Plan 6 "watch the agent think" viewer and by benchmark repair
stats. Serializes to JSONL (Fractions render as strings).
"""

import json
from dataclasses import dataclass, field
from typing import Any, Literal

StepKind = Literal[
    "PLAN", "PROPOSE", "SOLVE", "ORACLE", "REASON", "EDIT", "RECHECK", "SELECT"
]


@dataclass(frozen=True)
class TraceStep:
    kind: StepKind
    detail: str
    data: dict[str, Any]


@dataclass
class Trace:
    steps: list[TraceStep] = field(default_factory=list)

    def add(self, kind: StepKind, detail: str, **data: Any) -> None:
        self.steps.append(TraceStep(kind, detail, data))

    def to_jsonl(self) -> str:
        return "\n".join(
            json.dumps(
                {"kind": s.kind, "detail": s.detail, "data": s.data}, default=str
            )
            for s in self.steps
        )
