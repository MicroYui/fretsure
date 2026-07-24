import hashlib
import inspect
import json
import sys
from fractions import Fraction as F

import pytest

import fretsure.agent.trace as trace_module
from fretsure.agent.trace import (
    TRACE_SCHEMA_VERSION,
    Trace,
    TraceInputError,
    TraceStep,
    diagnostics_payload,
    oracle_trace_payload,
    tab_checkpoint,
    target_checkpoint,
)
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import Note
from fretsure.oracle.core import CHECKER_VERSION, OracleResult
from fretsure.oracle.diagnostics import Diagnostic
from fretsure.oracle.input import ORACLE_INPUT_SCHEMA_VERSION
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.tab import Tab, TabNote


def test_add_accumulates_in_order() -> None:
    t = Trace()
    t.add("PLAN", "analyze structure", bars=4)
    t.add("EDIT", "drop 5th", onset=F(1, 2), pitch=55)
    assert [s.kind for s in t.steps] == ["PLAN", "EDIT"]
    assert isinstance(t.steps[0], TraceStep)
    assert t.steps[1].data["pitch"] == 55


def test_to_jsonl_one_parseable_line_per_step() -> None:
    t = Trace()
    t.add("ORACLE", "AMBER", verdict="AMBER")
    t.add("EDIT", "octave down", onset=F(1, 2))  # Fraction must serialize
    lines = t.to_jsonl().split("\n")
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["kind"] == "ORACLE"
    assert parsed[1]["data"]["onset"] == "1/2"  # Fraction -> string


def test_wire_contract_is_versioned_sequenced_and_matches_jsonl() -> None:
    trace = Trace()
    trace.add("PLAN", "configured")
    trace.add(
        "EDIT",
        "The targeted edit was applied to the repair state.",
        event="EDIT_APPLIED",
        candidate_index=2,
        iteration=1,
        edit={
            "op": "drop_note",
            "target_onset": "0/1",
            "target_pitch": 60,
            "arg": 0,
        },
        status="applied",
        reason_code=None,
        before_target_sha256="a" * 64,
        after_target_sha256="b" * 64,
        state_changed=True,
    )

    wire = trace.to_wire()
    rows = wire["steps"]
    assert wire["schema_version"] == TRACE_SCHEMA_VERSION
    assert isinstance(rows, list)
    assert [row["seq"] for row in rows] == [0, 1]
    assert set(rows[0]) == {
        "trace_schema_version",
        "seq",
        "kind",
        "event",
        "candidate_index",
        "iteration",
        "detail",
        "data",
    }
    assert rows[1]["event"] == "EDIT_APPLIED"
    assert rows[1]["candidate_index"] == 2
    assert rows == [json.loads(line) for line in trace.to_jsonl().splitlines()]


def test_empty_wire_retains_schema_version() -> None:
    assert Trace().to_wire() == {
        "schema_version": TRACE_SCHEMA_VERSION,
        "steps": [],
    }


def test_add_takes_a_detached_snapshot() -> None:
    source = {"nested": [1, {"value": "before"}]}
    trace = Trace()
    trace.add("PLAN", "snapshot", source=source)

    source["nested"][1]["value"] = "after"  # type: ignore[index]
    source["nested"].append(2)  # type: ignore[union-attr]

    assert trace.steps[0].data["source"] == {"nested": [1, {"value": "before"}]}


def test_product_events_reject_missing_and_unknown_payload_fields() -> None:
    trace = Trace()
    with pytest.raises(TraceInputError, match="missing fields"):
        trace.add(
            "PROPOSE",
            "bad",
            event="CANDIDATE_PROPOSED",
            temperature=0.0,
        )
    with pytest.raises(TraceInputError, match="unknown fields"):
        trace.add(
            "PROPOSE",
            "bad",
            event="CANDIDATE_PROPOSED",
            temperature=0.0,
            target_checkpoint={},
            prompt="must never be accepted",
        )


def _product_event(event: str, **changes: object) -> Trace:
    target = target_checkpoint((Note(F(0), F(1), 60, "melody"),))
    tab = tab_checkpoint(
        Tab(
            (TabNote(F(0), F(1), 1, 3, 1, "i"),),
            STANDARD_TUNING,
            0,
        )
    )
    empty_diagnostics_sha = hashlib.sha256(b"[]").hexdigest()
    edit = {
        "op": "drop_note",
        "target_onset": "0/1",
        "target_pitch": 60,
        "arg": 0,
    }
    rows: dict[str, tuple[str, str, int | None, int | None, dict[str, object]]] = {
        "PIPELINE_CONFIGURED": (
            "PLAN",
            "pipeline configured from source metadata and explicit options",
            None,
            None,
            {
                "llm_model_id": "constant-stub",
                "source_tempo_bpm": 90.0,
                "effective_tempo_bpm": 90.0,
                "time_signature": "4/4",
                "tuning": list(STANDARD_TUNING),
                "capo": 0,
                "profile": MEDIAN_HAND.version,
                "checker_version": CHECKER_VERSION,
                "profile_version": MEDIAN_HAND.version,
                "profile_fingerprint": MEDIAN_HAND.fingerprint,
                "input_schema_version": ORACLE_INPUT_SCHEMA_VERSION,
                "fidelity_checker_version": "fidelity@0.3.0",
                "candidates": 1,
                "max_repair_iterations": 1,
                "critic_enabled": False,
            },
        ),
        "CANDIDATE_PROPOSED": (
            "PROPOSE",
            "Candidate 0 produced a bounded target-note checkpoint.",
            0,
            None,
            {"temperature": 0.0, "target_checkpoint": target},
        ),
        "CANDIDATE_FINISHED": (
            "SOLVE",
            "Candidate 0 finished with GREEN.",
            0,
            0,
            {"verdict": "GREEN", "tab_available": True, "repair_iterations": 0},
        ),
        "SOLVER_RETURNED_TAB": (
            "SOLVE",
            "Solver returned a tablature candidate.",
            0,
            0,
            {"status": "TAB", "target_sha256": "a" * 64, "target_note_count": 1},
        ),
        "SOLVER_RETURNED_NO_TAB": (
            "SOLVE",
            "The bounded fingering search returned no candidate (EMPTY_TARGET) at "
            "an unspecified onset.",
            None,
            0,
            {
                "status": "NO_TAB",
                "target_sha256": "a" * 64,
                "target_note_count": 0,
                "infeasible": {
                    "code": "EMPTY_TARGET",
                    "onset": None,
                    "pitches": [],
                    "bounded_search": True,
                },
                "terminal_reason": None,
            },
        ),
        "PLAYABILITY_CHECKED": (
            "ORACLE",
            "Oracle returned GREEN with 0 diagnostics.",
            0,
            0,
            {
                "diagnostics": [],
                "diagnostic_count": 0,
                "diagnostics_complete": True,
                "diagnostics_sha256": empty_diagnostics_sha,
                "verdict": "GREEN",
                "tab_checkpoint": tab,
                "checker_version": CHECKER_VERSION,
                "profile_version": MEDIAN_HAND.version,
                "profile_fingerprint": MEDIAN_HAND.fingerprint,
                "input_schema_version": ORACLE_INPUT_SCHEMA_VERSION,
                "terminal_reason": "GREEN",
            },
        ),
        "TIER_CHECKED": (
            "ORACLE",
            "The deterministic tier checker returned meets=True for beginner.",
            None,
            0,
            {
                "tier": "beginner",
                "meets": True,
                "tier_violation_count": 0,
                "target_sha256": "a" * 64,
                "tab_checkpoint": tab,
                "terminal_reason": "TIER_MET",
            },
        ),
        "REPAIR_EDIT_PROPOSED": (
            "REASON",
            "Thin one non-melody voice at onset 0/1 and recheck pitch 60.",
            0,
            1,
            {"edit": edit, "based_on_diagnostic_codes": ["FRET_SPAN"]},
        ),
        "MODEL_CALL_FAILED": (
            "REASON",
            "The model call failed; repair stopped without exposing transport details.",
            0,
            1,
            {"reason_code": "LLM_TRANSPORT_FAILURE", "target_sha256": "a" * 64},
        ),
        "EDIT_APPLIED": (
            "EDIT",
            "The targeted edit was applied to the repair state.",
            0,
            1,
            {
                "edit": edit,
                "status": "applied",
                "reason_code": None,
                "before_target_sha256": "a" * 64,
                "after_target_sha256": "b" * 64,
                "state_changed": True,
            },
        ),
        "EDIT_REJECTED": (
            "EDIT",
            "The edit matched no target note and changed no state.",
            0,
            1,
            {
                "edit": edit,
                "status": "noop",
                "reason_code": "TARGET_NOT_FOUND",
                "before_target_sha256": "a" * 64,
                "after_target_sha256": "a" * 64,
                "state_changed": False,
            },
        ),
        "MODEL_EDIT_INVALID": (
            "EDIT",
            "The model JSON did not satisfy the edit schema.",
            0,
            1,
            {
                "edit": None,
                "status": "unparseable",
                "reason_code": "INVALID_EDIT_SCHEMA",
                "before_target_sha256": "a" * 64,
                "after_target_sha256": "a" * 64,
                "state_changed": False,
            },
        ),
        "RECHECK_STARTED": (
            "RECHECK",
            "Run the bounded solver and oracle again for the post-edit target.",
            0,
            1,
            {"trigger": "EDIT_APPLIED", "target_checkpoint": target},
        ),
        "CANDIDATE_SELECTED": (
            "SELECT",
            "Selected candidate 0; playability and fidelity remain separate gates.",
            0,
            None,
            {
                "winner_candidate_index": 0,
                "candidates_considered": 1,
                "verdict": "GREEN",
                "green_certified": True,
                "playability_gate": "passed",
                "faithfulness_passed": True,
                "ranking_melody_recall": 1.0,
                "ranking_bass_preserved": 1.0,
                "ranking_harmony_jaccard": 1.0,
                "melody_f1": 1.0,
                "bass_root_accuracy": 1.0,
                "harmony_jaccard": 1.0,
                "evaluated_dimensions": ["melody", "bass_root", "harmony"],
                "unavailable_dimensions": [],
                "critic_status": "NOT_RUN",
                "critic_overall": None,
            },
        ),
        "NO_CANDIDATE_SELECTED": (
            "SELECT",
            "No candidate returned a tablature result within the bounded search.",
            None,
            None,
            {
                "winner_candidate_index": None,
                "candidates_considered": 0,
                "playability_gate": None,
                "faithfulness_passed": None,
            },
        ),
    }
    kind, detail, candidate_index, iteration, data = rows[event]
    data.update(changes)
    trace = Trace()
    trace.add(
        kind,  # type: ignore[arg-type]
        detail,
        event=event,  # type: ignore[arg-type]
        candidate_index=candidate_index,
        iteration=iteration,
        **data,
    )
    return trace


def test_every_product_event_has_one_frozen_valid_example() -> None:
    for event in trace_module.PRODUCT_TRACE_EVENTS:
        wire = _product_event(event).to_wire()
        assert wire["steps"][0]["event"] == event


def test_repair_edit_proposal_accepts_no_median_profile_diagnostics() -> None:
    wire = _product_event(
        "REPAIR_EDIT_PROPOSED",
        based_on_diagnostic_codes=[],
    ).to_wire()

    assert wire["steps"][0]["data"]["based_on_diagnostic_codes"] == []


def test_candidate_selection_accepts_canonical_unavailable_fidelity_dimensions() -> None:
    wire = _product_event(
        "CANDIDATE_SELECTED",
        bass_root_accuracy=None,
        harmony_jaccard=None,
        evaluated_dimensions=["melody"],
        unavailable_dimensions=["bass_root", "harmony"],
    ).to_wire()

    data = wire["steps"][0]["data"]
    assert data["melody_f1"] == 1.0
    assert data["bass_root_accuracy"] is None
    assert data["harmony_jaccard"] is None
    assert data["faithfulness_passed"] is True


def test_candidate_selection_accepts_index_free_deterministic_baseline() -> None:
    candidate = _product_event("CANDIDATE_SELECTED").steps[0]
    data = dict(candidate.data)
    data["winner_candidate_index"] = None
    trace = Trace()
    trace.add(
        "SELECT",
        "Selected the deterministic baseline after the model candidates returned no tablature.",
        event="CANDIDATE_SELECTED",
        candidate_index=None,
        **data,
    )

    step = trace.to_wire()["steps"][0]
    assert step["candidate_index"] is None
    assert step["data"]["winner_candidate_index"] is None


@pytest.mark.parametrize(
    ("event", "changes"),
    [
        ("PIPELINE_CONFIGURED", {"source_tempo_bpm": True}),
        ("CANDIDATE_PROPOSED", {"temperature": 0}),
        ("CANDIDATE_FINISHED", {"verdict": {"raw_prompt": "secret"}}),
        ("SOLVER_RETURNED_TAB", {"target_note_count": True}),
        (
            "SOLVER_RETURNED_NO_TAB",
            {
                "infeasible": {
                    "code": "EMPTY_TARGET",
                    "onset": None,
                    "pitches": [],
                    "bounded_search": False,
                }
            },
        ),
        ("PLAYABILITY_CHECKED", {"profile_fingerprint": "0" * 63}),
        ("TIER_CHECKED", {"meets": "true"}),
        ("REPAIR_EDIT_PROPOSED", {"based_on_diagnostic_codes": "FRET_SPAN"}),
        ("MODEL_CALL_FAILED", {"reason_code": "MODEL_CALL_FAILED"}),
        ("EDIT_APPLIED", {"status": "APPLIED"}),
        ("EDIT_REJECTED", {"status": "rejected"}),
        ("MODEL_EDIT_INVALID", {"status": "rejected"}),
        ("RECHECK_STARTED", {"trigger": "RAW_EXCEPTION"}),
        ("CANDIDATE_SELECTED", {"ranking_melody_recall": 99.0}),
        (
            "CANDIDATE_SELECTED",
            {"bass_root_accuracy": None},
        ),
        (
            "CANDIDATE_SELECTED",
            {
                "evaluated_dimensions": ["melody", "melody"],
                "unavailable_dimensions": ["bass_root", "harmony"],
            },
        ),
        ("NO_CANDIDATE_SELECTED", {"playability_gate": "not_passed"}),
    ],
)
def test_product_event_semantic_forgery_is_rejected(event: str, changes: dict[str, object]) -> None:
    with pytest.raises(TraceInputError):
        _product_event(event, **changes)


@pytest.mark.parametrize("field", ["sha256", "state_bytes", "note_count", "state"])
def test_complete_checkpoint_integrity_is_recomputed(field: str) -> None:
    checkpoint = target_checkpoint((Note(F(0), F(1), 60, "melody"),))
    if field == "sha256":
        checkpoint[field] = "0" * 64
    elif field == "state":
        checkpoint[field] = {"notes": []}
    else:
        checkpoint[field] = 0

    with pytest.raises(TraceInputError, match=field):
        _product_event("CANDIDATE_PROPOSED", target_checkpoint=checkpoint)


@pytest.mark.parametrize(
    ("detail", "data"),
    [
        ("Bearer server-secret", {}),
        ("API_KEY=/private/token", {}),
        ("safe", {"prompt": "raw model request"}),
        ("safe", {"prompt_text": "full user instructions"}),
        ("safe", {"api_key_value": "redacted"}),
        ("safe", {"exception_message": "transport failed"}),
        ("safe", {"modelPromptText": "full model instructions"}),
        ("safe", {"nested": {"raw_reply": "model output"}}),
        ("RuntimeError: transport failed", {}),
    ],
)
def test_public_trace_rejects_sensitive_keys_and_content(
    detail: str, data: dict[str, object]
) -> None:
    trace = Trace()
    with pytest.raises(TraceInputError, match="sensitive|exception"):
        trace.add("PLAN", detail, **data)


def test_sensitive_mutation_after_add_is_rejected_at_public_serialization() -> None:
    trace = Trace()
    trace.add("PLAN", "safe", evidence={"status": "bounded"})
    trace.steps[0].data["raw_response"] = "Bearer mutated-secret"

    with pytest.raises(TraceInputError, match="sensitive"):
        trace.to_wire()


def test_aggregate_checkpoint_budget_omits_whole_later_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoint = target_checkpoint((Note(F(0), F(1), 60, "melody"),))
    state_bytes = checkpoint["state_bytes"]
    assert isinstance(state_bytes, int)
    monkeypatch.setattr(trace_module, "MAX_TRACE_EMBEDDED_STATE_BYTES", state_bytes)
    trace = Trace()
    trace.add("PLAN", "first", checkpoint=checkpoint)
    trace.add("PLAN", "second", checkpoint=checkpoint)

    rows = trace.to_wire()["steps"]
    assert isinstance(rows, list)
    first = rows[0]["data"]["checkpoint"]
    second = rows[1]["data"]["checkpoint"]
    assert first["complete"] is True and first["state"] is not None
    assert second["complete"] is False and second["state"] is None
    assert second["omission"] == {
        "code": "TRACE_BUDGET",
        "limit_bytes": state_bytes,
    }


def test_structured_diagnostics_are_bounded_and_digest_the_complete_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = OracleResult(
        "AMBER",
        (
            Diagnostic(1, F(1), "FRET_SPAN", (0, 1), 12.5, ("drop_5th",)),
            Diagnostic(2, F(3, 2), "SHIFT_SPEED", (2,), 4.0, ("reposition",)),
        ),
        CHECKER_VERSION,
        MEDIAN_HAND.version,
        MEDIAN_HAND.fingerprint,
        ORACLE_INPUT_SCHEMA_VERSION,
    )
    monkeypatch.setattr(trace_module, "MAX_TRACE_DIAGNOSTICS_PER_STEP", 1)

    payload = diagnostics_payload(result)

    assert payload["diagnostic_count"] == 2
    assert payload["diagnostics_complete"] is False
    assert len(payload["diagnostics"]) == 1
    first = payload["diagnostics"][0]
    assert first["code"] == "FRET_SPAN"
    assert first["beat"] == "1/1"
    assert first["offending_note_indices"] == [0, 1]
    assert "checker-defined units" in first["message"]
    assert len(payload["diagnostics_sha256"]) == 64


def test_amber_trace_may_have_no_median_profile_diagnostics() -> None:
    tab = Tab(
        (TabNote(F(0), F(1), 0, 0, 0, "p"),),
        STANDARD_TUNING,
        0,
    )
    result = OracleResult(
        "AMBER",
        (),
        CHECKER_VERSION,
        MEDIAN_HAND.version,
        MEDIAN_HAND.fingerprint,
        ORACLE_INPUT_SCHEMA_VERSION,
    )
    trace = Trace()

    trace.add(
        "ORACLE",
        "Oracle returned AMBER with 0 diagnostics.",
        event="PLAYABILITY_CHECKED",
        candidate_index=4,
        iteration=0,
        **oracle_trace_payload(result, tab, terminal_reason=None),
    )

    data = trace.to_wire()["steps"][0]["data"]
    assert data["verdict"] == "AMBER"
    assert data["diagnostic_count"] == 0


def test_empty_trace_jsonl_is_empty() -> None:
    assert Trace().to_jsonl() == ""


def test_deterministic() -> None:
    a = Trace()
    b = Trace()
    for t in (a, b):
        t.add("SOLVE", "ok", n=3)
    assert a.to_jsonl() == b.to_jsonl()


def test_jsonl_is_canonical_across_mapping_insertion_order() -> None:
    a = Trace()
    b = Trace()
    a.add("PLAN", "ordered", z=1, a=2)
    b.add("PLAN", "ordered", a=2, z=1)

    assert a.to_jsonl() == b.to_jsonl()
    assert "NaN" not in a.to_jsonl()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_numbers_fail_closed(value: float) -> None:
    trace = Trace()
    with pytest.raises(TraceInputError, match="finite") as caught:
        trace.add("PLAN", "invalid", value=value)

    assert caught.value.path.endswith(".value[0]")


def test_nesting_and_node_limits_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    trace = Trace()
    trace.add("PLAN", "", value=[[["too deep"]]])
    monkeypatch.setattr(trace_module, "MAX_TRACE_JSON_DEPTH", 2)

    with pytest.raises(TraceInputError, match="nesting"):
        trace.to_jsonl()

    monkeypatch.setattr(trace_module, "MAX_TRACE_JSON_DEPTH", 64)
    monkeypatch.setattr(trace_module, "MAX_TRACE_JSON_NODES", 4)
    with pytest.raises(TraceInputError, match="value count"):
        trace.to_jsonl()


def test_scalar_and_output_size_limits_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    trace = Trace()
    trace.add("PLAN", "", value="x" * 17)
    monkeypatch.setattr(trace_module, "MAX_TRACE_SCALAR_BYTES", 16)

    with pytest.raises(TraceInputError, match="byte limit"):
        trace.to_jsonl()

    monkeypatch.setattr(trace_module, "MAX_TRACE_SCALAR_BYTES", 1024)
    monkeypatch.setattr(trace_module, "MAX_TRACE_JSONL_BYTES", 16)
    with pytest.raises(TraceInputError, match="JSONL output"):
        trace.to_jsonl()


def test_escaped_output_budget_is_proved_before_json_encoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = Trace()
    trace.add("PLAN", "\0" * 10)
    monkeypatch.setattr(trace_module, "MAX_TRACE_JSONL_BYTES", 20)

    def forbidden_encoder(*args: object, **kwargs: object) -> str:
        raise AssertionError("over-budget trace reached json.dumps")

    monkeypatch.setattr(trace_module.json, "dumps", forbidden_encoder)
    with pytest.raises(TraceInputError, match="JSONL output"):
        trace.to_jsonl()


_HOSTILE_HOOK_CALLS: list[str] = []


class _HostileMeta(type):
    def __getattribute__(cls, name: str) -> object:
        if name == "__name__":
            _HOSTILE_HOOK_CALLS.append("metaclass name")
            raise AssertionError("type-name hook must not run")
        return super().__getattribute__(name)


class _HostileObject(metaclass=_HostileMeta):
    def __repr__(self) -> str:
        _HOSTILE_HOOK_CALLS.append("repr")
        raise AssertionError("repr must not run")

    def __str__(self) -> str:
        _HOSTILE_HOOK_CALLS.append("str")
        raise AssertionError("str must not run")


def test_arbitrary_objects_fail_closed_without_executing_hooks() -> None:
    _HOSTILE_HOOK_CALLS.clear()
    trace = Trace()
    with pytest.raises(TraceInputError) as caught:
        trace.add("PLAN", "invalid", value=_HostileObject())

    assert caught.value.path.endswith(".value[0]")
    assert _HOSTILE_HOOK_CALLS == []


def test_cyclic_containers_fail_closed() -> None:
    value: list[object] = []
    value.append(value)
    trace = Trace()
    with pytest.raises(TraceInputError, match="cyclic"):
        trace.add("PLAN", "invalid", value=value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("_numerator", object()),
        ("_denominator", object()),
        ("_denominator", 0),
        ("_denominator", -2),
        ("_numerator", 2),
    ],
)
def test_low_level_corrupted_fraction_is_typed_invalid(
    field: str,
    value: object,
) -> None:
    fraction = F(1, 2)
    object.__setattr__(fraction, field, value)
    trace = Trace()
    with pytest.raises(TraceInputError, match="Fraction"):
        trace.add("PLAN", "invalid", value=fraction)


def test_fraction_rendering_obeys_runtime_integer_string_limit() -> None:
    fraction = F(1 << 3_000, 1)
    trace = Trace()
    previous = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(640)
    try:
        with pytest.raises(TraceInputError, match="runtime integer limit"):
            trace.add("PLAN", "large fraction", value=fraction)
    finally:
        sys.set_int_max_str_digits(previous)


@pytest.mark.parametrize("field", ["_numerator", "_denominator"])
def test_fraction_with_deleted_component_is_typed_invalid(field: str) -> None:
    fraction = F(1, 2)
    object.__delattr__(fraction, field)
    trace = Trace()
    with pytest.raises(TraceInputError, match="components are missing"):
        trace.add("PLAN", "invalid", value=fraction)


def _source_line(function: object, fragment: str) -> int:
    source, start_line = inspect.getsourcelines(function)
    return start_line + next(index for index, line in enumerate(source) if fragment in line)


def test_step_snapshot_is_bounded_if_list_grows_after_length_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trace = Trace()
    trace.add("PLAN", "one")
    snapshot_line = _source_line(Trace.to_jsonl, "steps = tuple")
    mutated = False

    def grow(frame: object, event: str, arg: object) -> object:
        del arg
        nonlocal mutated
        if (
            not mutated
            and event == "line"
            and getattr(frame, "f_code", None) is Trace.to_jsonl.__code__
            and getattr(frame, "f_lineno", None) == snapshot_line
        ):
            trace.steps.extend([trace.steps[0]] * 10_000)
            mutated = True
        return grow

    monkeypatch.setattr(trace_module, "MAX_TRACE_STEPS", 2)
    sys.settrace(grow)
    try:
        with pytest.raises(TraceInputError, match="step count"):
            trace.to_jsonl()
    finally:
        sys.settrace(None)
    assert mutated


@pytest.mark.parametrize(
    ("value", "fragment"),
    [([], "sequence_items = tuple"), ({}, "dict_items: tuple")],
)
def test_nested_container_is_detached_before_source_grows_during_serialization(
    value: object,
    fragment: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if type(value) is list:
        value.append(1)  # type: ignore[union-attr]
    else:
        value["one"] = 1  # type: ignore[index]
    trace = Trace()
    trace.add("PLAN", "one", value=value)
    snapshot_line = _source_line(trace_module._normalize_json_value, fragment)
    mutated = False

    def grow(frame: object, event: str, arg: object) -> object:
        del arg
        nonlocal mutated
        if (
            not mutated
            and event == "line"
            and getattr(frame, "f_code", None) is trace_module._normalize_json_value.__code__
            and getattr(frame, "f_lineno", None) == snapshot_line
        ):
            if type(value) is list:
                value.extend([1] * 10_000)
            else:
                value.update({str(index): index for index in range(10_000)})
            mutated = True
        return grow

    sys.settrace(grow)
    try:
        encoded = trace.to_jsonl()
    finally:
        sys.settrace(None)
    assert mutated
    parsed = json.loads(encoded)
    assert parsed["data"]["value"] == ([1] if type(value) is list else {"one": 1})
