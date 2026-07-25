from fractions import Fraction as F
from types import SimpleNamespace

import pytest

import fretsure.solver.api as solver_api
from fretsure.geometry import STANDARD_TUNING
from fretsure.ir import Note
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.solver.cost import QualityCost
from fretsure.solver.technique import technique_quality_key
from fretsure.tab import Tab, TabNote


def _tab(fret: int) -> Tab:
    return Tab(
        (TabNote(F(0), F(1), 0, fret, 1, "p"),),
        STANDARD_TUNING,
        0,
    )


def test_technique_keys_reorder_only_explicit_preference_dimensions() -> None:
    general_first = QualityCost(awkward_fingering_events=0, barre_burden=4, max_fret=12)
    preference_first = QualityCost(awkward_fingering_events=3, barre_burden=0, max_fret=2)

    assert general_first < preference_first
    assert technique_quality_key(preference_first, "avoid_barres") < technique_quality_key(
        general_first, "avoid_barres"
    )
    assert technique_quality_key(preference_first, "low_position") < technique_quality_key(
        general_first, "low_position"
    )


def test_nondefault_technique_selects_only_from_certified_green_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ordinary = _tab(12)
    preferred = _tab(2)
    green_pool = (
        SimpleNamespace(
            tab=ordinary,
            quality=QualityCost(awkward_fingering_events=0, barre_burden=4),
            stable_rank=0,
        ),
        SimpleNamespace(
            tab=preferred,
            quality=QualityCost(awkward_fingering_events=3, barre_burden=0),
            stable_rank=1,
        ),
    )

    def outcome(*args: object, **kwargs: object) -> object:
        del args
        assert kwargs["_collect_full_green_pool"] is True
        return SimpleNamespace(result=ordinary, green_pool=green_pool)

    monkeypatch.setattr(solver_api, "_solve_fingering_with_green_pool", outcome)
    result = solver_api.solve_fingering(
        (Note(F(0), F(1), 60, "melody"),),
        STANDARD_TUNING,
        0,
        MEDIAN_HAND,
        technique_profile_name="avoid_barres",
    )

    assert result is preferred

