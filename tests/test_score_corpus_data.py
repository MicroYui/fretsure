from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parents[1]
CORPUS = ROOT / "data" / "score_corpus" / "carcassi_op59.json"
ADDITIONAL = ROOT / "data" / "score_corpus" / "mutopia_pd_additional.json"
CC_BY_SA = ROOT / "data" / "score_corpus" / "mutopia_cc_by_sa.json"
CC_BY_SA_MANIFEST = (
    ROOT / "data" / "score_corpus" / "mutopia_cc_by_sa_manifest.json"
)
SOURCE = ROOT / "data" / "score_corpus" / "sources" / "CarcassiMethodPreludes.ly"
REFERENCE = (
    ROOT / "tests" / "fixtures" / "fingering_reference" / "carcassi_op59_prelude_1.json"
)


def _fraction(value: list[int]) -> tuple[int, int]:
    return value[0], value[1]


def test_carcassi_corpus_is_provenance_complete_and_grouped() -> None:
    payload = CORPUS.read_bytes()
    document = json.loads(payload)
    examples = document["examples"]

    assert hashlib.sha256(payload).hexdigest() == (
        "4f906864d68ed9272a4ed74be11743b080d2e5e8519e1125ffae9c8fd9363908"
    )
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() == (
        "b1f42476923d13fd7f849c2275fddd55f1af2833815f7f712719dda2b0175cce"
    )
    assert len(examples) == 16
    assert sum(len(example["notes"]) for example in examples) == 1_745
    assert sum(len(example["annotations"]) for example in examples) == 451
    assert {split: sum(example["split"] == split for example in examples) for split in (
        "train",
        "dev",
        "test",
    )} == {"train": 11, "dev": 2, "test": 3}

    group_splits: dict[str, set[str]] = {}
    source_digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    for example in examples:
        assert example["source_url"]
        assert example["license"] == "Public Domain"
        assert example["edition"]
        assert example["group_id"]
        assert example["source_sha256"] == source_digest
        assert len(example["root_sha256"]) == 64
        group_splits.setdefault(example["group_id"], set()).add(example["split"])
        note_keys = {
            (_fraction(note["onset"]), note["pitch"])
            for note in example["notes"]
        }
        assert all(
            (_fraction(annotation["onset"]), annotation["pitch"]) in note_keys
            for annotation in example["annotations"]
        )
    assert all(len(splits) == 1 for splits in group_splits.values())


def test_carcassi_prelude_one_matches_independent_reference() -> None:
    example = json.loads(CORPUS.read_text(encoding="utf-8"))["examples"][0]
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    actual = {
        (_fraction(annotation["onset"]), annotation["pitch"]): tuple(
            annotation["accepted_fingers"]
        )
        for annotation in example["annotations"]
    }
    expected = {
        (_fraction(annotation["onset"]), annotation["pitch"]): (
            annotation["left_finger"],
        )
        for annotation in reference["annotations"]
    }

    assert actual == expected


def test_additional_mutopia_corpus_has_independent_public_domain_sources() -> None:
    payload = ADDITIONAL.read_bytes()
    examples = json.loads(payload)["examples"]

    assert hashlib.sha256(payload).hexdigest() == (
        "4bf36f7633693f4b02f19bccb1f8ccf704de47915bec2e4bab3f25fad7997e37"
    )
    assert len(examples) == 5
    assert len({example["composer"] for example in examples}) == 4
    assert sum(len(example["notes"]) for example in examples) == 1_738
    assert sum(len(example["annotations"]) for example in examples) == 256
    assert {split: sum(example["split"] == split for example in examples) for split in (
        "train",
        "dev",
        "test",
    )} == {"train": 3, "dev": 1, "test": 1}
    for example in examples:
        source = ROOT / "data" / "score_corpus" / "sources" / example["source_filename"]
        assert example["license"] == "Public Domain"
        assert hashlib.sha256(source.read_bytes()).hexdigest() == example["source_sha256"]


def test_mutopia_sharealike_corpus_is_large_licensed_and_source_complete() -> None:
    payload = CC_BY_SA.read_bytes()
    document = json.loads(payload)
    examples = document["examples"]
    manifest_payload = CC_BY_SA_MANIFEST.read_bytes()
    manifest = json.loads(manifest_payload)

    assert hashlib.sha256(payload).hexdigest() == (
        "c53ae16e24ae2d512f1f7a6f72b225322135b10756a71ba2e45493ca63d5d6bb"
    )
    assert hashlib.sha256(manifest_payload).hexdigest() == (
        "d8c228a394a772582a4141de00f871e7a09040bb9ab3570add0391daa655bff4"
    )
    assert len(manifest["entries"]) == 35
    assert len(examples) == 37
    assert sum(len(example["notes"]) for example in examples) == 14_461
    assert sum(len(example["annotations"]) for example in examples) == 1_776
    assert sum(
        any(1 <= finger <= 4 for finger in annotation["accepted_fingers"])
        for example in examples
        for annotation in example["annotations"]
    ) == 1_673
    assert {example["license"] for example in examples} == {
        "CC-BY-SA-3.0",
        "CC-BY-SA-4.0",
    }
    assert {example["composer"] for example in examples} == {
        "Dionisio Aguado",
        "Fernando Sor",
        "Francisco Tárrega",
        "Matteo Carcassi",
    }
    assert {split: sum(example["split"] == split for example in examples) for split in (
        "train",
        "dev",
        "test",
    )} == {"train": 26, "dev": 5, "test": 6}

    root_digests = [example["root_sha256"] for example in examples]
    assert len(root_digests) == len(set(root_digests))
    group_splits: dict[str, set[str]] = {}
    for example in examples:
        source = (
            ROOT
            / "data"
            / "score_corpus"
            / "sources"
            / "mutopia_cc_by_sa"
            / example["source_filename"]
        )
        assert hashlib.sha256(source.read_bytes()).hexdigest() == example["source_sha256"]
        group_splits.setdefault(example["group_id"], set()).add(example["split"])
    assert all(len(splits) == 1 for splits in group_splits.values())
