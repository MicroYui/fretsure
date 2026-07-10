from fractions import Fraction as F

from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import Meta, MusicIR, Note
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


def test_harmony_jaccard_perfect() -> None:
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
    assert 0.0 < harmony_jaccard(ir, tab) < 1.0


def test_fidelity_combines() -> None:
    ir = MusicIR((Note(F(0), F(1), 64, "melody"),), (), _meta())
    tab = Tab((TabNote(F(0), F(1), 5, 0, 0, "a"),), STANDARD_TUNING, 0)
    f = fidelity(ir, tab)
    assert f.melody_recall == 1.0
