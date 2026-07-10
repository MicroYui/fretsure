"""Leave-one-out ablation runner.

Each capability (repair / best-of-N / critic) earns its existence only if
removing it degrades a checker-scored metric. Switches map onto Plan 3
``arrange``: repair off -> max_iters=0, best_of_n -> n, critic off -> use_critic.
The ``llm_factory`` yields a fresh LLM per configuration so runs are independent
and deterministic under FakeLLM.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace

from fretsure.agent.arranger import ArrangeGoal
from fretsure.agent.harness import ArrangeResult, arrange, arrange_pool, best_of_k
from fretsure.bench.corpus import CorpusItem
from fretsure.llm.client import LLMClient
from fretsure.metrics.fidelity import faithfulness
from fretsure.oracle.profiles import Profile

LLMFactory = Callable[[], LLMClient]


@dataclass(frozen=True)
class AblationConfig:
    repair: bool = True
    best_of_n: int = 4
    critic: bool = True


_FULL = AblationConfig()


@dataclass(frozen=True)
class ConfigMetrics:
    joint_success: float  # GREEN AND faithfulness gate
    green_rate: float
    mean_melody_f1: float
    mean_edit_steps: float
    items: int


def _score(result: ArrangeResult, item: CorpusItem) -> tuple[bool, bool, float, int]:
    """(is_green, joint_success, melody_f1, applied_edit_steps) for one arrangement."""
    is_green = result.oracle is not None and result.oracle.verdict == "GREEN"
    # count only APPLIED edits (they carry an "op"), not skipped/melody-protected ones
    edits = sum(1 for s in result.trace.steps if s.kind == "EDIT" and "op" in s.data)
    if result.tab is None:
        return is_green, False, 0.0, edits
    gate = faithfulness(item.ir, result.tab)
    return is_green, is_green and gate.passed, gate.melody_f1, edits


def run_config(
    items: list[CorpusItem],
    goal: ArrangeGoal,
    llm_factory: LLMFactory,
    cfg: AblationConfig,
    profile: Profile,
) -> ConfigMetrics:
    green = 0
    joint = 0
    mf1_total = 0.0
    edit_total = 0
    for item in items:
        llm = llm_factory()  # fresh per item: FakeLLM scripts stay isolated + deterministic
        result = arrange(
            item.ir,
            goal,
            llm,
            profile=profile,
            n=cfg.best_of_n,
            max_iters=8 if cfg.repair else 0,
            use_critic=cfg.critic,
        )
        is_green, is_joint, mf1, edits = _score(result, item)
        green += int(is_green)
        joint += int(is_joint)
        mf1_total += mf1
        edit_total += edits
    n = len(items)
    return ConfigMetrics(joint / n, green / n, mf1_total / n, edit_total / n, n)


def leave_one_out(
    items: list[CorpusItem],
    goal: ArrangeGoal,
    llm_factory: LLMFactory,
    profile: Profile,
    *,
    base: AblationConfig = _FULL,
) -> dict[str, ConfigMetrics]:
    out = {
        "full": run_config(items, goal, llm_factory, base, profile),
        "-repair": run_config(items, goal, llm_factory, replace(base, repair=False), profile),
        "-critic": run_config(items, goal, llm_factory, replace(base, critic=False), profile),
    }
    # a best-of-N ablation is only meaningful when the base actually searches >1
    if base.best_of_n > 1:
        out["-best_of_n"] = run_config(
            items, goal, llm_factory, replace(base, best_of_n=1), profile
        )
    return out


@dataclass(frozen=True)
class PairedBestOfN:
    """Best-of-1 vs best-of-N measured on the SAME per-item proposal pool."""

    n: int
    best_of_1: ConfigMetrics
    best_of_n: ConfigMetrics
    green_delta: float  # best_of_n.green_rate - best_of_1.green_rate
    joint_delta: float  # best_of_n.joint_success - best_of_1.joint_success
    items: int


def paired_best_of_n(
    items: list[CorpusItem],
    goal: ArrangeGoal,
    llm_factory: LLMFactory,
    profile: Profile,
    *,
    n: int = 4,
    max_iters: int = 8,
    use_critic: bool = True,
) -> PairedBestOfN:
    """Paired best-of-N ablation: build one pool of N candidates per item, then
    score best-of-1 (the greedy draw) vs best-of-N over that SAME pool.

    ``leave_one_out``'s ``-best_of_n`` arm re-samples the LLM independently, so its
    delta is confounded by stochastic-draw noise (its sign even flips between seeds,
    see docs/BENCHMARK_RESULTS.md). Pairing removes that: the only difference between
    the two arms is selection breadth over identical proposals. Deterministic under
    FakeLLM.
    """
    n = max(2, n)  # best-of-1 vs best-of-1 is a degenerate comparison
    g1 = j1 = e1 = gn = jn = en = 0
    mf1_1 = mf1_n = 0.0
    for item in items:
        llm = llm_factory()
        pool = arrange_pool(
            item.ir, goal, llm, profile=profile, n=n, max_iters=max_iters, use_critic=use_critic
        )
        s1 = _score(best_of_k(pool, 1), item)
        sn = _score(best_of_k(pool, pool.n), item)
        g1 += int(s1[0])
        j1 += int(s1[1])
        mf1_1 += s1[2]
        e1 += s1[3]
        gn += int(sn[0])
        jn += int(sn[1])
        mf1_n += sn[2]
        en += sn[3]
    m = len(items)
    d = m or 1
    k1 = ConfigMetrics(j1 / d, g1 / d, mf1_1 / d, e1 / d, m)
    kn = ConfigMetrics(jn / d, gn / d, mf1_n / d, en / d, m)
    return PairedBestOfN(
        n, k1, kn, kn.green_rate - k1.green_rate, kn.joint_success - k1.joint_success, m
    )
