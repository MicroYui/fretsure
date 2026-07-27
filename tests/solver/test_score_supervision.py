from __future__ import annotations

import hashlib
import json
from fractions import Fraction
from pathlib import Path

from fretsure.ir import Note
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.api import _solve_fingering_with_green_pool
from fretsure.solver.cost import QualityCost
from fretsure.solver.score_supervision import (
    PUBLISHED_FINGERING_FEATURE_NAMES,
    PUBLISHED_FINGERING_FEATURE_SCHEMA,
    PUBLISHED_FINGERING_MODEL_SHA256,
    PUBLISHED_FINGERING_RANKER_VERSION,
    published_fingering_features,
    select_score_supervised_green_index,
)
from fretsure.tab import Tab, TabNote


def _tab(left_finger: int, *, fret: int = 1) -> Tab:
    return Tab(
        (TabNote(Fraction(0), Fraction(1), 0, fret, left_finger, "p"),),
        (40, 45, 50, 55, 59, 64),
        0,
    )


def _sequence(left_finger: int, *, fret: int = 1) -> Tab:
    return Tab(
        tuple(
            TabNote(Fraction(index), Fraction(1), 0, fret, left_finger, "p")
            for index in range(4)
        ),
        (40, 45, 50, 55, 59, 64),
        0,
    )


def _changing_sequence(left_finger: int) -> Tab:
    return Tab(
        tuple(
            TabNote(Fraction(index), Fraction(1), 0, fret, left_finger, "p")
            for index, fret in enumerate((1, 2, 3, 4))
        ),
        (40, 45, 50, 55, 59, 64),
        0,
    )


def test_frozen_model_has_heldout_gain_and_audited_provenance() -> None:
    path = (
        Path(__file__).parents[2]
        / "src/fretsure/solver/models/published-fingering-ranker-v0.1.0.json"
    )
    raw = path.read_bytes()
    document = json.loads(raw)

    assert hashlib.sha256(raw).hexdigest() == PUBLISHED_FINGERING_MODEL_SHA256
    assert document["ranker_version"] == PUBLISHED_FINGERING_RANKER_VERSION
    assert document["feature_schema"] == PUBLISHED_FINGERING_FEATURE_SCHEMA
    assert tuple(feature["name"] for feature in document["features"]) == (
        PUBLISHED_FINGERING_FEATURE_NAMES
    )
    assert document["training"]["split_policy"] == "composer-grouped-sha256@0.1.0"
    assert set(document["training"]["composer_splits"].values()) == {
        "train",
        "dev",
        "test",
    }
    assert document["evaluation"]["dev"]["model_exact_matches"] > (
        document["evaluation"]["dev"]["baseline_exact_matches"]
    )
    assert document["evaluation"]["test"]["model_exact_matches"] > (
        document["evaluation"]["test"]["baseline_exact_matches"]
    )
    assert document["evaluation"]["carcassi_prelude_1"]["model_exact_matches"] == (
        document["evaluation"]["carcassi_prelude_1"]["baseline_exact_matches"]
    )


def test_features_contain_only_generic_quality_and_finger_fret_counts() -> None:
    values = published_fingering_features(_tab(1), QualityCost(max_fret=1))

    assert len(values) == len(PUBLISHED_FINGERING_FEATURE_NAMES) == 43
    assert all(
        forbidden not in name
        for name in PUBLISHED_FINGERING_FEATURE_NAMES
        for forbidden in ("title", "composer", "style", "grade", "key", "pitch")
    )


def test_score_supervision_selects_supported_finger_fret_near_tie() -> None:
    quality = QualityCost(max_fret=1)
    selected = select_score_supervised_green_index(
        (_changing_sequence(2), _changing_sequence(1)),
        (quality, quality),
        (0, 1),
    )

    assert selected == 1


def test_score_supervision_abstains_outside_four_onset_training_scope() -> None:
    quality = QualityCost(max_fret=1)

    selected = select_score_supervised_green_index(
        (_tab(2), _tab(1)),
        (quality, quality),
        (0, 1),
    )

    assert selected == 0


def test_score_supervision_abstains_for_repeated_identical_shape() -> None:
    quality = QualityCost(max_fret=1)

    selected = select_score_supervised_green_index(
        (_sequence(2), _sequence(1)),
        (quality, quality),
        (0, 1),
    )

    assert selected == 0


def test_score_supervision_guards_ergonomic_boundaries() -> None:
    legacy = QualityCost(max_fret=1, left_hand_effort=2)
    disallowed = (
        QualityCost(max_fret=2, left_hand_effort=2),
        QualityCost(max_fret=1, awkward_fingering_events=1, left_hand_effort=2),
        QualityCost(max_fret=1, left_hand_effort=9),
    )

    for quality in disallowed:
        selected = select_score_supervised_green_index(
            (_changing_sequence(2), _changing_sequence(1)),
            (legacy, quality),
            (0, 1),
        )
        assert selected == 0


def test_decimal_runtime_reproduces_frozen_development_selection() -> None:
    path = Path(__file__).parents[2] / "data/score_corpus/mutopia_pd_additional.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    example = next(
        item
        for item in document["examples"]
        if item["id"] == "mutopia-brahms-op39-no9"
    )
    notes = tuple(
        Note(
            Fraction(*raw["onset"]),
            Fraction(*raw["duration"]),
            raw["pitch"],
            raw["voice"],
        )
        for raw in example["notes"]
    )
    onsets = sorted({note.onset for note in notes})
    window = frozenset(onsets[16:20])
    outcome = _solve_fingering_with_green_pool(
        tuple(note for note in notes if note.onset in window),
        tuple(example["tuning"]),
        example["capo"],
        MEDIAN_HAND,
        tempo_bpm=example["tempo_bpm"],
        beats_per_bar=example["time_signature"][0],
        beam=16,
        _collect_full_green_pool=True,
    )

    selected = select_score_supervised_green_index(
        tuple(finalist.tab for finalist in outcome.green_pool),
        tuple(finalist.quality for finalist in outcome.green_pool),
        tuple(finalist.stable_rank for finalist in outcome.green_pool),
    )

    # The index is a property of the pool, and the pool is whatever the search
    # kept.  Re-frozen twice now: first when the incremental mirror became an
    # exact replica of check_shift_speed, and again for the per-pair d_max table,
    # since a different index-middle allowance admits different frame
    # configurations and so a different set of states survives the beam.  What
    # must hold is not the number but that the ranker still moves off the
    # incumbent to a certified GREEN finalist inside its effort guard, which is
    # asserted directly below rather than implied by the index.
    assert selected != 0
    assert selected == 1
    assert outcome.green_pool[selected].quality.awkward_fingering_events == 0
