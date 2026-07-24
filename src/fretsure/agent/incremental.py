"""Baseline-first incremental arrangement for the product path.

The legacy benchmark policy asks the model for a complete target and then repairs
that target destructively.  This module deliberately leaves that frozen policy
alone.  It starts from the exact source melody, proves that seed GREEN, and treats
the non-melody part of one normal proposal as a bounded pool of optional additions.
The deterministic solver/oracle accepts playable batches and bisects rejected
batches.  A failed addition can therefore never destroy the last GREEN checkpoint.

There is exactly one proposal call per real-model candidate and, optionally, one
critic call for its final enriched checkpoint.  Bisection consumes solver calls,
not model calls.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from fractions import Fraction
from typing import cast

from fretsure.agent.arranger import (
    ArrangeGoal,
    ProposalOutcome,
    ProposalStatus,
    arrangement_solver_ir,
    propose_arrangement_outcome,
)
from fretsure.agent.critic import CriticOutcome, CriticScore, CriticStatus, critique_outcome
from fretsure.agent.harness import ArrangeResult
from fretsure.agent.model_calls import ModelCallScopeFactory
from fretsure.agent.tools import solve_and_check
from fretsure.agent.trace import Trace, TraceStep, target_checkpoint
from fretsure.ir import ChordSymbol, MusicIR, Note, snapshot_music_ir
from fretsure.llm.client import LLMClient
from fretsure.metrics.fidelity import FaithfulnessGate, Fidelity, faithfulness, fidelity
from fretsure.oracle.core import OracleResult
from fretsure.oracle.input import (
    ensure_boolean_control,
    ensure_candidate_count,
    ensure_repair_iterations,
    ensure_solver_domain,
)
from fretsure.oracle.profiles import MEDIAN_HAND, Profile
from fretsure.solver.api import Infeasible
from fretsure.tab import Tab

MAX_INCREMENTAL_TRIAL_SOLVES = 8
MAX_INCREMENTAL_BATCH_DECISIONS = 64
_CONSONANT_INTERVAL_CLASSES = frozenset({0, 3, 4, 5, 7, 8, 9})


class IncrementalRejectReason(StrEnum):
    """Stable, public-safe reason for rejecting one proposed addition batch."""

    STATIC_GATE = "STATIC_GATE"
    NO_TAB = "NO_TAB"
    NON_GREEN = "NON_GREEN"
    FAITHFULNESS_REGRESSION = "FAITHFULNESS_REGRESSION"
    NO_MUSICAL_GAIN = "NO_MUSICAL_GAIN"


@dataclass(frozen=True, slots=True)
class EnrichmentQuality:
    """Small deterministic taste proxy used only as an acceptance floor.

    Points reward useful coverage, not raw note count.  The optional final LLM
    critic remains the richer taste signal for best-of-N selection.
    """

    covered_measures: int
    covered_slots: int
    root_segments: int
    bass_leap_sum: int
    addition_note_count: int

    def __post_init__(self) -> None:
        for name in (
            "covered_measures",
            "covered_slots",
            "root_segments",
            "bass_leap_sum",
            "addition_note_count",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be an exact non-negative integer")

    @property
    def points(self) -> int:
        return 4 * self.root_segments + 2 * self.covered_measures + self.covered_slots


@dataclass(frozen=True, slots=True)
class IncrementalCheckpoint:
    """One immutable GREEN target/tab checkpoint."""

    target: tuple[Note, ...]
    tab: Tab
    oracle: OracleResult
    fidelity: Fidelity
    faithfulness: FaithfulnessGate
    quality: EnrichmentQuality

    def __post_init__(self) -> None:
        if type(self.target) is not tuple:
            raise ValueError("checkpoint target must be an exact tuple")
        if type(self.tab) is not Tab or type(self.oracle) is not OracleResult:
            raise ValueError("checkpoint must contain exact solver/oracle values")
        if self.oracle.verdict != "GREEN":
            raise ValueError("incremental checkpoints must be GREEN")


@dataclass(frozen=True, slots=True)
class IncrementalTrial:
    """One transactional batch decision during deterministic salvage."""

    ordinal: int
    batch: tuple[Note, ...]
    solver_called: bool
    accepted: bool
    reason: IncrementalRejectReason | None
    verdict: str | None

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("trial ordinal must be an exact non-negative integer")
        if type(self.batch) is not tuple or not self.batch:
            raise ValueError("trial batch must be a non-empty exact tuple")
        if type(self.solver_called) is not bool or type(self.accepted) is not bool:
            raise ValueError("trial flags must be exact booleans")
        if self.accepted != (self.reason is None):
            raise ValueError("accepted trials must have no rejection reason")
        if not self.solver_called and self.verdict is not None:
            raise ValueError("a solver-free trial cannot carry a verdict")


@dataclass(frozen=True, slots=True)
class IncrementalCandidate:
    """One best-of-N model draw, salvaged from the shared GREEN seed."""

    index: int
    temperature: float
    proposal: ProposalOutcome
    best: IncrementalCheckpoint
    trials: tuple[IncrementalTrial, ...]
    critic_outcome: CriticOutcome | None
    trace_steps: tuple[TraceStep, ...]
    solver_calls: int
    proposed_addition_count: int

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValueError("candidate index must be an exact non-negative integer")
        if type(self.temperature) is not float or not math.isfinite(self.temperature):
            raise ValueError("candidate temperature must be a finite float")
        if type(self.trials) is not tuple or type(self.trace_steps) is not tuple:
            raise ValueError("candidate trials/trace must be exact tuples")
        if (
            type(self.solver_calls) is not int
            or not 0 <= self.solver_calls <= MAX_INCREMENTAL_TRIAL_SOLVES
        ):
            raise ValueError("candidate trial-solver count is outside the bounded budget")
        if self.solver_calls != sum(trial.solver_called for trial in self.trials):
            raise ValueError("candidate solver count disagrees with its trials")
        if type(self.proposed_addition_count) is not int or self.proposed_addition_count < 0:
            raise ValueError("proposed addition count must be non-negative")

    @property
    def accepted_addition_count(self) -> int:
        return self.best.quality.addition_note_count

    @property
    def critic(self) -> CriticScore | None:
        return self.critic_outcome.score if self.critic_outcome is not None else None

    @property
    def llm_calls(self) -> int:
        return self.proposal.llm_calls + (
            self.critic_outcome.llm_calls if self.critic_outcome is not None else 0
        )


@dataclass(frozen=True, slots=True)
class IncrementalPool:
    """A shared melody seed plus independent best-of-N enrichment trajectories."""

    seed_target: tuple[Note, ...]
    seed_checkpoint: IncrementalCheckpoint | None
    seed_solved: Tab | Infeasible
    seed_oracle: OracleResult | None
    seed_trace_steps: tuple[TraceStep, ...]
    candidates: tuple[IncrementalCandidate, ...]
    requested_candidates: int

    @property
    def solver_calls(self) -> int:
        return 1 + sum(candidate.solver_calls for candidate in self.candidates)

    @property
    def llm_calls(self) -> int:
        return sum(candidate.llm_calls for candidate in self.candidates)


def melody_seed_target(ir: MusicIR) -> tuple[Note, ...]:
    """Return the exact coalesced source melody and no accompaniment notes."""

    source = snapshot_music_ir(ir)
    melody_only = MusicIR(
        tuple(note for note in source.notes if note.voice == "melody"),
        source.chords,
        source.meta,
    )
    return arrangement_solver_ir(melody_only).notes


def _piece_end(ir: MusicIR) -> Fraction:
    inferred = max((note.onset + note.duration for note in ir.notes), default=Fraction(0))
    return ir.meta.duration_beats if ir.meta.duration_beats is not None else inferred


def _bar_duration(ir: MusicIR) -> Fraction:
    numerator, denominator = ir.meta.time_sig
    return Fraction(numerator * 4, denominator)


def _active_chord(ir: MusicIR, onset: Fraction) -> ChordSymbol | None:
    active: ChordSymbol | None = None
    for chord in sorted(ir.chords, key=lambda item: item.onset):
        if chord.onset > onset:
            break
        active = chord
    return active


def _reference_melody_pitch(ir: MusicIR, onset: Fraction) -> int | None:
    melody = tuple(note for note in ir.notes if note.voice == "melody")
    sounding = tuple(
        note.pitch for note in melody if note.onset <= onset < note.onset + note.duration
    )
    if sounding:
        return max(sounding)
    if not melody:
        return None
    nearest = min(
        melody,
        key=lambda note: (
            abs(note.onset - onset),
            0 if note.onset <= onset else 1,
            note.onset,
            note.pitch,
        ),
    )
    return nearest.pitch


def _addition_priority(ir: MusicIR, note: Note) -> tuple[int, Fraction, int, int]:
    chord = _active_chord(ir, note.onset)
    if chord is not None and note.voice == "bass" and note.pitch % 12 == chord.root_pc:
        priority = 0
    elif chord is not None and note.voice == "bass":
        priority = 1
    elif chord is not None:
        priority = 2
    elif note.voice == "bass":
        priority = 3
    elif note.voice == "harmony":
        priority = 4
    else:
        # A model-authored melodic fill is useful only after the accompaniment
        # skeleton has been considered across the whole piece.
        priority = 5
    return priority, note.onset, 0 if note.voice == "bass" else 1, note.pitch


def proposal_additions(ir: MusicIR, proposal: ProposalOutcome) -> tuple[Note, ...]:
    """Extract genuine model-authored accompaniment and melodic-fill notes.

    Rule fallbacks remain useful provenance but are never mislabeled as Agent
    contributions. Exact source-melody anchors are removed here; any other
    model-authored ``melody`` note remains a candidate and must pass the strict
    silent-gap gate before it can reach the solver.
    """

    if proposal.status is not ProposalStatus.LLM_SUCCESS:
        return ()
    source = snapshot_music_ir(ir)
    seed_anchors = set(melody_seed_target(source))
    additions = {
        note
        for note in proposal.target
        if note not in seed_anchors
    }
    by_onset: dict[Fraction, list[Note]] = {}
    for note in additions:
        by_onset.setdefault(note.onset, []).append(note)
    groups = sorted(
        by_onset.values(),
        key=lambda group: (
            min(_addition_priority(source, note)[0] for note in group),
            group[0].onset,
        ),
    )
    return tuple(
        note
        for group in groups
        for note in sorted(group, key=lambda item: (item.voice != "bass", item.pitch))
    )


def _outside_in_groups(
    groups: tuple[tuple[Note, ...], ...],
) -> tuple[tuple[Note, ...], ...]:
    """Interleave early and late attacks so any bounded prefix spans the piece."""

    ordered = sorted(groups, key=lambda group: group[0].onset)
    result: list[tuple[Note, ...]] = []
    left = 0
    right = len(ordered) - 1
    while left <= right:
        result.append(ordered[left])
        left += 1
        if left <= right:
            result.append(ordered[right])
            right -= 1
    return tuple(result)


def _flatten_groups(groups: tuple[tuple[Note, ...], ...]) -> tuple[Note, ...]:
    return tuple(note for group in groups for note in group)


def _proposal_layers(
    ir: MusicIR, proposal: ProposalOutcome
) -> tuple[tuple[Note, ...], ...]:
    """Pre-crop one proposal into whole-piece accompaniment layers.

    Layer 1 selects at most one bass-led strong attack per bar, preferring an
    annotated root. Layer 2 selects at most one remaining later attack per bar,
    preferring inner motion or a legal melodic fill. Each layer is ordered
    outside-in so breadth-first bisection never spends the entire eight-solve
    budget on only the beginning of a song.
    """

    additions = proposal_additions(ir, proposal)
    if not additions:
        return ()
    bar_duration = _bar_duration(ir)
    accompaniment_by_onset: dict[Fraction, list[Note]] = {}
    valid_fills: list[tuple[Note, ...]] = []
    invalid_fills: list[tuple[Note, ...]] = []
    for note in additions:
        if note.voice == "melody":
            fill_group = (note,)
            if _melody_fill_reasons(ir, note):
                invalid_fills.append(fill_group)
            else:
                valid_fills.append(fill_group)
        else:
            accompaniment_by_onset.setdefault(note.onset, []).append(note)
    accompaniment_groups = tuple(
        tuple(sorted(notes, key=lambda note: (note.voice != "bass", note.pitch)))
        for _onset, notes in sorted(accompaniment_by_onset.items())
    )

    by_bar: dict[int, list[tuple[Note, ...]]] = {}
    for group in (*accompaniment_groups, *valid_fills):
        by_bar.setdefault(int(group[0].onset // bar_duration), []).append(group)

    layer_one: list[tuple[Note, ...]] = []
    selected: set[tuple[Note, ...]] = set()
    for bar in sorted(by_bar):
        bass_groups = [
            group for group in by_bar[bar] if any(note.voice == "bass" for note in group)
        ]
        if not bass_groups:
            continue

        def skeleton_key(
            group: tuple[Note, ...], bar_index: int = bar
        ) -> tuple[int, int, int, Fraction]:
            onset = group[0].onset
            position = onset - bar_index * bar_duration
            strong = position in {Fraction(0), bar_duration / 2}
            chord = _active_chord(ir, onset)
            root = chord is not None and any(
                note.voice == "bass" and note.pitch % 12 == chord.root_pc
                for note in group
            )
            return (
                0 if root and strong else 1,
                0 if root else 1,
                0 if strong else 1,
                onset,
            )

        chosen = min(bass_groups, key=skeleton_key)
        layer_one.append(chosen)
        selected.add(chosen)

    layer_two: list[tuple[Note, ...]] = []
    for bar in sorted(by_bar):
        remaining = [group for group in by_bar[bar] if group not in selected]
        if not remaining:
            continue

        def color_key(
            group: tuple[Note, ...], bar_index: int = bar
        ) -> tuple[int, int, Fraction]:
            onset = group[0].onset
            position = onset - bar_index * bar_duration
            colored = any(note.voice in {"melody", "harmony"} for note in group)
            return (
                0 if colored else 1,
                0 if position >= bar_duration / 2 else 1,
                onset,
            )

        chosen = min(remaining, key=color_key)
        layer_two.append(chosen)
        selected.add(chosen)

    melody_attacks = len({note.onset for note in ir.notes if note.voice == "melody"})
    maximum_attacks = (melody_attacks + 1) // 2
    ordered_one = _outside_in_groups(tuple(layer_one))[:maximum_attacks]
    remaining_budget = max(0, maximum_attacks - len(ordered_one))
    ordered_two = _outside_in_groups(tuple(layer_two))[:remaining_budget]
    ordered_invalid = _outside_in_groups(tuple(invalid_fills))
    return tuple(
        layer
        for layer in (
            _flatten_groups(ordered_one),
            _flatten_groups(ordered_two),
            _flatten_groups(ordered_invalid),
        )
        if layer
    )


def _addition_notes(ir: MusicIR, target: tuple[Note, ...]) -> tuple[Note, ...]:
    anchors = set(melody_seed_target(ir))
    return tuple(note for note in target if note not in anchors)


def enrichment_quality(ir: MusicIR, target: tuple[Note, ...]) -> EnrichmentQuality:
    """Compute the deterministic sparse-coverage score for one target."""

    additions = _addition_notes(ir, target)
    bar_duration = _bar_duration(ir)
    slots: set[tuple[int, int, str]] = set()
    measures: set[int] = set()
    for note in additions:
        bar = int(note.onset // bar_duration)
        position = note.onset - bar * bar_duration
        # A beat/pulse bucket rewards useful motion on beats 1 and 2 (or 3 and
        # 4) separately. The old half-bar bucket made a continuous bass line
        # look identical to one isolated note and therefore stopped enrichment
        # too early.
        beat_duration = Fraction(4, ir.meta.time_sig[1])
        pulse = int(position // beat_duration)
        measures.add(bar)
        slots.add((bar, pulse, note.voice))

    root_segments = 0
    bass = tuple(note for note in additions if note.voice == "bass")
    for chord in ir.chords:
        if any(
            note.onset <= chord.onset < note.onset + note.duration
            and note.pitch % 12 == chord.root_pc
            for note in bass
        ):
            root_segments += 1
    ordered_bass = sorted(bass, key=lambda note: (note.onset, note.pitch))
    bass_leap_sum = sum(
        abs(right.pitch - left.pitch)
        for left, right in zip(ordered_bass, ordered_bass[1:], strict=False)
    )
    return EnrichmentQuality(
        len(measures),
        len(slots),
        root_segments,
        bass_leap_sum,
        len(additions),
    )


def _melody_fill_reasons(ir: MusicIR, note: Note) -> tuple[str, ...]:
    """Return why a proposed melodic fill is not confined to a bounded rest."""

    melody = tuple(sorted(melody_seed_target(ir), key=lambda item: (item.onset, item.pitch)))
    reasons: list[str] = []
    if note.onset in {item.onset for item in melody}:
        reasons.append("SOURCE_MELODY_ONSET")
    if any(
        note.onset < item.onset + item.duration
        and note.onset + note.duration > item.onset
        for item in melody
    ):
        reasons.append("SOURCE_MELODY_OVERLAP")

    previous = tuple(
        item for item in melody if item.onset + item.duration <= note.onset
    )
    following = tuple(item for item in melody if item.onset > note.onset)
    if not previous or not following:
        reasons.append("UNBOUNDED_MELODY_GAP")
        return tuple(dict.fromkeys(reasons))
    previous_end = max(item.onset + item.duration for item in previous)
    previous_note = max(
        (item for item in previous if item.onset + item.duration == previous_end),
        key=lambda item: (item.onset, item.pitch),
    )
    next_onset = min(item.onset for item in following)
    next_note = max(
        (item for item in following if item.onset == next_onset),
        key=lambda item: item.pitch,
    )
    if note.onset < previous_end or note.onset + note.duration > next_onset:
        reasons.append("MELODY_FILL_EXCEEDS_GAP")
    left_leap = abs(note.pitch - previous_note.pitch)
    right_leap = abs(next_note.pitch - note.pitch)
    if left_leap > 7 or right_leap > 7 or min(left_leap, right_leap) > 4:
        reasons.append("MELODY_FILL_LEAP")
    return tuple(dict.fromkeys(reasons))


def _source_melody_anchors_preserved(
    ir: MusicIR, target: tuple[Note, ...]
) -> bool:
    return set(melody_seed_target(ir)).issubset(target)


def _static_gate_reasons(ir: MusicIR, target: tuple[Note, ...]) -> tuple[str, ...]:
    additions = _addition_notes(ir, target)
    reasons: list[str] = []
    if not _source_melody_anchors_preserved(ir, target):
        reasons.append("SOURCE_MELODY_ANCHOR_CHANGED")
    piece_end = _piece_end(ir)
    if any(note.onset >= piece_end or note.onset + note.duration > piece_end for note in additions):
        reasons.append("OUTSIDE_SOURCE_DURATION")

    by_onset: dict[Fraction, list[Note]] = {}
    by_bar_onset: dict[int, set[Fraction]] = {}
    bar_duration = _bar_duration(ir)
    for note in additions:
        by_onset.setdefault(note.onset, []).append(note)
        bar = int(note.onset // bar_duration)
        by_bar_onset.setdefault(bar, set()).add(note.onset)
        if note.voice == "melody":
            reasons.extend(_melody_fill_reasons(ir, note))
            continue
        melody_pitch = _reference_melody_pitch(ir, note.onset)
        if melody_pitch is None or note.pitch >= melody_pitch:
            reasons.append("VOICE_CROSSING")
            continue
        chord = _active_chord(ir, note.onset)
        if chord is not None:
            if note.pitch % 12 not in chord.pitch_classes:
                reasons.append("NON_CHORD_TONE")
        elif (melody_pitch - note.pitch) % 12 not in _CONSONANT_INTERVAL_CLASSES:
            reasons.append("DISSONANT_WITH_MELODY")

    if any(len(notes) > 2 for notes in by_onset.values()):
        reasons.append("ONSET_DENSITY")
    if any(
        sum(note.voice == voice for note in notes) > 1
        for notes in by_onset.values()
        for voice in ("melody", "bass", "harmony")
    ):
        reasons.append("DUPLICATE_ROLE_AT_ONSET")
    if any(len(onsets) > 2 for onsets in by_bar_onset.values()):
        reasons.append("MEASURE_DENSITY")
    melody_attacks = len({note.onset for note in ir.notes if note.voice == "melody"})
    maximum_attacks = (melody_attacks + 1) // 2
    if len(by_onset) > maximum_attacks:
        reasons.append("GLOBAL_DENSITY")

    for notes in by_onset.values():
        bass = next((note for note in notes if note.voice == "bass"), None)
        harmony = next((note for note in notes if note.voice == "harmony"), None)
        if bass is not None and harmony is not None and bass.pitch >= harmony.pitch:
            reasons.append("VOICE_ORDER")

    ordered_bass = sorted(
        (note for note in additions if note.voice == "bass"),
        key=lambda note: (note.onset, note.pitch),
    )
    if any(
        abs(right.pitch - left.pitch) > 12
        for left, right in zip(ordered_bass, ordered_bass[1:], strict=False)
    ):
        reasons.append("BASS_LEAP")
    return tuple(dict.fromkeys(reasons))


def _faithfulness_non_regressive(
    ir: MusicIR,
    target: tuple[Note, ...],
    before: FaithfulnessGate,
    after: FaithfulnessGate,
) -> bool:
    # Legal fills occur only outside source attacks. Anchor preservation is the
    # authoritative melody invariant here; raw top-voice F1 is intentionally not
    # used to veto or rank a safe fill.
    if not _source_melody_anchors_preserved(ir, target):
        return False
    score_fields = {
        "melody": "melody_f1",
        "bass_root": "bass_root",
        "harmony": "harmony",
    }
    for dimension in before.evaluated_dimensions:
        if dimension == "melody":
            continue
        field = score_fields[dimension]
        old = cast(float, getattr(before, field))
        new = cast(float, getattr(after, field))
        if new < old:
            return False
    return True


def _merge_target(current: tuple[Note, ...], batch: tuple[Note, ...]) -> tuple[Note, ...]:
    merged = {(note.onset, note.pitch): note for note in current}
    for note in batch:
        merged[(note.onset, note.pitch)] = note
    return tuple(sorted(merged.values(), key=lambda note: (note.onset, note.pitch, note.voice)))


def _quality_has_marginal_gain(
    ir: MusicIR,
    before: IncrementalCheckpoint,
    tentative: tuple[Note, ...],
    batch: tuple[Note, ...],
) -> bool:
    after_quality = enrichment_quality(ir, tentative)
    return bool(batch) and after_quality.points > before.quality.points


def _batch_wire(batch: tuple[Note, ...]) -> list[dict[str, object]]:
    return [
        {
            "onset": f"{note.onset.numerator}/{note.onset.denominator}",
            "duration": f"{note.duration.numerator}/{note.duration.denominator}",
            "pitch": note.pitch,
            "voice": note.voice,
        }
        for note in batch
    ]


def _trace_trial(
    trace: Trace,
    *,
    candidate_index: int,
    trial: IncrementalTrial,
    current: IncrementalCheckpoint,
) -> None:
    trace.add(
        "EDIT",
        "Accepted an additive batch into the GREEN checkpoint."
        if trial.accepted
        else "Rejected an additive batch and retained the prior GREEN checkpoint.",
        event="EDIT",
        candidate_index=candidate_index,
        iteration=trial.ordinal + 1,
        policy="incremental_v1",
        additions=_batch_wire(trial.batch),
        solver_called=trial.solver_called,
        accepted=trial.accepted,
        reason_code=None if trial.reason is None else trial.reason.value,
        verdict=trial.verdict,
        retained_target_checkpoint=target_checkpoint(current.target),
    )


def _split(batch: tuple[Note, ...]) -> tuple[tuple[Note, ...], tuple[Note, ...]]:
    # Simultaneous notes express one musical attack/voicing and remain atomic.
    # Bisection may isolate bad attacks, but it must never tear one chord apart.
    groups: list[tuple[Note, ...]] = []
    current: list[Note] = []
    current_onset: Fraction | None = None
    for note in batch:
        if current and note.onset != current_onset:
            groups.append(tuple(current))
            current = []
        current_onset = note.onset
        current.append(note)
    if current:
        groups.append(tuple(current))
    if len(groups) < 2:
        return batch, ()
    midpoint = (len(groups) + 1) // 2
    return (
        tuple(note for group in groups[:midpoint] for note in group),
        tuple(note for group in groups[midpoint:] for note in group),
    )


def _checkpoint(
    source: MusicIR,
    target: tuple[Note, ...],
    tab: Tab,
    oracle: OracleResult,
) -> IncrementalCheckpoint:
    return IncrementalCheckpoint(
        target,
        tab,
        oracle,
        fidelity(source, tab),
        faithfulness(source, tab),
        enrichment_quality(source, target),
    )


def _salvage_candidate(
    source: MusicIR,
    goal: ArrangeGoal,
    profile: Profile,
    proposal: ProposalOutcome,
    seed: IncrementalCheckpoint,
    *,
    candidate_index: int,
    temperature: float,
    max_trials: int,
    llm: LLMClient,
    use_critic: bool,
    call_scope_factory: ModelCallScopeFactory | None,
) -> IncrementalCandidate:
    additions = proposal_additions(source, proposal)
    layers = _proposal_layers(source, proposal)
    trace = Trace()
    trace.add(
        "PROPOSE",
        "Extracted optional accompaniment and rest-fill additions from one bounded model proposal.",
        event="PROPOSE",
        candidate_index=candidate_index,
        policy="incremental_v1",
        temperature=temperature,
        proposal_status=proposal.status.value,
        proposed_addition_count=len(additions),
        scheduled_addition_count=sum(len(layer) for layer in layers),
        seed_target_checkpoint=target_checkpoint(seed.target),
    )
    best = seed
    layer_queues = [deque([layer]) for layer in layers]
    next_layer_index = 0
    trials: list[IncrementalTrial] = []
    solver_calls = 0
    ordinal = 0
    while (
        any(layer_queues)
        and solver_calls < max_trials
        and ordinal < MAX_INCREMENTAL_BATCH_DECISIONS
    ):
        # Rotate across musical layers instead of exhausting every bisection of
        # the bass skeleton before the color layer gets one chance. Each deque
        # remains breadth-first: rejected children go to its tail, while this
        # cursor advances to the next non-empty layer.
        selected_layer_index: int | None = None
        for offset in range(len(layer_queues)):
            layer_index = (next_layer_index + offset) % len(layer_queues)
            if layer_queues[layer_index]:
                selected_layer_index = layer_index
                break
        assert selected_layer_index is not None
        next_layer_index = (selected_layer_index + 1) % len(layer_queues)
        queue = layer_queues[selected_layer_index]
        batch = queue.popleft()
        tentative = _merge_target(best.target, batch)
        static_reasons = _static_gate_reasons(source, tentative)
        if static_reasons:
            trial = IncrementalTrial(
                ordinal,
                batch,
                False,
                False,
                IncrementalRejectReason.STATIC_GATE,
                None,
            )
            trials.append(trial)
            _trace_trial(trace, candidate_index=candidate_index, trial=trial, current=best)
            ordinal += 1
            left, right = _split(batch)
            if right:
                # Breadth-first salvage plus outside-in layer ordering makes a
                # bounded prefix representative of the whole piece.
                queue.append(left)
                queue.append(right)
            continue

        solved, oracle = solve_and_check(
            tentative,
            goal.tuning,
            goal.capo,
            profile,
            tempo_bpm=goal.tempo_bpm,
            beats_per_bar=source.meta.time_sig[0],
        )
        solver_calls += 1
        accepted = False
        reason: IncrementalRejectReason | None
        verdict: str | None
        checkpoint: IncrementalCheckpoint | None = None
        if isinstance(solved, Infeasible):
            reason = IncrementalRejectReason.NO_TAB
            verdict = "INFEASIBLE"
        else:
            assert oracle is not None
            verdict = oracle.verdict
            if oracle.verdict != "GREEN":
                reason = IncrementalRejectReason.NON_GREEN
            else:
                candidate_checkpoint = _checkpoint(source, tentative, solved, oracle)
                if not _faithfulness_non_regressive(
                    source,
                    tentative,
                    best.faithfulness,
                    candidate_checkpoint.faithfulness,
                ):
                    reason = IncrementalRejectReason.FAITHFULNESS_REGRESSION
                elif not _quality_has_marginal_gain(source, best, tentative, batch):
                    reason = IncrementalRejectReason.NO_MUSICAL_GAIN
                else:
                    reason = None
                    accepted = True
                    checkpoint = candidate_checkpoint
        if checkpoint is not None:
            best = checkpoint
        trial = IncrementalTrial(
            ordinal,
            batch,
            True,
            accepted,
            reason,
            verdict,
        )
        trials.append(trial)
        _trace_trial(trace, candidate_index=candidate_index, trial=trial, current=best)
        ordinal += 1
        if not accepted:
            left, right = _split(batch)
            if right:
                queue.append(left)
                queue.append(right)

    untested_batch_count = sum(len(queue) for queue in layer_queues)
    if untested_batch_count:
        decision_budget_exhausted = ordinal >= MAX_INCREMENTAL_BATCH_DECISIONS
        trace.add(
            "PLAN",
            "Stopped additive salvage at a fixed deterministic search budget.",
            event="PLAN",
            candidate_index=candidate_index,
            policy="incremental_v1",
            reason_code=(
                "BATCH_DECISION_BUDGET_EXHAUSTED"
                if decision_budget_exhausted
                else "SOLVER_TRIAL_BUDGET_EXHAUSTED"
            ),
            maximum_trial_solves=max_trials,
            maximum_batch_decisions=MAX_INCREMENTAL_BATCH_DECISIONS,
            untested_batch_count=untested_batch_count,
        )
    accepted_count = best.quality.addition_note_count
    critic_outcome = (
        critique_outcome(
            source,
            best.tab,
            llm,
            call_scope_factory=call_scope_factory,
            candidate_index=candidate_index,
        )
        if use_critic and accepted_count > 0
        else None
    )
    trace.add(
        "SOLVE",
        "Finished the candidate at its best retained GREEN checkpoint.",
        event="SOLVE",
        candidate_index=candidate_index,
        policy="incremental_v1",
        verdict="GREEN",
        proposed_addition_count=len(additions),
        accepted_addition_count=accepted_count,
        trial_solver_calls=solver_calls,
        final_target_checkpoint=target_checkpoint(best.target),
    )
    return IncrementalCandidate(
        candidate_index,
        temperature,
        proposal,
        best,
        tuple(trials),
        critic_outcome,
        tuple(trace.steps),
        solver_calls,
        len(additions),
    )


def _temperature_schedule(
    n: int,
    *,
    temperature: float | None,
    temperature_schedule: tuple[float, ...] | None,
) -> tuple[float, ...]:
    if temperature is not None and temperature_schedule is not None:
        raise ValueError("temperature and temperature_schedule are mutually exclusive")
    if temperature is not None:
        values = (temperature,) * n
    elif temperature_schedule is not None:
        if type(temperature_schedule) is not tuple or len(temperature_schedule) != n:
            raise ValueError("temperature_schedule must contain one exact value per candidate")
        values = temperature_schedule
    else:
        values = tuple(min(1.0, 0.2 * index) for index in range(n))
    for index, value in enumerate(values):
        if type(value) is not float or not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError(f"temperature_schedule[{index}] must be a finite float in 0..1")
    return values


def arrange_incremental_pool(
    ir: MusicIR,
    goal: ArrangeGoal,
    llm: LLMClient,
    *,
    profile: Profile = MEDIAN_HAND,
    n: int = 4,
    max_iters: int = MAX_INCREMENTAL_TRIAL_SOLVES,
    use_critic: bool = True,
    temperature: float | None = None,
    temperature_schedule: tuple[float, ...] | None = None,
    call_scope_factory: ModelCallScopeFactory | None = None,
) -> IncrementalPool:
    """Build a paired-safe pool without touching the legacy benchmark policy."""

    source = snapshot_music_ir(ir)
    n = ensure_candidate_count(n)
    max_trials = ensure_repair_iterations(max_iters)
    if max_trials > MAX_INCREMENTAL_TRIAL_SOLVES:
        raise ValueError(
            f"max_iters must be in 0..{MAX_INCREMENTAL_TRIAL_SOLVES} for incremental_v1"
        )
    use_critic = ensure_boolean_control(use_critic, path="use_critic")
    temperatures = _temperature_schedule(
        n,
        temperature=temperature,
        temperature_schedule=temperature_schedule,
    )
    seed = melody_seed_target(source)
    notes, tuning, capo, profile, tempo_bpm = ensure_solver_domain(
        seed,
        goal.tuning,
        goal.capo,
        profile,
        tempo_bpm=goal.tempo_bpm,
    )
    seed = notes
    goal = ArrangeGoal(
        style=goal.style,
        tier=goal.tier,
        tuning=tuning,
        capo=capo,
        tempo_bpm=tempo_bpm,
        extras=goal.extras,
    )
    seed_trace = Trace()
    solved, oracle = solve_and_check(
        seed,
        tuning,
        capo,
        profile,
        tempo_bpm=tempo_bpm,
        beats_per_bar=source.meta.time_sig[0],
    )
    seed_trace.add(
        "SOLVE",
        "Solved the exact melody-only seed before any model call.",
        event="SOLVE",
        policy="incremental_v1",
        target_checkpoint=target_checkpoint(seed),
        status="NO_TAB" if isinstance(solved, Infeasible) else "TAB",
        verdict=None if oracle is None else oracle.verdict,
    )
    if isinstance(solved, Infeasible) or oracle is None or oracle.verdict != "GREEN":
        return IncrementalPool(
            seed,
            None,
            solved,
            oracle,
            tuple(seed_trace.steps),
            (),
            n,
        )
    seed_checkpoint = _checkpoint(source, seed, solved, oracle)
    candidates: list[IncrementalCandidate] = []
    for candidate_index in range(n):
        proposal = propose_arrangement_outcome(
            source,
            goal,
            llm,
            temperature=temperatures[candidate_index],
            profile=profile,
            call_scope_factory=call_scope_factory,
            candidate_index=candidate_index,
            incremental_guidance=True,
        )
        candidates.append(
            _salvage_candidate(
                source,
                goal,
                profile,
                proposal,
                seed_checkpoint,
                candidate_index=candidate_index,
                temperature=temperatures[candidate_index],
                max_trials=max_trials,
                llm=llm,
                use_critic=use_critic,
                call_scope_factory=call_scope_factory,
            )
        )
    return IncrementalPool(
        seed,
        seed_checkpoint,
        solved,
        oracle,
        tuple(seed_trace.steps),
        tuple(candidates),
        n,
    )


def _available_score(gate: FaithfulnessGate, dimension: str) -> float:
    if dimension not in gate.evaluated_dimensions:
        return 1.0
    field = {"bass_root": "bass_root", "harmony": "harmony"}[dimension]
    return cast(float, getattr(gate, field))


def _rank(
    candidate: IncrementalCandidate, *, use_critic: bool
) -> tuple[int, int, int, float, float, int, float, float, int, int, int]:
    gate = candidate.best.faithfulness
    critic_outcome = candidate.critic_outcome
    critic_success = (
        use_critic
        and critic_outcome is not None
        and critic_outcome.status is CriticStatus.LLM_SUCCESS
    )
    critic_score = critic_outcome.score.overall if critic_success and critic_outcome else 0.0
    return (
        1,
        # Every retained checkpoint already contains the exact immutable source
        # anchors. Reward useful whole-piece enrichment before consulting the
        # legacy top-voice scalar so safe rest fills cannot lose by definition.
        candidate.best.quality.points,
        1 if gate.passed else 0,
        _available_score(gate, "bass_root"),
        _available_score(gate, "harmony"),
        1 if critic_success else 0,
        critic_score,
        cast(float, gate.melody_f1),
        -candidate.best.quality.bass_leap_sum,
        -candidate.accepted_addition_count,
        -candidate.index,
    )


def _selection_step(trace: Trace, candidate: IncrementalCandidate, considered: int) -> None:
    checkpoint = candidate.best
    gate = checkpoint.faithfulness
    score = checkpoint.fidelity
    critic = candidate.critic
    agent_contributed = (
        candidate.proposal.status is ProposalStatus.LLM_SUCCESS
        and candidate.accepted_addition_count > 0
    )
    selected_index = candidate.index if agent_contributed else None
    detail = (
        f"Selected candidate {candidate.index}; playability and fidelity remain separate gates."
        if agent_contributed
        else "Selected the deterministic melody baseline; no Agent addition passed every gate."
    )
    trace.add(
        "SELECT",
        detail,
        event="CANDIDATE_SELECTED",
        candidate_index=selected_index,
        winner_candidate_index=selected_index,
        candidates_considered=considered,
        verdict="GREEN",
        green_certified=True,
        playability_gate="passed",
        faithfulness_passed=gate.passed,
        ranking_melody_recall=score.melody_recall,
        ranking_bass_preserved=score.bass_preserved,
        ranking_harmony_jaccard=score.harmony_jaccard,
        melody_f1=gate.melody_f1,
        bass_root_accuracy=gate.bass_root,
        harmony_jaccard=gate.harmony,
        evaluated_dimensions=gate.evaluated_dimensions,
        unavailable_dimensions=gate.unavailable_dimensions,
        critic_status="SCORED" if critic is not None else "NOT_RUN",
        critic_overall=critic.overall if critic is not None else None,
    )


def best_of_incremental_pool(
    pool: IncrementalPool,
    k: int,
    *,
    use_critic: bool = True,
) -> ArrangeResult:
    """Select one prefix winner while ignoring unavailable source dimensions."""

    use_critic = ensure_boolean_control(use_critic, path="use_critic")
    k = min(ensure_candidate_count(k, path="k"), pool.requested_candidates)
    trace = Trace(list(pool.seed_trace_steps))
    candidates = pool.candidates[:k]
    if pool.seed_checkpoint is None or not candidates:
        trace.add(
            "SELECT",
            "No candidate returned a tablature result within the bounded search.",
            event="NO_CANDIDATE_SELECTED",
            winner_candidate_index=None,
            candidates_considered=0,
            playability_gate=None,
            faithfulness_passed=None,
        )
        return ArrangeResult(None, None, None, None, trace, 0)
    winner = max(candidates, key=lambda candidate: _rank(candidate, use_critic=use_critic))
    trace.steps.extend(winner.trace_steps)
    _selection_step(trace, winner, k)
    return ArrangeResult(
        winner.best.tab,
        winner.best.oracle,
        winner.best.fidelity,
        winner.critic,
        trace,
        k,
    )


def arrange_incremental(
    ir: MusicIR,
    goal: ArrangeGoal,
    llm: LLMClient,
    *,
    profile: Profile = MEDIAN_HAND,
    n: int = 4,
    max_iters: int = MAX_INCREMENTAL_TRIAL_SOLVES,
    use_critic: bool = True,
    temperature: float | None = None,
    temperature_schedule: tuple[float, ...] | None = None,
    call_scope_factory: ModelCallScopeFactory | None = None,
) -> ArrangeResult:
    """Arrange from a GREEN melody seed with bounded additive salvage."""

    pool = arrange_incremental_pool(
        ir,
        goal,
        llm,
        profile=profile,
        n=n,
        max_iters=max_iters,
        use_critic=use_critic,
        temperature=temperature,
        temperature_schedule=temperature_schedule,
        call_scope_factory=call_scope_factory,
    )
    return best_of_incremental_pool(pool, pool.requested_candidates, use_critic=use_critic)


__all__ = [
    "EnrichmentQuality",
    "IncrementalCandidate",
    "IncrementalCheckpoint",
    "IncrementalPool",
    "IncrementalRejectReason",
    "IncrementalTrial",
    "MAX_INCREMENTAL_BATCH_DECISIONS",
    "MAX_INCREMENTAL_TRIAL_SOLVES",
    "arrange_incremental",
    "arrange_incremental_pool",
    "best_of_incremental_pool",
    "enrichment_quality",
    "melody_seed_target",
    "proposal_additions",
]
