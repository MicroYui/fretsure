"""Deterministic long-score composition over the bounded fingering solver.

``solve_fingering`` deliberately rejects one search whose conservative work estimate
exceeds its fixed ceiling.  A score may still be handled without weakening that gate:
split only between complete onset frames, solve every part under the unchanged public
limit, then run the oracle once over the reassembled full-history Tab.  Reassembly is
one-sided: it may return ``Infeasible``, but it can never release a RED Tab.
"""

from __future__ import annotations

from collections.abc import Sequence
from fractions import Fraction

from fretsure.ir import Note
from fretsure.oracle.core import check_playability
from fretsure.oracle.input import (
    MAX_SOLVER_WORK_UNITS,
    OracleInputCode,
    SolverInputError,
    ensure_solver_domain,
)
from fretsure.oracle.profiles import Profile
from fretsure.solver.api import (
    Infeasible,
    InfeasibleCode,
    _select_score_supervised_finalist,
    _select_technique_finalist,
    _solve_fingering_with_green_pool,
    _SolverContinuation,
)
from fretsure.solver.score_supervision import PUBLISHED_FINGERING_MIN_ONSETS
from fretsure.solver.sustain import sustain_relaxations
from fretsure.solver.technique import DEFAULT_TECHNIQUE_PROFILE, technique_profile
from fretsure.tab import Tab

SCORE_SOLVER_VERSION = "score-solver@0.6.0"
# A long score legitimately needs more independent searches than a short one, and
# four was arbitrary: it rejected ten of fifty-eight published pieces for running
# out of splits rather than for anything a hand could not do.  Raising it is the
# only compute knob that buys any repertoire at all -- widening the beam, the
# per-frame configurations, the per-frame fingerings or the final full checks all
# measured at zero.  Thirty-two is where that gain stops.
MAX_SCORE_SOLVER_SEGMENTS = 32
# Sum of the conservative work estimates for admitted leaf searches. Rejected
# oversized preflight calls and the final full-history oracle are control work,
# not additional admitted solver searches.
MAX_SCORE_SOLVER_AGGREGATE_WORK_UNITS = MAX_SCORE_SOLVER_SEGMENTS * MAX_SOLVER_WORK_UNITS


def _work_limit_only(error: SolverInputError) -> bool:
    codes = {diagnostic.code for diagnostic in error.diagnostics}
    return codes == {OracleInputCode.SOLVER_WORK_LIMIT}


def _split_at_frame(notes: tuple[Note, ...]) -> tuple[tuple[Note, ...], tuple[Note, ...]]:
    onsets = tuple(sorted({note.onset for note in notes}))
    if len(onsets) < 2:
        raise ValueError("one onset frame cannot be split without changing the music")
    target_index = len(onsets) // 2
    candidates: list[tuple[Fraction, int, Fraction]] = []
    for index in range(1, len(onsets)):
        boundary = onsets[index]
        prior_release = max(note.onset + note.duration for note in notes if note.onset < boundary)
        rest = boundary - prior_release
        candidates.append((rest, -abs(index - target_index), boundary))
    _rest, _distance, boundary = max(candidates)
    left = tuple(note for note in notes if note.onset < boundary)
    right = tuple(note for note in notes if note.onset >= boundary)
    if not left or not right:  # pragma: no cover - protected by distinct onsets
        raise AssertionError("frame split produced an empty side")
    return left, right


def _solve_parts(
    notes: tuple[Note, ...],
    tuning: tuple[int, ...],
    capo: int,
    profile: Profile,
    *,
    tempo_bpm: float,
    beats_per_bar: int,
    beam: int,
    segment_budget: int,
    technique_profile_name: str,
    continuation: _SolverContinuation | None = None,
) -> tuple[tuple[Tab, ...], _SolverContinuation] | Infeasible:
    if segment_budget < 1:
        final_onset = max((note.onset for note in notes), default=None)
        return Infeasible(
            InfeasibleCode.NO_NON_RED_EXTENSION,
            final_onset,
            "score-level solver segment budget is exhausted",
            tuple(sorted(note.pitch for note in notes if note.onset == final_onset)),
        )
    try:
        outcome = _solve_fingering_with_green_pool(
            notes,
            tuning,
            capo,
            profile,
            tempo_bpm=tempo_bpm,
            beats_per_bar=beats_per_bar,
            beam=beam,
            _collect_full_green_pool=(
                len({note.onset for note in notes}) >= PUBLISHED_FINGERING_MIN_ONSETS
                or technique_profile_name != DEFAULT_TECHNIQUE_PROFILE
            ),
            _initial_continuation=continuation,
        )
        solved = outcome.result
        next_continuation = outcome.continuation
        if outcome.green_pool:
            selected = (
                _select_score_supervised_finalist(outcome.green_pool)
                if technique_profile_name == DEFAULT_TECHNIQUE_PROFILE
                else _select_technique_finalist(
                    outcome.green_pool,
                    technique_profile_name,
                )
            )
            solved = selected.tab
            next_continuation = selected.continuation
    except SolverInputError as error:
        if not _work_limit_only(error):
            raise
        if segment_budget == 1:
            final_onset = max((note.onset for note in notes), default=None)
            return Infeasible(
                InfeasibleCode.NO_NON_RED_EXTENSION,
                final_onset,
                "score-level solver segment budget is exhausted",
                tuple(sorted(note.pitch for note in notes if note.onset == final_onset)),
            )
        try:
            left, right = _split_at_frame(notes)
        except ValueError:
            raise error from None
        left_result = _solve_parts(
            left,
            tuning,
            capo,
            profile,
            tempo_bpm=tempo_bpm,
            beats_per_bar=beats_per_bar,
            beam=beam,
            segment_budget=segment_budget - 1,
            technique_profile_name=technique_profile_name,
            continuation=continuation,
        )
        if isinstance(left_result, Infeasible):
            return left_result
        left_parts, left_continuation = left_result
        right_result = _solve_parts(
            right,
            tuning,
            capo,
            profile,
            tempo_bpm=tempo_bpm,
            beats_per_bar=beats_per_bar,
            beam=beam,
            segment_budget=segment_budget - len(left_parts),
            technique_profile_name=technique_profile_name,
            continuation=left_continuation,
        )
        if isinstance(right_result, Infeasible):
            return right_result
        right_parts, right_continuation = right_result
        return (left_parts + right_parts, right_continuation)
    if isinstance(solved, Infeasible):
        return solved
    if next_continuation is None:  # pragma: no cover - successful invariant
        raise AssertionError("successful fingering search omitted continuation")
    return ((solved,), next_continuation)


def solve_fingering_score(
    notes: Sequence[Note],
    tuning: tuple[int, ...],
    capo: int,
    profile: Profile,
    *,
    tempo_bpm: float = 90.0,
    beats_per_bar: int = 4,
    beam: int = 16,
    technique_profile_name: str = DEFAULT_TECHNIQUE_PROFILE,
) -> Tab | Infeasible:
    """Solve a complete score while preserving the solver's per-search work gate."""

    preference = technique_profile(technique_profile_name)
    exact_notes, exact_tuning, exact_capo, exact_profile, exact_tempo_bpm = ensure_solver_domain(
        notes,
        tuning,
        capo,
        profile,
        tempo_bpm=tempo_bpm,
    )
    # The score exactly as written is always the first rung, so a score that
    # solves without giving anything up takes exactly the path it always took.
    # Later rungs let the accompanying voices go as early as the target itself
    # permits, which is the only way early release buys travel time: the hand
    # has to be free before the frame that needs it, not at it.
    first_failure: Infeasible | None = None
    for attempt in sustain_relaxations(exact_notes):
        solved = _solve_relaxation(
            attempt,
            exact_tuning,
            exact_capo,
            exact_profile,
            tempo_bpm=exact_tempo_bpm,
            beats_per_bar=beats_per_bar,
            beam=beam,
            technique_profile_name=preference.id,
        )
        if isinstance(solved, Tab):
            return solved
        # Report the score as written, not the last thing tried.
        first_failure = first_failure or solved
    assert first_failure is not None  # the ladder always has a first rung
    return first_failure


def _solve_relaxation(
    exact_notes: tuple[Note, ...],
    exact_tuning: tuple[int, ...],
    exact_capo: int,
    exact_profile: Profile,
    *,
    tempo_bpm: float,
    beats_per_bar: int,
    beam: int,
    technique_profile_name: str,
) -> Tab | Infeasible:
    """Solve one rung of the sustain ladder under the full-history oracle gate."""

    result = _solve_parts(
        exact_notes,
        exact_tuning,
        exact_capo,
        exact_profile,
        tempo_bpm=tempo_bpm,
        beats_per_bar=beats_per_bar,
        beam=beam,
        segment_budget=MAX_SCORE_SOLVER_SEGMENTS,
        technique_profile_name=technique_profile_name,
    )
    if isinstance(result, Infeasible):
        return result
    parts, _continuation = result
    combined = Tab(
        tuple(
            sorted(
                (note for part in parts for note in part.notes),
                key=lambda note: (note.onset, note.string),
            )
        ),
        parts[0].tuning,
        parts[0].capo,
    )
    oracle = check_playability(
        combined,
        exact_profile,
        tempo_bpm=tempo_bpm,
        beats_per_bar=beats_per_bar,
    )
    if oracle.verdict != "RED":
        return combined
    final_onset = max((note.onset for note in exact_notes), default=None)
    pitches = tuple(sorted(note.pitch for note in exact_notes if note.onset == final_onset))
    return Infeasible(
        InfeasibleCode.NO_NON_RED_EXTENSION,
        final_onset,
        "independently bounded score segments failed the full-history oracle gate",
        pitches,
    )


__all__ = [
    "MAX_SCORE_SOLVER_AGGREGATE_WORK_UNITS",
    "MAX_SCORE_SOLVER_SEGMENTS",
    "SCORE_SOLVER_VERSION",
    "solve_fingering_score",
]
