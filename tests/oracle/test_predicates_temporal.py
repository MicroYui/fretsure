from dataclasses import replace
from fractions import Fraction as F

from fretsure.geometry import press_x
from fretsure.oracle.core import check_playability
from fretsure.oracle.predicates import (
    check_barre,
    check_finger_count,
    check_finger_monotonic,
    check_fret_span,
    check_shift_speed,
    check_string_sustain,
    check_sustain,
)
from fretsure.oracle.profiles import MEDIAN_HAND, optimistic, pessimistic
from fretsure.tab import Tab, TabNote

TUN = (40, 45, 50, 55, 59, 64)


def _t(notes: list[TabNote]) -> Tab:
    return Tab(tuple(notes), TUN, 0)


def test_shift_speed_fast_jump_flagged() -> None:
    # fret 1 -> fret 12, a sixteenth apart, fast tempo: the hand cannot travel that fast
    t = _t(
        [
            TabNote(F(0), F(1, 8), 0, 1, 1, "p"),
            TabNote(F(1, 4), F(1), 0, 12, 1, "p"),
        ]
    )
    assert check_string_sustain(t, MEDIAN_HAND) == []
    d = check_shift_speed(t, MEDIAN_HAND, tempo_bpm=120.0)
    assert d and d[0].violation_type == "SHIFT_SPEED"
    assert d[0].overage > 0


def test_shift_speed_slow_ok() -> None:
    # same jump but four beats apart at the same tempo: plenty of time
    t = _t([TabNote(F(0), F(1), 0, 1, 1, "p"), TabNote(F(4), F(1), 0, 12, 1, "p")])
    assert check_shift_speed(t, MEDIAN_HAND, tempo_bpm=120.0) == []


def test_shift_bridged_by_open_frame_still_charged() -> None:
    # fret 2 -> (open-only frame) -> fret 20, all within a tiny interval: the open
    # frame must not reset the hand position and hide the impossible shift.
    t = _t(
        [
            TabNote(F(0), F(1, 64), 0, 2, 1, "p"),
            TabNote(F(1, 64), F(1, 64), 3, 0, 0, "i"),  # open-only bridging frame
            TabNote(F(2, 64), F(1), 0, 20, 1, "p"),
        ]
    )
    assert check_string_sustain(t, MEDIAN_HAND) == []
    d = check_shift_speed(t, MEDIAN_HAND, tempo_bpm=200.0)
    assert any(x.violation_type == "SHIFT_SPEED" for x in d)


def test_shift_speed_monotonic_in_tempo() -> None:
    # faster tempo can only add shift violations, never remove them
    t = _t(
        [
            TabNote(F(0), F(1, 4), 0, 1, 1, "p"),
            TabNote(F(1, 2), F(1), 0, 12, 1, "p"),
        ]
    )
    assert check_string_sustain(t, MEDIAN_HAND) == []
    slow = check_shift_speed(t, MEDIAN_HAND, tempo_bpm=40.0)
    fast = check_shift_speed(t, MEDIAN_HAND, tempo_bpm=200.0)
    assert len(fast) >= len(slow)


def test_shift_speed_guide_finger_relaxes() -> None:
    # Note 0 genuinely sounds across the later, statically reachable attack.
    # Its active constraint naturally anchors the propagated hand state.
    t = _t(
        [
            TabNote(F(0), F(1), 0, 1, 1, "p"),
            TabNote(F(1, 64), F(1, 64), 1, 3, 3, "i"),
        ]
    )
    assert check_string_sustain(t, MEDIAN_HAND) == []
    assert check_shift_speed(t, MEDIAN_HAND, tempo_bpm=120.0) == []


def test_sustained_guide_does_not_reset_reachable_path() -> None:
    # The sustained fret-3 note belongs to both shapes, but holding fret 6 has
    # already confined the reachable centre near the high end of its interval.
    # Replacing fret 6 with fret 1 at the same instant cannot teleport the hand
    # to the disjoint low-end interval merely because fret 3 is a guide.
    t = _t(
        [
            TabNote(F(0), F(2), 0, 3, 2, "p"),
            TabNote(F(0), F(1), 1, 6, 4, "i"),
            TabNote(F(1), F(1), 2, 1, 1, "m"),
        ]
    )
    diagnostics = check_shift_speed(t, MEDIAN_HAND, tempo_bpm=120.0)
    assert diagnostics and diagnostics[0].violation_type == "SHIFT_SPEED"
    result = check_playability(t, MEDIAN_HAND, tempo_bpm=120.0)
    assert result.verdict == "RED"
    assert any(d.violation_type == "SHIFT_SPEED" for d in result.diagnostics)


def test_repeated_value_after_release_is_not_a_guide_finger() -> None:
    # Both endpoint shapes are feasible around fret 5, but their feasible hand
    # intervals are disjoint.  Re-attacking the same value after release must
    # not reset the narrow reachable interval as a sounding guide would.
    t = _t(
        [
            TabNote(F(0), F(1), 0, 3, 1, "p"),
            TabNote(F(0), F(1), 1, 5, 2, "i"),
            TabNote(F(1), F(1, 64), 0, 0, 0, "p"),
            TabNote(F(65, 64), F(1), 1, 5, 2, "m"),
            TabNote(F(65, 64), F(1), 2, 8, 4, "a"),
        ]
    )
    assert check_string_sustain(t, MEDIAN_HAND) == []
    diagnostics = check_shift_speed(t, MEDIAN_HAND, tempo_bpm=120.0)
    assert diagnostics and diagnostics[0].violation_type == "SHIFT_SPEED"


def test_terminal_sustained_shape_controls_later_shift() -> None:
    """A note still sounding pins the hand, so a later distant attack is refused.

    The third note sits at fret 8 rather than fret 6 for margin. At fret 6 the
    optimistic hand cleared the gap by 3 mm once `reach_mm` became half the span
    on 2026-07-30, which turned this into an AMBER that was really testing where
    a constant happened to land rather than whether a sustained shape propagates.
    """

    t = _t(
        [
            TabNote(F(0), F(2), 0, 1, 1, "p"),
            TabNote(F(0), F(1), 1, 3, 3, "i"),
            TabNote(F(21, 10), F(1), 2, 8, 4, "m"),
        ]
    )
    diagnostics = check_shift_speed(t, MEDIAN_HAND, tempo_bpm=120.0)
    assert diagnostics and diagnostics[0].violation_type == "SHIFT_SPEED"
    result = check_playability(t, MEDIAN_HAND, tempo_bpm=120.0)
    assert result.verdict == "RED"
    assert any(d.violation_type == "SHIFT_SPEED" for d in result.diagnostics)


def test_terminal_center_ignores_earlier_released_shape_members() -> None:
    t = _t(
        [
            TabNote(F(0), F(2), 0, 1, 1, "p"),
            TabNote(F(0), F(1), 1, 2, 2, "i"),
            TabNote(F(21, 10), F(1), 2, 1, 2, "m"),
        ]
    )
    assert check_shift_speed(t, MEDIAN_HAND, tempo_bpm=120.0) == []


def test_current_full_shape_is_shift_destination_before_terminal_subset() -> None:
    profile = replace(MEDIAN_HAND, hand_span_mm=200.0, reach_mm=20.0)
    t = _t(
        [
            TabNote(F(0), F(1), 0, 5, 1, "p"),
            TabNote(F(1), F(2), 0, 5, 1, "p"),
            TabNote(F(1), F(1), 1, 8, 4, "i"),
            TabNote(F(1), F(1), 2, 8, 4, "m"),
            TabNote(F(1), F(1), 3, 8, 4, "a"),
        ]
    )
    diagnostics = check_shift_speed(t, profile, tempo_bpm=120.0)
    assert diagnostics and diagnostics[0].violation_type == "SHIFT_SPEED"
    result = check_playability(t, profile, tempo_bpm=120.0)
    assert result.verdict == "RED"
    assert any(d.violation_type == "SHIFT_SPEED" for d in result.diagnostics)


def test_unrelated_open_attack_does_not_change_shift_result() -> None:
    base_notes = [
        TabNote(F(0), F(2), 0, 1, 1, "p"),
        TabNote(F(0), F(1), 1, 3, 3, "i"),
        TabNote(F(21, 10), F(1), 2, 6, 4, "m"),
    ]
    with_open = [
        *base_notes[:2],
        TabNote(F(1), F(1, 4), 5, 0, 0, "a"),
        base_notes[2],
    ]
    plain = check_shift_speed(_t(base_notes), MEDIAN_HAND, tempo_bpm=120.0)
    observed = check_shift_speed(_t(with_open), MEDIAN_HAND, tempo_bpm=120.0)
    assert bool(plain) is bool(observed) is True
    assert plain[0].overage == observed[0].overage


def test_local_exact_release_transition_uses_reach_not_infinite_speed() -> None:
    t = _t(
        [
            TabNote(F(0), F(1), 0, 1, 1, "p"),
            TabNote(F(1), F(1), 0, 2, 2, "i"),
        ]
    )
    assert check_string_sustain(t, MEDIAN_HAND) == []
    assert check_shift_speed(t, pessimistic(MEDIAN_HAND)) == []


def test_distant_exact_release_transition_fails_even_optimistic() -> None:
    t = _t(
        [
            TabNote(F(0), F(1), 0, 1, 1, "p"),
            TabNote(F(1), F(1), 1, 12, 4, "i"),
        ]
    )
    diagnostics = check_shift_speed(t, optimistic(MEDIAN_HAND))
    assert diagnostics and diagnostics[0].violation_type == "SHIFT_SPEED"


def test_pairwise_reach_overlap_does_not_reset_reachable_path() -> None:
    # Every adjacent pair overlaps under pessimistic 2*reach=90 mm, but those
    # overlap slices are far apart.  A pairwise-only checker falsely made the
    # hand traverse 233 mm in 3/64 beat without charging any shift.
    frets = (1, 3, 6, 9)
    right_fingers = ("p", "i", "m", "a")
    t = _t(
        [
            TabNote(F(index, 64), F(1, 64), 0, fret, index + 1, right_fingers[index])
            for index, fret in enumerate(frets)
        ]
    )
    diagnostics = check_shift_speed(t, pessimistic(MEDIAN_HAND), tempo_bpm=120.0)
    assert diagnostics and diagnostics[0].violation_type == "SHIFT_SPEED"
    result = check_playability(t, MEDIAN_HAND, tempo_bpm=120.0)
    assert result.verdict == "RED"
    assert any(d.violation_type == "SHIFT_SPEED" for d in result.diagnostics)


def test_reachable_path_can_expand_while_a_local_shape_is_held() -> None:
    frets = (1, 3, 6, 9)
    right_fingers = ("p", "i", "m", "a")
    t = _t(
        [
            TabNote(F(index), F(1), 0, fret, index + 1, right_fingers[index])
            for index, fret in enumerate(frets)
        ]
    )
    assert check_shift_speed(t, pessimistic(MEDIAN_HAND), tempo_bpm=120.0) == []
    assert check_playability(t, MEDIAN_HAND, tempo_bpm=120.0).verdict == "GREEN"


def test_reach_interval_exact_touch_is_reachable() -> None:
    low = press_x(1, MEDIAN_HAND.string_length_mm)
    high = press_x(12, MEDIAN_HAND.string_length_mm)
    assert low is not None and high is not None
    exact_reach = (high - low) / 2.0
    t = _t(
        [
            TabNote(F(0), F(1), 0, 1, 1, "p"),
            TabNote(F(1), F(1), 0, 12, 4, "i"),
        ]
    )
    touching = replace(MEDIAN_HAND, reach_mm=exact_reach)
    separated = replace(MEDIAN_HAND, reach_mm=exact_reach - 1e-6)
    assert check_shift_speed(t, touching, tempo_bpm=120.0) == []
    diagnostics = check_shift_speed(t, separated, tempo_bpm=120.0)
    assert diagnostics and diagnostics[0].overage > 0.0


def test_sustain_until_new_attack_leaves_no_time_for_distant_shift() -> None:
    t = _t(
        [
            TabNote(F(0), F(4), 0, 1, 1, "p"),
            TabNote(F(4), F(1), 1, 15, 4, "i"),
        ]
    )
    diagnostics = check_shift_speed(t, MEDIAN_HAND, tempo_bpm=120.0)
    assert diagnostics and diagnostics[0].violation_type == "SHIFT_SPEED"
    assert diagnostics[0].overage > 0


def test_early_release_leaves_time_for_distant_shift() -> None:
    t = _t(
        [
            TabNote(F(0), F(1), 0, 1, 1, "p"),
            TabNote(F(4), F(1), 1, 15, 4, "i"),
        ]
    )
    assert check_shift_speed(t, MEDIAN_HAND, tempo_bpm=120.0) == []


def test_zero_time_shift_overage_is_finite() -> None:
    t = _t(
        [
            TabNote(F(0), F(1), 0, 1, 1, "p"),
            TabNote(F(1), F(1), 1, 15, 4, "i"),
        ]
    )
    diagnostics = check_shift_speed(t, MEDIAN_HAND)
    assert diagnostics
    assert diagnostics[0].overage < float("inf")


def test_sustained_note_participates_in_later_fret_span() -> None:
    t = _t(
        [
            TabNote(F(0), F(8), 0, 1, 1, "p"),
            TabNote(F(4), F(1), 1, 15, 4, "i"),
        ]
    )
    diagnostics = check_fret_span(t, MEDIAN_HAND)
    assert diagnostics and diagnostics[0].violation_type == "FRET_SPAN"
    assert diagnostics[0].offending_notes == (0, 1)
    result = check_playability(t, MEDIAN_HAND)
    assert result.verdict == "RED"
    assert any(d.violation_type == "FRET_SPAN" for d in result.diagnostics)


def test_sustained_barre_cannot_cover_a_lower_fret() -> None:
    t = _t(
        [
            TabNote(F(0), F(2), 0, 8, 2, "p"),
            TabNote(F(1), F(1), 1, 7, 1, "i"),
            TabNote(F(1), F(1), 2, 8, 2, "m"),
        ]
    )
    diagnostics = check_barre(t, MEDIAN_HAND)
    assert diagnostics and diagnostics[0].violation_type == "BARRE_INFEASIBLE"
    assert diagnostics[0].offending_notes == (1,)
    result = check_playability(t, MEDIAN_HAND)
    assert result.verdict == "RED"
    assert any(d.violation_type == "BARRE_INFEASIBLE" for d in result.diagnostics)


def test_sustained_notes_participate_in_finger_count() -> None:
    t = _t(
        [
            TabNote(F(0), F(2), 0, 1, 1, "p"),
            TabNote(F(0), F(2), 1, 2, 2, "i"),
            TabNote(F(0), F(2), 2, 3, 3, "m"),
            TabNote(F(0), F(2), 3, 4, 4, "a"),
            TabNote(F(1), F(1), 4, 5, 4, "p"),
        ]
    )
    diagnostics = check_finger_count(t, MEDIAN_HAND)
    assert diagnostics and diagnostics[0].violation_type == "FINGER_COUNT"
    assert set(diagnostics[0].offending_notes) == {0, 1, 2, 3, 4}


def test_sustained_note_participates_in_finger_monotonicity() -> None:
    t = _t(
        [
            TabNote(F(0), F(2), 0, 2, 4, "p"),
            TabNote(F(1), F(1), 1, 5, 1, "i"),
        ]
    )
    diagnostics = check_finger_monotonic(t, MEDIAN_HAND)
    assert diagnostics and diagnostics[0].violation_type == "FINGER_MONOTONIC"
    assert diagnostics[0].offending_notes == (0, 1)


def test_exact_end_touch_does_not_enter_later_hand_geometry() -> None:
    t = _t(
        [
            TabNote(F(0), F(4), 0, 1, 1, "p"),
            TabNote(F(4), F(1), 1, 15, 4, "i"),
        ]
    )
    assert check_fret_span(t, MEDIAN_HAND) == []


def test_sustain_same_finger_diff_fret_overlap_flagged() -> None:
    # finger 1 held at fret 3 (beats 0-2) while also needed at fret 5 (beats 1-2)
    t = _t([TabNote(F(0), F(2), 0, 3, 1, "p"), TabNote(F(1), F(1), 1, 5, 1, "i")])
    d = check_sustain(t, MEDIAN_HAND)
    assert d and d[0].violation_type == "SUSTAIN_CONFLICT"
    assert set(d[0].offending_notes) == {0, 1}


def test_sustain_barre_same_fret_ok() -> None:
    # same finger, same fret, different strings = a held barre, not a conflict
    t = _t([TabNote(F(0), F(2), 0, 2, 1, "p"), TabNote(F(1), F(1), 1, 2, 1, "i")])
    assert check_sustain(t, MEDIAN_HAND) == []


def test_sustain_no_overlap_ok() -> None:
    t = _t([TabNote(F(0), F(1), 0, 3, 1, "p"), TabNote(F(2), F(1), 1, 5, 1, "i")])
    assert check_sustain(t, MEDIAN_HAND) == []


def test_same_string_sounding_intervals_overlap_is_flagged() -> None:
    t = _t(
        [
            TabNote(F(0), F(2), 2, 3, 1, "p"),
            TabNote(F(1), F(1), 2, 5, 2, "i"),
        ]
    )
    diagnostics = check_string_sustain(t, MEDIAN_HAND)
    assert len(diagnostics) == 1
    assert diagnostics[0].violation_type == "STRING_SUSTAIN_CONFLICT"
    assert diagnostics[0].offending_notes == (0, 1)
    assert diagnostics[0].measure == 1
    assert diagnostics[0].beat == F(2)


def test_same_string_open_to_fretted_overlap_is_flagged() -> None:
    t = _t(
        [
            TabNote(F(0), F(2), 2, 0, 0, "p"),
            TabNote(F(1), F(1), 2, 5, 2, "i"),
        ]
    )
    assert check_string_sustain(t, MEDIAN_HAND)[0].violation_type == (
        "STRING_SUSTAIN_CONFLICT"
    )


def test_same_string_exact_end_touch_is_allowed() -> None:
    t = _t(
        [
            TabNote(F(0), F(1), 2, 3, 1, "p"),
            TabNote(F(1), F(1), 2, 5, 2, "i"),
        ]
    )
    assert check_string_sustain(t, MEDIAN_HAND) == []


def test_same_string_overlap_changes_public_checker_verdict() -> None:
    t = _t(
        [
            TabNote(F(0), F(2), 2, 3, 1, "p"),
            TabNote(F(1), F(1), 2, 5, 2, "i"),
        ]
    )
    result = check_playability(t, MEDIAN_HAND)
    assert result.verdict == "RED"
    assert any(d.violation_type == "STRING_SUSTAIN_CONFLICT" for d in result.diagnostics)


def test_sustain_predicates_emit_at_most_one_conflict_per_new_note() -> None:
    # A hostile but valid shape with many long overlaps must not explode into
    # O(n^2) diagnostics. It remains fully rejected and every later note is localized.
    # Large enough that a reintroduced all-pairs implementation is costly,
    # while the intended sort + bounded-active-state implementation remains a
    # cheap deterministic test.
    notes = [
        TabNote(F(i, 16), F(100), i % 6, 1 + (i % 3), 1 + (i % 2), "p")
        for i in range(2_000)
    ]
    tab = _t(notes)
    assert len(check_string_sustain(tab, MEDIAN_HAND)) <= len(notes) - 1
    assert len(check_sustain(tab, MEDIAN_HAND)) <= len(notes) - 1
    assert len(check_finger_count(tab, MEDIAN_HAND)) <= len(notes)
    assert len(check_finger_monotonic(tab, MEDIAN_HAND)) <= len(notes)
    assert len(check_fret_span(tab, MEDIAN_HAND)) <= len(notes)
    assert len(check_barre(tab, MEDIAN_HAND)) <= len(notes)
    assert len(check_shift_speed(tab, MEDIAN_HAND)) <= len(notes)


def test_the_hand_window_never_refuses_a_frame_the_exact_rules_admit() -> None:
    """The two rules state one limit, so the finger-blind one must be the loose one.

    `check_fret_span` bounds each pair of *different* fingers by `d_max`, and
    `check_barre` owns the same-finger case, where `d_max` is zero because one
    finger holding two frets is a different claim. Between them they say exactly
    how far apart a frame's fretted notes may be. `check_shift_speed` gives every
    fretted note a hand-centre interval of half-width `reach_mm` and intersects
    them, which is that same claim about the extreme pair with the fingers rubbed
    out. Whichever constant is smaller is the one in force.

    So the window has to be a relaxation: `2 * reach_mm` equals the widest
    allowance any pair gets, and then a single frame it refuses is always a frame
    an exact rule refuses too. Between 2026-07-29 and 07-30 it was 30 mm tighter,
    `d_max(1, 4)` became unreachable, and the verifier refused frets 3 to 7 held
    by fingers 1 and 4 -- an ordinary shape, and the very stretch the span had
    just been raised to admit.

    Enumerated rather than sampled: one frame is small enough to cover outright,
    and a property this cheap to check should not be left to chance.
    """

    for profile in (pessimistic(MEDIAN_HAND), MEDIAN_HAND, optimistic(MEDIAN_HAND)):
        for low_fret in range(1, 20):
            for high_fret in range(low_fret, 23):
                for low_finger in range(1, 5):
                    for high_finger in range(1, 5):
                        tab = Tab(
                            (
                                TabNote(F(0), F(1), 0, low_fret, low_finger, "p"),
                                TabNote(F(0), F(1), 1, high_fret, high_finger, "i"),
                            ),
                            TUN,
                            0,
                        )
                        if not check_shift_speed(tab, profile):
                            continue
                        assert check_fret_span(tab, profile) or check_barre(tab, profile), (
                            profile.version,
                            (low_fret, low_finger),
                            (high_fret, high_finger),
                        )
