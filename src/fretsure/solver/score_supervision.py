"""Published-score supervision for ranking certified GREEN fingerings.

The physical Oracle and bounded search remain the safety boundary.  This
module only resolves near-ties inside their complete-Oracle GREEN finalist
pool, using generic ergonomic and finger/fret counts learned from licensed
editor-prepared scores.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from decimal import Decimal, localcontext
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Final, NamedTuple, cast

from fretsure.solver.cost import QualityCost
from fretsure.tab import Tab

PUBLISHED_FINGERING_RANKER_VERSION: Final = "published-fingering-ranker@0.1.0"
PUBLISHED_FINGERING_FEATURE_SCHEMA: Final = "published-fingering-features@0.1.0"
PUBLISHED_FINGERING_MODEL_SHA256: Final = (
    "10bd1f9c2751417c5ef3a5f360da5696f736cc24db838857b9d2dd058b6cfed0"
)
PUBLISHED_FINGERING_SOURCE_SOLVER_VERSION: Final = "fingering-solver@0.5.0"
PUBLISHED_FINGERING_MIN_ONSETS: Final = 4
PUBLISHED_FINGERING_MIN_ATTACK_GEOMETRIES: Final = 2

_MODEL_PATH: Final = (
    Path(__file__).with_name("models") / "published-fingering-ranker-v0.1.0.json"
)
_FRET_BUCKETS: Final = (
    ("f1", 1, 1),
    ("f2", 2, 2),
    ("f3", 3, 3),
    ("f4", 4, 4),
    ("f5_7", 5, 7),
    ("f8p", 8, 10_000),
)

# The ranker was fit on exactly these fifteen quality fields, in this order.
# Deriving the list from ``QualityCost.__dataclass_fields__`` would silently
# re-scope a frozen model the moment the solver's objective gains a term, so the
# names are frozen here and cross-checked against the dataclass below.
PUBLISHED_FINGERING_QUALITY_FIELDS: Final = (
    "awkward_fingering_events",
    "left_hand_effort",
    "refingering_count",
    "barre_burden",
    "finger_crossover_burden",
    "fret_height_burden",
    "position_deviation",
    "position_shift_count",
    "position_shift_distance",
    "max_fret",
    "fret_exposure",
    "shift_count",
    "shift_distance_um",
    "finger_load",
    "string_crossings",
)

PUBLISHED_FINGERING_FEATURE_NAMES: Final = (
    *PUBLISHED_FINGERING_QUALITY_FIELDS,
    *(f"finger{finger}_count" for finger in range(1, 5)),
    *(
        f"finger{finger}_x_{bucket}"
        for finger in range(1, 5)
        for bucket, _lower, _upper in _FRET_BUCKETS
    ),
)

if not set(PUBLISHED_FINGERING_QUALITY_FIELDS) <= set(QualityCost.__dataclass_fields__):
    raise RuntimeError(
        "published fingering ranker names a quality field the solver no longer computes"
    )


class _Model(NamedTuple):
    scales: tuple[Decimal, ...]
    weights: tuple[Decimal, ...]
    threshold: Decimal
    max_effort_delta: int


def published_fingering_features(
    tab: Tab,
    quality: QualityCost,
) -> tuple[int | Fraction, ...]:
    """Return the identity-free finalist features used by the frozen model."""

    # Named lookup, not ``astuple``: the model was fit on fifteen specific terms
    # and must keep reading those even when the objective grows a new one.
    values: list[int | Fraction] = [
        getattr(quality, name) for name in PUBLISHED_FINGERING_QUALITY_FIELDS
    ]
    values.extend(
        sum(note.left_finger == finger for note in tab.notes)
        for finger in range(1, 5)
    )
    values.extend(
        sum(
            note.left_finger == finger and lower <= note.fret <= upper
            for note in tab.notes
        )
        for finger in range(1, 5)
        for _bucket, lower, upper in _FRET_BUCKETS
    )
    return tuple(values)


@lru_cache(maxsize=1)
def _model() -> _Model:
    document = json.loads(_MODEL_PATH.read_text(encoding="utf-8"))
    if document["ranker_version"] != PUBLISHED_FINGERING_RANKER_VERSION:
        raise RuntimeError("published-fingering model version does not match runtime")
    if document["feature_schema"] != PUBLISHED_FINGERING_FEATURE_SCHEMA:
        raise RuntimeError("published-fingering feature schema does not match runtime")
    features = cast(list[dict[str, str]], document["features"])
    names = tuple(feature["name"] for feature in features)
    if names != PUBLISHED_FINGERING_FEATURE_NAMES:
        raise RuntimeError("published-fingering feature order does not match runtime")
    guard = cast(dict[str, int], document["guard"])
    if guard["minimum_onsets"] != PUBLISHED_FINGERING_MIN_ONSETS:
        raise RuntimeError("published-fingering training scope does not match runtime")
    if (
        guard["minimum_distinct_attack_geometries"]
        != PUBLISHED_FINGERING_MIN_ATTACK_GEOMETRIES
    ):
        raise RuntimeError("published-fingering geometry scope does not match runtime")
    return _Model(
        tuple(Decimal(feature["scale"]) for feature in features),
        tuple(Decimal(feature["weight"]) for feature in features),
        Decimal(cast(str, document["minimum_score_margin"])),
        guard["max_left_hand_effort_delta"],
    )


def _decimal(value: int | Fraction) -> Decimal:
    if isinstance(value, Fraction):
        return Decimal(value.numerator) / Decimal(value.denominator)
    return Decimal(value)


def _score(tab: Tab, quality: QualityCost, model: _Model) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        return sum(
            (
                weight * _decimal(value) / scale
                for value, scale, weight in zip(
                    published_fingering_features(tab, quality),
                    model.scales,
                    model.weights,
                    strict=True,
                )
            ),
            start=Decimal(0),
        )


def select_score_supervised_green_index(
    tabs: Sequence[Tab],
    qualities: Sequence[QualityCost],
    stable_ranks: Sequence[int],
    *,
    legacy_index: int = 0,
) -> int:
    """Choose a model-supported ergonomic near-tie or keep the incumbent."""

    if not tabs or len(tabs) != len(qualities) or len(tabs) != len(stable_ranks):
        raise ValueError("GREEN finalist inputs must be non-empty and aligned")
    if not 0 <= legacy_index < len(tabs):
        raise ValueError("legacy GREEN index is outside the finalist pool")
    if (
        len({note.onset for note in tabs[legacy_index].notes})
        < PUBLISHED_FINGERING_MIN_ONSETS
    ):
        return legacy_index
    legacy_geometries = {
        tuple(
            sorted(
                (note.string, note.fret)
                for note in tabs[legacy_index].notes
                if note.onset == onset
            )
        )
        for onset in {note.onset for note in tabs[legacy_index].notes}
    }
    if len(legacy_geometries) < PUBLISHED_FINGERING_MIN_ATTACK_GEOMETRIES:
        return legacy_index

    model = _model()
    legacy = qualities[legacy_index]
    eligible = tuple(
        index
        for index, quality in enumerate(qualities)
        if quality.max_fret <= legacy.max_fret
        and quality.awkward_fingering_events <= legacy.awkward_fingering_events
        and quality.left_hand_effort
        <= legacy.left_hand_effort + model.max_effort_delta
    )
    scores = tuple(
        _score(tab, quality, model)
        for tab, quality in zip(tabs, qualities, strict=True)
    )
    selected = max(
        eligible,
        key=lambda index: (scores[index], -stable_ranks[index], -index),
    )
    if selected == legacy_index:
        return legacy_index
    if scores[selected] - scores[legacy_index] < model.threshold:
        return legacy_index
    return selected


__all__ = [
    "PUBLISHED_FINGERING_FEATURE_NAMES",
    "PUBLISHED_FINGERING_FEATURE_SCHEMA",
    "PUBLISHED_FINGERING_MODEL_SHA256",
    "PUBLISHED_FINGERING_MIN_ONSETS",
    "PUBLISHED_FINGERING_MIN_ATTACK_GEOMETRIES",
    "PUBLISHED_FINGERING_RANKER_VERSION",
    "PUBLISHED_FINGERING_SOURCE_SOLVER_VERSION",
    "published_fingering_features",
    "select_score_supervised_green_index",
]
