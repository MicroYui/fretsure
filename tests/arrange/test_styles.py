from fractions import Fraction as F

import pytest

from fretsure.arrange.propose import propose_style
from fretsure.ir import ChordSymbol, Meta, MusicIR, Note
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.api import Infeasible
from fretsure.solver.score import solve_fingering_score


def _lead_sheet() -> MusicIR:
    return MusicIR(
        (Note(F(0), F(4), 72, "melody"),),
        (ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 0),),
        Meta("C", (4, 4), 90.0, "test", "style", "CC0", F(4)),
    )


def test_offline_styles_preserve_source_melody_and_have_distinct_rhythms() -> None:
    source = _lead_sheet()
    source_melody = tuple(note for note in source.notes if note.voice == "melody")
    targets = {
        style: propose_style(source, style) for style in ("fingerstyle", "classical", "jazz", "rnb")
    }

    for target in targets.values():
        assert all(note in target for note in source_melody)

    assert not [note for note in targets["fingerstyle"] if note.voice == "harmony"]
    assert {note.onset for note in targets["classical"] if note.voice == "harmony"} == {F(1), F(3)}
    assert {note.onset for note in targets["jazz"] if note.voice == "harmony"} == {
        F(11, 4),
        F(15, 4),
    }
    assert {note.onset for note in targets["rnb"] if note.voice == "harmony"} == {
        F(1, 2),
        F(3, 2),
    }
    assert {
        note.onset: note.duration
        for note in targets["jazz"]
        if note.voice == "harmony"
    } == {F(11, 4): F(3, 4), F(15, 4): F(1, 4)}
    assert {note.duration for note in targets["rnb"] if note.voice == "harmony"} == {
        F(1, 4)
    }
    assert len(set(targets.values())) == 4


def test_style_answers_leave_space_below_a_close_melody_and_remain_solvable() -> None:
    source = MusicIR(
        (
            Note(F(0), F(1), 60, "melody"),
            Note(F(2), F(3), 62, "melody"),
            Note(F(5), F(1), 63, "melody"),
            Note(F(6), F(1), 66, "melody"),
        ),
        (
            ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 0),
            ChordSymbol(F(4), "G", frozenset({2, 7, 11}), 7),
        ),
        Meta("C", (4, 4), 96.0, "test", "close melody", "CC0", F(8)),
    )

    close_ranges = {
        "classical": (F(5), F(6)),
        "jazz": (F(6), F(7)),
        "rnb": (F(5), F(6)),
    }
    for style, (start, end) in close_ranges.items():
        target = propose_style(source, style)
        close_answers = [
            note
            for note in target
            if note.voice == "harmony" and start <= note.onset < end
        ]
        assert close_answers
        assert all(note.pitch <= 60 for note in close_answers)
        assert not isinstance(
            solve_fingering_score(
                target,
                (40, 45, 50, 55, 59, 64),
                0,
                MEDIAN_HAND,
                tempo_bpm=96,
            ),
            Infeasible,
        )


def test_difficulty_target_changes_optional_texture_but_not_source_melody() -> None:
    source = _lead_sheet()
    source_melody = tuple(note for note in source.notes if note.voice == "melody")
    targets = {
        tier: propose_style(source, "jazz", difficulty_tier=tier)
        for tier in ("beginner", "intermediate", "advanced")
    }

    assert all(
        tuple(note for note in target if note.voice == "melody") == source_melody
        for target in targets.values()
    )
    assert [
        len(tuple(note for note in targets[tier] if note.voice == "harmony"))
        for tier in ("beginner", "intermediate", "advanced")
    ] == [0, 2, 3]


def test_unknown_generation_difficulty_is_rejected() -> None:
    with pytest.raises(ValueError, match="difficulty tier"):
        propose_style(_lead_sheet(), "jazz", difficulty_tier="virtuoso")
