"""One-command reproducible benchmark runner + CLI.

``fretsure-bench --seed S --items N`` rebuilds the procedural corpus (from the
seed), runs the full agent and the leave-one-out ablation, and prints a
checker-scored, version-stamped report. Same seed + same LLM -> same numbers.
"""

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any

from fretsure.agent.arranger import ArrangeGoal
from fretsure.bench.ablation import AblationConfig, ConfigMetrics, LLMFactory, leave_one_out
from fretsure.bench.corpus import CorpusItem
from fretsure.bench.generator import GenConfig, generate_leadsheet
from fretsure.llm.client import LLMClient
from fretsure.oracle.core import CHECKER_VERSION
from fretsure.oracle.profiles import MEDIAN_HAND, Profile


@dataclass(frozen=True)
class BenchReport:
    seed: int
    n_items: int
    full: ConfigMetrics
    ablation: dict[str, ConfigMetrics]
    checker_version: str
    profile_version: str


def _corpus(seed: int, items: int, bars: int) -> list[CorpusItem]:
    return [
        CorpusItem(
            generate_leadsheet(GenConfig(seed=seed * 10007 + i, bars=bars)),
            "procedural",
            "generated",
            2,
            f"gen{seed}-{i}",
        )
        for i in range(items)
    ]


def run_benchmark(
    *,
    seed: int,
    items: int,
    llm_factory: LLMFactory,
    profile: Profile = MEDIAN_HAND,
    bars: int = 2,
) -> BenchReport:
    corpus = _corpus(seed, items, bars)
    loo = leave_one_out(
        corpus, ArrangeGoal(), llm_factory, profile, base=AblationConfig(best_of_n=1)
    )
    return BenchReport(seed, items, loo["full"], loo, CHECKER_VERSION, profile.version)


def report_to_dict(report: BenchReport) -> dict[str, Any]:
    return {
        "seed": report.seed,
        "n_items": report.n_items,
        "checker_version": report.checker_version,
        "profile_version": report.profile_version,
        "ablation": {name: asdict(m) for name, m in report.ablation.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="fretsure-bench")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--items", type=int, default=3)
    parser.add_argument("--bars", type=int, default=2)
    parser.add_argument("--stub", action="store_true", help="deterministic stub LLM (no proxy)")
    args = parser.parse_args()

    def factory() -> LLMClient:
        if args.stub:
            from fretsure.llm.client import ConstantLLM

            return ConstantLLM("noop")
        from fretsure.llm.client import ProxyLLM

        return ProxyLLM()

    report = run_benchmark(seed=args.seed, items=args.items, llm_factory=factory, bars=args.bars)
    print(json.dumps(report_to_dict(report), indent=2))


if __name__ == "__main__":
    main()
