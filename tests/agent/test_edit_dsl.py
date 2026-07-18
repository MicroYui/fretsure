from fractions import Fraction as F

import pytest

from fretsure.agent.edit_dsl import Edit, MelodyProtected, apply_edit, parse_edit
from fretsure.ir import Note

_NOTES = (
    Note(F(0), F(1), 64, "melody"),
    Note(F(0), F(1), 55, "harmony"),
    Note(F(0), F(1), 40, "bass"),
)


def test_drop_harmony_removes_it() -> None:
    out = apply_edit(_NOTES, Edit("drop_note", F(0), 55))
    assert 55 not in [n.pitch for n in out]
    assert 64 in [n.pitch for n in out] and 40 in [n.pitch for n in out]


def test_drop_melody_is_protected() -> None:
    with pytest.raises(MelodyProtected):
        apply_edit(_NOTES, Edit("drop_note", F(0), 64))


def test_octave_shift_bass() -> None:
    out = apply_edit(_NOTES, Edit("octave_shift", F(0), 40, arg=12))
    assert 52 in [n.pitch for n in out]  # 40 + 12
    assert 40 not in [n.pitch for n in out]


def test_octave_shift_melody_protected() -> None:
    with pytest.raises(MelodyProtected):
        apply_edit(_NOTES, Edit("octave_shift", F(0), 64, arg=-12))


def test_revoice_harmony() -> None:
    out = apply_edit(_NOTES, Edit("revoice", F(0), 55, arg=59))
    assert 59 in [n.pitch for n in out] and 55 not in [n.pitch for n in out]


@pytest.mark.parametrize(
    "edit",
    [
        Edit("octave_shift", F(0), 40, arg=-41),
        Edit("revoice", F(0), 55, arg=128),
    ],
)
def test_pitch_edit_cannot_leave_midi_domain(edit: Edit) -> None:
    with pytest.raises(ValueError):
        apply_edit(_NOTES, edit)


def test_pitch_edit_cannot_collide_at_the_same_onset() -> None:
    with pytest.raises(ValueError):
        apply_edit(_NOTES, Edit("revoice", F(0), 55, arg=40))


def test_apply_no_match_is_noop() -> None:
    out = apply_edit(_NOTES, Edit("drop_note", F(9), 99))
    assert out == tuple(sorted(_NOTES, key=lambda n: (n.onset, n.pitch)))


def test_parse_edit_valid() -> None:
    e = parse_edit({"op": "octave_shift", "target_onset": "1/2", "target_pitch": 43, "arg": -12})
    assert e == Edit("octave_shift", F(1, 2), 43, -12)


def test_parse_edit_numeric_onset() -> None:
    e = parse_edit({"op": "drop_note", "target_onset": 2, "target_pitch": 50})
    assert e.target_onset == F(2) and e.arg == 0


def test_parse_edit_bad_op_raises() -> None:
    with pytest.raises(ValueError):
        parse_edit({"op": "explode", "target_onset": 0, "target_pitch": 50})


def test_parse_edit_missing_field_raises() -> None:
    with pytest.raises(ValueError):
        parse_edit({"op": "drop_note", "target_onset": 0})


@pytest.mark.parametrize(
    "value",
    [
        {"op": "drop_note", "target_onset": -1, "target_pitch": 50},
        {"op": "drop_note", "target_onset": 0, "target_pitch": 128},
        {"op": "drop_note", "target_onset": 0, "target_pitch": 50, "arg": 1},
        {"op": "octave_shift", "target_onset": 0, "target_pitch": 50, "arg": 7},
        {"op": "revoice", "target_onset": 0, "target_pitch": 50, "arg": -1},
        {"op": "drop_note", "target_onset": 0, "target_pitch": 50.9},
        {"op": "drop_note", "target_onset": 0, "target_pitch": True},
        {"op": "octave_shift", "target_onset": 0, "target_pitch": 50, "arg": 12.9},
        {"op": "revoice", "target_onset": 0, "target_pitch": 50, "arg": "64"},
    ],
)
def test_parse_edit_rejects_values_outside_the_trace_and_target_domain(
    value: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        parse_edit(value)
