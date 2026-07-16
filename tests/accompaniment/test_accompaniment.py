from fractions import Fraction as F

import pytest

from fretsure.accompaniment.api import arrange_accompaniment
from fretsure.agent.arranger import ArrangeGoal
from fretsure.geometry import note_pitch
from fretsure.ir import ChordSymbol, Meta, MusicIR
from fretsure.oracle.core import check_playability
from fretsure.oracle.input import OracleInputCode, SolverInputError
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.tab import Tab


def _ir(*chords: ChordSymbol) -> MusicIR:
    return MusicIR((), chords, Meta("C", (4, 4), 90.0, "t", "t", "PD"))


def _pcs(tab: Tab) -> set[int]:
    return {note_pitch(n.string, n.fret, tab.tuning, tab.capo) % 12 for n in tab.notes}


def test_arpeggio_accompaniment_playable_and_faithful() -> None:
    ir = _ir(ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 0))
    result = arrange_accompaniment(ir, ArrangeGoal(), MEDIAN_HAND, style="arpeggio")
    assert isinstance(result, Tab)
    assert check_playability(result, MEDIAN_HAND).verdict != "RED"
    assert {0, 4, 7} <= _pcs(result)  # all chord tones sounded over the bar


def test_arpeggio_two_chords() -> None:
    ir = _ir(
        ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 0),
        ChordSymbol(F(4), "G", frozenset({7, 11, 2}), 7),
    )
    result = arrange_accompaniment(ir, ArrangeGoal(), MEDIAN_HAND, style="arpeggio")
    assert isinstance(result, Tab)
    assert check_playability(result, MEDIAN_HAND).verdict != "RED"


def test_deterministic() -> None:
    ir = _ir(ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 0))
    a = arrange_accompaniment(ir, ArrangeGoal(), MEDIAN_HAND)
    b = arrange_accompaniment(ir, ArrangeGoal(), MEDIAN_HAND)
    assert a == b


def test_accompaniment_validates_config_before_pattern_lookup() -> None:
    ir = _ir(ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 0))

    with pytest.raises(SolverInputError) as caught:
        arrange_accompaniment(
            ir,
            ArrangeGoal(tuning=()),
            MEDIAN_HAND,
            style="not-a-pattern",
        )

    assert OracleInputCode.TUNING_LENGTH in {
        diagnostic.code for diagnostic in caught.value.diagnostics
    }
