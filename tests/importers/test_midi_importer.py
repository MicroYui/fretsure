from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

import fretsure.importers.midi as midi_module
from fretsure.importers import (
    ImportCode,
    ImportFailure,
    ImportSuccess,
    import_midi,
    import_midi_bytes,
)
from fretsure.importers._music21_midi_adapter import MIDIDependencyError
from fretsure.ir import snapshot_music_ir


def _vlq(value: int) -> bytes:
    output = bytearray((value & 0x7F,))
    value >>= 7
    while value:
        output.insert(0, 0x80 | (value & 0x7F))
        value >>= 7
    return bytes(output)


def _smf(*events: tuple[int, bytes], ppqn: int = 480) -> bytes:
    track = bytearray()
    for delta, event in events:
        track.extend(_vlq(delta))
        track.extend(event)
    header = b"MThd\x00\x00\x00\x06\x00\x00\x00\x01" + ppqn.to_bytes(2, "big")
    return header + b"MTrk" + len(track).to_bytes(4, "big") + bytes(track)


def _supported(*, include_setup_loss: bool = False) -> bytes:
    events: list[tuple[int, bytes]] = [
        (0, b"\xff\x03\x0dImporter Tune"),
        (0, b"\xff\x02\x16Copyright 2026 Example"),
        (0, b"\xff\x51\x03\x09\x89\x68"),
        (0, b"\xff\x58\x04\x04\x02\x18\x08"),
        (0, b"\xff\x59\x02\x00\x00"),
    ]
    if include_setup_loss:
        events.append((0, b"\xb0\x02\x50"))
    events.extend(
        (
            (0, b"\x90\x3c\x40"),
            (480, b"\x80\x3c\x00"),
            (480, b"\xff\x2f\x00"),
        )
    )
    return _smf(*events)


def test_import_midi_bytes_builds_exact_loss_aware_ir() -> None:
    raw = _supported()

    result = import_midi_bytes(raw, "example.mid")

    assert isinstance(result, ImportSuccess)
    assert result.importer_version == "midi@0.1.0"
    assert result.sha256 == hashlib.sha256(raw).hexdigest()
    assert snapshot_music_ir(result.ir) == result.ir
    assert [(note.onset, note.duration, note.pitch, note.voice) for note in result.ir.notes] == [
        (0, 1, 60, "melody")
    ]
    assert result.ir.chords == ()
    assert result.ir.meta.title == "Importer Tune"
    assert result.ir.meta.key == "C"
    assert result.ir.meta.time_sig == (4, 4)
    assert result.ir.meta.tempo_bpm == 96.0
    assert result.ir.meta.duration_beats == 2
    assert result.ir.meta.license == "copyright-notice:Copyright 2026 Example"
    assert [warning.code for warning in result.warnings] == [
        ImportCode.MIDI_HARMONY_UNPROVIDED,
        ImportCode.MIDI_PERFORMANCE_DATA_IGNORED,
    ]
    assert result.provenance is not None
    assert result.provenance.source_format == "midi"
    assert result.provenance.raw_sha256 == result.sha256
    assert result.provenance.root_sha256 == result.sha256
    assert result.provenance.root_member is None
    assert result.provenance.container_version is None


def test_import_midi_path_and_bytes_are_identical(tmp_path: Path) -> None:
    raw = _supported()
    path = tmp_path / "EXAMPLE.MIDI"
    path.write_bytes(raw)

    from_path = import_midi(path)
    from_bytes = import_midi_bytes(raw, path.name)

    assert isinstance(from_path, ImportSuccess)
    assert from_path == from_bytes


def test_public_importer_hands_only_canonical_events_to_music21(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _supported(include_setup_loss=True)
    seen: list[bytes] = []

    def crosscheck(canonical: bytes, _metadata: object) -> None:
        seen.append(canonical)

    monkeypatch.setattr(midi_module, "crosscheck_music21_midi", crosscheck)

    result = import_midi_bytes(raw, "secret.mid")

    assert isinstance(result, ImportSuccess)
    assert len(seen) == 1
    assert seen[0] != raw
    assert b"Importer Tune" not in seen[0]
    assert b"Copyright 2026 Example" not in seen[0]
    assert b"\xb0\x02\x50" not in seen[0]


def test_preflight_error_stops_before_music21(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _supported() + b"trailing"

    def forbidden(_canonical: bytes, _metadata: object) -> None:
        raise AssertionError("adapter must not run")

    monkeypatch.setattr(midi_module, "crosscheck_music21_midi", forbidden)

    result = import_midi_bytes(raw, "bad.mid")

    assert isinstance(result, ImportFailure)
    assert result.diagnostics[0].code is ImportCode.MALFORMED_MIDI


def test_quarter_span_limit_stops_before_music21(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _smf(
        (0, b"\xff\x51\x03\x07\xa1\x20"),
        (0, b"\xff\x58\x04\x04\x02\x18\x08"),
        (0, b"\xff\x59\x02\x00\x00"),
        (4096, b"\x90\x3c\x40"),
        (1, b"\x80\x3c\x00"),
        (0, b"\xff\x2f\x00"),
        ppqn=1,
    )

    def forbidden(_canonical: bytes, _metadata: object) -> None:
        raise AssertionError("adapter must not run")

    monkeypatch.setattr(midi_module, "crosscheck_music21_midi", forbidden)

    result = import_midi_bytes(raw, "sparse.mid")

    assert isinstance(result, ImportFailure)
    assert [diagnostic.code for diagnostic in result.diagnostics] == [
        ImportCode.INPUT_LIMIT_EXCEEDED
    ]
    assert result.diagnostics[0].location is not None
    assert result.diagnostics[0].location.element == "track-duration"
    assert result.diagnostics[0].location.tick == 4097


def test_quarter_span_exact_boundary_crosschecks_safely() -> None:
    raw = _smf(
        (0, b"\xff\x51\x03\x07\xa1\x20"),
        (0, b"\xff\x58\x04\x04\x02\x18\x08"),
        (0, b"\xff\x59\x02\x00\x00"),
        (4095, b"\x90\x3c\x40"),
        (1, b"\x80\x3c\x00"),
        (0, b"\xff\x2f\x00"),
        ppqn=1,
    )

    result = import_midi_bytes(raw, "boundary.mid")

    assert isinstance(result, ImportSuccess)
    assert result.ir.meta.duration_beats == 4096
    assert [(note.onset, note.duration) for note in result.ir.notes] == [(4095, 1)]


def test_missing_music21_is_typed_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_canonical: bytes, _metadata: object) -> None:
        raise MIDIDependencyError("music21")

    monkeypatch.setattr(midi_module, "crosscheck_music21_midi", missing)

    result = import_midi_bytes(_supported(), "score.mid")

    assert isinstance(result, ImportFailure)
    assert [diagnostic.code for diagnostic in result.diagnostics] == [
        ImportCode.MISSING_DEPENDENCY
    ]
    assert "midi' or 'score" in result.diagnostics[0].message


@pytest.mark.parametrize(
    "filename",
    [
        "../escape.mid",
        "folder\\escape.midi",
        "C:escape.mid",
        "control\x00.mid",
        ".mid",
        "score.mid.exe",
    ],
)
def test_midi_bytes_rejects_non_inert_or_wrong_suffix(filename: str) -> None:
    result = import_midi_bytes(_supported(), filename)

    assert isinstance(result, ImportFailure)
    assert result.diagnostics[0].code in {
        ImportCode.INVALID_INPUT,
        ImportCode.UNSUPPORTED_FILE_TYPE,
    }


def test_midi_path_requires_a_regular_file(tmp_path: Path) -> None:
    directory = tmp_path / "directory.mid"
    directory.mkdir()

    result = import_midi(directory)

    assert isinstance(result, ImportFailure)
    assert result.diagnostics[0].code is ImportCode.NOT_A_FILE


def test_midi_path_fails_closed_when_open_file_grows_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "changing.mid"
    path.write_bytes(_supported())
    real_read = os.read
    mutated = False

    def mutate_after_first_read(descriptor: int, size: int) -> bytes:
        nonlocal mutated
        chunk = real_read(descriptor, size)
        if not mutated:
            mutated = True
            with path.open("ab") as handle:
                handle.write(b"x")
        return chunk

    monkeypatch.setattr(os, "read", mutate_after_first_read)

    result = import_midi(path)

    assert mutated
    assert isinstance(result, ImportFailure)
    assert result.diagnostics[0].code is ImportCode.FILE_READ_ERROR
    assert result.diagnostics[0].message == "MIDI file changed while it was being read"


@pytest.mark.skipif(os.name != "posix", reason="POSIX unlink keeps the open descriptor alive")
def test_midi_path_replacement_after_atomic_open_cannot_redirect_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "replace.mid"
    original = _supported()
    path.write_bytes(original)
    real_open = os.open
    replaced = False

    def replace_after_open(
        path_arg: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
    ) -> int:
        nonlocal replaced
        descriptor = real_open(path_arg, flags, mode)
        if path_arg == path:
            path.unlink()
            path.write_bytes(b"not MIDI")
            replaced = True
        return descriptor

    monkeypatch.setattr(os, "open", replace_after_open)

    result = import_midi(path)

    assert replaced
    assert isinstance(result, ImportSuccess)
    assert result.sha256 == hashlib.sha256(original).hexdigest()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_midi_path_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    path = tmp_path / "pipe.mid"
    os.mkfifo(path)

    result = import_midi(path)

    assert isinstance(result, ImportFailure)
    assert result.diagnostics[0].code is ImportCode.NOT_A_FILE
