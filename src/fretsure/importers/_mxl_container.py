"""Strict, in-memory reader for the deliberately small ``.mxl`` envelope.

This module does not interpret MusicXML.  It proves that the outer ZIP and
``META-INF/container.xml`` are within the frozen container contract, verifies
every member, and returns only the selected root member's bytes.
"""

from __future__ import annotations

import binascii
import stat
import struct
import unicodedata
import xml.etree.ElementTree as ET
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import import_module
from io import BytesIO
from typing import Never, Protocol, cast
from zipfile import (
    ZIP_DEFLATED,
    ZIP_STORED,
    BadZipFile,
    LargeZipFile,
    ZipFile,
    ZipInfo,
)

from fretsure.importers.contracts import (
    DiagnosticSeverity,
    ImportCode,
    ImportDiagnostic,
    ImportFailure,
    ImportLimits,
    SourceLocation,
    snapshot_import_limits,
)

MXL_CONTAINER_VERSION = "mxl-container@0.1.0"

_CONTAINER_PATH = "META-INF/container.xml"
_MIMETYPE_PATH = "mimetype"
_MIMETYPE_BYTES = b"application/vnd.recordare.musicxml"
_ROOT_MEDIA_TYPE = "application/vnd.recordare.musicxml+xml"

_EOCD_SIGNATURE = b"PK\x05\x06"
_CENTRAL_SIGNATURE = b"PK\x01\x02"
_LOCAL_SIGNATURE = b"PK\x03\x04"
_DATA_DESCRIPTOR_SIGNATURE = b"PK\x07\x08"
_ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_CENTRAL_DIGITAL_SIGNATURE = b"PK\x05\x05"

_EOCD = struct.Struct("<4s4H2LH")
_CENTRAL_HEADER = struct.Struct("<4s6H3L5H2L")
_LOCAL_HEADER = struct.Struct("<4s5H3L2H")
_DATA_DESCRIPTOR = struct.Struct("<3L")

_UTF8_FLAG = 1 << 11
_DATA_DESCRIPTOR_FLAG = 1 << 3
_ENCRYPTED_FLAG = 1 << 0
_PATCHED_DATA_FLAG = 1 << 5
_STRONG_ENCRYPTION_FLAG = 1 << 6
_MASKED_HEADER_FLAG = 1 << 13
_DEFLATE_OPTION_FLAGS = (1 << 1) | (1 << 2)
_SUPPORTED_FLAGS = _UTF8_FLAG | _DATA_DESCRIPTOR_FLAG | _DEFLATE_OPTION_FLAGS

# These fields carry timestamps or numeric ownership metadata only.  In
# particular, ZIP64, AES and Unicode-path override fields are intentionally
# absent.  New fields are added only after producer-driven review.
_EXTRA_NTFS = 0x000A
_EXTRA_UNIX = 0x000D
_EXTRA_OLD_UNIX = 0x5855
_EXTRA_OLD_UID_GID = 0x7855
_EXTRA_UID_GID = 0x7875
_EXTRA_EXTENDED_TIMESTAMP = 0x5455
_EXTRA_UNICODE_PATH = 0x7075
_EXTRA_AES = 0x9901
_EXTRA_ZIP64 = 0x0001
_ALLOWED_EXTRA_FIELDS = frozenset(
    {
        _EXTRA_NTFS,
        _EXTRA_UNIX,
        _EXTRA_OLD_UNIX,
        _EXTRA_OLD_UID_GID,
        _EXTRA_UID_GID,
        _EXTRA_EXTENDED_TIMESTAMP,
    }
)

_READ_CHUNK = 64 * 1024


@dataclass(frozen=True, slots=True)
class MXLContainerPayload:
    """A completely verified container root ready for MusicXML parsing."""

    root_bytes: bytes
    root_path: str
    warnings: tuple[ImportDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class _CentralMember:
    index: int
    version_made: int
    version_needed: int
    flags: int
    method: int
    crc32: int
    compressed_size: int
    uncompressed_size: int
    raw_name: bytes
    name: str
    path_parts: tuple[str, ...]
    normalized_parts: tuple[str, ...]
    extra: bytes
    internal_attr: int
    external_attr: int
    local_header_offset: int
    is_directory: bool


@dataclass(frozen=True, slots=True)
class _PreparedMember:
    member: _CentralMember
    data_start: int
    data_end: int


@dataclass(frozen=True, slots=True)
class _ArchiveLayout:
    central_members: tuple[_PreparedMember, ...]
    local_members: tuple[_PreparedMember, ...]
    central_offset: int


class _IterParse(Protocol):
    root: ET.Element

    def __iter__(self) -> Iterator[tuple[str, object]]: ...


class _DefusedElementTree(Protocol):
    def iterparse(
        self,
        source: BytesIO,
        events: tuple[str, ...],
        *,
        forbid_dtd: bool,
        forbid_entities: bool,
        forbid_external: bool,
    ) -> _IterParse: ...


class _RejectedContainer(Exception):
    def __init__(self, diagnostic: ImportDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(diagnostic.message)


def _diagnostic(
    code: ImportCode,
    message: str,
    location: SourceLocation | None = None,
    *,
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
) -> ImportDiagnostic:
    return ImportDiagnostic(code, severity, message, location)


def _reject(
    code: ImportCode,
    message: str,
    location: SourceLocation | None = None,
) -> Never:
    raise _RejectedContainer(_diagnostic(code, message, location))


def _member_location(name: str) -> SourceLocation:
    return SourceLocation(archive_member=name)


def _quoted(value: str) -> str:
    """Render untrusted metadata without terminal control characters."""

    return ascii(value)


def _normal_form(component: str) -> str:
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", component).casefold())


def _validate_path(
    name: str,
    *,
    is_directory: bool,
    limits: ImportLimits,
    code: ImportCode,
    context: str,
    encoded_length: int | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if encoded_length is None:
        try:
            encoded_length = len(name.encode("utf-8"))
        except UnicodeError:
            _reject(code, f"{context} is not valid Unicode")
    if encoded_length == 0:
        _reject(code, f"{context} must not be empty")
    if encoded_length > limits.max_mxl_member_name_bytes:
        _reject(
            ImportCode.INPUT_LIMIT_EXCEEDED,
            f"{context} is {encoded_length} encoded bytes; "
            f"limit is {limits.max_mxl_member_name_bytes}",
        )
    if "\\" in name:
        _reject(code, f"{context} must use POSIX '/' separators")
    if name.startswith("/"):
        _reject(code, f"{context} must be relative")
    if any(unicodedata.category(character) == "Cc" for character in name):
        _reject(code, f"{context} contains a control character")
    if is_directory:
        if not name.endswith("/"):
            _reject(code, f"{context} has an inconsistent directory marker")
        path = name[:-1]
    else:
        if name.endswith("/"):
            _reject(code, f"{context} names a directory where a file is required")
        path = name
    parts = tuple(path.split("/"))
    if not parts or any(part in {"", ".", ".."} for part in parts):
        _reject(code, f"{context} contains an empty, '.' or '..' path segment")
    first = parts[0]
    if len(first) >= 2 and first[0].isascii() and first[0].isalpha() and first[1] == ":":
        _reject(code, f"{context} contains a Windows drive prefix")
    if len(parts) > limits.max_mxl_path_depth:
        _reject(
            ImportCode.INPUT_LIMIT_EXCEEDED,
            f"{context} has path depth {len(parts)}; limit is {limits.max_mxl_path_depth}",
        )
    normalized = tuple(_normal_form(part) for part in parts)
    return parts, normalized


def _decode_member_name(raw_name: bytes, flags: int, index: int) -> str:
    if not raw_name:
        _reject(
            ImportCode.MXL_UNSAFE_MEMBER_PATH,
            f"archive member {index} has an empty name",
        )
    try:
        return raw_name.decode("utf-8" if flags & _UTF8_FLAG else "cp437", errors="strict")
    except UnicodeDecodeError:
        _reject(
            ImportCode.MXL_UNSAFE_MEMBER_PATH,
            f"archive member {index} has an invalid encoded name",
        )


def _validate_flags(flags: int, method: int, *, member_index: int) -> None:
    if flags & (_ENCRYPTED_FLAG | _STRONG_ENCRYPTION_FLAG | _MASKED_HEADER_FLAG):
        _reject(
            ImportCode.MXL_ENCRYPTED_MEMBER,
            f"archive member {member_index} uses encryption or masked headers",
        )
    if flags & _PATCHED_DATA_FLAG:
        _reject(
            ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
            f"archive member {member_index} uses patched data",
        )
    unsupported = flags & ~_SUPPORTED_FLAGS
    if unsupported:
        _reject(
            ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
            f"archive member {member_index} uses unsupported ZIP flags 0x{unsupported:04x}",
        )
    if method == ZIP_STORED and flags & _DEFLATE_OPTION_FLAGS:
        _reject(
            ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
            f"stored archive member {member_index} sets deflate-only option flags",
        )


def _validate_ntfs_extra(payload: bytes, *, member_name: str) -> None:
    if len(payload) < 4 or payload[:4] != b"\x00\x00\x00\x00":
        _reject(
            ImportCode.MXL_MALFORMED_ARCHIVE,
            f"member {_quoted(member_name)} has a malformed NTFS extra field",
            _member_location(member_name),
        )
    cursor = 4
    seen_tags: set[int] = set()
    while cursor < len(payload):
        if len(payload) - cursor < 4:
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"member {_quoted(member_name)} has a truncated NTFS attribute",
                _member_location(member_name),
            )
        tag, size = struct.unpack_from("<HH", payload, cursor)
        cursor += 4
        if cursor + size > len(payload) or tag in seen_tags:
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"member {_quoted(member_name)} has an ambiguous NTFS attribute",
                _member_location(member_name),
            )
        # Tag 1 is the standard mtime/atime/ctime triple.  Unknown NTFS
        # attributes are not needed by an MXL reader and remain fail-closed.
        if tag != 1 or size != 24:
            _reject(
                ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
                f"member {_quoted(member_name)} uses unsupported NTFS metadata",
                _member_location(member_name),
            )
        seen_tags.add(tag)
        cursor += size


def _validate_extra_payload(
    field_id: int,
    payload: bytes,
    *,
    central: bool,
    member_name: str,
) -> None:
    malformed = False
    if field_id == _EXTRA_EXTENDED_TIMESTAMP:
        if not payload or payload[0] & ~0x07:
            malformed = True
        else:
            flags = payload[0]
            full_length = 1 + 4 * flags.bit_count()
            central_mtime_length = 1 + 4 * int(bool(flags & 0x01))
            allowed_lengths = {full_length, central_mtime_length} if central else {full_length}
            malformed = len(payload) not in allowed_lengths
    elif field_id == _EXTRA_UID_GID:
        if len(payload) < 4 or payload[0] != 1:
            malformed = True
        else:
            uid_size = payload[1]
            gid_size_offset = 2 + uid_size
            if uid_size == 0 or uid_size > 8 or gid_size_offset >= len(payload):
                malformed = True
            else:
                gid_size = payload[gid_size_offset]
                malformed = (
                    gid_size == 0
                    or gid_size > 8
                    or gid_size_offset + 1 + gid_size != len(payload)
                )
    elif field_id == _EXTRA_OLD_UID_GID:
        malformed = len(payload) not in ({0, 4} if central else {4})
    elif field_id == _EXTRA_OLD_UNIX:
        malformed = len(payload) not in {8, 12}
    elif field_id == _EXTRA_UNIX:
        malformed = len(payload) != 12
    elif field_id == _EXTRA_NTFS:
        _validate_ntfs_extra(payload, member_name=member_name)
        return
    if malformed:
        _reject(
            ImportCode.MXL_MALFORMED_ARCHIVE,
            f"member {_quoted(member_name)} has malformed extra field 0x{field_id:04x}",
            _member_location(member_name),
        )


def _validate_extra(extra: bytes, *, central: bool, member_name: str) -> None:
    cursor = 0
    seen: set[int] = set()
    while cursor < len(extra):
        if len(extra) - cursor < 4:
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"member {_quoted(member_name)} has a truncated ZIP extra field",
                _member_location(member_name),
            )
        field_id, size = struct.unpack_from("<HH", extra, cursor)
        cursor += 4
        end = cursor + size
        if end > len(extra):
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"member {_quoted(member_name)} has an overrun ZIP extra field",
                _member_location(member_name),
            )
        if field_id in seen:
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"member {_quoted(member_name)} repeats ZIP extra field 0x{field_id:04x}",
                _member_location(member_name),
            )
        if field_id == _EXTRA_AES:
            _reject(
                ImportCode.MXL_ENCRYPTED_MEMBER,
                f"member {_quoted(member_name)} uses WinZip AES encryption metadata",
                _member_location(member_name),
            )
        if field_id == _EXTRA_ZIP64:
            _reject(
                ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
                f"member {_quoted(member_name)} uses ZIP64 metadata",
                _member_location(member_name),
            )
        if field_id == _EXTRA_UNICODE_PATH:
            _reject(
                ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
                f"member {_quoted(member_name)} uses a Unicode-path override",
                _member_location(member_name),
            )
        if field_id not in _ALLOWED_EXTRA_FIELDS:
            _reject(
                ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
                f"member {_quoted(member_name)} uses unsupported ZIP extra field "
                f"0x{field_id:04x}",
                _member_location(member_name),
            )
        seen.add(field_id)
        _validate_extra_payload(
            field_id,
            extra[cursor:end],
            central=central,
            member_name=member_name,
        )
        cursor = end


def _validate_member_type(
    *,
    version_made: int,
    external_attr: int,
    is_directory: bool,
    uncompressed_size: int,
    crc32: int,
    member_name: str,
) -> None:
    create_system = version_made >> 8
    unix_mode = (external_attr >> 16) & 0xFFFF
    unix_kind = stat.S_IFMT(unix_mode) if create_system == 3 else 0
    dos_directory = bool(external_attr & 0x10)
    dos_volume_label = bool(external_attr & 0x08)
    if create_system not in {0, 3}:
        _reject(
            ImportCode.MXL_MEMBER_TYPE_UNSUPPORTED,
            f"member {_quoted(member_name)} uses unsupported ZIP host system {create_system}",
            _member_location(member_name),
        )
    if dos_volume_label:
        _reject(
            ImportCode.MXL_MEMBER_TYPE_UNSUPPORTED,
            f"member {_quoted(member_name)} is marked as a DOS volume label",
            _member_location(member_name),
        )
    if unix_kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
        _reject(
            ImportCode.MXL_MEMBER_TYPE_UNSUPPORTED,
            f"member {_quoted(member_name)} is a symlink or special file",
            _member_location(member_name),
        )
    if is_directory:
        if unix_kind == stat.S_IFREG:
            _reject(
                ImportCode.MXL_MEMBER_TYPE_UNSUPPORTED,
                f"member {_quoted(member_name)} has conflicting file/directory metadata",
                _member_location(member_name),
            )
        if uncompressed_size != 0 or crc32 != 0:
            _reject(
                ImportCode.MXL_MEMBER_TYPE_UNSUPPORTED,
                f"directory member {_quoted(member_name)} must have an empty payload",
                _member_location(member_name),
            )
    elif unix_kind == stat.S_IFDIR or dos_directory:
        _reject(
            ImportCode.MXL_MEMBER_TYPE_UNSUPPORTED,
            f"member {_quoted(member_name)} has conflicting file/directory metadata",
            _member_location(member_name),
        )


def _validate_collisions(members: tuple[_CentralMember, ...]) -> None:
    exact_names: set[str] = set()
    full_paths: dict[tuple[str, ...], _CentralMember] = {}
    prefix_spellings: dict[tuple[str, ...], tuple[str, ...]] = {}
    file_paths: set[tuple[str, ...]] = set()

    for member in members:
        if member.name in exact_names:
            _reject(
                ImportCode.MXL_DUPLICATE_MEMBER,
                f"archive repeats exact member {_quoted(member.name)}",
                _member_location(member.name),
            )
        exact_names.add(member.name)

        key = member.normalized_parts
        prior = full_paths.get(key)
        if prior is not None:
            _reject(
                ImportCode.MXL_NORMALIZED_COLLISION,
                f"members {_quoted(prior.name)} and {_quoted(member.name)} collide after "
                "NFC/casefold normalization",
                _member_location(member.name),
            )
        for depth in range(1, len(key) + 1):
            prefix_key = key[:depth]
            spelling = member.path_parts[:depth]
            prior_spelling = prefix_spellings.get(prefix_key)
            if prior_spelling is not None and prior_spelling != spelling:
                _reject(
                    ImportCode.MXL_NORMALIZED_COLLISION,
                    f"member {_quoted(member.name)} aliases an existing path prefix after "
                    "NFC/casefold normalization",
                    _member_location(member.name),
                )
            prefix_spellings[prefix_key] = spelling
        for depth in range(1, len(key)):
            if key[:depth] in file_paths:
                _reject(
                    ImportCode.MXL_NORMALIZED_COLLISION,
                    f"member {_quoted(member.name)} descends from an existing file path",
                    _member_location(member.name),
                )
        if not member.is_directory:
            if any(len(other) > len(key) and other[: len(key)] == key for other in full_paths):
                _reject(
                    ImportCode.MXL_NORMALIZED_COLLISION,
                    f"file member {_quoted(member.name)} is a prefix of another member",
                    _member_location(member.name),
                )
            file_paths.add(key)
        full_paths[key] = member


def _check_ratio(
    *,
    uncompressed: int,
    compressed: int,
    maximum: int,
    description: str,
    location: SourceLocation | None = None,
) -> None:
    if uncompressed > compressed * maximum:
        _reject(
            ImportCode.INPUT_LIMIT_EXCEEDED,
            f"{description} exceeds integer compression-ratio limit {maximum}: "
            f"{uncompressed} uncompressed bytes from {compressed} compressed bytes",
            location,
        )


def _find_eocd(data: bytes, limits: ImportLimits) -> tuple[int, int, int]:
    if len(data) > limits.max_mxl_archive_bytes:
        _reject(
            ImportCode.INPUT_LIMIT_EXCEEDED,
            f"MXL archive is {len(data)} bytes; limit is {limits.max_mxl_archive_bytes}",
            SourceLocation(element="archive"),
        )
    if len(data) < _EOCD.size:
        _reject(ImportCode.MXL_MALFORMED_ARCHIVE, "MXL archive is too short for ZIP EOCD")
    search_start = max(0, len(data) - _EOCD.size - 0xFFFF)
    offset = data.rfind(_EOCD_SIGNATURE, search_start)
    if offset < 0 or offset + _EOCD.size > len(data):
        _reject(ImportCode.MXL_MALFORMED_ARCHIVE, "MXL archive has no complete ZIP EOCD")
    (
        signature,
        disk_number,
        central_disk,
        entries_on_disk,
        entry_count,
        central_size,
        central_offset,
        comment_length,
    ) = _EOCD.unpack_from(data, offset)
    if signature != _EOCD_SIGNATURE:
        _reject(ImportCode.MXL_MALFORMED_ARCHIVE, "invalid ZIP EOCD signature")
    expected_end = offset + _EOCD.size + comment_length
    if expected_end != len(data):
        _reject(
            ImportCode.MXL_MALFORMED_ARCHIVE,
            "ZIP EOCD is followed by trailing data or has an inconsistent comment length",
        )
    if comment_length:
        _reject(
            ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
            "MXL archive comments are unsupported",
        )
    if disk_number != 0 or central_disk != 0 or entries_on_disk != entry_count:
        _reject(
            ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
            "multi-disk ZIP archives are unsupported",
        )
    if (
        entry_count == 0xFFFF
        or entries_on_disk == 0xFFFF
        or central_size == 0xFFFFFFFF
        or central_offset == 0xFFFFFFFF
    ):
        _reject(ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED, "ZIP64 archives are unsupported")
    if offset >= 20 and data[offset - 20 : offset - 16] == _ZIP64_LOCATOR_SIGNATURE:
        _reject(ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED, "ZIP64 archives are unsupported")
    if entry_count > limits.max_mxl_members:
        _reject(
            ImportCode.INPUT_LIMIT_EXCEEDED,
            f"MXL archive declares {entry_count} members; limit is {limits.max_mxl_members}",
            SourceLocation(element="central-directory"),
        )
    if central_size > limits.max_mxl_central_directory_bytes:
        _reject(
            ImportCode.INPUT_LIMIT_EXCEEDED,
            f"ZIP central directory is {central_size} bytes; "
            f"limit is {limits.max_mxl_central_directory_bytes}",
            SourceLocation(element="central-directory"),
        )
    if central_offset + central_size != offset:
        if central_offset + central_size < offset:
            feature = data[central_offset + central_size : offset]
            prefix_length = offset - (central_offset + central_size)
            shifted_central = central_offset + prefix_length
            if (
                prefix_length > 0
                and (
                    data[shifted_central : shifted_central + 4] == _CENTRAL_SIGNATURE
                    or (
                        entry_count == 0
                        and data[shifted_central : shifted_central + 4] == _EOCD_SIGNATURE
                    )
                )
            ):
                _reject(
                    ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
                    "self-extracting/prefixed ZIP archives are unsupported",
                )
            if feature.startswith(_ZIP64_EOCD_SIGNATURE):
                _reject(
                    ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
                    "ZIP64 archives are unsupported",
                )
            if feature.startswith(_CENTRAL_DIGITAL_SIGNATURE):
                _reject(
                    ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
                    "central-directory digital signatures are unsupported",
                )
        _reject(
            ImportCode.MXL_MALFORMED_ARCHIVE,
            "ZIP central-directory bounds do not meet the EOCD exactly",
        )
    if central_offset > len(data) or central_size > len(data) - central_offset:
        _reject(ImportCode.MXL_MALFORMED_ARCHIVE, "ZIP central directory is out of bounds")
    return central_offset, central_size, entry_count


def _parse_central_directory(
    data: bytes,
    *,
    central_offset: int,
    central_size: int,
    entry_count: int,
    limits: ImportLimits,
) -> tuple[_CentralMember, ...]:
    cursor = central_offset
    central_end = central_offset + central_size
    members: list[_CentralMember] = []
    total_uncompressed = 0
    total_compressed = 0

    for index in range(entry_count):
        if cursor + _CENTRAL_HEADER.size > central_end:
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"central-directory entry {index} is truncated",
            )
        values = _CENTRAL_HEADER.unpack_from(data, cursor)
        (
            signature,
            version_made,
            version_needed,
            flags,
            method,
            _modified_time,
            _modified_date,
            crc32,
            compressed_size,
            uncompressed_size,
            name_length,
            extra_length,
            comment_length,
            disk_start,
            internal_attr,
            external_attr,
            local_header_offset,
        ) = values
        if signature != _CENTRAL_SIGNATURE:
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"central-directory entry {index} has an invalid signature",
            )
        variable_start = cursor + _CENTRAL_HEADER.size
        variable_end = variable_start + name_length + extra_length + comment_length
        if variable_end > central_end:
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"central-directory entry {index} overruns its declared bounds",
            )
        if name_length > limits.max_mxl_member_name_bytes:
            _reject(
                ImportCode.INPUT_LIMIT_EXCEEDED,
                f"archive member {index} name is {name_length} bytes; "
                f"limit is {limits.max_mxl_member_name_bytes}",
                SourceLocation(element="central-directory"),
            )
        if comment_length:
            _reject(
                ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
                f"archive member {index} has an unsupported comment",
            )
        if disk_start != 0:
            _reject(
                ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
                f"archive member {index} starts on another disk",
            )
        if (
            compressed_size == 0xFFFFFFFF
            or uncompressed_size == 0xFFFFFFFF
            or local_header_offset == 0xFFFFFFFF
            or disk_start == 0xFFFF
        ):
            _reject(ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED, "ZIP64 archives are unsupported")
        _validate_flags(flags, method, member_index=index)
        if method == 99:
            _reject(
                ImportCode.MXL_ENCRYPTED_MEMBER,
                f"archive member {index} uses the WinZip AES pseudo-method",
            )
        if method not in {ZIP_STORED, ZIP_DEFLATED}:
            _reject(
                ImportCode.MXL_COMPRESSION_UNSUPPORTED,
                f"archive member {index} uses compression method {method}; "
                "only stored and deflated are supported",
            )
        extract_version = version_needed & 0xFF
        extract_reserved = version_needed >> 8
        if extract_reserved != 0 or extract_version > 20:
            _reject(
                ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
                f"archive member {index} requires unsupported ZIP version metadata",
            )
        if method == ZIP_STORED and compressed_size != uncompressed_size:
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"stored archive member {index} has unequal compressed/uncompressed sizes",
            )
        if uncompressed_size > limits.max_mxl_member_bytes:
            _reject(
                ImportCode.INPUT_LIMIT_EXCEEDED,
                f"archive member {index} declares {uncompressed_size} bytes; "
                f"limit is {limits.max_mxl_member_bytes}",
            )
        raw_name = data[variable_start : variable_start + name_length]
        extra_start = variable_start + name_length
        extra = data[extra_start : extra_start + extra_length]
        name = _decode_member_name(raw_name, flags, index)
        is_directory = name.endswith("/")
        parts, normalized = _validate_path(
            name,
            is_directory=is_directory,
            limits=limits,
            code=ImportCode.MXL_UNSAFE_MEMBER_PATH,
            context=f"archive member {index} path",
            encoded_length=name_length,
        )
        _validate_extra(extra, central=True, member_name=name)
        _validate_member_type(
            version_made=version_made,
            external_attr=external_attr,
            is_directory=is_directory,
            uncompressed_size=uncompressed_size,
            crc32=crc32,
            member_name=name,
        )
        _check_ratio(
            uncompressed=uncompressed_size,
            compressed=compressed_size,
            maximum=limits.max_mxl_member_ratio,
            description=f"member {_quoted(name)}",
            location=_member_location(name),
        )
        total_uncompressed += uncompressed_size
        total_compressed += compressed_size
        if total_uncompressed > limits.max_mxl_total_uncompressed_bytes:
            _reject(
                ImportCode.INPUT_LIMIT_EXCEEDED,
                f"MXL members declare {total_uncompressed} total uncompressed bytes; "
                f"limit is {limits.max_mxl_total_uncompressed_bytes}",
                SourceLocation(element="central-directory"),
            )
        members.append(
            _CentralMember(
                index=index,
                version_made=version_made,
                version_needed=version_needed,
                flags=flags,
                method=method,
                crc32=crc32,
                compressed_size=compressed_size,
                uncompressed_size=uncompressed_size,
                raw_name=raw_name,
                name=name,
                path_parts=parts,
                normalized_parts=normalized,
                extra=extra,
                internal_attr=internal_attr,
                external_attr=external_attr,
                local_header_offset=local_header_offset,
                is_directory=is_directory,
            )
        )
        cursor = variable_end

    if cursor != central_end:
        remainder = data[cursor:central_end]
        if remainder.startswith(_CENTRAL_DIGITAL_SIGNATURE):
            _reject(
                ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
                "central-directory digital signatures are unsupported",
            )
        if remainder.startswith(_ZIP64_EOCD_SIGNATURE):
            _reject(ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED, "ZIP64 is unsupported")
        _reject(
            ImportCode.MXL_MALFORMED_ARCHIVE,
            "EOCD member count does not consume the complete central directory",
        )
    _check_ratio(
        uncompressed=total_uncompressed,
        compressed=total_compressed,
        maximum=limits.max_mxl_total_ratio,
        description="aggregate MXL payload",
        location=SourceLocation(element="central-directory"),
    )
    result = tuple(members)
    _validate_collisions(result)
    return result


def _parse_local_records(
    data: bytes,
    members: tuple[_CentralMember, ...],
    *,
    central_offset: int,
) -> tuple[tuple[_PreparedMember, ...], tuple[_PreparedMember, ...]]:
    local_order = sorted(members, key=lambda member: member.local_header_offset)
    if not local_order:
        if central_offset != 0:
            _reject(
                ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
                "empty ZIP archive contains an unsupported prefix",
            )
        return (), ()
    if local_order[0].local_header_offset != 0:
        _reject(
            ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
            "self-extracting/prefixed ZIP archives are unsupported",
        )
    if len({member.local_header_offset for member in local_order}) != len(local_order):
        _reject(
            ImportCode.MXL_MALFORMED_ARCHIVE,
            "multiple central-directory entries share a local-header offset",
        )

    prepared_local: list[_PreparedMember] = []
    expected_offset = 0
    for position, member in enumerate(local_order):
        offset = member.local_header_offset
        if offset != expected_offset:
            if offset < expected_offset:
                _reject(
                    ImportCode.MXL_MALFORMED_ARCHIVE,
                    f"local record for {_quoted(member.name)} overlaps its predecessor",
                    _member_location(member.name),
                )
            _reject(
                ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
                f"unsupported gap precedes local record {_quoted(member.name)}",
                _member_location(member.name),
            )
        if offset + _LOCAL_HEADER.size > central_offset:
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"local header for {_quoted(member.name)} is truncated",
                _member_location(member.name),
            )
        (
            signature,
            version_needed,
            flags,
            method,
            _modified_time,
            _modified_date,
            crc32,
            compressed_size,
            uncompressed_size,
            name_length,
            extra_length,
        ) = _LOCAL_HEADER.unpack_from(data, offset)
        if signature != _LOCAL_SIGNATURE:
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"local record for {_quoted(member.name)} has an invalid signature",
                _member_location(member.name),
            )
        variable_start = offset + _LOCAL_HEADER.size
        data_start = variable_start + name_length + extra_length
        if data_start > central_offset:
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"local header for {_quoted(member.name)} overruns the central directory",
                _member_location(member.name),
            )
        raw_name = data[variable_start : variable_start + name_length]
        extra = data[variable_start + name_length : data_start]
        if raw_name != member.raw_name:
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"local/central filename mismatch for {_quoted(member.name)}",
                _member_location(member.name),
            )
        if (
            version_needed != member.version_needed
            or flags != member.flags
            or method != member.method
        ):
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"local/central ZIP metadata mismatch for {_quoted(member.name)}",
                _member_location(member.name),
            )
        _validate_extra(extra, central=False, member_name=member.name)
        has_descriptor = bool(flags & _DATA_DESCRIPTOR_FLAG)
        if has_descriptor:
            if crc32 not in {0, member.crc32}:
                _reject(
                    ImportCode.MXL_MALFORMED_ARCHIVE,
                    f"local CRC placeholder disagrees for {_quoted(member.name)}",
                    _member_location(member.name),
                )
            if compressed_size not in {0, member.compressed_size} or uncompressed_size not in {
                0,
                member.uncompressed_size,
            }:
                _reject(
                    ImportCode.MXL_MALFORMED_ARCHIVE,
                    f"local size placeholder disagrees for {_quoted(member.name)}",
                    _member_location(member.name),
                )
        elif (
            crc32 != member.crc32
            or compressed_size != member.compressed_size
            or uncompressed_size != member.uncompressed_size
        ):
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"local/central CRC or size mismatch for {_quoted(member.name)}",
                _member_location(member.name),
            )
        data_end = data_start + member.compressed_size
        next_offset = (
            local_order[position + 1].local_header_offset
            if position + 1 < len(local_order)
            else central_offset
        )
        if data_end > next_offset:
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"compressed data for {_quoted(member.name)} overlaps the next ZIP record",
                _member_location(member.name),
            )
        if has_descriptor:
            descriptor_size = next_offset - data_end
            if descriptor_size == 16:
                if data[data_end : data_end + 4] != _DATA_DESCRIPTOR_SIGNATURE:
                    _reject(
                        ImportCode.MXL_MALFORMED_ARCHIVE,
                        f"data descriptor for {_quoted(member.name)} has an invalid signature",
                        _member_location(member.name),
                    )
                descriptor_offset = data_end + 4
            elif descriptor_size == 12:
                descriptor_offset = data_end
            else:
                _reject(
                    ImportCode.MXL_MALFORMED_ARCHIVE,
                    f"data descriptor for {_quoted(member.name)} is not a bounded 32-bit form",
                    _member_location(member.name),
                )
            descriptor = _DATA_DESCRIPTOR.unpack_from(data, descriptor_offset)
            if descriptor != (
                member.crc32,
                member.compressed_size,
                member.uncompressed_size,
            ):
                _reject(
                    ImportCode.MXL_MALFORMED_ARCHIVE,
                    f"data descriptor disagrees for {_quoted(member.name)}",
                    _member_location(member.name),
                )
        elif data_end != next_offset:
            _reject(
                ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
                f"unsupported bytes follow local data for {_quoted(member.name)}",
                _member_location(member.name),
            )
        prepared_local.append(_PreparedMember(member, data_start, data_end))
        expected_offset = next_offset
    if expected_offset != central_offset:
        _reject(
            ImportCode.MXL_MALFORMED_ARCHIVE,
            "local ZIP records do not meet the central directory continuously",
        )
    by_index = {prepared.member.index: prepared for prepared in prepared_local}
    prepared_central = tuple(by_index[member.index] for member in members)
    return prepared_central, tuple(prepared_local)


def _preflight_archive(data: bytes, limits: ImportLimits) -> _ArchiveLayout:
    central_offset, central_size, entry_count = _find_eocd(data, limits)
    members = _parse_central_directory(
        data,
        central_offset=central_offset,
        central_size=central_size,
        entry_count=entry_count,
        limits=limits,
    )
    prepared_central, prepared_local = _parse_local_records(
        data,
        members,
        central_offset=central_offset,
    )
    container_members = [
        prepared for prepared in prepared_central if prepared.member.name == _CONTAINER_PATH
    ]
    if not container_members:
        _reject(
            ImportCode.MXL_CONTAINER_MISSING,
            f"MXL archive has no exact {_quoted(_CONTAINER_PATH)} member",
        )
    if len(container_members) != 1 or container_members[0].member.is_directory:
        _reject(
            ImportCode.MXL_CONTAINER_INVALID,
            f"{_quoted(_CONTAINER_PATH)} must be one regular member",
        )
    container_size = container_members[0].member.uncompressed_size
    if container_size > limits.max_mxl_container_bytes:
        _reject(
            ImportCode.INPUT_LIMIT_EXCEEDED,
            f"container.xml declares {container_size} bytes; "
            f"limit is {limits.max_mxl_container_bytes}",
            _member_location(_CONTAINER_PATH),
        )

    mimetype = next(
        (prepared for prepared in prepared_central if prepared.member.name == _MIMETYPE_PATH),
        None,
    )
    if mimetype is not None:
        member = mimetype.member
        if (
            member.is_directory
            or member.method != ZIP_STORED
            or not prepared_local
            or prepared_local[0].member.index != member.index
            or member.uncompressed_size != len(_MIMETYPE_BYTES)
        ):
            _reject(
                ImportCode.MXL_MIMETYPE_INVALID,
                "optional mimetype must be the first local member, stored, and exact-sized",
                _member_location(_MIMETYPE_PATH),
            )
    return _ArchiveLayout(prepared_central, prepared_local, central_offset)


def _compare_zipfile_view(archive: ZipFile, layout: _ArchiveLayout) -> tuple[ZipInfo, ...]:
    if archive.comment:
        _reject(
            ImportCode.MXL_ARCHIVE_FEATURE_UNSUPPORTED,
            "ZipFile observed an unsupported archive comment",
        )
    if archive.start_dir != layout.central_offset:
        _reject(
            ImportCode.MXL_MALFORMED_ARCHIVE,
            "ZipFile central-directory offset disagrees with raw preflight",
        )
    infos = tuple(archive.infolist())
    if len(infos) != len(layout.central_members):
        _reject(
            ImportCode.MXL_MALFORMED_ARCHIVE,
            "ZipFile member count disagrees with raw preflight",
        )
    for prepared, info in zip(layout.central_members, infos, strict=True):
        member = prepared.member
        expected = (
            member.name,
            member.version_made & 0xFF,
            member.version_made >> 8,
            member.version_needed & 0xFF,
            member.version_needed >> 8,
            member.flags,
            member.method,
            member.crc32,
            member.compressed_size,
            member.uncompressed_size,
            member.extra,
            b"",
            member.internal_attr,
            member.external_attr,
            member.local_header_offset,
            member.is_directory,
        )
        observed = (
            info.filename,
            info.create_version,
            info.create_system,
            info.extract_version,
            info.reserved,
            info.flag_bits,
            info.compress_type,
            info.CRC,
            info.compress_size,
            info.file_size,
            info.extra,
            info.comment,
            info.internal_attr,
            info.external_attr,
            info.header_offset,
            info.is_dir(),
        )
        if observed != expected or info.orig_filename != member.name:
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"ZipFile metadata disagrees for member {_quoted(member.name)}",
                _member_location(member.name),
            )
    return infos


def _verify_raw_payload(data: bytes, prepared: _PreparedMember) -> None:
    member = prepared.member
    payload = memoryview(data)[prepared.data_start : prepared.data_end]
    actual_size = 0
    actual_crc = 0
    if member.method == ZIP_STORED:
        for offset in range(0, len(payload), _READ_CHUNK):
            chunk = payload[offset : offset + _READ_CHUNK]
            actual_size += len(chunk)
            actual_crc = binascii.crc32(chunk, actual_crc)
    else:
        decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
        try:
            for offset in range(0, len(payload), _READ_CHUNK):
                pending = bytes(payload[offset : offset + _READ_CHUNK])
                if decompressor.eof:
                    _reject(
                        ImportCode.MXL_MALFORMED_ARCHIVE,
                        f"deflated member {_quoted(member.name)} has trailing compressed data",
                        _member_location(member.name),
                    )
                while pending:
                    remaining = member.uncompressed_size - actual_size
                    output = decompressor.decompress(pending, max(1, remaining + 1))
                    actual_size += len(output)
                    actual_crc = binascii.crc32(output, actual_crc)
                    if actual_size > member.uncompressed_size:
                        _reject(
                            ImportCode.MXL_MALFORMED_ARCHIVE,
                            f"deflated member {_quoted(member.name)} expands beyond its "
                            "declared size",
                            _member_location(member.name),
                        )
                    if decompressor.unused_data:
                        _reject(
                            ImportCode.MXL_MALFORMED_ARCHIVE,
                            f"deflated member {_quoted(member.name)} has trailing compressed data",
                            _member_location(member.name),
                        )
                    tail = decompressor.unconsumed_tail
                    if tail and len(tail) == len(pending) and not output:
                        _reject(
                            ImportCode.MXL_MALFORMED_ARCHIVE,
                            f"deflated member {_quoted(member.name)} made no "
                            "decompression progress",
                            _member_location(member.name),
                        )
                    pending = tail
            remaining = member.uncompressed_size - actual_size
            output = decompressor.flush(max(1, remaining + 1))
            actual_size += len(output)
            actual_crc = binascii.crc32(output, actual_crc)
        except zlib.error as exc:
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"deflated member {_quoted(member.name)} is corrupt: {type(exc).__name__}",
                _member_location(member.name),
            )
        if not decompressor.eof or decompressor.unused_data or decompressor.unconsumed_tail:
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                f"deflated member {_quoted(member.name)} has a truncated or trailing stream",
                _member_location(member.name),
            )
    actual_crc &= 0xFFFFFFFF
    if actual_size != member.uncompressed_size:
        _reject(
            ImportCode.MXL_MALFORMED_ARCHIVE,
            f"member {_quoted(member.name)} produced {actual_size} bytes; "
            f"declared {member.uncompressed_size}",
            _member_location(member.name),
        )
    if actual_crc != member.crc32:
        _reject(
            ImportCode.MXL_CRC_MISMATCH,
            f"member {_quoted(member.name)} CRC does not match its central directory",
            _member_location(member.name),
        )


def _read_verified_member(
    archive: ZipFile,
    info: ZipInfo,
    prepared: _PreparedMember,
    raw_archive: bytes,
    *,
    retain: bool,
) -> bytes:
    member = prepared.member
    output = bytearray() if retain else None
    actual_size = 0
    actual_crc = 0
    try:
        with archive.open(info, "r") as stream:
            while True:
                chunk = stream.read(_READ_CHUNK)
                if not chunk:
                    break
                actual_size += len(chunk)
                if actual_size > member.uncompressed_size:
                    _reject(
                        ImportCode.MXL_MALFORMED_ARCHIVE,
                        f"ZipFile expanded {_quoted(member.name)} beyond its declared size",
                        _member_location(member.name),
                    )
                actual_crc = binascii.crc32(chunk, actual_crc)
                if output is not None:
                    output.extend(chunk)
    except BadZipFile as exc:
        code = (
            ImportCode.MXL_CRC_MISMATCH
            if "CRC" in str(exc).upper()
            else ImportCode.MXL_MALFORMED_ARCHIVE
        )
        _reject(
            code,
            f"ZipFile rejected member {_quoted(member.name)}: {type(exc).__name__}",
            _member_location(member.name),
        )
    except (EOFError, OSError, RuntimeError, NotImplementedError) as exc:
        _reject(
            ImportCode.MXL_MALFORMED_ARCHIVE,
            f"cannot read member {_quoted(member.name)}: {type(exc).__name__}",
            _member_location(member.name),
        )
    if actual_size != member.uncompressed_size:
        _reject(
            ImportCode.MXL_MALFORMED_ARCHIVE,
            f"ZipFile produced {actual_size} bytes for {_quoted(member.name)}; "
            f"declared {member.uncompressed_size}",
            _member_location(member.name),
        )
    if actual_crc & 0xFFFFFFFF != member.crc32:
        _reject(
            ImportCode.MXL_CRC_MISMATCH,
            f"member {_quoted(member.name)} CRC does not match its central directory",
            _member_location(member.name),
        )
    _verify_raw_payload(raw_archive, prepared)
    return bytes(output) if output is not None else b""


def _is_xml_whitespace(value: str | None) -> bool:
    return value is None or all(character in " \t\r\n" for character in value)


def _parse_container_xml(
    data: bytes,
    limits: ImportLimits,
) -> tuple[str, tuple[ImportDiagnostic, ...]]:
    try:
        module = cast(_DefusedElementTree, import_module("defusedxml.ElementTree"))
    except ModuleNotFoundError as exc:
        if exc.name is not None and exc.name.split(".", 1)[0] == "defusedxml":
            _reject(
                ImportCode.MISSING_DEPENDENCY,
                "MXL input requires the 'musicxml' extra: install with "
                "`pip install fretsure-oracle[musicxml]`",
            )
        raise

    try:
        parser = module.iterparse(
            BytesIO(data),
            ("start", "end", "start-ns", "comment", "pi"),
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
        element_count = 0
        depth = 0
        for event, _node in parser:
            if event in {"start-ns", "comment", "pi"}:
                _reject(
                    ImportCode.MXL_CONTAINER_INVALID,
                    f"container.xml contains unsupported XML event {event!r}",
                    _member_location(_CONTAINER_PATH),
                )
            if event == "start":
                element_count += 1
                depth += 1
                if element_count > limits.max_elements:
                    _reject(
                        ImportCode.INPUT_LIMIT_EXCEEDED,
                        f"container.xml element count exceeds {limits.max_elements}",
                        _member_location(_CONTAINER_PATH),
                    )
                if depth > limits.max_xml_depth:
                    _reject(
                        ImportCode.INPUT_LIMIT_EXCEEDED,
                        f"container.xml nesting depth exceeds {limits.max_xml_depth}",
                        _member_location(_CONTAINER_PATH),
                    )
            elif event == "end":
                depth -= 1
        root = parser.root
    except _RejectedContainer:
        raise
    except Exception as exc:
        _reject(
            ImportCode.MXL_CONTAINER_INVALID,
            f"container.xml is malformed or unsafe: {type(exc).__name__}",
            _member_location(_CONTAINER_PATH),
        )

    if root.tag != "container" or root.attrib or not _is_xml_whitespace(root.text):
        _reject(
            ImportCode.MXL_CONTAINER_INVALID,
            "container.xml root must be an attribute-free, no-namespace <container>",
            _member_location(_CONTAINER_PATH),
        )
    root_children = list(root)
    if len(root_children) != 1:
        _reject(
            ImportCode.MXL_CONTAINER_INVALID,
            "<container> must contain exactly one <rootfiles>",
            _member_location(_CONTAINER_PATH),
        )
    rootfiles = root_children[0]
    if (
        rootfiles.tag != "rootfiles"
        or rootfiles.attrib
        or not _is_xml_whitespace(rootfiles.text)
        or not _is_xml_whitespace(rootfiles.tail)
    ):
        _reject(
            ImportCode.MXL_CONTAINER_INVALID,
            "container.xml requires one attribute-free, no-namespace <rootfiles>",
            _member_location(_CONTAINER_PATH),
        )
    rootfile_elements = list(rootfiles)
    if any(element.tag != "rootfile" for element in rootfile_elements):
        _reject(
            ImportCode.MXL_CONTAINER_INVALID,
            "<rootfiles> contains an unknown element or namespace",
            _member_location(_CONTAINER_PATH),
        )
    if not rootfile_elements:
        _reject(
            ImportCode.MXL_ROOTFILE_MISSING,
            "container.xml contains no <rootfile>",
            _member_location(_CONTAINER_PATH),
        )
    if len(rootfile_elements) != 1:
        _reject(
            ImportCode.MXL_ROOTFILE_AMBIGUOUS,
            "container.xml must contain exactly one <rootfile>",
            _member_location(_CONTAINER_PATH),
        )
    rootfile = rootfile_elements[0]
    if (
        rootfile.tag != "rootfile"
        or list(rootfile)
        or not _is_xml_whitespace(rootfile.text)
        or not _is_xml_whitespace(rootfile.tail)
        or set(rootfile.attrib) - {"full-path", "media-type"}
    ):
        _reject(
            ImportCode.MXL_CONTAINER_INVALID,
            "<rootfile> contains an unknown element, attribute, namespace, or text",
            _member_location(_CONTAINER_PATH),
        )
    full_path = rootfile.get("full-path")
    if full_path is None or full_path == "":
        _reject(
            ImportCode.MXL_ROOTFILE_MISSING,
            "<rootfile> must provide a non-empty full-path",
            _member_location(_CONTAINER_PATH),
        )
    _validate_path(
        full_path,
        is_directory=False,
        limits=limits,
        code=ImportCode.MXL_ROOTFILE_UNSUPPORTED,
        context="rootfile full-path",
    )
    lowered_path = full_path.lower()
    if not lowered_path.endswith((".musicxml", ".xml")) or full_path == _CONTAINER_PATH:
        _reject(
            ImportCode.MXL_ROOTFILE_UNSUPPORTED,
            "rootfile full-path must select a non-container .musicxml or .xml member",
            _member_location(_CONTAINER_PATH),
        )
    media_type = rootfile.get("media-type")
    warnings: tuple[ImportDiagnostic, ...] = ()
    if media_type is None:
        warnings = (
            _diagnostic(
                ImportCode.MXL_ROOTFILE_MEDIA_TYPE_UNPROVIDED,
                "container rootfile omits media-type; accepted by the extension/path contract",
                SourceLocation(archive_member=_CONTAINER_PATH, element="rootfile"),
                severity=DiagnosticSeverity.WARNING,
            ),
        )
    elif media_type != _ROOT_MEDIA_TYPE:
        _reject(
            ImportCode.MXL_ROOTFILE_UNSUPPORTED,
            f"rootfile media-type must be {_ROOT_MEDIA_TYPE!r}",
            SourceLocation(archive_member=_CONTAINER_PATH, element="rootfile"),
        )
    return full_path, warnings


def _read_container(
    data: bytes,
    limits: ImportLimits,
    layout: _ArchiveLayout,
) -> MXLContainerPayload:
    by_name = {prepared.member.name: prepared for prepared in layout.central_members}
    verified: set[int] = set()
    try:
        with ZipFile(BytesIO(data), mode="r", allowZip64=False) as archive:
            infos = _compare_zipfile_view(archive, layout)

            mimetype = by_name.get(_MIMETYPE_PATH)
            if mimetype is not None:
                mimetype_bytes = _read_verified_member(
                    archive,
                    infos[mimetype.member.index],
                    mimetype,
                    data,
                    retain=True,
                )
                verified.add(mimetype.member.index)
                if mimetype_bytes != _MIMETYPE_BYTES:
                    _reject(
                        ImportCode.MXL_MIMETYPE_INVALID,
                        "optional mimetype member does not contain the exact MXL media type",
                        _member_location(_MIMETYPE_PATH),
                    )

            container = by_name[_CONTAINER_PATH]
            container_bytes = _read_verified_member(
                archive,
                infos[container.member.index],
                container,
                data,
                retain=True,
            )
            verified.add(container.member.index)
            root_path, warnings = _parse_container_xml(container_bytes, limits)
            root_member = by_name.get(root_path)
            if root_member is None:
                _reject(
                    ImportCode.MXL_ROOT_MEMBER_MISSING,
                    f"container rootfile {_quoted(root_path)} is absent from the archive",
                    _member_location(_CONTAINER_PATH),
                )
            if root_member.member.is_directory:
                _reject(
                    ImportCode.MXL_ROOTFILE_UNSUPPORTED,
                    f"container rootfile {_quoted(root_path)} names a directory",
                    _member_location(root_path),
                )
            if root_member.member.uncompressed_size > limits.max_bytes:
                _reject(
                    ImportCode.INPUT_LIMIT_EXCEEDED,
                    f"root MusicXML declares {root_member.member.uncompressed_size} bytes; "
                    f"limit is {limits.max_bytes}",
                    _member_location(root_path),
                )

            root_bytes = b""
            for prepared in layout.central_members:
                index = prepared.member.index
                if index in verified:
                    continue
                retain = index == root_member.member.index
                member_bytes = _read_verified_member(
                    archive,
                    infos[index],
                    prepared,
                    data,
                    retain=retain,
                )
                verified.add(index)
                if retain:
                    root_bytes = member_bytes
            if len(verified) != len(layout.central_members):
                _reject(
                    ImportCode.MXL_MALFORMED_ARCHIVE,
                    "not every archive member was integrity-checked",
                )
            return MXLContainerPayload(root_bytes, root_path, warnings)
    except _RejectedContainer:
        raise
    except (BadZipFile, LargeZipFile) as exc:
        _reject(
            ImportCode.MXL_MALFORMED_ARCHIVE,
            f"ZipFile rejected the preflighted archive: {type(exc).__name__}",
        )


def read_mxl_container(
    data: bytes,
    limits: ImportLimits,
) -> MXLContainerPayload | ImportFailure:
    """Validate an MXL archive and return its sole root MusicXML member.

    The archive is never extracted or written to disk.  Every failure is a
    typed ``ImportFailure``; a success means all members reached EOF and agreed
    with both their raw ZIP metadata and CRC.
    """

    try:
        if type(data) is not bytes:
            _reject(
                ImportCode.MXL_MALFORMED_ARCHIVE,
                "MXL container input must be immutable bytes",
            )
        try:
            limits = snapshot_import_limits(limits)
        except ValueError as exc:
            _reject(
                ImportCode.INPUT_LIMIT_EXCEEDED,
                str(exc),
                SourceLocation(element="limits"),
            )
        layout = _preflight_archive(data, limits)
        return _read_container(data, limits, layout)
    except _RejectedContainer as exc:
        return ImportFailure((exc.diagnostic,))
    except Exception as exc:
        # Hostile archive metadata must never escape as an untyped stdlib
        # exception.  Keep the diagnostic deterministic and avoid reflecting
        # exception text that may contain untrusted member bytes.
        return ImportFailure(
            (
                _diagnostic(
                    ImportCode.MXL_MALFORMED_ARCHIVE,
                    f"unexpected malformed MXL structure: {type(exc).__name__}",
                ),
            )
        )
