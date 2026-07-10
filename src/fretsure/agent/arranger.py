"""LLM arrangement proposer.

The LLM decides musical intent only — which notes carry the melody/bass/harmony,
in which octaves — and emits a target note set as JSON. It never decides fingering
(the deterministic solver does). On any malformed reply we fall back honestly to
the Plan 2 rule stub, so the pipeline always has a target.
"""

from dataclasses import dataclass, field
from fractions import Fraction
from typing import cast

from fretsure.arrange.propose import propose_fingerstyle
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import MusicIR, Note, VoiceRole
from fretsure.llm.client import LLMClient, extract_json

_ARRANGE_SYSTEM = (
    "You are a fingerstyle guitar arranger. Given a lead sheet, decide the musical "
    "intent for a solo fingerstyle arrangement: which notes carry the melody (always "
    "kept, as the top voice), which carry the bass, and optional inner 'harmony' notes. "
    "You decide notes and octaves, NOT fingering. Reply with ONLY a JSON object: "
    '{"notes": [{"onset": "<fraction>", "duration": "<fraction>", "pitch": <midi>, '
    '"voice": "melody|bass|harmony"}, ...]}. The melody must be present.'
)


@dataclass(frozen=True)
class ArrangeGoal:
    style: str = "fingerstyle"
    tier: str = "intermediate"
    tuning: tuple[int, ...] = STANDARD_TUNING
    capo: int = 0
    tempo_bpm: float = 90.0
    extras: dict[str, str] = field(default_factory=dict)


def _ir_summary(ir: MusicIR) -> str:
    mel = [n for n in ir.notes if n.voice == "melody"]
    mel_rows = "; ".join(f"onset={n.onset} pitch={n.pitch}" for n in mel[:64])
    chords = "; ".join(f"onset={c.onset} {c.symbol}" for c in ir.chords[:32])
    return (
        f"Key {ir.meta.key}, {ir.meta.time_sig[0]}/{ir.meta.time_sig[1]}, "
        f"tempo {ir.meta.tempo_bpm}.\nMelody: {mel_rows}\nChords: {chords}"
    )


def _parse_notes(obj: dict[str, object]) -> tuple[Note, ...]:
    raw = obj["notes"]
    if not isinstance(raw, list):
        raise ValueError("notes must be a list")
    notes: list[Note] = []
    for item in raw:
        voice = item["voice"]
        if voice not in ("melody", "bass", "harmony"):
            raise ValueError(f"bad voice {voice!r}")
        notes.append(
            Note(
                Fraction(str(item["onset"])),
                Fraction(str(item["duration"])),
                int(item["pitch"]),
                cast(VoiceRole, voice),
            )
        )
    return tuple(sorted(notes, key=lambda n: (n.onset, n.pitch)))


def propose_arrangement(
    ir: MusicIR, goal: ArrangeGoal, llm: LLMClient, *, temperature: float = 0.0
) -> tuple[Note, ...]:
    low = min(goal.tuning)
    high = max(goal.tuning) + 22
    user = (
        f"{_ir_summary(ir)}\n\nPlayable range on this tuning: MIDI {low}-{high} "
        f"(the lowest playable note is {low}; never write a note below {low}). "
        f"Keep at most 4 notes sounding at the same onset. "
        f"Goal: {goal.style}, {goal.tier} difficulty. Produce the target note set now."
    )
    try:
        reply = llm.complete(
            system=_ARRANGE_SYSTEM, user=user, temperature=temperature, max_tokens=2048
        )
        notes = _parse_notes(extract_json(reply))
        if not any(n.voice == "melody" for n in notes):
            raise ValueError("proposal has no melody")
        return notes
    except (ValueError, KeyError, TypeError):
        return propose_fingerstyle(ir)  # honest deterministic fallback
