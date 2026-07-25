from __future__ import annotations

import copy
import json
import re
from typing import cast

import pytest

from fretsure.bench.corpus import CorpusItem, corpus_sha256
from fretsure.bench.frozen_corpus import (
    FROZEN_CORPUS_SHA256,
    load_frozen_benchmark_corpus,
)
from fretsure.bench.preregistration import (
    BENCHMARK_COLLECTION_EXECUTION_VERSION,
    BENCHMARK_PREREGISTRATION_VERSION,
    FORMAL_OPERATIONAL_MAX_IN_FLIGHT_UNITS,
    PUBLIC_COMPACT_PROPOSAL_VERSION,
    BenchmarkPreregistration,
    PreregistrationError,
    artifact_ceilings,
    build_preregistration,
    preregistration_from_bytes,
    preregistration_from_dict,
)
from fretsure.oracle.core import CHECKER_VERSION

SHA256 = re.compile(r"[0-9a-f]{64}\Z")


@pytest.fixture(scope="module")
def preregistration_items() -> object:
    return load_frozen_benchmark_corpus()


@pytest.fixture(scope="module")
def preregistration(preregistration_items: object) -> BenchmarkPreregistration:
    return build_preregistration(preregistration_items)


def test_frozen_corpus_rebuilds_to_the_pinned_identity(preregistration_items: object) -> None:
    items = cast(tuple[CorpusItem, ...], preregistration_items)

    assert len(items) == 503
    assert corpus_sha256(items) == FROZEN_CORPUS_SHA256
    assert {item.item_id for item in items if item.layer != "procedural"} == {
        "public-classical-beethoven-op48-5",
        "public-midi-bwv774",
        "public-midi-bwv775",
    }


def test_preregistration_freezes_full_corpus_schedule_and_versions(
    preregistration: BenchmarkPreregistration,
) -> None:
    wire = preregistration.to_dict()
    corpus = cast(dict[str, object], wire["corpus"])
    snapshot = cast(dict[str, object], corpus["snapshot"])
    schedule = cast(dict[str, object], wire["schedule"])

    assert wire["schema"] == BENCHMARK_PREREGISTRATION_VERSION
    assert wire["run_id"] == "benchmark-v2-formal-20260717"
    assert corpus["corpus_sha256"] == FROZEN_CORPUS_SHA256
    assert len(cast(list[object], snapshot["items"])) == 503
    assert schedule["schedule_seed"] == 2_026_071_700
    assert schedule["collection_unit_count"] == 10_060
    assert len(cast(list[object], schedule["item_permutations"])) == 503
    assert len(cast(list[object], schedule["collection_schedule"])) == 10_060
    for raw in cast(list[dict[str, object]], schedule["item_permutations"]):
        assert sorted(cast(list[int], raw["candidate_permutation"])) == list(range(10))
    assert wire["collection_execution"] == {
        "admission_order": "collection_schedule_index_ascending",
        "canonical_merge_order": ("collection_schedule_index_ascending_then_local_call_index"),
        "client_ownership": "one_agent_and_one_raw_client_per_worker",
        "completion_order": "not_semantic",
        "durability": "unit_intent_and_attempt_fsync_before_provider_request",
        "max_in_flight_units": FORMAL_OPERATIONAL_MAX_IN_FLIGHT_UNITS,
        "protocol": BENCHMARK_COLLECTION_EXECUTION_VERSION,
        "resume_boundary": "completed_durable_unit",
    }
    versions = cast(dict[str, object], wire["versions"])
    assert versions["collection_execution"] == BENCHMARK_COLLECTION_EXECUTION_VERSION
    # Versions are live, not pinned: a bumped checker changes the preregistration
    # a future run declares, which is the honest behaviour.
    assert versions["oracle"] == CHECKER_VERSION


def test_preregistration_is_deterministic_for_one_corpus(
    preregistration_items: object,
    preregistration: BenchmarkPreregistration,
) -> None:
    assert build_preregistration(preregistration_items) == preregistration


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
    assert primary["provider_timeout_envelope_milliseconds"] == 49_582_500_000
    assert full["provider_timeout_envelope_milliseconds"] == 49_879_995_000
    assert budget["recorded_provider_call_elapsed_ceiling_seconds"] == 51_840_000
    assert (
        cast(int, full["provider_timeout_envelope_milliseconds"]) // 1_000
        + cast(int, full["maximum_attempts"]) * 10
    ) <= budget["recorded_provider_call_elapsed_ceiling_seconds"]
    assert budget["provider_policy"] == {
        "connect_timeout_seconds": 5.0,
        "maximum_attempts_per_logical_call": 3,
        "maximum_response_bytes": 1_048_576,
        "maximum_transport_response_bytes": 1_048_576,
        "recorded_attempt_elapsed_overhead_seconds": 10.0,
        "request_timeout_seconds": 300.0,
        "retry_backoff_seconds": [0.5, 1.0],
    }
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
                cast(dict[str, dict[str, int]], item["scheduled_unit_envelopes"])[arm][field]
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


def test_artifact_ceilings_come_from_the_declared_budgets(
    preregistration: BenchmarkPreregistration,
) -> None:
    maximum, unit = artifact_ceilings(preregistration)
    budget = cast(dict[str, object], preregistration.to_dict()["budgets"])
    full = cast(dict[str, object], budget["full_corpus"])

    assert maximum["max_logical_calls"] == full["logical_calls_total"]
    assert maximum["max_attempts"] == full["maximum_attempts"]
    assert maximum["max_recorded_provider_call_elapsed_microseconds"] == (
        cast(int, budget["recorded_provider_call_elapsed_ceiling_seconds"]) * 1_000_000
    )
    assert unit["logical_calls"] == 10
    assert unit["attempts"] == 30
    assert unit["attempt_reserved_output_tokens"] == unit["requested_output_tokens"] * 3
    # 30 attempts * (300s timeout + 10s recorded overhead) + 10 calls * 1.5s backoff
    assert unit["recorded_provider_call_elapsed_microseconds"] == 9_315_000_000


def test_prompt_slots_are_explicit(preregistration: BenchmarkPreregistration) -> None:
    wire = preregistration.to_dict()
    model = cast(dict[str, object], wire["model_and_prompts"])
    prompts = cast(list[dict[str, object]], model["prompts"])
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
    assert versions["arrangement_unison_coalescer"] == ("arrangement-unison-coalescer@0.1.0")


def test_strict_round_trip_rejects_unknown_or_drifted_content(
    preregistration: BenchmarkPreregistration,
) -> None:
    assert preregistration_from_bytes(preregistration.wire_json) == preregistration
    assert preregistration_from_dict(preregistration.to_dict()) == preregistration

    extra = copy.deepcopy(preregistration.to_dict())
    extra["unknown"] = True
    with pytest.raises(PreregistrationError, match="top-level keys"):
        preregistration_from_dict(extra)

    wrong_schema = copy.deepcopy(preregistration.to_dict())
    wrong_schema["schema"] = "benchmark-preregistration@0.2.0"
    with pytest.raises(PreregistrationError, match="version"):
        preregistration_from_dict(wrong_schema)

    drifted_identity = copy.deepcopy(preregistration.to_dict())
    cast(dict[str, object], drifted_identity["corpus"])["corpus_sha256"] = "0" * 64
    with pytest.raises(PreregistrationError, match="frozen benchmark corpus identity"):
        preregistration_from_dict(drifted_identity)

    missing_seed = copy.deepcopy(preregistration.to_dict())
    cast(dict[str, object], missing_seed["schedule"])["schedule_seed"] = "2026071700"
    with pytest.raises(PreregistrationError, match="schedule.schedule_seed"):
        preregistration_from_dict(missing_seed)

    pretty = json.dumps(preregistration.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
    with pytest.raises(PreregistrationError, match="canonical"):
        preregistration_from_bytes(pretty)
