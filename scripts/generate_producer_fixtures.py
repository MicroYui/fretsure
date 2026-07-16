"""Generate unedited MusicXML exports used by the producer-provenance gate.

Run with the exact exporter versions recorded below and a fresh, untracked
destination::

    uv run --isolated --with music21==10.5.0 --with musicxml==1.6.1 \
      python scripts/generate_producer_fixtures.py \
      --output-dir /tmp/fretsure-producer-exports

The optional MuseScore export uses the external ``mscore`` executable when it is
available.  The checked-in fixtures are immutable audit evidence: this script
refuses to write into their directory and refuses to overwrite any destination.
Copying a newly reviewed raw export into the frozen corpus is a deliberate
maintainer action, followed by a manifest/hash update and code review.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import musicxml as mx
from music21 import harmony, instrument, key, metadata, meter, note, stream, tempo

ROOT = Path(__file__).resolve().parents[1]
FROZEN_OUTPUT = ROOT / "tests" / "fixtures" / "producers"
MUSIC21_VERSION = "10.5.0"
MUSICXML_VERSION = "1.6.1"
MUSESCORE_VERSION = "4.7.4"


def _require_version(package: str, expected: str) -> None:
    actual = importlib.metadata.version(package)
    if actual != expected:
        raise RuntimeError(f"{package} fixture requires {expected}, found {actual}")


def _write_music21(path: Path) -> None:
    score = stream.Score(id="FretsureProducerFixture")
    score.metadata = metadata.Metadata()
    score.metadata.title = "Fretsure Producer Etude"
    score.metadata.composer = "Fretsure"
    score.metadata.copyright = "CC0-1.0"
    part = stream.Part(id="P1")
    part.partName = "Melody"
    part_instrument = instrument.Instrument()
    part_instrument.partId = "FretsureP1"
    part_instrument.partName = "Melody"
    part.insert(0, part_instrument)
    measure = stream.Measure(number=1)
    measure.insert(0, key.Key("a"))
    measure.insert(0, meter.TimeSignature("4/4"))
    measure.insert(
        0,
        tempo.MetronomeMark(
            number=96,
            referent=note.Note(type="quarter"),
        ),
    )
    measure.insert(0, harmony.ChordSymbol("Am"))
    for pitch in ("A4", "C5", "E5", "A5"):
        measure.append(note.Note(pitch, quarterLength=1))
    part.append(measure)
    score.insert(0, part)
    score.write("musicxml", fp=path)


def _write_musicxml_library(path: Path) -> None:
    score = mx.XMLScorePartwise(version="4.0")
    work = score.add_child(mx.XMLWork())
    work.add_child(mx.XMLWorkTitle("Fretsure XML Library Etude"))
    identification = score.add_child(mx.XMLIdentification())
    identification.add_child(mx.XMLCreator("Fretsure", type="composer"))
    identification.add_child(mx.XMLRights("CC0-1.0"))
    encoding = identification.add_child(mx.XMLEncoding())
    encoding.add_child(mx.XMLSoftware(f"musicxml {MUSICXML_VERSION}"))

    part_list = score.add_child(mx.XMLPartList())
    score_part = part_list.add_child(mx.XMLScorePart(id="P1"))
    score_part.add_child(mx.XMLPartName("Melody"))
    part = score.add_child(mx.XMLPart(id="P1"))
    measure = part.add_child(mx.XMLMeasure(number="1"))

    attributes = measure.add_child(mx.XMLAttributes())
    # The MusicXML 4.0 type is decimal, and this exporter deliberately emits
    # decimal lexical forms.  The fixture guards exact Fraction handling.
    attributes.add_child(mx.XMLDivisions(1.0))
    key_element = attributes.add_child(mx.XMLKey())
    key_element.add_child(mx.XMLFifths(0))
    key_element.add_child(mx.XMLMode("minor"))
    time_element = attributes.add_child(mx.XMLTime())
    time_element.add_child(mx.XMLBeats("4"))
    time_element.add_child(mx.XMLBeatType("4"))
    clef = attributes.add_child(mx.XMLClef())
    clef.add_child(mx.XMLSign("G"))
    clef.add_child(mx.XMLLine(2))

    direction = measure.add_child(mx.XMLDirection(placement="above"))
    direction_type = direction.add_child(mx.XMLDirectionType())
    metronome = direction_type.add_child(mx.XMLMetronome())
    metronome.add_child(mx.XMLBeatUnit("quarter"))
    metronome.add_child(mx.XMLPerMinute("96"))
    direction.add_child(mx.XMLSound(tempo=96.0))

    harmony_element = measure.add_child(mx.XMLHarmony())
    root = harmony_element.add_child(mx.XMLRoot())
    root.add_child(mx.XMLRootStep("A"))
    harmony_element.add_child(mx.XMLKind("minor"))

    for step, octave in (("A", 4), ("C", 5), ("E", 5), ("A", 5)):
        note_element = measure.add_child(mx.XMLNote())
        pitch = note_element.add_child(mx.XMLPitch())
        pitch.add_child(mx.XMLStep(step))
        pitch.add_child(mx.XMLOctave(octave))
        note_element.add_child(mx.XMLDuration(1.0))
        note_element.add_child(mx.XMLVoice("1"))
        note_element.add_child(mx.XMLType("quarter"))

    score.write(path, intelligent_choice=True)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="new, empty, untracked directory for raw reproduction output",
    )
    parser.add_argument(
        "--musescore",
        default=shutil.which("mscore"),
        help="MuseScore CLI; omit the negative producer fixture when unavailable",
    )
    args = parser.parse_args()
    _require_version("music21", MUSIC21_VERSION)
    _require_version("musicxml", MUSICXML_VERSION)
    output = args.output_dir.expanduser().resolve()
    frozen = FROZEN_OUTPUT.resolve()
    if output == frozen or frozen in output.parents:
        raise RuntimeError("refusing to overwrite the frozen producer-fixture corpus")
    if output.exists():
        raise RuntimeError(f"output directory already exists: {output}")
    output.mkdir(parents=True)

    music21_path = output / "music21-10.5.0.musicxml"
    musicxml_path = output / "musicxml-1.6.1.musicxml"
    _write_music21(music21_path)
    _write_musicxml_library(musicxml_path)

    fixtures: list[dict[str, str]] = [
        {
            "file": music21_path.name,
            "producer": "music21",
            "producer_class": "symbolic-music-toolkit",
            "version": MUSIC21_VERSION,
            "producer_license": "BSD-3-Clause",
            "score_license": "CC0-1.0",
            "expected": "success",
            "sha256": _sha256(music21_path),
        },
        {
            "file": musicxml_path.name,
            "producer": "musicxml",
            "producer_class": "object-model-library",
            "version": MUSICXML_VERSION,
            "producer_license": "MIT",
            "score_license": "CC0-1.0",
            "expected": "success",
            "sha256": _sha256(musicxml_path),
        },
    ]

    if args.musescore:
        muse_path = output / "musescore-4.7.4.musicxml"
        completed = subprocess.run(
            [args.musescore, "-o", str(muse_path), str(music21_path)],
            check=False,
        )
        # MuseScore 4.7.4 on this arm64 host sometimes aborts during GUI-runtime
        # teardown *after* completing a valid headless export.  Never accept a
        # generic failed process: require a complete XML document carrying the
        # exact exporter identity before preserving that result and exit code.
        try:
            ET.parse(muse_path)
            muse_bytes = muse_path.read_bytes()
        except (OSError, ET.ParseError) as exc:
            raise RuntimeError(
                f"MuseScore export failed with {completed.returncode} and no valid output"
            ) from exc
        if f"MuseScore Studio {MUSESCORE_VERSION}".encode() not in muse_bytes:
            raise RuntimeError("MuseScore output does not carry the expected exporter version")
        fixtures.append(
            {
                "file": muse_path.name,
                "producer": "MuseScore Studio",
                "producer_class": "notation-application",
                "version": MUSESCORE_VERSION,
                "producer_license": "GPL-3.0-only (external exporter only)",
                "score_license": "CC0-1.0",
                "expected": "UNSUPPORTED_KEY",
                "export_exit_code": str(completed.returncode),
                "sha256": _sha256(muse_path),
            }
        )

    manifest = {
        "schema": "fretsure-producer-fixtures@0.2.0",
        "note": (
            "Files are unedited exporter output. Success is observed only for the exact "
            "fixture and version; the expected field also preserves negative "
            "notation-application evidence. GPL software is not a project dependency."
        ),
        "fixtures": fixtures,
    }
    (output / "provenance.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
