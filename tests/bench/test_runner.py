from fretsure.bench.runner import BenchReport, report_to_dict, run_benchmark
from fretsure.llm.client import ConstantLLM
from fretsure.metrics.fidelity import FIDELITY_CHECKER_VERSION


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
    assert report_to_dict(r)["fidelity_checker_version"] == FIDELITY_CHECKER_VERSION


def test_run_benchmark_full_arranges_generated() -> None:
    # generated lead sheets (rule-stub fallback under the stub LLM) should arrange
    r = run_benchmark(seed=5, items=2, bars=1, llm_factory=lambda: ConstantLLM("noop"))
    assert r.full.green_rate > 0.0
