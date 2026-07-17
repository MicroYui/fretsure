"""Deterministic family-level statistics for benchmark-v2 reports.

The public estimands in this module never treat the ten candidate rows nested in a
musical family as ten independent observations.  Callers first form one value or one
paired delta per family; bootstrap resampling then keeps clusters and preregistered
strata intact.

Power calculation assumptions deliberately live outside this module.  Task 7 may
freeze exact or simulation DGPs and pass their named, pre-outcome power estimates to
``evaluate_power_gate`` without this statistics layer guessing an outcome model.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from enum import StrEnum

import numpy as np
from scipy.stats import beta, binom, norm

MAX_STATISTICAL_COUNT = 1_000_000
MAX_BOOTSTRAP_REPETITIONS = 100_000
MAX_BOOTSTRAP_WORK = 100_000_000
MAX_BOOTSTRAP_ENDPOINTS = 512
MAX_BOOTSTRAP_MATRIX_CELLS = 10_000_000
BOOTSTRAP_BATCH_CHUNK_REPETITIONS = 256
MAX_SIGN_FLIP_DRAWS = 1_000_000
MAX_SIGN_FLIP_WORK = 100_000_000
MAX_ABS_STATISTIC_VALUE = 1.0e15
EXACT_SIGN_FLIP_FAMILIES = 20


class StatisticsInputError(ValueError):
    """One statistical input was outside the finite typed contract."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid statistics {field}: {detail}")


class EstimateStatus(StrEnum):
    ESTIMATED = "estimated"
    DEGENERATE = "degenerate"
    NO_DATA = "no_data"
    NO_DISCORDANCE = "no_discordance"


class InferenceMethod(StrEnum):
    EXACT = "exact"
    MONTE_CARLO = "monte_carlo"


class IntervalMethod(StrEnum):
    WILSON = "wilson"
    CLOPPER_PEARSON = "clopper_pearson"


class BoundKind(StrEnum):
    ZERO = "zero"
    FINITE = "finite"
    POSITIVE_INFINITY = "positive_infinity"
    UNDEFINED = "undefined"


class PowerMethod(StrEnum):
    EXACT = "exact"
    SIMULATION = "simulation"


class PowerGateStatus(StrEnum):
    PASS = "pass"
    INCREASE_REQUIRED = "increase_required"
    NO_FEASIBLE_COUNT = "no_feasible_count"


def _identifier(value: object, field: str) -> str:
    if type(value) is not str or not value or len(value) > 128 or not value.isascii():
        raise StatisticsInputError(field, "must be one nonempty bounded ASCII identifier")
    if any(not (char.isalnum() or char in "._:@+-") for char in value):
        raise StatisticsInputError(field, "contains a character outside the identifier grammar")
    return value


def _exact_int(
    value: object,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise StatisticsInputError(
            field,
            f"must be an exact integer in {minimum}..{maximum}",
        )
    return value


def _finite_number(
    value: object,
    field: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if type(value) is not int and type(value) is not float:
        raise StatisticsInputError(field, "must be an exact finite integer or float")
    number = float(value)
    if not math.isfinite(number) or abs(number) > MAX_ABS_STATISTIC_VALUE:
        raise StatisticsInputError(field, "must be finite and inside the statistical envelope")
    if minimum is not None and number < minimum:
        raise StatisticsInputError(field, f"must be at least {minimum}")
    if maximum is not None and number > maximum:
        raise StatisticsInputError(field, f"must be at most {maximum}")
    return number


def _probability(value: object, field: str, *, open_interval: bool) -> float:
    number = _finite_number(value, field, minimum=0.0, maximum=1.0)
    if open_interval and not 0.0 < number < 1.0:
        raise StatisticsInputError(field, "must be strictly between zero and one")
    return number


def _binomial_counts(successes: object, total: object) -> tuple[int, int]:
    valid_successes = _exact_int(
        successes,
        "successes",
        minimum=0,
        maximum=MAX_STATISTICAL_COUNT,
    )
    valid_total = _exact_int(
        total,
        "total",
        minimum=0,
        maximum=MAX_STATISTICAL_COUNT,
    )
    if valid_successes > valid_total:
        raise StatisticsInputError("successes", "must not exceed total")
    return valid_successes, valid_total


@dataclass(frozen=True, slots=True)
class ConfidenceInterval:
    confidence: float
    lower: float
    upper: float

    def __post_init__(self) -> None:
        _probability(self.confidence, "interval.confidence", open_interval=True)
        lower = _finite_number(self.lower, "interval.lower")
        upper = _finite_number(self.upper, "interval.upper")
        if lower > upper:
            raise StatisticsInputError("interval", "lower must not exceed upper")


@dataclass(frozen=True, slots=True)
class BinomialIntervalResult:
    status: EstimateStatus
    method: IntervalMethod
    successes: int
    total: int
    estimate: float | None
    confidence: float
    interval: ConfidenceInterval | None


def wilson_interval(
    successes: int,
    total: int,
    confidence: float = 0.95,
) -> BinomialIntervalResult:
    """Return a typed Wilson score interval for one family-level binary rate."""

    valid_successes, valid_total = _binomial_counts(successes, total)
    valid_confidence = _probability(confidence, "confidence", open_interval=True)
    if valid_total == 0:
        return BinomialIntervalResult(
            EstimateStatus.NO_DATA,
            IntervalMethod.WILSON,
            0,
            0,
            None,
            valid_confidence,
            None,
        )
    z = float(norm.ppf(1.0 - (1.0 - valid_confidence) / 2.0))
    estimate = valid_successes / valid_total
    denominator = 1.0 + z * z / valid_total
    center = (estimate + z * z / (2.0 * valid_total)) / denominator
    margin = (z / denominator) * math.sqrt(
        estimate * (1.0 - estimate) / valid_total + z * z / (4.0 * valid_total * valid_total)
    )
    interval = ConfidenceInterval(
        valid_confidence,
        max(0.0, center - margin),
        min(1.0, center + margin),
    )
    return BinomialIntervalResult(
        EstimateStatus.ESTIMATED,
        IntervalMethod.WILSON,
        valid_successes,
        valid_total,
        estimate,
        valid_confidence,
        interval,
    )


def clopper_pearson_interval(
    successes: int,
    total: int,
    confidence: float = 0.95,
) -> BinomialIntervalResult:
    """Return an equal-tailed two-sided exact Clopper--Pearson interval."""

    valid_successes, valid_total = _binomial_counts(successes, total)
    valid_confidence = _probability(confidence, "confidence", open_interval=True)
    if valid_total == 0:
        return BinomialIntervalResult(
            EstimateStatus.NO_DATA,
            IntervalMethod.CLOPPER_PEARSON,
            0,
            0,
            None,
            valid_confidence,
            None,
        )
    alpha = 1.0 - valid_confidence
    lower = (
        0.0
        if valid_successes == 0
        else float(beta.ppf(alpha / 2.0, valid_successes, valid_total - valid_successes + 1))
    )
    upper = (
        1.0
        if valid_successes == valid_total
        else float(
            beta.ppf(
                1.0 - alpha / 2.0,
                valid_successes + 1,
                valid_total - valid_successes,
            )
        )
    )
    return BinomialIntervalResult(
        EstimateStatus.ESTIMATED,
        IntervalMethod.CLOPPER_PEARSON,
        valid_successes,
        valid_total,
        valid_successes / valid_total,
        valid_confidence,
        ConfidenceInterval(valid_confidence, lower, upper),
    )


@dataclass(frozen=True, slots=True)
class ExtendedBound:
    kind: BoundKind
    value: float | None

    def __post_init__(self) -> None:
        if type(self.kind) is not BoundKind:
            raise StatisticsInputError("bound.kind", "must be one exact BoundKind")
        if self.kind is BoundKind.FINITE:
            if self.value is None:
                raise StatisticsInputError("bound.value", "finite bounds require a value")
            value = _finite_number(self.value, "bound.value", minimum=0.0)
            if value == 0.0:
                raise StatisticsInputError("bound.value", "zero requires the ZERO bound kind")
        elif self.kind is BoundKind.ZERO:
            if self.value != 0.0:
                raise StatisticsInputError("bound.value", "ZERO requires exact 0.0")
        elif self.value is not None:
            raise StatisticsInputError("bound.value", "non-finite bound kinds require null")


@dataclass(frozen=True, slots=True)
class ExtendedInterval:
    confidence: float
    lower: ExtendedBound
    upper: ExtendedBound


@dataclass(frozen=True, slots=True)
class MatchedOddsRatioResult:
    improved: int
    worsened: int
    point: ExtendedBound
    interval: ExtendedInterval


def _zero_bound() -> ExtendedBound:
    return ExtendedBound(BoundKind.ZERO, 0.0)


def _infinite_bound() -> ExtendedBound:
    return ExtendedBound(BoundKind.POSITIVE_INFINITY, None)


def _undefined_bound() -> ExtendedBound:
    return ExtendedBound(BoundKind.UNDEFINED, None)


def _finite_bound(value: float) -> ExtendedBound:
    if value == 0.0:
        return _zero_bound()
    return ExtendedBound(BoundKind.FINITE, value)


def _odds_from_probability(probability: float) -> ExtendedBound:
    if probability <= 0.0:
        return _zero_bound()
    if probability >= 1.0:
        return _infinite_bound()
    return _finite_bound(probability / (1.0 - probability))


def matched_odds_ratio_interval(
    improved: int,
    worsened: int,
    confidence: float = 0.95,
) -> MatchedOddsRatioResult:
    """Exact conditional matched odds ratio, oriented as improved/worsened."""

    valid_improved = _exact_int(
        improved,
        "improved",
        minimum=0,
        maximum=MAX_STATISTICAL_COUNT,
    )
    valid_worsened = _exact_int(
        worsened,
        "worsened",
        minimum=0,
        maximum=MAX_STATISTICAL_COUNT,
    )
    if valid_improved + valid_worsened > MAX_STATISTICAL_COUNT:
        raise StatisticsInputError("discordant", "sum exceeds the statistical count limit")
    valid_confidence = _probability(confidence, "confidence", open_interval=True)
    alpha = 1.0 - valid_confidence

    if valid_improved == 0 and valid_worsened == 0:
        point = _undefined_bound()
        lower = _zero_bound()
        upper = _infinite_bound()
    else:
        point = (
            _infinite_bound()
            if valid_worsened == 0
            else _zero_bound()
            if valid_improved == 0
            else _finite_bound(valid_improved / valid_worsened)
        )
        probability_lower = (
            0.0
            if valid_improved == 0
            else float(beta.ppf(alpha / 2.0, valid_improved, valid_worsened + 1))
        )
        probability_upper = (
            1.0
            if valid_worsened == 0
            else float(beta.ppf(1.0 - alpha / 2.0, valid_improved + 1, valid_worsened))
        )
        lower = _odds_from_probability(probability_lower)
        upper = _odds_from_probability(probability_upper)
    return MatchedOddsRatioResult(
        valid_improved,
        valid_worsened,
        point,
        ExtendedInterval(valid_confidence, lower, upper),
    )


@dataclass(frozen=True, slots=True)
class McNemarResult:
    status: EstimateStatus
    improved: int
    worsened: int
    discordant: int
    alternative: str
    p_value: float
    odds_ratio: MatchedOddsRatioResult
    continuity_corrected_chi_square: float


def exact_mcnemar(
    improved: int,
    worsened: int,
    confidence: float = 0.95,
) -> McNemarResult:
    """One-sided exact McNemar inference for a treatment-improvement direction."""

    odds_ratio = matched_odds_ratio_interval(improved, worsened, confidence)
    valid_improved = odds_ratio.improved
    valid_worsened = odds_ratio.worsened
    discordant = valid_improved + valid_worsened
    if discordant == 0:
        p_value = 1.0
        status = EstimateStatus.NO_DISCORDANCE
        compatibility = 0.0
    else:
        p_value = float(binom.sf(valid_improved - 1, discordant, 0.5))
        status = EstimateStatus.ESTIMATED
        corrected = max(abs(valid_improved - valid_worsened) - 1, 0)
        compatibility = corrected * corrected / discordant
    return McNemarResult(
        status,
        valid_improved,
        valid_worsened,
        discordant,
        "improved_greater",
        p_value,
        odds_ratio,
        compatibility,
    )


@dataclass(frozen=True, slots=True)
class FamilyDelta:
    family_id: str
    delta: float

    def __post_init__(self) -> None:
        _identifier(self.family_id, "family_delta.family_id")
        _finite_number(self.delta, "family_delta.delta")


@dataclass(frozen=True, slots=True)
class SignFlipResult:
    status: EstimateStatus
    method: InferenceMethod | None
    n_families: int
    n_nonzero: int
    point_delta: float | None
    alternative: str
    p_value: float | None
    seed: int
    exhaustive_permutations: int | None
    monte_carlo_draws: int | None
    extreme_draws: int


def _family_delta_snapshot(values: object) -> tuple[FamilyDelta, ...]:
    if type(values) is not tuple:
        raise StatisticsInputError("family_deltas", "must be an exact tuple")
    snapshot = values
    if len(snapshot) > MAX_STATISTICAL_COUNT:
        raise StatisticsInputError("family_deltas", "family count exceeds the limit")
    if any(type(value) is not FamilyDelta for value in snapshot):
        raise StatisticsInputError("family_deltas", "must contain exact FamilyDelta values")
    ordered = tuple(sorted(snapshot, key=lambda value: value.family_id))
    identifiers = tuple(value.family_id for value in ordered)
    if len(identifiers) != len(set(identifiers)):
        raise StatisticsInputError("family_deltas.family_id", "must be unique")
    return ordered


def _integer_sign_flip_values(
    values: tuple[float, ...],
) -> tuple[tuple[int, ...], int]:
    magnitudes = tuple(abs(value).as_integer_ratio() for value in values)
    scale = max(denominator for _numerator, denominator in magnitudes)
    weights = tuple(numerator * (scale // denominator) for numerator, denominator in magnitudes)
    observed = sum(
        weight if value > 0.0 else -weight for value, weight in zip(values, weights, strict=True)
    )
    return weights, observed


def _exact_sign_flip_extremes(weights: tuple[int, ...], observed: int) -> int:
    permutations = 1 << len(weights)
    current = -sum(weights)
    previous_gray = 0
    extreme = int(current >= observed)
    for ordinal in range(1, permutations):
        gray = ordinal ^ (ordinal >> 1)
        changed = gray ^ previous_gray
        bit = (changed & -changed).bit_length() - 1
        if gray & changed:
            current += 2 * weights[bit]
        else:  # pragma: no cover - reflected Gray code exercises both directions
            current -= 2 * weights[bit]
        extreme += int(current >= observed)
        previous_gray = gray
    return extreme


def paired_sign_flip_test(
    family_deltas: tuple[FamilyDelta, ...],
    *,
    seed: int,
    draws: int = 100_000,
) -> SignFlipResult:
    """One-sided family-level sign-flip test with the frozen exact/MC boundary."""

    values = _family_delta_snapshot(family_deltas)
    valid_seed = _exact_int(seed, "seed", minimum=0, maximum=(1 << 63) - 1)
    valid_draws = _exact_int(
        draws,
        "draws",
        minimum=1,
        maximum=MAX_SIGN_FLIP_DRAWS,
    )
    if not values:
        return SignFlipResult(
            EstimateStatus.NO_DATA,
            None,
            0,
            0,
            None,
            "positive",
            None,
            valid_seed,
            None,
            None,
            0,
        )
    point = math.fsum(value.delta for value in values) / len(values)
    nonzero = tuple(value.delta for value in values if value.delta != 0.0)
    if not nonzero:
        return SignFlipResult(
            EstimateStatus.DEGENERATE,
            InferenceMethod.EXACT,
            len(values),
            0,
            point,
            "positive",
            1.0,
            valid_seed,
            1,
            None,
            1,
        )
    weights, observed = _integer_sign_flip_values(nonzero)
    n_nonzero = len(nonzero)
    if n_nonzero <= EXACT_SIGN_FLIP_FAMILIES:
        permutations = 1 << n_nonzero
        extreme = _exact_sign_flip_extremes(weights, observed)
        return SignFlipResult(
            EstimateStatus.ESTIMATED,
            InferenceMethod.EXACT,
            len(values),
            n_nonzero,
            point,
            "positive",
            extreme / permutations,
            valid_seed,
            permutations,
            None,
            extreme,
        )
    if n_nonzero * valid_draws > MAX_SIGN_FLIP_WORK:
        raise StatisticsInputError("draws", "family-count times draws exceeds the work limit")
    rng = random.Random(valid_seed)
    total_weight = sum(weights)
    extreme = 0
    for _draw in range(valid_draws):
        mask = rng.getrandbits(n_nonzero)
        signed_sum = -total_weight + 2 * sum(
            weight for index, weight in enumerate(weights) if mask & (1 << index)
        )
        extreme += int(signed_sum >= observed)
    return SignFlipResult(
        EstimateStatus.ESTIMATED,
        InferenceMethod.MONTE_CARLO,
        len(values),
        n_nonzero,
        point,
        "positive",
        (extreme + 1) / (valid_draws + 1),
        valid_seed,
        None,
        valid_draws,
        extreme,
    )


@dataclass(frozen=True, slots=True)
class FamilyValue:
    family_id: str
    cluster_id: str
    stratum: str
    value: float

    def __post_init__(self) -> None:
        _identifier(self.family_id, "family_value.family_id")
        _identifier(self.cluster_id, "family_value.cluster_id")
        _identifier(self.stratum, "family_value.stratum")
        _finite_number(self.value, "family_value.value")


@dataclass(frozen=True, slots=True)
class BootstrapStratum:
    stratum: str
    n_families: int
    n_clusters: int


@dataclass(frozen=True, slots=True)
class BootstrapMeanResult:
    status: EstimateStatus
    point: float | None
    n_families: int
    n_clusters: int
    strata: tuple[BootstrapStratum, ...]
    repetitions: int
    seed: int
    two_sided_95: ConfidenceInterval | None
    one_sided_97_5_lower: float | None


@dataclass(frozen=True, slots=True)
class FamilyBootstrapEndpoint:
    """One named estimand sharing a family/cluster bootstrap layout."""

    name: str
    family_values: tuple[FamilyValue, ...]

    def __post_init__(self) -> None:
        _identifier(self.name, "endpoint.name")
        _family_value_snapshot(self.family_values)


def type7_quantile(values: tuple[float, ...], probability: float) -> float:
    """R type-7 / NumPy-linear quantile with a frozen explicit implementation."""

    if type(values) is not tuple or not values:
        raise StatisticsInputError("values", "must be one nonempty exact tuple")
    valid_probability = _probability(probability, "probability", open_interval=False)
    snapshot = tuple(
        sorted(_finite_number(value, f"values[{index}]") for index, value in enumerate(values))
    )
    position = (len(snapshot) - 1) * valid_probability
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return snapshot[lower_index]
    fraction = position - lower_index
    return snapshot[lower_index] + fraction * (snapshot[upper_index] - snapshot[lower_index])


def _family_value_snapshot(values: object) -> tuple[FamilyValue, ...]:
    if type(values) is not tuple:
        raise StatisticsInputError("family_values", "must be an exact tuple")
    snapshot = values
    if len(snapshot) > MAX_STATISTICAL_COUNT:
        raise StatisticsInputError("family_values", "family count exceeds the limit")
    if any(type(value) is not FamilyValue for value in snapshot):
        raise StatisticsInputError("family_values", "must contain exact FamilyValue values")
    ordered = tuple(
        sorted(snapshot, key=lambda value: (value.stratum, value.cluster_id, value.family_id))
    )
    family_ids = tuple(value.family_id for value in ordered)
    if len(family_ids) != len(set(family_ids)):
        raise StatisticsInputError("family_values.family_id", "must be unique")
    cluster_strata: dict[str, str] = {}
    for value in ordered:
        previous = cluster_strata.setdefault(value.cluster_id, value.stratum)
        if previous != value.stratum:
            raise StatisticsInputError(
                "family_values.cluster_id",
                "one cluster cannot cross resampling strata",
            )
    return ordered


def family_cluster_bootstrap_mean(
    family_values: tuple[FamilyValue, ...],
    *,
    seed: int,
    repetitions: int = 10_000,
) -> BootstrapMeanResult:
    """Stratum-preserving cluster bootstrap of an equal-family-weight mean."""

    values = _family_value_snapshot(family_values)
    valid_seed = _exact_int(seed, "seed", minimum=0, maximum=(1 << 63) - 1)
    valid_repetitions = _exact_int(
        repetitions,
        "repetitions",
        minimum=1,
        maximum=MAX_BOOTSTRAP_REPETITIONS,
    )
    if len(values) * valid_repetitions > MAX_BOOTSTRAP_WORK:
        raise StatisticsInputError(
            "repetitions",
            "family-count times repetitions exceeds the work limit",
        )
    if not values:
        return BootstrapMeanResult(
            EstimateStatus.NO_DATA,
            None,
            0,
            0,
            (),
            valid_repetitions,
            valid_seed,
            None,
            None,
        )

    grouped: dict[str, dict[str, list[float]]] = {}
    for value in values:
        grouped.setdefault(value.stratum, {}).setdefault(value.cluster_id, []).append(value.value)
    ordered_groups = tuple(
        (
            stratum,
            tuple(
                (cluster_id, math.fsum(cluster_values), len(cluster_values))
                for cluster_id, cluster_values in sorted(clusters.items())
            ),
            sum(len(cluster_values) for cluster_values in clusters.values()),
        )
        for stratum, clusters in sorted(grouped.items())
    )
    strata = tuple(
        BootstrapStratum(
            stratum,
            sum(cluster_count for _cluster_id, _cluster_sum, cluster_count in clusters),
            len(clusters),
        )
        for stratum, clusters, _original_family_count in ordered_groups
    )
    point = math.fsum(value.value for value in values) / len(values)
    rng = random.Random(valid_seed)
    replicates: list[float] = []
    for _replicate in range(valid_repetitions):
        weighted_stratum_means: list[float] = []
        for _stratum, clusters, original_family_count in ordered_groups:
            selected_sum = 0.0
            selected_count = 0
            for _draw in range(len(clusters)):
                _cluster_id, cluster_sum, cluster_count = clusters[rng.randrange(len(clusters))]
                selected_sum += cluster_sum
                selected_count += cluster_count
            weighted_stratum_means.append(
                (selected_sum / selected_count) * (original_family_count / len(values))
            )
        estimate = math.fsum(weighted_stratum_means)
        if not math.isfinite(estimate):
            raise StatisticsInputError("family_values", "bootstrap mean became non-finite")
        replicates.append(estimate)
    ordered_replicates = tuple(sorted(replicates))
    lower = type7_quantile(ordered_replicates, 0.025)
    upper = type7_quantile(ordered_replicates, 0.975)
    status = (
        EstimateStatus.DEGENERATE
        if ordered_replicates[0] == ordered_replicates[-1]
        else EstimateStatus.ESTIMATED
    )
    return BootstrapMeanResult(
        status,
        point,
        len(values),
        sum(value.n_clusters for value in strata),
        strata,
        valid_repetitions,
        valid_seed,
        ConfidenceInterval(0.95, lower, upper),
        lower,
    )


def _bootstrap_chunk_ranges(
    repetitions: int,
) -> tuple[tuple[int, int], ...]:
    return tuple(
        (
            start,
            min(start + BOOTSTRAP_BATCH_CHUNK_REPETITIONS, repetitions),
        )
        for start in range(0, repetitions, BOOTSTRAP_BATCH_CHUNK_REPETITIONS)
    )


def family_cluster_bootstrap_means(
    endpoints: tuple[FamilyBootstrapEndpoint, ...],
    *,
    seed: int,
    repetitions: int = 10_000,
) -> tuple[tuple[str, BootstrapMeanResult], ...]:
    """Batch equal-family endpoints over one shared cluster-resample schedule.

    All endpoints must have the same canonical family/cluster/stratum layout.  One
    PCG64 schedule is then reused across endpoint columns, avoiding repeated Python
    cluster draws while preserving the exact stratified whole-cluster estimand.
    """

    if type(endpoints) is not tuple or any(
        type(value) is not FamilyBootstrapEndpoint for value in endpoints
    ):
        raise StatisticsInputError(
            "endpoints", "must be an exact tuple of FamilyBootstrapEndpoint values"
        )
    valid_seed = _exact_int(seed, "seed", minimum=0, maximum=(1 << 63) - 1)
    valid_repetitions = _exact_int(
        repetitions,
        "repetitions",
        minimum=1,
        maximum=MAX_BOOTSTRAP_REPETITIONS,
    )
    if not endpoints:
        return ()
    names = tuple(value.name for value in endpoints)
    if len(names) != len(set(names)):
        raise StatisticsInputError("endpoints.name", "must be unique")
    snapshots = tuple(_family_value_snapshot(value.family_values) for value in endpoints)
    layout = tuple((value.family_id, value.cluster_id, value.stratum) for value in snapshots[0])
    if any(
        tuple((value.family_id, value.cluster_id, value.stratum) for value in snapshot) != layout
        for snapshot in snapshots[1:]
    ):
        raise StatisticsInputError(
            "endpoints.family_values",
            "all endpoints must share one exact family/cluster/stratum layout",
        )
    family_count = len(layout)
    endpoint_count = len(endpoints)
    if endpoint_count > MAX_BOOTSTRAP_ENDPOINTS:
        raise StatisticsInputError("endpoints", "endpoint count exceeds the batch limit")
    if (
        max(
            family_count * endpoint_count,
            valid_repetitions * endpoint_count,
        )
        > MAX_BOOTSTRAP_MATRIX_CELLS
    ):
        raise StatisticsInputError("endpoints", "batch matrix exceeds the cell limit")
    if family_count * valid_repetitions > MAX_BOOTSTRAP_WORK:
        raise StatisticsInputError(
            "repetitions", "family-count times repetitions exceeds the work limit"
        )
    if family_count == 0:
        empty = BootstrapMeanResult(
            EstimateStatus.NO_DATA,
            None,
            0,
            0,
            (),
            valid_repetitions,
            valid_seed,
            None,
            None,
        )
        return tuple((name, empty) for name in sorted(names))

    values = np.asarray(
        [[snapshot[index].value for snapshot in snapshots] for index in range(family_count)],
        dtype=np.float64,
    )
    grouped: dict[str, dict[str, list[int]]] = {}
    for index, (_family_id, cluster_id, stratum) in enumerate(layout):
        grouped.setdefault(stratum, {}).setdefault(cluster_id, []).append(index)
    strata = tuple(
        BootstrapStratum(
            stratum,
            sum(len(indices) for indices in clusters.values()),
            len(clusters),
        )
        for stratum, clusters in sorted(grouped.items())
    )
    rng = np.random.Generator(np.random.PCG64(valid_seed))
    replicates = np.zeros((valid_repetitions, endpoint_count), dtype=np.float64)
    for _stratum, clusters in sorted(grouped.items()):
        ordered_clusters = tuple(indices for _cluster, indices in sorted(clusters.items()))
        cluster_sums = np.asarray(
            [values[indices, :].sum(axis=0) for indices in ordered_clusters],
            dtype=np.float64,
        )
        cluster_counts = np.asarray([len(indices) for indices in ordered_clusters], dtype=np.int64)
        original_family_count = sum(len(indices) for indices in ordered_clusters)
        cluster_count = len(ordered_clusters)
        for start, stop in _bootstrap_chunk_ranges(valid_repetitions):
            chunk_repetitions = stop - start
            draws = rng.integers(
                0,
                cluster_count,
                size=(chunk_repetitions, cluster_count),
                dtype=np.int64,
            )
            sampled_cluster_counts = np.zeros((chunk_repetitions, cluster_count), dtype=np.int32)
            rows = np.repeat(np.arange(chunk_repetitions), cluster_count)
            np.add.at(sampled_cluster_counts, (rows, draws.reshape(-1)), 1)
            selected_sums = sampled_cluster_counts @ cluster_sums
            selected_counts = sampled_cluster_counts @ cluster_counts
            replicates[start:stop, :] += (selected_sums / selected_counts[:, np.newaxis]) * (
                original_family_count / family_count
            )
    if not bool(np.isfinite(replicates).all()):
        raise StatisticsInputError("endpoints", "bootstrap mean became non-finite")
    quantiles = np.quantile(replicates, (0.025, 0.975), axis=0, method="linear")
    points = values.mean(axis=0)
    results: list[tuple[str, BootstrapMeanResult]] = []
    for endpoint_index, endpoint in enumerate(endpoints):
        lower = float(quantiles[0, endpoint_index])
        upper = float(quantiles[1, endpoint_index])
        column = replicates[:, endpoint_index]
        status = (
            EstimateStatus.DEGENERATE
            if float(column.min()) == float(column.max())
            else EstimateStatus.ESTIMATED
        )
        results.append(
            (
                endpoint.name,
                BootstrapMeanResult(
                    status,
                    float(points[endpoint_index]),
                    family_count,
                    sum(value.n_clusters for value in strata),
                    strata,
                    valid_repetitions,
                    valid_seed,
                    ConfidenceInterval(0.95, lower, upper),
                    lower,
                ),
            )
        )
    return tuple(sorted(results, key=lambda value: value[0]))


@dataclass(frozen=True, slots=True)
class NamedPValue:
    name: str
    p_value: float

    def __post_init__(self) -> None:
        _identifier(self.name, "p_value.name")
        _probability(self.p_value, "p_value", open_interval=False)


def holm_adjust(p_values: tuple[NamedPValue, ...]) -> tuple[NamedPValue, ...]:
    """Holm--Bonferroni adjusted p-values, returned in canonical name order."""

    if type(p_values) is not tuple or any(type(value) is not NamedPValue for value in p_values):
        raise StatisticsInputError("p_values", "must be an exact tuple of NamedPValue values")
    names = tuple(value.name for value in p_values)
    if len(names) != len(set(names)):
        raise StatisticsInputError("p_values.name", "must be unique")
    ordered = tuple(sorted(p_values, key=lambda value: (value.p_value, value.name)))
    adjusted_by_name: dict[str, float] = {}
    running = 0.0
    count = len(ordered)
    for index, value in enumerate(ordered):
        raw_adjusted = min(1.0, (count - index) * value.p_value)
        running = max(running, raw_adjusted)
        adjusted_by_name[value.name] = running
    return tuple(NamedPValue(name, adjusted_by_name[name]) for name in sorted(adjusted_by_name))


@dataclass(frozen=True, slots=True)
class PowerEstimate:
    test_id: str
    family_count: int
    alpha: float
    power: float
    method: PowerMethod
    assumption_id: str

    def __post_init__(self) -> None:
        _identifier(self.test_id, "power.test_id")
        _exact_int(
            self.family_count,
            "power.family_count",
            minimum=1,
            maximum=MAX_STATISTICAL_COUNT,
        )
        _probability(self.alpha, "power.alpha", open_interval=True)
        _probability(self.power, "power.power", open_interval=False)
        if type(self.method) is not PowerMethod:
            raise StatisticsInputError("power.method", "must be one exact PowerMethod")
        _identifier(self.assumption_id, "power.assumption_id")


@dataclass(frozen=True, slots=True)
class PowerGateResult:
    status: PowerGateStatus
    initial_family_count: int
    target_power: float
    per_test_alpha: float
    required_tests: tuple[str, ...]
    analyzed_family_counts: tuple[int, ...]
    selected_family_count: int | None
    selected_estimates: tuple[PowerEstimate, ...]
    minimum_power: float | None


def evaluate_power_gate(
    estimates: tuple[PowerEstimate, ...],
    *,
    required_tests: tuple[str, ...],
    initial_family_count: int,
    target_power: float = 0.8,
    required_alpha: float = 0.025,
) -> PowerGateResult:
    """Select the first analyzed N satisfying every named pre-outcome power gate.

    This function evaluates caller-supplied exact/simulation results and never reads
    benchmark outcomes or invents a DGP.  The machine preregistration owns each
    ``assumption_id`` and the procedure that produced its power estimate.
    """

    if type(estimates) is not tuple or any(type(value) is not PowerEstimate for value in estimates):
        raise StatisticsInputError("power.estimates", "must be an exact tuple of PowerEstimate")
    if type(required_tests) is not tuple or not required_tests:
        raise StatisticsInputError("power.required_tests", "must be one nonempty exact tuple")
    canonical_tests = tuple(
        sorted(_identifier(value, "power.required_tests") for value in required_tests)
    )
    if len(canonical_tests) != len(set(canonical_tests)):
        raise StatisticsInputError("power.required_tests", "must be unique")
    valid_initial = _exact_int(
        initial_family_count,
        "power.initial_family_count",
        minimum=1,
        maximum=MAX_STATISTICAL_COUNT,
    )
    valid_target = _probability(target_power, "power.target_power", open_interval=True)
    valid_alpha = _probability(required_alpha, "power.required_alpha", open_interval=True)
    by_key: dict[tuple[int, str], PowerEstimate] = {}
    for estimate in estimates:
        if estimate.test_id in canonical_tests and estimate.alpha != valid_alpha:
            raise StatisticsInputError(
                "power.estimates.alpha",
                "required-test estimates must use the preregistered per-test alpha",
            )
        key = (estimate.family_count, estimate.test_id)
        if key in by_key:
            raise StatisticsInputError("power.estimates", "contains a duplicate test/count pair")
        by_key[key] = estimate
    counts = tuple(
        sorted({value.family_count for value in estimates if value.family_count >= valid_initial})
    )
    for family_count in counts:
        selected = tuple(
            by_key[(family_count, test_id)]
            for test_id in canonical_tests
            if (family_count, test_id) in by_key
        )
        if len(selected) != len(canonical_tests):
            continue
        minimum = min(value.power for value in selected)
        if minimum >= valid_target:
            return PowerGateResult(
                PowerGateStatus.PASS
                if family_count == valid_initial
                else PowerGateStatus.INCREASE_REQUIRED,
                valid_initial,
                valid_target,
                valid_alpha,
                canonical_tests,
                counts,
                family_count,
                selected,
                minimum,
            )
    return PowerGateResult(
        PowerGateStatus.NO_FEASIBLE_COUNT,
        valid_initial,
        valid_target,
        valid_alpha,
        canonical_tests,
        counts,
        None,
        (),
        None,
    )


__all__ = [
    "EXACT_SIGN_FLIP_FAMILIES",
    "BinomialIntervalResult",
    "BootstrapMeanResult",
    "BootstrapStratum",
    "BoundKind",
    "ConfidenceInterval",
    "EstimateStatus",
    "ExtendedBound",
    "ExtendedInterval",
    "FamilyDelta",
    "FamilyBootstrapEndpoint",
    "FamilyValue",
    "InferenceMethod",
    "IntervalMethod",
    "MatchedOddsRatioResult",
    "McNemarResult",
    "NamedPValue",
    "PowerEstimate",
    "PowerGateResult",
    "PowerGateStatus",
    "SignFlipResult",
    "StatisticsInputError",
    "PowerMethod",
    "clopper_pearson_interval",
    "evaluate_power_gate",
    "exact_mcnemar",
    "family_cluster_bootstrap_mean",
    "family_cluster_bootstrap_means",
    "holm_adjust",
    "matched_odds_ratio_interval",
    "paired_sign_flip_test",
    "type7_quantile",
    "wilson_interval",
]
