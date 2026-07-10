from fractions import Fraction as F

from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import ChordSymbol, Meta, MusicIR, Note
from fretsure.metrics.fidelity import (
    FaithfulnessGate,
    bass_root_accuracy,
    faithfulness,
    melody_f1,
)
from fretsure.tab import Tab, TabNote


def _ir() -> MusicIR:
    return MusicIR(
        (
            Note(F(0), F(1), 64, "melody"),
            Note(F(1), F(1), 65, "melody"),
            Note(F(0), F(2), 40, "bass"),
        ),
        (ChordSymbol(F(0), "E", frozenset({4, 8, 11}), 4),),
        Meta("C", (4, 4), 90.0, "t", "t", "PD"),
    )


# faithful tab: melody 64@0 (top), bass 40@0, melody 65@1
_FAITHFUL = Tab(
    (
        TabNote(F(0), F(1), 5, 0, 0, "a"),
        TabNote(F(0), F(2), 0, 0, 0, "p"),
        TabNote(F(1), F(1), 5, 1, 1, "a"),
    ),
    STANDARD_TUNING,
    0,
)


def test_melody_f1_perfect() -> None:
    assert melody_f1(_ir(), _FAITHFUL) == 1.0


def test_melody_f1_drops_when_note_missing() -> None:
    partial = Tab((TabNote(F(0), F(1), 5, 0, 0, "a"),), STANDARD_TUNING, 0)  # first note only
    assert melody_f1(_ir(), partial) < 1.0


def test_melody_f1_empty_melody_is_one() -> None:
    ir = MusicIR((Note(F(0), F(1), 40, "bass"),), (), Meta("C", (4, 4), 90.0, "t", "t", "PD"))
    assert melody_f1(ir, Tab((), STANDARD_TUNING, 0)) == 1.0


def test_bass_root_accuracy_hits_on_strong_beat() -> None:
    # chord root pc 4 (E); tab lowest pitch at onset 0 is 40 (pc 4)
    assert bass_root_accuracy(_ir(), _FAITHFUL) == 1.0


def test_faithfulness_gate_passes_faithful() -> None:
    g = faithfulness(_ir(), _FAITHFUL)
    assert isinstance(g, FaithfulnessGate)
    assert g.passed and g.melody_f1 == 1.0


def test_faithfulness_gate_fails_when_melody_lost() -> None:
    partial = Tab((TabNote(F(0), F(1), 0, 0, 0, "p"),), STANDARD_TUNING, 0)  # bass only, no melody
    g = faithfulness(_ir(), partial)
    assert not g.passed
