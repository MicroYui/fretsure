#!/usr/bin/env python3
"""Build the reviewed Mutopia CC BY-SA fingering corpus.

The product does not depend on LilyPond or ``python-ly``.  This offline builder
loads the pinned converter from the upstream ``graded-guitar`` checkout,
validates every source/conversion digest in the reviewed manifest, and emits
the same canonical score-corpus contract used by the runtime experiments.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import sys
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any, Final, cast

from fretsure.score_corpus import (
    ScoreCorpusExample,
    ScoreCorpusMetadata,
    musical_identity,
    parse_score_corpus_source,
    score_corpus_json_bytes,
)

MANIFEST_SCHEMA: Final = "fretsure-mutopia-lilypond-manifest@0.2.0"
DEFAULT_SPLIT_SEED: Final = "mutopia-cc-by-sa-v1"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _required_string(value: Mapping[str, object], key: str) -> str:
    result = value.get(key)
    if type(result) is not str or not result:
        raise ValueError(f"manifest {key} must be a non-empty string")
    return result


def _required_integer(value: Mapping[str, object], key: str) -> int:
    result = value.get(key)
    if type(result) is not int or result <= 0:
        raise ValueError(f"manifest {key} must be a positive integer")
    return result


def _load_converter(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "fretsure_pinned_mutopia_converter", path
    )
    if spec is None or spec.loader is None:
        raise ValueError(f"could not load converter at {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if not callable(getattr(module, "convert_lilypond", None)):
        raise ValueError("converter does not expose convert_lilypond")
    return module


def _pressed_label_count(example: ScoreCorpusExample) -> int:
    return sum(
        1
        for annotation in example.annotations
        if any(1 <= finger <= 4 for finger in annotation.accepted_fingers)
    )


def _non_negative_integer(value: Mapping[str, object], key: str) -> int:
    """A count that is allowed to be zero.

    Annotation counts pin that a rebuild produced the same thing, and "the same
    thing" is sometimes no fingering at all: plenty of real repertoire is
    engraved without editorial fingering.  Requiring a positive count here
    silently excluded every such score, which biases the corpus toward heavily
    edited editions -- exactly the kind of selection this corpus exists to avoid.
    """

    result = value.get(key)
    if type(result) is not int or result < 0:
        raise ValueError(f"manifest {key} must be a non-negative integer")
    return result


def _positive_number(value: Mapping[str, object], key: str) -> float:
    result = value.get(key)
    if type(result) not in (int, float) or float(cast(int | float, result)) <= 0:
        raise ValueError(f"manifest {key} must be a positive number")
    return float(cast(int | float, result))


def content_sha256(example: ScoreCorpusExample) -> str:
    """A digest of the music, independent of how the XML happened to serialise.

    The manifest used to bind on ``root_sha256``, the digest of the intermediate
    MusicXML the converter emits.  That turned out to depend on the libxml2 build
    lxml links against -- not only on the pinned ``lxml`` and ``python-ly`` -- so
    the shipped corpus could not be rebuilt on another machine even though the
    music was identical note for note.  A digest that is too tight is not extra
    safety; it is a check that fires on the wrong thing and trains people to
    ignore it.

    The digest now lives in ``fretsure.score_corpus`` because consumers need it
    too -- they hold JSON rows rather than parsed examples, and duplicate-freeness
    of the shipped corpus is checked against the same identity this pins.
    """

    return musical_identity(example)


def _declared_license(result: Any) -> str | None:
    """The licence the source states, under either field name Mutopia uses.

    LilyPond scores from the 2.x era write ``copyright = "..."`` where later
    ones write ``license = "..."``.  Reading only the newer name silently
    excluded every older engraving, which is how this corpus ended up sourced
    almost entirely from one typesetter.  The source bytes are already pinned by
    digest, so this comparison is a redundancy rather than the licence's
    guarantee -- reading the field the file actually uses simply makes the
    redundancy correct.
    """

    metadata = result.metadata
    for field in ("license", "copyright"):
        value = metadata.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _convert_source(
    convert_lilypond: Callable[[str], Any],
    source_text: str,
) -> Any:
    # python-ly logs unsupported engraving-only commands to stdout/stderr.  The
    # pinned wrapper turns structural music loss into explicit movement errors,
    # so those diagnostics are noise after its checks have passed.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
        io.StringIO()
    ):
        return convert_lilypond(source_text)


def build_mutopia_examples(
    manifest_path: Path,
    converter_path: Path,
) -> tuple[ScoreCorpusExample, ...]:
    """Convert and validate every explicitly selected manifest movement."""

    document = cast(
        dict[str, object], json.loads(manifest_path.read_text(encoding="utf-8"))
    )
    if document.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"manifest schema must be {MANIFEST_SCHEMA}")

    conversion = cast(dict[str, object], document.get("conversion"))
    expected_converter_sha = _required_string(conversion, "script_sha256")
    actual_converter_sha = _sha256(converter_path.read_bytes())
    if actual_converter_sha != expected_converter_sha:
        raise ValueError(
            "converter digest mismatch: "
            f"expected {expected_converter_sha}, got {actual_converter_sha}"
        )

    raw_entries = document.get("entries")
    if type(raw_entries) is not list or not raw_entries:
        raise ValueError("manifest entries must be a non-empty array")
    converter = _load_converter(converter_path)
    convert_lilypond = cast(Callable[[str], Any], converter.convert_lilypond)
    examples: list[ScoreCorpusExample] = []
    selected_roots: set[str] = set()

    for raw_entry in raw_entries:
        if type(raw_entry) is not dict:
            raise ValueError("manifest entries must be objects")
        entry = cast(dict[str, object], raw_entry)
        source_path = manifest_path.parent / _required_string(entry, "path")
        source_raw = source_path.read_bytes()
        source_sha = _sha256(source_raw)
        expected_source_sha = _required_string(entry, "source_sha256")
        if source_sha != expected_source_sha:
            raise ValueError(
                f"source digest mismatch for {source_path.name}: "
                f"expected {expected_source_sha}, got {source_sha}"
            )
        try:
            source_text = source_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"source is not UTF-8: {source_path.name}") from exc

        result = _convert_source(convert_lilypond, source_text)
        declared_license = _required_string(entry, "source_license_declaration")
        if _declared_license(result) != declared_license:
            raise ValueError(f"source license changed for {source_path.name}")
        by_index = {movement.movement_index: movement for movement in result.movements}

        raw_movements = entry.get("movements")
        if type(raw_movements) is not list or not raw_movements:
            raise ValueError("manifest movements must be a non-empty array")
        for raw_movement in raw_movements:
            if type(raw_movement) is not dict:
                raise ValueError("manifest movements must be objects")
            movement_spec = cast(dict[str, object], raw_movement)
            movement_index = _required_integer(movement_spec, "index")
            movement = by_index.get(movement_index)
            if movement is None:
                raise ValueError(
                    f"movement {movement_index} missing from {source_path.name}"
                )
            if not movement.success or movement.musicxml_bytes is None:
                raise ValueError(
                    f"movement {movement_index} failed for {source_path.name}: "
                    f"{movement.failure_reason}"
                )

            # Recorded as build provenance, deliberately not binding: it is the
            # digest of a derived intermediate whose byte form depends on the
            # XML library build.  ``content_sha256`` below is what a rebuild must
            # reproduce.
            actual_root_sha = _sha256(movement.musicxml_bytes)
            if actual_root_sha in selected_roots:
                raise ValueError(f"duplicate converted movement: {actual_root_sha}")
            selected_roots.add(actual_root_sha)

            identifier = _required_string(movement_spec, "id")
            metadata = ScoreCorpusMetadata(
                id=identifier,
                work_id=_required_string(entry, "work_id"),
                group_id=_required_string(entry, "group_id"),
                title=_required_string(movement_spec, "title"),
                composer=_required_string(entry, "composer"),
                edition=_required_string(entry, "edition"),
                source_url=_required_string(entry, "source_url"),
                license=_required_string(entry, "license"),
                styles=("classical",),
                tempo_bpm=_positive_number(movement_spec, "tempo_bpm"),
            )
            example = parse_score_corpus_source(
                movement.musicxml_bytes,
                f"{source_path.stem}-movement-{movement_index}.musicxml",
                metadata,
            )
            expected_annotations = _non_negative_integer(
                movement_spec, "annotation_count"
            )
            expected_pressed = _non_negative_integer(
                movement_spec, "pressed_annotation_count"
            )
            expected_content = _required_string(movement_spec, "content_sha256")
            actual_content = content_sha256(example)
            if actual_content != expected_content:
                raise ValueError(
                    f"converted content digest mismatch for {identifier}: "
                    f"expected {expected_content}, got {actual_content}"
                )
            expected_notes = _non_negative_integer(movement_spec, "note_count")
            if len(example.notes) != expected_notes:
                raise ValueError(
                    f"note count changed for {identifier}: "
                    f"expected {expected_notes}, got {len(example.notes)}"
                )
            if len(example.annotations) != expected_annotations:
                raise ValueError(
                    f"annotation count changed for {identifier}: "
                    f"expected {expected_annotations}, got {len(example.annotations)}"
                )
            pressed = _pressed_label_count(example)
            if pressed != expected_pressed:
                raise ValueError(
                    f"pressed annotation count changed for {identifier}: "
                    f"expected {expected_pressed}, got {pressed}"
                )
            examples.append(
                replace(
                    example,
                    source_filename=source_path.name,
                    source_sha256=source_sha,
                )
            )

    ids = [example.id for example in examples]
    if len(ids) != len(set(ids)):
        raise ValueError("manifest movement ids must be unique")
    return tuple(sorted(examples, key=lambda example: example.id))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--converter", type=Path, required=True)
    parser.add_argument("--split-seed", default=DEFAULT_SPLIT_SEED)
    args = parser.parse_args()

    examples = build_mutopia_examples(args.manifest, args.converter)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(
        score_corpus_json_bytes(examples, split_seed=args.split_seed)
    )


if __name__ == "__main__":
    main()
