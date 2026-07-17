import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from fractions import Fraction as F

import pytest

from fretsure.agent.arranger import ArrangeGoal, ProposalStatus
from fretsure.agent.critic import CriticStatus
from fretsure.agent.harness import (
    ArrangeResult,
    CandidateStatus,
    arrange,
    arrange_pool,
    best_of_k,
    build_candidate_trajectory,
)
from fretsure.agent.model_calls import ModelCallStage
from fretsure.geometry import STANDARD_TUNING, note_pitch
from fretsure.ir import Meta, MusicIR, Note
from fretsure.llm.client import FakeLLM, LLMIntegrityError
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
_PROP_INFEASIBLE = (
    '{"notes":[{"onset":"0","duration":"1","pitch":85,"voice":"melody"},'
    '{"onset":"0","duration":"1","pitch":86,"voice":"harmony"}]}'
)


def _script() -> list[str]:
    # per candidate: propose, then (both already GREEN -> 0 repair calls) critic
    return [_PROP_A, '{"overall":0.6}', _PROP_B, '{"overall":0.9}']


class _RecordingScopeFactory:
    def __init__(self) -> None:
        self.active: tuple[ModelCallStage, int, int] | None = None
        self.entries: list[tuple[ModelCallStage, int, int]] = []
        self.exits: list[tuple[ModelCallStage, int, int]] = []

    @contextmanager
    def __call__(
        self,
        stage: ModelCallStage,
        candidate_index: int,
        stage_ordinal: int,
    ) -> Iterator[None]:
        identity = (stage, stage_ordinal, candidate_index)
        assert self.active is None
        self.active = identity
        self.entries.append(identity)
        try:
            yield
        finally:
            assert self.active == identity
            self.exits.append(identity)
            self.active = None


class _ScopeCheckingLLM:
    model_id = "scope-checking-test"

    def __init__(self, scripted: list[str], scopes: _RecordingScopeFactory) -> None:
        self._scripted = iter(scripted)
        self._scopes = scopes
        self.calls: list[tuple[ModelCallStage, int, int]] = []

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        temperature: float = 0.0,
    ) -> str:
        del system, user, max_tokens, temperature
        assert self._scopes.active is not None
        self.calls.append(self._scopes.active)
        return next(self._scripted)


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
    selected = next(step for step in r.trace.steps if step.event == "CANDIDATE_SELECTED")
    assert selected.candidate_index == 1
    assert selected.data["winner_candidate_index"] == 1
    assert selected.data["green_certified"] is True
    assert selected.data["playability_gate"] == "passed"
    assert selected.data["faithfulness_passed"] is True
    assert selected.data["melody_f1"] == 1.0
    assert selected.data["bass_root_accuracy"] is None
    assert selected.data["harmony_jaccard"] is None
    assert selected.data["evaluated_dimensions"] == ["melody"]
    assert selected.data["unavailable_dimensions"] == ["bass_root", "harmony"]
    assert selected.data["ranking_harmony_jaccard"] == 0.5
    winner_replay = [
        step
        for step in r.trace.steps
        if step.event in {"SOLVER_RETURNED_TAB", "PLAYABILITY_CHECKED"}
    ]
    assert winner_replay and {step.candidate_index for step in winner_replay} == {1}
    assert [step.event for step in r.trace.steps] == [
        "CANDIDATE_PROPOSED",
        "SOLVER_RETURNED_TAB",
        "PLAYABILITY_CHECKED",
        "CANDIDATE_FINISHED",
        "CANDIDATE_SELECTED",
    ]


def test_no_tab_retains_one_complete_bounded_failure_replay() -> None:
    result = arrange(
        _IR,
        ArrangeGoal(),
        FakeLLM([_PROP_INFEASIBLE]),
        n=1,
        max_iters=0,
        use_critic=False,
    )

    assert result.tab is None
    assert [step.event for step in result.trace.steps] == [
        "CANDIDATE_PROPOSED",
        "SOLVER_RETURNED_NO_TAB",
        "CANDIDATE_FINISHED",
        "NO_CANDIDATE_SELECTED",
    ]
    assert {step.candidate_index for step in result.trace.steps[:-1]} == {0}
    terminal = result.trace.steps[-1]
    assert terminal.data["playability_gate"] is None
    assert terminal.data["faithfulness_passed"] is None
    assert terminal.data["candidates_considered"] == 1
    result.trace.to_public_dict()


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


def test_pool_retains_every_failed_candidate_trajectory_and_bounded_work() -> None:
    pool = arrange_pool(
        _IR,
        ArrangeGoal(),
        FakeLLM([_PROP_INFEASIBLE, _PROP_INFEASIBLE]),
        n=2,
        max_iters=0,
        use_critic=False,
    )

    assert pool.candidates == (None, None)
    assert len(pool.trajectories) == 2
    for index, trajectory in enumerate(pool.trajectories):
        assert trajectory.index == index
        assert trajectory.status is CandidateStatus.NO_TAB
        assert trajectory.proposal.status is ProposalStatus.LLM_SUCCESS
        assert trajectory.initial_target == trajectory.proposal.target
        assert trajectory.iteration_zero == trajectory.terminal
        assert trajectory.terminal.tab is None
        assert trajectory.terminal.infeasible is not None
        assert trajectory.critic is None
        assert trajectory.work.proposal_llm_calls == 1
        assert trajectory.work.repair_llm_calls == 0
        assert trajectory.work.critic_llm_calls == 0
        assert trajectory.work.solver_calls == 1
        assert trajectory.work.total_llm_calls == 1
        assert trajectory.trace_steps == pool.candidate_traces[index]
        assert {step.candidate_index for step in trajectory.trace_steps} == {index}
    frozen_field = "index"
    with pytest.raises(FrozenInstanceError):
        setattr(pool.trajectories[0], frozen_field, 2)


def test_one_candidate_scopes_every_proposal_repair_and_critic_call_independently() -> None:
    scopes = _RecordingScopeFactory()
    llm = _ScopeCheckingLLM(
        [
            _PROP_INFEASIBLE,
            '{"op":"drop_note","target_onset":"0","target_pitch":85}',
            '{"op":"drop_note","target_onset":"0","target_pitch":86}',
            '{"overall":0.7}',
        ],
        scopes,
    )

    trajectory = build_candidate_trajectory(
        _IR,
        ArrangeGoal(),
        llm,
        candidate_index=3,
        max_iters=2,
        temperature=0.8,
        call_scope_factory=scopes,
    )

    expected = [
        ("proposal", 0, 3),
        ("repair", 0, 3),
        ("repair", 1, 3),
        ("critic", 0, 3),
    ]
    assert scopes.entries == expected
    assert scopes.exits == expected
    assert llm.calls == expected
    assert len(set(llm.calls)) == len(llm.calls)
    assert trajectory.temperature == 0.8
    assert trajectory.critic_outcome is not None
    assert trajectory.critic_outcome.status is CriticStatus.LLM_SUCCESS
    assert trajectory.work.total_llm_calls == 4


def test_scope_factory_failure_aborts_candidate_instead_of_selecting_fallback() -> None:
    def broken_scope_factory(*args: object) -> object:
        del args
        raise RuntimeError("scope internals must be redacted")

    llm = FakeLLM([_PROP_A])
    with pytest.raises(LLMIntegrityError, match="scope entry failed"):
        build_candidate_trajectory(
            _IR,
            ArrangeGoal(),
            llm,
            candidate_index=0,
            call_scope_factory=broken_scope_factory,  # type: ignore[arg-type]
        )
    assert llm.calls == []


def test_trajectory_critic_outcome_retains_parse_fallback_status_and_work() -> None:
    pool = arrange_pool(
        _IR,
        ArrangeGoal(),
        FakeLLM([_PROP_A, "not critic json"]),
        n=1,
    )

    trajectory = pool.trajectories[0]
    assert trajectory.critic_outcome is not None
    assert trajectory.critic_outcome.status is CriticStatus.PARSE_VALIDATION_FALLBACK
    assert trajectory.critic is not None and trajectory.critic.overall == 0.5
    assert trajectory.work.critic_llm_calls == 1


def test_candidate_trace_snapshots_are_deeply_immutable_and_detached() -> None:
    pool = arrange_pool(
        _IR,
        ArrangeGoal(),
        FakeLLM([_PROP_A]),
        n=1,
        use_critic=False,
    )
    trajectory = pool.trajectories[0]
    stored = trajectory.trace_snapshots[0].data_json
    exposed = trajectory.trace_steps
    exposed[0].data["temperature"] = 1.0
    checkpoint = exposed[0].data["target_checkpoint"]
    assert isinstance(checkpoint, dict)
    checkpoint["sha256"] = "0" * 64

    fresh = trajectory.trace_steps
    assert fresh[0].data["temperature"] == 0.0
    assert fresh[0].data["target_checkpoint"]["sha256"] != "0" * 64
    assert trajectory.trace_snapshots[0].data_json == stored
    assert pool.candidate_traces[0][0].data["temperature"] == 0.0
    with pytest.raises(FrozenInstanceError):
        trajectory.trace_snapshots[0].data_json = b"{}"  # type: ignore[misc]


def test_formal_fixed_temperature_and_injected_schedule_are_exact() -> None:
    fixed_llm = FakeLLM([_PROP_A, _PROP_A, _PROP_A])
    fixed = arrange_pool(
        _IR,
        ArrangeGoal(),
        fixed_llm,
        n=3,
        use_critic=False,
        temperature=0.8,
    )
    assert [call["temperature"] for call in fixed_llm.calls] == [0.8, 0.8, 0.8]
    assert [trajectory.temperature for trajectory in fixed.trajectories] == [0.8, 0.8, 0.8]

    scheduled_llm = FakeLLM([_PROP_A, _PROP_A, _PROP_A])
    scheduled = arrange_pool(
        _IR,
        ArrangeGoal(),
        scheduled_llm,
        n=3,
        use_critic=False,
        temperature_schedule=(0.7, 0.8, 0.9),
    )
    assert [call["temperature"] for call in scheduled_llm.calls] == [0.7, 0.8, 0.9]
    assert [trajectory.temperature for trajectory in scheduled.trajectories] == [0.7, 0.8, 0.9]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"temperature": 0},
        {"temperature": True},
        {"temperature": float("nan")},
        {"temperature": 1.1},
        {"temperature_schedule": [0.8, 0.8]},
        {"temperature_schedule": (0.8,)},
        {"temperature_schedule": (0.8, True)},
        {"temperature": 0.8, "temperature_schedule": (0.8, 0.8)},
    ],
)
def test_temperature_controls_fail_closed_before_any_model_call(
    kwargs: dict[str, object],
) -> None:
    llm = FakeLLM([])
    with pytest.raises(ValueError, match="temperature"):
        arrange_pool(
            _IR,
            ArrangeGoal(),
            llm,
            n=2,
            use_critic=False,
            **kwargs,  # type: ignore[arg-type]
        )
    assert llm.calls == []


def test_arrange_wrapper_matches_shared_pool_primitive_without_trace_drift() -> None:
    direct = arrange(_IR, ArrangeGoal(), FakeLLM(_script()), n=2)
    pool = arrange_pool(_IR, ArrangeGoal(), FakeLLM(_script()), n=2)
    selected = best_of_k(pool, 2)

    assert direct.tab == selected.tab
    assert direct.oracle == selected.oracle
    assert direct.fidelity == selected.fidelity
    assert direct.critic == selected.critic
    assert direct.candidates_tried == selected.candidates_tried
    assert direct.trace.to_jsonl() == selected.trace.to_jsonl()
    assert pool.trajectories[1].work.total_llm_calls == 2


def test_public_arrange_results_and_traces_match_clean_prerefactor_goldens() -> None:
    # Captured by executing the clean preregistration commit
    # 44927517958ecd3b9868bafb7bfe6133be25cc8e from a git archive. These hashes
    # compare against the pre-trajectory implementation, not another new code path.
    repaired = arrange(
        _IR,
        ArrangeGoal(),
        FakeLLM(
            [
                _PROP_INFEASIBLE,
                '{"op":"drop_note","target_onset":"0","target_pitch":86}',
            ]
        ),
        n=1,
        max_iters=1,
        use_critic=False,
    )
    cases = {
        "green-critic": arrange(_IR, ArrangeGoal(), FakeLLM(_script()), n=2),
        "repair": repaired,
        "fallback": arrange(
            _IR,
            ArrangeGoal(),
            FakeLLM(["not json"]),
            n=1,
            max_iters=0,
            use_critic=False,
        ),
        "no-tab": arrange(
            _IR,
            ArrangeGoal(),
            FakeLLM([_PROP_INFEASIBLE]),
            n=1,
            max_iters=0,
            use_critic=False,
        ),
    }
    expected = {
        "green-critic": (
            "09a20699f868cc15a4c140164d8ad0fe644c2ec187e80d0f579a4a0511a347e7",
            ("GREEN", 1.0, 0.9, 2),
        ),
        "repair": (
            "a5c7d9878ae8dfba8e05a14dfe9a9968e491e3a0b5fad66f6a0b0ef360f967a1",
            ("GREEN", 0.0, None, 1),
        ),
        "fallback": (
            "5470d25061ff7888ecd8fa097b98c1995f6081521f6516d1f62830b58094ebd2",
            ("GREEN", 1.0, None, 1),
        ),
        "no-tab": (
            "908a8b23d7ab5aeb44b0f1f28cbfba8fcfb1cd5f0fba29fd7dc08d8ee4ac07ee",
            (None, None, None, 1),
        ),
    }

    for name, result in cases.items():
        trace_digest = hashlib.sha256(result.trace.to_jsonl().encode("utf-8")).hexdigest()
        fingerprint = (
            result.oracle.verdict if result.oracle is not None else None,
            result.fidelity.melody_recall if result.fidelity is not None else None,
            result.critic.overall if result.critic is not None else None,
            result.candidates_tried,
        )
        assert (trace_digest, fingerprint) == expected[name]


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
    assert {d.code for d in caught.value.diagnostics} == {OracleInputCode.CANDIDATE_COUNT}


@pytest.mark.parametrize("use_critic", [0, 1, "false", None])
def test_harness_rejects_truthy_critic_controls_before_llm(
    use_critic: object,
) -> None:
    llm = FakeLLM([])
    with pytest.raises(SolverInputError) as caught:
        arrange(_IR, ArrangeGoal(), llm, use_critic=use_critic)  # type: ignore[arg-type]
    assert llm.calls == []
    assert {d.code for d in caught.value.diagnostics} == {OracleInputCode.BOOLEAN_CONTROL}


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
