#!/usr/bin/env python3
"""Offline fingering-quality evaluation against per-string guitar annotations.

The evaluator streams GuitarSet JAMS files directly from their annotation ZIP
and reads EGSet12 JAMS files without extracting or modifying either corpus.  It
turns the human string annotations into small, deterministic solver windows,
runs the public fingering solver, and reports both playability and descriptive
quality metrics.  Corpus-specific quality targets are deliberately absent: all
window sizes, quantization, beam size, and split choices are explicit run
configuration rather than hidden pass/fail thresholds.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import re
import sys
import time
import zipfile
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Final, Literal, NoReturn, cast

import fretsure.solver.api as solver_api
from fretsure.geometry import STANDARD_TUNING, note_pitch
from fretsure.ir import Note
from fretsure.oracle.core import check_playability
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.solver.api import (
    FINGERING_SOLVER_VERSION,
    Infeasible,
    SolverInputError,
)
from fretsure.solver.cost import advance_hand_window, hand_window_for_frets
from fretsure.tab import Tab, TabNote

Split = Literal["train", "dev", "test", "held-out"]
RequestedSplit = Literal["train", "dev", "test", "held-out", "all"]
CorpusName = Literal["guitarset", "egset12"]
GuitarSetMode = Literal["comp", "solo"]

EVALUATION_SCHEMA: Final = "fingering-corpus-quality-eval@0.3.1"
GUITARSET_SAMPLING_PROTOCOL: Final = "performer-mode-round-robin@0.1.0"
GUITARSET_CORPUS_ID: Final = "guitarset@zenodo-3371780"
EGSET12_CORPUS_ID: Final = "egset12@zenodo-11406378"
GUITARSET_RECORD_URL: Final = "https://zenodo.org/records/3371780"
EGSET12_RECORD_URL: Final = "https://zenodo.org/records/11406378"
CORPUS_LICENSE: Final = "CC-BY-4.0"
DEFAULT_GUITARSET_ZIP: Final = Path("data/corpus/guitarset/annotation.zip")
DEFAULT_EGSET12_DIR: Final = Path("data/corpus/egset12")
GUITARSET_PERFORMER_SPLITS: Final[dict[str, Split]] = {
    "00": "train",
    "01": "train",
    "02": "train",
    "03": "train",
    "04": "dev",
    "05": "test",
}
GUITARSET_MODE_ORDER: Final[tuple[GuitarSetMode, ...]] = ("comp", "solo")
_GUITARSET_MEMBER_RE: Final = re.compile(
    r"(?P<performer>[0-9]{2})_.+_(?P<mode>comp|solo)\.jams\Z"
)


class CorpusDataError(ValueError):
    """A local corpus file does not satisfy the expected JAMS subset."""

    def __init__(self, path: str, detail: str) -> None:
        self.path = path
        self.detail = detail
        super().__init__(f"invalid corpus data at {path}: {detail}")


@dataclass(frozen=True, slots=True)
class ReferenceNote:
    """One human string assignment in source seconds."""

    time_seconds: Fraction
    duration_seconds: Fraction
    pitch: int
    string: int
    fret: int


@dataclass(frozen=True, slots=True)
class CorpusDocument:
    corpus: CorpusName
    corpus_id: str
    source_id: str
    source_sha256: str
    split: Split
    tempo_bpm: Fraction
    notes: tuple[ReferenceNote, ...]


@dataclass(frozen=True, slots=True)
class WindowNote:
    """One normalized, quantized attack in solver beats."""

    onset: Fraction
    duration: Fraction
    pitch: int
    string: int
    fret: int


@dataclass(frozen=True, slots=True)
class EvaluationWindow:
    corpus: CorpusName
    corpus_id: str
    source_id: str
    source_sha256: str
    split: Split
    window_index: int
    tempo_bpm: Fraction
    notes: tuple[WindowNote, ...]


@dataclass(frozen=True, slots=True)
class WindowConstructionRejection:
    """A preselected corpus window that cannot be represented after quantization."""

    corpus: CorpusName
    corpus_id: str
    source_id: str
    source_sha256: str
    split: Split
    window_index: int
    tempo_bpm: Fraction
    note_count: int
    code: str
    path: str
    message: str


WindowSelection = EvaluationWindow | WindowConstructionRejection


@dataclass(frozen=True, slots=True)
class EvaluationConfig:
    guitarset_zip: Path = DEFAULT_GUITARSET_ZIP
    egset12_dir: Path = DEFAULT_EGSET12_DIR
    requested_split: RequestedSplit = "train"
    windows_per_split: int = 1
    notes_per_window: int = 8
    window_offset: int = 0
    quantize_denominator: int = 96
    beam: int = 16
    beats_per_bar: int = 4
    provenance_label: str = "local-offline-quality-eval"

    def validate(self) -> None:
        positive = {
            "windows_per_split": self.windows_per_split,
            "notes_per_window": self.notes_per_window,
            "quantize_denominator": self.quantize_denominator,
            "beam": self.beam,
            "beats_per_bar": self.beats_per_bar,
        }
        for name, value in positive.items():
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if type(self.window_offset) is not int or self.window_offset < 0:
            raise ValueError("window_offset must be a non-negative integer")
        if not self.provenance_label:
            raise ValueError("provenance_label must be non-empty")


def _fail(path: str, detail: str) -> NoReturn:
    raise CorpusDataError(path, detail)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _number_fraction(value: object, path: str) -> Fraction:
    if type(value) is int:
        return Fraction(value)
    if type(value) is Decimal:
        if not value.is_finite():
            _fail(path, "expected a finite JSON number")
        return Fraction(value)
    if type(value) is float:
        # This branch is useful to direct callers constructing fixtures.  JAMS
        # bytes are decoded with Decimal and never inherit binary-float noise.
        if not math.isfinite(value):
            _fail(path, "expected a finite JSON number")
        return Fraction(str(value))
    _fail(path, "expected a finite JSON number")


def _non_negative(value: object, path: str) -> Fraction:
    result = _number_fraction(value, path)
    if result < 0:
        _fail(path, "expected a non-negative number")
    return result


def _positive(value: object, path: str) -> Fraction:
    result = _number_fraction(value, path)
    if result <= 0:
        _fail(path, "expected a positive number")
    return result


def _object(value: object, path: str) -> dict[str, object]:
    if type(value) is not dict:
        _fail(path, "expected an object")
    return cast(dict[str, object], value)


def _array(value: object, path: str) -> list[object]:
    if type(value) is not list:
        _fail(path, "expected an array")
    return cast(list[object], value)


def _annotation_rows(value: object, path: str) -> tuple[dict[str, object], ...]:
    """Normalize both ordinary JAMS rows and column-oriented JAMS exports."""

    if type(value) is list:
        return tuple(_object(row, f"{path}[{index}]") for index, row in enumerate(value))
    columns = _object(value, path)
    if not columns:
        return ()
    arrays: dict[str, list[object]] = {
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


def _round_midi(value: Fraction, path: str) -> int:
    if value < 0:
        _fail(path, "MIDI pitch must be non-negative")
    # Nearest semitone with exact half-up behavior.  The corpus values are
    # pitch estimates around an intended integer, not arbitrary solver targets.
    pitch = (2 * value.numerator + value.denominator) // (2 * value.denominator)
    if not 0 <= pitch <= 127:
        _fail(path, "rounded MIDI pitch is outside 0..127")
    return pitch


def _parse_tempo(annotations: Sequence[object], source_id: str) -> Fraction:
    for index, raw_annotation in enumerate(annotations):
        path = f"{source_id}.annotations[{index}]"
        annotation = _object(raw_annotation, path)
        if annotation.get("namespace") != "tempo":
            continue
        rows = _annotation_rows(annotation.get("data"), f"{path}.data")
        if rows:
            return _positive(rows[0].get("value"), f"{path}.data[0].value")
    _fail(source_id, "missing non-empty tempo annotation")


def _parse_reference_notes(
    annotations: Sequence[object], source_id: str
) -> tuple[ReferenceNote, ...]:
    notes: list[ReferenceNote] = []
    for annotation_index, raw_annotation in enumerate(annotations):
        path = f"{source_id}.annotations[{annotation_index}]"
        annotation = _object(raw_annotation, path)
        if annotation.get("namespace") != "note_midi":
            continue
        metadata = _object(annotation.get("annotation_metadata"), f"{path}.annotation_metadata")
        raw_source = metadata.get("data_source")
        if type(raw_source) is not str or re.fullmatch(r"[0-5]", raw_source) is None:
            _fail(f"{path}.annotation_metadata.data_source", "expected string id 0..5")
        string = int(raw_source)
        rows = _annotation_rows(annotation.get("data"), f"{path}.data")
        for row_index, row in enumerate(rows):
            row_path = f"{path}.data[{row_index}]"
            time_seconds = _non_negative(row.get("time"), f"{row_path}.time")
            duration_seconds = _positive(row.get("duration"), f"{row_path}.duration")
            pitch = _round_midi(
                _number_fraction(row.get("value"), f"{row_path}.value"),
                f"{row_path}.value",
            )
            fret = pitch - STANDARD_TUNING[string]
            if fret < 0:
                _fail(row_path, "rounded pitch lies below its annotated open string")
            notes.append(
                ReferenceNote(time_seconds, duration_seconds, pitch, string, fret)
            )
    if not notes:
        _fail(source_id, "contains no per-string note_midi rows")
    return tuple(
        sorted(notes, key=lambda note: (note.time_seconds, note.string, note.pitch))
    )


def parse_jams_document(
    raw: bytes,
    *,
    corpus: CorpusName,
    source_id: str,
    split: Split,
) -> CorpusDocument:
    """Parse the small JAMS subset needed by the evaluator."""

    try:
        root = json.loads(raw.decode("utf-8"), parse_float=Decimal)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _fail(source_id, f"invalid UTF-8 JSON: {exc}")
    payload = _object(root, source_id)
    annotations = _array(payload.get("annotations"), f"{source_id}.annotations")
    corpus_id = GUITARSET_CORPUS_ID if corpus == "guitarset" else EGSET12_CORPUS_ID
    return CorpusDocument(
        corpus=corpus,
        corpus_id=corpus_id,
        source_id=source_id,
        source_sha256=_sha256(raw),
        split=split,
        tempo_bpm=_parse_tempo(annotations, source_id),
        notes=_parse_reference_notes(annotations, source_id),
    )


def _guitarset_member_identity(
    member: str,
) -> tuple[str, GuitarSetMode]:
    filename = Path(member).name
    match = _GUITARSET_MEMBER_RE.fullmatch(filename)
    if match is None:
        _fail(
            member,
            "GuitarSet filename must encode a two-digit performer and comp/solo mode",
        )
    return (match.group("performer"), cast(GuitarSetMode, match.group("mode")))


def guitarset_split(member: str) -> Literal["train", "dev", "test"]:
    """Apply the frozen performer-disjoint GuitarSet train/dev/test split."""

    performer, _ = _guitarset_member_identity(member)
    split = GUITARSET_PERFORMER_SPLITS.get(performer)
    if split is None or split == "held-out":
        _fail(member, f"GuitarSet performer {performer} is outside the frozen split")
    return split


def _ordered_guitarset_members(
    members: Sequence[str],
    split: Literal["train", "dev", "test"],
) -> tuple[str, ...]:
    """Fixed performer×mode round-robin, with filename order inside groups."""

    if len(set(members)) != len(members):
        _fail("GuitarSet ZIP", "contains duplicate JAMS member names")
    performers = tuple(
        performer
        for performer, performer_split in GUITARSET_PERFORMER_SPLITS.items()
        if performer_split == split
    )
    group_order = tuple(
        (performer, mode)
        for performer in performers
        for mode in GUITARSET_MODE_ORDER
    )
    groups: dict[tuple[str, GuitarSetMode], list[str]] = {
        group: [] for group in group_order
    }
    for member in members:
        performer, mode = _guitarset_member_identity(member)
        if guitarset_split(member) != split:
            continue
        groups[(performer, mode)].append(member)
    for members_in_group in groups.values():
        members_in_group.sort()

    ordered: list[str] = []
    round_index = 0
    while True:
        added = False
        for group_key in group_order:
            members_in_group = groups[group_key]
            if round_index >= len(members_in_group):
                continue
            ordered.append(members_in_group[round_index])
            added = True
        if not added:
            break
        round_index += 1
    expected = sum(len(group) for group in groups.values())
    assert len(ordered) == expected and len(set(ordered)) == expected
    return tuple(ordered)


def iter_guitarset_documents(
    archive: Path,
    *,
    split: Literal["train", "dev", "test"],
) -> Iterator[CorpusDocument]:
    if not archive.is_file():
        raise FileNotFoundError(f"GuitarSet annotation archive not found: {archive}")
    with zipfile.ZipFile(archive) as source:
        members = tuple(name for name in source.namelist() if name.endswith(".jams"))
        for member in _ordered_guitarset_members(members, split):
            raw = source.read(member)
            yield parse_jams_document(
                raw,
                corpus="guitarset",
                source_id=member,
                split=split,
            )


def iter_egset12_documents(directory: Path) -> Iterator[CorpusDocument]:
    if not directory.is_dir():
        raise FileNotFoundError(f"EGSet12 directory not found: {directory}")
    paths = sorted(directory.glob("*.jams"))
    if not paths:
        raise FileNotFoundError(f"EGSet12 JAMS files not found: {directory}")
    for path in paths:
        raw = path.read_bytes()
        yield parse_jams_document(
            raw,
            corpus="egset12",
            source_id=path.name,
            split="held-out",
        )


def _round_to_grid(value: Fraction, denominator: int) -> Fraction:
    if value < 0:
        raise ValueError("grid input must be non-negative")
    scaled = value * denominator
    rounded = (2 * scaled.numerator + scaled.denominator) // (2 * scaled.denominator)
    return Fraction(rounded, denominator)


def build_window(
    document: CorpusDocument,
    *,
    notes_per_window: int,
    window_offset: int,
    quantize_denominator: int,
    window_index: int = 0,
) -> WindowSelection:
    """Create one deterministic solver window from a document.

    Quantization is explicit run configuration.  Durations are clipped at the
    next attack on the same string, which removes annotation overlap without
    inventing a release later than the human's next pluck.
    """

    selected = document.notes[window_offset : window_offset + notes_per_window]
    if not selected:
        return WindowConstructionRejection(
            corpus=document.corpus,
            corpus_id=document.corpus_id,
            source_id=document.source_id,
            source_sha256=document.source_sha256,
            split=document.split,
            window_index=window_index,
            tempo_bpm=document.tempo_bpm,
            note_count=0,
            code="EMPTY_SELECTED_WINDOW",
            path="$window.notes",
            message="the configured offset selects no annotated notes",
        )
    seconds_to_beats = document.tempo_bpm / 60
    first_beat = min(note.time_seconds * seconds_to_beats for note in selected)
    step = Fraction(1, quantize_denominator)
    provisional: list[WindowNote] = []
    for source_note in selected:
        onset = _round_to_grid(
            source_note.time_seconds * seconds_to_beats - first_beat,
            quantize_denominator,
        )
        duration = max(
            step,
            _round_to_grid(
                source_note.duration_seconds * seconds_to_beats,
                quantize_denominator,
            ),
        )
        provisional.append(
            WindowNote(
                onset,
                duration,
                source_note.pitch,
                source_note.string,
                source_note.fret,
            )
        )

    by_string: dict[int, list[int]] = defaultdict(list)
    for index, window_note in enumerate(provisional):
        by_string[window_note.string].append(index)
    clipped = list(provisional)
    for indexes in by_string.values():
        indexes.sort(key=lambda index: (provisional[index].onset, index))
        for current_index, next_index in itertools.pairwise(indexes):
            current = clipped[current_index]
            next_onset = provisional[next_index].onset
            if next_onset <= current.onset:
                # Exact simultaneous attacks on one physical string cannot be
                # represented by either the human reference or the solver.
                return WindowConstructionRejection(
                    corpus=document.corpus,
                    corpus_id=document.corpus_id,
                    source_id=document.source_id,
                    source_sha256=document.source_sha256,
                    split=document.split,
                    window_index=window_index,
                    tempo_bpm=document.tempo_bpm,
                    note_count=len(selected),
                    code="QUANTIZATION_SAME_STRING_COLLISION",
                    path=f"$window.notes[{next_index}].onset",
                    message=(
                        "two attacks on one annotated string quantize to the same onset; "
                        "use a finer grid"
                    ),
                )
            clipped[current_index] = WindowNote(
                current.onset,
                min(current.duration, next_onset - current.onset),
                current.pitch,
                current.string,
                current.fret,
            )

    notes = tuple(sorted(clipped, key=lambda note: (note.onset, note.string, note.pitch)))
    return EvaluationWindow(
        corpus=document.corpus,
        corpus_id=document.corpus_id,
        source_id=document.source_id,
        source_sha256=document.source_sha256,
        split=document.split,
        window_index=window_index,
        tempo_bpm=document.tempo_bpm,
        notes=notes,
    )


def _fraction_text(value: Fraction) -> str:
    return f"{value.numerator}/{value.denominator}"


def _fraction_metric(value: Fraction) -> dict[str, object]:
    return {"exact": _fraction_text(value), "value": float(value)}


def _geometry_metrics(
    notes: Sequence[WindowNote] | Sequence[TabNote], profile: Profile
) -> dict[str, object]:
    max_fret = max((note.fret for note in notes), default=0)
    exposure = sum(
        (note.duration * note.fret for note in notes),
        start=Fraction(0),
    )
    hand_window: tuple[int, int] | None = None
    shift_count = 0
    shift_distance_um = 0
    for onset in sorted({note.onset for note in notes}):
        active_frets = tuple(
            note.fret
            for note in notes
            if note.onset <= onset < note.onset + note.duration
        )
        shape = hand_window_for_frets(active_frets, 0, profile)
        hand_window, shifted, distance = advance_hand_window(hand_window, shape)
        shift_count += int(shifted)
        shift_distance_um += distance
    return {
        "max_fret": max_fret,
        "duration_weighted_fret_exposure": _fraction_metric(exposure),
        "shift_count": shift_count,
        "shift_distance_micrometres": shift_distance_um,
        "shift_distance_mm": shift_distance_um / 1_000.0,
    }


def _alignment_key(note: WindowNote) -> tuple[Fraction, int]:
    return (note.onset, note.pitch)


def _tab_alignment_key(note: TabNote, tab: Tab) -> tuple[Fraction, int]:
    return (note.onset, note_pitch(note.string, note.fret, tab.tuning, tab.capo))


def _match_duplicate_attack(
    reference: Sequence[WindowNote], solved: Sequence[TabNote]
) -> tuple[tuple[WindowNote, TabNote], ...]:
    """Deterministically align unison attacks without assuming a string order."""

    if len(reference) != len(solved):
        raise ValueError("reference and solver attack multiplicities differ")
    best_key: tuple[object, ...] | None = None
    best: tuple[TabNote, ...] | None = None
    ordered_reference = tuple(sorted(reference, key=lambda note: (note.string, note.fret)))
    for candidate in itertools.permutations(solved):
        joint_mismatch = sum(
            int(left.string != right.string or left.fret != right.fret)
            for left, right in zip(ordered_reference, candidate, strict=True)
        )
        string_distance = sum(
            abs(left.string - right.string)
            for left, right in zip(ordered_reference, candidate, strict=True)
        )
        fret_distance = sum(
            abs(left.fret - right.fret)
            for left, right in zip(ordered_reference, candidate, strict=True)
        )
        key: tuple[object, ...] = (
            joint_mismatch,
            string_distance,
            fret_distance,
            tuple((note.string, note.fret) for note in candidate),
        )
        if best_key is None or key < best_key:
            best_key = key
            best = candidate
    assert best is not None
    return tuple(zip(ordered_reference, best, strict=True))


def _comparison_metrics(window: EvaluationWindow, tab: Tab) -> dict[str, object]:
    reference_groups: dict[tuple[Fraction, int], list[WindowNote]] = defaultdict(list)
    solved_groups: dict[tuple[Fraction, int], list[TabNote]] = defaultdict(list)
    for reference_note in window.notes:
        reference_groups[_alignment_key(reference_note)].append(reference_note)
    for tab_note in tab.notes:
        solved_groups[_tab_alignment_key(tab_note, tab)].append(tab_note)
    if reference_groups.keys() != solved_groups.keys():
        raise ValueError("solver output attack keys do not match the reference window")

    pairs: list[tuple[WindowNote, TabNote]] = []
    for key in sorted(reference_groups):
        pairs.extend(_match_duplicate_attack(reference_groups[key], solved_groups[key]))
    note_count = len(pairs)
    string_exact = sum(int(left.string == right.string) for left, right in pairs)
    fret_exact = sum(int(left.fret == right.fret) for left, right in pairs)
    joint_exact = sum(
        int(left.string == right.string and left.fret == right.fret)
        for left, right in pairs
    )
    fret_abs_error = sum(abs(left.fret - right.fret) for left, right in pairs)
    string_abs_error = sum(abs(left.string - right.string) for left, right in pairs)

    def ratio(numerator: int) -> float | None:
        return numerator / note_count if note_count else None

    return {
        "notes_compared": note_count,
        "string_exact_count": string_exact,
        "string_exact_rate": ratio(string_exact),
        "fret_exact_count": fret_exact,
        "fret_exact_rate": ratio(fret_exact),
        "string_fret_exact_count": joint_exact,
        "string_fret_exact_rate": ratio(joint_exact),
        "joint_mismatch_count": note_count - joint_exact,
        "string_absolute_error_sum": string_abs_error,
        "fret_absolute_error_sum": fret_abs_error,
        "fret_mae": ratio(fret_abs_error),
    }


def _imitation_key(
    comparison: dict[str, object], candidate_order: int
) -> tuple[int, int, int, int]:
    """Offline-only human imitation key; never feeds production selection."""

    return (
        cast(int, comparison["joint_mismatch_count"]),
        cast(int, comparison["string_absolute_error_sum"]),
        cast(int, comparison["fret_absolute_error_sum"]),
        candidate_order,
    )


def _imitation_key_payload(key: tuple[int, int, int, int]) -> dict[str, int]:
    return {
        "joint_mismatch_count": key[0],
        "string_distance": key[1],
        "fret_distance": key[2],
        "stable_candidate_order": key[3],
    }


def _tab_geometry_key(tab: Tab) -> tuple[tuple[Fraction, int, int], ...]:
    """Attack geometry independent of left/right finger spelling."""

    return tuple(
        sorted((note.onset, note.string, note.fret) for note in tab.notes)
    )


def _empty_pool_metrics() -> dict[str, object]:
    return {
        "green_pool_size": 0,
        "pool_geometry_diversity": {
            "unique_geometries": 0,
            "rate": None,
        },
        "best_in_pool_candidate": None,
        "best_in_pool_comparison": None,
        "selected_vs_best": None,
    }


def _green_pool_metrics(
    window: EvaluationWindow,
    selected: Tab,
    pool: Sequence[solver_api._GreenFinalist],
) -> dict[str, object]:
    if not pool:
        return _empty_pool_metrics()

    comparisons = tuple(_comparison_metrics(window, finalist.tab) for finalist in pool)
    keys = tuple(
        _imitation_key(comparison, index)
        for index, comparison in enumerate(comparisons)
    )
    best_index = min(range(len(pool)), key=lambda index: keys[index])
    selected_index = next(
        (index for index, finalist in enumerate(pool) if finalist.tab == selected),
        None,
    )
    if selected_index is None:
        raise RuntimeError("GREEN pool does not contain the production-selected tab")

    best_comparison = comparisons[best_index]
    selected_comparison = comparisons[selected_index]
    best_key = keys[best_index]
    selected_key = keys[selected_index]
    note_count = cast(int, selected_comparison["notes_compared"])
    joint_gain = cast(int, best_comparison["string_fret_exact_count"]) - cast(
        int, selected_comparison["string_fret_exact_count"]
    )
    unique_geometries = len({_tab_geometry_key(finalist.tab) for finalist in pool})
    return {
        "green_pool_size": len(pool),
        "pool_geometry_diversity": {
            "unique_geometries": unique_geometries,
            "rate": unique_geometries / len(pool),
        },
        "best_in_pool_candidate": {
            "candidate_index": best_index,
            "stable_rank": pool[best_index].stable_rank,
            "imitation_key": _imitation_key_payload(best_key),
        },
        "best_in_pool_comparison": best_comparison,
        "selected_vs_best": {
            "selected_candidate_index": selected_index,
            "selected_stable_rank": pool[selected_index].stable_rank,
            "best_candidate_index": best_index,
            "has_imitation_regret": selected_key[:3] > best_key[:3],
            "selected_imitation_key": _imitation_key_payload(selected_key),
            "best_imitation_key": _imitation_key_payload(best_key),
            "headroom": {
                "joint_exact_count_gain": joint_gain,
                "joint_exact_rate_gain": joint_gain / note_count if note_count else None,
                "string_distance_reduction": cast(
                    int, selected_comparison["string_absolute_error_sum"]
                )
                - cast(int, best_comparison["string_absolute_error_sum"]),
                "fret_distance_reduction": cast(
                    int, selected_comparison["fret_absolute_error_sum"]
                )
                - cast(int, best_comparison["fret_absolute_error_sum"]),
            },
        },
    }


def _construction_rejection_result(
    rejection: WindowConstructionRejection,
) -> dict[str, object]:
    return {
        "corpus": rejection.corpus,
        "corpus_id": rejection.corpus_id,
        "source_id": rejection.source_id,
        "source_sha256": rejection.source_sha256,
        "split": rejection.split,
        "window_index": rejection.window_index,
        "tempo_bpm": _fraction_metric(rejection.tempo_bpm),
        "note_count": rejection.note_count,
        "runtime_nanoseconds": 0,
        "runtime_ms": 0.0,
        "outcome": "UNSUPPORTED_INPUT",
        "verdict": None,
        "supported": False,
        "comparable": False,
        "unsupported_input": {
            "stage": "window_construction",
            "diagnostics": [
                {
                    "code": rejection.code,
                    "path": rejection.path,
                    "message": rejection.message,
                }
            ],
        },
        "infeasible": None,
        "reference": None,
        "solver": None,
        "comparison": None,
        "selected_comparison": None,
        **_empty_pool_metrics(),
    }


def _evaluation_base(
    window: EvaluationWindow,
    *,
    elapsed_ns: int,
    profile: Profile,
) -> dict[str, object]:
    return {
        "corpus": window.corpus,
        "corpus_id": window.corpus_id,
        "source_id": window.source_id,
        "source_sha256": window.source_sha256,
        "split": window.split,
        "window_index": window.window_index,
        "tempo_bpm": _fraction_metric(window.tempo_bpm),
        "note_count": len(window.notes),
        "runtime_nanoseconds": elapsed_ns,
        "runtime_ms": elapsed_ns / 1_000_000.0,
        "reference": _geometry_metrics(window.notes, profile),
    }


def evaluate_window(
    window: EvaluationWindow,
    *,
    profile: Profile = MEDIAN_HAND,
    beam: int = 16,
    beats_per_bar: int = 4,
) -> dict[str, object]:
    target = tuple(
        Note(note.onset, note.duration, note.pitch, "melody") for note in window.notes
    )
    started = time.perf_counter_ns()
    try:
        search_outcome = solver_api._solve_fingering_with_green_pool(
            target,
            STANDARD_TUNING,
            0,
            profile,
            tempo_bpm=float(window.tempo_bpm),
            beats_per_bar=beats_per_bar,
            beam=beam,
        )
    except SolverInputError as exc:
        elapsed_ns = time.perf_counter_ns() - started
        base = _evaluation_base(window, elapsed_ns=elapsed_ns, profile=profile)
        base.update(
            {
                "outcome": "UNSUPPORTED_INPUT",
                "verdict": None,
                "supported": False,
                "comparable": False,
                "unsupported_input": {
                    "stage": "solver_input",
                    "diagnostics": [
                        {
                            "code": diagnostic.code.value,
                            "path": diagnostic.path,
                            "message": diagnostic.message,
                        }
                        for diagnostic in exc.diagnostics
                    ],
                },
                "infeasible": None,
                "solver": None,
                "comparison": None,
                "selected_comparison": None,
                **_empty_pool_metrics(),
            }
        )
        return base
    elapsed_ns = time.perf_counter_ns() - started
    base = _evaluation_base(window, elapsed_ns=elapsed_ns, profile=profile)
    result = search_outcome.result
    if isinstance(result, Infeasible):
        base.update(
            {
                "outcome": "INFEASIBLE",
                "verdict": None,
                "supported": True,
                "comparable": False,
                "unsupported_input": None,
                "infeasible": {
                    "code": result.code.value,
                    "onset": _fraction_text(result.onset) if result.onset is not None else None,
                    "reason": result.reason,
                    "pitches": list(result.pitches),
                },
                "solver": None,
                "comparison": None,
                "selected_comparison": None,
                **_empty_pool_metrics(),
            }
        )
        return base

    verdict = check_playability(
        result,
        profile,
        tempo_bpm=float(window.tempo_bpm),
        beats_per_bar=beats_per_bar,
    ).verdict
    selected_comparison = _comparison_metrics(window, result)
    base.update(
        {
            "outcome": verdict,
            "verdict": verdict,
            "supported": True,
            "comparable": True,
            "unsupported_input": None,
            "infeasible": None,
            "solver": _geometry_metrics(result.notes, profile),
            "comparison": selected_comparison,
            "selected_comparison": selected_comparison,
            **_green_pool_metrics(window, result, search_outcome.green_pool),
        }
    )
    return base


def evaluate_selection(
    selection: WindowSelection,
    *,
    profile: Profile = MEDIAN_HAND,
    beam: int = 16,
    beats_per_bar: int = 4,
) -> dict[str, object]:
    if isinstance(selection, WindowConstructionRejection):
        return _construction_rejection_result(selection)
    return evaluate_window(
        selection,
        profile=profile,
        beam=beam,
        beats_per_bar=beats_per_bar,
    )


def _requested_splits(requested: RequestedSplit) -> tuple[Split, ...]:
    if requested == "all":
        return ("train", "dev", "test", "held-out")
    return (requested,)


def _documents_for_split(
    config: EvaluationConfig, split: Split
) -> Iterator[CorpusDocument]:
    if split in ("train", "dev", "test"):
        yield from iter_guitarset_documents(
            config.guitarset_zip,
            split=split,
        )
    else:
        yield from iter_egset12_documents(config.egset12_dir)


def _windows_for_split(
    config: EvaluationConfig, split: Split
) -> Iterator[WindowSelection]:
    emitted = 0
    for document in _documents_for_split(config, split):
        selection = build_window(
            document,
            notes_per_window=config.notes_per_window,
            window_offset=config.window_offset,
            quantize_denominator=config.quantize_denominator,
        )
        # A preselected document always consumes one evaluation slot.  Rejected
        # construction is data, not a reason to substitute an easier recording.
        yield selection
        emitted += 1
        if emitted >= config.windows_per_split:
            return


def aggregate_windows(windows: Sequence[dict[str, object]]) -> dict[str, object]:
    outcomes = Counter(cast(str, window["outcome"]) for window in windows)
    supported_windows = sum(window.get("supported") is True for window in windows)
    comparable_windows = sum(window.get("comparable") is True for window in windows)
    supported_non_green_windows = sum(
        window.get("supported") is True and window.get("outcome") != "GREEN"
        for window in windows
    )
    pool_windows = [
        window for window in windows if cast(int, window.get("green_pool_size", 0)) > 0
    ]
    pool_sizes = [
        cast(int, window.get("green_pool_size", 0))
        for window in windows
        if window.get("supported") is True
    ]
    best_pool_comparisons = [
        cast(dict[str, object], comparison)
        for window in pool_windows
        if (comparison := window.get("best_in_pool_comparison")) is not None
    ]
    selected_vs_best = [
        cast(dict[str, object], comparison)
        for window in pool_windows
        if (comparison := window.get("selected_vs_best")) is not None
    ]
    unsupported_payloads = [
        cast(dict[str, object], payload)
        for window in windows
        if (payload := window.get("unsupported_input")) is not None
    ]
    unsupported_stages = Counter(
        cast(str, payload["stage"]) for payload in unsupported_payloads
    )
    unsupported_codes: Counter[str] = Counter()
    for payload in unsupported_payloads:
        diagnostics = cast(list[object], payload["diagnostics"])
        for diagnostic in diagnostics:
            unsupported_codes[cast(str, cast(dict[str, object], diagnostic)["code"])] += 1
    comparisons = [
        cast(dict[str, object], comparison)
        for window in windows
        if (comparison := window.get("comparison")) is not None
    ]
    solvers = [
        cast(dict[str, object], solver)
        for window in windows
        if (solver := window.get("solver")) is not None
    ]
    references = [
        cast(dict[str, object], reference)
        for window in windows
        if (reference := window.get("reference")) is not None
    ]
    notes_compared = sum(cast(int, item["notes_compared"]) for item in comparisons)

    def comparison_sum(field: str) -> int:
        return sum(cast(int, item[field]) for item in comparisons)

    def rate(numerator: int) -> float | None:
        return numerator / notes_compared if notes_compared else None

    string_exact = comparison_sum("string_exact_count")
    fret_exact = comparison_sum("fret_exact_count")
    joint_exact = comparison_sum("string_fret_exact_count")
    string_error = comparison_sum("string_absolute_error_sum")
    fret_error = comparison_sum("fret_absolute_error_sum")

    best_notes_compared = sum(
        cast(int, item["notes_compared"]) for item in best_pool_comparisons
    )
    best_joint_exact = sum(
        cast(int, item["string_fret_exact_count"]) for item in best_pool_comparisons
    )
    headrooms = [
        cast(dict[str, object], item["headroom"]) for item in selected_vs_best
    ]
    joint_gain = sum(cast(int, item["joint_exact_count_gain"]) for item in headrooms)

    def exposure_sum(metrics: Sequence[dict[str, object]]) -> Fraction:
        values = (
            cast(dict[str, object], item["duration_weighted_fret_exposure"])["exact"]
            for item in metrics
        )
        return sum((Fraction(cast(str, value)) for value in values), start=Fraction(0))

    runtime_ns = sum(cast(int, window["runtime_nanoseconds"]) for window in windows)
    return {
        "windows_total": len(windows),
        "supported_windows": supported_windows,
        "unsupported_windows": len(windows) - supported_windows,
        "comparable_windows": comparable_windows,
        "non_comparable_windows": len(windows) - comparable_windows,
        "unsupported_input_stage_counts": dict(sorted(unsupported_stages.items())),
        "unsupported_input_code_counts": dict(sorted(unsupported_codes.items())),
        "green_windows": outcomes["GREEN"],
        "non_green_windows": len(windows) - outcomes["GREEN"],
        "supported_non_green_windows": supported_non_green_windows,
        "outcome_counts": dict(sorted(outcomes.items())),
        "green_pool_windows": len(pool_windows),
        "green_pool_coverage_supported_rate": (
            len(pool_windows) / supported_windows if supported_windows else None
        ),
        "green_pool_size_total": sum(pool_sizes),
        "green_pool_size_mean_supported": (
            sum(pool_sizes) / supported_windows if supported_windows else None
        ),
        "green_pool_size_mean_covered": (
            sum(pool_sizes) / len(pool_windows) if pool_windows else None
        ),
        "best_in_pool_notes_compared": best_notes_compared,
        "best_in_pool_string_fret_exact_count": best_joint_exact,
        "best_in_pool_string_fret_exact_rate": (
            best_joint_exact / best_notes_compared if best_notes_compared else None
        ),
        "selected_regret_windows": sum(
            cast(bool, item["has_imitation_regret"]) for item in selected_vs_best
        ),
        "selected_vs_best_joint_exact_count_gain": joint_gain,
        "selected_vs_best_joint_exact_rate_gain": (
            joint_gain / best_notes_compared if best_notes_compared else None
        ),
        "selected_vs_best_string_distance_reduction": sum(
            cast(int, item["string_distance_reduction"]) for item in headrooms
        ),
        "selected_vs_best_fret_distance_reduction": sum(
            cast(int, item["fret_distance_reduction"]) for item in headrooms
        ),
        "runtime_nanoseconds_total": runtime_ns,
        "runtime_ms_total": runtime_ns / 1_000_000.0,
        "runtime_ms_mean": runtime_ns / 1_000_000.0 / len(windows) if windows else None,
        "notes_compared": notes_compared,
        "string_exact_count": string_exact,
        "string_exact_rate": rate(string_exact),
        "fret_exact_count": fret_exact,
        "fret_exact_rate": rate(fret_exact),
        "string_fret_exact_count": joint_exact,
        "string_fret_exact_rate": rate(joint_exact),
        "string_absolute_error_sum": string_error,
        "fret_absolute_error_sum": fret_error,
        "fret_mae": rate(fret_error),
        "solver_max_fret": max(
            (cast(int, item["max_fret"]) for item in solvers),
            default=None,
        ),
        "reference_max_fret": max(
            (cast(int, item["max_fret"]) for item in references),
            default=None,
        ),
        "solver_duration_weighted_fret_exposure": _fraction_metric(
            exposure_sum(solvers)
        ),
        "reference_duration_weighted_fret_exposure": _fraction_metric(
            exposure_sum(references)
        ),
        "solver_shift_count": sum(cast(int, item["shift_count"]) for item in solvers),
        "reference_shift_count": sum(
            cast(int, item["shift_count"]) for item in references
        ),
        "solver_shift_distance_micrometres": sum(
            cast(int, item["shift_distance_micrometres"]) for item in solvers
        ),
        "reference_shift_distance_micrometres": sum(
            cast(int, item["shift_distance_micrometres"]) for item in references
        ),
        "solver_shift_distance_mm": sum(
            cast(int, item["shift_distance_micrometres"]) for item in solvers
        )
        / 1_000.0,
        "reference_shift_distance_mm": sum(
            cast(int, item["shift_distance_micrometres"]) for item in references
        )
        / 1_000.0,
    }


def _guitarset_sampling_protocol_payload() -> dict[str, object]:
    return {
        "id": GUITARSET_SAMPLING_PROTOCOL,
        "group_key": ["performer", "mode"],
        "mode_order": list(GUITARSET_MODE_ORDER),
        "group_order_by_split": {
            split: [
                f"{performer}/{mode}"
                for performer, performer_split in GUITARSET_PERFORMER_SPLITS.items()
                if performer_split == split
                for mode in GUITARSET_MODE_ORDER
            ]
            for split in ("train", "dev", "test")
        },
        "within_group_order": "filename_ascending",
        "traversal": "round_robin_one_member_per_group_per_round",
    }


def _path_provenance(config: EvaluationConfig, splits: Sequence[Split]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    if any(split in ("train", "dev", "test") for split in splits):
        raw = config.guitarset_zip.read_bytes()
        result.append(
            {
                "corpus": "guitarset",
                "corpus_id": GUITARSET_CORPUS_ID,
                "role": "train/dev/test",
                "performer_splits": dict(sorted(GUITARSET_PERFORMER_SPLITS.items())),
                "sampling_protocol": _guitarset_sampling_protocol_payload(),
                "path": str(config.guitarset_zip),
                "sha256": _sha256(raw),
                "license": CORPUS_LICENSE,
                "record_url": GUITARSET_RECORD_URL,
            }
        )
    if "held-out" in splits:
        paths = sorted(config.egset12_dir.glob("*.jams"))
        digest = hashlib.sha256()
        files: list[dict[str, str]] = []
        for path in paths:
            raw = path.read_bytes()
            file_sha = _sha256(raw)
            digest.update(path.name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(raw)
            files.append({"name": path.name, "sha256": file_sha})
        result.append(
            {
                "corpus": "egset12",
                "corpus_id": EGSET12_CORPUS_ID,
                "role": "external-audit",
                "path": str(config.egset12_dir),
                "sha256": digest.hexdigest(),
                "files": files,
                "license": CORPUS_LICENSE,
                "record_url": EGSET12_RECORD_URL,
            }
        )
    return result


def run_evaluation(
    config: EvaluationConfig,
    *,
    profile: Profile = MEDIAN_HAND,
) -> dict[str, object]:
    config.validate()
    splits = _requested_splits(config.requested_split)
    windows: list[dict[str, object]] = []
    for split in splits:
        selected = list(_windows_for_split(config, split))
        if len(selected) != config.windows_per_split:
            raise ValueError(
                f"split {split} produced {len(selected)} windows; "
                f"requested {config.windows_per_split}"
            )
        windows.extend(
            evaluate_selection(
                selection,
                profile=profile,
                beam=config.beam,
                beats_per_bar=config.beats_per_bar,
            )
            for selection in selected
        )
    return {
        "schema": EVALUATION_SCHEMA,
        "provenance_label": config.provenance_label,
        "provenance": _path_provenance(config, splits),
        "configuration": {
            "requested_split": config.requested_split,
            "splits": list(splits),
            "windows_per_split": config.windows_per_split,
            "notes_per_window": config.notes_per_window,
            "window_offset": config.window_offset,
            "quantize_denominator": config.quantize_denominator,
            "beam": config.beam,
            "beats_per_bar": config.beats_per_bar,
            "standard_tuning": list(STANDARD_TUNING),
            "capo": 0,
            "guitarset_performer_splits": dict(
                sorted(GUITARSET_PERFORMER_SPLITS.items())
            ),
            "guitarset_sampling_protocol": GUITARSET_SAMPLING_PROTOCOL,
            "egset12_access_policy": "explicit-held-out-or-all-only",
            "profile_version": profile.version,
            "profile_fingerprint": profile.fingerprint,
            "fingering_solver_version": FINGERING_SOLVER_VERSION,
        },
        "windows": windows,
        "aggregate": aggregate_windows(windows),
        "aggregate_by_split": {
            split: aggregate_windows(
                [window for window in windows if window["split"] == split]
            )
            for split in splits
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--guitarset-zip", type=Path, default=DEFAULT_GUITARSET_ZIP)
    parser.add_argument("--egset12-dir", type=Path, default=DEFAULT_EGSET12_DIR)
    parser.add_argument(
        "--split",
        choices=("train", "dev", "test", "held-out", "all"),
        default="train",
        help="train/dev/test use disjoint GuitarSet performers; held-out uses EGSet12",
    )
    parser.add_argument("--windows-per-split", type=int, default=1)
    parser.add_argument("--notes-per-window", type=int, default=8)
    parser.add_argument("--window-offset", type=int, default=0)
    parser.add_argument("--quantize-denominator", type=int, default=96)
    parser.add_argument("--beam", type=int, default=16)
    parser.add_argument("--beats-per-bar", type=int, default=4)
    parser.add_argument("--provenance-label", default="local-offline-quality-eval")
    parser.add_argument(
        "--output",
        type=Path,
        help="write JSON here instead of stdout; corpus inputs remain read-only",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = EvaluationConfig(
        guitarset_zip=args.guitarset_zip,
        egset12_dir=args.egset12_dir,
        requested_split=args.split,
        windows_per_split=args.windows_per_split,
        notes_per_window=args.notes_per_window,
        window_offset=args.window_offset,
        quantize_denominator=args.quantize_denominator,
        beam=args.beam,
        beats_per_bar=args.beats_per_bar,
        provenance_label=args.provenance_label,
    )
    try:
        report = run_evaluation(config)
    except (CorpusDataError, FileNotFoundError, ValueError, zipfile.BadZipFile) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    rendered = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    if args.output is None:
        sys.stdout.write(rendered)
    else:
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
