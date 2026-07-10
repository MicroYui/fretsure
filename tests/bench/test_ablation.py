from fractions import Fraction as F

from fretsure.agent.arranger import ArrangeGoal
from fretsure.bench.ablation import AblationConfig, leave_one_out, run_config
from fretsure.bench.corpus import CorpusItem
from fretsure.ir import Meta, MusicIR, Note
from fretsure.llm.client import FakeLLM
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


def _factory() -> object:
    return lambda: FakeLLM([_PROP, _EDIT, _CRITIC])


def test_full_config_reaches_joint_success() -> None:
    m = run_config(_items(), ArrangeGoal(), _factory(), AblationConfig(best_of_n=1), MEDIAN_HAND)
    assert m.green_rate == 1.0
    assert m.joint_success == 1.0


def test_ablate_repair_drops_joint_success() -> None:
    m = run_config(
        _items(), ArrangeGoal(), _factory(), AblationConfig(repair=False, best_of_n=1), MEDIAN_HAND
    )
    assert m.joint_success == 0.0  # infeasible proposal never repaired


def test_leave_one_out_repair_earns_existence() -> None:
    loo = leave_one_out(
        _items(), ArrangeGoal(), _factory(), MEDIAN_HAND, base=AblationConfig(best_of_n=1)
    )
    assert loo["full"].joint_success > loo["-repair"].joint_success  # headline #1


def test_deterministic() -> None:
    cfg = AblationConfig(best_of_n=1)
    a = run_config(_items(), ArrangeGoal(), _factory(), cfg, MEDIAN_HAND)
    b = run_config(_items(), ArrangeGoal(), _factory(), cfg, MEDIAN_HAND)
    assert a == b
