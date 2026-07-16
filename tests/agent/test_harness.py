from fractions import Fraction as F

import pytest

from fretsure.agent.arranger import ArrangeGoal
from fretsure.agent.harness import ArrangeResult, arrange
from fretsure.geometry import STANDARD_TUNING, note_pitch
from fretsure.ir import Meta, MusicIR, Note
from fretsure.llm.client import FakeLLM
from fretsure.oracle.input import OracleInputCode, SolverInputError

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


# IR with a real bass voice: selection must prefer the candidate that keeps it.
_IR_BASS = MusicIR(
    (Note(F(0), F(1), 64, "melody"), Note(F(0), F(1), 40, "bass")),
    (),
    Meta("C", (4, 4), 90.0, "t", "t", "PD"),
)
_PROP_DROPS_BASS = (  # bass re-octaved to 52 (same pc, wrong pitch) -> not preserved
    '{"notes":[{"onset":"0","duration":"1","pitch":64,"voice":"melody"},'
    '{"onset":"0","duration":"1","pitch":52,"voice":"bass"}]}'
)
_PROP_KEEPS_BASS = (
    '{"notes":[{"onset":"0","duration":"1","pitch":64,"voice":"melody"},'
    '{"onset":"0","duration":"1","pitch":40,"voice":"bass"}]}'
)


def test_selection_prefers_bass_preservation_over_order() -> None:
    # Both GREEN, equal melody + equal critic + equal harmony: only bass differs.
    # The bass-dropping candidate is proposed first, so a bass-blind ranker would
    # keep it; ranking on bass preservation must instead pick the second.
    script = [_PROP_DROPS_BASS, '{"overall":0.8}', _PROP_KEEPS_BASS, '{"overall":0.8}']
    r = arrange(_IR_BASS, ArrangeGoal(), FakeLLM(script), n=2)
    assert r.tab is not None and r.oracle is not None and r.oracle.verdict == "GREEN"
    played = {note_pitch(n.string, n.fret, r.tab.tuning, r.tab.capo) for n in r.tab.notes}
    assert 40 in played and 52 not in played  # kept the input bass


def test_best_of_k_is_paired_on_one_pool() -> None:
    # Build ONE pool, then compare best-of-1 (greedy) vs best-of-2 on that SAME
    # pool — no re-sampling. This is the paired comparison the ablation needs.
    from fretsure.agent.harness import arrange_pool, best_of_k

    script = [_PROP_DROPS_BASS, '{"overall":0.8}', _PROP_KEEPS_BASS, '{"overall":0.8}']
    pool = arrange_pool(_IR_BASS, ArrangeGoal(), FakeLLM(script), n=2)
    r1 = best_of_k(pool, 1)
    r2 = best_of_k(pool, 2)
    assert r1.tab is not None and r2.tab is not None
    p1 = {note_pitch(n.string, n.fret, r1.tab.tuning, r1.tab.capo) for n in r1.tab.notes}
    p2 = {note_pitch(n.string, n.fret, r2.tab.tuning, r2.tab.capo) for n in r2.tab.notes}
    assert 52 in p1 and 40 not in p1  # best-of-1 is the greedy (bass-dropping) draw
    assert 40 in p2 and 52 not in p2  # best-of-2 recovers the faithful one
    assert r1.candidates_tried == 1 and r2.candidates_tried == 2


def test_deterministic() -> None:
    a = arrange(_IR, ArrangeGoal(), FakeLLM(_script()), n=2)
    b = arrange(_IR, ArrangeGoal(), FakeLLM(_script()), n=2)
    assert a.tab == b.tab and a.critic == b.critic


def test_arrange_zero_n_makes_no_llm_call() -> None:
    # n=0 must not propose anything: an empty FakeLLM script would raise on any call.
    r = arrange(_IR, ArrangeGoal(), FakeLLM([]), n=0)
    assert r.tab is None and r.oracle is None and r.candidates_tried == 0


@pytest.mark.parametrize("n", [-1, True, 1.5, 65])
def test_harness_rejects_unbounded_candidate_controls_before_llm(n: object) -> None:
    llm = FakeLLM([])
    with pytest.raises(SolverInputError) as caught:
        arrange(_IR, ArrangeGoal(), llm, n=n)  # type: ignore[arg-type]
    assert llm.calls == []
    assert {d.code for d in caught.value.diagnostics} == {
        OracleInputCode.CANDIDATE_COUNT
    }


@pytest.mark.parametrize("use_critic", [0, 1, "false", None])
def test_harness_rejects_truthy_critic_controls_before_llm(
    use_critic: object,
) -> None:
    llm = FakeLLM([])
    with pytest.raises(SolverInputError) as caught:
        arrange(_IR, ArrangeGoal(), llm, use_critic=use_critic)  # type: ignore[arg-type]
    assert llm.calls == []
    assert {d.code for d in caught.value.diagnostics} == {
        OracleInputCode.BOOLEAN_CONTROL
    }


def test_harness_validates_before_candidate_proposal() -> None:
    llm = FakeLLM([])

    with pytest.raises(SolverInputError) as caught:
        arrange(_IR, ArrangeGoal(tuning=(40, 45, 50, 55, 55, 64)), llm, n=1)

    assert llm.calls == []
    assert OracleInputCode.TUNING_ORDER in {
        diagnostic.code for diagnostic in caught.value.diagnostics
    }


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
