"""Safe public entry point for the frozen Standard MIDI File subset."""

from __future__ import annotations

import hashlib
import os
import stat
import unicodedata
from pathlib import Path
from urllib.parse import quote

from fretsure.importers._midi_preflight import preflight_midi
from fretsure.importers._music21_midi_adapter import (
    MIDIDependencyError,
    Music21MIDIAdapterError,
    crosscheck_music21_midi,
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
    MIDIImportResult,
    SourceLocation,
    snapshot_import_limits,
)
from fretsure.ir import IRInputError, Meta, MusicIR, Note, snapshot_music_ir, validate_ir

MIDI_IMPORTER_VERSION = "midi@0.1.0"
IMPORTER_VERSION = MIDI_IMPORTER_VERSION

_SUPPORTED_EXTENSIONS = frozenset({".mid", ".midi"})
_FILE_READ_CHUNK = 64 * 1024
_MAX_SOURCE_FILENAME_BYTES = 1024
_MAX_IR_DIAGNOSTICS = 256


def _diagnostic(
    code: ImportCode,
    message: str,
    location: SourceLocation | None = None,
) -> ImportDiagnostic:
    return ImportDiagnostic(code, DiagnosticSeverity.ERROR, message, location)


def _one_failure(
    code: ImportCode,
    message: str,
    location: SourceLocation | None = None,
) -> ImportFailure:
    return ImportFailure((_diagnostic(code, message, location),))


def _validate_source_filename(filename: object) -> str | ImportFailure:
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
            "unsupported input suffix; expected .mid or .midi",
            location,
        )
    return suffix


def validate_midi_filename(filename: object) -> str | ImportFailure:
    """Validate an inert public MIDI filename without reading its bytes."""

    return _validate_source_filename(filename)


def _snapshot_limits(limits: object) -> ImportLimits | ImportFailure:
    try:
        return snapshot_import_limits(limits)
    except ValueError as exc:
        return _one_failure(
            ImportCode.INPUT_LIMIT_EXCEEDED,
            str(exc),
            SourceLocation(element="limits"),
        )


def _read_bounded(path: Path, limits: ImportLimits) -> bytes | ImportFailure:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return _one_failure(ImportCode.FILE_NOT_FOUND, f"MIDI file not found: {path}")
    except OSError as exc:
        return _one_failure(
            ImportCode.FILE_READ_ERROR,
            f"cannot open MIDI file {path}: {type(exc).__name__}: {exc}",
        )

    result: bytes | ImportFailure
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            result = _one_failure(
                ImportCode.NOT_A_FILE,
                f"MIDI path is not a regular file: {path}",
            )
        elif before.st_size > limits.max_midi_bytes:
            result = _one_failure(
                ImportCode.INPUT_LIMIT_EXCEEDED,
                f"input is {before.st_size} bytes; limit is {limits.max_midi_bytes}",
                SourceLocation(element="file"),
            )
        else:
            output = bytearray()
            while True:
                read_size = min(
                    _FILE_READ_CHUNK,
                    limits.max_midi_bytes - len(output) + 1,
                )
                chunk = os.read(descriptor, read_size)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > limits.max_midi_bytes:
                    break
            if len(output) > limits.max_midi_bytes:
                result = _one_failure(
                    ImportCode.INPUT_LIMIT_EXCEEDED,
                    f"input exceeds {limits.max_midi_bytes} bytes",
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
                        "MIDI file changed while it was being read",
                        SourceLocation(element="file"),
                    )
                else:
                    result = bytes(output)
    except OSError as exc:
        result = _one_failure(
            ImportCode.FILE_READ_ERROR,
            f"cannot read MIDI file {path}: {type(exc).__name__}: {exc}",
        )
    try:
        os.close(descriptor)
    except OSError as exc:
        if not isinstance(result, ImportFailure):
            return _one_failure(
                ImportCode.FILE_READ_ERROR,
                f"cannot close MIDI file {path}: {type(exc).__name__}: {exc}",
            )
    return result


def _source_description(filename: str, sha256: str) -> str:
    escaped_filename = quote(
        filename,
        safe="@._-/",
        encoding="utf-8",
        errors="surrogatepass",
    )
    return ";".join(
        (
            f"filename={escaped_filename}",
            "format=midi",
            f"sha256={sha256}",
            f"root_sha256={sha256}",
            f"importer={MIDI_IMPORTER_VERSION}",
        )
    )


def _validated_ir(filename: str, sha256: str, metadata: object) -> MusicIR | ImportFailure:
    # Attribute access is intentionally contained here: malformed/fake preflight
    # output must fail at the importer boundary rather than reach the pipeline.
    try:
        note_events = object.__getattribute__(metadata, "note_events")
        ir = MusicIR(
            tuple(
                Note(event.onset, event.duration, event.pitch, "melody")
                for event in note_events
            ),
            (),
            Meta(
                object.__getattribute__(metadata, "key"),
                object.__getattribute__(metadata, "time_sig"),
                object.__getattribute__(metadata, "tempo_bpm"),
                _source_description(filename, sha256),
                object.__getattribute__(metadata, "title"),
                object.__getattribute__(metadata, "rights"),
                duration_beats=object.__getattribute__(metadata, "duration_beats"),
            ),
        )
        ir = snapshot_music_ir(ir)
    except (AttributeError, IRInputError, TypeError) as exc:
        detail = (
            f"{exc.field}: {exc.detail}"
            if isinstance(exc, IRInputError)
            else type(exc).__name__
        )
        return _one_failure(
            ImportCode.IR_INVALID,
            f"cannot construct bounded MIDI MusicIR: {detail}",
            SourceLocation(element="MusicIR"),
        )

    violations = validate_ir(ir)
    if not violations:
        return ir
    diagnostics = tuple(
        _diagnostic(
            ImportCode.IR_INVALID,
            f"{violation.kind}: {violation.detail}",
            SourceLocation(element="MusicIR"),
        )
        for violation in violations[:_MAX_IR_DIAGNOSTICS]
    )
    overflow = (
        (
            _diagnostic(
                ImportCode.INPUT_LIMIT_EXCEEDED,
                "MusicIR validation produced more than 256 diagnostics; remaining "
                "diagnostics were omitted",
                SourceLocation(element="diagnostics"),
            ),
        )
        if len(violations) > _MAX_IR_DIAGNOSTICS
        else ()
    )
    return ImportFailure(diagnostics + overflow)


def _import_midi_bytes_snapshot(
    data: bytes,
    filename: str,
    limits: ImportLimits,
) -> MIDIImportResult:
    if len(data) > limits.max_midi_bytes:
        return _one_failure(
            ImportCode.INPUT_LIMIT_EXCEEDED,
            f"input is {len(data)} bytes; limit is {limits.max_midi_bytes}",
            SourceLocation(element="file"),
        )
    sha256 = hashlib.sha256(data).hexdigest()
    try:
        preflight = preflight_midi(data, limits)
    except Exception as exc:
        return _one_failure(
            ImportCode.MALFORMED_MIDI,
            f"MIDI preflight failed safely: {type(exc).__name__}",
            SourceLocation(element="file"),
        )
    if any(
        diagnostic.severity is DiagnosticSeverity.ERROR
        for diagnostic in preflight.diagnostics
    ):
        return ImportFailure(preflight.diagnostics)
    if preflight.metadata is None or preflight.canonical_midi is None:
        return _one_failure(
            ImportCode.ADAPTER_ERROR,
            "MIDI preflight produced no adapter input despite having no errors",
        )
    try:
        crosscheck_music21_midi(preflight.canonical_midi, preflight.metadata)
    except MIDIDependencyError as exc:
        return _one_failure(
            ImportCode.MISSING_DEPENDENCY,
            f"MIDI input requires {exc}; install the 'midi' or 'score' extra",
        )
    except Music21MIDIAdapterError as exc:
        return _one_failure(ImportCode.ADAPTER_ERROR, str(exc))
    except Exception as exc:
        return _one_failure(
            ImportCode.ADAPTER_ERROR,
            f"unexpected MIDI adapter failure: {type(exc).__name__}",
        )

    ir = _validated_ir(filename, sha256, preflight.metadata)
    if isinstance(ir, ImportFailure):
        return ir
    warnings = tuple(
        diagnostic
        for diagnostic in preflight.diagnostics
        if diagnostic.severity is DiagnosticSeverity.WARNING
    )
    provenance = ImportProvenance(
        filename,
        "midi",
        sha256,
        None,
        sha256,
        None,
    )
    return ImportSuccess(
        ir,
        warnings,
        MIDI_IMPORTER_VERSION,
        sha256,
        provenance,
    )


def import_midi_bytes(
    data: bytes,
    filename: str,
    *,
    limits: ImportLimits = DEFAULT_LIMITS,
) -> MIDIImportResult:
    """Safely import exact in-memory SMF bytes with an inert filename."""

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
    return _import_midi_bytes_snapshot(data, filename, snapshot)


def import_midi(
    path: Path,
    *,
    limits: ImportLimits = DEFAULT_LIMITS,
) -> MIDIImportResult:
    """Safely read and import one supported Standard MIDI File."""

    snapshot = _snapshot_limits(limits)
    if isinstance(snapshot, ImportFailure):
        return snapshot
    if not isinstance(path, Path):
        return _one_failure(
            ImportCode.INVALID_INPUT,
            "path must be a pathlib.Path",
            SourceLocation(element="path"),
        )
    suffix = _validate_source_filename(path.name)
    if isinstance(suffix, ImportFailure):
        return suffix
    raw = _read_bounded(path, snapshot)
    if isinstance(raw, ImportFailure):
        return raw
    return _import_midi_bytes_snapshot(raw, path.name, snapshot)


__all__ = [
    "IMPORTER_VERSION",
    "MIDI_IMPORTER_VERSION",
    "import_midi",
    "import_midi_bytes",
    "validate_midi_filename",
]
