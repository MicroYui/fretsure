"""Dependency-light contracts for Fretsure's application-service boundary.

The HTTP and MCP adapters intentionally depend on these immutable values rather
than reaching into the importer, pipeline, oracle, or solver independently.
Transport-specific concepts (HTTP status codes, MCP errors, engine selection)
do not belong here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from fretsure.arrange.revision import SectionSelection
from fretsure.arrange.styles import DEFAULT_ARRANGEMENT_STYLE
from fretsure.difficulty.checker import TierResult
from fretsure.difficulty.estimate import PublishedGradeEstimate
from fretsure.difficulty.tiers import Tier
from fretsure.geometry import STANDARD_TUNING
from fretsure.importers.contracts import ImportSuccess
from fretsure.ir import Note
from fretsure.metrics.fidelity import FaithfulnessGate
from fretsure.oracle.core import OracleResult
from fretsure.oracle.profiles import Profile
from fretsure.solver.api import Infeasible
from fretsure.solver.technique import DEFAULT_TECHNIQUE_PROFILE
from fretsure.tab import Tab

SERVICE_VERSION = "fretsure-service@0.3.0"
PROFILE_REGISTRY_VERSION = "profile-registry@0.2.0"


class ApplicationCode(StrEnum):
    """Stable failure categories exposed by the application seam."""

    INVALID_ARGUMENT = "INVALID_ARGUMENT"
    INVALID_OPTIONS = "INVALID_OPTIONS"
    UNKNOWN_PROFILE = "UNKNOWN_PROFILE"
    IMPORT_REJECTED = "IMPORT_REJECTED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    TAB_INPUT_REJECTED = "TAB_INPUT_REJECTED"
    TARGET_INPUT_REJECTED = "TARGET_INPUT_REJECTED"
    SOLVER_INPUT_REJECTED = "SOLVER_INPUT_REJECTED"
    UNSUPPORTED_RENDER_FORMAT = "UNSUPPORTED_RENDER_FORMAT"
    LLM_CONFIGURATION_REJECTED = "LLM_CONFIGURATION_REJECTED"
    ARRANGEMENT_FAILED = "ARRANGEMENT_FAILED"
    CHECK_FAILED = "CHECK_FAILED"
    SOLVER_FAILED = "SOLVER_FAILED"
    RENDER_FAILED = "RENDER_FAILED"
    SERIALIZATION_FAILED = "SERIALIZATION_FAILED"


@dataclass(frozen=True, slots=True)
class ApplicationDiagnostic:
    """One bounded, machine-readable reason attached to an application error."""

    code: str
    path: str
    message: str


class ApplicationError(ValueError):
    """Safe typed failure suitable for translation by an untrusted adapter.

    ``detail`` is deliberately a stable application-authored sentence.  Raw
    parser, model-provider, filesystem, or traceback text must never be placed
    in this object.
    """

    code: ApplicationCode
    path: str
    detail: str
    diagnostics: tuple[ApplicationDiagnostic, ...]

    def __init__(
        self,
        code: ApplicationCode,
        path: str,
        detail: str,
        diagnostics: tuple[ApplicationDiagnostic, ...] = (),
    ) -> None:
        self.code = code
        self.path = path
        self.detail = detail
        self.diagnostics = diagnostics
        super().__init__(f"{code.value} at {path}: {detail}")


@dataclass(frozen=True, slots=True)
class ArrangeOptions:
    """User-selectable controls for one score-arrangement request."""

    profile: str = "median"
    style: str = DEFAULT_ARRANGEMENT_STYLE
    difficulty_tier: str = "intermediate"
    technique_profile: str = DEFAULT_TECHNIQUE_PROFILE
    n: int = 1
    max_iters: int = 0
    use_critic: bool = False
    tempo_bpm: float | None = None


@dataclass(frozen=True, slots=True)
class CheckOptions:
    """Controls for a versioned playability check."""

    profile: str = "median"
    tempo_bpm: float = 90.0
    beats_per_bar: int = 4


@dataclass(frozen=True, slots=True)
class DifficultyOptions:
    """Controls for checking one canonical Tab against a named tier."""

    tier: str = "beginner"
    tempo_bpm: float = 90.0
    beats_per_bar: int = 4


@dataclass(frozen=True, slots=True)
class SolveOptions:
    """Explicit finite search budget and instrument configuration."""

    profile: str = "median"
    tuning: tuple[int, ...] = STANDARD_TUNING
    capo: int = 0
    tempo_bpm: float = 90.0
    beam: int = 16


@dataclass(frozen=True, slots=True)
class RenderOptions:
    """Controls for notation rendering; Plan 6A implements ASCII only."""

    format: str = "ascii"
    profile: str = "median"
    tempo_bpm: float = 90.0
    beats_per_bar: int = 4


ArrangeStatus = Literal["tab_produced", "no_fingering_within_budget"]
FingeringEditStatus = Literal["applied", "rejected", "unchanged"]
SectionRegenerationStatus = Literal["accepted", "preserved", "unchanged"]
SolveStatus = Literal["found", "not_found_within_budget"]


@dataclass(frozen=True, slots=True)
class VerifiedAlternative:
    """One independently checked best-of-N checkpoint and its actual work."""

    candidate_index: int
    tab: Tab
    oracle: OracleResult
    faithfulness: FaithfulnessGate
    ascii: str
    model_calls: int
    solver_calls: int
    proposed_additions: int
    accepted_additions: int
    proposal_status: str
    critic_status: str | None
    critic_overall: float | None

    def __post_init__(self) -> None:
        if (
            type(self.candidate_index) is not int
            or self.candidate_index < 0
            or type(self.tab) is not Tab
            or type(self.oracle) is not OracleResult
            or self.oracle.verdict != "GREEN"
            or type(self.faithfulness) is not FaithfulnessGate
            or type(self.ascii) is not str
            or type(self.model_calls) is not int
            or self.model_calls < 0
            or type(self.solver_calls) is not int
            or self.solver_calls < 0
            or type(self.proposed_additions) is not int
            or self.proposed_additions < 0
            or type(self.accepted_additions) is not int
            or not 0 <= self.accepted_additions <= self.proposed_additions
            or type(self.proposal_status) is not str
            or not self.proposal_status
        ):
            raise ValueError("invalid verified alternative fields")
        if self.critic_status is None:
            if self.critic_overall is not None:
                raise ValueError("critic value requires critic provenance")
        elif (
            type(self.critic_status) is not str
            or not self.critic_status
            or type(self.critic_overall) is not float
            or not math.isfinite(self.critic_overall)
            or not 0.0 <= self.critic_overall <= 1.0
        ):
            raise ValueError("invalid observed critic evidence")


@dataclass(frozen=True, slots=True)
class ArrangeOutcome:
    """Detached arrangement result; serialization is kept separate.

    The public trace is captured as canonical JSON text because the execution
    ``Trace`` builder is intentionally mutable.  No mutable pipeline object is
    retained after the service call returns.
    """

    status: ArrangeStatus
    imported: ImportSuccess
    target: tuple[Note, ...] | None
    tab: Tab | None
    oracle: OracleResult | None
    faithfulness: FaithfulnessGate | None
    ascii: str | None
    alternatives: tuple[VerifiedAlternative, ...]
    trace_document_json: str
    source_tempo_bpm: float
    effective_tempo_bpm: float
    options: ArrangeOptions
    profile: Profile
    model_id: str

    def __post_init__(self) -> None:
        if self.status not in {"tab_produced", "no_fingering_within_budget"}:
            raise ValueError("invalid arrangement outcome status")
        tab_produced = self.tab is not None
        if tab_produced != (self.status == "tab_produced"):
            raise ValueError("arrangement status and Tab presence disagree")
        if tab_produced != (self.target is not None):
            raise ValueError("arrangement target and Tab presence disagree")
        if tab_produced:
            if (
                type(self.tab) is not Tab
                or type(self.oracle) is not OracleResult
                or type(self.faithfulness) is not FaithfulnessGate
                or type(self.ascii) is not str
            ):
                raise ValueError("a produced Tab requires both gates and ASCII")
        elif any(value is not None for value in (self.oracle, self.faithfulness, self.ascii)):
            raise ValueError("a no-fingering outcome cannot contain product outputs")
        if (
            type(self.imported) is not ImportSuccess
            or (self.target is not None and type(self.target) is not tuple)
            or type(self.trace_document_json) is not str
            or type(self.options) is not ArrangeOptions
            or type(self.profile) is not Profile
            or type(self.model_id) is not str
            or type(self.alternatives) is not tuple
            or any(type(item) is not VerifiedAlternative for item in self.alternatives)
            or type(self.source_tempo_bpm) is not float
            or not math.isfinite(self.source_tempo_bpm)
            or type(self.effective_tempo_bpm) is not float
            or not math.isfinite(self.effective_tempo_bpm)
        ):
            raise ValueError("invalid arrangement outcome fields")
        if not tab_produced and self.alternatives:
            raise ValueError("a no-fingering outcome cannot contain alternatives")
        if len(self.alternatives) > self.options.n or len(
            {item.candidate_index for item in self.alternatives}
        ) != len(self.alternatives):
            raise ValueError("verified alternatives disagree with the requested pool")


@dataclass(frozen=True, slots=True)
class CheckOutcome:
    """A checked, canonical Tab and its oracle evidence."""

    tab: Tab
    oracle: OracleResult
    options: CheckOptions
    profile: Profile

    def __post_init__(self) -> None:
        if (
            type(self.tab) is not Tab
            or type(self.oracle) is not OracleResult
            or type(self.options) is not CheckOptions
            or type(self.profile) is not Profile
        ):
            raise ValueError("invalid check outcome fields")


@dataclass(frozen=True, slots=True)
class SectionRegenerationOutcome:
    """One transactional measure-scoped revision result."""

    status: SectionRegenerationStatus
    target: tuple[Note, ...]
    tab: Tab
    oracle: OracleResult
    faithfulness: FaithfulnessGate
    ascii: str
    selection: SectionSelection
    options: ArrangeOptions
    profile: Profile
    model_id: str
    proposal_status: str | None
    model_calls: int
    reason: str | None

    def __post_init__(self) -> None:
        if self.status not in {"accepted", "preserved", "unchanged"}:
            raise ValueError("invalid section regeneration status")
        if (
            type(self.target) is not tuple
            or type(self.tab) is not Tab
            or type(self.oracle) is not OracleResult
            or type(self.faithfulness) is not FaithfulnessGate
            or type(self.ascii) is not str
            or type(self.selection) is not SectionSelection
            or type(self.options) is not ArrangeOptions
            or type(self.profile) is not Profile
            or type(self.model_id) is not str
            or type(self.model_calls) is not int
            or self.model_calls not in (0, 1)
            or (self.proposal_status is not None and type(self.proposal_status) is not str)
            or (self.reason is not None and type(self.reason) is not str)
        ):
            raise ValueError("invalid section regeneration fields")
        if self.status == "accepted" and self.oracle.verdict != "GREEN":
            raise ValueError("accepted section regeneration must remain GREEN")
        if self.status == "accepted" and not self.faithfulness.passed:
            raise ValueError("accepted section regeneration must pass faithfulness")


@dataclass(frozen=True, slots=True)
class FingeringEditOutcome:
    """One transactional left-hand finger-number edit."""

    status: FingeringEditStatus
    tab: Tab
    oracle: OracleResult
    attempted_oracle: OracleResult
    ascii: str
    note_index: int
    before_finger: int
    requested_finger: int
    options: CheckOptions
    profile: Profile
    reason: str | None

    def __post_init__(self) -> None:
        if self.status not in {"applied", "rejected", "unchanged"}:
            raise ValueError("invalid fingering edit status")
        if (
            type(self.tab) is not Tab
            or type(self.oracle) is not OracleResult
            or type(self.attempted_oracle) is not OracleResult
            or type(self.ascii) is not str
            or type(self.note_index) is not int
            or self.note_index < 0
            or type(self.before_finger) is not int
            or type(self.requested_finger) is not int
            or not 1 <= self.requested_finger <= 4
            or type(self.options) is not CheckOptions
            or type(self.profile) is not Profile
            or (self.reason is not None and type(self.reason) is not str)
        ):
            raise ValueError("invalid fingering edit fields")
        if self.status == "applied" and self.oracle.verdict != "GREEN":
            raise ValueError("applied fingering edits must remain GREEN")


@dataclass(frozen=True, slots=True)
class DifficultyOutcome:
    """A canonical Tab checked against one versioned difficulty tier."""

    tab: Tab
    result: TierResult
    published_grade: PublishedGradeEstimate
    options: DifficultyOptions
    tier: Tier

    def __post_init__(self) -> None:
        if (
            type(self.tab) is not Tab
            or type(self.result) is not TierResult
            or type(self.published_grade) is not PublishedGradeEstimate
            or type(self.options) is not DifficultyOptions
            or type(self.tier) is not Tier
        ):
            raise ValueError("invalid difficulty outcome fields")


@dataclass(frozen=True, slots=True)
class SolveOutcome:
    """One bounded search result without a completeness claim."""

    status: SolveStatus
    tab: Tab | None
    oracle: OracleResult | None
    infeasible: Infeasible | None
    options: SolveOptions
    profile: Profile
    search_complete: Literal[False] = False
    max_solutions: Literal[1] = 1

    def __post_init__(self) -> None:
        if (
            type(self.options) is not SolveOptions
            or type(self.profile) is not Profile
            or self.search_complete is not False
            or type(self.max_solutions) is not int
            or self.max_solutions != 1
        ):
            raise ValueError("invalid bounded-search outcome fields")
        if self.status == "found":
            if (
                type(self.tab) is not Tab
                or type(self.oracle) is not OracleResult
                or self.infeasible is not None
            ):
                raise ValueError("found status requires one checked Tab")
        elif self.status == "not_found_within_budget":
            if (
                self.tab is not None
                or self.oracle is not None
                or type(self.infeasible) is not Infeasible
            ):
                raise ValueError("not-found status requires one bounded-search reason")
        else:
            raise ValueError("invalid bounded-search outcome status")


@dataclass(frozen=True, slots=True)
class RenderOutcome:
    """Canonical input Tab plus one deterministic rendered representation."""

    tab: Tab
    content: str
    options: RenderOptions
    profile: Profile

    def __post_init__(self) -> None:
        if (
            type(self.tab) is not Tab
            or type(self.content) is not str
            or type(self.options) is not RenderOptions
            or type(self.profile) is not Profile
        ):
            raise ValueError("invalid render outcome fields")


@dataclass(frozen=True, slots=True)
class ServiceCapabilities:
    """Transport-neutral capabilities of the application service itself."""

    service_version: str
    profile_registry_version: str
    score_input_version: str
    score_format_registry: Mapping[str, str]
    target_input_schema_version: str
    profiles: tuple[str, ...]
    arrangement_styles: tuple[str, ...]
    technique_profiles: tuple[str, ...]
    difficulty_tiers: tuple[str, ...]
    input_suffixes: tuple[str, ...]
    render_formats: tuple[str, ...]
    default_arrange_options: ArrangeOptions
    default_check_options: CheckOptions
    default_difficulty_options: DifficultyOptions
    default_solve_options: SolveOptions
    default_render_options: RenderOptions


__all__ = [
    "PROFILE_REGISTRY_VERSION",
    "SERVICE_VERSION",
    "ApplicationCode",
    "ApplicationDiagnostic",
    "ApplicationError",
    "ArrangeOptions",
    "ArrangeOutcome",
    "VerifiedAlternative",
    "ArrangeStatus",
    "CheckOptions",
    "CheckOutcome",
    "DifficultyOptions",
    "DifficultyOutcome",
    "FingeringEditOutcome",
    "FingeringEditStatus",
    "RenderOptions",
    "RenderOutcome",
    "SectionRegenerationOutcome",
    "SectionRegenerationStatus",
    "ServiceCapabilities",
    "SolveOptions",
    "SolveOutcome",
    "SolveStatus",
]
