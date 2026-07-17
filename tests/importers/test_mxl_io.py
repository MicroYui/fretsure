from __future__ import annotations

import builtins
import hashlib
import io
import socket
import stat
import struct
import urllib.request
import warnings
import zipfile
from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path

import pytest

import fretsure.importers.musicxml as musicxml_module
from fretsure.cli import main as cli_main
from fretsure.importers import (
    DEFAULT_LIMITS,
    DiagnosticSeverity,
    ImportCode,
    ImportFailure,
    ImportLimits,
    ImportSuccess,
    import_musicxml,
)
from fretsure.importers._mxl_container import read_mxl_container

FIXTURES = Path(__file__).parents[1] / "fixtures" / "musicxml"
BASIC = FIXTURES / "supported_basic.musicxml"

IMPORTER_VERSION = "musicxml@0.3.0"
CONTAINER_VERSION = "mxl-container@0.1.0"
ROOT_MEDIA_TYPE = "application/vnd.recordare.musicxml+xml"
MIMETYPE = b"application/vnd.recordare.musicxml"

_EOCD = b"PK\x05\x06"
_CENTRAL = b"PK\x01\x02"
_LOCAL = b"PK\x03\x04"


@dataclass(frozen=True)
class _Entry:
    name: str
    data: bytes
    compression: int = zipfile.ZIP_STORED
    mode: int | None = None
    comment: bytes = b""
    extra: bytes = b""


@dataclass(frozen=True)
class _CentralRecord:
    offset: int
    name: bytes
    flags: int
    method: int
    crc32: int
    compressed_size: int
    uncompressed_size: int
    local_offset: int


class _UnseekableBytesIO(BytesIO):
    """Small write sink that makes stdlib zipfile emit data descriptors."""

    def seek(self, offset: int, whence: int = 0) -> int:
        raise OSError(f"unseekable output: {offset=}, {whence=}")


def _container_xml(
    root_path: str = "score.musicxml", media_type: str | None = ROOT_MEDIA_TYPE
) -> bytes:
    media_attribute = "" if media_type is None else f' media-type="{media_type}"'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<container><rootfiles>"
        f'<rootfile full-path="{root_path}"{media_attribute}/>'
        "</rootfiles></container>"
    ).encode()


def _extra_field(field_id: int, payload: bytes) -> bytes:
    return struct.pack("<HH", field_id, len(payload)) + payload


def _archive(
    entries: list[_Entry],
    *,
    archive_comment: bytes = b"",
    use_data_descriptors: bool = False,
) -> bytes:
    output = _UnseekableBytesIO() if use_data_descriptors else BytesIO()
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Duplicate name:.*", category=UserWarning)
        with zipfile.ZipFile(output, "w", allowZip64=True) as archive:
            archive.comment = archive_comment
            for entry in entries:
                info = zipfile.ZipInfo(entry.name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = entry.compression
                info.create_system = 3
                mode = entry.mode
                if mode is None:
                    mode = (
                        stat.S_IFDIR | 0o755
                        if entry.name.endswith("/")
                        else stat.S_IFREG | 0o644
                    )
                info.external_attr = mode << 16
                if stat.S_ISDIR(mode):
                    info.external_attr |= 0x10
                info.comment = entry.comment
                info.extra = entry.extra
                archive.writestr(info, entry.data)
    return output.getvalue()


def _mxl(
    *,
    root_path: str = "score.musicxml",
    root: bytes | None = None,
    compression: int = zipfile.ZIP_DEFLATED,
    media_type: str | None = ROOT_MEDIA_TYPE,
    container_xml: bytes | None = None,
    mimetype: bytes | None = None,
    mimetype_compression: int = zipfile.ZIP_STORED,
    mimetype_first: bool = True,
    extras: tuple[_Entry, ...] = (),
    use_data_descriptors: bool = False,
) -> bytes:
    root_bytes = BASIC.read_bytes() if root is None else root
    container_bytes = (
        _container_xml(root_path, media_type) if container_xml is None else container_xml
    )
    mimetype_entry = (
        []
        if mimetype is None
        else [_Entry("mimetype", mimetype, mimetype_compression)]
    )
    content = [
        _Entry("META-INF/container.xml", container_bytes, compression),
        _Entry(root_path, root_bytes, compression),
        *extras,
    ]
    entries = mimetype_entry + content if mimetype_first else content + mimetype_entry
    return _archive(entries, use_data_descriptors=use_data_descriptors)


def _write(tmp_path: Path, raw: bytes, filename: str = "score.mxl") -> Path:
    path = tmp_path / filename
    path.write_bytes(raw)
    return path


def _success(path: Path, *, limits: ImportLimits = DEFAULT_LIMITS) -> ImportSuccess:
    result = import_musicxml(path, limits=limits)
    assert isinstance(result, ImportSuccess), getattr(result, "diagnostics", None)
    return result


def _only_error_code(result: ImportSuccess | ImportFailure) -> ImportCode:
    assert isinstance(result, ImportFailure)
    errors = tuple(
        diagnostic.code
        for diagnostic in result.diagnostics
        if diagnostic.severity is DiagnosticSeverity.ERROR
    )
    assert len(errors) == 1, result.diagnostics
    assert not hasattr(result, "ir")
    return errors[0]


def _assert_code(
    path: Path, expected: ImportCode, *, limits: ImportLimits = DEFAULT_LIMITS
) -> None:
    assert _only_error_code(import_musicxml(path, limits=limits)) is expected


def _eocd(raw: bytes) -> tuple[int, int, int, int]:
    offset = raw.rfind(_EOCD)
    assert offset >= 0
    (
        signature,
        _disk_number,
        _central_disk,
        _entries_on_disk,
        entries,
        central_size,
        central_offset,
        _comment_size,
    ) = struct.unpack_from("<4s4H2LH", raw, offset)
    assert signature == _EOCD
    return offset, entries, central_offset, central_size


def _central_records(raw: bytes) -> tuple[_CentralRecord, ...]:
    _eocd_offset, _entries, central_offset, central_size = _eocd(raw)
    cursor = central_offset
    end = central_offset + central_size
    records: list[_CentralRecord] = []
    while cursor < end:
        fields = struct.unpack_from("<4s6H3L5H2L", raw, cursor)
        assert fields[0] == _CENTRAL
        name_size, extra_size, comment_size = fields[10:13]
        name = raw[cursor + 46 : cursor + 46 + name_size]
        records.append(
            _CentralRecord(
                offset=cursor,
                name=name,
                flags=fields[3],
                method=fields[4],
                crc32=fields[7],
                compressed_size=fields[8],
                uncompressed_size=fields[9],
                local_offset=fields[16],
            )
        )
        cursor += 46 + name_size + extra_size + comment_size
    assert cursor == end
    return tuple(records)


def _record(raw: bytes, name: str) -> _CentralRecord:
    encoded = name.encode("utf-8")
    matches = [record for record in _central_records(raw) if record.name == encoded]
    assert len(matches) == 1
    return matches[0]


def _rename_raw_member(raw: bytes, old: str, new: bytes) -> bytes:
    data = bytearray(raw)
    record = _record(raw, old)
    assert len(record.name) == len(new)
    data[record.offset + 46 : record.offset + 46 + len(new)] = new
    assert data[record.local_offset : record.local_offset + 4] == _LOCAL
    local_name_size = struct.unpack_from("<H", data, record.local_offset + 26)[0]
    assert local_name_size == len(new)
    local_name_offset = record.local_offset + 30
    data[local_name_offset : local_name_offset + len(new)] = new
    return bytes(data)


def _patch_member_method(raw: bytes, name: str, method: int) -> bytes:
    data = bytearray(raw)
    record = _record(raw, name)
    struct.pack_into("<H", data, record.offset + 10, method)
    struct.pack_into("<H", data, record.local_offset + 8, method)
    return bytes(data)


def _patch_member_flags(raw: bytes, name: str, flags: int) -> bytes:
    data = bytearray(raw)
    record = _record(raw, name)
    struct.pack_into("<H", data, record.offset + 8, record.flags | flags)
    struct.pack_into("<H", data, record.local_offset + 6, record.flags | flags)
    return bytes(data)


def _corrupt_stored_member(raw: bytes, name: str) -> bytes:
    data = bytearray(raw)
    record = _record(raw, name)
    assert record.method == zipfile.ZIP_STORED
    assert record.compressed_size > 0
    assert data[record.local_offset : record.local_offset + 4] == _LOCAL
    name_size, extra_size = struct.unpack_from("<2H", data, record.local_offset + 26)
    payload_offset = record.local_offset + 30 + name_size + extra_size
    data[payload_offset] ^= 0x01
    return bytes(data)


def _patch_eocd_counts(raw: bytes, count: int) -> bytes:
    data = bytearray(raw)
    offset, _entries, _central_offset, _central_size = _eocd(raw)
    struct.pack_into("<H", data, offset + 8, count)
    struct.pack_into("<H", data, offset + 10, count)
    return bytes(data)


def _local_data_start(raw: bytes, record: _CentralRecord) -> int:
    assert raw[record.local_offset : record.local_offset + 4] == _LOCAL
    name_size, extra_size = struct.unpack_from("<2H", raw, record.local_offset + 26)
    return record.local_offset + 30 + name_size + extra_size


def _splice_local_area(raw: bytes, start: int, end: int, replacement: bytes) -> bytes:
    """Splice bytes before the central directory and repair referenced offsets."""

    eocd_offset, _entries, central_offset, _central_size = _eocd(raw)
    assert 0 <= start <= end <= central_offset
    records = _central_records(raw)
    delta = len(replacement) - (end - start)
    data = bytearray(raw[:start] + replacement + raw[end:])

    def shifted(offset: int) -> int:
        if offset < start:
            return offset
        if offset >= end:
            return offset + delta
        raise AssertionError("a ZIP record offset points inside the test splice")

    for record in records:
        central_record_offset = shifted(record.offset)
        struct.pack_into("<L", data, central_record_offset + 42, shifted(record.local_offset))
    struct.pack_into("<L", data, shifted(eocd_offset) + 16, shifted(central_offset))
    return bytes(data)


def _compressed_payload(raw: bytes, name: str) -> bytes:
    record = _record(raw, name)
    start = _local_data_start(raw, record)
    return raw[start : start + record.compressed_size]


def _replace_compressed_payload(raw: bytes, name: str, payload: bytes) -> bytes:
    record = _record(raw, name)
    start = _local_data_start(raw, record)
    end = start + record.compressed_size
    changed = bytearray(_splice_local_area(raw, start, end, payload))
    updated = _record(bytes(changed), name)
    struct.pack_into("<L", changed, updated.offset + 20, len(payload))
    struct.pack_into("<L", changed, updated.local_offset + 18, len(payload))
    return bytes(changed)


def _descriptor_offset(raw: bytes, name: str) -> int:
    record = _record(raw, name)
    assert record.flags & 0x0008
    return _local_data_start(raw, record) + record.compressed_size


def _patch_local_metadata(raw: bytes, name: str, field: str) -> bytes:
    data = bytearray(raw)
    record = _record(raw, name)
    offsets = {
        "flags": (6, "H"),
        "method": (8, "H"),
        "crc": (14, "L"),
        "compressed_size": (18, "L"),
        "uncompressed_size": (22, "L"),
    }
    offset, format_code = offsets[field]
    current = struct.unpack_from(f"<{format_code}", data, record.local_offset + offset)[0]
    struct.pack_into(f"<{format_code}", data, record.local_offset + offset, current ^ 1)
    return bytes(data)


def _patch_declared_uncompressed_size(raw: bytes, name: str, size: int) -> bytes:
    data = bytearray(raw)
    record = _record(raw, name)
    struct.pack_into("<L", data, record.offset + 24, size)
    struct.pack_into("<L", data, record.local_offset + 22, size)
    return bytes(data)


def _without_source(result: ImportSuccess) -> object:
    return replace(result.ir, meta=replace(result.ir.meta, source=""))


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_valid_stored_and_deflated_mxl_match_uncompressed_music_ir(
    tmp_path: Path, compression: int
) -> None:
    result = _success(_write(tmp_path, _mxl(compression=compression)))
    uncompressed = _success(BASIC)
    assert _without_source(result) == _without_source(uncompressed)
    assert result.importer_version == IMPORTER_VERSION
    assert result.rootfile_path == "score.musicxml"


def test_nested_root_and_uppercase_mxl_extension_are_supported(tmp_path: Path) -> None:
    root_path = "scores/nested/lead.musicxml"
    result = _success(_write(tmp_path, _mxl(root_path=root_path), "SCORE.MXL"))
    assert result.rootfile_path == root_path
    assert result.provenance is not None
    assert result.provenance.root_member == root_path


def test_missing_rootfile_media_type_is_a_stable_warning(tmp_path: Path) -> None:
    result = _success(_write(tmp_path, _mxl(media_type=None)))
    matching = [
        warning
        for warning in result.warnings
        if warning.code is ImportCode.MXL_ROOTFILE_MEDIA_TYPE_UNPROVIDED
    ]
    assert len(matching) == 1
    assert matching[0].severity is DiagnosticSeverity.WARNING
    assert matching[0].location is not None
    assert matching[0].location.archive_member == "META-INF/container.xml"


def test_mode_unprovided_warning_follows_container_warning(tmp_path: Path) -> None:
    root = BASIC.read_bytes().replace(
        b"<key><fifths>0</fifths><mode>major</mode></key>",
        b"<key><fifths>0</fifths></key>",
        1,
    )
    result = _success(_write(tmp_path, _mxl(root=root, media_type=None)))

    assert [warning.code for warning in result.warnings] == [
        ImportCode.MXL_ROOTFILE_MEDIA_TYPE_UNPROVIDED,
        ImportCode.KEY_MODE_UNPROVIDED,
    ]


def test_optional_standard_mimetype_member_is_accepted(tmp_path: Path) -> None:
    result = _success(_write(tmp_path, _mxl(mimetype=MIMETYPE)))
    assert result.rootfile_path == "score.musicxml"


def test_archive_hash_and_structured_provenance_bind_raw_and_root_bytes(
    tmp_path: Path,
) -> None:
    root_path = "scores/My Etude.musicxml"
    root = BASIC.read_bytes()
    raw = _mxl(root_path=root_path, root=root, mimetype=MIMETYPE)
    result = _success(_write(tmp_path, raw, "submitted.mxl"))

    raw_hash = hashlib.sha256(raw).hexdigest()
    root_hash = hashlib.sha256(root).hexdigest()
    assert result.importer_version == IMPORTER_VERSION
    assert result.sha256 == raw_hash
    assert result.provenance is not None
    assert result.provenance.source_filename == "submitted.mxl"
    assert result.provenance.source_format == "mxl"
    assert result.provenance.raw_sha256 == raw_hash
    assert result.provenance.root_member == root_path
    assert result.provenance.root_sha256 == root_hash
    assert result.provenance.container_version == CONTAINER_VERSION
    assert raw_hash in result.ir.meta.source
    assert root_hash in result.ir.meta.source
    assert IMPORTER_VERSION in result.ir.meta.source
    assert CONTAINER_VERSION in result.ir.meta.source
    assert "root_member=scores/My%20Etude.musicxml" in result.ir.meta.source


def test_provenance_percent_escapes_field_delimiters(tmp_path: Path) -> None:
    root_path = "scores/root%;container=forged.musicxml"
    raw = _mxl(root_path=root_path)
    result = _success(_write(tmp_path, raw, "outer%;importer=forged.mxl"))

    source = result.ir.meta.source
    assert "filename=outer%25%3Bimporter%3Dforged.mxl" in source
    assert "root_member=scores/root%25%3Bcontainer%3Dforged.musicxml" in source
    assert source.count(";importer=") == 1
    assert source.count(";container=") == 1


def test_only_container_selected_root_is_parsed_as_musicxml(tmp_path: Path) -> None:
    decoy = b'<score-timewise version="4.0"><not-even-valid-for-this-importer/></score-timewise>'
    raw = _mxl(extras=(_Entry("decoy.musicxml", decoy, zipfile.ZIP_DEFLATED),))
    result = _success(_write(tmp_path, raw))
    expected = _success(BASIC)
    assert _without_source(result) == _without_source(expected)


def test_non_zip_input_is_a_typed_archive_failure(tmp_path: Path) -> None:
    _assert_code(
        _write(tmp_path, b"this is not a zip archive"),
        ImportCode.MXL_MALFORMED_ARCHIVE,
    )


@pytest.mark.parametrize("comment_kind", ["archive", "member"])
def test_zip_comments_are_rejected(tmp_path: Path, comment_kind: str) -> None:
    if comment_kind == "archive":
        raw = _archive(
            [
                _Entry("META-INF/container.xml", _container_xml()),
                _Entry("score.musicxml", BASIC.read_bytes()),
            ],
            archive_comment=b"not permitted",
        )
    else:
        raw = _mxl(extras=(_Entry("cover.txt", b"x", comment=b"not permitted"),))
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED)


def test_trailing_junk_after_eocd_is_rejected(tmp_path: Path) -> None:
    _assert_code(
        _write(tmp_path, _mxl() + b"trailing-junk"),
        ImportCode.MXL_MALFORMED_ARCHIVE,
    )


def test_self_extracting_archive_prefix_is_rejected(tmp_path: Path) -> None:
    raw = _splice_local_area(_mxl(), 0, 0, b"MZ\x90\x00SFX-prefix")
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED)


def test_gap_between_local_records_is_rejected(tmp_path: Path) -> None:
    raw = _mxl()
    second = sorted(_central_records(raw), key=lambda record: record.local_offset)[1]
    raw = _splice_local_area(raw, second.local_offset, second.local_offset, b"gap")
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED)


def test_overlapping_local_record_offsets_are_rejected(tmp_path: Path) -> None:
    raw = _mxl()
    second = sorted(_central_records(raw), key=lambda record: record.local_offset)[1]
    data = bytearray(raw)
    struct.pack_into("<L", data, second.offset + 42, second.local_offset - 1)
    _assert_code(_write(tmp_path, bytes(data)), ImportCode.MXL_MALFORMED_ARCHIVE)


def test_forged_low_eocd_entry_count_is_rejected(tmp_path: Path) -> None:
    raw = _patch_eocd_counts(_mxl(), 1)
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_MALFORMED_ARCHIVE)


def test_forged_high_count_exposes_truncated_central_entry(tmp_path: Path) -> None:
    raw = _mxl()
    raw = _patch_eocd_counts(raw, len(_central_records(raw)) + 1)
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_MALFORMED_ARCHIVE)


def test_central_variable_fields_cannot_overrun_directory_bounds(tmp_path: Path) -> None:
    raw = _mxl()
    data = bytearray(raw)
    record = _record(raw, "score.musicxml")
    struct.pack_into("<H", data, record.offset + 28, 0xFFFF)
    _assert_code(_write(tmp_path, bytes(data)), ImportCode.MXL_MALFORMED_ARCHIVE)


def test_local_and_central_filename_mismatch_is_rejected(tmp_path: Path) -> None:
    raw = _mxl()
    data = bytearray(raw)
    record = _record(raw, "score.musicxml")
    local_name_offset = record.local_offset + 30
    data[local_name_offset] = ord("X")
    _assert_code(_write(tmp_path, bytes(data)), ImportCode.MXL_MALFORMED_ARCHIVE)


@pytest.mark.parametrize(
    "field", ["flags", "method", "compressed_size", "uncompressed_size", "crc"]
)
def test_local_and_central_flags_size_and_crc_must_match(
    tmp_path: Path, field: str
) -> None:
    raw = _patch_local_metadata(_mxl(), "score.musicxml", field)
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_MALFORMED_ARCHIVE)


@pytest.mark.parametrize("delta", [-1, 1])
def test_declared_and_actual_uncompressed_size_must_match(
    tmp_path: Path, delta: int
) -> None:
    name = "payload.bin"
    raw = _mxl(extras=(_Entry(name, b"payload-" * 100, zipfile.ZIP_DEFLATED),))
    record = _record(raw, name)
    raw = _patch_declared_uncompressed_size(raw, name, record.uncompressed_size + delta)
    result = import_musicxml(_write(tmp_path, raw))
    assert _only_error_code(result) in {
        ImportCode.MXL_MALFORMED_ARCHIVE,
        ImportCode.MXL_CRC_MISMATCH,
    }


def test_zip64_sentinel_is_rejected_as_an_unsupported_archive_feature(
    tmp_path: Path,
) -> None:
    raw = _patch_eocd_counts(_mxl(), 0xFFFF)
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED)


def test_multi_disk_eocd_is_rejected(tmp_path: Path) -> None:
    raw = _mxl()
    data = bytearray(raw)
    offset, _entries, _central_offset, _central_size = _eocd(raw)
    struct.pack_into("<H", data, offset + 4, 1)
    _assert_code(
        _write(tmp_path, bytes(data)),
        ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
    )


def test_central_directory_digital_signature_is_rejected(tmp_path: Path) -> None:
    raw = _mxl()
    eocd_offset, _entries, _central_offset, central_size = _eocd(raw)
    signature = b"PK\x05\x05\x00\x00"
    changed = bytearray(raw[:eocd_offset] + signature + raw[eocd_offset:])
    struct.pack_into(
        "<L",
        changed,
        eocd_offset + len(signature) + 12,
        central_size + len(signature),
    )
    _assert_code(
        _write(tmp_path, bytes(changed)),
        ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
    )


def test_forged_eocd_count_fails_before_zipfile_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, _patch_eocd_counts(_mxl(), 1))

    def forbidden_init(self: object, *args: object, **kwargs: object) -> None:
        raise AssertionError("ZipFile constructed before EOCD/central preflight")

    monkeypatch.setattr(zipfile.ZipFile, "__init__", forbidden_init)
    _assert_code(path, ImportCode.MXL_MALFORMED_ARCHIVE)


def test_unknown_compression_method_is_typed(tmp_path: Path) -> None:
    raw = _mxl(extras=(_Entry("attachment.bin", b"content"),))
    raw = _patch_member_method(raw, "attachment.bin", 98)
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_COMPRESSION_UNSUPPORTED)


def test_winzip_aes_pseudo_method_is_typed_as_encryption(tmp_path: Path) -> None:
    raw = _mxl(extras=(_Entry("attachment.bin", b"content"),))
    raw = _patch_member_method(raw, "attachment.bin", 99)
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_ENCRYPTED_MEMBER)


def test_patched_data_flag_is_rejected(tmp_path: Path) -> None:
    raw = _mxl(extras=(_Entry("patched.bin", b"content"),))
    raw = _patch_member_flags(raw, "patched.bin", 0x0020)
    _assert_code(
        _write(tmp_path, raw),
        ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
    )


@pytest.mark.parametrize("mutation", ["truncated", "trailing"])
def test_truncated_and_trailing_deflate_streams_are_rejected(
    tmp_path: Path, mutation: str
) -> None:
    name = "compressed-attachment.bin"
    raw = _mxl(extras=(_Entry(name, b"payload-" * 100, zipfile.ZIP_DEFLATED),))
    payload = _compressed_payload(raw, name)
    assert len(payload) > 2
    changed = payload[:-1] if mutation == "truncated" else payload + b"\x03\x00"
    raw = _replace_compressed_payload(raw, name, changed)
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_MALFORMED_ARCHIVE)


@pytest.mark.parametrize("signed", [True, False])
def test_signed_and_unsigned_data_descriptors_are_supported(
    tmp_path: Path, signed: bool
) -> None:
    raw = _mxl(use_data_descriptors=True)
    descriptor = _descriptor_offset(raw, "score.musicxml")
    assert raw[descriptor : descriptor + 4] == b"PK\x07\x08"
    if not signed:
        raw = _splice_local_area(raw, descriptor, descriptor + 4, b"")
    assert isinstance(import_musicxml(_write(tmp_path, raw)), ImportSuccess)


@pytest.mark.parametrize("mutation", ["signature", "value", "size"])
def test_malformed_data_descriptors_are_rejected(
    tmp_path: Path, mutation: str
) -> None:
    raw = _mxl(use_data_descriptors=True)
    descriptor = _descriptor_offset(raw, "score.musicxml")
    if mutation == "size":
        raw = _splice_local_area(raw, descriptor, descriptor + 1, b"")
    else:
        data = bytearray(raw)
        patch_offset = descriptor if mutation == "signature" else descriptor + 4
        data[patch_offset] ^= 0x01
        raw = bytes(data)
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_MALFORMED_ARCHIVE)


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "/absolute.xml",
        "../escape.xml",
        "nested/../escape.xml",
        "nested/./score.xml",
        "nested//score.xml",
        "nested\\score.xml",
        "C:/score.xml",
        "control\x01.xml",
    ],
)
def test_unsafe_member_paths_are_rejected(tmp_path: Path, unsafe_name: str) -> None:
    raw = _mxl(extras=(_Entry(unsafe_name, b"unused"),))
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_UNSAFE_MEMBER_PATH)


def test_raw_nul_in_member_name_is_rejected_before_zipfile_truncation(
    tmp_path: Path,
) -> None:
    raw = _mxl(extras=(_Entry("bad0.xml", b"unused"),))
    raw = _rename_raw_member(raw, "bad0.xml", b"bad\x00.xml")
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_UNSAFE_MEMBER_PATH)


def test_cp437_member_name_is_decoded_consistently(tmp_path: Path) -> None:
    raw = _mxl(extras=(_Entry("cafX.bin", b"unused"),))
    raw = _rename_raw_member(raw, "cafX.bin", b"caf\x82.bin")
    assert isinstance(import_musicxml(_write(tmp_path, raw)), ImportSuccess)


def test_exact_duplicate_member_is_rejected(tmp_path: Path) -> None:
    raw = _mxl(
        extras=(
            _Entry("attachment.bin", b"first"),
            _Entry("attachment.bin", b"second"),
        )
    )
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_DUPLICATE_MEMBER)


@pytest.mark.parametrize(
    "names",
    [
        ("café.xml", "cafe\N{COMBINING ACUTE ACCENT}.xml"),
        ("Assets/Cover.PNG", "assets/cover.png"),
    ],
)
def test_unicode_nfc_and_casefold_aliases_are_rejected(
    tmp_path: Path, names: tuple[str, str]
) -> None:
    raw = _mxl(extras=(_Entry(names[0], b"first"), _Entry(names[1], b"second")))
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_NORMALIZED_COLLISION)


@pytest.mark.parametrize("with_directory_entry", [False, True])
def test_file_prefix_and_file_directory_aliases_are_rejected(
    tmp_path: Path, with_directory_entry: bool
) -> None:
    second = _Entry("assets/", b"") if with_directory_entry else _Entry("assets/cover.png", b"x")
    raw = _mxl(extras=(_Entry("assets", b"regular file"), second))
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_NORMALIZED_COLLISION)


def test_symlink_member_is_rejected(tmp_path: Path) -> None:
    symlink_mode = stat.S_IFLNK | 0o777
    raw = _mxl(extras=(_Entry("link", b"score.musicxml", mode=symlink_mode),))
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_MEMBER_TYPE_UNSUPPORTED)


@pytest.mark.parametrize("encryption_flag", [0x0001, 0x0040])
def test_encrypted_and_strong_encrypted_flags_are_rejected(
    tmp_path: Path, encryption_flag: int
) -> None:
    raw = _mxl(extras=(_Entry("private.bin", b"secret"),))
    raw = _patch_member_flags(raw, "private.bin", encryption_flag)
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_ENCRYPTED_MEMBER)


@pytest.mark.parametrize("field_id", [0xCAFE, 0x7075, 0x0001, 0x9901])
def test_unknown_path_override_and_zip64_extra_fields_fail_before_zipfile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field_id: int,
) -> None:
    extra = _extra_field(field_id, b"\x00" * 8)
    path = _write(tmp_path, _mxl(extras=(_Entry("metadata.bin", b"x", extra=extra),)))

    def forbidden_init(self: object, *args: object, **kwargs: object) -> None:
        raise AssertionError("ZipFile constructed before unsupported extra-field rejection")

    monkeypatch.setattr(zipfile.ZipFile, "__init__", forbidden_init)
    expected = (
        ImportCode.MXL_ENCRYPTED_MEMBER
        if field_id == 0x9901
        else ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED
    )
    _assert_code(path, expected)


@pytest.mark.parametrize(
    "extra",
    [
        _extra_field(0x5455, struct.pack("<B3L", 0x07, 1, 2, 3)),
        _extra_field(0x000D, b"\x00" * 12),
        _extra_field(0x7875, b"\x01\x01\x00\x01\x00"),
        _extra_field(0x000A, b"\x00" * 4 + struct.pack("<HH", 1, 24) + b"\x00" * 24),
    ],
)
def test_supported_full_timestamp_and_unix_extra_fields_are_accepted(
    tmp_path: Path, extra: bytes
) -> None:
    raw = _mxl(extras=(_Entry("metadata.bin", b"x", extra=extra),))
    assert isinstance(import_musicxml(_write(tmp_path, raw)), ImportSuccess)


def test_malformed_unix_extra_field_is_rejected(tmp_path: Path) -> None:
    extra = _extra_field(0x000D, b"\x00" * 8)
    raw = _mxl(extras=(_Entry("metadata.bin", b"x", extra=extra),))
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_MALFORMED_ARCHIVE)


@pytest.mark.parametrize(
    "extra",
    [
        b"\x55\x54\x04",
        _extra_field(0x5455, b"\x01\x00\x00\x00\x00") * 2,
    ],
)
def test_truncated_and_duplicate_extra_fields_are_rejected(
    tmp_path: Path, extra: bytes
) -> None:
    raw = _mxl(extras=(_Entry("metadata.bin", b"x", extra=extra),))
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_MALFORMED_ARCHIVE)


def test_invalid_utf8_member_name_fails_before_zipfile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _mxl(extras=(_Entry("bad0.xml", b"unused"),))
    raw = _patch_member_flags(raw, "bad0.xml", 0x0800)
    raw = _rename_raw_member(raw, "bad0.xml", b"bad\xff.xml")
    path = _write(tmp_path, raw)

    def forbidden_init(self: object, *args: object, **kwargs: object) -> None:
        raise AssertionError("ZipFile constructed before invalid UTF-8 name rejection")

    monkeypatch.setattr(zipfile.ZipFile, "__init__", forbidden_init)
    _assert_code(path, ImportCode.MXL_UNSAFE_MEMBER_PATH)


def test_directory_with_payload_is_rejected(tmp_path: Path) -> None:
    raw = _mxl(extras=(_Entry("assets/", b"not empty"),))
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_MEMBER_TYPE_UNSUPPORTED)


def test_deflated_empty_directory_is_accepted_for_real_producer_compatibility(
    tmp_path: Path,
) -> None:
    directory = _Entry("assets/", b"", zipfile.ZIP_DEFLATED)
    assert isinstance(import_musicxml(_write(tmp_path, _mxl(extras=(directory,)))), ImportSuccess)


def test_non_symlink_special_file_is_rejected(tmp_path: Path) -> None:
    device_mode = stat.S_IFCHR | 0o600
    raw = _mxl(extras=(_Entry("device", b"", mode=device_mode),))
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_MEMBER_TYPE_UNSUPPORTED)


def test_unsupported_zip_host_system_is_rejected(tmp_path: Path) -> None:
    raw = _mxl(extras=(_Entry("metadata.bin", b"x"),))
    data = bytearray(raw)
    record = _record(raw, "metadata.bin")
    version_made = struct.unpack_from("<H", data, record.offset + 4)[0]
    struct.pack_into("<H", data, record.offset + 4, (42 << 8) | (version_made & 0xFF))
    _assert_code(_write(tmp_path, bytes(data)), ImportCode.MXL_MEMBER_TYPE_UNSUPPORTED)


def test_dos_volume_label_is_rejected(tmp_path: Path) -> None:
    raw = _mxl(extras=(_Entry("VOLUME", b"x"),))
    data = bytearray(raw)
    record = _record(raw, "VOLUME")
    external_attr = struct.unpack_from("<L", data, record.offset + 38)[0]
    struct.pack_into("<L", data, record.offset + 38, external_attr | 0x08)
    _assert_code(_write(tmp_path, bytes(data)), ImportCode.MXL_MEMBER_TYPE_UNSUPPORTED)


@pytest.mark.parametrize(
    "field",
    [
        "max_mxl_archive_bytes",
        "max_mxl_central_directory_bytes",
        "max_mxl_members",
        "max_mxl_member_name_bytes",
        "max_mxl_path_depth",
        "max_mxl_member_bytes",
        "max_mxl_total_uncompressed_bytes",
        "max_mxl_container_bytes",
        "max_bytes",
        "max_mxl_member_ratio",
        "max_mxl_total_ratio",
    ],
)
def test_every_archive_resource_zero_is_a_hard_rejection(
    tmp_path: Path, field: str
) -> None:
    limits = replace(DEFAULT_LIMITS, **{field: 0})
    _assert_code(
        _write(tmp_path, _mxl()),
        ImportCode.INPUT_LIMIT_EXCEEDED,
        limits=limits,
    )


def _exact_archive_limits(raw: bytes) -> dict[str, int]:
    records = _central_records(raw)
    _eocd_offset, entries, _central_offset, central_size = _eocd(raw)
    compressed_total = sum(record.compressed_size for record in records)
    uncompressed_total = sum(record.uncompressed_size for record in records)
    member_ratios = [
        (record.uncompressed_size + record.compressed_size - 1) // record.compressed_size
        for record in records
        if record.uncompressed_size > 0 and record.compressed_size > 0
    ]
    path_depth = max(len(record.name.rstrip(b"/").split(b"/")) for record in records)
    return {
        "max_mxl_archive_bytes": len(raw),
        "max_mxl_central_directory_bytes": central_size,
        "max_mxl_members": entries,
        "max_mxl_member_name_bytes": max(len(record.name) for record in records),
        "max_mxl_path_depth": path_depth,
        "max_mxl_member_bytes": max(record.uncompressed_size for record in records),
        "max_mxl_total_uncompressed_bytes": uncompressed_total,
        "max_mxl_container_bytes": len(_container_xml()),
        "max_bytes": len(BASIC.read_bytes()),
        "max_mxl_member_ratio": max(member_ratios),
        "max_mxl_total_ratio": (uncompressed_total + compressed_total - 1)
        // compressed_total,
    }


def test_archive_resource_limits_are_inclusive_and_one_below_is_rejected(
    tmp_path: Path,
) -> None:
    raw = _mxl()
    path = _write(tmp_path, raw)
    exact = _exact_archive_limits(raw)
    assert isinstance(import_musicxml(path, limits=replace(DEFAULT_LIMITS, **exact)), ImportSuccess)

    for field, boundary in exact.items():
        assert boundary > 0
        limits = replace(DEFAULT_LIMITS, **{field: boundary - 1})
        result = import_musicxml(path, limits=limits)
        assert _only_error_code(result) is ImportCode.INPUT_LIMIT_EXCEEDED, field


def test_maximum_valid_archive_limit_does_not_overflow_read_size(
    tmp_path: Path,
) -> None:
    limits = replace(DEFAULT_LIMITS, max_mxl_archive_bytes=(1 << 63) - 1)
    assert isinstance(
        import_musicxml(_write(tmp_path, _mxl()), limits=limits),
        ImportSuccess,
    )


@pytest.mark.parametrize(
    "field",
    [
        "max_mxl_archive_bytes",
        "max_mxl_central_directory_bytes",
        "max_mxl_members",
        "max_mxl_member_name_bytes",
        "max_mxl_path_depth",
        "max_mxl_member_bytes",
        "max_mxl_total_uncompressed_bytes",
        "max_mxl_container_bytes",
        "max_mxl_member_ratio",
        "max_mxl_total_ratio",
    ],
)
def test_metadata_resource_rejections_happen_before_zipfile_construction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    path = _write(tmp_path, _mxl())

    def forbidden_init(self: object, *args: object, **kwargs: object) -> None:
        raise AssertionError("ZipFile constructed before raw archive resource preflight")

    monkeypatch.setattr(zipfile.ZipFile, "__init__", forbidden_init)
    limits = replace(DEFAULT_LIMITS, **{field: 0})
    _assert_code(path, ImportCode.INPUT_LIMIT_EXCEEDED, limits=limits)


def test_missing_container_member_is_typed(tmp_path: Path) -> None:
    raw = _archive([_Entry("score.musicxml", BASIC.read_bytes())])
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_CONTAINER_MISSING)


def test_container_dtd_and_entity_are_rejected(tmp_path: Path) -> None:
    container = b"""<?xml version="1.0"?>
<!DOCTYPE container [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<container><rootfiles><rootfile full-path="score.musicxml"
media-type="application/vnd.recordare.musicxml+xml"/></rootfiles>&xxe;</container>
"""
    _assert_code(
        _write(tmp_path, _mxl(container_xml=container)),
        ImportCode.MXL_CONTAINER_INVALID,
    )


def test_missing_defusedxml_for_mxl_is_a_typed_dependency_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fretsure.importers import _mxl_container

    real_import_module = _mxl_container.import_module

    def missing(name: str):  # type: ignore[no-untyped-def]
        if name.startswith("defusedxml"):
            raise ModuleNotFoundError("No module named 'defusedxml'", name="defusedxml")
        return real_import_module(name)

    monkeypatch.setattr(_mxl_container, "import_module", missing)
    _assert_code(_write(tmp_path, _mxl()), ImportCode.MISSING_DEPENDENCY)


@pytest.mark.parametrize(
    "injected",
    [
        "<!-- comments are not part of the frozen schema -->",
        "<?producer processing-instruction?>",
        '<x:extension xmlns:x="urn:example"/>',
    ],
)
def test_container_comments_processing_instructions_and_namespaces_are_rejected(
    tmp_path: Path, injected: str
) -> None:
    container = (
        "<container>"
        f"{injected}"
        "<rootfiles>"
        f'<rootfile full-path="score.musicxml" media-type="{ROOT_MEDIA_TYPE}"/>'
        "</rootfiles></container>"
    ).encode()
    _assert_code(
        _write(tmp_path, _mxl(container_xml=container)),
        ImportCode.MXL_CONTAINER_INVALID,
    )


@pytest.mark.parametrize(("field", "maximum"), [("max_xml_depth", 2), ("max_elements", 2)])
def test_container_xml_obeys_shared_depth_and_element_limits(
    tmp_path: Path, field: str, maximum: int
) -> None:
    limits = replace(DEFAULT_LIMITS, **{field: maximum})
    _assert_code(
        _write(tmp_path, _mxl()),
        ImportCode.INPUT_LIMIT_EXCEEDED,
        limits=limits,
    )


@pytest.mark.parametrize(
    "container",
    [
        b"<container>",
        b"<package><rootfiles/></package>",
        b'<container xmlns="urn:example"><rootfiles/></container>',
        b'<container version="1.0"><rootfiles/></container>',
        b"<container><rootfiles/><unknown/></container>",
        b"<container>non-whitespace<rootfiles/></container>",
        (
            b"<container><rootfiles extra=\"x\"><rootfile full-path=\"score.musicxml\" "
            b"media-type=\"application/vnd.recordare.musicxml+xml\"/></rootfiles></container>"
        ),
        (
            b"<container><rootfiles><rootfile full-path=\"score.musicxml\" extra=\"x\" "
            b"media-type=\"application/vnd.recordare.musicxml+xml\"/></rootfiles></container>"
        ),
    ],
)
def test_container_schema_is_fail_closed(tmp_path: Path, container: bytes) -> None:
    _assert_code(
        _write(tmp_path, _mxl(container_xml=container)),
        ImportCode.MXL_CONTAINER_INVALID,
    )


def test_multiple_rootfiles_are_rejected_as_ambiguous(tmp_path: Path) -> None:
    container = f"""<container><rootfiles>
<rootfile full-path="score.musicxml" media-type="{ROOT_MEDIA_TYPE}"/>
<rootfile full-path="other.musicxml" media-type="{ROOT_MEDIA_TYPE}"/>
</rootfiles></container>""".encode()
    raw = _mxl(
        container_xml=container,
        extras=(_Entry("other.musicxml", BASIC.read_bytes()),),
    )
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_ROOTFILE_AMBIGUOUS)


def test_container_without_rootfile_is_typed(tmp_path: Path) -> None:
    container = b"<container><rootfiles/></container>"
    _assert_code(
        _write(tmp_path, _mxl(container_xml=container)),
        ImportCode.MXL_ROOTFILE_MISSING,
    )


@pytest.mark.parametrize("media_type", ["", "text/xml", "application/xml"])
def test_empty_or_wrong_rootfile_media_type_is_rejected(
    tmp_path: Path, media_type: str
) -> None:
    _assert_code(
        _write(tmp_path, _mxl(media_type=media_type)),
        ImportCode.MXL_ROOTFILE_UNSUPPORTED,
    )


@pytest.mark.parametrize("root_path", ["../score.musicxml", "score.pdf"])
def test_unsafe_or_wrong_extension_rootfile_is_rejected(
    tmp_path: Path, root_path: str
) -> None:
    container = _container_xml(root_path)
    raw = _mxl(container_xml=container)
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_ROOTFILE_UNSUPPORTED)


def test_designated_root_member_must_exist(tmp_path: Path) -> None:
    raw = _archive([_Entry("META-INF/container.xml", _container_xml("missing.xml"))])
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_ROOT_MEMBER_MISSING)


def test_duplicate_container_member_is_rejected(tmp_path: Path) -> None:
    raw = _archive(
        [
            _Entry("META-INF/container.xml", _container_xml()),
            _Entry("META-INF/container.xml", _container_xml()),
            _Entry("score.musicxml", BASIC.read_bytes()),
        ]
    )
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_DUPLICATE_MEMBER)


def test_rootfile_directory_spelling_is_rejected(tmp_path: Path) -> None:
    container = _container_xml("score.musicxml/")
    raw = _archive(
        [
            _Entry("META-INF/container.xml", container),
            _Entry("score.musicxml/", b""),
        ]
    )
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_ROOTFILE_UNSUPPORTED)


@pytest.mark.parametrize("violation", ["content", "compression", "order"])
def test_optional_mimetype_must_be_exact_stored_and_first(
    tmp_path: Path, violation: str
) -> None:
    mimetype = b"application/xml" if violation == "content" else MIMETYPE
    compression = zipfile.ZIP_DEFLATED if violation == "compression" else zipfile.ZIP_STORED
    raw = _mxl(
        mimetype=mimetype,
        mimetype_compression=compression,
        mimetype_first=violation != "order",
    )
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_MIMETYPE_INVALID)


@pytest.mark.parametrize(
    "member",
    ["META-INF/container.xml", "score.musicxml", "unused.bin"],
)
def test_crc_mismatch_in_container_root_or_unused_member_rejects_whole_archive(
    tmp_path: Path, member: str
) -> None:
    raw = _mxl(
        compression=zipfile.ZIP_STORED,
        extras=(_Entry("unused.bin", b"unused attachment"),),
    )
    raw = _corrupt_stored_member(raw, member)
    _assert_code(_write(tmp_path, raw), ImportCode.MXL_CRC_MISMATCH)


def test_root_xml_size_limit_rejects_before_root_decompression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fretsure.importers import _mxl_container

    root_reads = 0
    real_read = _mxl_container._read_verified_member

    def observe_read(*args: object, **kwargs: object) -> bytes:
        nonlocal root_reads
        prepared = args[2]
        if prepared.member.name == "score.musicxml":  # type: ignore[attr-defined]
            root_reads += 1
        return real_read(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_mxl_container, "_read_verified_member", observe_read)
    limits = replace(DEFAULT_LIMITS, max_bytes=0)
    _assert_code(
        _write(tmp_path, _mxl()),
        ImportCode.INPUT_LIMIT_EXCEEDED,
        limits=limits,
    )
    assert root_reads == 0


def test_external_reference_in_mxl_root_fails_before_music21_adapter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = BASIC.read_text(encoding="utf-8")
    needle = "  <part-list>"
    assert needle in root
    hostile = root.replace(
        needle,
        (
            '  <credit page="1"><credit-image source="file:///etc/passwd" '
            'type="image/png"/></credit>\n  <part-list>'
        ),
        1,
    ).encode()
    path = _write(tmp_path, _mxl(root=hostile))

    def adapter_must_not_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("external MXL root reference reached music21 adapter")

    real_builtin_open = builtins.open
    real_io_open = io.open

    def guarded_builtin_open(file: object, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if str(file) == "/etc/passwd":
            raise AssertionError("MXL import attempted filesystem dereference")
        return real_builtin_open(file, *args, **kwargs)

    def guarded_io_open(file: object, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if str(file) == "/etc/passwd":
            raise AssertionError("MXL import attempted filesystem dereference")
        return real_io_open(file, *args, **kwargs)

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("MXL import attempted network access")

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_must_not_run)
    monkeypatch.setattr(builtins, "open", guarded_builtin_open)
    monkeypatch.setattr(io, "open", guarded_io_open)
    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    result = import_musicxml(path)
    assert isinstance(result, ImportFailure)
    unsafe = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code is ImportCode.UNSAFE_XML
    ]
    assert len(unsafe) == 1
    assert unsafe[0].location is not None
    assert unsafe[0].location.element == "credit-image"


def test_hostile_archive_sweep_never_leaks_an_untyped_exception(tmp_path: Path) -> None:
    valid = _mxl()
    _eocd_offset, _entries, central_offset, _central_size = _eocd(valid)
    cases = (
        b"",
        b"PK",
        b"PK\x03\x04" + b"\x00" * 40,
        b"PK\x05\x06" + b"\x00" * 18,
        valid[:21],
        valid[: central_offset + 10],
        valid[:-1],
        b"\x00" * len(valid),
    )
    for index, raw in enumerate(cases):
        result = import_musicxml(_write(tmp_path, raw, f"hostile-{index}.mxl"))
        assert isinstance(result, ImportFailure), index
        assert result.diagnostics, index
        assert all(isinstance(diagnostic.code, ImportCode) for diagnostic in result.diagnostics)


def test_direct_container_reader_rejects_bytes_subclass_before_hooks() -> None:
    class HostileBytes(bytes):
        def __len__(self) -> int:
            raise AssertionError("hostile bytes reached archive parsing")

    result = read_mxl_container(HostileBytes(_mxl()), DEFAULT_LIMITS)
    assert _only_error_code(result) is ImportCode.MXL_MALFORMED_ARCHIVE


def test_import_rejects_mutated_limit_scalar_before_hooks() -> None:
    class HostileInt(int):
        def __lt__(self, other: object) -> bool:
            raise AssertionError(f"hostile limit compared with {type(other).__name__}")

        def bit_length(self) -> int:
            raise AssertionError("hostile limit bit_length executed")

    limits = ImportLimits()
    object.__setattr__(limits, "max_mxl_archive_bytes", HostileInt(1))

    result = import_musicxml(BASIC, limits=limits)
    assert _only_error_code(result) is ImportCode.INPUT_LIMIT_EXCEEDED


def test_import_uses_detached_limits_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ImportLimits()
    real_read = musicxml_module._read_bounded

    def mutate_source_after_barrier(path: Path, snapshot: ImportLimits):
        object.__setattr__(source, "max_bytes", 0)
        assert snapshot.max_bytes == DEFAULT_LIMITS.max_bytes
        return real_read(path, snapshot)

    monkeypatch.setattr(musicxml_module, "_read_bounded", mutate_source_after_barrier)
    assert isinstance(import_musicxml(BASIC, limits=source), ImportSuccess)


def test_import_never_uses_zipfile_extract_or_extractall(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, _mxl(mimetype=MIMETYPE))

    def forbidden_extract(*args: object, **kwargs: object) -> None:
        raise AssertionError("MXL reader attempted to extract an archive member to disk")

    monkeypatch.setattr(zipfile.ZipFile, "extract", forbidden_extract)
    monkeypatch.setattr(zipfile.ZipFile, "extractall", forbidden_extract)
    assert isinstance(import_musicxml(path), ImportSuccess)


def test_real_mxl_cli_and_trace_are_deterministic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root_path = "scores/lead.musicxml"
    score = _write(tmp_path, _mxl(root_path=root_path, mimetype=MIMETYPE), "demo.mxl")
    trace_path = tmp_path / "trace.jsonl"
    argv = [
        str(score),
        "--n",
        "1",
        "--no-critic",
        "--trace-jsonl",
        str(trace_path),
    ]

    first_code = cli_main(argv)
    first = capsys.readouterr()
    first_trace = trace_path.read_bytes()
    second_code = cli_main(argv)
    second = capsys.readouterr()
    second_trace = trace_path.read_bytes()

    assert first_code == second_code == 0
    assert first.out == second.out
    assert first.err == second.err == ""
    assert first_trace == second_trace
    assert "IMPORTER          : musicxml@0.3.0" in first.out
    assert "ROOTFILE MEMBER   : scores/lead.musicxml" in first.out
