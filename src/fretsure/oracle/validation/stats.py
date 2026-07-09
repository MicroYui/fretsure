"""Trust statistics for the oracle: confusion matrix vs a human gold set and the
Clopper-Pearson upper bound on the GREEN false-accept rate.

The headline trust number is the GREEN false-accept rate: of the tabs the oracle
certifies GREEN, how many did a real player find unplayable. Soundness demands
this be ~0; we report a one-sided upper bound so "0/N observed" still yields an
honest ceiling.
"""

import json
import math
from dataclasses import dataclass

from scipy.stats import beta, norm

from fretsure.oracle.core import check_playability
from fretsure.oracle.profiles import Profile
from fretsure.tab import tab_from_json

LabeledRow = dict[str, object]


@dataclass(frozen=True)
class ConfusionMatrix:
    green_playable: int
    green_unplayable: int  # GREEN false accepts — the dangerous cell
    red_playable: int  # RED false rejects
    red_unplayable: int
    amber_playable: int
    amber_unplayable: int


def load_labeled(path: str) -> list[LabeledRow]:
    """Load a JSONL gold file: one {"tab": {...}, "human_playable": bool} per line."""
    rows: list[LabeledRow] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def confusion_from_labeled(rows: list[LabeledRow], profile: Profile) -> ConfusionMatrix:
    counts = {
        ("GREEN", True): 0, ("GREEN", False): 0,
        ("RED", True): 0, ("RED", False): 0,
        ("AMBER", True): 0, ("AMBER", False): 0,
    }
    for row in rows:
        tab = tab_from_json(json.dumps(row["tab"]))
        verdict = check_playability(tab, profile).verdict
        playable = bool(row["human_playable"])
        counts[(verdict, playable)] += 1
    return ConfusionMatrix(
        green_playable=counts[("GREEN", True)],
        green_unplayable=counts[("GREEN", False)],
        red_playable=counts[("RED", True)],
        red_unplayable=counts[("RED", False)],
        amber_playable=counts[("AMBER", True)],
        amber_unplayable=counts[("AMBER", False)],
    )


def green_false_accept_upper_bound(cm: ConfusionMatrix, conf: float = 0.975) -> float:
    """One-sided Clopper-Pearson upper bound on P(unplayable | certified GREEN).

    For 0 observed false accepts in n GREEN tabs this equals 1-(1-conf)**(1/n).
    """
    x = cm.green_unplayable
    n = cm.green_playable + cm.green_unplayable
    if n == 0:
        return 0.0
    if x >= n:
        return 1.0
    return float(beta.ppf(conf, x + 1, n - x))


def cohen_kappa(cm: ConfusionMatrix) -> float:
    """Cohen's kappa between the oracle (GREEN=playable / RED=unplayable) and the
    human labels. AMBER is excluded (it is not a certified verdict)."""
    tp = cm.green_playable
    fp = cm.green_unplayable
    fn = cm.red_playable
    tn = cm.red_unplayable
    total = tp + fp + fn + tn
    if total == 0:
        return 1.0
    po = (tp + tn) / total
    pred_yes = (tp + fp) / total
    act_yes = (tp + fn) / total
    pe = pred_yes * act_yes + (1 - pred_yes) * (1 - act_yes)
    if pe >= 1.0:
        return 1.0
    return (po - pe) / (1 - pe)


def wilson_ci(successes: int, n: int, conf: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (clamped to [0, 1])."""
    if n == 0:
        return (0.0, 1.0)
    z = float(norm.ppf(1 - (1 - conf) / 2))
    phat = successes / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))
    return (max(0.0, center - margin), min(1.0, center + margin))
