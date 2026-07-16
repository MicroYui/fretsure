import json
from fractions import Fraction as F

from fretsure.bench.corpus import (
    CorpusItem,
    datasheet,
    ir_to_notegraph,
    notegraph_to_ir,
)
from fretsure.bench.generator import GenConfig, generate_leadsheet
from fretsure.ir import Meta, MusicIR


def test_meta_duration_beats_is_optional_and_positional_calls_remain_compatible() -> None:
    legacy = Meta("C", (4, 4), 90.0, "unit", "title", "PD")
    explicit = Meta("C", (4, 4), 90.0, "unit", "title", "PD", F(8))

    assert legacy.duration_beats is None
    assert explicit.duration_beats == F(8)


def test_notegraph_json_roundtrip_identity() -> None:
    ir = generate_leadsheet(GenConfig(seed=2, bars=4))
    obj = ir_to_notegraph(ir)
    restored = notegraph_to_ir(json.loads(json.dumps(obj)))
    assert restored == ir


def test_notegraph_roundtrips_explicit_piece_duration() -> None:
    ir = MusicIR(
        (),
        (),
        Meta("C", (4, 4), 90.0, "unit", "title", "PD", F(8)),
    )

    obj = ir_to_notegraph(ir)

    assert obj["meta"]["duration_beats"] == "8"
    assert notegraph_to_ir(json.loads(json.dumps(obj))) == ir


def test_notegraph_reads_legacy_meta_without_piece_duration() -> None:
    ir = generate_leadsheet(GenConfig(seed=2, bars=4))
    obj = ir_to_notegraph(ir)
    del obj["meta"]["duration_beats"]

    restored = notegraph_to_ir(obj)

    assert restored.meta.duration_beats is None


def test_datasheet_counts_by_stratum() -> None:
    items = [
        CorpusItem(generate_leadsheet(GenConfig(seed=i)), "procedural", "generated", d, f"g{i}")
        for i, d in enumerate([1, 1, 2, 3])
    ]
    ds = datasheet(items)
    assert ds["count"] == 4
    assert ds["by_layer"]["procedural"] == 4
    assert ds["by_difficulty"][1] == 2
