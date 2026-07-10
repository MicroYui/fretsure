"""M0 arrangement proposer.

A deterministic **rule stub**: keep the melody and bass voices, drop harmony, so
the M0 slice produces the simplest playable fingerstyle skeleton. The real LLM
arranger (voicing, inner-voice density, texture) arrives in Plan 3.
"""

from fretsure.ir import MusicIR, Note


def propose_fingerstyle(ir: MusicIR) -> tuple[Note, ...]:
    kept = [n for n in ir.notes if n.voice in ("melody", "bass")]
    kept.sort(key=lambda n: (n.onset, n.pitch))
    return tuple(kept)
