from fractions import Fraction as F

from fretsure.geometry import STANDARD_TUNING
from fretsure.oracle.core import check_playability
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.frames import FrameConfig, frame_configs
from fretsure.tab import Tab, TabNote

_RANK = {"p": 0, "i": 1, "m": 2, "a": 3}


def _tab(cfg: FrameConfig) -> Tab:
    notes = tuple(
        TabNote(F(0), F(1), p.string, p.fret, p.left_finger, p.right_finger)
        for p in cfg.placements
    )
    return Tab(notes, STANDARD_TUNING, 0)


def test_single_pitch_has_configs() -> None:
    cfgs = frame_configs((64,), STANDARD_TUNING, 0, MEDIAN_HAND)
    assert cfgs
    for c in cfgs:
        assert len(c.placements) == 1
        assert c.placements[0].pitch == 64


def test_two_pitch_distinct_strings_and_right_order() -> None:
    cfgs = frame_configs((52, 67), STANDARD_TUNING, 0, MEDIAN_HAND)
    assert cfgs
    for c in cfgs:
        strings = [p.string for p in c.placements]
        assert len(set(strings)) == len(strings)  # one string one note
        by_string = sorted(c.placements, key=lambda p: p.string)
        ranks = [_RANK[p.right_finger] for p in by_string]
        assert ranks == sorted(ranks)  # p-i-m-a follow ascending strings


def test_each_config_passes_oracle_non_red() -> None:
    # (49,53,59) can enumerate a behind-the-barre placement; frame_configs must
    # verify and drop any config that is RED in isolation (I1).
    for pitches in [(64,), (52, 67), (40, 55, 64), (49, 53, 59)]:
        cfgs = frame_configs(pitches, STANDARD_TUNING, 0, MEDIAN_HAND)
        for c in cfgs:
            r = check_playability(_tab(c), MEDIAN_HAND)
            assert r.verdict != "RED", (pitches, c, r.diagnostics)


def test_single_note_offers_multiple_right_fingers() -> None:
    # a lone note must not be forced to the thumb; the solver needs alternatives
    cfgs = frame_configs((64,), STANDARD_TUNING, 0, MEDIAN_HAND)
    rights = {c.placements[0].right_finger for c in cfgs}
    assert len(rights) >= 2


def test_over_four_notes_empty() -> None:
    # five distinct pitches -> every config has 5 plucks > 4 -> none feasible
    assert frame_configs((40, 45, 50, 55, 59), STANDARD_TUNING, 0, MEDIAN_HAND) == []


def test_empty_pitches_trivial_config() -> None:
    assert frame_configs((), STANDARD_TUNING, 0, MEDIAN_HAND) == [FrameConfig(())]


def test_pitch_with_no_candidate_empty() -> None:
    assert frame_configs((10,), STANDARD_TUNING, 0, MEDIAN_HAND) == []


def test_deterministic() -> None:
    a = frame_configs((52, 67), STANDARD_TUNING, 0, MEDIAN_HAND)
    b = frame_configs((52, 67), STANDARD_TUNING, 0, MEDIAN_HAND)
    assert a == b
