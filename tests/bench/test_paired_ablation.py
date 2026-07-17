from fractions import Fraction as F

from fretsure.agent.arranger import ArrangeGoal
from fretsure.agent.harness import arrange_pool, best_of_k
from fretsure.bench.ablation import paired_best_of_n, paired_critic
from fretsure.bench.corpus import CorpusItem
from fretsure.geometry import note_pitch
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


# --- paired critic ablation ---

# melody E4 only. Candidate 0 adds a harmony note (lower harmony-jaccard) but earns a
# high critic; candidate 1 is melody-only (perfect harmony-jaccard) with a low critic.
# The critic term (rank index 3) outranks harmony (index 4), so it flips the selection.
_MEL_IR = MusicIR((Note(F(0), F(1), 64, "melody"),), (), Meta("C", (4, 4), 90.0, "t", "t", "PD"))
_MEL_ITEM = CorpusItem(_MEL_IR, "test", "generated", 1, "critic-1")
_WITH_HARMONY = (
    '{"notes":[{"onset":"0","duration":"1","pitch":64,"voice":"melody"},'
    '{"onset":"0","duration":"1","pitch":67,"voice":"harmony"}]}'
)
_MELODY_ONLY = '{"notes":[{"onset":"0","duration":"1","pitch":64,"voice":"melody"}]}'
_CRITIC_SCRIPT = [_WITH_HARMONY, '{"overall":0.9}', _MELODY_ONLY, '{"overall":0.3}']


def test_critic_term_flips_selection_on_a_shared_pool() -> None:
    pool = arrange_pool(_MEL_IR, ArrangeGoal(), FakeLLM(_CRITIC_SCRIPT), n=2, use_critic=True)
    on = best_of_k(pool, 2, use_critic=True)
    off = best_of_k(pool, 2, use_critic=False)
    assert on.tab is not None and off.tab is not None
    onp = {note_pitch(x.string, x.fret, on.tab.tuning, on.tab.capo) for x in on.tab.notes}
    offp = {note_pitch(x.string, x.fret, off.tab.tuning, off.tab.capo) for x in off.tab.notes}
    assert 67 in onp  # critic (0.9) picked the harmony-added candidate
    assert 67 not in offp  # without critic, harmony-jaccard picked the melody-only one


def test_paired_critic_captures_selection_effect() -> None:
    res = paired_critic(
        [_MEL_ITEM], ArrangeGoal(), lambda: FakeLLM(_CRITIC_SCRIPT), MEDIAN_HAND, n=2
    )
    # critic ranks below green, so it can never change green-ness (structural).
    assert res.green_delta == 0.0
    # taste_delta is the critic's real yardstick: enabling it selects the higher-critic
    # candidate (0.9 vs 0.3) -> taste goes up. (This is what the critic is FOR.)
    assert res.taste_with > res.taste_without
    assert res.taste_delta > 0.0
    # joint_delta is a SIDE EFFECT, not the objective: here the ranker keys on
    # melody_recall while the joint gate keys on top-voice melody_f1, so the critic's
    # pick (melody 64 + harmony 67 above it) fails the gate -> joint_delta == -1. This
    # asserts the machinery captures the effect; the sign is not the critic's verdict.
    # This legacy compatibility assertion is intentionally tied to the frozen
    # fidelity@0.3.0 exact-onset/top-voice semantics. A future checker version would
    # require an explicit preregistration and deliberate test update.
    assert res.joint_delta == -1.0
    assert res.items == 1


def test_paired_critic_deterministic() -> None:
    a = paired_critic([_MEL_ITEM], ArrangeGoal(), lambda: FakeLLM(_CRITIC_SCRIPT), MEDIAN_HAND, n=2)
    b = paired_critic([_MEL_ITEM], ArrangeGoal(), lambda: FakeLLM(_CRITIC_SCRIPT), MEDIAN_HAND, n=2)
    assert a == b
