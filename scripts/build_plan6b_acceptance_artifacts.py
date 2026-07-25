"""Build same-checkpoint Plan 6B files for desktop interoperability receipts."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from xml.etree import ElementTree

import guitarpro  # type: ignore[import-untyped]
from pypdf import PdfReader

from fretsure.application import ArrangeOptions, arrange_score_bytes
from fretsure.audio import fluidsynth_version, render_wav
from fretsure.llm.client import ConstantLLM
from fretsure.render.guitar_pro import render_guitar_pro
from fretsure.render.midi import render_midi
from fretsure.render.musicxml_tab import render_musicxml_tab
from fretsure.render.pdf_tab import render_tab_pdf
from fretsure.tab import tab_to_json

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/fixtures/musicxml/supported_basic.musicxml"
OUTPUT = ROOT / "artifacts/plan6b-interoperability"


def _record(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {
        "filename": path.name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "structural_check": "PASS",
    }


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    outcome = arrange_score_bytes(
        SOURCE.read_bytes(),
        filename=SOURCE.name,
        options=ArrangeOptions(),
        llm=ConstantLLM("noop"),
    )
    if outcome.tab is None:
        raise RuntimeError("acceptance fixture produced no canonical Tab")
    tab = outcome.tab
    tempo = outcome.effective_tempo_bpm
    files = {
        "musicxml": OUTPUT / "fretsure-acceptance.musicxml",
        "gp5": OUTPUT / "fretsure-acceptance.gp5",
        "pdf": OUTPUT / "fretsure-acceptance.pdf",
        "midi": OUTPUT / "fretsure-acceptance.mid",
        "wav": OUTPUT / "fretsure-acceptance.wav",
        "tab_json": OUTPUT / "fretsure-acceptance.tab.json",
    }
    files["musicxml"].write_bytes(render_musicxml_tab(tab, tempo_bpm=tempo))
    files["gp5"].write_bytes(render_guitar_pro(tab, tempo_bpm=tempo))
    files["pdf"].write_bytes(render_tab_pdf(tab, tempo_bpm=tempo))
    files["midi"].write_bytes(render_midi(tab, tempo_bpm=tempo))
    files["wav"].write_bytes(render_wav(tab, tempo_bpm=tempo))
    files["tab_json"].write_text(tab_to_json(tab), encoding="utf-8")

    ElementTree.fromstring(files["musicxml"].read_bytes())
    guitarpro.parse(str(files["gp5"]))
    PdfReader(files["pdf"])
    if not files["midi"].read_bytes().startswith(b"MThd"):
        raise RuntimeError("generated MIDI has no SMF header")
    wav = files["wav"].read_bytes()
    if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise RuntimeError("generated audio has no WAV header")
    json.loads(files["tab_json"].read_text(encoding="utf-8"))

    manifest = {
        "schema_version": "plan6b-interoperability-manifest@0.1.0",
        "checkpoint": {
            "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
            "tab_sha256": hashlib.sha256(tab_to_json(tab).encode("utf-8")).hexdigest(),
            "tempo_bpm": tempo,
            "oracle_verdict": outcome.oracle.verdict if outcome.oracle else None,
            "model_id": outcome.model_id,
        },
        "audio_runtime": {
            "renderer": "FluidSynth",
            "runtime_version": fluidsynth_version(),
        },
        "files": {name: _record(path) for name, path in files.items()},
        "desktop_receipts": {
            "musicxml": "PENDING",
            "gp5": "PENDING",
            "gp7": "PENDING",
        },
        "human_guitarist_receipt": "PENDING",
    }
    (OUTPUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(OUTPUT)


if __name__ == "__main__":
    main()
