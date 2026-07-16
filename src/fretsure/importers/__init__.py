"""External score importer APIs.

Optional parser dependencies are loaded only when :func:`import_musicxml` or
:func:`import_musicxml_bytes` is called, so importing Fretsure's core remains
dependency-isolated.
"""

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
)
from fretsure.importers.musicxml import (
    IMPORTER_VERSION,
    import_musicxml,
    import_musicxml_bytes,
    validate_musicxml_filename,
)

__all__ = [
    "DEFAULT_LIMITS",
    "IMPORTER_VERSION",
    "DiagnosticSeverity",
    "ImportCode",
    "ImportDiagnostic",
    "ImportFailure",
    "ImportLimits",
    "ImportProvenance",
    "ImportSuccess",
    "MusicXMLImportResult",
    "SourceLocation",
    "import_musicxml",
    "import_musicxml_bytes",
    "validate_musicxml_filename",
]
