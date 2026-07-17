import json
from dataclasses import replace
from fractions import Fraction as F
from pathlib import Path

import pytest

from fretsure.agent.trace import TraceInputError
from fretsure.cli import main
from fretsure.demo import sample_ir
from fretsure.importers import (
    DiagnosticSeverity,
    ImportCode,
    ImportDiagnostic,
    ImportFailure,
    ImportSuccess,
)
from fretsure.ir import MusicIR, Note


def test_cli_success_prints_full_product_result_and_writes_trace(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    warning = ImportDiagnostic(
        ImportCode.IGNORED_NOTATION,
        DiagnosticSeverity.WARNING,
        "lyrics are not represented in MusicIR",
    )
    success = ImportSuccess(sample_ir(bars=1), (warning,), "musicxml@0.1.0", "abc123")
    monkeypatch.setattr("fretsure.cli.import_score", lambda path: success)  # type: ignore[attr-defined]
    trace_path = tmp_path / "trace.jsonl"

    exit_code = main(["song.musicxml", "--n", "1", "--no-critic", "--trace-jsonl", str(trace_path)])

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert exit_code == 0
    for section in (
        "IMPORT WARNINGS",
        "IR SUMMARY",
        "source tempo",
        "effective tempo",
        "ARRANGED TAB",
        "ORACLE VERDICT",
        "FAITHFULNESS TO INPUT",
        "TRACE",
    ):
        assert section in captured.out
    assert "GREEN" in captured.out
    assert "model-relative GREEN certification" in captured.out
    assert "profile SHA-256" in captured.out
    assert "input schema tab-input@0.2.0" in captured.out
    assert "checker fidelity@0.3.0" in captured.out
    assert "symbolic score to versioned-model-checked fingerstyle tab" in captured.out
    assert "SCORE ROUTER      : score-input@0.1.0" in captured.out
    assert "ConstantLLM (constant-stub; deterministic offline fallback)" in captured.out
    trace_rows = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]
    metadata = trace_rows[0]["data"]
    assert metadata["llm_model_id"] == "constant-stub"
    assert metadata["checker_version"] == "oracle@0.2.0"
    assert metadata["input_schema_version"] == "tab-input@0.2.0"
    assert metadata["fidelity_checker_version"] == "fidelity@0.3.0"
    assert len(metadata["profile_fingerprint"]) == 64


def test_cli_marks_unavailable_fidelity_dimensions_as_na(
    monkeypatch: object, capsys: object
) -> None:
    base = sample_ir(bars=1)
    ir = MusicIR(
        (Note(F(0), F(1), 64, "melody"),),
        (),
        replace(
            base.meta,
            key="key-signature:unprovided",
            duration_beats=F(1),
        ),
    )
    success = ImportSuccess(ir, (), "midi@0.1.0", "abc123")
    monkeypatch.setattr("fretsure.cli.import_score", lambda path: success)  # type: ignore[attr-defined]

    assert main(["song.mid", "--n", "1", "--no-critic"]) == 0

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "melody-F1 1.00" in output
    assert "bass-root N/A" in output
    assert "harmony N/A" in output
    assert "available-dimension gate PASS (1/3 evaluated)" in output
    assert "unavailable: bass_root, harmony" in output


def test_cli_import_failure_is_nonzero_and_has_no_traceback(
    monkeypatch: object, capsys: object
) -> None:
    failure = ImportFailure(
        (
            ImportDiagnostic(
                ImportCode.FILE_NOT_FOUND,
                DiagnosticSeverity.ERROR,
                "file does not exist",
            ),
        )
    )
    monkeypatch.setattr("fretsure.cli.import_score", lambda path: failure)  # type: ignore[attr-defined]

    exit_code = main(["missing.musicxml"])

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert exit_code != 0
    assert "FILE_NOT_FOUND" in captured.err
    assert "file does not exist" in captured.err
    assert "score import failed" in captured.err
    assert "Traceback" not in captured.err


def test_cli_real_midi_uses_router_dynamic_importer_and_na_evidence(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    raw = bytes.fromhex(
        "4d546864000000060000000101e0"
        "4d54726b00000022"
        "00ff510307a120"
        "00ff580404021808"
        "00ff59020000"
        "00903c40"
        "8360803c00"
        "00ff2f00"
    )
    path = tmp_path / "minimal.mid"
    path.write_bytes(raw)

    exit_code = main([str(path), "--n", "1", "--max-iters", "0", "--no-critic"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.err == ""
    assert "SCORE ROUTER      : score-input@0.1.0" in captured.out
    assert "IMPORTER          : midi@0.1.0" in captured.out
    assert "bass-root N/A" in captured.out
    assert "harmony N/A" in captured.out
    assert "available-dimension gate PASS (1/3 evaluated)" in captured.out


def test_cli_trace_contract_failure_preserves_existing_file_without_traceback(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    success = ImportSuccess(sample_ir(bars=1), (), "musicxml@0.1.0", "abc123")
    monkeypatch.setattr("fretsure.cli.import_score", lambda path: success)  # type: ignore[attr-defined]
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text("previous-good-trace", encoding="utf-8")

    def fail_trace(_trace: object) -> str:
        raise TraceInputError("steps[0]", "injected contract failure")

    monkeypatch.setattr("fretsure.agent.trace.Trace.to_jsonl", fail_trace)  # type: ignore[attr-defined]
    exit_code = main(
        ["song.musicxml", "--n", "1", "--no-critic", "--trace-jsonl", str(trace_path)]
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert exit_code == 4
    assert trace_path.read_text(encoding="utf-8") == "previous-good-trace"
    assert "injected contract failure" in captured.err
    assert "Traceback" not in captured.err


def test_cli_atomic_replace_failure_cleans_temp_and_preserves_existing_file(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    success = ImportSuccess(sample_ir(bars=1), (), "musicxml@0.1.0", "abc123")
    monkeypatch.setattr("fretsure.cli.import_score", lambda path: success)  # type: ignore[attr-defined]
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text("previous-good-trace", encoding="utf-8")

    def fail_replace(source: object, destination: object) -> None:
        del source, destination
        raise OSError("injected replace failure")

    monkeypatch.setattr("fretsure.cli.os.replace", fail_replace)  # type: ignore[attr-defined]
    exit_code = main(
        ["song.musicxml", "--n", "1", "--no-critic", "--trace-jsonl", str(trace_path)]
    )

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert exit_code == 4
    assert trace_path.read_text(encoding="utf-8") == "previous-good-trace"
    assert not list(tmp_path.glob(".trace.jsonl.*.tmp"))
    assert "injected replace failure" in captured.err
    assert "Traceback" not in captured.err


def test_cli_tempo_override_is_shown(monkeypatch: object, capsys: object) -> None:
    success = ImportSuccess(sample_ir(bars=1), (), "musicxml@0.1.0", "abc123")
    monkeypatch.setattr("fretsure.cli.import_score", lambda path: success)  # type: ignore[attr-defined]

    exit_code = main(["song.musicxml", "--n", "1", "--no-critic", "--tempo-bpm", "72"])

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert exit_code == 0
    assert "source tempo    : 90 bpm" in captured.out
    assert "effective tempo : 72 bpm" in captured.out


@pytest.mark.parametrize("tempo", ["nan", "inf", "-inf", "0", "0.5", "1001"])
def test_cli_rejects_nonphysical_tempo_before_import(
    tempo: str, monkeypatch: object, capsys: object
) -> None:
    called = False

    def must_not_import(path: Path) -> ImportSuccess:
        nonlocal called
        called = True
        raise AssertionError(path)

    monkeypatch.setattr("fretsure.cli.import_score", must_not_import)  # type: ignore[attr-defined]
    with pytest.raises(SystemExit) as exc_info:
        main(["song.musicxml", f"--tempo-bpm={tempo}"])

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert exc_info.value.code == 2
    assert "must be finite and in 1..1000 BPM" in captured.err
    assert not called


@pytest.mark.parametrize(
    "control",
    [
        ["--n", "0"],
        ["--n", "65"],
        ["--max-iters", "-1"],
        ["--max-iters", "65"],
    ],
)
def test_cli_rejects_out_of_budget_controls_before_import(
    control: list[str], monkeypatch: object, capsys: object
) -> None:
    called = False

    def must_not_import(path: Path) -> ImportSuccess:
        nonlocal called
        called = True
        raise AssertionError(path)

    monkeypatch.setattr("fretsure.cli.import_score", must_not_import)  # type: ignore[attr-defined]
    with pytest.raises(SystemExit) as exc_info:
        main(["song.musicxml", *control])

    assert exc_info.value.code == 2
    assert not called
    assert "must be in" in capsys.readouterr().err  # type: ignore[attr-defined]


def test_cli_redacts_unexpected_importer_and_pipeline_failures(
    monkeypatch: object, capsys: object
) -> None:
    secret = "Bearer TOP_SECRET /private/provider"

    def broken_import(_path: Path) -> ImportSuccess:
        raise RuntimeError(secret)

    monkeypatch.setattr("fretsure.cli.import_score", broken_import)  # type: ignore[attr-defined]
    assert main(["song.musicxml"]) == 2
    importer_error = capsys.readouterr().err  # type: ignore[attr-defined]
    assert "failed unexpectedly" in importer_error
    assert secret not in importer_error
    assert "RuntimeError" not in importer_error

    success = ImportSuccess(sample_ir(bars=1), (), "musicxml@0.1.0", "abc123")
    monkeypatch.setattr("fretsure.cli.import_score", lambda _path: success)  # type: ignore[attr-defined]

    def broken_pipeline(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError(secret)

    monkeypatch.setattr("fretsure.cli.run_pipeline", broken_pipeline)  # type: ignore[attr-defined]
    assert main(["song.musicxml", "--n", "1"]) == 3
    pipeline_error = capsys.readouterr().err  # type: ignore[attr-defined]
    assert "failed unexpectedly" in pipeline_error
    assert secret not in pipeline_error
    assert "RuntimeError" not in pipeline_error


def test_cli_escapes_control_characters_in_untrusted_metadata_and_path(
    monkeypatch: object, capsys: object
) -> None:
    ir = sample_ir(bars=1)
    ir = replace(
        ir,
        meta=replace(
            ir.meta,
            title="unsafe\x1b[31m\nname",
            source="source\rspoof",
            license="rights\tunknown",
        ),
    )
    success = ImportSuccess(ir, (), "musicxml@0.1.0", "abc123")
    monkeypatch.setattr("fretsure.cli.import_score", lambda path: success)  # type: ignore[attr-defined]

    exit_code = main(["evil\x1b[2J.musicxml", "--n", "1", "--no-critic"])

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert exit_code == 0
    assert "\x1b" not in captured.out
    assert "\\x1b[31m\\x0aname" in captured.out
    assert "evil\\x1b[2J.musicxml" in captured.out
    assert "source\\x0dspoof" in captured.out
    assert "rights\\x09unknown" in captured.out


def test_cli_real_llm_capacity_failure_is_explicit_before_proxy_creation(
    monkeypatch: object, capsys: object
) -> None:
    base = sample_ir(bars=1)
    notes = tuple(Note(F(i), F(1), 60 + (i % 12), "melody") for i in range(170))
    success = ImportSuccess(
        MusicIR(notes, (), base.meta), (), "musicxml@0.1.0", "abc123"
    )
    monkeypatch.setattr("fretsure.cli.import_score", lambda path: success)  # type: ignore[attr-defined]

    exit_code = main(["long.musicxml", "--llm"])

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert exit_code != 0
    assert "input was not truncated" in captured.err
    assert "use the deterministic path" in captured.err
    assert "chunking is deferred" in captured.err
    assert "Traceback" not in captured.err


def test_real_musicxml_fixture_cli_is_deterministic(
    tmp_path: Path, capsys: object
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "musicxml" / "supported_basic.musicxml"
    trace_path = tmp_path / "trace.jsonl"
    argv = [
        str(fixture),
        "--n",
        "1",
        "--no-critic",
        "--trace-jsonl",
        str(trace_path),
    ]

    first_code = main(argv)
    first = capsys.readouterr()  # type: ignore[attr-defined]
    first_trace = trace_path.read_text(encoding="utf-8")
    second_code = main(argv)
    second = capsys.readouterr()  # type: ignore[attr-defined]
    second_trace = trace_path.read_text(encoding="utf-8")

    assert first_code == second_code == 0
    assert first.out == second.out
    assert first.err == second.err == ""
    assert first_trace == second_trace
    assert "effective tempo : 96 bpm" in first.out


@pytest.mark.parametrize(
    ("filename", "warning_codes"),
    [
        ("musescore-4.7.4.musicxml", ["KEY_MODE_UNPROVIDED"]),
        (
            "musescore-4.7.4-roundtrip-supported_basic.mxl",
            ["MXL_ROOTFILE_MEDIA_TYPE_UNPROVIDED", "KEY_MODE_UNPROVIDED"],
        ),
    ],
)
def test_frozen_musescore_cli_is_loss_aware_and_deterministic(
    filename: str,
    warning_codes: list[str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "producers" / filename
    trace_path = tmp_path / f"{fixture.name}.trace.jsonl"
    argv = [
        str(fixture),
        "--n",
        "1",
        "--max-iters",
        "0",
        "--no-critic",
        "--trace-jsonl",
        str(trace_path),
    ]

    first_code = main(argv)
    first = capsys.readouterr()
    first_trace = trace_path.read_bytes()
    second_code = main(argv)
    second = capsys.readouterr()
    second_trace = trace_path.read_bytes()

    assert first_code == second_code == 0
    assert first.out == second.out
    assert first.err == second.err == ""
    assert first_trace == second_trace
    assert "IMPORTER          : musicxml@0.3.0" in first.out
    assert "key / meter     : key-signature:fifths=0;mode=unprovided / 4/4" in first.out
    assert "key / meter     : C /" not in first.out
    assert "key / meter     : C major /" not in first.out
    assert "key / meter     : Am /" not in first.out
    assert "key / meter     : A minor /" not in first.out
    for warning_code in warning_codes:
        assert warning_code in first.out
