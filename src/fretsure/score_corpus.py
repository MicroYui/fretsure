"""Deterministic, provenance-bearing score supervision corpus.

The runtime arranger never needs this module.  It is the offline boundary that
turns licensed MusicXML/MXL editions into stable notes, explicit fingering
labels, and leakage-resistant train/dev/test groups.
"""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal, cast

from fretsure.importers._mxl_container import MXLContainerPayload, read_mxl_container
from fretsure.importers.contracts import DEFAULT_LIMITS, ImportFailure
from fretsure.ir import Note, VoiceRole

SCORE_CORPUS_SCHEMA = "fretsure-score-corpus@0.1.0"
SCORE_CORPUS_MANIFEST_SCHEMA = "fretsure-score-corpus-manifest@0.1.0"
SCORE_CORPUS_SPLIT_POLICY = "grouped-sha256@0.1.0"
STANDARD_TUNING = (40, 45, 50, 55, 59, 64)

SplitName = Literal["train", "dev", "test"]


@dataclass(frozen=True, slots=True)
class CorpusGrade:
    """A published grade in one named grading system."""

    system: str
    value: str
    rank: int


@dataclass(frozen=True, slots=True)
class ScoreCorpusMetadata:
    """Edition metadata supplied by the reviewed source manifest."""

    id: str
    work_id: str
    group_id: str
    title: str
    composer: str
    edition: str
    source_url: str
    license: str
    styles: tuple[str, ...] = ()
    grade: CorpusGrade | None = None
    tuning: tuple[int, ...] = STANDARD_TUNING
    capo: int = 0
    tempo_bpm: float | None = None


@dataclass(frozen=True, slots=True)
class FingeringAnnotation:
    """Only explicitly printed technical notation; absence is not a label."""

    onset: Fraction
    pitch: int
    accepted_fingers: tuple[int, ...]
    string: int | None
    fret: int | None


@dataclass(frozen=True, slots=True)
class ScoreCorpusExample:
    id: str
    work_id: str
    group_id: str
    title: str
    composer: str
    edition: str
    source_url: str
    license: str
    source_filename: str
    source_sha256: str
    root_sha256: str
    root_member: str | None
    tempo_bpm: float
    time_signature: tuple[int, int]
    tuning: tuple[int, ...]
    capo: int
    styles: tuple[str, ...]
    grade: CorpusGrade | None
    notes: tuple[Note, ...]
    annotations: tuple[FingeringAnnotation, ...]


@dataclass(slots=True)
class _ParsedNote:
    onset: Fraction
    duration: Fraction
    pitch: int
    voice_id: str
    fingers: set[int]
    string: int | None
    fret: int | None
    tie_start: bool
    tie_stop: bool


_STEP_TO_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _child(element: ET.Element, name: str) -> ET.Element | None:
    return next((child for child in element if _tag(child) == name), None)


def _children(element: ET.Element, name: str) -> tuple[ET.Element, ...]:
    return tuple(child for child in element if _tag(child) == name)


def _text(element: ET.Element, name: str) -> str | None:
    child = _child(element, name)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _duration(element: ET.Element, divisions: int) -> Fraction:
    text = _text(element, "duration")
    return Fraction(0) if text is None else Fraction(int(text), divisions)


def _score_root(raw: bytes, filename: str) -> tuple[bytes, str | None]:
    if Path(filename).suffix.lower() == ".mxl" or raw.startswith(b"PK\x03\x04"):
        payload = read_mxl_container(raw, DEFAULT_LIMITS)
        if isinstance(payload, ImportFailure):
            details = "; ".join(
                f"{diagnostic.code.value}: {diagnostic.message}"
                for diagnostic in payload.diagnostics
            )
            raise ValueError(f"invalid MXL source: {details}")
        assert isinstance(payload, MXLContainerPayload)
        return payload.root_bytes, payload.root_path
    return raw, None


def _pitch(note: ET.Element, transpose: int) -> int | None:
    pitch = _child(note, "pitch")
    if pitch is None:
        return None
    step = _text(pitch, "step")
    octave = _text(pitch, "octave")
    if step not in _STEP_TO_PC or octave is None:
        return None
    alter = int(_text(pitch, "alter") or "0")
    midi = 12 * (int(octave) + 1) + _STEP_TO_PC[step] + alter + transpose
    return midi if 0 <= midi <= 127 else None


def _technical_values(
    note: ET.Element,
    string_count: int,
) -> tuple[set[int], int | None, int | None]:
    fingers: set[int] = set()
    strings: set[int] = set()
    frets: set[int] = set()
    for element in note.iter():
        name = _tag(element)
        text = "" if element.text is None else element.text.strip()
        if not text:
            continue
        if name == "fingering" and text.isdigit() and 0 <= int(text) <= 4:
            fingers.add(int(text))
        elif name == "string" and text.isdigit() and 1 <= int(text) <= string_count:
            strings.add(string_count - int(text))
        elif name == "fret" and text.isdigit():
            frets.add(int(text))
    string = next(iter(strings)) if len(strings) == 1 else None
    fret = next(iter(frets)) if len(frets) == 1 else None
    return fingers, string, fret


def _tie_types(note: ET.Element) -> tuple[bool, bool]:
    types = {
        element.attrib.get("type", "")
        for element in note
        if _tag(element) == "tie"
    }
    return "start" in types, "stop" in types


def _voice_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)


def _merge_ties(notes: list[_ParsedNote]) -> list[_ParsedNote]:
    merged: list[_ParsedNote] = []
    active: dict[tuple[str, int], int] = {}
    for note in notes:
        key = (note.voice_id, note.pitch)
        prior_index = active.get(key)
        if note.tie_stop and prior_index is not None:
            prior = merged[prior_index]
            if prior.onset + prior.duration == note.onset:
                prior.duration += note.duration
                prior.fingers.update(note.fingers)
                if prior.string != note.string:
                    prior.string = prior.string if note.string is None else None
                if prior.fret != note.fret:
                    prior.fret = prior.fret if note.fret is None else None
                if note.tie_start:
                    active[key] = prior_index
                else:
                    active.pop(key, None)
                continue
        merged.append(note)
        if note.tie_start:
            active[key] = len(merged) - 1
    return merged


def _collapse_notes(
    parsed: list[_ParsedNote],
) -> tuple[tuple[Note, ...], tuple[FingeringAnnotation, ...]]:
    if not parsed:
        raise ValueError("score contains no positive-duration pitched notes")
    primary_voice = min((note.voice_id for note in parsed), key=_voice_key)
    primary_highest: dict[Fraction, int] = {}
    for note in parsed:
        if note.voice_id == primary_voice:
            primary_highest[note.onset] = max(
                primary_highest.get(note.onset, note.pitch), note.pitch
            )

    grouped: dict[tuple[Fraction, int], list[_ParsedNote]] = {}
    for note in parsed:
        grouped.setdefault((note.onset, note.pitch), []).append(note)

    notes: list[Note] = []
    annotations: list[FingeringAnnotation] = []
    for (onset, pitch), members in sorted(grouped.items()):
        duration = max(member.duration for member in members)
        if any(member.voice_id != primary_voice for member in members):
            voice: VoiceRole = "bass"
        elif pitch == primary_highest.get(onset):
            voice = "melody"
        else:
            voice = "harmony"
        notes.append(Note(onset, duration, pitch, voice))

        fingers = tuple(sorted({finger for member in members for finger in member.fingers}))
        strings = {member.string for member in members if member.string is not None}
        frets = {member.fret for member in members if member.fret is not None}
        string = next(iter(strings)) if len(strings) == 1 else None
        fret = next(iter(frets)) if len(frets) == 1 else None
        if fingers or string is not None or fret is not None:
            annotations.append(FingeringAnnotation(onset, pitch, fingers, string, fret))
    return tuple(notes), tuple(annotations)


def parse_score_corpus_source(
    raw: bytes,
    filename: str,
    metadata: ScoreCorpusMetadata,
) -> ScoreCorpusExample:
    """Parse one reviewed MusicXML/MXL edition into the corpus contract."""

    root_bytes, root_member = _score_root(raw, filename)
    try:
        root = ET.fromstring(root_bytes)
    except ET.ParseError as exc:
        raise ValueError(f"malformed MusicXML: {exc}") from None
    if _tag(root) != "score-partwise":
        raise ValueError("score corpus accepts score-partwise MusicXML")
    parts = _children(root, "part")
    if len(parts) != 1:
        raise ValueError("score corpus entries must contain exactly one part")

    divisions: int | None = None
    time_signature: tuple[int, int] | None = None
    transpose = 0
    tempo = metadata.tempo_bpm
    measure_start = Fraction(0)
    parsed: list[_ParsedNote] = []

    for measure in _children(parts[0], "measure"):
        cursor = measure_start
        measure_max = measure_start
        seen_voices: set[str] = set()
        last_onset: dict[str, Fraction] = {}
        for element in measure:
            name = _tag(element)
            if name == "attributes":
                divisions_text = _text(element, "divisions")
                if divisions_text is not None:
                    divisions = int(divisions_text)
                    if divisions <= 0:
                        raise ValueError("MusicXML divisions must be positive")
                time = _child(element, "time")
                if time is not None:
                    beats = _text(time, "beats")
                    beat_type = _text(time, "beat-type")
                    if beats is not None and beat_type is not None:
                        current_time = (int(beats), int(beat_type))
                        if time_signature is None:
                            time_signature = current_time
                transposition = _child(element, "transpose")
                if transposition is not None:
                    chromatic = int(_text(transposition, "chromatic") or "0")
                    octave_change = int(_text(transposition, "octave-change") or "0")
                    transpose = chromatic + 12 * octave_change
                continue
            if name == "direction":
                if tempo is None:
                    for descendant in element.iter():
                        if _tag(descendant) == "sound" and "tempo" in descendant.attrib:
                            tempo = float(descendant.attrib["tempo"])
                            break
                continue
            if name in {"backup", "forward"}:
                if divisions is None:
                    raise ValueError("timeline control appears before divisions")
                amount = _duration(element, divisions)
                cursor = (
                    cursor + amount
                    if name == "forward"
                    else max(measure_start, cursor - amount)
                )
                continue
            if name != "note":
                continue
            if divisions is None:
                raise ValueError("note appears before MusicXML divisions")
            voice_id = _text(element, "voice") or "1"
            duration = _duration(element, divisions)
            chord = _child(element, "chord") is not None
            nominal_end = None
            if time_signature is not None:
                nominal_length = Fraction(4 * time_signature[0], time_signature[1])
                nominal_end = measure_start + nominal_length
            if (
                not chord
                and voice_id not in seen_voices
                and seen_voices
                and nominal_end is not None
                and cursor >= nominal_end
            ):
                # Some reviewed LilyPond exports omit the first backup before a
                # newly introduced lower voice.  Voice-local intent is still
                # unambiguous at the completed bar boundary.
                cursor = measure_start
            onset = last_onset.get(voice_id, cursor) if chord else cursor
            if not chord:
                last_onset[voice_id] = onset
                cursor += duration
            seen_voices.add(voice_id)
            measure_max = max(measure_max, onset + duration, cursor)
            if _child(element, "rest") is not None or duration <= 0:
                continue
            midi = _pitch(element, transpose)
            if midi is None:
                continue
            fingers, string, fret = _technical_values(element, len(metadata.tuning))
            tie_start, tie_stop = _tie_types(element)
            parsed.append(
                _ParsedNote(
                    onset,
                    duration,
                    midi,
                    voice_id,
                    fingers,
                    string,
                    fret,
                    tie_start,
                    tie_stop,
                )
            )

        if time_signature is None:
            raise ValueError("score has no time signature")
        nominal_length = Fraction(4 * time_signature[0], time_signature[1])
        if measure.attrib.get("implicit") == "yes":
            measure_start = measure_max
        else:
            measure_start = max(measure_start + nominal_length, measure_max)

    notes, annotations = _collapse_notes(_merge_ties(parsed))
    assert time_signature is not None
    resolved_tempo = 90.0 if tempo is None else float(tempo)
    if resolved_tempo <= 0:
        raise ValueError("tempo must be positive")
    return ScoreCorpusExample(
        id=metadata.id,
        work_id=metadata.work_id,
        group_id=metadata.group_id,
        title=metadata.title,
        composer=metadata.composer,
        edition=metadata.edition,
        source_url=metadata.source_url,
        license=metadata.license,
        source_filename=filename,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        root_sha256=hashlib.sha256(root_bytes).hexdigest(),
        root_member=root_member,
        tempo_bpm=resolved_tempo,
        time_signature=time_signature,
        tuning=metadata.tuning,
        capo=metadata.capo,
        styles=metadata.styles,
        grade=metadata.grade,
        notes=notes,
        annotations=annotations,
    )


def grouped_splits(
    examples: tuple[ScoreCorpusExample, ...],
    *,
    seed: str,
) -> dict[str, SplitName]:
    """Assign complete groups to deterministic 70/15/15 partitions."""

    groups = sorted(
        {example.group_id for example in examples},
        key=lambda group: (hashlib.sha256(f"{seed}\0{group}".encode()).digest(), group),
    )
    count = len(groups)
    train_end = round(count * 0.70)
    dev_count = round(count * 0.15)
    if count >= 3:
        train_end = min(train_end, count - 2)
        dev_count = max(1, min(dev_count, count - train_end - 1))
    dev_end = train_end + dev_count
    by_group: dict[str, SplitName] = {}
    for index, group in enumerate(groups):
        by_group[group] = "train" if index < train_end else "dev" if index < dev_end else "test"
    return {example.id: by_group[example.group_id] for example in examples}


def _fraction_json(value: Fraction) -> list[int]:
    return [value.numerator, value.denominator]


def _musical_identity(
    notes: list[list[object]],
    annotations: list[list[object]],
    tuning: list[int],
    capo: int,
    time_signature: list[int],
    tempo_bpm: float,
) -> str:
    """The digest itself, over primitives, so it has exactly one definition.

    Two callers need this: the manifest builder, which holds parsed examples,
    and any consumer checking the shipped corpus, which holds JSON rows.  A
    second implementation would be a second rule set to keep in agreement, and
    this project has already paid for one of those.
    """

    payload = {
        "notes": notes,
        "annotations": annotations,
        "tuning": tuning,
        "capo": capo,
        "time_signature": time_signature,
        "tempo_bpm": tempo_bpm,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def musical_identity(example: ScoreCorpusExample) -> str:
    """What the piece *is*, independent of how it was encoded or where it came from.

    Provenance -- title, source URL, licence, the intermediate MusicXML bytes --
    is deliberately excluded, because the same music reaching the corpus by two
    routes is one piece and must be counted once.  Deduping on the MusicXML
    instead let 86 pieces in twice: Mutopia sources conventionally carry a
    ``\\score`` wrapping ``\\layout`` and another wrapping ``\\midi``, holding the
    same notes and serialising to different bytes.
    """

    return _musical_identity(
        [
            [
                _fraction_json(note.onset),
                _fraction_json(note.duration),
                note.pitch,
                note.voice,
            ]
            for note in example.notes
        ],
        [
            [
                _fraction_json(annotation.onset),
                annotation.pitch,
                list(annotation.accepted_fingers),
                annotation.string,
                annotation.fret,
            ]
            for annotation in example.annotations
        ],
        list(example.tuning),
        example.capo,
        list(example.time_signature),
        example.tempo_bpm,
    )


def musical_identity_of_row(row: Mapping[str, object]) -> str:
    """The same digest for a row of a shipped corpus artifact.

    Consumers read the corpus as JSON rather than as parsed examples, and the
    duplicate-freeness of what actually ships is the property worth checking.
    """

    notes = cast(list[Mapping[str, object]], row["notes"])
    annotations = cast(list[Mapping[str, object]], row.get("annotations") or [])
    return _musical_identity(
        [
            [note["onset"], note["duration"], note["pitch"], note["voice"]]
            for note in notes
        ],
        [
            [
                annotation["onset"],
                annotation["pitch"],
                list(cast(list[int], annotation["accepted_fingers"])),
                annotation["string"],
                annotation["fret"],
            ]
            for annotation in annotations
        ],
        list(cast(list[int], row["tuning"])),
        cast(int, row["capo"]),
        list(cast(list[int], row["time_signature"])),
        cast(float, row["tempo_bpm"]),
    )


def score_corpus_json_bytes(
    examples: tuple[ScoreCorpusExample, ...],
    *,
    split_seed: str,
) -> bytes:
    """Return canonical UTF-8 JSON suitable for a checked-in training artifact."""

    splits = grouped_splits(examples, seed=split_seed)
    rows: list[dict[str, object]] = []
    for example in sorted(examples, key=lambda item: item.id):
        grade = None
        if example.grade is not None:
            grade = {
                "system": example.grade.system,
                "value": example.grade.value,
                "rank": example.grade.rank,
            }
        rows.append(
            {
                "id": example.id,
                "work_id": example.work_id,
                "group_id": example.group_id,
                "split": splits[example.id],
                "title": example.title,
                "composer": example.composer,
                "edition": example.edition,
                "source_url": example.source_url,
                "license": example.license,
                "source_filename": example.source_filename,
                "source_sha256": example.source_sha256,
                "root_sha256": example.root_sha256,
                "root_member": example.root_member,
                "tempo_bpm": example.tempo_bpm,
                "time_signature": list(example.time_signature),
                "tuning": list(example.tuning),
                "capo": example.capo,
                "styles": list(example.styles),
                "grade": grade,
                "notes": [
                    {
                        "onset": _fraction_json(note.onset),
                        "duration": _fraction_json(note.duration),
                        "pitch": note.pitch,
                        "voice": note.voice,
                    }
                    for note in example.notes
                ],
                "annotations": [
                    {
                        "onset": _fraction_json(annotation.onset),
                        "pitch": annotation.pitch,
                        "accepted_fingers": list(annotation.accepted_fingers),
                        "string": annotation.string,
                        "fret": annotation.fret,
                    }
                    for annotation in example.annotations
                ],
            }
        )
    document = {
        "schema": SCORE_CORPUS_SCHEMA,
        "split_policy": SCORE_CORPUS_SPLIT_POLICY,
        "split_seed": split_seed,
        "examples": rows,
    }
    return (json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def _required_string(row: dict[str, object], field: str) -> str:
    value = row.get(field)
    if type(value) is not str or not value:
        raise ValueError(f"manifest field {field!r} must be a non-empty string")
    return value


def build_score_corpus_from_manifest(path: Path) -> tuple[ScoreCorpusExample, ...]:
    """Read local reviewed sources declared by a versioned JSON manifest."""

    document = json.loads(path.read_text(encoding="utf-8"))
    if type(document) is not dict or document.get("schema") != SCORE_CORPUS_MANIFEST_SCHEMA:
        raise ValueError(f"manifest schema must be {SCORE_CORPUS_MANIFEST_SCHEMA}")
    raw_entries = document.get("entries")
    if type(raw_entries) is not list:
        raise ValueError("manifest entries must be an array")
    examples: list[ScoreCorpusExample] = []
    for raw_entry in raw_entries:
        if type(raw_entry) is not dict:
            raise ValueError("each manifest entry must be an object")
        entry = cast(dict[str, object], raw_entry)
        styles_value = entry.get("styles", [])
        if type(styles_value) is not list or any(type(value) is not str for value in styles_value):
            raise ValueError("manifest styles must be an array of strings")
        tuning_value = entry.get("tuning", list(STANDARD_TUNING))
        if type(tuning_value) is not list or any(type(value) is not int for value in tuning_value):
            raise ValueError("manifest tuning must be an array of MIDI integers")
        grade_value = entry.get("grade")
        grade = None
        if grade_value is not None:
            if type(grade_value) is not dict:
                raise ValueError("manifest grade must be an object")
            grade_row = cast(dict[str, object], grade_value)
            rank = grade_row.get("rank")
            if type(rank) is not int:
                raise ValueError("manifest grade rank must be an integer")
            grade = CorpusGrade(
                _required_string(grade_row, "system"),
                _required_string(grade_row, "value"),
                rank,
            )
        tempo_value = entry.get("tempo_bpm")
        if tempo_value is not None and type(tempo_value) not in (int, float):
            raise ValueError("manifest tempo_bpm must be numeric")
        capo_value = entry.get("capo", 0)
        if type(capo_value) is not int:
            raise ValueError("manifest capo must be an integer")
        metadata = ScoreCorpusMetadata(
            id=_required_string(entry, "id"),
            work_id=_required_string(entry, "work_id"),
            group_id=_required_string(entry, "group_id"),
            title=_required_string(entry, "title"),
            composer=_required_string(entry, "composer"),
            edition=_required_string(entry, "edition"),
            source_url=_required_string(entry, "source_url"),
            license=_required_string(entry, "license"),
            styles=tuple(cast(list[str], styles_value)),
            grade=grade,
            tuning=tuple(cast(list[int], tuning_value)),
            capo=capo_value,
            tempo_bpm=(
                None if tempo_value is None else float(cast(int | float, tempo_value))
            ),
        )
        source_path = path.parent / _required_string(entry, "path")
        raw = source_path.read_bytes()
        example = parse_score_corpus_source(raw, source_path.name, metadata)
        expected_digest = _required_string(entry, "source_sha256")
        if example.source_sha256 != expected_digest:
            raise ValueError(
                f"source digest mismatch for {metadata.id}: "
                f"expected {expected_digest}, got {example.source_sha256}"
            )
        examples.append(example)
    ids = [example.id for example in examples]
    if len(ids) != len(set(ids)):
        raise ValueError("manifest example ids must be unique")
    return tuple(sorted(examples, key=lambda example: example.id))


__all__ = [
    "CorpusGrade",
    "FingeringAnnotation",
    "SCORE_CORPUS_MANIFEST_SCHEMA",
    "SCORE_CORPUS_SCHEMA",
    "SCORE_CORPUS_SPLIT_POLICY",
    "STANDARD_TUNING",
    "ScoreCorpusExample",
    "ScoreCorpusMetadata",
    "build_score_corpus_from_manifest",
    "grouped_splits",
    "musical_identity",
    "musical_identity_of_row",
    "parse_score_corpus_source",
    "score_corpus_json_bytes",
]
