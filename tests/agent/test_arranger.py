from fractions import Fraction as F

import pytest

from fretsure.agent.arranger import (
    ArrangeGoal,
    ArrangementCapacityError,
    propose_arrangement,
)
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import ChordSymbol, Meta, MusicIR, Note
from fretsure.llm.client import ConstantLLM, FakeLLM


def _meta() -> Meta:
    return Meta("C", (4, 4), 90.0, "t", "t", "PD")


def _leadsheet() -> MusicIR:
    return MusicIR(
        (Note(F(0), F(1), 64, "melody"), Note(F(0), F(1), 40, "bass")),
        (ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 0),),
        _meta(),
    )


_VALID = (
    '{"notes": [{"onset":"0","duration":"1","pitch":64,"voice":"melody"},'
    '{"onset":"0","duration":"1","pitch":47,"voice":"bass"},'
    '{"onset":"0","duration":"1","pitch":55,"voice":"harmony"}]}'
)


def test_parses_valid_arrangement_with_melody() -> None:
    notes = propose_arrangement(_leadsheet(), ArrangeGoal(), FakeLLM([_VALID]))
    pitches = [n.pitch for n in notes]
    assert 64 in pitches and 55 in pitches
    assert any(n.voice == "melody" for n in notes)


def test_bad_json_falls_back_to_rule_stub() -> None:
    notes = propose_arrangement(_leadsheet(), ArrangeGoal(), FakeLLM(["not json at all"]))
    # fallback = propose_fingerstyle -> melody + bass from the IR
    pitches = {n.pitch for n in notes}
    assert 64 in pitches and 40 in pitches


def test_no_melody_in_reply_falls_back() -> None:
    reply = '{"notes": [{"onset":"0","duration":"1","pitch":40,"voice":"bass"}]}'
    notes = propose_arrangement(_leadsheet(), ArrangeGoal(), FakeLLM([reply]))
    assert any(n.voice == "melody" for n in notes)  # fallback restores melody


@pytest.mark.parametrize(
    "reply",
    [
        '{"notes":[{"onset":"-1","duration":"1","pitch":64,"voice":"melody"}]}',
        '{"notes":[{"onset":"0","duration":"0","pitch":64,"voice":"melody"}]}',
        '{"notes":[{"onset":"0","duration":"1","pitch":128,"voice":"melody"}]}',
        '{"notes":[{"onset":"0","duration":"1","pitch":64.5,"voice":"melody"}]}',
        (
            '{"notes":['
            '{"onset":"0","duration":"1","pitch":64,"voice":"melody"},'
            '{"onset":"0","duration":"2","pitch":64,"voice":"harmony"}'
            "]}"
        ),
    ],
)
def test_invalid_llm_note_domain_falls_back_before_solver(reply: str) -> None:
    notes = propose_arrangement(_leadsheet(), ArrangeGoal(), FakeLLM([reply]))

    assert notes == propose_arrangement(
        _leadsheet(), ArrangeGoal(), ConstantLLM("noop")
    )


def test_prompt_contains_every_melody_duration_and_chord_without_truncation() -> None:
    notes = tuple(
        Note(F(i), F(3, 2), 60 + (i % 12), "melody") for i in range(65)
    )
    chords = tuple(
        ChordSymbol(F(i), f"C{i}", frozenset({0, 4, 7}), 0) for i in range(33)
    )
    ir = MusicIR(notes, chords, _meta())
    llm = FakeLLM([_VALID])

    propose_arrangement(ir, ArrangeGoal(), llm)

    prompt = llm.calls[0]["user"]
    assert "onset=0 duration=3/2 pitch=60" in prompt
    assert "onset=64 duration=3/2 pitch=64" in prompt
    assert "onset=32 C32" in prompt
    assert prompt.count("duration=3/2") == 65
    assert llm.calls[0]["max_tokens"] > 2048


def test_prompt_playable_range_accounts_for_capo() -> None:
    llm = FakeLLM([_VALID])

    propose_arrangement(_leadsheet(), ArrangeGoal(capo=2, tempo_bpm=72.0), llm)

    assert "Playable range on this tuning: MIDI 42-88" in llm.calls[0]["user"]
    assert "source tempo 90.0 BPM" in llm.calls[0]["user"]
    assert "Effective arrangement tempo: 72.0 BPM" in llm.calls[0]["user"]


def test_real_llm_path_rejects_unrepresentable_input_instead_of_truncating() -> None:
    notes = tuple(Note(F(i), F(1), 60 + (i % 12), "melody") for i in range(170))
    ir = MusicIR(notes, (), _meta())
    llm = FakeLLM([_VALID])

    with pytest.raises(ArrangementCapacityError, match="chunking is deferred"):
        propose_arrangement(ir, ArrangeGoal(), llm)
    assert llm.calls == []


def test_deterministic_path_supports_input_beyond_real_llm_single_call_capacity() -> None:
    notes = tuple(Note(F(i), F(1), 60 + (i % 12), "melody") for i in range(170))
    ir = MusicIR(notes, (), _meta())
    assert propose_arrangement(ir, ArrangeGoal(), ConstantLLM("noop")) == notes


@pytest.mark.integration
def test_real_llm_proposes_parseable_arrangement() -> None:
    import os

    if not os.environ.get("ANTHROPIC_BASE_URL"):
        pytest.skip("no local LLM proxy configured")
    from fretsure.llm.client import ProxyLLM

    notes = propose_arrangement(_leadsheet(), ArrangeGoal(tuning=STANDARD_TUNING), ProxyLLM())
    assert any(n.voice == "melody" for n in notes)
