from fractions import Fraction as F
from inspect import Parameter, signature

import pytest

import fretsure.solver.api as solver_api
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import Note
from fretsure.oracle.core import OracleResult
from fretsure.oracle.input import MAX_SOLVER_FINAL_CHECKS
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.solver.api import Infeasible, solve_fingering
from fretsure.tab import Tab


def _notes() -> tuple[Note, ...]:
    return (
        Note(F(0), F(1), 60, "melody"),
        Note(F(1), F(1), 60, "melody"),
    )


def _verdict(profile: Profile, verdict: str) -> OracleResult:
    return OracleResult(
        verdict,  # type: ignore[arg-type]
        (),
        "test-oracle",
        profile.version,
        profile.fingerprint,
        "test-input",
    )


def test_green_pool_is_canonical_fully_checked_and_hard_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked: list[Tab] = []

    def always_green(
        tab: Tab,
        profile: Profile,
        *,
        tempo_bpm: float = 90.0,
        beats_per_bar: int = 4,
    ) -> OracleResult:
        del tempo_bpm, beats_per_bar
        checked.append(tab)
        return _verdict(profile, "GREEN")

    monkeypatch.setattr(solver_api, "check_playability", always_green)
    outcome = solver_api._solve_fingering_with_green_pool(
        _notes(),
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
        beam=64,
    )

    assert len(checked) == len(outcome.green_pool) == MAX_SOLVER_FINAL_CHECKS
    assert tuple(candidate.tab for candidate in outcome.green_pool) == tuple(checked)
    keys = tuple(
        (candidate.quality, candidate.stable_rank)
        for candidate in outcome.green_pool
    )
    assert keys == tuple(sorted(keys))
    assert len({candidate.stable_rank for candidate in outcome.green_pool}) == len(
        outcome.green_pool
    )


def test_public_winner_is_first_green_pool_item_and_signature_is_unchanged() -> None:
    outcome = solver_api._solve_fingering_with_green_pool(
        _notes(),
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
        beam=8,
    )
    public = solve_fingering(_notes(), STANDARD_TUNING, 0, MEDIAN_HAND, beam=8)

    assert outcome.green_pool
    assert public == outcome.result == outcome.green_pool[0].tab

    parameters = signature(solve_fingering).parameters
    assert tuple(parameters) == (
        "notes",
        "tuning",
        "capo",
        "profile",
        "tempo_bpm",
        "beats_per_bar",
        "beam",
    )
    assert all(
        parameters[name].kind is Parameter.POSITIONAL_OR_KEYWORD
        for name in ("notes", "tuning", "capo", "profile")
    )
    assert all(
        parameters[name].kind is Parameter.KEYWORD_ONLY
        for name in ("tempo_bpm", "beats_per_bar", "beam")
    )
    assert "_solve_fingering_with_green_pool" not in solver_api.__all__
    assert "_GreenFinalist" not in solver_api.__all__


@pytest.mark.parametrize("verdict", ["AMBER", "RED"])
def test_non_green_results_never_enter_pool(
    monkeypatch: pytest.MonkeyPatch,
    verdict: str,
) -> None:
    checked: list[Tab] = []

    def fixed_verdict(
        tab: Tab,
        profile: Profile,
        *,
        tempo_bpm: float = 90.0,
        beats_per_bar: int = 4,
    ) -> OracleResult:
        del tempo_bpm, beats_per_bar
        checked.append(tab)
        return _verdict(profile, verdict)

    monkeypatch.setattr(solver_api, "check_playability", fixed_verdict)
    outcome = solver_api._solve_fingering_with_green_pool(
        _notes(),
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
        beam=8,
    )

    assert checked
    assert outcome.green_pool == ()
    if verdict == "AMBER":
        assert outcome.result == checked[0]
    else:
        assert isinstance(outcome.result, Infeasible)
