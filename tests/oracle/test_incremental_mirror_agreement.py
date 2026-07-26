"""The solver's incremental oracle mirror must never disagree with the oracle.

Three copies of the physical rules exist: the public predicates, the solver's
incremental admission state, and the CSP's ``assignment_valid``.  Nothing tested
that they agree, so a change to one could silently drift from the others -- the
worst possible failure mode for a project whose entire claim is that a
deterministic verifier is the boundary.

The properties here are one-sided in the direction the design promises:
admission by the mirror must not contradict the real predicates.  The mirror is
allowed to be conservative (reject something the oracle would accept); it is not
allowed to be permissive.
"""

from __future__ import annotations

from fractions import Fraction

from hypothesis import given, settings
from hypothesis import strategies as st

import fretsure.solver.api as solver_api
from fretsure.oracle.csp import assignment_valid
from fretsure.oracle.predicates import (
    check_barre,
    check_finger_count,
    check_finger_monotonic,
    check_fret_span,
    check_one_string_one_note,
    check_shift_speed,
    check_string_sustain,
)
from fretsure.oracle.profiles import MEDIAN_HAND, optimistic
from fretsure.tab import Tab, TabNote

_TUNING = (40, 45, 50, 55, 59, 64)
_FRAME_LOCAL = (
    check_one_string_one_note,
    check_finger_count,
    check_finger_monotonic,
    check_barre,
    check_fret_span,
    check_string_sustain,
)


@st.composite
def _tab_notes(draw: st.DrawFn) -> tuple[TabNote, ...]:
    """Small multi-frame tabs with sustains that cross frame boundaries."""

    frame_count = draw(st.integers(min_value=1, max_value=4))
    step = draw(st.sampled_from((Fraction(1, 8), Fraction(1, 4), Fraction(1, 2), Fraction(1))))
    notes: list[TabNote] = []
    for frame in range(frame_count):
        onset = step * frame
        strings = draw(
            st.lists(st.integers(min_value=0, max_value=5), min_size=1, max_size=4, unique=True)
        )
        for string in sorted(strings):
            fret = draw(st.integers(min_value=0, max_value=17))
            notes.append(
                TabNote(
                    onset=onset,
                    duration=step * draw(st.integers(min_value=1, max_value=4)),
                    string=string,
                    fret=fret,
                    left_finger=0 if fret == 0 else draw(st.integers(min_value=1, max_value=4)),
                    right_finger=("p", "i", "m", "a")[min(strings.index(string), 3)],
                )
            )
    return tuple(notes)


def _advance_all(notes: tuple[TabNote, ...]) -> solver_api._IncrementalOracleState | None:
    """Feed the notes to the mirror one attack frame at a time."""

    profile = optimistic(MEDIAN_HAND)
    state: solver_api._IncrementalOracleState | None = solver_api._IncrementalOracleState(
        (), (None, None, None, None), None
    )
    next_id = 0
    for onset in sorted({note.onset for note in notes}):
        frame = tuple(note for note in notes if note.onset == onset)
        assert state is not None
        state = solver_api._advance_oracle_state(
            state,
            onset=onset,
            added=frame,
            first_note_id=next_id,
            tuning=_TUNING,
            capo=0,
            profile=profile,
            tempo_bpm=90.0,
        )
        if state is None:
            return None
        next_id += len(frame)
    return state


@settings(max_examples=300, deadline=None)
@given(_tab_notes())
def test_mirror_admission_implies_frame_local_oracle_pass(notes: tuple[TabNote, ...]) -> None:
    """Admission by the incremental mirror must not contradict the predicates."""

    if _advance_all(notes) is None:
        return
    tab = Tab(tuple(sorted(notes, key=lambda note: (note.onset, note.string))), _TUNING, 0)
    profile = optimistic(MEDIAN_HAND)
    for predicate in _FRAME_LOCAL:
        assert not predicate(tab, profile), (
            f"{predicate.__name__} rejects a tab the solver's mirror admitted"
        )


@settings(max_examples=300, deadline=None)
@given(_tab_notes())
def test_mirror_admission_implies_shift_speed_pass(notes: tuple[TabNote, ...]) -> None:
    """The same one-sided property for the temporal predicate.

    The two implementations differ structurally -- the mirror compares hand
    centres only for disjoint fretted sets, the predicate propagates a reachable
    interval -- so this property holding over this distribution is evidence, not
    proof.  It becomes true by construction once the mirror replays the
    predicate's own event loop.
    """

    if _advance_all(notes) is None:
        return
    tab = Tab(tuple(sorted(notes, key=lambda note: (note.onset, note.string))), _TUNING, 0)
    assert not check_shift_speed(tab, optimistic(MEDIAN_HAND), tempo_bpm=90.0)


@settings(max_examples=300, deadline=None)
@given(_tab_notes())
def test_assignment_valid_matches_frame_local_predicates(notes: tuple[TabNote, ...]) -> None:
    """The CSP's copy of the geometry rules must agree with the predicates.

    ``assignment_valid`` covers monotonicity, barre and span; those three are
    compared, and any divergence means the solver is searching a different space
    from the one the oracle certifies.
    """

    profile = MEDIAN_HAND
    for onset in sorted({note.onset for note in notes}):
        frame = tuple(note for note in notes if note.onset == onset)
        fretted = [note for note in frame if note.fret > 0]
        if not fretted or len({note.string for note in frame}) != len(frame):
            continue
        assignment = tuple(note.left_finger for note in fretted)
        csp_ok = assignment_valid(fretted, assignment, profile, capo=0)
        single = Tab(tuple(fretted), _TUNING, 0)
        predicate_ok = not (
            check_finger_monotonic(single, profile)
            or check_barre(single, profile)
            or check_fret_span(single, profile)
        )
        assert csp_ok == predicate_ok, (
            f"assignment_valid={csp_ok} but the predicates say {predicate_ok} for {fretted}"
        )
