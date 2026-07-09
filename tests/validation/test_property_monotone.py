"""Flagship self-check: the oracle must be monotone in resources.

A larger hand / faster shift / higher repeat-rate ceiling can only make a tab
*more* playable, never less: RED -> AMBER -> GREEN, never the reverse. If any
predicate violated this, GREEN could falsely certify an unplayable tab — the
worst possible bug. Hypothesis stress-tests it over hundreds of random tabs.
"""

from fractions import Fraction as F

from hypothesis import given, settings
from hypothesis import strategies as st

from fretsure.oracle.core import check_playability
from fretsure.oracle.profiles import Profile
from fretsure.tab import Tab, TabNote

TUN = (40, 45, 50, 55, 59, 64)
_RANK = {"RED": 0, "AMBER": 1, "GREEN": 2}

# large dominates small in every scaled resource; other params equal.
SMALL = Profile("small_test", 80.0, 40.0, 400.0, 6.0, 648.0, 22)
LARGE = Profile("large_test", 130.0, 70.0, 700.0, 12.0, 648.0, 22)


@st.composite
def tabs(draw: st.DrawFn) -> Tab:
    n_frames = draw(st.integers(1, 4))
    notes: list[TabNote] = []
    for onset in range(n_frames):
        n_notes = draw(st.integers(1, 4))
        strings = draw(
            st.lists(st.integers(0, 5), min_size=n_notes, max_size=n_notes, unique=True)
        )
        for s in strings:
            fret = draw(st.integers(0, 16))
            finger = draw(st.integers(0, 4))
            rf = draw(st.sampled_from(["p", "i", "m", "a"]))
            notes.append(TabNote(F(onset), F(1), s, fret, finger, rf))
    return Tab(tuple(notes), TUN, 0)


@settings(max_examples=400, deadline=None)
@given(tabs())
def test_monotone_in_resources(tab: Tab) -> None:
    small = check_playability(tab, SMALL).verdict
    large = check_playability(tab, LARGE).verdict
    assert _RANK[large] >= _RANK[small], (small, large, tab)


@settings(max_examples=200, deadline=None)
@given(tabs(), st.data())
def test_monotone_under_uniform_scale_up(tab: Tab, data: st.DataObject) -> None:
    # scaling a random base profile's resources up can only improve the verdict
    hs = data.draw(st.floats(80.0, 120.0))
    vs = data.draw(st.floats(400.0, 600.0))
    rm = data.draw(st.floats(6.0, 10.0))
    base = Profile("base", hs, 50.0, vs, rm, 648.0, 22)
    bigger = Profile("bigger", hs * 1.3, 50.0, vs * 1.3, rm * 1.3, 648.0, 22)
    small = check_playability(tab, base).verdict
    large = check_playability(tab, bigger).verdict
    assert _RANK[large] >= _RANK[small], (small, large, tab)
