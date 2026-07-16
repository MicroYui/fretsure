from fractions import Fraction as F

import pytest

import fretsure.solver.frames as frames_module
from fretsure.geometry import STANDARD_TUNING
from fretsure.oracle.core import check_playability
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.solver.frames import FrameConfig, frame_configs
from fretsure.tab import Tab, TabNote

_RANK = {"p": 0, "i": 1, "m": 2, "a": 3}


def _tab(cfg: FrameConfig) -> Tab:
    notes = tuple(
        TabNote(F(0), F(1), p.string, p.fret, p.left_finger, p.right_finger) for p in cfg.placements
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


def test_over_four_notes_short_circuit_before_candidate_product(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_candidates(*_args: object, **_kwargs: object) -> list[tuple[int, int]]:
        raise AssertionError("candidate generation must not run for a >4-note frame")

    monkeypatch.setattr(frames_module, "candidates", unexpected_candidates)
    pitches = tuple(range(40, 56))
    assert frame_configs(pitches, STANDARD_TUNING, 0, MEDIAN_HAND) == []


def test_config_limit_preserves_placement_geometry_before_finger_variants() -> None:
    cfgs = frame_configs((62,), STANDARD_TUNING, 0, MEDIAN_HAND, limit=5)
    assert {(config.placements[0].string, config.placements[0].fret) for config in cfgs} == {
        (0, 22),
        (1, 17),
        (2, 12),
        (3, 7),
        (4, 3),
    }


def test_config_limit_round_robins_left_fingers_within_each_geometry() -> None:
    cfgs = frame_configs((57,), STANDARD_TUNING, 0, MEDIAN_HAND, limit=16)
    fingers_by_geometry: dict[tuple[int, int], set[int]] = {}
    for config in cfgs:
        placement = config.placements[0]
        fingers_by_geometry.setdefault((placement.string, placement.fret), set()).add(
            placement.left_finger
        )

    assert fingers_by_geometry == {
        (0, 17): {1, 2, 3, 4},
        (1, 12): {1, 2, 3, 4},
        (2, 7): {1, 2, 3, 4},
        (3, 2): {1, 2, 3, 4},
    }


def test_stress_frame_bounds_internal_work_and_constructs_only_final_configs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Count both bounded internals and the much smaller public result objects."""

    stress_profile = Profile(
        "stress@0.1",
        250.0,
        200.0,
        5_000.0,
        50.0,
        1e-6,
        max_fret=36,
    )
    tight_tuning = (0, 1, 2, 3, 4, 5)
    materialized = 0
    static_checks = 0
    internal_candidates = 0
    real_frame_config = frames_module.FrameConfig
    real_static_check = frames_module._single_frame_static_passes
    real_candidate = frames_module._candidate

    def counted_frame_config(*args: object, **kwargs: object) -> FrameConfig:
        nonlocal materialized
        materialized += 1
        return real_frame_config(*args, **kwargs)  # type: ignore[arg-type]

    def counted_static_check(*args: object, **kwargs: object) -> bool:
        nonlocal static_checks
        static_checks += 1
        return real_static_check(*args, **kwargs)  # type: ignore[arg-type]

    def counted_candidate(*args: object, **kwargs: object) -> object:
        nonlocal internal_candidates
        internal_candidates += 1
        return real_candidate(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(frames_module, "FrameConfig", counted_frame_config)
    monkeypatch.setattr(frames_module, "_single_frame_static_passes", counted_static_check)
    monkeypatch.setattr(frames_module, "_candidate", counted_candidate)

    configs = frame_configs(
        (10, 11, 12),
        tight_tuning,
        0,
        stress_profile,
        limit=7,
    )

    assert len(configs) == 7
    assert static_checks == 1_160
    assert internal_candidates == 4_640
    assert materialized == 7


def test_empty_pitches_trivial_config() -> None:
    assert frame_configs((), STANDARD_TUNING, 0, MEDIAN_HAND) == [FrameConfig(())]


def test_pitch_with_no_candidate_empty() -> None:
    assert frame_configs((10,), STANDARD_TUNING, 0, MEDIAN_HAND) == []


def test_deterministic() -> None:
    a = frame_configs((52, 67), STANDARD_TUNING, 0, MEDIAN_HAND)
    b = frame_configs((52, 67), STANDARD_TUNING, 0, MEDIAN_HAND)
    assert a == b
