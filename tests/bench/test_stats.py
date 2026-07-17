from __future__ import annotations

import json
import math
import random
from dataclasses import FrozenInstanceError, asdict

import pytest

import fretsure.bench.stats as stats_module
from fretsure.bench.stats import (
    BoundKind,
    EstimateStatus,
    FamilyBootstrapEndpoint,
    FamilyDelta,
    FamilyValue,
    InferenceMethod,
    NamedPValue,
    PowerEstimate,
    PowerGateStatus,
    PowerMethod,
    StatisticsInputError,
    clopper_pearson_interval,
    evaluate_power_gate,
    exact_mcnemar,
    family_cluster_bootstrap_mean,
    family_cluster_bootstrap_means,
    holm_adjust,
    matched_odds_ratio_interval,
    paired_sign_flip_test,
    type7_quantile,
    wilson_interval,
)


def test_batch_cluster_bootstrap_reuses_one_chunked_layout_for_many_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tuple(
        FamilyValue(
            f"family-{index}",
            f"cluster-{index // 2}",
            "procedural",
            index / 49,
        )
        for index in range(50)
    )
    endpoints = tuple(
        FamilyBootstrapEndpoint(
            f"endpoint-{endpoint}",
            tuple(
                FamilyValue(value.family_id, value.cluster_id, value.stratum, value.value**power)
                for value in base
            ),
        )
        for endpoint, power in enumerate(range(1, 21))
    )

    original_chunks = stats_module._bootstrap_chunk_ranges  # noqa: SLF001
    observed_chunks: list[tuple[tuple[int, int], ...]] = []

    def tracked_chunks(repetitions: int) -> tuple[tuple[int, int], ...]:
        result = original_chunks(repetitions)
        observed_chunks.append(result)
        return result

    monkeypatch.setattr(stats_module, "_bootstrap_chunk_ranges", tracked_chunks)
    first = family_cluster_bootstrap_means(endpoints, seed=91, repetitions=513)
    second = family_cluster_bootstrap_means(endpoints, seed=91, repetitions=513)

    assert first == second
    assert len(first) == 20
    assert first[0][1].n_families == 50
    assert first[0][1].n_clusters == 25
    assert first[0][1].point == pytest.approx(sum(value.value for value in base) / 50)
    assert all(result.repetitions == 513 for _name, result in first)
    assert all(len(chunks) == 3 for chunks in observed_chunks)
    assert all(stop - start <= 256 for chunks in observed_chunks for start, stop in chunks)


def test_batch_cluster_bootstrap_bounds_endpoint_and_matrix_allocations() -> None:
    with pytest.raises(StatisticsInputError, match="endpoint count"):
        family_cluster_bootstrap_means(
            tuple(
                FamilyBootstrapEndpoint(f"endpoint-{index}", ())
                for index in range(513)
            ),
            seed=1,
            repetitions=10,
        )

    one_family = (FamilyValue("family", "cluster", "stratum", 0.5),)
    with pytest.raises(StatisticsInputError, match="matrix"):
        family_cluster_bootstrap_means(
            tuple(
                FamilyBootstrapEndpoint(f"endpoint-{index}", one_family)
                for index in range(101)
            ),
            seed=1,
            repetitions=100_000,
        )


def test_wilson_and_two_sided_clopper_pearson_preserve_typed_no_data() -> None:
    wilson = wilson_interval(5, 10)
    exact = clopper_pearson_interval(5, 10)

    assert wilson.status is EstimateStatus.ESTIMATED
    assert wilson.estimate == 0.5
    assert wilson.interval is not None
    assert wilson.interval.lower < 0.5 < wilson.interval.upper
    assert exact.status is EstimateStatus.ESTIMATED
    assert exact.interval is not None
    assert exact.interval.lower < wilson.interval.lower
    assert exact.interval.upper > wilson.interval.upper

    for empty in (wilson_interval(0, 0), clopper_pearson_interval(0, 0)):
        assert empty.status is EstimateStatus.NO_DATA
        assert empty.estimate is None
        assert empty.interval is None


def test_clopper_pearson_zero_and_full_cells_use_exact_boundaries() -> None:
    zero = clopper_pearson_interval(0, 10)
    full = clopper_pearson_interval(10, 10)

    assert zero.interval is not None and full.interval is not None
    assert zero.interval.lower == 0.0
    assert zero.interval.upper == pytest.approx(0.3084971078187608)
    assert full.interval.lower == pytest.approx(1.0 - zero.interval.upper)
    assert full.interval.upper == 1.0


@pytest.mark.parametrize(
    ("function", "args", "field"),
    [
        (wilson_interval, (True, 1), "successes"),
        (clopper_pearson_interval, (1, False), "total"),
        (wilson_interval, (2, 1), "successes"),
        (clopper_pearson_interval, (0, 1, float("nan")), "confidence"),
    ],
)
def test_binomial_intervals_reject_invalid_inputs(
    function: object,
    args: tuple[object, ...],
    field: str,
) -> None:
    with pytest.raises(StatisticsInputError) as caught:
        function(*args)  # type: ignore[operator]

    assert caught.value.field == field


def test_exact_mcnemar_is_directional_and_reports_the_fixed_compatibility_statistic() -> None:
    result = exact_mcnemar(improved=8, worsened=1)

    assert result.status is EstimateStatus.ESTIMATED
    assert result.alternative == "improved_greater"
    assert result.discordant == 9
    assert result.p_value == pytest.approx(10 / 512)
    assert result.continuity_corrected_chi_square == pytest.approx(4.0)
    assert result.odds_ratio.point.kind is BoundKind.FINITE
    assert result.odds_ratio.point.value == 8.0

    tied = exact_mcnemar(improved=1, worsened=1)
    assert tied.continuity_corrected_chi_square == 0.0


def test_mcnemar_no_discordance_and_matched_odds_ratio_bounds_are_json_safe() -> None:
    none = exact_mcnemar(improved=0, worsened=0)
    assert none.status is EstimateStatus.NO_DISCORDANCE
    assert none.p_value == 1.0
    assert none.odds_ratio.point.kind is BoundKind.UNDEFINED
    assert none.odds_ratio.interval.lower.kind is BoundKind.ZERO
    assert none.odds_ratio.interval.upper.kind is BoundKind.POSITIVE_INFINITY

    favorable = matched_odds_ratio_interval(improved=4, worsened=0)
    assert favorable.point.kind is BoundKind.POSITIVE_INFINITY
    assert favorable.interval.lower.kind is BoundKind.FINITE
    assert favorable.interval.upper.kind is BoundKind.POSITIVE_INFINITY
    assert favorable.interval.upper.value is None

    unfavorable = matched_odds_ratio_interval(improved=0, worsened=4)
    assert unfavorable.point.kind is BoundKind.ZERO
    assert unfavorable.interval.lower.kind is BoundKind.ZERO
    assert unfavorable.interval.upper.kind is BoundKind.FINITE
    assert unfavorable.interval.upper.value is not None
    assert math.isfinite(unfavorable.interval.upper.value)
    assert "Infinity" not in json.dumps(asdict(favorable), allow_nan=False)


def test_sign_flip_uses_family_deltas_and_the_exact_twenty_family_boundary() -> None:
    values = tuple(FamilyDelta(f"family-{index:02d}", 1.0) for index in range(20))
    result = paired_sign_flip_test(values, seed=7, draws=11)

    assert result.status is EstimateStatus.ESTIMATED
    assert result.method is InferenceMethod.EXACT
    assert result.n_families == result.n_nonzero == 20
    assert result.exhaustive_permutations == 2**20
    assert result.monte_carlo_draws is None
    assert result.p_value == pytest.approx(1 / 2**20)


def test_sign_flip_switches_at_twenty_one_and_uses_plus_one_correction() -> None:
    values = tuple(FamilyDelta(f"family-{index:02d}", 1.0) for index in range(21))
    result = paired_sign_flip_test(values, seed=11, draws=37)

    assert result.method is InferenceMethod.MONTE_CARLO
    assert result.exhaustive_permutations is None
    assert result.monte_carlo_draws == 37
    assert result.extreme_draws == 0
    assert result.p_value == pytest.approx(1 / 38)
    assert result == paired_sign_flip_test(tuple(reversed(values)), seed=11, draws=37)


def test_sign_flip_all_zero_is_an_explicit_degenerate_result() -> None:
    result = paired_sign_flip_test(
        (FamilyDelta("family-b", 0.0), FamilyDelta("family-a", 0.0)),
        seed=1,
    )

    assert result.status is EstimateStatus.DEGENERATE
    assert result.method is InferenceMethod.EXACT
    assert result.point_delta == 0.0
    assert result.p_value == 1.0
    assert result.exhaustive_permutations == 1

    empty = paired_sign_flip_test((), seed=1)
    assert empty.status is EstimateStatus.NO_DATA
    assert empty.point_delta is None and empty.p_value is None


def test_sign_flip_rejects_duplicate_family_ids_and_non_tuple_input() -> None:
    duplicate = (FamilyDelta("family", 0.1), FamilyDelta("family", 0.2))
    with pytest.raises(StatisticsInputError, match="unique"):
        paired_sign_flip_test(duplicate, seed=1)
    with pytest.raises(StatisticsInputError, match="exact tuple"):
        paired_sign_flip_test([FamilyDelta("family", 0.1)], seed=1)  # type: ignore[arg-type]


def test_type7_quantile_is_linear_and_validates_probability() -> None:
    assert type7_quantile((0.0, 10.0), 0.25) == 2.5
    assert type7_quantile((10.0, 0.0), 0.25) == 2.5
    assert type7_quantile((0.0, 10.0), 0.975) == 9.75

    with pytest.raises(StatisticsInputError, match="probability"):
        type7_quantile((0.0,), 1.1)


def _manual_cluster_replicates(seed: int, repetitions: int) -> tuple[float, ...]:
    # Canonical cluster order is a, b. Cluster a carries two families together;
    # cluster b carries one. This expected result would differ under row resampling.
    clusters = ((0.0, 0.0), (1.0,))
    rng = random.Random(seed)
    out: list[float] = []
    for _ in range(repetitions):
        selected = [clusters[rng.randrange(2)] for _draw in range(2)]
        values = tuple(value for cluster in selected for value in cluster)
        out.append(math.fsum(values) / len(values))
    return tuple(out)


def test_cluster_bootstrap_keeps_clusters_together_and_uses_type7_intervals() -> None:
    records = (
        FamilyValue("family-a1", "cluster-a", "primary", 0.0),
        FamilyValue("family-a2", "cluster-a", "primary", 0.0),
        FamilyValue("family-b1", "cluster-b", "primary", 1.0),
    )
    repetitions = 101
    result = family_cluster_bootstrap_mean(records, seed=19, repetitions=repetitions)
    expected = tuple(sorted(_manual_cluster_replicates(19, repetitions)))

    assert result.status is EstimateStatus.ESTIMATED
    assert result.point == pytest.approx(1 / 3)
    assert result.n_families == 3
    assert result.n_clusters == 2
    assert result.two_sided_95 is not None
    assert result.two_sided_95.lower == type7_quantile(expected, 0.025)
    assert result.two_sided_95.upper == type7_quantile(expected, 0.975)
    assert result.one_sided_97_5_lower == type7_quantile(expected, 0.025)


def test_bootstrap_preserves_strata_is_order_independent_and_marks_degeneracy() -> None:
    records = (
        FamilyValue("a-1", "a-1", "low", 0.0),
        FamilyValue("a-2", "a-2", "low", 0.0),
        FamilyValue("b-1", "b-1", "high", 1.0),
        FamilyValue("b-2", "b-2", "high", 1.0),
    )
    first = family_cluster_bootstrap_mean(records, seed=5, repetitions=31)
    repeated = family_cluster_bootstrap_mean(tuple(reversed(records)), seed=5, repetitions=31)

    assert first == repeated
    assert first.status is EstimateStatus.DEGENERATE
    assert first.point == 0.5
    assert first.two_sided_95 is not None
    assert first.two_sided_95.lower == first.two_sided_95.upper == 0.5
    assert [(value.stratum, value.n_clusters) for value in first.strata] == [
        ("high", 2),
        ("low", 2),
    ]


def test_bootstrap_keeps_original_family_weights_when_strata_have_unequal_clusters() -> None:
    records = (
        FamilyValue("low-a1", "low-a", "low", 0.0),
        FamilyValue("low-a2", "low-a", "low", 0.0),
        FamilyValue("low-b1", "low-b", "low", 0.0),
        FamilyValue("high-a1", "high-a", "high", 1.0),
        FamilyValue("high-b1", "high-b", "high", 1.0),
    )

    result = family_cluster_bootstrap_mean(records, seed=29, repetitions=101)

    # Each stratum is internally constant. Its bootstrap mean therefore stays fixed,
    # and the overall replicate must keep the original 3:2 family weighting.
    assert result.status is EstimateStatus.DEGENERATE
    assert result.point == 0.4
    assert result.two_sided_95 is not None
    assert result.two_sided_95.lower == result.two_sided_95.upper == 0.4


def test_bootstrap_empty_input_is_typed_no_data() -> None:
    result = family_cluster_bootstrap_mean((), seed=1, repetitions=10)

    assert result.status is EstimateStatus.NO_DATA
    assert result.point is None
    assert result.two_sided_95 is None
    assert result.one_sided_97_5_lower is None


def test_bootstrap_rejects_duplicate_families_and_cross_stratum_clusters() -> None:
    duplicate = (
        FamilyValue("family", "cluster-a", "low", 0.0),
        FamilyValue("family", "cluster-b", "low", 1.0),
    )
    with pytest.raises(StatisticsInputError, match="family_id"):
        family_cluster_bootstrap_mean(duplicate, seed=1, repetitions=10)

    crossed = (
        FamilyValue("family-a", "cluster", "low", 0.0),
        FamilyValue("family-b", "cluster", "high", 1.0),
    )
    with pytest.raises(StatisticsInputError, match="cross resampling strata"):
        family_cluster_bootstrap_mean(crossed, seed=1, repetitions=10)

    with pytest.raises(StatisticsInputError, match="repetitions"):
        family_cluster_bootstrap_mean((), seed=1, repetitions=0)


def test_holm_adjustment_is_named_stable_and_monotone() -> None:
    values = (NamedPValue("search", 0.04), NamedPValue("repair", 0.01))
    adjusted = holm_adjust(values)

    assert adjusted == (
        NamedPValue("repair", 0.02),
        NamedPValue("search", 0.04),
    )
    assert adjusted == holm_adjust(tuple(reversed(values)))
    assert holm_adjust((NamedPValue("a", 0.03), NamedPValue("b", 0.04))) == (
        NamedPValue("a", 0.06),
        NamedPValue("b", 0.06),
    )

    with pytest.raises(StatisticsInputError, match="unique"):
        holm_adjust((NamedPValue("same", 0.1), NamedPValue("same", 0.2)))


def test_power_gate_selects_the_first_frozen_pre_outcome_family_count() -> None:
    estimates = (
        PowerEstimate("repair", 500, 0.025, 0.78, PowerMethod.SIMULATION, "repair-dgp-v1"),
        PowerEstimate("search", 500, 0.025, 0.85, PowerMethod.EXACT, "search-dgp-v1"),
        PowerEstimate("repair", 540, 0.025, 0.82, PowerMethod.SIMULATION, "repair-dgp-v1"),
        PowerEstimate("search", 540, 0.025, 0.87, PowerMethod.EXACT, "search-dgp-v1"),
    )
    result = evaluate_power_gate(
        tuple(reversed(estimates)),
        required_tests=("repair", "search"),
        initial_family_count=500,
        target_power=0.8,
    )

    assert result.status is PowerGateStatus.INCREASE_REQUIRED
    assert result.selected_family_count == 540
    assert tuple(value.test_id for value in result.selected_estimates) == ("repair", "search")
    assert result.minimum_power == 0.82
    with pytest.raises(FrozenInstanceError):
        result.selected_family_count = 500  # type: ignore[misc]


def test_power_gate_reports_no_feasible_analyzed_count_without_guessing_a_dgp() -> None:
    result = evaluate_power_gate(
        (
            PowerEstimate(
                "repair",
                500,
                0.025,
                0.79,
                PowerMethod.SIMULATION,
                "frozen-repair-dgp",
            ),
            PowerEstimate(
                "search",
                500,
                0.025,
                0.90,
                PowerMethod.EXACT,
                "frozen-search-dgp",
            ),
        ),
        required_tests=("repair", "search"),
        initial_family_count=500,
    )

    assert result.status is PowerGateStatus.NO_FEASIBLE_COUNT
    assert result.selected_family_count is None
    assert result.selected_estimates == ()
    assert result.minimum_power is None


def test_power_gate_passes_at_initial_n_and_requires_the_preregistered_alpha() -> None:
    estimates = (
        PowerEstimate("repair", 500, 0.025, 0.81, PowerMethod.SIMULATION, "repair-dgp"),
        PowerEstimate("search", 500, 0.025, 0.82, PowerMethod.EXACT, "search-dgp"),
    )
    passed = evaluate_power_gate(
        estimates,
        required_tests=("repair", "search"),
        initial_family_count=500,
        required_alpha=0.025,
    )

    assert passed.status is PowerGateStatus.PASS
    assert passed.selected_family_count == 500
    assert passed.per_test_alpha == 0.025

    with pytest.raises(StatisticsInputError, match="alpha"):
        evaluate_power_gate(
            (
                PowerEstimate(
                    "repair",
                    500,
                    0.05,
                    0.99,
                    PowerMethod.SIMULATION,
                    "wrong-alpha-dgp",
                ),
                estimates[1],
            ),
            required_tests=("repair", "search"),
            initial_family_count=500,
            required_alpha=0.025,
        )
