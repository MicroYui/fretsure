from fractions import Fraction as F

from fretsure.agent.arranger import ArrangeGoal
from fretsure.bench.ablation import paired_best_of_n
from fretsure.bench.corpus import CorpusItem
from fretsure.ir import ChordSymbol, Meta, MusicIR, Note
from fretsure.llm.client import FakeLLM
from fretsure.oracle.profiles import MEDIAN_HAND

# melody E4 + a C bass under a C chord: the faithful arrangement keeps the C bass.
_IR = MusicIR(
    (Note(F(0), F(1), 64, "melody"), Note(F(0), F(1), 48, "bass")),
    (ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 0),),
    Meta("C", (4, 4), 90.0, "t", "t", "PD"),
)
_ITEM = CorpusItem(_IR, "test", "generated", 2, "paired-1")

# Candidate 0 (greedy): GREEN but bass is G (pc 7) -> bass_root fails the gate.
_WRONG = (
    '{"notes":[{"onset":"0","duration":"1","pitch":64,"voice":"melody"},'
    '{"onset":"0","duration":"1","pitch":43,"voice":"bass"}]}'
)
# Candidate 1: GREEN and keeps the C bass -> passes the gate.
_RIGHT = (
    '{"notes":[{"onset":"0","duration":"1","pitch":64,"voice":"melody"},'
    '{"onset":"0","duration":"1","pitch":48,"voice":"bass"}]}'
)
_SCRIPT = [_WRONG, '{"overall":0.8}', _RIGHT, '{"overall":0.8}']


def test_paired_best_of_n_isolates_selection_benefit() -> None:
    res = paired_best_of_n(
        [_ITEM], ArrangeGoal(), lambda: FakeLLM(_SCRIPT), MEDIAN_HAND, n=2
    )
    # both draws are GREEN, so the green delta is zero — the difference is faithfulness
    assert res.best_of_1.green_rate == 1.0
    assert res.best_of_n.green_rate == 1.0
    # the greedy draw drops the bass root and fails the joint gate; best-of-2 recovers it
    assert res.best_of_1.joint_success == 0.0
    assert res.best_of_n.joint_success == 1.0
    assert res.joint_delta == 1.0
    assert res.green_delta == 0.0
    assert res.items == 1


def test_paired_best_of_n_deterministic() -> None:
    a = paired_best_of_n([_ITEM], ArrangeGoal(), lambda: FakeLLM(_SCRIPT), MEDIAN_HAND, n=2)
    b = paired_best_of_n([_ITEM], ArrangeGoal(), lambda: FakeLLM(_SCRIPT), MEDIAN_HAND, n=2)
    assert a == b


def test_paired_best_of_n_forces_min_two() -> None:
    # n=1 would make best-of-1 vs best-of-1 degenerate; the function clamps to >=2.
    res = paired_best_of_n(
        [_ITEM], ArrangeGoal(), lambda: FakeLLM(_SCRIPT), MEDIAN_HAND, n=1
    )
    assert res.n == 2


def test_green_delta_is_never_negative_by_construction() -> None:
    # best-of-N selects over a superset that includes the greedy draw, and is_green is
    # _rank's top key, so best-of-N green >= best-of-1 green ALWAYS (structural).
    res = paired_best_of_n(
        [_ITEM], ArrangeGoal(), lambda: FakeLLM(_SCRIPT), MEDIAN_HAND, n=2
    )
    assert res.green_delta >= 0.0
    assert res.best_of_n.green_rate >= res.best_of_1.green_rate
