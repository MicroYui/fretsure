from fretsure.demo import DemoResult, render_demo, run_demo, sample_ir
from fretsure.llm.client import ConstantLLM


def test_sample_ir_is_legal_and_has_chords() -> None:
    from fretsure.ir import validate_ir

    ir = sample_ir()
    assert validate_ir(ir) == []
    assert ir.chords and any(n.voice == "melody" for n in ir.notes)


def test_demo_stub_produces_provably_playable_tab() -> None:
    ir = sample_ir()
    demo = run_demo(ir, ConstantLLM("noop"), n=2)
    assert isinstance(demo, DemoResult)
    assert demo.result.tab is not None
    assert demo.result.oracle is not None and demo.result.oracle.verdict == "GREEN"
    assert demo.gate is not None and demo.gate.passed


def test_demo_is_deterministic_offline() -> None:
    ir = sample_ir()
    a = run_demo(ir, ConstantLLM("noop"), n=2)
    b = run_demo(ir, ConstantLLM("noop"), n=2)
    assert a.result.tab == b.result.tab


def test_render_demo_has_expected_sections() -> None:
    ir = sample_ir()
    demo = run_demo(ir, ConstantLLM("noop"), n=2)
    text = render_demo(demo, ir, engine="stub")
    for section in ("INPUT", "ARRANGED TAB", "ORACLE VERDICT", "GREEN", "WHAT THIS PROVES"):
        assert section in text
    # the ASCII tab renders 6 strings, each prefixed with a name + bar
    assert text.count("|") >= 12
