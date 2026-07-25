"""Corpus-calibrated published-grade estimate for a completed tab.

This is descriptive evidence, not a replacement for the verifiable tier gate.
The calibration source is a single curator and is strongly composer-confounded,
so every result deliberately carries a low-confidence interval.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal, cast

from fretsure.geometry import note_pitch
from fretsure.tab import Tab, TabNote

PUBLISHED_GRADE_ESTIMATOR_VERSION: Final = "published-grade-estimator@0.1.0"
PUBLISHED_GRADE_SYSTEM: Final = "Delcamp/Eric Crouch 1–10"
PUBLISHED_GRADE_MODEL_SHA256: Final = (
    "a3bb39aaf5f881513ed0141d20b3e3776c8b38357dd11351681c38701dddf16a"
)
PUBLISHED_GRADE_TRAINING_SCOPE: Final = (
    "427 attributed classical-guitar scores; one curator; composer-confounded"
)
_MODEL_PATH: Final = Path(__file__).with_name("models") / "published-grade-v0.1.0.json"

DifficultyBand = Literal["foundational", "intermediate", "advanced"]


@dataclass(frozen=True, slots=True)
class PublishedGradeEstimate:
    model_version: str
    grade_system: str
    estimated_grade: int
    likely_interval: tuple[int, int]
    band: DifficultyBand
    confidence: Literal["low"]
    burden_percentile: float
    feature_percentiles: tuple[tuple[str, float], ...]


@dataclass(frozen=True, slots=True)
class _Model:
    version: str
    features: tuple[str, ...]
    knots: dict[str, tuple[tuple[float, float], ...]]
    bands: tuple[tuple[float, int], ...]


@lru_cache(maxsize=1)
def _model() -> _Model:
    document = json.loads(_MODEL_PATH.read_text(encoding="utf-8"))
    if document["model_version"] != PUBLISHED_GRADE_ESTIMATOR_VERSION:
        raise RuntimeError("published-grade model version does not match runtime")
    features = tuple(cast(list[str], document["features"]))
    raw_knots = cast(dict[str, list[list[float]]], document["percentile_knots"])
    knots = {
        feature: tuple(
            (float(value), float(percentile))
            for value, percentile in raw_knots[feature]
        )
        for feature in features
    }
    bands = tuple(
        (
            float(cast(int | float, row["upper_percentile"])),
            cast(int, row["grade"]),
        )
        for row in cast(list[dict[str, object]], document["grade_bands"])
    )
    return _Model(document["model_version"], features, knots, bands)


def _active_count(notes: tuple[TabNote, ...], onset: Fraction) -> int:
    return sum(note.onset <= onset < note.onset + note.duration for note in notes)


def published_grade_features(
    tab: Tab,
    *,
    beats_per_bar: int = 4,
) -> tuple[tuple[str, float], ...]:
    """Return the five runtime analogues of the published-score features."""

    if not tab.notes:
        raise ValueError("published-grade estimate requires at least one note")
    if type(beats_per_bar) is not int or beats_per_bar <= 0:
        raise ValueError("beats_per_bar must be a positive integer")
    pitches = tuple(
        note_pitch(note.string, note.fret, tab.tuning, tab.capo) for note in tab.notes
    )
    frames: defaultdict[Fraction, list[TabNote]] = defaultdict(list)
    for note in tab.notes:
        frames[note.onset].append(note)
    piece_end = max(note.onset + note.duration for note in tab.notes)
    measure_count = max(1, math.ceil(piece_end / beats_per_bar))
    polyphonic_bars: set[int] = set()
    voice_count_max = 1
    for onset, frame in frames.items():
        active = _active_count(tab.notes, onset)
        voice_count_max = max(voice_count_max, min(4, active))
        if len(frame) >= 2 or active >= 2:
            polyphonic_bars.add(int(onset // beats_per_bar))
    return (
        ("midi_max", float(max(pitches))),
        ("max_chord_stack", float(max(len(frame) for frame in frames.values()))),
        ("voice_count_max", float(voice_count_max)),
        ("polyphonic_measure_ratio", len(polyphonic_bars) / measure_count),
        ("measure_count", float(measure_count)),
    )


def _percentile(value: float, knots: tuple[tuple[float, float], ...]) -> float:
    if value <= knots[0][0]:
        return knots[0][1]
    if value >= knots[-1][0]:
        return knots[-1][1]
    for index in range(1, len(knots)):
        right_value, right_percentile = knots[index]
        if value > right_value:
            continue
        left_value, left_percentile = knots[index - 1]
        if right_value == left_value:
            return right_percentile
        ratio = (value - left_value) / (right_value - left_value)
        return left_percentile + ratio * (right_percentile - left_percentile)
    raise AssertionError("percentile knot lookup did not terminate")


def estimate_published_grade(
    tab: Tab,
    *,
    beats_per_bar: int = 4,
) -> PublishedGradeEstimate:
    """Estimate a published grade without changing tier or fingering decisions."""

    model = _model()
    raw = dict(published_grade_features(tab, beats_per_bar=beats_per_bar))
    feature_percentiles = tuple(
        (feature, _percentile(raw[feature], model.knots[feature]))
        for feature in model.features
    )
    burden = sum(value for _feature, value in feature_percentiles) / len(
        feature_percentiles
    )
    grade = model.bands[-1][1]
    for upper, candidate_grade in model.bands:
        if burden < upper:
            grade = candidate_grade
            break
    band: DifficultyBand = (
        "foundational" if grade <= 5 else "intermediate" if grade <= 7 else "advanced"
    )
    return PublishedGradeEstimate(
        model_version=model.version,
        grade_system=PUBLISHED_GRADE_SYSTEM,
        estimated_grade=grade,
        likely_interval=(max(3, grade - 1), min(9, grade + 1)),
        band=band,
        confidence="low",
        burden_percentile=round(burden, 1),
        feature_percentiles=tuple(
            (feature, round(value, 1)) for feature, value in feature_percentiles
        ),
    )


__all__ = [
    "PUBLISHED_GRADE_ESTIMATOR_VERSION",
    "PUBLISHED_GRADE_MODEL_SHA256",
    "PUBLISHED_GRADE_SYSTEM",
    "PUBLISHED_GRADE_TRAINING_SCOPE",
    "PublishedGradeEstimate",
    "estimate_published_grade",
    "published_grade_features",
]
