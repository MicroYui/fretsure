"""Typed, dependency-free contracts for external score importers."""

from dataclasses import dataclass, fields
from enum import StrEnum
from typing import Literal

from fretsure.ir import MusicIR


class ImportCode(StrEnum):
    """Stable machine-readable importer diagnostic codes."""

    INVALID_INPUT = "INVALID_INPUT"
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    NOT_A_FILE = "NOT_A_FILE"
    FILE_READ_ERROR = "FILE_READ_ERROR"
    UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
    COMPRESSED_MXL_UNSUPPORTED = "COMPRESSED_MXL_UNSUPPORTED"
    INPUT_LIMIT_EXCEEDED = "INPUT_LIMIT_EXCEEDED"
    MALFORMED_XML = "MALFORMED_XML"
    UNSAFE_XML = "UNSAFE_XML"
    UNSUPPORTED_ROOT = "UNSUPPORTED_ROOT"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    UNSUPPORTED_NAMESPACE = "UNSUPPORTED_NAMESPACE"
    MISSING_DEPENDENCY = "MISSING_DEPENDENCY"

    MXL_MALFORMED_ARCHIVE = "MXL_MALFORMED_ARCHIVE"
    MXL_ARCHIVE_FEATURE_UNSUPPORTED = "MXL_ARCHIVE_FEATURE_UNSUPPORTED"
    MXL_ENCRYPTED_MEMBER = "MXL_ENCRYPTED_MEMBER"
    MXL_COMPRESSION_UNSUPPORTED = "MXL_COMPRESSION_UNSUPPORTED"
    MXL_UNSAFE_MEMBER_PATH = "MXL_UNSAFE_MEMBER_PATH"
    MXL_DUPLICATE_MEMBER = "MXL_DUPLICATE_MEMBER"
    MXL_NORMALIZED_COLLISION = "MXL_NORMALIZED_COLLISION"
    MXL_MEMBER_TYPE_UNSUPPORTED = "MXL_MEMBER_TYPE_UNSUPPORTED"
    MXL_CONTAINER_MISSING = "MXL_CONTAINER_MISSING"
    MXL_CONTAINER_INVALID = "MXL_CONTAINER_INVALID"
    MXL_ROOTFILE_MISSING = "MXL_ROOTFILE_MISSING"
    MXL_ROOTFILE_UNSUPPORTED = "MXL_ROOTFILE_UNSUPPORTED"
    MXL_ROOTFILE_AMBIGUOUS = "MXL_ROOTFILE_AMBIGUOUS"
    MXL_ROOT_MEMBER_MISSING = "MXL_ROOT_MEMBER_MISSING"
    MXL_MIMETYPE_INVALID = "MXL_MIMETYPE_INVALID"
    MXL_CRC_MISMATCH = "MXL_CRC_MISMATCH"
    MXL_ROOTFILE_MEDIA_TYPE_UNPROVIDED = "MXL_ROOTFILE_MEDIA_TYPE_UNPROVIDED"

    NO_NOTE_BEARING_PART = "NO_NOTE_BEARING_PART"
    MULTIPLE_NOTE_BEARING_PARTS = "MULTIPLE_NOTE_BEARING_PARTS"
    MULTIPLE_STAVES_UNSUPPORTED = "MULTIPLE_STAVES_UNSUPPORTED"
    MULTIPLE_VOICES_UNSUPPORTED = "MULTIPLE_VOICES_UNSUPPORTED"
    CHORD_NOTATION_UNSUPPORTED = "CHORD_NOTATION_UNSUPPORTED"
    REPEAT_UNSUPPORTED = "REPEAT_UNSUPPORTED"
    ENDING_UNSUPPORTED = "ENDING_UNSUPPORTED"
    NAVIGATION_UNSUPPORTED = "NAVIGATION_UNSUPPORTED"
    MEASURE_REPEAT_UNSUPPORTED = "MEASURE_REPEAT_UNSUPPORTED"
    MULTIPLE_REST_UNSUPPORTED = "MULTIPLE_REST_UNSUPPORTED"
    TIMELINE_CONTROL_UNSUPPORTED = "TIMELINE_CONTROL_UNSUPPORTED"
    TUPLET_UNSUPPORTED = "TUPLET_UNSUPPORTED"
    GRACE_NOTE_UNSUPPORTED = "GRACE_NOTE_UNSUPPORTED"
    CUE_NOTE_UNSUPPORTED = "CUE_NOTE_UNSUPPORTED"
    UNPITCHED_UNSUPPORTED = "UNPITCHED_UNSUPPORTED"
    MICROTONE_UNSUPPORTED = "MICROTONE_UNSUPPORTED"
    TRANSPOSE_UNSUPPORTED = "TRANSPOSE_UNSUPPORTED"
    PICKUP_UNSUPPORTED = "PICKUP_UNSUPPORTED"
    INCOMPLETE_MEASURE = "INCOMPLETE_MEASURE"

    MISSING_DIVISIONS = "MISSING_DIVISIONS"
    INVALID_DIVISIONS = "INVALID_DIVISIONS"
    DIVISIONS_CHANGE_UNSUPPORTED = "DIVISIONS_CHANGE_UNSUPPORTED"
    MISSING_KEY = "MISSING_KEY"
    UNSUPPORTED_KEY = "UNSUPPORTED_KEY"
    KEY_CHANGE_UNSUPPORTED = "KEY_CHANGE_UNSUPPORTED"
    MISSING_TIME_SIGNATURE = "MISSING_TIME_SIGNATURE"
    UNSUPPORTED_TIME_SIGNATURE = "UNSUPPORTED_TIME_SIGNATURE"
    TIME_SIGNATURE_CHANGE_UNSUPPORTED = "TIME_SIGNATURE_CHANGE_UNSUPPORTED"
    MISSING_TEMPO = "MISSING_TEMPO"
    UNSUPPORTED_TEMPO = "UNSUPPORTED_TEMPO"
    TEMPO_CHANGE_UNSUPPORTED = "TEMPO_CHANGE_UNSUPPORTED"

    SLASH_BASS_UNSUPPORTED = "SLASH_BASS_UNSUPPORTED"
    MISSING_HARMONY = "MISSING_HARMONY"
    DUPLICATE_HARMONY = "DUPLICATE_HARMONY"
    UNSUPPORTED_HARMONY_TYPE = "UNSUPPORTED_HARMONY_TYPE"
    STACKED_HARMONY_UNSUPPORTED = "STACKED_HARMONY_UNSUPPORTED"
    HARMONY_OFFSET_UNSUPPORTED = "HARMONY_OFFSET_UNSUPPORTED"
    NO_CHORD_UNSUPPORTED = "NO_CHORD_UNSUPPORTED"
    FUNCTION_HARMONY_UNSUPPORTED = "FUNCTION_HARMONY_UNSUPPORTED"
    NUMERAL_HARMONY_UNSUPPORTED = "NUMERAL_HARMONY_UNSUPPORTED"
    HARMONY_DEGREE_UNSUPPORTED = "HARMONY_DEGREE_UNSUPPORTED"
    UNSUPPORTED_HARMONY_KIND = "UNSUPPORTED_HARMONY_KIND"
    PERFORMANCE_NOTATION_UNSUPPORTED = "PERFORMANCE_NOTATION_UNSUPPORTED"
    UNSUPPORTED_NOTE = "UNSUPPORTED_NOTE"
    UNSUPPORTED_ELEMENT = "UNSUPPORTED_ELEMENT"
    TIE_ERROR = "TIE_ERROR"

    IGNORED_NOTATION = "IGNORED_NOTATION"
    RIGHTS_UNPROVIDED = "RIGHTS_UNPROVIDED"

    IR_INVALID = "IR_INVALID"
    ADAPTER_ERROR = "ADAPTER_ERROR"


class DiagnosticSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True)
class SourceLocation:
    part_id: str | None = None
    measure: str | None = None
    voice: str | None = None
    element: str | None = None
    archive_member: str | None = None


@dataclass(frozen=True, slots=True)
class ImportDiagnostic:
    code: ImportCode
    severity: DiagnosticSeverity
    message: str
    location: SourceLocation | None = None


@dataclass(frozen=True, slots=True)
class ImportProvenance:
    """Structured source identity retained across archive canonicalization."""

    source_filename: str
    source_format: Literal["musicxml", "mxl"]
    raw_sha256: str
    root_member: str | None
    root_sha256: str
    container_version: str | None


@dataclass(frozen=True, slots=True)
class ImportSuccess:
    ir: MusicIR
    warnings: tuple[ImportDiagnostic, ...]
    importer_version: str
    sha256: str
    provenance: ImportProvenance | None = None

    @property
    def rootfile_path(self) -> str | None:
        """Exact archive member selected by container.xml, if any."""

        return None if self.provenance is None else self.provenance.root_member


@dataclass(frozen=True, slots=True)
class ImportFailure:
    diagnostics: tuple[ImportDiagnostic, ...]


MusicXMLImportResult = ImportSuccess | ImportFailure


@dataclass(frozen=True, slots=True)
class ImportLimits:
    """Hard resource ceilings; a zero value intentionally rejects any occurrence."""

    max_bytes: int = 10 * 1024 * 1024
    max_xml_depth: int = 64
    max_elements: int = 250_000
    max_measures: int = 5_000
    max_notes: int = 20_000
    max_harmonies: int = 20_000
    max_decimal_chars: int = 128
    max_mxl_archive_bytes: int = 20 * 1024 * 1024
    max_mxl_members: int = 256
    max_mxl_central_directory_bytes: int = 1 * 1024 * 1024
    max_mxl_member_name_bytes: int = 1024
    max_mxl_path_depth: int = 32
    max_mxl_container_bytes: int = 64 * 1024
    max_mxl_member_bytes: int = 16 * 1024 * 1024
    max_mxl_total_uncompressed_bytes: int = 32 * 1024 * 1024
    max_mxl_member_ratio: int = 100
    max_mxl_total_ratio: int = 100

    def __post_init__(self) -> None:
        for field in fields(self):
            value = object.__getattribute__(self, field.name)
            if type(value) is not int or value < 0 or value.bit_length() > 63:
                raise ValueError(
                    f"{field.name} must be an exact non-negative signed-63-bit integer"
                )


def snapshot_import_limits(value: object) -> ImportLimits:
    """Return a detached exact limits object or raise a bounded ``ValueError``."""

    if type(value) is not ImportLimits:
        raise ValueError("limits must be an exact ImportLimits instance")
    values: dict[str, int] = {}
    for field in fields(ImportLimits):
        try:
            raw = object.__getattribute__(value, field.name)
        except (AttributeError, TypeError):
            raise ValueError(f"limits.{field.name} is missing") from None
        if type(raw) is not int or raw < 0 or raw.bit_length() > 63:
            raise ValueError(
                f"limits.{field.name} must be an exact non-negative signed-63-bit integer"
            )
        values[field.name] = raw
    return ImportLimits(**values)


DEFAULT_LIMITS = ImportLimits()
