import math
from fractions import Fraction

import pytest

from fretsure.bench.generator import (
    GENERATOR_VERSION,
    MAX_GENERATOR_BARS,
    MAX_GENERATOR_NOTE_EVENTS,
    MAX_GENERATOR_SEED,
    PROCEDURAL_LEVELS,
    GenConfig,
    GeneratorInputError,
    ProceduralVariation,
    generate_leadsheet,
    generate_procedural_variant,
    procedural_variations,
    snapshot_gen_config,
)
from fretsure.ir import MusicIR, validate_ir
from fretsure.metrics.fidelity import faithfulness_dimensions

_MAJOR = {0, 2, 4, 5, 7, 9, 11}
_KEY_TONIC = {"C": 0, "G": 7, "D": 2, "A": 9, "F": 5}


def test_deterministic_same_seed() -> None:
    a = generate_leadsheet(GenConfig(seed=7))
    b = generate_leadsheet(GenConfig(seed=7))
    assert a == b


def test_default_output_remains_legacy_compatible() -> None:
    ir = generate_leadsheet(GenConfig())
    assert [
        (chord.onset, chord.symbol, chord.root_pc)
        for chord in ir.chords
    ] == [
        (Fraction(0), "Am", 9),
        (Fraction(4), "Dm", 2),
        (Fraction(8), "Bdim", 11),
        (Fraction(12), "Am", 9),
    ]
    assert [(note.onset, note.duration, note.pitch, note.voice) for note in ir.notes[:5]] == [
        (Fraction(0), Fraction(4), 57, "bass"),
        (Fraction(0), Fraction(1), 64, "melody"),
        (Fraction(1), Fraction(1), 60, "melody"),
        (Fraction(2), Fraction(1), 64, "melody"),
        (Fraction(3), Fraction(1), 67, "melody"),
    ]
    assert ir.meta.tempo_bpm == 90.0
    assert ir.meta.source == "procedural:seed0"
    assert ir.meta.title == "gen-C-0"


def test_different_seed_differs() -> None:
    a = generate_leadsheet(GenConfig(seed=1, bars=8))
    b = generate_leadsheet(GenConfig(seed=2, bars=8))
    assert a != b


def test_generated_ir_is_legal() -> None:
    ir = generate_leadsheet(GenConfig(seed=3, bars=4))
    assert validate_ir(ir) == []


def test_tempo_and_meter_are_preserved_with_quarter_beat_timing() -> None:
    ir = generate_leadsheet(GenConfig(meter=(3, 8), bars=2, tempo_bpm=96, seed=3))
    assert ir.meta.time_sig == (3, 8)
    assert ir.meta.tempo_bpm == 96.0
    assert [chord.onset for chord in ir.chords] == [Fraction(0), Fraction(3, 2)]
    bass = [note for note in ir.notes if note.voice == "bass"]
    assert [note.duration for note in bass] == [Fraction(3, 2), Fraction(3, 2)]
    assert validate_ir(ir) == []


def test_has_melody_bass_chords() -> None:
    ir = generate_leadsheet(GenConfig(seed=4, bars=4))
    voices = {n.voice for n in ir.notes}
    assert "melody" in voices and "bass" in voices
    assert len(ir.chords) == 4  # one chord per bar


def test_melody_pitch_classes_are_diatonic() -> None:
    cfg = GenConfig(key="G", bars=6, seed=5)
    ir = generate_leadsheet(cfg)
    tonic = _KEY_TONIC[cfg.key]
    for n in ir.notes:
        if n.voice == "melody":
            assert (n.pitch - tonic) % 12 in _MAJOR


def test_melody_above_bass() -> None:
    ir = generate_leadsheet(GenConfig(seed=6))
    melody = [n.pitch for n in ir.notes if n.voice == "melody"]
    bass = [n.pitch for n in ir.notes if n.voice == "bass"]
    assert min(melody) > max(bass)


def test_bars_parameter_respected() -> None:
    ir = generate_leadsheet(GenConfig(bars=8, seed=0))
    assert len(ir.chords) == 8


_PC_OF_NAME = {
    "C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5,
    "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11,
}


def _symbol_root_pc(sym: str) -> int:
    name = sym[:2] if len(sym) > 1 and sym[1] == "#" else sym[:1]
    return _PC_OF_NAME[name]


def test_chord_symbol_root_matches_root_pc() -> None:
    # The human/LLM-facing symbol string must name the same root the metric scores
    # (root_pc). A mislabeled key prefix once made the LLM place the wrong bass while
    # bass_root_accuracy scored against root_pc -> permanent joint_success=0.
    for key in ("C", "G", "D", "A", "F"):
        ir = generate_leadsheet(GenConfig(key=key, bars=8, seed=11))
        for c in ir.chords:
            assert _symbol_root_pc(c.symbol) == c.root_pc, (c.symbol, c.root_pc)


def test_chord_symbol_quality_matches_triad() -> None:
    # Symbol quality (''/'m'/'dim') must match the triad's intervals from the root.
    ir = generate_leadsheet(GenConfig(key="C", bars=8, seed=12))
    for c in ir.chords:
        intervals = frozenset((p - c.root_pc) % 12 for p in c.pitch_classes)
        if c.symbol.endswith("dim"):
            assert intervals == {0, 3, 6}
        elif c.symbol.endswith("m"):
            assert intervals == {0, 3, 7}
        else:
            assert intervals == {0, 4, 7}


@pytest.mark.parametrize("key", ["H", "c", "", " C"])
def test_unknown_key_is_rejected_instead_of_falling_back_to_c(key: str) -> None:
    with pytest.raises(GeneratorInputError, match="key") as exc_info:
        GenConfig(key=key)
    assert exc_info.value.field == "key"


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"meter": [4, 4]}, "meter"),
        ({"meter": (4,)}, "meter"),
        ({"meter": (True, 4)}, "meter"),
        ({"meter": (0, 4)}, "meter"),
        ({"meter": (4, 3)}, "meter"),
        ({"bars": True}, "bars"),
        ({"bars": 0}, "bars"),
        ({"bars": MAX_GENERATOR_BARS + 1}, "bars"),
        ({"seed": True}, "seed"),
        ({"seed": MAX_GENERATOR_SEED + 1}, "seed"),
        ({"tempo_bpm": True}, "tempo_bpm"),
        ({"tempo_bpm": 0.0}, "tempo_bpm"),
        ({"tempo_bpm": 1000.1}, "tempo_bpm"),
        ({"tempo_bpm": 10**10_000}, "tempo_bpm"),
        ({"tempo_bpm": math.inf}, "tempo_bpm"),
        ({"tempo_bpm": math.nan}, "tempo_bpm"),
    ],
)
def test_config_rejects_non_exact_or_out_of_resource_values(
    kwargs: dict[str, object], field: str
) -> None:
    with pytest.raises(GeneratorInputError) as exc_info:
        GenConfig(**kwargs)  # type: ignore[arg-type]
    assert exc_info.value.field == field


def test_snapshot_is_canonical_detached_and_revalidates_mutation() -> None:
    cfg = GenConfig(key="Bb", meter=(6, 8), bars=3, seed=-7, tempo_bpm=96)
    snapshot = snapshot_gen_config(cfg)
    assert snapshot == GenConfig(
        key="Bb", meter=(6, 8), bars=3, seed=-7, tempo_bpm=96.0
    )
    assert snapshot is not cfg
    assert type(snapshot.key) is str
    assert type(snapshot.meter) is tuple
    assert type(snapshot.tempo_bpm) is float

    object.__setattr__(cfg, "bars", 0)
    with pytest.raises(GeneratorInputError, match="bars"):
        snapshot_gen_config(cfg)


def test_generate_rejects_non_config_at_runtime() -> None:
    with pytest.raises(GeneratorInputError, match="exact GenConfig"):
        generate_leadsheet(object())  # type: ignore[arg-type]


def test_generator_version_is_public_and_versioned() -> None:
    assert GENERATOR_VERSION == "procedural-generator@0.1.0"


def _max_sounding_notes(ir: MusicIR) -> int:
    # Generated values are MusicIR; keeping this helper local makes the assertion
    # state explicitly that polyphony includes held notes, not just attacks.
    notes = ir.notes
    return max(
        sum(note.onset <= onset < note.onset + note.duration for note in notes)
        for onset in {note.onset for note in notes}
    )


def test_v2_variation_grid_is_canonical_and_exact() -> None:
    variations = procedural_variations()
    assert len(variations) == 9
    assert len(set(variations)) == 9
    assert variations[0] == ProceduralVariation("low", "low")
    assert variations[-1] == ProceduralVariation("high", "high")
    with pytest.raises(GeneratorInputError, match="synthetic_complexity"):
        ProceduralVariation("extreme", "low")  # type: ignore[arg-type]
    with pytest.raises(GeneratorInputError, match="polyphony"):
        ProceduralVariation("low", "dense")  # type: ignore[arg-type]


def test_v2_variations_are_deterministic_legal_and_full_evidence() -> None:
    cfg = GenConfig(key="D", bars=4, seed=311, tempo_bpm=104)
    for variation in procedural_variations():
        first = generate_procedural_variant(cfg, variation)
        second = generate_procedural_variant(cfg, variation)
        assert first == second
        assert validate_ir(first) == []
        assert faithfulness_dimensions(first) == ("melody", "bass_root", "harmony")
        assert GENERATOR_VERSION in first.meta.source
        assert variation.synthetic_complexity in first.meta.source
        assert variation.polyphony in first.meta.source
        assert len(first.notes) <= MAX_GENERATOR_NOTE_EVENTS


def test_v2_complexity_and_polyphony_axes_change_only_their_declared_shape() -> None:
    cfg = GenConfig(bars=4, seed=41)
    melody_counts = []
    for level in PROCEDURAL_LEVELS:
        ir = generate_procedural_variant(cfg, ProceduralVariation(level, "low"))
        melody_counts.append(sum(note.voice == "melody" for note in ir.notes))
        assert _max_sounding_notes(ir) == 2
    assert melody_counts[0] < melody_counts[1] < melody_counts[2]

    sounding_counts = []
    for level in PROCEDURAL_LEVELS:
        ir = generate_procedural_variant(cfg, ProceduralVariation("medium", level))
        sounding_counts.append(_max_sounding_notes(ir))
        assert sum(note.voice == "melody" for note in ir.notes) == melody_counts[1]
    assert sounding_counts == [2, 3, 4]
