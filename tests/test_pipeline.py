import json
from dataclasses import replace

import pytest

import fretsure.pipeline as pipeline_module
from fretsure.agent.arranger import ArrangeGoal
from fretsure.agent.harness import ArrangeResult
from fretsure.agent.trace import Trace
from fretsure.demo import sample_ir
from fretsure.ir import IRInputError
from fretsure.llm.client import ConstantLLM, LLMModelIdError
from fretsure.metrics.fidelity import FIDELITY_CHECKER_VERSION
from fretsure.oracle.core import CHECKER_VERSION
from fretsure.oracle.input import (
    ORACLE_INPUT_SCHEMA_VERSION,
    OracleInputCode,
    SolverInputError,
)
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.pipeline import PipelineOptions, run_pipeline


def test_source_tempo_is_the_default_effective_tempo(monkeypatch: pytest.MonkeyPatch) -> None:
    ir = sample_ir(bars=1)
    ir = replace(ir, meta=replace(ir.meta, tempo_bpm=137.5))
    seen: list[ArrangeGoal] = []

    def fake_arrange(*args: object, **kwargs: object) -> ArrangeResult:
        seen.append(args[1])  # type: ignore[arg-type]
        return ArrangeResult(None, None, None, None, Trace(), 0)

    monkeypatch.setattr("fretsure.pipeline.arrange", fake_arrange)

    result = run_pipeline(ir, ConstantLLM(), options=PipelineOptions())

    assert seen[0].tempo_bpm == 137.5
    assert result.source_tempo_bpm == 137.5
    assert result.effective_tempo_bpm == 137.5


def test_explicit_tempo_override_reaches_arrange_goal(monkeypatch: pytest.MonkeyPatch) -> None:
    ir = sample_ir(bars=1)
    seen: list[ArrangeGoal] = []

    def fake_arrange(*args: object, **kwargs: object) -> ArrangeResult:
        seen.append(args[1])  # type: ignore[arg-type]
        return ArrangeResult(None, None, None, None, Trace(), 0)

    monkeypatch.setattr("fretsure.pipeline.arrange", fake_arrange)

    result = run_pipeline(
        ir,
        ConstantLLM(),
        options=PipelineOptions(tempo_override_bpm=72.0),
    )

    assert seen[0].tempo_bpm == 72.0
    assert result.source_tempo_bpm == ir.meta.tempo_bpm
    assert result.effective_tempo_bpm == 72.0


def test_pipeline_uses_one_detached_profile_for_execution_and_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_profile = replace(MEDIAN_HAND, version="pipeline-snapshot@0.1")
    expected_fingerprint = source_profile.fingerprint
    seen_profiles: list[object] = []

    def fake_arrange(*args: object, **kwargs: object) -> ArrangeResult:
        seen_profiles.append(kwargs["profile"])
        object.__setattr__(source_profile, "version", "mutated@0.1")
        object.__setattr__(source_profile, "hand_span_mm", 200.0)
        return ArrangeResult(None, None, None, None, Trace(), 0)

    monkeypatch.setattr("fretsure.pipeline.arrange", fake_arrange)
    result = run_pipeline(
        sample_ir(bars=1),
        ConstantLLM(),
        options=PipelineOptions(profile=source_profile),
    )
    plan = result.trace.steps[0].data

    assert seen_profiles[0] is not source_profile
    assert plan["profile_version"] == "pipeline-snapshot@0.1"
    assert plan["profile_fingerprint"] == expected_fingerprint


def test_pipeline_snapshots_model_id_before_calls_and_rejects_invalid_id_before_call() -> None:
    class DriftingLLM:
        def __init__(self, model_id: str) -> None:
            self.current_model_id = model_id
            self.calls = 0

        @property
        def model_id(self) -> str:
            return self.current_model_id

        def complete(
            self,
            *,
            system: str,
            user: str,
            max_tokens: int = 1024,
            temperature: float = 0.0,
        ) -> str:
            self.calls += 1
            self.current_model_id = "claimed-after-call"
            return "noop"

    drifting = DriftingLLM("actual-before-call")
    result = run_pipeline(
        sample_ir(bars=1),
        drifting,
        options=PipelineOptions(n=1, use_critic=False),
    )
    assert drifting.calls > 0
    assert result.trace.steps[0].data["llm_model_id"] == "actual-before-call"

    invalid = DriftingLLM("bad\nmodel")
    with pytest.raises(LLMModelIdError, match="printable exact string"):
        run_pipeline(
            sample_ir(bars=1),
            invalid,
            options=PipelineOptions(n=1, use_critic=False),
        )
    assert invalid.calls == 0


def test_pipeline_controls_are_detached_before_instrument_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_options = PipelineOptions(n=1, max_iters=2, use_critic=False)
    real_ensure = pipeline_module.ensure_instrument_config
    seen: list[tuple[object, object, object]] = []

    def mutate_options_after_capture(*args: object, **kwargs: object) -> object:
        result = real_ensure(*args, **kwargs)
        object.__setattr__(source_options, "n", 1_000_000)
        object.__setattr__(source_options, "max_iters", 1_000_000)
        object.__setattr__(source_options, "use_critic", "yes")
        return result

    def fake_arrange(*args: object, **kwargs: object) -> ArrangeResult:
        seen.append((kwargs["n"], kwargs["max_iters"], kwargs["use_critic"]))
        return ArrangeResult(None, None, None, None, Trace(), 0)

    monkeypatch.setattr(pipeline_module, "ensure_instrument_config", mutate_options_after_capture)
    monkeypatch.setattr(pipeline_module, "arrange", fake_arrange)
    run_pipeline(sample_ir(bars=1), ConstantLLM(), options=source_options)

    assert seen == [(1, 2, False)]


def test_pipeline_uses_one_deep_ir_snapshot_through_faithfulness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = sample_ir(bars=1)
    expected = run_pipeline(source, ConstantLLM(), options=PipelineOptions(n=1))
    real_arrange = pipeline_module.arrange

    def mutate_source_then_arrange(*args: object, **kwargs: object) -> ArrangeResult:
        object.__setattr__(source, "notes", ())
        object.__setattr__(source, "chords", ())
        return real_arrange(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline_module, "arrange", mutate_source_then_arrange)
    actual = run_pipeline(source, ConstantLLM(), options=PipelineOptions(n=1))

    assert source.notes == () and source.chords == ()
    assert actual.faithfulness == expected.faithfulness


def test_pipeline_bounds_ir_before_semantic_validation_or_arrangement() -> None:
    source = sample_ir(bars=1)
    note = source.notes[0]
    object.__setattr__(source, "notes", (note,) * 20_001)

    with pytest.raises(IRInputError, match="count exceeds"):
        run_pipeline(source, ConstantLLM(), options=PipelineOptions())


@pytest.mark.parametrize(
    "tempo",
    [float("nan"), float("inf"), float("-inf"), 0.0, 0.5, 1_001.0, True, "90"],
)
def test_pipeline_rejects_invalid_source_tempo_before_arranging(
    tempo: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    ir = sample_ir(bars=1)
    ir = replace(ir, meta=replace(ir.meta, tempo_bpm=tempo))  # type: ignore[arg-type]

    def must_not_arrange(*args: object, **kwargs: object) -> ArrangeResult:
        raise AssertionError("invalid tempo reached arranger")

    monkeypatch.setattr("fretsure.pipeline.arrange", must_not_arrange)
    with pytest.raises(SolverInputError) as caught:
        run_pipeline(ir, ConstantLLM(), options=PipelineOptions())
    assert OracleInputCode.TEMPO in {
        diagnostic.code for diagnostic in caught.value.diagnostics
    }


@pytest.mark.parametrize(
    "tempo",
    [float("nan"), float("inf"), float("-inf"), 0.0, 0.5, 1_001.0, True, "90"],
)
def test_pipeline_rejects_invalid_tempo_override_before_arranging(
    tempo: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    def must_not_arrange(*args: object, **kwargs: object) -> ArrangeResult:
        raise AssertionError("invalid tempo reached arranger")

    monkeypatch.setattr("fretsure.pipeline.arrange", must_not_arrange)
    with pytest.raises(SolverInputError) as caught:
        run_pipeline(
            sample_ir(bars=1),
            ConstantLLM(),
            options=PipelineOptions(tempo_override_bpm=tempo),  # type: ignore[arg-type]
        )
    assert OracleInputCode.TEMPO in {
        diagnostic.code for diagnostic in caught.value.diagnostics
    }


@pytest.mark.parametrize(
    "tuning",
    [
        (),
        (40, 45, 50, 55, 59),
        (40, 45, 50, 55, 59, 64, 69),
        (-1, 45, 50, 55, 59, 64),
        (40, 45, 50, 55, 59, 128),
        (40, 45, 50, 55, 59, True),
    ],
)
def test_pipeline_rejects_invalid_six_string_tuning(tuning: tuple[int, ...]) -> None:
    with pytest.raises(SolverInputError):
        run_pipeline(
            sample_ir(bars=1),
            ConstantLLM(),
            options=PipelineOptions(tuning=tuning),
        )


def test_pipeline_rejects_nonascending_tuning_before_arrange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def must_not_arrange(*args: object, **kwargs: object) -> ArrangeResult:
        raise AssertionError("invalid tuning reached arranger")

    monkeypatch.setattr("fretsure.pipeline.arrange", must_not_arrange)
    with pytest.raises(SolverInputError) as caught:
        run_pipeline(
            sample_ir(bars=1),
            ConstantLLM(),
            options=PipelineOptions(tuning=(40, 45, 50, 55, 55, 64)),
        )
    assert OracleInputCode.TUNING_ORDER in {
        diagnostic.code for diagnostic in caught.value.diagnostics
    }


@pytest.mark.parametrize("n", [0, -1, True, 1.5, 65])
def test_pipeline_strictly_rejects_invalid_candidate_count(n: object) -> None:
    with pytest.raises(ValueError, match="candidate count"):
        run_pipeline(
            sample_ir(bars=1),
            ConstantLLM(),
            options=PipelineOptions(n=n),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("max_iters", [-1, True, 1.5, 65])
def test_pipeline_strictly_rejects_invalid_repair_budget(max_iters: object) -> None:
    with pytest.raises(ValueError, match="max_iters"):
        run_pipeline(
            sample_ir(bars=1),
            ConstantLLM(),
            options=PipelineOptions(max_iters=max_iters),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("use_critic", [0, 1, "false", None])
def test_pipeline_strictly_rejects_non_boolean_critic_flag(
    use_critic: object,
) -> None:
    with pytest.raises(ValueError, match="use_critic"):
        run_pipeline(
            sample_ir(bars=1),
            ConstantLLM(),
            options=PipelineOptions(use_critic=use_critic),  # type: ignore[arg-type]
        )


def test_pipeline_rejects_meter_outside_current_four_four_contract() -> None:
    ir = sample_ir(bars=1)
    ir = replace(ir, meta=replace(ir.meta, time_sig=(3, 4)))
    with pytest.raises(ValueError, match="only 4/4"):
        run_pipeline(ir, ConstantLLM(), options=PipelineOptions())


def test_pipeline_offline_result_contains_tab_ascii_gate_and_trace() -> None:
    ir = sample_ir(bars=1)

    result = run_pipeline(
        ir,
        ConstantLLM("noop"),
        options=PipelineOptions(n=1, use_critic=False),
    )

    assert result.arrangement.tab is not None
    assert result.arrangement.oracle is not None
    assert result.ascii is not None and result.ascii.count("|") == 12
    assert result.faithfulness is not None
    assert result.trace is result.arrangement.trace
    assert result.trace.steps[0].kind == "PLAN"
    assert result.trace.steps[0].data["source_tempo_bpm"] == 90.0
    assert result.trace.steps[0].data["effective_tempo_bpm"] == 90.0
    assert (
        result.trace.steps[0].data["profile_fingerprint"]
        == result.arrangement.oracle.profile_fingerprint
    )
    first_jsonl_row = json.loads(result.trace.to_jsonl().splitlines()[0])
    assert first_jsonl_row["data"]["llm_model_id"] == "constant-stub"
    assert first_jsonl_row["data"]["checker_version"] == CHECKER_VERSION
    assert (
        first_jsonl_row["data"]["input_schema_version"]
        == ORACLE_INPUT_SCHEMA_VERSION
    )
    assert (
        first_jsonl_row["data"]["fidelity_checker_version"]
        == FIDELITY_CHECKER_VERSION
    )


def test_pipeline_offline_is_deterministic() -> None:
    ir = sample_ir(bars=1)
    options = PipelineOptions(n=1, use_critic=False)
    a = run_pipeline(ir, ConstantLLM("noop"), options=options)
    b = run_pipeline(ir, ConstantLLM("noop"), options=options)
    assert a == b
