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
from fretsure.bench.ablation import (
    AblationConfig,
    ConfigMetrics,
    LLMFactory,
    PairedBestOfN,
    PairedCritic,
    leave_one_out,
    paired_best_of_n,
    paired_critic,
)
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
    paired: PairedBestOfN | None = None
    paired_crit: PairedCritic | None = None


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
    paired: bool = False,
) -> BenchReport:
    """Rebuild the procedural corpus and run the full agent + leave-one-out ablation.

    NOTE ON HEADLINES: the ablation deltas (repair/critic/best-of-N "earn existence")
    only appear with a STOCHASTIC LLM (ProxyLLM) on a corpus whose proposals are
    sometimes infeasible. Under ``--stub``/ConstantLLM the rule-stub fallback is
    already GREEN with no repair, so every arm ties `full` — a flat ablation there
    is expected, not evidence that a capability is worthless. best_of_n>=2 so the
    best-of-N arm is a real ablation.

    ``paired`` additionally runs the paired best-of-N ablation (best-of-1 vs
    best-of-N on one shared proposal pool), which — unlike the unpaired ``-best_of_n``
    arm — is not confounded by independent stochastic draws.
    """
    corpus = _corpus(seed, items, bars)
    goal = ArrangeGoal()
    loo = leave_one_out(corpus, goal, llm_factory, profile, base=AblationConfig(best_of_n=2))
    pbn = paired_best_of_n(corpus, goal, llm_factory, profile, n=2) if paired else None
    pcr = paired_critic(corpus, goal, llm_factory, profile, n=2) if paired else None
    return BenchReport(seed, items, loo["full"], loo, CHECKER_VERSION, profile.version, pbn, pcr)


def report_to_dict(report: BenchReport) -> dict[str, Any]:
    out: dict[str, Any] = {
        "seed": report.seed,
        "n_items": report.n_items,
        "checker_version": report.checker_version,
        "profile_version": report.profile_version,
        "ablation": {name: asdict(m) for name, m in report.ablation.items()},
    }
    if report.paired is not None:
        p = report.paired
        out["paired_best_of_n"] = {
            "n": p.n,
            "best_of_1": asdict(p.best_of_1),
            "best_of_n": asdict(p.best_of_n),
            "green_delta": p.green_delta,
            "joint_delta": p.joint_delta,
            "items": p.items,
        }
    if report.paired_crit is not None:
        c = report.paired_crit
        out["paired_critic"] = {
            "n": c.n,
            "without_critic": asdict(c.without_critic),
            "with_critic": asdict(c.with_critic),
            "green_delta": c.green_delta,
            "joint_delta": c.joint_delta,
            "taste_without": c.taste_without,
            "taste_with": c.taste_with,
            "taste_delta": c.taste_delta,
            "items": c.items,
        }
    return out


def main() -> None:
    parser = argparse.ArgumentParser(prog="fretsure-bench")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--items", type=int, default=3)
    parser.add_argument("--bars", type=int, default=2)
    parser.add_argument("--paired", action="store_true", help="also run paired best-of-N")
    parser.add_argument("--stub", action="store_true", help="deterministic stub LLM (no proxy)")
    args = parser.parse_args()

    def factory() -> LLMClient:
        if args.stub:
            from fretsure.llm.client import ConstantLLM

            return ConstantLLM("noop")
        from fretsure.llm.client import ProxyLLM

        return ProxyLLM()

    report = run_benchmark(
        seed=args.seed, items=args.items, llm_factory=factory, bars=args.bars, paired=args.paired
    )
    print(json.dumps(report_to_dict(report), indent=2))


if __name__ == "__main__":
    main()
