"""Application pipeline from validated :class:`MusicIR` to checked tablature.

The importer owns file parsing.  This module owns the product semantics after
that boundary: IR validation, source/effective tempo selection, arrangement,
the independent faithfulness gate, rendering, and trace exposure.
"""

import math
from dataclasses import dataclass, replace

from fretsure.agent.arranger import ArrangeGoal
from fretsure.agent.harness import ArrangeResult, arrange
from fretsure.agent.trace import Trace
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import MusicIR, validate_ir
from fretsure.llm.client import LLMClient
from fretsure.metrics.fidelity import FaithfulnessGate, faithfulness
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.render.ascii import render_ascii

MAX_PIPELINE_CANDIDATES = 64
MAX_PIPELINE_REPAIR_ITERS = 64
MIN_PIPELINE_TEMPO_BPM = 1.0
MAX_PIPELINE_TEMPO_BPM = 1_000.0


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


def _validated_tempo(value: object, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a real number")
    tempo = float(value)
    if (
        not math.isfinite(tempo)
        or not MIN_PIPELINE_TEMPO_BPM <= tempo <= MAX_PIPELINE_TEMPO_BPM
    ):
        raise ValueError(
            f"{label} must be finite and in "
            f"{MIN_PIPELINE_TEMPO_BPM:g}..{MAX_PIPELINE_TEMPO_BPM:g} BPM"
        )
    return tempo


def _effective_tempo(ir: MusicIR, options: PipelineOptions) -> tuple[float, float]:
    source = _validated_tempo(ir.meta.tempo_bpm, label="source tempo")
    effective = (
        source
        if options.tempo_override_bpm is None
        else _validated_tempo(options.tempo_override_bpm, label="tempo override")
    )
    return source, effective


def _validate_pipeline_controls(options: PipelineOptions) -> None:
    """Validate controls that are not part of the shared solver contract."""
    if (
        type(options.n) is not int
        or not 1 <= options.n <= MAX_PIPELINE_CANDIDATES
    ):
        raise ValueError(
            "best-of-N candidate count must be an exact integer in "
            f"1..{MAX_PIPELINE_CANDIDATES}"
        )
    if (
        type(options.max_iters) is not int
        or not 0 <= options.max_iters <= MAX_PIPELINE_REPAIR_ITERS
    ):
        raise ValueError(
            "max_iters must be an exact integer in "
            f"0..{MAX_PIPELINE_REPAIR_ITERS}"
        )
    if type(options.use_critic) is not bool:
        raise ValueError("use_critic must be an exact bool")


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
    _validate_pipeline_controls(options)

    source_tempo, effective_tempo = _effective_tempo(ir, options)
    goal = ArrangeGoal(
        tuning=options.tuning,
        capo=options.capo,
        tempo_bpm=effective_tempo,
    )
    raw_arrangement = arrange(
        ir,
        goal,
        llm,
        profile=options.profile,
        n=options.n,
        max_iters=options.max_iters,
        use_critic=options.use_critic,
    )
    trace = Trace()
    trace.add(
        "PLAN",
        "pipeline configured from source metadata and explicit options",
        source_tempo_bpm=source_tempo,
        effective_tempo_bpm=effective_tempo,
        time_signature="4/4",
        tuning=options.tuning,
        capo=options.capo,
        profile=options.profile.version,
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
