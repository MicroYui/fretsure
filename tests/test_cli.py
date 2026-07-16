import json
from dataclasses import replace
from fractions import Fraction as F
from pathlib import Path

import pytest

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
    monkeypatch.setattr("fretsure.cli.import_musicxml", lambda path: success)  # type: ignore[attr-defined]
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
    assert "checker fidelity@0.2.0" in captured.out
    assert "ConstantLLM (constant-stub; deterministic offline fallback)" in captured.out
    trace_rows = [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
    ]
    metadata = trace_rows[0]["data"]
    assert metadata["llm_model_id"] == "constant-stub"
    assert metadata["checker_version"] == "oracle@0.2.0"
    assert metadata["input_schema_version"] == "tab-input@0.2.0"
    assert metadata["fidelity_checker_version"] == "fidelity@0.2.0"
    assert len(metadata["profile_fingerprint"]) == 64


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
    monkeypatch.setattr("fretsure.cli.import_musicxml", lambda path: failure)  # type: ignore[attr-defined]

    exit_code = main(["missing.musicxml"])

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert exit_code != 0
    assert "FILE_NOT_FOUND" in captured.err
    assert "file does not exist" in captured.err
    assert "Traceback" not in captured.err


def test_cli_tempo_override_is_shown(monkeypatch: object, capsys: object) -> None:
    success = ImportSuccess(sample_ir(bars=1), (), "musicxml@0.1.0", "abc123")
    monkeypatch.setattr("fretsure.cli.import_musicxml", lambda path: success)  # type: ignore[attr-defined]

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

    monkeypatch.setattr("fretsure.cli.import_musicxml", must_not_import)  # type: ignore[attr-defined]
    with pytest.raises(SystemExit) as exc_info:
        main(["song.musicxml", f"--tempo-bpm={tempo}"])

    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert exc_info.value.code == 2
    assert "must be finite and in 1..1000 BPM" in captured.err
    assert not called


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
    monkeypatch.setattr("fretsure.cli.import_musicxml", lambda path: success)  # type: ignore[attr-defined]

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
    monkeypatch.setattr("fretsure.cli.import_musicxml", lambda path: success)  # type: ignore[attr-defined]

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
