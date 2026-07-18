from dataclasses import replace
from fractions import Fraction as F
from typing import Any, cast

import pytest

import fretsure.difficulty.simplify as simplify_module
from fretsure.difficulty.simplify import SimplifyResult, simplify_to_tier
from fretsure.difficulty.tiers import BEGINNER
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import Note
from fretsure.llm.client import FakeLLM
from fretsure.oracle.input import OracleInputCode, SolverInputError

# 3 open notes at one onset: playable but exceeds beginner's max 2 simultaneous
_TARGET = (
    Note(F(0), F(1), 64, "melody"),
    Note(F(0), F(1), 40, "bass"),
    Note(F(0), F(1), 55, "harmony"),
)
_DROP = '{"op": "drop_note", "target_onset": "0", "target_pitch": 55}'
_OUT_OF_RANGE_EDIT_TARGET = (
    Note(F(0), F(1), 64, "melody"),
    Note(F(0), F(1), 40, "bass"),
    Note(F(0), F(1), 120, "harmony"),
)
_OCTAVE_120_OUT_OF_RANGE = (
    '{"op": "octave_shift", "target_onset": "0", "target_pitch": 120, "arg": 12}'
)
_DROP_120 = '{"op": "drop_note", "target_onset": "0", "target_pitch": 120}'
_REVOICE_55_COLLISION = (
    '{"op": "revoice", "target_onset": "0", "target_pitch": 55, "arg": 40}'
)


def test_simplify_dense_chord_to_beginner() -> None:
    r = simplify_to_tier(_TARGET, BEGINNER, STANDARD_TUNING, 0, FakeLLM([_DROP]))
    assert isinstance(r, SimplifyResult)
    assert r.tier_result is not None and r.tier_result.meets
    assert r.iterations == 1
    assert 64 in [n.pitch for n in r.target]  # melody preserved
    assert 55 not in [n.pitch for n in r.target]


def test_simplifier_detaches_tier_before_solver_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_tier = replace(BEGINNER)
    real_ensure = simplify_module.ensure_solver_input

    def relax_source_after_barrier(*args: object, **kwargs: object) -> object:
        snapshot = real_ensure(*args, **kwargs)
        object.__setattr__(source_tier, "max_simultaneous", 6)
        object.__setattr__(source_tier, "allow_barre", True)
        object.__setattr__(source_tier, "max_position", 36)
        object.__setattr__(source_tier, "max_shifts_per_bar", 10_000)
        return snapshot

    monkeypatch.setattr(simplify_module, "ensure_solver_input", relax_source_after_barrier)
    result = simplify_module.simplify_to_tier(
        _TARGET,
        source_tier,
        STANDARD_TUNING,
        0,
        FakeLLM([_DROP]),
    )

    assert result.iterations == 1
    assert result.tier_result is not None and result.tier_result.meets


def test_already_meets_zero_iterations() -> None:
    target = (Note(F(0), F(1), 64, "melody"), Note(F(0), F(1), 40, "bass"))
    r = simplify_to_tier(target, BEGINNER, STANDARD_TUNING, 0, FakeLLM([]))
    assert r.tier_result is not None and r.tier_result.meets
    assert r.iterations == 0


def test_deterministic() -> None:
    a = simplify_to_tier(_TARGET, BEGINNER, STANDARD_TUNING, 0, FakeLLM([_DROP]))
    b = simplify_to_tier(_TARGET, BEGINNER, STANDARD_TUNING, 0, FakeLLM([_DROP]))
    assert a.tab == b.tab and a.iterations == b.iterations


def test_simplify_trace_redacts_raw_reply_and_transport_exception() -> None:
    secret_reply = f"private-before {_DROP} private-after"
    repaired = simplify_to_tier(
        _TARGET,
        BEGINNER,
        STANDARD_TUNING,
        0,
        FakeLLM([secret_reply]),
    )
    encoded = repaired.trace.to_jsonl()
    assert "private-before" not in encoded
    assert "private-after" not in encoded
    assert any(step.event == "EDIT_APPLIED" for step in repaired.trace.steps)

    class FailingLLM:
        @property
        def model_id(self) -> str:
            return "failing-test"

        def complete(self, **kwargs: object) -> str:
            del kwargs
            raise RuntimeError("Bearer tier-secret via https://proxy.invalid/private")

    failed = simplify_to_tier(
        _TARGET,
        BEGINNER,
        STANDARD_TUNING,
        0,
        FailingLLM(),
    )
    failed_json = failed.trace.to_jsonl()
    assert "tier-secret" not in failed_json
    assert "proxy.invalid" not in failed_json
    assert any(step.event == "MODEL_CALL_FAILED" for step in failed.trace.steps)


@pytest.mark.parametrize(
    ("target", "invalid_reply", "valid_reply"),
    [
        (_OUT_OF_RANGE_EDIT_TARGET, _OCTAVE_120_OUT_OF_RANGE, _DROP_120),
        (_TARGET, _REVOICE_55_COLLISION, _DROP),
    ],
    ids=["pitch-out-of-range", "onset-pitch-collision"],
)
def test_invalid_post_edit_target_is_rejected_then_rechecked_unchanged(
    target: tuple[Note, ...],
    invalid_reply: str,
    valid_reply: str,
) -> None:
    result = simplify_to_tier(
        target,
        BEGINNER,
        STANDARD_TUNING,
        0,
        FakeLLM([invalid_reply, valid_reply]),
        max_iters=2,
    )

    invalid = next(
        step
        for step in result.trace.steps
        if step.event == "MODEL_EDIT_INVALID" and step.iteration == 1
    )
    recheck = next(
        step
        for step in result.trace.steps
        if step.event == "RECHECK_STARTED" and step.iteration == 1
    )
    repeated_state = next(
        step
        for step in result.trace.steps
        if step.event in {"TIER_CHECKED", "SOLVER_RETURNED_NO_TAB"}
        and step.iteration == 1
    )

    assert invalid.data["reason_code"] == "INVALID_EDIT_SCHEMA"
    assert invalid.data["state_changed"] is False
    assert invalid.data["before_target_sha256"] == invalid.data["after_target_sha256"]
    assert recheck.data["trigger"] == "MODEL_EDIT_INVALID"
    assert (
        recheck.data["target_checkpoint"]["sha256"]
        == repeated_state.data["target_sha256"]
        == invalid.data["before_target_sha256"]
    )
    assert not any(
        step.event in {"EDIT_APPLIED", "REPAIR_EDIT_PROPOSED"}
        and step.iteration == 1
        for step in result.trace.steps
    )
    assert result.tier_result is not None and result.tier_result.meets
    assert result.target == (
        Note(F(0), F(1), 40, "bass"),
        Note(F(0), F(1), 64, "melody"),
    )
    assert result.iterations == 2


def test_simplifier_validates_target_before_sorting_or_llm() -> None:
    llm = FakeLLM([])

    with pytest.raises(SolverInputError) as caught:
        simplify_to_tier(
            cast(tuple[Note, ...], (cast(Any, object()),)),
            BEGINNER,
            STANDARD_TUNING,
            0,
            llm,
        )

    assert llm.calls == []
    assert OracleInputCode.NOTE_TYPE in {
        diagnostic.code for diagnostic in caught.value.diagnostics
    }


@pytest.mark.parametrize("max_iters", [-1, True, 1.5, 65])
def test_simplifier_rejects_unbounded_iteration_controls_before_llm(
    max_iters: object,
) -> None:
    llm = FakeLLM([])
    with pytest.raises(SolverInputError) as caught:
        simplify_to_tier(
            _TARGET,
            BEGINNER,
            STANDARD_TUNING,
            0,
            llm,
            max_iters=max_iters,  # type: ignore[arg-type]
        )
    assert llm.calls == []
    assert {d.code for d in caught.value.diagnostics} == {
        OracleInputCode.REPAIR_ITERATIONS
    }


@pytest.mark.integration
def test_real_llm_simplifies_to_beginner() -> None:
    import os

    if not os.environ.get("ANTHROPIC_BASE_URL"):
        pytest.skip("no local LLM proxy configured")
    from fretsure.llm.client import ProxyLLM

    r = simplify_to_tier(_TARGET, BEGINNER, STANDARD_TUNING, 0, ProxyLLM(), max_iters=5)
    assert r.tier_result is not None and r.tier_result.meets
    assert 64 in [n.pitch for n in r.target]  # melody preserved
