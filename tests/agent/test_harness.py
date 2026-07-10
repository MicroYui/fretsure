from fractions import Fraction as F

import pytest

from fretsure.agent.arranger import ArrangeGoal
from fretsure.agent.harness import ArrangeResult, arrange
from fretsure.geometry import STANDARD_TUNING, note_pitch
from fretsure.ir import Meta, MusicIR, Note
from fretsure.llm.client import FakeLLM

_IR = MusicIR((Note(F(0), F(1), 64, "melody"),), (), Meta("C", (4, 4), 90.0, "t", "t", "PD"))

_PROP_A = (
    '{"notes":[{"onset":"0","duration":"1","pitch":64,"voice":"melody"},'
    '{"onset":"0","duration":"1","pitch":40,"voice":"bass"}]}'
)
_PROP_B = (
    '{"notes":[{"onset":"0","duration":"1","pitch":64,"voice":"melody"},'
    '{"onset":"0","duration":"1","pitch":47,"voice":"bass"}]}'
)


def _script() -> list[str]:
    # per candidate: propose, then (both already GREEN -> 0 repair calls) critic
    return [_PROP_A, '{"overall":0.6}', _PROP_B, '{"overall":0.9}']


def test_best_of_n_picks_higher_critic_green_candidate() -> None:
    r = arrange(_IR, ArrangeGoal(), FakeLLM(_script()), n=2)
    assert isinstance(r, ArrangeResult)
    assert r.oracle is not None and r.oracle.verdict == "GREEN"
    assert r.critic is not None and r.critic.overall == 0.9  # candidate B
    assert r.candidates_tried == 2
    assert r.tab is not None
    played = {note_pitch(n.string, n.fret, r.tab.tuning, r.tab.capo) for n in r.tab.notes}
    assert 47 in played and 40 not in played  # B (bass 47), not A (bass 40)
    assert any(s.kind == "SELECT" for s in r.trace.steps)


def test_melody_preserved_in_selection() -> None:
    r = arrange(_IR, ArrangeGoal(), FakeLLM(_script()), n=2)
    assert r.fidelity is not None and r.fidelity.melody_recall == 1.0


def test_deterministic() -> None:
    a = arrange(_IR, ArrangeGoal(), FakeLLM(_script()), n=2)
    b = arrange(_IR, ArrangeGoal(), FakeLLM(_script()), n=2)
    assert a.tab == b.tab and a.critic == b.critic


@pytest.mark.integration
def test_real_llm_end_to_end_arrange() -> None:
    import os

    if not os.environ.get("ANTHROPIC_BASE_URL"):
        pytest.skip("no local LLM proxy configured")
    from fretsure.llm.client import ProxyLLM

    ir = MusicIR(
        (
            Note(F(0), F(1), 60, "melody"),
            Note(F(0), F(1), 48, "bass"),
            Note(F(1), F(1), 64, "melody"),
            Note(F(1), F(1), 48, "bass"),
        ),
        (),
        Meta("C", (4, 4), 90.0, "t", "t", "PD"),
    )
    r = arrange(ir, ArrangeGoal(tuning=STANDARD_TUNING), ProxyLLM(), n=2, max_iters=5)
    assert r.tab is not None and r.oracle is not None
    assert r.oracle.verdict != "RED"
