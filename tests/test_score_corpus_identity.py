"""One piece, one row: the corpus may not carry the same music twice.

The corpus reported 389 published scores and held 303.  Mutopia sources
conventionally carry two ``\\score`` blocks -- one wrapping ``\\layout`` for
engraving, one wrapping ``\\midi`` for playback -- around the same music, and the
manifest builder deduplicated on the intermediate MusicXML bytes, which differ
between the two.  So 86 pieces entered twice, under ``-movement-1`` and
``-movement-2`` ids that claimed to be different music.

That is not a cosmetic miscount.  Every acceptance rate measured on this corpus
had an inflated denominator, and the inflation was not uniform: the duplicates
all landed in two of the six artifacts, so a rate computed over the whole corpus
was weighted toward whichever pieces happened to be doubled.  A duplicate also
defeats the grouped split, since the same music can land in train and in test.

These tests bind the property rather than the count, so an expansion is free to
grow the corpus and still cannot reintroduce the defect.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from fretsure.score_corpus import musical_identity_of_row

ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = ROOT / "data" / "score_corpus"

# The three artifacts the repertoire gate was frozen against.  A piece appearing
# both here and in an expansion must lose its expansion copy, never its baseline
# one, or every measurement taken against the frozen 58 becomes incomparable.
BASELINE = ("carcassi_op59.json", "mutopia_pd_additional.json", "mutopia_cc_by_sa.json")


def _corpus_paths() -> tuple[Path, ...]:
    return tuple(
        path
        for path in sorted(CORPUS_DIR.glob("*.json"))
        if not path.name.endswith("_manifest.json")
    )


def _rows() -> tuple[tuple[str, dict[str, object]], ...]:
    rows: list[tuple[str, dict[str, object]]] = []
    for path in _corpus_paths():
        document = json.loads(path.read_text(encoding="utf-8"))
        for row in document["examples"]:
            rows.append((path.name, row))
    return tuple(rows)


def test_no_two_corpus_rows_carry_the_same_music() -> None:
    """The invariant itself, across every artifact rather than within each one.

    Checking per-file would have missed the case that motivated seeding the
    builder from the shipped corpus: ``twominorpreludes`` shipped in an expansion
    as a second copy of two Carcassi op.59 preludes already in the baseline.
    """

    by_identity: dict[str, list[str]] = defaultdict(list)
    for filename, row in _rows():
        by_identity[musical_identity_of_row(row)].append(f"{filename}:{row['id']}")

    duplicates = {key: ids for key, ids in by_identity.items() if len(ids) > 1}
    assert not duplicates, (
        f"{len(duplicates)} pieces appear more than once: "
        f"{sorted(ids for ids in duplicates.values())[:5]}"
    )


def test_ids_are_unique_across_artifacts() -> None:
    """A weaker property than the one above, and it was never the failing one.

    Worth pinning anyway: the duplicates all had distinct ids, so id uniqueness
    held throughout and said nothing. A test that passes through the bug it is
    near is exactly the test that should be labelled as not covering it.
    """

    seen: dict[str, str] = {}
    for filename, row in _rows():
        identifier = str(row["id"])
        assert identifier not in seen, f"{identifier} in both {seen[identifier]} and {filename}"
        seen[identifier] = filename


def test_the_frozen_baseline_is_intact() -> None:
    """Deduplication was resolved in the baseline's favour, and quarantine was not.

    No duplicate was ever removed from the baseline -- all 86 fell in the two
    expanded artifacts. Two baseline pieces did leave, for the other reason:
    `sorf-op35-no21` and `sorf-op45n01` ask for more simultaneous attacks than
    the guitar has strings, so the importer no longer accepts them.

    That means the baseline is 56 and every historical figure quoted "of 58" was
    measured over a set containing two misparsed rows. Both were refused in
    every run, so the accepted counts carry over unchanged; it is the
    denominator that was two larger than the music justified.
    """

    counts = {path.name: len(json.loads(path.read_text())["examples"]) for path in _corpus_paths()}
    assert [counts[name] for name in BASELINE] == [16, 5, 35]
    assert sum(counts[name] for name in BASELINE) == 56


def test_the_corpus_is_the_size_it_reports() -> None:
    """The number every rate is divided by.

    Pinned because it moved silently once: 389 rows, 303 pieces, then 292 after
    the misparsed rows were refused. If an expansion changes this, the
    acceptance rates in docs/ must be recomputed rather than carried forward.
    """

    rows = _rows()
    assert len(rows) == 292
    assert len({musical_identity_of_row(row) for _, row in rows}) == 292


def test_every_manifest_movement_still_has_its_corpus_row() -> None:
    """Pruning had to touch both artifacts, since a rebuild reads the manifest.

    Dropping a duplicate row while leaving the manifest movement that produced it
    would mean the next rebuild puts the duplicate straight back.
    """

    shipped = {str(row["id"]) for _, row in _rows()}
    for manifest_path in sorted(CORPUS_DIR.glob("*_manifest.json")):
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in document["entries"]:
            assert entry["movements"], f"{manifest_path.name}: {entry['path']} has no movements"
            for movement in entry["movements"]:
                assert movement["id"] in shipped, (
                    f"{manifest_path.name} still selects {movement['id']}, "
                    "which is no longer in the corpus"
                )
