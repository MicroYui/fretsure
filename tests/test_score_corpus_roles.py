"""Which line is the bass, when the engraver wrote more than two voices.

`bass` is a monophonic role: the sustain model gives it a floor of half its
written value that inner voices do not get, so that a chord's root is still
sounding when the chord arrives -- reasoning that depends on the labelled bass
actually being the lowest note. The importer nonetheless assigned the role to
*every* note outside the engraver's primary voice, so a score written as three
or four LilyPond voices had its inner parts filed as bass. Across the corpus
that put chords in a monophonic role at 2,867 onsets in two thirds of the
pieces.

Nothing failed. The existing corpus tests use a two-voice fixture, where "not
the primary voice" and "the bass line" happen to coincide, so they passed
straight through the defect. These use three.
"""

from __future__ import annotations

from fractions import Fraction

from fretsure.score_corpus import ScoreCorpusMetadata, parse_score_corpus_source

_HEADER = b"""<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list><score-part id="P1"><part-name>Guitar</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>1</divisions>
        <time><beats>4</beats><beat-type>4</beat-type></time>
      </attributes>
"""
_FOOTER = b"""    </measure>
  </part>
</score-partwise>
"""


def _note(step: str, octave: int, voice: int, duration: int = 4) -> bytes:
    return (
        f"      <note><pitch><step>{step}</step><octave>{octave}</octave></pitch>"
        f"<duration>{duration}</duration><voice>{voice}</voice></note>\n"
    ).encode()


def _backup(duration: int = 4) -> bytes:
    return f"      <backup><duration>{duration}</duration></backup>\n".encode()


def _metadata() -> ScoreCorpusMetadata:
    return ScoreCorpusMetadata(
        id="roles",
        work_id="work",
        group_id="group",
        title="Study",
        composer="Composer",
        edition="Reviewed edition",
        source_url="https://example.test/roles.musicxml",
        license="Public Domain",
    )


def _roles(source: bytes) -> dict[int, str]:
    example = parse_score_corpus_source(source, "roles.musicxml", _metadata())
    return {note.pitch: note.voice for note in example.notes}


def test_only_the_lowest_of_the_accompanying_voices_is_the_bass() -> None:
    """Three voices at one onset: melody on top, one bass, one inner voice.

    Before this rule both lower voices were called bass, which is a chord in a
    role that is supposed to name a single line.
    """

    source = (
        _HEADER
        + _note("E", 5, voice=1)  # melody
        + _backup()
        + _note("C", 4, voice=2)  # inner voice
        + _backup()
        + _note("C", 3, voice=3)  # the actual bass
        + _FOOTER
    )
    assert _roles(source) == {76: "melody", 60: "harmony", 48: "bass"}


def test_a_two_voice_score_is_unchanged() -> None:
    """The shape the old rule got right has to keep working.

    Most of the corpus is written this way, so a fix that moved these labels
    would be rewriting the majority of the data to repair a minority of it.
    """

    source = (
        _HEADER
        + _note("E", 5, voice=1)
        + _backup()
        + _note("C", 3, voice=2)
        + _FOOTER
    )
    assert _roles(source) == {76: "melody", 48: "bass"}


def test_the_bass_stays_one_note_however_many_voices_accompany() -> None:
    source = (
        _HEADER
        + _note("E", 5, voice=1)
        + _backup()
        + _note("G", 4, voice=2)
        + _backup()
        + _note("E", 4, voice=3)
        + _backup()
        + _note("C", 3, voice=4)
        + _FOOTER
    )
    roles = _roles(source)
    assert sum(role == "bass" for role in roles.values()) == 1
    assert roles[48] == "bass"
    assert roles[67] == roles[64] == "harmony"


def test_an_onset_the_melody_does_not_attack_is_left_without_one() -> None:
    """The rule this test rules out.

    Deriving melody from pitch -- "the highest note at each onset" -- would give
    every onset a melody, including the 12,018 in the corpus where a held melody
    note spans the bar and only the accompaniment attacks. Labelling an
    accompaniment note "melody" there pins it to its full written value in the
    sustain model and scores it in melody-F1, both on the strength of a line the
    engraver did not write.
    """

    source = (
        _HEADER
        + _note("E", 5, voice=1, duration=4)  # melody holds the whole bar
        + _backup(4)
        + _note("C", 3, voice=2, duration=2)
        + _note("G", 3, voice=2, duration=2)  # second onset: accompaniment only
        + _FOOTER
    )
    example = parse_score_corpus_source(source, "roles.musicxml", _metadata())
    second = [note for note in example.notes if note.onset == Fraction(2)]
    assert [note.voice for note in second] == ["bass"]
    assert all(note.voice != "melody" for note in second)


def test_a_chord_within_the_primary_voice_is_still_harmony() -> None:
    """Untouched by this change, and pinned so it stays that way."""

    source = (
        _HEADER
        + _note("E", 5, voice=1)
        + b"      <note><chord/><pitch><step>C</step><octave>5</octave></pitch>"
        b"<duration>4</duration><voice>1</voice></note>\n"
        + _backup()
        + _note("C", 3, voice=2)
        + _FOOTER
    )
    assert _roles(source) == {76: "melody", 72: "harmony", 48: "bass"}


def test_no_shipped_corpus_row_carries_a_chord_in_the_bass() -> None:
    """The invariant on the data, not just on the parser.

    `validate_ir` checks `melody_polyphony` and has no counterpart for bass, so
    nothing in the runtime would have objected to the 2,867 onsets that carried
    one. The check is deliberately placed here rather than in `validate_ir`:
    tightening the runtime contract would also start rejecting LLM proposals
    that put two notes in the bass, which is a product decision and not part of
    repairing the corpus.
    """

    import json
    from collections import defaultdict
    from pathlib import Path

    corpus_dir = Path(__file__).resolve().parents[1] / "data" / "score_corpus"
    offenders: list[str] = []
    for path in sorted(corpus_dir.glob("*.json")):
        if path.name.endswith("_manifest.json"):
            continue
        for example in json.loads(path.read_text(encoding="utf-8"))["examples"]:
            per_onset: dict[tuple[int, int], int] = defaultdict(int)
            for note in example["notes"]:
                if note["voice"] == "bass":
                    per_onset[tuple(note["onset"])] += 1
            if any(count > 1 for count in per_onset.values()):
                offenders.append(str(example["id"]))
    assert not offenders, f"{len(offenders)} pieces carry a chord in the bass: {offenders[:5]}"


def test_no_shipped_corpus_row_needs_more_strings_than_the_instrument_has() -> None:
    """The parser refuses these now, so none may still be shipping.

    Eleven were, and they were being counted as refusals of the solver rather
    than of the importer.
    """

    import json
    from collections import defaultdict
    from pathlib import Path

    corpus_dir = Path(__file__).resolve().parents[1] / "data" / "score_corpus"
    offenders: list[str] = []
    for path in sorted(corpus_dir.glob("*.json")):
        if path.name.endswith("_manifest.json"):
            continue
        for example in json.loads(path.read_text(encoding="utf-8"))["examples"]:
            per_onset: dict[tuple[int, int], int] = defaultdict(int)
            for note in example["notes"]:
                per_onset[tuple(note["onset"])] += 1
            if any(count > len(example["tuning"]) for count in per_onset.values()):
                offenders.append(str(example["id"]))
    assert not offenders, f"{len(offenders)} pieces exceed their string count: {offenders[:5]}"
