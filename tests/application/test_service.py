from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

import fretsure.application.service as service_module
from fretsure.application import (
    SERVICE_VERSION,
    ApplicationCode,
    ApplicationError,
    ArrangeOptions,
    CheckOptions,
    RenderOptions,
    SolveOptions,
    arrange_outcome_to_wire,
    arrange_score_bytes,
    capabilities,
    check_tab_json,
    render_tab_json,
    solve_target_json,
)
from fretsure.demo import sample_ir
from fretsure.importers import (
    DiagnosticSeverity,
    ImportCode,
    ImportDiagnostic,
    ImportFailure,
    ImportSuccess,
    import_musicxml_bytes,
)
from fretsure.ir import Note
from fretsure.llm.client import ConstantLLM
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.pipeline import PipelineOptions, run_pipeline
from fretsure.tab import tab_to_json

_BASIC = Path("tests/fixtures/musicxml/supported_basic.musicxml")
_PRODUCERS = Path("tests/fixtures/producers")
_MUSESCORE_XML = _PRODUCERS / "musescore-4.7.4.musicxml"
_MUSESCORE_MXL = _PRODUCERS / "musescore-4.7.4-roundtrip-supported_basic.mxl"
_MIDI_PRODUCERS = Path("tests/fixtures/midi/producers")
_MUSIC21_MIDI = _MIDI_PRODUCERS / "music21-10.5.0-melody_only.mid"
_UNPROVIDED_KEY = "key-signature:fifths=0;mode=unprovided"
_MINIMAL_MIDI = bytes.fromhex(
    "4d546864000000060000000101e0"  # format 0, one track, PPQN 480
    "4d54726b00000022"
    "00ff510307a120"  # tick 0: 120 BPM
    "00ff580404021808"  # tick 0: 4/4
    "00ff59020000"  # tick 0: C major
    "00903c40"  # tick 0: C4 note on
    "8360803c00"  # tick 480: C4 note off
    "00ff2f00"  # tick 480: end of track
)


def _application_error(call: Any) -> ApplicationError:
    with pytest.raises(ApplicationError) as caught:
        call()
    return caught.value


class _CountingLLM(ConstantLLM):
    def __init__(self, reply: str = "noop") -> None:
        super().__init__(reply)
        self.model_reads = 0

    @property
    def model_id(self) -> str:
        self.model_reads += 1
        return f"counting-{self.model_reads}"


def test_capabilities_freeze_service_target_and_profile_contracts() -> None:
    value = capabilities()
    assert value.service_version == SERVICE_VERSION == "fretsure-service@0.2.0"
    assert value.score_input_version == "score-input@0.1.0"
    assert dict(value.score_format_registry) == {
        "musicxml": "musicxml@0.3.0",
        "mxl": "musicxml@0.3.0",
        "midi": "midi@0.1.0",
    }
    assert value.input_suffixes == (".musicxml", ".xml", ".mxl", ".mid", ".midi")
    assert value.target_input_schema_version == "target-input@0.1.0"
    assert value.profiles == ("median",)
    assert value.render_formats == ("ascii",)
    assert value.default_arrange_options == ArrangeOptions(
        n=1,
        max_iters=0,
        use_critic=False,
    )
    with pytest.raises(FrozenInstanceError):
        value.service_version = "mutated"  # type: ignore[misc]
    with pytest.raises(TypeError):
        value.score_format_registry["midi"] = "mutated"  # type: ignore[index]


def test_arrange_uses_generic_score_router_with_exact_filename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[bytes, str]] = []
    failure = ImportFailure(
        (
            ImportDiagnostic(
                ImportCode.MALFORMED_XML,
                DiagnosticSeverity.ERROR,
                "deliberate test rejection",
            ),
        )
    )

    def route(data: bytes, filename: str) -> ImportFailure:
        calls.append((data, filename))
        return failure

    monkeypatch.setattr(service_module, "import_score_bytes", route)
    error = _application_error(
        lambda: arrange_score_bytes(
            b"exact-midi-bytes",
            filename="melody.MID",
            options=ArrangeOptions(),
            llm=ConstantLLM(),
        )
    )

    assert calls == [(b"exact-midi-bytes", "melody.MID")]
    assert error.code is ApplicationCode.IMPORT_REJECTED
    assert error.detail == "score bytes were rejected by the selected score importer"


def test_invalid_arrange_options_fail_before_importer_or_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def must_not_import(*args: object, **kwargs: object) -> ImportSuccess:
        calls.append("import")
        raise AssertionError

    llm = _CountingLLM()
    monkeypatch.setattr(service_module, "import_score_bytes", must_not_import)
    error = _application_error(
        lambda: arrange_score_bytes(
            b"irrelevant",
            filename="score.musicxml",
            options=ArrangeOptions(n=True),
            llm=llm,
        )
    )
    assert error.code is ApplicationCode.INVALID_OPTIONS
    assert calls == []
    assert llm.model_reads == 0


def test_non_bytes_fail_before_importer(monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_import(*args: object, **kwargs: object) -> ImportSuccess:
        raise AssertionError

    monkeypatch.setattr(service_module, "import_score_bytes", must_not_import)
    error = _application_error(
        lambda: arrange_score_bytes(
            bytearray(b"xml"),  # type: ignore[arg-type]
            filename="score.musicxml",
            options=ArrangeOptions(),
            llm=ConstantLLM(),
        )
    )
    assert error.code is ApplicationCode.INVALID_ARGUMENT
    assert error.path == "data"


def test_import_failure_is_redacted_at_the_application_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "raw parser traceback /private/tmp/secret"
    failure = ImportFailure(
        (
            ImportDiagnostic(
                ImportCode.MALFORMED_XML,
                DiagnosticSeverity.ERROR,
                secret,
            ),
        )
    )
    monkeypatch.setattr(service_module, "import_score_bytes", lambda *a, **k: failure)
    error = _application_error(
        lambda: arrange_score_bytes(
            b"<bad>",
            filename="score.musicxml",
            options=ArrangeOptions(),
            llm=ConstantLLM(),
        )
    )
    assert error.code is ApplicationCode.IMPORT_REJECTED
    assert secret not in str(error)
    assert all(secret not in item.message for item in error.diagnostics)


def test_missing_optional_importer_dependency_has_a_distinct_stable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = ImportFailure(
        (
            ImportDiagnostic(
                ImportCode.MISSING_DEPENDENCY,
                DiagnosticSeverity.ERROR,
                "raw module import exception",
            ),
        )
    )
    monkeypatch.setattr(service_module, "import_score_bytes", lambda *a, **k: failure)
    error = _application_error(
        lambda: arrange_score_bytes(
            b"<score/>",
            filename="score.musicxml",
            options=ArrangeOptions(),
            llm=ConstantLLM(),
        )
    )
    assert error.code is ApplicationCode.DEPENDENCY_UNAVAILABLE
    assert "exception" not in str(error)


def test_rejected_score_never_reads_llm_provenance() -> None:
    llm = _CountingLLM()
    error = _application_error(
        lambda: arrange_score_bytes(
            b"not xml",
            filename="score.musicxml",
            options=ArrangeOptions(),
            llm=llm,
        )
    )
    assert error.code is ApplicationCode.IMPORT_REJECTED
    assert llm.model_reads == 0


def test_pipeline_failure_is_safe_and_drops_raw_provider_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    imported = ImportSuccess(sample_ir(bars=1), (), "musicxml@test", "a" * 64)
    monkeypatch.setattr(service_module, "import_score_bytes", lambda *a, **k: imported)

    def fail_pipeline(*args: object, **kwargs: object) -> object:
        raise RuntimeError("provider secret token and traceback")

    monkeypatch.setattr(service_module, "run_pipeline", fail_pipeline)
    error = _application_error(
        lambda: arrange_score_bytes(
            b"ignored",
            filename="score.musicxml",
            options=ArrangeOptions(),
            llm=ConstantLLM(),
        )
    )
    assert error.code is ApplicationCode.ARRANGEMENT_FAILED
    assert "provider" not in str(error)
    assert error.__cause__ is None


def test_incremental_proposal_transport_failure_is_a_typed_service_failure_not_a_200() -> None:
    secret = "Bearer private-provider-token at /private/provider/socket"

    class FailsDuringProposal:
        model_id = "incremental-transport-test"

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, **kwargs: object) -> str:
            del kwargs
            self.calls += 1
            raise RuntimeError(secret)

    llm = FailsDuringProposal()
    error = _application_error(
        lambda: arrange_score_bytes(
            _BASIC.read_bytes(),
            filename=_BASIC.name,
            options=ArrangeOptions(n=1, max_iters=1, use_critic=False),
            llm=llm,
        )
    )

    assert llm.calls == 1
    assert error.code is ApplicationCode.ARRANGEMENT_FAILED
    assert error.path == "arrangement"
    assert secret not in str(error)
    assert error.__cause__ is None


def test_real_arrangement_matches_the_existing_pipeline_and_pins_model_once() -> None:
    data = _BASIC.read_bytes()
    llm = _CountingLLM()
    options = ArrangeOptions(n=1, max_iters=0, use_critic=False)
    outcome = arrange_score_bytes(
        data,
        filename=_BASIC.name,
        options=options,
        llm=llm,
    )
    assert llm.model_reads == 1
    assert outcome.status == "tab_produced"
    assert outcome.model_id == "counting-1"
    assert outcome.tab is not None
    assert outcome.oracle is not None
    assert outcome.oracle.verdict == "GREEN"
    assert outcome.trace_document_json.startswith("{")
    assert not hasattr(outcome, "pipeline")

    imported = import_musicxml_bytes(data, _BASIC.name)
    assert isinstance(imported, ImportSuccess)
    direct = run_pipeline(
        imported.ir,
        ConstantLLM("noop"),
        options=PipelineOptions(n=1, max_iters=0, use_critic=False),
        incremental_agent=True,
    )
    assert direct.arrangement.tab is not None
    assert tab_to_json(outcome.tab) == tab_to_json(direct.arrangement.tab)
    assert outcome.oracle.verdict == direct.arrangement.oracle.verdict  # type: ignore[union-attr]
    assert outcome.faithfulness == direct.faithfulness


def test_minimal_midi_crosses_application_with_dynamic_stamps_and_na_evidence() -> None:
    outcome = arrange_score_bytes(
        _MINIMAL_MIDI,
        filename="minimal.mid",
        options=ArrangeOptions(n=1, max_iters=0, use_critic=False),
        llm=ConstantLLM("noop"),
    )
    wire = arrange_outcome_to_wire(outcome)

    assert outcome.imported.provenance is not None
    assert outcome.imported.provenance.source_format == "midi"
    assert outcome.imported.ir.chords == ()
    assert wire["source"]["format"] == "midi"
    assert wire["source"]["importer_version"] == "midi@0.1.0"
    assert wire["stamps"]["score_input_version"] == "score-input@0.1.0"
    assert wire["stamps"]["importer_version"] == "midi@0.1.0"
    assert wire["faithfulness"] == {
        "melody_f1": 1.0,
        "bass_root_accuracy": None,
        "harmony_jaccard": None,
        "evaluated_dimensions": ["melody"],
        "unavailable_dimensions": ["bass_root", "harmony"],
        "passed": True,
        "checker_version": "fidelity@0.3.0",
    }
    selected = next(
        step for step in wire["trace"]["steps"] if step["event"] == "CANDIDATE_SELECTED"
    )
    selected_data = selected["data"]
    for field in (
        "melody_f1",
        "bass_root_accuracy",
        "harmony_jaccard",
        "evaluated_dimensions",
        "unavailable_dimensions",
    ):
        assert selected_data[field] == wire["faithfulness"][field]
    assert selected_data["faithfulness_passed"] is wire["faithfulness"]["passed"]
    assert {
        "ranking_melody_recall",
        "ranking_bass_preserved",
        "ranking_harmony_jaccard",
    } <= set(selected_data)
    assert not {"melody_recall", "bass_preserved"} & set(selected_data)


@pytest.mark.parametrize(
    ("score", "source_format", "warning_codes", "root_member"),
    [
        (
            _MUSESCORE_XML,
            "musicxml",
            ["KEY_MODE_UNPROVIDED"],
            None,
        ),
        (
            _MUSESCORE_MXL,
            "mxl",
            ["MXL_ROOTFILE_MEDIA_TYPE_UNPROVIDED", "KEY_MODE_UNPROVIDED"],
            "score.xml",
        ),
    ],
)
def test_frozen_musescore_inputs_cross_the_application_seam_deterministically(
    score: Path,
    source_format: str,
    warning_codes: list[str],
    root_member: str | None,
) -> None:
    options = ArrangeOptions(n=1, max_iters=0, use_critic=False)

    first = arrange_score_bytes(
        score.read_bytes(),
        filename=score.name,
        options=options,
        llm=ConstantLLM("noop"),
    )
    second = arrange_score_bytes(
        score.read_bytes(),
        filename=score.name,
        options=options,
        llm=ConstantLLM("noop"),
    )
    first_wire = arrange_outcome_to_wire(first)
    second_wire = arrange_outcome_to_wire(second)

    assert first_wire == second_wire
    assert first.imported.ir.meta.key == _UNPROVIDED_KEY
    assert [item.code.value for item in first.imported.warnings] == warning_codes
    assert first_wire["score"]["key"] == _UNPROVIDED_KEY
    assert first_wire["score"]["key"] not in {"C", "C major", "Am", "A minor"}
    assert first_wire["source"]["format"] == source_format
    assert first_wire["source"]["root_member"] == root_member
    assert [item["code"] for item in first_wire["source"]["warnings"]] == warning_codes
    assert first_wire["source"]["importer_version"] == "musicxml@0.3.0"
    assert first_wire["stamps"]["importer_version"] == "musicxml@0.3.0"


@pytest.mark.integration
def test_real_proxy_arranges_frozen_musescore_and_stamps_every_contract() -> None:
    import os

    if not os.environ.get("ANTHROPIC_BASE_URL"):
        pytest.skip("no local LLM proxy configured")
    from fretsure.llm.client import ProxyLLM

    outcome = arrange_score_bytes(
        _MUSESCORE_XML.read_bytes(),
        filename=_MUSESCORE_XML.name,
        options=ArrangeOptions(n=1, max_iters=0, use_critic=False),
        llm=ProxyLLM(),
    )
    wire = arrange_outcome_to_wire(outcome)

    assert outcome.status in {"tab_produced", "no_fingering_within_budget"}
    assert wire["score"]["key"] == _UNPROVIDED_KEY
    assert [item["code"] for item in wire["source"]["warnings"]] == [
        "KEY_MODE_UNPROVIDED"
    ]
    assert wire["model"] == {"model_id": "gpt-5.6-sol"}
    assert wire["stamps"]["model_id"] == "gpt-5.6-sol"
    assert wire["stamps"]["importer_version"] == "musicxml@0.3.0"
    assert wire["stamps"]["oracle_checker_version"] == "oracle@0.2.0"
    assert wire["stamps"]["profile_version"] == "median@0.1"


@pytest.mark.integration
def test_real_proxy_arranges_exact_frozen_midi_with_na_source_evidence() -> None:
    import os

    if not os.environ.get("ANTHROPIC_BASE_URL"):
        pytest.skip("no local LLM proxy configured")
    from fretsure.llm.client import ProxyLLM

    outcome = arrange_score_bytes(
        _MUSIC21_MIDI.read_bytes(),
        filename=_MUSIC21_MIDI.name,
        options=ArrangeOptions(n=1, max_iters=0, use_critic=False),
        llm=ProxyLLM(),
    )
    wire = arrange_outcome_to_wire(outcome)

    assert outcome.status in {"tab_produced", "no_fingering_within_budget"}
    assert wire["source"]["raw_sha256"] == (
        "9d6dff16ad49f7a2cb75f43b60af4a85bd86797f505d7f5e7f5efd7a06ea227c"
    )
    assert wire["source"]["format"] == "midi"
    assert wire["source"]["importer_version"] == "midi@0.1.0"
    assert wire["score"]["chord_count"] == 0
    assert wire["model"] == {"model_id": "gpt-5.6-sol"}
    assert wire["stamps"]["score_input_version"] == "score-input@0.1.0"
    assert wire["stamps"]["importer_version"] == "midi@0.1.0"
    if wire["faithfulness"] is not None:
        assert wire["faithfulness"]["evaluated_dimensions"] == ["melody"]
        assert wire["faithfulness"]["unavailable_dimensions"] == [
            "bass_root",
            "harmony",
        ]
        assert wire["faithfulness"]["bass_root_accuracy"] is None
        assert wire["faithfulness"]["harmony_jaccard"] is None


def test_arrange_outcome_is_frozen_and_trace_is_an_immutable_snapshot() -> None:
    outcome = arrange_score_bytes(
        _BASIC.read_bytes(),
        filename=_BASIC.name,
        options=ArrangeOptions(n=1, max_iters=0, use_critic=False),
        llm=ConstantLLM("noop"),
    )
    original = outcome.trace_document_json
    parsed = json.loads(original)
    parsed["steps"].clear()
    assert json.loads(outcome.trace_document_json)["steps"]
    with pytest.raises(FrozenInstanceError):
        outcome.status = "no_fingering_within_budget"  # type: ignore[misc]


def test_unknown_profile_is_a_stable_option_error() -> None:
    error = _application_error(lambda: check_tab_json("{}", options=CheckOptions(profile="large")))
    assert error.code is ApplicationCode.UNKNOWN_PROFILE
    assert error.path == "options.profile"


def test_check_maps_strict_tab_schema_and_returns_oracle_evidence() -> None:
    solved = solve_target_json(
        '{"notes":[{"onset":"0/1","duration":"1/1","pitch":60,"voice":"melody"}]}',
        options=SolveOptions(),
    )
    assert solved.tab is not None
    outcome = check_tab_json(tab_to_json(solved.tab), options=CheckOptions())
    assert outcome.tab == solved.tab
    assert outcome.oracle.verdict != "RED"

    error = _application_error(
        lambda: check_tab_json(
            '{"tuning":[],"capo":0,"capo":1,"notes":[]}',
            options=CheckOptions(),
        )
    )
    assert error.code is ApplicationCode.TAB_INPUT_REJECTED
    assert error.diagnostics[0].code == "DUPLICATE_KEY"


def test_solve_returns_one_found_result_without_completeness_claim() -> None:
    target = (
        '{"notes":['
        '{"onset":"0/1","duration":"1/1","pitch":60,"voice":"melody"},'
        '{"onset":"1/1","duration":"1/1","pitch":62,"voice":"melody"}'
        "]}"
    )
    outcome = solve_target_json(target, options=SolveOptions(beam=4))
    assert outcome.status == "found"
    assert outcome.search_complete is False
    assert outcome.max_solutions == 1
    assert outcome.tab is not None
    assert outcome.oracle is not None and outcome.oracle.verdict != "RED"
    assert outcome.infeasible is None


def test_empty_target_is_not_an_unsatisfiability_claim() -> None:
    outcome = solve_target_json('{"notes":[]}', options=SolveOptions())
    assert outcome.status == "not_found_within_budget"
    assert outcome.search_complete is False
    assert outcome.tab is None
    assert outcome.oracle is None
    assert outcome.infeasible is not None
    assert outcome.infeasible.code.value == "EMPTY_TARGET"


def test_invalid_solve_options_precede_target_parser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def must_not_parse(payload: str) -> tuple[Note, ...]:
        calls.append(payload)
        raise AssertionError

    monkeypatch.setattr(service_module, "target_from_json", must_not_parse)
    error = _application_error(
        lambda: solve_target_json("not-json", options=SolveOptions(beam=True))
    )
    assert error.code is ApplicationCode.INVALID_OPTIONS
    assert calls == []


def test_invalid_target_maps_to_stable_application_diagnostic() -> None:
    error = _application_error(
        lambda: solve_target_json(
            '{"notes":[{"onset":"2/4","duration":"1/1","pitch":60,"voice":"melody"}]}',
            options=SolveOptions(),
        )
    )
    assert error.code is ApplicationCode.TARGET_INPUT_REJECTED
    assert error.diagnostics[0].code == "INVALID_FRACTION"
    assert error.diagnostics[0].path == "$.notes[0].onset"


def test_render_validates_then_renders_ascii() -> None:
    solved = solve_target_json(
        '{"notes":[{"onset":"0/1","duration":"1/1","pitch":60,"voice":"melody"}]}',
        options=SolveOptions(),
    )
    assert solved.tab is not None
    outcome = render_tab_json(tab_to_json(solved.tab), options=RenderOptions())
    assert outcome.options.format == "ascii"
    assert outcome.content.count("\n") == 5
    assert "e|" in outcome.content


def test_unknown_render_format_fails_before_tab_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def must_not_parse(*args: object, **kwargs: object) -> object:
        raise AssertionError

    monkeypatch.setattr(service_module, "validated_tab_from_json", must_not_parse)
    error = _application_error(
        lambda: render_tab_json("not-json", options=RenderOptions(format="html"))
    )
    assert error.code is ApplicationCode.UNSUPPORTED_RENDER_FORMAT


def test_options_require_exact_contract_classes() -> None:
    class ArrangeOptionsSubclass(ArrangeOptions):
        pass

    error = _application_error(
        lambda: arrange_score_bytes(
            b"x",
            filename="score.musicxml",
            options=ArrangeOptionsSubclass(),
            llm=ConstantLLM(),
        )
    )
    assert error.code is ApplicationCode.INVALID_OPTIONS


def test_solver_uses_requested_instrument_and_profile_snapshot() -> None:
    outcome = solve_target_json(
        '{"notes":[{"onset":"0/1","duration":"1/1","pitch":60,"voice":"melody"}]}',
        options=SolveOptions(
            profile="median",
            tuning=(40, 45, 50, 55, 59, 64),
            capo=0,
            tempo_bpm=90,
            beam=4,
        ),
    )
    assert outcome.profile == MEDIAN_HAND
    assert outcome.options.tempo_bpm == 90.0
