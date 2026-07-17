"""Deterministic corpus split and contamination audit for benchmark v2.

The audit deliberately reports two independent pools: procedural items and every
non-procedural (real-source/checker) item. A relationship in one report can never
borrow an item or denominator from the other. A separate cross-pool collision gate
rejects musical overlap without creating pooled statistics. Musical variants are
retained as typed detections; they are violations only when they escaped their family
(or split), while byte-for-byte musical duplicates are always violations.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from itertools import combinations
from typing import TypeAlias, cast

from fretsure.bench.corpus import CorpusItem, snapshot_corpus_item
from fretsure.ir import ChordSymbol, MusicIR, Note

_NEAR_DUPLICATE_NUMERATOR = 9
_NEAR_DUPLICATE_DENOMINATOR = 10
NEAR_DUPLICATE_SIMILARITY = _NEAR_DUPLICATE_NUMERATOR / _NEAR_DUPLICATE_DENOMINATOR


class ContaminationInputError(ValueError):
    """One audit-only input does not satisfy the small typed contract."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid contamination audit {field}: {detail}")


class CorpusStratum(StrEnum):
    CROSS = "cross"
    REAL = "real"
    PROCEDURAL = "procedural"


class ContaminationKind(StrEnum):
    FAMILY_SPLIT = "family_split"
    EXACT_DUPLICATE = "exact_duplicate"
    NEAR_DUPLICATE = "near_duplicate"
    TRANSPOSITION_VARIANT = "transposition_variant"
    TEMPO_VARIANT = "tempo_variant"
    CANARY_LEAKAGE = "canary_leakage"
    ITEM_OVERLAP = "item_overlap"
    PRODUCER_DUPLICATE = "producer_duplicate"


@dataclass(frozen=True, slots=True)
class CanaryDocument:
    """One text in which benchmark canaries must not occur."""

    document_id: str
    text: str


@dataclass(frozen=True, slots=True)
class ContaminationFinding:
    kind: ContaminationKind
    stratum: CorpusStratum
    item_ids: tuple[str, ...]
    family_ids: tuple[str, ...]
    splits: tuple[str, ...]
    references: tuple[str, ...]
    evidence: str
    is_violation: bool


@dataclass(frozen=True, slots=True)
class StratumContaminationReport:
    stratum: CorpusStratum
    item_count: int
    split_counts: tuple[tuple[str, int], ...]
    findings: tuple[ContaminationFinding, ...]

    @property
    def violations(self) -> tuple[ContaminationFinding, ...]:
        return tuple(finding for finding in self.findings if finding.is_violation)

    @property
    def clean(self) -> bool:
        return not self.violations


@dataclass(frozen=True, slots=True)
class ContaminationReport:
    """Two separate reports plus a denominator-free cross-stratum collision gate."""

    real: StratumContaminationReport
    procedural: StratumContaminationReport
    cross_stratum_findings: tuple[ContaminationFinding, ...]

    @property
    def cross_stratum_clean(self) -> bool:
        return not self.cross_stratum_findings

    @property
    def clean(self) -> bool:
        return self.real.clean and self.procedural.clean and self.cross_stratum_clean

    def for_stratum(self, stratum: CorpusStratum) -> StratumContaminationReport:
        if stratum is CorpusStratum.REAL:
            return self.real
        if stratum is CorpusStratum.PROCEDURAL:
            return self.procedural
        if stratum is CorpusStratum.CROSS:
            raise ContaminationInputError(
                "stratum",
                "the cross-stratum gate has no denominator-bearing report",
            )
        raise ContaminationInputError("stratum", "must be a CorpusStratum")


_JsonAtom: TypeAlias = str | int | float
_JsonValue: TypeAlias = _JsonAtom | list["_JsonValue"]
_EventToken: TypeAlias = tuple[object, ...]


@dataclass(frozen=True, slots=True)
class _IndexedItem:
    serial: int
    item: CorpusItem
    exact_signature: str
    tempo_signature: str
    transposition_signature: str | None
    absolute_events_signature: str
    events: tuple[_EventToken, ...]
    transposed_events: tuple[_EventToken, ...]


def _stratum(item: CorpusItem) -> CorpusStratum:
    return CorpusStratum.PROCEDURAL if item.layer == "procedural" else CorpusStratum.REAL


def _fraction(value: Fraction | None) -> list[int] | None:
    if value is None:
        return None
    return [value.numerator, value.denominator]


def _ordered_notes(ir: MusicIR) -> tuple[Note, ...]:
    return tuple(
        sorted(
            ir.notes,
            key=lambda note: (
                note.onset,
                note.voice,
                note.pitch,
                note.duration,
            ),
        )
    )


def _ordered_chords(ir: MusicIR) -> tuple[ChordSymbol, ...]:
    return tuple(
        sorted(
            ir.chords,
            key=lambda chord: (
                chord.onset,
                chord.root_pc,
                tuple(sorted(chord.pitch_classes)),
            ),
        )
    )


def _musical_payload(
    ir: MusicIR,
    *,
    include_tempo: bool,
    transpose_to_origin: bool,
) -> list[_JsonValue]:
    notes = _ordered_notes(ir)
    chords = _ordered_chords(ir)
    anchor: int | None = None
    if transpose_to_origin:
        if notes:
            anchor = notes[0].pitch
        elif chords:
            anchor = chords[0].root_pc
        else:
            return []
    pitch_anchor = 0 if anchor is None else anchor
    pitch_class_anchor = pitch_anchor % 12
    payload: list[_JsonValue] = [
        [ir.meta.time_sig[0], ir.meta.time_sig[1]],
        cast(_JsonValue, _fraction(ir.meta.duration_beats)),
    ]
    if include_tempo:
        payload.append(ir.meta.tempo_bpm)
    payload.append(
        [
            [
                cast(_JsonValue, _fraction(note.onset)),
                cast(_JsonValue, _fraction(note.duration)),
                note.pitch - pitch_anchor if transpose_to_origin else note.pitch,
                note.voice,
            ]
            for note in notes
        ]
    )
    payload.append(
        [
            [
                cast(_JsonValue, _fraction(chord.onset)),
                (
                    (chord.root_pc - pitch_class_anchor) % 12
                    if transpose_to_origin
                    else chord.root_pc
                ),
                (
                    sorted(
                        (pitch_class - pitch_class_anchor) % 12
                        for pitch_class in chord.pitch_classes
                    )
                    if transpose_to_origin
                    else sorted(chord.pitch_classes)
                ),
            ]
            for chord in chords
        ]
    )
    return payload


def _signature(label: str, payload: list[_JsonValue]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("ascii")
    digest = hashlib.sha256()
    digest.update(f"fretsure:contamination:{label}@0.1.0\0".encode("ascii"))
    digest.update(encoded)
    return digest.hexdigest()


def _event_tokens(ir: MusicIR, *, transpose_to_origin: bool) -> tuple[_EventToken, ...]:
    notes = _ordered_notes(ir)
    chords = _ordered_chords(ir)
    anchor: int | None = None
    if transpose_to_origin:
        if notes:
            anchor = notes[0].pitch
        elif chords:
            anchor = chords[0].root_pc
    pitch_anchor = 0 if anchor is None else anchor
    pitch_class_anchor = pitch_anchor % 12
    note_tokens = tuple(
        (
            "note",
            note.onset.numerator,
            note.onset.denominator,
            note.duration.numerator,
            note.duration.denominator,
            note.pitch - pitch_anchor if transpose_to_origin else note.pitch,
            note.voice,
        )
        for note in notes
    )
    chord_tokens = tuple(
        (
            "chord",
            chord.onset.numerator,
            chord.onset.denominator,
            ((chord.root_pc - pitch_class_anchor) % 12 if transpose_to_origin else chord.root_pc),
            (
                tuple(
                    sorted(
                        (pitch_class - pitch_class_anchor) % 12
                        for pitch_class in chord.pitch_classes
                    )
                )
                if transpose_to_origin
                else tuple(sorted(chord.pitch_classes))
            ),
        )
        for chord in chords
    )
    return note_tokens + chord_tokens


def _index_item(serial: int, item: CorpusItem) -> _IndexedItem:
    exact_payload = _musical_payload(
        item.ir,
        include_tempo=True,
        transpose_to_origin=False,
    )
    tempo_payload = _musical_payload(
        item.ir,
        include_tempo=False,
        transpose_to_origin=False,
    )
    transposition_payload = _musical_payload(
        item.ir,
        include_tempo=False,
        transpose_to_origin=True,
    )
    events = _event_tokens(item.ir, transpose_to_origin=False)
    transposed_events = _event_tokens(item.ir, transpose_to_origin=True)
    return _IndexedItem(
        serial=serial,
        item=item,
        exact_signature=_signature("exact", exact_payload),
        tempo_signature=_signature("tempo", tempo_payload),
        transposition_signature=(
            _signature("transposition", transposition_payload) if transposition_payload else None
        ),
        absolute_events_signature=_signature("events", tempo_payload[2:]),
        events=events,
        transposed_events=transposed_events,
    )


def _snapshot_items(value: object) -> tuple[CorpusItem, ...]:
    if type(value) not in (tuple, list):
        raise ContaminationInputError("items", "must be an exact tuple or list")
    raw = cast(tuple[object, ...] | list[object], value)
    snapshots = tuple(snapshot_corpus_item(item) for item in raw)
    return tuple(
        sorted(
            snapshots,
            key=lambda item: (
                item.item_id,
                cast(int, item.position),
                cast(str, item.family_id),
                cast(str, item.cluster_id),
            ),
        )
    )


def _snapshot_documents(value: object) -> tuple[CanaryDocument, ...]:
    if type(value) not in (tuple, list):
        raise ContaminationInputError(
            "canary_documents",
            "must be an exact tuple or list",
        )
    raw = cast(tuple[object, ...] | list[object], value)
    documents: list[CanaryDocument] = []
    seen: set[str] = set()
    for index, document in enumerate(raw):
        if type(document) is not CanaryDocument:
            raise ContaminationInputError(
                f"canary_documents[{index}]",
                "must be an exact CanaryDocument",
            )
        if (
            type(document.document_id) is not str
            or not document.document_id
            or len(document.document_id) > 128
        ):
            raise ContaminationInputError(
                f"canary_documents[{index}].document_id",
                "must be a nonempty string of at most 128 characters",
            )
        if type(document.text) is not str:
            raise ContaminationInputError(
                f"canary_documents[{index}].text",
                "must be an exact string",
            )
        if document.document_id in seen:
            raise ContaminationInputError(
                f"canary_documents[{index}].document_id",
                "must be unique",
            )
        seen.add(document.document_id)
        documents.append(CanaryDocument(document.document_id, document.text))
    return tuple(sorted(documents, key=lambda document: document.document_id))


def _finding(
    kind: ContaminationKind,
    stratum: CorpusStratum,
    members: tuple[_IndexedItem, ...],
    *,
    evidence: str,
    references: tuple[str, ...] = (),
    always_violation: bool,
) -> ContaminationFinding:
    item_ids = tuple(sorted(member.item.item_id for member in members))
    family_ids = tuple(sorted({cast(str, member.item.family_id) for member in members}))
    splits = tuple(
        sorted(
            {
                member.item.provenance.split
                for member in members
                if member.item.provenance is not None
            }
        )
    )
    return ContaminationFinding(
        kind=kind,
        stratum=stratum,
        item_ids=item_ids,
        family_ids=family_ids,
        splits=splits,
        references=tuple(sorted(references)),
        evidence=evidence,
        is_violation=always_violation or len(family_ids) > 1 or len(splits) > 1,
    )


def _grouped(
    indexed: tuple[_IndexedItem, ...],
    key: Callable[[_IndexedItem], Hashable | None],
) -> tuple[tuple[Hashable, tuple[_IndexedItem, ...]], ...]:
    groups: dict[Hashable, list[_IndexedItem]] = defaultdict(list)
    for member in indexed:
        group_key = key(member)
        if group_key is not None:
            groups[group_key].append(member)
    return tuple(
        (group_key, tuple(members))
        for group_key, members in sorted(groups.items(), key=lambda pair: repr(pair[0]))
        if len(members) > 1
    )


def _dice_similarity(
    left: tuple[_EventToken, ...],
    right: tuple[_EventToken, ...],
) -> tuple[int, int]:
    denominator = len(left) + len(right)
    if denominator == 0:
        return (0, 0)
    overlap = sum((Counter(left) & Counter(right)).values())
    return (2 * overlap, denominator)


def _audit_stratum(
    stratum: CorpusStratum,
    items: tuple[CorpusItem, ...],
    documents: tuple[CanaryDocument, ...],
) -> StratumContaminationReport:
    indexed = tuple(_index_item(serial, item) for serial, item in enumerate(items))
    findings: list[ContaminationFinding] = []

    for family, members in _grouped(indexed, lambda value: value.item.family_id):
        splits = {
            member.item.provenance.split for member in members if member.item.provenance is not None
        }
        if len(splits) > 1:
            findings.append(
                _finding(
                    ContaminationKind.FAMILY_SPLIT,
                    stratum,
                    members,
                    evidence=cast(str, family),
                    always_violation=True,
                )
            )

    for item_id, members in _grouped(indexed, lambda value: value.item.item_id):
        findings.append(
            _finding(
                ContaminationKind.ITEM_OVERLAP,
                stratum,
                members,
                evidence=cast(str, item_id),
                always_violation=True,
            )
        )

    related_pairs: set[tuple[int, int]] = set()
    for signature, members in _grouped(indexed, lambda value: value.exact_signature):
        findings.append(
            _finding(
                ContaminationKind.EXACT_DUPLICATE,
                stratum,
                members,
                evidence=cast(str, signature),
                always_violation=True,
            )
        )
        related_pairs.update(
            (min(left.serial, right.serial), max(left.serial, right.serial))
            for left, right in combinations(members, 2)
        )

    for signature, members in _grouped(indexed, lambda value: value.tempo_signature):
        if len({member.item.ir.meta.tempo_bpm for member in members}) <= 1:
            continue
        findings.append(
            _finding(
                ContaminationKind.TEMPO_VARIANT,
                stratum,
                members,
                evidence=cast(str, signature),
                always_violation=False,
            )
        )
        related_pairs.update(
            (min(left.serial, right.serial), max(left.serial, right.serial))
            for left, right in combinations(members, 2)
        )

    for signature, members in _grouped(
        indexed,
        lambda value: value.transposition_signature,
    ):
        if len({member.absolute_events_signature for member in members}) <= 1:
            continue
        findings.append(
            _finding(
                ContaminationKind.TRANSPOSITION_VARIANT,
                stratum,
                members,
                evidence=cast(str, signature),
                always_violation=False,
            )
        )
        related_pairs.update(
            (min(left.serial, right.serial), max(left.serial, right.serial))
            for left, right in combinations(members, 2)
        )

    for left, right in combinations(indexed, 2):
        pair = (min(left.serial, right.serial), max(left.serial, right.serial))
        if pair in related_pairs:
            continue
        if left.item.ir.meta.time_sig != right.item.ir.meta.time_sig:
            continue
        absolute = _dice_similarity(left.events, right.events)
        transposed = _dice_similarity(left.transposed_events, right.transposed_events)
        if absolute[1] == 0:
            numerator, denominator = transposed
        elif transposed[1] == 0:
            numerator, denominator = absolute
        elif absolute[0] * transposed[1] >= transposed[0] * absolute[1]:
            numerator, denominator = absolute
        else:
            numerator, denominator = transposed
        if (
            denominator == 0
            or _NEAR_DUPLICATE_DENOMINATOR * numerator < _NEAR_DUPLICATE_NUMERATOR * denominator
        ):
            continue
        findings.append(
            _finding(
                ContaminationKind.NEAR_DUPLICATE,
                stratum,
                (left, right),
                evidence=f"dice:{numerator}/{denominator}",
                always_violation=False,
            )
        )

    for identity, members in _grouped(
        indexed,
        lambda value: (
            value.item.provenance.root_sha256 or value.item.provenance.source_sha256
            if value.item.provenance is not None
            else None
        ),
    ):
        digest = cast(str, identity)
        producers = tuple(
            sorted(
                {
                    member.item.provenance.producer
                    for member in members
                    if member.item.provenance is not None
                    and member.item.provenance.producer is not None
                }
            )
        )
        findings.append(
            _finding(
                ContaminationKind.PRODUCER_DUPLICATE,
                stratum,
                members,
                evidence=digest,
                references=producers,
                always_violation=True,
            )
        )

    for member in indexed:
        assert member.item.canary is not None
        references = tuple(
            document.document_id for document in documents if member.item.canary in document.text
        )
        if references:
            findings.append(
                _finding(
                    ContaminationKind.CANARY_LEAKAGE,
                    stratum,
                    (member,),
                    evidence=member.item.canary,
                    references=references,
                    always_violation=True,
                )
            )

    findings.sort(
        key=lambda finding: (
            finding.kind.value,
            finding.item_ids,
            finding.family_ids,
            finding.splits,
            finding.references,
            finding.evidence,
        )
    )
    split_counts = Counter(item.provenance.split for item in items if item.provenance is not None)
    return StratumContaminationReport(
        stratum=stratum,
        item_count=len(items),
        split_counts=tuple(sorted(split_counts.items())),
        findings=tuple(findings),
    )


def _cross_stratum_findings(
    real: tuple[CorpusItem, ...],
    procedural: tuple[CorpusItem, ...],
) -> tuple[ContaminationFinding, ...]:
    real_index = tuple(_index_item(index, item) for index, item in enumerate(real))
    procedural_index = tuple(
        _index_item(len(real_index) + index, item) for index, item in enumerate(procedural)
    )
    findings: list[ContaminationFinding] = []
    for left in real_index:
        for right in procedural_index:
            members = (left, right)
            if left.item.item_id == right.item.item_id:
                findings.append(
                    _finding(
                        ContaminationKind.ITEM_OVERLAP,
                        CorpusStratum.CROSS,
                        members,
                        evidence=left.item.item_id,
                        always_violation=True,
                    )
                )
            left_provenance = left.item.provenance
            right_provenance = right.item.provenance
            assert left_provenance is not None and right_provenance is not None
            left_source = left_provenance.root_sha256 or left_provenance.source_sha256
            right_source = right_provenance.root_sha256 or right_provenance.source_sha256
            if left_source is not None and left_source == right_source:
                findings.append(
                    _finding(
                        ContaminationKind.PRODUCER_DUPLICATE,
                        CorpusStratum.CROSS,
                        members,
                        evidence=left_source,
                        references=tuple(
                            sorted(
                                producer
                                for producer in {
                                    left_provenance.producer,
                                    right_provenance.producer,
                                }
                                if producer is not None
                            )
                        ),
                        always_violation=True,
                    )
                )
            if left.exact_signature == right.exact_signature:
                findings.append(
                    _finding(
                        ContaminationKind.EXACT_DUPLICATE,
                        CorpusStratum.CROSS,
                        members,
                        evidence=left.exact_signature,
                        always_violation=True,
                    )
                )
                continue
            if (
                left.tempo_signature == right.tempo_signature
                and left.item.ir.meta.tempo_bpm != right.item.ir.meta.tempo_bpm
            ):
                findings.append(
                    _finding(
                        ContaminationKind.TEMPO_VARIANT,
                        CorpusStratum.CROSS,
                        members,
                        evidence=left.tempo_signature,
                        always_violation=True,
                    )
                )
                continue
            if (
                left.transposition_signature is not None
                and left.transposition_signature == right.transposition_signature
                and left.absolute_events_signature != right.absolute_events_signature
            ):
                findings.append(
                    _finding(
                        ContaminationKind.TRANSPOSITION_VARIANT,
                        CorpusStratum.CROSS,
                        members,
                        evidence=left.transposition_signature,
                        always_violation=True,
                    )
                )
                continue
            if left.item.ir.meta.time_sig != right.item.ir.meta.time_sig:
                continue
            absolute = _dice_similarity(left.events, right.events)
            transposed = _dice_similarity(left.transposed_events, right.transposed_events)
            candidates = tuple(value for value in (absolute, transposed) if value[1] != 0)
            if not candidates:
                continue
            numerator, denominator = max(
                candidates,
                key=lambda value: Fraction(value[0], value[1]),
            )
            if _NEAR_DUPLICATE_DENOMINATOR * numerator >= _NEAR_DUPLICATE_NUMERATOR * denominator:
                findings.append(
                    _finding(
                        ContaminationKind.NEAR_DUPLICATE,
                        CorpusStratum.CROSS,
                        members,
                        evidence=f"dice:{numerator}/{denominator}",
                        always_violation=True,
                    )
                )
    return tuple(
        sorted(
            findings,
            key=lambda finding: (
                finding.kind.value,
                finding.item_ids,
                finding.references,
                finding.evidence,
            ),
        )
    )


def audit_contamination(
    items: object,
    *,
    canary_documents: object = (),
) -> ContaminationReport:
    """Audit strict corpus items without ever pooling real and procedural data."""

    snapshots = _snapshot_items(items)
    documents = _snapshot_documents(canary_documents)
    real = tuple(item for item in snapshots if _stratum(item) is CorpusStratum.REAL)
    procedural = tuple(item for item in snapshots if _stratum(item) is CorpusStratum.PROCEDURAL)
    return ContaminationReport(
        real=_audit_stratum(CorpusStratum.REAL, real, documents),
        procedural=_audit_stratum(
            CorpusStratum.PROCEDURAL,
            procedural,
            documents,
        ),
        cross_stratum_findings=_cross_stratum_findings(real, procedural),
    )


__all__ = [
    "NEAR_DUPLICATE_SIMILARITY",
    "CanaryDocument",
    "ContaminationFinding",
    "ContaminationInputError",
    "ContaminationKind",
    "ContaminationReport",
    "CorpusStratum",
    "StratumContaminationReport",
    "audit_contamination",
]
