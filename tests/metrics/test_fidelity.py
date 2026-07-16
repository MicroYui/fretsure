from fractions import Fraction as F

from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import ChordSymbol, Meta, MusicIR, Note
from fretsure.metrics.fidelity import (
    bass_preserved,
    fidelity,
    harmony_jaccard,
    melody_recall,
)
from fretsure.tab import Tab, TabNote


def _meta() -> Meta:
    return Meta("C", (4, 4), 90.0, "t", "t", "PD")


def test_melody_fully_preserved() -> None:
    ir = MusicIR((Note(F(0), F(1), 64, "melody"), Note(F(1), F(1), 65, "melody")), (), _meta())
    tab = Tab(
        (TabNote(F(0), F(1), 5, 0, 0, "a"), TabNote(F(1), F(1), 5, 1, 1, "a")),
        STANDARD_TUNING,
        0,
    )
    assert melody_recall(ir, tab) == 1.0


def test_melody_dropped_lowers_recall() -> None:
    ir = MusicIR((Note(F(0), F(1), 64, "melody"), Note(F(1), F(1), 65, "melody")), (), _meta())
    tab = Tab((TabNote(F(0), F(1), 5, 0, 0, "a"),), STANDARD_TUNING, 0)
    assert melody_recall(ir, tab) == 0.5


def test_empty_melody_is_one() -> None:
    ir = MusicIR((Note(F(0), F(1), 40, "bass"),), (), _meta())
    assert melody_recall(ir, Tab((), STANDARD_TUNING, 0)) == 1.0


def test_bass_preserved() -> None:
    ir = MusicIR((Note(F(0), F(1), 40, "bass"),), (), _meta())
    tab = Tab((TabNote(F(0), F(1), 0, 0, 0, "p"),), STANDARD_TUNING, 0)  # open low E = 40
    assert bass_preserved(ir, tab) == 1.0


def test_harmony_jaccard_no_chords_falls_back_to_note_onsets() -> None:
    ir = MusicIR((Note(F(0), F(1), 60, "melody"), Note(F(0), F(1), 64, "harmony")), (), _meta())
    tab = Tab(
        (TabNote(F(0), F(1), 4, 1, 1, "i"), TabNote(F(0), F(1), 5, 0, 0, "a")),  # 60 + 64
        STANDARD_TUNING,
        0,
    )
    assert harmony_jaccard(ir, tab) == 1.0


def test_harmony_jaccard_partial() -> None:
    ir = MusicIR((Note(F(0), F(1), 60, "melody"), Note(F(0), F(1), 64, "harmony")), (), _meta())
    tab = Tab((TabNote(F(0), F(1), 4, 1, 1, "i"),), STANDARD_TUNING, 0)  # only pc 0
    assert harmony_jaccard(ir, tab) == 0.5


def test_harmony_jaccard_uses_chord_segments_instead_of_source_notes() -> None:
    ir = MusicIR(
        (
            Note(F(0), F(2), 61, "melody"),
            Note(F(2), F(2), 63, "melody"),
        ),
        (
            ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 0),
            ChordSymbol(F(2), "G", frozenset({2, 7, 11}), 7),
        ),
        _meta(),
    )
    tuning = (60, 64, 67, 62, 67, 71)
    tab = Tab(
        (
            TabNote(F(0), F(1), 0, 0, 0, "p"),
            TabNote(F(1), F(1), 1, 0, 0, "i"),
            TabNote(F(1), F(1), 2, 0, 0, "m"),
            TabNote(F(2), F(1), 3, 0, 0, "p"),
            TabNote(F(3), F(1), 4, 0, 0, "i"),
            TabNote(F(3), F(1), 5, 0, 0, "m"),
        ),
        tuning,
        0,
    )

    assert harmony_jaccard(ir, tab) == 1.0


def test_harmony_jaccard_counts_notes_sounding_across_segment_boundary() -> None:
    ir = MusicIR(
        (Note(F(0), F(4), 60, "melody"),),
        (
            ChordSymbol(F(0), "C", frozenset({0}), 0),
            ChordSymbol(F(2), "C", frozenset({0}), 0),
        ),
        _meta(),
    )
    tab = Tab((TabNote(F(0), F(4), 0, 0, 0, "p"),), (60,), 0)

    assert harmony_jaccard(ir, tab) == 1.0


def test_harmony_jaccard_last_segment_stops_at_source_piece_end() -> None:
    ir = MusicIR(
        (Note(F(0), F(2), 60, "melody"),),
        (ChordSymbol(F(0), "C", frozenset({0}), 0),),
        _meta(),
    )
    tab = Tab(
        (
            TabNote(F(0), F(1), 0, 0, 0, "p"),
            TabNote(F(2), F(1), 1, 0, 0, "i"),
        ),
        (60, 61),
        0,
    )

    assert harmony_jaccard(ir, tab) == 1.0


def test_harmony_jaccard_last_segment_includes_notated_trailing_rest() -> None:
    ir = MusicIR(
        (Note(F(0), F(2), 60, "melody"),),
        (ChordSymbol(F(0), "C", frozenset({0}), 0),),
        Meta("C", (4, 4), 90.0, "t", "t", "PD", F(4)),
    )
    tab = Tab(
        (
            TabNote(F(0), F(1), 0, 0, 0, "p"),
            TabNote(F(3), F(1), 1, 0, 0, "i"),
        ),
        (60, 61),
        0,
    )

    assert harmony_jaccard(ir, tab) == 0.5


def test_harmony_jaccard_legacy_duration_still_uses_last_source_note() -> None:
    ir = MusicIR(
        (Note(F(0), F(2), 60, "melody"),),
        (ChordSymbol(F(0), "C", frozenset({0}), 0),),
        _meta(),
    )
    tab = Tab(
        (
            TabNote(F(0), F(1), 0, 0, 0, "p"),
            TabNote(F(3), F(1), 1, 0, 0, "i"),
        ),
        (60, 61),
        0,
    )

    assert harmony_jaccard(ir, tab) == 1.0


def test_harmony_jaccard_same_onset_chords_share_segment_and_are_averaged() -> None:
    ir = MusicIR(
        (Note(F(0), F(1), 60, "melody"),),
        (
            ChordSymbol(F(0), "C5", frozenset({0, 7}), 0),
            ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 0),
        ),
        _meta(),
    )
    tab = Tab(
        (
            TabNote(F(0), F(1), 0, 0, 0, "p"),
            TabNote(F(0), F(1), 1, 0, 0, "i"),
        ),
        (60, 67),
        0,
    )

    assert harmony_jaccard(ir, tab) == (1.0 + 2 / 3) / 2


def test_fidelity_combines() -> None:
    ir = MusicIR((Note(F(0), F(1), 64, "melody"),), (), _meta())
    tab = Tab((TabNote(F(0), F(1), 5, 0, 0, "a"),), STANDARD_TUNING, 0)
    f = fidelity(ir, tab)
    assert f.melody_recall == 1.0
