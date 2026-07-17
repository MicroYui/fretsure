from __future__ import annotations

import hashlib
import os
import socket
import stat
import urllib.request
import zipfile
from dataclasses import replace
from fractions import Fraction
from importlib import import_module
from io import BytesIO
from pathlib import Path

import pytest

import fretsure.importers._music21_adapter as music21_adapter_module
import fretsure.importers.musicxml as musicxml_module
from fretsure.importers import (
    DiagnosticSeverity,
    ImportCode,
    ImportDiagnostic,
    ImportFailure,
    ImportLimits,
    ImportSuccess,
    import_musicxml,
    import_musicxml_bytes,
)
from fretsure.ir import IRViolation, MusicIR, Note

FIXTURES = Path(__file__).parents[1] / "fixtures" / "musicxml"
BASIC = FIXTURES / "supported_basic.musicxml"
MIMETYPE = b"application/vnd.recordare.musicxml"
ROOT_MEDIA_TYPE = "application/vnd.recordare.musicxml+xml"


def _only_error(result: ImportSuccess | ImportFailure) -> ImportDiagnostic:
    assert isinstance(result, ImportFailure)
    errors = tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity is DiagnosticSeverity.ERROR
    )
    assert len(errors) == 1, result.diagnostics
    assert not hasattr(result, "ir")
    return errors[0]


def _write_zip_member(
    archive: zipfile.ZipFile,
    name: str,
    data: bytes,
    compression: int,
) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = compression
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    archive.writestr(info, data)


def _valid_mxl(root: bytes | None = None) -> bytes:
    root_bytes = BASIC.read_bytes() if root is None else root
    container = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<container><rootfiles>"
        '<rootfile full-path="score.musicxml" '
        f'media-type="{ROOT_MEDIA_TYPE}"/>'
        "</rootfiles></container>"
    ).encode()
    output = BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=True) as archive:
        _write_zip_member(archive, "mimetype", MIMETYPE, zipfile.ZIP_STORED)
        _write_zip_member(
            archive,
            "META-INF/container.xml",
            container,
            zipfile.ZIP_DEFLATED,
        )
        _write_zip_member(
            archive,
            "score.musicxml",
            root_bytes,
            zipfile.ZIP_DEFLATED,
        )
    return output.getvalue()


def _mode_unprovided_root(fifths: int = 0) -> bytes:
    return BASIC.read_bytes().replace(
        b"<key><fifths>0</fifths><mode>major</mode></key>",
        f"<key><fifths>{fifths}</fifths></key>".encode(),
        1,
    )


def _ir_without_source(result: ImportSuccess) -> MusicIR:
    return replace(result.ir, meta=replace(result.ir.meta, source=""))


def test_bytes_import_is_identical_to_path_import_for_musicxml() -> None:
    raw = BASIC.read_bytes()

    from_path = import_musicxml(BASIC)
    from_bytes = import_musicxml_bytes(raw, BASIC.name)

    assert isinstance(from_path, ImportSuccess)
    assert from_bytes == from_path


def test_bytes_import_is_identical_to_path_import_for_mxl(tmp_path: Path) -> None:
    raw = _valid_mxl()
    path = tmp_path / "score.mxl"
    path.write_bytes(raw)

    from_path = import_musicxml(path)
    from_bytes = import_musicxml_bytes(raw, path.name)

    assert isinstance(from_path, ImportSuccess)
    assert from_bytes == from_path


@pytest.mark.parametrize("filename", ["mode-unprovided.musicxml", "mode-unprovided.xml"])
def test_mode_unprovided_path_and_bytes_are_identical_for_plain_xml(
    tmp_path: Path, filename: str
) -> None:
    raw = _mode_unprovided_root(-2)
    path = tmp_path / filename
    path.write_bytes(raw)

    from_path = import_musicxml(path)
    from_bytes = import_musicxml_bytes(raw, filename)

    assert isinstance(from_path, ImportSuccess), getattr(from_path, "diagnostics", None)
    assert from_bytes == from_path
    assert from_path.ir.meta.key == "key-signature:fifths=-2;mode=unprovided"
    assert [warning.code for warning in from_path.warnings] == [
        ImportCode.KEY_MODE_UNPROVIDED
    ]


def test_mode_unprovided_path_bytes_and_synthetic_mxl_have_semantic_parity(
    tmp_path: Path,
) -> None:
    root = _mode_unprovided_root(6)
    xml_path = tmp_path / "score.musicxml"
    mxl_path = tmp_path / "score.mxl"
    xml_path.write_bytes(root)
    mxl = _valid_mxl(root)
    mxl_path.write_bytes(mxl)

    xml_result = import_musicxml(xml_path)
    mxl_from_path = import_musicxml(mxl_path)
    mxl_from_bytes = import_musicxml_bytes(mxl, mxl_path.name)

    assert isinstance(xml_result, ImportSuccess), getattr(xml_result, "diagnostics", None)
    assert isinstance(mxl_from_path, ImportSuccess), getattr(
        mxl_from_path, "diagnostics", None
    )
    assert mxl_from_bytes == mxl_from_path
    assert _ir_without_source(xml_result) == _ir_without_source(mxl_from_path)
    assert xml_result.warnings == mxl_from_path.warnings
    assert xml_result.ir.meta.key == "key-signature:fifths=6;mode=unprovided"


def test_mxl_image_in_ignored_visual_subtree_fails_before_music21(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = BASIC.read_bytes().replace(
        b'<measure number="1">',
        (
            b'<measure number="1"><print><image source="file:///etc/passwd" '
            b'type="image/png"/></print>'
        ),
        1,
    )

    def adapter_must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("nested external image reached music21")

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_must_not_run)
    result = import_musicxml_bytes(_valid_mxl(root), "nested-image.mxl")

    error = _only_error(result)
    assert error.code is ImportCode.UNSAFE_XML
    assert error.location is not None
    assert error.location.part_id == "P1"
    assert error.location.measure == "1"
    assert error.location.element == "image"


@pytest.mark.parametrize("filename", ["score.musicxml", "score.XML", "作品.MUSICXML"])
def test_bytes_import_accepts_supported_case_insensitive_suffixes(filename: str) -> None:
    raw = BASIC.read_bytes()
    result = import_musicxml_bytes(raw, filename)

    assert isinstance(result, ImportSuccess)
    assert result.sha256 == hashlib.sha256(raw).hexdigest()
    assert result.provenance is not None
    assert result.provenance.source_filename == filename
    assert result.provenance.source_format == "musicxml"


def test_filename_is_escaped_before_entering_ir_provenance() -> None:
    filename = "outer%;importer=forged.xml"
    result = import_musicxml_bytes(BASIC.read_bytes(), filename)

    assert isinstance(result, ImportSuccess)
    assert result.provenance is not None
    assert result.provenance.source_filename == filename
    assert "filename=outer%25%3Bimporter%3Dforged.xml" in result.ir.meta.source
    assert "filename=outer%;importer=forged.xml" not in result.ir.meta.source


class _HostileBytes(bytes):
    def __len__(self) -> int:
        raise AssertionError("bytes subclass hook ran")


@pytest.mark.parametrize(
    "data",
    [bytearray(b"xml"), memoryview(b"xml"), "xml", _HostileBytes(b"xml")],
    ids=["bytearray", "memoryview", "str", "bytes-subclass"],
)
def test_bytes_import_requires_exact_bytes_without_running_subclass_hooks(data: object) -> None:
    diagnostic = _only_error(
        import_musicxml_bytes(data, "score.xml")  # type: ignore[arg-type]
    )
    assert diagnostic.code is ImportCode.INVALID_INPUT
    assert diagnostic.message == "data must be exact bytes"
    assert diagnostic.location is not None
    assert diagnostic.location.element == "data"


class _HostileStr(str):
    def __len__(self) -> int:
        raise AssertionError("str subclass hook ran")

    def __str__(self) -> str:
        raise AssertionError("str subclass was rendered")


@pytest.mark.parametrize("filename", [Path("score.xml"), _HostileStr("score.xml")])
def test_bytes_import_requires_exact_str_without_running_subclass_hooks(
    filename: object,
) -> None:
    diagnostic = _only_error(
        import_musicxml_bytes(BASIC.read_bytes(), filename)  # type: ignore[arg-type]
    )
    assert diagnostic.code is ImportCode.INVALID_INPUT
    assert diagnostic.message == "filename must be an exact str"
    assert diagnostic.location is not None
    assert diagnostic.location.element == "filename"


@pytest.mark.parametrize(
    ("filename", "code", "message"),
    [
        ("", ImportCode.INVALID_INPUT, "must not be empty"),
        ("/score.xml", ImportCode.INVALID_INPUT, "without path separators"),
        ("folder/score.xml", ImportCode.INVALID_INPUT, "without path separators"),
        (r"folder\score.xml", ImportCode.INVALID_INPUT, "without path separators"),
        ("C:score.xml", ImportCode.INVALID_INPUT, "Windows drive prefix"),
        ("score\x00.xml", ImportCode.INVALID_INPUT, "control or format"),
        ("score\n.xml", ImportCode.INVALID_INPUT, "control or format"),
        ("score\u202e.xml", ImportCode.INVALID_INPUT, "control or format"),
        ("\ud800.xml", ImportCode.INVALID_INPUT, "valid Unicode"),
        (".", ImportCode.INVALID_INPUT, "must not be '.' or '..'"),
        ("..", ImportCode.INVALID_INPUT, "must not be '.' or '..'"),
        (".xml", ImportCode.UNSUPPORTED_FILE_TYPE, "unsupported input suffix"),
        ("score", ImportCode.UNSUPPORTED_FILE_TYPE, "unsupported input suffix"),
        ("score.mid", ImportCode.UNSUPPORTED_FILE_TYPE, "unsupported input suffix"),
    ],
)
def test_bytes_import_rejects_non_inert_or_unsupported_filename(
    filename: str,
    code: ImportCode,
    message: str,
) -> None:
    diagnostic = _only_error(import_musicxml_bytes(BASIC.read_bytes(), filename))
    assert diagnostic.code is code
    assert message in diagnostic.message
    if filename not in {"", ".", "..", ".xml"}:
        assert filename not in diagnostic.message
    assert diagnostic.location is not None
    assert diagnostic.location.element == "filename"


@pytest.mark.parametrize(
    "filename",
    ["a" * 1021 + ".xml", "é" * 511 + ".xml"],
    ids=["code-point-lower-bound", "utf8-encoded-size"],
)
def test_bytes_import_bounds_filename_utf8_size(filename: str) -> None:
    diagnostic = _only_error(import_musicxml_bytes(BASIC.read_bytes(), filename))
    assert diagnostic.code is ImportCode.INPUT_LIMIT_EXCEEDED
    assert diagnostic.message == "filename exceeds 1024 UTF-8 bytes"
    assert filename not in diagnostic.message


def test_bad_filename_fails_before_xml_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_parse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("invalid filename reached XML parser")

    monkeypatch.setattr(musicxml_module, "_safe_parse", must_not_parse)
    diagnostic = _only_error(
        import_musicxml_bytes(BASIC.read_bytes(), "../score.musicxml")
    )
    assert diagnostic.code is ImportCode.INVALID_INPUT


def test_xml_size_limit_fails_before_xml_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_parse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("oversized XML reached XML parser")

    raw = BASIC.read_bytes()
    monkeypatch.setattr(musicxml_module, "_safe_parse", must_not_parse)
    diagnostic = _only_error(
        import_musicxml_bytes(raw, "score.xml", limits=ImportLimits(max_bytes=len(raw) - 1))
    )
    assert diagnostic.code is ImportCode.INPUT_LIMIT_EXCEEDED


def test_mxl_size_limit_fails_before_container_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def must_not_read(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("oversized MXL reached container reader")

    raw = _valid_mxl()
    monkeypatch.setattr(musicxml_module, "read_mxl_container", must_not_read)
    diagnostic = _only_error(
        import_musicxml_bytes(
            raw,
            "score.mxl",
            limits=ImportLimits(max_mxl_archive_bytes=len(raw) - 1),
        )
    )
    assert diagnostic.code is ImportCode.INPUT_LIMIT_EXCEEDED


def test_mxl_archive_uses_its_independent_outer_byte_limit() -> None:
    raw = _valid_mxl()
    result = import_musicxml_bytes(
        raw,
        "score.mxl",
        limits=ImportLimits(
            max_bytes=len(BASIC.read_bytes()),
            max_mxl_archive_bytes=len(raw),
        ),
    )
    assert isinstance(result, ImportSuccess)


def test_bytes_import_uses_a_detached_limits_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ImportLimits()
    real_validate = musicxml_module._validate_source_filename

    def mutate_source_after_barrier(filename: object) -> str | ImportFailure:
        object.__setattr__(source, "max_bytes", 0)
        return real_validate(filename)

    monkeypatch.setattr(
        musicxml_module,
        "_validate_source_filename",
        mutate_source_after_barrier,
    )
    assert isinstance(
        import_musicxml_bytes(BASIC.read_bytes(), BASIC.name, limits=source),
        ImportSuccess,
    )


def test_bytes_import_rejects_mutated_limit_scalar_before_input_hooks() -> None:
    class HostileInt(int):
        def __lt__(self, other: object) -> bool:
            raise AssertionError(f"hostile limit compared with {type(other).__name__}")

        def bit_length(self) -> int:
            raise AssertionError("hostile limit bit_length ran")

    limits = ImportLimits()
    object.__setattr__(limits, "max_bytes", HostileInt(1))
    diagnostic = _only_error(
        import_musicxml_bytes(_HostileBytes(BASIC.read_bytes()), BASIC.name, limits=limits)
    )
    assert diagnostic.code is ImportCode.INPUT_LIMIT_EXCEEDED
    assert diagnostic.location is not None
    assert diagnostic.location.element == "limits"


def test_bytes_import_does_not_call_path_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_read_path(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("bytes importer attempted a filesystem read")

    monkeypatch.setattr(musicxml_module, "_read_bounded", must_not_read_path)
    assert isinstance(
        import_musicxml_bytes(BASIC.read_bytes(), BASIC.name),
        ImportSuccess,
    )


def test_bytes_import_reports_missing_safe_xml_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import_module = import_module

    def missing(name: str) -> object:
        if name.startswith("defusedxml"):
            raise ModuleNotFoundError("No module named 'defusedxml'", name="defusedxml")
        return real_import_module(name)

    monkeypatch.setattr(musicxml_module, "import_module", missing)
    diagnostic = _only_error(import_musicxml_bytes(BASIC.read_bytes(), BASIC.name))
    assert diagnostic.code is ImportCode.MISSING_DEPENDENCY


def test_bytes_import_reports_missing_semantic_parser_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import_module = import_module

    def missing(name: str) -> object:
        if name == "music21":
            raise ModuleNotFoundError("No module named 'music21'", name="music21")
        return real_import_module(name)

    monkeypatch.setattr(music21_adapter_module, "import_module", missing)
    diagnostic = _only_error(import_musicxml_bytes(BASIC.read_bytes(), BASIC.name))
    assert diagnostic.code is ImportCode.MISSING_DEPENDENCY


def test_adapter_output_is_strictly_snapshotted_before_import_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = import_musicxml_bytes(BASIC.read_bytes(), BASIC.name)
    assert isinstance(real, ImportSuccess)
    invalid = MusicIR(
        (
            Note(
                Fraction(1 << 256),
                Fraction(1),
                60,
                "melody",
            ),
        ),
        real.ir.chords,
        real.ir.meta,
    )

    def invalid_adapter(*_args: object, **_kwargs: object) -> MusicIR:
        return invalid

    def validate_must_not_run(_ir: MusicIR) -> list[object]:
        raise AssertionError("non-snapshottable adapter output reached validate_ir")

    monkeypatch.setattr(musicxml_module, "music21_to_ir", invalid_adapter)
    monkeypatch.setattr(musicxml_module, "validate_ir", validate_must_not_run)

    diagnostic = _only_error(import_musicxml_bytes(BASIC.read_bytes(), BASIC.name))

    assert diagnostic.code is ImportCode.IR_INVALID
    assert "256-bit" in diagnostic.message
    assert diagnostic.location is not None
    assert diagnostic.location.element == "MusicIR"


@pytest.mark.parametrize("violation_count", [256, 257])
def test_adapter_validation_diagnostics_are_bounded_at_the_exact_boundary(
    monkeypatch: pytest.MonkeyPatch,
    violation_count: int,
) -> None:
    real = import_musicxml_bytes(BASIC.read_bytes(), BASIC.name)
    assert isinstance(real, ImportSuccess)

    def adapter(*_args: object, **_kwargs: object) -> MusicIR:
        return real.ir

    def many_violations(_ir: MusicIR) -> list[IRViolation]:
        return [
            IRViolation("synthetic", f"violation-{index}", None)
            for index in range(violation_count)
        ]

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter)
    monkeypatch.setattr(musicxml_module, "validate_ir", many_violations)
    result = import_musicxml_bytes(BASIC.read_bytes(), BASIC.name)

    assert isinstance(result, ImportFailure)
    assert len(result.diagnostics) == violation_count
    assert all(
        diagnostic.code is ImportCode.IR_INVALID
        for diagnostic in result.diagnostics[:256]
    )
    if violation_count == 257:
        assert result.diagnostics[-1].code is ImportCode.INPUT_LIMIT_EXCEEDED
        assert result.diagnostics[-1].location is not None
        assert result.diagnostics[-1].location.element == "diagnostics"


def test_unexpected_adapter_exception_is_typed_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_adapter(*_args: object, **_kwargs: object) -> None:
        raise ValueError("provider payload must not cross the importer boundary")

    monkeypatch.setattr(musicxml_module, "music21_to_ir", unexpected_adapter)
    diagnostic = _only_error(import_musicxml_bytes(BASIC.read_bytes(), BASIC.name))

    assert diagnostic.code is ImportCode.ADAPTER_ERROR
    assert diagnostic.message == "unexpected MusicXML adapter failure: ValueError"
    assert "provider payload" not in diagnostic.message


def test_music21_parser_exception_is_typed_and_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    music21 = import_module("music21")
    converter = music21.converter

    def rejected(*_args: object, **_kwargs: object) -> None:
        raise ValueError("parser payload must not cross the importer boundary")

    monkeypatch.setattr(converter, "parseData", rejected)
    diagnostic = _only_error(import_musicxml_bytes(BASIC.read_bytes(), BASIC.name))

    assert diagnostic.code is ImportCode.ADAPTER_ERROR
    assert diagnostic.message == (
        "music21 rejected preflight-approved canonical XML: ValueError"
    )
    assert "parser payload" not in diagnostic.message


def test_bytes_import_rejects_external_entity_without_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("bytes import attempted network access")

    raw = b"""<?xml version="1.0"?>
<!DOCTYPE score-partwise [<!ENTITY xxe SYSTEM "https://example.invalid/secret">]>
<score-partwise version="4.0"><work><work-title>&xxe;</work-title></work></score-partwise>
"""
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    diagnostic = _only_error(import_musicxml_bytes(raw, "entity.xml"))
    assert diagnostic.code is ImportCode.UNSAFE_XML


def test_bytes_import_returns_typed_failure_for_malformed_mxl() -> None:
    diagnostic = _only_error(import_musicxml_bytes(b"PK\x03\x04", "broken.mxl"))
    assert diagnostic.code is ImportCode.MXL_MALFORMED_ARCHIVE


def test_path_reader_fails_closed_when_open_file_grows_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "changing.musicxml"
    path.write_bytes(BASIC.read_bytes())
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
    diagnostic = _only_error(import_musicxml(path))
    assert mutated
    assert diagnostic.code is ImportCode.FILE_READ_ERROR
    assert diagnostic.message == "MusicXML file changed while it was being read"


@pytest.mark.skipif(os.name != "posix", reason="POSIX unlink keeps the open descriptor alive")
def test_path_replacement_after_atomic_open_cannot_redirect_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "replace.musicxml"
    original = BASIC.read_bytes()
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
            path.write_bytes(b"not MusicXML")
            replaced = True
        return descriptor

    monkeypatch.setattr(os, "open", replace_after_open)
    result = import_musicxml(path)
    assert replaced
    assert isinstance(result, ImportSuccess)
    assert result.sha256 == hashlib.sha256(original).hexdigest()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_path_reader_rejects_fifo_without_blocking(tmp_path: Path) -> None:
    path = tmp_path / "pipe.musicxml"
    os.mkfifo(path)
    diagnostic = _only_error(import_musicxml(path))
    assert diagnostic.code is ImportCode.NOT_A_FILE
