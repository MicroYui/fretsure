"""Reliability estimators: pass@k and pass^k (unbiased, HumanEval-style) + Wilson.

pass@k = P(at least one of k samples passes). pass^k = P(all k pass) — the
"provably playable" reliability. Both use the combinatorial unbiased estimators
from c successes in n samples.
"""

from math import comb

from fretsure.oracle.validation.stats import wilson_ci


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased P(>=1 of k passes) given c of n samples pass. (HumanEval.)"""
    if k > n:
        raise ValueError(f"k={k} > n={n}")
    return 1.0 - comb(n - c, k) / comb(n, k)


def pass_hat_k_item(n: int, c: int, k: int) -> float:
    """Unbiased P(all k passes) for one item = C(c,k)/C(n,k)."""
    if k > n:
        raise ValueError(f"k={k} > n={n}")
    return comb(c, k) / comb(n, k)


def pass_hat_k(per_item: list[tuple[int, int]], k: int) -> float:
    """Mean pass^k over items; each item is (n_samples, c_successes)."""
    vals = [pass_hat_k_item(n, c, k) for n, c in per_item]
    return sum(vals) / len(vals) if vals else 0.0


def wilson(successes: int, n: int, conf: float = 0.95) -> tuple[float, float]:
    return wilson_ci(successes, n, conf)
