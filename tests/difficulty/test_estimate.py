from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from pathlib import Path

from fretsure.difficulty.estimate import (
    PUBLISHED_GRADE_ESTIMATOR_VERSION,
    PUBLISHED_GRADE_MODEL_SHA256,
    estimate_published_grade,
    published_grade_features,
)
from fretsure.geometry import STANDARD_TUNING
from fretsure.tab import Tab, TabNote


def _tab(*notes: TabNote) -> Tab:
    return Tab(notes, STANDARD_TUNING, 0)


def test_feature_extraction_is_deterministic_and_score_identity_free() -> None:
    tab = _tab(
        TabNote(Fraction(0), Fraction(2), 0, 3, 3, "p"),
        TabNote(Fraction(0), Fraction(1), 5, 3, 4, "a"),
        TabNote(Fraction(1), Fraction(1), 4, 5, 1, "i"),
        TabNote(Fraction(4), Fraction(1), 5, 8, 4, "m"),
    )

    assert dict(published_grade_features(tab)) == {
        "midi_max": 72.0,
        "max_chord_stack": 2.0,
        "voice_count_max": 2.0,
        "polyphonic_measure_ratio": 0.5,
        "measure_count": 2.0,
    }


def test_estimate_is_explicitly_low_confidence_and_versioned() -> None:
    tab = _tab(TabNote(Fraction(0), Fraction(1), 5, 0, 0, "i"))

    estimate = estimate_published_grade(tab)

    assert estimate.model_version == PUBLISHED_GRADE_ESTIMATOR_VERSION
    assert 3 <= estimate.estimated_grade <= 9
    assert estimate.likely_interval[0] <= estimate.estimated_grade
    assert estimate.likely_interval[1] >= estimate.estimated_grade
    assert estimate.confidence == "low"
    assert len(estimate.feature_percentiles) == 5


def test_more_published_burden_does_not_lower_estimate() -> None:
    short = _tab(TabNote(Fraction(0), Fraction(1), 5, 0, 0, "i"))
    long_high = _tab(
        *tuple(
            TabNote(Fraction(index), Fraction(1), 5, 15, 4, "i")
            for index in range(80)
        )
    )

    easy = estimate_published_grade(short)
    hard = estimate_published_grade(long_high)

    assert hard.burden_percentile > easy.burden_percentile
    assert hard.estimated_grade >= easy.estimated_grade


def test_model_uses_real_grades_and_passes_grouped_promotion_threshold() -> None:
    path = (
        Path(__file__).parents[2]
        / "src/fretsure/difficulty/models/published-grade-v0.1.0.json"
    )
    raw = path.read_bytes()
    document = json.loads(raw)
    evaluation = document["evaluation"]

    assert hashlib.sha256(raw).hexdigest() == PUBLISHED_GRADE_MODEL_SHA256
    assert evaluation["labelled_pieces"] == 427
    assert evaluation["grade_source"] == "delcamp-eric-crouch"
    assert "dummy" not in json.dumps(document).lower()
    assert evaluation["splits"]["test"]["within_one"] >= 0.70
    assert evaluation["splits"]["test"]["composers"] >= 2
