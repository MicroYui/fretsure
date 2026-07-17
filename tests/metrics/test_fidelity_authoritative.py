from collections.abc import Callable
from fractions import Fraction as F

import pytest

from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import ChordSymbol, Meta, MusicIR, Note
from fretsure.metrics.fidelity import (
    FIDELITY_CHECKER_VERSION,
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
        TabNote(F(0), F(1), 2, 6, 2, "i"),  # G#3, chord third
        TabNote(F(0), F(1), 4, 0, 0, "m"),  # B3, chord fifth
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


def test_bass_root_accuracy_counts_a_root_sustained_across_chord_onset() -> None:
    ir = MusicIR(
        (Note(F(0), F(2), 64, "melody"),),
        (
            ChordSymbol(F(0), "E", frozenset({4, 8, 11}), 4),
            ChordSymbol(F(1), "E", frozenset({4, 8, 11}), 4),
        ),
        Meta("E", (4, 4), 90.0, "t", "t", "PD"),
    )
    tab = Tab(
        (
            TabNote(F(0), F(2), 0, 0, 0, "p"),  # held low E (MIDI 40)
            TabNote(F(1), F(1), 5, 0, 0, "a"),  # newly attacked high E
        ),
        STANDARD_TUNING,
        0,
    )

    assert bass_root_accuracy(ir, tab) == 1.0


def test_bass_root_accuracy_uses_lowest_of_all_notes_sounding_at_chord_onset() -> None:
    ir = MusicIR(
        (Note(F(0), F(2), 64, "melody"),),
        (ChordSymbol(F(1), "E", frozenset({4, 8, 11}), 4),),
        Meta("E", (4, 4), 90.0, "t", "t", "PD"),
    )
    tab = Tab(
        (
            TabNote(F(0), F(2), 0, 1, 1, "p"),  # held F, below the new E
            TabNote(F(1), F(1), 5, 0, 0, "a"),
        ),
        STANDARD_TUNING,
        0,
    )

    assert bass_root_accuracy(ir, tab) == 0.0


def test_faithfulness_gate_passes_faithful() -> None:
    g = faithfulness(_ir(), _FAITHFUL)
    assert isinstance(g, FaithfulnessGate)
    assert g.passed and g.melody_f1 == 1.0
    assert g.evaluated_dimensions == ("melody", "bass_root", "harmony")
    assert g.unavailable_dimensions == ()


def test_faithfulness_gate_fails_when_melody_lost() -> None:
    partial = Tab((TabNote(F(0), F(1), 0, 0, 0, "p"),), STANDARD_TUNING, 0)  # bass only, no melody
    g = faithfulness(_ir(), partial)
    assert not g.passed


def test_melody_only_gate_marks_missing_evidence_unavailable() -> None:
    ir = MusicIR(
        (Note(F(0), F(1), 64, "melody"),),
        (),
        Meta("key-signature:unprovided", (4, 4), 120.0, "midi", "t", "unprovided"),
    )
    tab = Tab((TabNote(F(0), F(1), 5, 0, 0, "a"),), STANDARD_TUNING, 0)

    gate = faithfulness(ir, tab)

    assert gate == FaithfulnessGate(
        1.0,
        None,
        None,
        True,
        ("melody",),
        ("bass_root", "harmony"),
    )


def test_gate_with_no_source_evidence_cannot_pass() -> None:
    ir = MusicIR((), (), Meta("C", (4, 4), 90.0, "t", "t", "PD"))

    gate = faithfulness(ir, Tab((), STANDARD_TUNING, 0))

    assert gate == FaithfulnessGate(
        None,
        None,
        None,
        False,
        (),
        ("melody", "bass_root", "harmony"),
    )


@pytest.mark.parametrize(
    "gate",
    [
        lambda: FaithfulnessGate(
            1.0,
            None,
            None,
            True,
            ("melody", "melody"),
            ("bass_root", "harmony"),
        ),
        lambda: FaithfulnessGate(
            1.0,
            None,
            None,
            True,
            ("melody",),
            ("harmony",),
        ),
        lambda: FaithfulnessGate(
            1.0,
            1.0,
            None,
            True,
            ("melody",),
            ("bass_root", "harmony"),
        ),
        lambda: FaithfulnessGate(
            0.0,
            None,
            None,
            True,
            ("melody",),
            ("bass_root", "harmony"),
        ),
    ],
)
def test_gate_rejects_forged_availability_or_passed_state(
    gate: Callable[[], FaithfulnessGate],
) -> None:
    with pytest.raises(ValueError):
        gate()


def test_fidelity_checker_version_identifies_availability_semantics() -> None:
    assert FIDELITY_CHECKER_VERSION == "fidelity@0.3.0"
