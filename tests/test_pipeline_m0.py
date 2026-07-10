from fractions import Fraction as F

from fretsure.arrange.propose import propose_fingerstyle
from fretsure.geometry import STANDARD_TUNING, note_pitch
from fretsure.ir import ChordSymbol, Meta, MusicIR, Note
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.pipeline_m0 import run_m0


def _meta() -> Meta:
    return Meta("C", (4, 4), 90.0, "unit", "leadsheet", "PD")


def _leadsheet() -> MusicIR:
    notes = (
        Note(F(0), F(1), 60, "melody"), Note(F(0), F(1), 48, "bass"),
        Note(F(0), F(1), 55, "harmony"),  # should be dropped by the M0 proposer
        Note(F(1), F(1), 62, "melody"), Note(F(1), F(1), 48, "bass"),
        Note(F(2), F(1), 64, "melody"), Note(F(2), F(1), 53, "bass"),
        Note(F(3), F(1), 65, "melody"), Note(F(3), F(1), 53, "bass"),
    )
    return MusicIR(notes, (ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 0),), _meta())


def test_propose_drops_harmony_keeps_melody_bass() -> None:
    kept = propose_fingerstyle(_leadsheet())
    assert all(n.voice in ("melody", "bass") for n in kept)
    assert not any(n.voice == "harmony" for n in kept)
    pitches = [n.pitch for n in kept]
    assert 60 in pitches and 48 in pitches


def test_run_m0_produces_playable_tab() -> None:
    res = run_m0(_leadsheet(), STANDARD_TUNING, 0, MEDIAN_HAND)
    assert res.tab is not None
    assert res.infeasible is None
    assert res.oracle is not None and res.oracle.verdict != "RED"
    assert res.ascii is not None and len(res.ascii.split("\n")) == 6
    played = {
        note_pitch(n.string, n.fret, res.tab.tuning, res.tab.capo) for n in res.tab.notes
    }
    for melody_pitch in (60, 62, 64, 65):
        assert melody_pitch in played


def test_run_m0_deterministic() -> None:
    ir = _leadsheet()
    assert run_m0(ir, STANDARD_TUNING, 0, MEDIAN_HAND) == run_m0(
        ir, STANDARD_TUNING, 0, MEDIAN_HAND
    )
