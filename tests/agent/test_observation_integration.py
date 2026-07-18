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
_COLLIDING_REVOICE = (
    '{"op":"revoice","target_onset":"0","target_pitch":86,"arg":85}'
)
_DROP_HARMONY = '{"op":"drop_note","target_onset":"0","target_pitch":86}'


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


def test_invalid_applied_edit_observation_is_owned_by_one_repair_event() -> None:
    sink = InMemoryObservationSink()
    observed = ObservingLLM(
        FakeLLM([_INFEASIBLE, _COLLIDING_REVOICE, _DROP_HARMONY]),
        sink,
        clock_ns=lambda: 0,
    )
    scopes = CallSequence("run-invalid-edit").bind_candidate(
        item_id="item-invalid-edit",
        family_id="family-1",
        cluster_id="cluster-1",
        pair_id="pair-invalid-edit",
    )

    trajectory = build_candidate_trajectory(
        _IR,
        ArrangeGoal(),
        observed,
        candidate_index=3,
        max_iters=2,
        use_critic=False,
        temperature=0.8,
        call_scope_factory=scopes,
    )

    repair_intents = [intent for intent in sink.intents if intent.stage is CallStage.REPAIR]
    assert [(intent.stage_ordinal, intent.candidate_index) for intent in repair_intents] == [
        (0, 3),
        (1, 3),
    ]
    assert len(repair_intents) == trajectory.work.repair_llm_calls == 2
    repair_events = [
        step
        for step in trajectory.trace_steps
        if step.event
        in {"REPAIR_EDIT_PROPOSED", "MODEL_EDIT_INVALID", "MODEL_CALL_FAILED"}
    ]
    assert [(step.event, step.iteration) for step in repair_events] == [
        ("MODEL_EDIT_INVALID", 1),
        ("REPAIR_EDIT_PROPOSED", 2),
    ]
    assert len(repair_events) == trajectory.work.repair_llm_calls
    assert sum(step.event == "REPAIR_EDIT_PROPOSED" for step in repair_events) == 1
    assert len(sink.results) == len(sink.intents) == 3
