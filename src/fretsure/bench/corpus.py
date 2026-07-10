"""Corpus note-graph schema, normalizer, and datasheet.

Everything the benchmark measures is a stratified :class:`CorpusItem` (layer x
genre x difficulty). IR (de)serializes to a JSON note-graph so a run is
reproducible from seeds + a download script.
"""

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, cast

from fretsure.ir import ChordSymbol, Meta, MusicIR, Note, VoiceRole


def ir_to_notegraph(ir: MusicIR) -> dict[str, Any]:
    return {
        "meta": {
            "key": ir.meta.key,
            "time_sig": list(ir.meta.time_sig),
            "tempo_bpm": ir.meta.tempo_bpm,
            "source": ir.meta.source,
            "title": ir.meta.title,
            "license": ir.meta.license,
        },
        "notes": [
            {"onset": str(n.onset), "duration": str(n.duration), "midi": n.pitch, "voice": n.voice}
            for n in ir.notes
        ],
        "chords": [
            {
                "onset": str(c.onset),
                "symbol": c.symbol,
                "pitch_classes": sorted(c.pitch_classes),
                "root_pc": c.root_pc,
            }
            for c in ir.chords
        ],
    }


def notegraph_to_ir(obj: dict[str, Any]) -> MusicIR:
    notes = tuple(
        Note(
            Fraction(n["onset"]),
            Fraction(n["duration"]),
            int(n["midi"]),
            cast(VoiceRole, n["voice"]),
        )
        for n in obj["notes"]
    )
    chords = tuple(
        ChordSymbol(
            Fraction(c["onset"]),
            c["symbol"],
            frozenset(int(pc) for pc in c["pitch_classes"]),
            int(c["root_pc"]),
        )
        for c in obj["chords"]
    )
    m = obj["meta"]
    meta = Meta(
        m["key"],
        (int(m["time_sig"][0]), int(m["time_sig"][1])),
        float(m["tempo_bpm"]),
        m["source"],
        m["title"],
        m["license"],
    )
    return MusicIR(notes, chords, meta)


@dataclass(frozen=True)
class CorpusItem:
    ir: MusicIR
    layer: str  # e.g. "procedural", "public_leadsheet"
    genre: str
    difficulty: int
    item_id: str


def datasheet(items: list[CorpusItem]) -> dict[str, Any]:
    return {
        "count": len(items),
        "by_layer": dict(Counter(i.layer for i in items)),
        "by_genre": dict(Counter(i.genre for i in items)),
        "by_difficulty": dict(Counter(i.difficulty for i in items)),
    }
