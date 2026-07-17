import copy
import json
from collections import Counter
from dataclasses import replace
from fractions import Fraction as F

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from fretsure.bench.contracts import (
    BENCHMARK_CORPUS_VERSION,
    BENCHMARK_NOTEGRAPH_VERSION,
    BenchmarkContractError,
    canonical_json_bytes,
)
from fretsure.bench.corpus import (
    PRIMARY_PROCEDURAL_FAMILY_TARGET,
    CorpusItem,
    CorpusProvenance,
    EvidenceAvailability,
    GeneratorProvenance,
    LicenseProvenance,
    ProceduralCorpusConfig,
    build_primary_procedural_corpus,
    corpus_from_dict,
    corpus_item_sha256,
    corpus_sha256,
    corpus_to_dict,
    datasheet,
    ir_to_notegraph,
    notegraph_sha256,
    notegraph_to_ir,
    procedural_source_sha256,
    snapshot_corpus,
    snapshot_procedural_corpus_config,
)
from fretsure.bench.generator import (
    GENERATOR_VERSION,
    GenConfig,
    ProceduralVariation,
    generate_leadsheet,
    generate_procedural_variant,
)
from fretsure.ir import ChordSymbol, Meta, MusicIR, Note


def _ir(seed: int = 2) -> MusicIR:
    return generate_leadsheet(GenConfig(seed=seed, bars=4))


def _provenance(
    seed: int,
    *,
    tempo_bpm: float = 90.0,
    complexity: str = "low",
    polyphony: str = "low",
    position: int = 0,
) -> CorpusProvenance:
    config = GenConfig(seed=seed, bars=4, tempo_bpm=tempo_bpm)
    variation = ProceduralVariation(complexity, polyphony)  # type: ignore[arg-type]
    digest = procedural_source_sha256(config, variation, position)
    return CorpusProvenance(
        source_format="procedural",
        source_sha256=digest,
        root_sha256=digest,
        router_version=None,
        importer_version=None,
        container_version=None,
        source_url=None,
        producer="fretsure",
        retrieval_date="2026-07-17",
        license=LicenseProvenance(
            expression="CC0-1.0",
            status="generated",
            redistribution=True,
            derivatives=True,
            provider_submission=True,
        ),
        split="test",
        role_map=(("generated:bass", "bass"), ("generated:melody", "melody")),
        normalization=("functional-harmony-v1",),
        generator=GeneratorProvenance(
            version=GENERATOR_VERSION,
            key=config.key,
            meter=config.meter,
            bars=config.bars,
            seed=config.seed,
            tempo_bpm=config.tempo_bpm,
        ),
    )


def _item(index: int, *, family: str | None = None, split: str = "test") -> CorpusItem:
    complexity = ("low", "medium", "high")[index % 3]
    polyphony = ("low", "medium", "high")[index % 3]
    seed = index + 1
    provenance = _provenance(
        index + 1,
        complexity=complexity,
        polyphony=polyphony,
        position=index,
    )
    if split != provenance.split:
        provenance = CorpusProvenance(
            source_format=provenance.source_format,
            source_sha256=provenance.source_sha256,
            root_sha256=provenance.root_sha256,
            router_version=provenance.router_version,
            importer_version=provenance.importer_version,
            container_version=provenance.container_version,
            source_url=provenance.source_url,
            producer=provenance.producer,
            retrieval_date=provenance.retrieval_date,
            license=provenance.license,
            split=split,
            role_map=provenance.role_map,
            normalization=provenance.normalization,
            generator=provenance.generator,
        )
    family_id = family or f"family-{index}"
    return CorpusItem(
        generate_procedural_variant(
            GenConfig(seed=seed, bars=4),
            ProceduralVariation(complexity, polyphony),  # type: ignore[arg-type]
        ),
        "procedural",
        "generated",
        0,
        f"item-{index}",
        family_id=family_id,
        cluster_id=f"cluster-{family_id}",
        position=index,
        provenance=provenance,
        evidence=EvidenceAvailability(melody=True, bass=True, harmony=True),
        synthetic_complexity=complexity,
        polyphony=polyphony,
        canary=f"canary:item-{index}",
    )


def _public_item() -> CorpusItem:
    source = _item(0)
    assert source.provenance is not None
    provenance = replace(
        source.provenance,
        source_format="musicxml",
        source_sha256="1" * 64,
        root_sha256="2" * 64,
        router_version="score-input@0.1.0",
        importer_version="musicxml@0.3.0",
        retrieval_date="2026-07-17",
        license=replace(source.provenance.license, status="verified"),
        normalization=("musicxml-explicit-role-map",),
        generator=None,
    )
    return replace(
        source,
        layer="public_leadsheet",
        difficulty=0,
        provenance=provenance,
        synthetic_complexity="unrated",
        polyphony="polyphonic",
    )


def _checker_item(index: int) -> CorpusItem:
    ir = MusicIR(
        (Note(F(0), F(1), 60 + index, "melody"),),
        (),
        Meta("C", (4, 4), 90.0, "checker", f"checker-{index}", "CC0-1.0", F(1)),
    )
    return CorpusItem(
        ir,
        "checker_tab",
        "checker",
        0,
        f"checker-item-{index}",
        family_id=f"checker-family-{index}",
        cluster_id=f"checker-cluster-{index}",
        position=index,
        provenance=CorpusProvenance(
            source_format="tab",
            source_sha256=f"{index + 3:x}" * 64,
            root_sha256=None,
            router_version=None,
            importer_version=None,
            container_version=None,
            source_url=None,
            producer="fretsure-checker-fixture",
            retrieval_date="2026-07-17",
            license=LicenseProvenance(
                expression="CC0-1.0",
                status="verified",
                redistribution=True,
                derivatives=True,
                provider_submission=True,
            ),
            split="test",
            role_map=(),
            normalization=("checker-tab-normalized",),
            generator=None,
        ),
        evidence=EvidenceAvailability(False, False, False),
        synthetic_complexity="unrated",
        polyphony="unrated",
        canary=f"canary:checker-item-{index}",
    )


def test_meta_duration_beats_is_optional_and_positional_calls_remain_compatible() -> None:
    legacy = Meta("C", (4, 4), 90.0, "unit", "title", "PD")
    explicit = Meta("C", (4, 4), 90.0, "unit", "title", "PD", F(8))

    assert legacy.duration_beats is None
    assert explicit.duration_beats == F(8)


def test_notegraph_v2_writer_and_json_roundtrip_are_versioned() -> None:
    ir = _ir()

    obj = ir_to_notegraph(ir)

    assert obj["schema"] == BENCHMARK_NOTEGRAPH_VERSION
    assert set(obj) == {"schema", "meta", "notes", "chords"}
    assert notegraph_to_ir(json.loads(json.dumps(obj))) == ir
    assert notegraph_sha256(obj) == notegraph_sha256(copy.deepcopy(obj))


def test_notegraph_roundtrips_explicit_piece_duration() -> None:
    ir = MusicIR(
        (),
        (),
        Meta("C", (4, 4), 90.0, "unit", "title", "PD", F(8)),
    )

    obj = ir_to_notegraph(ir)

    assert obj["meta"]["duration_beats"] == "8"
    assert notegraph_to_ir(json.loads(json.dumps(obj))) == ir


def test_notegraph_reads_only_the_explicit_legacy_shape() -> None:
    obj = ir_to_notegraph(_ir())
    del obj["schema"]
    del obj["meta"]["duration_beats"]

    restored = notegraph_to_ir(obj)

    assert restored.meta.duration_beats is None
    with pytest.raises(BenchmarkContractError, match="legacy"):
        notegraph_to_ir(obj, allow_legacy=False)


@pytest.mark.parametrize(
    ("mutation", "path"),
    [
        (lambda obj: obj.update(extra=True), r"\$"),
        (lambda obj: obj.update(schema="benchmark-notegraph@9.9.9"), "schema"),
        (lambda obj: obj["meta"].pop("duration_beats"), "duration_beats"),
        (lambda obj: obj["meta"].update(tempo_bpm="90"), "tempo_bpm"),
        (lambda obj: obj["meta"].update(time_sig=[True, 4]), "time_sig"),
        (lambda obj: obj["notes"][0].update(midi="60"), "midi"),
        (lambda obj: obj["notes"][0].update(voice="inner"), "voice"),
        (lambda obj: obj["notes"][0].update(onset="01"), "onset"),
        (lambda obj: obj["notes"][0].update(duration="2/2"), "duration"),
        (lambda obj: obj["chords"][0].update(root_pc=True), "root_pc"),
        (lambda obj: obj["chords"][0].update(pitch_classes=[0, 0, 4]), "pitch_classes"),
    ],
)
def test_v2_notegraph_rejects_unknown_noncanonical_or_coerced_fields(
    mutation: object, path: str
) -> None:
    obj = ir_to_notegraph(_ir())
    assert callable(mutation)
    mutation(obj)

    with pytest.raises(BenchmarkContractError, match=path):
        notegraph_to_ir(obj)


def test_notegraph_rejects_mapping_subclasses_without_invoking_hooks() -> None:
    class HostileDict(dict[str, object]):
        def __getitem__(self, key: object) -> object:
            raise AssertionError("hostile mapping hook ran")

    obj = HostileDict(ir_to_notegraph(_ir()))

    with pytest.raises(BenchmarkContractError, match="exact object"):
        notegraph_to_ir(obj)


def test_exact_object_error_never_echoes_an_unbounded_hostile_key() -> None:
    hostile_key = "x" * 10_000
    obj = ir_to_notegraph(_ir())
    obj[hostile_key] = None

    with pytest.raises(BenchmarkContractError) as caught:
        notegraph_to_ir(obj)

    message = str(caught.value)
    assert hostile_key not in message
    assert "extra_count=1" in message
    assert len(message) < 256


def test_notegraph_never_invokes_numeric_coercion_hooks() -> None:
    class Coercible:
        def __int__(self) -> int:
            raise AssertionError("int coercion hook ran")

        def __float__(self) -> float:
            raise AssertionError("float coercion hook ran")

        def __str__(self) -> str:
            raise AssertionError("string coercion hook ran")

    obj = ir_to_notegraph(_ir())
    obj["notes"][0]["midi"] = Coercible()

    with pytest.raises(BenchmarkContractError, match="exact integer"):
        notegraph_to_ir(obj)


def test_notegraph_rejects_fraction_components_over_the_ir_limit() -> None:
    obj = ir_to_notegraph(_ir())
    obj["notes"][0]["onset"] = "1" + "0" * 78

    with pytest.raises(BenchmarkContractError, match="256-bit"):
        notegraph_to_ir(obj)


def test_notegraph_writer_and_reader_run_semantic_ir_validation() -> None:
    ir = _ir()
    bad = MusicIR(
        ir.notes,
        (ChordSymbol(F(0), "bad", frozenset({0, 4, 7}), 1),),
        ir.meta,
    )
    with pytest.raises(BenchmarkContractError, match="bad_chord_root"):
        ir_to_notegraph(bad)

    obj = ir_to_notegraph(ir)
    obj["chords"][0]["root_pc"] = 1
    with pytest.raises(BenchmarkContractError, match="bad_chord_root"):
        notegraph_to_ir(obj)


@settings(max_examples=30, deadline=None)
@given(
    seed=st.integers(min_value=-10_000, max_value=10_000),
    bars=st.integers(min_value=1, max_value=8),
    key=st.sampled_from(("C", "G", "D", "A", "E", "F", "Bb", "B")),
    meter=st.sampled_from(((4, 4), (3, 4), (6, 8))),
    complexity=st.sampled_from(("low", "medium", "high")),
    polyphony=st.sampled_from(("low", "medium", "high")),
)
def test_generated_notegraph_property_roundtrip_is_canonical(
    seed: int,
    bars: int,
    key: str,
    meter: tuple[int, int],
    complexity: str,
    polyphony: str,
) -> None:
    ir = generate_procedural_variant(
        GenConfig(seed=seed, bars=bars, key=key, meter=meter),
        ProceduralVariation(complexity, polyphony),  # type: ignore[arg-type]
    )
    wire = ir_to_notegraph(ir)
    assert notegraph_to_ir(json.loads(json.dumps(wire))) == ir
    assert ir_to_notegraph(notegraph_to_ir(wire)) == wire


def test_old_five_argument_corpus_item_constructor_remains_compatible() -> None:
    item = CorpusItem(_ir(), "procedural", "generated", 2, "legacy-id")

    assert item.item_id == "legacy-id"
    assert item.family_id is None
    assert item.provenance is None
    assert item.canary is None


def test_strict_corpus_roundtrip_hashes_and_datasheet() -> None:
    items = (_item(0), _item(1))

    wire = corpus_to_dict(items)
    restored = corpus_from_dict(json.loads(json.dumps(wire)))
    sheet = datasheet(restored)

    assert wire["schema"] == BENCHMARK_CORPUS_VERSION
    assert "difficulty" not in wire["items"][0]["item"]
    assert restored == items
    assert corpus_sha256(items) == corpus_sha256(restored)
    assert wire["items"][0]["item_sha256"] == corpus_item_sha256(items[0])
    assert sheet["schema"] == BENCHMARK_CORPUS_VERSION
    assert sheet["count"] == 2
    assert sheet["difficulty_status"] == "HUMAN_BLOCKED_UNRATED"
    assert sheet["by_layer"] == {"procedural": 2}
    assert sheet["by_evidence"] == {"melody+bass+harmony": 2}
    assert sheet["by_synthetic_complexity"] == {"low": 1, "medium": 1}
    assert sheet["by_polyphony"] == {"low": 1, "medium": 1}


def test_checker_layer_accepts_distinct_nonempty_notegraphs_without_source_evidence() -> None:
    items = (_checker_item(0), _checker_item(1))

    assert snapshot_corpus(items) == items
    assert datasheet(items)["by_evidence"] == {"none": 2}


def test_corpus_hashes_bind_canary_and_reject_tampering() -> None:
    item = _item(0)
    assert corpus_item_sha256(item) != corpus_item_sha256(
        replace(item, canary="canary:replacement")
    )

    wire = corpus_to_dict((item,))
    wire["items"][0]["item_sha256"] = "0" * 64
    with pytest.raises(BenchmarkContractError, match="item_sha256"):
        corpus_from_dict(wire)

    wire = corpus_to_dict((item,))
    wire["items"][0]["item"]["notegraph_sha256"] = "0" * 64
    with pytest.raises(BenchmarkContractError, match="notegraph_sha256"):
        corpus_from_dict(wire)


def test_legacy_datasheet_keeps_old_constructor_usable() -> None:
    items = [
        CorpusItem(_ir(i), "procedural", "generated", d, f"g{i}")
        for i, d in enumerate([1, 1, 2, 3])
    ]

    sheet = datasheet(items)

    assert sheet["count"] == 4
    assert sheet["by_layer"]["procedural"] == 4
    assert sheet["difficulty_status"] == "HUMAN_BLOCKED_UNRATED"


def test_snapshot_corpus_rejects_duplicate_ids_and_notegraphs() -> None:
    first = _item(0)
    second = _item(1)
    duplicate_id = CorpusItem(
        second.ir,
        "procedural",
        "generated",
        0,
        first.item_id,
        family_id="different-family",
        cluster_id="different-cluster",
        position=1,
        provenance=second.provenance,
        evidence=EvidenceAvailability(True, True, True),
        synthetic_complexity="medium",
        polyphony="medium",
        canary="canary:duplicate-id",
    )
    with pytest.raises(BenchmarkContractError, match="duplicate item_id"):
        snapshot_corpus((first, duplicate_id))

    assert first.provenance is not None
    exact_duplicate = CorpusItem(
        first.ir,
        "procedural",
        "generated",
        0,
        "different-id",
        family_id="different-family",
        cluster_id="different-cluster",
        position=1,
        provenance=replace(
            first.provenance,
            source_sha256=procedural_source_sha256(
                GenConfig(seed=1, bars=4),
                ProceduralVariation("low", "low"),
                1,
            ),
        ),
        evidence=EvidenceAvailability(True, True, True),
        synthetic_complexity=first.synthetic_complexity,
        polyphony=first.polyphony,
        canary="canary:duplicate-notegraph",
    )
    with pytest.raises(BenchmarkContractError, match="duplicate notegraph"):
        snapshot_corpus((first, exact_duplicate))


def test_snapshot_corpus_rejects_family_split_or_cluster_leakage() -> None:
    first = _item(0, family="shared", split="train")
    second = _item(1, family="shared", split="test")

    with pytest.raises(BenchmarkContractError, match="family.*split"):
        snapshot_corpus((first, second))

    same_split = _item(1, family="shared", split="train")
    conflicting_cluster = CorpusItem(
        same_split.ir,
        same_split.layer,
        same_split.genre,
        same_split.difficulty,
        same_split.item_id,
        family_id=same_split.family_id,
        cluster_id="another-cluster",
        position=same_split.position,
        provenance=same_split.provenance,
        evidence=same_split.evidence,
        synthetic_complexity=same_split.synthetic_complexity,
        polyphony=same_split.polyphony,
        canary=same_split.canary,
    )
    with pytest.raises(BenchmarkContractError, match="family.*cluster"):
        snapshot_corpus((first, conflicting_cluster))


def test_snapshot_corpus_requires_input_order_to_match_positions() -> None:
    with pytest.raises(BenchmarkContractError, match="position"):
        snapshot_corpus((_item(1), _item(0)))


def test_generator_provenance_roundtrips_tempo_and_rejects_drift() -> None:
    item = _item(0)
    wire = corpus_to_dict((item,))
    generator = wire["items"][0]["item"]["provenance"]["generator"]
    assert generator["version"] == GENERATOR_VERSION
    assert generator["config"]["tempo_bpm"] == 90.0

    assert item.provenance is not None
    assert item.provenance.generator is not None
    for bad_generator, message in (
        (replace(item.provenance.generator, version="other-generator@0.1.0"), "version"),
        (replace(item.provenance.generator, meter=(4, 3)), "meter"),
        (replace(item.provenance.generator, tempo_bpm=96.0), "tempo"),
    ):
        bad = replace(
            item,
            provenance=replace(item.provenance, generator=bad_generator),
        )
        with pytest.raises(BenchmarkContractError, match=message):
            snapshot_corpus((bad,))


def test_strict_strata_are_the_generator_three_level_axes() -> None:
    cases = (
        ("synthetic_complexity", replace(_item(0), synthetic_complexity="unrated")),
        ("polyphony", replace(_item(0), polyphony="polyphonic")),
    )
    for field, malformed in cases:
        with pytest.raises(BenchmarkContractError, match=field):
            snapshot_corpus((malformed,))


def test_strict_provenance_requires_identity_date_hash_and_permissions() -> None:
    item = _item(0)
    assert item.provenance is not None
    identity_cases = (
        replace(item.provenance, source_url=None, producer=None),
        replace(
            item.provenance,
            license=replace(item.provenance.license, provider_submission=None),
        ),
        replace(
            item.provenance,
            license=replace(item.provenance.license, status="verified"),
        ),
        replace(
            item.provenance,
            license=replace(item.provenance.license, redistribution=False),
        ),
    )
    for provenance, pattern in zip(
        identity_cases,
        (
            "source_url.*producer",
            "license permissions",
            "must be generated",
            "permit all recorded uses",
        ),
        strict=True,
    ):
        with pytest.raises(BenchmarkContractError, match=pattern):
            snapshot_corpus((replace(item, provenance=provenance),))

    public_provenance = replace(
        item.provenance,
        source_format="musicxml",
        router_version="score-input@0.1.0",
        importer_version="musicxml@0.3.0",
        license=replace(item.provenance.license, status="verified"),
        generator=None,
    )
    public = replace(
        item,
        layer="public_leadsheet",
        provenance=public_provenance,
        synthetic_complexity="unrated",
        polyphony="polyphonic",
    )
    for provenance, pattern in (
        (replace(public_provenance, retrieval_date=None), "retrieval_date"),
        (replace(public_provenance, source_sha256=None), "source_sha256"),
    ):
        with pytest.raises(BenchmarkContractError, match=pattern):
            snapshot_corpus((replace(public, provenance=provenance),))


def test_public_layers_use_unrated_synthetic_labels_and_bound_source_format() -> None:
    item = _public_item()
    assert snapshot_corpus((item,)) == (item,)

    assert item.provenance is not None
    cases = (
        (replace(item, difficulty=1), "difficulty"),
        (replace(item, synthetic_complexity="low"), "synthetic_complexity"),
        (replace(item, polyphony="monophonic"), "polyphony"),
        (
            replace(
                item,
                provenance=replace(item.provenance, source_format="midi"),
            ),
            "source_format",
        ),
        (
            replace(item, provenance=replace(item.provenance, role_map=())),
            "role_map",
        ),
        (
            replace(item, provenance=replace(item.provenance, normalization=())),
            "normalization",
        ),
        (
            replace(
                item,
                provenance=replace(
                    item.provenance,
                    license=replace(item.provenance.license, status="unavailable"),
                ),
            ),
            "license.status",
        ),
    )
    for malformed, pattern in cases:
        with pytest.raises(BenchmarkContractError, match=pattern):
            snapshot_corpus((malformed,))


def test_procedural_items_are_regenerated_and_source_hash_bound() -> None:
    item = _item(0)
    changed_note = replace(item.ir.notes[0], pitch=item.ir.notes[0].pitch + 1)
    changed_ir = replace(
        item.ir,
        notes=tuple(
            sorted(
                (changed_note, *item.ir.notes[1:]),
                key=lambda note: (note.onset, note.pitch),
            )
        ),
    )
    with pytest.raises(BenchmarkContractError, match="generator output"):
        snapshot_corpus((replace(item, ir=changed_ir),))

    assert item.provenance is not None
    with pytest.raises(BenchmarkContractError, match="source_sha256"):
        snapshot_corpus(
            (
                replace(
                    item,
                    provenance=replace(item.provenance, source_sha256="0" * 64),
                ),
            )
        )


def test_evidence_partition_must_match_the_notegraph() -> None:
    item = _item(0)
    with pytest.raises(BenchmarkContractError, match="authoritative source evidence"):
        snapshot_corpus((replace(item, evidence=EvidenceAvailability(True, False, True)),))


def test_partial_legacy_hybrid_is_rejected() -> None:
    legacy = CorpusItem(_ir(), "procedural", "generated", 2, "legacy-id")
    hybrid = replace(legacy, family_id="half-populated")

    with pytest.raises(BenchmarkContractError, match="partial"):
        snapshot_corpus((hybrid,), allow_legacy=True)


def test_cluster_cannot_cross_splits_even_between_different_families() -> None:
    first = _item(0, split="train")
    second = replace(
        _item(1, split="test"),
        cluster_id=first.cluster_id,
    )

    with pytest.raises(BenchmarkContractError, match="cluster.*split"):
        snapshot_corpus((first, second))


def test_canaries_are_bounded_canonical_and_unique() -> None:
    first = _item(0)
    with pytest.raises(BenchmarkContractError, match="canary"):
        snapshot_corpus((replace(first, canary="not a canary with spaces"),))
    with pytest.raises(BenchmarkContractError, match="duplicate canary"):
        snapshot_corpus((first, replace(_item(1), canary=first.canary)))


def test_aggregate_event_budget_fails_before_corpus_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fretsure.bench import corpus as corpus_module

    first = _item(0)
    monkeypatch.setattr(corpus_module, "MAX_CORPUS_TOTAL_NOTES", len(first.ir.notes))

    with pytest.raises(BenchmarkContractError, match="cumulative note count"):
        snapshot_corpus((first, _item(1)))


def test_aggregate_metadata_budget_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fretsure.bench import corpus as corpus_module

    monkeypatch.setattr(corpus_module, "MAX_CORPUS_TEXT_CHARS", 1)
    with pytest.raises(BenchmarkContractError, match="cumulative corpus metadata"):
        snapshot_corpus((_item(0),))


def test_wire_aggregate_budget_fails_before_item_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fretsure.bench import corpus as corpus_module

    first = _item(0)
    wire = corpus_to_dict((first, _item(1)))
    called = False

    def forbidden_parse(_value: object) -> CorpusItem:
        nonlocal called
        called = True
        raise AssertionError("item parsing ran before aggregate preflight")

    monkeypatch.setattr(corpus_module, "MAX_CORPUS_TOTAL_NOTES", len(first.ir.notes))
    monkeypatch.setattr(corpus_module, "corpus_item_from_dict", forbidden_parse)

    with pytest.raises(BenchmarkContractError, match="cumulative note count"):
        corpus_from_dict(wire)
    assert called is False


@pytest.mark.parametrize(
    ("config", "field"),
    [
        (object(), "procedural_config"),
        (ProceduralCorpusConfig(family_count=True), "family_count"),
        (ProceduralCorpusConfig(family_count=0), "family_count"),
        (ProceduralCorpusConfig(base_seed=True), "seed"),
        (ProceduralCorpusConfig(bars=0), "bars"),
        (ProceduralCorpusConfig(split="bad split"), "split"),
    ],
)
def test_primary_procedural_config_is_strict(config: object, field: str) -> None:
    with pytest.raises(BenchmarkContractError, match=field):
        snapshot_procedural_corpus_config(config)


def test_primary_procedural_preflight_rejects_before_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fretsure.bench import corpus as corpus_module

    called = False

    def forbidden_generate(*_args: object, **_kwargs: object) -> MusicIR:
        nonlocal called
        called = True
        raise AssertionError("generation ran after failed preflight")

    monkeypatch.setattr(corpus_module, "MAX_CORPUS_TOTAL_NOTES", 1)
    monkeypatch.setattr(corpus_module, "generate_procedural_variant", forbidden_generate)

    with pytest.raises(BenchmarkContractError, match="note count"):
        build_primary_procedural_corpus(ProceduralCorpusConfig(family_count=1))
    assert called is False


def test_primary_procedural_corpus_freezes_ids_seeds_strata_and_bytes() -> None:
    config = ProceduralCorpusConfig()
    first = build_primary_procedural_corpus(config)
    second = build_primary_procedural_corpus(config)

    assert len(first) == PRIMARY_PROCEDURAL_FAMILY_TARGET == 500
    assert len({item.item_id for item in first}) == 500
    assert len({item.family_id for item in first}) == 500
    assert len({item.cluster_id for item in first}) == 500
    assert len({item.canary for item in first}) == 500
    assert len(
        {
            item.provenance.generator.seed
            for item in first
            if item.provenance is not None and item.provenance.generator is not None
        }
    ) == 500
    strata = Counter(
        (item.synthetic_complexity, item.polyphony) for item in first
    )
    assert set(strata) == {
        (complexity, polyphony)
        for complexity in ("low", "medium", "high")
        for polyphony in ("low", "medium", "high")
    }
    assert max(strata.values()) - min(strata.values()) == 1
    assert all(item.evidence == EvidenceAvailability(True, True, True) for item in first)
    assert all(item.difficulty == 0 for item in first)
    assert any(item.ir.meta.tempo_bpm == 96.0 for item in first)
    assert canonical_json_bytes(corpus_to_dict(first)) == canonical_json_bytes(
        corpus_to_dict(second)
    )
    assert corpus_sha256(first) == (
        "66cc65dec524840f1c8c6ccf28ac6eea162b8677ef0cdf0abaa909d5496394a0"
    )
