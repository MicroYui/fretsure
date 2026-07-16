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
from fretsure.ir import MusicIR, Note, VoiceRole, snapshot_music_ir
from fretsure.llm.client import ConstantLLM, LLMClient, extract_json
from fretsure.oracle.input import ensure_solver_domain
from fretsure.oracle.profiles import MEDIAN_HAND, Profile

_ARRANGE_SYSTEM = (
    "You are a fingerstyle guitar arranger. Given a lead sheet, decide the musical "
    "intent for a solo fingerstyle arrangement: which notes carry the melody (always "
    "kept, as the top voice), which carry the bass, and optional inner 'harmony' notes. "
    "You decide notes and octaves, NOT fingering. Reply with ONLY a JSON object: "
    '{"notes": [{"onset": "<fraction>", "duration": "<fraction>", "pitch": <midi>, '
    '"voice": "melody|bass|harmony"}, ...]}. The melody must be present.'
)

MIN_OUTPUT_TOKENS = 2048
MAX_OUTPUT_TOKENS = 16_384


class ArrangementCapacityError(ValueError):
    """A real-LLM proposal cannot faithfully encode every legal source event."""


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
    bass = [n for n in ir.notes if n.voice == "bass"]
    harmony = [n for n in ir.notes if n.voice == "harmony"]

    def events(notes: list[Note]) -> str:
        return "; ".join(
            f"onset={n.onset} duration={n.duration} pitch={n.pitch}" for n in notes
        )

    chords = "; ".join(
        f"onset={c.onset} {c.symbol} root_pc={c.root_pc} "
        f"pitch_classes={','.join(str(pc) for pc in sorted(c.pitch_classes))}"
        for c in ir.chords
    )
    return (
        f"Key {ir.meta.key}, {ir.meta.time_sig[0]}/{ir.meta.time_sig[1]}, "
        f"source tempo {ir.meta.tempo_bpm} BPM.\n"
        f"Melody events: {events(mel)}\n"
        f"Source bass events: {events(bass)}\n"
        f"Source harmony-note events: {events(harmony)}\n"
        f"Chord annotations: {chords}"
    )


def _parse_notes(obj: dict[str, object]) -> tuple[Note, ...]:
    raw = obj["notes"]
    if not isinstance(raw, list):
        raise ValueError("notes must be a list")
    notes: list[Note] = []
    seen: set[tuple[Fraction, int]] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each note must be an object")
        voice = item["voice"]
        if voice not in ("melody", "bass", "harmony"):
            raise ValueError(f"bad voice {voice!r}")
        onset = Fraction(str(item["onset"]))
        duration = Fraction(str(item["duration"]))
        raw_pitch = item["pitch"]
        if not isinstance(raw_pitch, int) or isinstance(raw_pitch, bool):
            raise ValueError(f"pitch must be an integer MIDI value, got {raw_pitch!r}")
        if onset < 0:
            raise ValueError(f"onset must be non-negative, got {onset}")
        if duration <= 0:
            raise ValueError(f"duration must be positive, got {duration}")
        if not 0 <= raw_pitch <= 127:
            raise ValueError(f"pitch must be in MIDI range 0..127, got {raw_pitch}")
        identity = (onset, raw_pitch)
        if identity in seen:
            raise ValueError(
                f"duplicate pitch {raw_pitch} at onset {onset} is ambiguous"
            )
        seen.add(identity)
        notes.append(Note(onset, duration, raw_pitch, cast(VoiceRole, voice)))
    return tuple(sorted(notes, key=lambda n: (n.onset, n.pitch)))


def _output_token_budget(ir: MusicIR) -> int:
    """Budget a full structured reply for every legal input event.

    The old fixed 2k budget could truncate an otherwise-valid long arrangement.
    Imported resource limits bound the request; this scales generously with all
    source notes and chord annotations while retaining a provider-safe ceiling.
    """
    events = len(ir.notes) + len(ir.chords)
    required = max(MIN_OUTPUT_TOKENS, 128 + 96 * events)
    if required > MAX_OUTPUT_TOKENS:
        max_events = (MAX_OUTPUT_TOKENS - 128) // 96
        raise ArrangementCapacityError(
            f"real-LLM arrangement supports at most {max_events} source note/chord "
            f"events per request, got {events}; use the deterministic path. "
            "Long-score LLM chunking is deferred (the input was not truncated)"
        )
    return required


def ensure_llm_capacity(ir: MusicIR) -> None:
    """Raise a typed error rather than silently truncating a real-LLM request."""
    ir = snapshot_music_ir(ir)
    _output_token_budget(ir)


def propose_arrangement(
    ir: MusicIR,
    goal: ArrangeGoal,
    llm: LLMClient,
    *,
    temperature: float = 0.0,
    profile: Profile = MEDIAN_HAND,
) -> tuple[Note, ...]:
    ir = snapshot_music_ir(ir)
    notes, tuning, capo, profile, tempo_bpm = ensure_solver_domain(
        ir.notes,
        goal.tuning,
        goal.capo,
        profile,
        tempo_bpm=goal.tempo_bpm,
    )
    ir = MusicIR(notes, tuple(ir.chords), ir.meta)
    goal = ArrangeGoal(
        style=goal.style,
        tier=goal.tier,
        tuning=tuning,
        capo=capo,
        tempo_bpm=tempo_bpm,
        extras=goal.extras,
    )
    # ``ConstantLLM`` is the documented offline switch that previously reached
    # the same rule path via malformed JSON.  Dispatch directly so the non-LLM
    # vertical slice continues to accept the importer's much larger resource
    # envelope without constructing an enormous prompt.
    if isinstance(llm, ConstantLLM):
        return propose_fingerstyle(
            ir,
            goal.tuning,
            goal.capo,
            profile=profile,
            tempo_bpm=goal.tempo_bpm,
        )
    max_tokens = _output_token_budget(ir)
    low = min(goal.tuning) + goal.capo
    high = max(goal.tuning) + goal.capo + 22
    user = (
        f"{_ir_summary(ir)}\n"
        f"Effective arrangement tempo: {goal.tempo_bpm} BPM.\n\n"
        f"Playable range on this tuning: MIDI {low}-{high} "
        f"(the lowest playable note is {low}; never write a note below {low}). "
        f"Keep at most 4 notes sounding at the same onset. "
        f"Goal: {goal.style}, {goal.tier} difficulty. Produce the target note set now."
    )
    try:
        reply = llm.complete(
            system=_ARRANGE_SYSTEM,
            user=user,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        notes = _parse_notes(extract_json(reply))
        if not any(n.voice == "melody" for n in notes):
            raise ValueError("proposal has no melody")
        return notes
    except (ValueError, KeyError, TypeError, RuntimeError):
        return propose_fingerstyle(
            ir,
            goal.tuning,
            goal.capo,
            profile=profile,
            tempo_bpm=goal.tempo_bpm,
        )  # honest deterministic fallback
