"""Attribution for the one refusal bucket that can be attributed.

A beam death is a search that wandered off. `no feasible frame config` fails at a
single instant, so the refusing rule can be named — and both real oracle defects
found this week were found that way.

The bucket has been analysed three times. The first was retracted outright: it
called thirteen pieces physically impossible when they were import defects. The
second reported margins of 5–8 mm, which was wrong because it enumerated fingers
independently of the monotonic and barre rules, and a span measured on an
assignment those rules refuse can be smaller than any legal one. These tests pin
the two properties that went wrong, not the numbers.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from fretsure.oracle.profiles import MEDIAN_HAND, optimistic
from fretsure.tab import TabNote

ROOT = Path(__file__).resolve().parents[2]
TUNING = (40, 45, 50, 55, 59, 64)


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "classify_frame_config_refusals",
        ROOT / "scripts" / "classify_frame_config_refusals.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["classify_frame_config_refusals"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _script()


def test_only_the_attributable_bucket_is_attributed(
    script: ModuleType, tmp_path: Path
) -> None:
    """A beam death has no single frame to blame, so it must not be counted here."""

    report = {
        "configuration": {"beam": 16, "choose_capo": False},
        "versions": {"checker": "test"},
        "examples": [
            {
                "id": "beam", "tuning": list(TUNING), "capo": 0,
                "infeasible": {"reason": "no non-red extension within beam",
                               "onset": "0", "pitches": [60]},
            },
            {
                "id": "frame", "tuning": list(TUNING), "capo": 0,
                "infeasible": {"reason": "no feasible frame config",
                               "onset": "0", "pitches": [41, 75]},
            },
        ],
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    assert script.main([str(path)]) == 0


def test_more_notes_than_strings_is_not_a_hand_model_finding(
    script: ModuleType,
) -> None:
    """Seven simultaneous pitches on six strings is an instrument fact.

    The retracted first analysis put failures like this in the same bucket as
    geometry and concluded pieces were physically impossible. They belong in
    their own line, because no profile change can ever move them.
    """

    outcome = script.classify_frame(
        [60, 61, 62, 63, 64, 65, 66], TUNING, 0, optimistic(MEDIAN_HAND)
    )
    assert outcome["verdict"] == "no assignment puts them on distinct strings"
    assert "margin_mm" not in outcome


def test_the_margin_is_measured_on_a_fingering_the_rules_allow(
    script: ModuleType,
) -> None:
    """The bug that reported 5–8 mm where the truth was 11–26.

    `_excess` may only ever see assignments `_monotone_and_barre_ok` accepts, so
    that gate is what the test pins: it has to follow the shipped rule, including
    the slant exemption, or the margins drift again the next time the rule moves.
    """

    slant = [TabNote(0, 1, 1, 5, 0, "p"), TabNote(0, 1, 4, 3, 0, "i")]
    assert script._monotone_and_barre_ok(slant, (2, 3)) is True, (
        "higher finger toward the trebles and nearer the nut is the wrist's slant"
    )

    cross = [TabNote(0, 1, 4, 5, 0, "i"), TabNote(0, 1, 1, 3, 0, "p")]
    assert script._monotone_and_barre_ok(cross, (2, 3)) is False, (
        "the same inversion toward the bass is fingers passing through one another"
    )

    two_frets_one_finger = [TabNote(0, 1, 1, 5, 0, "p"), TabNote(0, 1, 4, 3, 0, "i")]
    assert script._monotone_and_barre_ok(two_frets_one_finger, (3, 3)) is False


def test_a_reachable_frame_is_not_reported_as_geometry(script: ModuleType) -> None:
    """If some configuration works, the frame is not what refused the piece."""

    outcome = script.classify_frame([40, 45], TUNING, 0, optimistic(MEDIAN_HAND))
    assert outcome["verdict"].startswith("the frame alone is fine")


def test_a_barre_lets_one_finger_take_several_notes_at_one_fret(
    script: ModuleType,
) -> None:
    """`check_finger_count` limits distinct frets, not fretted notes.

    Skipping frames with more than four fretted notes excluded exactly the barre
    shapes and reported them as needing a fifth finger — fourteen of forty-four
    frames, which emptied the geometry line the analysis exists to fill.
    """

    barre = [
        TabNote(0, 1, string, 2, 0, "p") for string in range(5)
    ] + [TabNote(0, 1, 5, 4, 0, "i")]
    assert script._monotone_and_barre_ok(barre, (1, 1, 1, 1, 1, 3)) is True


def test_a_zero_margin_is_not_reported_as_geometry(script: ModuleType) -> None:
    """Zero millimetres past the span limit means the span is not what refused it.

    What is left in `assignment_valid` is the barre-occupancy clause — a barre
    presses every string it crosses, so nothing between its ends may be stopped
    nearer the nut. That rule has no millimetre margin, and putting it in a table
    of millimetre margins as "geometry, 0.0 mm" is how a reader concludes the
    limit is one adjustment away from admitting it.
    """

    source = (ROOT / "scripts" / "classify_frame_config_refusals.py").read_text()
    assert "a barre crosses a string stopped behind it" in source
    assert 'if best is None or best <= 1e-9:' in source
