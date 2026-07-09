from fractions import Fraction as F

import pytest

from fretsure.tab import Tab, TabNote, frames, tab_from_json, tab_to_json


def _tab() -> Tab:
    return Tab(
        notes=(
            TabNote(F(0), F(1), 0, 3, 3, "p"),
            TabNote(F(0), F(1), 5, 0, 0, "a"),
            TabNote(F(1), F(1), 2, 2, 2, "i"),
        ),
        tuning=(40, 45, 50, 55, 59, 64),
        capo=0,
    )


def test_frames_grouped_and_sorted() -> None:
    fr = frames(_tab())
    assert len(fr) == 2
    assert [n.string for n in fr[0]] == [0, 5]  # onset 0, two notes, string-sorted
    assert [n.onset for n in fr[0]] == [F(0), F(0)]
    assert [n.string for n in fr[1]] == [2]


def test_frames_onset_ascending() -> None:
    t = Tab(
        notes=(TabNote(F(3), F(1), 0, 1, 1, "p"), TabNote(F(1), F(1), 1, 2, 2, "i")),
        tuning=(40, 45, 50, 55, 59, 64),
        capo=0,
    )
    fr = frames(t)
    assert [f[0].onset for f in fr] == [F(1), F(3)]


def test_json_roundtrip_identity() -> None:
    t = _tab()
    assert tab_from_json(tab_to_json(t)) == t


def test_json_roundtrip_fraction_precision() -> None:
    t = Tab(
        notes=(TabNote(F(1, 3), F(2, 3), 4, 7, 1, "m"),),
        tuning=(40, 45, 50, 55, 59, 64),
        capo=2,
    )
    back = tab_from_json(tab_to_json(t))
    assert back == t
    assert back.notes[0].onset == F(1, 3)


def test_tabnote_frozen() -> None:
    import dataclasses

    n = TabNote(F(0), F(1), 0, 3, 3, "p")
    with pytest.raises(dataclasses.FrozenInstanceError):
        n.fret = 4  # type: ignore[misc]
