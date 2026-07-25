from __future__ import annotations

import hashlib
import json
import stat
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from fretsure.score_corpus import (
    SCORE_CORPUS_MANIFEST_SCHEMA,
    ScoreCorpusMetadata,
    build_score_corpus_from_manifest,
    grouped_splits,
    parse_score_corpus_source,
    score_corpus_json_bytes,
)

SCORE = b'''<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="4.0">
  <part-list><score-part id="P1"><part-name>Guitar</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes>
        <divisions>2</divisions>
        <time><beats>2</beats><beat-type>4</beat-type></time>
      </attributes>
      <direction><sound tempo="72"/></direction>
      <note>
        <pitch><step>E</step><octave>4</octave></pitch><duration>2</duration><voice>1</voice>
        <notations><technical><fingering>1</fingering><string>1</string><fret>0</fret></technical></notations>
      </note>
      <note>
        <chord/><pitch><step>G</step><octave>4</octave></pitch><duration>2</duration><voice>1</voice>
        <notations><technical><fingering>3</fingering></technical></notations>
      </note>
      <note><pitch><step>F</step><octave>4</octave></pitch><duration>2</duration><voice>1</voice></note>
      <!-- Deliberately mirrors a known reviewed LilyPond export: voice 2 starts
           at the completed bar without a backup. -->
      <note>
        <pitch><step>C</step><octave>3</octave></pitch><duration>4</duration><voice>2</voice>
        <tie type="start"/>
        <notations><technical>
          <fingering>2</fingering><fingering alternate="yes">3</fingering>
        </technical></notations>
      </note>
    </measure>
    <measure number="2">
      <note>
        <pitch><step>C</step><octave>3</octave></pitch><duration>4</duration><voice>2</voice>
        <tie type="stop"/>
      </note>
      <backup><duration>4</duration></backup>
      <note><pitch><step>E</step><octave>4</octave></pitch><duration>4</duration><voice>1</voice></note>
    </measure>
  </part>
</score-partwise>
'''


def _metadata(identifier: str = "example") -> ScoreCorpusMetadata:
    return ScoreCorpusMetadata(
        id=identifier,
        work_id="work",
        group_id="composer/work/edition",
        title="Study",
        composer="Composer",
        edition="Reviewed edition",
        source_url="https://example.test/study.musicxml",
        license="Public Domain",
    )


def _write_member(archive: zipfile.ZipFile, name: str, data: bytes, compression: int) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = compression
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    archive.writestr(info, data)


def _mxl(score: bytes) -> bytes:
    container = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b"<container><rootfiles>"
        b'<rootfile full-path="score.musicxml" '
        b'media-type="application/vnd.recordare.musicxml+xml"/>'
        b"</rootfiles></container>"
    )
    output = BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=True) as archive:
        _write_member(
            archive,
            "mimetype",
            b"application/vnd.recordare.musicxml",
            zipfile.ZIP_STORED,
        )
        _write_member(archive, "META-INF/container.xml", container, zipfile.ZIP_DEFLATED)
        _write_member(archive, "score.musicxml", score, zipfile.ZIP_DEFLATED)
    return output.getvalue()


def test_musicxml_extracts_explicit_labels_and_merges_tied_notes() -> None:
    example = parse_score_corpus_source(SCORE, "study.musicxml", _metadata())

    assert example.tempo_bpm == 72
    assert example.time_signature == (2, 4)
    assert [(note.onset, note.pitch, note.voice) for note in example.notes[:3]] == [
        (0, 48, "bass"),
        (0, 64, "harmony"),
        (0, 67, "melody"),
    ]
    bass = example.notes[0]
    assert bass.duration == 4
    annotation = example.annotations[0]
    assert (annotation.onset, annotation.pitch) == (0, 48)
    assert annotation.accepted_fingers == (2, 3)
    assert next(item for item in example.annotations if item.pitch == 64).string == 5
    assert all(item.pitch != 65 for item in example.annotations)


def test_excess_voice_backup_cannot_create_a_negative_onset() -> None:
    source = SCORE.replace(
        b"      </attributes>\n      <direction>",
        b"      </attributes><backup><duration>8</duration></backup>\n      <direction>",
        1,
    )

    example = parse_score_corpus_source(source, "study.musicxml", _metadata())

    assert min(note.onset for note in example.notes) == 0


def test_mxl_preserves_raw_and_root_digests() -> None:
    raw = _mxl(SCORE)
    example = parse_score_corpus_source(raw, "study.mxl", _metadata())

    assert example.root_member == "score.musicxml"
    assert example.source_sha256 == hashlib.sha256(raw).hexdigest()
    assert example.root_sha256 == hashlib.sha256(SCORE).hexdigest()


def test_grouped_split_is_stable_and_never_separates_a_group() -> None:
    examples = tuple(
        parse_score_corpus_source(SCORE, f"{index}.musicxml", _metadata(str(index)))
        for index in range(3)
    )
    first = grouped_splits(examples, seed="fixed")
    second = grouped_splits(tuple(reversed(examples)), seed="fixed")

    assert first == second
    assert len(set(first.values())) == 1


def test_grouped_split_keeps_dev_and_test_for_three_or_more_groups() -> None:
    examples = tuple(
        parse_score_corpus_source(
            SCORE,
            f"{index}.musicxml",
            ScoreCorpusMetadata(
                id=str(index),
                work_id=str(index),
                group_id=str(index),
                title="Study",
                composer="Composer",
                edition="Edition",
                source_url="https://example.test/study.musicxml",
                license="Public Domain",
            ),
        )
        for index in range(5)
    )

    splits = grouped_splits(examples, seed="fixed")

    assert {split: tuple(splits.values()).count(split) for split in (
        "train",
        "dev",
        "test",
    )} == {"train": 3, "dev": 1, "test": 1}


def test_manifest_requires_matching_digest_and_emits_canonical_json(tmp_path: Path) -> None:
    source = tmp_path / "study.musicxml"
    source.write_bytes(SCORE)
    manifest = tmp_path / "manifest.json"
    row = {
        "id": "study",
        "work_id": "study",
        "group_id": "composer/study/edition",
        "title": "Study",
        "composer": "Composer",
        "edition": "Reviewed edition",
        "source_url": "https://example.test/study.musicxml",
        "license": "Public Domain",
        "path": source.name,
        "source_sha256": hashlib.sha256(SCORE).hexdigest(),
    }
    manifest.write_text(
        json.dumps({"schema": SCORE_CORPUS_MANIFEST_SCHEMA, "entries": [row]}),
        encoding="utf-8",
    )

    examples = build_score_corpus_from_manifest(manifest)
    document = json.loads(score_corpus_json_bytes(examples, split_seed="fixed"))
    assert document["examples"][0]["license"] == "Public Domain"
    assert document["examples"][0]["source_sha256"] == row["source_sha256"]

    row["source_sha256"] = "0" * 64
    manifest.write_text(
        json.dumps({"schema": SCORE_CORPUS_MANIFEST_SCHEMA, "entries": [row]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="source digest mismatch"):
        build_score_corpus_from_manifest(manifest)
