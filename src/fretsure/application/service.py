"""Single in-process application seam shared by HTTP and MCP adapters."""

from __future__ import annotations

import json
from collections.abc import Mapping
from types import MappingProxyType

from fretsure.application.contracts import (
    PROFILE_REGISTRY_VERSION,
    SERVICE_VERSION,
    ApplicationCode,
    ApplicationDiagnostic,
    ApplicationError,
    ArrangeOptions,
    ArrangeOutcome,
    ArrangeStatus,
    CheckOptions,
    CheckOutcome,
    RenderOptions,
    RenderOutcome,
    ServiceCapabilities,
    SolveOptions,
    SolveOutcome,
)
from fretsure.application.target import (
    TARGET_INPUT_SCHEMA_VERSION,
    TargetInputError,
    target_from_json,
)
from fretsure.geometry import STANDARD_TUNING
from fretsure.importers import ImportCode, ImportFailure, ImportSuccess, import_musicxml_bytes
from fretsure.llm.client import (
    CONSTANT_LLM_MODEL_ID,
    ConstantLLM,
    LLMClient,
    LLMModelIdError,
    snapshot_llm_model_id,
)
from fretsure.oracle.core import check_playability
from fretsure.oracle.input import (
    MAX_AGENT_CANDIDATES,
    MAX_AGENT_REPAIR_ITERS,
    MAX_BEATS_PER_BAR,
    MAX_SOLVER_BEAM,
    InputContractError,
    OracleInputError,
    SolverInputError,
    ensure_instrument_config,
)
from fretsure.oracle.profiles import MEDIAN_HAND, Profile, validated_profile_snapshot
from fretsure.pipeline import PipelineOptions, run_pipeline
from fretsure.render.ascii import render_ascii
from fretsure.solver.api import Infeasible, solve_fingering
from fretsure.tab import TabSchemaError, validated_tab_from_json

_PROFILE_REGISTRY: Mapping[str, Profile] = MappingProxyType({"median": MEDIAN_HAND})
PROFILE_NAMES = tuple(_PROFILE_REGISTRY)


class _PinnedModelLLM:
    """Pin provenance once while delegating completions to the supplied client."""

    def __init__(self, delegate: LLMClient, model_id: str) -> None:
        self._delegate = delegate
        self._model_id = model_id
        self._model_call_failed = False

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def model_call_failed(self) -> bool:
        return self._model_call_failed

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        try:
            return self._delegate.complete(
                system=system,
                user=user,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except Exception:
            # The core proposer intentionally turns malformed model output and
            # its historical RuntimeError transport into a deterministic
            # fallback.  At the explicit service boundary, a selected network
            # engine must not silently return a 200 response stamped with a
            # model that never answered.  Reclassify without retaining provider
            # text so the application boundary can fail safely.
            self._model_call_failed = True
            raise _ApplicationModelCallFailed from None


class _ApplicationModelCallFailed(Exception):
    """A redacted service-only model-call failure not swallowed as fallback."""


def _error(
    code: ApplicationCode,
    path: str,
    detail: str,
    diagnostics: tuple[ApplicationDiagnostic, ...] = (),
) -> ApplicationError:
    return ApplicationError(code, path, detail, diagnostics)


def _missing_options(name: str) -> ApplicationError:
    return _error(
        ApplicationCode.INVALID_OPTIONS,
        "options",
        f"options must be an exact {name} instance",
    )


def _option_field(options: object, field: str) -> object:
    try:
        return object.__getattribute__(options, field)
    except (AttributeError, TypeError):
        raise _error(
            ApplicationCode.INVALID_OPTIONS,
            f"options.{field}",
            "required option is missing",
        ) from None


def _profile(name: object, *, path: str = "options.profile") -> tuple[str, Profile]:
    if type(name) is not str:
        raise _error(
            ApplicationCode.INVALID_OPTIONS,
            path,
            "profile must be an exact string",
        )
    profile = _PROFILE_REGISTRY.get(name)
    if profile is None:
        raise _error(
            ApplicationCode.UNKNOWN_PROFILE,
            path,
            "profile is not in the public registry",
            (ApplicationDiagnostic("UNKNOWN_PROFILE", path, "supported profile: median"),),
        )
    return name, validated_profile_snapshot(profile)


def _contract_diagnostics(error: InputContractError) -> tuple[ApplicationDiagnostic, ...]:
    return tuple(
        ApplicationDiagnostic(item.code.value, item.path, item.message)
        for item in error.diagnostics
    )


def _validated_config(
    tuning: object,
    capo: object,
    profile: Profile,
    tempo_bpm: object,
) -> tuple[tuple[int, ...], int, Profile, float]:
    try:
        return ensure_instrument_config(
            tuning,
            capo,
            profile,
            tempo_bpm=tempo_bpm,
        )
    except SolverInputError as exc:
        raise _error(
            ApplicationCode.INVALID_OPTIONS,
            "options",
            "instrument or tempo options were rejected",
            _contract_diagnostics(exc),
        ) from None


def _snapshot_arrange_options(options: object) -> tuple[ArrangeOptions, Profile]:
    if type(options) is not ArrangeOptions:
        raise _missing_options("ArrangeOptions")
    profile_name, profile = _profile(_option_field(options, "profile"))
    n = _option_field(options, "n")
    max_iters = _option_field(options, "max_iters")
    use_critic = _option_field(options, "use_critic")
    tempo = _option_field(options, "tempo_bpm")
    diagnostics: list[ApplicationDiagnostic] = []
    if type(n) is not int or not 1 <= n <= MAX_AGENT_CANDIDATES:
        diagnostics.append(
            ApplicationDiagnostic(
                "CANDIDATE_COUNT",
                "options.n",
                f"must be an exact integer in 1..{MAX_AGENT_CANDIDATES}",
            )
        )
    if type(max_iters) is not int or not 0 <= max_iters <= MAX_AGENT_REPAIR_ITERS:
        diagnostics.append(
            ApplicationDiagnostic(
                "REPAIR_ITERATIONS",
                "options.max_iters",
                f"must be an exact integer in 0..{MAX_AGENT_REPAIR_ITERS}",
            )
        )
    if type(use_critic) is not bool:
        diagnostics.append(
            ApplicationDiagnostic(
                "BOOLEAN_CONTROL",
                "options.use_critic",
                "must be an exact bool",
            )
        )
    normalized_tempo: float | None = None
    if tempo is not None:
        _, _, profile, normalized_tempo = _validated_config(
            STANDARD_TUNING,
            0,
            profile,
            tempo,
        )
    if diagnostics:
        raise _error(
            ApplicationCode.INVALID_OPTIONS,
            "options",
            "arrangement controls were rejected",
            tuple(diagnostics),
        )
    assert type(n) is int
    assert type(max_iters) is int
    assert type(use_critic) is bool
    return (
        ArrangeOptions(
            profile=profile_name,
            n=n,
            max_iters=max_iters,
            use_critic=use_critic,
            tempo_bpm=normalized_tempo,
        ),
        profile,
    )


def _snapshot_check_options(options: object) -> tuple[CheckOptions, Profile]:
    if type(options) is not CheckOptions:
        raise _missing_options("CheckOptions")
    profile_name, profile = _profile(_option_field(options, "profile"))
    tempo = _option_field(options, "tempo_bpm")
    beats = _option_field(options, "beats_per_bar")
    _, _, profile, normalized_tempo = _validated_config(
        STANDARD_TUNING,
        0,
        profile,
        tempo,
    )
    if type(beats) is not int or not 1 <= beats <= MAX_BEATS_PER_BAR:
        raise _error(
            ApplicationCode.INVALID_OPTIONS,
            "options.beats_per_bar",
            f"beats_per_bar must be an exact integer in 1..{MAX_BEATS_PER_BAR}",
        )
    return (
        CheckOptions(profile_name, normalized_tempo, beats),
        profile,
    )


def _snapshot_solve_options(options: object) -> tuple[SolveOptions, Profile]:
    if type(options) is not SolveOptions:
        raise _missing_options("SolveOptions")
    profile_name, profile = _profile(_option_field(options, "profile"))
    tuning = _option_field(options, "tuning")
    capo = _option_field(options, "capo")
    tempo = _option_field(options, "tempo_bpm")
    beam = _option_field(options, "beam")
    tuning_snapshot, capo_snapshot, profile, normalized_tempo = _validated_config(
        tuning,
        capo,
        profile,
        tempo,
    )
    if type(beam) is not int or not 1 <= beam <= MAX_SOLVER_BEAM:
        raise _error(
            ApplicationCode.INVALID_OPTIONS,
            "options.beam",
            f"beam must be an exact integer in 1..{MAX_SOLVER_BEAM}",
        )
    return (
        SolveOptions(
            profile_name,
            tuning_snapshot,
            capo_snapshot,
            normalized_tempo,
            beam,
        ),
        profile,
    )


def _snapshot_render_options(options: object) -> tuple[RenderOptions, Profile]:
    if type(options) is not RenderOptions:
        raise _missing_options("RenderOptions")
    format_name = _option_field(options, "format")
    if type(format_name) is not str or format_name != "ascii":
        raise _error(
            ApplicationCode.UNSUPPORTED_RENDER_FORMAT,
            "options.format",
            "Plan 6A supports only the exact format name 'ascii'",
        )
    profile_name, profile = _profile(_option_field(options, "profile"))
    tempo = _option_field(options, "tempo_bpm")
    beats = _option_field(options, "beats_per_bar")
    _, _, profile, normalized_tempo = _validated_config(
        STANDARD_TUNING,
        0,
        profile,
        tempo,
    )
    if type(beats) is not int or not 1 <= beats <= MAX_BEATS_PER_BAR:
        raise _error(
            ApplicationCode.INVALID_OPTIONS,
            "options.beats_per_bar",
            f"beats_per_bar must be an exact integer in 1..{MAX_BEATS_PER_BAR}",
        )
    return (
        RenderOptions("ascii", profile_name, normalized_tempo, beats),
        profile,
    )


def _tab_error(exc: TabSchemaError | OracleInputError) -> ApplicationError:
    diagnostics: tuple[ApplicationDiagnostic, ...]
    if isinstance(exc, TabSchemaError):
        diagnostics = (ApplicationDiagnostic(exc.code.value, exc.path, exc.message),)
    else:
        diagnostics = _contract_diagnostics(exc)
    return _error(
        ApplicationCode.TAB_INPUT_REJECTED,
        "tab_json",
        "Tab JSON was rejected by the public input contract",
        diagnostics,
    )


def capabilities() -> ServiceCapabilities:
    """Return the immutable transport-neutral application capability contract."""

    return ServiceCapabilities(
        SERVICE_VERSION,
        PROFILE_REGISTRY_VERSION,
        TARGET_INPUT_SCHEMA_VERSION,
        PROFILE_NAMES,
        (".musicxml", ".xml", ".mxl"),
        ("ascii",),
        ArrangeOptions(),
        CheckOptions(),
        SolveOptions(),
        RenderOptions(),
    )


def arrange_score_bytes(
    data: bytes,
    *,
    filename: str,
    options: ArrangeOptions,
    llm: LLMClient,
) -> ArrangeOutcome:
    """Import and arrange exact in-memory score bytes without touching disk."""

    options_snapshot, profile = _snapshot_arrange_options(options)
    if type(data) is not bytes:
        raise _error(
            ApplicationCode.INVALID_ARGUMENT,
            "data",
            "data must be exact bytes",
        )
    if type(filename) is not str:
        raise _error(
            ApplicationCode.INVALID_ARGUMENT,
            "filename",
            "filename must be an exact string",
        )
    imported = import_musicxml_bytes(data, filename)
    if isinstance(imported, ImportFailure):
        diagnostics = tuple(
            ApplicationDiagnostic(
                diagnostic.code.value,
                "score",
                f"score importer rejected input ({diagnostic.code.value})",
            )
            for diagnostic in imported.diagnostics
        )
        missing_dependency = any(
            diagnostic.code is ImportCode.MISSING_DEPENDENCY for diagnostic in imported.diagnostics
        )
        raise _error(
            (
                ApplicationCode.DEPENDENCY_UNAVAILABLE
                if missing_dependency
                else ApplicationCode.IMPORT_REJECTED
            ),
            "score",
            (
                "the optional MusicXML runtime dependency is unavailable"
                if missing_dependency
                else "score bytes were rejected by the MusicXML/MXL importer"
            ),
            diagnostics,
        )
    assert isinstance(imported, ImportSuccess)
    try:
        model_id = snapshot_llm_model_id(llm)
    except LLMModelIdError:
        raise _error(
            ApplicationCode.LLM_CONFIGURATION_REJECTED,
            "llm.model_id",
            "LLM model provenance is missing or invalid",
        ) from None
    # Preserve the core's documented large-score deterministic fast path even
    # when a transport adapter uses a lazy startup wrapper around its factory.
    # The factory/model identity is still validated only after import succeeds.
    pinned_model: _PinnedModelLLM | None = None
    if model_id == CONSTANT_LLM_MODEL_ID:
        pinned_llm: LLMClient = ConstantLLM("noop")
    else:
        pinned_model = _PinnedModelLLM(llm, model_id)
        pinned_llm = pinned_model
    try:
        pipeline = run_pipeline(
            imported.ir,
            pinned_llm,
            options=PipelineOptions(
                profile=profile,
                n=options_snapshot.n,
                max_iters=options_snapshot.max_iters,
                use_critic=options_snapshot.use_critic,
                tempo_override_bpm=options_snapshot.tempo_bpm,
            ),
        )
        # The core repair loop records and terminates on transport failure so
        # direct library callers retain a diagnostic replay.  The service seam
        # must still reject that request instead of stamping a successful
        # response with a model that failed to answer.
        if pinned_model is not None and pinned_model.model_call_failed:
            raise _ApplicationModelCallFailed
        trace_document_json = json.dumps(
            pipeline.trace.to_public_dict(),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except Exception:
        # Model/provider exceptions and internal parser text are not public API.
        raise _error(
            ApplicationCode.ARRANGEMENT_FAILED,
            "arrangement",
            "arrangement could not be completed",
        ) from None
    status: ArrangeStatus = (
        "tab_produced" if pipeline.arrangement.tab is not None else "no_fingering_within_budget"
    )
    return ArrangeOutcome(
        status,
        imported,
        pipeline.arrangement.tab,
        pipeline.arrangement.oracle,
        pipeline.faithfulness,
        pipeline.ascii,
        trace_document_json,
        pipeline.source_tempo_bpm,
        pipeline.effective_tempo_bpm,
        options_snapshot,
        profile,
        model_id,
    )


def check_tab_json(tab_json: str, *, options: CheckOptions) -> CheckOutcome:
    """Parse and check one strict canonical Tab JSON document."""

    options_snapshot, profile = _snapshot_check_options(options)
    try:
        tab = validated_tab_from_json(
            tab_json,
            profile=profile,
            tempo_bpm=options_snapshot.tempo_bpm,
            beats_per_bar=options_snapshot.beats_per_bar,
        )
        oracle = check_playability(
            tab,
            profile,
            tempo_bpm=options_snapshot.tempo_bpm,
            beats_per_bar=options_snapshot.beats_per_bar,
        )
    except (TabSchemaError, OracleInputError) as exc:
        raise _tab_error(exc) from None
    except Exception:
        raise _error(
            ApplicationCode.CHECK_FAILED,
            "tab_json",
            "playability check could not be completed",
        ) from None
    return CheckOutcome(tab, oracle, options_snapshot, profile)


def solve_target_json(target_json: str, *, options: SolveOptions) -> SolveOutcome:
    """Run one explicitly bounded fingering search over strict target JSON."""

    options_snapshot, profile = _snapshot_solve_options(options)
    try:
        notes = target_from_json(target_json)
    except TargetInputError as exc:
        raise _error(
            ApplicationCode.TARGET_INPUT_REJECTED,
            "target_json",
            "target JSON was rejected by the public input contract",
            (ApplicationDiagnostic(exc.code.value, exc.path, exc.detail),),
        ) from None
    try:
        result = solve_fingering(
            notes,
            options_snapshot.tuning,
            options_snapshot.capo,
            profile,
            tempo_bpm=options_snapshot.tempo_bpm,
            beam=options_snapshot.beam,
        )
    except SolverInputError as exc:
        raise _error(
            ApplicationCode.SOLVER_INPUT_REJECTED,
            "target_json",
            "target exceeds the bounded solver input contract",
            _contract_diagnostics(exc),
        ) from None
    except Exception:
        raise _error(
            ApplicationCode.SOLVER_FAILED,
            "target_json",
            "bounded fingering search could not be completed",
        ) from None
    if isinstance(result, Infeasible):
        return SolveOutcome(
            "not_found_within_budget",
            None,
            None,
            result,
            options_snapshot,
            profile,
        )
    try:
        oracle = check_playability(
            result,
            profile,
            tempo_bpm=options_snapshot.tempo_bpm,
        )
    except Exception:
        raise _error(
            ApplicationCode.SOLVER_FAILED,
            "target_json",
            "bounded fingering search could not be completed",
        ) from None
    return SolveOutcome(
        "found",
        result,
        oracle,
        None,
        options_snapshot,
        profile,
    )


def render_tab_json(tab_json: str, *, options: RenderOptions) -> RenderOutcome:
    """Render one semantically valid canonical Tab as deterministic ASCII."""

    options_snapshot, profile = _snapshot_render_options(options)
    try:
        tab = validated_tab_from_json(
            tab_json,
            profile=profile,
            tempo_bpm=options_snapshot.tempo_bpm,
            beats_per_bar=options_snapshot.beats_per_bar,
        )
        content = render_ascii(tab)
    except (TabSchemaError, OracleInputError) as exc:
        raise _tab_error(exc) from None
    except Exception:
        raise _error(
            ApplicationCode.RENDER_FAILED,
            "tab_json",
            "notation rendering could not be completed",
        ) from None
    return RenderOutcome(tab, content, options_snapshot, profile)


__all__ = [
    "PROFILE_NAMES",
    "arrange_score_bytes",
    "capabilities",
    "check_tab_json",
    "render_tab_json",
    "solve_target_json",
]
