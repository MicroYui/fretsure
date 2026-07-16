"""Raw MusicXML capability preflight.

This module inspects the safely parsed XML tree *before* music21 gets a chance
to normalize or discard notation.  The first importer release is deliberately
strict: unsupported sounding semantics become typed errors, while explicitly
lossy non-sounding annotations become located warnings.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction
from xml.etree.ElementTree import Element

from fretsure.importers.contracts import (
    DiagnosticSeverity,
    ImportCode,
    ImportDiagnostic,
    ImportLimits,
    SourceLocation,
)

_MAJOR_KEYS = ("Cb", "Gb", "Db", "Ab", "Eb", "Bb", "F", "C", "G", "D", "A", "E", "B", "F#", "C#")
_MINOR_KEYS = (
    "Abm",
    "Ebm",
    "Bbm",
    "Fm",
    "Cm",
    "Gm",
    "Dm",
    "Am",
    "Em",
    "Bm",
    "F#m",
    "C#m",
    "G#m",
    "D#m",
    "A#m",
)

HARMONY_KIND_SUFFIXES = {
    "major": "",
    "minor": "m",
    "augmented": "+",
    "diminished": "dim",
    "dominant": "7",
    "major-seventh": "maj7",
    "minor-seventh": "m7",
    "diminished-seventh": "dim7",
    "augmented-seventh": "+7",
    "half-diminished": "m7b5",
    "major-minor": "m(maj7)",
    "major-sixth": "6",
    "minor-sixth": "m6",
    "dominant-ninth": "9",
    "major-ninth": "maj9",
    "minor-ninth": "m9",
    "dominant-11th": "11",
    "major-11th": "maj11",
    "minor-11th": "m11",
    "dominant-13th": "13",
    "major-13th": "maj13",
    "minor-13th": "m13",
    "suspended-second": "sus2",
    "suspended-fourth": "sus4",
    "power": "5",
}
SUPPORTED_HARMONY_KINDS = frozenset(HARMONY_KIND_SUFFIXES)

_XSD_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z", re.ASCII)
_DECIMAL_TEXT_ELEMENTS = frozenset(
    {"alter", "divisions", "duration", "offset", "per-minute", "root-alter"}
)

_PERFORMANCE_TAGS = frozenset(
    {
        "ornaments",
        "tremolo",
        "bend",
        "damp",
        "damp-all",
        "harmonic",
        "glissando",
        "slide",
        "fermata",
        "arpeggiate",
        "non-arpeggiate",
        "octave-shift",
        "pedal",
        "hammer-on",
        "pull-off",
        "tap",
        "heel",
        "toe",
        "play",
        "listen",
        "other-direction",
    }
)
_IGNORED_NOTATION_TAGS = frozenset({"lyric", "slur", "dynamics", "articulations", "wedge"})
_NAVIGATION_TAGS = frozenset({"segno", "coda", "dalsegno", "tocoda"})
_NAVIGATION_SOUND_ATTRIBUTES = frozenset({"dacapo", "dalsegno", "segno", "coda", "tocoda", "fine"})
_NAVIGATION_WORDS = re.compile(
    r"(?:\bD\.?\s*[CS]\.?\b|da\s+capo|dal\s+segno|to\s+coda|\bfine\b|\bcoda\b|\bsegno\b)",
    re.IGNORECASE,
)

_ALLOWED_MEASURE_TAGS = frozenset(
    {
        "measure",
        "attributes",
        "divisions",
        "key",
        "fifths",
        "mode",
        "cancel",
        "time",
        "beats",
        "beat-type",
        "senza-misura",
        "staves",
        "clef",
        "sign",
        "line",
        "clef-octave-change",
        "direction",
        "direction-type",
        "metronome",
        "beat-unit",
        "beat-unit-dot",
        "per-minute",
        "sound",
        "offset",
        "harmony",
        "root",
        "root-step",
        "root-alter",
        "kind",
        "inversion",
        "bass",
        "bass-step",
        "bass-alter",
        "degree",
        "degree-value",
        "degree-alter",
        "degree-type",
        "function",
        "numeral",
        "numeral-root",
        "note",
        "pitch",
        "step",
        "alter",
        "octave",
        "rest",
        "duration",
        "tie",
        "voice",
        "type",
        "dot",
        "accidental",
        "staff",
        "stem",
        "notehead",
        "notehead-text",
        "beam",
        "notations",
        "tied",
        "chord",
        "grace",
        "cue",
        "unpitched",
        "display-step",
        "display-octave",
        "time-modification",
        "actual-notes",
        "normal-notes",
        "tuplet",
        "lyric",
        "text",
        "syllabic",
        "extend",
        "elision",
        "slur",
        "articulations",
        "dynamics",
        "wedge",
        "ornaments",
        "technical",
        "barline",
        "bar-style",
        "print",
        "transpose",
        "diatonic",
        "chromatic",
        "octave-change",
        "double",
        "measure-style",
        "multiple-rest",
        "measure-repeat",
        "beat-repeat",
        "slash",
        "backup",
        "forward",
        "repeat",
        "ending",
        "segno",
        "coda",
        "dalsegno",
        "tocoda",
        "words",
    }
)
_VISUAL_SUBTREE_ROOTS = frozenset({"print", "notehead-text"})
_EXTERNAL_RESOURCE_ELEMENTS = frozenset({"credit-image", "link", "opus"})
_XLINK_NAMESPACE = "http://www.w3.org/1999/xlink"
_NOTE_VISUAL_ATTRIBUTES = frozenset(
    {
        "default-x",
        "default-y",
        "relative-x",
        "relative-y",
        "font-family",
        "font-style",
        "font-size",
        "font-weight",
        "color",
        "print-object",
        "print-dot",
        "print-spacing",
        "print-lyric",
    }
)
_NOTE_IGNORED_ATTRIBUTES = frozenset({"dynamics", "end-dynamics"})


@dataclass(frozen=True, slots=True)
class PreflightNoteEvent:
    onset: Fraction
    duration: Fraction
    pitch: int
    tie_type: str | None


@dataclass(frozen=True, slots=True)
class PreflightHarmonyEvent:
    onset: Fraction
    symbol: str
    root_pc: int


@dataclass(frozen=True, slots=True)
class PreflightMetadata:
    note_part_id: str
    key: str
    time_sig: tuple[int, int]
    tempo_bpm: float
    title: str
    rights: str
    duration_beats: Fraction
    note_events: tuple[PreflightNoteEvent, ...]
    harmony_events: tuple[PreflightHarmonyEvent, ...]


@dataclass(frozen=True, slots=True)
class PreflightResult:
    diagnostics: tuple[ImportDiagnostic, ...]
    metadata: PreflightMetadata | None


@dataclass(slots=True)
class _TieState:
    pitch: tuple[str, Fraction, str]
    end: Fraction
    location: SourceLocation


def _error(
    code: ImportCode, message: str, location: SourceLocation | None = None
) -> ImportDiagnostic:
    return ImportDiagnostic(code, DiagnosticSeverity.ERROR, message, location)


def _warning(
    code: ImportCode, message: str, location: SourceLocation | None = None
) -> ImportDiagnostic:
    return ImportDiagnostic(code, DiagnosticSeverity.WARNING, message, location)


def _text(element: Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip()
    return value or None


def _location(
    part_id: str | None,
    measure: Element | None,
    *,
    voice: str | None = None,
    element: str | None = None,
) -> SourceLocation:
    return SourceLocation(
        part_id=part_id,
        measure=measure.get("number") if measure is not None else None,
        voice=voice,
        element=element,
    )


def _xsd_decimal(value: str | None, limits: ImportLimits) -> Fraction | None:
    """Parse one bounded XML Schema decimal without Python's broader grammar."""

    if value is None or len(value) > limits.max_decimal_chars:
        return None
    if _XSD_DECIMAL.fullmatch(value) is None:
        return None
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError):
        return None


def _fraction_text(element: Element | None, limits: ImportLimits) -> Fraction | None:
    value = _text(element)
    return _xsd_decimal(value, limits)


def _tempo_decimal(value: str | None, limits: ImportLimits) -> Fraction | None:
    parsed = _xsd_decimal(value, limits)
    if parsed is None or not Fraction(1) <= parsed <= Fraction(1000):
        return None
    return parsed


def _pitch_identity(note: Element, limits: ImportLimits) -> tuple[str, Fraction, str] | None:
    pitch = note.find("pitch")
    if pitch is None:
        return None
    step = _text(pitch.find("step"))
    octave = _text(pitch.find("octave"))
    if step is None or octave is None:
        return None
    alter_element = pitch.find("alter")
    alter = (
        Fraction(0)
        if alter_element is None
        else _fraction_text(alter_element, limits)
    )
    if alter is None:
        return None
    return step, alter, octave


def _pitch_midi(identity: tuple[str, Fraction, str] | None) -> int | None:
    if identity is None:
        return None
    step, alter, octave_text = identity
    pitch_classes = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    try:
        octave = int(octave_text)
    except ValueError:
        return None
    if step not in pitch_classes or alter.denominator != 1:
        return None
    midi = (octave + 1) * 12 + pitch_classes[step] + int(alter)
    return midi if 0 <= midi <= 127 else None


def _note_voice(note: Element) -> str:
    return _text(note.find("voice")) or "1"


def _has_pitched_note(part: Element) -> bool:
    return any(
        note.find("pitch") is not None or note.find("unpitched") is not None
        for note in part.iter("note")
    )


def _key_name(fifths: int, mode: str) -> str | None:
    if not -7 <= fifths <= 7:
        return None
    index = fifths + 7
    if mode == "major":
        return _MAJOR_KEYS[index]
    if mode == "minor":
        return _MINOR_KEYS[index]
    return None


def _check_tree_resource_limits(root: Element, limits: ImportLimits) -> list[ImportDiagnostic]:
    diagnostics: list[ImportDiagnostic] = []
    counts = (
        ("measure", sum(1 for _ in root.iter("measure")), limits.max_measures),
        ("note", sum(1 for _ in root.iter("note")), limits.max_notes),
        ("harmony", sum(1 for _ in root.iter("harmony")), limits.max_harmonies),
    )
    for element_name, actual, maximum in counts:
        if actual > maximum:
            diagnostics.append(
                _error(
                    ImportCode.INPUT_LIMIT_EXCEEDED,
                    f"MusicXML contains {actual} {element_name} elements; limit is {maximum}",
                    SourceLocation(element=element_name),
                )
            )
    for element in root.iter():
        values: list[tuple[str, str]] = []
        if element.tag in _DECIMAL_TEXT_ELEMENTS and element.text is not None:
            values.append((element.tag, element.text.strip()))
        if element.tag == "sound" and "tempo" in element.attrib:
            values.append(("sound@tempo", element.attrib["tempo"].strip()))
        for label, value in values:
            if len(value) > limits.max_decimal_chars:
                diagnostics.append(
                    _error(
                        ImportCode.INPUT_LIMIT_EXCEEDED,
                        f"{label} decimal token exceeds {limits.max_decimal_chars} characters",
                        SourceLocation(element=label),
                    )
                )
                return diagnostics
    return diagnostics


def _split_expanded_name(name: str) -> tuple[str, str]:
    if name.startswith("{"):
        namespace, separator, local = name[1:].partition("}")
        if separator:
            return namespace, local
    return "", name


def _check_external_resource_references(root: Element) -> list[ImportDiagnostic]:
    """Reject XML constructs that can ask downstream parsers to dereference resources."""

    diagnostics: list[ImportDiagnostic] = []

    def visit(
        element: Element,
        part_id: str | None,
        measure: Element | None,
        voice: str | None,
    ) -> None:
        current_part_id = element.get("id") if element.tag == "part" else part_id
        current_measure = element if element.tag == "measure" else measure
        current_voice = _note_voice(element) if element.tag == "note" else voice
        has_xlink_href = any(
            _split_expanded_name(attribute) == (_XLINK_NAMESPACE, "href")
            for attribute in element.attrib
        )

        if element.tag in _EXTERNAL_RESOURCE_ELEMENTS or has_xlink_href:
            if element.tag in _EXTERNAL_RESOURCE_ELEMENTS:
                detail = f"external resource element <{element.tag}>"
            else:
                detail = f"external xlink:href on <{element.tag}>"
            diagnostics.append(
                _error(
                    ImportCode.UNSAFE_XML,
                    f"{detail} is not allowed in the frozen MusicXML subset",
                    _location(
                        current_part_id,
                        current_measure,
                        voice=current_voice,
                        element=element.tag,
                    ),
                )
            )
            return

        for child in element:
            visit(child, current_part_id, current_measure, current_voice)

    visit(root, None, None, None)
    return diagnostics


def _scan_unsupported_elements(
    part: Element,
    part_id: str,
    diagnostics: list[ImportDiagnostic],
    limits: ImportLimits,
) -> None:
    for measure in part.findall("measure"):
        if measure.get("implicit", "no").lower() == "yes":
            diagnostics.append(
                _error(
                    ImportCode.PICKUP_UNSUPPORTED,
                    "implicit/pickup measures are deferred",
                    _location(part_id, measure, element="measure"),
                )
            )

        for note in measure.iter("note"):
            voice = _note_voice(note)

            def note_location(
                element: str, *, _measure: Element = measure, _voice: str = voice
            ) -> SourceLocation:
                return _location(part_id, _measure, voice=_voice, element=element)

            ignored_attributes = _NOTE_IGNORED_ATTRIBUTES.intersection(note.attrib)
            if ignored_attributes:
                diagnostics.append(
                    _warning(
                        ImportCode.IGNORED_NOTATION,
                        "note velocity attributes were ignored: "
                        + ", ".join(sorted(ignored_attributes)),
                        note_location("note"),
                    )
                )
            sounding_or_unknown_attributes = set(note.attrib).difference(
                _NOTE_VISUAL_ATTRIBUTES | _NOTE_IGNORED_ATTRIBUTES
            )
            if sounding_or_unknown_attributes:
                diagnostics.append(
                    _error(
                        ImportCode.PERFORMANCE_NOTATION_UNSUPPORTED,
                        "unsupported sounding <note> attributes: "
                        + ", ".join(sorted(sounding_or_unknown_attributes)),
                        note_location("note"),
                    )
                )

            if note.find("chord") is not None:
                diagnostics.append(
                    _error(
                        ImportCode.CHORD_NOTATION_UNSUPPORTED,
                        "simultaneous <chord/> notes are deferred",
                        note_location("chord"),
                    )
                )
            if note.find("grace") is not None:
                diagnostics.append(
                    _error(
                        ImportCode.GRACE_NOTE_UNSUPPORTED,
                        "grace notes are deferred",
                        note_location("grace"),
                    )
                )
            if note.find("cue") is not None:
                diagnostics.append(
                    _error(
                        ImportCode.CUE_NOTE_UNSUPPORTED,
                        "cue notes are deferred",
                        note_location("cue"),
                    )
                )
            if note.find("unpitched") is not None:
                diagnostics.append(
                    _error(
                        ImportCode.UNPITCHED_UNSUPPORTED,
                        "unpitched/percussion notes are deferred",
                        note_location("unpitched"),
                    )
                )
            note_kinds = sum(len(note.findall(tag)) for tag in ("pitch", "rest", "unpitched"))
            if note_kinds != 1:
                diagnostics.append(
                    _error(
                        ImportCode.UNSUPPORTED_NOTE,
                        "note must contain exactly one pitched note or rest",
                        note_location("note"),
                    )
                )
            pitch = note.find("pitch")
            if pitch is not None:
                step = _text(pitch.find("step"))
                octave_text = _text(pitch.find("octave"))
                alter_value = (
                    Fraction(0)
                    if pitch.find("alter") is None
                    else _fraction_text(pitch.find("alter"), limits)
                )
                try:
                    octave = int(octave_text or "")
                except ValueError:
                    octave = -99
                pitch_classes = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
                midi = (
                    (octave + 1) * 12 + pitch_classes[step] + int(alter_value)
                    if step in pitch_classes
                    and alter_value is not None
                    and alter_value.denominator == 1
                    else -1
                )
                if not 0 <= midi <= 127:
                    diagnostics.append(
                        _error(
                            ImportCode.UNSUPPORTED_NOTE,
                            "pitched note must have a valid A-G step and MIDI-range octave",
                            note_location("pitch"),
                        )
                    )
            for element in note.iter():
                if element.tag in {"time-modification", "tuplet"}:
                    diagnostics.append(
                        _error(
                            ImportCode.TUPLET_UNSUPPORTED,
                            "tuplets are deferred",
                            note_location(element.tag),
                        )
                    )
                if element.tag in _PERFORMANCE_TAGS:
                    if element.tag == "ornaments" and any(
                        nested is not element and nested.tag in _PERFORMANCE_TAGS
                        for nested in element.iter()
                    ):
                        continue
                    diagnostics.append(
                        _error(
                            ImportCode.PERFORMANCE_NOTATION_UNSUPPORTED,
                            f"performance-affecting <{element.tag}> is deferred",
                            note_location(element.tag),
                        )
                    )
                if element.tag in _IGNORED_NOTATION_TAGS:
                    diagnostics.append(
                        _warning(
                            ImportCode.IGNORED_NOTATION,
                            f"non-IR <{element.tag}> annotation was ignored",
                            note_location(element.tag),
                        )
                    )

            alter = note.find("pitch/alter")
            if alter is not None:
                value = _fraction_text(alter, limits)
                if value is None or value.denominator != 1:
                    diagnostics.append(
                        _error(
                            ImportCode.MICROTONE_UNSUPPORTED,
                            "fractional or invalid pitch alteration is deferred",
                            note_location("alter"),
                        )
                    )

        tag_codes = {
            "repeat": ImportCode.REPEAT_UNSUPPORTED,
            "ending": ImportCode.ENDING_UNSUPPORTED,
            "measure-repeat": ImportCode.MEASURE_REPEAT_UNSUPPORTED,
            "beat-repeat": ImportCode.MEASURE_REPEAT_UNSUPPORTED,
            "slash": ImportCode.TIMELINE_CONTROL_UNSUPPORTED,
            "multiple-rest": ImportCode.MULTIPLE_REST_UNSUPPORTED,
            "backup": ImportCode.TIMELINE_CONTROL_UNSUPPORTED,
            "forward": ImportCode.TIMELINE_CONTROL_UNSUPPORTED,
            "transpose": ImportCode.TRANSPOSE_UNSUPPORTED,
        }
        note_nested_ids = {id(nested) for note in measure.iter("note") for nested in note.iter()}
        handled_subtree_ids: set[int] = set()
        for subtree_root in measure.iter():
            if subtree_root.tag in (
                _PERFORMANCE_TAGS | _IGNORED_NOTATION_TAGS | _VISUAL_SUBTREE_ROOTS
            ):
                handled_subtree_ids.update(id(nested) for nested in subtree_root.iter())
        for element in measure.iter():
            if element.tag in tag_codes:
                diagnostics.append(
                    _error(
                        tag_codes[element.tag],
                        f"<{element.tag}> is deferred in the frozen MusicXML subset",
                        _location(part_id, measure, element=element.tag),
                    )
                )
            if element.tag in _NAVIGATION_TAGS:
                diagnostics.append(
                    _error(
                        ImportCode.NAVIGATION_UNSUPPORTED,
                        f"navigation marker <{element.tag}> is deferred",
                        _location(part_id, measure, element=element.tag),
                    )
                )
            if element.tag in _PERFORMANCE_TAGS and id(element) not in note_nested_ids:
                diagnostics.append(
                    _error(
                        ImportCode.PERFORMANCE_NOTATION_UNSUPPORTED,
                        f"performance-affecting <{element.tag}> is deferred",
                        _location(part_id, measure, element=element.tag),
                    )
                )
            if element.tag in _IGNORED_NOTATION_TAGS and id(element) not in note_nested_ids:
                diagnostics.append(
                    _warning(
                        ImportCode.IGNORED_NOTATION,
                        f"non-IR <{element.tag}> annotation was ignored",
                        _location(part_id, measure, element=element.tag),
                    )
                )
            if element.tag == "sound" and _NAVIGATION_SOUND_ATTRIBUTES.intersection(element.attrib):
                diagnostics.append(
                    _error(
                        ImportCode.NAVIGATION_UNSUPPORTED,
                        "navigation attributes on <sound> are deferred",
                        _location(part_id, measure, element="sound"),
                    )
                )
            if element.tag == "sound":
                lossy_attributes = {"dynamics", "pan", "elevation"}.intersection(element.attrib)
                if lossy_attributes:
                    diagnostics.append(
                        _warning(
                            ImportCode.IGNORED_NOTATION,
                            "sound dynamics/spatial attributes were ignored",
                            _location(part_id, measure, element="sound"),
                        )
                    )
                sounding_attributes = set(element.attrib).difference(
                    {"tempo", "dynamics", "pan", "elevation"} | _NAVIGATION_SOUND_ATTRIBUTES
                )
                if sounding_attributes:
                    diagnostics.append(
                        _error(
                            ImportCode.PERFORMANCE_NOTATION_UNSUPPORTED,
                            "unsupported sounding <sound> attributes: "
                            + ", ".join(sorted(sounding_attributes)),
                            _location(part_id, measure, element="sound"),
                        )
                    )
            if element.tag == "words":
                if _NAVIGATION_WORDS.search(_text(element) or ""):
                    diagnostics.append(
                        _error(
                            ImportCode.NAVIGATION_UNSUPPORTED,
                            "textual score navigation is deferred",
                            _location(part_id, measure, element="words"),
                        )
                    )
                else:
                    diagnostics.append(
                        _error(
                            ImportCode.PERFORMANCE_NOTATION_UNSUPPORTED,
                            "text directions are outside the frozen sounding-semantics subset",
                            _location(part_id, measure, element="words"),
                        )
                    )
            if element.tag not in _ALLOWED_MEASURE_TAGS and id(element) not in handled_subtree_ids:
                diagnostics.append(
                    _error(
                        ImportCode.UNSUPPORTED_ELEMENT,
                        f"unknown <{element.tag}> is outside the frozen MusicXML subset",
                        _location(part_id, measure, element=element.tag),
                    )
                )


def _check_parts_and_voices(
    root: Element, diagnostics: list[ImportDiagnostic]
) -> tuple[Element, str] | None:
    parts = root.findall("part")
    bearing = [part for part in parts if _has_pitched_note(part)]
    if not bearing:
        diagnostics.append(
            _error(
                ImportCode.NO_NOTE_BEARING_PART,
                "score must contain one note-bearing part",
                SourceLocation(element="part"),
            )
        )
        return None
    if len(bearing) > 1:
        diagnostics.append(
            _error(
                ImportCode.MULTIPLE_NOTE_BEARING_PARTS,
                f"found {len(bearing)} note-bearing parts; exactly one is supported",
                SourceLocation(part_id=bearing[1].get("id"), element="part"),
            )
        )

    part = bearing[0]
    part_id = part.get("id") or ""
    voices: dict[str, tuple[Element, Element]] = {}
    staffs: dict[str, tuple[Element, Element]] = {}
    for measure in part.findall("measure"):
        staves = _text(measure.find("attributes/staves"))
        if staves is not None:
            try:
                staves_count = int(staves)
            except ValueError:
                staves_count = 2
            if staves_count != 1:
                diagnostics.append(
                    _error(
                        ImportCode.MULTIPLE_STAVES_UNSUPPORTED,
                        "exactly one staff is supported",
                        _location(part_id, measure, element="staves"),
                    )
                )
        for note in measure.iter("note"):
            voice = _note_voice(note)
            voices.setdefault(voice, (measure, note))
            staff = _text(note.find("staff")) or "1"
            staffs.setdefault(staff, (measure, note))

    if len(voices) > 1:
        second_voice = list(voices)[1]
        measure, _note_element = voices[second_voice]
        diagnostics.append(
            _error(
                ImportCode.MULTIPLE_VOICES_UNSUPPORTED,
                f"multiple voices found: {', '.join(voices)}",
                _location(part_id, measure, voice=second_voice, element="voice"),
            )
        )
    if len(staffs) > 1:
        second_staff = list(staffs)[1]
        measure, note_element = staffs[second_staff]
        diagnostics.append(
            _error(
                ImportCode.MULTIPLE_STAVES_UNSUPPORTED,
                f"multiple note staves found: {', '.join(staffs)}",
                _location(
                    part_id,
                    measure,
                    voice=_note_voice(note_element),
                    element="staff",
                ),
            )
        )
    return part, part_id


def _check_keys(part: Element, part_id: str, diagnostics: list[ImportDiagnostic]) -> str | None:
    entries: list[tuple[int, str, Element]] = []
    for measure in part.findall("measure"):
        for key in measure.findall("attributes/key"):
            if key.find("key-step") is not None or key.find("key-alter") is not None:
                diagnostics.append(
                    _error(
                        ImportCode.UNSUPPORTED_KEY,
                        "non-traditional keys are deferred",
                        _location(part_id, measure, element="key"),
                    )
                )
                continue
            fifths_text = _text(key.find("fifths"))
            mode = (_text(key.find("mode")) or "").lower()
            try:
                fifths = int(fifths_text or "")
            except ValueError:
                fifths = 99
            if _key_name(fifths, mode) is None:
                diagnostics.append(
                    _error(
                        ImportCode.UNSUPPORTED_KEY,
                        "only standard major/minor keys with -7..7 fifths are supported",
                        _location(part_id, measure, element="key"),
                    )
                )
            entries.append((fifths, mode, measure))

    first_measure = part.find("measure")
    if not entries:
        diagnostics.append(
            _error(
                ImportCode.MISSING_KEY,
                "an explicit major/minor key is required",
                _location(part_id, first_measure, element="key"),
            )
        )
        return None
    if entries[0][2] is not first_measure:
        diagnostics.append(
            _error(
                ImportCode.MISSING_KEY,
                "the global key must be declared in measure 1",
                _location(part_id, first_measure, element="key"),
            )
        )
        return None
    first = entries[0][:2]
    for fifths, mode, measure in entries[1:]:
        if (fifths, mode) != first:
            diagnostics.append(
                _error(
                    ImportCode.KEY_CHANGE_UNSUPPORTED,
                    "mid-score key changes are deferred",
                    _location(part_id, measure, element="key"),
                )
            )
    return _key_name(first[0], first[1])


def _check_divisions(
    part: Element,
    part_id: str,
    diagnostics: list[ImportDiagnostic],
    limits: ImportLimits,
) -> Fraction | None:
    """Freeze one global divisions value before any timed score data."""

    entries: list[tuple[Fraction | None, Element]] = []
    timed_data_seen = False
    declaration_after_timed_data = False
    for measure in part.findall("measure"):
        for child in measure:
            if child.tag == "attributes":
                for element in child.findall("divisions"):
                    if not entries and timed_data_seen:
                        declaration_after_timed_data = True
                    value = _fraction_text(element, limits)
                    if value is None or value <= 0:
                        diagnostics.append(
                            _error(
                                ImportCode.INVALID_DIVISIONS,
                                "divisions must be a positive XSD decimal",
                                _location(part_id, measure, element="divisions"),
                            )
                        )
                        entries.append((None, measure))
                    else:
                        entries.append((value, measure))
            elif child.tag in {"note", "harmony", "backup", "forward"}:
                timed_data_seen = True

    first_measure = part.find("measure")
    if not entries:
        diagnostics.append(
            _error(
                ImportCode.MISSING_DIVISIONS,
                "positive divisions must be declared before notes or harmonies",
                _location(part_id, first_measure, element="divisions"),
            )
        )
        return None
    if entries[0][1] is not first_measure or declaration_after_timed_data:
        diagnostics.append(
            _error(
                ImportCode.MISSING_DIVISIONS,
                "global divisions must be declared in measure 1 before timed score data",
                _location(part_id, first_measure, element="divisions"),
            )
        )
        return None

    first = entries[0][0]
    if first is None:
        return None
    for value, measure in entries[1:]:
        if value is not None and value != first:
            diagnostics.append(
                _error(
                    ImportCode.DIVISIONS_CHANGE_UNSUPPORTED,
                    "the first importer release requires one fixed divisions value",
                    _location(part_id, measure, element="divisions"),
                )
            )
    return first


def _supported_time_signature(time: Element) -> tuple[int, int] | None:
    """Recognize frozen 4/4 without constructing attacker-controlled integers."""

    beats_values = [_text(element) for element in time.findall("beats")]
    beat_type_values = [_text(element) for element in time.findall("beat-type")]
    if (
        beats_values == ["4"]
        and beat_type_values == ["4"]
        and time.find("senza-misura") is None
    ):
        return 4, 4
    return None


def _check_times(
    part: Element, part_id: str, diagnostics: list[ImportDiagnostic]
) -> tuple[int, int] | None:
    entries: list[tuple[tuple[int, int] | None, Element]] = []
    for measure in part.findall("measure"):
        for time in measure.findall("attributes/time"):
            signature = _supported_time_signature(time)
            entries.append((signature, measure))
            if signature is None:
                diagnostics.append(
                    _error(
                        ImportCode.UNSUPPORTED_TIME_SIGNATURE,
                        "the first importer release supports fixed 4/4 only",
                        _location(part_id, measure, element="time"),
                    )
                )

    first_measure = part.find("measure")
    if not entries:
        diagnostics.append(
            _error(
                ImportCode.MISSING_TIME_SIGNATURE,
                "an explicit 4/4 time signature is required",
                _location(part_id, first_measure, element="time"),
            )
        )
        return None
    if entries[0][1] is not first_measure:
        diagnostics.append(
            _error(
                ImportCode.MISSING_TIME_SIGNATURE,
                "the global time signature must be declared in measure 1",
                _location(part_id, first_measure, element="time"),
            )
        )
        return None
    first = entries[0][0]
    for signature, measure in entries[1:]:
        if signature != first:
            diagnostics.append(
                _error(
                    ImportCode.TIME_SIGNATURE_CHANGE_UNSUPPORTED,
                    "mid-score time-signature changes are deferred",
                    _location(part_id, measure, element="time"),
                )
            )
    return first


def _tempo_from_sound(
    sound: Element,
    part_id: str,
    measure: Element,
    diagnostics: list[ImportDiagnostic],
    limits: ImportLimits,
) -> Fraction | None:
    if sound.find("offset") is not None:
        diagnostics.append(
            _error(
                ImportCode.UNSUPPORTED_TEMPO,
                "sound-specific offsets are deferred",
                _location(part_id, measure, element="offset"),
            )
        )
    if "tempo" not in sound.attrib:
        return None
    tempo = _tempo_decimal(sound.get("tempo"), limits)
    if tempo is None:
        diagnostics.append(
            _error(
                ImportCode.UNSUPPORTED_TEMPO,
                "sound tempo must be an XSD decimal in 1..1000 BPM",
                _location(part_id, measure, element="sound"),
            )
        )
    return tempo


def _tempo_from_direction(
    direction: Element,
    part_id: str,
    measure: Element,
    diagnostics: list[ImportDiagnostic],
    limits: ImportLimits,
) -> Fraction | None:
    values: list[Fraction] = []
    metronomes = list(direction.iter("metronome"))
    for metronome in metronomes:
        beat_units = [_text(element) for element in metronome.findall("beat-unit")]
        dots = list(metronome.findall("beat-unit-dot"))
        per_minute = _tempo_decimal(_text(metronome.find("per-minute")), limits)
        if beat_units != ["quarter"] or dots or per_minute is None:
            diagnostics.append(
                _error(
                    ImportCode.UNSUPPORTED_TEMPO,
                    "tempo must be a quarter-note value in 1..1000 BPM",
                    _location(part_id, measure, element="metronome"),
                )
            )
        else:
            values.append(per_minute)

    for sound in direction.findall("sound"):
        tempo = _tempo_from_sound(sound, part_id, measure, diagnostics, limits)
        if tempo is not None:
            values.append(tempo)

    if not values:
        return None
    if len(set(values)) > 1:
        diagnostics.append(
            _error(
                ImportCode.UNSUPPORTED_TEMPO,
                "metronome and sound tempo values disagree",
                _location(part_id, measure, element="metronome"),
            )
        )
    return values[0]


def _check_tempi(
    part: Element,
    part_id: str,
    diagnostics: list[ImportDiagnostic],
    limits: ImportLimits,
) -> float | None:
    entries: list[tuple[Fraction, Element, bool]] = []
    first_measure = part.find("measure")
    for measure in part.findall("measure"):
        elapsed = False
        for child in measure:
            if child.tag == "direction":
                tempo = _tempo_from_direction(
                    child, part_id, measure, diagnostics, limits
                )
                if tempo is not None:
                    offset = _fraction_text(child.find("offset"), limits)
                    at_zero = (
                        measure is first_measure
                        and not elapsed
                        and (child.find("offset") is None or offset == 0)
                    )
                    entries.append((tempo, measure, at_zero))
            elif child.tag == "sound":
                tempo = _tempo_from_sound(
                    child, part_id, measure, diagnostics, limits
                )
                if tempo is not None:
                    entries.append((tempo, measure, measure is first_measure and not elapsed))
            elif child.tag == "note" and child.find("chord") is None:
                duration = _fraction_text(child.find("duration"), limits)
                elapsed = elapsed or (duration is not None and duration > 0)

    if not entries:
        diagnostics.append(
            _error(
                ImportCode.MISSING_TEMPO,
                "one explicit quarter-note tempo is required",
                _location(part_id, first_measure, element="metronome"),
            )
        )
        return None
    if not entries[0][2]:
        diagnostics.append(
            _error(
                ImportCode.MISSING_TEMPO,
                "the single global tempo must begin at measure 1 onset 0",
                _location(part_id, first_measure, element="metronome"),
            )
        )
        return None
    for _tempo, measure, _at_zero in entries[1:]:
        diagnostics.append(
            _error(
                ImportCode.TEMPO_CHANGE_UNSUPPORTED,
                "multiple or mid-score tempo markings are deferred",
                _location(part_id, measure, element="metronome"),
            )
        )
    return float(entries[0][0])


def _check_harmonies(
    part: Element,
    part_id: str,
    divisions: Fraction | None,
    diagnostics: list[ImportDiagnostic],
    limits: ImportLimits,
) -> tuple[PreflightHarmonyEvent, ...]:
    harmony_count = 0
    seen_onsets: dict[Fraction, SourceLocation] = {}
    events: list[PreflightHarmonyEvent] = []
    global_measure_onset = Fraction(0)
    for measure in part.findall("measure"):
        cursor = Fraction(0)
        for child in measure:
            if child.tag == "harmony":
                harmony_count += 1
                location = _location(part_id, measure, element="harmony")
                harmony_type = child.get("type")
                if harmony_type not in {None, "explicit", "implied"}:
                    diagnostics.append(
                        _error(
                            ImportCode.UNSUPPORTED_HARMONY_TYPE,
                            "alternate or unknown harmony analyses are deferred",
                            location,
                        )
                    )
                group_heads = [
                    element
                    for element in child
                    if element.tag in {"root", "function", "numeral"}
                ]
                if len(group_heads) > 1:
                    diagnostics.append(
                        _error(
                            ImportCode.STACKED_HARMONY_UNSUPPORTED,
                            "stacked or secondary harmony analyses are deferred",
                            location,
                        )
                    )
                offset_element = child.find("offset")
                offset = (
                    Fraction(0)
                    if offset_element is None
                    else _fraction_text(offset_element, limits)
                )
                if offset is None or offset != 0:
                    diagnostics.append(
                        _error(
                            ImportCode.HARMONY_OFFSET_UNSUPPORTED,
                            "nonzero or invalid harmony offsets are deferred",
                            _location(part_id, measure, element="offset"),
                        )
                    )
                    offset = Fraction(0)
                onset: Fraction | None = None
                unique_onset = False
                if divisions is not None and divisions > 0:
                    onset = global_measure_onset + (cursor + offset) / divisions
                    if onset in seen_onsets:
                        diagnostics.append(
                            _error(
                                ImportCode.DUPLICATE_HARMONY,
                                "multiple harmony symbols at the same onset are ambiguous",
                                location,
                            )
                        )
                    else:
                        seen_onsets[onset] = location
                        unique_onset = True

                if child.find("bass") is not None:
                    diagnostics.append(
                        _error(
                            ImportCode.SLASH_BASS_UNSUPPORTED,
                            "slash-bass harmony is deferred",
                            _location(part_id, measure, element="bass"),
                        )
                    )
                if child.find("inversion") is not None:
                    diagnostics.append(
                        _error(
                            ImportCode.SLASH_BASS_UNSUPPORTED,
                            "harmony inversions are deferred",
                            _location(part_id, measure, element="inversion"),
                        )
                    )
                if child.find("degree") is not None:
                    diagnostics.append(
                        _error(
                            ImportCode.HARMONY_DEGREE_UNSUPPORTED,
                            "harmony degree additions/alterations are deferred",
                            _location(part_id, measure, element="degree"),
                        )
                    )
                if child.find("function") is not None:
                    diagnostics.append(
                        _error(
                            ImportCode.FUNCTION_HARMONY_UNSUPPORTED,
                            "functional harmony is deferred",
                            _location(part_id, measure, element="function"),
                        )
                    )
                if child.find("numeral") is not None:
                    diagnostics.append(
                        _error(
                            ImportCode.NUMERAL_HARMONY_UNSUPPORTED,
                            "numeral harmony is deferred",
                            _location(part_id, measure, element="numeral"),
                        )
                    )
                kind = (_text(child.find("kind")) or "").strip()
                if kind == "none":
                    diagnostics.append(
                        _error(
                            ImportCode.NO_CHORD_UNSUPPORTED,
                            "N.C. harmony is deferred",
                            _location(part_id, measure, element="kind"),
                        )
                    )
                elif kind not in SUPPORTED_HARMONY_KINDS:
                    diagnostics.append(
                        _error(
                            ImportCode.UNSUPPORTED_HARMONY_KIND,
                            f"harmony kind {kind!r} is outside the frozen whitelist",
                            _location(part_id, measure, element="kind"),
                        )
                    )
                root = child.find("root")
                root_step = _text(root.find("root-step")) if root is not None else None
                if root_step not in set("ABCDEFG"):
                    diagnostics.append(
                        _error(
                            ImportCode.UNSUPPORTED_HARMONY_KIND,
                            "harmony requires a conventional A-G root",
                            _location(part_id, measure, element="root"),
                        )
                    )
                root_alter_value: Fraction | None = Fraction(0)
                root_alter = root.find("root-alter") if root is not None else None
                if root_alter is not None:
                    value = _fraction_text(root_alter, limits)
                    root_alter_value = value
                    if value is None or value.denominator != 1:
                        diagnostics.append(
                            _error(
                                ImportCode.MICROTONE_UNSUPPORTED,
                                "fractional harmony roots are deferred",
                                _location(part_id, measure, element="root-alter"),
                            )
                        )
                    elif abs(value) > 1:
                        diagnostics.append(
                            _error(
                                ImportCode.UNSUPPORTED_HARMONY_KIND,
                                "double-altered harmony roots are deferred",
                                _location(part_id, measure, element="root-alter"),
                            )
                        )
                if (
                    onset is not None
                    and unique_onset
                    and root_step in set("ABCDEFG")
                    and root_alter_value is not None
                    and root_alter_value.denominator == 1
                    and abs(root_alter_value) <= 1
                    and kind in HARMONY_KIND_SUFFIXES
                ):
                    alteration = int(root_alter_value)
                    accidental = "#" if alteration == 1 else "b" if alteration == -1 else ""
                    root_pc = (
                        {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[
                            root_step
                        ]
                        + alteration
                    ) % 12
                    events.append(
                        PreflightHarmonyEvent(
                            onset,
                            f"{root_step}{accidental}{HARMONY_KIND_SUFFIXES[kind]}",
                            root_pc,
                        )
                    )
            elif child.tag == "note" and child.find("chord") is None:
                duration = _fraction_text(child.find("duration"), limits)
                if duration is not None:
                    cursor += duration
        global_measure_onset += 4

    if harmony_count == 0:
        diagnostics.append(
            _error(
                ImportCode.MISSING_HARMONY,
                "the frozen lead-sheet contract requires at least one <harmony>",
                _location(part_id, part.find("measure"), element="harmony"),
            )
        )
    return tuple(events)


def _tie_types(note: Element) -> tuple[list[str], list[str]]:
    sound = [tie.get("type", "") for tie in note.findall("tie")]
    visual = [tied.get("type", "") for tied in note.findall("notations/tied")]
    return sound, visual


def _check_ties_and_measure_durations(
    part: Element,
    part_id: str,
    time_sig: tuple[int, int] | None,
    divisions: Fraction | None,
    diagnostics: list[ImportDiagnostic],
    limits: ImportLimits,
) -> tuple[Fraction | None, tuple[PreflightNoteEvent, ...]]:
    tie_state: _TieState | None = None
    events: list[PreflightNoteEvent] = []
    global_measure_onset = Fraction(0)
    current_time = time_sig
    timeline_valid = current_time is not None
    for measure in part.findall("measure"):
        time_element = measure.find("attributes/time")
        if time_element is not None:
            current_time = _supported_time_signature(time_element)
            if current_time is None:
                timeline_valid = False

        cursor = Fraction(0)
        for note in measure.findall("note"):
            if note.find("chord") is not None or note.find("grace") is not None:
                continue
            voice = _note_voice(note)
            duration_units = _fraction_text(note.find("duration"), limits)
            if duration_units is None or duration_units <= 0:
                diagnostics.append(
                    _error(
                        ImportCode.UNSUPPORTED_NOTE,
                        "ordinary notes/rests require a positive decimal duration",
                        _location(part_id, measure, voice=voice, element="duration"),
                    )
                )
                continue
            if divisions is None:
                continue
            onset = global_measure_onset + cursor / divisions
            duration = duration_units / divisions
            cursor += duration_units

            sound_types, visual_types = _tie_types(note)
            allowed_types = {"start", "stop"}
            location = _location(part_id, measure, voice=voice, element="tie")
            if (
                any(tie_type not in allowed_types for tie_type in sound_types + visual_types)
                or any(set(tie.attrib) != {"type"} for tie in note.findall("tie"))
                or len(sound_types) != len(set(sound_types))
                or len(visual_types) != len(set(visual_types))
                or (sound_types and visual_types and set(sound_types) != set(visual_types))
            ):
                diagnostics.append(
                    _error(
                        ImportCode.TIE_ERROR,
                        "<tie> and <tied> must contain matching, non-duplicate start/stop types",
                        location,
                    )
                )
                continue
            effective_types = set(sound_types or visual_types)
            pitch = _pitch_identity(note, limits)
            if effective_types and pitch is None:
                diagnostics.append(
                    _error(
                        ImportCode.TIE_ERROR,
                        "ties are valid only on pitched notes",
                        location,
                    )
                )
                continue

            has_start = "start" in effective_types
            has_stop = "stop" in effective_types
            pitch_midi = _pitch_midi(pitch)
            if pitch_midi is not None:
                if has_start and has_stop:
                    tie_type = "continue"
                elif has_start:
                    tie_type = "start"
                elif has_stop:
                    tie_type = "stop"
                else:
                    tie_type = None
                events.append(
                    PreflightNoteEvent(onset, duration, pitch_midi, tie_type)
                )
            if has_stop:
                if tie_state is None:
                    diagnostics.append(
                        _error(
                            ImportCode.TIE_ERROR,
                            "tie stop has no preceding start",
                            location,
                        )
                    )
                elif tie_state.pitch != pitch or tie_state.end != onset:
                    diagnostics.append(
                        _error(
                            ImportCode.TIE_ERROR,
                            "tie stop must be adjacent and match the started pitch",
                            location,
                        )
                    )
                    tie_state = None
                elif not has_start:
                    tie_state = None
            elif tie_state is not None:
                diagnostics.append(
                    _error(
                        ImportCode.TIE_ERROR,
                        "tie start is dangling before an untied event",
                        tie_state.location,
                    )
                )
                tie_state = None

            if has_start:
                if tie_state is not None and not has_stop:
                    diagnostics.append(
                        _error(
                            ImportCode.TIE_ERROR,
                            "a second tie start occurred before the first stopped",
                            location,
                        )
                    )
                elif pitch is not None:
                    tie_state = _TieState(pitch, onset + duration, location)
            elif has_stop and tie_state is not None:
                tie_state.end = onset + duration

        if divisions is not None and current_time is not None and current_time[1] > 0:
            expected_units = Fraction(current_time[0] * 4 * divisions, current_time[1])
            if cursor != expected_units:
                diagnostics.append(
                    _error(
                        ImportCode.INCOMPLETE_MEASURE,
                        f"measure duration is {cursor} divisions; expected {expected_units}",
                        _location(part_id, measure, element="measure"),
                    )
                )
        if current_time is not None and current_time[0] > 0 and current_time[1] > 0:
            global_measure_onset += Fraction(current_time[0] * 4, current_time[1])
        else:
            timeline_valid = False

    if tie_state is not None:
        diagnostics.append(
            _error(
                ImportCode.TIE_ERROR,
                "tie start has no matching stop before end of score",
                tie_state.location,
            )
        )
    duration_beats = global_measure_onset if timeline_valid else None
    return duration_beats, tuple(events)


def _metadata_text(root: Element, path: str) -> str:
    values = [_text(element) for element in root.findall(path)]
    return " | ".join(value for value in values if value is not None)


def preflight_musicxml(root: Element, limits: ImportLimits) -> PreflightResult:
    """Return all stable capability diagnostics and normalized global metadata."""

    diagnostics = _check_tree_resource_limits(root, limits)
    if any(
        diagnostic.code is ImportCode.INPUT_LIMIT_EXCEEDED
        for diagnostic in diagnostics
    ):
        return PreflightResult(tuple(diagnostics), None)
    diagnostics.extend(_check_external_resource_references(root))
    if any(diagnostic.code is ImportCode.UNSAFE_XML for diagnostic in diagnostics):
        return PreflightResult(tuple(diagnostics), None)
    selected = _check_parts_and_voices(root, diagnostics)
    if selected is None:
        return PreflightResult(tuple(diagnostics), None)
    part, part_id = selected

    _scan_unsupported_elements(part, part_id, diagnostics, limits)
    divisions = _check_divisions(part, part_id, diagnostics, limits)
    key = _check_keys(part, part_id, diagnostics)
    time_sig = _check_times(part, part_id, diagnostics)
    tempo = _check_tempi(part, part_id, diagnostics, limits)
    harmony_events = _check_harmonies(part, part_id, divisions, diagnostics, limits)
    duration_beats, note_events = _check_ties_and_measure_durations(
        part, part_id, time_sig, divisions, diagnostics, limits
    )

    title = _metadata_text(root, "work/work-title") or _metadata_text(root, "movement-title")
    rights = _metadata_text(root, "identification/rights")
    if not rights:
        rights = "unprovided"
        diagnostics.append(
            _warning(
                ImportCode.RIGHTS_UNPROVIDED,
                "source supplied no rights/license metadata",
                SourceLocation(element="rights"),
            )
        )

    metadata = None
    if (
        key is not None
        and time_sig is not None
        and tempo is not None
        and duration_beats is not None
    ):
        metadata = PreflightMetadata(
            part_id,
            key,
            time_sig,
            tempo,
            title,
            rights,
            duration_beats,
            note_events,
            harmony_events,
        )
    return PreflightResult(tuple(diagnostics), metadata)
