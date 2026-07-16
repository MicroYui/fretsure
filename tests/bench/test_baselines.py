from fractions import Fraction as F

from fretsure.agent.arranger import ArrangeGoal
from fretsure.bench.baselines import baseline_pure_solver, baseline_raw_llm
from fretsure.bench.generator import GenConfig, generate_leadsheet
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import Meta, MusicIR, Note
from fretsure.llm.client import FakeLLM
from fretsure.oracle.core import check_playability
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.api import Infeasible
from fretsure.tab import Tab

_IR = MusicIR((Note(F(0), F(1), 64, "melody"), Note(F(0), F(1), 40, "bass")), (),
              Meta("C", (4, 4), 90.0, "t", "t", "PD"))

# a raw LLM tab that is UNPLAYABLE (fret 1 + fret 15) — the point of the B1 baseline
_RAW_RED = (
    '{"tuning":[40,45,50,55,59,64],"capo":0,"notes":['
    '{"onset":"0/1","duration":"1/1","string":0,"fret":1,"left_finger":1,"right_finger":"p"},'
    '{"onset":"0/1","duration":"1/1","string":1,"fret":15,"left_finger":4,"right_finger":"i"}]}'
)


def test_raw_llm_baseline_can_produce_unplayable() -> None:
    tab = baseline_raw_llm(_IR, ArrangeGoal(), FakeLLM([_RAW_RED]), MEDIAN_HAND)
    assert isinstance(tab, Tab)
    assert check_playability(tab, MEDIAN_HAND).verdict == "RED"  # not verified/repaired


def test_raw_llm_baseline_bad_output_is_none() -> None:
    assert (
        baseline_raw_llm(
            _IR,
            ArrangeGoal(),
            FakeLLM(["not a tab"]),
            MEDIAN_HAND,
        )
        is None
    )


def test_pure_solver_baseline_never_red() -> None:
    ir = generate_leadsheet(GenConfig(seed=1, bars=4))
    result = baseline_pure_solver(ir, ArrangeGoal(tuning=STANDARD_TUNING), MEDIAN_HAND)
    if isinstance(result, Tab):
        assert check_playability(result, MEDIAN_HAND).verdict != "RED"
    else:
        assert isinstance(result, Infeasible)


def test_pure_solver_deterministic() -> None:
    ir = generate_leadsheet(GenConfig(seed=2, bars=4))
    a = baseline_pure_solver(ir, ArrangeGoal(), MEDIAN_HAND)
    b = baseline_pure_solver(ir, ArrangeGoal(), MEDIAN_HAND)
    assert a == b
