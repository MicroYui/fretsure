"""Experimental learned ranking for fully certified GREEN finalists.

The candidate generator, bounded beam, and Oracle are deliberately outside this
offline module.  It can compare only candidates which already passed the
complete Oracle; it is not imported by the runtime solver.  The relative
guard prevents experimental comparisons from increasing the maximum fret over
the legacy GREEN baseline for the same request.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Final

from fretsure.solver.cost import QualityCost

FINGERING_RANKER_VERSION: Final = "fingering-green-ranker@0.1.0"
FINGERING_RANKER_MODEL_SHA256: Final = (
    "b6cc57b0b55ed55f959d827e46276371e87820938c5678adf860ffa60f845315"
)
FINGERING_RANKER_SOURCE_SOLVER_VERSION: Final = "fingering-solver@0.3.0"
FINGERING_RANKER_FEATURE_SCHEMA: Final = (
    "fingering-generic-burden-features@0.1.0"
)
FINGERING_RANKER_MANIFEST: Final = (
    "models/fingering-green-ranker-v0.1.0.json"
)
FINGERING_RANKER_MANIFEST_SHA256: Final = (
    "63fb10e54f22903b30260d3c87b7323d5e18eec0a9431c3df227c4b94f07b9be"
)

# Order, scales, and weights are copied verbatim from the SHA-bound training
# artifact.  Keeping decimal text avoids a platform-dependent binary-float
# parse at the selection boundary; scoring uses a private fixed-precision
# Decimal context below.
FINGERING_RANKER_FEATURE_NAMES: Final = (
    "max_fret",
    "duration_weighted_fret_exposure",
    "shift_count",
    "shift_distance_micrometres",
    "finger_load",
    "string_crossings",
)
FINGERING_RANKER_FEATURE_SCALES_TEXT: Final = (
    "11.883403459353628",
    "151.77989344489924",
    "4.7738194196944495",
    "196095.44941775638",
    "19.807464455304423",
    "20.310323957516268",
)
FINGERING_RANKER_SCALED_WEIGHTS_TEXT: Final = (
    "0.90993065483100111",
    "0.41021332982702235",
    "0.81580745511648445",
    "0.47354811322436891",
    "0.033571922300352407",
    "0",
)

_FEATURE_SCALES = tuple(Decimal(value) for value in FINGERING_RANKER_FEATURE_SCALES_TEXT)
_SCALED_WEIGHTS = tuple(
    Decimal(value) for value in FINGERING_RANKER_SCALED_WEIGHTS_TEXT
)


def _quality_values(quality: QualityCost) -> tuple[int | Fraction, ...]:
    return (
        quality.max_fret,
        quality.fret_exposure,
        quality.shift_count,
        quality.shift_distance_um,
        quality.finger_load,
        quality.string_crossings,
    )


def _decimal(value: int | Fraction) -> Decimal:
    if isinstance(value, Fraction):
        return Decimal(value.numerator) / Decimal(value.denominator)
    return Decimal(value)


def _model_score(quality: QualityCost) -> Decimal:
    """Return the fixed model's non-negative generic-burden score."""

    with localcontext() as context:
        context.prec = 50
        return sum(
            (
                weight * _decimal(value) / scale
                for weight, value, scale in zip(
                    _SCALED_WEIGHTS,
                    _quality_values(quality),
                    _FEATURE_SCALES,
                    strict=True,
                )
            ),
            start=Decimal(0),
        )


def _dominates(left: QualityCost, right: QualityCost) -> bool:
    left_values = _quality_values(left)
    right_values = _quality_values(right)
    return all(
        left_value <= right_value
        for left_value, right_value in zip(left_values, right_values, strict=True)
    ) and any(
        left_value < right_value
        for left_value, right_value in zip(left_values, right_values, strict=True)
    )


def select_guarded_green_index(
    qualities: Sequence[QualityCost],
    stable_ranks: Sequence[int],
    *,
    legacy_index: int = 0,
) -> int:
    """Select a deterministic learned winner for offline experimentation.

    ``qualities`` must describe only complete-Oracle-GREEN candidates.  The
    caller identifies the first GREEN under the legacy baseline order.  No
    absolute fret threshold is used: eligibility is relative to that incumbent
    for this exact request.  Pareto filtering makes it impossible for a
    generically dominated candidate to win even when a learned weight is zero.
    """

    if not qualities or len(qualities) != len(stable_ranks):
        raise ValueError("GREEN quality/rank inputs must be non-empty and aligned")
    if not 0 <= legacy_index < len(qualities):
        raise ValueError("legacy GREEN index is outside the candidate pool")
    if any(type(rank) is not int or rank < 0 for rank in stable_ranks):
        raise ValueError("stable ranks must be non-negative integers")

    legacy_max_fret = qualities[legacy_index].max_fret
    eligible = tuple(
        index
        for index, quality in enumerate(qualities)
        if quality.max_fret <= legacy_max_fret
    )
    frontier = tuple(
        index
        for index in eligible
        if not any(
            other != index and _dominates(qualities[other], qualities[index])
            for other in eligible
        )
    )
    if not frontier:  # A non-empty finite partial order always has a minimum.
        raise RuntimeError("experimental GREEN pool has an empty Pareto frontier")
    return min(
        frontier,
        key=lambda index: (
            _model_score(qualities[index]),
            qualities[index],
            stable_ranks[index],
            index,
        ),
    )


__all__ = [
    "FINGERING_RANKER_FEATURE_NAMES",
    "FINGERING_RANKER_FEATURE_SCALES_TEXT",
    "FINGERING_RANKER_FEATURE_SCHEMA",
    "FINGERING_RANKER_MANIFEST",
    "FINGERING_RANKER_MANIFEST_SHA256",
    "FINGERING_RANKER_MODEL_SHA256",
    "FINGERING_RANKER_SCALED_WEIGHTS_TEXT",
    "FINGERING_RANKER_SOURCE_SOLVER_VERSION",
    "FINGERING_RANKER_VERSION",
    "select_guarded_green_index",
]
