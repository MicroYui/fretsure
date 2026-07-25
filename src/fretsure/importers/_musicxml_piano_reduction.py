"""Narrow, fail-closed adapter for common two-staff piano reductions.

The public MusicXML importer remains lead-sheet shaped.  This adapter accepts
only one unambiguous producer pattern: a monophonic upper staff followed by an
exactly rewound lower staff whose simultaneous pitch sets identify a unique
supported chord.  It then rebuilds a single-staff melody plus explicit harmony
events and lets the ordinary raw preflight validate that rebuilt tree again.

This is deliberately not a general polyphonic MusicXML flattener.  Lower-staff
voicing and inversion are disclosed as reduced rather than silently presented
as source-preserved guitar voices.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from fractions import Fraction
from xml.etree.ElementTree import Element, SubElement

from fretsure.importers.contracts import (
    DiagnosticSeverity,
    ImportCode,
    ImportDiagnostic,
    SourceLocation,
)


@dataclass(frozen=True, slots=True)
class PianoReductionSuccess:
    root: Element
    warnings: tuple[ImportDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class PianoReductionFailure:
    diagnostic: ImportDiagnostic


PianoReductionResult = PianoReductionSuccess | PianoReductionFailure | None


@dataclass(frozen=True, slots=True)
class _Pitch:
    step: str
    alter: int
    octave: int
    midi: int

    @property
    def pitch_class(self) -> int:
        return self.midi % 12


@dataclass(frozen=True, slots=True)
class _ChordFrame:
    onset: Fraction
    duration: Fraction
    pitches: tuple[_Pitch, ...]


class _ReductionError(Exception):
    def __init__(self, message: str, *, measure: str | None = None) -> None:
        self.message = message
        self.measure = measure
        super().__init__(message)


# These are the errors produced solely because the frozen lead-sheet preflight
# sees the supported piano-reduction shape before this adapter has rebuilt it.
# Any other raw error (unsafe XML, an unsupported sounding notation, malformed
# numbers, repeats, etc.) prevents adaptation and remains authoritative.
_EXPECTED_RAW_ERRORS = frozenset(
    {
        ImportCode.MULTIPLE_STAVES_UNSUPPORTED,
        ImportCode.MULTIPLE_VOICES_UNSUPPORTED,
        ImportCode.CHORD_NOTATION_UNSUPPORTED,
        ImportCode.TIMELINE_CONTROL_UNSUPPORTED,
        ImportCode.MISSING_HARMONY,
        ImportCode.INCOMPLETE_MEASURE,
    }
)


# Exact pitch-class sets only.  Omitted chord members are never guessed.  Some
# sets (augmented/diminished-seventh/sixth chords) can match multiple roots;
# the uniqueness check below rejects those rather than choosing one.
_CHORD_PATTERNS: tuple[tuple[str, frozenset[int]], ...] = (
    ("major", frozenset({0, 4, 7})),
    ("minor", frozenset({0, 3, 7})),
    ("diminished", frozenset({0, 3, 6})),
    ("augmented", frozenset({0, 4, 8})),
    ("dominant", frozenset({0, 4, 7, 10})),
    ("major-seventh", frozenset({0, 4, 7, 11})),
    ("minor-seventh", frozenset({0, 3, 7, 10})),
    ("diminished-seventh", frozenset({0, 3, 6, 9})),
    ("half-diminished", frozenset({0, 3, 6, 10})),
    ("major-minor", frozenset({0, 3, 7, 11})),
    ("major-sixth", frozenset({0, 4, 7, 9})),
    ("minor-sixth", frozenset({0, 3, 7, 9})),
    ("suspended-second", frozenset({0, 2, 7})),
    ("suspended-fourth", frozenset({0, 5, 7})),
    ("power", frozenset({0, 7})),
)

_STEP_PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _text(element: Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def _fraction(element: Element | None, *, label: str, measure: str) -> Fraction:
    raw = _text(element)
    if raw is None:
        raise _ReductionError(f"{label} is missing", measure=measure)
    try:
        value = Fraction(raw)
    except (ValueError, ZeroDivisionError):
        raise _ReductionError(f"{label} is not an exact decimal", measure=measure) from None
    if value <= 0:
        raise _ReductionError(f"{label} must be positive", measure=measure)
    return value


def _note_staff(note: Element, *, measure: str) -> str:
    staff = _text(note.find("staff"))
    if staff not in {"1", "2"}:
        raise _ReductionError(
            "every piano-reduction note must explicitly select staff 1 or 2",
            measure=measure,
        )
    return staff


def _note_voice(note: Element, *, measure: str) -> str:
    voice = _text(note.find("voice"))
    if voice is None:
        raise _ReductionError(
            "every piano-reduction note must explicitly identify its voice",
            measure=measure,
        )
    return voice


def _pitch(note: Element, *, measure: str) -> _Pitch:
    pitch = note.find("pitch")
    if pitch is None or note.find("rest") is not None:
        raise _ReductionError(
            "lower-staff accompaniment must contain pitched chord frames, not rests",
            measure=measure,
        )
    step = _text(pitch.find("step"))
    octave_text = _text(pitch.find("octave"))
    alter_text = _text(pitch.find("alter")) or "0"
    if step not in _STEP_PITCH_CLASS or octave_text is None:
        raise _ReductionError("lower-staff pitch is incomplete", measure=measure)
    try:
        octave = int(octave_text)
        alter_fraction = Fraction(alter_text)
    except (ValueError, ZeroDivisionError):
        raise _ReductionError("lower-staff pitch is malformed", measure=measure) from None
    if alter_fraction.denominator != 1:
        raise _ReductionError(
            "microtonal lower-staff harmony cannot be reduced",
            measure=measure,
        )
    alter = int(alter_fraction)
    midi = 12 * (octave + 1) + _STEP_PITCH_CLASS[step] + alter
    if not 0 <= midi <= 127:
        raise _ReductionError("lower-staff pitch is outside MIDI range", measure=measure)
    return _Pitch(step, alter, octave, midi)


def _infer_harmony(frame: _ChordFrame, *, measure: str) -> Element:
    pitch_classes = frozenset(pitch.pitch_class for pitch in frame.pitches)
    matches: list[tuple[int, str]] = []
    for root_pc in range(12):
        intervals = frozenset((pitch_class - root_pc) % 12 for pitch_class in pitch_classes)
        for kind, pattern in _CHORD_PATTERNS:
            if intervals == pattern:
                matches.append((root_pc, kind))
    if len(matches) != 1:
        raise _ReductionError(
            "lower-staff chord at division "
            f"{frame.onset} does not identify one unique supported harmony",
            measure=measure,
        )

    root_pc, kind = matches[0]
    root_pitches = [pitch for pitch in frame.pitches if pitch.pitch_class == root_pc]
    if not root_pitches:
        raise _ReductionError("derived harmony omits its root", measure=measure)
    root_pitch = min(root_pitches, key=lambda pitch: (pitch.midi, pitch.step, pitch.alter))
    if abs(root_pitch.alter) > 1:
        raise _ReductionError(
            "double-altered derived harmony roots are unsupported",
            measure=measure,
        )

    harmony = Element("harmony")
    root = SubElement(harmony, "root")
    SubElement(root, "root-step").text = root_pitch.step
    if root_pitch.alter:
        SubElement(root, "root-alter").text = str(root_pitch.alter)
    SubElement(harmony, "kind").text = kind
    return harmony


def _normalize_attributes(measure: Element, *, measure_number: str) -> None:
    for attributes in measure.findall("attributes"):
        for staves in attributes.findall("staves"):
            if _text(staves) != "2":
                raise _ReductionError(
                    "piano-reduction staves declarations must remain exactly 2",
                    measure=measure_number,
                )
            staves.text = "1"
        for clef in tuple(attributes.findall("clef")):
            number = clef.get("number")
            if number == "2":
                attributes.remove(clef)
            elif number in {None, "1"}:
                clef.attrib.pop("number", None)
            else:
                raise _ReductionError(
                    "piano-reduction clefs must select staff 1 or 2",
                    measure=measure_number,
                )


def _reduce_measure(measure: Element) -> tuple[str, str]:
    measure_number = measure.get("number") or "?"
    if measure.findall("harmony"):
        raise _ReductionError(
            "two-staff reduction with existing harmony is ambiguous",
            measure=measure_number,
        )

    upper_cursor = Fraction(0)
    lower_cursor = Fraction(0)
    backup_seen = False
    upper_events: list[tuple[Element, Fraction]] = []
    lower_frames: list[_ChordFrame] = []
    current_lower_notes: list[_Pitch] = []
    current_lower_onset: Fraction | None = None
    current_lower_duration: Fraction | None = None
    upper_voices: set[str] = set()
    lower_voices: set[str] = set()

    def finish_lower_frame() -> None:
        nonlocal current_lower_notes, current_lower_onset, current_lower_duration
        if current_lower_onset is None or current_lower_duration is None:
            return
        lower_frames.append(
            _ChordFrame(
                current_lower_onset,
                current_lower_duration,
                tuple(current_lower_notes),
            )
        )
        current_lower_notes = []
        current_lower_onset = None
        current_lower_duration = None

    for child in measure:
        if child.tag == "forward":
            raise _ReductionError(
                "forward timeline controls are outside the piano-reduction contract",
                measure=measure_number,
            )
        if child.tag == "backup":
            if backup_seen or not upper_events:
                raise _ReductionError(
                    "each measure requires one backup after the upper-staff melody",
                    measure=measure_number,
                )
            duration = _fraction(
                child.find("duration"), label="backup duration", measure=measure_number
            )
            if duration != upper_cursor:
                raise _ReductionError(
                    "backup duration must exactly rewind the complete upper staff",
                    measure=measure_number,
                )
            backup_seen = True
            lower_cursor = upper_cursor - duration
            continue
        if child.tag != "note":
            continue

        staff = _note_staff(child, measure=measure_number)
        voice = _note_voice(child, measure=measure_number)
        duration = _fraction(
            child.find("duration"), label="note duration", measure=measure_number
        )
        is_chord_member = child.find("chord") is not None
        if not backup_seen:
            if staff != "1" or is_chord_member:
                raise _ReductionError(
                    "staff 1 must be one monophonic melody before the backup",
                    measure=measure_number,
                )
            upper_voices.add(voice)
            upper_events.append((child, upper_cursor))
            upper_cursor += duration
            continue

        if staff != "2":
            raise _ReductionError(
                "only staff 2 accompaniment may follow the backup",
                measure=measure_number,
            )
        lower_voices.add(voice)
        if is_chord_member:
            if current_lower_onset is None or current_lower_duration is None:
                raise _ReductionError(
                    "a lower-staff chord member has no preceding frame head",
                    measure=measure_number,
                )
            if duration != current_lower_duration:
                raise _ReductionError(
                    "all notes in a lower-staff chord frame need the same duration",
                    measure=measure_number,
                )
            current_lower_notes.append(_pitch(child, measure=measure_number))
        else:
            finish_lower_frame()
            current_lower_onset = lower_cursor
            current_lower_duration = duration
            current_lower_notes = [_pitch(child, measure=measure_number)]
            lower_cursor += duration

    finish_lower_frame()
    if not backup_seen:
        raise _ReductionError(
            "each piano-reduction measure requires one staff-rewinding backup",
            measure=measure_number,
        )
    if lower_cursor != upper_cursor:
        raise _ReductionError(
            "upper and lower staff timelines must end together",
            measure=measure_number,
        )
    if len(upper_voices) != 1 or len(lower_voices) != 1:
        raise _ReductionError(
            "each staff must contain exactly one stable voice",
            measure=measure_number,
        )
    if not lower_frames:
        raise _ReductionError(
            "lower staff contains no chord frames",
            measure=measure_number,
        )

    harmony_by_onset: dict[Fraction, Element] = {}
    for frame in lower_frames:
        if frame.onset in harmony_by_onset:
            raise _ReductionError(
                "multiple lower-staff chord frames share one onset",
                measure=measure_number,
            )
        harmony_by_onset[frame.onset] = _infer_harmony(frame, measure=measure_number)

    upper_by_id = {id(note): onset for note, onset in upper_events}
    upper_boundaries = frozenset(upper_by_id.values())
    missing_boundaries = sorted(set(harmony_by_onset).difference(upper_boundaries))
    if missing_boundaries:
        raise _ReductionError(
            "derived harmony onset does not align with an upper-staff event boundary: "
            + ", ".join(str(value) for value in missing_boundaries),
            measure=measure_number,
        )

    _normalize_attributes(measure, measure_number=measure_number)
    rebuilt: list[Element] = []
    inserted: set[Fraction] = set()
    for child in list(measure):
        if child.tag == "backup":
            continue
        if child.tag == "note":
            staff = _note_staff(child, measure=measure_number)
            if staff == "2":
                continue
            onset = upper_by_id[id(child)]
            harmony = harmony_by_onset.get(onset)
            if harmony is not None and onset not in inserted:
                rebuilt.append(harmony)
                inserted.add(onset)
            for tag in ("voice", "staff"):
                for element in tuple(child.findall(tag)):
                    child.remove(element)
        rebuilt.append(child)
    if inserted != set(harmony_by_onset):
        raise _ReductionError(
            "not every derived harmony could be placed on the melody timeline",
            measure=measure_number,
        )
    measure[:] = rebuilt
    return next(iter(upper_voices)), next(iter(lower_voices))


def _looks_like_two_staff_piano(root: Element) -> bool:
    parts = root.findall("part")
    if len(parts) != 1:
        return False
    part = parts[0]
    declarations = [_text(element) for element in part.findall("measure/attributes/staves")]
    if not declarations or any(value != "2" for value in declarations):
        return False
    staffs = {_text(note.find("staff")) for note in part.findall("measure/note")}
    return staffs == {"1", "2"}


def maybe_reduce_piano_score(
    root: Element,
    raw_diagnostics: tuple[ImportDiagnostic, ...],
) -> PianoReductionResult:
    """Return a rebuilt lead sheet only for the exact supported piano shape."""

    if not _looks_like_two_staff_piano(root):
        return None
    raw_errors = tuple(
        diagnostic
        for diagnostic in raw_diagnostics
        if diagnostic.severity is DiagnosticSeverity.ERROR
    )
    if not raw_errors or any(
        diagnostic.code not in _EXPECTED_RAW_ERRORS for diagnostic in raw_errors
    ):
        return None

    rebuilt = deepcopy(root)
    part = rebuilt.find("part")
    assert part is not None  # recognized above and preserved by deepcopy
    upper_voice: str | None = None
    lower_voice: str | None = None
    try:
        for measure in part.findall("measure"):
            observed_upper, observed_lower = _reduce_measure(measure)
            if upper_voice is None:
                upper_voice = observed_upper
                lower_voice = observed_lower
            elif observed_upper != upper_voice or observed_lower != lower_voice:
                raise _ReductionError(
                    "staff voice identifiers must remain stable across the score",
                    measure=measure.get("number"),
                )
    except _ReductionError as exc:
        return PianoReductionFailure(
            ImportDiagnostic(
                ImportCode.PIANO_REDUCTION_UNSUPPORTED,
                DiagnosticSeverity.ERROR,
                exc.message,
                SourceLocation(
                    part_id=part.get("id"),
                    measure=exc.measure,
                    element="piano-reduction",
                ),
            )
        )

    warning = ImportDiagnostic(
        ImportCode.PIANO_REDUCTION_DERIVED,
        DiagnosticSeverity.WARNING,
        "two-staff piano reduction imported with staff 1 as melody and unique "
        "staff 2 pitch sets as harmony; lower-staff voicing and inversion were not preserved",
        SourceLocation(part_id=part.get("id"), element="piano-reduction"),
    )
    return PianoReductionSuccess(rebuilt, (warning,))


__all__ = [
    "PianoReductionFailure",
    "PianoReductionResult",
    "PianoReductionSuccess",
    "maybe_reduce_piano_score",
]
