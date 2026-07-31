"""Every corpus score has to hold notes the guitar can sound.

This guard did not exist. The corpus was rebuilt, deduplicated and re-attributed
three times in July 2026 and nineteen of its 292 scores held pitches no guitar
can produce -- thirteen entered at written pitch, five in a tuning the corpus did
not record, one with a scattered minority an octave high. Only six announced
themselves as out of range; the rest failed at whichever bucket they reached
first and were counted as beam deaths and frame-config failures, contaminating
both.

The cause is that `build_mutopia_lilypond_corpus.py` reads notes and nothing
else: no clef, no `stringTunings`, no `\\transposition`. Its generator
(`m1_lilypond.py`) is upstream and not vendored, so the corrections live in
`scripts/repair_corpus_pitches.py` as an explicit table and a rebuild would put
every defect straight back. **This test is what fails when that happens.**

It asserts producibility only -- whether the pitches exist on the instrument at
all. Whether a hand can hold them is the oracle's question and a different one.
"""

from __future__ import annotations

import json
from pathlib import Path

from fretsure.geometry import STANDARD_TUNING
from fretsure.oracle.profiles import MEDIAN_HAND

CORPUS_DIR = Path(__file__).resolve().parents[1] / "data" / "score_corpus"

# Named rather than filtered by a rule, so the exemption is as visible as the
# corrections it sits beside. See `scripts/repair_corpus_pitches.py`.
QUARANTINED = {
    "mutopia-aguado-op03n05": (
        "23 of 345 notes an octave high, scattered across the piece; a "
        "whole-score transform is wrong for it and selecting notes by their "
        "range would fit the repair to the symptom"
    ),
}


def _examples() -> list[tuple[str, dict[str, object]]]:
    out = []
    for path in sorted(CORPUS_DIR.glob("*.json")):
        if path.name.endswith("_manifest.json"):
            continue
        for example in json.loads(path.read_text(encoding="utf-8"))["examples"]:
            out.append((path.name, example))
    return out


def test_every_score_holds_pitches_the_guitar_can_sound() -> None:
    """The arithmetic, not a model: does the note exist on the instrument?"""

    offenders = []
    for filename, example in _examples():
        identity = str(example.get("id"))
        if identity in QUARANTINED:
            continue
        raw = example.get("tuning")
        tuning = (
            STANDARD_TUNING if type(raw) is not list else tuple(int(v) for v in raw)
        )
        capo = int(example.get("capo") or 0)
        pitches = [int(note["pitch"]) for note in example["notes"]]
        low = tuning[0] + capo
        high = max(tuning) + capo + MEDIAN_HAND.max_fret
        if min(pitches) < low or max(pitches) > high:
            offenders.append(
                f"{filename}:{identity} range {min(pitches)}-{max(pitches)} "
                f"outside {low}-{high}"
            )
    assert not offenders, offenders


def test_the_quarantine_is_still_needed_and_still_only_this() -> None:
    """An exemption nobody revisits becomes a permanent hole.

    So it is asserted in both directions: the quarantined score must still fail,
    and nothing else may have been added to the list without failing too.
    """

    by_id = {str(example.get("id")): example for _f, example in _examples()}
    for identity in QUARANTINED:
        example = by_id[identity]
        tuning = tuple(int(v) for v in example["tuning"])  # type: ignore[union-attr]
        pitches = [int(note["pitch"]) for note in example["notes"]]
        high = max(tuning) + int(example.get("capo") or 0) + MEDIAN_HAND.max_fret
        assert max(pitches) > high or min(pitches) < tuning[0], (
            f"{identity} now fits the instrument; remove it from QUARANTINED"
        )


def test_the_repair_table_and_the_quarantine_agree() -> None:
    """Two places name the same score, and they must not drift apart."""

    import importlib.util
    import sys

    path = CORPUS_DIR.parents[1] / "scripts" / "repair_corpus_pitches.py"
    spec = importlib.util.spec_from_file_location("repair_corpus_pitches", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["repair_corpus_pitches"] = module
    spec.loader.exec_module(module)

    assert set(module.QUARANTINED) == set(QUARANTINED)
    assert not set(module.CORRECTIONS) & set(QUARANTINED), (
        "a score cannot be both repaired and exempt from the repair"
    )
