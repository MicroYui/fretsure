from dataclasses import replace

import pytest

from fretsure.bench.runner import (
    MAX_BENCHMARK_BARS,
    MAX_BENCHMARK_ITEMS,
    BenchmarkInputError,
    BenchReport,
    report_to_dict,
    run_benchmark,
)
from fretsure.llm.client import ConstantLLM
from fretsure.metrics.fidelity import FIDELITY_CHECKER_VERSION
from fretsure.oracle.input import ORACLE_INPUT_SCHEMA_VERSION
from fretsure.oracle.profiles import MEDIAN_HAND


def test_run_benchmark_reproducible() -> None:
    a = run_benchmark(seed=1, items=2, bars=1, llm_factory=lambda: ConstantLLM("noop"))
    b = run_benchmark(seed=1, items=2, bars=1, llm_factory=lambda: ConstantLLM("noop"))
    assert a == b


def test_run_benchmark_reports_ablation() -> None:
    r = run_benchmark(seed=3, items=2, bars=1, llm_factory=lambda: ConstantLLM("noop"))
    assert isinstance(r, BenchReport)
    assert r.full.items == 2
    assert set(r.ablation) >= {"full", "-repair", "-critic", "-best_of_n"}
    assert r.checker_version.startswith("oracle@")
    assert r.fidelity_checker_version == FIDELITY_CHECKER_VERSION
    assert r.profile_fingerprint == MEDIAN_HAND.fingerprint
    assert r.input_schema_version == ORACLE_INPUT_SCHEMA_VERSION
    assert report_to_dict(r)["fidelity_checker_version"] == FIDELITY_CHECKER_VERSION
    assert report_to_dict(r)["profile_fingerprint"] == MEDIAN_HAND.fingerprint
    assert report_to_dict(r)["input_schema_version"] == ORACLE_INPUT_SCHEMA_VERSION


def test_run_benchmark_full_arranges_generated() -> None:
    # generated lead sheets (rule-stub fallback under the stub LLM) should arrange
    r = run_benchmark(seed=5, items=2, bars=1, llm_factory=lambda: ConstantLLM("noop"))
    assert r.full.green_rate > 0.0


def test_run_benchmark_stamps_one_detached_profile_snapshot() -> None:
    source_profile = replace(MEDIAN_HAND, version="bench-snapshot@0.1")
    expected_fingerprint = source_profile.fingerprint
    mutated = False

    def factory() -> ConstantLLM:
        nonlocal mutated
        if not mutated:
            object.__setattr__(source_profile, "version", "mutated@0.1")
            object.__setattr__(source_profile, "hand_span_mm", 200.0)
            mutated = True
        return ConstantLLM("noop")

    report = run_benchmark(
        seed=5,
        items=1,
        bars=1,
        llm_factory=factory,
        profile=source_profile,
    )

    assert mutated
    assert report.profile_version == "bench-snapshot@0.1"
    assert report.profile_fingerprint == expected_fingerprint


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"seed": True}, "seed"),
        ({"seed": 1 << 63}, "seed"),
        ({"items": 0}, "items"),
        ({"items": -1}, "items"),
        ({"items": True}, "items"),
        ({"items": MAX_BENCHMARK_ITEMS + 1}, "items"),
        ({"bars": 0}, "bars"),
        ({"bars": True}, "bars"),
        ({"bars": MAX_BENCHMARK_BARS + 1}, "bars"),
        ({"items": 100, "bars": 64}, "items*bars"),
        ({"paired": 1}, "paired"),
    ],
)
def test_benchmark_rejects_invalid_or_unbounded_controls_before_factory(
    changes: dict[str, object],
    field: str,
) -> None:
    calls = 0

    def factory() -> ConstantLLM:
        nonlocal calls
        calls += 1
        return ConstantLLM("noop")

    kwargs: dict[str, object] = {
        "seed": 1,
        "items": 1,
        "bars": 1,
        "paired": False,
    }
    kwargs.update(changes)
    with pytest.raises(BenchmarkInputError) as caught:
        run_benchmark(llm_factory=factory, **kwargs)  # type: ignore[arg-type]

    assert caught.value.field == field
    assert calls == 0
