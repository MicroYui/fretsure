from fractions import Fraction as F

import pytest

from fretsure.ir import (
    ChordSymbol,
    IRInputError,
    Meta,
    MusicIR,
    Note,
    snapshot_music_ir,
    validate_ir,
)


def _meta() -> Meta:
    return Meta("C", (4, 4), 90.0, "unit", "t", "PD")


def test_valid_ir_has_no_violations() -> None:
    ir = MusicIR(
        notes=(Note(F(0), F(1), 60, "melody"), Note(F(0), F(1), 48, "bass")),
        chords=(ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 0),),
        meta=_meta(),
    )
    assert validate_ir(ir) == []


@pytest.mark.parametrize("tempo", [1 << 4096, -(1 << 4096)])
def test_snapshot_rejects_huge_exact_tempo_before_float_conversion(tempo: int) -> None:
    ir = MusicIR((), (), Meta("C", (4, 4), tempo, "unit", "t", "PD"))

    with pytest.raises(IRInputError, match="meta.tempo_bpm"):
        snapshot_music_ir(ir)


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


def test_missing_melody_needs_no_melody_in_whole_piece() -> None:
    # onset 0 has notes but only bass/harmony, no melody anywhere -> flagged once
    ir = MusicIR((Note(F(0), F(1), 48, "bass"),), (), _meta())
    viols = validate_ir(ir)
    assert [v.kind for v in viols] == ["missing_melody"]
    assert viols[0].onset is None


def test_sustained_melody_with_accompaniment_onsets_ok() -> None:
    # melody sustains from beat 1; bass/harmony onset at beats 2,3 -> NOT flagged.
    # This is the fingerstyle bread-and-butter case (Travis / alternating bass).
    ir = MusicIR(
        (
            Note(F(0), F(3), 72, "melody"),
            Note(F(1), F(1), 48, "bass"),
            Note(F(2), F(1), 55, "harmony"),
        ),
        (),
        _meta(),
    )
    assert not any(v.kind == "missing_melody" for v in validate_ir(ir))


def test_bad_chord_root_flagged() -> None:
    ir = MusicIR(
        (Note(F(0), F(1), 60, "melody"),),
        (ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 2),),
        _meta(),
    )
    assert any(v.kind == "bad_chord_root" for v in validate_ir(ir))


def test_note_and_chord_onsets_must_be_nonnegative() -> None:
    ir = MusicIR(
        (Note(F(-1), F(1), 60, "melody"),),
        (ChordSymbol(F(-2), "C", frozenset({0, 4, 7}), 0),),
        _meta(),
    )

    violations = validate_ir(ir)

    assert [v.kind for v in violations] == ["negative_onset", "negative_onset"]
    assert [v.onset for v in violations] == [F(-1), F(-2)]


def test_empty_ir_may_have_zero_explicit_piece_duration() -> None:
    ir = MusicIR((), (), Meta("C", (4, 4), 90.0, "unit", "t", "PD", F(0)))

    assert validate_ir(ir) == []


@pytest.mark.parametrize(
    ("duration", "expected_kind"),
    [(F(-1), "negative_piece_duration"), (F(0), "nonpositive_piece_duration")],
)
def test_explicit_piece_duration_must_be_positive_when_events_exist(
    duration: F, expected_kind: str
) -> None:
    ir = MusicIR(
        (Note(F(0), F(1), 60, "melody"),),
        (),
        Meta("C", (4, 4), 90.0, "unit", "t", "PD", duration),
    )

    assert [v.kind for v in validate_ir(ir)] == [expected_kind]


def test_explicit_piece_duration_must_cover_every_note_end() -> None:
    ir = MusicIR(
        (Note(F(1), F(2), 60, "melody"),),
        (),
        Meta("C", (4, 4), 90.0, "unit", "t", "PD", F(2)),
    )

    violations = validate_ir(ir)

    assert [v.kind for v in violations] == ["note_beyond_piece_end"]
    assert violations[0].onset == F(1)


def test_chord_onset_must_be_strictly_before_explicit_piece_end() -> None:
    ir = MusicIR(
        (),
        (ChordSymbol(F(4), "C", frozenset({0, 4, 7}), 0),),
        Meta("C", (4, 4), 90.0, "unit", "t", "PD", F(4)),
    )

    violations = validate_ir(ir)

    assert [v.kind for v in violations] == ["chord_outside_piece"]
    assert violations[0].onset == F(4)


def test_legacy_meta_without_piece_duration_keeps_unbounded_timeline_behavior() -> None:
    ir = MusicIR(
        (Note(F(8), F(2), 60, "melody"),),
        (ChordSymbol(F(12), "C", frozenset({0, 4, 7}), 0),),
        _meta(),
    )

    assert validate_ir(ir) == []


def test_validate_ir_deterministic_exact_order() -> None:
    # melody note has zero duration (nonpositive), chord root not in pcs (bad_chord_root).
    # Pins BOTH determinism and the emission order: per-note first, then chords.
    ir = MusicIR(
        (Note(F(2), F(1), 48, "bass"), Note(F(0), F(0), 60, "melody")),
        (ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 2),),
        _meta(),
    )
    result = validate_ir(ir)
    assert validate_ir(ir) == result
    assert [v.kind for v in result] == ["nonpositive_duration", "bad_chord_root"]


def test_dataclasses_are_frozen() -> None:
    import dataclasses

    n = Note(F(0), F(1), 60, "melody")
    with pytest.raises(dataclasses.FrozenInstanceError):
        n.pitch = 61  # type: ignore[misc]
