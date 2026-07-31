"""Whether the corpus holds music the instrument can produce at all.

A gate number is meaningless for a score whose pitches no guitar can sound. Two
ways that happens here, and they need different repairs, so the audit must not
report them as one number:

* **written pitch** — guitar is notated an octave above sounding pitch, LilyPond
  note names are sounding pitch, and engravers routinely type what they see. The
  converter never consults the clef, so those scores arrive twelve semitones high.
* **unrecorded tuning** — Capricho Arabe is in drop D and always has been. The
  notes are right and the corpus recorded the wrong instrument.

These pin the classification, not the counts, because the counts change the day
someone repairs the corpus and the classification must not.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
STANDARD = [40, 45, 50, 55, 59, 64]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "audit_corpus_range", ROOT / "scripts" / "audit_corpus_range.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["audit_corpus_range"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _script()


def _example(pitches: list[int], tuning: list[int] | None = None) -> dict[str, object]:
    return {
        "id": "x",
        "split": "train",
        "capo": 0,
        "tuning": tuning or STANDARD,
        "notes": [
            {"onset": [i, 1], "duration": [1, 1], "pitch": pitch, "voice": "melody"}
            for i, pitch in enumerate(pitches)
        ],
    }


def test_music_the_guitar_can_sound_is_not_reported(script: ModuleType) -> None:
    """Anything inside the fretboard is not this audit's business.

    The audit asks only whether the pitches exist on the instrument. Whether a
    hand can reach them is the oracle's question and a different one.
    """

    assert script.audit_example(_example([40, 64, 86]), 22) is None


def test_a_score_an_octave_high_is_named_as_written_pitch(script: ModuleType) -> None:
    """The signature: unplayable as recorded, exact on the instrument twelve down.

    Thirteen corpus scores match it, minimum 52 and maximum 88 in nine of them --
    the open low E and a high E, both exactly an octave up.
    """

    row = script.audit_example(_example([52, 70, 88]), 22)
    assert row is not None
    assert row["verdict"] == "written pitch: fits exactly an octave down"
    assert row["above"] == 1


def test_a_score_below_the_open_e_is_named_as_a_tuning(script: ModuleType) -> None:
    """Drop D is a fact about the instrument, not about the notes.

    Reporting it as the same defect as an octave error would send someone to
    transpose Capricho Arabe, which is in drop D and correct as written.
    """

    row = script.audit_example(_example([38, 60, 79]), 22)
    assert row is not None
    assert row["verdict"] == "unrecorded tuning: fits with the sixth string down 2"
    assert row["below"] == 1


def test_a_score_needing_both_says_both(script: ModuleType) -> None:
    """One repair is not the other, and a score can need each."""

    row = script.audit_example(_example([50, 88]), 22)
    assert row is not None
    assert row["verdict"] == "written pitch and sixth string down 2"


def test_a_score_that_no_guitar_produces_is_not_called_a_defect(
    script: ModuleType,
) -> None:
    """Some transcriptions are simply for another instrument.

    Calling those an import defect is what the retracted first frame-config
    analysis did with thirteen pieces, and it sent the work in the wrong
    direction for a week.
    """

    row = script.audit_example(_example([39, 98]), 22)
    assert row is not None
    assert row["verdict"].endswith("not a guitar score")
