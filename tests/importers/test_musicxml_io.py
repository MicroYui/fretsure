from __future__ import annotations

import hashlib
import socket
import urllib.request
from pathlib import Path

import pytest

from fretsure.importers import (
    DEFAULT_LIMITS,
    DiagnosticSeverity,
    ImportCode,
    ImportFailure,
    ImportLimits,
    ImportSuccess,
    import_musicxml,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "musicxml"
BASIC = FIXTURES / "supported_basic.musicxml"


def _only_code(result: ImportSuccess | ImportFailure) -> ImportCode:
    assert isinstance(result, ImportFailure)
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.severity is DiagnosticSeverity.ERROR
    assert not hasattr(result, "ir")
    return diagnostic.code


def test_public_contract_has_bounded_default_limits() -> None:
    assert DEFAULT_LIMITS == ImportLimits()
    assert DEFAULT_LIMITS.max_bytes > 0
    assert DEFAULT_LIMITS.max_xml_depth > 0
    assert DEFAULT_LIMITS.max_elements > 0
    assert DEFAULT_LIMITS.max_measures > 0
    assert DEFAULT_LIMITS.max_notes > 0
    assert DEFAULT_LIMITS.max_harmonies > 0
    assert DEFAULT_LIMITS.max_decimal_chars > 0
    assert DEFAULT_LIMITS.max_mxl_archive_bytes > 0
    assert DEFAULT_LIMITS.max_mxl_members > 0
    assert DEFAULT_LIMITS.max_mxl_central_directory_bytes > 0
    assert DEFAULT_LIMITS.max_mxl_member_name_bytes > 0
    assert DEFAULT_LIMITS.max_mxl_path_depth > 0
    assert DEFAULT_LIMITS.max_mxl_container_bytes > 0
    assert DEFAULT_LIMITS.max_mxl_member_bytes > 0
    assert DEFAULT_LIMITS.max_mxl_total_uncompressed_bytes > 0
    assert DEFAULT_LIMITS.max_mxl_member_ratio > 0
    assert DEFAULT_LIMITS.max_mxl_total_ratio > 0


@pytest.mark.parametrize("value", [True, -1, 1.5, "1", 1 << 63])
def test_import_limits_require_exact_bounded_integers(value: object) -> None:
    with pytest.raises(ValueError, match="max_bytes"):
        ImportLimits(max_bytes=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("missing.musicxml", ImportCode.FILE_NOT_FOUND),
        ("song.mid", ImportCode.UNSUPPORTED_FILE_TYPE),
    ],
)
def test_path_failures_are_typed(tmp_path: Path, name: str, expected: ImportCode) -> None:
    path = tmp_path / name
    if path.suffix == ".mid":
        path.write_bytes(b"not relevant")
    assert _only_code(import_musicxml(path)) is expected


def test_directory_is_not_accepted_as_a_file(tmp_path: Path) -> None:
    directory = tmp_path / "score.musicxml"
    directory.mkdir()
    assert _only_code(import_musicxml(directory)) is ImportCode.NOT_A_FILE


def test_non_zip_mxl_is_a_typed_archive_failure(tmp_path: Path) -> None:
    path = tmp_path / "score.mxl"
    path.write_bytes(b"PK\x03\x04")

    assert _only_code(import_musicxml(path)) is ImportCode.MXL_MALFORMED_ARCHIVE
    assert ImportCode.COMPRESSED_MXL_UNSUPPORTED.value == "COMPRESSED_MXL_UNSUPPORTED"


def test_musicxml_and_xml_extensions_are_case_insensitive(tmp_path: Path) -> None:
    raw = BASIC.read_bytes()
    for filename in ("score.MUSICXML", "score.XML"):
        path = tmp_path / filename
        path.write_bytes(raw)
        assert isinstance(import_musicxml(path), ImportSuccess)


def test_size_limit_is_rejection_not_truncation(tmp_path: Path) -> None:
    path = tmp_path / "large.musicxml"
    path.write_bytes(BASIC.read_bytes())
    result = import_musicxml(path, limits=ImportLimits(max_bytes=32))
    assert _only_code(result) is ImportCode.INPUT_LIMIT_EXCEEDED


def test_maximum_valid_file_limit_does_not_overflow_read_size() -> None:
    result = import_musicxml(BASIC, limits=ImportLimits(max_bytes=(1 << 63) - 1))
    assert isinstance(result, ImportSuccess)


def test_malformed_xml_is_typed(tmp_path: Path) -> None:
    path = tmp_path / "broken.musicxml"
    path.write_text('<score-partwise version="4.0"><part-list>', encoding="utf-8")
    assert _only_code(import_musicxml(path)) is ImportCode.MALFORMED_XML


def test_entity_declaration_is_rejected_without_expansion(tmp_path: Path) -> None:
    path = tmp_path / "entity.musicxml"
    path.write_text(
        """<?xml version="1.0"?>
<!DOCTYPE score-partwise [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<score-partwise version="4.0"><work><work-title>&xxe;</work-title></work></score-partwise>
""",
        encoding="utf-8",
    )
    assert _only_code(import_musicxml(path)) is ImportCode.UNSAFE_XML


def test_standard_external_musicxml_doctype_is_allowed_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_network(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("MusicXML import attempted network access")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    assert isinstance(import_musicxml(BASIC), ImportSuccess)


@pytest.mark.parametrize(
    ("xml", "expected"),
    [
        (
            '<score-timewise version="4.0"><part-list/></score-timewise>',
            ImportCode.UNSUPPORTED_ROOT,
        ),
        (
            '<score-partwise version="2.0"><part-list/></score-partwise>',
            ImportCode.UNSUPPORTED_VERSION,
        ),
        (
            '<score-partwise xmlns="https://example.invalid/music" version="4.0"/>',
            ImportCode.UNSUPPORTED_NAMESPACE,
        ),
    ],
)
def test_root_version_and_namespace_are_checked_before_semantic_parse(
    tmp_path: Path, xml: str, expected: ImportCode
) -> None:
    path = tmp_path / "root.musicxml"
    path.write_text(xml, encoding="utf-8")
    assert _only_code(import_musicxml(path)) is expected


def test_mixed_foreign_element_namespace_is_rejected(tmp_path: Path) -> None:
    raw = BASIC.read_text(encoding="utf-8")
    mixed = raw.replace(
        '<score-partwise version="4.0">',
        '<score-partwise version="4.0" xmlns:foreign="https://example.invalid/music">',
    ).replace("<part-list>", "<foreign:extension/><part-list>")
    path = tmp_path / "mixed.musicxml"
    path.write_text(mixed, encoding="utf-8")
    assert _only_code(import_musicxml(path)) is ImportCode.UNSUPPORTED_NAMESPACE


def test_depth_and_element_limits_apply_during_safe_parse() -> None:
    depth_result = import_musicxml(BASIC, limits=ImportLimits(max_xml_depth=2))
    element_result = import_musicxml(BASIC, limits=ImportLimits(max_elements=4))
    assert _only_code(depth_result) is ImportCode.INPUT_LIMIT_EXCEEDED
    assert _only_code(element_result) is ImportCode.INPUT_LIMIT_EXCEEDED


def test_missing_defusedxml_extra_is_a_typed_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from fretsure.importers import musicxml

    real_import_module = musicxml.import_module

    def missing(name: str):  # type: ignore[no-untyped-def]
        if name.startswith("defusedxml"):
            raise ModuleNotFoundError("No module named 'defusedxml'", name="defusedxml")
        return real_import_module(name)

    monkeypatch.setattr(musicxml, "import_module", missing)
    assert _only_code(import_musicxml(BASIC)) is ImportCode.MISSING_DEPENDENCY


def test_missing_music21_extra_is_a_typed_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from fretsure.importers import _music21_adapter

    real_import_module = _music21_adapter.import_module

    def missing(name: str):  # type: ignore[no-untyped-def]
        if name == "music21":
            raise ModuleNotFoundError("No module named 'music21'", name="music21")
        return real_import_module(name)

    monkeypatch.setattr(_music21_adapter, "import_module", missing)
    assert _only_code(import_musicxml(BASIC)) is ImportCode.MISSING_DEPENDENCY


def test_sha256_is_over_original_bytes() -> None:
    result = import_musicxml(BASIC)
    assert isinstance(result, ImportSuccess)
    source_hash = hashlib.sha256(BASIC.read_bytes()).hexdigest()
    assert result.sha256 == source_hash
    assert result.provenance is not None
    assert result.provenance.source_format == "musicxml"
    assert result.provenance.raw_sha256 == source_hash
    assert result.provenance.root_sha256 == source_hash
    assert result.provenance.root_member is None
    assert result.provenance.container_version is None
