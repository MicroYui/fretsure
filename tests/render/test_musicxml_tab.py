from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from fractions import Fraction as F

import pytest

from fretsure.geometry import STANDARD_TUNING
from fretsure.render.musicxml_tab import (
    MAX_MUSICXML_DIVISIONS,
    MAX_MUSICXML_MEASURES,
    MUSICXML_TAB_EXPORT_VERSION,
    MusicXMLTabExportCode,
    MusicXMLTabExportError,
    render_musicxml_tab,
)
from fretsure.tab import Tab, TabNote


def _pitch(note: ET.Element) -> int:
    steps = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
    pitch = note.find("pitch")
    assert pitch is not None
    step = pitch.findtext("step")
    octave = pitch.findtext("octave")
    assert step in steps
    assert octave is not None
    return 12 * (int(octave) + 1) + steps[step] + int(pitch.findtext("alter", "0"))


def _round_trip_notes(xml: bytes) -> list[tuple[F, F, int, int, int, int, str]]:
    """Decode the exact subset emitted by render_musicxml_tab."""

    root = ET.fromstring(xml)
    part = root.find("part")
    assert part is not None
    first = part.find("measure")
    assert first is not None
    divisions = int(first.findtext("attributes/divisions", "0"))
    beats_per_bar = int(first.findtext("attributes/time/beats", "0"))
    assert divisions > 0
    assert beats_per_bar > 0

    segments: list[tuple[int, F, F, int, int, int, int, str, bool, bool]] = []
    for measure_index, measure in enumerate(part.findall("measure")):
        cursor = 0
        last_onset: dict[int, F] = {}
        for child in measure:
            if child.tag == "backup":
                cursor -= int(child.findtext("duration", "0"))
                continue
            if child.tag != "note":
                continue
            duration = int(child.findtext("duration", "0"))
            voice = int(child.findtext("voice", "0"))
            if child.find("chord") is None:
                onset = F(measure_index * beats_per_bar) + F(cursor, divisions)
                last_onset[voice] = onset
                cursor += duration
            else:
                onset = last_onset[voice]
            if child.find("pitch") is None:
                continue

            technical = child.find("notations/technical")
            assert technical is not None
            fret = int(technical.findtext("fret", "-1"))
            string = int(technical.findtext("string", "0"))
            finger_text = technical.findtext("fingering")
            left_finger = 0 if finger_text is None else int(finger_text)
            if left_finger == 0:
                assert technical.find("open-string") is not None
            pluck = technical.findtext("pluck")
            assert pluck is not None
            tie_types = {tie.attrib["type"] for tie in child.findall("tie")}
            segments.append(
                (
                    voice,
                    onset,
                    F(duration, divisions),
                    _pitch(child),
                    string,
                    fret,
                    left_finger,
                    pluck,
                    "stop" in tie_types,
                    "start" in tie_types,
                )
            )
        assert cursor == beats_per_bar * divisions

    result: list[tuple[F, F, int, int, int, int, str]] = []
    active: dict[tuple[int, int, int, int, int, str], tuple[F, F]] = {}
    for voice, onset, duration, pitch, string, fret, left, right, stop, start in segments:
        key = voice, pitch, string, fret, left, right
        if stop:
            original_onset, accumulated = active.pop(key)
            assert original_onset + accumulated == onset
            accumulated += duration
        else:
            original_onset, accumulated = onset, duration
        if start:
            active[key] = original_onset, accumulated
        else:
            result.append(
                (original_onset, accumulated, pitch, string, fret, left, right)
            )
    assert not active
    return sorted(result)


def _expected(tab: Tab) -> list[tuple[F, F, int, int, int, int, str]]:
    return sorted(
        (
            note.onset,
            note.duration,
            tab.tuning[note.string] + tab.capo + note.fret,
            6 - note.string,
            note.fret,
            note.left_finger,
            note.right_finger,
        )
        for note in tab.notes
    )


def test_musicxml_has_standard_tab_staff_tempo_meter_and_fingering() -> None:
    tab = Tab(
        (
            TabNote(F(1, 3), F(2, 3), 5, 3, 3, "a"),
            TabNote(F(1, 3), F(2, 3), 1, 2, 2, "i"),
            TabNote(F(2), F(1), 0, 0, 0, "p"),
        ),
        STANDARD_TUNING,
        2,
    )

    first = render_musicxml_tab(tab, tempo_bpm=72.5, beats_per_bar=3)
    second = render_musicxml_tab(tab, tempo_bpm=72.5, beats_per_bar=3)

    assert first == second
    assert first.startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n')
    assert b"MusicXML 4.0 Partwise" in first
    root = ET.fromstring(first)
    assert root.tag == "score-partwise"
    assert root.attrib == {"version": "4.0"}
    assert root.findtext("identification/encoding/software") == (
        f"Fretsure {MUSICXML_TAB_EXPORT_VERSION}"
    )
    assert root.findtext("part-list/score-part/part-name") == "Guitar"

    measure = root.find("part/measure")
    assert measure is not None
    assert measure.findtext("attributes/divisions") == "3"
    assert measure.findtext("attributes/time/beats") == "3"
    assert measure.findtext("attributes/time/beat-type") == "4"
    assert measure.findtext("attributes/clef/sign") == "TAB"
    assert measure.findtext("attributes/clef/line") == "5"
    assert measure.findtext("attributes/staff-details/staff-lines") == "6"
    assert measure.findtext("attributes/staff-details/capo") == "2"
    tunings = measure.findall("attributes/staff-details/staff-tuning")
    assert [item.attrib["line"] for item in tunings] == ["1", "2", "3", "4", "5", "6"]
    assert [item.findtext("tuning-step") for item in tunings] == [
        "E",
        "A",
        "D",
        "G",
        "B",
        "E",
    ]
    assert [item.findtext("tuning-octave") for item in tunings] == [
        "2",
        "2",
        "3",
        "3",
        "3",
        "4",
    ]
    assert measure.findtext("direction/direction-type/metronome/per-minute") == "72.5"
    assert measure.find("direction/sound").attrib["tempo"] == "72.5"  # type: ignore[union-attr]

    pitched = [note for note in measure.findall("note") if note.find("pitch") is not None]
    assert len(pitched) == 3
    assert sum(note.find("chord") is not None for note in pitched) == 1
    technical = pitched[0].find("notations/technical")
    assert technical is not None
    assert technical.findtext("fingering") == "2"
    assert technical.findtext("pluck") == "i"
    assert technical.findtext("string") == "5"
    assert technical.findtext("fret") == "2"
    assert any(note.find("rest") is not None for note in measure.findall("note"))


def test_musicxml_round_trips_chords_overlaps_ties_rests_and_fractions() -> None:
    tab = Tab(
        (
            TabNote(F(0), F(5), 0, 3, 1, "p"),
            TabNote(F(0), F(5), 2, 2, 2, "i"),
            TabNote(F(1), F(1, 2), 5, 0, 0, "a"),
            TabNote(F(13, 3), F(2, 3), 4, 1, 1, "m"),
        ),
        STANDARD_TUNING,
        0,
    )

    xml = render_musicxml_tab(tab)
    root = ET.fromstring(xml)

    assert len(root.findall("part/measure")) == 2
    assert root.findtext("part/measure/attributes/divisions") == "6"
    assert root.findall(".//backup")
    assert root.findall(".//tie[@type='start']")
    assert root.findall(".//tie[@type='stop']")
    assert root.findall(".//tied[@type='start']")
    assert root.findall(".//tied[@type='stop']")
    assert _round_trip_notes(xml) == _expected(tab)


@pytest.mark.parametrize(
    ("tab", "code"),
    [
        (Tab((), STANDARD_TUNING, 0), MusicXMLTabExportCode.INVALID_TAB),
        (
            Tab(
                (
                    TabNote(
                        F(0),
                        F(1, MAX_MUSICXML_DIVISIONS + 1),
                        0,
                        0,
                        0,
                        "p",
                    ),
                ),
                STANDARD_TUNING,
                0,
            ),
            MusicXMLTabExportCode.RHYTHM_UNREPRESENTABLE,
        ),
        (
            Tab(
                (
                    TabNote(
                        F(4 * MAX_MUSICXML_MEASURES),
                        F(1),
                        0,
                        0,
                        0,
                        "p",
                    ),
                ),
                STANDARD_TUNING,
                0,
            ),
            MusicXMLTabExportCode.TIMELINE_UNREPRESENTABLE,
        ),
    ],
)
def test_musicxml_rejects_non_lossless_or_out_of_bound_inputs(
    tab: Tab,
    code: MusicXMLTabExportCode,
) -> None:
    with pytest.raises(MusicXMLTabExportError) as caught:
        render_musicxml_tab(tab)
    assert caught.value.code is code


def test_musicxml_assigns_the_minimum_deterministic_voice_count() -> None:
    tab = Tab(
        (
            TabNote(F(0), F(2), 0, 0, 0, "p"),
            TabNote(F(1), F(2), 1, 0, 0, "i"),
            TabNote(F(2), F(1), 2, 0, 0, "m"),
        ),
        STANDARD_TUNING,
        0,
    )

    root = ET.fromstring(render_musicxml_tab(tab))
    pitched_voices: dict[int, int] = defaultdict(int)
    for note in root.findall(".//note"):
        if note.find("pitch") is not None:
            pitched_voices[int(note.findtext("voice", "0"))] += 1

    assert sorted(pitched_voices) == [1, 2]
    assert pitched_voices == {1: 2, 2: 1}
