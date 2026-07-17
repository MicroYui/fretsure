"""Beam-search fingering solver.

Builds the tab frame by frame.  The inner loop carries the finite oracle state
needed by a new attack (sounding notes, right-finger history, and the previous
required hand shape) instead of rechecking an ever-growing partial Tab for every
beam extension.  A complete ``check_playability`` gate still guards every
returned Tab, so the public contract remains: the solver never returns RED.

Contract: the returned Tab is always non-RED under the given profile. The dual
"finds a solution whenever one exists" is best-effort — the bounded beam plus the
``frame_configs`` / ``feasible_fingerings`` caps can drop a config a later frame
needs — and biases to a safe Infeasible rather than a RED output.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from typing import cast

from fretsure.geometry import press_x
from fretsure.ir import Note
from fretsure.oracle.core import check_playability
from fretsure.oracle.input import (
    MAX_SOLVER_FINAL_CHECKS,
    MAX_SOLVER_FRAME_CONFIGS,
    SolverInputError,
    ensure_solver_input,
)
from fretsure.oracle.predicates import (
    check_barre,
    check_finger_count,
    check_finger_monotonic,
    check_fret_span,
)
from fretsure.oracle.profiles import Profile, optimistic
from fretsure.solver.candidates import candidates
from fretsure.solver.cost import config_base_cost, transition_cost
from fretsure.solver.frames import FrameConfig
from fretsure.solver.frames import frame_configs as _frame_configs
from fretsure.tab import RightFinger, Tab, TabNote


class InfeasibleCode(StrEnum):
    """Stable reasons that a valid target has no returned fingering."""

    EMPTY_TARGET = "EMPTY_TARGET"
    UNREACHABLE_PITCH = "UNREACHABLE_PITCH"
    NO_FRAME_CONFIG = "NO_FRAME_CONFIG"
    NO_NON_RED_EXTENSION = "NO_NON_RED_EXTENSION"


@dataclass(frozen=True)
class Infeasible:
    code: InfeasibleCode
    onset: Fraction | None
    reason: str
    pitches: tuple[int, ...]


_RIGHT_ORDER: tuple[RightFinger, ...] = ("p", "i", "m", "a")
_RIGHT_RANK = {finger: rank for rank, finger in enumerate(_RIGHT_ORDER)}
_RightHistory = tuple[
    Fraction | None,
    Fraction | None,
    Fraction | None,
    Fraction | None,
]


@dataclass(frozen=True, slots=True)
class _ShiftOracleState:
    center: float
    active_note_ids: frozenset[int]
    latest_release: Fraction


@dataclass(frozen=True, slots=True)
class _IncrementalOracleState:
    active: tuple[tuple[int, TabNote], ...]
    right_last_used: _RightHistory
    shift: _ShiftOracleState | None


@dataclass(frozen=True, slots=True)
class _State:
    cost: float
    parent: _State | None
    added: tuple[TabNote, ...]
    note_count: int
    rank: int
    last_cfg: FrameConfig | None
    oracle: _IncrementalOracleState


def _state_sort_key(state: _State) -> tuple[float, int]:
    """Constant-size deterministic ordering for beam and finalist selection."""

    return (state.cost, state.rank)


def _reconstruct_notes(state: _State) -> tuple[TabNote, ...]:
    """Iteratively materialize one explicitly selected finalist path."""

    chunks: list[tuple[TabNote, ...]] = []
    cursor: _State | None = state
    while cursor is not None:
        if cursor.added:
            chunks.append(cursor.added)
        cursor = cursor.parent
    notes = tuple(note for chunk in reversed(chunks) for note in chunk)
    assert len(notes) == state.note_count
    return notes


def _frame_geometry(config: FrameConfig | None) -> tuple[tuple[int, int], ...]:
    if config is None:
        return ()
    return tuple((placement.string, placement.fret) for placement in config.placements)


def _state_diversity_key(
    state: _State,
) -> tuple[tuple[tuple[int, int], ...], float | None]:
    """Group RH/LH variants while retaining distinct placement/hand positions."""

    shift_center = state.oracle.shift.center if state.oracle.shift is not None else None
    return (_frame_geometry(state.last_cfg), shift_center)


def _select_diverse_states(states: list[_State], limit: int) -> list[_State]:
    ordered = sorted(states, key=_state_sort_key)
    groups: dict[
        tuple[tuple[tuple[int, int], ...], float | None],
        list[tuple[int, _State]],
    ] = {}
    for index, state in enumerate(ordered):
        groups.setdefault(_state_diversity_key(state), []).append((index, state))

    selected_indices: list[int] = []
    selected: set[int] = set()
    seen_left: dict[
        tuple[tuple[tuple[int, int], ...], float | None],
        set[tuple[tuple[int, int, int], ...]],
    ] = {geometry: set() for geometry in groups}
    seen_right: dict[
        tuple[tuple[tuple[int, int], ...], float | None],
        set[_RightHistory],
    ] = {geometry: set() for geometry in groups}

    def left_key(state: _State) -> tuple[tuple[int, int, int], ...]:
        return tuple(
            (note.string, note.fret, note.left_finger)
            for _, note in sorted(
                state.oracle.active,
                key=lambda item: (item[1].string, item[0]),
            )
        )

    def add(
        index: int,
        state: _State,
        geometry: tuple[tuple[tuple[int, int], ...], float | None],
    ) -> None:
        selected_indices.append(index)
        selected.add(index)
        seen_left[geometry].add(left_key(state))
        seen_right[geometry].add(state.oracle.right_last_used)

    # One state per placement/hand-position geometry comes first.
    for geometry, variants in groups.items():
        index, state = variants[0]
        add(index, state, geometry)
        if len(selected_indices) >= limit:
            return [ordered[i] for i in selected_indices]

    # Alternate LH-shape and RH-history novelty rounds across geometries.  This
    # keeps, for example, both L3 and L4 on a sustained bass as well as distinct
    # p-i-m-a histories, instead of letting cheap near-duplicate states fill the
    # beam before a later attack reveals which history was required.
    while True:
        added_novelty = False
        for feature in ("left", "right"):
            for geometry, variants in groups.items():
                candidate: tuple[int, _State] | None = None
                for index, state in variants:
                    if index in selected:
                        continue
                    if feature == "left" and left_key(state) in seen_left[geometry]:
                        continue
                    if feature == "right" and state.oracle.right_last_used in seen_right[geometry]:
                        continue
                    candidate = (index, state)
                    break
                if candidate is None:
                    continue
                add(candidate[0], candidate[1], geometry)
                added_novelty = True
                if len(selected_indices) >= limit:
                    return [ordered[i] for i in selected_indices]
        if not added_novelty:
            break

    # Use any remaining budget round-robin rather than returning to a single
    # cheapest geometry group.
    while len(selected_indices) < limit:
        added_variant = False
        for geometry, variants in groups.items():
            candidate = next(
                ((index, state) for index, state in variants if index not in selected),
                None,
            )
            if candidate is None:
                continue
            add(candidate[0], candidate[1], geometry)
            added_variant = True
            if len(selected_indices) >= limit:
                return [ordered[i] for i in selected_indices]
        if not added_variant:
            break
    return [ordered[i] for i in selected_indices]


def _left_hand_frame_passes(
    active: tuple[tuple[int, TabNote], ...],
    tuning: tuple[int, ...],
    capo: int,
    profile: Profile,
) -> bool:
    """Run the oracle's static LH predicates on the current sounding shape."""

    projected = tuple(
        TabNote(
            Fraction(0),
            Fraction(1),
            note.string,
            note.fret,
            note.left_finger,
            note.right_finger,
        )
        for _, note in active
    )
    frame = Tab(projected, tuning, capo)
    return not (
        check_finger_count(frame, profile)
        or check_finger_monotonic(frame, profile)
        or check_barre(frame, profile)
        or check_fret_span(frame, profile)
    )


def _advance_oracle_state(
    prior: _IncrementalOracleState,
    *,
    onset: Fraction,
    added: tuple[TabNote, ...],
    first_note_id: int,
    tuning: tuple[int, ...],
    capo: int,
    profile: Profile,
    tempo_bpm: float,
) -> _IncrementalOracleState | None:
    """Conservative one-sided prefilter for one new frame.

    ``profile`` is already the optimistic oracle profile.  Previously accepted
    history cannot acquire a new local violation by itself, so only notes still
    sounding, the last use of each right finger, and a permissive summary of the
    prior fretted shape are carried.  Every rejection must also fail the complete
    optimistic-prefix oracle; admission is intentionally one-sided and may keep
    a false-positive state.  The complete oracle remains the final soundness gate.
    """

    still_active = tuple(item for item in prior.active if onset < item[1].onset + item[1].duration)
    added_with_ids = tuple((first_note_id + offset, note) for offset, note in enumerate(added))
    active = still_active + added_with_ids

    # A newly attacked string cannot coexist with a note still sounding there.
    strings = [note.string for _, note in active]
    if len(strings) != len(set(strings)):
        return None
    if not _left_hand_frame_passes(active, tuning, capo, profile):
        return None

    # frame_configs guarantees this too, but keeping the transition check local
    # makes the finite-state contract explicit and fail-closed.
    if len(added) > len(_RIGHT_ORDER):
        return None
    used_right: set[RightFinger] = set()
    prior_string = -1
    prior_rank = -1
    right_history = list(prior.right_last_used)
    for note in sorted(added, key=lambda item: item.string):
        rank = _RIGHT_RANK.get(note.right_finger)
        if (
            rank is None
            or note.right_finger in used_right
            or note.string <= prior_string
            or rank < prior_rank
        ):
            return None
        used_right.add(note.right_finger)
        prior_string = note.string
        prior_rank = rank
        last_used = right_history[rank]
        if last_used is not None:
            elapsed_seconds = float(onset - last_used) * 60.0 / tempo_bpm
            if 0 < elapsed_seconds < 1.0 / profile.r_max_hz:
                return None
    for note in added:
        right_history[_RIGHT_RANK[note.right_finger]] = onset

    shift = prior.shift
    fretted = tuple((index, note) for index, note in active if note.fret > 0)
    if fretted:
        xs = tuple(press_x(capo + note.fret, profile.string_length_mm) for _, note in fretted)
        assert all(value is not None for value in xs)
        current_center = sum(value for value in xs if value is not None) / len(xs)
        active_note_ids = frozenset(index for index, _ in fretted)
        latest_release = max(note.onset + note.duration for _, note in fretted)
        terminal_xs = tuple(
            press_x(capo + note.fret, profile.string_length_mm)
            for _, note in fretted
            if note.onset + note.duration == latest_release
        )
        assert terminal_xs and all(value is not None for value in terminal_xs)
        terminal_center = sum(value for value in terminal_xs if value is not None) / len(
            terminal_xs
        )

        if shift is not None and not (active_note_ids & shift.active_note_ids):
            available_beats = max(Fraction(0), onset - shift.latest_release)
            available_seconds = float(available_beats) * 60.0 / tempo_bpm
            centre_delta = abs(current_center - shift.center)
            required_distance = max(0.0, centre_delta - 2.0 * profile.reach_mm)
            allowed_distance = profile.v_shift_mm_per_s * available_seconds
            if required_distance > allowed_distance:
                return None
        shift = _ShiftOracleState(
            terminal_center,
            active_note_ids,
            latest_release,
        )

    return _IncrementalOracleState(
        active,
        cast(_RightHistory, tuple(right_history)),
        shift,
    )


def solve_fingering(
    notes: Sequence[Note],
    tuning: tuple[int, ...],
    capo: int,
    profile: Profile,
    *,
    tempo_bpm: float = 90.0,
    beats_per_bar: int = 4,
    beam: int = 16,
) -> Tab | Infeasible:
    notes, tuning, capo, profile, tempo_bpm, beam = ensure_solver_input(
        notes,
        tuning,
        capo,
        profile,
        tempo_bpm=tempo_bpm,
        beam=beam,
    )
    by_onset: dict[Fraction, list[Note]] = defaultdict(list)
    for n in notes:
        by_onset[n.onset].append(n)
    onsets = sorted(by_onset)
    if not onsets:
        return Infeasible(
            code=InfeasibleCode.EMPTY_TARGET,
            onset=None,
            reason="target contains no notes",
            pitches=(),
        )

    empty_oracle_state = _IncrementalOracleState(
        (),
        (None, None, None, None),
        None,
    )
    states: list[_State] = [
        _State(0.0, None, (), 0, 0, None, empty_oracle_state)
    ]
    next_state_rank = 1
    pitches_at_onset = {
        onset: tuple(sorted(note.pitch for note in frame_notes))
        for onset, frame_notes in by_onset.items()
    }
    pitch_frame_counts = Counter(pitches_at_onset.values())
    config_cache: dict[tuple[int, ...], tuple[FrameConfig, ...]] = {}
    optimistic_profile = optimistic(profile)
    for onset in onsets:
        fnotes = by_onset[onset]
        pitches = pitches_at_onset[onset]
        durs = {fn.pitch: fn.duration for fn in fnotes}
        if len(pitches) > len(_RIGHT_ORDER):
            return Infeasible(
                code=InfeasibleCode.NO_FRAME_CONFIG,
                onset=onset,
                reason="frame has more attacks than available right-hand fingers",
                pitches=pitches,
            )
        for pitch in pitches:
            if not candidates(pitch, tuning, capo, profile.max_fret):
                return Infeasible(
                    code=InfeasibleCode.UNREACHABLE_PITCH,
                    onset=onset,
                    reason=f"pitch {pitch} unreachable on this tuning/capo",
                    pitches=(pitch,),
                )
        cacheable = pitch_frame_counts[pitches] > 1
        cfgs = config_cache.get(pitches) if cacheable else None
        if cfgs is None:
            cfgs = tuple(
                _frame_configs(
                    pitches,
                    tuning,
                    capo,
                    profile,
                    limit=MAX_SOLVER_FRAME_CONFIGS,
                )
            )
            if cacheable:
                config_cache[pitches] = cfgs
        if not cfgs:
            return Infeasible(
                code=InfeasibleCode.NO_FRAME_CONFIG,
                onset=onset,
                reason="no feasible frame config",
                pitches=pitches,
            )

        extended: list[_State] = []
        for state in states:
            for cfg in cfgs:
                added = tuple(
                    TabNote(onset, durs[p.pitch], p.string, p.fret, p.left_finger, p.right_finger)
                    for p in cfg.placements
                )
                oracle_state = _advance_oracle_state(
                    state.oracle,
                    onset=onset,
                    added=added,
                    first_note_id=state.note_count,
                    tuning=tuning,
                    capo=capo,
                    profile=optimistic_profile,
                    tempo_bpm=tempo_bpm,
                )
                if oracle_state is None:
                    continue
                step = (
                    0.0
                    if state.last_cfg is None
                    else transition_cost(state.last_cfg, cfg, capo, profile)
                )
                extended.append(
                    _State(
                        state.cost + step + config_base_cost(cfg),
                        state,
                        added,
                        state.note_count + len(added),
                        next_state_rank,
                        cfg,
                        oracle_state,
                    )
                )
                next_state_rank += 1

        if not extended:
            return Infeasible(
                code=InfeasibleCode.NO_NON_RED_EXTENSION,
                onset=onset,
                reason="no non-red extension within beam",
                pitches=pitches,
            )
        states = _select_diverse_states(extended, beam)

    # This full-history gate is the public soundness boundary.  Incremental
    # pruning may conservatively return Infeasible, but no discrepancy can leak
    # a RED Tab to the caller.
    first_amber: Tab | None = None
    finalists = sorted(states, key=_state_sort_key)[:MAX_SOLVER_FINAL_CHECKS]
    for state in finalists:
        result = Tab(_reconstruct_notes(state), tuning, capo)
        verdict = check_playability(
            result,
            profile,
            tempo_bpm=tempo_bpm,
            beats_per_bar=beats_per_bar,
        ).verdict
        if verdict == "GREEN":
            return result
        if verdict == "AMBER" and first_amber is None:
            first_amber = result
    if first_amber is not None:
        return first_amber

    final_onset = onsets[-1]
    return Infeasible(
        code=InfeasibleCode.NO_NON_RED_EXTENSION,
        onset=final_onset,
        reason="no candidate passes the final full-oracle gate",
        pitches=tuple(sorted(note.pitch for note in by_onset[final_onset])),
    )


__all__ = [
    "Infeasible",
    "InfeasibleCode",
    "SolverInputError",
    "solve_fingering",
]
