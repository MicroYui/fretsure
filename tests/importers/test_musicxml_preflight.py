from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest

from fretsure.importers import (
    DiagnosticSeverity,
    ImportCode,
    ImportFailure,
    ImportLimits,
    ImportSuccess,
    import_musicxml,
    import_musicxml_bytes,
)
from fretsure.ir import snapshot_music_ir


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
    version: str = "4.0",
) -> str:
    rights_xml = "<identification><rights>CC0-1.0</rights></identification>" if rights else ""
    second_score_part = (
        '<score-part id="P2"><part-name>Other</part-name></score-part>' if second_part else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="{version}">
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


def _without_key_mode(xml: str, fifths: int = 0) -> str:
    return xml.replace(
        "<key><fifths>0</fifths><mode>major</mode></key>",
        f"<key><fifths>{fifths}</fifths></key>",
        1,
    )


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
    ("case", "mutate", "expected_code"),
    [
        (
            "missing-part-list",
            lambda xml: xml.replace(
                '<part-list><score-part id="P1"><part-name>Melody</part-name></score-part>\n'
                "    </part-list>",
                "",
                1,
            ),
            ImportCode.UNSUPPORTED_ELEMENT,
        ),
        (
            "duplicate-part-list",
            lambda xml: xml.replace(
                "  <part id=\"P1\">",
                (
                    '  <part-list><score-part id="P2"><part-name>Other</part-name>'
                    "</score-part></part-list><part id=\"P1\">"
                ),
                1,
            ),
            ImportCode.UNSUPPORTED_ELEMENT,
        ),
        (
            "duplicate-score-part",
            lambda xml: xml.replace(
                "</part-list>",
                '<score-part id="P1"><part-name>Duplicate</part-name></score-part></part-list>',
                1,
            ),
            ImportCode.UNSUPPORTED_ELEMENT,
        ),
        (
            "mismatched-id",
            lambda xml: xml.replace('<part id="P1">', '<part id="P2">', 1),
            ImportCode.UNSUPPORTED_ELEMENT,
        ),
        (
            "empty-id",
            lambda xml: xml.replace('id="P1"', 'id=""'),
            ImportCode.UNSUPPORTED_ELEMENT,
        ),
    ],
)
def test_note_part_identity_is_validated_before_adapter_reconstruction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    mutate: Callable[[str], str],
    expected_code: ImportCode,
) -> None:
    import fretsure.importers.musicxml as musicxml_module

    adapter_calls = 0

    def adapter_spy(*_args: object, **_kwargs: object) -> None:
        nonlocal adapter_calls
        adapter_calls += 1

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_spy)
    base = _score(_valid_first())
    xml = mutate(base)
    result = import_musicxml(_write(tmp_path, xml, f"{case}.musicxml"))

    assert isinstance(result, ImportFailure)
    assert expected_code in {diagnostic.code for diagnostic in result.diagnostics}
    assert adapter_calls == 0


@pytest.mark.parametrize(
    ("case", "needle", "replacement", "expected_code", "expected_element"),
    [
        (
            "note-duration",
            "<duration>1</duration>",
            "<duration>1</duration><duration>999</duration>",
            ImportCode.UNSUPPORTED_NOTE,
            "note",
        ),
        (
            "note-voice",
            "<voice>1</voice>",
            "<voice>1</voice><voice>2</voice>",
            ImportCode.UNSUPPORTED_NOTE,
            "note",
        ),
        (
            "pitch-step",
            "<step>C</step>",
            "<step>C</step><step>D</step>",
            ImportCode.UNSUPPORTED_NOTE,
            "pitch",
        ),
        (
            "pitch-octave",
            "<octave>4</octave>",
            "<octave>4</octave><octave>5</octave>",
            ImportCode.UNSUPPORTED_NOTE,
            "pitch",
        ),
        (
            "pitch-alter",
            "<step>C</step>",
            "<step>C</step><alter>0</alter><alter>1</alter>",
            ImportCode.UNSUPPORTED_NOTE,
            "pitch",
        ),
        (
            "harmony-kind",
            "<kind>major</kind>",
            "<kind>major</kind><kind>minor</kind>",
            ImportCode.UNSUPPORTED_HARMONY_KIND,
            "harmony",
        ),
        (
            "harmony-root-step",
            "<root-step>C</root-step>",
            "<root-step>C</root-step><root-step>D</root-step>",
            ImportCode.UNSUPPORTED_HARMONY_KIND,
            "harmony",
        ),
        (
            "harmony-root-alter",
            "<root-step>C</root-step>",
            "<root-step>C</root-step><root-alter>0</root-alter><root-alter>1</root-alter>",
            ImportCode.UNSUPPORTED_HARMONY_KIND,
            "harmony",
        ),
        (
            "metronome-per-minute",
            "<per-minute>96</per-minute>",
            "<per-minute>96</per-minute><per-minute>500</per-minute>",
            ImportCode.UNSUPPORTED_TEMPO,
            "metronome",
        ),
        (
            "direction-offset",
            "</direction-type>",
            "</direction-type><offset>0</offset><offset>0</offset>",
            ImportCode.UNSUPPORTED_TEMPO,
            "offset",
        ),
        (
            "direction-sound",
            '<sound tempo="96"/>',
            '<sound tempo="96"/><sound tempo="96"/>',
            ImportCode.UNSUPPORTED_TEMPO,
            "sound",
        ),
        (
            "divisions",
            "<divisions>1</divisions>",
            "<divisions>1</divisions><divisions>2</divisions>",
            ImportCode.INVALID_DIVISIONS,
            "divisions",
        ),
        (
            "time",
            "<time><beats>4</beats><beat-type>4</beat-type></time>",
            (
                "<time><beats>4</beats><beat-type>4</beat-type></time>"
                "<time><beats>4</beats><beat-type>4</beat-type></time>"
            ),
            ImportCode.UNSUPPORTED_TIME_SIGNATURE,
            "time",
        ),
        (
            "staves",
            "<time><beats>4</beats>",
            "<staves>1</staves><staves>1</staves><time><beats>4</beats>",
            ImportCode.MULTIPLE_STAVES_UNSUPPORTED,
            "staves",
        ),
    ],
)
def test_repeated_authoritative_scalars_fail_before_music21(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    needle: str,
    replacement: str,
    expected_code: ImportCode,
    expected_element: str,
) -> None:
    import fretsure.importers.musicxml as musicxml_module

    adapter_calls = 0

    def adapter_spy(*_args: object, **_kwargs: object) -> None:
        nonlocal adapter_calls
        adapter_calls += 1

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_spy)
    xml = _score(_valid_first()).replace(needle, replacement, 1)
    result = import_musicxml(_write(tmp_path, xml, f"repeated-{case}.musicxml"))

    assert isinstance(result, ImportFailure)
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity is DiagnosticSeverity.ERROR
    ]
    matches = [diagnostic for diagnostic in errors if diagnostic.code is expected_code]
    assert matches, [diagnostic.code for diagnostic in errors]
    assert matches[0].location is not None
    assert matches[0].location.element == expected_element
    assert adapter_calls == 0


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


@pytest.mark.parametrize("fifths", [-7, 0, 7])
def test_traditional_key_without_mode_has_exact_loss_aware_golden(
    tmp_path: Path, fifths: int
) -> None:
    xml = _without_key_mode(_score(_valid_first()), fifths)

    result = import_musicxml(_write(tmp_path, xml, f"mode-unprovided-{fifths}.musicxml"))

    assert isinstance(result, ImportSuccess), getattr(result, "diagnostics", None)
    assert result.ir.meta.key == f"key-signature:fifths={fifths};mode=unprovided"
    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert warning.code is ImportCode.KEY_MODE_UNPROVIDED
    assert warning.severity is DiagnosticSeverity.WARNING
    assert warning.location is not None
    assert warning.location.part_id == "P1"
    assert warning.location.measure == "1"
    assert warning.location.voice is None
    assert warning.location.element == "key"
    assert warning.location.archive_member is None


@pytest.mark.parametrize("entrypoint", ["path", "bytes"])
@pytest.mark.parametrize("version", ["3.1", "4.0"])
def test_mode_omission_success_domain_is_musicxml_40_only(
    tmp_path: Path,
    entrypoint: str,
    version: str,
) -> None:
    xml = _without_key_mode(_score(_valid_first(), version=version), -2)
    raw = xml.encode()
    result = (
        import_musicxml(_write(tmp_path, xml, f"mode-unprovided-{version}.musicxml"))
        if entrypoint == "path"
        else import_musicxml_bytes(raw, f"mode-unprovided-{version}.musicxml")
    )

    if version == "4.0":
        assert isinstance(result, ImportSuccess), getattr(result, "diagnostics", None)
        assert result.ir.meta.key == "key-signature:fifths=-2;mode=unprovided"
        assert [warning.code for warning in result.warnings] == [ImportCode.KEY_MODE_UNPROVIDED]
    else:
        assert isinstance(result, ImportFailure)
        assert [
            diagnostic.code
            for diagnostic in result.diagnostics
            if diagnostic.severity is DiagnosticSeverity.ERROR
        ] == [ImportCode.UNSUPPORTED_KEY]
        assert not any(
            diagnostic.code is ImportCode.KEY_MODE_UNPROVIDED for diagnostic in result.diagnostics
        )


@pytest.mark.parametrize("version", ["3.1", "4.0"])
@pytest.mark.parametrize(
    ("mode", "expected_key"),
    [("major", "C"), ("minor", "Am")],
)
def test_explicit_major_minor_key_behavior_is_stable_across_versions(
    tmp_path: Path,
    version: str,
    mode: str,
    expected_key: str,
) -> None:
    xml = _score(_valid_first(), version=version).replace(
        "<mode>major</mode>", f"<mode>{mode}</mode>", 1
    )

    result = import_musicxml(_write(tmp_path, xml, f"explicit-{mode}-{version}.musicxml"))

    assert isinstance(result, ImportSuccess), getattr(result, "diagnostics", None)
    assert result.ir.meta.key == expected_key
    assert all(
        diagnostic.code is not ImportCode.KEY_MODE_UNPROVIDED for diagnostic in result.warnings
    )


@pytest.mark.parametrize(
    ("mode_xml", "expected_key", "expected_warning"),
    [
        ("<mode>major</mode>", "C", None),
        ("", "key-signature:fifths=0;mode=unprovided", ImportCode.KEY_MODE_UNPROVIDED),
    ],
    ids=["explicit-mode", "omitted-mode"],
)
def test_canonical_staff_one_key_number_is_supported(
    tmp_path: Path,
    mode_xml: str,
    expected_key: str,
    expected_warning: ImportCode | None,
) -> None:
    key = f'<key number="1"><fifths>0</fifths>{mode_xml}</key>'
    xml = _score(_valid_first()).replace("<key><fifths>0</fifths><mode>major</mode></key>", key, 1)

    result = import_musicxml(_write(tmp_path, xml, "staff-one-key.musicxml"))

    assert isinstance(result, ImportSuccess), getattr(result, "diagnostics", None)
    assert result.ir.meta.key == expected_key
    key_warnings = [
        diagnostic.code
        for diagnostic in result.warnings
        if diagnostic.code is ImportCode.KEY_MODE_UNPROVIDED
    ]
    assert key_warnings == ([] if expected_warning is None else [expected_warning])


@pytest.mark.parametrize(
    "number",
    ["2", "0", "-1", "1.5", "staff", "", "01", "+1", " 1 "],
    ids=[
        "second-staff",
        "zero",
        "negative",
        "decimal",
        "non-integer",
        "empty",
        "leading-zero",
        "explicit-plus",
        "whitespace",
    ],
)
@pytest.mark.parametrize(
    "mode_xml",
    ["<mode>major</mode>", ""],
    ids=["explicit-mode", "omitted-mode"],
)
def test_noncanonical_key_number_is_typed_before_music21(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    number: str,
    mode_xml: str,
) -> None:
    import fretsure.importers.musicxml as musicxml_module

    def adapter_must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("unsupported key staff number reached music21")

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_must_not_run)
    key = f'<key number="{number}"><fifths>0</fifths>{mode_xml}</key>'
    xml = _score(_valid_first()).replace("<key><fifths>0</fifths><mode>major</mode></key>", key, 1)

    result = import_musicxml(_write(tmp_path, xml, "unsupported-key-number.musicxml"))

    assert isinstance(result, ImportFailure)
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity is DiagnosticSeverity.ERROR
    ]
    assert [diagnostic.code for diagnostic in errors] == [ImportCode.UNSUPPORTED_KEY]
    assert errors[0].location is not None
    assert errors[0].location.part_id == "P1"
    assert errors[0].location.measure == "1"
    assert errors[0].location.element == "key"
    assert not any(
        diagnostic.code is ImportCode.KEY_MODE_UNPROVIDED for diagnostic in result.diagnostics
    )


def test_every_traditional_fifths_value_without_mode_is_a_snapshot_safe_success() -> None:
    """Exhaust the finite -7..7 property instead of sampling three spellings."""

    for fifths in range(-7, 8):
        raw = _without_key_mode(_score(_valid_first()), fifths).encode()
        result = import_musicxml_bytes(raw, f"fifths-{fifths}.xml")
        assert isinstance(result, ImportSuccess), getattr(result, "diagnostics", None)
        assert result.ir.meta.key == f"key-signature:fifths={fifths};mode=unprovided"
        assert snapshot_music_ir(result.ir) == result.ir


def test_unprovided_key_descriptor_is_independent_of_harmony_and_melody_content(
    tmp_path: Path,
) -> None:
    base = _without_key_mode(_score(_valid_first()), 2)
    variants = (
        base,
        base.replace("<root-step>C</root-step>", "<root-step>D</root-step>", 1),
        base.replace("<step>C</step><octave>4</octave>", "<step>B</step><octave>4</octave>", 1),
    )

    results = [
        import_musicxml(_write(tmp_path, xml, f"no-inference-{index}.musicxml"))
        for index, xml in enumerate(variants)
    ]

    assert all(isinstance(result, ImportSuccess) for result in results)
    successes = [result for result in results if isinstance(result, ImportSuccess)]
    assert {result.ir.meta.key for result in successes} == {
        "key-signature:fifths=2;mode=unprovided"
    }
    assert all(
        [warning.code for warning in result.warnings] == [ImportCode.KEY_MODE_UNPROVIDED]
        for result in successes
    )
    assert successes[0].ir.chords != successes[1].ir.chords
    assert successes[0].ir.notes != successes[2].ir.notes


@pytest.mark.parametrize(
    "mode_xml",
    [
        "<mode/>",
        "<mode> </mode>",
        "<mode>dorian</mode>",
        "<mode>none</mode>",
        "<mode>unprovided</mode>",
        "<mode>MAJOR</mode>",
        "<mode>Major</mode>",
        "<mode> major </mode>",
    ],
)
def test_empty_or_explicitly_unsupported_mode_remains_unsupported(
    tmp_path: Path, mode_xml: str
) -> None:
    xml = _score(_valid_first()).replace("<mode>major</mode>", mode_xml, 1)

    diagnostic = _diagnostic_for(
        _write(tmp_path, xml, "unsupported-mode.musicxml"), ImportCode.UNSUPPORTED_KEY
    )

    assert diagnostic.severity is DiagnosticSeverity.ERROR
    assert diagnostic.location is not None
    assert diagnostic.location.part_id == "P1"
    assert diagnostic.location.measure == "1"
    assert diagnostic.location.element == "key"


@pytest.mark.parametrize(
    "key_xml",
    [
        "<key><fifths>-8</fifths></key>",
        "<key><fifths>8</fifths></key>",
        "<key><fifths>0.0</fifths></key>",
        "<key><fifths>0_0</fifths></key>",
        "<key><fifths>٠</fifths></key>",
        "<key><fifths>not-an-integer</fifths></key>",
        "<key/>",
        "<key><key-step>C</key-step><key-alter>0</key-alter></key>",
    ],
)
def test_mode_omission_does_not_widen_the_traditional_fifths_contract(
    tmp_path: Path, key_xml: str
) -> None:
    xml = _score(_valid_first()).replace(
        "<key><fifths>0</fifths><mode>major</mode></key>", key_xml, 1
    )

    diagnostic = _diagnostic_for(
        _write(tmp_path, xml, "unsupported-key.musicxml"), ImportCode.UNSUPPORTED_KEY
    )

    assert diagnostic.severity is DiagnosticSeverity.ERROR
    assert diagnostic.location is not None
    assert diagnostic.location.part_id == "P1"
    assert diagnostic.location.measure == "1"
    assert diagnostic.location.element == "key"


@pytest.mark.parametrize(
    ("case", "needle", "replacement", "expected_code", "expected_element"),
    [
        (
            "underscored-fifths",
            "<fifths>0</fifths>",
            "<fifths>0_0</fifths>",
            ImportCode.UNSUPPORTED_KEY,
            "key",
        ),
        (
            "unicode-fifths",
            "<fifths>0</fifths>",
            "<fifths>٠</fifths>",
            ImportCode.UNSUPPORTED_KEY,
            "key",
        ),
        (
            "underscored-octave",
            "<octave>4</octave>",
            "<octave>0_4</octave>",
            ImportCode.UNSUPPORTED_NOTE,
            "pitch",
        ),
        (
            "unicode-octave",
            "<octave>4</octave>",
            "<octave>٤</octave>",
            ImportCode.UNSUPPORTED_NOTE,
            "pitch",
        ),
        (
            "non-xml-whitespace-octave",
            "<octave>4</octave>",
            "<octave>\u00a04\u00a0</octave>",
            ImportCode.UNSUPPORTED_NOTE,
            "pitch",
        ),
        (
            "underscored-staves",
            "<time><beats>4</beats>",
            "<staves>0_1</staves><time><beats>4</beats>",
            ImportCode.MULTIPLE_STAVES_UNSUPPORTED,
            "staves",
        ),
        (
            "unicode-staves",
            "<time><beats>4</beats>",
            "<staves>١</staves><time><beats>4</beats>",
            ImportCode.MULTIPLE_STAVES_UNSUPPORTED,
            "staves",
        ),
        (
            "oversized-fifths",
            "<fifths>0</fifths>",
            f"<fifths>{'1' * 129}</fifths>",
            ImportCode.INPUT_LIMIT_EXCEEDED,
            "fifths",
        ),
    ],
)
def test_small_integer_fields_use_bounded_ascii_xsd_lexing_before_music21(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    needle: str,
    replacement: str,
    expected_code: ImportCode,
    expected_element: str,
) -> None:
    import fretsure.importers.musicxml as musicxml_module

    adapter_calls = 0

    def adapter_spy(*_args: object, **_kwargs: object) -> None:
        nonlocal adapter_calls
        adapter_calls += 1

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_spy)
    xml = _score(_valid_first()).replace(needle, replacement, 1)
    result = import_musicxml(_write(tmp_path, xml, f"{case}.musicxml"))

    assert isinstance(result, ImportFailure)
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity is DiagnosticSeverity.ERROR
    ]
    assert [diagnostic.code for diagnostic in errors] == [expected_code]
    assert errors[0].location is not None
    assert errors[0].location.element == expected_element
    assert adapter_calls == 0


@pytest.mark.parametrize(
    ("case", "needle", "replacement", "expected_element"),
    [
        (
            "note-staff",
            "<voice>1</voice>",
            "<voice>1</voice><staff>0_1</staff>",
            "staff",
        ),
        (
            "direction-staff",
            "</direction>",
            "<staff>٢</staff></direction>",
            "staff",
        ),
        (
            "harmony-staff",
            "</harmony>",
            "<staff>2</staff></harmony>",
            "staff",
        ),
        (
            "time-number",
            "<time>",
            '<time number="2">',
            "time",
        ),
        (
            "clef-number",
            "<time>",
            '<clef number="2"><sign>G</sign><line>2</line></clef><time>',
            "clef",
        ),
        (
            "print-staff-layout",
            '<measure number="1">',
            '<measure number="1"><print><staff-layout number="two"/></print>',
            "staff-layout",
        ),
    ],
)
def test_single_staff_selectors_fail_before_music21(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    needle: str,
    replacement: str,
    expected_element: str,
) -> None:
    import fretsure.importers.musicxml as musicxml_module

    adapter_calls = 0

    def adapter_spy(*_args: object, **_kwargs: object) -> None:
        nonlocal adapter_calls
        adapter_calls += 1

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_spy)
    xml = _score(_valid_first()).replace(needle, replacement, 1)
    result = import_musicxml(_write(tmp_path, xml, f"{case}.musicxml"))

    assert isinstance(result, ImportFailure)
    matches = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code is ImportCode.MULTIPLE_STAVES_UNSUPPORTED
        and diagnostic.location is not None
        and diagnostic.location.element == expected_element
    ]
    assert matches, result.diagnostics
    assert adapter_calls == 0


@pytest.mark.parametrize("version", ["3.1", "4.0"])
@pytest.mark.parametrize(
    "replacement",
    [
        "<key><fifths>0</fifths><fifths>0</fifths></key>",
        "<key><fifths>0</fifths><fifths>1</fifths></key>",
        "<key><fifths>0</fifths><mode>major</mode><mode>major</mode></key>",
        "<key><fifths>0</fifths><mode>major</mode><mode>minor</mode></key>",
    ],
    ids=[
        "identical-fifths",
        "conflicting-fifths",
        "identical-mode",
        "conflicting-mode",
    ],
)
def test_duplicate_key_scalar_is_typed_before_music21(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
    version: str,
) -> None:
    import fretsure.importers.musicxml as musicxml_module

    def adapter_must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("ambiguous key shape reached music21")

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_must_not_run)
    xml = _score(_valid_first(), version=version).replace(
        "<key><fifths>0</fifths><mode>major</mode></key>", replacement, 1
    )
    result = import_musicxml(_write(tmp_path, xml, "duplicate-key-scalar.musicxml"))

    assert isinstance(result, ImportFailure)
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity is DiagnosticSeverity.ERROR
    ]
    assert [diagnostic.code for diagnostic in errors] == [ImportCode.UNSUPPORTED_KEY]
    assert errors[0].location is not None
    assert errors[0].location.part_id == "P1"
    assert errors[0].location.measure == "1"
    assert errors[0].location.element == "key"


@pytest.mark.parametrize("entrypoint", ["path", "bytes"])
@pytest.mark.parametrize(
    ("attribute", "extra_child"),
    [
        (' evil="x"', ""),
        ("", "<duration>1</duration>"),
        ("", "<time><beats>4</beats><beat-type>4</beat-type></time>"),
        ("", "<cancel>0</cancel>"),
        ("", "evil"),
    ],
    ids=[
        "unknown-attribute",
        "duration-child",
        "time-child",
        "cancel-child",
        "mixed-character-data",
    ],
)
@pytest.mark.parametrize("mode_xml", ["<mode>major</mode>", ""])
def test_traditional_key_rejects_unknown_attributes_and_extra_direct_children(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
    attribute: str,
    extra_child: str,
    mode_xml: str,
) -> None:
    import fretsure.importers.musicxml as musicxml_module

    adapter_calls = 0

    def adapter_spy(*_args: object, **_kwargs: object) -> None:
        nonlocal adapter_calls
        adapter_calls += 1

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_spy)
    key = f"<key{attribute}><fifths>0</fifths>{mode_xml}{extra_child}</key>"
    xml = _score(_valid_first()).replace(
        "<key><fifths>0</fifths><mode>major</mode></key>",
        key,
        1,
    )
    raw = xml.encode()
    result = (
        import_musicxml(_write(tmp_path, xml, "invalid-key-shape.musicxml"))
        if entrypoint == "path"
        else import_musicxml_bytes(raw, "invalid-key-shape.musicxml")
    )

    assert isinstance(result, ImportFailure)
    assert ImportCode.UNSUPPORTED_KEY in {error.code for error in result.diagnostics}
    assert ImportCode.KEY_MODE_UNPROVIDED not in {
        diagnostic.code for diagnostic in result.diagnostics
    }
    assert adapter_calls == 0


@pytest.mark.parametrize("entrypoint", ["path", "bytes"])
@pytest.mark.parametrize(
    "key",
    [
        "<key>evil<fifths>0</fifths><mode>major</mode></key>",
        "<key>evil<fifths>0</fifths></key>",
        "<key><mode>major</mode><fifths>0</fifths></key>",
        '<key><fifths print-object="no">0</fifths><mode>major</mode></key>',
        '<key><fifths print-object="no">0</fifths></key>',
        "<key><fifths>0<duration>1</duration></fifths><mode>major</mode></key>",
        "<key><fifths>0<duration>1</duration></fifths></key>",
    ],
    ids=[
        "explicit-leading-text",
        "omitted-leading-text",
        "mode-before-fifths",
        "explicit-attributed-fifths",
        "omitted-attributed-fifths",
        "explicit-nested-fifths",
        "omitted-nested-fifths",
    ],
)
def test_traditional_key_rejects_non_element_only_or_out_of_order_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
    key: str,
) -> None:
    import fretsure.importers.musicxml as musicxml_module

    adapter_calls = 0

    def adapter_spy(*_args: object, **_kwargs: object) -> None:
        nonlocal adapter_calls
        adapter_calls += 1

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_spy)
    xml = _score(_valid_first()).replace(
        "<key><fifths>0</fifths><mode>major</mode></key>",
        key,
        1,
    )
    raw = xml.encode()
    result = (
        import_musicxml(_write(tmp_path, xml, "invalid-key-content.musicxml"))
        if entrypoint == "path"
        else import_musicxml_bytes(raw, "invalid-key-content.musicxml")
    )

    assert isinstance(result, ImportFailure)
    assert ImportCode.UNSUPPORTED_KEY in {error.code for error in result.diagnostics}
    assert adapter_calls == 0


def test_traditional_key_keeps_supported_visual_attributes(tmp_path: Path) -> None:
    key = (
        '<key number="1" print-object="no" default-x="1" default-y="2" '
        'relative-x="3" relative-y="4" font-family="serif" font-size="10" '
        'font-style="normal" font-weight="normal" color="#000000" '
        'id="key-1">'
        "<fifths>0</fifths></key>"
    )
    xml = _score(_valid_first()).replace(
        "<key><fifths>0</fifths><mode>major</mode></key>",
        key,
        1,
    )

    result = import_musicxml(_write(tmp_path, xml, "visual-key-attributes.musicxml"))

    assert isinstance(result, ImportSuccess), getattr(result, "diagnostics", None)
    assert result.ir.meta.key == "key-signature:fifths=0;mode=unprovided"
    assert [warning.code for warning in result.warnings] == [
        ImportCode.KEY_MODE_UNPROVIDED
    ]


@pytest.mark.parametrize("version", ["3.1", "4.0"])
@pytest.mark.parametrize("split_attributes", [False, True])
def test_duplicate_key_in_one_measure_is_typed_before_music21(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    split_attributes: bool,
    version: str,
) -> None:
    import fretsure.importers.musicxml as musicxml_module

    def adapter_must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("duplicate measure key reached music21")

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_must_not_run)
    duplicate = "<key><fifths>0</fifths><mode>major</mode></key>"
    insertion = (
        f"</attributes><attributes>{duplicate}</attributes>"
        if split_attributes
        else f"{duplicate}</attributes>"
    )
    xml = _score(_valid_first(), version=version).replace("</attributes>", insertion, 1)
    result = import_musicxml(_write(tmp_path, xml, "duplicate-measure-key.musicxml"))

    assert isinstance(result, ImportFailure)
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity is DiagnosticSeverity.ERROR
    ]
    assert [diagnostic.code for diagnostic in errors] == [ImportCode.UNSUPPORTED_KEY]
    assert errors[0].location is not None
    assert errors[0].location.part_id == "P1"
    assert errors[0].location.measure == "1"
    assert errors[0].location.element == "key"


@pytest.mark.parametrize("version", ["3.1", "4.0"])
@pytest.mark.parametrize(
    "replacement",
    [
        (
            "<key><fifths>0</fifths><mode>major</mode></key>"
            "<direction><key><fifths>1</fifths><mode>minor</mode></key></direction>"
        ),
        (
            "<key><fifths>0</fifths><mode>major</mode>"
            "<cancel><fifths>1</fifths></cancel></key>"
        ),
        (
            "<key><fifths>0</fifths><mode>major</mode>"
            "<cancel><mode>minor</mode></cancel></key>"
        ),
        (
            "<key><fifths>0</fifths><mode>major</mode>"
            "<cancel><key-step>D</key-step><key-alter>1</key-alter></cancel></key>"
        ),
    ],
    ids=["nested-key", "nested-fifths", "nested-mode", "nested-nontraditional-key"],
)
def test_nested_key_semantics_fail_before_music21(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
    version: str,
) -> None:
    import fretsure.importers.musicxml as musicxml_module

    adapter_calls = 0

    def adapter_spy(*_args: object, **_kwargs: object) -> None:
        nonlocal adapter_calls
        adapter_calls += 1

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_spy)
    xml = _score(_valid_first(), version=version).replace(
        "<key><fifths>0</fifths><mode>major</mode></key>", replacement, 1
    )
    result = import_musicxml(_write(tmp_path, xml, "nested-key-semantics.musicxml"))

    assert isinstance(result, ImportFailure)
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity is DiagnosticSeverity.ERROR
    ]
    assert ImportCode.UNSUPPORTED_KEY in {diagnostic.code for diagnostic in errors}
    assert adapter_calls == 0


@pytest.mark.parametrize(
    ("first_key", "second_key"),
    [
        (
            "<key><fifths>0</fifths></key>",
            "<key><fifths>0</fifths><mode>major</mode></key>",
        ),
        (
            "<key><fifths>0</fifths><mode>major</mode></key>",
            "<key><fifths>0</fifths></key>",
        ),
        (
            "<key><fifths>0</fifths></key>",
            "<key><fifths>1</fifths></key>",
        ),
    ],
)
def test_key_identity_transition_with_unprovided_mode_remains_unsupported(
    tmp_path: Path, first_key: str, second_key: str
) -> None:
    first = _valid_first().replace("<key><fifths>0</fifths><mode>major</mode></key>", first_key, 1)
    second = _measure("2", attributes=f"<attributes>{second_key}</attributes>")

    diagnostic = _diagnostic_for(
        _write(tmp_path, _score(first, second_measure=second), "key-transition.musicxml"),
        ImportCode.KEY_CHANGE_UNSUPPORTED,
    )

    assert diagnostic.location is not None
    assert diagnostic.location.part_id == "P1"
    assert diagnostic.location.measure == "2"
    assert diagnostic.location.element == "key"


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
def test_sounding_or_unknown_note_attributes_fail_closed(tmp_path: Path, attribute: str) -> None:
    xml = _score(_valid_first().replace("<note>", f"<note {attribute}>", 1))

    diagnostic = _diagnostic_for(
        _write(tmp_path, xml, "note-attribute.musicxml"),
        ImportCode.PERFORMANCE_NOTATION_UNSUPPORTED,
    )

    assert diagnostic.location is not None
    assert diagnostic.location.element == "note"


@pytest.mark.parametrize("harmony_type", ["alternate", "future-analysis"])
def test_alternate_or_unknown_harmony_type_fails_closed(tmp_path: Path, harmony_type: str) -> None:
    xml = _score(_valid_first().replace("<harmony>", f'<harmony type="{harmony_type}">', 1))

    diagnostic = _diagnostic_for(
        _write(tmp_path, xml, "harmony-type.musicxml"),
        ImportCode.UNSUPPORTED_HARMONY_TYPE,
    )

    assert diagnostic.location is not None
    assert diagnostic.location.element == "harmony"


@pytest.mark.parametrize("harmony_type", ["explicit", "implied"])
def test_supported_harmony_types_remain_source_chords(tmp_path: Path, harmony_type: str) -> None:
    xml = _score(_valid_first().replace("<harmony>", f'<harmony type="{harmony_type}">', 1))

    result = import_musicxml(_write(tmp_path, xml, f"{harmony_type}-harmony.musicxml"))

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
    attributes = 'default-x="10" color="#000000" print-object="yes" dynamics="70" end-dynamics="60"'
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


@pytest.mark.parametrize("entrypoint", ["path", "bytes"])
def test_decimal_lexing_does_not_trim_non_xml_unicode_whitespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
) -> None:
    import fretsure.importers.musicxml as musicxml_module

    adapter_calls = 0

    def adapter_spy(*_args: object, **_kwargs: object) -> None:
        nonlocal adapter_calls
        adapter_calls += 1

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_spy)
    xml = _score(_valid_first()).replace(
        "<divisions>1</divisions>",
        "<divisions>\u00a01.0\u00a0</divisions>",
        1,
    )
    result = (
        import_musicxml(_write(tmp_path, xml, "unicode-space-decimal.musicxml"))
        if entrypoint == "path"
        else import_musicxml_bytes(xml.encode(), "unicode-space-decimal.musicxml")
    )

    assert isinstance(result, ImportFailure)
    assert ImportCode.INVALID_DIVISIONS in {
        diagnostic.code for diagnostic in result.diagnostics
    }
    assert adapter_calls == 0


@pytest.mark.parametrize("value", ["1/2", "1e-3", "1_0.0", "NaN", "Infinity"])
def test_divisions_rejects_non_xsd_decimal_lexical_forms(tmp_path: Path, value: str) -> None:
    xml = _score(
        _valid_first().replace("<divisions>1</divisions>", f"<divisions>{value}</divisions>")
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
        _valid_first().replace("<duration>1</duration>", "<duration>0.123456789</duration>", 1)
    )

    result = import_musicxml(
        _write(tmp_path, xml, "long-decimal.musicxml"),
        limits=ImportLimits(max_decimal_chars=8),
    )

    assert isinstance(result, ImportFailure)
    assert ImportCode.INPUT_LIMIT_EXCEEDED in {diagnostic.code for diagnostic in result.diagnostics}


def _score_with_integer_timeline_scale(value: int) -> str:
    token = str(value)
    return (
        _score(_valid_first())
        .replace("<divisions>1</divisions>", f"<divisions>{token}</divisions>", 1)
        .replace("<duration>1</duration>", f"<duration>{token}</duration>")
    )


def _one_plus_reciprocal_power_of_two_decimal(scale: int) -> str:
    return "1." + str(5**scale).zfill(scale)


def _score_with_one_plus_reciprocal_timeline(scale: int) -> tuple[str, str]:
    token = _one_plus_reciprocal_power_of_two_decimal(scale)
    xml = (
        _score(_valid_first())
        .replace("<divisions>1</divisions>", f"<divisions>{token}</divisions>", 1)
        .replace("<duration>1</duration>", f"<duration>{token}</duration>")
    )
    return xml, token


def test_256_bit_decimal_components_are_accepted_at_the_ir_boundary(
    tmp_path: Path,
) -> None:
    boundary = 1 << 255
    result = import_musicxml(
        _write(
            tmp_path,
            _score_with_integer_timeline_scale(boundary),
            "decimal-256-bit.musicxml",
        )
    )

    assert boundary.bit_length() == 256
    assert isinstance(result, ImportSuccess), getattr(result, "diagnostics", None)
    assert [note.duration for note in result.ir.notes] == [Fraction(1)] * 4
    assert snapshot_music_ir(result.ir) == result.ir


def test_256_bit_decimal_denominator_is_accepted_at_the_ir_boundary(
    tmp_path: Path,
) -> None:
    scale = 255
    xml, token = _score_with_one_plus_reciprocal_timeline(scale)
    exact = Fraction(token)

    result = import_musicxml(
        _write(tmp_path, xml, "decimal-denominator-256-bit.musicxml"),
        limits=ImportLimits(max_decimal_chars=len(token)),
    )

    assert exact == 1 + Fraction(1, 1 << scale)
    assert exact.denominator.bit_length() == 256
    assert isinstance(result, ImportSuccess), getattr(result, "diagnostics", None)
    assert [note.duration for note in result.ir.notes] == [Fraction(1)] * 4
    assert snapshot_music_ir(result.ir) == result.ir


def test_257_bit_decimal_denominator_fails_before_music21(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fretsure.importers.musicxml as musicxml_module

    scale = 256
    xml, token = _score_with_one_plus_reciprocal_timeline(scale)
    exact = Fraction(token)
    adapter_calls = 0

    def adapter_spy(*_args: object, **_kwargs: object) -> None:
        nonlocal adapter_calls
        adapter_calls += 1

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_spy)
    result = import_musicxml(
        _write(tmp_path, xml, "decimal-denominator-257-bit.musicxml"),
        limits=ImportLimits(max_decimal_chars=len(token)),
    )

    assert exact == 1 + Fraction(1, 1 << scale)
    assert exact.denominator.bit_length() == 257
    assert isinstance(result, ImportFailure)
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity is DiagnosticSeverity.ERROR
    ]
    assert [diagnostic.code for diagnostic in errors] == [ImportCode.INPUT_LIMIT_EXCEEDED]
    assert errors[0].location is not None
    assert errors[0].location.element == "divisions"
    assert adapter_calls == 0


@pytest.mark.parametrize("max_decimal_chars", [128, 512])
@pytest.mark.parametrize("component_bits", [257, 333])
def test_decimal_components_beyond_ir_envelope_fail_before_music21(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    component_bits: int,
    max_decimal_chars: int,
) -> None:
    import fretsure.importers.musicxml as musicxml_module

    value = 1 << (component_bits - 1)
    assert value.bit_length() == component_bits

    def adapter_must_not_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("out-of-envelope decimal reached music21")

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_must_not_run)
    result = import_musicxml(
        _write(
            tmp_path,
            _score_with_integer_timeline_scale(value),
            f"decimal-{component_bits}-bit.musicxml",
        ),
        limits=ImportLimits(max_decimal_chars=max_decimal_chars),
    )

    assert isinstance(result, ImportFailure)
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity is DiagnosticSeverity.ERROR
    ]
    assert [diagnostic.code for diagnostic in errors] == [ImportCode.INPUT_LIMIT_EXCEEDED]
    assert errors[0].location is not None
    assert errors[0].location.element == "divisions"


def test_old_fraction_boundary_exploit_fails_before_music21(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regress the exact unequal-duration input that escaped importer 0.2."""

    import fretsure.importers.musicxml as musicxml_module

    divisions = 1 << 332
    durations = (1, divisions, divisions, 2 * divisions - 1)
    assert divisions.bit_length() == 333
    assert len(str(divisions)) < 128
    assert sum(durations) == 4 * divisions
    xml = _score(_valid_first()).replace(
        "<divisions>1</divisions>",
        f"<divisions>{divisions}</divisions>",
        1,
    )
    for duration in durations:
        xml = xml.replace(
            "<duration>1</duration>",
            f"<duration>{duration}</duration>",
            1,
        )
    adapter_calls = 0

    def adapter_spy(*_args: object, **_kwargs: object) -> None:
        nonlocal adapter_calls
        adapter_calls += 1

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_spy)
    result = import_musicxml(
        _write(tmp_path, xml, "old-fraction-boundary-exploit.musicxml")
    )

    assert isinstance(result, ImportFailure)
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity is DiagnosticSeverity.ERROR
    ]
    assert [diagnostic.code for diagnostic in errors] == [ImportCode.INPUT_LIMIT_EXCEEDED]
    assert errors[0].location is not None
    assert errors[0].location.element == "divisions"
    assert adapter_calls == 0


def test_derived_note_onset_beyond_ir_envelope_fails_before_music21(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound cursor arithmetic, not only each source decimal token."""

    import fretsure.importers.musicxml as musicxml_module

    divisions = 1 << 255
    durations = (2 * divisions - 1, 2 * divisions - 1, 1, 1)
    assert all(value.bit_length() <= 256 for value in (divisions, *durations))
    assert sum(durations) == 4 * divisions
    notes = "".join(_note(duration=str(duration)) for duration in durations)
    xml = _score(_valid_first(notes=notes)).replace(
        "<divisions>1</divisions>",
        f"<divisions>{divisions}</divisions>",
        1,
    )
    adapter_calls = 0

    def adapter_spy(*_args: object, **_kwargs: object) -> None:
        nonlocal adapter_calls
        adapter_calls += 1

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_spy)
    result = import_musicxml(_write(tmp_path, xml, "derived-onset-overflow.musicxml"))

    assert isinstance(result, ImportFailure)
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity is DiagnosticSeverity.ERROR
    ]
    assert [diagnostic.code for diagnostic in errors] == [ImportCode.INPUT_LIMIT_EXCEEDED]
    assert errors[0].location is not None
    assert errors[0].location.element == "note[3].onset"
    assert adapter_calls == 0


def test_derived_tied_duration_beyond_ir_envelope_fails_before_music21(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound the coalesced duration the adapter would place in one IR Note."""

    import fretsure.importers.musicxml as musicxml_module

    divisions = 1 << 255
    long_segment = 2 * divisions - 1
    start = '<tie type="start"/><notations><tied type="start"/></notations>'
    continuation = (
        '<tie type="stop"/><tie type="start"/>'
        '<notations><tied type="stop"/><tied type="start"/></notations>'
    )
    stop = '<tie type="stop"/><notations><tied type="stop"/></notations>'
    notes = (
        _note(duration=str(long_segment), extra=start)
        + _note(duration=str(long_segment), extra=continuation)
        + _note(duration="1", extra=stop)
        + _note(pitch="<rest/>", duration="1")
    )
    xml = _score(_valid_first(notes=notes)).replace(
        "<divisions>1</divisions>",
        f"<divisions>{divisions}</divisions>",
        1,
    )
    merged = Fraction(4 * divisions - 1, divisions)
    assert merged.numerator.bit_length() == 257
    adapter_calls = 0

    def adapter_spy(*_args: object, **_kwargs: object) -> None:
        nonlocal adapter_calls
        adapter_calls += 1

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_spy)
    result = import_musicxml(_write(tmp_path, xml, "derived-tie-overflow.musicxml"))

    assert isinstance(result, ImportFailure)
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity is DiagnosticSeverity.ERROR
    ]
    assert [diagnostic.code for diagnostic in errors] == [ImportCode.INPUT_LIMIT_EXCEEDED]
    assert errors[0].location is not None
    assert errors[0].location.element == "note[2].tied_duration"
    assert adapter_calls == 0


def test_derived_harmony_onset_beyond_ir_envelope_fails_before_music21(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bound the exact onset the adapter would place in an IR ChordSymbol."""

    import fretsure.importers.musicxml as musicxml_module

    divisions = 1 << 255
    long_segment = 2 * divisions - 1
    timed_content = (
        _note(duration=str(long_segment))
        + _note(duration=str(long_segment))
        + _note(duration="1")
        + _harmony()
        + _note(pitch="<rest/>", duration="1")
    )
    first = _measure(
        "1",
        attributes=_attributes(divisions=str(divisions)),
        prefix=_tempo(),
        notes=timed_content,
    )
    xml = _score(first)
    chord_onset = Fraction(4 * divisions - 1, divisions)
    assert chord_onset.numerator.bit_length() == 257
    adapter_calls = 0

    def adapter_spy(*_args: object, **_kwargs: object) -> None:
        nonlocal adapter_calls
        adapter_calls += 1

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_spy)
    result = import_musicxml(_write(tmp_path, xml, "derived-chord-overflow.musicxml"))

    assert isinstance(result, ImportFailure)
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity is DiagnosticSeverity.ERROR
    ]
    assert [diagnostic.code for diagnostic in errors] == [ImportCode.INPUT_LIMIT_EXCEEDED]
    assert errors[0].location is not None
    assert errors[0].location.element == "chord[0].onset"
    assert adapter_calls == 0


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


@pytest.mark.parametrize(
    ("case", "expected_element"),
    [
        ("part-id", "score-part@id"),
        ("measure-number", "measure@number"),
        ("voice", "voice"),
        ("element-name", "element-name"),
    ],
)
def test_location_scalar_limit_stops_repeated_location_amplification_before_music21(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    expected_element: str,
) -> None:
    import fretsure.importers.musicxml as musicxml_module

    oversized = "P" * 1025
    annotations = "<lyric><text>x</text></lyric>" * 100
    xml = _score(_valid_first())
    if case == "part-id":
        xml = xml.replace('id="P1"', f'id="{oversized}"')
    elif case == "measure-number":
        xml = xml.replace('number="1"', f'number="{oversized}"', 1)
    elif case == "voice":
        xml = xml.replace("<voice>1</voice>", f"<voice>{oversized}</voice>", 1)
    else:
        xml = xml.replace('<measure number="1">', f'<measure number="1"><{oversized}/>', 1)
    xml = xml.replace("</note>", annotations + "</note>", 1)
    adapter_calls = 0

    def adapter_spy(*_args: object, **_kwargs: object) -> None:
        nonlocal adapter_calls
        adapter_calls += 1

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_spy)
    result = import_musicxml(_write(tmp_path, xml, "oversized-location.musicxml"))

    assert isinstance(result, ImportFailure)
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity is DiagnosticSeverity.ERROR
    ]
    assert [diagnostic.code for diagnostic in errors] == [ImportCode.INPUT_LIMIT_EXCEEDED]
    assert errors[0].location is not None
    assert errors[0].location.element == expected_element
    assert adapter_calls == 0


def test_warning_flood_becomes_bounded_typed_failure_before_music21(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fretsure.importers.musicxml as musicxml_module

    annotations = "<lyric><text>x</text></lyric>" * 300
    xml = _score(_valid_first()).replace("</note>", annotations + "</note>", 1)
    adapter_calls = 0

    def adapter_spy(*_args: object, **_kwargs: object) -> None:
        nonlocal adapter_calls
        adapter_calls += 1

    monkeypatch.setattr(musicxml_module, "music21_to_ir", adapter_spy)
    result = import_musicxml(_write(tmp_path, xml, "warning-flood.musicxml"))

    assert isinstance(result, ImportFailure)
    assert len(result.diagnostics) == 257
    assert result.diagnostics[-1].code is ImportCode.INPUT_LIMIT_EXCEEDED
    assert result.diagnostics[-1].location is not None
    assert result.diagnostics[-1].location.element == "diagnostics"
    assert adapter_calls == 0


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
        _valid_first().replace("<beat-type>4</beat-type>", f"<beat-type>{huge}</beat-type>")
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
