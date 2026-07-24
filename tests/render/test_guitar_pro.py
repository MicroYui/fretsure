from __future__ import annotations

from fractions import Fraction as F
from io import BytesIO

import guitarpro as gp  # type: ignore[import-untyped]
import pytest

from fretsure.geometry import STANDARD_TUNING
from fretsure.render.guitar_pro import (
    GUITAR_PRO_FILE_VERSION,
    GuitarProExportCode,
    GuitarProExportError,
    render_guitar_pro,
)
from fretsure.tab import Tab, TabNote


def _note(
    onset: F,
    duration: F,
    string: int,
    fret: int,
    left: int,
    right: str,
) -> TabNote:
    return TabNote(onset, duration, string, fret, left, right)  # type: ignore[arg-type]


def _tab() -> Tab:
    return Tab(
        (
            _note(F(0), F(2), 0, 3, 3, "p"),
            _note(F(0), F(1), 4, 1, 1, "i"),
            _note(F(1), F(1, 2), 5, 3, 3, "a"),
            _note(F(3, 2), F(1, 2), 5, 5, 4, "m"),
        ),
        STANDARD_TUNING,
        2,
    )


def _parse(data: bytes) -> object:
    return gp.parse(BytesIO(data), encoding="cp1252")


def _attacks(song: object) -> list[tuple[F, F, int, int, object, object]]:
    result: list[list[object]] = []
    active_index: dict[int, int] = {}
    for measure in song.tracks[0].measures:
        for beat in measure.voices[0].beats:
            onset = F(beat.start - gp.Duration.quarterTime, gp.Duration.quarterTime)
            duration = F(beat.duration.time, gp.Duration.quarterTime)
            if beat.status is gp.BeatStatus.rest:
                active_index.clear()
                continue
            present = {note.string for note in beat.notes}
            for string in tuple(active_index):
                if string not in present:
                    active_index.pop(string)
            for note in beat.notes:
                if note.type is gp.NoteType.normal:
                    active_index[note.string] = len(result)
                    result.append(
                        [
                            onset,
                            duration,
                            note.string,
                            note.value,
                            note.effect.leftHandFinger,
                            note.effect.rightHandFinger,
                        ]
                    )
                elif note.type is gp.NoteType.tie:
                    result[active_index[note.string]][1] += duration
    return [tuple(row) for row in result]  # type: ignore[misc]


def test_gp5_is_deterministic_and_round_trips_score_metadata() -> None:
    first = render_guitar_pro(_tab(), tempo_bpm=96.0, title="Two Tigers")
    second = render_guitar_pro(_tab(), tempo_bpm=96.0, title="Two Tigers")

    assert first == second
    assert b"FICHIER GUITAR PRO v5.10" in first[:32]

    song = _parse(first)
    assert song.versionTuple == GUITAR_PRO_FILE_VERSION
    assert song.title == "Two Tigers"
    assert song.tempo == 96
    assert len(song.tracks) == 1
    track = song.tracks[0]
    assert track.name == "Fretsure Guitar"
    assert track.offset == 2
    assert [(string.number, string.value) for string in track.strings] == [
        (1, 64),
        (2, 59),
        (3, 55),
        (4, 50),
        (5, 45),
        (6, 40),
    ]


def test_gp5_round_trip_preserves_timing_strings_frets_and_fingering() -> None:
    song = _parse(render_guitar_pro(_tab(), tempo_bpm=90.0))

    assert _attacks(song) == [
        (F(0), F(1), 2, 1, gp.Fingering.index, gp.Fingering.index),
        (F(0), F(2), 6, 3, gp.Fingering.annular, gp.Fingering.thumb),
        (F(1), F(1, 2), 1, 3, gp.Fingering.annular, gp.Fingering.annular),
        (F(3, 2), F(1, 2), 1, 5, gp.Fingering.little, gp.Fingering.middle),
    ]


def test_gp5_preserves_exact_tuplet_timing_and_cross_bar_tie() -> None:
    tab = Tab(
        (
            _note(F(1, 3), F(1, 3), 5, 1, 1, "i"),
            _note(F(15, 4), F(1, 2), 0, 3, 3, "p"),
        ),
        STANDARD_TUNING,
        0,
    )

    song = _parse(render_guitar_pro(tab))

    assert len(song.measureHeaders) == 2
    assert _attacks(song) == [
        (F(1, 3), F(1, 3), 1, 1, gp.Fingering.index, gp.Fingering.index),
        (F(15, 4), F(1, 2), 6, 3, gp.Fingering.annular, gp.Fingering.thumb),
    ]


def test_gp5_rounds_fractional_tempo_half_up_and_stamps_exporter() -> None:
    song = _parse(render_guitar_pro(_tab(), tempo_bpm=90.5))

    assert song.tempo == 91
    assert song.notice == [
        "Generated deterministically from canonical Fretsure Tab "
        "with guitar-pro-5.1@0.1.0."
    ]


def test_gp5_rejects_domains_it_cannot_represent_without_loss() -> None:
    with pytest.raises(GuitarProExportError) as invalid:
        render_guitar_pro(Tab((), STANDARD_TUNING, 0))
    assert invalid.value.code is GuitarProExportCode.INVALID_TAB

    with pytest.raises(GuitarProExportError) as title:
        render_guitar_pro(_tab(), title="两只老虎")
    assert title.value.code is GuitarProExportCode.TITLE_UNREPRESENTABLE

    overlap = Tab(
        (
            _note(F(0), F(2), 0, 3, 3, "p"),
            _note(F(1), F(1), 0, 5, 4, "p"),
        ),
        STANDARD_TUNING,
        0,
    )
    with pytest.raises(GuitarProExportError) as string_overlap:
        render_guitar_pro(overlap)
    assert string_overlap.value.code is GuitarProExportCode.STRING_OVERLAP

    unspellable = Tab(
        (_note(F(1, 17), F(1), 5, 1, 1, "i"),),
        STANDARD_TUNING,
        0,
    )
    with pytest.raises(GuitarProExportError) as timing:
        render_guitar_pro(unspellable)
    assert timing.value.code is GuitarProExportCode.TIMING_UNREPRESENTABLE
