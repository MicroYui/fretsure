from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def _load_script() -> ModuleType:
    name = "evaluate_left_hand_reference"
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "scripts/evaluate_left_hand_reference.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


evaluator = _load_script()


def test_carcassi_public_domain_reference_meets_frozen_threshold() -> None:
    report = evaluator.evaluate_fixture(evaluator.DEFAULT_FIXTURE, beam=16)

    assert report["status"] == "evaluated"
    assert report["passed"] is True
    assert report["oracle_verdict"] == "AMBER"
    assert report["exact_matches"] == 17
    assert report["annotation_count"] == 21
    assert report["minimum_exact_matches"] == 17
    assert report["difficulty_independent"] is True
    assert report["versions"] == {
        "fingering_solver": "fingering-solver@0.6.0",
        "score_solver": "score-solver@0.6.0",
        "left_hand_model": "left-hand-ergonomics@0.1.0",
        "oracle": "oracle@0.3.0",
        "profile": "median@0.1",
        "profile_fingerprint": ("fcefa5394cba876b94881fc77886e6db130d8be10406d46538ad6c83c40b7b62"),
    }
    assert report["mismatches"] == [
        {
            "onset": [9, 1],
            "pitch": 62,
            "expected_left_finger": 4,
            "actual_left_finger": 3,
        },
        {
            "onset": [15, 1],
            "pitch": 62,
            "expected_left_finger": 4,
            "actual_left_finger": 3,
        },
        {
            "onset": [19, 1],
            "pitch": 61,
            "expected_left_finger": 3,
            "actual_left_finger": 2,
        },
        {
            "onset": [21, 1],
            "pitch": 62,
            "expected_left_finger": 4,
            "actual_left_finger": 3,
        },
    ]
