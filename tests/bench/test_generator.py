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
