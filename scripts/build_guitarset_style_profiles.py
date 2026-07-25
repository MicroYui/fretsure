#!/usr/bin/env python3
"""Derive small, attributable rhythm profiles from GuitarSet annotations.

Only accompaniment performances from the frozen performer-grouped training
split select rhythmic phases. Development and test performers are summarized
for audit but never influence the selected profile. GuitarSet contains Jazz
and Funk, not R&B; the latter is therefore recorded as an explicit adjacent
proxy instead of being mislabeled as direct R&B supervision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Final, Literal, NoReturn, cast

PROFILE_SCHEMA: Final = "fretsure-guitarset-style-profiles@0.1.0"
PROFILE_VERSION: Final = "guitarset-style-profiles@0.1.0"
GUITARSET_ARCHIVE_SHA256: Final = (
    "8daa02e6417ccca1685feb44b135e95928ad7037e5032ecb326b5791856fda99"
)
GUITARSET_RECORD_URL: Final = "https://zenodo.org/records/3371780"
GUITARSET_DOI: Final = "10.5281/zenodo.3371780"
GUITARSET_LICENSE: Final = "CC-BY-4.0"
EXPECTED_MEMBER_COUNT: Final = 360
STEPS_PER_BEAT: Final = 4
BEATS_PER_BAR: Final = 4

Split = Literal["train", "dev", "test"]
Mode = Literal["comp", "solo"]

PERFORMER_SPLITS: Final[dict[str, Split]] = {
    "00": "train",
    "01": "train",
    "02": "train",
    "03": "train",
    "04": "dev",
    "05": "test",
}
MEMBER_RE: Final = re.compile(
    r"(?P<performer>[0-9]{2})_"
    r"(?P<family>BN|Funk|Jazz|Rock|SS)(?P<variant>[123])-"
    r"(?P<bpm>[0-9]+)-(?P<key>[^_]+)_(?P<mode>comp|solo)\.jams\Z"
)


class GuitarSetDataError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PerformanceProfile:
    source_id: str
    performer: str
    split: Split
    family: str
    mode: Mode
    tempo_bpm: Fraction
    phase_distribution: tuple[Fraction, ...]
    median_duration: Fraction
    note_count: int
    attack_count: int


def _fail(path: str, detail: str) -> NoReturn:
    raise GuitarSetDataError(f"invalid GuitarSet data at {path}: {detail}")


def _object(value: object, path: str) -> dict[str, object]:
    if type(value) is not dict:
        _fail(path, "expected an object")
    return cast(dict[str, object], value)


def _array(value: object, path: str) -> list[object]:
    if type(value) is not list:
        _fail(path, "expected an array")
    return cast(list[object], value)


def _number(value: object, path: str) -> Fraction:
    if type(value) is int:
        return Fraction(value)
    if type(value) is Decimal:
        if not value.is_finite():
            _fail(path, "expected a finite number")
        return Fraction(value)
    if type(value) is float and math.isfinite(value):
        return Fraction(str(value))
    _fail(path, "expected a finite number")


def _rows(value: object, path: str) -> tuple[dict[str, object], ...]:
    if type(value) is list:
        return tuple(
            _object(item, f"{path}[{index}]")
            for index, item in enumerate(cast(list[object], value))
        )
    columns = _object(value, path)
    if not columns:
        return ()
    arrays = {
        key: _array(column, f"{path}.{key}") for key, column in columns.items()
    }
    lengths = {len(column) for column in arrays.values()}
    if len(lengths) != 1:
        _fail(path, "column arrays must have equal lengths")
    length = next(iter(lengths))
    return tuple(
        {key: column[index] for key, column in arrays.items()}
        for index in range(length)
    )


def _round_grid(value: Fraction) -> Fraction:
    scaled = value * STEPS_PER_BEAT
    rounded = (2 * scaled.numerator + scaled.denominator) // (
        2 * scaled.denominator
    )
    return Fraction(rounded, STEPS_PER_BEAT)


def _median(values: Sequence[Fraction]) -> Fraction:
    if not values:
        raise ValueError("median requires at least one value")
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    return (ordered[midpoint - 1] + ordered[midpoint]) / 2


def parse_performance(source_id: str, raw: bytes) -> PerformanceProfile:
    """Parse the small JAMS subset required by the style profile."""

    filename = Path(source_id).name
    match = MEMBER_RE.fullmatch(filename)
    if match is None:
        _fail(source_id, "filename does not match the GuitarSet identity contract")
    performer = match.group("performer")
    split = PERFORMER_SPLITS.get(performer)
    if split is None:
        _fail(source_id, "performer is outside the frozen split")

    try:
        root = json.loads(raw.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(source_id, f"invalid UTF-8 JSON: {exc}")
    annotations = _array(
        _object(root, source_id).get("annotations"), f"{source_id}.annotations"
    )
    tempo: Fraction | None = None
    note_rows: list[tuple[Fraction, Fraction]] = []
    for annotation_index, raw_annotation in enumerate(annotations):
        path = f"{source_id}.annotations[{annotation_index}]"
        annotation = _object(raw_annotation, path)
        namespace = annotation.get("namespace")
        rows = _rows(annotation.get("data"), f"{path}.data")
        if namespace == "tempo" and rows and tempo is None:
            tempo = _number(rows[0].get("value"), f"{path}.data[0].value")
            if tempo <= 0:
                _fail(path, "tempo must be positive")
        elif namespace == "note_midi":
            metadata = _object(
                annotation.get("annotation_metadata"),
                f"{path}.annotation_metadata",
            )
            data_source = metadata.get("data_source")
            if type(data_source) is not str or not re.fullmatch(r"[0-5]", data_source):
                _fail(path, "note_midi data_source must identify string 0..5")
            for row_index, row in enumerate(rows):
                row_path = f"{path}.data[{row_index}]"
                time = _number(row.get("time"), f"{row_path}.time")
                duration = _number(row.get("duration"), f"{row_path}.duration")
                if time < 0 or duration <= 0:
                    _fail(row_path, "note time must be non-negative and duration positive")
                note_rows.append((time, duration))
    if tempo is None:
        _fail(source_id, "missing tempo annotation")
    if tempo != int(match.group("bpm")):
        _fail(source_id, "annotated tempo does not match filename")
    if not note_rows:
        _fail(source_id, "contains no per-string note_midi rows")

    seconds_to_beats = tempo / 60
    attacks = {
        _round_grid(time * seconds_to_beats) for time, _duration in note_rows
    }
    phase_counts = [0 for _ in range(STEPS_PER_BEAT * BEATS_PER_BAR)]
    for attack in attacks:
        phase = attack % BEATS_PER_BAR
        phase_counts[int(phase * STEPS_PER_BEAT)] += 1
    attack_count = sum(phase_counts)
    durations = tuple(
        max(Fraction(1, STEPS_PER_BEAT), _round_grid(duration * seconds_to_beats))
        for _time, duration in note_rows
    )
    return PerformanceProfile(
        source_id=source_id,
        performer=performer,
        split=split,
        family=match.group("family"),
        mode=cast(Mode, match.group("mode")),
        tempo_bpm=tempo,
        phase_distribution=tuple(
            Fraction(count, attack_count) for count in phase_counts
        ),
        median_duration=_median(durations),
        note_count=len(note_rows),
        attack_count=attack_count,
    )


def _mean_distribution(
    profiles: Sequence[PerformanceProfile],
) -> tuple[Fraction, ...]:
    if not profiles:
        raise ValueError("profile group must be non-empty")
    return tuple(
        sum((profile.phase_distribution[index] for profile in profiles), Fraction(0))
        / len(profiles)
        for index in range(STEPS_PER_BEAT * BEATS_PER_BAR)
    )


def _fraction_wire(value: Fraction) -> list[int]:
    return [value.numerator, value.denominator]


def _distribution_wire(values: Sequence[Fraction]) -> list[list[int]]:
    return [_fraction_wire(value) for value in values]


def _total_variation(
    left: Sequence[Fraction], right: Sequence[Fraction]
) -> Fraction:
    return sum(
        (abs(a - b) for a, b in zip(left, right, strict=True)), Fraction(0)
    ) / 2


def _style_profile(
    profiles: Sequence[PerformanceProfile],
    *,
    family: str,
    evidence_role: str,
) -> dict[str, object]:
    by_split = {
        split: tuple(
            profile
            for profile in profiles
            if profile.family == family
            and profile.mode == "comp"
            and profile.split == split
        )
        for split in cast(tuple[Split, ...], ("train", "dev", "test"))
    }
    expected = {"train": 24, "dev": 6, "test": 6}
    actual = {split: len(items) for split, items in by_split.items()}
    if actual != expected:
        raise ValueError(f"unexpected {family} accompaniment split counts: {actual}")
    distributions = {
        split: _mean_distribution(items) for split, items in by_split.items()
    }
    training = distributions["train"]
    eligible = tuple(
        index
        for index in range(len(training))
        if index % STEPS_PER_BEAT != 0
    )
    ranked = sorted(eligible, key=lambda index: (-training[index], index))
    selected = tuple(Fraction(index, STEPS_PER_BEAT) for index in ranked[:3])
    per_source_durations = tuple(
        profile.median_duration for profile in by_split["train"]
    )
    duration = min(Fraction(1), max(Fraction(1, 4), _median(per_source_durations)))
    return {
        "evidence_role": evidence_role,
        "source_family": family,
        "source_mode": "comp",
        "selected_from": "performers-00-through-03-train-only",
        "source_documents": actual,
        "source_notes": {
            split: sum(profile.note_count for profile in items)
            for split, items in by_split.items()
        },
        "intermediate_answer_phases": [
            _fraction_wire(value) for value in selected[:2]
        ],
        "advanced_answer_phases": [_fraction_wire(value) for value in selected],
        "answer_duration_beats": _fraction_wire(duration),
        "phase_distribution": {
            split: _distribution_wire(distribution)
            for split, distribution in distributions.items()
        },
        "total_variation_from_train": {
            split: format(
                float(_total_variation(training, distributions[split])), ".8f"
            )
            for split in cast(tuple[Split, ...], ("dev", "test"))
        },
    }


def build_profile_document(archive: Path) -> dict[str, object]:
    raw_archive = archive.read_bytes()
    actual_sha = hashlib.sha256(raw_archive).hexdigest()
    if actual_sha != GUITARSET_ARCHIVE_SHA256:
        raise ValueError(
            "GuitarSet archive digest mismatch: "
            f"expected {GUITARSET_ARCHIVE_SHA256}, got {actual_sha}"
        )
    profiles: list[PerformanceProfile] = []
    with zipfile.ZipFile(archive) as source:
        members = sorted(name for name in source.namelist() if name.endswith(".jams"))
        if len(members) != EXPECTED_MEMBER_COUNT or len(set(members)) != len(members):
            raise ValueError(
                f"GuitarSet archive must contain {EXPECTED_MEMBER_COUNT} unique JAMS files"
            )
        profiles.extend(parse_performance(member, source.read(member)) for member in members)

    return {
        "schema": PROFILE_SCHEMA,
        "profile_version": PROFILE_VERSION,
        "source": {
            "corpus": "GuitarSet",
            "record_url": GUITARSET_RECORD_URL,
            "doi": GUITARSET_DOI,
            "license": GUITARSET_LICENSE,
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "creators": [
                "Qingyang Xi",
                "Rachel M. Bittner",
                "Johan Pauwels",
                "Xuzhou Ye",
                "Juan P. Bello",
            ],
            "archive_sha256": actual_sha,
            "member_count": len(profiles),
            "redistribution": "derived aggregate only; JAMS and audio are not bundled",
        },
        "preprocessing": {
            "mode": "comp-only",
            "beats_per_bar": BEATS_PER_BAR,
            "steps_per_beat": STEPS_PER_BEAT,
            "attack_weighting": "equal-source normalized attack histograms",
            "simultaneous_notes": "collapsed to one quantized attack",
            "duration": "median per source, then median across training sources",
        },
        "split_policy": {
            "train": ["00", "01", "02", "03"],
            "dev": ["04"],
            "test": ["05"],
        },
        "profiles": {
            "jazz": _style_profile(
                profiles,
                family="Jazz",
                evidence_role="direct-jazz-performance",
            ),
            "rnb": _style_profile(
                profiles,
                family="Funk",
                evidence_role="adjacent-funk-proxy-not-rnb-supervision",
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    document = build_profile_document(args.archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
