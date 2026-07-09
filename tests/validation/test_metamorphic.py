"""Metamorphic self-checks: relations that must hold under transformations.

- tempo up  => shift/right-hand violations only increase
- time reversal => static (non-temporal) predicates unchanged
- shifting a stretch up the neck => span overage only decreases (mm geometry)
"""

from dataclasses import replace
from fractions import Fraction as F

from hypothesis import given, settings
from hypothesis import strategies as st

from fretsure.oracle.predicates import (
    check_barre,
    check_finger_count,
    check_finger_monotonic,
    check_fret_span,
    check_one_string_one_note,
    check_range,
    check_right_hand,
    check_shift_speed,
)
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.tab import Tab, TabNote

TUN = (40, 45, 50, 55, 59, 64)

_STATIC = [
    check_range,
    check_one_string_one_note,
    check_finger_count,
    check_finger_monotonic,
    check_fret_span,
    check_barre,
]


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
            notes.append(
                TabNote(
                    F(onset),
                    F(1),
                    s,
                    draw(st.integers(0, 16)),
                    draw(st.integers(0, 4)),
                    draw(st.sampled_from(["p", "i", "m", "a"])),
                )
            )
    return Tab(tuple(notes), TUN, 0)


@settings(max_examples=250, deadline=None)
@given(tabs())
def test_tempo_up_only_adds_shift_and_rh_violations(tab: Tab) -> None:
    slow_shift = len(check_shift_speed(tab, MEDIAN_HAND, tempo_bpm=30.0))
    fast_shift = len(check_shift_speed(tab, MEDIAN_HAND, tempo_bpm=240.0))
    assert fast_shift >= slow_shift
    slow_rh = len(check_right_hand(tab, MEDIAN_HAND, tempo_bpm=30.0))
    fast_rh = len(check_right_hand(tab, MEDIAN_HAND, tempo_bpm=240.0))
    assert fast_rh >= slow_rh


@settings(max_examples=250, deadline=None)
@given(tabs())
def test_time_reversal_leaves_static_predicates_unchanged(tab: Tab) -> None:
    onsets = sorted({n.onset for n in tab.notes})
    remap = {o: onsets[len(onsets) - 1 - i] for i, o in enumerate(onsets)}
    rev = Tab(
        tuple(replace(n, onset=remap[n.onset]) for n in tab.notes), tab.tuning, tab.capo
    )
    for pred in _STATIC:
        assert len(pred(tab, MEDIAN_HAND)) == len(pred(rev, MEDIAN_HAND))


def test_span_overage_eases_up_the_neck() -> None:
    # identical 3-fret spread, low vs high position; frets compress up the neck
    low = Tab((TabNote(F(0), F(1), 0, 1, 1, "p"), TabNote(F(0), F(1), 1, 4, 4, "i")), TUN, 0)
    high = Tab((TabNote(F(0), F(1), 0, 11, 1, "p"), TabNote(F(0), F(1), 1, 14, 4, "i")), TUN, 0)
    lo = check_fret_span(low, MEDIAN_HAND)
    hi = check_fret_span(high, MEDIAN_HAND)
    lo_over = lo[0].overage if lo else 0.0
    hi_over = hi[0].overage if hi else 0.0
    assert hi_over <= lo_over
