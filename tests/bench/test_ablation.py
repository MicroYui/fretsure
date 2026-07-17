from fractions import Fraction as F

import pytest

from fretsure.agent.arranger import ArrangeGoal
from fretsure.agent.harness import ArrangeResult
from fretsure.agent.trace import Trace
from fretsure.bench.ablation import AblationConfig, LLMFactory, leave_one_out, run_config
from fretsure.bench.corpus import CorpusItem
from fretsure.ir import Meta, MusicIR, Note
from fretsure.llm.client import ConstantLLM, FakeLLM
from fretsure.oracle.profiles import MEDIAN_HAND

# proposal: 85 (melody) + 86 (harmony) — infeasible together (both only high-E)
_PROP = (
    '{"notes":[{"onset":"0","duration":"1","pitch":85,"voice":"melody"},'
    '{"onset":"0","duration":"1","pitch":86,"voice":"harmony"}]}'
)
_EDIT = '{"op":"drop_note","target_onset":"0","target_pitch":86}'
_CRITIC = '{"overall":0.8}'


def _items() -> list[CorpusItem]:
    ir = MusicIR((Note(F(0), F(1), 85, "melody"),), (), Meta("C", (4, 4), 90.0, "t", "t", "PD"))
    return [CorpusItem(ir, "procedural", "gen", 3, "i0")]


def _factory() -> LLMFactory:
    return lambda: FakeLLM([_PROP, _EDIT, _CRITIC])


def test_full_config_reaches_joint_success() -> None:
    m = run_config(_items(), ArrangeGoal(), _factory(), AblationConfig(best_of_n=1), MEDIAN_HAND)
    assert m.green_rate == 1.0
    assert m.joint_success == 1.0
    assert m.mean_melody_f1 == 1.0
    assert m.melody_evaluated_items == 1


def test_melody_mean_excludes_items_without_source_melody_evidence() -> None:
    ir = MusicIR(
        (Note(F(0), F(1), 40, "bass"),),
        (),
        Meta("C", (4, 4), 90.0, "t", "t", "PD"),
    )
    item = CorpusItem(ir, "test", "generated", 1, "no-melody")

    metrics = run_config(
        [item],
        ArrangeGoal(),
        lambda: ConstantLLM("noop"),
        AblationConfig(best_of_n=1, critic=False),
        MEDIAN_HAND,
    )

    assert metrics.mean_melody_f1 is None
    assert metrics.melody_evaluated_items == 0


def test_ablate_repair_drops_joint_success() -> None:
    m = run_config(
        _items(), ArrangeGoal(), _factory(), AblationConfig(repair=False, best_of_n=1), MEDIAN_HAND
    )
    assert m.joint_success == 0.0  # infeasible proposal never repaired
    assert m.mean_melody_f1 == 0.0
    assert m.melody_evaluated_items == 1


def test_leave_one_out_repair_earns_existence() -> None:
    loo = leave_one_out(
        _items(), ArrangeGoal(), _factory(), MEDIAN_HAND, base=AblationConfig(best_of_n=1)
    )
    assert loo["full"].joint_success > loo["-repair"].joint_success  # legacy signal


def test_deterministic() -> None:
    cfg = AblationConfig(best_of_n=1)
    a = run_config(_items(), ArrangeGoal(), _factory(), cfg, MEDIAN_HAND)
    b = run_config(_items(), ArrangeGoal(), _factory(), cfg, MEDIAN_HAND)
    assert a == b


def test_each_item_is_evaluated_at_its_source_tempo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_tempos: list[float] = []
    ir = MusicIR(
        (Note(F(0), F(1), 64, "melody"),),
        (),
        Meta("C", (4, 4), 96.0, "t", "t", "PD"),
    )

    def capture_arrange(
        ir: MusicIR,
        goal: ArrangeGoal,
        llm: object,
        **_kwargs: object,
    ) -> ArrangeResult:
        del ir, llm
        observed_tempos.append(goal.tempo_bpm)
        return ArrangeResult(None, None, None, None, Trace(), 1)

    monkeypatch.setattr("fretsure.bench.ablation.arrange", capture_arrange)

    run_config(
        [CorpusItem(ir, "procedural", "generated", 0, "tempo-96")],
        ArrangeGoal(),
        lambda: ConstantLLM("noop"),
        AblationConfig(best_of_n=1, critic=False),
        MEDIAN_HAND,
    )

    assert observed_tempos == [96.0]


def test_run_config_closes_each_stateful_llm_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closes = 0

    class ClosableFake(FakeLLM):
        def close(self) -> None:
            nonlocal closes
            closes += 1

    def fail_arrange(*_args: object, **_kwargs: object) -> ArrangeResult:
        raise RuntimeError("arrange failed")

    monkeypatch.setattr("fretsure.bench.ablation.arrange", fail_arrange)
    with pytest.raises(RuntimeError, match="arrange failed"):
        run_config(
            _items(),
            ArrangeGoal(),
            lambda: ClosableFake([]),
            AblationConfig(best_of_n=1),
            MEDIAN_HAND,
        )
    assert closes == 1
