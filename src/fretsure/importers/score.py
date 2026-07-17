"""Versioned dispatcher for every supported symbolic score input format."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from fretsure.importers.contracts import (
    DEFAULT_LIMITS,
    DiagnosticSeverity,
    ImportCode,
    ImportDiagnostic,
    ImportFailure,
    ImportLimits,
    ScoreImportResult,
    SourceLocation,
)
from fretsure.importers.midi import (
    MIDI_IMPORTER_VERSION,
    import_midi,
    import_midi_bytes,
    validate_midi_filename,
)
from fretsure.importers.musicxml import (
    MUSICXML_IMPORTER_VERSION,
    import_musicxml,
    import_musicxml_bytes,
    validate_musicxml_filename,
)

SCORE_INPUT_VERSION = "score-input@0.1.0"
SCORE_SUFFIXES = (".musicxml", ".xml", ".mxl", ".mid", ".midi")
SCORE_FORMAT_REGISTRY: Mapping[str, str] = MappingProxyType(
    {
        "musicxml": MUSICXML_IMPORTER_VERSION,
        "mxl": MUSICXML_IMPORTER_VERSION,
        "midi": MIDI_IMPORTER_VERSION,
    }
)


def _unsupported_filename() -> ImportFailure:
    return ImportFailure(
        (
            ImportDiagnostic(
                ImportCode.UNSUPPORTED_FILE_TYPE,
                DiagnosticSeverity.ERROR,
                "unsupported input suffix; expected .musicxml, .xml, .mxl, .mid, or .midi",
                SourceLocation(element="filename"),
            ),
        )
    )


def validate_score_filename(filename: object) -> str | ImportFailure:
    """Validate one inert score basename and return its normalized suffix."""

    musicxml = validate_musicxml_filename(filename)
    if isinstance(musicxml, str):
        return musicxml
    midi = validate_midi_filename(filename)
    if isinstance(midi, str):
        return midi
    musicxml_unsupported = all(
        item.code is ImportCode.UNSUPPORTED_FILE_TYPE for item in musicxml.diagnostics
    )
    midi_unsupported = all(
        item.code is ImportCode.UNSUPPORTED_FILE_TYPE for item in midi.diagnostics
    )
    if musicxml_unsupported and midi_unsupported:
        return _unsupported_filename()
    # Both validators share the exact inert-basename envelope.  Preserve the
    # first non-suffix failure deterministically rather than merging duplicates.
    return midi if not midi_unsupported else musicxml


def import_score_bytes(
    data: bytes,
    filename: str,
    *,
    limits: ImportLimits = DEFAULT_LIMITS,
) -> ScoreImportResult:
    """Dispatch exact in-memory bytes by their already-inert source filename."""

    suffix = validate_score_filename(filename)
    if isinstance(suffix, ImportFailure):
        return suffix
    if suffix in {".mid", ".midi"}:
        return import_midi_bytes(data, filename, limits=limits)
    return import_musicxml_bytes(data, filename, limits=limits)


def import_score(
    path: Path,
    *,
    limits: ImportLimits = DEFAULT_LIMITS,
) -> ScoreImportResult:
    """Dispatch one safely read score path by its normalized suffix."""

    if not isinstance(path, Path):
        return ImportFailure(
            (
                ImportDiagnostic(
                    ImportCode.INVALID_INPUT,
                    DiagnosticSeverity.ERROR,
                    "path must be a pathlib.Path",
                    SourceLocation(element="path"),
                ),
            )
        )
    suffix = validate_score_filename(path.name)
    if isinstance(suffix, ImportFailure):
        return suffix
    if suffix in {".mid", ".midi"}:
        return import_midi(path, limits=limits)
    return import_musicxml(path, limits=limits)


__all__ = [
    "SCORE_FORMAT_REGISTRY",
    "SCORE_INPUT_VERSION",
    "SCORE_SUFFIXES",
    "import_score",
    "import_score_bytes",
    "validate_score_filename",
]
