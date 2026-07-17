"""Pure execution-receipt declarations for benchmark artifacts.

Git cleanliness and SHA equality are release-gate responsibilities.  Runtime code
records the already accepted execution SHA; it does not inspect a checkout, mutate
``sys.path``, spawn Git, or perform filesystem/network I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import NoReturn

_GIT_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class ExecutionMode(StrEnum):
    LIVE_COLLECTION = "live_collection"
    INSTALLED_WHEEL_REPLAY = "installed_wheel_replay"


class ExecutionBinding(StrEnum):
    EXTERNAL_GIT_GATE = "external_git_gate"
    INSTALLED_WHEEL_RECORD = "installed_wheel_record"


class ExecutionProvenanceError(ValueError):
    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid execution provenance {field}: {detail}")


def _fail(field: str, detail: str) -> NoReturn:
    raise ExecutionProvenanceError(field, detail)


def _git_sha(value: object, field: str) -> str:
    if type(value) is not str or _GIT_SHA.fullmatch(value) is None:
        _fail(field, "must be an exact lowercase Git commit id")
    return value


@dataclass(frozen=True, slots=True)
class ExecutionProvenance:
    mode: ExecutionMode
    execution_git_sha: str | None
    binding: ExecutionBinding

    def __post_init__(self) -> None:
        if type(self.mode) is not ExecutionMode:
            _fail("mode", "must be an exact ExecutionMode")
        if type(self.binding) is not ExecutionBinding:
            _fail("binding", "must be an exact ExecutionBinding")
        if self.mode is ExecutionMode.LIVE_COLLECTION:
            _git_sha(self.execution_git_sha, "execution_git_sha")
            if self.binding is not ExecutionBinding.EXTERNAL_GIT_GATE:
                _fail("binding", "live collection requires the external Git gate")
            return
        if (
            self.mode is not ExecutionMode.INSTALLED_WHEEL_REPLAY
            or self.execution_git_sha is not None
            or self.binding is not ExecutionBinding.INSTALLED_WHEEL_RECORD
        ):
            _fail("receipt", "installed-wheel replay fields are inconsistent")

    def public_snapshot(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "execution_git_sha": self.execution_git_sha,
            "binding": self.binding.value,
        }


def declare_live_execution(execution_git_sha: object) -> ExecutionProvenance:
    """Record a SHA that the external runner-ready gate has already accepted."""

    return ExecutionProvenance(
        ExecutionMode.LIVE_COLLECTION,
        _git_sha(execution_git_sha, "execution_git_sha"),
        ExecutionBinding.EXTERNAL_GIT_GATE,
    )


def declare_installed_wheel_replay() -> ExecutionProvenance:
    """Declare replay whose wheel/RECORD binding is supplied by its manifest."""

    return ExecutionProvenance(
        ExecutionMode.INSTALLED_WHEEL_REPLAY,
        None,
        ExecutionBinding.INSTALLED_WHEEL_RECORD,
    )


__all__ = [
    "ExecutionBinding",
    "ExecutionMode",
    "ExecutionProvenance",
    "ExecutionProvenanceError",
    "declare_installed_wheel_replay",
    "declare_live_execution",
]
