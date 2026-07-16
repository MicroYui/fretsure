from __future__ import annotations

from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from fretsure.importers import (
    ImportCode,
    ImportFailure,
    ImportLimits,
    ImportSuccess,
    import_musicxml,
)


def _note(
    *,
    pitch: str = "<pitch><step>C</step><octave>4</octave></pitch>",
    duration: str = "1",
    voice: str = "1",
    extra: str = "",
) -> str:
    return (
        f"<note>{pitch}<duration>{duration}</duration><voice>{voice}</voice>"
        f"<type>quarter</type>{extra}</note>"
    )


def _attributes(*, extra: str = "", divisions: str = "1") -> str:
    return f"""
      <attributes>
        <divisions>{divisions}</divisions>
        <key><fifths>0</fifths><mode>major</mode></key>
        <time><beats>4</beats><beat-type>4</beat-type></time>
        {extra}
      </attributes>
"""


def _tempo(value: str = "96", beat_unit: str = "quarter") -> str:
    return f"""
      <direction>
        <direction-type><metronome><beat-unit>{beat_unit}</beat-unit>
          <per-minute>{value}</per-minute></metronome></direction-type>
        <sound tempo="{value}"/>
      </direction>
"""


def _harmony(extra: str = "", *, kind: str = "major", root: str | None = "C") -> str:
    root_xml = "" if root is None else f"<root><root-step>{root}</root-step></root>"
    return f"<harmony>{root_xml}<kind>{kind}</kind>{extra}</harmony>"


def _measure(
    number: str,
    *,
    prefix: str = "",
    notes: str | None = None,
    suffix: str = "",
    attributes: str = "",
) -> str:
    note_xml = notes if notes is not None else "".join(_note() for _ in range(4))
    return f'<measure number="{number}">{attributes}{prefix}{note_xml}{suffix}</measure>'


def _score(
    first_measure: str,
    *,
    second_measure: str = "",
    second_part: str = "",
    rights: bool = True,
) -> str:
    rights_xml = "<identification><rights>CC0-1.0</rights></identification>" if rights else ""
    second_score_part = (
        '<score-part id="P2"><part-name>Other</part-name></score-part>' if second_part else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <work><work-title>Preflight</work-title></work>{rights_xml}
  <part-list><score-part id="P1"><part-name>Melody</part-name></score-part>
    {second_score_part}</part-list>
  <part id="P1">{first_measure}{second_measure}</part>{second_part}
</score-partwise>
"""


def _valid_first(*, notes: str | None = None, extra_attributes: str = "") -> str:
    return _measure(
        "1",
        attributes=_attributes(extra=extra_attributes),
        prefix=_tempo() + _harmony(),
        notes=notes,
    )


def _write(tmp_path: Path, xml: str, name: str = "case.musicxml") -> Path:
    path = tmp_path / name
    path.write_text(xml, encoding="utf-8")
    return path


def _diagnostic_for(path: Path, code: ImportCode):
    result = import_musicxml(path)
    assert isinstance(result, ImportFailure)
    matches = [diagnostic for diagnostic in result.diagnostics if diagnostic.code is code]
    assert matches, [diagnostic.code for diagnostic in result.diagnostics]
    return matches[0]


@pytest.mark.parametrize(
    ("case", "xml", "code", "element"),
    [
        (
            "multiple_parts",
            _score(
                _valid_first(),
                second_part=f'<part id="P2">{_valid_first()}</part>',
            ),
            ImportCode.MULTIPLE_NOTE_BEARING_PARTS,
            "part",
        ),
        (
            "multiple_voices",
            _score(_valid_first(notes=_note(voice="1") + _note(voice="2") + _note() + _note())),
            ImportCode.MULTIPLE_VOICES_UNSUPPORTED,
            "voice",
        ),
        (
            "multiple_staves",
            _score(_valid_first(extra_attributes="<staves>2</staves>")),
            ImportCode.MULTIPLE_STAVES_UNSUPPORTED,
            "staves",
        ),
        (
            "chord_notation",
            _score(
                _valid_first(notes=_note() + _note(extra="<chord/>") + _note() + _note() + _note())
            ),
            ImportCode.CHORD_NOTATION_UNSUPPORTED,
            "chord",
        ),
        (
            "repeat",
            _score(
                _measure(
                    "1",
                    attributes=_attributes(),
                    prefix=_tempo(),
                    suffix='<barline><repeat direction="backward"/></barline>',
                )
            ),
            ImportCode.REPEAT_UNSUPPORTED,
            "repeat",
        ),
        (
            "ending",
            _score(
                _measure(
                    "1",
                    attributes=_attributes(),
                    prefix=_tempo(),
                    suffix='<barline><ending number="1" type="start"/></barline>',
                )
            ),
            ImportCode.ENDING_UNSUPPORTED,
            "ending",
        ),
        (
            "navigation",
            _score(
                _valid_first().replace(
                    _tempo(),
                    _tempo() + "<direction><direction-type><segno/></direction-type></direction>",
                )
            ),
            ImportCode.NAVIGATION_UNSUPPORTED,
            "segno",
        ),
        (
            "measure_repeat",
            _score(
                _valid_first(
                    extra_attributes=(
                        '<measure-style><measure-repeat type="start">1</measure-repeat>'
                        "</measure-style>"
                    )
                )
            ),
            ImportCode.MEASURE_REPEAT_UNSUPPORTED,
            "measure-repeat",
        ),
        (
            "beat_repeat",
            _score(
                _valid_first(
                    extra_attributes=('<measure-style><beat-repeat type="start"/></measure-style>')
                )
            ),
            ImportCode.MEASURE_REPEAT_UNSUPPORTED,
            "beat-repeat",
        ),
        (
            "rhythmic_slash",
            _score(
                _valid_first(
                    extra_attributes=('<measure-style><slash type="start"/></measure-style>')
                )
            ),
            ImportCode.TIMELINE_CONTROL_UNSUPPORTED,
            "slash",
        ),
        (
            "multiple_rest",
            _score(
                _valid_first(
                    extra_attributes="<measure-style><multiple-rest>2</multiple-rest></measure-style>"
                )
            ),
            ImportCode.MULTIPLE_REST_UNSUPPORTED,
            "multiple-rest",
        ),
        (
            "tuplet",
            _score(
                _valid_first(
                    notes=_note(
                        extra="<time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification>"
                    )
                    + _note()
                    + _note()
                    + _note()
                )
            ),
            ImportCode.TUPLET_UNSUPPORTED,
            "time-modification",
        ),
        (
            "grace",
            _score(
                _valid_first(
                    notes="<note><grace/><pitch><step>D</step><octave>4</octave></pitch><voice>1</voice></note>"
                    + "".join(_note() for _ in range(4))
                )
            ),
            ImportCode.GRACE_NOTE_UNSUPPORTED,
            "grace",
        ),
        (
            "cue",
            _score(_valid_first(notes=_note(extra="<cue/>") + "".join(_note() for _ in range(3)))),
            ImportCode.CUE_NOTE_UNSUPPORTED,
            "cue",
        ),
        (
            "transpose",
            _score(
                _valid_first(extra_attributes="<transpose><chromatic>2</chromatic></transpose>")
            ),
            ImportCode.TRANSPOSE_UNSUPPORTED,
            "transpose",
        ),
        (
            "microtone",
            _score(
                _valid_first(
                    notes=_note(
                        pitch="<pitch><step>C</step><alter>0.5</alter><octave>4</octave></pitch>"
                    )
                    + "".join(_note() for _ in range(3))
                )
            ),
            ImportCode.MICROTONE_UNSUPPORTED,
            "alter",
        ),
        (
            "unpitched",
            _score(
                _valid_first(
                    notes=_note(
                        pitch="<unpitched><display-step>C</display-step><display-octave>4</display-octave></unpitched>"
                    )
                    + "".join(_note() for _ in range(3))
                )
            ),
            ImportCode.UNPITCHED_UNSUPPORTED,
            "unpitched",
        ),
        (
            "implicit_pickup",
            _score(
                _valid_first().replace(
                    '<measure number="1">', '<measure number="1" implicit="yes">'
                )
            ),
            ImportCode.PICKUP_UNSUPPORTED,
            "measure",
        ),
        (
            "incomplete_measure",
            _score(_valid_first(notes="".join(_note() for _ in range(3)))),
            ImportCode.INCOMPLETE_MEASURE,
            "measure",
        ),
        (
            "key_change",
            _score(
                _valid_first(),
                second_measure=_measure(
                    "2",
                    attributes="<attributes><key><fifths>1</fifths><mode>major</mode></key></attributes>",
                ),
            ),
            ImportCode.KEY_CHANGE_UNSUPPORTED,
            "key",
        ),
        (
            "time_change",
            _score(
                _valid_first(),
                second_measure=_measure(
                    "2",
                    attributes="<attributes><time><beats>3</beats><beat-type>4</beat-type></time></attributes>",
                    notes="".join(_note() for _ in range(3)),
                ),
            ),
            ImportCode.TIME_SIGNATURE_CHANGE_UNSUPPORTED,
            "time",
        ),
        (
            "tempo_change",
            _score(_valid_first(), second_measure=_measure("2", prefix=_tempo("120"))),
            ImportCode.TEMPO_CHANGE_UNSUPPORTED,
            "metronome",
        ),
        (
            "slash_harmony",
            _score(
                _valid_first().replace(
                    _harmony(), _harmony("<bass><bass-step>E</bass-step></bass>")
                )
            ),
            ImportCode.SLASH_BASS_UNSUPPORTED,
            "bass",
        ),
        (
            "harmony_inversion",
            _score(_valid_first().replace(_harmony(), _harmony("<inversion>1</inversion>"))),
            ImportCode.SLASH_BASS_UNSUPPORTED,
            "inversion",
        ),
        (
            "harmony_degree",
            _score(
                _valid_first().replace(
                    _harmony(),
                    _harmony(
                        "<degree><degree-value>9</degree-value><degree-alter>1</degree-alter><degree-type>add</degree-type></degree>"
                    ),
                )
            ),
            ImportCode.HARMONY_DEGREE_UNSUPPORTED,
            "degree",
        ),
        (
            "function_harmony",
            _score(
                _valid_first().replace(
                    _harmony(), "<harmony><function>V</function><kind>major</kind></harmony>"
                )
            ),
            ImportCode.FUNCTION_HARMONY_UNSUPPORTED,
            "function",
        ),
        (
            "numeral_harmony",
            _score(
                _valid_first().replace(
                    _harmony(),
                    "<harmony><numeral><numeral-root>5</numeral-root></numeral><kind>major</kind></harmony>",
                )
            ),
            ImportCode.NUMERAL_HARMONY_UNSUPPORTED,
            "numeral",
        ),
        (
            "no_chord",
            _score(_valid_first().replace(_harmony(), _harmony(kind="none", root=None))),
            ImportCode.NO_CHORD_UNSUPPORTED,
            "kind",
        ),
        (
            "unknown_harmony",
            _score(_valid_first().replace(_harmony(), _harmony(kind="Tristan"))),
            ImportCode.UNSUPPORTED_HARMONY_KIND,
            "kind",
        ),
        (
            "backup",
            _score(
                _valid_first().replace(
                    _harmony(), _harmony() + "<backup><duration>1</duration></backup>"
                )
            ),
            ImportCode.TIMELINE_CONTROL_UNSUPPORTED,
            "backup",
        ),
        (
            "forward",
            _score(
                _valid_first().replace(
                    _harmony(), _harmony() + "<forward><duration>1</duration></forward>"
                )
            ),
            ImportCode.TIMELINE_CONTROL_UNSUPPORTED,
            "forward",
        ),
        (
            "ornament",
            _score(
                _valid_first(
                    notes=_note(extra="<notations><ornaments><turn/></ornaments></notations>")
                    + "".join(_note() for _ in range(3))
                )
            ),
            ImportCode.PERFORMANCE_NOTATION_UNSUPPORTED,
            "ornaments",
        ),
        (
            "tremolo",
            _score(
                _valid_first(
                    notes=_note(
                        extra="<notations><ornaments><tremolo>3</tremolo></ornaments></notations>"
                    )
                    + "".join(_note() for _ in range(3))
                )
            ),
            ImportCode.PERFORMANCE_NOTATION_UNSUPPORTED,
            "tremolo",
        ),
        (
            "bend",
            _score(
                _valid_first(
                    notes=_note(
                        extra="<notations><technical><bend><bend-alter>1</bend-alter></bend></technical></notations>"
                    )
                    + "".join(_note() for _ in range(3))
                )
            ),
            ImportCode.PERFORMANCE_NOTATION_UNSUPPORTED,
            "bend",
        ),
        (
            "harmonic",
            _score(
                _valid_first(
                    notes=_note(
                        extra="<notations><technical><harmonic><natural/>"
                        "</harmonic></technical></notations>"
                    )
                    + "".join(_note() for _ in range(3))
                )
            ),
            ImportCode.PERFORMANCE_NOTATION_UNSUPPORTED,
            "harmonic",
        ),
        (
            "damp",
            _score(
                _valid_first().replace(
                    _tempo(),
                    _tempo() + "<direction><direction-type><damp>yes</damp>"
                    "</direction-type></direction>",
                )
            ),
            ImportCode.PERFORMANCE_NOTATION_UNSUPPORTED,
            "damp",
        ),
        (
            "other_direction",
            _score(
                _valid_first().replace(
                    _tempo(),
                    _tempo() + "<direction><direction-type><other-direction>rit."
                    "</other-direction></direction-type></direction>",
                )
            ),
            ImportCode.PERFORMANCE_NOTATION_UNSUPPORTED,
            "other-direction",
        ),
        (
            "glissando",
            _score(
                _valid_first(
                    notes=_note(
                        extra='<notations><glissando type="start">gliss.</glissando></notations>'
                    )
                    + "".join(_note() for _ in range(3))
                )
            ),
            ImportCode.PERFORMANCE_NOTATION_UNSUPPORTED,
            "glissando",
        ),
        (
            "fermata",
            _score(
                _valid_first(
                    notes=_note(extra="<notations><fermata/></notations>")
                    + "".join(_note() for _ in range(3))
                )
            ),
            ImportCode.PERFORMANCE_NOTATION_UNSUPPORTED,
            "fermata",
        ),
    ],
)
def test_unsupported_semantics_are_rejected_before_music21(
    tmp_path: Path, case: str, xml: str, code: ImportCode, element: str
) -> None:
    diagnostic = _diagnostic_for(_write(tmp_path, xml, f"{case}.musicxml"), code)
    assert diagnostic.location is not None
    assert diagnostic.location.measure is not None or code is ImportCode.MULTIPLE_NOTE_BEARING_PARTS
    assert diagnostic.location.element == element


@pytest.mark.parametrize(
    ("remove", "code", "element"),
    [
        ("<divisions>1</divisions>", ImportCode.MISSING_DIVISIONS, "divisions"),
        ("<key><fifths>0</fifths><mode>major</mode></key>", ImportCode.MISSING_KEY, "key"),
        (
            "<time><beats>4</beats><beat-type>4</beat-type></time>",
            ImportCode.MISSING_TIME_SIGNATURE,
            "time",
        ),
        (_tempo(), ImportCode.MISSING_TEMPO, "metronome"),
    ],
)
def test_required_global_music_metadata_is_not_inferred(
    tmp_path: Path, remove: str, code: ImportCode, element: str
) -> None:
    xml = _score(_valid_first().replace(remove, ""))
    diagnostic = _diagnostic_for(_write(tmp_path, xml), code)
    assert diagnostic.location is not None
    assert diagnostic.location.element == element


@pytest.mark.parametrize(
    ("case", "first_remove", "second_attributes", "second_prefix", "code"),
    [
        (
            "late_key",
            "<key><fifths>0</fifths><mode>major</mode></key>",
            "<attributes><key><fifths>0</fifths><mode>major</mode></key></attributes>",
            "",
            ImportCode.MISSING_KEY,
        ),
        (
            "late_time",
            "<time><beats>4</beats><beat-type>4</beat-type></time>",
            "<attributes><time><beats>4</beats><beat-type>4</beat-type></time></attributes>",
            "",
            ImportCode.MISSING_TIME_SIGNATURE,
        ),
        (
            "late_tempo",
            _tempo(),
            "",
            _tempo(),
            ImportCode.MISSING_TEMPO,
        ),
    ],
)
def test_global_metadata_must_start_at_measure_one_onset_zero(
    tmp_path: Path,
    case: str,
    first_remove: str,
    second_attributes: str,
    second_prefix: str,
    code: ImportCode,
) -> None:
    first = _valid_first().replace(first_remove, "")
    second = _measure("2", attributes=second_attributes, prefix=second_prefix)
    diagnostic = _diagnostic_for(
        _write(tmp_path, _score(first, second_measure=second), f"{case}.musicxml"),
        code,
    )
    assert diagnostic.location is not None
    assert diagnostic.location.measure == "1"


def test_note_must_not_contain_both_pitch_and_rest(tmp_path: Path) -> None:
    both = "<pitch><step>C</step><octave>4</octave></pitch><rest/>"
    xml = _score(_valid_first(notes=_note(pitch=both) + "".join(_note() for _ in range(3))))
    diagnostic = _diagnostic_for(_write(tmp_path, xml), ImportCode.UNSUPPORTED_NOTE)
    assert diagnostic.location is not None
    assert diagnostic.location.element == "note"


def test_sounding_sound_attributes_are_not_silently_ignored(tmp_path: Path) -> None:
    xml = _score(
        _valid_first().replace('<sound tempo="96"/>', '<sound tempo="96" pizzicato="yes"/>')
    )
    diagnostic = _diagnostic_for(_write(tmp_path, xml), ImportCode.PERFORMANCE_NOTATION_UNSUPPORTED)
    assert diagnostic.location is not None
    assert diagnostic.location.element == "sound"


@pytest.mark.parametrize(
    "attribute",
    [
        'attack="1"',
        'release="-1"',
        'time-only="1"',
        'pizzicato="yes"',
        'future-sounding-attribute="yes"',
    ],
)
def test_sounding_or_unknown_note_attributes_fail_closed(
    tmp_path: Path, attribute: str
) -> None:
    xml = _score(_valid_first().replace("<note>", f"<note {attribute}>", 1))

    diagnostic = _diagnostic_for(
        _write(tmp_path, xml, "note-attribute.musicxml"),
        ImportCode.PERFORMANCE_NOTATION_UNSUPPORTED,
    )

    assert diagnostic.location is not None
    assert diagnostic.location.element == "note"


@pytest.mark.parametrize("harmony_type", ["alternate", "future-analysis"])
def test_alternate_or_unknown_harmony_type_fails_closed(
    tmp_path: Path, harmony_type: str
) -> None:
    xml = _score(
        _valid_first().replace(
            "<harmony>", f'<harmony type="{harmony_type}">', 1
        )
    )

    diagnostic = _diagnostic_for(
        _write(tmp_path, xml, "harmony-type.musicxml"),
        ImportCode.UNSUPPORTED_HARMONY_TYPE,
    )

    assert diagnostic.location is not None
    assert diagnostic.location.element == "harmony"


@pytest.mark.parametrize("harmony_type", ["explicit", "implied"])
def test_supported_harmony_types_remain_source_chords(
    tmp_path: Path, harmony_type: str
) -> None:
    xml = _score(
        _valid_first().replace(
            "<harmony>", f'<harmony type="{harmony_type}">', 1
        )
    )

    result = import_musicxml(
        _write(tmp_path, xml, f"{harmony_type}-harmony.musicxml")
    )

    assert isinstance(result, ImportSuccess), getattr(result, "diagnostics", None)
    assert result.ir.chords[0].symbol == "C"


def test_stacked_secondary_harmony_fails_closed(tmp_path: Path) -> None:
    stacked = (
        "<harmony><root><root-step>C</root-step></root><kind>major</kind>"
        "<root><root-step>G</root-step></root><kind>dominant</kind></harmony>"
    )
    xml = _score(_valid_first().replace(_harmony(), stacked, 1))

    diagnostic = _diagnostic_for(
        _write(tmp_path, xml, "stacked-harmony.musicxml"),
        ImportCode.STACKED_HARMONY_UNSUPPORTED,
    )

    assert diagnostic.location is not None
    assert diagnostic.location.element == "harmony"


def test_visual_and_velocity_note_attributes_follow_the_frozen_loss_policy(
    tmp_path: Path,
) -> None:
    attributes = (
        'default-x="10" color="#000000" print-object="yes" '
        'dynamics="70" end-dynamics="60"'
    )
    xml = _score(_valid_first().replace("<note>", f"<note {attributes}>", 1))

    result = import_musicxml(_write(tmp_path, xml, "lossy-note-attributes.musicxml"))

    assert isinstance(result, ImportSuccess), getattr(result, "diagnostics", None)
    assert [warning.code for warning in result.warnings].count(ImportCode.IGNORED_NOTATION) == 1


def test_unknown_measure_element_fails_closed(tmp_path: Path) -> None:
    xml = _score(_valid_first().replace(_harmony(), _harmony() + "<future-sound/>"))
    diagnostic = _diagnostic_for(_write(tmp_path, xml), ImportCode.UNSUPPORTED_ELEMENT)
    assert diagnostic.location is not None
    assert diagnostic.location.element == "future-sound"


def test_nonpositive_divisions_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, _score(_valid_first().replace("<divisions>1", "<divisions>0")))
    assert _diagnostic_for(path, ImportCode.INVALID_DIVISIONS).location is not None


def test_mid_measure_divisions_change_fails_before_timeline_can_be_mistranslated(
    tmp_path: Path,
) -> None:
    notes = (
        _note(pitch="<pitch><step>C</step><octave>4</octave></pitch>", duration="5")
        + "<attributes><divisions>1</divisions></attributes>"
        + _note(pitch="<pitch><step>D</step><octave>4</octave></pitch>", duration="1")
        + "<attributes><divisions>4</divisions></attributes>"
        + _note(pitch="<pitch><step>E</step><octave>4</octave></pitch>", duration="2")
    )
    xml = _score(
        _measure(
            "1",
            attributes=_attributes(divisions="2"),
            prefix=_tempo() + _harmony(),
            notes=notes,
        )
    )

    diagnostic = _diagnostic_for(
        _write(tmp_path, xml, "divisions-change.musicxml"),
        ImportCode.DIVISIONS_CHANGE_UNSUPPORTED,
    )

    assert diagnostic.location is not None
    assert diagnostic.location.measure == "1"
    assert diagnostic.location.element == "divisions"


def test_decimal_divisions_and_durations_are_exactly_supported(tmp_path: Path) -> None:
    xml = _score(
        _valid_first()
        .replace("<divisions>1</divisions>", "<divisions>1.0</divisions>")
        .replace("<duration>1</duration>", "<duration>1.0</duration>")
    )
    result = import_musicxml(_write(tmp_path, xml, "decimal-divisions.musicxml"))

    assert isinstance(result, ImportSuccess), getattr(result, "diagnostics", None)
    assert all(note.duration == Fraction(1) for note in result.ir.notes)


@pytest.mark.parametrize("value", ["1/2", "1e-3", "1_0.0", "NaN", "Infinity"])
def test_divisions_rejects_non_xsd_decimal_lexical_forms(
    tmp_path: Path, value: str
) -> None:
    xml = _score(
        _valid_first().replace(
            "<divisions>1</divisions>", f"<divisions>{value}</divisions>"
        )
    )

    diagnostic = _diagnostic_for(
        _write(tmp_path, xml, f"bad-decimal-{value.replace('/', '-')}.musicxml"),
        ImportCode.INVALID_DIVISIONS,
    )

    assert diagnostic.location is not None
    assert diagnostic.location.element == "divisions"


def test_huge_decimal_exponent_is_rejected_without_big_integer_work(tmp_path: Path) -> None:
    xml = _score(
        _valid_first().replace(
            "<divisions>1</divisions>",
            "<divisions>1e-1000000000</divisions>",
        )
    )

    diagnostic = _diagnostic_for(
        _write(tmp_path, xml, "huge-exponent.musicxml"),
        ImportCode.INVALID_DIVISIONS,
    )

    assert diagnostic.location is not None
    assert diagnostic.location.element == "divisions"


def test_decimal_token_length_limit_is_typed_before_conversion(tmp_path: Path) -> None:
    xml = _score(
        _valid_first().replace(
            "<duration>1</duration>", "<duration>0.123456789</duration>", 1
        )
    )

    result = import_musicxml(
        _write(tmp_path, xml, "long-decimal.musicxml"),
        limits=ImportLimits(max_decimal_chars=8),
    )

    assert isinstance(result, ImportFailure)
    assert ImportCode.INPUT_LIMIT_EXCEEDED in {
        diagnostic.code for diagnostic in result.diagnostics
    }


def test_tree_resource_limit_stops_before_semantic_scans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fretsure.importers._musicxml_preflight as preflight_module

    def unexpected_semantic_scan(*args: object, **kwargs: object) -> None:
        raise AssertionError("semantic scan ran after a hard resource limit")

    monkeypatch.setattr(
        preflight_module, "_check_external_resource_references", unexpected_semantic_scan
    )
    result = import_musicxml(
        _write(tmp_path, _score(_valid_first()), "over-note-limit.musicxml"),
        limits=replace(ImportLimits(), max_notes=3),
    )

    assert isinstance(result, ImportFailure)
    assert [diagnostic.code for diagnostic in result.diagnostics] == [
        ImportCode.INPUT_LIMIT_EXCEEDED
    ]


@pytest.mark.parametrize("value", ["0.999", "1000.001", "1e2", "1e999999999"])
def test_tempo_is_a_bounded_xsd_decimal(tmp_path: Path, value: str) -> None:
    xml = _score(_valid_first().replace(_tempo(), _tempo(value)))

    diagnostic = _diagnostic_for(
        _write(tmp_path, xml, f"bad-tempo-{value}.musicxml"),
        ImportCode.UNSUPPORTED_TEMPO,
    )

    assert diagnostic.location is not None
    assert diagnostic.location.element in {"metronome", "sound"}


@pytest.mark.parametrize("value", ["1", "1000"])
def test_tempo_contract_accepts_inclusive_bounds(tmp_path: Path, value: str) -> None:
    xml = _score(_valid_first().replace(_tempo(), _tempo(value)))

    result = import_musicxml(_write(tmp_path, xml, f"tempo-{value}.musicxml"))

    assert isinstance(result, ImportSuccess), getattr(result, "diagnostics", None)
    assert result.ir.meta.tempo_bpm == float(value)


def test_standalone_sound_is_a_supported_global_quarter_tempo(tmp_path: Path) -> None:
    xml = _score(_valid_first().replace(_tempo(), '<sound tempo="96"/>'))

    result = import_musicxml(_write(tmp_path, xml, "standalone-tempo.musicxml"))

    assert isinstance(result, ImportSuccess), getattr(result, "diagnostics", None)
    assert result.ir.meta.tempo_bpm == 96.0


def test_midscore_standalone_sound_tempo_is_a_typed_change(tmp_path: Path) -> None:
    second = _measure("2", prefix='<sound tempo="200"/>')
    xml = _score(_valid_first(), second_measure=second)

    diagnostic = _diagnostic_for(
        _write(tmp_path, xml, "standalone-tempo-change.musicxml"),
        ImportCode.TEMPO_CHANGE_UNSUPPORTED,
    )

    assert diagnostic.location is not None
    assert diagnostic.location.measure == "2"


def test_sound_offset_cannot_override_direction_offset(tmp_path: Path) -> None:
    xml = _score(
        _valid_first().replace(
            '<sound tempo="96"/>',
            '<sound tempo="96"><offset>0</offset></sound>',
        )
    )

    diagnostic = _diagnostic_for(
        _write(tmp_path, xml, "sound-offset.musicxml"),
        ImportCode.UNSUPPORTED_TEMPO,
    )

    assert diagnostic.location is not None
    assert diagnostic.location.element == "offset"


def test_huge_unsupported_time_token_never_enters_fraction_arithmetic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fretsure.importers._musicxml_preflight as preflight_module

    exact_fraction = Fraction

    def guarded_fraction(*args: object) -> Fraction:
        for value in args:
            if isinstance(value, int) and value.bit_length() > 64:
                raise AssertionError("unsupported time token reached Fraction")
        return exact_fraction(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(preflight_module, "Fraction", guarded_fraction)
    huge = "9" * 1_000
    xml = _score(
        _valid_first().replace(
            "<beat-type>4</beat-type>", f"<beat-type>{huge}</beat-type>"
        )
    )

    diagnostic = _diagnostic_for(
        _write(tmp_path, xml, "huge-time-token.musicxml"),
        ImportCode.UNSUPPORTED_TIME_SIGNATURE,
    )

    assert diagnostic.location is not None
    assert diagnostic.location.element == "time"


@pytest.mark.parametrize("words", ["ritardando", "a tempo", "swing", "8va"])
def test_text_directions_fail_closed_when_sounding_intent_is_unknown(
    tmp_path: Path, words: str
) -> None:
    direction = f"<direction><direction-type><words>{words}</words></direction-type></direction>"
    xml = _score(_valid_first().replace(_tempo(), _tempo() + direction))

    diagnostic = _diagnostic_for(
        _write(tmp_path, xml, "text-direction.musicxml"),
        ImportCode.PERFORMANCE_NOTATION_UNSUPPORTED,
    )

    assert diagnostic.location is not None
    assert diagnostic.location.element == "words"


def test_tie_time_only_attribute_fails_closed(tmp_path: Path) -> None:
    start = _note(extra='<tie type="start" time-only="2"/>')
    stop = _note(extra='<tie type="stop"/>')
    xml = _score(_valid_first(notes=start + stop + _note() + _note()))

    diagnostic = _diagnostic_for(
        _write(tmp_path, xml, "tie-time-only.musicxml"), ImportCode.TIE_ERROR
    )

    assert diagnostic.location is not None
    assert diagnostic.location.element == "tie"


@pytest.mark.parametrize(
    ("case", "tempo_xml"),
    [
        ("conflict", _tempo().replace('sound tempo="96"', 'sound tempo="120"')),
        ("non_quarter", _tempo(beat_unit="eighth")),
    ],
)
def test_ambiguous_or_non_quarter_tempo_is_rejected(
    tmp_path: Path, case: str, tempo_xml: str
) -> None:
    xml = _score(_valid_first().replace(_tempo(), tempo_xml))
    diagnostic = _diagnostic_for(
        _write(tmp_path, xml, f"{case}.musicxml"), ImportCode.UNSUPPORTED_TEMPO
    )
    assert diagnostic.location is not None
    assert diagnostic.location.measure == "1"


@pytest.mark.parametrize(
    ("case", "notes"),
    [
        ("zero_duration", _note(duration="0") + "".join(_note() for _ in range(3))),
        (
            "invalid_pitch",
            _note(pitch="<pitch><step>H</step><octave>4</octave></pitch>")
            + "".join(_note() for _ in range(3)),
        ),
        (
            "missing_step",
            _note(pitch="<pitch><octave>4</octave></pitch>") + "".join(_note() for _ in range(3)),
        ),
        (
            "missing_octave",
            _note(pitch="<pitch><step>C</step></pitch>") + "".join(_note() for _ in range(3)),
        ),
        (
            "multiple_pitch",
            _note(
                pitch=(
                    "<pitch><step>C</step><octave>4</octave></pitch>"
                    "<pitch><step>D</step><octave>4</octave></pitch>"
                )
            )
            + "".join(_note() for _ in range(3)),
        ),
        (
            "multiple_rest",
            _note(pitch="<rest/><rest/>") + "".join(_note() for _ in range(3)),
        ),
    ],
)
def test_invalid_ordinary_notes_are_typed(tmp_path: Path, case: str, notes: str) -> None:
    xml = _score(_valid_first(notes=notes))
    diagnostic = _diagnostic_for(
        _write(tmp_path, xml, f"{case}.musicxml"), ImportCode.UNSUPPORTED_NOTE
    )
    assert diagnostic.location is not None
    assert diagnostic.location.measure == "1"


def test_harmony_is_required_for_the_frozen_lead_sheet_contract(tmp_path: Path) -> None:
    xml = _score(_valid_first().replace(_harmony(), ""))
    diagnostic = _diagnostic_for(_write(tmp_path, xml), ImportCode.MISSING_HARMONY)
    assert diagnostic.location is not None
    assert diagnostic.location.measure == "1"
    assert diagnostic.location.element == "harmony"


def test_duplicate_harmony_at_one_onset_is_rejected(tmp_path: Path) -> None:
    xml = _score(_valid_first().replace(_harmony(), _harmony() + _harmony(kind="minor")))
    diagnostic = _diagnostic_for(_write(tmp_path, xml), ImportCode.DUPLICATE_HARMONY)
    assert diagnostic.location is not None
    assert diagnostic.location.measure == "1"
    assert diagnostic.location.element == "harmony"


@pytest.mark.parametrize("offset", ["-1", "1", "not-a-number"])
def test_harmony_offset_is_explicitly_deferred(tmp_path: Path, offset: str) -> None:
    shifted = _harmony(f"<offset>{offset}</offset>")
    xml = _score(_valid_first().replace(_harmony(), shifted))
    diagnostic = _diagnostic_for(_write(tmp_path, xml), ImportCode.HARMONY_OFFSET_UNSUPPORTED)
    assert diagnostic.location is not None
    assert diagnostic.location.measure == "1"
    assert diagnostic.location.element == "offset"


@pytest.mark.parametrize(
    ("case", "notes", "expected_measure"),
    [
        (
            "dangling_start",
            _note(extra='<tie type="start"/><notations><tied type="start"/></notations>')
            + "".join(_note() for _ in range(3)),
            "1",
        ),
        (
            "dangling_stop",
            _note(extra='<tie type="stop"/><notations><tied type="stop"/></notations>')
            + "".join(_note() for _ in range(3)),
            "1",
        ),
        (
            "repeated_start",
            _note(extra='<tie type="start"/><notations><tied type="start"/></notations>')
            + _note(extra='<tie type="start"/><notations><tied type="start"/></notations>')
            + _note()
            + _note(),
            "1",
        ),
        (
            "pitch_mismatch",
            _note(extra='<tie type="start"/><notations><tied type="start"/></notations>')
            + _note(
                pitch="<pitch><step>D</step><octave>4</octave></pitch>",
                extra='<tie type="stop"/><notations><tied type="stop"/></notations>',
            )
            + _note()
            + _note(),
            "1",
        ),
        (
            "tie_tied_disagree",
            _note(extra='<tie type="start"/><notations><tied type="stop"/></notations>')
            + "".join(_note() for _ in range(3)),
            "1",
        ),
    ],
)
def test_malformed_ties_are_typed_before_music21(
    tmp_path: Path, case: str, notes: str, expected_measure: str
) -> None:
    path = _write(tmp_path, _score(_valid_first(notes=notes)), f"{case}.musicxml")
    diagnostic = _diagnostic_for(path, ImportCode.TIE_ERROR)
    assert diagnostic.location is not None
    assert diagnostic.location.measure == expected_measure
    assert diagnostic.location.element in {"tie", "tied"}


@pytest.mark.parametrize(
    ("kind", "limits"),
    [
        ("measure", replace(ImportLimits(), max_measures=0)),
        ("note", replace(ImportLimits(), max_notes=3)),
        ("harmony", replace(ImportLimits(), max_harmonies=0)),
    ],
)
def test_semantic_resource_limits_are_typed(
    tmp_path: Path, kind: str, limits: ImportLimits
) -> None:
    path = _write(tmp_path, _score(_valid_first()))
    result = import_musicxml(path, limits=limits)
    assert isinstance(result, ImportFailure), kind
    assert ImportCode.INPUT_LIMIT_EXCEEDED in {d.code for d in result.diagnostics}


def test_lossy_annotations_emit_warnings_with_locations(tmp_path: Path) -> None:
    annotations = """
      <direction><direction-type><dynamics><p/></dynamics></direction-type></direction>
"""
    notes = _note(
        extra=(
            "<lyric><text>la</text></lyric>"
            '<notations><slur type="start"/><articulations><staccato/></articulations></notations>'
        )
    ) + "".join(_note() for _ in range(3))
    xml = _score(_valid_first(notes=notes).replace(_harmony(), _harmony() + annotations))
    result = import_musicxml(_write(tmp_path, xml))
    assert isinstance(result, ImportSuccess)
    ignored = [w for w in result.warnings if w.code is ImportCode.IGNORED_NOTATION]
    assert {w.location.element for w in ignored if w.location is not None} >= {
        "lyric",
        "slur",
        "dynamics",
        "articulations",
    }


def test_missing_rights_is_unprovided_not_guessed(tmp_path: Path) -> None:
    result = import_musicxml(_write(tmp_path, _score(_valid_first(), rights=False)))
    assert isinstance(result, ImportSuccess)
    assert result.ir.meta.license == "unprovided"
    assert ImportCode.RIGHTS_UNPROVIDED in {warning.code for warning in result.warnings}
