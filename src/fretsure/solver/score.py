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
from fretsure.solver.api import Infeasible, InfeasibleCode, solve_fingering
from fretsure.tab import Tab

SCORE_SOLVER_VERSION = "score-solver@0.1.0"
MAX_SCORE_SOLVER_SEGMENTS = 4
# Sum of the conservative work estimates for admitted leaf searches. Rejected
# oversized preflight calls and the final full-history oracle are control work,
# not additional admitted solver searches.
MAX_SCORE_SOLVER_AGGREGATE_WORK_UNITS = (
    MAX_SCORE_SOLVER_SEGMENTS * MAX_SOLVER_WORK_UNITS
)


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
        prior_release = max(
            note.onset + note.duration for note in notes if note.onset < boundary
        )
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
) -> tuple[Tab, ...] | Infeasible:
    if segment_budget < 1:
        final_onset = max((note.onset for note in notes), default=None)
        return Infeasible(
            InfeasibleCode.NO_NON_RED_EXTENSION,
            final_onset,
            "score-level solver segment budget is exhausted",
            tuple(sorted(note.pitch for note in notes if note.onset == final_onset)),
        )
    try:
        solved = solve_fingering(
            notes,
            tuning,
            capo,
            profile,
            tempo_bpm=tempo_bpm,
            beats_per_bar=beats_per_bar,
            beam=beam,
        )
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
        )
        if isinstance(left_result, Infeasible):
            return left_result
        right_result = _solve_parts(
            right,
            tuning,
            capo,
            profile,
            tempo_bpm=tempo_bpm,
            beats_per_bar=beats_per_bar,
            beam=beam,
            segment_budget=segment_budget - len(left_result),
        )
        if isinstance(right_result, Infeasible):
            return right_result
        return left_result + right_result
    if isinstance(solved, Infeasible):
        return solved
    return (solved,)


def solve_fingering_score(
    notes: Sequence[Note],
    tuning: tuple[int, ...],
    capo: int,
    profile: Profile,
    *,
    tempo_bpm: float = 90.0,
    beats_per_bar: int = 4,
    beam: int = 16,
) -> Tab | Infeasible:
    """Solve a complete score while preserving the solver's per-search work gate."""

    exact_notes, exact_tuning, exact_capo, exact_profile, exact_tempo_bpm = (
        ensure_solver_domain(
            notes,
            tuning,
            capo,
            profile,
            tempo_bpm=tempo_bpm,
        )
    )
    result = _solve_parts(
        exact_notes,
        exact_tuning,
        exact_capo,
        exact_profile,
        tempo_bpm=exact_tempo_bpm,
        beats_per_bar=beats_per_bar,
        beam=beam,
        segment_budget=MAX_SCORE_SOLVER_SEGMENTS,
    )
    if isinstance(result, Infeasible):
        return result
    combined = Tab(
        tuple(
            sorted(
                (note for part in result for note in part.notes),
                key=lambda note: (note.onset, note.string),
            )
        ),
        result[0].tuning,
        result[0].capo,
    )
    oracle = check_playability(
        combined,
        exact_profile,
        tempo_bpm=exact_tempo_bpm,
        beats_per_bar=beats_per_bar,
    )
    if oracle.verdict != "RED":
        return combined
    final_onset = max((note.onset for note in exact_notes), default=None)
    pitches = tuple(
        sorted(note.pitch for note in exact_notes if note.onset == final_onset)
    )
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
