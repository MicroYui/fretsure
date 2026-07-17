from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from fretsure.bench.corpus import (
    ProceduralCorpusConfig,
    build_primary_procedural_corpus,
    snapshot_corpus,
)
from fretsure.bench.corpus_sources import SourceStatus
from fretsure.bench.preregistration import (
    PUBLIC_COMPACT_PROPOSAL_VERSION,
    BenchmarkPreregistration,
    PreregistrationError,
    budget_markdown,
    build_preregistration,
    preregistration_from_bytes,
    preregistration_from_dict,
)

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "fretsure_test_preregistration_corpus_builder",
    ROOT / "scripts" / "build_benchmark_corpus.py",
)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODULE
_SPEC.loader.exec_module(_MODULE)
_BUILDER = cast(Any, _MODULE)
DEFAULT_CENSUS = cast(Path, _BUILDER.DEFAULT_CENSUS)
DEFAULT_SOURCE_CACHE = cast(Path, _BUILDER.DEFAULT_SOURCE_CACHE)
_public_item = _BUILDER._public_item
_read_census = _BUILDER._read_census
_read_pinned_sources = _BUILDER._read_pinned_sources
PREREG_PATH = ROOT / "docs" / "experiments" / "2026-07-17-benchmark-v2-prereg.json"
BUDGET_PATH = ROOT / "docs" / "experiments" / "2026-07-17-benchmark-v2-budget.md"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@pytest.fixture(scope="module")
def preregistration() -> BenchmarkPreregistration:
    census = _read_census(DEFAULT_CENSUS)
    payloads, _source_hashes = _read_pinned_sources(census, DEFAULT_SOURCE_CACHE)
    procedural = build_primary_procedural_corpus(ProceduralCorpusConfig())
    public = tuple(
        _public_item(
            source,
            payloads[source.source_id],
            position=len(procedural) + index,
        )
        for index, source in enumerate(
            source for source in census.sources if source.status is SourceStatus.INCLUDED
        )
    )
    return build_preregistration(snapshot_corpus(procedural + public))


def test_preregistration_freezes_full_corpus_schedule_and_versions(
    preregistration: BenchmarkPreregistration,
) -> None:
    wire = preregistration.to_dict()
    corpus = cast(dict[str, object], wire["corpus"])
    snapshot = cast(dict[str, object], corpus["snapshot"])
    schedule = cast(dict[str, object], wire["schedule"])

    assert wire["run_id"] == "benchmark-v2-formal-20260717"
    assert wire["package_target_version"] == "0.6.0"
    assert wire["plan_receipt_git_sha"] == "44927517958ecd3b9868bafb7bfe6133be25cc8e"
    assert corpus["corpus_sha256"] == (
        "b4e2a1ed05eb07d82bdea18b9105cdd92b564cf864d8acedaa3c37d820848e8b"
    )
    assert len(cast(list[object], snapshot["items"])) == 503
    assert len(cast(list[object], corpus["ordered_bindings"])) == 503
    assert schedule["schedule_seed"] == 2_026_071_700
    assert schedule["collection_unit_count"] == 10_060
    assert len(cast(list[object], schedule["item_permutations"])) == 503
    assert len(cast(list[object], schedule["collection_schedule"])) == 10_060
    assert schedule["digest_sha256"] == (
        "7453447c7be99c2e4a614015bca739373057fddb44621684f45124a1e0afa2dc"
    )
    for raw in cast(list[dict[str, object]], schedule["item_permutations"]):
        assert sorted(cast(list[int], raw["candidate_permutation"])) == list(range(10))


def test_preregistration_power_gate_is_pre_outcome_and_powered(
    preregistration: BenchmarkPreregistration,
) -> None:
    power = cast(dict[str, object], preregistration.to_dict()["power"])
    gate = cast(dict[str, object], power["gate"])
    search = cast(dict[str, object], power["search"])
    repair = cast(dict[str, object], power["repair"])
    simulation = cast(dict[str, object], repair["frozen_simulation"])

    assert gate == {
        "minimum_power": pytest.approx(0.8024095994885648),
        "per_test_alpha": 0.025,
        "required_tests": ["repair_joint", "search_best4_joint"],
        "selected_family_count": 500,
        "status": "pass",
        "target_power": 0.8,
    }
    assert search["power"] == pytest.approx(0.8024095994885648)
    assert search["improved_probability"] == 0.10
    assert search["worsened_probability"] == 0.05
    assert search["discordance"] == 0.15
    assert simulation["seed"] == 2_026_071_703
    assert simulation["repetitions"] == 100_000
    assert simulation["certified_rejections"] == 88_352
    assert simulation["estimate"] == 0.88352
    assert simulation["mc_se"] == pytest.approx(0.0010144575378003755)
    assert simulation["simulation_sha256"] == (
        "68cac29d4197a723ffddf73429ff248f043f48ae219dc7b2ee13f3b214ab7a52"
    )
    assert len(cast(list[object], repair["sensitivity"])) == 3
    assert len(cast(list[object], search["sensitivity"])) == 3


def test_budget_uses_existing_primary_totals_and_lossless_public_compact_tokens(
    preregistration: BenchmarkPreregistration,
) -> None:
    budget = cast(dict[str, object], preregistration.to_dict()["budgets"])
    primary = cast(dict[str, object], budget["primary_procedural"])
    full = cast(dict[str, object], budget["full_corpus"])
    per_item = cast(list[dict[str, object]], budget["per_item"])
    by_id = {cast(str, value["item_id"]): value for value in per_item}

    assert primary["logical_calls_total"] == 55_000
    assert primary["maximum_attempts"] == 165_000
    assert primary["requested_output_tokens_total"] == 91_909_120
    assert primary["attempt_reserved_output_tokens"] == 275_727_360
    assert primary["response_text_bytes"] == 2_941_091_840
    assert full["logical_calls_total"] == 55_330
    assert full["maximum_attempts"] == 165_990
    assert full["requested_output_tokens_total"] == 92_904_960
    assert full["attempt_reserved_output_tokens"] == 278_714_880
    assert by_id["public-classical-beethoven-op48-5"]["proposal_raw_max_tokens"] == 6_464
    assert by_id["public-midi-bwv775"]["proposal_raw_max_tokens"] == 14_304
    assert by_id["public-midi-bwv774"]["proposal_raw_max_tokens"] == 15_968
    assert by_id["public-classical-beethoven-op48-5"]["event_count"] == 198
    assert by_id["public-midi-bwv775"]["event_count"] == 443
    assert by_id["public-midi-bwv774"]["event_count"] == 495
    assert all(
        by_id[item_id]["proposal_strategy"] == PUBLIC_COMPACT_PROPOSAL_VERSION
        for item_id in (
            "public-classical-beethoven-op48-5",
            "public-midi-bwv775",
            "public-midi-bwv774",
        )
    )
    assert budget["matched_control_prefix_counts"] == {
        "1": 57,
        "2": 167,
        "3": 148,
        "4": 56,
        "5": 75,
    }
    assert budget["ceiling_scope"] == "single_collection_attempt_nontransferable"
    reservation = cast(dict[str, object], budget["reserve_before_next_scheduled_unit"])
    assert reservation["logical_calls"] == 10
    assert reservation["attempts"] == 30
    for item in per_item:
        pair = cast(dict[str, int], item["paired_sample_maximum_envelope"])
        units = cast(dict[str, dict[str, int]], item["scheduled_unit_envelopes"])
        agent = units["agent"]
        raw = units["raw"]
        assert pair["logical_calls"] == agent["logical_calls"] + raw["logical_calls"] == 11
        assert pair["attempts"] == agent["attempts"] + raw["attempts"] == 33
        for field in (
            "requested_output_tokens",
            "response_text_bytes",
            "transport_response_bytes",
        ):
            assert pair[field] == agent[field] + raw[field]
    schedule_sums = {
        field: 10
        * sum(
            sum(
                cast(dict[str, dict[str, int]], item["scheduled_unit_envelopes"])[arm][
                    field
                ]
                for arm in ("agent", "raw")
            )
            for item in per_item
        )
        for field in (
            "logical_calls",
            "attempts",
            "requested_output_tokens",
            "response_text_bytes",
            "transport_response_bytes",
        )
    }
    assert schedule_sums == {
        "logical_calls": full["logical_calls_total"],
        "attempts": full["maximum_attempts"],
        "requested_output_tokens": full["requested_output_tokens_total"],
        "response_text_bytes": full["response_text_bytes"],
        "transport_response_bytes": full["transport_response_bytes"],
    }


def test_prompt_slots_and_dynamic_pre_call_bindings_are_explicit(
    preregistration: BenchmarkPreregistration,
) -> None:
    wire = preregistration.to_dict()
    model = cast(dict[str, object], wire["model_and_prompts"])
    prompts = cast(list[dict[str, object]], model["prompts"])
    bindings = cast(dict[str, object], wire["pre_call_manifest_requirements"])
    itt = cast(dict[str, object], wire["itt_missingness"])
    unit_contract = cast(dict[str, object], wire["unit_contract"])
    solver_target = cast(dict[str, object], unit_contract["solver_target"])
    versions = cast(dict[str, object], wire["versions"])

    assert [value["stage"] for value in prompts] == [
        "proposal_object",
        "proposal_compact",
        "raw_object",
        "raw_compact",
        "repair",
        "critic",
    ]
    assert [value["output_protocol_version"] for value in prompts] == [
        "arrangement-proposal-object@0.1.0",
        "arrangement-proposal-compact@0.1.0",
        "raw-tab-object@0.1.0",
        "raw-tab-compact@0.1.0",
        None,
        None,
    ]
    assert all(SHA256.fullmatch(cast(str, value["template_sha256"])) for value in prompts)
    assert "execution_git_sha" not in wire
    assert "prereg_file_sha256" not in wire
    assert bindings["execution_git_sha"] == (
        "required_external_clean_runner_ready_gate_value_not_stored_in_prereg"
    )
    assert bindings["prereg_file_sha256"] == "required_raw_file_digest_not_stored_in_prereg"
    assert bindings["analysis_binding"] == (
        "analysis_module_digest_or_installed_wheel_RECORD_digest_including_"
        "bound_proposal_raw_protocol_constants"
    )
    assert bindings["forbidden_runtime_discovery"] == [
        "git",
        "subprocess",
        "ambient_import_path_inspection",
    ]
    assert versions["arrangement_unison_coalescer"] == (
        "arrangement-unison-coalescer@0.1.0"
    )
    assert versions["score_solver_composition"] == "score-solver@0.1.0"
    assert solver_target == {
        "aggregate_admitted_segment_search_work_limit": 48_000_000,
        "full_score_reassembly_gate": "oracle_RED_returns_Infeasible",
        "long_score_split": "deterministic_complete_onset_frames_only",
        "maximum_segments": 4,
        "per_segment_solver_work_limit": 12_000_000,
        "source_event_budget_basis": (
            "original_source_notes_plus_chords_before_target_coalescing"
        ),
        "unison_coalescing": (
            "same_onset_pitch_solver_target_only_source_prompt_fidelity_unchanged"
        ),
    }
    assert itt["orphan_intent"] == {
        "abandoned_attempt_analysis": "excluded_in_full",
        "artifact_requirement": "fresh_output_directory",
        "authorization": "new_pre_call_config_and_cost_authorization_required",
        "budget_scope": "single_collection_attempt_nontransferable",
        "complete_attempt_selection": (
            "lowest_numbered_complete_attempt_only_no_replacement_after_complete"
        ),
        "formal_experiment_id": "preregistration.run_id",
        "next_attempt": "strictly_higher_positive_collection_attempt",
        "partial_outcome_use": "forbidden_for_restart_selection",
        "run_id_derivation": "<formal_experiment_id>-attempt-{collection_attempt:03d}",
    }


def test_strict_round_trip_rejects_unknown_or_drifted_content(
    preregistration: BenchmarkPreregistration,
) -> None:
    assert preregistration_from_bytes(preregistration.wire_json) == preregistration
    assert preregistration_from_dict(preregistration.to_dict()) == preregistration

    extra = copy.deepcopy(preregistration.to_dict())
    extra["unknown"] = True
    with pytest.raises(PreregistrationError, match="top-level keys"):
        preregistration_from_dict(extra)

    drift = copy.deepcopy(preregistration.to_dict())
    cast(dict[str, object], drift["schedule"])["digest_sha256"] = "0" * 64
    with pytest.raises(PreregistrationError, match="differs"):
        preregistration_from_dict(drift)

    pretty = json.dumps(preregistration.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
    with pytest.raises(PreregistrationError, match="canonical"):
        preregistration_from_bytes(pretty)


def test_checked_in_preregistration_and_budget_are_exact_generated_bytes(
    preregistration: BenchmarkPreregistration,
) -> None:
    assert PREREG_PATH.read_bytes() == preregistration.wire_json
    assert BUDGET_PATH.read_bytes() == budget_markdown(preregistration).encode("utf-8")
