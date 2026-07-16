"""Musicality critic — the one genuine second agent.

The oracle cannot judge whether an arrangement is *good*; this LLM critic rates
voice leading, bass motion, and texture. It judges TASTE only, never playability
(that stays with the deterministic oracle). Malformed output degrades to a
neutral 0.5 so it can never block the pipeline.
"""

from dataclasses import dataclass
from typing import Any

from fretsure.ir import MusicIR, snapshot_music_ir
from fretsure.llm.client import LLMClient, extract_json
from fretsure.render.ascii import render_ascii
from fretsure.tab import Tab

_CRITIC_SYSTEM = (
    "You are a guitar-arrangement critic. Judge ONLY musicality — voice leading, bass "
    "motion, texture — NOT playability (a separate deterministic oracle handles that). "
    'Reply with ONLY JSON: {"overall": 0..1, "voice_leading": 0..1, "bass_motion": 0..1, '
    '"texture": 0..1, "notes": "<one sentence>"}.'
)


@dataclass(frozen=True)
class CriticScore:
    overall: float
    voice_leading: float
    bass_motion: float
    texture: float
    notes: str


def _clamp01(value: Any) -> float:
    return max(0.0, min(1.0, float(value)))


def critique(ir: MusicIR, tab: Tab, llm: LLMClient) -> CriticScore:
    ir = snapshot_music_ir(ir)
    user = f"Key {ir.meta.key}. Arrangement tab:\n{render_ascii(tab)}\n\nRate its musicality."
    try:
        obj = extract_json(llm.complete(system=_CRITIC_SYSTEM, user=user, max_tokens=512))
        overall = _clamp01(obj["overall"])
        return CriticScore(
            overall=overall,
            voice_leading=_clamp01(obj.get("voice_leading", overall)),
            bass_motion=_clamp01(obj.get("bass_motion", overall)),
            texture=_clamp01(obj.get("texture", overall)),
            notes=str(obj.get("notes", "")),
        )
    except (ValueError, KeyError, TypeError, RuntimeError):
        return CriticScore(0.5, 0.5, 0.5, 0.5, "unparsed critic output; neutral score")
