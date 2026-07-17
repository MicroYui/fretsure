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
from fretsure.metrics.fidelity import faithfulness, faithfulness_dimensions
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
    mean_melody_f1: float | None
    melody_evaluated_items: int
    mean_edit_steps: float
    items: int


def _score(
    result: ArrangeResult, item: CorpusItem
) -> tuple[bool, bool, float | None, int]:
    """(is_green, joint_success, melody_f1, applied_edit_steps) for one arrangement."""
    is_green = result.oracle is not None and result.oracle.verdict == "GREEN"
    # Count only versioned applied-edit events, never rejected/no-op model output.
    edits = sum(1 for step in result.trace.steps if step.event == "EDIT_APPLIED")
    if result.tab is None:
        melody_score = (
            0.0 if "melody" in faithfulness_dimensions(item.ir) else None
        )
        return is_green, False, melody_score, edits
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
    mf1_items = 0
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
        if mf1 is not None:
            mf1_total += mf1
            mf1_items += 1
        edit_total += edits
    n = len(items)
    denominator = n or 1
    return ConfigMetrics(
        joint_success=joint / denominator,
        green_rate=green / denominator,
        mean_melody_f1=None if mf1_items == 0 else mf1_total / mf1_items,
        melody_evaluated_items=mf1_items,
        mean_edit_steps=edit_total / denominator,
        items=n,
    )


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
    mf1_items_1 = mf1_items_n = 0
    for item in items:
        llm = llm_factory()
        pool = arrange_pool(
            item.ir, goal, llm, profile=profile, n=n, max_iters=max_iters, use_critic=use_critic
        )
        s1 = _score(best_of_k(pool, 1), item)
        sn = _score(best_of_k(pool, pool.n), item)
        g1 += int(s1[0])
        j1 += int(s1[1])
        if s1[2] is not None:
            mf1_1 += s1[2]
            mf1_items_1 += 1
        e1 += s1[3]
        gn += int(sn[0])
        jn += int(sn[1])
        if sn[2] is not None:
            mf1_n += sn[2]
            mf1_items_n += 1
        en += sn[3]
    m = len(items)
    d = m or 1
    k1 = ConfigMetrics(
        joint_success=j1 / d,
        green_rate=g1 / d,
        mean_melody_f1=None if mf1_items_1 == 0 else mf1_1 / mf1_items_1,
        melody_evaluated_items=mf1_items_1,
        mean_edit_steps=e1 / d,
        items=m,
    )
    kn = ConfigMetrics(
        joint_success=jn / d,
        green_rate=gn / d,
        mean_melody_f1=None if mf1_items_n == 0 else mf1_n / mf1_items_n,
        melody_evaluated_items=mf1_items_n,
        mean_edit_steps=en / d,
        items=m,
    )
    return PairedBestOfN(
        n, k1, kn, kn.green_rate - k1.green_rate, kn.joint_success - k1.joint_success, m
    )


@dataclass(frozen=True)
class PairedCritic:
    """Critic-on vs critic-off selection measured over one shared candidate pool.

    The critic's JOB is musical taste, not the playability+faithfulness joint gate —
    and because `_rank` keys on melody_recall while the gate keys on top-voice
    melody_f1, the critic can be neutral or even NEGATIVE on ``joint_delta`` by
    construction. So the critic is judged on ``taste_delta`` (did enabling it select
    higher-critic-scored arrangements?); ``joint_delta`` is reported honestly as the
    playability-faithfulness side effect, not as the critic's yardstick.
    """

    n: int
    without_critic: ConfigMetrics
    with_critic: ConfigMetrics
    green_delta: float  # with - without (0 by construction: critic ranks below green)
    joint_delta: float  # with - without (a side effect, NOT the critic's objective)
    taste_without: float  # mean critic.overall of the without-critic selection
    taste_with: float  # mean critic.overall of the with-critic selection
    taste_delta: float  # with - without (>= 0: critic steers toward higher taste)
    items: int


def _selected_taste(result: ArrangeResult) -> float:
    return result.critic.overall if result.critic is not None else 0.0


def paired_critic(
    items: list[CorpusItem],
    goal: ArrangeGoal,
    llm_factory: LLMFactory,
    profile: Profile,
    *,
    n: int = 4,
    max_iters: int = 8,
) -> PairedCritic:
    """Paired critic ablation: build one pool (with critic scores) per item, then
    select best-of-N WITH vs WITHOUT the critic term on that SAME pool.

    Like ``paired_best_of_n``, this removes the stochastic-draw noise that confounds
    ``leave_one_out``'s unpaired ``-critic`` arm: the only difference between the two
    arms is whether the critic participates in ranking. Deterministic under FakeLLM.
    The critic is judged on ``taste_delta`` (its actual objective); see PairedCritic.
    """
    n = max(1, n)
    g0 = j0 = e0 = g1 = j1 = e1 = 0
    mf0 = mf1 = t0 = t1 = 0.0
    mf_items_0 = mf_items_1 = 0
    for item in items:
        llm = llm_factory()
        pool = arrange_pool(
            item.ir, goal, llm, profile=profile, n=n, max_iters=max_iters, use_critic=True
        )
        r0 = best_of_k(pool, pool.n, use_critic=False)
        r1 = best_of_k(pool, pool.n, use_critic=True)
        s0 = _score(r0, item)
        s1 = _score(r1, item)
        g0 += int(s0[0])
        j0 += int(s0[1])
        if s0[2] is not None:
            mf0 += s0[2]
            mf_items_0 += 1
        e0 += s0[3]
        t0 += _selected_taste(r0)
        g1 += int(s1[0])
        j1 += int(s1[1])
        if s1[2] is not None:
            mf1 += s1[2]
            mf_items_1 += 1
        e1 += s1[3]
        t1 += _selected_taste(r1)
    m = len(items)
    d = m or 1
    without = ConfigMetrics(
        joint_success=j0 / d,
        green_rate=g0 / d,
        mean_melody_f1=None if mf_items_0 == 0 else mf0 / mf_items_0,
        melody_evaluated_items=mf_items_0,
        mean_edit_steps=e0 / d,
        items=m,
    )
    with_ = ConfigMetrics(
        joint_success=j1 / d,
        green_rate=g1 / d,
        mean_melody_f1=None if mf_items_1 == 0 else mf1 / mf_items_1,
        melody_evaluated_items=mf_items_1,
        mean_edit_steps=e1 / d,
        items=m,
    )
    return PairedCritic(
        n, without, with_,
        with_.green_rate - without.green_rate,
        with_.joint_success - without.joint_success,
        t0 / d, t1 / d, (t1 - t0) / d, m,
    )
