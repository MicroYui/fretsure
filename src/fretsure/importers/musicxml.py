"""Safe public entry point for the frozen MusicXML/MXL semantic subset."""

from __future__ import annotations

import hashlib
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
from fretsure.ir import validate_ir

IMPORTER_VERSION = "musicxml@0.2.0"

_MUSICXML_NAMESPACE = "http://www.musicxml.org/ns/musicxml"
_SUPPORTED_EXTENSIONS = frozenset({".musicxml", ".xml", ".mxl"})
_FILE_READ_CHUNK = 64 * 1024


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
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return _one_failure(ImportCode.FILE_NOT_FOUND, f"MusicXML file not found: {path}")
    except OSError as exc:
        return _one_failure(
            ImportCode.FILE_READ_ERROR,
            f"cannot stat MusicXML file {path}: {type(exc).__name__}: {exc}",
        )
    if not path.is_file():
        return _one_failure(ImportCode.NOT_A_FILE, f"MusicXML path is not a regular file: {path}")

    suffix = path.suffix.lower()
    if suffix not in _SUPPORTED_EXTENSIONS:
        return _one_failure(
            ImportCode.UNSUPPORTED_FILE_TYPE,
            f"unsupported input suffix {path.suffix!r}; expected .musicxml, .xml, or .mxl",
        )
    byte_limit = limits.max_mxl_archive_bytes if suffix == ".mxl" else limits.max_bytes
    if size > byte_limit:
        return _one_failure(
            ImportCode.INPUT_LIMIT_EXCEEDED,
            f"input is {size} bytes; limit is {byte_limit}",
            SourceLocation(element="file"),
        )
    try:
        with path.open("rb") as handle:
            output = bytearray()
            while True:
                # Read in fixed chunks so a valid signed-63 custom limit never
                # reaches a platform-sized ``read(n)`` conversion.  Once the
                # declared limit is reached, read exactly one byte to distinguish
                # EOF from a concurrently grown/oversized file.
                read_size = min(_FILE_READ_CHUNK, byte_limit - len(output) + 1)
                chunk = handle.read(read_size)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > byte_limit:
                    return _one_failure(
                        ImportCode.INPUT_LIMIT_EXCEEDED,
                        f"input exceeds {byte_limit} bytes",
                        SourceLocation(element="file"),
                    )
    except OSError as exc:
        return _one_failure(
            ImportCode.FILE_READ_ERROR,
            f"cannot read MusicXML file {path}: {type(exc).__name__}: {exc}",
        )
    return bytes(output)


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


def _canonical_xml(root: ET.Element) -> bytes:
    # Envelope validation has already proved every element is in the same
    # supported namespace.  Removing that optional namespace gives music21 a
    # producer-independent canonical form, while serialization drops DTDs,
    # entity declarations, comments and processing instructions.
    for element in root.iter():
        _namespace, local = _split_tag(element.tag)
        element.tag = local
    return cast(bytes, ET.tostring(root, encoding="utf-8", xml_declaration=True))


def import_musicxml(path: Path, *, limits: ImportLimits = DEFAULT_LIMITS) -> MusicXMLImportResult:
    """Safely import one supported MusicXML or compressed MXL score."""

    try:
        limits = snapshot_import_limits(limits)
    except ValueError as exc:
        return _one_failure(
            ImportCode.INPUT_LIMIT_EXCEEDED,
            str(exc),
            SourceLocation(element="limits"),
        )

    raw = _read_bounded(path, limits)
    if isinstance(raw, ImportFailure):
        return raw
    sha256 = hashlib.sha256(raw).hexdigest()
    root_bytes = raw
    root_path: str | None = None
    container_version: str | None = None
    container_warnings: tuple[ImportDiagnostic, ...] = ()
    source_format: Literal["musicxml", "mxl"] = "musicxml"
    if path.suffix.lower() == ".mxl":
        container = read_mxl_container(raw, limits)
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
    canonical = _canonical_xml(root)
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
        ir = music21_to_ir(
            canonical,
            metadata=preflight.metadata,
            source_filename=path.name,
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

    violations = validate_ir(ir)
    if violations:
        return ImportFailure(
            container_warnings
            + tuple(
                _diagnostic(
                    ImportCode.IR_INVALID,
                    f"{violation.kind}: {violation.detail}",
                    SourceLocation(element="MusicIR"),
                )
                for violation in violations
            )
        )
    warnings = container_warnings + tuple(
        diagnostic
        for diagnostic in preflight.diagnostics
        if diagnostic.severity is DiagnosticSeverity.WARNING
    )
    provenance = ImportProvenance(
        path.name,
        source_format,
        sha256,
        root_path,
        root_sha256,
        container_version,
    )
    return ImportSuccess(ir, warnings, IMPORTER_VERSION, sha256, provenance)
