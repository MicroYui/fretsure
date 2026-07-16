"""Application pipeline from validated :class:`MusicIR` to checked tablature.

The importer owns file parsing.  This module owns the product semantics after
that boundary: IR validation, source/effective tempo selection, arrangement,
the independent faithfulness gate, rendering, and trace exposure.
"""

from dataclasses import dataclass, replace

from fretsure.agent.arranger import ArrangeGoal
from fretsure.agent.harness import ArrangeResult, arrange
from fretsure.agent.trace import Trace
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import IRInputError, MusicIR, snapshot_music_ir, validate_ir
from fretsure.llm.client import LLMClient, snapshot_llm_model_id
from fretsure.metrics.fidelity import (
    FIDELITY_CHECKER_VERSION,
    FaithfulnessGate,
    faithfulness,
)
from fretsure.oracle.core import CHECKER_VERSION
from fretsure.oracle.input import (
    MAX_AGENT_CANDIDATES,
    MAX_AGENT_REPAIR_ITERS,
    ORACLE_INPUT_SCHEMA_VERSION,
    ensure_instrument_config,
)
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.render.ascii import render_ascii

MAX_PIPELINE_CANDIDATES = MAX_AGENT_CANDIDATES
MAX_PIPELINE_REPAIR_ITERS = MAX_AGENT_REPAIR_ITERS


@dataclass(frozen=True)
class PipelineOptions:
    """User-selectable controls for the complete backend arrangement path."""

    tuning: tuple[int, ...] = STANDARD_TUNING
    capo: int = 0
    profile: Profile = MEDIAN_HAND
    n: int = 4
    max_iters: int = 8
    use_critic: bool = True
    tempo_override_bpm: float | None = None


@dataclass(frozen=True)
class PipelineResult:
    """All product outputs, keeping feasibility and faithfulness independent."""

    arrangement: ArrangeResult
    faithfulness: FaithfulnessGate | None
    ascii: str | None
    source_tempo_bpm: float
    effective_tempo_bpm: float

    @property
    def trace(self) -> Trace:
        return self.arrangement.trace

    @property
    def result(self) -> ArrangeResult:
        """Compatibility alias matching :class:`fretsure.demo.DemoResult`."""
        return self.arrangement

    @property
    def gate(self) -> FaithfulnessGate | None:
        """Compatibility alias for callers that name the checker output a gate."""
        return self.faithfulness


def _validated_pipeline_options(options: object) -> PipelineOptions:
    """Capture once and validate controls outside the shared solver contract."""

    if type(options) is not PipelineOptions:
        raise ValueError("options must be an exact PipelineOptions instance")
    try:
        snapshot = PipelineOptions(
            tuning=object.__getattribute__(options, "tuning"),
            capo=object.__getattribute__(options, "capo"),
            profile=object.__getattribute__(options, "profile"),
            n=object.__getattribute__(options, "n"),
            max_iters=object.__getattribute__(options, "max_iters"),
            use_critic=object.__getattribute__(options, "use_critic"),
            tempo_override_bpm=object.__getattribute__(options, "tempo_override_bpm"),
        )
    except (AttributeError, TypeError):
        raise ValueError("PipelineOptions fields are missing") from None
    if (
        type(snapshot.n) is not int
        or not 1 <= snapshot.n <= MAX_PIPELINE_CANDIDATES
    ):
        raise ValueError(
            "best-of-N candidate count must be an exact integer in "
            f"1..{MAX_PIPELINE_CANDIDATES}"
        )
    if (
        type(snapshot.max_iters) is not int
        or not 0 <= snapshot.max_iters <= MAX_PIPELINE_REPAIR_ITERS
    ):
        raise ValueError(
            "max_iters must be an exact integer in "
            f"0..{MAX_PIPELINE_REPAIR_ITERS}"
        )
    if type(snapshot.use_critic) is not bool:
        raise ValueError("use_critic must be an exact bool")
    return snapshot


def run_pipeline(
    ir: MusicIR,
    llm: LLMClient,
    *,
    options: PipelineOptions,
) -> PipelineResult:
    """Arrange, solve, verify, score, render, and expose a deterministic trace.

    The source tempo is authoritative unless the caller provides an explicit
    override.  The resulting effective tempo enters :class:`ArrangeGoal`, which
    the harness passes unchanged to both solver and oracle during every repair.
    """
    options = _validated_pipeline_options(options)
    source_ir = ir
    try:
        ir = snapshot_music_ir(source_ir)
    except IRInputError as error:
        if error.field == "meta.tempo_bpm":
            try:
                raw_meta = object.__getattribute__(source_ir, "meta")
                raw_tempo = object.__getattribute__(raw_meta, "tempo_bpm")
            except (AttributeError, TypeError):
                raise error from None
            # Preserve the shared public tempo diagnostic/code at the pipeline
            # boundary while the IR snapshot itself stays safe for direct users.
            ensure_instrument_config(
                options.tuning,
                options.capo,
                options.profile,
                tempo_bpm=raw_tempo,
            )
        raise
    violations = validate_ir(ir)
    if violations:
        details = "; ".join(
            f"{v.kind}@{v.onset}: {v.detail}" if v.onset is not None else f"{v.kind}: {v.detail}"
            for v in violations
        )
        raise ValueError(f"invalid MusicIR: {details}")
    if ir.meta.time_sig != (4, 4):
        raise ValueError(
            f"pipeline currently supports only 4/4, got {ir.meta.time_sig[0]}/{ir.meta.time_sig[1]}"
        )
    n = options.n
    max_iters = options.max_iters
    use_critic = options.use_critic
    tempo_override = options.tempo_override_bpm

    # One shared contract owns tuning/capo/profile/tempo validation.  Run it
    # before arranger/proposer code can inspect tuning or invoke the LLM.
    tuning, capo, profile, source_tempo = ensure_instrument_config(
        options.tuning,
        options.capo,
        options.profile,
        tempo_bpm=ir.meta.tempo_bpm,
    )
    effective_tempo = source_tempo
    if tempo_override is not None:
        tuning, capo, profile, effective_tempo = ensure_instrument_config(
            tuning,
            capo,
            profile,
            tempo_bpm=tempo_override,
        )
    goal = ArrangeGoal(
        tuning=tuning,
        capo=capo,
        tempo_bpm=effective_tempo,
    )
    llm_model_id = snapshot_llm_model_id(llm)
    raw_arrangement = arrange(
        ir,
        goal,
        llm,
        profile=profile,
        n=n,
        max_iters=max_iters,
        use_critic=use_critic,
    )
    trace = Trace()
    trace.add(
        "PLAN",
        "pipeline configured from source metadata and explicit options",
        event="PIPELINE_CONFIGURED",
        llm_model_id=llm_model_id,
        source_tempo_bpm=source_tempo,
        effective_tempo_bpm=effective_tempo,
        time_signature="4/4",
        tuning=tuning,
        capo=capo,
        profile=profile.version,
        checker_version=CHECKER_VERSION,
        profile_version=profile.version,
        profile_fingerprint=profile.fingerprint,
        input_schema_version=ORACLE_INPUT_SCHEMA_VERSION,
        fidelity_checker_version=FIDELITY_CHECKER_VERSION,
        candidates=n,
        max_repair_iterations=max_iters,
        critic_enabled=use_critic,
    )
    trace.steps.extend(raw_arrangement.trace.steps)
    arrangement = replace(raw_arrangement, trace=trace)
    if arrangement.tab is None:
        gate = None
        ascii_tab = None
    else:
        gate = faithfulness(ir, arrangement.tab)
        ascii_tab = render_ascii(arrangement.tab)
    return PipelineResult(
        arrangement=arrangement,
        faithfulness=gate,
        ascii=ascii_tab,
        source_tempo_bpm=source_tempo,
        effective_tempo_bpm=effective_tempo,
    )
