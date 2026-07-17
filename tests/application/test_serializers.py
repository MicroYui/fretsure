from __future__ import annotations

import json
from pathlib import Path

import pytest

import fretsure
from fretsure.application import (
    ApplicationCode,
    ApplicationDiagnostic,
    ApplicationError,
    ArrangeOptions,
    CheckOptions,
    RenderOptions,
    SolveOptions,
    application_error_to_wire,
    arrange_outcome_to_wire,
    arrange_score_bytes,
    capabilities,
    capabilities_to_wire,
    check_outcome_to_wire,
    check_tab_json,
    render_outcome_to_wire,
    render_tab_json,
    solve_outcome_to_wire,
    solve_target_json,
)
from fretsure.importers import (
    DiagnosticSeverity,
    ImportCode,
    ImportDiagnostic,
    ImportProvenance,
    SourceLocation,
)
from fretsure.llm.client import ConstantLLM
from fretsure.metrics.fidelity import FaithfulnessGate
from fretsure.solver.api import Infeasible
from fretsure.tab import tab_to_json

_BASIC = Path("tests/fixtures/musicxml/supported_basic.musicxml")
_MIDI = Path("tests/fixtures/midi/producers/music21-10.5.0-melody_only.mid")


@pytest.fixture(scope="module")
def arranged_wire() -> dict[str, object]:
    outcome = arrange_score_bytes(
        _BASIC.read_bytes(),
        filename=_BASIC.name,
        options=ArrangeOptions(n=1, max_iters=0, use_critic=False),
        llm=ConstantLLM("noop"),
    )
    return arrange_outcome_to_wire(outcome)


def test_arrangement_wire_has_the_frozen_top_level_shape(
    arranged_wire: dict[str, object],
) -> None:
    assert set(arranged_wire) == {
        "service_version",
        "status",
        "source",
        "score",
        "options",
        "model",
        "tab",
        "ascii",
        "playability",
        "faithfulness",
        "trace",
        "stamps",
    }
    assert arranged_wire["status"] == "tab_produced"
    assert arranged_wire["tab"] is not None
    assert arranged_wire["ascii"] is not None
    assert arranged_wire["playability"] is not None
    assert arranged_wire["faithfulness"] is not None


def test_faithfulness_wire_freezes_availability_and_nullable_scores(
    arranged_wire: dict[str, object],
) -> None:
    wire = arranged_wire["faithfulness"]
    assert isinstance(wire, dict)
    assert wire == {
        "melody_f1": 1.0,
        "bass_root_accuracy": 1.0,
        "harmony_jaccard": wire["harmony_jaccard"],
        "evaluated_dimensions": ["melody", "bass_root", "harmony"],
        "unavailable_dimensions": [],
        "passed": False,
        "checker_version": "fidelity@0.3.0",
    }


def test_trace_binding_rejects_a_gate_mutated_without_its_selection_row() -> None:
    outcome = arrange_score_bytes(
        _BASIC.read_bytes(),
        filename=_BASIC.name,
        options=ArrangeOptions(n=1, max_iters=0, use_critic=False),
        llm=ConstantLLM("noop"),
    )
    object.__setattr__(
        outcome,
        "faithfulness",
        FaithfulnessGate(
            melody_f1=1.0,
            bass_root=None,
            harmony=None,
            passed=True,
            evaluated_dimensions=("melody",),
            unavailable_dimensions=("bass_root", "harmony"),
        ),
    )

    with pytest.raises(ApplicationError) as caught:
        arrange_outcome_to_wire(outcome)

    assert caught.value.code is ApplicationCode.SERIALIZATION_FAILED
    assert caught.value.path == "trace.faithfulness"


@pytest.mark.parametrize("mutation", ["missing-nullable", "legacy-extra"])
def test_trace_binding_requires_the_exact_selected_data_contract(mutation: str) -> None:
    outcome = arrange_score_bytes(
        _MIDI.read_bytes(),
        filename=_MIDI.name,
        options=ArrangeOptions(n=1, max_iters=0, use_critic=False),
        llm=ConstantLLM("noop"),
    )
    document = json.loads(outcome.trace_document_json)
    selected = next(
        step for step in document["steps"] if step["event"] == "CANDIDATE_SELECTED"
    )
    if mutation == "missing-nullable":
        selected["data"].pop("bass_root_accuracy")
    else:
        selected["data"]["melody_recall"] = 1.0
    object.__setattr__(
        outcome,
        "trace_document_json",
        json.dumps(document, separators=(",", ":"), sort_keys=True),
    )

    with pytest.raises(ApplicationError) as caught:
        arrange_outcome_to_wire(outcome)

    assert caught.value.code is ApplicationCode.SERIALIZATION_FAILED
    assert caught.value.path == "trace.faithfulness"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("harmony", None),
        ("unavailable_dimensions", ("harmony",)),
        ("passed", True),
    ],
)
def test_faithfulness_wire_revalidates_mutated_gate_fail_closed(
    field: str,
    value: object,
) -> None:
    outcome = arrange_score_bytes(
        _BASIC.read_bytes(),
        filename=_BASIC.name,
        options=ArrangeOptions(n=1, max_iters=0, use_critic=False),
        llm=ConstantLLM("noop"),
    )
    assert outcome.faithfulness is not None
    object.__setattr__(outcome.faithfulness, field, value)

    with pytest.raises(ApplicationError) as caught:
        arrange_outcome_to_wire(outcome)

    assert caught.value.code is ApplicationCode.SERIALIZATION_FAILED
    assert caught.value.path == "faithfulness"


def test_arrangement_wire_stamps_actual_model_and_all_public_contracts(
    arranged_wire: dict[str, object],
) -> None:
    stamps = arranged_wire["stamps"]
    assert isinstance(stamps, dict)
    assert stamps["service_version"] == "fretsure-service@0.2.0"
    assert stamps["score_input_version"] == "score-input@0.1.0"
    assert stamps["model_id"] == "constant-stub"
    assert stamps["oracle_checker_version"] == "oracle@0.2.0"
    assert stamps["oracle_input_schema_version"] == "tab-input@0.2.0"
    assert stamps["fidelity_checker_version"] == "fidelity@0.3.0"
    assert stamps["target_input_schema_version"] == "target-input@0.1.0"
    assert stamps["trace_schema_version"] == "agent-trace@0.2.0"
    assert stamps["package_version"] == fretsure.__version__ == "0.5.0"
    assert stamps["importer_version"] == "musicxml@0.3.0"
    assert len(str(stamps["profile_fingerprint"])) == 64


def test_arrangement_trace_uses_public_versioned_rows_not_jsonl_reparse(
    arranged_wire: dict[str, object],
) -> None:
    trace = arranged_wire["trace"]
    assert isinstance(trace, dict)
    assert trace["schema_version"] == "agent-trace@0.2.0"
    steps = trace["steps"]
    assert isinstance(steps, list) and steps
    assert list(steps[0]) == sorted(steps[0])
    assert set(steps[0]) == {
        "trace_schema_version",
        "seq",
        "kind",
        "event",
        "candidate_index",
        "iteration",
        "detail",
        "data",
    }


def test_wire_serialization_is_deterministic(
    arranged_wire: dict[str, object],
) -> None:
    encoded_a = json.dumps(arranged_wire, sort_keys=True, separators=(",", ":"))
    outcome = arrange_score_bytes(
        _BASIC.read_bytes(),
        filename=_BASIC.name,
        options=ArrangeOptions(n=1, max_iters=0, use_critic=False),
        llm=ConstantLLM("noop"),
    )
    encoded_b = json.dumps(
        arrange_outcome_to_wire(outcome),
        sort_keys=True,
        separators=(",", ":"),
    )
    assert encoded_a == encoded_b


def test_check_solve_and_render_wires_share_canonical_tab() -> None:
    solve = solve_target_json(
        '{"notes":[{"onset":"0/1","duration":"1/1",'
        '"pitch":60,"voice":"melody"}]}',
        options=SolveOptions(),
    )
    solve_wire = solve_outcome_to_wire(solve)
    assert solve_wire["status"] == "found"
    assert solve_wire["search_complete"] is False
    assert solve_wire["max_solutions"] == 1
    assert solve_wire["infeasible"] is None
    assert solve.tab is not None

    check = check_tab_json(tab_to_json(solve.tab), options=CheckOptions())
    check_wire = check_outcome_to_wire(check)
    assert check_wire["status"] == "checked"
    assert check_wire["tab"] == solve_wire["tab"]
    assert check_wire["playability"] == solve_wire["playability"]
    for wire in (solve_wire, check_wire):
        assert "importer_version" not in wire["stamps"]  # type: ignore[operator]
        assert "score_input_version" not in wire["stamps"]  # type: ignore[operator]

    render = render_tab_json(tab_to_json(solve.tab), options=RenderOptions())
    render_wire = render_outcome_to_wire(render)
    assert render_wire["status"] == "rendered"
    assert render_wire["tab"] == solve_wire["tab"]
    assert render_wire["format"] == "ascii"
    assert render_wire["content"] == solve_wire["ascii"]
    assert "importer_version" not in render_wire["stamps"]  # type: ignore[operator]
    assert "score_input_version" not in render_wire["stamps"]  # type: ignore[operator]


def test_not_found_wire_never_exposes_free_form_solver_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outcome = solve_target_json('{"notes":[]}', options=SolveOptions())
    assert isinstance(outcome.infeasible, Infeasible)
    object.__setattr__(outcome.infeasible, "reason", "secret internal solver text")
    wire = solve_outcome_to_wire(outcome)
    assert wire["status"] == "not_found_within_budget"
    assert wire["search_complete"] is False
    assert "secret" not in json.dumps(wire)
    infeasible = wire["infeasible"]
    assert isinstance(infeasible, dict)
    assert infeasible["claim"] == "bounded_search_result_not_an_unsatisfiability_proof"


def test_capabilities_wire_is_transport_neutral_and_honest() -> None:
    wire = capabilities_to_wire(capabilities())
    assert wire["service_version"] == "fretsure-service@0.2.0"
    assert wire["profiles"] == [
        {
            "name": "median",
            "version": "median@0.1",
            "fingerprint": wire["profiles"][0]["fingerprint"],  # type: ignore[index]
            "calibration_status": "placeholder_pending_human_calibration",
        }
    ]
    inputs = wire["inputs"]
    assert isinstance(inputs, dict)
    assert inputs["target_json"]["max_depth"] == 64
    assert inputs["target_json"]["max_nodes"] == 250_000
    assert inputs["score_suffixes"] == [
        ".musicxml",
        ".xml",
        ".mxl",
        ".mid",
        ".midi",
    ]
    assert inputs["score_input"] == {
        "router_version": "score-input@0.1.0",
        "format_importers": {
            "musicxml": "musicxml@0.3.0",
            "mxl": "musicxml@0.3.0",
            "midi": "midi@0.1.0",
        },
    }
    assert "render_audio" in wire["deferred"]  # type: ignore[operator]
    assert "render_audio" not in wire["implemented"]  # type: ignore[operator]
    assert wire["stamps"]["package_version"] == "0.5.0"  # type: ignore[index]
    assert wire["stamps"]["score_input_version"] == "score-input@0.1.0"  # type: ignore[index]
    assert "importer_version" not in wire["stamps"]  # type: ignore[operator]


def test_capabilities_serializer_rejects_registry_drift() -> None:
    value = capabilities()
    object.__setattr__(value, "score_format_registry", {"midi": "midi@stale"})

    with pytest.raises(ApplicationError) as caught:
        capabilities_to_wire(value)

    assert caught.value.code is ApplicationCode.SERIALIZATION_FAILED
    assert caught.value.path == "capabilities.score_input"


def test_arrangement_serializer_rejects_a_stale_importer_stamp() -> None:
    outcome = arrange_score_bytes(
        _BASIC.read_bytes(),
        filename=_BASIC.name,
        options=ArrangeOptions(n=1, max_iters=0, use_critic=False),
        llm=ConstantLLM("noop"),
    )
    object.__setattr__(outcome.imported, "importer_version", "musicxml@stale")
    with pytest.raises(ApplicationError) as caught:
        arrange_outcome_to_wire(outcome)
    assert caught.value.code is ApplicationCode.SERIALIZATION_FAILED
    assert caught.value.path == "source.importer_version"


def test_arrangement_serializer_accepts_only_the_importer_for_source_format() -> None:
    outcome = arrange_score_bytes(
        _BASIC.read_bytes(),
        filename=_BASIC.name,
        options=ArrangeOptions(n=1, max_iters=0, use_critic=False),
        llm=ConstantLLM("noop"),
    )
    provenance = outcome.imported.provenance
    assert provenance is not None
    object.__setattr__(
        outcome.imported,
        "provenance",
        ImportProvenance(
            provenance.source_filename,
            "midi",
            provenance.raw_sha256,
            None,
            provenance.raw_sha256,
            None,
        ),
    )
    object.__setattr__(outcome.imported, "importer_version", "midi@0.1.0")

    wire = arrange_outcome_to_wire(outcome)
    assert wire["source"]["format"] == "midi"  # type: ignore[index]
    assert wire["source"]["importer_version"] == "midi@0.1.0"  # type: ignore[index]
    assert wire["stamps"]["score_input_version"] == "score-input@0.1.0"  # type: ignore[index]
    assert wire["stamps"]["importer_version"] == "midi@0.1.0"  # type: ignore[index]

    object.__setattr__(outcome.imported, "importer_version", "musicxml@0.3.0")
    with pytest.raises(ApplicationError) as caught:
        arrange_outcome_to_wire(outcome)
    assert caught.value.path == "source.importer_version"


def test_source_location_wire_includes_midi_coordinates() -> None:
    outcome = arrange_score_bytes(
        _BASIC.read_bytes(),
        filename=_BASIC.name,
        options=ArrangeOptions(n=1, max_iters=0, use_critic=False),
        llm=ConstantLLM("noop"),
    )
    warning = ImportDiagnostic(
        ImportCode.MIDI_TEXT_IGNORED,
        DiagnosticSeverity.WARNING,
        "bounded MIDI text was ignored",
        SourceLocation(track_index=1, event_index=7, channel=2, tick=960),
    )
    object.__setattr__(outcome.imported, "warnings", (warning,))

    wire = arrange_outcome_to_wire(outcome)
    assert wire["source"]["warnings"][0]["location"] == {  # type: ignore[index]
        "part_id": None,
        "measure": None,
        "voice": None,
        "element": None,
        "archive_member": None,
        "track_index": 1,
        "event_index": 7,
        "channel": 2,
        "tick": 960,
    }


def test_application_error_wire_contains_only_stable_fields() -> None:
    error = ApplicationError(
        ApplicationCode.TARGET_INPUT_REJECTED,
        "target_json",
        "target JSON was rejected by the public input contract",
        (
            ApplicationDiagnostic(
                "DUPLICATE_KEY",
                "$.notes[0].pitch",
                "object key occurs more than once",
            ),
        ),
    )
    wire = application_error_to_wire(error)
    assert wire == {
        "service_version": "fretsure-service@0.2.0",
        "code": "TARGET_INPUT_REJECTED",
        "path": "target_json",
        "detail": "target JSON was rejected by the public input contract",
        "diagnostics": [
            {
                "code": "DUPLICATE_KEY",
                "path": "$.notes[0].pitch",
                "message": "object key occurs more than once",
            }
        ],
    }


def test_serializer_rejects_wrong_outcome_type_safely() -> None:
    with pytest.raises(ApplicationError) as caught:
        arrange_outcome_to_wire(object())  # type: ignore[arg-type]
    assert caught.value.code is ApplicationCode.SERIALIZATION_FAILED
    assert caught.value.__cause__ is None
