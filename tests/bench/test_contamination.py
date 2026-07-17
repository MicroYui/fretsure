from __future__ import annotations

import hashlib
from dataclasses import replace
from fractions import Fraction as F

import pytest

from fretsure.bench.contamination import (
    CanaryDocument,
    ContaminationFinding,
    ContaminationInputError,
    ContaminationKind,
    ContaminationReport,
    CorpusStratum,
    audit_contamination,
)
from fretsure.bench.corpus import (
    CorpusItem,
    CorpusProvenance,
    EvidenceAvailability,
    LicenseProvenance,
    ProceduralCorpusConfig,
    build_primary_procedural_corpus,
)
from fretsure.ir import ChordSymbol, Meta, MusicIR, Note


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _ir(
    pitches: tuple[int, ...],
    *,
    tempo: float = 90.0,
    title: str = "fixture",
) -> MusicIR:
    return MusicIR(
        notes=tuple(Note(F(index), F(1), pitch, "melody") for index, pitch in enumerate(pitches)),
        chords=(),
        meta=Meta(
            key="C",
            time_sig=(4, 4),
            tempo_bpm=tempo,
            source="contamination-test",
            title=title,
            license="CC0-1.0",
            duration_beats=F(max(len(pitches), 1)),
        ),
    )


def _polyphony(ir: MusicIR) -> str:
    events = sorted(
        (
            (time, delta)
            for note in ir.notes
            for time, delta in ((note.onset, 1), (note.onset + note.duration, -1))
        ),
        key=lambda event: (event[0], event[1]),
    )
    sounding = 0
    maximum = 0
    for _time, delta in events:
        sounding += delta
        maximum = max(maximum, sounding)
    return "monophonic" if maximum <= 1 else "polyphonic"


def _real_item(
    item_id: str,
    pitches: tuple[int, ...],
    *,
    position: int,
    family: str,
    split: str = "test",
    tempo: float = 90.0,
    producer: str = "fixture-producer",
    source_digest: str | None = None,
    root_digest: str | None = None,
    canary: str | None = None,
    ir: MusicIR | None = None,
) -> CorpusItem:
    music = _ir(pitches, tempo=tempo, title=item_id) if ir is None else ir
    return CorpusItem(
        ir=music,
        layer="public_leadsheet",
        genre="fixture",
        difficulty=0,
        item_id=item_id,
        family_id=family,
        cluster_id=f"cluster-{family}",
        position=position,
        provenance=CorpusProvenance(
            source_format="musicxml",
            source_sha256=source_digest or _digest(f"source:{item_id}:{position}"),
            root_sha256=root_digest or _digest(f"root:{item_id}:{position}"),
            router_version="score-input@0.1.0",
            importer_version="musicxml@0.3.0",
            container_version=None,
            source_url=f"https://example.test/{item_id}-{position}.musicxml",
            producer=producer,
            retrieval_date="2026-07-17",
            license=LicenseProvenance(
                expression="CC0-1.0",
                status="verified",
                redistribution=True,
                derivatives=True,
                provider_submission=True,
            ),
            split=split,
            role_map=(("track:0", "melody"),),
            normalization=("explicit-role-map",),
            generator=None,
        ),
        evidence=EvidenceAvailability(
            melody=any(note.voice == "melody" for note in music.notes),
            bass=bool(music.chords),
            harmony=bool(music.chords)
            or any(note.voice in {"bass", "harmony"} for note in music.notes),
        ),
        synthetic_complexity="unrated",
        polyphony=_polyphony(music),
        canary=canary or f"canary:{item_id}:{position}",
    )


def _findings(
    report: ContaminationReport,
    kind: ContaminationKind,
) -> tuple[ContaminationFinding, ...]:
    return tuple(finding for finding in report.real.findings if finding.kind is kind)


def test_family_first_split_is_a_typed_violation() -> None:
    first = _real_item("first", (60, 62, 64), position=0, family="shared", split="train")
    second = _real_item(
        "second",
        (60, 65, 61, 70, 63),
        position=1,
        family="shared",
        split="test",
    )

    report = audit_contamination((first, second))
    finding = _findings(report, ContaminationKind.FAMILY_SPLIT)[0]

    assert finding.stratum is CorpusStratum.REAL
    assert finding.family_ids == ("shared",)
    assert finding.splits == ("test", "train")
    assert finding.is_violation is True
    assert report.real.clean is False


def test_exact_duplicate_is_always_a_violation_even_inside_one_family() -> None:
    first = _real_item("exact-a", (60, 62, 64), position=0, family="family")
    second = _real_item("exact-b", (60, 62, 64), position=1, family="family")

    report = audit_contamination((first, second))
    finding = _findings(report, ContaminationKind.EXACT_DUPLICATE)[0]

    assert finding.item_ids == ("exact-a", "exact-b")
    assert finding.is_violation is True
    assert not _findings(report, ContaminationKind.NEAR_DUPLICATE)


def test_near_duplicate_uses_a_frozen_nine_tenths_event_overlap() -> None:
    first = _real_item(
        "near-a",
        (60, 61, 62, 63, 64, 65, 66, 67, 68, 69),
        position=0,
        family="family-a",
    )
    second = _real_item(
        "near-b",
        (60, 61, 62, 63, 64, 65, 66, 67, 68, 70),
        position=1,
        family="family-b",
    )

    finding = _findings(
        audit_contamination((first, second)),
        ContaminationKind.NEAR_DUPLICATE,
    )[0]

    assert finding.evidence == "dice:18/20"
    assert finding.is_violation is True


def test_tempo_and_transposition_variants_are_detected_before_near_duplicates() -> None:
    base = _real_item("base", (60, 62, 65), position=0, family="base-family")
    tempo = _real_item(
        "tempo",
        (60, 62, 65),
        position=1,
        family="tempo-family",
        tempo=120.0,
    )
    transposed = _real_item(
        "transposed",
        (65, 67, 70),
        position=2,
        family="transposed-family",
    )

    report = audit_contamination((base, tempo, transposed))
    tempo_finding = _findings(report, ContaminationKind.TEMPO_VARIANT)[0]
    transposition_finding = _findings(
        report,
        ContaminationKind.TRANSPOSITION_VARIANT,
    )[0]

    assert tempo_finding.item_ids == ("base", "tempo")
    assert tempo_finding.is_violation is True
    assert transposition_finding.item_ids == ("base", "tempo", "transposed")
    assert transposition_finding.is_violation is True
    assert not _findings(report, ContaminationKind.NEAR_DUPLICATE)


def test_chord_transposition_is_canonical_across_pitch_class_wraparound() -> None:
    c_major = replace(
        _ir((60,)),
        chords=(ChordSymbol(F(0), "C", frozenset({0, 4, 7}), 0),),
    )
    f_major = replace(
        _ir((65,)),
        chords=(ChordSymbol(F(0), "F", frozenset({0, 5, 9}), 5),),
    )
    first = _real_item(
        "chord-c",
        (),
        position=0,
        family="family-c",
        ir=c_major,
    )
    second = _real_item(
        "chord-f",
        (),
        position=1,
        family="family-f",
        ir=f_major,
    )

    finding = _findings(
        audit_contamination((first, second)),
        ContaminationKind.TRANSPOSITION_VARIANT,
    )[0]

    assert finding.item_ids == ("chord-c", "chord-f")
    assert finding.is_violation is True


@pytest.mark.parametrize(
    ("kind", "first", "second"),
    [
        (
            ContaminationKind.ITEM_OVERLAP,
            _real_item("overlap", (60, 62), position=0, family="family-a"),
            _real_item("overlap", (60, 66, 63), position=1, family="family-b"),
        ),
        (
            ContaminationKind.PRODUCER_DUPLICATE,
            _real_item(
                "producer-a",
                (60, 62),
                position=0,
                family="family-a",
                root_digest=_digest("shared-producer-root"),
            ),
            _real_item(
                "producer-b",
                (60, 66, 63),
                position=1,
                family="family-b",
                producer="other-producer",
                root_digest=_digest("shared-producer-root"),
            ),
        ),
    ],
)
def test_item_overlap_and_producer_duplicates_are_explicit_typed_findings(
    kind: ContaminationKind,
    first: CorpusItem,
    second: CorpusItem,
) -> None:
    finding = _findings(audit_contamination((first, second)), kind)[0]

    assert finding.kind is kind
    assert finding.is_violation is True
    if kind is ContaminationKind.PRODUCER_DUPLICATE:
        assert finding.references == ("fixture-producer", "other-producer")


def test_canary_leakage_names_every_matching_document() -> None:
    item = _real_item(
        "canary-item",
        (60, 62),
        position=0,
        family="family",
        canary="canary:do-not-leak",
    )
    documents = (
        CanaryDocument("prompt", "prefix canary:do-not-leak suffix"),
        CanaryDocument("training", "canary:do-not-leak"),
        CanaryDocument("clean", "nothing here"),
    )

    finding = _findings(
        audit_contamination((item,), canary_documents=documents),
        ContaminationKind.CANARY_LEAKAGE,
    )[0]

    assert finding.references == ("prompt", "training")
    assert finding.evidence == "canary:do-not-leak"
    assert finding.is_violation is True


def test_variants_controlled_inside_one_family_do_not_fail_family_first_gate() -> None:
    first = _real_item("variant-a", (60, 62, 65), position=0, family="family")
    second = _real_item("variant-b", (65, 67, 70), position=1, family="family")

    report = audit_contamination((first, second))
    finding = _findings(report, ContaminationKind.TRANSPOSITION_VARIANT)[0]

    assert finding.is_violation is False
    assert report.real.clean is True
    assert report.clean is True


def test_real_and_procedural_reports_stay_separate_while_cross_collision_fails() -> None:
    procedural = build_primary_procedural_corpus(ProceduralCorpusConfig(family_count=1))[0]
    real = _real_item(
        procedural.item_id,
        (),
        position=1,
        family=procedural.family_id or "family",
        ir=procedural.ir,
    )
    real = replace(real, canary=procedural.canary)

    report = audit_contamination((procedural, real))

    assert report.real.item_count == 1
    assert report.procedural.item_count == 1
    assert report.real.findings == ()
    assert report.procedural.findings == ()
    assert [finding.kind for finding in report.cross_stratum_findings] == [
        ContaminationKind.EXACT_DUPLICATE,
        ContaminationKind.ITEM_OVERLAP,
    ]
    assert all(
        finding.stratum is CorpusStratum.CROSS and finding.is_violation
        for finding in report.cross_stratum_findings
    )
    assert report.clean is False


def test_cross_stratum_gate_detects_transposed_procedural_music() -> None:
    procedural = build_primary_procedural_corpus(ProceduralCorpusConfig(family_count=1))[0]
    semitones = 5
    transposed = replace(
        procedural.ir,
        notes=tuple(replace(note, pitch=note.pitch + semitones) for note in procedural.ir.notes),
        chords=tuple(
            replace(
                chord,
                pitch_classes=frozenset(
                    (pitch_class + semitones) % 12 for pitch_class in chord.pitch_classes
                ),
                root_pc=(chord.root_pc + semitones) % 12,
            )
            for chord in procedural.ir.chords
        ),
        meta=replace(procedural.ir.meta, key="transposed-fixture"),
    )
    real = _real_item(
        "cross-transposed-real",
        (),
        position=1,
        family="cross-transposed-family",
        ir=transposed,
    )

    report = audit_contamination((procedural, real))

    assert [finding.kind for finding in report.cross_stratum_findings] == [
        ContaminationKind.TRANSPOSITION_VARIANT
    ]
    assert report.real.clean and report.procedural.clean
    assert not report.clean


def test_audit_order_is_deterministic_for_items_and_canary_documents() -> None:
    first = _real_item(
        "deterministic-a",
        (60, 62, 64),
        position=0,
        family="family-a",
        canary="canary:deterministic:a",
    )
    second = _real_item(
        "deterministic-b",
        (60, 62, 64),
        position=1,
        family="family-b",
        canary="canary:deterministic:b",
    )
    documents = (
        CanaryDocument("z", "canary:deterministic:b"),
        CanaryDocument("a", "canary:deterministic:a"),
    )

    forward = audit_contamination((first, second), canary_documents=documents)
    reverse = audit_contamination(
        (second, first),
        canary_documents=tuple(reversed(documents)),
    )

    assert forward == reverse


def test_canary_documents_and_stratum_lookup_keep_typed_failures() -> None:
    with pytest.raises(ContaminationInputError, match="CanaryDocument"):
        audit_contamination((), canary_documents=("not-a-document",))
    with pytest.raises(ContaminationInputError, match="unique"):
        audit_contamination(
            (),
            canary_documents=(CanaryDocument("same", "a"), CanaryDocument("same", "b")),
        )

    report = audit_contamination(())
    with pytest.raises(ContaminationInputError, match="no denominator-bearing report"):
        report.for_stratum(CorpusStratum.CROSS)
    with pytest.raises(ContaminationInputError, match="CorpusStratum"):
        report.for_stratum("real")  # type: ignore[arg-type]
