from fretsure.bench.generator import GenConfig, generate_leadsheet
from fretsure.ir import validate_ir

_MAJOR = {0, 2, 4, 5, 7, 9, 11}
_KEY_TONIC = {"C": 0, "G": 7, "D": 2, "A": 9, "F": 5}


def test_deterministic_same_seed() -> None:
    a = generate_leadsheet(GenConfig(seed=7))
    b = generate_leadsheet(GenConfig(seed=7))
    assert a == b


def test_different_seed_differs() -> None:
    a = generate_leadsheet(GenConfig(seed=1, bars=8))
    b = generate_leadsheet(GenConfig(seed=2, bars=8))
    assert a != b


def test_generated_ir_is_legal() -> None:
    ir = generate_leadsheet(GenConfig(seed=3, bars=4))
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
