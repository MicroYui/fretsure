"""Fail-closed reliability estimators for benchmark reports.

pass@k = P(at least one of k samples passes). pass^k = P(all k pass the stamped
model/profile). Both use the combinatorial unbiased estimators from c successes
in n samples.  Invalid count data is rejected before combinatorics run, and an
empty item collection is represented as an explicit no-data result rather than
the superficially perfect-looking value ``0.0``.
"""

import math
from dataclasses import dataclass
from math import comb
from typing import Literal

from fretsure.oracle.validation.stats import wilson_ci

PassKStatus = Literal["estimated", "no_items"]

MAX_RELIABILITY_SAMPLES = 10_000
MAX_RELIABILITY_ITEMS = 100_000
MAX_RELIABILITY_WORK = 1_000_000


class ReliabilityInputError(ValueError):
    """A typed failure for malformed reliability-estimator inputs."""

    def __init__(self, field: str, value: object, detail: str) -> None:
        self.field = field
        self.value = value
        self.detail = detail
        # Do not render or inspect the type name of an untrusted value here.
        # Both operations can execute caller-controlled hooks.
        super().__init__(f"invalid {field}: {detail}")


@dataclass(frozen=True)
class PassKResult:
    """Canonical mean pass^k estimate, including its item denominator."""

    status: PassKStatus
    n_items: int
    k: int
    value: float | None


def _validate_exact_int(value: object, field: str) -> int:
    # bool is an int subclass and must not silently become count data.
    if type(value) is not int:
        raise ReliabilityInputError(
            field,
            value,
            "must be an exact int (bool is not accepted)",
        )
    return value


def _validate_counts(
    n: object,
    c: object,
    k: object,
    *,
    field_prefix: str | None = None,
) -> tuple[int, int, int]:
    def field(name: str) -> str:
        return f"{field_prefix}.{name}" if field_prefix is not None else name

    valid_n = _validate_exact_int(n, field("n"))
    valid_c = _validate_exact_int(c, field("c"))
    valid_k = _validate_exact_int(k, field("k"))

    if valid_n <= 0:
        raise ReliabilityInputError(field("n"), valid_n, "must be greater than zero")
    if valid_n > MAX_RELIABILITY_SAMPLES:
        raise ReliabilityInputError(
            field("n"),
            valid_n,
            f"must not exceed the public sample limit {MAX_RELIABILITY_SAMPLES}",
        )
    if valid_c < 0:
        raise ReliabilityInputError(field("c"), valid_c, "must be non-negative")
    if valid_c > valid_n:
        raise ReliabilityInputError(
            field("c"),
            valid_c,
            f"must not exceed {field('n')}={valid_n}",
        )
    if valid_k < 1:
        raise ReliabilityInputError(field("k"), valid_k, "must be at least one")
    if valid_k > valid_n:
        raise ReliabilityInputError(
            field("k"),
            valid_k,
            f"must not exceed {field('n')}={valid_n}",
        )
    return valid_n, valid_c, valid_k


def _validate_requested_k(k: object) -> int:
    valid_k = _validate_exact_int(k, "k")
    if valid_k < 1:
        raise ReliabilityInputError("k", valid_k, "must be at least one")
    if valid_k > MAX_RELIABILITY_SAMPLES:
        raise ReliabilityInputError(
            "k",
            valid_k,
            f"must not exceed the public sample limit {MAX_RELIABILITY_SAMPLES}",
        )
    return valid_k


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased P(>=1 of k passes) given c of n samples pass. (HumanEval.)"""
    valid_n, valid_c, valid_k = _validate_counts(n, c, k)
    return 1.0 - comb(valid_n - valid_c, valid_k) / comb(valid_n, valid_k)


def pass_hat_k_item(n: int, c: int, k: int) -> float:
    """Unbiased P(all k passes) for one item = C(c,k)/C(n,k)."""
    valid_n, valid_c, valid_k = _validate_counts(n, c, k)
    return comb(valid_c, valid_k) / comb(valid_n, valid_k)


def pass_hat_k_estimate(per_item: list[tuple[int, int]], k: int) -> PassKResult:
    """Return canonical mean pass^k, or an explicit no-items result.

    Each item must be an exact ``(n_samples, c_successes)`` tuple.  ``k`` is
    validated even when no items exist; its upper bound is then checked against
    every item's sample count when data is present.
    """

    valid_k = _validate_requested_k(k)
    if type(per_item) is not list:
        raise ReliabilityInputError("per_item", per_item, "must be a list")
    if len(per_item) > MAX_RELIABILITY_ITEMS:
        raise ReliabilityInputError(
            "per_item",
            len(per_item),
            f"item count must not exceed {MAX_RELIABILITY_ITEMS}",
        )
    # Iterate an inert snapshot.  A caller may otherwise mutate the exact list
    # after the length checks (including from a tracing hook) and change both
    # the work performed and the reported denominator.
    item_snapshot = tuple(per_item[: MAX_RELIABILITY_ITEMS + 1])
    if len(item_snapshot) > MAX_RELIABILITY_ITEMS:
        raise ReliabilityInputError(
            "per_item",
            len(item_snapshot),
            f"item count must not exceed {MAX_RELIABILITY_ITEMS}",
        )
    if len(item_snapshot) * valid_k > MAX_RELIABILITY_WORK:
        raise ReliabilityInputError(
            "per_item",
            len(item_snapshot),
            (
                "item-count times k must not exceed the public work limit "
                f"{MAX_RELIABILITY_WORK}"
            ),
        )
    validated_items: list[tuple[int, int]] = []
    for index, item in enumerate(item_snapshot):
        item_field = f"per_item[{index}]"
        if type(item) is not tuple or len(item) != 2:
            raise ReliabilityInputError(
                item_field,
                item,
                "must be an exact (n_samples, c_successes) tuple",
            )
        n, c = item
        valid_n, valid_c, _ = _validate_counts(
            n,
            c,
            valid_k,
            field_prefix=item_field,
        )
        validated_items.append((valid_n, valid_c))

    if not validated_items:
        return PassKResult(status="no_items", n_items=0, k=valid_k, value=None)
    values = [
        comb(valid_c, valid_k) / comb(valid_n, valid_k)
        for valid_n, valid_c in tuple(validated_items)
    ]
    return PassKResult(
        status="estimated",
        n_items=len(values),
        k=valid_k,
        value=sum(values) / len(values),
    )


def pass_hat_k(per_item: list[tuple[int, int]], k: int) -> float | None:
    """Compatibility helper returning mean pass^k, or ``None`` with no items."""

    return pass_hat_k_estimate(per_item, k).value


def wilson(successes: int, n: int, conf: float = 0.95) -> tuple[float, float]:
    valid_successes = _validate_exact_int(successes, "successes")
    valid_n = _validate_exact_int(n, "n")
    if valid_successes < 0:
        raise ReliabilityInputError("successes", valid_successes, "must be non-negative")
    if valid_n < 0:
        raise ReliabilityInputError("n", valid_n, "must be non-negative")
    if valid_n > MAX_RELIABILITY_SAMPLES:
        raise ReliabilityInputError(
            "n",
            valid_n,
            f"must not exceed the public sample limit {MAX_RELIABILITY_SAMPLES}",
        )
    if valid_successes > valid_n:
        raise ReliabilityInputError(
            "successes",
            valid_successes,
            f"must not exceed n={valid_n}",
        )
    conf_type = type(conf)
    if conf_type is not int and conf_type is not float:
        raise ReliabilityInputError(
            "confidence",
            conf,
            "must be a finite real number strictly between 0 and 1",
        )
    try:
        valid_conf = float(conf)
    except (OverflowError, ValueError) as error:
        raise ReliabilityInputError(
            "confidence",
            conf,
            "must be a finite real number strictly between 0 and 1",
        ) from error
    if not math.isfinite(valid_conf) or not 0.0 < valid_conf < 1.0:
        raise ReliabilityInputError(
            "confidence",
            conf,
            "must be a finite real number strictly between 0 and 1",
        )
    return wilson_ci(valid_successes, valid_n, valid_conf)
