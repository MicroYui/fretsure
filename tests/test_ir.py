from fractions import Fraction as F

import pytest

from fretsure.ir import ChordSymbol, Meta, MusicIR, Note, validate_ir


def _meta() -> Meta:
    return Meta("C", (4, 4), 90.0, "unit", "t", "PD")


def test_valid_ir_has_no_violations() -> None:
    ir = MusicIR(
        notes=(Note(F(0), F(1), 60, "melody"), Note(F(0), F(1), 48, "bass")),
        chords=(ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 0),),
        meta=_meta(),
    )
    assert validate_ir(ir) == []


def test_nonpositive_duration_flagged() -> None:
    ir = MusicIR((Note(F(0), F(0), 60, "melody"),), (), _meta())
    assert any(v.kind == "nonpositive_duration" for v in validate_ir(ir))


def test_pitch_range_flagged() -> None:
    ir = MusicIR((Note(F(0), F(1), 200, "melody"),), (), _meta())
    assert any(v.kind == "pitch_range" for v in validate_ir(ir))


def test_melody_polyphony_flagged() -> None:
    ir = MusicIR(
        (Note(F(0), F(1), 60, "melody"), Note(F(0), F(1), 62, "melody")), (), _meta()
    )
    assert any(v.kind == "melody_polyphony" for v in validate_ir(ir))


def test_missing_melody_flagged() -> None:
    # onset 0 has notes but only bass/inner, no melody
    ir = MusicIR((Note(F(0), F(1), 48, "bass"),), (), _meta())
    assert any(v.kind == "missing_melody" for v in validate_ir(ir))


def test_bad_chord_root_flagged() -> None:
    ir = MusicIR(
        (Note(F(0), F(1), 60, "melody"),),
        (ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 2),),
        _meta(),
    )
    assert any(v.kind == "bad_chord_root" for v in validate_ir(ir))


def test_validate_ir_is_deterministic() -> None:
    ir = MusicIR(
        (Note(F(2), F(1), 48, "bass"), Note(F(0), F(0), 60, "melody")),
        (ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 2),),
        _meta(),
    )
    assert validate_ir(ir) == validate_ir(ir)


def test_dataclasses_are_frozen() -> None:
    import dataclasses

    n = Note(F(0), F(1), 60, "melody")
    with pytest.raises(dataclasses.FrozenInstanceError):
        n.pitch = 61  # type: ignore[misc]
