"""The verifier's boundary, measured against what guitarists write.

`scripts/measure_oracle_discrimination.py` builds both sides of the comparison
out of the published corpus: positives are frames where an editor printed which
finger plays which note, negatives are those same frames with one note pulled
along the neck. Nothing in it rests on provenance, which is what the 1,718
raw-LLM tabs rest on -- a presumption that turned out to be wrong for eleven of
them, all G major chords.

These tests pin the instrument rather than the numbers it currently reports. A
measuring device that silently starts measuring something else is worse than no
device, and this one has already done that twice: once by spelling every chord
as simultaneous thumb plucks, so the right-hand rule refused everything and the
verifier appeared to reject 100% of printed fingerings; and once by
re-enumerating fingerings for the displaced case, so a note moved a third of a
metre still looked admissible half the time because *some other* fingering
survived.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from fretsure.oracle.core import check_playability
from fretsure.oracle.profiles import MEDIAN_HAND

ROOT = Path(__file__).resolve().parents[2]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "measure_oracle_discrimination",
        ROOT / "scripts" / "measure_oracle_discrimination.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["measure_oracle_discrimination"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _script()


def test_a_chord_is_spelled_as_a_gesture_not_as_simultaneous_thumb_plucks(script) -> None:
    """The bug that made the verifier look 100% wrong.

    Six notes at one onset are one right-hand gesture. Written as six thumb
    plucks they violate the right-hand rule whatever the left hand does, so the
    measurement reports the spelling instead of the geometry.
    """

    placements = [(string, 2 if string else 0, 1 if string else 0) for string in range(6)]
    tab = script._frame_tab(placements, (40, 45, 50, 55, 59, 64), 0)
    thumbs = [note for note in tab.notes if note.right_finger == "p"]
    assert len(thumbs) == 3, "the thumb sweeps the low three, i-m-a take the top three"
    assert {note.attack_group for note in thumbs} == {1}, "the sweep is one gesture"
    assert all(note.attack_group == 0 for note in tab.notes if note.right_finger != "p")


def test_the_displaced_frame_is_the_same_hand_shape_pulled_apart(script) -> None:
    """The bug that made a third of a metre look playable.

    The negative has to be the fingering that was admitted, with one note moved.
    Re-deriving a fingering for the displaced pitches asks a different and much
    easier question, and with dozens of realisations per frame something always
    survives it.
    """

    # Pitches whose admitted realisation sits low on the neck, so that twelve
    # frets of displacement still lands on the fretboard. Higher pitches run off
    # the end and the instrument correctly reports them as unjudgeable, which
    # would make this test pass for the wrong reason.
    frame = {
        "tuning": (40, 45, 50, 55, 59, 64),
        "capo": 0,
        "sounding": (41, 48),
        "fingers": {41: frozenset({1}), 48: frozenset({3})},
    }
    realisation = script._admitted_realisation(frame, MEDIAN_HAND)
    assert realisation == [(0, 1, 1), (1, 3, 3)], realisation
    near = script._displaced_admits(frame, MEDIAN_HAND, realisation, 1)
    far = script._displaced_admits(frame, MEDIAN_HAND, realisation, 12)
    assert far is False, "twelve frets apart is not a shape any hand makes"
    assert near is True, "one fret of extra stretch on a first-position shape is ordinary"


def test_the_curve_separates_printed_fingerings_from_pulled_apart_ones(script) -> None:
    """The property the instrument exists to check, stated as a shape.

    Not a threshold: a verifier refusing at zero displacement is cutting into
    real music, and one admitting at twelve frets is a rubber stamp. Only the
    gap between the ends says whether it discriminates at all.
    """

    report = script.measure(MEDIAN_HAND, frozenset())
    curve = {row["displacement_frets"]: row["refused_rate"] for row in report["curve"]}
    assert curve[0] is not None and curve[12] is not None
    assert curve[0] < 0.25, (
        f"the verifier refuses {100*curve[0]:.1f}% of fingerings a human printed"
    )
    assert curve[12] > 0.95, (
        f"a note moved twelve frets is still admitted {100*(1-curve[12]):.1f}% of the time"
    )
    assert curve[12] - curve[0] > 0.6, "the ends have to be far apart to mean anything"
    # Monotone in the middle, allowing for frames that drop out as the
    # displacement runs off the fretboard.
    ordered = [curve[d] for d in (0, 1, 2, 3, 6, 12) if curve[d] is not None]
    assert ordered == sorted(ordered), f"refusal should not fall as the stretch grows: {ordered}"


def test_frames_that_cannot_be_judged_are_reported_not_absorbed(script) -> None:
    """A frame too dense to enumerate is not evidence either way.

    Folding them into the admitted side would flatter the verifier; folding them
    into the refused side would damn it. Both would be the instrument reporting
    its own limits as a finding.
    """

    report = script.measure(MEDIAN_HAND, frozenset({"test"}))
    for row in report["curve"]:
        judged = row["admitted"] + row["refused"]
        assert row["unjudged"] >= 0
        if judged:
            assert abs(row["refused_rate"] - row["refused"] / judged) < 1e-9


def test_displacing_widens_the_shape_instead_of_collapsing_it(script) -> None:
    """The bug that made correct admissions look like lost discrimination.

    The second version moved whichever fretted note came first in the
    realisation, and always up the neck. When that note was the *lowest* of the
    shape, moving it up carried it toward the others: four frames counted as
    six-fret negatives ended with two fingers on one fret, which every hand can
    hold. Admitting them is right, so the instrument was scoring a correct
    answer as a failure.

    Here the first-listed note is the low one, and seven frets of upward
    displacement would land it exactly on top of its neighbour.
    """

    frame = {"tuning": (40, 45, 50, 55, 59, 64), "capo": 0}
    realisation = [(0, 1, 1), (1, 8, 4)]

    collapsed = script._frame_tab([(0, 8, 1), (1, 8, 4)], frame["tuning"], 0)
    assert check_playability(collapsed, MEDIAN_HAND).verdict != "RED", (
        "two fingers on one fret is playable, so it cannot be used as a negative"
    )

    assert script._displaced_admits(frame, MEDIAN_HAND, realisation, 7) is False
