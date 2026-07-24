from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest

from fretsure.tab import Tab, TabNote

ROOT = Path(__file__).resolve().parents[2]
_MODULE_SPEC = importlib.util.spec_from_file_location(
    "solver_quality_eval", ROOT / "scripts/solver_quality_eval.py"
)
assert _MODULE_SPEC is not None and _MODULE_SPEC.loader is not None
quality_eval = importlib.util.module_from_spec(_MODULE_SPEC)
sys.modules[_MODULE_SPEC.name] = quality_eval
_MODULE_SPEC.loader.exec_module(quality_eval)


def _jams_bytes(
    rows: list[dict[str, float | int]],
    *,
    string: int = 5,
    tempo: int = 60,
    columnar: bool = False,
) -> bytes:
    data: object
    if columnar:
        data = {
            key: [row[key] for row in rows]
            for key in ("time", "duration", "value", "confidence")
        }
    else:
        data = rows
    payload = {
        "annotations": [
            {
                "namespace": "note_midi",
                "annotation_metadata": {"data_source": str(string)},
                "data": data,
            },
            {
                "namespace": "tempo",
                "annotation_metadata": {"data_source": ""},
                "data": [
                    {
                        "time": 0,
                        "duration": 10,
                        "value": tempo,
                        "confidence": 1,
                    }
                ],
            },
        ]
    }
    return json.dumps(payload, separators=(",", ":")).encode()


def _two_note_jams(*, columnar: bool = False) -> bytes:
    return _jams_bytes(
        [
            {"time": 0, "duration": 0.5, "value": 64.02, "confidence": 1},
            {"time": 1, "duration": 0.5, "value": 65.01, "confidence": 1},
        ],
        columnar=columnar,
    )


def test_parse_row_and_column_jams_derive_reference_string_and_fret() -> None:
    row = quality_eval.parse_jams_document(
        _two_note_jams(), corpus="guitarset", source_id="00_fixture.jams", split="train"
    )
    column = quality_eval.parse_jams_document(
        _two_note_jams(columnar=True),
        corpus="egset12",
        source_id="01.jams",
        split="held-out",
    )

    assert [(note.pitch, note.string, note.fret) for note in row.notes] == [
        (64, 5, 0),
        (65, 5, 1),
    ]
    assert column.notes == row.notes
    assert row.tempo_bpm == Fraction(60)
    assert row.corpus_id == quality_eval.GUITARSET_CORPUS_ID
    assert column.corpus_id == quality_eval.EGSET12_CORPUS_ID


def test_column_jams_rejects_mismatched_column_lengths() -> None:
    payload = json.loads(_two_note_jams(columnar=True))
    payload["annotations"][0]["data"]["value"].pop()

    with pytest.raises(quality_eval.CorpusDataError, match="equal lengths"):
        quality_eval.parse_jams_document(
            json.dumps(payload).encode(),
            corpus="egset12",
            source_id="bad.jams",
            split="held-out",
        )


def test_build_window_quantizes_and_clips_same_string_overlap() -> None:
    document = quality_eval.parse_jams_document(
        _jams_bytes(
            [
                {"time": 0, "duration": 2, "value": 64, "confidence": 1},
                {"time": 1, "duration": 0.5, "value": 65, "confidence": 1},
            ],
            tempo=60,
        ),
        corpus="guitarset",
        source_id="00_overlap.jams",
        split="train",
    )

    window = quality_eval.build_window(
        document,
        notes_per_window=2,
        window_offset=0,
        quantize_denominator=4,
    )

    assert isinstance(window, quality_eval.EvaluationWindow)
    assert [(note.onset, note.duration) for note in window.notes] == [
        (Fraction(0), Fraction(1)),
        (Fraction(1), Fraction(1, 2)),
    ]


def test_guitarset_split_is_by_performer() -> None:
    assert quality_eval.guitarset_split("00_piece_solo.jams") == "train"
    assert quality_eval.guitarset_split("03_piece_solo.jams") == "train"
    assert quality_eval.guitarset_split("04_piece_solo.jams") == "dev"
    assert quality_eval.guitarset_split("05_piece_solo.jams") == "test"
    with pytest.raises(quality_eval.CorpusDataError, match="outside the frozen split"):
        quality_eval.guitarset_split("06_piece_solo.jams")


def test_train_member_order_round_robins_all_performer_mode_groups() -> None:
    train_members = [
        f"{performer}_{variant}_{mode}.jams"
        for performer in ("00", "01", "02", "03")
        for mode in ("comp", "solo")
        for variant in ("z", "a")
    ]
    mixed_input = list(reversed(train_members)) + [
        "04_a_comp.jams",
        "05_a_solo.jams",
    ]

    ordered = quality_eval._ordered_guitarset_members(mixed_input, "train")

    assert ordered[:8] == tuple(
        f"{performer}_a_{mode}.jams"
        for performer in ("00", "01", "02", "03")
        for mode in ("comp", "solo")
    )
    assert {
        quality_eval._guitarset_member_identity(member) for member in ordered[:8]
    } == {
        (performer, mode)
        for performer in ("00", "01", "02", "03")
        for mode in ("comp", "solo")
    }
    assert len(ordered) == len(set(ordered)) == len(train_members)
    assert sorted(ordered) == sorted(train_members)


def test_default_run_is_train_only_and_does_not_open_egset12() -> None:
    assert quality_eval.EvaluationConfig().requested_split == "train"
    assert quality_eval._parser().parse_args([]).split == "train"


def test_build_window_records_quantization_collision() -> None:
    document = quality_eval.parse_jams_document(
        _jams_bytes(
            [
                {"time": 0, "duration": 0.5, "value": 64, "confidence": 1},
                {"time": 0.01, "duration": 0.5, "value": 65, "confidence": 1},
            ],
            tempo=60,
        ),
        corpus="guitarset",
        source_id="00_collision.jams",
        split="train",
    )

    selection = quality_eval.build_window(
        document,
        notes_per_window=2,
        window_offset=0,
        quantize_denominator=4,
    )

    assert isinstance(selection, quality_eval.WindowConstructionRejection)
    assert selection.code == "QUANTIZATION_SAME_STRING_COLLISION"
    assert selection.note_count == 2


def test_duplicate_unison_alignment_is_order_independent() -> None:
    notes = (
        quality_eval.WindowNote(Fraction(0), Fraction(1), 64, 4, 5),
        quality_eval.WindowNote(Fraction(0), Fraction(1), 64, 5, 0),
    )
    window = quality_eval.EvaluationWindow(
        corpus="guitarset",
        corpus_id="fixture",
        source_id="fixture.jams",
        source_sha256="0" * 64,
        split="dev",
        window_index=0,
        tempo_bpm=Fraction(60),
        notes=notes,
    )
    tab = Tab(
        (
            TabNote(Fraction(0), Fraction(1), 5, 0, 0, "i"),
            TabNote(Fraction(0), Fraction(1), 4, 5, 1, "p"),
        ),
        quality_eval.STANDARD_TUNING,
        0,
    )

    comparison = quality_eval._comparison_metrics(window, tab)

    assert comparison["notes_compared"] == 2
    assert comparison["string_fret_exact_count"] == 2
    assert comparison["fret_mae"] == 0


def test_evaluate_window_reports_descriptive_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = quality_eval.EvaluationWindow(
        corpus="guitarset",
        corpus_id="fixture",
        source_id="fixture.jams",
        source_sha256="0" * 64,
        split="dev",
        window_index=0,
        tempo_bpm=Fraction(60),
        notes=(quality_eval.WindowNote(Fraction(0), Fraction(1), 64, 5, 0),),
    )
    solved = Tab(
        (TabNote(Fraction(0), Fraction(1), 4, 5, 1, "p"),),
        quality_eval.STANDARD_TUNING,
        0,
    )
    ticks = iter((1_000, 101_000))
    search_outcome = SimpleNamespace(
        result=solved,
        green_pool=(SimpleNamespace(tab=solved, stable_rank=7),),
    )
    monkeypatch.setattr(
        quality_eval.solver_api,
        "_solve_fingering_with_green_pool",
        lambda *args, **kwargs: search_outcome,
    )
    monkeypatch.setattr(quality_eval.time, "perf_counter_ns", lambda: next(ticks))

    result = quality_eval.evaluate_window(window)

    assert result["outcome"] == "GREEN"
    assert result["supported"] is True
    assert result["comparable"] is True
    assert result["runtime_nanoseconds"] == 100_000
    assert result["solver"]["max_fret"] == 5
    assert result["solver"]["duration_weighted_fret_exposure"] == {
        "exact": "5/1",
        "value": 5.0,
    }
    assert result["comparison"]["string_fret_exact_count"] == 0
    assert result["comparison"]["fret_mae"] == 5
    assert result["green_pool_size"] == 1
    assert result["pool_geometry_diversity"] == {
        "unique_geometries": 1,
        "rate": 1.0,
    }
    assert result["best_in_pool_comparison"] == result["selected_comparison"]
    assert result["selected_vs_best"]["headroom"]["joint_exact_count_gain"] == 0


def test_green_pool_imitation_headroom_and_aggregation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = quality_eval.EvaluationWindow(
        corpus="guitarset",
        corpus_id="fixture",
        source_id="pool.jams",
        source_sha256="0" * 64,
        split="test",
        window_index=0,
        tempo_bpm=Fraction(60),
        notes=(quality_eval.WindowNote(Fraction(0), Fraction(1), 64, 5, 0),),
    )
    selected = Tab(
        (TabNote(Fraction(0), Fraction(1), 4, 5, 1, "p"),),
        quality_eval.STANDARD_TUNING,
        0,
    )
    human_match = Tab(
        (TabNote(Fraction(0), Fraction(1), 5, 0, 0, "p"),),
        quality_eval.STANDARD_TUNING,
        0,
    )
    same_selected_geometry = Tab(
        (TabNote(Fraction(0), Fraction(1), 4, 5, 2, "i"),),
        quality_eval.STANDARD_TUNING,
        0,
    )
    search_outcome = SimpleNamespace(
        result=selected,
        green_pool=(
            SimpleNamespace(tab=selected, stable_rank=10),
            SimpleNamespace(tab=human_match, stable_rank=11),
            SimpleNamespace(tab=same_selected_geometry, stable_rank=12),
        ),
    )
    monkeypatch.setattr(
        quality_eval.solver_api,
        "_solve_fingering_with_green_pool",
        lambda *args, **kwargs: search_outcome,
    )

    result = quality_eval.evaluate_window(window)
    aggregate = quality_eval.aggregate_windows([result])

    assert result["green_pool_size"] == 3
    assert result["pool_geometry_diversity"] == {
        "unique_geometries": 2,
        "rate": pytest.approx(2 / 3),
    }
    assert result["best_in_pool_candidate"] == {
        "candidate_index": 1,
        "stable_rank": 11,
        "imitation_key": {
            "joint_mismatch_count": 0,
            "string_distance": 0,
            "fret_distance": 0,
            "stable_candidate_order": 1,
        },
    }
    assert result["selected_comparison"]["string_fret_exact_count"] == 0
    assert result["best_in_pool_comparison"]["string_fret_exact_count"] == 1
    assert result["selected_vs_best"]["has_imitation_regret"] is True
    assert result["selected_vs_best"]["headroom"] == {
        "joint_exact_count_gain": 1,
        "joint_exact_rate_gain": 1.0,
        "string_distance_reduction": 1,
        "fret_distance_reduction": 5,
    }
    assert aggregate["green_pool_coverage_supported_rate"] == 1.0
    assert aggregate["green_pool_size_mean_supported"] == 3.0
    assert aggregate["best_in_pool_string_fret_exact_rate"] == 1.0
    assert aggregate["selected_vs_best_joint_exact_count_gain"] == 1
    assert aggregate["selected_vs_best_joint_exact_rate_gain"] == 1.0


def test_solver_input_rejection_is_reported_and_aggregated() -> None:
    window = quality_eval.EvaluationWindow(
        corpus="guitarset",
        corpus_id="fixture",
        source_id="duplicate.jams",
        source_sha256="0" * 64,
        split="dev",
        window_index=0,
        tempo_bpm=Fraction(60),
        notes=(
            quality_eval.WindowNote(Fraction(0), Fraction(1), 64, 4, 5),
            quality_eval.WindowNote(Fraction(0), Fraction(1), 64, 5, 0),
        ),
    )

    result = quality_eval.evaluate_window(window)
    aggregate = quality_eval.aggregate_windows([result])

    assert result["outcome"] == "UNSUPPORTED_INPUT"
    assert result["supported"] is False
    assert result["comparable"] is False
    assert result["unsupported_input"]["stage"] == "solver_input"
    assert result["unsupported_input"]["diagnostics"] == [
        {
            "code": "DUPLICATE_ONSET_PITCH",
            "path": "notes[1]",
            "message": (
                "duplicates pitch 64 at onset 0 from notes[0]; "
                "duration/voice would be ambiguous"
            ),
        }
    ]
    assert aggregate["supported_windows"] == 0
    assert aggregate["unsupported_windows"] == 1
    assert aggregate["comparable_windows"] == 0
    assert aggregate["supported_non_green_windows"] == 0
    assert result["green_pool_size"] == 0
    assert result["best_in_pool_comparison"] is None
    assert result["selected_vs_best"] is None
    assert aggregate["unsupported_input_code_counts"] == {"DUPLICATE_ONSET_PITCH": 1}


def test_construction_rejection_consumes_selected_slot_without_replacement(
    tmp_path: Path,
) -> None:
    archive = tmp_path / "annotation.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr(
            "00_a_collision_comp.jams",
            _jams_bytes(
                [
                    {"time": 0, "duration": 0.5, "value": 64, "confidence": 1},
                    {"time": 0.01, "duration": 0.5, "value": 65, "confidence": 1},
                ]
            ),
        )
        target.writestr("00_b_valid_comp.jams", _two_note_jams())
    config = quality_eval.EvaluationConfig(
        guitarset_zip=archive,
        egset12_dir=tmp_path / "unused",
        requested_split="train",
        windows_per_split=1,
        notes_per_window=2,
        quantize_denominator=4,
        beam=4,
    )

    report = quality_eval.run_evaluation(config)

    assert [window["source_id"] for window in report["windows"]] == [
        "00_a_collision_comp.jams"
    ]
    assert report["windows"][0]["outcome"] == "UNSUPPORTED_INPUT"
    assert report["aggregate"]["supported_windows"] == 0
    assert report["aggregate"]["unsupported_input_stage_counts"] == {
        "window_construction": 1
    }


def test_run_evaluation_streams_all_four_declared_splits(tmp_path: Path) -> None:
    archive = tmp_path / "annotation.zip"
    with zipfile.ZipFile(archive, "w") as target:
        target.writestr("00_piece_solo.jams", _two_note_jams())
        target.writestr("04_piece_solo.jams", _two_note_jams())
        target.writestr("05_piece_solo.jams", _two_note_jams())
    egset = tmp_path / "egset12"
    egset.mkdir()
    (egset / "01.jams").write_bytes(_two_note_jams(columnar=True))
    config = quality_eval.EvaluationConfig(
        guitarset_zip=archive,
        egset12_dir=egset,
        requested_split="all",
        windows_per_split=1,
        notes_per_window=2,
        quantize_denominator=24,
        beam=4,
        provenance_label="fixture-run",
    )

    report = quality_eval.run_evaluation(config)

    assert report["schema"] == quality_eval.EVALUATION_SCHEMA
    assert [window["split"] for window in report["windows"]] == [
        "train",
        "dev",
        "test",
        "held-out",
    ]
    assert report["aggregate"]["windows_total"] == 4
    assert report["aggregate"]["supported_windows"] == 4
    assert report["aggregate"]["comparable_windows"] == 4
    assert report["aggregate"]["notes_compared"] == 8
    assert {
        split: aggregate["windows_total"]
        for split, aggregate in report["aggregate_by_split"].items()
    } == {"train": 1, "dev": 1, "test": 1, "held-out": 1}
    assert [source["role"] for source in report["provenance"]] == [
        "train/dev/test",
        "external-audit",
    ]
    assert report["provenance"][0]["performer_splits"]["05"] == "test"
