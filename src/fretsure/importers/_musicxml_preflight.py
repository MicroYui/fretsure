"""Raw MusicXML capability preflight.

This module inspects the safely parsed XML tree *before* music21 gets a chance
to normalize or discard notation.  The current frozen importer contract is deliberately
strict: unsupported sounding semantics become typed errors, while explicitly
lossy non-sounding annotations become located warnings.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
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
from fretsure.ir import MAX_IR_FRACTION_COMPONENT_BITS

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

_MAX_DIAGNOSTICS = 256
_MAX_LOCATION_SCALAR_UTF8_BYTES = 1024
_MAX_NUMERIC_TOKEN_CHARS = 3 * MAX_IR_FRACTION_COMPONENT_BITS

_XSD_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z", re.ASCII)
_XSD_INTEGER = re.compile(r"[+-]?[0-9]+\Z", re.ASCII)
_DECIMAL_TEXT_ELEMENTS = frozenset(
    {"alter", "divisions", "duration", "offset", "per-minute", "root-alter"}
)
_INTEGER_TEXT_ELEMENTS = frozenset(
    {
        "fifths",
        "octave",
        "staff",
        "staves",
    }
)
_NUMERIC_TEXT_ELEMENTS = _DECIMAL_TEXT_ELEMENTS | _INTEGER_TEXT_ELEMENTS

_KEY_ALLOWED_ATTRIBUTES = frozenset(
    {
        "color",
        "default-x",
        "default-y",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "id",
        "number",
        "print-object",
        "relative-x",
        "relative-y",
    }
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
_EXTERNAL_RESOURCE_ELEMENTS = frozenset({"credit-image", "image", "link", "opus"})
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
    pitch: tuple[str, Fraction, int]
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


class _BoundedDiagnostics(list[ImportDiagnostic]):
    """Bound diagnostics without silently truncating a would-be success."""

    __slots__ = ("_overflowed",)

    def __init__(self) -> None:
        super().__init__()
        self._overflowed = False

    def append(self, diagnostic: ImportDiagnostic) -> None:
        if self._overflowed:
            return
        if len(self) >= _MAX_DIAGNOSTICS:
            super().append(
                _error(
                    ImportCode.INPUT_LIMIT_EXCEEDED,
                    "MusicXML produced more than "
                    f"{_MAX_DIAGNOSTICS} source diagnostics; remaining diagnostics "
                    "were omitted",
                    SourceLocation(element="diagnostics"),
                )
            )
            self._overflowed = True
            return
        super().append(diagnostic)

    def extend(self, diagnostics: Iterable[ImportDiagnostic]) -> None:
        for diagnostic in diagnostics:
            self.append(diagnostic)


def _text(element: Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    value = element.text.strip(" \t\r\n")
    return value or None


def _xml_whitespace_only(value: str | None) -> bool:
    return value is None or not value.strip(" \t\r\n")


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


def _ir_bounded_decimal(value: str) -> Fraction | None:
    """Parse a lexical decimal only when its reduced components fit MusicIR.

    The lexical character limit is configurable, so it cannot by itself protect
    the fixed 256-bit MusicIR fraction contract. Normalize harmless padding
    before applying a small construction ceiling; any decimal whose reduced
    numerator and denominator both fit 256 bits is below this ceiling.
    """

    unsigned = value[1:] if value.startswith(("+", "-")) else value
    whole, separator, fractional = unsigned.partition(".")
    whole = whole.lstrip("0") or "0"
    if separator:
        fractional = fractional.rstrip("0")

    # Once trailing decimal zeroes are removed, the denominator retains at
    # least 2**scale or 5**scale. A scale of 256 therefore cannot fit the
    # public 256-bit denominator contract.
    if len(fractional) >= MAX_IR_FRACTION_COMPONENT_BITS:
        return None
    significant = (whole + fractional).lstrip("0") or "0"
    if len(significant) > 3 * MAX_IR_FRACTION_COMPONENT_BITS:
        return None

    sign = "-" if value.startswith("-") else ""
    normalized = sign + whole
    if fractional:
        normalized += "." + fractional
    try:
        parsed = Fraction(normalized)
    except (ValueError, ZeroDivisionError):
        return None
    if (
        parsed.numerator.bit_length() > MAX_IR_FRACTION_COMPONENT_BITS
        or parsed.denominator.bit_length() > MAX_IR_FRACTION_COMPONENT_BITS
    ):
        return None
    return parsed


def _fits_music_ir_fraction(value: Fraction) -> bool:
    """Return whether one exact value fits the public MusicIR component bound."""

    return (
        value.numerator.bit_length() <= MAX_IR_FRACTION_COMPONENT_BITS
        and value.denominator.bit_length() <= MAX_IR_FRACTION_COMPONENT_BITS
    )


def _xsd_decimal(value: str | None, limits: ImportLimits) -> Fraction | None:
    """Parse one IR-bounded XML Schema decimal without Python's broader grammar."""

    if (
        value is None
        or len(value) > limits.max_decimal_chars
        or len(value) > _MAX_NUMERIC_TOKEN_CHARS
    ):
        return None
    if _XSD_DECIMAL.fullmatch(value) is None:
        return None
    return _ir_bounded_decimal(value)


def _bounded_xsd_integer(
    value: str | None,
    limits: ImportLimits,
    *,
    max_significant_digits: int,
) -> int | None:
    """Parse a small ASCII XSD integer without constructing an attacker-sized int."""

    if (
        value is None
        or len(value) > limits.max_decimal_chars
        or len(value) > _MAX_NUMERIC_TOKEN_CHARS
        or _XSD_INTEGER.fullmatch(value) is None
    ):
        return None
    negative = value.startswith("-")
    digits = value[1:] if value.startswith(("+", "-")) else value
    significant = digits.lstrip("0") or "0"
    if len(significant) > max_significant_digits:
        return None
    parsed = int(significant)
    return -parsed if negative else parsed


def _fraction_text(element: Element | None, limits: ImportLimits) -> Fraction | None:
    value = _text(element)
    return _xsd_decimal(value, limits)


def _tempo_decimal(value: str | None, limits: ImportLimits) -> Fraction | None:
    parsed = _xsd_decimal(value, limits)
    if parsed is None or not Fraction(1) <= parsed <= Fraction(1000):
        return None
    return parsed


def _pitch_identity(note: Element, limits: ImportLimits) -> tuple[str, Fraction, int] | None:
    pitch = note.find("pitch")
    if pitch is None:
        return None
    step = _text(pitch.find("step"))
    octave = _text(pitch.find("octave"))
    octave_value = _bounded_xsd_integer(octave, limits, max_significant_digits=3)
    if step is None or octave_value is None:
        return None
    alter_element = pitch.find("alter")
    alter = Fraction(0) if alter_element is None else _fraction_text(alter_element, limits)
    if alter is None:
        return None
    return step, alter, octave_value


def _pitch_midi(identity: tuple[str, Fraction, int] | None) -> int | None:
    if identity is None:
        return None
    step, alter, octave = identity
    pitch_classes = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
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


def _key_name(fifths: int, mode: str | None) -> str | None:
    if not -7 <= fifths <= 7:
        return None
    if mode is None:
        return f"key-signature:fifths={fifths};mode=unprovided"
    index = fifths + 7
    if mode == "major":
        return _MAJOR_KEYS[index]
    if mode == "minor":
        return _MINOR_KEYS[index]
    return None


def _exceeds_utf8_bytes(value: str, maximum: int) -> bool:
    if len(value) > maximum:
        return True
    return len(value.encode("utf-8")) > maximum


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
        location_values: list[tuple[str, str]] = [("element-name", element.tag)]
        if element.tag in {"part", "score-part"} and element.get("id") is not None:
            location_values.append((f"{element.tag}@id", element.get("id", "")))
        if element.tag == "measure" and element.get("number") is not None:
            location_values.append(("measure@number", element.get("number", "")))
        if element.tag == "voice" and element.text is not None:
            location_values.append(("voice", element.text))
        for label, value in location_values:
            if _exceeds_utf8_bytes(value, _MAX_LOCATION_SCALAR_UTF8_BYTES):
                diagnostics.append(
                    _error(
                        ImportCode.INPUT_LIMIT_EXCEEDED,
                        f"{label} exceeds the {_MAX_LOCATION_SCALAR_UTF8_BYTES}-byte "
                        "diagnostic-location limit",
                        SourceLocation(element=label),
                    )
                )
                return diagnostics

        values: list[tuple[str, str]] = []
        if element.tag in _NUMERIC_TEXT_ELEMENTS and element.text is not None:
            values.append((element.tag, element.text.strip(" \t\r\n")))
        if element.tag == "sound" and "tempo" in element.attrib:
            values.append(("sound@tempo", element.attrib["tempo"].strip(" \t\r\n")))
        for label, value in values:
            numeric_limit = min(limits.max_decimal_chars, _MAX_NUMERIC_TOKEN_CHARS)
            if len(value) > numeric_limit:
                diagnostics.append(
                    _error(
                        ImportCode.INPUT_LIMIT_EXCEEDED,
                        f"{label} numeric token exceeds {numeric_limit} characters",
                        SourceLocation(element=label),
                    )
                )
                return diagnostics
            if _XSD_DECIMAL.fullmatch(value) is not None and _ir_bounded_decimal(value) is None:
                diagnostics.append(
                    _error(
                        ImportCode.INPUT_LIMIT_EXCEEDED,
                        f"{label} decimal components exceed the "
                        f"{MAX_IR_FRACTION_COMPONENT_BITS}-bit MusicIR limit",
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


def _check_external_resource_references(
    root: Element,
    diagnostics: list[ImportDiagnostic],
) -> None:
    """Reject XML constructs that can ask downstream parsers to dereference resources."""

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
            duplicate_note_scalars = [
                name
                for name in ("duration", "voice", "staff")
                if len(note.findall(name)) > 1
            ]
            if duplicate_note_scalars:
                diagnostics.append(
                    _error(
                        ImportCode.UNSUPPORTED_NOTE,
                        "repeated note scalar elements are ambiguous: "
                        + ", ".join(duplicate_note_scalars),
                        note_location("note"),
                    )
                )
            pitch = note.find("pitch")
            if pitch is not None:
                duplicate_pitch_scalars = [
                    name
                    for name in ("step", "alter", "octave")
                    if len(pitch.findall(name)) > 1
                ]
                if duplicate_pitch_scalars:
                    diagnostics.append(
                        _error(
                            ImportCode.UNSUPPORTED_NOTE,
                            "repeated pitch scalar elements are ambiguous: "
                            + ", ".join(duplicate_pitch_scalars),
                            note_location("pitch"),
                        )
                    )
                step = _text(pitch.find("step"))
                octave_text = _text(pitch.find("octave"))
                alter_value = (
                    Fraction(0)
                    if pitch.find("alter") is None
                    else _fraction_text(pitch.find("alter"), limits)
                )
                octave = _bounded_xsd_integer(
                    octave_text,
                    limits,
                    max_significant_digits=3,
                )
                pitch_classes = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
                midi = (
                    (octave + 1) * 12 + pitch_classes[step] + int(alter_value)
                    if step in pitch_classes
                    and octave is not None
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
    root: Element,
    diagnostics: list[ImportDiagnostic],
    limits: ImportLimits,
) -> tuple[Element, str] | None:
    part_lists = root.findall("part-list")
    if len(part_lists) != 1:
        diagnostics.append(
            _error(
                ImportCode.UNSUPPORTED_ELEMENT,
                "the frozen MusicXML contract requires one direct part-list",
                SourceLocation(element="part-list"),
            )
        )
        return None

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
        return None

    part = bearing[0]
    part_id = part.get("id") or ""
    matching_score_parts = [
        score_part
        for score_part in part_lists[0].findall("score-part")
        if (score_part.get("id") or "") == part_id
    ]
    if not part_id or len(matching_score_parts) != 1:
        diagnostics.append(
            _error(
                ImportCode.UNSUPPORTED_ELEMENT,
                "the note-bearing part requires one matching direct score-part and a non-empty id",
                SourceLocation(part_id=part_id or None, element="part"),
            )
        )
        return None
    for parent in part.iter():
        staff_children = parent.findall("staff")
        if len(staff_children) > 1:
            diagnostics.append(
                _error(
                    ImportCode.MULTIPLE_STAVES_UNSUPPORTED,
                    "multiple staff selectors on one element are ambiguous",
                    _location(part_id, None, element="staff"),
                )
            )
        for staff_element in staff_children:
            staff_value = _bounded_xsd_integer(
                _text(staff_element),
                limits,
                max_significant_digits=1,
            )
            if staff_value != 1:
                diagnostics.append(
                    _error(
                        ImportCode.MULTIPLE_STAVES_UNSUPPORTED,
                        "the frozen single-staff contract permits only staff 1",
                        _location(part_id, None, element="staff"),
                    )
                )
        if parent.tag in {"time", "clef", "staff-layout"}:
            number = parent.get("number")
            if number is not None and number != "1":
                diagnostics.append(
                    _error(
                        ImportCode.MULTIPLE_STAVES_UNSUPPORTED,
                        f"<{parent.tag}> number must be absent or canonical integer 1",
                        _location(part_id, None, element=parent.tag),
                    )
                )
    voices: dict[str, tuple[Element, Element]] = {}
    staffs: dict[str, tuple[Element, Element]] = {}
    for measure in part.findall("measure"):
        staves_elements = measure.findall("attributes/staves")
        if len(staves_elements) > 1:
            diagnostics.append(
                _error(
                    ImportCode.MULTIPLE_STAVES_UNSUPPORTED,
                    "multiple staves declarations in one attributes block are ambiguous",
                    _location(part_id, measure, element="staves"),
                )
            )
        staves = _text(staves_elements[0]) if staves_elements else None
        if staves is not None:
            staves_count = _bounded_xsd_integer(
                staves,
                limits,
                max_significant_digits=1,
            )
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
            raw_staff = _text(note.find("staff"))
            parsed_staff = (
                1
                if raw_staff is None
                else _bounded_xsd_integer(raw_staff, limits, max_significant_digits=1)
            )
            staff = str(parsed_staff) if parsed_staff is not None else "invalid"
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


def _check_keys(
    part: Element,
    part_id: str,
    musicxml_version: str | None,
    diagnostics: list[ImportDiagnostic],
    limits: ImportLimits,
) -> str | None:
    entries: list[tuple[int, str | None, Element]] = []
    invalid_shape = False
    direct_keys = [
        key
        for measure in part.findall("measure")
        for key in measure.findall("attributes/key")
    ]
    all_keys = list(part.iter("key"))
    if {id(key) for key in all_keys} != {id(key) for key in direct_keys}:
        diagnostics.append(
            _error(
                ImportCode.UNSUPPORTED_KEY,
                "every key must be a direct child of attributes in a measure",
                _location(part_id, None, element="key"),
            )
        )
        invalid_shape = True

    for scalar_name in ("fifths", "mode"):
        part_scalars = {id(element) for element in part.iter(scalar_name)}
        key_scalars = {
            id(element)
            for key in all_keys
            for element in key.iter(scalar_name)
        }
        if part_scalars != key_scalars:
            diagnostics.append(
                _error(
                    ImportCode.UNSUPPORTED_KEY,
                    f"every {scalar_name} must belong to a traditional key",
                    _location(part_id, None, element=scalar_name),
                )
            )
            invalid_shape = True

    for measure in part.findall("measure"):
        keys = measure.findall("attributes/key")
        if len(keys) > 1:
            diagnostics.append(
                _error(
                    ImportCode.UNSUPPORTED_KEY,
                    "at most one key declaration is supported per measure",
                    _location(part_id, measure, element="key"),
                )
            )
            invalid_shape = True
            continue
        for key in keys:
            unsupported_attributes = set(key.attrib).difference(_KEY_ALLOWED_ATTRIBUTES)
            if unsupported_attributes:
                diagnostics.append(
                    _error(
                        ImportCode.UNSUPPORTED_KEY,
                        "unsupported key attributes: "
                        + ", ".join(sorted(unsupported_attributes)),
                        _location(part_id, measure, element="key"),
                    )
                )
                invalid_shape = True
            unsupported_children = [
                child.tag for child in key if child.tag not in {"fifths", "mode"}
            ]
            if unsupported_children:
                diagnostics.append(
                    _error(
                        ImportCode.UNSUPPORTED_KEY,
                        "traditional key contains unsupported direct children: "
                        + ", ".join(unsupported_children),
                        _location(part_id, measure, element="key"),
                    )
                )
                invalid_shape = True
            if unsupported_attributes or unsupported_children:
                continue
            key_number = key.get("number")
            if key_number is not None and key_number != "1":
                diagnostics.append(
                    _error(
                        ImportCode.UNSUPPORTED_KEY,
                        "a single-staff key number must be absent or canonical integer 1",
                        _location(part_id, measure, element="key"),
                    )
                )
                invalid_shape = True
                continue
            if (
                next(key.iter("key-step"), None) is not None
                or next(key.iter("key-alter"), None) is not None
            ):
                diagnostics.append(
                    _error(
                        ImportCode.UNSUPPORTED_KEY,
                        "non-traditional keys are deferred",
                        _location(part_id, measure, element="key"),
                    )
                )
                invalid_shape = True
                continue
            fifths_elements = key.findall("fifths")
            mode_elements = key.findall("mode")
            child_tags = [child.tag for child in key]
            if (
                child_tags not in (["fifths"], ["fifths", "mode"])
                or len(fifths_elements) != 1
                or len(mode_elements) > 1
                or len(list(key.iter("fifths"))) != len(fifths_elements)
                or len(list(key.iter("mode"))) != len(mode_elements)
                or any(list(element) or element.attrib for element in fifths_elements)
                or any(list(element) or element.attrib for element in mode_elements)
                or not _xml_whitespace_only(key.text)
                or any(not _xml_whitespace_only(child.tail) for child in key)
            ):
                diagnostics.append(
                    _error(
                        ImportCode.UNSUPPORTED_KEY,
                        "a traditional key requires exactly one fifths and at most one mode",
                        _location(part_id, measure, element="key"),
                    )
                )
                invalid_shape = True
                continue
            fifths_text = _text(fifths_elements[0])
            mode_element = mode_elements[0] if mode_elements else None
            mode = None if mode_element is None else (mode_element.text or "")
            fifths = _bounded_xsd_integer(
                fifths_text,
                limits,
                max_significant_digits=1,
            )
            normalized_key = None if fifths is None else _key_name(fifths, mode)
            if normalized_key is None:
                diagnostics.append(
                    _error(
                        ImportCode.UNSUPPORTED_KEY,
                        "only traditional -7..7 fifths with major, minor, or an "
                        "entirely absent mode are supported",
                        _location(part_id, measure, element="key"),
                    )
                )
                if fifths is None:
                    invalid_shape = True
            elif mode is None:
                if musicxml_version == "4.0":
                    diagnostics.append(
                        _warning(
                            ImportCode.KEY_MODE_UNPROVIDED,
                            "traditional key signature supplied no mode; no mode was inferred",
                            _location(part_id, measure, element="key"),
                        )
                    )
                else:
                    diagnostics.append(
                        _error(
                            ImportCode.UNSUPPORTED_KEY,
                            "an absent key mode is supported only for MusicXML 4.0",
                            _location(part_id, measure, element="key"),
                        )
                    )
            if fifths is not None:
                entries.append((fifths, mode, measure))

    first_measure = part.find("measure")
    if invalid_shape:
        return None
    if not entries:
        diagnostics.append(
            _error(
                ImportCode.MISSING_KEY,
                "an explicit traditional key signature is required",
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
                divisions_elements = child.findall("divisions")
                if len(divisions_elements) > 1:
                    diagnostics.append(
                        _error(
                            ImportCode.INVALID_DIVISIONS,
                            "multiple divisions declarations in one attributes block are ambiguous",
                            _location(part_id, measure, element="divisions"),
                        )
                    )
                for element in divisions_elements:
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
                    "the current frozen importer contract requires one fixed divisions value",
                    _location(part_id, measure, element="divisions"),
                )
            )
    return first


def _supported_time_signature(time: Element) -> tuple[int, int] | None:
    """Recognize frozen 4/4 without constructing attacker-controlled integers."""

    beats_values = [_text(element) for element in time.findall("beats")]
    beat_type_values = [_text(element) for element in time.findall("beat-type")]
    if beats_values == ["4"] and beat_type_values == ["4"] and time.find("senza-misura") is None:
        return 4, 4
    return None


def _check_times(
    part: Element, part_id: str, diagnostics: list[ImportDiagnostic]
) -> tuple[int, int] | None:
    entries: list[tuple[tuple[int, int] | None, Element]] = []
    for measure in part.findall("measure"):
        times = measure.findall("attributes/time")
        if len(times) > 1:
            diagnostics.append(
                _error(
                    ImportCode.UNSUPPORTED_TIME_SIGNATURE,
                    "multiple time declarations in one measure are ambiguous",
                    _location(part_id, measure, element="time"),
                )
            )
        for time in times:
            signature = _supported_time_signature(time)
            entries.append((signature, measure))
            if signature is None:
                diagnostics.append(
                    _error(
                        ImportCode.UNSUPPORTED_TIME_SIGNATURE,
                        "the current frozen importer contract supports fixed 4/4 only",
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
    if len(direction.findall("offset")) > 1:
        diagnostics.append(
            _error(
                ImportCode.UNSUPPORTED_TEMPO,
                "multiple direction offsets are ambiguous",
                _location(part_id, measure, element="offset"),
            )
        )
    metronomes = list(direction.iter("metronome"))
    if len(metronomes) > 1:
        diagnostics.append(
            _error(
                ImportCode.UNSUPPORTED_TEMPO,
                "multiple metronome declarations in one direction are ambiguous",
                _location(part_id, measure, element="metronome"),
            )
        )
    for metronome in metronomes:
        beat_units = [_text(element) for element in metronome.findall("beat-unit")]
        dots = list(metronome.findall("beat-unit-dot"))
        per_minutes = metronome.findall("per-minute")
        per_minute = (
            _tempo_decimal(_text(per_minutes[0]), limits)
            if len(per_minutes) == 1
            else None
        )
        if beat_units != ["quarter"] or dots or len(per_minutes) != 1 or per_minute is None:
            diagnostics.append(
                _error(
                    ImportCode.UNSUPPORTED_TEMPO,
                    "tempo must be a quarter-note value in 1..1000 BPM",
                    _location(part_id, measure, element="metronome"),
                )
            )
        else:
            values.append(per_minute)

    sounds = direction.findall("sound")
    if len(sounds) > 1:
        diagnostics.append(
            _error(
                ImportCode.UNSUPPORTED_TEMPO,
                "multiple sound tempo declarations in one direction are ambiguous",
                _location(part_id, measure, element="sound"),
            )
        )
    for sound in sounds:
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
                tempo = _tempo_from_direction(child, part_id, measure, diagnostics, limits)
                if tempo is not None:
                    offset = _fraction_text(child.find("offset"), limits)
                    at_zero = (
                        measure is first_measure
                        and not elapsed
                        and (child.find("offset") is None or offset == 0)
                    )
                    entries.append((tempo, measure, at_zero))
            elif child.tag == "sound":
                tempo = _tempo_from_sound(child, part_id, measure, diagnostics, limits)
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
                    element for element in child if element.tag in {"root", "function", "numeral"}
                ]
                if len(group_heads) > 1:
                    diagnostics.append(
                        _error(
                            ImportCode.STACKED_HARMONY_UNSUPPORTED,
                            "stacked or secondary harmony analyses are deferred",
                            location,
                        )
                    )
                duplicate_harmony_scalars = [
                    name
                    for name in ("kind", "offset")
                    if len(child.findall(name)) > 1
                ]
                root_for_shape = child.find("root")
                if root_for_shape is not None:
                    duplicate_harmony_scalars.extend(
                        f"root/{name}"
                        for name in ("root-step", "root-alter")
                        if len(root_for_shape.findall(name)) > 1
                    )
                if duplicate_harmony_scalars:
                    diagnostics.append(
                        _error(
                            ImportCode.UNSUPPORTED_HARMONY_KIND,
                            "repeated harmony scalar elements are ambiguous: "
                            + ", ".join(duplicate_harmony_scalars),
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
                kind = _text(child.find("kind")) or ""
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
                        {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[root_step]
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
                events.append(PreflightNoteEvent(onset, duration, pitch_midi, tie_type))
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


def _check_derived_ir_fraction_limits(
    part_id: str,
    duration_beats: Fraction | None,
    note_events: tuple[PreflightNoteEvent, ...],
    harmony_events: tuple[PreflightHarmonyEvent, ...],
    diagnostics: list[ImportDiagnostic],
) -> None:
    """Reject exact IR-bound values that grow past the public limit.

    Bounding each lexical decimal is necessary but not sufficient: division,
    cursor addition, and tie coalescing can produce a larger reduced fraction.
    This pass mirrors every Fraction field the adapter can place in MusicIR so
    those inputs fail before music21 is imported or invoked.
    """

    def reject(value: Fraction, field: str) -> bool:
        if _fits_music_ir_fraction(value):
            return False
        diagnostics.append(
            _error(
                ImportCode.INPUT_LIMIT_EXCEEDED,
                f"derived {field} fraction components exceed the "
                f"{MAX_IR_FRACTION_COMPONENT_BITS}-bit MusicIR limit",
                SourceLocation(part_id=part_id, element=field),
            )
        )
        return True

    if duration_beats is not None and reject(duration_beats, "duration_beats"):
        return

    pending_tie_duration: Fraction | None = None
    for index, note_event in enumerate(note_events):
        if reject(note_event.onset, f"note[{index}].onset"):
            return
        if reject(note_event.duration, f"note[{index}].duration"):
            return

        if note_event.tie_type == "start":
            pending_tie_duration = note_event.duration
        elif note_event.tie_type in {"continue", "stop"} and pending_tie_duration is not None:
            pending_tie_duration += note_event.duration
            if reject(pending_tie_duration, f"note[{index}].tied_duration"):
                return
            if note_event.tie_type == "stop":
                pending_tie_duration = None
        elif note_event.tie_type is None:
            pending_tie_duration = None

    for index, harmony_event in enumerate(harmony_events):
        if reject(harmony_event.onset, f"chord[{index}].onset"):
            return


def _metadata_text(root: Element, path: str) -> str:
    values = [_text(element) for element in root.findall(path)]
    return " | ".join(value for value in values if value is not None)


def preflight_musicxml(root: Element, limits: ImportLimits) -> PreflightResult:
    """Return all stable capability diagnostics and normalized global metadata."""

    diagnostics = _BoundedDiagnostics()
    diagnostics.extend(_check_tree_resource_limits(root, limits))
    if any(diagnostic.code is ImportCode.INPUT_LIMIT_EXCEEDED for diagnostic in diagnostics):
        return PreflightResult(tuple(diagnostics), None)
    _check_external_resource_references(root, diagnostics)
    if any(
        diagnostic.code in {ImportCode.INPUT_LIMIT_EXCEEDED, ImportCode.UNSAFE_XML}
        for diagnostic in diagnostics
    ):
        return PreflightResult(tuple(diagnostics), None)
    selected = _check_parts_and_voices(root, diagnostics, limits)
    if selected is None:
        return PreflightResult(tuple(diagnostics), None)
    part, part_id = selected

    _scan_unsupported_elements(part, part_id, diagnostics, limits)
    if any(diagnostic.code is ImportCode.INPUT_LIMIT_EXCEEDED for diagnostic in diagnostics):
        return PreflightResult(tuple(diagnostics), None)
    divisions = _check_divisions(part, part_id, diagnostics, limits)
    key = _check_keys(part, part_id, root.get("version"), diagnostics, limits)
    time_sig = _check_times(part, part_id, diagnostics)
    tempo = _check_tempi(part, part_id, diagnostics, limits)
    harmony_events = _check_harmonies(part, part_id, divisions, diagnostics, limits)
    duration_beats, note_events = _check_ties_and_measure_durations(
        part, part_id, time_sig, divisions, diagnostics, limits
    )
    _check_derived_ir_fraction_limits(
        part_id,
        duration_beats,
        note_events,
        harmony_events,
        diagnostics,
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
