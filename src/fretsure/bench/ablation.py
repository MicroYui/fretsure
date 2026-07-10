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
from fretsure.agent.harness import arrange
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
        is_green = result.oracle is not None and result.oracle.verdict == "GREEN"
        green += int(is_green)
        if result.tab is not None:
            gate = faithfulness(item.ir, result.tab)
            mf1_total += gate.melody_f1
            if is_green and gate.passed:
                joint += 1
        # count only APPLIED edits (they carry an "op"), not skipped/melody-protected ones
        edit_total += sum(1 for s in result.trace.steps if s.kind == "EDIT" and "op" in s.data)
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
