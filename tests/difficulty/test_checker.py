from dataclasses import replace
from fractions import Fraction as F

import pytest

import fretsure.difficulty.checker as checker_module
from fretsure.difficulty.checker import TierResult, check_tier
from fretsure.difficulty.tiers import ADVANCED, BEGINNER
from fretsure.geometry import STANDARD_TUNING
from fretsure.tab import Tab, TabNote

TUN = STANDARD_TUNING


def _t(notes: list[TabNote]) -> Tab:
    return Tab(tuple(notes), TUN, 0)


def test_simple_first_position_meets_beginner() -> None:
    t = _t([TabNote(F(0), F(1), 3, 2, 1, "p"), TabNote(F(1), F(1), 4, 1, 1, "i")])
    r = check_tier(t, BEGINNER)
    assert isinstance(r, TierResult)
    assert r.meets and r.playable == "GREEN" and r.tier_violations == ()


def test_high_position_does_not_meet_beginner() -> None:
    t = _t([TabNote(F(0), F(1), 0, 8, 1, "p")])  # fret 8 > beginner max_position/max_fret 5
    r = check_tier(t, BEGINNER)
    assert not r.meets
    assert check_tier(t, ADVANCED).meets  # advanced allows it


def test_playable_but_barre_fails_beginner() -> None:
    # a 2-string barre at fret 2: geometrically fine, but beginners can't barre
    t = _t([TabNote(F(0), F(1), 0, 2, 1, "p"), TabNote(F(0), F(1), 1, 2, 1, "i")])
    r = check_tier(t, BEGINNER)
    assert not r.meets
    assert any("barre" in v for v in r.tier_violations)


def test_unreachable_stretch_not_playable_any_tier() -> None:
    t = _t([TabNote(F(0), F(1), 0, 1, 1, "p"), TabNote(F(0), F(1), 1, 15, 4, "i")])
    assert not check_tier(t, ADVANCED).meets  # RED under geometry


def test_check_tier_uses_same_detached_tier_for_oracle_and_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_tier = replace(BEGINNER)
    tab = _t(
        [
            TabNote(F(0), F(1), 0, 0, 0, "p"),
            TabNote(F(0), F(1), 1, 0, 0, "i"),
            TabNote(F(0), F(1), 2, 0, 0, "m"),
        ]
    )
    real_check = checker_module.check_playability

    def relax_source_after_barrier(*args: object, **kwargs: object) -> object:
        object.__setattr__(source_tier, "max_simultaneous", 6)
        object.__setattr__(source_tier, "allow_barre", True)
        object.__setattr__(source_tier, "max_position", 36)
        object.__setattr__(source_tier, "max_shifts_per_bar", 10_000)
        return real_check(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(checker_module, "check_playability", relax_source_after_barrier)
    result = checker_module.check_tier(tab, source_tier)

    assert not result.meets
    assert any("simultaneous" in item for item in result.tier_violations)
