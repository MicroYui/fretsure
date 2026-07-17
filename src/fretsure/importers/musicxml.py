"""Safe public entry point for the frozen MusicXML/MXL semantic subset."""

from __future__ import annotations

import hashlib
import os
import stat
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from importlib import import_module
from io import BytesIO
from pathlib import Path
from typing import Literal, Protocol, cast

from fretsure.importers._music21_adapter import (
    Music21AdapterError,
    MusicXMLDependencyError,
    music21_to_ir,
)
from fretsure.importers._musicxml_preflight import preflight_musicxml
from fretsure.importers._mxl_container import (
    MXL_CONTAINER_VERSION,
    MXLContainerPayload,
    read_mxl_container,
)
from fretsure.importers.contracts import (
    DEFAULT_LIMITS,
    DiagnosticSeverity,
    ImportCode,
    ImportDiagnostic,
    ImportFailure,
    ImportLimits,
    ImportProvenance,
    ImportSuccess,
    MusicXMLImportResult,
    SourceLocation,
    snapshot_import_limits,
)
from fretsure.ir import IRInputError, snapshot_music_ir, validate_ir

MUSICXML_IMPORTER_VERSION = "musicxml@0.3.0"
# Backwards-compatible alias retained for callers of the original single-format API.
IMPORTER_VERSION = MUSICXML_IMPORTER_VERSION

_MUSICXML_NAMESPACE = "http://www.musicxml.org/ns/musicxml"
_SUPPORTED_EXTENSIONS = frozenset({".musicxml", ".xml", ".mxl"})
_FILE_READ_CHUNK = 64 * 1024
_MAX_SOURCE_FILENAME_BYTES = 1024
_MAX_ADAPTER_VALIDATION_DIAGNOSTICS = 256


class _IterParse(Protocol):
    root: ET.Element

    def __iter__(self) -> Iterator[tuple[str, ET.Element]]: ...


class _DefusedElementTree(Protocol):
    def iterparse(
        self,
        source: BytesIO,
        events: tuple[str, str],
        *,
        forbid_dtd: bool,
        forbid_entities: bool,
        forbid_external: bool,
    ) -> _IterParse: ...


class _ParseFailure(Exception):
    def __init__(self, diagnostic: ImportDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(diagnostic.message)


def _diagnostic(
    code: ImportCode, message: str, location: SourceLocation | None = None
) -> ImportDiagnostic:
    return ImportDiagnostic(code, DiagnosticSeverity.ERROR, message, location)


def _one_failure(
    code: ImportCode, message: str, location: SourceLocation | None = None
) -> ImportFailure:
    return ImportFailure((_diagnostic(code, message, location),))


def _read_bounded(path: Path, limits: ImportLimits) -> bytes | ImportFailure:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return _one_failure(ImportCode.FILE_NOT_FOUND, f"MusicXML file not found: {path}")
    except OSError as exc:
        return _one_failure(
            ImportCode.FILE_READ_ERROR,
            f"cannot open MusicXML file {path}: {type(exc).__name__}: {exc}",
        )
    result: bytes | ImportFailure
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            result = _one_failure(
                ImportCode.NOT_A_FILE,
                f"MusicXML path is not a regular file: {path}",
            )
        else:
            suffix = path.suffix.lower()
            if suffix not in _SUPPORTED_EXTENSIONS:
                result = _one_failure(
                    ImportCode.UNSUPPORTED_FILE_TYPE,
                    f"unsupported input suffix {path.suffix!r}; "
                    "expected .musicxml, .xml, or .mxl",
                )
            else:
                byte_limit = (
                    limits.max_mxl_archive_bytes if suffix == ".mxl" else limits.max_bytes
                )
                if before.st_size > byte_limit:
                    result = _one_failure(
                        ImportCode.INPUT_LIMIT_EXCEEDED,
                        f"input is {before.st_size} bytes; limit is {byte_limit}",
                        SourceLocation(element="file"),
                    )
                else:
                    output = bytearray()
                    while True:
                        # Once the declared limit is reached, read exactly one
                        # byte to distinguish EOF from a concurrently grown file.
                        read_size = min(_FILE_READ_CHUNK, byte_limit - len(output) + 1)
                        chunk = os.read(descriptor, read_size)
                        if not chunk:
                            break
                        output.extend(chunk)
                        if len(output) > byte_limit:
                            break
                    if len(output) > byte_limit:
                        result = _one_failure(
                            ImportCode.INPUT_LIMIT_EXCEEDED,
                            f"input exceeds {byte_limit} bytes",
                            SourceLocation(element="file"),
                        )
                    else:
                        after = os.fstat(descriptor)
                        stable_before = (
                            before.st_dev,
                            before.st_ino,
                            stat.S_IFMT(before.st_mode),
                            before.st_nlink,
                            before.st_size,
                            before.st_mtime_ns,
                            before.st_ctime_ns,
                        )
                        stable_after = (
                            after.st_dev,
                            after.st_ino,
                            stat.S_IFMT(after.st_mode),
                            after.st_nlink,
                            after.st_size,
                            after.st_mtime_ns,
                            after.st_ctime_ns,
                        )
                        if stable_after != stable_before or len(output) != before.st_size:
                            result = _one_failure(
                                ImportCode.FILE_READ_ERROR,
                                "MusicXML file changed while it was being read",
                                SourceLocation(element="file"),
                            )
                        else:
                            result = bytes(output)
    except OSError as exc:
        result = _one_failure(
            ImportCode.FILE_READ_ERROR,
            f"cannot read MusicXML file {path}: {type(exc).__name__}: {exc}",
        )
    try:
        os.close(descriptor)
    except OSError as exc:
        if not isinstance(result, ImportFailure):
            return _one_failure(
                ImportCode.FILE_READ_ERROR,
                f"cannot close MusicXML file {path}: {type(exc).__name__}: {exc}",
            )
    return result


def _validate_source_filename(filename: object) -> str | ImportFailure:
    """Validate an inert source identity and return its normalized suffix."""

    location = SourceLocation(element="filename")
    if type(filename) is not str:
        return _one_failure(
            ImportCode.INVALID_INPUT,
            "filename must be an exact str",
            location,
        )
    if not filename:
        return _one_failure(
            ImportCode.INVALID_INPUT,
            "filename must not be empty",
            location,
        )
    # Every Unicode scalar consumes at least one UTF-8 byte.  Check this cheap
    # lower bound before encoding so an already-oversized name cannot force a
    # second, potentially very large allocation.
    if len(filename) > _MAX_SOURCE_FILENAME_BYTES:
        return _one_failure(
            ImportCode.INPUT_LIMIT_EXCEEDED,
            f"filename exceeds {_MAX_SOURCE_FILENAME_BYTES} UTF-8 bytes",
            location,
        )
    try:
        encoded_size = len(filename.encode("utf-8"))
    except UnicodeEncodeError:
        return _one_failure(
            ImportCode.INVALID_INPUT,
            "filename must be valid Unicode without lone surrogates",
            location,
        )
    if encoded_size > _MAX_SOURCE_FILENAME_BYTES:
        return _one_failure(
            ImportCode.INPUT_LIMIT_EXCEEDED,
            f"filename exceeds {_MAX_SOURCE_FILENAME_BYTES} UTF-8 bytes",
            location,
        )
    if "/" in filename or "\\" in filename:
        return _one_failure(
            ImportCode.INVALID_INPUT,
            "filename must be a basename without path separators",
            location,
        )
    if filename in {".", ".."}:
        return _one_failure(
            ImportCode.INVALID_INPUT,
            "filename must not be '.' or '..'",
            location,
        )
    if (
        len(filename) >= 2
        and filename[0].isascii()
        and filename[0].isalpha()
        and filename[1] == ":"
    ):
        return _one_failure(
            ImportCode.INVALID_INPUT,
            "filename must not contain a Windows drive prefix",
            location,
        )
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in filename):
        return _one_failure(
            ImportCode.INVALID_INPUT,
            "filename must not contain control or format characters",
            location,
        )

    dot = filename.rfind(".")
    suffix = filename[dot:].lower() if dot > 0 else ""
    if suffix not in _SUPPORTED_EXTENSIONS:
        return _one_failure(
            ImportCode.UNSUPPORTED_FILE_TYPE,
            "unsupported input suffix; expected .musicxml, .xml, or .mxl",
            location,
        )
    return suffix


def validate_musicxml_filename(filename: object) -> str | ImportFailure:
    """Validate one inert public score filename without reading or parsing bytes."""

    return _validate_source_filename(filename)


def _safe_parse(data: bytes, limits: ImportLimits) -> ET.Element:
    try:
        module = cast(_DefusedElementTree, import_module("defusedxml.ElementTree"))
    except ModuleNotFoundError as exc:
        if exc.name is not None and exc.name.split(".", 1)[0] == "defusedxml":
            raise _ParseFailure(
                _diagnostic(
                    ImportCode.MISSING_DEPENDENCY,
                    "MusicXML input requires the 'musicxml' extra: install with "
                    "`pip install fretsure-oracle[musicxml]`",
                )
            ) from exc
        raise

    try:
        parser = module.iterparse(
            BytesIO(data),
            ("start", "end"),
            forbid_dtd=False,
            forbid_entities=True,
            forbid_external=True,
        )
        element_count = 0
        depth = 0
        for event, _element in parser:
            if event == "start":
                element_count += 1
                depth += 1
                if element_count > limits.max_elements:
                    raise _ParseFailure(
                        _diagnostic(
                            ImportCode.INPUT_LIMIT_EXCEEDED,
                            f"XML element count exceeds {limits.max_elements}",
                            SourceLocation(element="xml"),
                        )
                    )
                if depth > limits.max_xml_depth:
                    raise _ParseFailure(
                        _diagnostic(
                            ImportCode.INPUT_LIMIT_EXCEEDED,
                            f"XML nesting depth exceeds {limits.max_xml_depth}",
                            SourceLocation(element="xml"),
                        )
                    )
            else:
                depth -= 1
        return parser.root
    except _ParseFailure:
        raise
    except Exception as exc:
        if exc.__class__.__module__.startswith("defusedxml"):
            raise _ParseFailure(
                _diagnostic(
                    ImportCode.UNSAFE_XML,
                    f"unsafe XML construct rejected: {type(exc).__name__}",
                    SourceLocation(element="xml"),
                )
            ) from exc
        raise _ParseFailure(
            _diagnostic(
                ImportCode.MALFORMED_XML,
                f"malformed MusicXML: {type(exc).__name__}: {exc}",
                SourceLocation(element="xml"),
            )
        ) from exc


def _split_tag(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        namespace, local = tag[1:].split("}", 1)
        return namespace, local
    return "", tag


def _validate_envelope(root: ET.Element) -> ImportDiagnostic | None:
    root_namespace, root_name = _split_tag(root.tag)
    if root_name != "score-partwise":
        return _diagnostic(
            ImportCode.UNSUPPORTED_ROOT,
            f"expected score-partwise MusicXML, got {root_name!r}",
            SourceLocation(element=root_name),
        )
    if root_namespace not in {"", _MUSICXML_NAMESPACE}:
        return _diagnostic(
            ImportCode.UNSUPPORTED_NAMESPACE,
            f"unsupported MusicXML namespace {root_namespace!r}",
            SourceLocation(element=root_name),
        )
    if root.get("version") not in {"3.1", "4.0"}:
        return _diagnostic(
            ImportCode.UNSUPPORTED_VERSION,
            f"supported MusicXML versions are 3.1 and 4.0, got {root.get('version')!r}",
            SourceLocation(element=root_name),
        )
    for element in root.iter():
        namespace, local = _split_tag(element.tag)
        if namespace != root_namespace:
            return _diagnostic(
                ImportCode.UNSUPPORTED_NAMESPACE,
                f"mixed or foreign namespace {namespace!r} on <{local}>",
                SourceLocation(element=local),
            )
    return None


def _strip_namespaces(root: ET.Element) -> None:
    """Normalize the already-validated optional MusicXML namespace in place."""

    for element in root.iter():
        _namespace, local = _split_tag(element.tag)
        element.tag = local


def _append_text(parent: ET.Element, tag: str, source: ET.Element | None) -> None:
    if source is None:
        return
    child = ET.SubElement(parent, tag)
    child.text = (source.text or "").strip(" \t\r\n")


def _music21_adapter_xml(root: ET.Element, note_part_id: str) -> bytes:
    """Build the minimum preflight-approved event tree handed to music21.

    Raw preflight remains the semantic and diagnostic authority.  Metadata,
    visual annotations, instruments, credits, and ignored notation never need
    to enter the third-party parser and are intentionally absent here.
    """

    adapter_root = ET.Element("score-partwise", {"version": root.get("version", "4.0")})
    part_list = ET.SubElement(adapter_root, "part-list")
    score_part = ET.SubElement(part_list, "score-part", {"id": "P1"})
    part_name = ET.SubElement(score_part, "part-name")
    part_name.text = "Fretsure adapter input"
    adapter_part = ET.SubElement(adapter_root, "part", {"id": "P1"})

    source_parts = [
        part
        for part in root.findall("part")
        if (part.get("id") or "") == note_part_id
        and any(note.find("pitch") is not None for note in part.iter("note"))
    ]
    if len(source_parts) != 1:
        raise ValueError("preflight note part is not uniquely available for adapter input")
    source_part = source_parts[0]

    for measure_index, source_measure in enumerate(source_part.findall("measure"), start=1):
        adapter_measure = ET.SubElement(
            adapter_part,
            "measure",
            {"number": str(measure_index)},
        )
        for source_child in source_measure:
            if source_child.tag == "attributes":
                divisions = source_child.findall("divisions")
                if divisions:
                    adapter_attributes = ET.SubElement(adapter_measure, "attributes")
                    _append_text(adapter_attributes, "divisions", divisions[0])
            elif source_child.tag == "harmony":
                adapter_harmony = ET.SubElement(adapter_measure, "harmony")
                source_root = source_child.find("root")
                if source_root is not None:
                    adapter_root_note = ET.SubElement(adapter_harmony, "root")
                    _append_text(adapter_root_note, "root-step", source_root.find("root-step"))
                    _append_text(
                        adapter_root_note,
                        "root-alter",
                        source_root.find("root-alter"),
                    )
                _append_text(adapter_harmony, "kind", source_child.find("kind"))
            elif source_child.tag == "note":
                adapter_note = ET.SubElement(adapter_measure, "note")
                source_pitch = source_child.find("pitch")
                if source_pitch is not None:
                    adapter_pitch = ET.SubElement(adapter_note, "pitch")
                    for pitch_tag in ("step", "alter", "octave"):
                        _append_text(adapter_pitch, pitch_tag, source_pitch.find(pitch_tag))
                elif source_child.find("rest") is not None:
                    ET.SubElement(adapter_note, "rest")
                _append_text(adapter_note, "duration", source_child.find("duration"))

                sound_ties = source_child.findall("tie")
                tie_sources = sound_ties or source_child.findall("notations/tied")
                for source_tie in tie_sources:
                    tie_type = source_tie.get("type")
                    if tie_type is not None:
                        ET.SubElement(adapter_note, "tie", {"type": tie_type})

    return cast(
        bytes,
        ET.tostring(adapter_root, encoding="utf-8", xml_declaration=True),
    )


def _import_musicxml_bytes_snapshot(
    data: bytes,
    filename: str,
    suffix: str,
    limits: ImportLimits,
) -> MusicXMLImportResult:
    """Import already-snapshotted, exact inputs through the shared semantic path."""

    byte_limit = limits.max_mxl_archive_bytes if suffix == ".mxl" else limits.max_bytes
    if len(data) > byte_limit:
        return _one_failure(
            ImportCode.INPUT_LIMIT_EXCEEDED,
            f"input is {len(data)} bytes; limit is {byte_limit}",
            SourceLocation(element="file"),
        )

    sha256 = hashlib.sha256(data).hexdigest()
    root_bytes = data
    root_path: str | None = None
    container_version: str | None = None
    container_warnings: tuple[ImportDiagnostic, ...] = ()
    source_format: Literal["musicxml", "mxl"] = "musicxml"
    if suffix == ".mxl":
        container = read_mxl_container(data, limits)
        if isinstance(container, ImportFailure):
            return container
        assert isinstance(container, MXLContainerPayload)
        root_bytes = container.root_bytes
        root_path = container.root_path
        container_warnings = container.warnings
        container_version = MXL_CONTAINER_VERSION
        source_format = "mxl"
    root_sha256 = hashlib.sha256(root_bytes).hexdigest()
    try:
        root = _safe_parse(root_bytes, limits)
    except _ParseFailure as exc:
        return ImportFailure((*container_warnings, exc.diagnostic))

    envelope_error = _validate_envelope(root)
    if envelope_error is not None:
        return ImportFailure((*container_warnings, envelope_error))
    _strip_namespaces(root)
    preflight = preflight_musicxml(root, limits)
    errors = tuple(
        diagnostic
        for diagnostic in preflight.diagnostics
        if diagnostic.severity is DiagnosticSeverity.ERROR
    )
    if errors:
        return ImportFailure((*container_warnings, *preflight.diagnostics))
    if preflight.metadata is None:
        return ImportFailure(
            (
                *container_warnings,
                _diagnostic(
                    ImportCode.ADAPTER_ERROR,
                    "preflight produced no normalized metadata despite having no errors",
                ),
            )
        )

    try:
        canonical = _music21_adapter_xml(root, preflight.metadata.note_part_id)
    except (TypeError, ValueError) as exc:
        return ImportFailure(
            (
                *container_warnings,
                _diagnostic(
                    ImportCode.ADAPTER_ERROR,
                    f"cannot construct bounded MusicXML adapter input: {type(exc).__name__}",
                ),
            )
        )

    try:
        ir = music21_to_ir(
            canonical,
            metadata=preflight.metadata,
            source_filename=filename,
            source_format=source_format,
            sha256=sha256,
            root_member=root_path,
            root_sha256=root_sha256,
            container_version=container_version,
            importer_version=IMPORTER_VERSION,
        )
    except MusicXMLDependencyError as exc:
        return ImportFailure(
            (
                *container_warnings,
                _diagnostic(
                    ImportCode.MISSING_DEPENDENCY,
                    f"MusicXML input requires {exc.package}; install the 'musicxml' extra",
                ),
            )
        )
    except Music21AdapterError as exc:
        return ImportFailure(
            (*container_warnings, _diagnostic(ImportCode.ADAPTER_ERROR, str(exc)))
        )
    except Exception as exc:
        return ImportFailure(
            (
                *container_warnings,
                _diagnostic(
                    ImportCode.ADAPTER_ERROR,
                    f"unexpected MusicXML adapter failure: {type(exc).__name__}",
                ),
            )
        )

    try:
        ir = snapshot_music_ir(ir)
    except IRInputError as exc:
        return ImportFailure(
            (
                *container_warnings,
                _diagnostic(
                    ImportCode.IR_INVALID,
                    f"{exc.field}: {exc.detail}",
                    SourceLocation(element="MusicIR"),
                ),
            )
        )

    violations = validate_ir(ir)
    if violations:
        violation_diagnostics = tuple(
            _diagnostic(
                ImportCode.IR_INVALID,
                f"{violation.kind}: {violation.detail}",
                SourceLocation(element="MusicIR"),
            )
            for violation in violations[:_MAX_ADAPTER_VALIDATION_DIAGNOSTICS]
        )
        overflow = (
            (
                _diagnostic(
                    ImportCode.INPUT_LIMIT_EXCEEDED,
                    "MusicIR validation produced more than "
                    f"{_MAX_ADAPTER_VALIDATION_DIAGNOSTICS} diagnostics; "
                    "remaining diagnostics were omitted",
                    SourceLocation(element="diagnostics"),
                ),
            )
            if len(violations) > _MAX_ADAPTER_VALIDATION_DIAGNOSTICS
            else ()
        )
        return ImportFailure(
            container_warnings + violation_diagnostics + overflow
        )
    warnings = container_warnings + tuple(
        diagnostic
        for diagnostic in preflight.diagnostics
        if diagnostic.severity is DiagnosticSeverity.WARNING
    )
    provenance = ImportProvenance(
        filename,
        source_format,
        sha256,
        root_path,
        root_sha256,
        container_version,
    )
    return ImportSuccess(ir, warnings, IMPORTER_VERSION, sha256, provenance)


def _snapshot_limits(limits: object) -> ImportLimits | ImportFailure:
    try:
        return snapshot_import_limits(limits)
    except ValueError as exc:
        return _one_failure(
            ImportCode.INPUT_LIMIT_EXCEEDED,
            str(exc),
            SourceLocation(element="limits"),
        )


def import_musicxml_bytes(
    data: bytes,
    filename: str,
    *,
    limits: ImportLimits = DEFAULT_LIMITS,
) -> MusicXMLImportResult:
    """Safely import exact in-memory MusicXML/MXL bytes with an inert filename."""

    snapshot = _snapshot_limits(limits)
    if isinstance(snapshot, ImportFailure):
        return snapshot
    if type(data) is not bytes:
        return _one_failure(
            ImportCode.INVALID_INPUT,
            "data must be exact bytes",
            SourceLocation(element="data"),
        )
    suffix = _validate_source_filename(filename)
    if isinstance(suffix, ImportFailure):
        return suffix
    return _import_musicxml_bytes_snapshot(data, filename, suffix, snapshot)


def import_musicxml(path: Path, *, limits: ImportLimits = DEFAULT_LIMITS) -> MusicXMLImportResult:
    """Safely read and import one supported MusicXML or compressed MXL file."""

    snapshot = _snapshot_limits(limits)
    if isinstance(snapshot, ImportFailure):
        return snapshot
    raw = _read_bounded(path, snapshot)
    if isinstance(raw, ImportFailure):
        return raw
    filename = path.name
    suffix = _validate_source_filename(filename)
    if isinstance(suffix, ImportFailure):
        return suffix
    return _import_musicxml_bytes_snapshot(raw, filename, suffix, snapshot)
