import json
from fractions import Fraction as F

import pytest

from fretsure.agent.arranger import ArrangeGoal, ProposalStatus
from fretsure.agent.harness import ArrangeResult
from fretsure.agent.incremental import (
    IncrementalRejectReason,
    arrange_incremental,
    arrange_incremental_pool,
    best_of_incremental_pool,
    melody_seed_target,
)
from fretsure.agent.tools import solve_and_check as original_solve_and_check
from fretsure.geometry import note_pitch
from fretsure.ir import ChordSymbol, Meta, MusicIR, Note
from fretsure.llm.client import FakeLLM
from fretsure.solver.api import Infeasible, InfeasibleCode


def _meta(*, duration: int = 4) -> Meta:
    return Meta("C", (4, 4), 90.0, "test", "test", "PD", F(duration))


def _single_note_ir(*, pitch: int = 64, chords: bool = False) -> MusicIR:
    chord_rows = (
        (ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 0),) if chords else ()
    )
    return MusicIR((Note(F(0), F(1), pitch, "melody"),), chord_rows, _meta())


def _four_note_ir() -> MusicIR:
    return MusicIR(
        tuple(
            Note(F(onset), F(1), pitch, "melody")
            for onset, pitch in enumerate((64, 65, 67, 64))
        ),
        (),
        _meta(),
    )


def _reply(melody: tuple[Note, ...], additions: tuple[Note, ...]) -> str:
    rows = [
        {
            "onset": str(note.onset),
            "duration": str(note.duration),
            "pitch": note.pitch,
            "voice": note.voice,
        }
        for note in (*melody, *additions)
    ]
    return json.dumps({"notes": rows}, separators=(",", ":"))


def _played_pitches(result: ArrangeResult) -> set[int]:
    tab = result.tab
    assert tab is not None
    return {
        note_pitch(note.string, note.fret, tab.tuning, tab.capo)
        for note in tab.notes
    }


def test_seed_is_exact_melody_and_is_solved_before_any_model_call() -> None:
    ir = MusicIR(
        (
            Note(F(0), F(1), 64, "melody"),
            Note(F(0), F(1), 40, "bass"),
            Note(F(1), F(1), 55, "harmony"),
        ),
        (),
        _meta(),
    )
    llm = FakeLLM(["not json"])

    pool = arrange_incremental_pool(
        ir,
        ArrangeGoal(),
        llm,
        n=1,
        max_iters=0,
        use_critic=False,
    )

    assert melody_seed_target(ir) == (Note(F(0), F(1), 64, "melody"),)
    assert pool.seed_checkpoint is not None
    assert pool.seed_checkpoint.oracle.verdict == "GREEN"
    assert [step.detail for step in pool.seed_trace_steps] == [
        "Solved the exact melody-only seed before any model call."
    ]
    assert len(llm.calls) == 1  # proposal happens only after the GREEN seed proof


def test_seed_coalescing_cannot_extend_melody_from_another_voice() -> None:
    ir = MusicIR(
        (
            Note(F(0), F(1), 64, "melody"),
            Note(F(0), F(4), 64, "harmony"),
        ),
        (),
        _meta(),
    )

    assert melody_seed_target(ir) == (Note(F(0), F(1), 64, "melody"),)


def test_non_green_seed_makes_zero_model_calls_and_returns_no_tab() -> None:
    ir = _single_note_ir(pitch=20)
    llm = FakeLLM([])

    pool = arrange_incremental_pool(ir, ArrangeGoal(), llm, n=1, use_critic=False)
    result = best_of_incremental_pool(pool, 1, use_critic=False)

    assert pool.seed_checkpoint is None
    assert pool.llm_calls == 0
    assert llm.calls == []
    assert result.tab is None and result.oracle is None


def test_green_full_batch_is_accepted_and_model_melody_rewrite_is_ignored() -> None:
    ir = _single_note_ir()
    rewritten_melody = (Note(F(0), F(1), 65, "melody"),)
    bass = (Note(F(0), F(1), 40, "bass"),)
    llm = FakeLLM([_reply(rewritten_melody, bass)])

    pool = arrange_incremental_pool(
        ir,
        ArrangeGoal(),
        llm,
        n=1,
        max_iters=8,
        use_critic=False,
    )
    candidate = pool.candidates[0]
    result = best_of_incremental_pool(pool, 1, use_critic=False)

    assert candidate.proposal.status is ProposalStatus.LLM_SUCCESS
    assert candidate.proposed_addition_count == 2
    assert candidate.accepted_addition_count == 1
    assert candidate.solver_calls == 1
    assert any(
        trial.reason is IncrementalRejectReason.STATIC_GATE
        and trial.batch == rewritten_melody
        for trial in candidate.trials
    )
    assert result.oracle is not None and result.oracle.verdict == "GREEN"
    assert _played_pitches(result) == {40, 64}
    assert 65 not in _played_pitches(result)
    result.trace.to_public_dict()
    assert result.trace.steps[-1].event == "CANDIDATE_SELECTED"


def test_model_fallback_is_never_reported_as_agent_contribution() -> None:
    ir = _single_note_ir(chords=True)
    pool = arrange_incremental_pool(
        ir,
        ArrangeGoal(),
        FakeLLM(["malformed"]),
        n=1,
        use_critic=False,
    )
    candidate = pool.candidates[0]

    assert candidate.proposal.status is ProposalStatus.PARSE_VALIDATION_FALLBACK
    assert candidate.proposed_addition_count == 0
    assert candidate.accepted_addition_count == 0
    assert candidate.trials == ()
    assert candidate.best.target == melody_seed_target(ir)
    result = best_of_incremental_pool(pool, 1, use_critic=False)
    selection = result.trace.steps[-1]
    assert selection.event == "CANDIDATE_SELECTED"
    assert selection.candidate_index is None
    assert selection.data["winner_candidate_index"] is None
    assert "deterministic melody baseline" in selection.detail


def test_static_quality_gate_rejects_voice_crossing_without_trial_solve() -> None:
    ir = _single_note_ir()
    reply = _reply(ir.notes, (Note(F(0), F(1), 67, "harmony"),))
    pool = arrange_incremental_pool(
        ir,
        ArrangeGoal(),
        FakeLLM([reply]),
        n=1,
        use_critic=False,
    )
    candidate = pool.candidates[0]

    assert candidate.solver_calls == 0
    assert candidate.accepted_addition_count == 0
    assert len(candidate.trials) == 1
    assert candidate.trials[0].reason is IncrementalRejectReason.STATIC_GATE
    assert candidate.trials[0].solver_called is False
    result = best_of_incremental_pool(pool, 1, use_critic=False)
    selection = result.trace.steps[-1]
    assert candidate.proposal.status is ProposalStatus.LLM_SUCCESS
    assert selection.candidate_index is None
    assert selection.data["winner_candidate_index"] is None
    assert pool.solver_calls == 1  # shared seed only


def test_failed_full_batch_is_bisected_and_retains_the_good_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ir = MusicIR(
        tuple(Note(F(i), F(1), 64 + (i % 3), "melody") for i in range(8)),
        (),
        _meta(duration=8),
    )
    additions = (
        Note(F(0), F(1), 40, "bass"),
        Note(F(4), F(1), 41, "bass"),
    )
    original = original_solve_and_check

    def fail_multi_addition(
        target: tuple[Note, ...],
        tuning: tuple[int, ...],
        capo: int,
        profile: object,
        **kwargs: object,
    ) -> object:
        if sum(note.voice != "melody" for note in target) > 1:
            return (
                Infeasible(
                    InfeasibleCode.NO_FRAME_CONFIG,
                    F(2),
                    "synthetic bounded-search miss",
                        (41,),
                ),
                None,
            )
        return original(target, tuning, capo, profile, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "fretsure.agent.incremental.solve_and_check", fail_multi_addition
    )
    pool = arrange_incremental_pool(
        ir,
        ArrangeGoal(),
        FakeLLM([_reply(ir.notes, additions)]),
        n=1,
        max_iters=8,
        use_critic=False,
    )
    candidate = pool.candidates[0]

    assert candidate.solver_calls == 3
    assert [trial.accepted for trial in candidate.trials] == [False, True, False]
    assert candidate.trials[0].reason is IncrementalRejectReason.NO_TAB
    assert candidate.accepted_addition_count == 1
    assert {note.pitch for note in candidate.best.target if note.voice != "melody"} == {40}
    assert candidate.best.oracle.verdict == "GREEN"


def test_one_simultaneous_chord_attack_is_atomic_during_bisection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ir = MusicIR(
        (
            Note(F(0), F(1), 64, "melody"),
            Note(F(1), F(1), 65, "melody"),
        ),
        (),
        _meta(),
    )
    chord_attack = (
        Note(F(0), F(1), 40, "bass"),
        Note(F(0), F(1), 52, "harmony"),
    )
    original = original_solve_and_check

    def reject_chord(
        target: tuple[Note, ...],
        tuning: tuple[int, ...],
        capo: int,
        profile: object,
        **kwargs: object,
    ) -> object:
        if any(note.voice != "melody" for note in target):
            return (
                Infeasible(
                    InfeasibleCode.NO_FRAME_CONFIG,
                    F(0),
                    "synthetic bounded-search miss",
                    (40, 52),
                ),
                None,
            )
        return original(target, tuning, capo, profile, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("fretsure.agent.incremental.solve_and_check", reject_chord)
    pool = arrange_incremental_pool(
        ir,
        ArrangeGoal(),
        FakeLLM([_reply(ir.notes, chord_attack)]),
        n=1,
        use_critic=False,
    )
    candidate = pool.candidates[0]

    assert candidate.solver_calls == 1
    assert len(candidate.trials) == 1
    assert candidate.trials[0].batch == chord_attack
    assert candidate.accepted_addition_count == 0


def test_batch_gain_does_not_require_every_note_to_own_a_separate_coverage_point() -> None:
    ir = _four_note_ir()
    # The beat/pulse quality buckets distinguish beats 1 and 2 in the same half
    # of a measure, so continuous bass motion remains a deterministic gain.
    additions = (
        Note(F(0), F(1), 40, "bass"),
        Note(F(1), F(1), 41, "bass"),
    )
    pool = arrange_incremental_pool(
        ir,
        ArrangeGoal(),
        FakeLLM([_reply(ir.notes, additions)]),
        n=1,
        use_critic=False,
    )

    assert pool.candidates[0].accepted_addition_count == 2
    assert pool.candidates[0].solver_calls == 2


def test_dense_eight_bar_proposal_is_layered_across_the_whole_piece() -> None:
    melody = tuple(
        Note(F(onset), F(1), 64, "melody") for onset in range(8 * 4)
    )
    chords = tuple(
        ChordSymbol(F(bar * 4), "C", frozenset({0, 4, 7}), 0)
        for bar in range(8)
    )
    ir = MusicIR(melody, chords, _meta(duration=32))
    additions = tuple(
        note
        for bar in range(8)
        for note in (
            Note(F(bar * 4), F(4), 48, "bass"),
            Note(F(bar * 4 + 1), F(1), 48, "bass"),
            Note(F(bar * 4 + 2), F(1), 55, "harmony"),
            Note(F(bar * 4 + 3), F(1), 52, "harmony"),
        )
    )
    pool = arrange_incremental_pool(
        ir,
        ArrangeGoal(),
        FakeLLM([_reply(ir.notes, additions)]),
        n=1,
        use_critic=False,
    )
    candidate = pool.candidates[0]
    proposed_step = candidate.trace_steps[0]
    accepted = tuple(note for note in candidate.best.target if note.voice != "melody")

    assert candidate.proposed_addition_count == 32
    assert proposed_step.data["scheduled_addition_count"] == 16
    assert candidate.solver_calls <= 8
    assert candidate.best.oracle.verdict == "GREEN"
    assert {note.onset for note in candidate.trials[0].batch} >= {F(0), F(28)}
    for bar in range(8):
        bar_start = F(bar * 4)
        bar_notes = tuple(
            note for note in accepted if bar_start <= note.onset < bar_start + 4
        )
        assert len({note.onset for note in bar_notes}) <= 2
        assert any(
            note.voice == "bass"
            and note.onset == bar_start
            and note.pitch % 12 == 0
            for note in bar_notes
        )
        assert any(
            note.voice == "harmony" and note.onset == bar_start + 2
            for note in bar_notes
        )


def test_failed_bass_bisection_cannot_starve_the_color_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    melody = tuple(Note(F(onset), F(1), 64, "melody") for onset in range(8 * 4))
    chords = tuple(
        ChordSymbol(F(bar * 4), "C", frozenset({0, 4, 7}), 0)
        for bar in range(8)
    )
    ir = MusicIR(melody, chords, _meta(duration=32))
    additions = tuple(
        note
        for bar in range(8)
        for note in (
            Note(F(bar * 4), F(4), 48, "bass"),
            Note(F(bar * 4 + 2), F(1), 55, "harmony"),
        )
    )
    original = original_solve_and_check

    def reject_bass(
        target: tuple[Note, ...],
        tuning: tuple[int, ...],
        capo: int,
        profile: object,
        **kwargs: object,
    ) -> object:
        if any(note.voice == "bass" for note in target):
            return (
                Infeasible(
                    InfeasibleCode.NO_FRAME_CONFIG,
                    F(0),
                    "synthetic bass-layer miss",
                    (40, 52),
                ),
                None,
            )
        return original(target, tuning, capo, profile, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr("fretsure.agent.incremental.solve_and_check", reject_bass)
    pool = arrange_incremental_pool(
        ir,
        ArrangeGoal(),
        FakeLLM([_reply(ir.notes, additions)]),
        n=1,
        use_critic=False,
    )
    candidate = pool.candidates[0]

    assert candidate.solver_calls == 8
    assert {note.voice for note in candidate.trials[0].batch} == {"bass"}
    assert {note.voice for note in candidate.trials[1].batch} == {"harmony"}
    assert candidate.trials[1].accepted is True
    first_bass_half = {note.onset for note in candidate.trials[2].batch}
    second_bass_half = {note.onset for note in candidate.trials[3].batch}
    assert len(first_bass_half) == len(second_bass_half) == 4
    assert first_bass_half.isdisjoint(second_bass_half)
    assert any(note.voice == "harmony" for note in candidate.best.target)
    assert not any(note.voice == "bass" for note in candidate.best.target)


def test_legal_silent_gap_melody_fill_is_green_and_preserves_exact_anchors() -> None:
    ir = MusicIR(
        (
            Note(F(0), F(1), 64, "melody"),
            Note(F(2), F(1), 67, "melody"),
        ),
        (),
        _meta(),
    )
    fill = Note(F(1), F(1), 65, "melody")
    pool = arrange_incremental_pool(
        ir,
        ArrangeGoal(),
        FakeLLM([_reply(ir.notes, (fill,))]),
        n=1,
        use_critic=False,
    )
    candidate = pool.candidates[0]

    assert candidate.proposed_addition_count == candidate.accepted_addition_count == 1
    assert candidate.solver_calls == 1
    assert candidate.best.oracle.verdict == "GREEN"
    assert fill in candidate.best.target
    assert set(melody_seed_target(ir)).issubset(candidate.best.target)
    assert candidate.best.faithfulness.melody_f1 == 1.0


@pytest.mark.parametrize(
    "fill",
    [
        Note(F(1, 2), F(1), 65, "melody"),  # overlaps the first source note
        Note(F(0), F(1, 2), 65, "melody"),  # occupies a source onset
        Note(F(1), F(1), 52, "melody"),  # exceeds the bounded neighbor leaps
    ],
)
def test_unsafe_melody_fill_is_rejected_before_solver(fill: Note) -> None:
    ir = MusicIR(
        (
            Note(F(0), F(1), 64, "melody"),
            Note(F(2), F(1), 67, "melody"),
        ),
        (),
        _meta(),
    )
    pool = arrange_incremental_pool(
        ir,
        ArrangeGoal(),
        FakeLLM([_reply(ir.notes, (fill,))]),
        n=1,
        use_critic=False,
    )
    candidate = pool.candidates[0]

    assert candidate.proposed_addition_count == 1
    assert candidate.accepted_addition_count == 0
    assert candidate.solver_calls == 0
    assert len(candidate.trials) == 1
    assert candidate.trials[0].reason is IncrementalRejectReason.STATIC_GATE
    assert candidate.best.target == melody_seed_target(ir)


def test_green_but_source_faithfulness_regression_is_rolled_back() -> None:
    ir = MusicIR(
        (Note(F(0), F(1), 60, "melody"),),
        (ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 0),),
        _meta(),
    )
    # E is a legal C-chord tone and below the melody, but it becomes the lowest
    # sounding pitch at the chord onset and changes bass-root accuracy 1 -> 0.
    addition = (Note(F(0), F(1), 52, "bass"),)
    pool = arrange_incremental_pool(
        ir,
        ArrangeGoal(),
        FakeLLM([_reply(ir.notes, addition)]),
        n=1,
        use_critic=False,
    )
    candidate = pool.candidates[0]

    assert candidate.solver_calls == 1
    assert candidate.trials[0].verdict == "GREEN"
    assert candidate.trials[0].reason is IncrementalRejectReason.FAITHFULNESS_REGRESSION
    assert candidate.accepted_addition_count == 0
    assert candidate.best.faithfulness.bass_root == 1.0


def test_melody_only_rank_rewards_valid_enrichment_without_legacy_harmony_penalty() -> None:
    ir = _single_note_ir()
    enriched = _reply(ir.notes, (Note(F(0), F(1), 40, "bass"),))
    llm = FakeLLM(["malformed", enriched])

    pool = arrange_incremental_pool(
        ir,
        ArrangeGoal(),
        llm,
        n=2,
        use_critic=False,
    )
    result = best_of_incremental_pool(pool, 2, use_critic=False)
    selected = next(step for step in result.trace.steps if step.event == "CANDIDATE_SELECTED")

    assert pool.candidates[0].accepted_addition_count == 0
    assert pool.candidates[1].accepted_addition_count == 1
    assert pool.candidates[1].best.faithfulness.evaluated_dimensions == ("melody",)
    assert selected.candidate_index == 1
    assert _played_pitches(result) == {40, 64}


def test_solver_trial_budget_is_strictly_capped_at_eight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    melody = tuple(Note(F(i), F(1), 64, "melody") for i in range(16))
    ir = MusicIR(melody, (), _meta(duration=16))
    additions = tuple(Note(F(i), F(1), 40, "bass") for i in range(0, 16, 2))
    original = original_solve_and_check

    def reject_every_enrichment(
        target: tuple[Note, ...],
        tuning: tuple[int, ...],
        capo: int,
        profile: object,
        **kwargs: object,
    ) -> object:
        if any(note.voice != "melody" for note in target):
            return (
                Infeasible(
                    InfeasibleCode.NO_FRAME_CONFIG,
                    F(0),
                    "synthetic bounded-search miss",
                    (40,),
                ),
                None,
            )
        return original(target, tuning, capo, profile, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "fretsure.agent.incremental.solve_and_check", reject_every_enrichment
    )
    pool = arrange_incremental_pool(
        ir,
        ArrangeGoal(),
        FakeLLM([_reply(ir.notes, additions)]),
        n=1,
        max_iters=8,
        use_critic=False,
    )

    assert pool.candidates[0].solver_calls == 8
    assert pool.solver_calls == 9  # one shared seed plus eight transactional trials
    assert pool.candidates[0].accepted_addition_count == 0


def test_incremental_trial_budget_cannot_exceed_eight() -> None:
    with pytest.raises(ValueError, match="0..8"):
        arrange_incremental_pool(
            _single_note_ir(),
            ArrangeGoal(),
            FakeLLM([]),
            n=1,
            max_iters=9,
            use_critic=False,
        )


def test_worst_case_model_budget_is_proposal_plus_optional_final_critic() -> None:
    ir = _single_note_ir()
    proposal = _reply(ir.notes, (Note(F(0), F(1), 40, "bass"),))
    script: list[str] = []
    for score in (0.4, 0.5, 0.6, 0.7):
        script.extend([proposal, json.dumps({"overall": score})])
    llm = FakeLLM(script)

    pool = arrange_incremental_pool(
        ir,
        ArrangeGoal(),
        llm,
        n=4,
        max_iters=8,
        use_critic=True,
    )
    result = arrange_incremental(
        ir,
        ArrangeGoal(),
        FakeLLM(script),
        n=4,
        max_iters=8,
        use_critic=True,
    )

    assert pool.llm_calls == len(llm.calls) == 8
    assert pool.solver_calls == 5
    assert result.critic is not None and result.critic.overall == 0.7
    assert result.candidates_tried == 4


def test_best_of_one_is_the_exact_prefix_of_the_same_best_of_four_pool() -> None:
    ir = _single_note_ir()
    proposals = [
        "malformed",
        _reply(ir.notes, (Note(F(0), F(1), 40, "bass"),)),
        _reply(ir.notes, (Note(F(0), F(1), 52, "harmony"),)),
        "malformed",
    ]
    pool = arrange_incremental_pool(
        ir,
        ArrangeGoal(),
        FakeLLM(proposals),
        n=4,
        use_critic=False,
    )

    first = best_of_incremental_pool(pool, 1, use_critic=False)
    all_four = best_of_incremental_pool(pool, 4, use_critic=False)
    first_selected = next(
        step for step in first.trace.steps if step.event == "CANDIDATE_SELECTED"
    )
    all_selected = next(
        step for step in all_four.trace.steps if step.event == "CANDIDATE_SELECTED"
    )

    assert first_selected.candidate_index is None
    assert first_selected.data["winner_candidate_index"] is None
    assert all_selected.candidate_index in {1, 2}
    assert first.candidates_tried == 1
    assert all_four.candidates_tried == 4
    first.trace.to_public_dict()
    all_four.trace.to_public_dict()
