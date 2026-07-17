from fractions import Fraction as F

from fretsure.agent.arranger import ArrangeGoal
from fretsure.agent.critic import CriticStatus
from fretsure.agent.harness import build_candidate_trajectory
from fretsure.bench.observe import (
    CallSequence,
    CallStage,
    InMemoryObservationSink,
    ObservingLLM,
)
from fretsure.ir import Meta, MusicIR, Note
from fretsure.llm.client import FakeLLM

_IR = MusicIR(
    (Note(F(0), F(1), 64, "melody"),),
    (),
    Meta("C", (4, 4), 90.0, "t", "t", "PD"),
)
_INFEASIBLE = (
    '{"notes":[{"onset":"0","duration":"1","pitch":85,"voice":"melody"},'
    '{"onset":"0","duration":"1","pitch":86,"voice":"harmony"}]}'
)


def test_real_observer_records_unique_proposal_repair_and_critic_contexts() -> None:
    delegate = FakeLLM(
        [
            _INFEASIBLE,
            '{"op":"drop_note","target_onset":"0","target_pitch":85}',
            '{"op":"drop_note","target_onset":"0","target_pitch":86}',
            '{"overall":0.7}',
        ]
    )
    sink = InMemoryObservationSink()
    observed = ObservingLLM(delegate, sink, clock_ns=lambda: 0)
    scopes = CallSequence("run-1").bind_candidate(
        item_id="item-1",
        family_id="family-1",
        cluster_id="cluster-1",
        pair_id="pair-1",
    )

    trajectory = build_candidate_trajectory(
        _IR,
        ArrangeGoal(),
        observed,
        candidate_index=3,
        max_iters=2,
        temperature=0.8,
        call_scope_factory=scopes,
    )

    assert [(intent.stage, intent.stage_ordinal) for intent in sink.intents] == [
        (CallStage.PROPOSAL, 0),
        (CallStage.REPAIR, 0),
        (CallStage.REPAIR, 1),
        (CallStage.CRITIC, 0),
    ]
    assert [intent.candidate_index for intent in sink.intents] == [3, 3, 3, 3]
    assert [intent.call_index for intent in sink.intents] == [0, 1, 2, 3]
    assert len({intent.logical_call_id for intent in sink.intents}) == 4
    assert len(sink.results) == len(sink.attempt_intents) == len(sink.attempt_results) == 4
    assert trajectory.critic_outcome is not None
    assert trajectory.critic_outcome.status is CriticStatus.LLM_SUCCESS
    assert trajectory.work.total_llm_calls == 4
