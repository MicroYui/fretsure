"""External symbolic-score importer APIs.

Optional parser dependencies remain lazy: importing Fretsure's core never loads
MusicXML or MIDI parser packages.
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
    MIDIImportResult,
    MusicXMLImportResult,
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
    IMPORTER_VERSION,
    MUSICXML_IMPORTER_VERSION,
    import_musicxml,
    import_musicxml_bytes,
    validate_musicxml_filename,
)
from fretsure.importers.score import (
    SCORE_FORMAT_REGISTRY,
    SCORE_INPUT_VERSION,
    SCORE_SUFFIXES,
    import_score,
    import_score_bytes,
    validate_score_filename,
)

__all__ = [
    "DEFAULT_LIMITS",
    "IMPORTER_VERSION",
    "MIDI_IMPORTER_VERSION",
    "MIDIImportResult",
    "MUSICXML_IMPORTER_VERSION",
    "SCORE_FORMAT_REGISTRY",
    "SCORE_INPUT_VERSION",
    "SCORE_SUFFIXES",
    "DiagnosticSeverity",
    "ImportCode",
    "ImportDiagnostic",
    "ImportFailure",
    "ImportLimits",
    "ImportProvenance",
    "ImportSuccess",
    "MusicXMLImportResult",
    "ScoreImportResult",
    "SourceLocation",
    "import_midi",
    "import_midi_bytes",
    "import_musicxml",
    "import_musicxml_bytes",
    "import_score",
    "import_score_bytes",
    "validate_midi_filename",
    "validate_musicxml_filename",
    "validate_score_filename",
]
