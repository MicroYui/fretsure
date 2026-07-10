import json

from fretsure.bench.corpus import (
    CorpusItem,
    datasheet,
    ir_to_notegraph,
    notegraph_to_ir,
)
from fretsure.bench.generator import GenConfig, generate_leadsheet


def test_notegraph_json_roundtrip_identity() -> None:
    ir = generate_leadsheet(GenConfig(seed=2, bars=4))
    obj = ir_to_notegraph(ir)
    restored = notegraph_to_ir(json.loads(json.dumps(obj)))
    assert restored == ir


def test_datasheet_counts_by_stratum() -> None:
    items = [
        CorpusItem(generate_leadsheet(GenConfig(seed=i)), "procedural", "generated", d, f"g{i}")
        for i, d in enumerate([1, 1, 2, 3])
    ]
    ds = datasheet(items)
    assert ds["count"] == 4
    assert ds["by_layer"]["procedural"] == 4
    assert ds["by_difficulty"][1] == 2
