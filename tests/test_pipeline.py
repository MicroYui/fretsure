from dataclasses import replace

import pytest

from fretsure.agent.arranger import ArrangeGoal
from fretsure.agent.harness import ArrangeResult
from fretsure.agent.trace import Trace
from fretsure.demo import sample_ir
from fretsure.llm.client import ConstantLLM
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
    with pytest.raises(ValueError, match="source tempo"):
        run_pipeline(ir, ConstantLLM(), options=PipelineOptions())


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
    with pytest.raises(ValueError, match="tempo override"):
        run_pipeline(
            sample_ir(bars=1),
            ConstantLLM(),
            options=PipelineOptions(tempo_override_bpm=tempo),  # type: ignore[arg-type]
        )


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


def test_pipeline_offline_is_deterministic() -> None:
    ir = sample_ir(bars=1)
    options = PipelineOptions(n=1, use_critic=False)
    a = run_pipeline(ir, ConstantLLM("noop"), options=options)
    b = run_pipeline(ir, ConstantLLM("noop"), options=options)
    assert a == b
