import json
from fractions import Fraction as F
from typing import Any, cast

import pytest

from fretsure.agent.repair import REPAIR_MAX_TOKENS, repair
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import Note
from fretsure.llm.client import FakeLLM, LLMIntegrityError
from fretsure.oracle.input import OracleInputCode, SolverInputError
from fretsure.oracle.profiles import MEDIAN_HAND

# 85 (melody) + 86 (harmony) each reach only the high-E string -> infeasible together.
_INFEASIBLE = (Note(F(0), F(1), 85, "melody"), Note(F(0), F(1), 86, "harmony"))
_DROP_86 = '{"op": "drop_note", "target_onset": "0", "target_pitch": 86}'
_DROP_85 = '{"op": "drop_note", "target_onset": "0", "target_pitch": 85}'  # melody -> protected
_MISS = '{"op": "drop_note", "target_onset": "0", "target_pitch": 84}'
_AMBER = (Note(F(0), F(1), 41, "harmony"), Note(F(0), F(1), 49, "melody"))
_DROP_41 = '{"op": "drop_note", "target_onset": "0", "target_pitch": 41}'
_AMBER_WITHOUT_MEDIAN_DIAGNOSTICS = (
    Note(F(0), F(1), 44, "harmony"),
    Note(F(0), F(1), 71, "melody"),
)
_DROP_44 = '{"op": "drop_note", "target_onset": "0", "target_pitch": 44}'


def test_already_green_returns_immediately() -> None:
    target = (Note(F(0), F(1), 60, "melody"),)
    r = repair(target, STANDARD_TUNING, 0, MEDIAN_HAND, FakeLLM([]))
    assert r.oracle is not None and r.oracle.verdict == "GREEN"
    assert r.iterations == 0


def test_repair_drops_harmony_to_reach_green() -> None:
    r = repair(_INFEASIBLE, STANDARD_TUNING, 0, MEDIAN_HAND, FakeLLM([_DROP_86]))
    assert r.oracle is not None and r.oracle.verdict == "GREEN"
    assert r.tab is not None
    assert r.iterations == 1
    assert 85 in [n.pitch for n in r.target]  # melody preserved
    assert 86 not in [n.pitch for n in r.target]
    assert any(s.kind == "EDIT" for s in r.trace.steps)


def test_repair_uses_the_public_fixed_output_budget() -> None:
    llm = FakeLLM([_DROP_86])

    repair(_INFEASIBLE, STANDARD_TUNING, 0, MEDIAN_HAND, llm)

    assert llm.calls[0]["max_tokens"] == REPAIR_MAX_TOKENS == 1024


def test_repair_exposes_iteration_zero_and_terminal_solver_states() -> None:
    result = repair(
        _INFEASIBLE,
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
        FakeLLM([_DROP_86]),
    )

    assert result.iteration_zero.iteration == 0
    assert result.iteration_zero.target == _INFEASIBLE
    assert result.iteration_zero.tab is None
    assert result.iteration_zero.oracle is None
    assert result.iteration_zero.infeasible is not None
    assert result.iteration_zero.solved == result.iteration_zero.infeasible
    assert result.terminal.iteration == 1
    assert result.terminal.target == result.target
    assert result.terminal.tab == result.tab
    assert result.terminal.oracle == result.oracle
    assert result.terminal.infeasible is None
    assert result.model_calls == 1
    assert result.solve_calls == 2


def test_iteration_zero_retains_tab_and_oracle_before_repair() -> None:
    result = repair(
        _AMBER,
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
        FakeLLM([_DROP_41]),
        max_iters=2,
    )

    assert result.iteration_zero.tab is not None
    assert result.iteration_zero.oracle is not None
    assert result.iteration_zero.oracle.verdict == "AMBER"
    assert result.iteration_zero.infeasible is None
    assert result.terminal.oracle is not None
    assert result.terminal.oracle.verdict == "GREEN"


def test_repair_accepts_amber_without_median_profile_diagnostics() -> None:
    result = repair(
        _AMBER_WITHOUT_MEDIAN_DIAGNOSTICS,
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
        FakeLLM([_DROP_44]),
        max_iters=2,
    )

    checks = [step for step in result.trace.steps if step.event == "PLAYABILITY_CHECKED"]
    proposed = next(step for step in result.trace.steps if step.event == "REPAIR_EDIT_PROPOSED")
    assert result.iteration_zero.oracle is not None
    assert result.iteration_zero.oracle.verdict == "AMBER"
    assert result.iteration_zero.oracle.diagnostics == ()
    assert checks[0].data["diagnostic_count"] == 0
    assert proposed.data["based_on_diagnostic_codes"] == []
    assert result.oracle is not None and result.oracle.verdict == "GREEN"
    assert result.iterations == 1


def test_repair_trace_is_digest_linked_and_candidate_scoped() -> None:
    r = repair(
        _INFEASIBLE,
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
        FakeLLM([_DROP_86]),
        candidate_index=3,
    )
    events = [step.event for step in r.trace.steps]
    assert events == [
        "SOLVER_RETURNED_NO_TAB",
        "REPAIR_EDIT_PROPOSED",
        "EDIT_APPLIED",
        "RECHECK_STARTED",
        "SOLVER_RETURNED_TAB",
        "PLAYABILITY_CHECKED",
    ]
    assert {step.candidate_index for step in r.trace.steps} == {3}
    edit = next(step for step in r.trace.steps if step.event == "EDIT_APPLIED")
    recheck = next(step for step in r.trace.steps if step.event == "RECHECK_STARTED")
    solved = next(
        step
        for step in r.trace.steps
        if step.event == "SOLVER_RETURNED_TAB" and step.iteration == 1
    )
    after = edit.data["after_target_sha256"]
    assert after == recheck.data["target_checkpoint"]["sha256"]
    assert after == solved.data["target_sha256"]
    oracle = next(step for step in r.trace.steps if step.event == "PLAYABILITY_CHECKED")
    assert oracle.data["verdict"] == "GREEN"
    assert oracle.data["diagnostics"] == []
    assert oracle.data["terminal_reason"] == "GREEN"


def test_trace_replays_localized_diagnostic_edit_and_green_recheck() -> None:
    result = repair(
        _AMBER,
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
        FakeLLM([_DROP_41]),
        max_iters=2,
    )

    events = [step.event for step in result.trace.steps]
    assert events == [
        "SOLVER_RETURNED_TAB",
        "PLAYABILITY_CHECKED",
        "REPAIR_EDIT_PROPOSED",
        "EDIT_APPLIED",
        "RECHECK_STARTED",
        "SOLVER_RETURNED_TAB",
        "PLAYABILITY_CHECKED",
    ]
    checks = [step for step in result.trace.steps if step.event == "PLAYABILITY_CHECKED"]
    assert [step.data["verdict"] for step in checks] == ["AMBER", "GREEN"]
    first_diagnostics = checks[0].data["diagnostics"]
    assert {row["code"] for row in first_diagnostics} == {
        "FRET_SPAN",
        "SHIFT_SPEED",
    }
    assert all(row["measure"] == 1 and row["beat"] == "1/1" for row in first_diagnostics)
    edit = next(step for step in result.trace.steps if step.event == "EDIT_APPLIED")
    assert edit.data["edit"] == {
        "op": "drop_note",
        "target_onset": "0/1",
        "target_pitch": 41,
        "arg": 0,
    }
    assert edit.data["status"] == "applied"
    assert checks[-1].data["terminal_reason"] == "GREEN"


def test_repair_trace_never_records_raw_model_reply_or_transport_exception() -> None:
    secret_reply = f"private-token-before {_DROP_86} private-token-after"
    repaired = repair(
        _INFEASIBLE,
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
        FakeLLM([secret_reply]),
    )
    encoded = repaired.trace.to_jsonl()
    assert "private-token" not in encoded
    assert _DROP_86 not in encoded

    zero_denominator = repair(
        _INFEASIBLE,
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
        FakeLLM(
            [
                '{"op":"drop_note","target_onset":"1/0","target_pitch":86}'
            ]
        ),
        max_iters=1,
    )
    invalid = next(
        step for step in zero_denominator.trace.steps if step.event == "MODEL_EDIT_INVALID"
    )
    assert invalid.data["reason_code"] == "INVALID_EDIT_SCHEMA"

    infinite_pitch = repair(
        _INFEASIBLE,
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
        FakeLLM(
            [
                '{"op":"drop_note","target_onset":"0","target_pitch":Infinity}'
            ]
        ),
        max_iters=1,
    )
    invalid = next(
        step for step in infinite_pitch.trace.steps if step.event == "MODEL_EDIT_INVALID"
    )
    assert invalid.data["reason_code"] == "INVALID_EDIT_SCHEMA"

    class FailingLLM:
        @property
        def model_id(self) -> str:
            return "failing-test"

        def complete(self, **kwargs: object) -> str:
            del kwargs
            raise RuntimeError("Bearer top-secret at https://proxy.invalid/private?auth=top-secret")

    failed = repair(
        _INFEASIBLE,
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
        FailingLLM(),
    )
    failed_json = failed.trace.to_jsonl()
    assert "top-secret" not in failed_json
    assert "proxy.invalid" not in failed_json
    assert failed.iteration_zero == failed.terminal
    assert failed.model_calls == 1
    assert failed.solve_calls == 1
    failure_rows = [json.loads(line) for line in failed_json.splitlines()]
    failure = next(row for row in failure_rows if row["event"] == "MODEL_CALL_FAILED")
    assert failure["data"] == {
        "reason_code": "LLM_TRANSPORT_FAILURE",
        "target_sha256": failure["data"]["target_sha256"],
    }


def test_repair_never_converts_integrity_failure_into_terminal_model_failure() -> None:
    class IntegrityFailingLLM:
        model_id = "integrity-test"

        def complete(self, **kwargs: object) -> str:
            del kwargs
            raise LLMIntegrityError("formal observation failed")

    with pytest.raises(LLMIntegrityError, match="formal observation failed"):
        repair(_INFEASIBLE, STANDARD_TUNING, 0, MEDIAN_HAND, IntegrityFailingLLM())


def test_nonmatching_edit_is_explicit_rejection_not_an_applied_edit() -> None:
    r = repair(
        _INFEASIBLE,
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
        FakeLLM([_MISS]),
        max_iters=1,
    )
    rejected = next(step for step in r.trace.steps if step.event == "EDIT_REJECTED")
    assert rejected.data["reason_code"] == "TARGET_NOT_FOUND"
    assert rejected.data["status"] == "noop"
    assert rejected.data["state_changed"] is False
    assert not any(step.event == "EDIT_APPLIED" for step in r.trace.steps)


def test_melody_protected_edit_is_skipped_then_valid_edit_applied() -> None:
    r = repair(_INFEASIBLE, STANDARD_TUNING, 0, MEDIAN_HAND, FakeLLM([_DROP_85, _DROP_86]))
    assert r.oracle is not None and r.oracle.verdict == "GREEN"
    assert r.iterations == 2  # first attempt protected, second worked
    protected = next(
        step
        for step in r.trace.steps
        if step.event == "EDIT_REJECTED" and step.data["reason_code"] == "MELODY_PROTECTED"
    )
    assert protected.data["status"] == "rejected"


def test_max_iters_stops_without_crash() -> None:
    r = repair(_INFEASIBLE, STANDARD_TUNING, 0, MEDIAN_HAND, FakeLLM([_DROP_85] * 3), max_iters=2)
    assert r.iterations == 2
    assert r.tab is None and r.infeasible is not None  # never reached GREEN


@pytest.mark.parametrize("max_iters", [-1, True, 1.5, 65])
def test_repair_rejects_unbounded_iteration_controls_before_llm(
    max_iters: object,
) -> None:
    llm = FakeLLM([])
    with pytest.raises(SolverInputError) as caught:
        repair(
            _INFEASIBLE,
            STANDARD_TUNING,
            0,
            MEDIAN_HAND,
            llm,
            max_iters=max_iters,  # type: ignore[arg-type]
        )
    assert llm.calls == []
    assert {d.code for d in caught.value.diagnostics} == {OracleInputCode.REPAIR_ITERATIONS}


def test_repair_validates_target_before_sorting_or_llm() -> None:
    llm = FakeLLM([])

    with pytest.raises(SolverInputError) as caught:
        repair(
            cast(tuple[Note, ...], (cast(Any, object()),)),
            STANDARD_TUNING,
            0,
            MEDIAN_HAND,
            llm,
        )

    assert llm.calls == []
    assert OracleInputCode.NOTE_TYPE in {diagnostic.code for diagnostic in caught.value.diagnostics}


def test_deterministic() -> None:
    a = repair(_INFEASIBLE, STANDARD_TUNING, 0, MEDIAN_HAND, FakeLLM([_DROP_86]))
    b = repair(_INFEASIBLE, STANDARD_TUNING, 0, MEDIAN_HAND, FakeLLM([_DROP_86]))
    assert a.tab == b.tab and a.iterations == b.iterations


@pytest.mark.integration
def test_repair_with_real_llm_reaches_green() -> None:
    import os

    if not os.environ.get("ANTHROPIC_BASE_URL"):
        pytest.skip("no local LLM proxy configured")
    from fretsure.llm.client import ProxyLLM

    r = repair(_INFEASIBLE, STANDARD_TUNING, 0, MEDIAN_HAND, ProxyLLM(), max_iters=5)
    assert r.oracle is not None and r.oracle.verdict == "GREEN"
    assert 85 in [n.pitch for n in r.target]  # melody preserved by the real LLM
